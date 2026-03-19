from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import smoke_runner as sr


def _sample_packet(packet_class: str = "generic_packet", max_attempts: int = 2) -> dict:
    return {
        "packet_class": packet_class,
        "phase_name": "phase-01",
        "goal": "apply narrow fix",
        "scope": "narrow",
        "allowed_files": ["src/example.py"],
        "forbidden_files": ["docs/"],
        "success_check": {
            "type": "validation",
            "target": "unit tests",
            "metric": "pass",
        },
        "parallel_mode": "off",
        "retry_strategy": {
            "max_attempts": max_attempts,
            "observed_vs_expected": "expected pass, got fail",
            "next_probe": "run focused unit test",
            "verifier_feedback": "assertion remains failing",
        },
        "fast_path_attempt": {
            "eligible": True,
            "allowed_files_count": 1,
            "budget_exempt": True,
            "status": "not_attempted",
            "verifier_result": "na",
            "validation_proof": "na",
        },
        "verifier": {
            "verdict": "fail",
            "reasons": "targeted test still fails",
            "retryable": True,
            "validation_proof": "unit test failed on assertion",
        },
        "next_if_pass": "phase-02",
        "packet_exhaustion": "retry_pending",
    }


def test_packet_runner_policy_smoke_check_passes() -> None:
    result = sr.check_packet_runner_policy(sr.REPO_ROOT)
    assert result["status"] == "pass", result.get("issues")


def test_packet_artifact_rejects_generic_packet_attempt_three() -> None:
    packet = _sample_packet(packet_class="generic_packet", max_attempts=3)
    issues = sr.validate_packet_artifact(packet)
    assert any(
        "generic_packet must use retry_strategy.max_attempts=2" in i for i in issues
    )


def test_packet_artifact_allows_failing_test_repair_attempt_three() -> None:
    packet = _sample_packet(packet_class="failing_test_repair", max_attempts=3)
    issues = sr.validate_packet_artifact(packet)
    assert not issues, issues


def test_packet_artifact_enforces_verifier_shape() -> None:
    packet = _sample_packet()
    packet["verifier"] = {
        "verdict": "fail",
        "reasons": "failed",
        "retryable": True,
    }
    issues = sr.validate_packet_artifact(packet)
    assert any("verifier keys must be exactly" in i for i in issues)


def test_packet_artifact_rejects_log_blob_validation_proof() -> None:
    packet = _sample_packet()
    packet["verifier"]["validation_proof"] = "stdout: full log payload follows"
    issues = sr.validate_packet_artifact(packet)
    assert any("must not contain raw logs or payload blobs" in i for i in issues)


def test_fast_path_allowed_files_count_uses_unique_normalized_paths() -> None:
    packet = _sample_packet()
    packet["allowed_files"] = ["src/example.py", "src\\example.py", "src/example.py"]
    packet["fast_path_attempt"]["allowed_files_count"] = 1
    issues = sr.validate_packet_artifact(packet)
    assert not issues, issues


def test_fast_path_rejects_incorrect_allowed_files_count() -> None:
    packet = _sample_packet()
    packet["allowed_files"] = ["src/a.py", "src/b.py"]
    packet["fast_path_attempt"]["allowed_files_count"] = 1
    issues = sr.validate_packet_artifact(packet)
    assert any(
        "fast_path_attempt.allowed_files_count must equal unique normalized allowed_files count"
        in i
        for i in issues
    )


def test_fast_path_attempt_is_budget_exempt() -> None:
    packet = _sample_packet()
    packet["fast_path_attempt"]["budget_exempt"] = False
    issues = sr.validate_packet_artifact(packet)
    assert any(
        "fast_path_attempt must be budget-exempt from retry_strategy.max_attempts" in i
        for i in issues
    )


def test_fast_path_success_requires_verifier_result_and_validation_proof() -> None:
    packet = _sample_packet()
    packet["fast_path_attempt"]["status"] = "pass"
    packet["fast_path_attempt"]["verifier_result"] = "na"
    packet["fast_path_attempt"]["validation_proof"] = ""
    issues = sr.validate_packet_artifact(packet)
    assert any(
        "fast_path_attempt status=pass must record verifier_result=pass and non-empty validation_proof"
        in i
        for i in issues
    )


def test_final_renderer_uses_blocked_template_for_terminated_execution() -> None:
    rendered = sr.render_final_user_output(
        {
            "termination_status": "terminated",
            "status": "fail",
        }
    )
    lowered = rendered.lower()
    assert rendered.startswith("## ")
    assert "blocked" in lowered


def test_final_renderer_fallback_uses_success_template() -> None:
    rendered = sr.render_final_user_output({"status": "unknown"})
    lowered = rendered.lower()
    assert rendered.startswith("## ")
    assert "status" in lowered
