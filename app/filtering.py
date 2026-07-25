"""Node filtering: the regexp builder, its compiler, and the protocol filter."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .formats import Node, canonical_protocol

MAX_REGEX_LENGTH = 1000
MAX_CONDITIONS = 32

ConditionOp = Literal[
    "contains",
    "not_contains",
    "starts_with",
    "not_starts_with",
    "ends_with",
    "not_ends_with",
    "equals",
    "not_equals",
    "regex",
    "not_regex",
]

#: Labels for the admin UI, and the source of truth for which ops exist.
CONDITION_OPS: dict[str, str] = {
    "contains": "содержит",
    "not_contains": "не содержит",
    "starts_with": "начинается с",
    "not_starts_with": "не начинается с",
    "ends_with": "заканчивается на",
    "not_ends_with": "не заканчивается на",
    "equals": "равно",
    "not_equals": "не равно",
    "regex": "подходит под regexp",
    "not_regex": "не подходит под regexp",
}

#: Ready-made condition sets offered as one-click presets in the UI.
PRESETS: list[dict[str, Any]] = [
    {
        "id": "contains_lte",
        "title": "Всё, что содержит LTE",
        "description": "Оставить только мобильные каналы",
        "match": "all",
        "conditions": [{"op": "contains", "value": "LTE"}],
    },
    {
        "id": "not_contains_ru",
        "title": "Всё, что НЕ содержит RU",
        "description": "Убрать российские локации",
        "match": "all",
        "conditions": [{"op": "not_contains", "value": "RU"}],
    },
    {
        "id": "lte_not_ru",
        "title": "Содержит LTE и не содержит RU",
        "description": "Мобильные каналы за пределами РФ",
        "match": "all",
        "conditions": [
            {"op": "contains", "value": "LTE"},
            {"op": "not_contains", "value": "RU"},
        ],
    },
    {
        "id": "any_of",
        "title": "Любая из стран: NL, DE, FI",
        "description": "Несколько локаций через ИЛИ",
        "match": "any",
        "conditions": [
            {"op": "contains", "value": "NL"},
            {"op": "contains", "value": "DE"},
            {"op": "contains", "value": "FI"},
        ],
    },
    {
        "id": "no_info_entries",
        "title": "Убрать служебные записи",
        "description": "Скрыть строки вида «Трафик», «Осталось», «Истекает»",
        "match": "all",
        "conditions": [
            {"op": "not_regex", "value": "траф|осталось|истек|expire|remaining|бонус"},
        ],
    },
]


class FilterError(ValueError):
    """The stored filter cannot be compiled — bad regexp, or too big."""


@dataclass(slots=True)
class Condition:
    op: str
    value: str

    @classmethod
    def from_dict(cls, data: Any) -> Condition:
        if not isinstance(data, dict):
            raise FilterError("условие должно быть объектом")
        op = str(data.get("op", "contains"))
        if op not in CONDITION_OPS:
            raise FilterError(f"неизвестная операция: {op}")
        value = str(data.get("value", ""))
        if not value:
            raise FilterError("пустое значение в условии")
        if len(value) > MAX_REGEX_LENGTH:
            raise FilterError("слишком длинное значение условия")
        return cls(op=op, value=value)

    def as_dict(self) -> dict[str, str]:
        return {"op": self.op, "value": self.value}


@dataclass(slots=True)
class FilterConfig:
    mode: Literal["builder", "raw"] = "builder"
    case_sensitive: bool = False
    match: Literal["all", "any"] = "all"
    conditions: list[Condition] = field(default_factory=list)
    include_regex: str = ""
    exclude_regex: str = ""

    @classmethod
    def from_dict(cls, data: Any) -> FilterConfig:
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise FilterError("фильтр должен быть объектом")
        mode = str(data.get("mode", "builder"))
        if mode not in {"builder", "raw"}:
            raise FilterError(f"неизвестный режим фильтра: {mode}")
        match = str(data.get("match", "all"))
        if match not in {"all", "any"}:
            raise FilterError(f"неизвестный режим объединения условий: {match}")
        raw_conditions = data.get("conditions") or []
        if not isinstance(raw_conditions, list):
            raise FilterError("conditions должен быть списком")
        if len(raw_conditions) > MAX_CONDITIONS:
            raise FilterError(f"слишком много условий (максимум {MAX_CONDITIONS})")
        include_regex = str(data.get("include_regex", "") or "")
        exclude_regex = str(data.get("exclude_regex", "") or "")
        for value in (include_regex, exclude_regex):
            if len(value) > MAX_REGEX_LENGTH:
                raise FilterError("слишком длинное регулярное выражение")
        return cls(
            mode=mode,  # type: ignore[arg-type]
            case_sensitive=bool(data.get("case_sensitive", False)),
            match=match,  # type: ignore[arg-type]
            conditions=[Condition.from_dict(item) for item in raw_conditions],
            include_regex=include_regex,
            exclude_regex=exclude_regex,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "case_sensitive": self.case_sensitive,
            "match": self.match,
            "conditions": [c.as_dict() for c in self.conditions],
            "include_regex": self.include_regex,
            "exclude_regex": self.exclude_regex,
        }

    def is_empty(self) -> bool:
        if self.mode == "builder":
            return not self.conditions
        return not self.include_regex and not self.exclude_regex


# --------------------------------------------------------------------- compile


def condition_fragment(condition: Condition) -> str:
    """Turn one condition into a zero-width assertion anchored at the string start."""
    op = condition.op
    literal = re.escape(condition.value)
    raw = f"(?:{condition.value})"

    match op:
        case "contains":
            return f"(?=.*{literal})"
        case "not_contains":
            return f"(?!.*{literal})"
        case "starts_with":
            return f"(?={literal})"
        case "not_starts_with":
            return f"(?!{literal})"
        case "ends_with":
            return f"(?=.*{literal}$)"
        case "not_ends_with":
            return f"(?!.*{literal}$)"
        case "equals":
            return f"(?={literal}$)"
        case "not_equals":
            return f"(?!{literal}$)"
        case "regex":
            return f"(?=.*{raw})"
        case "not_regex":
            return f"(?!.*{raw})"
    raise FilterError(f"неизвестная операция: {op}")


def build_regex(config: FilterConfig) -> str:
    """Compile the builder's conditions into a single regexp.

    This exact string is what the UI shows *and* what the filter runs, so there
    is never a gap between the preview and the behaviour.
    """
    if not config.conditions:
        return ""
    fragments = [condition_fragment(c) for c in config.conditions]
    flag = "" if config.case_sensitive else "(?i)"
    if config.match == "all":
        return f"{flag}^{''.join(fragments)}.*$"
    alternatives = "|".join(f"(?:{fragment}.*)" for fragment in fragments)
    return f"{flag}^(?:{alternatives})$"


def _compile(pattern: str, case_sensitive: bool) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        raise FilterError(f"некорректное регулярное выражение: {exc}") from exc


@dataclass(slots=True)
class CompiledFilter:
    """A filter ready to be applied to nodes."""

    config: FilterConfig
    allowed_protocols: frozenset[str]
    builder_regex: str = ""
    _builder: re.Pattern[str] | None = None
    _include: re.Pattern[str] | None = None
    _exclude: re.Pattern[str] | None = None
    _per_condition: list[tuple[Condition, re.Pattern[str]]] = field(default_factory=list)

    @classmethod
    def build(cls, config: FilterConfig, protocols: list[str] | None = None) -> CompiledFilter:
        allowed = frozenset(canonical_protocol(p) for p in (protocols or []))
        compiled = cls(config=config, allowed_protocols=allowed)
        if config.mode == "builder":
            compiled.builder_regex = build_regex(config)
            if compiled.builder_regex:
                # The flag is inline, so compile without a redundant IGNORECASE.
                compiled._builder = _compile(compiled.builder_regex, case_sensitive=True)
            flag = "" if config.case_sensitive else "(?i)"
            compiled._per_condition = [
                (c, _compile(f"{flag}^{condition_fragment(c)}.*$", case_sensitive=True))
                for c in config.conditions
            ]
        else:
            if config.include_regex:
                compiled._include = _compile(config.include_regex, config.case_sensitive)
            if config.exclude_regex:
                compiled._exclude = _compile(config.exclude_regex, config.case_sensitive)
        return compiled

    # ------------------------------------------------------------------ apply

    def check_name(self, name: str) -> tuple[bool, str, str]:
        """Return ``(kept, reason, detail)`` for a node name."""
        if self.config.mode == "builder":
            if self._builder is None:
                return True, "ok", ""
            if self._builder.search(name):
                return True, "ok", ""
            failed = [
                CONDITION_OPS[c.op] + " «" + c.value + "»"
                for c, pattern in self._per_condition
                if not pattern.search(name)
            ]
            if self.config.match == "any":
                detail = "не выполнено ни одно условие"
            elif failed:
                detail = "не выполнено: " + "; ".join(failed)
            else:
                detail = "не подошло под условия"
            return False, "name", detail

        if self._include is not None and not self._include.search(name):
            return False, "include_regex", f"не подходит под include: {self.config.include_regex}"
        if self._exclude is not None and self._exclude.search(name):
            return False, "exclude_regex", f"попало под exclude: {self.config.exclude_regex}"
        return True, "ok", ""

    def check_protocol(self, protocol: str) -> tuple[bool, str, str]:
        if not self.allowed_protocols:
            return True, "ok", ""
        if protocol in self.allowed_protocols:
            return True, "ok", ""
        return False, "protocol", f"протокол {protocol} не разрешён"


@dataclass(slots=True)
class Decision:
    node: Node
    kept: bool
    reason: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        data = self.node.as_dict()
        data.update({"kept": self.kept, "reason": self.reason, "detail": self.detail})
        return data


def apply_filter(nodes: list[Node], compiled: CompiledFilter) -> list[Decision]:
    """Evaluate every node, recording why each one was kept or dropped."""
    decisions: list[Decision] = []
    for node in nodes:
        kept, reason, detail = compiled.check_protocol(node.protocol)
        if kept:
            kept, reason, detail = compiled.check_name(node.name)
        decisions.append(Decision(node=node, kept=kept, reason=reason, detail=detail))
    return decisions
