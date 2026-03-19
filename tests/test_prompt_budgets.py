from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import smoke_runner as sr


def test_prompt_budget_constraints() -> None:
    result = sr.check_prompt_budgets(sr.REPO_ROOT)
    assert result["status"] == "pass", result.get("violations")


def test_duplicate_instruction_detection() -> None:
    result = sr.check_duplicate_blocks(sr.REPO_ROOT)
    assert result["status"] == "pass", result.get("duplicates")
