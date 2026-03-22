from __future__ import annotations

import json
from pathlib import Path

from lxml import etree

NS = {"p": "urn:pxml:v1"}
AUDITED_TASK_IDS = ["task_verify_post_smoke_001", "task_impl_feature_direct_001"]


def _doc_id(path: Path) -> str:
    tree = etree.parse(str(path))
    values = tree.xpath("/p:pxml/p:meta/p:doc_id/text()", namespaces=NS)
    return str(values[0]).strip() if values else ""


def _assert_no_traceback(stdout: str, stderr: str) -> None:
    merged = f"{stdout}\n{stderr}"
    assert "Traceback (most recent call last)" not in merged


def _task_index_path(runtime_root: Path, task_id: str) -> Path:
    return runtime_root / "index" / "tasks" / f"{task_id}.json"


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_verify_phase_audit_promotes_latest_on_valid_report(
    sandbox_runtime: Path,
    run_python,
    parse_kv_lines,
) -> None:
    release_task_id = "task_release_candidate_batch10"
    result = run_python(
        "scripts/verify_phase_audit.py",
        "--runtime-root",
        sandbox_runtime,
        "--release-task-id",
        release_task_id,
    )
    _assert_no_traceback(result.stdout, result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr

    parsed = parse_kv_lines(result.stdout)
    report_path = Path(parsed["verify_phase_audit_report"])
    assert report_path.exists()
    report_doc_id = _doc_id(report_path)
    report_rel = str(report_path.relative_to(sandbox_runtime)).replace("\\", "/")

    latest_paths = [
        sandbox_runtime
        / "latest"
        / f"{release_task_id}_verify_phase_audit_report.pxml",
        sandbox_runtime / "latest" / "release_verify_phase_audit_report.pxml",
        sandbox_runtime
        / "latest"
        / "task_verify_post_smoke_001_verify_phase_audit_report.pxml",
        sandbox_runtime
        / "latest"
        / "task_impl_feature_direct_001_verify_phase_audit_report.pxml",
    ]
    for latest_path in latest_paths:
        assert latest_path.exists()
        assert _doc_id(latest_path) == report_doc_id

    release_index = _load_json(_task_index_path(sandbox_runtime, release_task_id))
    assert release_index["latest_verify_phase_audit_report"] == report_rel

    for task_id in AUDITED_TASK_IDS:
        task_index = _load_json(_task_index_path(sandbox_runtime, task_id))
        assert task_index["latest_verify_phase_audit_report"] == report_rel

    artifact_index_path = (
        sandbox_runtime / "index" / "artifacts" / f"{report_doc_id}.json"
    )
    assert artifact_index_path.exists()
    artifact_index = _load_json(artifact_index_path)
    assert artifact_index["doc_class"] == "verify_phase_audit_report"
    assert artifact_index["doc_id"] == report_doc_id
    assert artifact_index["path"] == report_rel
    assert artifact_index["task_id"] == release_task_id


def test_verify_phase_audit_does_not_promote_latest_on_validation_failure(
    sandbox_runtime: Path,
    run_python,
    create_failing_validator_script,
) -> None:
    release_task_id = "task_verify_phase_guard_fail_001"
    validator_script = create_failing_validator_script(
        "intentional verify-phase validation failure"
    )

    scoped_latest_path = (
        sandbox_runtime / "latest" / f"{release_task_id}_verify_phase_audit_report.pxml"
    )
    release_index_path = _task_index_path(sandbox_runtime, release_task_id)
    artifacts_dir = sandbox_runtime / "index" / "artifacts"
    audits_dir = sandbox_runtime / "release" / "audits"

    before_artifact_files = {path.name for path in artifacts_dir.glob("*.json")}
    before_audit_reports = {path.name for path in audits_dir.glob("*.pxml")}
    before_task_indexes = {
        task_id: _load_json(_task_index_path(sandbox_runtime, task_id))
        for task_id in AUDITED_TASK_IDS
    }

    result = run_python(
        "scripts/verify_phase_audit.py",
        "--runtime-root",
        sandbox_runtime,
        "--release-task-id",
        release_task_id,
        "--validator",
        validator_script,
    )
    _assert_no_traceback(result.stdout, result.stderr)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "validation failed" in result.stderr.lower()

    assert not scoped_latest_path.exists()
    assert not release_index_path.exists()

    after_artifact_files = {path.name for path in artifacts_dir.glob("*.json")}
    assert after_artifact_files == before_artifact_files

    for task_id in AUDITED_TASK_IDS:
        assert (
            _load_json(_task_index_path(sandbox_runtime, task_id))
            == before_task_indexes[task_id]
        )

    after_audit_reports = {path.name for path in audits_dir.glob("*.pxml")}
    assert len(after_audit_reports) >= len(before_audit_reports) + 1


def test_verify_phase_audit_failure_preserves_existing_latest(
    sandbox_runtime: Path,
    run_python,
    create_failing_validator_script,
) -> None:
    release_task_id = "task_verify_phase_guard_existing_001"
    validator_script = create_failing_validator_script(
        "intentional verify-phase validation failure with existing latest"
    )

    audits_dir = sandbox_runtime / "release" / "audits"
    baseline_reports = sorted(audits_dir.glob("*.pxml"))
    assert baseline_reports
    baseline_report = baseline_reports[0]
    baseline_doc_id = _doc_id(baseline_report)
    baseline_rel = str(baseline_report.relative_to(sandbox_runtime)).replace("\\", "/")

    latest_dir = sandbox_runtime / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    tracked_latest = [
        latest_dir / f"{release_task_id}_verify_phase_audit_report.pxml",
        latest_dir / "release_verify_phase_audit_report.pxml",
        latest_dir / "task_verify_post_smoke_001_verify_phase_audit_report.pxml",
        latest_dir / "task_impl_feature_direct_001_verify_phase_audit_report.pxml",
    ]
    for latest_path in tracked_latest:
        latest_path.write_text(
            baseline_report.read_text(encoding="utf-8"), encoding="utf-8"
        )

    release_index_path = _task_index_path(sandbox_runtime, release_task_id)
    release_index_path.parent.mkdir(parents=True, exist_ok=True)
    release_index_path.write_text(
        json.dumps(
            {
                "task_id": release_task_id,
                "latest_verify_phase_audit_report": baseline_rel,
                "updated_at": "2026-03-23T00:00:00Z",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    artifacts_dir = sandbox_runtime / "index" / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    existing_artifact_index = artifacts_dir / f"{baseline_doc_id}.json"
    existing_artifact_index.write_text(
        json.dumps(
            {
                "doc_id": baseline_doc_id,
                "doc_class": "verify_phase_audit_report",
                "task_id": release_task_id,
                "path": baseline_rel,
                "updated_at": "2026-03-23T00:00:00Z",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    before_latest_doc_ids = {path.name: _doc_id(path) for path in tracked_latest}
    before_release_index = _load_json(release_index_path)
    before_artifact_files = {path.name for path in artifacts_dir.glob("*.json")}
    before_task_indexes = {
        task_id: _load_json(_task_index_path(sandbox_runtime, task_id))
        for task_id in AUDITED_TASK_IDS
    }

    result = run_python(
        "scripts/verify_phase_audit.py",
        "--runtime-root",
        sandbox_runtime,
        "--release-task-id",
        release_task_id,
        "--validator",
        validator_script,
    )
    _assert_no_traceback(result.stdout, result.stderr)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "validation failed" in result.stderr.lower()

    after_latest_doc_ids = {path.name: _doc_id(path) for path in tracked_latest}
    assert after_latest_doc_ids == before_latest_doc_ids
    assert _load_json(release_index_path) == before_release_index

    after_artifact_files = {path.name for path in artifacts_dir.glob("*.json")}
    assert after_artifact_files == before_artifact_files

    for task_id in AUDITED_TASK_IDS:
        assert (
            _load_json(_task_index_path(sandbox_runtime, task_id))
            == before_task_indexes[task_id]
        )
