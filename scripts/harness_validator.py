#!/usr/bin/env python3
"""Batch 3 harness-level artifact flow validator.

Validates task-scoped runtime artifacts and reports pass/fail/inconclusive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

try:
    from lxml import etree
except ModuleNotFoundError:
    print(
        "ERROR: lxml is required. Install with: python -m pip install lxml",
        file=sys.stderr,
    )
    raise SystemExit(3)


NS = "urn:pxml:v1"
XPATH_NS = {"p": NS}

FLOW_DOC_CLASSES = {
    "task_intake",
    "manager_route",
    "execution_packet",
    "plan_sidecar",
    "review_sidecar",
    "implementer_result",
    "verification_result",
    "execution_trace",
    "task_status_report",
    "compaction_checkpoint",
    "operator_preflight_report",
    "final_render_report",
    "session_report",
    "pruning_report",
}


@dataclass
class Artifact:
    path: Path
    doc_id: str
    doc_class: str
    task_id: str
    sequence: int
    created_at: str
    tree: etree._ElementTree


@dataclass
class EscalationPolicyConfig:
    repeated_failure_threshold: int = 3
    stop_after_escalation: bool = True


@dataclass
class RetryPolicyConfig:
    same_cause_fast_escalation_threshold: int = 2


@dataclass
class TraceEventRecord:
    event_seq: int
    event_type: str
    reason_code: Optional[str]
    attempt: Optional[int]
    lineage_lock_sha256: Optional[str]
    verify_phase: Optional[str]
    ref_doc_ids: List[str]
    ref_doc_classes: List[str]


def text_at(tree: etree._ElementTree, xpath_expr: str) -> Optional[str]:
    nodes = tree.xpath(xpath_expr, namespaces=XPATH_NS)
    if not nodes:
        return None
    first = nodes[0]
    if isinstance(first, etree._Element):
        text = first.text
    else:
        text = str(first)
    if text is None:
        return None
    text = text.strip()
    return text or None


def discover_pxml_files(path: Path) -> List[Path]:
    if not path.exists():
        return []
    files = [candidate for candidate in path.rglob("*.pxml") if candidate.is_file()]
    files.sort()
    return files


def parse_artifact(path: Path) -> Optional[Artifact]:
    try:
        tree = etree.parse(str(path))
    except etree.XMLSyntaxError:
        return None
    doc_id = text_at(tree, "/p:pxml/p:meta/p:doc_id")
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    task_id = text_at(tree, "/p:pxml/p:meta/p:task_id")
    seq_text = text_at(tree, "/p:pxml/p:meta/p:sequence")
    created_at = text_at(tree, "/p:pxml/p:meta/p:created_at")
    if (
        doc_id is None
        or doc_class is None
        or task_id is None
        or seq_text is None
        or created_at is None
    ):
        return None
    try:
        sequence = int(seq_text)
    except ValueError:
        sequence = 0
    return Artifact(
        path=path,
        doc_id=doc_id,
        doc_class=doc_class,
        task_id=task_id,
        sequence=sequence,
        created_at=created_at,
        tree=tree,
    )


def collect_task_artifacts(runtime_root: Path, task_id: str) -> List[Artifact]:
    candidates: List[Path] = []
    scan_dirs = [
        runtime_root / "inbox" / "task_intake",
        runtime_root / "packets" / "manager_route",
        runtime_root / "packets" / "execution_packet",
        runtime_root / "implementer" / "results",
        runtime_root / "sidecars" / "planner",
        runtime_root / "sidecars" / "reviewer",
        runtime_root / "sidecars" / "verifier",
        runtime_root / "verification" / "results",
        runtime_root / "traces" / "by_task",
        runtime_root / "status" / "reports",
        runtime_root / "compaction" / "checkpoints",
        runtime_root / "preflight" / "reports",
        runtime_root / "rendered" / "reports",
        runtime_root / "ops" / "session_reports",
        runtime_root / "pruning" / "reports",
    ]
    for directory in scan_dirs:
        candidates.extend(discover_pxml_files(directory))

    artifacts: List[Artifact] = []
    for path in candidates:
        artifact = parse_artifact(path)
        if artifact is None:
            continue
        if artifact.task_id != task_id:
            continue
        artifacts.append(artifact)
    artifacts.sort(key=lambda item: (item.doc_class, item.sequence, str(item.path)))
    return artifacts


def latest_by_class(
    artifacts: Sequence[Artifact], doc_class: str
) -> Optional[Artifact]:
    class_items = [item for item in artifacts if item.doc_class == doc_class]
    if not class_items:
        return None
    class_items.sort(key=lambda item: (item.sequence, item.created_at, str(item.path)))
    return class_items[-1]


def refs_of(tree: etree._ElementTree) -> List[Tuple[str, Optional[str], Optional[str]]]:
    refs: List[Tuple[str, Optional[str], Optional[str]]] = []
    ref_nodes = tree.xpath("/p:pxml/p:refs/p:ref", namespaces=XPATH_NS)
    for node in ref_nodes:
        node_tree = etree.ElementTree(node)
        doc_id = text_at(node_tree, "./p:doc_id")
        doc_class = text_at(node_tree, "./p:doc_class")
        relation = text_at(node_tree, "./p:relation")
        if doc_id is not None:
            refs.append((doc_id, doc_class, relation))
    return refs


def acceptance_lock_hash(packet: Artifact) -> Optional[str]:
    check_nodes = packet.tree.xpath(
        "/p:pxml/p:payload/p:acceptance_checks/p:check", namespaces=XPATH_NS
    )
    checks: List[Dict[str, object]] = []
    for node in check_nodes:
        node_tree = etree.ElementTree(node)
        check_id = text_at(node_tree, "./p:check_id")
        check_type = text_at(node_tree, "./p:check_type")
        command = text_at(node_tree, "./p:command")
        pass_condition = text_at(node_tree, "./p:pass_condition")
        deterministic_text = text_at(node_tree, "./p:deterministic")
        timeout_text = text_at(node_tree, "./p:timeout_sec")
        if (
            check_id is None
            or check_type is None
            or command is None
            or pass_condition is None
            or deterministic_text is None
            or timeout_text is None
        ):
            return None
        try:
            timeout = int(timeout_text)
        except ValueError:
            return None
        checks.append(
            {
                "check_id": check_id,
                "check_type": check_type,
                "command": command,
                "pass_condition": pass_condition,
                "deterministic": deterministic_text.lower() == "true",
                "timeout_sec": timeout,
            }
        )

    if not checks:
        return None
    encoded = json.dumps(checks, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_artifacts_with_pxml_validator(
    validator: Path,
    artifacts: Sequence[Artifact],
) -> Tuple[bool, str]:
    if not artifacts:
        return False, "No artifacts to validate"

    with tempfile.TemporaryDirectory(prefix="pxml_harness_validate_") as temp_dir:
        temp_root = Path(temp_dir)
        by_doc_id: Dict[str, Artifact] = {}
        for artifact in artifacts:
            existing = by_doc_id.get(artifact.doc_id)
            if existing is None or artifact.sequence >= existing.sequence:
                by_doc_id[artifact.doc_id] = artifact

        copied_paths: List[Path] = []
        for artifact in sorted(
            by_doc_id.values(),
            key=lambda item: (item.doc_class, item.sequence, item.doc_id),
        ):
            target = temp_root / artifact.path.name
            shutil.copy2(artifact.path, target)
            copied_paths.append(target)

        cmd = [
            sys.executable,
            str(validator),
            str(temp_root),
            "--context-dir",
            str(temp_root),
        ]
        run = subprocess.run(cmd, check=False, capture_output=True, text=True)
        output = (run.stdout or "") + (run.stderr or "")
        return run.returncode == 0, output.strip()


def load_escalation_policy(path: Path) -> EscalationPolicyConfig:
    config = EscalationPolicyConfig()
    if not path.exists():
        return config
    try:
        tree = etree.parse(str(path))
    except etree.XMLSyntaxError:
        return config
    repeated = text_at(tree, "/p:pxml/p:payload/p:repeated_failure_threshold")
    stop_after = text_at(tree, "/p:pxml/p:payload/p:stop_after_escalation")
    if repeated and repeated.isdigit():
        config.repeated_failure_threshold = int(repeated)
    if stop_after is not None:
        config.stop_after_escalation = stop_after.lower() == "true"
    return config


def load_retry_policy(path: Path) -> RetryPolicyConfig:
    config = RetryPolicyConfig()
    if not path.exists():
        return config
    try:
        tree = etree.parse(str(path))
    except etree.XMLSyntaxError:
        return config
    same_cause = text_at(
        tree, "/p:pxml/p:payload/p:same_cause_fast_escalation_threshold"
    )
    if same_cause and same_cause.isdigit():
        config.same_cause_fast_escalation_threshold = int(same_cause)
    return config


def parse_trace_events(trace: Artifact) -> List[TraceEventRecord]:
    records: List[TraceEventRecord] = []
    nodes = trace.tree.xpath("/p:pxml/p:payload/p:events/p:event", namespaces=XPATH_NS)
    for index, node in enumerate(nodes, start=1):
        node_tree = etree.ElementTree(node)
        event_type = text_at(node_tree, "./p:event_type")
        if event_type is None:
            continue
        seq_text = text_at(node_tree, "./p:event_seq")
        event_seq = int(seq_text) if seq_text and seq_text.isdigit() else index
        reason_code = text_at(node_tree, "./p:reason_code")
        attempt_text = text_at(node_tree, "./p:attempt")
        attempt = int(attempt_text) if attempt_text and attempt_text.isdigit() else None
        lineage_lock = text_at(node_tree, "./p:lineage_lock_sha256")
        verify_phase = text_at(node_tree, "./p:verify_phase")

        ref_doc_ids: List[str] = []
        ref_doc_classes: List[str] = []
        ref_nodes = node.xpath("./p:artifact_refs/p:ref", namespaces=XPATH_NS)
        for ref_node in ref_nodes:
            ref_tree = etree.ElementTree(ref_node)
            ref_id = text_at(ref_tree, "./p:doc_id")
            ref_class = text_at(ref_tree, "./p:doc_class")
            if ref_id:
                ref_doc_ids.append(ref_id)
            if ref_class:
                ref_doc_classes.append(ref_class)

        records.append(
            TraceEventRecord(
                event_seq=event_seq,
                event_type=event_type,
                reason_code=reason_code,
                attempt=attempt,
                lineage_lock_sha256=lineage_lock,
                verify_phase=verify_phase,
                ref_doc_ids=ref_doc_ids,
                ref_doc_classes=ref_doc_classes,
            )
        )
    return records


def parse_release_link(path: Path) -> Optional[Artifact]:
    if not path.exists():
        return None
    return parse_artifact(path)


def load_failure_taxonomy_codes(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        tree = etree.parse(str(path))
    except etree.XMLSyntaxError:
        return set()
    values = tree.xpath(
        "/p:pxml/p:payload/p:reasons/p:reason/p:code/text()", namespaces=XPATH_NS
    )
    return {value.strip() for value in values if value and value.strip()}


def load_failure_index_reason_codes(runtime_root: Path, task_id: str) -> List[str]:
    token = sanitize(task_id)
    index_path = runtime_root / "index" / "failures" / f"{token}.json"
    if not index_path.exists():
        return []
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return []
    reasons: List[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        reason = entry.get("reason_code")
        if isinstance(reason, str) and reason.strip():
            reasons.append(reason.strip())
    return reasons


def load_quarantine_flags(runtime_root: Path, task_id: str) -> List[str]:
    manifest_dir = runtime_root / "quarantine" / "manifests"
    if not manifest_dir.exists():
        return []
    flags: List[str] = []
    for path in sorted(manifest_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        entries = payload.get("entries")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("task_id") != task_id:
                continue
            reasons = entry.get("reasons")
            if isinstance(reasons, list):
                for reason in reasons:
                    if isinstance(reason, str) and reason.strip():
                        flags.append(reason.strip())
            else:
                flags.append("quarantine_entry")
    return sorted(set(flags))


def sanitize(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9._-]", "_", lowered)
    lowered = re.sub(r"_+", "_", lowered).strip("_")
    return lowered or "id"


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Validate task-level harness artifact flow."
    )
    parser.add_argument("--task-id", required=True, help="Target task_id.")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=repo_root / "runtime",
        help="Runtime root path.",
    )
    parser.add_argument(
        "--validator",
        type=Path,
        default=repo_root / "scripts" / "pxml_validator.py",
        help="PXML validator path.",
    )
    parser.add_argument(
        "--escalation-policy",
        type=Path,
        default=repo_root / "instructions" / "escalation_policy.pxml",
        help="Escalation policy artifact path.",
    )
    parser.add_argument(
        "--retry-policy",
        type=Path,
        default=repo_root / "instructions" / "retry_policy.pxml",
        help="Retry policy artifact path.",
    )
    parser.add_argument(
        "--failure-taxonomy",
        type=Path,
        default=repo_root / "instructions" / "failure_reason_taxonomy.pxml",
        help="Failure reason taxonomy artifact path.",
    )
    parser.add_argument(
        "--release-readiness",
        action="store_true",
        help="Evaluate strict release-readiness mode (pass/fail/inconclusive).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime_root = args.runtime_root.resolve()
    validator = args.validator.resolve()
    escalation_policy = load_escalation_policy(args.escalation_policy.resolve())
    retry_policy = load_retry_policy(args.retry_policy.resolve())
    failure_taxonomy_path = args.failure_taxonomy.resolve()
    taxonomy_codes = load_failure_taxonomy_codes(failure_taxonomy_path)

    if not validator.exists():
        print(f"FAIL: validator script not found: {validator}")
        return 1

    artifacts = collect_task_artifacts(runtime_root, args.task_id)
    if not artifacts:
        print(f"FAIL: no runtime artifacts found for task_id={args.task_id}")
        return 1

    hard_failures: List[str] = []
    inconclusive: List[str] = []

    if not failure_taxonomy_path.exists():
        hard_failures.append(
            f"Missing failure_reason_taxonomy artifact: {failure_taxonomy_path}"
        )
    elif not taxonomy_codes:
        hard_failures.append("failure_reason_taxonomy has no reason code entries.")

    validator_ok, validator_output = validate_artifacts_with_pxml_validator(
        validator, artifacts
    )
    if not validator_ok:
        hard_failures.append("At least one artifact failed schema/rule validation.")

    intake = latest_by_class(artifacts, "task_intake")
    route = latest_by_class(artifacts, "manager_route")
    packet = latest_by_class(artifacts, "execution_packet")
    implementer = latest_by_class(artifacts, "implementer_result")
    trace = latest_by_class(artifacts, "execution_trace")
    planner = latest_by_class(artifacts, "plan_sidecar")
    reviewer = latest_by_class(artifacts, "review_sidecar")
    verification = latest_by_class(artifacts, "verification_result")
    status_report = latest_by_class(artifacts, "task_status_report")
    compaction_checkpoint = latest_by_class(artifacts, "compaction_checkpoint")
    preflight_report = latest_by_class(artifacts, "operator_preflight_report")
    final_render_report = latest_by_class(artifacts, "final_render_report")
    session_report = latest_by_class(artifacts, "session_report")
    pruning_report = latest_by_class(artifacts, "pruning_report")
    quarantine_flags = load_quarantine_flags(runtime_root, args.task_id)

    if intake is None:
        hard_failures.append("Missing task_intake artifact.")
    if route is None:
        hard_failures.append("Missing manager_route artifact.")
    if packet is None:
        hard_failures.append("Missing execution_packet artifact.")
    if trace is None:
        hard_failures.append("Missing execution_trace artifact.")
    if status_report is None:
        inconclusive.append("Missing task_status_report artifact.")
    if quarantine_flags:
        inconclusive.append(
            "Task has quarantine manifest flags: " + ", ".join(quarantine_flags)
        )

    docs_by_id = {artifact.doc_id: artifact for artifact in artifacts}
    implementer_status = (
        text_at(implementer.tree, "/p:pxml/p:payload/p:result_status")
        if implementer is not None
        else None
    )

    if route is not None and intake is not None:
        route_refs = refs_of(route.tree)
        intake_refs = [item for item in route_refs if item[1] == "task_intake"]
        if len(intake_refs) != 1:
            hard_failures.append(
                "manager_route must reference exactly one task_intake."
            )
        elif intake_refs[0][0] != intake.doc_id:
            hard_failures.append(
                "manager_route task_intake ref does not match latest intake artifact."
            )

    if packet is not None and route is not None:
        packet_refs = refs_of(packet.tree)
        route_refs = [item for item in packet_refs if item[1] == "manager_route"]
        if len(route_refs) != 1:
            hard_failures.append(
                "execution_packet must reference exactly one manager_route."
            )
        elif route_refs[0][0] != route.doc_id:
            hard_failures.append(
                "execution_packet manager_route ref does not match latest route artifact."
            )

    if packet is not None and route is not None:
        packet_hash = acceptance_lock_hash(packet)
        packet_declared_lock = text_at(
            packet.tree, "/p:pxml/p:payload/p:acceptance_lock_hash"
        )
        route_lock = text_at(
            route.tree, "/p:pxml/p:payload/p:acceptance_lock/p:lock_sha256"
        )
        if packet_hash is None:
            hard_failures.append(
                "Unable to compute acceptance hash from execution_packet checks."
            )
        elif packet_declared_lock is None:
            hard_failures.append(
                "execution_packet missing payload acceptance_lock_hash."
            )
        elif packet_hash != packet_declared_lock:
            hard_failures.append(
                "execution_packet acceptance_lock_hash does not match computed acceptance checks hash."
            )
        elif route_lock is None:
            hard_failures.append("manager_route missing acceptance_lock.lock_sha256.")
        elif packet_declared_lock != route_lock:
            hard_failures.append(
                "Acceptance lock hash mismatch between manager_route and execution_packet."
            )

    if implementer is not None and packet is not None:
        impl_refs = refs_of(implementer.tree)
        packet_refs = [item for item in impl_refs if item[1] == "execution_packet"]
        if len(packet_refs) != 1:
            hard_failures.append(
                "implementer_result must reference exactly one execution_packet."
            )
        elif packet_refs[0][0] != packet.doc_id:
            hard_failures.append(
                "implementer_result does not reference latest execution_packet."
            )

        payload_packet_ref = text_at(
            implementer.tree, "/p:pxml/p:payload/p:packet_ref/p:doc_id"
        )
        payload_packet_class = text_at(
            implementer.tree, "/p:pxml/p:payload/p:packet_ref/p:doc_class"
        )
        if (
            payload_packet_ref != packet.doc_id
            or payload_packet_class != "execution_packet"
        ):
            hard_failures.append(
                "implementer_result payload packet_ref must match latest execution_packet.",
            )

        impl_task_id = text_at(implementer.tree, "/p:pxml/p:payload/p:task_id")
        if impl_task_id != args.task_id:
            hard_failures.append("implementer_result payload task_id mismatch.")

        impl_status = implementer_status
        blocked_reason = text_at(implementer.tree, "/p:pxml/p:payload/p:blocked_reason")
        retry_text = text_at(implementer.tree, "/p:pxml/p:payload/p:retry_count")
        escalation_requested = text_at(
            implementer.tree, "/p:pxml/p:payload/p:escalation_requested"
        )
        evidence_items = implementer.tree.xpath(
            "/p:pxml/p:payload/p:patch_evidence_refs/p:item", namespaces=XPATH_NS
        )
        if impl_status == "applied" and len(evidence_items) == 0:
            hard_failures.append(
                "implementer_result status=applied requires patch_evidence_refs.",
            )
        if (
            impl_status in {"blocked", "retry_failed", "escalated"}
            and blocked_reason is None
        ):
            hard_failures.append(
                "implementer_result blocked/retry_failed/escalated requires blocked_reason.",
            )
        if impl_status == "retry_failed":
            retry_count = int(retry_text) if retry_text and retry_text.isdigit() else 0
            if retry_count < 1:
                hard_failures.append(
                    "implementer_result retry_failed requires retry_count >= 1.",
                )
            if escalation_requested != "true":
                hard_failures.append(
                    "implementer_result retry_failed requires escalation_requested=true.",
                )

    selected_path = (
        text_at(route.tree, "/p:pxml/p:payload/p:selected_path")
        if route is not None
        else None
    )
    if selected_path in {"planner_pre", "full_lane"} and planner is None:
        inconclusive.append(
            "Route requires planner sidecar but no plan_sidecar artifact was found."
        )
    if selected_path in {"reviewer_post", "full_lane"} and reviewer is None:
        inconclusive.append(
            "Route requires reviewer sidecar but no review_sidecar artifact was found."
        )
    if selected_path in {"verifier_post", "full_lane"} and verification is None:
        inconclusive.append(
            "Route requires verification_result but verifier artifact was not found."
        )

    if status_report is not None:
        report_task_id = text_at(status_report.tree, "/p:pxml/p:payload/p:task_id")
        if report_task_id != args.task_id:
            hard_failures.append("task_status_report payload task_id mismatch.")

        report_impl_ref = text_at(
            status_report.tree,
            "/p:pxml/p:payload/p:latest_implementer_result_ref/p:doc_id",
        )
        if implementer is not None and report_impl_ref != implementer.doc_id:
            hard_failures.append(
                "task_status_report latest_implementer_result_ref must match latest implementer_result."
            )

        report_verify_ref = text_at(
            status_report.tree,
            "/p:pxml/p:payload/p:latest_verification_ref/p:doc_id",
        )
        if verification is not None and report_verify_ref != verification.doc_id:
            hard_failures.append(
                "task_status_report latest_verification_ref must match latest verification_result."
            )

        report_trace_ref = text_at(
            status_report.tree,
            "/p:pxml/p:payload/p:latest_trace_ref/p:doc_id",
        )
        if trace is not None and report_trace_ref != trace.doc_id:
            hard_failures.append(
                "task_status_report latest_trace_ref must match latest execution_trace."
            )

    if compaction_checkpoint is not None:
        checkpoint_task_id = text_at(
            compaction_checkpoint.tree, "/p:pxml/p:payload/p:task_id"
        )
        if checkpoint_task_id != args.task_id:
            hard_failures.append("compaction_checkpoint payload task_id mismatch.")

        source_trace_ref = text_at(
            compaction_checkpoint.tree,
            "/p:pxml/p:payload/p:source_trace_ref/p:doc_id",
        )
        if trace is not None and source_trace_ref != trace.doc_id:
            hard_failures.append(
                "compaction_checkpoint source_trace_ref must match latest execution_trace."
            )

        source_last_seq = text_at(
            compaction_checkpoint.tree,
            "/p:pxml/p:payload/p:source_trace_last_sequence",
        )
        trace_meta_seq = (
            text_at(trace.tree, "/p:pxml/p:meta/p:sequence")
            if trace is not None
            else None
        )
        if (
            source_last_seq
            and source_last_seq.isdigit()
            and trace_meta_seq
            and trace_meta_seq.isdigit()
            and int(source_last_seq) > int(trace_meta_seq)
        ):
            hard_failures.append(
                "compaction_checkpoint source_trace_last_sequence exceeds trace meta sequence."
            )

        checkpoint_packet_ref = text_at(
            compaction_checkpoint.tree,
            "/p:pxml/p:payload/p:created_from_latest_packet_ref/p:doc_id",
        )
        if packet is not None and checkpoint_packet_ref != packet.doc_id:
            hard_failures.append(
                "compaction_checkpoint created_from_latest_packet_ref must match latest execution_packet."
            )

        checkpoint_route_ref = text_at(
            compaction_checkpoint.tree,
            "/p:pxml/p:payload/p:created_from_latest_route_ref/p:doc_id",
        )
        if route is not None and checkpoint_route_ref != route.doc_id:
            hard_failures.append(
                "compaction_checkpoint created_from_latest_route_ref must match latest manager_route."
            )

        checkpoint_lineage = text_at(
            compaction_checkpoint.tree, "/p:pxml/p:payload/p:lineage_lock_sha256"
        )
        packet_declared_lock = (
            text_at(packet.tree, "/p:pxml/p:payload/p:acceptance_lock_hash")
            if packet is not None
            else None
        )
        if (
            checkpoint_lineage is not None
            and packet_declared_lock is not None
            and checkpoint_lineage != packet_declared_lock
        ):
            hard_failures.append(
                "compaction_checkpoint lineage_lock_sha256 must match latest execution_packet lock."
            )

    if preflight_report is not None:
        preflight_task_id = text_at(
            preflight_report.tree, "/p:pxml/p:payload/p:task_id"
        )
        if preflight_task_id != args.task_id:
            hard_failures.append("operator_preflight_report payload task_id mismatch.")

        preflight_route_ref = text_at(
            preflight_report.tree, "/p:pxml/p:payload/p:latest_route_ref/p:doc_id"
        )
        preflight_packet_ref = text_at(
            preflight_report.tree, "/p:pxml/p:payload/p:latest_packet_ref/p:doc_id"
        )
        preflight_status_ref = text_at(
            preflight_report.tree,
            "/p:pxml/p:payload/p:latest_status_report_ref/p:doc_id",
        )
        preflight_trace_ref = text_at(
            preflight_report.tree, "/p:pxml/p:payload/p:latest_trace_ref/p:doc_id"
        )

        if route is not None and preflight_route_ref != route.doc_id:
            hard_failures.append(
                "operator_preflight_report latest_route_ref must match latest manager_route."
            )
        if packet is not None and preflight_packet_ref != packet.doc_id:
            hard_failures.append(
                "operator_preflight_report latest_packet_ref must match latest execution_packet."
            )
        if status_report is not None and preflight_status_ref != status_report.doc_id:
            hard_failures.append(
                "operator_preflight_report latest_status_report_ref must match latest task_status_report."
            )
        if trace is not None and preflight_trace_ref != trace.doc_id:
            hard_failures.append(
                "operator_preflight_report latest_trace_ref must match latest execution_trace."
            )

        readiness = text_at(
            preflight_report.tree, "/p:pxml/p:payload/p:render_readiness"
        )
        lineage_ok = text_at(preflight_report.tree, "/p:pxml/p:payload/p:lineage_ok")
        status_ok = text_at(preflight_report.tree, "/p:pxml/p:payload/p:status_ok")
        if readiness == "ready" and (lineage_ok != "true" or status_ok != "true"):
            hard_failures.append(
                "operator_preflight_report ready state requires lineage_ok=true and status_ok=true."
            )
        if readiness in {"caution", "not_ready"}:
            inconclusive.append(
                f"operator_preflight_report render_readiness={readiness}."
            )

    if final_render_report is not None:
        render_task_id = text_at(
            final_render_report.tree, "/p:pxml/p:payload/p:task_id"
        )
        if render_task_id != args.task_id:
            hard_failures.append("final_render_report payload task_id mismatch.")

        derived_flag = text_at(final_render_report.tree, "/p:pxml/p:payload/p:derived")
        if derived_flag != "true":
            hard_failures.append("final_render_report payload derived must be true.")

        render_preflight_ref = text_at(
            final_render_report.tree,
            "/p:pxml/p:payload/p:source_preflight_ref/p:doc_id",
        )
        if preflight_report is None:
            hard_failures.append(
                "final_render_report exists but operator_preflight_report is missing."
            )
        elif render_preflight_ref != preflight_report.doc_id:
            hard_failures.append(
                "final_render_report source_preflight_ref must match latest operator_preflight_report."
            )

        render_status_ref = text_at(
            final_render_report.tree,
            "/p:pxml/p:payload/p:source_status_report_ref/p:doc_id",
        )
        render_route_ref = text_at(
            final_render_report.tree,
            "/p:pxml/p:payload/p:source_route_ref/p:doc_id",
        )
        render_packet_ref = text_at(
            final_render_report.tree,
            "/p:pxml/p:payload/p:source_packet_ref/p:doc_id",
        )
        render_trace_ref = text_at(
            final_render_report.tree,
            "/p:pxml/p:payload/p:source_trace_ref/p:doc_id",
        )
        if status_report is not None and render_status_ref != status_report.doc_id:
            hard_failures.append(
                "final_render_report source_status_report_ref must match latest task_status_report."
            )
        if route is not None and render_route_ref != route.doc_id:
            hard_failures.append(
                "final_render_report source_route_ref must match latest manager_route."
            )
        if packet is not None and render_packet_ref != packet.doc_id:
            hard_failures.append(
                "final_render_report source_packet_ref must match latest execution_packet."
            )
        if trace is not None and render_trace_ref != trace.doc_id:
            hard_failures.append(
                "final_render_report source_trace_ref must match latest execution_trace."
            )

        readiness_basis = text_at(
            final_render_report.tree,
            "/p:pxml/p:payload/p:render_readiness_basis",
        )
        render_mode = text_at(
            final_render_report.tree, "/p:pxml/p:payload/p:render_mode"
        )
        render_warnings = {
            item.strip()
            for item in final_render_report.tree.xpath(
                "/p:pxml/p:payload/p:warnings/p:item/text()",
                namespaces=XPATH_NS,
            )
            if isinstance(item, str) and item.strip()
        }

        if preflight_report is not None:
            preflight_readiness = text_at(
                preflight_report.tree, "/p:pxml/p:payload/p:render_readiness"
            )
            if preflight_readiness != readiness_basis:
                hard_failures.append(
                    "final_render_report render_readiness_basis must match latest preflight render_readiness."
                )
            if preflight_readiness == "not_ready" and render_mode != "denied":
                if "override_not_ready" not in render_warnings:
                    hard_failures.append(
                        "not_ready render requires denied mode or explicit override_not_ready warning."
                    )

        exports_pxml = text_at(
            final_render_report.tree,
            "/p:pxml/p:payload/p:generated_exports/p:pxml_path",
        )
        exports_md = text_at(
            final_render_report.tree,
            "/p:pxml/p:payload/p:generated_exports/p:markdown_path",
        )
        if exports_pxml:
            expected_report_path = str(
                final_render_report.path.relative_to(runtime_root)
            ).replace("/", "\\")
            normalized_export = exports_pxml.replace("/", "\\")
            if normalized_export != expected_report_path:
                hard_failures.append(
                    "final_render_report generated_exports pxml_path must match report artifact path."
                )
        if render_mode == "denied" and exports_md is not None:
            hard_failures.append(
                "final_render_report render_mode=denied must not include markdown export."
            )
        if exports_md:
            md_path = runtime_root / exports_md
            if not md_path.exists():
                hard_failures.append(
                    "final_render_report markdown export path does not exist on disk."
                )

    if session_report is not None:
        session_task_id = text_at(session_report.tree, "/p:pxml/p:payload/p:task_id")
        if session_task_id != args.task_id:
            hard_failures.append("session_report payload task_id mismatch.")

        derived_flag = text_at(session_report.tree, "/p:pxml/p:payload/p:derived")
        if derived_flag != "true":
            hard_failures.append("session_report payload derived must be true.")

        session_status_ref = text_at(
            session_report.tree,
            "/p:pxml/p:payload/p:latest_status_report_ref/p:doc_id",
        )
        if status_report is not None and session_status_ref != status_report.doc_id:
            hard_failures.append(
                "session_report latest_status_report_ref must match latest task_status_report."
            )

        session_preflight_ref = text_at(
            session_report.tree,
            "/p:pxml/p:payload/p:latest_preflight_ref/p:doc_id",
        )
        if (
            preflight_report is not None
            and session_preflight_ref != preflight_report.doc_id
        ):
            hard_failures.append(
                "session_report latest_preflight_ref must match latest operator_preflight_report."
            )

        session_render_ref = text_at(
            session_report.tree,
            "/p:pxml/p:payload/p:latest_render_report_ref/p:doc_id",
        )
        render_decision = text_at(
            session_report.tree,
            "/p:pxml/p:payload/p:render_decision",
        )
        if final_render_report is not None and render_decision in {
            "rendered",
            "rendered_with_warning",
            "denied",
        }:
            if session_render_ref != final_render_report.doc_id:
                hard_failures.append(
                    "session_report latest_render_report_ref must match latest final_render_report for rendered/denied decisions."
                )

        override_used = text_at(
            session_report.tree,
            "/p:pxml/p:payload/p:render_override_used",
        )
        if override_used == "true" and render_decision != "rendered_with_warning":
            hard_failures.append(
                "session_report render_override_used=true requires render_decision=rendered_with_warning."
            )

        release_result = text_at(
            session_report.tree,
            "/p:pxml/p:payload/p:release_readiness_result",
        )
        runbook_result = text_at(
            session_report.tree,
            "/p:pxml/p:payload/p:runbook_result",
        )
        if release_result == "pass" and runbook_result != "success":
            hard_failures.append(
                "session_report release_readiness_result=pass requires runbook_result=success."
            )
        if release_result == "fail" and runbook_result == "success":
            hard_failures.append(
                "session_report release_readiness_result=fail cannot pair with runbook_result=success."
            )

    if pruning_report is not None:
        pruning_task_scope = text_at(
            pruning_report.tree, "/p:pxml/p:payload/p:task_scope"
        )
        if pruning_task_scope != args.task_id:
            hard_failures.append(
                "pruning_report payload task_scope must match harness task_id in task-scoped validation."
            )

        latest_pointer_safety_ok = text_at(
            pruning_report.tree,
            "/p:pxml/p:payload/p:latest_pointer_safety_ok",
        )
        lineage_safety_ok = text_at(
            pruning_report.tree,
            "/p:pxml/p:payload/p:lineage_safety_ok",
        )
        if latest_pointer_safety_ok != "true":
            hard_failures.append(
                "pruning_report latest_pointer_safety_ok must be true for task-scoped harness runs."
            )
        if lineage_safety_ok != "true":
            hard_failures.append(
                "pruning_report lineage_safety_ok must be true for task-scoped harness runs."
            )

    if verification is not None and packet is not None:
        verify_refs = refs_of(verification.tree)
        packet_refs = [item for item in verify_refs if item[1] == "execution_packet"]
        if len(packet_refs) != 1:
            hard_failures.append(
                "verification_result must reference exactly one execution_packet."
            )
        elif packet_refs[0][0] != packet.doc_id:
            hard_failures.append(
                "verification_result does not reference latest execution_packet."
            )

        verdict = text_at(verification.tree, "/p:pxml/p:payload/p:final_verdict")
        verification_lock = text_at(
            verification.tree, "/p:pxml/p:payload/p:acceptance_lock_sha256"
        )
        packet_declared_lock = text_at(
            packet.tree, "/p:pxml/p:payload/p:acceptance_lock_hash"
        )
        if verification_lock is None:
            hard_failures.append(
                "verification_result missing payload acceptance_lock_sha256."
            )
        elif (
            packet_declared_lock is not None
            and verification_lock != packet_declared_lock
        ):
            hard_failures.append(
                "verification_result acceptance lock lineage does not match execution_packet.",
            )
        if verdict == "fail":
            hard_failures.append("verification_result final_verdict=fail.")
        elif verdict == "inconclusive":
            inconclusive.append("verification_result final_verdict=inconclusive.")

        verification_phase = text_at(
            verification.tree, "/p:pxml/p:payload/p:verify_phase"
        )
        if verification_phase is not None and verification_phase not in {
            "lane",
            "post_implement",
            "unknown_legacy",
        }:
            hard_failures.append(
                "verification_result verify_phase must be lane/post_implement/unknown_legacy."
            )
        if verification_phase == "lane" and selected_path not in {
            "verifier_post",
            "full_lane",
        }:
            hard_failures.append(
                "verification_result verify_phase=lane requires manager_route selected_path verifier_post/full_lane."
            )

    if trace is not None:
        trace_events = parse_trace_events(trace)
        event_set = {event.event_type for event in trace_events}
        packet_declared_lock = (
            text_at(packet.tree, "/p:pxml/p:payload/p:acceptance_lock_hash")
            if packet is not None
            else None
        )
        if "route" not in event_set:
            hard_failures.append("execution_trace missing route event.")
        if "packet_issued" not in event_set:
            hard_failures.append("execution_trace missing packet_issued event.")
        if reviewer is not None and "review_done" not in event_set:
            inconclusive.append(
                "review_sidecar exists but execution_trace has no review_done event."
            )
        if verification is not None and "verify_done" not in event_set:
            inconclusive.append(
                "verification_result exists but execution_trace has no verify_done event."
            )
        if implementer is not None and "implement_start" not in event_set:
            hard_failures.append(
                "implementer_result exists but execution_trace has no implement_start event."
            )
        if (
            implementer_status in {"applied", "no_op"}
            and "patch_applied" not in event_set
        ):
            hard_failures.append(
                "implementer_result status applied/no_op requires patch_applied event."
            )
        if implementer_status == "blocked" and "blocked" not in event_set:
            hard_failures.append(
                "implementer_result status=blocked requires blocked trace event."
            )
        if implementer_status == "retry_failed":
            if "retry_failed" not in event_set:
                hard_failures.append(
                    "implementer_result status=retry_failed requires retry_failed trace event."
                )
            if "escalation" not in event_set:
                hard_failures.append(
                    "implementer_result status=retry_failed requires escalation trace event."
                )

        verify_done_sequences = sorted(
            [
                event.event_seq
                for event in trace_events
                if event.event_type == "verify_done"
            ]
        )
        verify_done_phase_values = {
            event.verify_phase
            for event in trace_events
            if event.event_type == "verify_done" and event.verify_phase is not None
        }
        implement_start_sequences = [
            event.event_seq
            for event in trace_events
            if event.event_type == "implement_start"
        ]
        patch_applied_sequences = [
            event.event_seq
            for event in trace_events
            if event.event_type == "patch_applied"
        ]
        implement_start_seq = (
            min(implement_start_sequences) if implement_start_sequences else None
        )
        patch_applied_seq = (
            min(patch_applied_sequences) if patch_applied_sequences else None
        )

        if (
            verify_done_sequences
            and implement_start_seq is not None
            and selected_path not in {"verifier_post", "full_lane"}
            and any(seq < implement_start_seq for seq in verify_done_sequences)
        ):
            inconclusive.append(
                "verify_done event appears before implement_start on non-verifier lane path."
            )

        if (
            verification is not None
            and implementer_status in {"applied", "no_op"}
            and patch_applied_seq is not None
            and not any(seq > patch_applied_seq for seq in verify_done_sequences)
        ):
            hard_failures.append(
                "verification_result exists but no post-implement verify_done event appears after patch_applied."
            )

        for event in trace_events:
            if event.event_type != "verify_done" or event.verify_phase is None:
                continue
            if event.verify_phase not in {"lane", "post_implement", "unknown_legacy"}:
                hard_failures.append(
                    "verify_done event verify_phase must be lane/post_implement/unknown_legacy."
                )
                continue
            if event.verify_phase == "lane" and selected_path not in {
                "verifier_post",
                "full_lane",
            }:
                hard_failures.append(
                    "verify_done event verify_phase=lane requires selected_path verifier_post/full_lane."
                )
            if (
                event.verify_phase == "post_implement"
                and patch_applied_seq is not None
                and event.event_seq <= patch_applied_seq
            ):
                hard_failures.append(
                    "verify_done event verify_phase=post_implement must occur after patch_applied."
                )

        if verification is not None:
            verification_phase = text_at(
                verification.tree, "/p:pxml/p:payload/p:verify_phase"
            )
            if (
                verification_phase in {"lane", "post_implement"}
                and verification_phase not in verify_done_phase_values
            ):
                inconclusive.append(
                    "verification_result verify_phase is not reflected in verify_done event metadata."
                )

        for event in trace_events:
            if (
                event.event_type == "route"
                and "manager_route" not in event.ref_doc_classes
            ):
                hard_failures.append(
                    "route event must reference manager_route artifact."
                )
            if (
                event.event_type == "packet_issued"
                and "execution_packet" not in event.ref_doc_classes
            ):
                hard_failures.append(
                    "packet_issued event must reference execution_packet artifact."
                )
            if event.event_type == "implement_start":
                if "execution_packet" not in event.ref_doc_classes:
                    hard_failures.append(
                        "implement_start event must reference execution_packet artifact."
                    )
                if (
                    implementer is not None
                    and packet_declared_lock
                    and event.lineage_lock_sha256 != packet_declared_lock
                ):
                    hard_failures.append(
                        "implement_start event lineage_lock_sha256 does not match execution_packet lock.",
                    )
            if event.event_type == "patch_applied":
                if "implementer_result" not in event.ref_doc_classes:
                    hard_failures.append(
                        "patch_applied event must reference implementer_result artifact.",
                    )
                if (
                    packet_declared_lock
                    and event.lineage_lock_sha256 != packet_declared_lock
                ):
                    hard_failures.append(
                        "patch_applied event lineage_lock_sha256 does not match execution_packet lock.",
                    )
            if event.event_type == "blocked":
                if "implementer_result" not in event.ref_doc_classes:
                    hard_failures.append(
                        "blocked event must reference implementer_result artifact.",
                    )
                if event.reason_code is None:
                    hard_failures.append("blocked event missing reason_code.")
                if event.attempt is None or event.attempt < 1:
                    hard_failures.append("blocked event missing valid attempt.")
                if (
                    packet_declared_lock
                    and event.lineage_lock_sha256 != packet_declared_lock
                ):
                    hard_failures.append(
                        "blocked event lineage_lock_sha256 does not match execution_packet lock.",
                    )
            if event.event_type == "retry_failed":
                if "implementer_result" not in event.ref_doc_classes:
                    hard_failures.append(
                        "retry_failed event must reference implementer_result artifact.",
                    )
                if event.reason_code is None:
                    hard_failures.append("retry_failed event missing reason_code.")
                if event.attempt is None or event.attempt < 1:
                    hard_failures.append("retry_failed event missing valid attempt.")
                if (
                    packet_declared_lock
                    and event.lineage_lock_sha256 != packet_declared_lock
                ):
                    hard_failures.append(
                        "retry_failed event lineage_lock_sha256 does not match execution_packet lock.",
                    )
            if event.event_type == "review_done":
                if "review_sidecar" not in event.ref_doc_classes:
                    hard_failures.append(
                        "review_done event must reference review_sidecar artifact."
                    )
                if (
                    packet_declared_lock
                    and event.lineage_lock_sha256 != packet_declared_lock
                ):
                    hard_failures.append(
                        "review_done event lineage_lock_sha256 does not match execution_packet lock.",
                    )
            if event.event_type == "verify_done":
                if "verification_result" not in event.ref_doc_classes:
                    hard_failures.append(
                        "verify_done event must reference verification_result artifact.",
                    )
                if (
                    packet_declared_lock
                    and event.lineage_lock_sha256 != packet_declared_lock
                ):
                    hard_failures.append(
                        "verify_done event lineage_lock_sha256 does not match execution_packet lock.",
                    )
            if event.event_type == "escalation":
                if event.reason_code is None:
                    hard_failures.append("escalation event missing reason_code.")
                if event.attempt is None or event.attempt < 1:
                    hard_failures.append("escalation event missing valid attempt.")

        if status_report is not None:
            report_codes = status_report.tree.xpath(
                "/p:pxml/p:payload/p:failure_reason_codes/p:item/text()",
                namespaces=XPATH_NS,
            )
            report_set = {
                code.strip()
                for code in report_codes
                if isinstance(code, str) and code.strip() and code.strip() != "none"
            }
            trace_set = {
                event.reason_code.strip()
                for event in trace_events
                if event.reason_code and event.reason_code.strip()
            }
            if implementer is not None:
                blocked_reason = text_at(
                    implementer.tree, "/p:pxml/p:payload/p:blocked_reason"
                )
                if blocked_reason:
                    trace_set.add(blocked_reason)
            for reason in load_failure_index_reason_codes(runtime_root, args.task_id):
                trace_set.add(reason)
            if report_set != trace_set:
                hard_failures.append(
                    "task_status_report failure_reason_codes must match trace/implementer/failure-index reason codes."
                )

            if taxonomy_codes:
                unknown = sorted(
                    [code for code in trace_set if code not in taxonomy_codes]
                )
                if unknown:
                    hard_failures.append(
                        "Failure reason code(s) missing from failure_reason_taxonomy: "
                        + ", ".join(unknown)
                    )

        reason_counts: Dict[str, int] = {}
        for event in trace_events:
            if event.event_type != "escalation" or not event.reason_code:
                continue
            reason_counts[event.reason_code] = (
                reason_counts.get(event.reason_code, 0) + 1
            )

        for reason_code, count in reason_counts.items():
            if (
                count >= retry_policy.same_cause_fast_escalation_threshold
                and count < escalation_policy.repeated_failure_threshold
            ):
                inconclusive.append(
                    f"Escalation reason '{reason_code}' repeated {count} times before repeated_failure_threshold.",
                )

        if escalation_policy.stop_after_escalation:
            has_escalation = any(
                event.event_type == "escalation" for event in trace_events
            )
            has_stop = any(event.event_type == "stop" for event in trace_events)
            if (
                has_escalation
                and not has_stop
                and any(
                    count >= escalation_policy.repeated_failure_threshold
                    for count in reason_counts.values()
                )
            ):
                hard_failures.append(
                    "Escalation repeated beyond threshold without stop event while stop_after_escalation=true.",
                )

    for artifact in artifacts:
        for ref_doc_id, ref_doc_class, _ in refs_of(artifact.tree):
            if ref_doc_class in FLOW_DOC_CLASSES and ref_doc_id not in docs_by_id:
                hard_failures.append(
                    f"Missing referenced runtime artifact: {artifact.doc_id} -> {ref_doc_id} ({ref_doc_class})."
                )

    release_report_latest = (
        runtime_root
        / "latest"
        / f"{sanitize(args.task_id)}_release_candidate_report.pxml"
    )
    release_manifest_latest = (
        runtime_root
        / "latest"
        / f"{sanitize(args.task_id)}_release_bundle_manifest.pxml"
    )
    release_verify_audit_latest = (
        runtime_root
        / "latest"
        / f"{sanitize(args.task_id)}_verify_phase_audit_report.pxml"
    )
    report_doc = parse_release_link(release_report_latest)
    manifest_doc = parse_release_link(release_manifest_latest)
    verify_audit_doc = parse_release_link(release_verify_audit_latest)
    if release_report_latest.exists() or release_manifest_latest.exists():
        if report_doc is None:
            hard_failures.append(
                "Latest release_candidate_report link exists but artifact is missing or invalid."
            )
        if manifest_doc is None:
            hard_failures.append(
                "Latest release_bundle_manifest link exists but artifact is missing or invalid."
            )
        if (
            report_doc is not None
            and report_doc.doc_class != "release_candidate_report"
        ):
            hard_failures.append(
                "Latest release_candidate_report link does not point to release_candidate_report."
            )
        if (
            manifest_doc is not None
            and manifest_doc.doc_class != "release_bundle_manifest"
        ):
            hard_failures.append(
                "Latest release_bundle_manifest link does not point to release_bundle_manifest."
            )
        if report_doc is not None and manifest_doc is not None:
            source_report_ref = text_at(
                manifest_doc.tree,
                "/p:pxml/p:payload/p:source_release_candidate_report_ref/p:doc_id",
            )
            if source_report_ref != report_doc.doc_id:
                hard_failures.append(
                    "release_bundle_manifest source_release_candidate_report_ref must match latest release_candidate_report doc_id."
                )

            report_profile_refs = report_doc.tree.xpath(
                "/p:pxml/p:refs/p:ref[p:doc_class='release_gate_profile']/p:doc_id/text()",
                namespaces=XPATH_NS,
            )
            if len(report_profile_refs) == 0:
                hard_failures.append(
                    "release_candidate_report must include release_gate_profile reference in top-level refs."
                )

            report_coverage_policy_refs = report_doc.tree.xpath(
                "/p:pxml/p:refs/p:ref[p:doc_class='coverage_outcome_policy']/p:doc_id/text()",
                namespaces=XPATH_NS,
            )
            if len(report_coverage_policy_refs) == 0:
                hard_failures.append(
                    "release_candidate_report must include coverage_outcome_policy reference in top-level refs."
                )

            report_profile_governance_refs = report_doc.tree.xpath(
                "/p:pxml/p:refs/p:ref[p:doc_class='release_profile_governance_policy']/p:doc_id/text()",
                namespaces=XPATH_NS,
            )
            if len(report_profile_governance_refs) == 0:
                hard_failures.append(
                    "release_candidate_report must include release_profile_governance_policy reference in top-level refs."
                )

            report_ci_policy_refs = report_doc.tree.xpath(
                "/p:pxml/p:refs/p:ref[p:doc_class='ci_exit_code_policy']/p:doc_id/text()",
                namespaces=XPATH_NS,
            )
            if len(report_ci_policy_refs) == 0:
                hard_failures.append(
                    "release_candidate_report must include ci_exit_code_policy reference in top-level refs."
                )

            report_verify_phase_policy_refs = report_doc.tree.xpath(
                "/p:pxml/p:refs/p:ref[p:doc_class='verify_phase_audit_policy']/p:doc_id/text()",
                namespaces=XPATH_NS,
            )
            if len(report_verify_phase_policy_refs) == 0:
                hard_failures.append(
                    "release_candidate_report must include verify_phase_audit_policy reference in top-level refs."
                )

            harness_ci_policy_refs = report_doc.tree.xpath(
                "/p:pxml/p:payload/p:harness_version_refs/p:ref[p:doc_class='ci_exit_code_policy']/p:doc_id/text()",
                namespaces=XPATH_NS,
            )
            if len(harness_ci_policy_refs) == 0:
                hard_failures.append(
                    "release_candidate_report harness_version_refs must include ci_exit_code_policy."
                )

            harness_verify_phase_policy_refs = report_doc.tree.xpath(
                "/p:pxml/p:payload/p:harness_version_refs/p:ref[p:doc_class='verify_phase_audit_policy']/p:doc_id/text()",
                namespaces=XPATH_NS,
            )
            if len(harness_verify_phase_policy_refs) == 0:
                hard_failures.append(
                    "release_candidate_report harness_version_refs must include verify_phase_audit_policy."
                )

            manifest_ci_policy_refs = manifest_doc.tree.xpath(
                "/p:pxml/p:payload/p:key_policy_refs/p:ref[p:doc_class='ci_exit_code_policy']/p:doc_id/text()",
                namespaces=XPATH_NS,
            )
            if len(manifest_ci_policy_refs) == 0:
                hard_failures.append(
                    "release_bundle_manifest key_policy_refs must include ci_exit_code_policy."
                )

            manifest_verify_phase_policy_refs = manifest_doc.tree.xpath(
                "/p:pxml/p:payload/p:key_policy_refs/p:ref[p:doc_class='verify_phase_audit_policy']/p:doc_id/text()",
                namespaces=XPATH_NS,
            )
            if len(manifest_verify_phase_policy_refs) == 0:
                hard_failures.append(
                    "release_bundle_manifest key_policy_refs must include verify_phase_audit_policy."
                )

            if (
                report_ci_policy_refs
                and manifest_ci_policy_refs
                and report_ci_policy_refs[0].strip()
                != manifest_ci_policy_refs[0].strip()
            ):
                hard_failures.append(
                    "CI policy doc_id must match between release_candidate_report refs and release_bundle_manifest key_policy_refs."
                )

            if (
                report_verify_phase_policy_refs
                and manifest_verify_phase_policy_refs
                and report_verify_phase_policy_refs[0].strip()
                != manifest_verify_phase_policy_refs[0].strip()
            ):
                hard_failures.append(
                    "verify_phase policy doc_id must match between release_candidate_report refs and release_bundle_manifest key_policy_refs."
                )

            manifest_runtime_refs = {
                item.strip()
                for item in manifest_doc.tree.xpath(
                    "/p:pxml/p:payload/p:key_runtime_refs/p:item/text()",
                    namespaces=XPATH_NS,
                )
                if isinstance(item, str) and item.strip()
            }
            if "runtime/release/audits" not in manifest_runtime_refs:
                hard_failures.append(
                    "release_bundle_manifest key_runtime_refs must include runtime/release/audits."
                )

            manifest_script_refs = {
                item.strip()
                for item in manifest_doc.tree.xpath(
                    "/p:pxml/p:payload/p:key_script_refs/p:item/text()",
                    namespaces=XPATH_NS,
                )
                if isinstance(item, str) and item.strip()
            }
            if "scripts/verify_phase_audit.py" not in manifest_script_refs:
                hard_failures.append(
                    "release_bundle_manifest key_script_refs must include scripts/verify_phase_audit.py."
                )

            manifest_schema_refs = {
                item.strip()
                for item in manifest_doc.tree.xpath(
                    "/p:pxml/p:payload/p:key_schema_refs/p:item/text()",
                    namespaces=XPATH_NS,
                )
                if isinstance(item, str) and item.strip()
            }
            if "contracts/schemas/ci_exit_code_policy.xsd" not in manifest_schema_refs:
                hard_failures.append(
                    "release_bundle_manifest key_schema_refs must include contracts/schemas/ci_exit_code_policy.xsd."
                )
            if (
                "contracts/schemas/verify_phase_audit_report.xsd"
                not in manifest_schema_refs
            ):
                hard_failures.append(
                    "release_bundle_manifest key_schema_refs must include contracts/schemas/verify_phase_audit_report.xsd."
                )

    if release_verify_audit_latest.exists():
        if verify_audit_doc is None:
            hard_failures.append(
                "Latest verify_phase_audit_report link exists but artifact is missing or invalid."
            )
        elif verify_audit_doc.doc_class != "verify_phase_audit_report":
            hard_failures.append(
                "Latest verify_phase_audit_report link does not point to verify_phase_audit_report."
            )
        else:
            policy_class = text_at(
                verify_audit_doc.tree,
                "/p:pxml/p:payload/p:policy_ref/p:doc_class",
            )
            if policy_class != "verify_phase_audit_policy":
                hard_failures.append(
                    "verify_phase_audit_report policy_ref doc_class must be verify_phase_audit_policy."
                )

            audit_result = text_at(
                verify_audit_doc.tree,
                "/p:pxml/p:payload/p:result",
            )
            if audit_result not in {"pass", "caution", "fail"}:
                hard_failures.append(
                    "verify_phase_audit_report result must be pass/caution/fail."
                )

            audit_blockers = {
                item.strip()
                for item in verify_audit_doc.tree.xpath(
                    "/p:pxml/p:payload/p:blockers/p:item/text()",
                    namespaces=XPATH_NS,
                )
                if isinstance(item, str) and item.strip()
            }
            if audit_result == "pass" and audit_blockers != {"none"}:
                hard_failures.append(
                    "verify_phase_audit_report result=pass requires blockers=['none']."
                )

    if args.release_readiness:
        release_failures = list(hard_failures)
        release_inconclusive = list(inconclusive)

        preflight_readiness: Optional[str] = None
        if preflight_report is None:
            release_failures.append(
                "Missing operator_preflight_report for release-readiness mode."
            )
        else:
            preflight_readiness = text_at(
                preflight_report.tree,
                "/p:pxml/p:payload/p:render_readiness",
            )
            if preflight_readiness == "not_ready":
                release_failures.append(
                    "operator_preflight_report render_readiness=not_ready in release-readiness mode."
                )
            elif preflight_readiness == "caution":
                release_inconclusive.append(
                    "operator_preflight_report render_readiness=caution in release-readiness mode."
                )

        status_value: Optional[str] = None
        if status_report is None:
            release_failures.append(
                "Missing task_status_report for release-readiness mode."
            )
        else:
            status_value = text_at(
                status_report.tree, "/p:pxml/p:payload/p:current_status"
            )
            if status_value in {"failed", "retry_failed", "escalated"}:
                release_failures.append(
                    f"task_status_report current_status={status_value} in release-readiness mode."
                )
            elif status_value in {
                "blocked",
                "pending",
                "running",
                "inconclusive",
                "no_op",
            }:
                release_inconclusive.append(
                    f"task_status_report current_status={status_value} in release-readiness mode."
                )

        render_mode: Optional[str] = None
        if final_render_report is None:
            if preflight_readiness == "ready":
                release_failures.append(
                    "Missing final_render_report while preflight readiness is ready."
                )
            else:
                release_inconclusive.append(
                    "final_render_report is missing; render gate outcome not fully materialized."
                )
        else:
            render_mode = text_at(
                final_render_report.tree,
                "/p:pxml/p:payload/p:render_mode",
            )
            if preflight_readiness == "ready" and render_mode not in {
                "rendered",
                "rendered_with_warning",
            }:
                release_failures.append(
                    "ready preflight requires final_render_report render_mode rendered/rendered_with_warning."
                )
            if preflight_readiness == "caution" and render_mode == "denied":
                release_inconclusive.append(
                    "caution preflight produced denied render_mode without override."
                )
            if preflight_readiness == "not_ready" and render_mode == "rendered":
                release_failures.append(
                    "not_ready preflight cannot produce final_render_report render_mode=rendered."
                )

        if quarantine_flags:
            release_inconclusive.append(
                "Quarantine manifest flags are present in release-readiness mode: "
                + ", ".join(quarantine_flags)
            )

        session_release_result: Optional[str] = None
        if session_report is not None:
            session_status_ref = text_at(
                session_report.tree,
                "/p:pxml/p:payload/p:latest_status_report_ref/p:doc_id",
            )
            session_preflight_ref = text_at(
                session_report.tree,
                "/p:pxml/p:payload/p:latest_preflight_ref/p:doc_id",
            )
            session_render_ref = text_at(
                session_report.tree,
                "/p:pxml/p:payload/p:latest_render_report_ref/p:doc_id",
            )
            session_render_decision = text_at(
                session_report.tree,
                "/p:pxml/p:payload/p:render_decision",
            )
            session_release_result = text_at(
                session_report.tree,
                "/p:pxml/p:payload/p:release_readiness_result",
            )

            if status_report is not None and session_status_ref != status_report.doc_id:
                release_failures.append(
                    "session_report latest_status_report_ref mismatch in release-readiness mode."
                )
            if (
                preflight_report is not None
                and session_preflight_ref != preflight_report.doc_id
            ):
                release_failures.append(
                    "session_report latest_preflight_ref mismatch in release-readiness mode."
                )
            if (
                final_render_report is not None
                and session_render_decision
                in {"rendered", "rendered_with_warning", "denied"}
                and session_render_ref != final_render_report.doc_id
            ):
                release_failures.append(
                    "session_report latest_render_report_ref mismatch in release-readiness mode."
                )
            if (
                render_mode
                and session_render_decision
                and render_mode != session_render_decision
            ):
                release_failures.append(
                    "session_report render_decision does not match final_render_report render_mode."
                )

        if release_failures:
            computed_release = "fail"
        elif release_inconclusive:
            computed_release = "inconclusive"
        else:
            computed_release = "pass"

        if (
            session_report is not None
            and session_release_result is not None
            and session_release_result != computed_release
        ):
            release_failures.append(
                "session_report release_readiness_result does not match harness release-readiness result."
            )
            computed_release = "fail"

        if release_failures:
            print("Result: fail")
            print("mode: release_readiness")
            print(f"task_id: {args.task_id}")
            print("reasons:")
            for reason in release_failures:
                print(f"- {reason}")
            if validator_output:
                print("validator_output:")
                print(validator_output)
            return 1

        if release_inconclusive:
            print("Result: inconclusive")
            print("mode: release_readiness")
            print(f"task_id: {args.task_id}")
            print("reasons:")
            for reason in release_inconclusive:
                print(f"- {reason}")
            if validator_output:
                print("validator_output:")
                print(validator_output)
            return 2

        print("Result: pass")
        print("mode: release_readiness")
        print(f"task_id: {args.task_id}")
        print(f"artifact_count: {len(artifacts)}")
        if validator_output:
            print("validator_output:")
            print(validator_output)
        return 0

    if hard_failures:
        print("Result: fail")
        print(f"task_id: {args.task_id}")
        print("reasons:")
        for reason in hard_failures:
            print(f"- {reason}")
        if validator_output:
            print("validator_output:")
            print(validator_output)
        return 1

    if inconclusive:
        print("Result: inconclusive")
        print(f"task_id: {args.task_id}")
        print("reasons:")
        for reason in inconclusive:
            print(f"- {reason}")
        if validator_output:
            print("validator_output:")
            print(validator_output)
        return 2

    print("Result: pass")
    print(f"task_id: {args.task_id}")
    print(f"artifact_count: {len(artifacts)}")
    if validator_output:
        print("validator_output:")
        print(validator_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
