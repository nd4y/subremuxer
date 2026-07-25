from __future__ import annotations

import pytest

from app.filtering import (
    PRESETS,
    CompiledFilter,
    FilterConfig,
    FilterError,
    apply_filter,
    build_regex,
)
from app.formats import Node

NAMES = ["NL-1 LTE", "RU-1 LTE", "DE-1", "FI-1 lte", "RU-2 home"]


def make(**kwargs) -> CompiledFilter:
    protocols = kwargs.pop("protocols", None)
    return CompiledFilter.build(FilterConfig.from_dict(kwargs), protocols)


def kept(compiled: CompiledFilter, names=NAMES) -> list[str]:
    return [name for name in names if compiled.check_name(name)[0]]


def test_no_conditions_keeps_everything():
    assert kept(make()) == NAMES


def test_contains_is_case_insensitive_by_default():
    compiled = make(conditions=[{"op": "contains", "value": "LTE"}])
    assert kept(compiled) == ["NL-1 LTE", "RU-1 LTE", "FI-1 lte"]


def test_case_sensitive_contains():
    compiled = make(conditions=[{"op": "contains", "value": "LTE"}], case_sensitive=True)
    assert kept(compiled) == ["NL-1 LTE", "RU-1 LTE"]


def test_not_contains():
    compiled = make(conditions=[{"op": "not_contains", "value": "RU"}])
    assert kept(compiled) == ["NL-1 LTE", "DE-1", "FI-1 lte"]


def test_contains_and_not_contains_together():
    compiled = make(
        conditions=[
            {"op": "contains", "value": "LTE"},
            {"op": "not_contains", "value": "RU"},
        ]
    )
    assert kept(compiled) == ["NL-1 LTE", "FI-1 lte"]


def test_any_mode_is_a_union():
    compiled = make(
        match="any",
        conditions=[
            {"op": "contains", "value": "DE"},
            {"op": "contains", "value": "FI"},
        ],
    )
    assert kept(compiled) == ["DE-1", "FI-1 lte"]


@pytest.mark.parametrize(
    ("op", "value", "expected"),
    [
        ("starts_with", "RU", ["RU-1 LTE", "RU-2 home"]),
        ("not_starts_with", "RU", ["NL-1 LTE", "DE-1", "FI-1 lte"]),
        ("ends_with", "LTE", ["NL-1 LTE", "RU-1 LTE", "FI-1 lte"]),
        ("not_ends_with", "LTE", ["DE-1", "RU-2 home"]),
        ("equals", "DE-1", ["DE-1"]),
        ("not_equals", "DE-1", ["NL-1 LTE", "RU-1 LTE", "FI-1 lte", "RU-2 home"]),
        ("regex", r"^(NL|DE)", ["NL-1 LTE", "DE-1"]),
        ("not_regex", r"^\w\w-1", ["RU-2 home"]),
    ],
)
def test_every_operator(op, value, expected):
    compiled = make(conditions=[{"op": op, "value": value}])
    assert kept(compiled) == expected


def test_generated_regex_is_what_actually_runs():
    config = FilterConfig.from_dict(
        {
            "conditions": [
                {"op": "contains", "value": "LTE"},
                {"op": "not_contains", "value": "RU"},
            ]
        }
    )
    regex = build_regex(config)
    assert regex == r"(?i)^(?=.*LTE)(?!.*RU).*$"

    import re

    compiled = CompiledFilter.build(config)
    pattern = re.compile(regex)
    for name in NAMES:
        assert bool(pattern.search(name)) is compiled.check_name(name)[0]


def test_raw_mode_include_and_exclude():
    compiled = make(mode="raw", include_regex="LTE", exclude_regex="^RU")
    assert kept(compiled) == ["NL-1 LTE", "FI-1 lte"]


def test_raw_mode_reports_which_side_rejected():
    compiled = make(mode="raw", include_regex="LTE", exclude_regex="^RU")
    assert compiled.check_name("DE-1")[1] == "include_regex"
    assert compiled.check_name("RU-1 LTE")[1] == "exclude_regex"


def test_broken_regex_is_rejected_with_a_readable_message():
    with pytest.raises(FilterError) as excinfo:
        make(mode="raw", include_regex="([unclosed")
    assert "регуляр" in str(excinfo.value)


def test_unknown_operator_is_rejected():
    with pytest.raises(FilterError):
        FilterConfig.from_dict({"conditions": [{"op": "sorcery", "value": "x"}]})


def test_empty_condition_value_is_rejected():
    with pytest.raises(FilterError):
        FilterConfig.from_dict({"conditions": [{"op": "contains", "value": ""}]})


def test_too_many_conditions_rejected():
    with pytest.raises(FilterError):
        FilterConfig.from_dict(
            {"conditions": [{"op": "contains", "value": "x"} for _ in range(40)]}
        )


# ------------------------------------------------------------------ protocols


def nodes() -> list[Node]:
    return [
        Node(index=0, name="NL-1 LTE", protocol="vless"),
        Node(index=1, name="RU-1 LTE", protocol="trojan"),
        Node(index=2, name="DE-1", protocol="hysteria2"),
    ]


def test_protocol_filter_alone():
    compiled = make(protocols=["vless", "trojan"])
    decisions = apply_filter(nodes(), compiled)
    assert [d.kept for d in decisions] == [True, True, False]
    assert decisions[2].reason == "protocol"


def test_protocol_and_name_filters_combine():
    compiled = make(protocols=["vless", "trojan"], conditions=[{"op": "not_contains", "value": "RU"}])
    decisions = apply_filter(nodes(), compiled)
    assert [d.kept for d in decisions] == [True, False, False]
    assert decisions[1].reason == "name"
    assert decisions[2].reason == "protocol"


def test_protocol_aliases_are_normalised():
    compiled = make(protocols=["ss"])
    assert compiled.allowed_protocols == frozenset({"shadowsocks"})


def test_rejection_detail_names_the_failing_condition():
    compiled = make(
        conditions=[
            {"op": "contains", "value": "LTE"},
            {"op": "not_contains", "value": "RU"},
        ]
    )
    _, _, detail = compiled.check_name("RU-1 LTE")
    assert "не содержит" in detail
    assert "RU" in detail


# -------------------------------------------------------------------- presets


@pytest.mark.parametrize("preset", PRESETS, ids=[preset["id"] for preset in PRESETS])
def test_every_preset_compiles(preset):
    config = FilterConfig.from_dict(
        {"match": preset["match"], "conditions": preset["conditions"]}
    )
    compiled = CompiledFilter.build(config)
    assert compiled.builder_regex
    # And it must be usable against a real name without blowing up.
    compiled.check_name("NL-1 LTE")


def test_preset_lte_not_ru_matches_its_description():
    preset = next(p for p in PRESETS if p["id"] == "lte_not_ru")
    compiled = CompiledFilter.build(
        FilterConfig.from_dict({"match": preset["match"], "conditions": preset["conditions"]})
    )
    assert kept(compiled) == ["NL-1 LTE", "FI-1 lte"]
