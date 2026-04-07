from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _write_feature_intake(path: Path, task_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<pxml xmlns=\"urn:pxml:v1\">
  <meta>
    <doc_id>doc_task_intake_{task_id[5:]}</doc_id>
    <doc_class>task_intake</doc_class>
    <schema_version>1.0.0</schema_version>
    <task_id>{task_id}</task_id>
    <run_id>run_{task_id[5:]}</run_id>
    <sequence>1</sequence>
    <writer_agent>manager</writer_agent>
    <created_at>2026-03-23T00:00:00Z</created_at>
  </meta>
  <payload>
    <request_text>Implement a bounded feature helper.</request_text>
    <task_type>feature</task_type>
    <requested_outcome>Deliver a small local feature update.</requested_outcome>
    <constraints>
      <item>keep-runtime-safe</item>
    </constraints>
    <risk_hint>medium</risk_hint>
  </payload>
  <integrity>
    <content_sha256>0000000000000000000000000000000000000000000000000000000000000000</content_sha256>
  </integrity>
</pxml>
""",
        encoding="utf-8",
    )


def _write_verification_packet(path: Path, task_id: str) -> None:
    check = {
        "check_id": "check_guard_pass",
        "check_type": "test",
        "command": 'python -c "import sys; raise SystemExit(0)"',
        "pass_condition": "exit_code==0",
        "deterministic": True,
        "timeout_sec": 30,
    }
    lock_hash = hashlib.sha256(
        json.dumps([check], sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<pxml xmlns=\"urn:pxml:v1\">
  <meta>
    <doc_id>doc_execpkt_latest_guard_001</doc_id>
    <doc_class>execution_packet</doc_class>
    <schema_version>1.0.0</schema_version>
    <task_id>{task_id}</task_id>
    <run_id>run_{task_id[5:]}</run_id>
    <sequence>1</sequence>
    <writer_agent>manager</writer_agent>
    <created_at>2026-03-23T00:00:00Z</created_at>
  </meta>
  <refs>
    <ref>
      <doc_id>doc_manager_route_latest_guard_001</doc_id>
      <doc_class>manager_route</doc_class>
      <relation>derived_from</relation>
    </ref>
  </refs>
  <payload>
    <task_summary>verification latest guard</task_summary>
    <in_scope>
      <item>src/</item>
    </in_scope>
    <out_of_scope>
      <item>docs/</item>
    </out_of_scope>
    <expected_files>
      <file>
        <path>src/target_bugfix.py</path>
        <mode>modify</mode>
      </file>
    </expected_files>
    <patch_constraints>
      <patch_mode>patch_first</patch_mode>
      <max_files>1</max_files>
      <rewrite_exception_approved>false</rewrite_exception_approved>
    </patch_constraints>
    <acceptance_checks>
      <check>
        <check_id>{check["check_id"]}</check_id>
        <check_type>{check["check_type"]}</check_type>
        <command>{check["command"]}</command>
        <pass_condition>{check["pass_condition"]}</pass_condition>
        <deterministic>true</deterministic>
        <timeout_sec>{check["timeout_sec"]}</timeout_sec>
      </check>
    </acceptance_checks>
    <acceptance_lock_hash>{lock_hash}</acceptance_lock_hash>
  </payload>
  <integrity>
    <content_sha256>0000000000000000000000000000000000000000000000000000000000000000</content_sha256>
  </integrity>
</pxml>
""",
        encoding="utf-8",
    )


def _seed_latest_guard(
    runtime_root: Path,
    *,
    task_id: str,
    suffix: str,
    index_key: str,
    pointer_value: str,
    sentinel_text: str,
) -> Path:
    latest_path = runtime_root / "latest" / f"{task_id}_{suffix}.pxml"
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(sentinel_text, encoding="utf-8")

    task_index_path = runtime_root / "index" / "tasks" / f"{task_id}.json"
    task_index_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"task_id": task_id, "updated_at": "2026-03-23T00:00:00Z"}
    if task_index_path.exists():
        payload.update(json.loads(task_index_path.read_text(encoding="utf-8")))
    payload[index_key] = pointer_value
    payload["updated_at"] = "2026-03-23T00:00:00Z"
    task_index_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return task_index_path


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


