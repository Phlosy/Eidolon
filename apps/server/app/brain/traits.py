"""BrainTraits：`EmployeeBrain.traits` 的受校验读视图（设计契约 §4）。

traits 是唯一权威来源；`EmployeeBrain.curiosity` 继续作为兼容镜像存在（只读迁移来源 +
老调用方兼容）。双写必须走本模块，避免出现两个真源。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from typing import Any

from app.brain.registry import TRAIT_REGISTRY, TraitSpec, is_registered

logger = logging.getLogger(__name__)


class UnknownTrait(KeyError):
    """未注册 trait —— 写侧必须 4xx，读侧回落并告警。"""


class TraitOutOfRange(ValueError):
    """trait 越界（域由 TraitSpec.domain 声明）。"""


class BrainTraits(Mapping[str, float]):
    """只读的 trait 视图：键集封闭、值域封闭、永不抛 KeyError。"""

    SCHEMA_VERSION = 1

    def __init__(
        self, values: Mapping[str, float] | None = None, *, source: str = "traits"
    ) -> None:
        resolved: dict[str, float] = {}
        for spec in TRAIT_REGISTRY.values():
            if values is not None and spec.key in values:
                resolved[spec.key] = spec.clamp(float(values[spec.key]))
            else:
                resolved[spec.key] = spec.default
        self._values = resolved
        self._source = source

    # ---- 构造 ----

    @classmethod
    def from_brain(cls, brain: Any) -> BrainTraits:
        """brain 为 None / 字段缺失时回落注册表默认值。"""
        if brain is None:
            return cls({}, source="missing_brain")
        raw = dict(getattr(brain, "traits", None) or {})
        version = raw.pop("schema_version", None)
        if version is not None and isinstance(version, int) and version > cls.SCHEMA_VERSION:
            logger.warning(
                "traits schema_version=%s 高于当前支持版本 %s，缺失键回落默认值",
                version,
                cls.SCHEMA_VERSION,
            )
        values: dict[str, float] = {}
        for key, value in raw.items():
            if not is_registered(key):
                logger.warning("忽略未注册的人格特质：%s", key)
                continue
            try:
                values[key] = _coerce(key, value)
            except (UnknownTrait, TraitOutOfRange, TypeError, ValueError) as exc:
                logger.warning("人格特质 %s 无效（%s），回落默认值", key, exc)
        if "curiosity" not in values:
            legacy = getattr(brain, "curiosity", None)  # 迁移回填后仍保留的兼容镜像
            if legacy is not None:
                try:
                    values["curiosity"] = _coerce("curiosity", legacy)
                except (UnknownTrait, TraitOutOfRange, TypeError, ValueError):
                    logger.warning("curiosity 镜像列值非法，回落默认值")
        return cls(values, source="traits" if raw else "defaults")

    @classmethod
    def build(cls, raw: Mapping[str, Any] | None) -> dict[str, Any]:
        """写侧入口：校验并返回可直接落库的 traits。未注册 / 越界 / 非数值都抛错。

        `schema_version` 由本模块控制，调用方传什么都忽略（决策 1）。
        """
        return cls(_validate(raw)).to_json()

    @classmethod
    def merge(
        cls, existing: Mapping[str, Any] | None, patch: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        """部分更新：先读现有 traits（走同一套默认/回落规则），再覆盖 patch。"""
        current = cls.coerce(existing)
        payload = current.snapshot()
        payload.update(_validate(patch))
        return cls(payload).to_json()

    @classmethod
    def coerce(cls, raw: Mapping[str, Any] | None) -> BrainTraits:
        """宽松读视图：非法/未注册键忽略并告警，缺失键用默认值。"""
        values: dict[str, float] = {}
        for key, value in (raw or {}).items():
            if key == "schema_version":
                continue
            try:
                values[key] = _coerce(key, value)
            except (UnknownTrait, TraitOutOfRange, TypeError, ValueError) as exc:
                logger.warning("忽略无效的人格特质 %s：%s", key, exc)
        return cls(values)

    # ---- Mapping ----

    def __getitem__(self, key: str) -> float:
        return _coerce(key, self._values.get(key, TRAIT_REGISTRY[key].default))

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    @property
    def source(self) -> str:
        return self._source

    def snapshot(self) -> dict[str, float]:
        """写进 metadata_json / 投影文件用；只含 trait 值，不含任何置信度语义。"""
        return dict(self._values)

    def band(self, key: str, boundaries: tuple[float, float] = (0.30, 0.70)) -> str:
        low, high = boundaries
        value = self[key]
        if value < low:
            return "low"
        if value < high:
            return "moderate"
        return "high"

    def to_json(self) -> dict[str, Any]:
        """可直接落库的 traits（带 schema_version）。"""
        payload: dict[str, Any] = {"schema_version": self.SCHEMA_VERSION}
        payload.update(self._values)
        return payload


def _validate(raw: Mapping[str, Any] | None) -> dict[str, float]:
    """严格校验：返回归一化值；未注册 trait 与越界值都抛错（由 API 层转 422）。"""
    normalized: dict[str, float] = {}
    for key, value in (raw or {}).items():
        if key == "schema_version":
            continue
        normalized[key] = _coerce(key, value)
    return normalized


def _coerce(key: str, value: Any) -> float:
    spec: TraitSpec | None = TRAIT_REGISTRY.get(key)
    if spec is None:
        raise UnknownTrait(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TraitOutOfRange(f"{key} 需要数值，收到 {type(value).__name__}")
    number = float(value)
    if not spec.in_domain(number):
        raise TraitOutOfRange(f"{key}={number} 超出域 {spec.domain}")
    return number


def write_traits_to_brain(brain: Any, traits: Mapping[str, Any]) -> dict[str, float]:
    """校验 + 双写（traits 为权威、legacy 列同步），返回归一化后的 traits。

    越界 / 未注册在这里抛错；调用方（Pydantic 校验器、API 层）负责转成 422。
    `schema_version` 由本模块掌控，调用方传回则忽略（允许 round-trip `BrainTraits.build` 的结果）。
    """
    normalized = _validate(traits)
    payload: dict[str, Any] = {"schema_version": BrainTraits.SCHEMA_VERSION}
    payload.update(normalized)
    brain.traits = payload
    legacy = TRAIT_REGISTRY.get("curiosity")
    if legacy is not None and "curiosity" in normalized:
        brain.curiosity = normalized["curiosity"]
    return normalized
