from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import smoke_runner as sr


def test_patch_first_policy() -> None:
    result = sr.check_patch_first_policy(sr.REPO_ROOT)
    assert result["status"] == "pass", result.get("issues")


def test_patch_sequence_behavior_for_repair_task() -> None:
    tasks, errors = sr.load_sample_tasks(sr.REPO_ROOT)
    assert not errors, errors
    failing = [t for t in tasks if t["id"] == "failing_test_repair_001"][0]
    steps = sr.repair_strategy(failing)
    assert steps[:4] == ["classify_failure", "localize_bug", "minimal_patch", "retest"]
    assert steps[-1] == "rewrite_or_redesign_last"
