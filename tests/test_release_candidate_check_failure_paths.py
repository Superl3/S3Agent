from __future__ import annotations

from pathlib import Path

from lxml import etree

NS = {"p": "urn:pxml:v1"}


def _text(tree: etree._ElementTree, expr: str) -> str:
    values = tree.xpath(expr, namespaces=NS)
    if not values:
        return ""
    first = values[0]
    if isinstance(first, etree._Element):
        return (first.text or "").strip()
    return str(first).strip()


def _items(tree: etree._ElementTree, expr: str) -> list[str]:
    values = tree.xpath(expr, namespaces=NS)
    output: list[str] = []
    for item in values:
        normalized = str(item).strip()
        if normalized:
            output.append(normalized)
    return output


def _doc_id(path: Path) -> str:
    tree = etree.parse(str(path))
    return _text(tree, "/p:pxml/p:meta/p:doc_id")


def _assert_no_traceback(stdout: str, stderr: str) -> None:
    merged = f"{stdout}\n{stderr}"
    assert "Traceback (most recent call last)" not in merged
    assert "FileNotFoundError" not in merged


def test_release_candidate_check_pass_on_healthy_candidate_subset(
    sandbox_runtime: Path,
    run_python,
    parse_kv_lines,
) -> None:
    result = run_python(
        "scripts/release_candidate_check.py",
        "--runtime-root",
        sandbox_runtime,
        "--allow-caution-rc",
    )
    _assert_no_traceback(result.stdout, result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr

    parsed = parse_kv_lines(result.stdout)
    assert parsed.get("rc_result") == "pass"

    report_path = Path(parsed["release_candidate_report"])
    manifest_path = Path(parsed["release_bundle_manifest"])
    assert report_path.exists()
    assert manifest_path.exists()

    report_tree = etree.parse(str(report_path))
    blockers = _items(report_tree, "/p:pxml/p:payload/p:blockers/p:item/text()")
    assert blockers == ["none"]

    latest_report = (
        sandbox_runtime
        / "latest"
        / "task_release_candidate_batch10_release_candidate_report.pxml"
    )
    latest_manifest = (
        sandbox_runtime
        / "latest"
        / "task_release_candidate_batch10_release_bundle_manifest.pxml"
    )
    assert latest_report.exists()
    assert latest_manifest.exists()
    assert _doc_id(latest_report) == _doc_id(report_path)
    assert _doc_id(latest_manifest) == _doc_id(manifest_path)


def test_release_candidate_check_missing_candidate_returns_fail_without_crash(
    sandbox_runtime: Path,
    run_python,
    parse_kv_lines,
) -> None:
    result = run_python(
        "scripts/release_candidate_check.py",
        "--runtime-root",
        sandbox_runtime,
        "--candidate-task-id",
        "task_missing_candidate_001",
        "--allow-caution-rc",
    )
    _assert_no_traceback(result.stdout, result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr

    parsed = parse_kv_lines(result.stdout)
    assert parsed.get("rc_result") == "fail"

    report_path = Path(parsed["release_candidate_report"])
    manifest_path = Path(parsed["release_bundle_manifest"])
    assert report_path.exists()
    assert manifest_path.exists()

    report_tree = etree.parse(str(report_path))
    blockers = _items(report_tree, "/p:pxml/p:payload/p:blockers/p:item/text()")
    assert any(
        item.startswith("rc_candidate_task_missing:task_missing_candidate_001")
        for item in blockers
    )

    gate_summary = _items(report_tree, "/p:pxml/p:payload/p:gate_summary/p:item/text()")
    assert any("rc_candidate_task_missing" in item for item in gate_summary)

    latest_report = (
        sandbox_runtime
        / "latest"
        / "task_release_candidate_batch10_release_candidate_report.pxml"
    )
    latest_manifest = (
        sandbox_runtime
        / "latest"
        / "task_release_candidate_batch10_release_bundle_manifest.pxml"
    )
    assert latest_report.exists()
    assert latest_manifest.exists()
    assert _doc_id(latest_report) == _doc_id(report_path)
    assert _doc_id(latest_manifest) == _doc_id(manifest_path)


def test_release_candidate_check_missing_coverage_task_becomes_warning_not_gate_fail(
    sandbox_runtime: Path,
    run_python,
    parse_kv_lines,
) -> None:
    result = run_python(
        "scripts/release_candidate_check.py",
        "--runtime-root",
        sandbox_runtime,
        "--coverage-task-id",
        "task_missing_coverage_only_001",
        "--allow-caution-rc",
    )
    _assert_no_traceback(result.stdout, result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr

    parsed = parse_kv_lines(result.stdout)
    assert parsed.get("rc_result") in {"pass", "caution"}
    coverage_warnings_text = parsed.get("coverage_warnings", "")
    assert (
        "rc_coverage_task_missing:task_missing_coverage_only_001"
        in coverage_warnings_text
    )

    report_path = Path(parsed["release_candidate_report"])
    assert report_path.exists()
    report_tree = etree.parse(str(report_path))

    coverage_summary = _items(
        report_tree,
        "/p:pxml/p:payload/p:coverage_summary/p:item/text()",
    )
    assert any(
        "rc_coverage_task_missing:task_missing_coverage_only_001" in item
        for item in coverage_summary
    )

    blockers = _items(report_tree, "/p:pxml/p:payload/p:blockers/p:item/text()")
    assert not any(
        item.startswith("rc_candidate_task_missing:task_missing_coverage_only_001")
        for item in blockers
    )


def test_release_candidate_check_broken_latest_chain_returns_fail_without_crash(
    sandbox_runtime: Path,
    create_broken_candidate_index,
    run_python,
    parse_kv_lines,
) -> None:
    broken_task_id = "task_rc_broken_latest_001"
    create_broken_candidate_index(sandbox_runtime, broken_task_id)

    result = run_python(
        "scripts/release_candidate_check.py",
        "--runtime-root",
        sandbox_runtime,
        "--candidate-task-id",
        broken_task_id,
        "--allow-caution-rc",
    )
    _assert_no_traceback(result.stdout, result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr

    parsed = parse_kv_lines(result.stdout)
    assert parsed.get("rc_result") == "fail"

    report_path = Path(parsed["release_candidate_report"])
    assert report_path.exists()
    report_tree = etree.parse(str(report_path))
    blockers = _items(report_tree, "/p:pxml/p:payload/p:blockers/p:item/text()")
    assert any(
        item.startswith(
            (
                f"rc_candidate_latest_missing:{broken_task_id}",
                f"rc_candidate_ref_broken:{broken_task_id}",
                f"rc_candidate_required_artifact_missing:{broken_task_id}",
            )
        )
        for item in blockers
    )

    latest_report = (
        sandbox_runtime
        / "latest"
        / "task_release_candidate_batch10_release_candidate_report.pxml"
    )
    assert latest_report.exists()
    assert _doc_id(latest_report) == _doc_id(report_path)


def test_harness_validator_strict_release_readiness_regression(
    sandbox_runtime: Path,
    run_python,
) -> None:
    result = run_python(
        "scripts/harness_validator.py",
        "--task-id",
        "task_impl_feature_direct_001",
        "--runtime-root",
        sandbox_runtime,
        "--release-readiness",
    )
    _assert_no_traceback(result.stdout, result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Result: pass" in result.stdout
