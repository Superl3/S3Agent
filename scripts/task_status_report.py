#!/usr/bin/env python3
"""Batch 6 operator-facing task status report generator."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from lxml import etree
except ModuleNotFoundError:
    print(
        "ERROR: lxml is required. Install with: python -m pip install lxml",
        file=sys.stderr,
    )
    raise SystemExit(3)


NS = "urn:pxml:v1"
NSMAP = {None: NS}
XPATH_NS = {"p": NS}


@dataclass
class ArtifactRefInfo:
    path: Path
    doc_id: str
    doc_class: str
    task_id: str
    sequence: int
    created_at: str


def q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


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


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def sanitize(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9._-]", "_", lowered)
    lowered = re.sub(r"_+", "_", lowered).strip("_")
    return lowered or "id"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_content_hash(
    meta: etree._Element, refs: Optional[etree._Element], payload: etree._Element
) -> str:
    material = etree.Element(q("hash_material"), nsmap=NSMAP)
    material.append(copy.deepcopy(meta))
    if refs is not None:
        material.append(copy.deepcopy(refs))
    material.append(copy.deepcopy(payload))
    c14n = etree.tostring(material, method="c14n", exclusive=True, with_comments=False)
    return sha256_hex(c14n)


def discover_pxml_files(path: Path) -> List[Path]:
    if not path.exists():
        return []
    files = [candidate for candidate in path.rglob("*.pxml") if candidate.is_file()]
    files.sort()
    return files


def parse_artifact(path: Path) -> Optional[ArtifactRefInfo]:
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
        return None
    return ArtifactRefInfo(
        path=path,
        doc_id=doc_id,
        doc_class=doc_class,
        task_id=task_id,
        sequence=sequence,
        created_at=created_at,
    )


def latest_for_task(
    directory: Path, doc_class: str, task_id: str
) -> Optional[ArtifactRefInfo]:
    candidates: List[ArtifactRefInfo] = []
    for path in discover_pxml_files(directory):
        parsed = parse_artifact(path)
        if parsed is None:
            continue
        if parsed.task_id != task_id or parsed.doc_class != doc_class:
            continue
        candidates.append(parsed)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item.sequence, item.created_at, str(item.path)))
    return candidates[-1]


def latest_map(
    runtime_root: Path, task_id: str
) -> Dict[str, Optional[ArtifactRefInfo]]:
    return {
        "task_intake": latest_for_task(
            runtime_root / "inbox" / "task_intake", "task_intake", task_id
        ),
        "manager_route": latest_for_task(
            runtime_root / "packets" / "manager_route", "manager_route", task_id
        ),
        "execution_packet": latest_for_task(
            runtime_root / "packets" / "execution_packet", "execution_packet", task_id
        ),
        "plan_sidecar": latest_for_task(
            runtime_root / "sidecars" / "planner", "plan_sidecar", task_id
        ),
        "review_sidecar": latest_for_task(
            runtime_root / "sidecars" / "reviewer", "review_sidecar", task_id
        ),
        "implementer_result": latest_for_task(
            runtime_root / "implementer" / "results", "implementer_result", task_id
        ),
        "verification_result": latest_for_task(
            runtime_root / "verification" / "results", "verification_result", task_id
        ),
        "execution_trace": latest_for_task(
            runtime_root / "traces" / "by_task", "execution_trace", task_id
        ),
    }


def next_sequence(runtime_root: Path, task_id: str) -> int:
    max_sequence = 0
    for path in discover_pxml_files(runtime_root):
        parsed = parse_artifact(path)
        if parsed is None or parsed.task_id != task_id:
            continue
        max_sequence = max(max_sequence, parsed.sequence)
    return max_sequence + 1


def load_failure_entries(runtime_root: Path, task_id: str) -> List[Dict[str, object]]:
    index_path = runtime_root / "index" / "failures" / f"{sanitize(task_id)}.json"
    if not index_path.exists():
        return []
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    entries = payload.get("entries")
    if isinstance(entries, list):
        return [item for item in entries if isinstance(item, dict)]
    return []


def load_taxonomy_codes(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        tree = etree.parse(str(path))
    except etree.XMLSyntaxError:
        return set()
    nodes = tree.xpath(
        "/p:pxml/p:payload/p:reasons/p:reason/p:code/text()", namespaces=XPATH_NS
    )
    return {item.strip() for item in nodes if item and item.strip()}


def collect_trace_reason_codes(trace_info: Optional[ArtifactRefInfo]) -> List[str]:
    if trace_info is None:
        return []
    tree = etree.parse(str(trace_info.path))
    nodes = tree.xpath(
        "/p:pxml/p:payload/p:events/p:event/p:reason_code/text()", namespaces=XPATH_NS
    )
    values: List[str] = []
    for value in nodes:
        normalized = value.strip()
        if normalized:
            values.append(normalized)
    return values


def unique_preserve(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def determine_phase_status(
    selected_path: Optional[str],
    implementer_status: Optional[str],
    verification_verdict: Optional[str],
    escalation_state: str,
    has_route: bool,
    has_packet: bool,
    has_planner: bool,
    has_reviewer: bool,
) -> Tuple[str, str]:
    if escalation_state == "stopped":
        return "stopped", "escalated"
    if escalation_state == "escalated":
        return "escalated", "escalated"

    if implementer_status == "blocked":
        return "implementing", "blocked"
    if implementer_status == "retry_failed":
        return "implementing", "retry_failed"
    if implementer_status == "escalated":
        return "escalated", "escalated"

    if implementer_status in {"applied", "no_op"}:
        if verification_verdict == "pass":
            return "completed", "passed"
        if verification_verdict == "fail":
            return "completed", "failed"
        if verification_verdict == "inconclusive":
            return "verifying", "inconclusive"

    if implementer_status == "no_op":
        return "completed", "no_op"
    if implementer_status == "applied":
        if selected_path in {"verifier_post", "full_lane"}:
            return "verifying", "running"
        return "implementing", "running"

    if verification_verdict == "pass":
        return "completed", "passed"
    if verification_verdict == "fail":
        return "completed", "failed"
    if verification_verdict == "inconclusive":
        return "verifying", "inconclusive"

    if has_reviewer:
        return "reviewing", "running"
    if has_planner:
        return "planning", "running"
    if has_packet:
        return "implementing", "pending"
    if has_route:
        return "routing", "running"
    return "intake", "pending"


def final_verdict_candidate(
    verification_verdict: Optional[str],
    implementer_status: Optional[str],
    escalation_state: str,
) -> str:
    if escalation_state in {"escalated", "stopped"}:
        return "fail"
    if implementer_status in {"blocked", "retry_failed", "escalated"}:
        return "fail"
    if verification_verdict in {"pass", "fail", "inconclusive"}:
        return verification_verdict
    if implementer_status in {"applied", "no_op"}:
        return "inconclusive"
    return "unknown"


def recommended_action(
    status: str, escalation_state: str, selected_path: Optional[str]
) -> str:
    if escalation_state == "stopped":
        return "Escalate to manager and refresh packet before further execution."
    if status == "blocked":
        return "Fix packet expected_files or missing targets, then rerun implementer runner."
    if status == "retry_failed":
        return "Address repeated blocked cause and issue human escalation decision."
    if status == "escalated":
        return "Await manager decision before continuing execution pipeline."
    if status == "inconclusive":
        return "Resolve verification gaps and rerun verification with deterministic checks."
    if status == "failed":
        return "Repair failed acceptance checks and rerun implementer and verifier."
    if status == "passed":
        return "Task execution is green; proceed with normal closeout workflow."
    if status == "no_op":
        return "No-op result recorded; run verifier only if operator requests extra evidence."
    if selected_path in {"verifier_post", "full_lane"}:
        return "Run verifier or task executor auto policy to close verification phase."
    return "Continue execution pipeline with task_executor for remaining phases."


def append_payload_ref(
    payload: etree._Element,
    tag: str,
    info: Optional[ArtifactRefInfo],
    relation: str,
) -> None:
    if info is None:
        return
    node = etree.SubElement(payload, q(tag))
    etree.SubElement(node, q("doc_id")).text = info.doc_id
    etree.SubElement(node, q("doc_class")).text = info.doc_class
    etree.SubElement(node, q("relation")).text = relation


def build_status_report(
    runtime_root: Path,
    task_id: str,
    refs_map: Dict[str, Optional[ArtifactRefInfo]],
    taxonomy_codes: set[str],
) -> Tuple[etree._ElementTree, str, str]:
    route_info = refs_map["manager_route"]
    packet_info = refs_map["execution_packet"]
    planner_info = refs_map["plan_sidecar"]
    reviewer_info = refs_map["review_sidecar"]
    implementer_info = refs_map["implementer_result"]
    verification_info = refs_map["verification_result"]
    trace_info = refs_map["execution_trace"]

    selected_path: Optional[str] = None
    acceptance_lock: Optional[str] = None
    if route_info is not None:
        route_tree = etree.parse(str(route_info.path))
        selected_path = text_at(route_tree, "/p:pxml/p:payload/p:selected_path")
    if packet_info is not None:
        packet_tree = etree.parse(str(packet_info.path))
        acceptance_lock = text_at(
            packet_tree, "/p:pxml/p:payload/p:acceptance_lock_hash"
        )

    implementer_status: Optional[str] = None
    implementer_retry_count = 0
    implementer_blocked_reason: Optional[str] = None
    implementer_escalation = False
    if implementer_info is not None:
        impl_tree = etree.parse(str(implementer_info.path))
        implementer_status = text_at(impl_tree, "/p:pxml/p:payload/p:result_status")
        retry_text = text_at(impl_tree, "/p:pxml/p:payload/p:retry_count")
        blocked_reason = text_at(impl_tree, "/p:pxml/p:payload/p:blocked_reason")
        escalated_text = text_at(impl_tree, "/p:pxml/p:payload/p:escalation_requested")
        implementer_retry_count = (
            int(retry_text) if retry_text and retry_text.isdigit() else 0
        )
        implementer_blocked_reason = blocked_reason
        implementer_escalation = escalated_text == "true"

    verification_verdict: Optional[str] = None
    if verification_info is not None:
        verify_tree = etree.parse(str(verification_info.path))
        verification_verdict = text_at(verify_tree, "/p:pxml/p:payload/p:final_verdict")

    trace_event_types: List[str] = []
    if trace_info is not None:
        trace_tree = etree.parse(str(trace_info.path))
        trace_event_types = trace_tree.xpath(
            "/p:pxml/p:payload/p:events/p:event/p:event_type/text()",
            namespaces=XPATH_NS,
        )

    escalation_state = "none"
    if "stop" in trace_event_types:
        escalation_state = "stopped"
    elif "escalation" in trace_event_types:
        escalation_state = "escalated"
    elif implementer_escalation:
        escalation_state = "requested"

    failure_entries = load_failure_entries(runtime_root, task_id)
    failure_codes: List[str] = []
    if implementer_blocked_reason:
        failure_codes.append(implementer_blocked_reason)
    failure_codes.extend(collect_trace_reason_codes(trace_info))
    for entry in failure_entries:
        reason = entry.get("reason_code")
        if isinstance(reason, str) and reason.strip():
            failure_codes.append(reason.strip())
    failure_codes = unique_preserve(failure_codes)
    if not failure_codes:
        failure_codes = ["none"]

    known_codes = taxonomy_codes
    unknown_failure_codes = [
        code
        for code in failure_codes
        if code != "none" and known_codes and code not in known_codes
    ]
    if unknown_failure_codes:
        escalation_state = "requested"

    retry_count = implementer_retry_count
    for entry in failure_entries:
        count = entry.get("retry_count")
        if isinstance(count, int):
            retry_count = max(retry_count, count)

    phase, status = determine_phase_status(
        selected_path=selected_path,
        implementer_status=implementer_status,
        verification_verdict=verification_verdict,
        escalation_state=escalation_state,
        has_route=route_info is not None,
        has_packet=packet_info is not None,
        has_planner=planner_info is not None,
        has_reviewer=reviewer_info is not None,
    )

    verdict_candidate = final_verdict_candidate(
        verification_verdict=verification_verdict,
        implementer_status=implementer_status,
        escalation_state=escalation_state,
    )
    next_action = recommended_action(
        status=status,
        escalation_state=escalation_state,
        selected_path=selected_path,
    )

    sequence = next_sequence(runtime_root, task_id)
    doc_id = f"doc_task_status_{sanitize(task_id)[:20]}_{sequence:04d}"
    if not re.fullmatch(r"doc_[a-z0-9][a-z0-9._-]{5,63}", doc_id):
        doc_id = (
            f"doc_task_status_{sequence:04d}_{sha256_hex(task_id.encode('utf-8'))[:8]}"
        )

    root = etree.Element(q("pxml"), nsmap=NSMAP)
    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = doc_id
    etree.SubElement(meta, q("doc_class")).text = "task_status_report"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = task_id
    etree.SubElement(meta, q("run_id")).text = f"run_status_{sanitize(task_id)}"
    etree.SubElement(meta, q("sequence")).text = str(sequence)
    etree.SubElement(meta, q("writer_agent")).text = "system"
    etree.SubElement(meta, q("created_at")).text = now_iso()

    refs = etree.SubElement(root, q("refs"))
    for key, relation in [
        ("manager_route", "latest_route"),
        ("execution_packet", "latest_packet"),
        ("implementer_result", "latest_implementer_result"),
        ("review_sidecar", "latest_review"),
        ("verification_result", "latest_verification"),
        ("execution_trace", "latest_trace"),
    ]:
        info = refs_map.get(key)
        if info is None:
            continue
        ref = etree.SubElement(refs, q("ref"))
        etree.SubElement(ref, q("doc_id")).text = info.doc_id
        etree.SubElement(ref, q("doc_class")).text = info.doc_class
        etree.SubElement(ref, q("relation")).text = relation

    payload = etree.SubElement(root, q("payload"))
    etree.SubElement(payload, q("task_id")).text = task_id
    etree.SubElement(payload, q("current_phase")).text = phase
    etree.SubElement(payload, q("current_status")).text = status
    if selected_path:
        etree.SubElement(payload, q("selected_path")).text = selected_path

    append_payload_ref(payload, "latest_route_ref", route_info, "latest_route")
    append_payload_ref(payload, "latest_packet_ref", packet_info, "latest_packet")
    append_payload_ref(
        payload,
        "latest_implementer_result_ref",
        implementer_info,
        "latest_implementer_result",
    )
    append_payload_ref(payload, "latest_review_ref", reviewer_info, "latest_review")
    append_payload_ref(
        payload,
        "latest_verification_ref",
        verification_info,
        "latest_verification",
    )
    append_payload_ref(payload, "latest_trace_ref", trace_info, "latest_trace")

    if acceptance_lock:
        etree.SubElement(payload, q("acceptance_lock_sha256")).text = acceptance_lock
    etree.SubElement(payload, q("retry_count")).text = str(retry_count)
    etree.SubElement(payload, q("escalation_state")).text = escalation_state
    etree.SubElement(payload, q("final_verdict_candidate")).text = verdict_candidate
    etree.SubElement(payload, q("next_recommended_action")).text = next_action

    failure_codes_node = etree.SubElement(payload, q("failure_reason_codes"))
    for code in failure_codes:
        etree.SubElement(failure_codes_node, q("item")).text = code

    integrity = etree.SubElement(root, q("integrity"))
    content_sha = compute_content_hash(meta, refs, payload)
    etree.SubElement(integrity, q("content_sha256")).text = content_sha

    if packet_info is not None:
        packet_tree = etree.parse(str(packet_info.path))
        packet_content = text_at(packet_tree, "/p:pxml/p:integrity/p:content_sha256")
        if packet_content is not None:
            etree.SubElement(integrity, q("parent_sha256")).text = packet_content

    return etree.ElementTree(root), doc_id, status


def write_xml(tree: etree._ElementTree, path: Path) -> None:
    ensure_dir(path.parent)
    tree.write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True)


def update_indexes(
    runtime_root: Path, task_id: str, doc_id: str, report_path: Path
) -> None:
    tasks_dir = runtime_root / "index" / "tasks"
    artifacts_dir = runtime_root / "index" / "artifacts"
    ensure_dir(tasks_dir)
    ensure_dir(artifacts_dir)

    task_index_path = tasks_dir / f"{sanitize(task_id)}.json"
    current: Dict[str, object] = {}
    if task_index_path.exists():
        try:
            current = json.loads(task_index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            current = {}
    current["task_id"] = task_id
    current["latest_task_status_report"] = str(report_path.relative_to(runtime_root))
    current["updated_at"] = now_iso()
    task_index_path.write_text(
        json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    artifact_index = {
        "doc_id": doc_id,
        "doc_class": "task_status_report",
        "task_id": task_id,
        "path": str(report_path.relative_to(runtime_root)),
        "updated_at": now_iso(),
    }
    (artifacts_dir / f"{doc_id}.json").write_text(
        json.dumps(artifact_index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_validation(
    validator: Path,
    report_path: Path,
    context_files: Sequence[Path],
) -> None:
    with tempfile.TemporaryDirectory(prefix="pxml_status_validate_") as temp_dir:
        temp_root = Path(temp_dir)
        copied_report = temp_root / report_path.name
        shutil.copy2(report_path, copied_report)
        for file_path in context_files:
            if not file_path.exists():
                continue
            shutil.copy2(file_path, temp_root / file_path.name)

        command = [
            sys.executable,
            str(validator),
            str(copied_report),
            "--context-dir",
            str(temp_root),
        ]
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Validation failed for {report_path}")


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Build task_status_report artifact.")
    parser.add_argument("--task-id", required=True, help="Task id to summarize.")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=repo_root / "runtime",
        help="Runtime root directory.",
    )
    parser.add_argument(
        "--failure-taxonomy",
        type=Path,
        default=repo_root / "instructions" / "failure_reason_taxonomy.pxml",
        help="Failure reason taxonomy artifact path.",
    )
    parser.add_argument(
        "--validator",
        type=Path,
        default=repo_root / "scripts" / "pxml_validator.py",
        help="PXML validator path.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip validator execution for generated report.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime_root = args.runtime_root.resolve()
    validator_path = args.validator.resolve()
    taxonomy_path = args.failure_taxonomy.resolve()

    if not runtime_root.exists():
        print(f"ERROR: runtime root not found: {runtime_root}", file=sys.stderr)
        return 2

    refs_map = latest_map(runtime_root, args.task_id)
    if refs_map["task_intake"] is None:
        print(
            f"ERROR: task_intake artifact not found for task_id={args.task_id}",
            file=sys.stderr,
        )
        return 2

    taxonomy_codes = load_taxonomy_codes(taxonomy_path)
    report_tree, doc_id, status = build_status_report(
        runtime_root=runtime_root,
        task_id=args.task_id,
        refs_map=refs_map,
        taxonomy_codes=taxonomy_codes,
    )

    reports_dir = runtime_root / "status" / "reports"
    ensure_dir(reports_dir)
    report_path = reports_dir / f"{doc_id}.pxml"
    write_xml(report_tree, report_path)

    latest_path = (
        runtime_root / "latest" / f"{sanitize(args.task_id)}_task_status_report.pxml"
    )
    ensure_dir(latest_path.parent)
    shutil.copy2(report_path, latest_path)

    update_indexes(runtime_root, args.task_id, doc_id, report_path)

    if not args.skip_validate:
        if not validator_path.exists():
            print(f"ERROR: validator not found: {validator_path}", file=sys.stderr)
            return 2
        context_files: List[Path] = []
        for item in refs_map.values():
            if item is None:
                continue
            context_files.append(item.path)
        if taxonomy_path.exists():
            context_files.append(taxonomy_path)
        try:
            run_validation(validator_path, report_path, context_files)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    print(f"Generated task_status_report: {report_path}")
    print(f"current_status={status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
