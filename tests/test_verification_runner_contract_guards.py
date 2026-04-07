from __future__ import annotations

import hashlib
import json
from pathlib import Path

from lxml import etree

NS = {"p": "urn:pxml:v1"}


def _build_packet(
    packet_path: Path,
    *,
    task_id: str,
    run_id: str,
    checks: list[dict[str, object]],
    intended_behaviors: list[str] | None = None,
    proof_requirements: list[dict[str, object]] | None = None,
    acceptance_lock_hash_override: str | None = None,
) -> None:
    normalized_checks = [
        {
            "check_id": str(item["check_id"]),
            "check_type": str(item["check_type"]),
            "command": str(item["command"]),
            "pass_condition": str(item["pass_condition"]),
            "deterministic": bool(item["deterministic"]),
            "timeout_sec": int(item["timeout_sec"]),
        }
        for item in checks
    ]
    lock_hash = hashlib.sha256(
        json.dumps(normalized_checks, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    if acceptance_lock_hash_override is not None:
        lock_hash = acceptance_lock_hash_override

    root = etree.Element("{urn:pxml:v1}pxml", nsmap={None: "urn:pxml:v1"})
    meta = etree.SubElement(root, "{urn:pxml:v1}meta")
    etree.SubElement(meta, "{urn:pxml:v1}doc_id").text = "doc_execpkt_test_guard_001"
    etree.SubElement(meta, "{urn:pxml:v1}doc_class").text = "execution_packet"
    etree.SubElement(meta, "{urn:pxml:v1}schema_version").text = "1.0.0"
    etree.SubElement(meta, "{urn:pxml:v1}task_id").text = task_id
    etree.SubElement(meta, "{urn:pxml:v1}run_id").text = run_id
    etree.SubElement(meta, "{urn:pxml:v1}sequence").text = "1"
    etree.SubElement(meta, "{urn:pxml:v1}writer_agent").text = "manager"
    etree.SubElement(meta, "{urn:pxml:v1}created_at").text = "2026-03-23T00:00:00Z"

    refs = etree.SubElement(root, "{urn:pxml:v1}refs")
    ref = etree.SubElement(refs, "{urn:pxml:v1}ref")
    etree.SubElement(ref, "{urn:pxml:v1}doc_id").text = "doc_manager_route_guard_001"
    etree.SubElement(ref, "{urn:pxml:v1}doc_class").text = "manager_route"
    etree.SubElement(ref, "{urn:pxml:v1}relation").text = "derived_from"

    payload = etree.SubElement(root, "{urn:pxml:v1}payload")
    etree.SubElement(
        payload, "{urn:pxml:v1}task_summary"
    ).text = "verification test packet"
    if intended_behaviors:
        intended_node = etree.SubElement(payload, "{urn:pxml:v1}intended_behaviors")
        for item in intended_behaviors:
            etree.SubElement(intended_node, "{urn:pxml:v1}item").text = item
    if proof_requirements:
        proof_node = etree.SubElement(payload, "{urn:pxml:v1}proof_requirements")
        for requirement in proof_requirements:
            proof = etree.SubElement(proof_node, "{urn:pxml:v1}proof")
            etree.SubElement(proof, "{urn:pxml:v1}proof_category").text = str(
                requirement["proof_category"]
            )
            etree.SubElement(proof, "{urn:pxml:v1}required").text = str(
                requirement["required"]
            ).lower()
            etree.SubElement(proof, "{urn:pxml:v1}proof_method").text = "automated"
            etree.SubElement(proof, "{urn:pxml:v1}minimum_evidence").text = "log"

    in_scope = etree.SubElement(payload, "{urn:pxml:v1}in_scope")
    etree.SubElement(in_scope, "{urn:pxml:v1}item").text = "src/"
    out_scope = etree.SubElement(payload, "{urn:pxml:v1}out_of_scope")
    etree.SubElement(out_scope, "{urn:pxml:v1}item").text = "docs/"
    expected_files = etree.SubElement(payload, "{urn:pxml:v1}expected_files")
    expected_file = etree.SubElement(expected_files, "{urn:pxml:v1}file")
    etree.SubElement(expected_file, "{urn:pxml:v1}path").text = "src/target_bugfix.py"
    etree.SubElement(expected_file, "{urn:pxml:v1}mode").text = "modify"
    patch_constraints = etree.SubElement(payload, "{urn:pxml:v1}patch_constraints")
    etree.SubElement(patch_constraints, "{urn:pxml:v1}patch_mode").text = "patch_first"
    etree.SubElement(patch_constraints, "{urn:pxml:v1}max_files").text = "2"
    etree.SubElement(
        patch_constraints, "{urn:pxml:v1}rewrite_exception_approved"
    ).text = "false"

    checks_node = etree.SubElement(payload, "{urn:pxml:v1}acceptance_checks")
    for check in normalized_checks:
        node = etree.SubElement(checks_node, "{urn:pxml:v1}check")
        etree.SubElement(node, "{urn:pxml:v1}check_id").text = str(check["check_id"])
        etree.SubElement(node, "{urn:pxml:v1}check_type").text = str(
            check["check_type"]
        )
        etree.SubElement(node, "{urn:pxml:v1}command").text = str(check["command"])
        etree.SubElement(node, "{urn:pxml:v1}pass_condition").text = str(
            check["pass_condition"]
        )
        etree.SubElement(node, "{urn:pxml:v1}deterministic").text = str(
            check["deterministic"]
        ).lower()
        etree.SubElement(node, "{urn:pxml:v1}timeout_sec").text = str(
            check["timeout_sec"]
        )

    etree.SubElement(payload, "{urn:pxml:v1}acceptance_lock_hash").text = lock_hash
    test_guidance = etree.SubElement(payload, "{urn:pxml:v1}test_guidance")
    etree.SubElement(test_guidance, "{urn:pxml:v1}item").text = "run declared checks"
    escalation = etree.SubElement(payload, "{urn:pxml:v1}escalation_triggers")
    etree.SubElement(escalation, "{urn:pxml:v1}item").text = "failed verification"
    stop_conditions = etree.SubElement(payload, "{urn:pxml:v1}stop_conditions")
    etree.SubElement(stop_conditions, "{urn:pxml:v1}item").text = "none"

    integrity = etree.SubElement(root, "{urn:pxml:v1}integrity")
    etree.SubElement(integrity, "{urn:pxml:v1}content_sha256").text = "0" * 64

    packet_path.parent.mkdir(parents=True, exist_ok=True)
    etree.ElementTree(root).write(
        str(packet_path), encoding="UTF-8", xml_declaration=True, pretty_print=True
    )


def _latest_result(runtime_root: Path) -> Path:
    results = sorted((runtime_root / "verification" / "results").glob("*.pxml"))
    assert results
    return results[-1]


def _text(tree: etree._ElementTree, expr: str) -> str | None:
    values = tree.xpath(expr, namespaces=NS)
    if not values:
        return None
    value = values[0]
    if isinstance(value, etree._Element):
        text = value.text
    else:
        text = str(value)
    return text.strip() if text else None


def test_behavior_changing_structural_only_stays_unproven(
    tmp_path: Path,
    run_python,
) -> None:
    runtime_root = tmp_path / "runtime"
    packet_path = tmp_path / "packet_behavior_guard.pxml"
    _build_packet(
        packet_path,
        task_id="task_verifier_guard_behavior_001",
        run_id="run_verifier_guard_001",
        checks=[
            {
                "check_id": "build_structural_only",
                "check_type": "build",
                "command": 'python -c "import sys; raise SystemExit(0)"',
                "pass_condition": "exit_code==0",
                "deterministic": True,
                "timeout_sec": 30,
            }
        ],
        intended_behaviors=["runtime symptom is resolved"],
        proof_requirements=[
            {"proof_category": "behavioral", "required": True},
            {"proof_category": "structural", "required": True},
        ],
    )

    result = run_python(
        "scripts/verification_runner.py",
        "--packet",
        packet_path,
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    tree = etree.parse(str(_latest_result(runtime_root)))
    assert _text(tree, "/p:pxml/p:payload/p:proof_coverage/p:structural") == "PASS"
    assert _text(tree, "/p:pxml/p:payload/p:proof_coverage/p:behavioral") == "NOT-RUN"
    assert _text(tree, "/p:pxml/p:payload/p:final_verdict") == "inconclusive"
    assert (
        "unproven=" in (_text(tree, "/p:pxml/p:payload/p:verdict_reason") or "").lower()
    )
    unverified = tree.xpath(
        "/p:pxml/p:payload/p:unverified_areas/p:item/text()", namespaces=NS
    )
    assert any("proof.behavioral:UNPROVEN" in item for item in unverified)


def test_proof_category_coverage_reflects_structural_behavioral_regression(
    tmp_path: Path,
    run_python,
) -> None:
    runtime_root = tmp_path / "runtime"
    packet_path = tmp_path / "packet_proof_coverage.pxml"
    _build_packet(
        packet_path,
        task_id="task_verifier_guard_categories_001",
        run_id="run_verifier_guard_002",
        checks=[
            {
                "check_id": "build_structural",
                "check_type": "build",
                "command": 'python -c "import sys; raise SystemExit(0)"',
                "pass_condition": "exit_code==0",
                "deterministic": True,
                "timeout_sec": 30,
            },
            {
                "check_id": "behavior_runtime_probe",
                "check_type": "test",
                "command": 'python -c "import sys; raise SystemExit(0)"',
                "pass_condition": "exit_code==0",
                "deterministic": True,
                "timeout_sec": 30,
            },
            {
                "check_id": "regression_smoke",
                "check_type": "test",
                "command": 'python -c "import sys; raise SystemExit(0)"',
                "pass_condition": "exit_code==0",
                "deterministic": True,
                "timeout_sec": 30,
            },
        ],
    )

    result = run_python(
        "scripts/verification_runner.py",
        "--packet",
        packet_path,
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    tree = etree.parse(str(_latest_result(runtime_root)))
    assert _text(tree, "/p:pxml/p:payload/p:proof_coverage/p:structural") == "PASS"
    assert _text(tree, "/p:pxml/p:payload/p:proof_coverage/p:behavioral") == "PASS"
    assert _text(tree, "/p:pxml/p:payload/p:proof_coverage/p:regression") == "PASS"
    assert _text(tree, "/p:pxml/p:payload/p:final_verdict") == "pass"


def test_environment_limited_behavioral_verification_is_inconclusive(
    tmp_path: Path,
    run_python,
) -> None:
    runtime_root = tmp_path / "runtime"
    packet_path = tmp_path / "packet_env_limited.pxml"
    _build_packet(
        packet_path,
        task_id="task_verifier_guard_env_001",
        run_id="run_verifier_guard_003",
        checks=[
            {
                "check_id": "behavior_runtime_probe",
                "check_type": "test",
                "command": 'python -c "import sys; raise SystemExit(0)"',
                "pass_condition": "exit_code==0",
                "deterministic": True,
                "timeout_sec": 30,
            }
        ],
        intended_behaviors=["runtime behavior check required"],
        proof_requirements=[
            {"proof_category": "behavioral", "required": True},
        ],
    )

    result = run_python(
        "scripts/verification_runner.py",
        "--packet",
        packet_path,
        "--runtime-root",
        runtime_root,
        "--dry-run",
        "--skip-validate",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    tree = etree.parse(str(_latest_result(runtime_root)))
    assert _text(tree, "/p:pxml/p:payload/p:final_verdict") == "inconclusive"
    verdict_reason = _text(tree, "/p:pxml/p:payload/p:verdict_reason") or ""
    assert "Environment-limited verification" in verdict_reason
    unverified = tree.xpath(
        "/p:pxml/p:payload/p:unverified_areas/p:item/text()", namespaces=NS
    )
    assert any("category=behavioral" in item for item in unverified)
    assert any("blocked=dry_run_skipped" in item for item in unverified)


def test_acceptance_lock_mismatch_is_rejected(
    tmp_path: Path,
    run_python,
) -> None:
    runtime_root = tmp_path / "runtime"
    packet_path = tmp_path / "packet_lock_mismatch.pxml"
    _build_packet(
        packet_path,
        task_id="task_verifier_guard_lock_001",
        run_id="run_verifier_guard_004",
        checks=[
            {
                "check_id": "build_structural_only",
                "check_type": "build",
                "command": 'python -c "import sys; raise SystemExit(0)"',
                "pass_condition": "exit_code==0",
                "deterministic": True,
                "timeout_sec": 30,
            }
        ],
        acceptance_lock_hash_override="f" * 64,
    )

    result = run_python(
        "scripts/verification_runner.py",
        "--packet",
        packet_path,
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "acceptance_lock_hash does not match" in (result.stdout + result.stderr)
