"""The admin UI previews the generated regexp locally so typing stays instant.

That means the same mapping exists twice — once in Python, once in JS. This test
fails the moment the two drift apart.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.filtering import CONDITION_OPS, Condition, condition_fragment

APP_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "app.js"


def js_fragments() -> dict[str, str]:
    """Pull the `case "op": return \\`...\\`;` pairs out of buildRegexLocally."""
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("function buildRegexLocally")
    end = source.index("/* ---", start)
    body = source[start:end]
    pairs = re.findall(r'case "(\w+)":\s*\n\s*return `([^`]*)`;', body)
    # Normalise JS interpolation to Python's brace form.
    return {
        op: template.replace("${literal}", "{literal}").replace("${raw}", "{raw}")
        for op, template in pairs
    }


def python_fragment(op: str) -> str:
    """Render the Python fragment with recognisable placeholders."""
    fragment = condition_fragment(Condition(op=op, value="VALUE"))
    return fragment.replace("(?:VALUE)", "{raw}").replace("VALUE", "{literal}")


def test_every_operator_is_implemented_in_the_ui():
    assert set(js_fragments()) == set(CONDITION_OPS)


@pytest.mark.parametrize("op", sorted(CONDITION_OPS))
def test_fragment_templates_match(op):
    assert js_fragments()[op] == python_fragment(op)


def test_the_ui_uses_the_same_case_insensitivity_flag():
    source = APP_JS.read_text(encoding="utf-8")
    assert 'filter.case_sensitive ? "" : "(?i)"' in source


def test_the_ui_joins_conditions_the_same_way():
    source = APP_JS.read_text(encoding="utf-8")
    assert "`${flag}^${fragments.join(\"\")}.*$`" in source
    assert '`${flag}^(?:${fragments.map((part) => `(?:${part}.*)`).join("|")})$`' in source
