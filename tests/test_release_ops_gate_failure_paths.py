from __future__ import annotations

from pathlib import Path

from lxml import etree

NS = {"p": "urn:pxml:v1"}


def _items(tree: etree._ElementTree, expr: str) -> list[str]:
    values = tree.xpath(expr, namespaces=NS)
    output: list[str] = []
    for item in values:
        normalized = str(item).strip()
        if normalized:
            output.append(normalized)
    return output


def _assert_no_traceback(stdout: str, stderr: str) -> None:
    merged = f"{stdout}\n{stderr}"
    assert "Traceback (most recent call last)" not in merged
    assert "FileNotFoundError" not in merged


def test_release_ops_gate_pass_exit_code_zero_for_healthy_run(
    sandbox_runtime: Path,
    run_python,
    parse_kv_lines,
) -> None:
    result = run_python(
        "scripts/release_ops_gate.py",
        "--runtime-root",
        sandbox_runtime,
        "--ci-policy",
        "instructions/ci_exit_code_policy.pxml",
        "--allow-caution-rc",
        "--run-verify-phase-audit",
    )
    _assert_no_traceback(result.stdout, result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr

    parsed = parse_kv_lines(result.stdout)
    assert parsed.get("release_ops_gate_result") == "pass"
    assert parsed.get("rc_result") == "pass"
    assert parsed.get("ci_exit_code") == "0"

    report_ref = Path(parsed["release_candidate_report_ref"])
    manifest_ref = Path(parsed["release_bundle_manifest_ref"])
    audit_ref = Path(parsed["verify_phase_audit_report_ref"])
    assert report_ref.exists()
    assert manifest_ref.exists()
    assert audit_ref.exists()


def test_release_ops_gate_missing_candidate_maps_to_fail_exit_code(
    sandbox_runtime: Path,
    run_python,
    parse_kv_lines,
) -> None:
    result = run_python(
        "scripts/release_ops_gate.py",
        "--runtime-root",
        sandbox_runtime,
        "--ci-policy",
        "instructions/ci_exit_code_policy.pxml",
        "--candidate-task-id",
        "task_missing_candidate_001",
    )
    _assert_no_traceback(result.stdout, result.stderr)
    assert result.returncode == 1, result.stdout + result.stderr

    parsed = parse_kv_lines(result.stdout)
    assert parsed.get("release_ops_gate_result") == "fail"
    assert parsed.get("rc_result") == "fail"
    assert parsed.get("ci_exit_code") == "1"

    report_ref = Path(parsed["release_candidate_report_ref"])
    assert report_ref.exists()


def test_release_ops_gate_missing_coverage_task_does_not_crash(
    sandbox_runtime: Path,
    run_python,
    parse_kv_lines,
) -> None:
    result = run_python(
        "scripts/release_ops_gate.py",
        "--runtime-root",
        sandbox_runtime,
        "--ci-policy",
        "instructions/ci_exit_code_policy.pxml",
        "--coverage-task-id",
        "task_missing_coverage_only_001",
        "--allow-caution-rc",
    )
    _assert_no_traceback(result.stdout, result.stderr)
    assert result.returncode in {0, 2}, result.stdout + result.stderr

    parsed = parse_kv_lines(result.stdout)
    assert parsed.get("release_ops_gate_result") in {"pass", "caution"}
    assert parsed.get("rc_result") in {"pass", "caution"}

    expected_ci_code = "0" if parsed.get("release_ops_gate_result") == "pass" else "2"
    assert parsed.get("ci_exit_code") == expected_ci_code

    report_ref = Path(parsed["release_candidate_report_ref"])
    assert report_ref.exists()

    report_tree = etree.parse(str(report_ref))
    coverage_summary = _items(
        report_tree,
        "/p:pxml/p:payload/p:coverage_summary/p:item/text()",
    )
    assert any(
        "rc_coverage_task_missing:task_missing_coverage_only_001" in item
        for item in coverage_summary
    )


def test_release_ops_gate_usage_error_maps_to_usage_exit_code(
    tmp_path: Path,
    run_python,
) -> None:
    missing_runtime = tmp_path / "missing_runtime"
    result = run_python(
        "scripts/release_ops_gate.py",
        "--runtime-root",
        missing_runtime,
        "--ci-policy",
        "instructions/ci_exit_code_policy.pxml",
    )
    _assert_no_traceback(result.stdout, result.stderr)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "runtime root not found" in result.stderr.lower()
