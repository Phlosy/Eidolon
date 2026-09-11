"""LedgerService —— **唯一 Posting Core**（M1.1b，设计 §12b，E27/E28）。

所有资金变化只有这一条路径：

```
便利原语（transfer / escrow_* / MonetaryAuthority.mint|burn|treasury_transfer）
      ↓ 按 LEG_BLUEPRINTS 构造 posting（方向不手写）
LedgerService.post()
      ├── 幂等（同 key → 返回既有交易，绝不二次过账）
      ├── 校验（≥2 腿、金额正整数、账户存在且 active、币种一致、无自指腿、Σdebit == Σcredit）
      ├── 权限（mint / burn / treasury 需 MonetaryAuthority 令牌）
      ├── INSERT ledger_transactions + ledger_entries（append-only）
      └── 投影增量更新 + CAS（同事务；rowcount == 0 → 整笔回滚）
```

**同事务纪律**（E28）：CAS、Ledger 写入、投影更新在同一个数据库事务里；失败一律
`rollback`（调用方拿到的 session 不会残留半笔账）。`commit=False` 供上层组合更大事务
（此时事件由调用方负责发，见 `post()` 文档）。

**不做**：分布式锁 / 多节点共识 —— SQLite 单写 + 事务 + 唯一约束 + CAS 足够，
且不假装多线程安全（设计 §33）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.economy.contracts import (
    LEG_BLUEPRINTS,
    EconomicActor,
    EconomyContractError,
    assert_balanced_totals,
    balance_delta,
    circulating_supply,
    requires_funds,
    total_supply,
    validate_amount,
)
from app.events.bus import bus
from app.models.base import utcnow
from app.models.economy import LedgerAccount, LedgerEntry, LedgerTransaction
from app.models.enums import (
    Currency,
    EconomicActorKind,
    LedgerAccountKind,
    LedgerAccountStatus,
    LedgerEntryDirection,
    LedgerTransactionStatus,
    TransactionKind,
)
from app.repositories import economy as economy_repo
from app.services.economy.accounts import AccountService
from app.services.economy.authority import AUTHORITY_TOKEN
from app.services.economy.balances import derive_wallets

logger = get_logger(__name__)

#: 只有 MonetaryAuthority 能构造的交易类型（E4/E5/E23）
AUTHORITY_KINDS = frozenset(
    {TransactionKind.mint, TransactionKind.burn, TransactionKind.treasury_transfer}
)

_ESCROW_KINDS = frozenset(
    {TransactionKind.escrow_fund, TransactionKind.escrow_release, TransactionKind.escrow_refund}
)


class LedgerError(RuntimeError):
    """账本领域错误（reason code 机器可读；API/上层服务据此转换）。"""

    def __init__(self, reason: str, http_status: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


class PostingRejected(LedgerError):
    """结构/契约层拒绝（腿数、金额、币种、账户状态、自指、不平）。"""


class InsufficientFunds(LedgerError):
    """资金不足（CAS rowcount = 0）—— E24 的对外表达。"""

    def __init__(self, account_id: int, reason: str = "insufficient_funds") -> None:
        super().__init__(reason)
        self.account_id = account_id


class IdempotencyConflict(LedgerError):
    """同一个幂等键被用于**不同**的过账内容（键复用 = 调用方 bug）。"""


class AuthorityRequired(LedgerError):
    """mint / burn / treasury_transfer 未携带 MonetaryAuthority 令牌（E4/E23）。"""

    def __init__(self, transaction_type: TransactionKind) -> None:
        super().__init__("authority_required", http_status=403)
        self.transaction_type = transaction_type


@dataclass(frozen=True)
class PostingEntry:
    """一条过账腿（账户 id + 方向 + 正整数金额）。"""

    account_id: int
    direction: LedgerEntryDirection
    amount: int


@dataclass(frozen=True)
class Posting:
    """一笔待过账交易（业务字段 + 腿）。"""

    transaction_type: TransactionKind
    entries: tuple[PostingEntry, ...]
    currency: Currency = Currency.credit
    idempotency_key: str | None = None
    reference_type: str = ""
    reference_id: str = ""
    reason: str = ""
    initiated_by: EconomicActor | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PostingResult:
    """过账结果。`created=False` 表示命中幂等键、复用了既有交易（没有再次记账）。"""

    transaction: LedgerTransaction
    entries: tuple[LedgerEntry, ...]
    created: bool


@dataclass(frozen=True)
class SupplySnapshot:
    """货币供给快照（§4/§19）：minted − burned = supply；transfer 不影响供给。"""

    minted: int
    burned: int
    supply: int
    treasury_balance: int
    escrow_balance: int
    circulating: int


def blueprint_entries(
    transaction_type: TransactionKind, legs: dict[str, tuple[int, int]]
) -> tuple[PostingEntry, ...]:
    """按**冻结的腿蓝图**构造 posting 腿：`{role: (account_id, amount)}`。

    方向来自 `LEG_BLUEPRINTS`，调用方**不手写 debit/credit**（E26/E27 的落地点）。
    角色缺失或多余一律拒绝 —— 避免"少一腿"的账被静默接受。
    """
    specs = LEG_BLUEPRINTS[transaction_type]
    expected = {spec.role for spec in specs}
    if expected != set(legs):
        raise PostingRejected(
            f"{transaction_type.value} requires legs {sorted(expected)}, got {sorted(legs)}"
        )
    return tuple(
        PostingEntry(
            account_id=int(legs[spec.role][0]),
            direction=spec.direction,
            amount=legs[spec.role][1],
        )
        for spec in specs
    )


class LedgerService:
    """过账核心（构造 LegderService(db) 后调用；不持有跨请求状态）。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.accounts = AccountService(db)

    # ---------------------------------------------------------------- 唯一入口

    def post(
        self,
        posting: Posting,
        *,
        authority: object | None = None,
        commit: bool = True,
    ) -> PostingResult:
        """过账（幂等 + 守恒 + 资金 CAS + 投影更新，全在一个事务内）。

        `commit=False`：把提交权交给调用方（组合更大的业务事务）。此时
        `ledger.transaction_posted` 事件**不发** —— 由调用方在提交后自行发布，
        避免"事件说发生了、事务却回滚了"。
        """
        if posting.idempotency_key:
            replay = self._replay(posting)
            if replay is not None:
                return replay

        try:
            self._validate(posting, authority=authority)
            result = self._write(posting)
        except IntegrityError:
            # 并发下两个线程携带同一幂等键：唯一索引裁定赢家，输家回滚后复用赢家的交易
            # （不靠"先查再插"—— TOCTOU 会真的扣两次钱）。
            self.db.rollback()
            replay = self._replay(posting)
            if replay is not None:
                return replay
            raise
        except Exception:
            if commit:
                self.db.rollback()
            raise
        try:
            if commit:
                self.db.commit()
                self._publish(posting, result)
            return result
        except Exception:
            if commit:
                self.db.rollback()
            raise

    def _replay(self, posting: Posting) -> PostingResult | None:
        """幂等复用：命中既有交易时校验内容一致并返回（绝不二次过账）。"""
        if not posting.idempotency_key:
            return None
        existing = economy_repo.find_transaction_by_idempotency(self.db, posting.idempotency_key)
        if existing is None:
            return None
        self._assert_replay_matches(existing, posting)
        return PostingResult(
            transaction=existing,
            entries=tuple(economy_repo.list_entries(self.db, transaction_id=existing.id)),
            created=False,
        )

    def transfer(
        self,
        *,
        payer_account_id: int,
        payee_account_id: int,
        amount: int,
        reason: str = "",
        reference_type: str = "",
        reference_id: str = "",
        idempotency_key: str | None = None,
        initiated_by: EconomicActor | None = None,
        metadata: dict | None = None,
        commit: bool = True,
    ) -> PostingResult:
        """转账（玩家/公司之间的合法业务操作；不改变 Total Supply，E6）。"""
        posting = Posting(
            transaction_type=TransactionKind.transfer,
            entries=blueprint_entries(
                TransactionKind.transfer,
                {"payer": (payer_account_id, amount), "payee": (payee_account_id, amount)},
            ),
            idempotency_key=idempotency_key,
            reference_type=reference_type,
            reference_id=reference_id,
            reason=reason,
            initiated_by=initiated_by,
            metadata=dict(metadata or {}),
        )
        return self.post(posting, commit=commit)

    def escrow_fund(
        self,
        *,
        escrow_account_id: int,
        payer_account_id: int,
        amount: int,
        **kwargs: object,
    ) -> PostingResult:
        """锁资：资金离开出资人账户、进入该笔 Escrow 账户（Supply 不变，E7）。"""
        return self._post_escrow(
            TransactionKind.escrow_fund,
            "payer",
            escrow_account_id,
            payer_account_id,
            amount,
            kwargs,
        )

    def escrow_release(
        self,
        *,
        escrow_account_id: int,
        payee_account_id: int,
        amount: int,
        **kwargs: object,
    ) -> PostingResult:
        """释放：Escrow → 收款人（条件满足后；Escrow 账户归零，E25）。"""
        return self._post_escrow(
            TransactionKind.escrow_release,
            "payee",
            escrow_account_id,
            payee_account_id,
            amount,
            kwargs,
        )

    def escrow_refund(
        self,
        *,
        escrow_account_id: int,
        payer_account_id: int,
        amount: int,
        **kwargs: object,
    ) -> PostingResult:
        """退回：Escrow → **原出资人**（身份不匹配即拒绝，防止把托管资金退给第三方）。"""
        return self._post_escrow(
            TransactionKind.escrow_refund,
            "payer",
            escrow_account_id,
            payer_account_id,
            amount,
            kwargs,
        )

    # ---------------------------------------------------------------- 供给

    def supply(self) -> SupplySnapshot:
        """货币供给快照（**只读账本聚合**，不看投影）。"""
        minted, burned = economy_repo.system_supply_totals(self.db)
        supply = total_supply(issuance_credit_total=minted, burn_debit_total=burned)
        wallets = derive_wallets(self.db)
        by_id = {
            int(account.id): account
            for account in economy_repo.list_accounts_by_kind(
                self.db,
                kinds=(LedgerAccountKind.treasury.value, LedgerAccountKind.escrow.value),
            )
        }
        treasury = sum(
            wallet.available_balance
            for account_id, wallet in wallets.items()
            if by_id.get(account_id) is not None
            and by_id[account_id].kind == LedgerAccountKind.treasury.value
        )
        escrow = sum(
            wallet.available_balance
            for account_id, wallet in wallets.items()
            if by_id.get(account_id) is not None
            and by_id[account_id].kind == LedgerAccountKind.escrow.value
        )
        return SupplySnapshot(
            minted=minted,
            burned=burned,
            supply=supply,
            treasury_balance=treasury,
            escrow_balance=escrow,
            circulating=circulating_supply(
                total=supply, treasury_balance=treasury, escrow_balance=escrow
            ),
        )

    def ledger_balance(self, account_id: int) -> int:
        """某账户的账本余额（派生值；重建/校验时用它对照投影）。"""
        wallets = derive_wallets(self.db, account_ids=[account_id])
        wallet = wallets.get(account_id)
        return wallet.available_balance if wallet is not None else 0

    # ---------------------------------------------------------------- 内部

    def _post_escrow(
        self,
        transaction_type: TransactionKind,
        role: str,
        escrow_account_id: int,
        counterparty_account_id: int,
        amount: int,
        options: dict[str, object],
    ) -> PostingResult:
        """Escrow 三种原语的公共构造（腿角色与方向取自冻结蓝图）。"""
        commit = bool(options.pop("commit", True))
        posting = Posting(
            transaction_type=transaction_type,
            entries=blueprint_entries(
                transaction_type,
                {
                    "escrow": (escrow_account_id, amount),
                    role: (counterparty_account_id, amount),
                },
            ),
            idempotency_key=options.pop("idempotency_key", None),  # type: ignore[arg-type]
            reference_type=str(options.pop("reference_type", "")),
            reference_id=str(options.pop("reference_id", "")),
            reason=str(options.pop("reason", "")),
            initiated_by=options.pop("initiated_by", None),  # type: ignore[arg-type]
            metadata=dict(options.pop("metadata", {}) or {}),  # type: ignore[arg-type]
        )
        if options:
            raise PostingRejected(f"unknown options: {sorted(options)}")
        return self.post(posting, commit=commit)

    def _assert_replay_matches(self, existing: LedgerTransaction, posting: Posting) -> None:
        """幂等命中时校验内容一致：同 key 不同内容 = 调用方 bug，必须报错而不是静默复用。"""
        if existing.transaction_type != posting.transaction_type.value:
            raise IdempotencyConflict(
                f"idempotency key {existing.idempotency_key!r} was used for "
                f"{existing.transaction_type}, not {posting.transaction_type.value}"
            )
        if existing.currency != posting.currency.value:
            raise IdempotencyConflict("idempotency key reused with a different currency")
        existing_entries = {
            (int(entry.account_id), entry.direction, int(entry.amount))
            for entry in economy_repo.list_entries(self.db, transaction_id=existing.id)
        }
        expected = {
            (entry.account_id, entry.direction.value, entry.amount) for entry in posting.entries
        }
        if existing_entries != expected:
            raise IdempotencyConflict("idempotency key reused with different posting entries")

    def _validate(self, posting: Posting, *, authority: object | None) -> None:
        if posting.transaction_type in AUTHORITY_KINDS and authority is not AUTHORITY_TOKEN:
            raise AuthorityRequired(posting.transaction_type)

        entries = posting.entries
        if len(entries) < 2:
            raise PostingRejected("at_least_two_legs")
        seen: set[int] = set()
        accounts: dict[int, LedgerAccount] = {}
        for entry in entries:
            if not isinstance(entry.direction, LedgerEntryDirection):
                raise PostingRejected("invalid_direction")
            try:
                validate_amount(entry.amount)
            except EconomyContractError as exc:
                raise PostingRejected("invalid_amount") from exc
            if entry.account_id in seen:
                raise PostingRejected("duplicate_account_leg")  # 自指腿：净额为零的假交易
            seen.add(entry.account_id)
            account = economy_repo.get_account(self.db, entry.account_id)
            if account is None:
                raise PostingRejected("account_not_found", http_status=404)
            if account.status != LedgerAccountStatus.active.value:
                raise PostingRejected("account_not_active")
            if account.currency != posting.currency.value:
                raise PostingRejected("currency_mismatch")
            accounts[entry.account_id] = account

        debits, credits = _totals(entries)
        try:
            assert_balanced_totals(debit_total=debits, credit_total=credits)
        except EconomyContractError as exc:
            raise PostingRejected("posting_unbalanced") from exc

        if posting.transaction_type in _ESCROW_KINDS:
            self._validate_escrow(posting, accounts)

    def _validate_escrow(self, posting: Posting, accounts: dict[int, LedgerAccount]) -> None:
        escrow_accounts = [
            account
            for account in accounts.values()
            if account.kind == LedgerAccountKind.escrow.value
        ]
        if len(escrow_accounts) != 1:
            raise PostingRejected("escrow_kind_requires_exactly_one_escrow_account")
        escrow_id = int(escrow_accounts[0].id)
        try:
            funder_id = economy_repo.escrow_funder_map(self.db).get(escrow_id)
        except EconomyContractError as exc:  # 账本里已有多个出资人：拒绝新过账，交给对账处理
            raise PostingRejected("escrow_funder_conflict") from exc

        if posting.transaction_type is TransactionKind.escrow_fund:
            funder_ids = {
                entry.account_id
                for entry in posting.entries
                if entry.direction is LedgerEntryDirection.credit and entry.account_id != escrow_id
            }
            if funder_id is not None and funder_ids != {funder_id}:
                raise PostingRejected("escrow_already_funded_by_another_actor")
        elif posting.transaction_type is TransactionKind.escrow_refund:
            recipient_ids = {
                entry.account_id
                for entry in posting.entries
                if entry.direction is LedgerEntryDirection.debit and entry.account_id != escrow_id
            }
            if funder_id is not None and recipient_ids != {funder_id}:
                raise PostingRejected("escrow_refund_must_return_to_the_funder")

    def _write(self, posting: Posting) -> PostingResult:
        initiated_by = posting.initiated_by
        initiated_kind: str | None = None
        initiated_ref: int | None = None
        if initiated_by is not None:
            initiated_kind, initiated_ref = economy_repo.actor_columns(initiated_by)
            if initiated_kind == EconomicActorKind.system.value:
                initiated_ref = None  # 系统主体没有宿主 id（account 的 actor_ref 恒为 0）

        now = utcnow()
        transaction = economy_repo.insert_transaction(
            self.db,
            transaction_type=posting.transaction_type.value,
            currency=posting.currency.value,
            status=LedgerTransactionStatus.posted.value,
            idempotency_key=posting.idempotency_key,
            reference_type=posting.reference_type,
            reference_id=posting.reference_id,
            reason=posting.reason,
            initiated_by_kind=initiated_kind,
            initiated_by_ref=initiated_ref,
            occurred_at=now,
            posted_at=now,
            metadata_json=dict(posting.metadata),
        )
        entries = economy_repo.insert_entries(
            self.db,
            [
                {
                    "transaction_id": transaction.id,
                    "account_id": entry.account_id,
                    "direction": entry.direction.value,
                    "amount": entry.amount,
                    "currency": posting.currency.value,
                }
                for entry in posting.entries
            ],
        )
        self._apply_projection(posting, entries)
        return PostingResult(transaction=transaction, entries=tuple(entries), created=True)

    def _apply_projection(self, posting: Posting, entries: list[LedgerEntry]) -> None:
        """投影增量更新 + 资金 CAS（与 Ledger 写入同事务，E28）。"""
        accounts = {
            int(entry.account_id): economy_repo.get_account(self.db, int(entry.account_id))
            for entry in entries
        }
        deltas: dict[int, dict[str, int | None]] = {}
        for entry in entries:
            account = accounts[int(entry.account_id)]
            assert account is not None  # _validate 已确认存在
            own_delta = balance_delta(
                LedgerAccountKind(account.kind),
                LedgerEntryDirection(entry.direction),  # ORM 读回的是 str，必须显式转枚举
                int(entry.amount),
            )
            delta = deltas.setdefault(
                int(entry.account_id),
                {"posted": 0, "available": 0, "reserved": 0, "last_entry_id": None},
            )
            delta["posted"] = int(delta["posted"]) + own_delta
            delta["available"] = int(delta["available"]) + own_delta
            last = delta["last_entry_id"]
            last_ids = [int(last), int(entry.id)] if last is not None else [int(entry.id)]
            delta["last_entry_id"] = max(last_ids)

        self._apply_escrow_attribution(posting, accounts, entries, deltas)

        for account_id, delta in deltas.items():
            account = accounts.get(account_id) or economy_repo.get_account(self.db, account_id)
            if account is None:  # pragma: no cover - 归因账户必然存在（来自账本）
                raise PostingRejected("account_not_found", http_status=404)
            accounts[account_id] = account
            available_delta = int(delta["available"])
            needs_funds = requires_funds(LedgerAccountKind(account.kind))
            required = max(0, -available_delta) if needs_funds else 0
            economy_repo.ensure_projection(
                self.db, account_id=account_id, currency=posting.currency.value
            )
            rowcount = economy_repo.cas_update_projection(
                self.db,
                account_id=account_id,
                posted_delta=int(delta["posted"]),
                available_delta=available_delta,
                reserved_delta=int(delta["reserved"]),
                last_entry_id=delta["last_entry_id"],  # type: ignore[arg-type]
                required_available=required,
            )
            if rowcount == 0:
                raise InsufficientFunds(account_id)

    def _apply_escrow_attribution(
        self,
        posting: Posting,
        accounts: dict[int, LedgerAccount | None],
        entries: list[LedgerEntry],
        deltas: dict[int, dict[str, int | None]],
    ) -> None:
        """Escrow 归因（E30）：锁定份额记在**出资人**的 `reserved` 上。

        - fund：出资人 `reserved += amt`（同时它的可花余额已在自身腿里 −amt ⇒ 净资产不变）；
        - release：出资人 `reserved -= amt` 且 `posted -= amt`（钱真正离开该主体，付给收款人）；
        - refund：出资人 `reserved -= amt` 且 `posted -= amt`（自身腿已 +amt ⇒ 净资产不变）。
        """
        if posting.transaction_type not in _ESCROW_KINDS:
            return
        escrow_ids = [
            account_id
            for account_id, account in accounts.items()
            if account is not None and account.kind == LedgerAccountKind.escrow.value
        ]
        escrow_id = escrow_ids[0]
        escrow_entry = next(entry for entry in entries if int(entry.account_id) == escrow_id)
        amount = int(escrow_entry.amount)

        if posting.transaction_type is TransactionKind.escrow_fund:
            funder_ids = [
                int(entry.account_id)
                for entry in entries
                if LedgerEntryDirection(entry.direction) is LedgerEntryDirection.credit
                and int(entry.account_id) != escrow_id
            ]
            funder_id = funder_ids[0]
            existing_funder = economy_repo.escrow_funder_map(self.db).get(escrow_id)
            if existing_funder is not None and existing_funder != funder_id:
                raise PostingRejected("escrow_already_funded_by_another_actor")
            self._bump(deltas, funder_id, reserved=amount, posted=amount)
            return

        funder_id = economy_repo.escrow_funder_map(self.db).get(escrow_id)
        if funder_id is None:
            raise PostingRejected("escrow_release_or_refund_requires_a_funder")
        # release：钱付给收款人 ⇒ 出资人总资产减少；refund：自身腿已把可花余额加回，
        # 这里减掉锁定份额 ⇒ 总资产净变化 0（资金只是解锁）。
        self._bump(deltas, funder_id, reserved=-amount, posted=-amount)

    @staticmethod
    def _bump(
        deltas: dict[int, dict[str, int | None]],
        account_id: int,
        *,
        reserved: int = 0,
        posted: int = 0,
    ) -> None:
        delta = deltas.setdefault(
            account_id, {"posted": 0, "available": 0, "reserved": 0, "last_entry_id": None}
        )
        delta["posted"] = int(delta["posted"]) + posted
        delta["reserved"] = int(delta["reserved"]) + reserved

    def _publish(self, posting: Posting, result: PostingResult) -> None:
        company_id = self._company_id_of(result)
        entries = [
            {
                "account_id": int(entry.account_id),
                "direction": entry.direction,
                "amount": int(entry.amount),
            }
            for entry in result.entries
        ]
        bus.publish(
            "ledger.transaction_posted",
            {
                "transaction_id": int(result.transaction.id),
                "transaction_type": posting.transaction_type.value,
                "currency": posting.currency.value,
                "reference_type": posting.reference_type,
                "reference_id": posting.reference_id,
                "entries": entries,
                "amount": sum(
                    int(entry.amount)
                    for entry in result.entries
                    if entry.direction == LedgerEntryDirection.debit.value
                ),
            },
            company_id=company_id,
        )

    def _company_id_of(self, result: PostingResult) -> int | None:
        """事件作用域：取交易里第一个公司主体（系统/NPC 交易没有公司作用域）。"""
        for entry in result.entries:
            account = economy_repo.get_account(self.db, int(entry.account_id))
            if account is not None and account.actor_kind == EconomicActorKind.company.value:
                return int(account.actor_ref)
        return None


def _totals(entries: tuple[PostingEntry, ...]) -> tuple[int, int]:
    debits = sum(entry.amount for entry in entries if entry.direction is LedgerEntryDirection.debit)
    credits = sum(
        entry.amount for entry in entries if entry.direction is LedgerEntryDirection.credit
    )
    return debits, credits
