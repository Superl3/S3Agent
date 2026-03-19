from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import smoke_runner as sr


def test_phase_gate_policy() -> None:
    result = sr.check_phase_gate_policy(sr.REPO_ROOT)
    assert result["status"] == "pass", result.get("issues")


def test_execution_packet_trigger_conditions() -> None:
    assert sr.should_require_execution_packets("DEEP", "low", "narrow", 1)
    assert sr.should_require_execution_packets("STANDARD", "high", "narrow", 1)
    assert sr.should_require_execution_packets("STANDARD", "low", "broad", 1)
    assert sr.should_require_execution_packets("STANDARD", "low", "narrow", 4)
    assert not sr.should_require_execution_packets("MICRO", "low", "narrow", 3)


def test_packet_required_route_cannot_mark_not_required_gate() -> None:
    registry, errors = sr.parse_skills_registry(sr.REPO_ROOT)
    assert not errors, errors
    tasks, task_errors = sr.load_sample_tasks(sr.REPO_ROOT)
    assert not task_errors, task_errors

    refactor = [t for t in tasks if t["id"] == "high_risk_refactor_001"][0]
    decision, _, _ = sr.route_task(refactor, registry)
    assert decision["packet_required"] is True

    mutated = dict(decision)
    mutated["packet_gate_status"] = "not_required"
    validation = sr.validate_routing_decision(mutated, registry)
    assert (
        "packet_required true cannot use packet_gate_status not_required" in validation
    )
