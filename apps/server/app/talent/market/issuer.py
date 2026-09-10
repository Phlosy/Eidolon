"""IssuerService（T2.4）—— 系统发行方投放成品角色（设计 §7 D11 / plan §4.5）。

**唯一纪律：发行走真实培养链**（D11）——Character → `advance_program` 跑满每个模板 →
阶段评估（证据聚合器）→ 挂牌。绝不存在"直接写能力分"的生成器：
品质档位（normal/fine/rare）只影响**参数与概率分布**（模板组合 / 证据 signal 修正 /
际遇触发权重 / 阶段覆盖主题数），能力仍由证据聚合产生。

本地模拟市场的发行方 = `MarketParticipant(kind=system_issuer, company_id=None)`（D8：
NPC/系统角色不进 `companies`）。发行角色的 `owner_company_id` 保持 **NULL**（T1 语义：
NULL = 在市场）；评估需要一个历史公司上下文（`assessment_runs.company_id` 非空），
由 `IssuerService` 显式传入"本部署的默认公司"作为**历史上下文**（只作 provenance，
不产生所有权/可见性：`/persons/*` 不因它而可读 —— 见设计 D13）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from app.models.enums import MarketParticipantKind, TalentOrigin
from app.repositories import cultivation as cultivation_repo
from app.talent.cultivation import engine as cultivation_engine
from app.talent.cultivation.templates import TEMPLATES, CultivationParams

_SURNAMES = ("林", "苏", "陈", "闻", "周", "许", "沈", "叶", "顾", "白", "陆", "祁")
_GIVEN_NAMES = ("舟", "禾", "默", "溪", "远", "清", "野", "川", "岚", "序", "舟", "临")


@dataclass(frozen=True)
class IssueTier:
    """一次发行的参数组合（**只影响采样**，不影响能力）。

    - `templates`：按序跑满的培养实例（不同模板 → 不同证据类型与覆盖面）；
    - `params`：培养参数（见 `CultivationParams`）。
    """

    tier: str
    templates: tuple[str, ...]
    params: CultivationParams


#: 首发三档（愿景 §2.1 的"品质分层不标战力"）：
#: normal = 职业教育底子；fine = 学院派 + 更高的证据强度/际遇权重；
#: rare = 学院派 + 职业派双履历（覆盖面更宽）+ 最高参数。
TIERS: dict[str, IssueTier] = {
    "normal": IssueTier("normal", ("vocational",), CultivationParams()),
    "fine": IssueTier(
        "fine",
        ("academic",),
        CultivationParams(signal_bonus=5, fortune_weight=1.25, intensity_bonus=1),
    ),
    "rare": IssueTier(
        "rare",
        ("academic", "vocational"),
        CultivationParams(signal_bonus=10, fortune_weight=1.5, intensity_bonus=1),
    ),
}


@dataclass(frozen=True)
class IssuedTalent:
    """发行结果（CLI/测试用；API 层尚无发行端点 —— 不做 UI，属 T2.7）。"""

    person_id: int
    profile_id: int
    identity_id: str
    tier: str
    templates: tuple[str, ...]
    evidence_count: int
    mean_signal: float | None
    listing_id: int | None


def get_tier(tier: str) -> IssueTier:
    spec = TIERS.get(tier)
    if spec is None:
        raise ValueError(f"unknown issuer tier: {tier}（可选 {sorted(TIERS)}）")
    return spec


def _default_tier_name(tier: str, index: int) -> str:
    surname = _SURNAMES[index % len(_SURNAMES)]
    given = _GIVEN_NAMES[(index // len(_SURNAMES)) % len(_GIVEN_NAMES)]
    return f"{surname}{given}"


class IssuerService:
    """系统发行方：按档位产出成品角色，并（默认）直接投放本地市场。"""

    def issue(
        self,
        db: Session,
        *,
        tier: str = "normal",
        name: str | None = None,
        owner_context_company_id: int,
        template: str | None = None,
        seed: str | None = None,
        list_on_market: bool = True,
        quality_tier: str | None = None,
    ) -> IssuedTalent:
        """发行一名角色。

        - `owner_context_company_id`：评估快照用的**历史上下文**（本地部署的默认公司）；
        - `template`：覆盖档位默认模板（测试用来隔离"参数"与"模板组合"的影响）；
        - `seed`：确定性来源（同 seed 同角色；传给每个 program 的 rng_seed）；
        - `list_on_market`：False 时只产出角色（供测试/离线生成）。
        """
        spec = get_tier(tier)
        templates = (template,) if template else spec.templates
        for template_id in templates:
            if template_id not in TEMPLATES:
                raise ValueError(f"unknown template: {template_id}")

        if name is None:
            issued_count = cultivation_repo.count_profiles_by_origin(db, TalentOrigin.issued.value)
            name = _default_tier_name(tier, issued_count)

        # 发行角色一生下来就在市场（T1 语义：owner_company_id IS NULL = 在市场）
        person, profile = cultivation_repo.create_character(
            db, name=name, origin=TalentOrigin.issued.value, owner_company_id=None
        )

        params_payload = asdict(spec.params)
        programs = []
        for index, template_id in enumerate(templates):
            program = cultivation_repo.create_program(
                db,
                person_id=person.id,
                template=template_id,
                rng_seed=f"{seed}:{index}" if seed else None,
                metadata_json={
                    "issuer": {"tier": spec.tier, "template_index": index, **params_payload}
                },
            )
            programs.append(program)

        cultivation_engine.initialize_character_brain(
            db, person.id, template_id=templates[0], seed=programs[0].rng_seed
        )
        db.commit()

        # 真实培养：每个实例按阶段推进直到 completed（评估节点自动触发）。
        # 有界循环：阶段数已知，超出即视为引擎异常（不静默死循环）。
        for program in programs:
            stage_count = len(TEMPLATES[program.template].stages)
            for _ in range(stage_count + 1):
                if program.status != "active":
                    break
                cultivation_engine.advance_program(
                    db,
                    program.id,
                    assessment_company_id=owner_context_company_id,
                )
            if program.status != "completed":
                raise RuntimeError(
                    f"issuer cultivation did not complete: program={program.id}"
                    f" status={program.status}"
                )

        listing_id: int | None = None
        if list_on_market:
            from app.repositories import market as market_repo
            from app.services import market as market_service

            participant = market_repo.ensure_participant(
                db,
                kind=MarketParticipantKind.system_issuer.value,
                company_id=None,
                display_name="Eidolon 发行方",
            )
            view, _created = market_service.list_for_participant(
                db,
                person_id=int(person.id),
                participant_id=int(participant.id),
                quality_tier=quality_tier or spec.tier,
                company_id=owner_context_company_id,
            )
            listing_id = view.listing_id

        # 事件只发 market.listed（list_for_participant 内）—— "发行"是挂牌的一种来源，
        # 不新增同义事件类型（事件计划表见 plan §7）。
        evidence_count, mean_signal = self._evidence_stats(db, int(person.id))
        return IssuedTalent(
            person_id=int(person.id),
            profile_id=int(profile.id),
            identity_id=profile.identity_id,
            tier=spec.tier,
            templates=templates,
            evidence_count=evidence_count,
            mean_signal=mean_signal,
            listing_id=listing_id,
        )

    def issue_batch(self, db: Session, *, count: int, **kwargs) -> list[IssuedTalent]:
        """批量发行（CLI 用）：逐人独立，互不影响（失败即停，不静默跳过）。"""
        if count < 1:
            raise ValueError("count must be >= 1")
        return [self.issue(db, **kwargs) for _ in range(count)]

    @staticmethod
    def _evidence_stats(db: Session, person_id: int) -> tuple[int, float | None]:
        """发行结果的**唯一**度量：证据条数与平均 signal（不读、不写能力分）。"""
        from sqlalchemy import func, select

        from app.models.competency import CompetencyEvidence

        count, mean = db.execute(
            select(func.count(), func.avg(CompetencyEvidence.signal)).where(
                CompetencyEvidence.person_id == person_id
            )
        ).one()
        return int(count or 0), (round(float(mean), 2) if mean is not None else None)