def test_packet_builder_validation_failure_does_not_promote_latest(
    tmp_path: Path,
    run_python,
    create_failing_validator_script,
) -> None:
    task_id = "task_packet_latest_guard_001"
    runtime_root = tmp_path / "runtime"
    intake_path = tmp_path / "intake" / f"{task_id}.pxml"
    _write_feature_intake(intake_path, task_id)

    task_index_path = _seed_latest_guard(
        runtime_root,
        task_id=task_id,
        suffix="manager_route",
        index_key="latest_manager_route",
        pointer_value="latest/sentinel_route.pxml",
        sentinel_text="sentinel-route\n",
    )
    _seed_latest_guard(
        runtime_root,
        task_id=task_id,
        suffix="execution_packet",
        index_key="latest_execution_packet",
        pointer_value="latest/sentinel_packet.pxml",
        sentinel_text="sentinel-packet\n",
    )
    validator_script = create_failing_validator_script(
        "intentional packet builder validation failure"
    )

    result = run_python(
        "scripts/packet_builder.py",
        "--intake",
        intake_path,
        "--runtime-root",
        runtime_root,
        "--validator",
        validator_script,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "validation failed" in result.stderr.lower()

    latest_route = runtime_root / "latest" / f"{task_id}_manager_route.pxml"
    latest_packet = runtime_root / "latest" / f"{task_id}_execution_packet.pxml"
    assert latest_route.read_text(encoding="utf-8") == "sentinel-route\n"
    assert latest_packet.read_text(encoding="utf-8") == "sentinel-packet\n"

    task_index_after = json.loads(task_index_path.read_text(encoding="utf-8"))
    assert task_index_after["latest_manager_route"] == "latest/sentinel_route.pxml"
    assert task_index_after["latest_execution_packet"] == "latest/sentinel_packet.pxml"
    assert task_index_after["updated_at"] == "2026-03-23T00:00:00Z"


def test_packet_builder_preserves_existing_task_index_fields(
    tmp_path: Path,
    run_python,
) -> None:
    task_id = "task_packet_preserve_index_001"
    runtime_root = tmp_path / "runtime"
    intake_path = tmp_path / "intake" / f"{task_id}.pxml"
    _write_feature_intake(intake_path, task_id)

    task_index_path = runtime_root / "index" / "tasks" / f"{task_id}.json"
    task_index_path.parent.mkdir(parents=True, exist_ok=True)
    task_index_path.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "latest_implementer_result": "implementer/results/existing_impl_result.pxml",
                "latest_verification_result": "verification/results/existing_verify_result.pxml",
                "custom_metadata": "keep-me",
                "updated_at": "2026-03-23T00:00:00Z",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_python(
        "scripts/packet_builder.py",
        "--intake",
        intake_path,
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    task_index_after = json.loads(task_index_path.read_text(encoding="utf-8"))
    assert (
        task_index_after["latest_implementer_result"]
        == "implementer/results/existing_impl_result.pxml"
    )
    assert (
        task_index_after["latest_verification_result"]
        == "verification/results/existing_verify_result.pxml"
    )
    assert task_index_after["custom_metadata"] == "keep-me"
    assert "latest_manager_route" in task_index_after
    assert "latest_execution_packet" in task_index_after


def test_implementer_validation_failure_does_not_promote_latest(
    tmp_path: Path,
    run_python,
    create_failing_validator_script,
) -> None:
    task_id = "task_impl_latest_guard_001"
    runtime_root = tmp_path / "runtime"
    workspace_root = tmp_path / "workspace"
    intake_path = tmp_path / "intake" / f"{task_id}.pxml"
    _write_feature_intake(intake_path, task_id)
    workspace_root.mkdir(parents=True, exist_ok=True)

    build = run_python(
        "scripts/packet_builder.py",
        "--intake",
        intake_path,
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert build.returncode == 0, build.stdout + build.stderr

    task_index_path = _seed_latest_guard(
        runtime_root,
        task_id=task_id,
        suffix="implementer_result",
        index_key="latest_implementer_result",
        pointer_value="latest/sentinel_impl_result.pxml",
        sentinel_text="sentinel-implementer\n",
    )
    validator_script = create_failing_validator_script(
        "intentional implementer validation failure"
    )
    packet_path = runtime_root / "latest" / f"{task_id}_execution_packet.pxml"

    result = run_python(
        "scripts/implementer_runner.py",
        "--packet",
        packet_path,
        "--runtime-root",
        runtime_root,
        "--workspace-root",
        workspace_root,
        "--validator",
        validator_script,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "validation failed" in result.stderr.lower()

    latest_impl = runtime_root / "latest" / f"{task_id}_implementer_result.pxml"
    assert latest_impl.read_text(encoding="utf-8") == "sentinel-implementer\n"

    task_index_after = json.loads(task_index_path.read_text(encoding="utf-8"))
    assert (
        task_index_after["latest_implementer_result"]
        == "latest/sentinel_impl_result.pxml"
    )


def test_verification_validation_failure_does_not_promote_latest(
    tmp_path: Path,
    run_python,
    create_failing_validator_script,
) -> None:
    task_id = "task_verify_latest_guard_001"
    runtime_root = tmp_path / "runtime"
    packet_path = tmp_path / "packet_latest_guard.pxml"
    _write_verification_packet(packet_path, task_id)

    task_index_path = _seed_latest_guard(
        runtime_root,
        task_id=task_id,
        suffix="verification_result",
        index_key="latest_verification_result",
        pointer_value="latest/sentinel_verify_result.pxml",
        sentinel_text="sentinel-verification\n",
    )
    validator_script = create_failing_validator_script(
        "intentional verification validation failure"
    )

    result = run_python(
        "scripts/verification_runner.py",
        "--packet",
        packet_path,
        "--runtime-root",
        runtime_root,
        "--validator",
        validator_script,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "validation failed" in result.stderr.lower()

    latest_verify = runtime_root / "latest" / f"{task_id}_verification_result.pxml"
    assert latest_verify.read_text(encoding="utf-8") == "sentinel-verification\n"

    task_index_after = json.loads(task_index_path.read_text(encoding="utf-8"))
    assert (
        task_index_after["latest_verification_result"]
        == "latest/sentinel_verify_result.pxml"
    )
    assert task_index_after["updated_at"] == "2026-03-23T00:00:00Z"
