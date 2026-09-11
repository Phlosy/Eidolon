"""NPC 市场参与者（T2.7c）—— 本地市场里的"其他公司"作为交易对手雏形。

设计边界（T2 设计 §7 D8 / plan §4.8）：

- NPC **不写 `companies`**（避免污染公司作用域读面），只用
  `market_participants(kind=npc_company, company_id=NULL)` 表达身份；
- NPC 招人**不建 Employee**（没有公司行、也不该伪造员工行）：
  只 CAS 关闭挂牌并写 `recruited_participant_id` + `close_reason='npc_recruited'`，
  然后发 `market.candidate_taken`；
- 选拔**复用同一个 Fit 引擎**（`fit_service.calculate_person_fit`）——不发明第二套评分；
  门槛是"分数 + 置信度"**并列**（`Unknown != Bad`：证据不足的人不会被"当成最差"买走，
  只是不被选中）；
- NPC 的招聘标准是一个**全局职位模板**（`company_id=NULL`，`template_scope=system`）
  + 已发布的画像版本 —— 与公司职位同构，只是不属任何公司。

节奏由调用方决定（CLI / 未来的调度器）：`ensure_participants` 幂等建标准与身份，
`run_once` 跑一轮"发现 → Fit → 选择 → 成交"。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.events.bus import bus
from app.models.competency import CompetencyDefinition, CompetencyDomain
from app.models.enums import MarketParticipantKind, TemplateScope
from app.models.market import MarketParticipant
from app.models.position import PositionDefinition
from app.repositories import market as market_repo
from app.services import position_profile as profile_service
from app.talent.fit import service as fit_service
from app.talent.market.contracts import MarketListingView, MarketSearchQuery
from app.talent.market.local_adapter import LocalMarketAdapter


@dataclass(frozen=True)
class NpcRequirement:
    competency_code: str
    minimum_score: int
    target_score: int
    minimum_confidence: float = 0.4
    critical: bool = False


@dataclass(frozen=True)
class NpcSpec:
    """一个 NPC 雇主：身份 + 招聘标准（全局职位模板）+ 出手阈值。"""

    key: str
    display_name: str
    position_code: str
    position_name: str
    legacy_role: str
    requirements: tuple[NpcRequirement, ...]
    #: 出手阈值：分数与置信度**并列**达标（不用任何混合总分）
    min_fit_score: float = 0.7
    min_fit_confidence: float = 0.4
    max_per_round: int = 1
    notes: str = ""


#: 首发 NPC（本地模拟的对手方雏形；品质偏好不同、不标"战力"）
NPC_SPECS: tuple[NpcSpec, ...] = (
    NpcSpec(
        key="xinghai",
        display_name="星海科技",
        position_code="npc-xinghai-engineer",
        position_name="星海科技 · 工程师",
        legacy_role="engineer",
        requirements=(
            NpcRequirement("analysis_problem_solving", 60, 80, 0.4, critical=True),
            NpcRequirement("execution", 55, 75, 0.35),
        ),
        min_fit_score=0.7,
        min_fit_confidence=0.4,
        notes="偏好工程执行与问题解决，出手稳。",
    ),
    NpcSpec(
        key="mercury",
        display_name="墨丘利实验室",
        position_code="npc-mercury-researcher",
        position_name="墨丘利实验室 · 研究员",
        legacy_role="researcher",
        requirements=(
            NpcRequirement("analysis_problem_solving", 65, 85, 0.45, critical=True),
            NpcRequirement("learning_growth", 55, 75, 0.35),
        ),
        min_fit_score=0.75,
        min_fit_confidence=0.45,
        notes="研究品味挑剔，喜欢证据扎实的履历。",
    ),
)


@dataclass(frozen=True)
class NpcAcquisition:
    listing_id: int
    person_id: int
    identity_id: str
    participant_id: int
    npc_name: str
    fit_score: float | None
    fit_confidence: float | None


@dataclass(frozen=True)
class NpcRoundReport:
    npc_name: str
    considered: int = 0
    acquired: list[NpcAcquisition] = field(default_factory=list)
    skipped_below_threshold: int = 0
    dry_run: bool = False


class NpcMarketService:
    """NPC 雇主的市场活动（发现 → Fit → 选择 → 成交）。"""

    # ---- 幂等建"标准 + 身份" ----

    def ensure_participants(
        self, db: Session, *, company_context_id: int
    ) -> list[MarketParticipant]:
        """幂等确保每个 NPC 的全局职位模板（含已发布画像）与参与者身份存在。"""
        participants: list[MarketParticipant] = []
        for spec in NPC_SPECS:
            definition = self._ensure_definition(db, spec)
            self._ensure_published_profile(db, spec, definition, company_context_id)
            # NPC 行都是 (kind=npc_company, company_id=NULL)：唯一性靠 display_name 区分
            # （specs 之间互不相同；profile_json.npc_key 是身份锚点）
            participant = self._participant_by_key(db, spec.key)
            if participant is None:
                participant = MarketParticipant(
                    kind=MarketParticipantKind.npc_company.value,
                    company_id=None,
                    display_name=spec.display_name,
                    profile_json={
                        "npc_key": spec.key,
                        "position_code": spec.position_code,
                        "min_fit_score": spec.min_fit_score,
                        "min_fit_confidence": spec.min_fit_confidence,
                        "max_per_round": spec.max_per_round,
                        "notes": spec.notes,
                    },
                    active=True,
                )
                db.add(participant)
                db.flush()
            participants.append(participant)
        db.commit()
        return participants

    @staticmethod
    def _participant_by_key(db: Session, key: str) -> MarketParticipant | None:
        return db.scalars(
            sa.select(MarketParticipant).where(
                MarketParticipant.kind == MarketParticipantKind.npc_company.value,
                MarketParticipant.display_name == _SPEC_BY_KEY[key].display_name,
            )
        ).first()

    def _ensure_definition(self, db: Session, spec: NpcSpec) -> PositionDefinition:
        existing = db.scalars(
            sa.select(PositionDefinition).where(
                PositionDefinition.code == spec.position_code,
                PositionDefinition.company_id.is_(None),
            )
        ).first()
        if existing is not None:
            return existing
        definition = PositionDefinition(
            company_id=None,  # 全局模板：不属任何公司（D8）
            template_scope=TemplateScope.system.value,
            code=spec.position_code,
            name=spec.position_name,
            description=f"{spec.display_name} 的招聘标准（本地市场 NPC，非玩家公司）",
            job_family="",
            level=1,
            responsibilities=[],
            career_path_metadata={"npc_key": spec.key},
            built_in=False,
            legacy_role=spec.legacy_role,
        )
        db.add(definition)
        db.flush()
        return definition

    def _ensure_published_profile(
        self,
        db: Session,
        spec: NpcSpec,
        definition: PositionDefinition,
        company_context_id: int,
    ) -> None:
        if profile_service.active_profile(db, int(definition.id)) is not None:
            return
        version = profile_service.create_draft(db, definition)
        for requirement in spec.requirements:
            profile_service.add_requirement(
                db,
                version,
                self._definition_id(db, requirement.competency_code),
                company_id=company_context_id,  # 目录校验用上下文；全局目录对任何公司可见
                requirement_type="required",
                minimum_score=requirement.minimum_score,
                target_score=requirement.target_score,
                minimum_confidence=requirement.minimum_confidence,
                critical=requirement.critical,
                weight=1.0,
                notes=f"{spec.display_name} 标准",
            )
        profile_service.publish_profile(db, version, note=f"{spec.display_name} 招聘标准（T2.7c）")

    @staticmethod
    def ensure_system_definition(db: Session):
        """任意**系统模板**职位（给"没有 NPC spec"的调用方做 fit 口径）。

        M1.8 的 NPC 经济允许直接指定参与者（测试/运维补充的 NPC）—— 那些参与者没有
        自己的招聘标准模板，用系统模板做统一口径即可（不新增模板、不改 NPC spec）。
        """
        from app.repositories import position as position_repo

        definitions = position_repo.list_definitions(db)
        system_defs = [row for row in definitions if row.company_id is None]
        if not system_defs:  # pragma: no cover - 仓库 seed 必然有系统模板
            return None
        return sorted(system_defs, key=lambda row: int(row.id))[0]

    @staticmethod
    def _definition_id(db: Session, code: str) -> int:
        value = db.scalar(
            sa.select(CompetencyDefinition.id)
            .join(CompetencyDomain, CompetencyDomain.id == CompetencyDefinition.domain_id)
            .where(CompetencyDefinition.code == code, CompetencyDomain.company_id.is_(None))
        )
        if value is None:
            raise ValueError(f"competency definition not found: {code}")
        return int(value)

    # ---- 一轮市场活动 ----

    def take_candidate(
        self,
        db: Session,
        *,
        listing_id: int,
        person_id: int,
        identity_id: str,
        participant_id: int,
        npc_name: str,
        fit_score: float | None,
        fit_confidence: float | None,
        company_context_id: int,
        reason: str = "npc_recruited",
        commit: bool = True,
    ) -> bool:
        """把某个候选人"拿走"（CAS 关闭挂牌 + 事件）；返回是否真的关掉了这一行。

        - **CAS 语义不变**：并发/重复调用只有一个赢家（与玩家招募同一纪律）；
        - `commit=False`（M1.8）：事务与事件都归调用方 —— M1 的
          `NpcEconomyService` 要在同一事务里先付钱再落这一笔（§29）；
        - 抽出来的理由：T2 的 NPC 活动（无资金）与 M1 的 NPC 经济（有资金）
          必须共用**同一个**成交原语，避免两套"谁被拿走了"的写法。
        """
        claimed = market_repo.close_active_listing(
            db,
            int(listing_id),
            reason=reason,
            recruited_participant_id=int(participant_id),
        )
        if not claimed:
            return False
        if commit:
            db.commit()
            bus.publish(
                "market.candidate_taken",
                {
                    "person_id": int(person_id),
                    "identity_id": identity_id,
                    "listing_id": int(listing_id),
                    "participant_id": int(participant_id),
                    "participant_name": npc_name,
                    "known_fit_score": round(fit_score, 4) if fit_score is not None else None,
                    "fit_confidence": (
                        round(fit_confidence, 4) if fit_confidence is not None else None
                    ),
                },
                company_id=company_context_id,
            )
        return True

    def publish_candidate_taken(
        self,
        *,
        listing_id: int,
        person_id: int,
        identity_id: str,
        participant_id: int,
        npc_name: str,
        fit_score: float | None,
        fit_confidence: float | None,
        company_context_id: int,
    ) -> None:
        """事件单独暴露：`commit=False` 的调用方在自己的事务提交后补发（与 T2/M1 约定一致）。"""
        bus.publish(
            "market.candidate_taken",
            {
                "person_id": int(person_id),
                "identity_id": identity_id,
                "listing_id": int(listing_id),
                "participant_id": int(participant_id),
                "participant_name": npc_name,
                "known_fit_score": round(fit_score, 4) if fit_score is not None else None,
                "fit_confidence": (
                    round(fit_confidence, 4) if fit_confidence is not None else None
                ),
            },
            company_id=company_context_id,
        )

    def run_once(
        self,
        db: Session,
        *,
        company_context_id: int,
        only_npc_key: str | None = None,
        dry_run: bool = False,
    ) -> list[NpcRoundReport]:
        """跑一轮：每个 NPC 各自发现 → Fit → 选择 → 成交（未达标者只是落选）。"""
        specs = [s for s in NPC_SPECS if only_npc_key in (None, s.key)]
        if only_npc_key is not None and not specs:
            raise ValueError(
                f"unknown npc key: {only_npc_key}（可选 {[s.key for s in NPC_SPECS]}）"
            )

        self.ensure_participants(db, company_context_id=company_context_id)
        adapter = LocalMarketAdapter()
        candidates: list[MarketListingView] = adapter.search_listings(
            db, MarketSearchQuery(limit=None)
        )
        reports: list[NpcRoundReport] = []
        for spec in specs:
            participant = self._participant_by_key(db, spec.key)
            assert participant is not None
            definition = self._ensure_definition(db, spec)
            considered = 0
            scored: list[tuple[float, float, MarketListingView]] = []
            skipped = 0
            for view in candidates:
                result = fit_service.calculate_person_fit(
                    db,
                    person_id=view.person_id,
                    position_definition_id=int(definition.id),
                    company_id=company_context_id,
                )
                considered += 1
                score = result.known_fit_score
                confidence = result.fit_confidence
                if (
                    score is None
                    or confidence is None
                    or score < spec.min_fit_score
                    or confidence < spec.min_fit_confidence
                ):
                    skipped += 1
                    continue
                scored.append((float(score), float(confidence), view))
            scored.sort(key=lambda item: (-item[0], -item[1], item[2].listing_id))
            acquired: list[NpcAcquisition] = []
            for score, confidence, view in scored[: spec.max_per_round]:
                if dry_run:
                    acquired.append(
                        NpcAcquisition(
                            listing_id=view.listing_id,
                            person_id=view.person_id,
                            identity_id=view.identity_id,
                            participant_id=int(participant.id),
                            npc_name=spec.display_name,
                            fit_score=score,
                            fit_confidence=confidence,
                        )
                    )
                    continue
                # CAS 关闭 + 事件（M1.8 起抽成 `take_candidate`：M1 的 NPC 经济要在
                # 同一事务里加"付钱"这一步，见 docs/m1-economy-design.md §29）
                claimed = self.take_candidate(
                    db,
                    listing_id=view.listing_id,
                    person_id=view.person_id,
                    identity_id=view.identity_id,
                    participant_id=int(participant.id),
                    npc_name=spec.display_name,
                    fit_score=score,
                    fit_confidence=confidence,
                    company_context_id=company_context_id,
                )
                if not claimed:
                    continue
                acquired.append(
                    NpcAcquisition(
                        listing_id=view.listing_id,
                        person_id=view.person_id,
                        identity_id=view.identity_id,
                        participant_id=int(participant.id),
                        npc_name=spec.display_name,
                        fit_score=score,
                        fit_confidence=confidence,
                    )
                )
            reports.append(
                NpcRoundReport(
                    npc_name=spec.display_name,
                    considered=considered,
                    acquired=acquired,
                    skipped_below_threshold=skipped,
                    dry_run=dry_run,
                )
            )
        return reports


_SPEC_BY_KEY: dict[str, NpcSpec] = {spec.key: spec for spec in NPC_SPECS}


def npc_profile(db: Session, key: str) -> dict:
    """NPC 的公开偏好（市场 UI 可用于标注"谁在跟你抢人"）。"""
    spec = _SPEC_BY_KEY.get(key)
    if spec is None:
        raise ValueError(f"unknown npc key: {key}")
    return {
        "key": spec.key,
        "display_name": spec.display_name,
        "position_code": spec.position_code,
        "notes": spec.notes,
        "min_fit_score": spec.min_fit_score,
        "min_fit_confidence": spec.min_fit_confidence,
    }
