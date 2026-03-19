from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import smoke_runner as sr


def test_failure_classification_produces_known_category() -> None:
    event = {
        "stage": "validation",
        "failure_type": "",
        "symptoms": "retry loop with no progress",
        "result": "fail",
    }
    category = sr.classify_failure_event(event)
    assert category in sr.SELF_DIAG_FAILURE_TYPE_SET


def test_evaluation_output_structure_is_required_shape() -> None:
    output = sr.evaluate_harness_failure(
        {
            "stage": "orchestrator",
            "failure_type": "ROUTING_FAILURE",
            "symptoms": "wrong agent selected",
            "result": "fail",
        }
    )
    assert list(output.keys()) == sr.SELF_DIAG_OUTPUT_KEYS
    assert not sr.validate_harness_evaluation_output(output)


def test_evaluation_produces_at_most_one_minimal_fix() -> None:
    output = sr.evaluate_harness_failure(
        {
            "stage": "debugger",
            "failure_type": "REPAIR_LOOP_FAILURE",
            "symptoms": "retry loop exceeded threshold",
            "result": "fail",
        }
    )
    minimal_fix = output["minimal_fix"]
    assert "\n" not in minimal_fix
    assert ";" not in minimal_fix


def test_evaluation_never_proposes_architectural_redesign() -> None:
    output = sr.evaluate_harness_failure(
        {
            "stage": "implementer",
            "failure_type": "PLAN_FAILURE",
            "symptoms": "scope drift",
            "result": "fail",
        }
    )
    lowered = output["minimal_fix"].lower()
    assert "redesign" not in lowered
    assert "architecture overhaul" not in lowered
    assert "new orchestration layer" not in lowered


def test_major_inefficiency_is_deterministic_and_bounded() -> None:
    assert sr.is_major_inefficiency({"retry_count": 2}) is True
    assert sr.is_major_inefficiency({"repeated_no_progress_count": 2}) is True
    assert sr.is_major_inefficiency({"scope_deviation_flag": True}) is True
    assert sr.is_major_inefficiency({"retry_count": 1}) is False


def test_role_boundary_collapse_is_classified_as_routing_failure() -> None:
    category = sr.classify_failure_event(
        {
            "stage": "validation",
            "failure_type": "",
            "symptoms": "role-boundary collapse between orchestrator and execution",
            "result": "fail",
        }
    )
    assert category == "ROUTING_FAILURE"
