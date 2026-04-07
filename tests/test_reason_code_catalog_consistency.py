from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

NS = {"p": "urn:pxml:v1"}


def _catalog_codes(path: Path) -> set[str]:
    tree = etree.parse(str(path))
    values = tree.xpath(
        "/p:pxml/p:payload/p:reasons/p:reason/p:code/text()", namespaces=NS
    )
    return {value.strip() for value in values if value and value.strip()}


def _catalog_categories(path: Path) -> set[str]:
    tree = etree.parse(str(path))
    values = tree.xpath(
        "/p:pxml/p:payload/p:reasons/p:reason/p:category/text()", namespaces=NS
    )
    return {value.strip() for value in values if value and value.strip()}


def test_reason_code_catalog_passes_validator(repo_root: Path, run_python) -> None:
    catalog_path = repo_root / "instructions" / "reason_code_catalog.pxml"
    result = run_python("scripts/pxml_validator.py", catalog_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_reason_code_catalog_covers_release_candidate_rc_codes(repo_root: Path) -> None:
    catalog_path = repo_root / "instructions" / "reason_code_catalog.pxml"
    catalog_codes = _catalog_codes(catalog_path)

    release_candidate_script = repo_root / "scripts" / "release_candidate_check.py"
    script_text = release_candidate_script.read_text(encoding="utf-8")
    rc_reason_tokens = {
        match.group(1) for match in re.finditer(r"(rc_[a-z0-9_]+):", script_text)
    }
    assert rc_reason_tokens, "expected at least one rc_ reason token"
    assert rc_reason_tokens.issubset(catalog_codes)


def test_reason_code_catalog_has_required_category_coverage(repo_root: Path) -> None:
    catalog_path = repo_root / "instructions" / "reason_code_catalog.pxml"
    categories = _catalog_categories(catalog_path)
    assert categories == {
        "rc",
        "implementer",
        "verifier",
        "coordinator",
        "planner",
        "system",
    }
