from __future__ import annotations

import json
from pathlib import Path


def test_release_candidate_check_validation_failure_does_not_promote_latest(
    sandbox_runtime: Path,
    run_python,
    create_failing_validator_script,
) -> None:
    release_task_id = "task_release_candidate_batch10"
    latest_dir = sandbox_runtime / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)

    latest_report = latest_dir / f"{release_task_id}_release_candidate_report.pxml"
    latest_manifest = latest_dir / f"{release_task_id}_release_bundle_manifest.pxml"
    latest_report.write_text("sentinel-report\n", encoding="utf-8")
    latest_manifest.write_text("sentinel-manifest\n", encoding="utf-8")

    task_index_path = sandbox_runtime / "index" / "tasks" / f"{release_task_id}.json"
    task_index_path.parent.mkdir(parents=True, exist_ok=True)
    task_index_path.write_text(
        json.dumps(
            {
                "task_id": release_task_id,
                "latest_release_candidate_report": "latest/sentinel_report.pxml",
                "latest_release_bundle_manifest": "latest/sentinel_manifest.pxml",
                "updated_at": "2026-03-23T00:00:00Z",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    validator_script = create_failing_validator_script(
        "intentional validation failure for latest-pointer guard"
    )

    result = run_python(
        "scripts/release_candidate_check.py",
        "--runtime-root",
        sandbox_runtime,
        "--validator",
        validator_script,
        "--allow-caution-rc",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "validation failed" in result.stderr.lower()

    assert latest_report.read_text(encoding="utf-8") == "sentinel-report\n"
    assert latest_manifest.read_text(encoding="utf-8") == "sentinel-manifest\n"

    task_index_after = json.loads(task_index_path.read_text(encoding="utf-8"))
    assert (
        task_index_after["latest_release_candidate_report"]
        == "latest/sentinel_report.pxml"
    )
    assert (
        task_index_after["latest_release_bundle_manifest"]
        == "latest/sentinel_manifest.pxml"
    )
    assert task_index_after["updated_at"] == "2026-03-23T00:00:00Z"
