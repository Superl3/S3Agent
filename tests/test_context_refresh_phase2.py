from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from lxml import etree

NS = {"p": "urn:pxml:v1"}
NS_URI = "urn:pxml:v1"


def q(tag: str) -> str:
    return f"{{{NS_URI}}}{tag}"


def _load_script_module(module_name: str):
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / module_name
    spec = importlib.util.spec_from_file_location(f"test_{module_name}", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_task_intake(
    path: Path,
    task_id: str,
    request_text: str,
    outcome: str,
    task_type: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
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
    <request_text>{request_text}</request_text>
    <task_type>{task_type}</task_type>
    <requested_outcome>{outcome}</requested_outcome>
    <constraints>
      <item>keep-runtime-safe</item>
    </constraints>
    <risk_hint>medium</risk_hint>
  </payload>
  <integrity>
    <content_sha256>0000000000000000000000000000000000000000000000000000000000000000</content_sha256>
  </integrity>
</pxml>
"""
    path.write_text(content, encoding="utf-8")


def _text(tree: etree._ElementTree, expr: str) -> str | None:
    values = tree.xpath(expr, namespaces=NS)
    if not values:
        return None
    value = values[0]
    if isinstance(value, etree._Element):
        text = value.text
    else:
        text = str(value)
    if text is None:
        return None
    normalized = text.strip()
    return normalized or None


def _items(tree: etree._ElementTree, expr: str) -> list[str]:
    values = tree.xpath(expr, namespaces=NS)
    items: list[str] = []
    for value in values:
        if isinstance(value, etree._Element):
            text = value.text
        else:
            text = str(value)
        if text is None:
            continue
        normalized = text.strip()
        if normalized:
            items.append(normalized)
    return items


def _latest(runtime_root: Path, task_id: str, suffix: str) -> Path:
    return runtime_root / "latest" / f"{task_id}_{suffix}.pxml"


def _seed_exploration_result(
    runtime_root: Path,
    *,
    task_id: str,
    packet_doc_id: str,
    doc_id: str,
    actionability: str,
) -> Path:
    root = etree.Element(q("pxml"), nsmap={None: NS_URI})
    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = doc_id
    etree.SubElement(meta, q("doc_class")).text = "exploration_result"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = task_id
    etree.SubElement(meta, q("run_id")).text = f"run_{task_id[5:]}"
    etree.SubElement(meta, q("sequence")).text = "3"
    etree.SubElement(meta, q("writer_agent")).text = "explorer"
    etree.SubElement(meta, q("created_at")).text = "2026-03-23T00:00:03Z"

    refs = etree.SubElement(root, q("refs"))
    packet_ref = etree.SubElement(refs, q("ref"))
    etree.SubElement(packet_ref, q("doc_id")).text = packet_doc_id
    etree.SubElement(packet_ref, q("doc_class")).text = "execution_packet"
    etree.SubElement(packet_ref, q("relation")).text = "exploration_target"

    payload = etree.SubElement(root, q("payload"))
    payload_packet = etree.SubElement(payload, q("packet_ref"))
    etree.SubElement(payload_packet, q("doc_id")).text = packet_doc_id
    etree.SubElement(payload_packet, q("doc_class")).text = "execution_packet"
    etree.SubElement(payload_packet, q("relation")).text = "exploration_target"
    etree.SubElement(payload, q("task_id")).text = task_id
    etree.SubElement(payload, q("exploration_kind")).text = "investigation"
    etree.SubElement(payload, q("exploration_scope")).text = "baseline"
    etree.SubElement(payload, q("actionability")).text = actionability
    etree.SubElement(payload, q("target_root")).text = "C:/tmp/workspace"
    providers = etree.SubElement(payload, q("providers"))
    provider = etree.SubElement(providers, q("provider"))
    etree.SubElement(provider, q("name")).text = "text_search"
    etree.SubElement(provider, q("used")).text = "true"
    etree.SubElement(provider, q("success")).text = "true"
    etree.SubElement(provider, q("notes")).text = "seed"
    focus = etree.SubElement(payload, q("focus_questions"))
    etree.SubElement(focus, q("item")).text = "Which file owns this area?"
    findings = etree.SubElement(payload, q("key_findings"))
    etree.SubElement(findings, q("item")).text = "Relevant file: src/target_bugfix.py"
    evidence_items = etree.SubElement(payload, q("evidence_items"))
    evidence = etree.SubElement(evidence_items, q("evidence"))
    etree.SubElement(evidence, q("source_provider")).text = "text_search"
    etree.SubElement(evidence, q("path")).text = "src/target_bugfix.py"
    etree.SubElement(evidence, q("summary")).text = "seed evidence"
    next_actions = etree.SubElement(payload, q("recommended_next_actions"))
    etree.SubElement(next_actions, q("item")).text = "inspect seeded file"
    etree.SubElement(payload, q("completion_state")).text = "completed_and_verified"
    etree.SubElement(payload, q("escalation_requested")).text = "false"
    notes = etree.SubElement(payload, q("notes"))
    etree.SubElement(notes, q("item")).text = "seeded exploration"

    integrity = etree.SubElement(root, q("integrity"))
    etree.SubElement(integrity, q("content_sha256")).text = "1" * 64
    etree.SubElement(integrity, q("parent_sha256")).text = "2" * 64

    results_dir = runtime_root / "exploration" / "results"
    latest_dir = runtime_root / "latest"
    results_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{doc_id}.pxml"
    etree.ElementTree(root).write(
        str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True
    )
    etree.ElementTree(root).write(
        str(latest_dir / f"{task_id}_exploration_result.pxml"),
        encoding="UTF-8",
        xml_declaration=True,
        pretty_print=True,
    )
    return path


def test_implementer_modify_target_missing_creates_context_refresh(
    tmp_path: Path, run_python
) -> None:
    task_id = "task_phase2_impl_refresh_001"
    runtime_root = tmp_path / "runtime"
    workspace_root = tmp_path / "workspace"
    intake_path = tmp_path / "intake" / f"{task_id}.pxml"
    workspace_root.mkdir(parents=True, exist_ok=True)

    _write_task_intake(
        intake_path,
        task_id,
        request_text="Fix auth bug in target file.",
        outcome="Apply bugfix patch safely.",
        task_type="bugfix",
    )
    build_result = run_python(
        "scripts/packet_builder.py",
        "--intake",
        intake_path,
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert build_result.returncode == 0, build_result.stdout + build_result.stderr

    packet_path = _latest(runtime_root, task_id, "execution_packet")
    packet_tree = etree.parse(str(packet_path))
    packet_doc_id = _text(packet_tree, "/p:pxml/p:meta/p:doc_id")
    assert packet_doc_id is not None

    _seed_exploration_result(
        runtime_root,
        task_id=task_id,
        packet_doc_id=packet_doc_id,
        doc_id="doc_exploration_seed_impl_0001",
        actionability="manager_reusable",
    )

    rebuild = run_python(
        "scripts/packet_builder.py",
        "--intake",
        intake_path,
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert rebuild.returncode == 0, rebuild.stdout + rebuild.stderr
    packet_path = _latest(runtime_root, task_id, "execution_packet")

    impl = run_python(
        "scripts/implementer_runner.py",
        "--packet",
        packet_path,
        "--runtime-root",
        runtime_root,
        "--workspace-root",
        workspace_root,
        "--skip-validate",
    )
    assert impl.returncode == 1, impl.stdout + impl.stderr
    request_path = _latest(runtime_root, task_id, "exploration_request")
    result_path = _latest(runtime_root, task_id, "exploration_result")
    assert request_path.exists()
    assert result_path.exists()
    focused_tree = etree.parse(str(result_path))
    assert (
        _text(focused_tree, "/p:pxml/p:payload/p:exploration_scope")
        == "focused_refresh"
    )
    assert (
        _text(focused_tree, "/p:pxml/p:payload/p:actionability")
        == "contract_refresh_required"
    )
    impl_tree = etree.parse(str(_latest(runtime_root, task_id, "implementer_result")))
    ref_classes = _items(impl_tree, "/p:pxml/p:refs/p:ref/p:doc_class/text()")
    assert "exploration_request" in ref_classes
    assert "exploration_result" in ref_classes
    assert _text(impl_tree, "/p:pxml/p:payload/p:escalation_requested") == "true"


def test_implementer_write_intent_disabled_does_not_create_context_refresh(
    tmp_path: Path, run_python
) -> None:
    task_id = "task_phase2_impl_denied_001"
    runtime_root = tmp_path / "runtime"
    intake_path = tmp_path / "intake" / f"{task_id}.pxml"
    tmp_path.mkdir(parents=True, exist_ok=True)

    _write_task_intake(
        intake_path,
        task_id,
        request_text="read-only exploration only; do not modify files",
        outcome="investigate only",
        task_type="docs",
    )
    build_result = run_python(
        "scripts/packet_builder.py",
        "--intake",
        intake_path,
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert build_result.returncode == 0, build_result.stdout + build_result.stderr
    packet_path = _latest(runtime_root, task_id, "execution_packet")

    impl = run_python(
        "scripts/implementer_runner.py",
        "--packet",
        packet_path,
        "--runtime-root",
        runtime_root,
        "--workspace-root",
        tmp_path,
        "--skip-validate",
    )
    assert impl.returncode == 1, impl.stdout + impl.stderr
    assert not _latest(runtime_root, task_id, "exploration_request").exists()


def test_verifier_inconclusive_creates_context_refresh(
    tmp_path: Path, run_python
) -> None:
    task_id = "task_phase2_verify_refresh_001"
    runtime_root = tmp_path / "runtime"
    workspace_root = tmp_path / "workspace"
    intake_path = tmp_path / "intake" / f"{task_id}.pxml"
    (workspace_root / "src").mkdir(parents=True, exist_ok=True)
    (workspace_root / "src" / "feature.ts").write_text(
        "export const feature = true;\n", encoding="utf-8"
    )

    _write_task_intake(
        intake_path,
        task_id,
        request_text="Implement feature safely with verification.",
        outcome="Ship a small feature with tests.",
        task_type="feature",
    )
    build_result = run_python(
        "scripts/packet_builder.py",
        "--intake",
        intake_path,
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert build_result.returncode == 0, build_result.stdout + build_result.stderr

    packet_path = _latest(runtime_root, task_id, "execution_packet")
    packet_tree = etree.parse(str(packet_path))
    packet_doc_id = _text(packet_tree, "/p:pxml/p:meta/p:doc_id")
    assert packet_doc_id is not None
    _seed_exploration_result(
        runtime_root,
        task_id=task_id,
        packet_doc_id=packet_doc_id,
        doc_id="doc_exploration_seed_verifier_0001",
        actionability="manager_reusable",
    )
    rebuild = run_python(
        "scripts/packet_builder.py",
        "--intake",
        intake_path,
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert rebuild.returncode == 0, rebuild.stdout + rebuild.stderr
    packet_path = _latest(runtime_root, task_id, "execution_packet")

    verify = run_python(
        "scripts/verification_runner.py",
        "--packet",
        packet_path,
        "--runtime-root",
        runtime_root,
        "--workspace-root",
        workspace_root,
        "--dry-run",
        "--skip-validate",
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr
    request_path = _latest(runtime_root, task_id, "exploration_request")
    result_path = _latest(runtime_root, task_id, "exploration_result")
    assert request_path.exists()
    assert result_path.exists()
    verify_tree = etree.parse(
        str(_latest(runtime_root, task_id, "verification_result"))
    )
    assert _text(verify_tree, "/p:pxml/p:payload/p:final_verdict") == "inconclusive"
    ref_classes = _items(verify_tree, "/p:pxml/p:refs/p:ref/p:doc_class/text()")
    assert "exploration_request" in ref_classes
    assert "exploration_result" in ref_classes


def test_harness_validator_collects_context_refresh_request_artifacts(
    tmp_path: Path, run_python
) -> None:
    task_id = "task_phase2_harness_refresh_001"
    runtime_root = tmp_path / "runtime"
    workspace_root = tmp_path / "workspace"
    intake_path = tmp_path / "intake" / f"{task_id}.pxml"
    (workspace_root / "src").mkdir(parents=True, exist_ok=True)
    (workspace_root / "src" / "feature.ts").write_text(
        "export const feature = true;\n", encoding="utf-8"
    )

    _write_task_intake(
        intake_path,
        task_id,
        request_text="Implement feature safely with verification.",
        outcome="Ship a small feature with tests.",
        task_type="feature",
    )
    build_result = run_python(
        "scripts/packet_builder.py",
        "--intake",
        intake_path,
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert build_result.returncode == 0, build_result.stdout + build_result.stderr

    packet_path = _latest(runtime_root, task_id, "execution_packet")
    packet_tree = etree.parse(str(packet_path))
    packet_doc_id = _text(packet_tree, "/p:pxml/p:meta/p:doc_id")
    assert packet_doc_id is not None
    _seed_exploration_result(
        runtime_root,
        task_id=task_id,
        packet_doc_id=packet_doc_id,
        doc_id="doc_exploration_seed_harness_0001",
        actionability="manager_reusable",
    )
    rebuild = run_python(
        "scripts/packet_builder.py",
        "--intake",
        intake_path,
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert rebuild.returncode == 0, rebuild.stdout + rebuild.stderr

    verify = run_python(
        "scripts/verification_runner.py",
        "--packet",
        _latest(runtime_root, task_id, "execution_packet"),
        "--runtime-root",
        runtime_root,
        "--workspace-root",
        workspace_root,
        "--dry-run",
        "--skip-validate",
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr

    harness_validator = _load_script_module("harness_validator.py")
    artifacts = harness_validator.collect_task_artifacts(runtime_root, task_id)
    doc_classes = {artifact.doc_class for artifact in artifacts}
    assert "exploration_request" in harness_validator.FLOW_DOC_CLASSES
    assert "exploration_request" in doc_classes

    repo_root = Path(__file__).resolve().parents[1]
    validator_path = repo_root / "scripts" / "pxml_validator.py"
    valid, output = harness_validator.validate_artifacts_with_pxml_validator(
        validator_path, artifacts
    )
    assert valid, output
