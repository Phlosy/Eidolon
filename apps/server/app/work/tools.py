"""M2.3 管理工具面的**机制层**（用户拍板：Read Shared / Write Internal，T1–T12）。

这个模块只有机械部分：`ToolSpec` / `ToolRegistry` / 参数校验 / 自主等级门禁。
**没有任何业务逻辑** —— 读工具在 `tool_reads.py`、写工具在 `tool_writes.py`、
执行与审计在 `tool_executor.py`。

四条纪律（用户拍板）：

1. **Tool 不拥有业务真相**（T1）：handler 只是既有 service / query 的适配器；
   真相永远在领域表里，工具既不建表也不缓存。
2. **HTTP API 与 Agent Tool 共用同一个应用/领域服务**（T2）：
   `UI rules == Agent rules`。所以读工具必须调用既有读面，写工具必须调用既有 service。
3. **不存在玩家面的通用 `/tools` 写路由**（T3）：写工具**只有**内部执行面 +
   默认关闭的调试 CLI；人类管理动作继续走各领域自己的正式 API。
4. **Transport 不代表信任**（T4）：内部面照样做完整 Authority 校验；
   写工具的 spec **必须**同时声明 `required_authority` 与 `authority_target`
   （缺一个就在注册时炸，而不是运行时"忘了校验"）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.models.enums import (
    AuthorityKind,
    AutonomyLevel,
    DecisionSemantics,
    ToolSideEffect,
    ToolTransport,
)
from app.work import contracts as C


def json_safe(value):
    """把领域返回值转成**可持久化**的形状（审计与事件都要写 JSON 列）。

    为什么需要：领域层返回的是**事实**，里面可能带 datetime / Decimal / UUID /
    set 这类"真话但 JSON 不认"的值。让审计层炸掉等于丢掉整条执行事实
    （M2.10 黄金路径实测踩到：`list_company_people` 带 `created_at` 直接把
    `tool_audits` 的 INSERT 打挂）。所以这里统一**降级成字符串**，
    并保持结构（dict/list/tuple 递归；未知类型 `str()`）。
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, "value") and hasattr(value, "name"):  # StrEnum / Enum
        return json_safe(value.value)
    return str(value)


class ToolError(ValueError):
    """工具契约被违反（未注册 / 参数非法 / 缺授权声明 / 自主等级不允许）。"""


# ---------------------------------------------------------------------------
# 参数校验（JSON Schema 的一个**明确子集**，不引第三方依赖）
# ---------------------------------------------------------------------------

_SCALAR_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


def validate_arguments(schema: dict, args: dict, *, tool: str) -> None:
    """按 spec 里声明的 schema 校验参数（子集：type / required / enum / items）。

    刻意**不**引进 `jsonschema`：这里只需要校验"调用方给的东西形状对不对"，
    引一个大依赖换这点校验是负收益（仓库也没有这个依赖）。
    未声明的属性默认拒绝（`additionalProperties` 语义固定为 false）—— 工具参数是
    给模型看的契约，多出来的键往往是幻觉，静默忽略比报错危险得多。
    """
    if not isinstance(args, dict):
        raise ToolError(f"{tool}: arguments must be an object")
    properties: dict = schema.get("properties") or {}
    # 身份字段先判：它必须给出"身份只能由上下文注入"这条**明确**的拒绝理由，
    # 而不是被"未知参数"顺手挡掉（那会让人以为换个键名就能传身份）。
    forbidden = sorted(set(args) & C.FORBIDDEN_TOOL_ARGUMENT_KEYS)
    if forbidden:
        raise ToolError(
            f"{tool}: actor identity must be injected by context, not passed as arguments "
            f"(rejected identity arguments: {forbidden})"
        )
    for key in args:
        if key not in properties:
            raise ToolError(f"{tool}: unknown argument {key!r}")
    for key in schema.get("required") or []:
        if key not in args or args[key] is None:
            raise ToolError(f"{tool}: missing required argument {key!r}")
    for key, value in args.items():
        if value is None:
            continue
        expected = properties[key].get("type")
        if expected is None:
            continue
        allowed = _SCALAR_TYPES.get(str(expected))
        if allowed is None:
            raise ToolError(f"{tool}: unsupported schema type {expected!r} for {key!r}")
        if isinstance(value, bool) and expected in {"integer", "number"}:
            raise ToolError(f"{tool}: {key!r} must be {expected}, got boolean")
        if not isinstance(value, allowed):
            raise ToolError(f"{tool}: {key!r} must be {expected}")
        enum_values = properties[key].get("enum")
        if enum_values is not None and value not in enum_values:
            raise ToolError(f"{tool}: {key!r} must be one of {list(enum_values)}")
        if expected == "array" and properties[key].get("items"):
            item_type = _SCALAR_TYPES.get(str(properties[key]["items"].get("type")))
            if item_type is not None:
                for item in value:
                    if not isinstance(item, item_type):
                        raise ToolError(
                            f"{tool}: {key!r} items must be {properties[key]['items'].get('type')}"
                        )


def object_schema(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required)}


# ---------------------------------------------------------------------------
# Actor 上下文：身份由系统注入，不由模型参数提供（T5）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCallContext:
    """一次工具调用的**执行上下文**（身份 + 作用域 + 出处）。

    构造入口只有两个（`tool_executor.context_for_work_session` /
    `context_for_employee`）：身份永远来自**库里的事实**（任职、会话、公司），
    绝不来自模型给的参数。
    """

    employee_id: int
    company_id: int
    person_id: int | None = None
    #: 出处：从哪来（审计用）
    origin: str = "internal"
    work_session_id: int | None = None
    task_id: int | None = None
    project_id: int | None = None
    runtime_type: str = ""

    def as_dict(self) -> dict:
        return {
            "employee_id": self.employee_id,
            "person_id": self.person_id,
            "company_id": self.company_id,
            "origin": self.origin,
            "work_session_id": self.work_session_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "runtime_type": self.runtime_type,
        }


