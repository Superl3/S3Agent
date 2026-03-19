from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import smoke_runner as sr


def test_failure_memory_policy() -> None:
    result = sr.check_failure_memory_policy(sr.REPO_ROOT)
    assert result["status"] == "pass", result.get("issues")


def test_failure_memory_has_seed_rules() -> None:
    path = sr.REPO_ROOT / "memory" / "failure_rules.md"
    entries, errors = sr.parse_failure_rules(path)
    assert not errors, errors
    assert len(entries) >= 2
    for entry in entries:
        assert all(
            key in entry for key in ["ID", "TRIGGER", "RULE", "CHECK", "EXAMPLE"]
        )
