from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import smoke_runner as sr


def test_parallelization_gating_policy() -> None:
    result = sr.check_parallelization_policy(sr.REPO_ROOT)
    assert result["status"] == "pass", result.get("issues")


def test_tiny_task_stays_single_agent() -> None:
    registry, errors = sr.parse_skills_registry(sr.REPO_ROOT)
    assert not errors, errors
    tasks, task_errors = sr.load_sample_tasks(sr.REPO_ROOT)
    assert not task_errors, task_errors

    tiny = [t for t in tasks if t["id"] == "tiny_bugfix_001"][0]
    decision, _, _ = sr.route_task(tiny, registry)
    assert decision["mode"] == "MICRO"
    assert decision["parallel"] is False
    assert "SINGLE_AGENT_DEFAULT" in decision["reason_codes"]
