"""K2 · 检索增强（docs/talent-ecosystem-plan.md §3 / vision §5 / 迁移 v26）。

锁：
- 正文 content 参与检索（FTS5 trigram）：只在正文里的关键词能命中；
- 中文查询串命中中文正文（CJK 长 token 拆 3 字滑窗）；
- stale 条目同档排后（降置信），不剔除；
- FTS 不可用（表缺失/查询失败）时回落 K1 的 token-overlap 旧口径，不炸；
- 服务层同步：create_knowledge_item 建/改后检索可见；
- v26 内联回填：存量行全部进 FTS 索引。
"""

import time
import uuid

import sqlalchemy as sa
from alembic import command

from app.core.database import _alembic_config
from app.learning import retrieval
from app.models.enums import KnowledgeScope
from app.repositories import knowledge as knowledge_repo

V25 = "x0e2f4a6c8d1"


def _marker() -> str:
    return f"zzk2{int(time.time() * 1000)}{uuid.uuid4().hex[:6]}"


def _item(db, employee_id: int, marker: str, *, content: str = "", stale: bool = False):
    item = knowledge_repo.create_knowledge_item(
        db,
        scope=KnowledgeScope.private.value,
        owner_employee_id=employee_id,
        title=f"{marker} note",
        content=content,
        topic=marker,
        status="active",
        confidence=0.9,
        sources=[],
        freshness_status="stale" if stale else "fresh",
    )
    db.commit()
    return item


def _upgrade(engine, revision: str) -> None:
    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def test_content_only_keyword_matches_via_fts(db, employees_by_slug):
    """关键词只出现在 content（title/topic 里没有）也能命中——K2 前不可能。"""
    alice = employees_by_slug["alice"]
    marker = _marker()
    secret = f"quixoticzebra{uuid.uuid4().hex[:8]}"
    item = _item(db, alice["id"], marker, content=f"正文里藏着 {secret} 这个关键词")

    result = retrieval.retrieve_for_task(db, alice["id"], f"研究 {secret} 的方案", "")
    assert item.topic in result.knowledge


def test_chinese_query_matches_chinese_content(db, employees_by_slug):
    """CJK：查询串与正文共享局部片段即可命中（3 字滑窗），不要求整串出现。"""
    alice = employees_by_slug["alice"]
    marker = _marker()
    item = _item(db, alice["id"], marker, content="本文介绍部署流水线的设计与实践")
    other = _item(db, alice["id"], f"{marker}other", content="完全不相干的内容记录")

    result = retrieval.retrieve_for_task(db, alice["id"], "梳理流水线设计的现状", "")
    assert item.topic in result.knowledge
    assert other.topic not in result.knowledge


def test_stale_items_rank_after_fresh_in_the_same_tier(db, employees_by_slug):
    """同强度同 scope：fresh 在前、stale 在后；stale 不剔除（stale ≠ 无效）。"""
    alice = employees_by_slug["alice"]
    marker = _marker()
    stale_item = _item(db, alice["id"], f"{marker}s", stale=True)
    fresh_item = _item(db, alice["id"], f"{marker}f")

    knowledge = retrieval.retrieve_for_task(db, alice["id"], f"{marker}s {marker}f", "").knowledge
    assert fresh_item.topic in knowledge and stale_item.topic in knowledge
    assert knowledge.index(fresh_item.topic) < knowledge.index(stale_item.topic)


def test_fts_unavailable_falls_back_to_legacy_overlap(db, employees_by_slug, monkeypatch):
    """FTS 失败 → 回落旧口径：title/topic 命中照常；只在正文里的词不再命中。"""
    alice = employees_by_slug["alice"]
    marker = _marker()
    secret = f"fallbackonly{uuid.uuid4().hex[:8]}"
    title_hit = _item(db, alice["id"], marker)
    body_only = _item(db, alice["id"], f"{marker}b", content=f"body has {secret}")
    monkeypatch.setattr(
        knowledge_repo,
        "fts_match_ids",
        lambda db, tokens: None,  # 模拟虚拟表不存在
    )

    result = retrieval.retrieve_for_task(db, alice["id"], f"{marker} {secret}", "")
    assert title_hit.topic in result.knowledge  # 旧口径 title/topic 命中
    assert body_only.topic not in result.knowledge  # 旧口径不扫 content


def test_fts_syncs_on_create_and_resync(db, employees_by_slug):
    """服务层同步：create_knowledge_item 后立即可被 FTS 命中；重同步幂等。"""
    alice = employees_by_slug["alice"]
    marker = _marker()
    item = _item(db, alice["id"], marker)

    assert item.id in knowledge_repo.fts_match_ids(db, {marker})
    # 更新路径（delete+insert 幂等）：改正文后重同步，新词可命中、旧词不残留
    fresh = f"resynced{uuid.uuid4().hex[:8]}"
    item.content = f"重写后的正文 {fresh}"
    db.flush()
    knowledge_repo.sync_knowledge_fts(db, item)
    db.commit()
    assert item.id in knowledge_repo.fts_match_ids(db, {fresh})


def test_v26_backfills_existing_items_into_fts(tmp_path):
    """v26：建 FTS 表 + 内联回填存量行；回填后正文关键词可被 MATCH 命中。"""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'fts.db'}")
    _upgrade(engine, V25)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO knowledge_items (scope, title, content, topic, status, confidence,"
                " sources, freshness_status, created_at, updated_at) VALUES ('company',"
                " '旧条目', '正文包含 legacydeepterm 这个词', 'legacy-topic', 'active', 0.9,"
                " '[]', 'fresh', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )

    _upgrade(engine, "head")

    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM knowledge_items_fts")).scalar_one() == 1
        hit = conn.execute(
            sa.text("SELECT rowid FROM knowledge_items_fts WHERE knowledge_items_fts MATCH :q"),
            {"q": '"legacydeepterm"'},
        ).all()
        assert [row[0] for row in hit] == [1]
        # 虚拟表 + 影子表都在（alembic 漂移排除见 migrations/env.py 的前缀过滤）
        shadows = {
            row[0]
            for row in conn.execute(
                sa.text("SELECT name FROM sqlite_master WHERE name LIKE 'knowledge_items_fts%'")
            )
        }
        assert "knowledge_items_fts" in shadows