# ---------------------------------------------------------------------------
# Spec / Registry
# ---------------------------------------------------------------------------

Handler = Callable[[Any, ToolCallContext, dict], dict]
TargetFn = Callable[[Any, ToolCallContext, dict], C.AuthorityTarget]


@dataclass(frozen=True)
class ToolSpec:
    """一个管理工具的自描述（用户拍板 §4 的六个字段 + 校验所需的两处）。

    `required_authority` / `authority_target` 对**非读**工具是强制项：
    注册时校验，缺一个直接 `ToolError` —— 让"忘了做授权校验"在启动时暴露，
    而不是等到某次调用悄悄放行（T4）。
    """

    name: str
    description: str
    side_effect: ToolSideEffect
    input_schema: dict
    output_schema: dict
    handler: Handler
    required_authority: AuthorityKind | None = None
    authority_target: TargetFn | None = None
    #: 参数里允许出现的、需要额外授权的"金额"键（只有 high_impact 才可能有）
    amount_arg: str | None = None
    #: 与「管理决策」的关系（M2.4，用户拍板 §6）：none | optional | required。
    #: 写动作**必须**显式声明 —— 不允许"不知道自己算不算决策"。
    decision_semantics: DecisionSemantics = DecisionSemantics.none

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ToolError("tool name must not be empty")
        if not self.description.strip():
            raise ToolError(f"{self.name}: description must not be empty")
        if self.side_effect is ToolSideEffect.read:
            if self.required_authority is not None or self.authority_target is not None:
                raise ToolError(
                    f"{self.name}: read tools must not declare authority "
                    "(facts inside the company scope need no management authority)"
                )
            if self.decision_semantics is not DecisionSemantics.none:
                raise ToolError(
                    f"{self.name}: read tools must declare decision_semantics=none "
                    "(a fact query is not a management decision, DR7)"
                )
            return
        if self.decision_semantics is DecisionSemantics.none:
            raise ToolError(
                f"{self.name}: write tools must declare decision_semantics "
                "as optional or required — 写动作不允许静默地'不知道自己算不算决策'（DR7）"
            )
        if self.required_authority is None:
            raise ToolError(
                f"{self.name}: {self.side_effect.value} tools must declare required_authority (T4)"
            )
        if self.authority_target is None:
            raise ToolError(
                f"{self.name}: {self.side_effect.value} tools must declare authority_target (T4)"
            )
        if self.side_effect is ToolSideEffect.high_impact and self.amount_arg is None:
            raise ToolError(
                f"{self.name}: high_impact tools must declare amount_arg "
                "(金额类动作必须有可校验的额度入参)"
            )

    @property
    def is_read(self) -> bool:
        return self.side_effect is ToolSideEffect.read

    @property
    def autonomy(self) -> AutonomyLevel:
        """本副作用等级当前的自主等级（M2.3 的冻结边界，见 contracts）。"""
        return C.AUTONOMY_BY_SIDE_EFFECT[self.side_effect]

    @property
    def transports(self) -> frozenset[ToolTransport]:
        """写工具**只有内部面**（T3）；读工具是共享能力（由既有领域读面服务 UI）。"""
        if self.is_read:
            return frozenset({ToolTransport.internal, ToolTransport.debug_cli})
        if self.side_effect is ToolSideEffect.write:
            return frozenset({ToolTransport.internal, ToolTransport.debug_cli})
        return frozenset({ToolTransport.internal})

    def as_dict(self) -> dict:
        """对外描述（给 Agent Runtime 做工具清单用；**不含 handler**）。"""
        return {
            "name": self.name,
            "description": self.description,
            "side_effect": self.side_effect.value,
            "autonomy": self.autonomy.value,
            "required_authority": (
                self.required_authority.value if self.required_authority else None
            ),
            "decision_semantics": self.decision_semantics.value,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "transports": sorted(t.value for t in self.transports),
        }


@dataclass
class ToolRegistry:
    """工具注册表（**数据**，不是控制流；每个工具必须是可枚举的自描述条目）。"""

    _specs: dict[str, ToolSpec] = field(default_factory=dict)

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._specs:
            raise ToolError(f"tool already registered: {spec.name}")
        self._specs[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise ToolError(f"unknown tool: {name}")
        return spec

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._specs[name] for name in self.names())

    def catalog(self) -> list[dict]:
        """给 Runtime / 调试面用的公开清单."""
        return [spec.as_dict() for spec in self.specs()]

    def by_side_effect(self, side_effect: ToolSideEffect) -> tuple[ToolSpec, ...]:
        return tuple(spec for spec in self.specs() if spec.side_effect is side_effect)


def assert_registry_is_sound(registry: ToolRegistry) -> None:
    """注册表的整体校验（启动/测试时调；把"漏声明"变成一次明确的失败）。"""
    for spec in registry.specs():
        if spec.side_effect is ToolSideEffect.high_impact:
            raise ToolError(
                f"{spec.name}: M2.3 不注册 high_impact 工具 "
                "(用户拍板：不为完整列表写空业务；等级与自主边界已冻结)"
            )
        for key in spec.input_schema.get("properties") or {}:
            if key in C.FORBIDDEN_TOOL_ARGUMENT_KEYS:
                raise ToolError(
                    f"{spec.name}: 参数 {key!r} 属于身份字段 —— 身份只能由上下文注入 (T5)"
                )
