from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import smoke_runner as sr


def test_bug_localization_policy() -> None:
    result = sr.check_bug_localization_policy(sr.REPO_ROOT)
    assert result["status"] == "pass", result.get("issues")


def test_bug_localization_reason_code_present() -> None:
    registry, errors = sr.parse_skills_registry(sr.REPO_ROOT)
    assert not errors, errors
    tasks, task_errors = sr.load_sample_tasks(sr.REPO_ROOT)
    assert not task_errors, task_errors

    target = [t for t in tasks if t["id"] == "failing_test_repair_001"][0]
    decision, _, _ = sr.route_task(target, registry)
    assert "BUG_LOCALIZATION_REQUIRED" in decision["reason_codes"]
