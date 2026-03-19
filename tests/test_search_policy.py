from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import smoke_runner as sr


def test_check_search_policy_passes() -> None:
    result = sr.check_search_policy(sr.REPO_ROOT)
    assert result["status"] == "pass", result


def test_search_policy_sections_are_present() -> None:
    search_text = (
        Path(ROOT / "instructions/search_policy.md").read_text(encoding="utf-8").lower()
    )
    exploration_text = (
        Path(ROOT / "instructions/exploration_policy.md")
        .read_text(encoding="utf-8")
        .lower()
    )

    for marker in ["## stage order", "stage0", "stage1", "stage2", "stage3 stop"]:
        assert marker in search_text

    for marker in [
        "## discovery order",
        "## evidence-first summary",
        "## symbol discovery restrictions",
        "task target",
        "indexed candidates",
    ]:
        assert marker in exploration_text
