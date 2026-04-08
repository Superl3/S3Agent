#!/usr/bin/env python3
"""Read-only exploration runner for investigation and design packets."""

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
from pathlib import Path
from typing import Dict, List, Optional, Sequence

try:
    from lxml import etree
except ModuleNotFoundError:
    print(
        "ERROR: lxml is required. Install with: python -m pip install lxml",
        file=sys.stderr,
    )
    raise SystemExit(3)

from repo_scout import RepoScoutResult, run_repo_scout
from context_contract import load_exploration_bundle, promote_exploration_result


NS = "urn:pxml:v1"
NSMAP = {None: NS}
XPATH_NS = {"p": NS}
READ_ONLY_SHAPES = {"read_only_investigation", "read_only_design_artifact"}


@dataclass
class ArtifactInfo:
    path: Path
    doc_id: str
    doc_class: str
    task_id: str
    sequence: int
    created_at: str


@dataclass
class PacketInfo:
    path: Path
    doc_id: str
    task_id: str
    run_id: str
    sequence: int
    created_at: str
    content_sha256: str
    acceptance_lock_hash: str
    write_intent: bool
    execution_shape: str
    task_summary: str
    localization_targets: List[str]


@dataclass
class RequestInfo:
    path: Path
    doc_id: str
    task_id: str
    requester_agent: str
    request_kind: str
    blocking: bool
    reason_code: str
    focus_questions: List[str]
    target_hints: List[str]
    contract_change_suspected: bool
    packet_doc_id: str
    baseline_exploration_doc_id: str


def q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def now_iso() -> str:
    from datetime import datetime, timezone

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def text_at(tree: etree._ElementTree, xpath_expr: str) -> Optional[str]:
    values = tree.xpath(xpath_expr, namespaces=XPATH_NS)
    if not values:
        return None
    first = values[0]
    if isinstance(first, etree._Element):
        text = first.text
    else:
        text = str(first)
    if text is None:
        return None
    normalized = text.strip()
    return normalized or None


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


def parse_artifact(path: Path) -> Optional[ArtifactInfo]:
    try:
        tree = etree.parse(str(path))
    except etree.XMLSyntaxError:
        return None
    doc_id = text_at(tree, "/p:pxml/p:meta/p:doc_id")
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    task_id = text_at(tree, "/p:pxml/p:meta/p:task_id")
    seq_text = text_at(tree, "/p:pxml/p:meta/p:sequence")
    created_at = text_at(tree, "/p:pxml/p:meta/p:created_at")
    if not all([doc_id, doc_class, task_id, seq_text, created_at]):
        return None
    try:
        sequence = int(seq_text)
    except ValueError:
        return None
    return ArtifactInfo(
        path=path,
        doc_id=doc_id,
        doc_class=doc_class,
        task_id=task_id,
        sequence=sequence,
        created_at=created_at,
    )


def latest_for_task(
    directory: Path, doc_class: str, task_id: str
) -> Optional[ArtifactInfo]:
    matches: List[ArtifactInfo] = []
    for path in discover_pxml_files(directory):
        parsed = parse_artifact(path)
        if parsed is None:
            continue
        if parsed.task_id != task_id or parsed.doc_class != doc_class:
            continue
        matches.append(parsed)
    if not matches:
        return None
    matches.sort(key=lambda item: (item.sequence, item.created_at, str(item.path)))
    return matches[-1]


def next_sequence(runtime_root: Path, task_id: str) -> int:
    max_sequence = 0
    for path in discover_pxml_files(runtime_root):
        parsed = parse_artifact(path)
        if parsed is None or parsed.task_id != task_id:
            continue
        max_sequence = max(max_sequence, parsed.sequence)
    return max_sequence + 1


def find_artifact_by_doc_id(runtime_root: Path, doc_id: str) -> Optional[ArtifactInfo]:
    for path in discover_pxml_files(runtime_root):
        parsed = parse_artifact(path)
        if parsed is None:
            continue
        if parsed.doc_id == doc_id:
            return parsed
    return None


def parse_packet(packet_path: Path) -> PacketInfo:
    tree = etree.parse(str(packet_path))
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    if doc_class != "execution_packet":
        raise ValueError(f"Input artifact must be execution_packet (got {doc_class!r})")

    required = {
        "doc_id": text_at(tree, "/p:pxml/p:meta/p:doc_id"),
        "task_id": text_at(tree, "/p:pxml/p:meta/p:task_id"),
        "run_id": text_at(tree, "/p:pxml/p:meta/p:run_id"),
        "sequence": text_at(tree, "/p:pxml/p:meta/p:sequence"),
        "created_at": text_at(tree, "/p:pxml/p:meta/p:created_at"),
        "content_sha256": text_at(tree, "/p:pxml/p:integrity/p:content_sha256"),
        "acceptance_lock_hash": text_at(
            tree, "/p:pxml/p:payload/p:acceptance_lock_hash"
        ),
        "execution_shape": text_at(tree, "/p:pxml/p:payload/p:execution_shape"),
        "task_summary": text_at(tree, "/p:pxml/p:payload/p:task_summary"),
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise ValueError(
            "execution_packet missing required fields: " + ", ".join(sorted(missing))
        )

    write_text = text_at(tree, "/p:pxml/p:payload/p:write_intent")
    localization_nodes = tree.xpath(
        "/p:pxml/p:payload/p:localization_targets/p:item/text()", namespaces=XPATH_NS
    )
    localization_targets = [
        item.strip() for item in localization_nodes if item and item.strip()
    ]

    return PacketInfo(
        path=packet_path,
        doc_id=required["doc_id"] or "",
        task_id=required["task_id"] or "",
        run_id=required["run_id"] or "",
        sequence=int(required["sequence"] or "0"),
        created_at=required["created_at"] or "",
        content_sha256=required["content_sha256"] or "",
        acceptance_lock_hash=required["acceptance_lock_hash"] or "",
        write_intent=(write_text or "true").lower() == "true",
        execution_shape=required["execution_shape"] or "",
        task_summary=required["task_summary"] or "",
        localization_targets=localization_targets,
    )


def load_intake_text(intake_info: Optional[ArtifactInfo]) -> tuple[str, str]:
    if intake_info is None:
        return "", ""
    tree = etree.parse(str(intake_info.path))
    request_text = text_at(tree, "/p:pxml/p:payload/p:request_text") or ""
    requested_outcome = text_at(tree, "/p:pxml/p:payload/p:requested_outcome") or ""
    return request_text, requested_outcome


def parse_request(request_path: Path) -> RequestInfo:
    tree = etree.parse(str(request_path))
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    if doc_class != "exploration_request":
        raise ValueError(
            f"Input artifact must be exploration_request (got {doc_class!r})"
        )

    required = {
        "doc_id": text_at(tree, "/p:pxml/p:meta/p:doc_id"),
        "task_id": text_at(tree, "/p:pxml/p:meta/p:task_id"),
        "requester_agent": text_at(tree, "/p:pxml/p:payload/p:requester_agent"),
        "request_kind": text_at(tree, "/p:pxml/p:payload/p:request_kind"),
        "reason_code": text_at(tree, "/p:pxml/p:payload/p:reason_code"),
        "packet_doc_id": text_at(
            tree, "/p:pxml/p:refs/p:ref[p:relation='request_packet']/p:doc_id"
        ),
        "baseline_doc_id": text_at(
            tree, "/p:pxml/p:refs/p:ref[p:relation='baseline_context']/p:doc_id"
        ),
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise ValueError(
            "exploration_request missing required fields: " + ", ".join(sorted(missing))
        )

    blocking_text = text_at(tree, "/p:pxml/p:payload/p:blocking") or "false"
    focus_questions = [
        item.strip()
        for item in tree.xpath(
            "/p:pxml/p:payload/p:focus_questions/p:item/text()", namespaces=XPATH_NS
        )
        if item and item.strip()
    ]
    target_hints = [
        item.strip()
        for item in tree.xpath(
            "/p:pxml/p:payload/p:target_hints/p:item/text()", namespaces=XPATH_NS
        )
        if item and item.strip()
    ]
    contract_change_text = (
        text_at(tree, "/p:pxml/p:payload/p:contract_change_suspected") or "false"
    )

    return RequestInfo(
        path=request_path,
        doc_id=required["doc_id"] or "",
        task_id=required["task_id"] or "",
        requester_agent=required["requester_agent"] or "",
        request_kind=required["request_kind"] or "",
        blocking=blocking_text.lower() == "true",
        reason_code=required["reason_code"] or "",
        focus_questions=focus_questions,
        target_hints=target_hints,
        contract_change_suspected=contract_change_text.lower() == "true",
        packet_doc_id=required["packet_doc_id"] or "",
        baseline_exploration_doc_id=required["baseline_doc_id"] or "",
    )


def write_xml(tree: etree._ElementTree, path: Path) -> None:
    ensure_dir(path.parent)
    tree.write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True)


def update_indexes(
    runtime_root: Path,
    task_id: str,
    doc_id: str,
    result_path: Path,
    exploration_scope: str,
) -> None:
    promote_exploration_result(
        runtime_root=runtime_root,
        task_id=task_id,
        doc_id=doc_id,
        result_path=result_path,
        exploration_scope=exploration_scope,
    )


def append_trace_event(
    trace_script: Path,
    runtime_root: Path,
    task_id: str,
    event_type: str,
    message: str,
    lineage_lock_sha256: str,
    artifact_files: Sequence[Path],
    reason_code: Optional[str] = None,
) -> None:
    command = [
        sys.executable,
        str(trace_script),
        "--task-id",
        task_id,
        "--event-type",
        event_type,
        "--actor",
        "explorer",
        "--message",
        message,
        "--runtime-root",
        str(runtime_root),
        "--lineage-lock-sha256",
        lineage_lock_sha256,
    ]
    if reason_code:
        command.extend(["--reason-code", reason_code])
    for artifact in artifact_files:
        command.extend(["--artifact-file", str(artifact)])
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"trace_appender failed for event {event_type!r}")


def run_validation(
    validator: Path, result_path: Path, context_files: Sequence[Path]
) -> None:
    with tempfile.TemporaryDirectory(prefix="pxml_explore_validate_") as temp_dir:
        temp_root = Path(temp_dir)
        copied_result = temp_root / result_path.name
        shutil.copy2(result_path, copied_result)
        for file_path in context_files:
            if not file_path.exists():
                continue
            shutil.copy2(file_path, temp_root / file_path.name)

        command = [
            sys.executable,
            str(validator),
            str(copied_result),
            "--context-dir",
            str(temp_root),
        ]
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Validation failed for {result_path}")


def build_exploration_result(
    packet: PacketInfo,
    route_info: Optional[ArtifactInfo],
    intake_info: Optional[ArtifactInfo],
    scout: RepoScoutResult,
    doc_id: str,
    sequence: int,
    request_info: Optional[RequestInfo],
    parent_exploration: Optional[ArtifactInfo],
) -> etree._ElementTree:
    root = etree.Element(q("pxml"), nsmap=NSMAP)

    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = doc_id
    etree.SubElement(meta, q("doc_class")).text = "exploration_result"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = packet.task_id
    etree.SubElement(meta, q("run_id")).text = packet.run_id
    etree.SubElement(meta, q("sequence")).text = str(sequence)
    etree.SubElement(meta, q("writer_agent")).text = "explorer"
    etree.SubElement(meta, q("created_at")).text = now_iso()

    refs = etree.SubElement(root, q("refs"))
    packet_ref = etree.SubElement(refs, q("ref"))
    etree.SubElement(packet_ref, q("doc_id")).text = packet.doc_id
    etree.SubElement(packet_ref, q("doc_class")).text = "execution_packet"
    etree.SubElement(packet_ref, q("relation")).text = "exploration_target"
    if route_info is not None:
        route_ref = etree.SubElement(refs, q("ref"))
        etree.SubElement(route_ref, q("doc_id")).text = route_info.doc_id
        etree.SubElement(route_ref, q("doc_class")).text = "manager_route"
        etree.SubElement(route_ref, q("relation")).text = "latest_route"
    if intake_info is not None:
        intake_ref = etree.SubElement(refs, q("ref"))
        etree.SubElement(intake_ref, q("doc_id")).text = intake_info.doc_id
        etree.SubElement(intake_ref, q("doc_class")).text = "task_intake"
        etree.SubElement(intake_ref, q("relation")).text = "source_intake"
    if request_info is not None:
        request_ref = etree.SubElement(refs, q("ref"))
        etree.SubElement(request_ref, q("doc_id")).text = request_info.doc_id
        etree.SubElement(request_ref, q("doc_class")).text = "exploration_request"
        etree.SubElement(request_ref, q("relation")).text = "request"
    if parent_exploration is not None:
        parent_ref = etree.SubElement(refs, q("ref"))
        etree.SubElement(parent_ref, q("doc_id")).text = parent_exploration.doc_id
        etree.SubElement(parent_ref, q("doc_class")).text = "exploration_result"
        etree.SubElement(parent_ref, q("relation")).text = "parent_exploration"

    payload = etree.SubElement(root, q("payload"))
    payload_packet_ref = etree.SubElement(payload, q("packet_ref"))
    etree.SubElement(payload_packet_ref, q("doc_id")).text = packet.doc_id
    etree.SubElement(payload_packet_ref, q("doc_class")).text = "execution_packet"
    etree.SubElement(payload_packet_ref, q("relation")).text = "exploration_target"
    etree.SubElement(payload, q("task_id")).text = packet.task_id
    etree.SubElement(payload, q("exploration_kind")).text = scout.exploration_kind
    etree.SubElement(payload, q("exploration_scope")).text = scout.exploration_scope
    etree.SubElement(payload, q("actionability")).text = scout.actionability
    etree.SubElement(payload, q("target_root")).text = scout.target_root
    etree.SubElement(payload, q("context_producer")).text = "explorer_runner"
    etree.SubElement(payload, q("context_mode")).text = (
        "focused_refresh" if request_info is not None else "read_only_exploration"
    )
    etree.SubElement(payload, q("search_scope")).text = scout.search_scope
    etree.SubElement(payload, q("budget_used")).text = scout.budget_used

    providers_node = etree.SubElement(payload, q("providers"))
    for provider in scout.providers:
        provider_node = etree.SubElement(providers_node, q("provider"))
        etree.SubElement(provider_node, q("name")).text = provider.name
        etree.SubElement(provider_node, q("used")).text = (
            "true" if provider.used else "false"
        )
        etree.SubElement(provider_node, q("success")).text = (
            "true" if provider.success else "false"
        )
        etree.SubElement(provider_node, q("notes")).text = provider.notes

    focus_node = etree.SubElement(payload, q("focus_questions"))
    for item in scout.focus_questions:
        etree.SubElement(focus_node, q("item")).text = item

    findings_node = etree.SubElement(payload, q("key_findings"))
    for item in scout.key_findings:
        etree.SubElement(findings_node, q("item")).text = item

    evidence_node = etree.SubElement(payload, q("evidence_items"))
    for item in scout.evidence_items:
        evidence = etree.SubElement(evidence_node, q("evidence"))
        etree.SubElement(evidence, q("source_provider")).text = item.source_provider
        etree.SubElement(evidence, q("path")).text = item.path
        if item.line_start is not None:
            etree.SubElement(evidence, q("line_start")).text = str(item.line_start)
        if item.line_end is not None:
            etree.SubElement(evidence, q("line_end")).text = str(item.line_end)
        if item.symbol:
            etree.SubElement(evidence, q("symbol")).text = item.symbol
        etree.SubElement(evidence, q("summary")).text = item.summary

    if scout.open_questions:
        open_node = etree.SubElement(payload, q("open_questions"))
        for item in scout.open_questions:
            etree.SubElement(open_node, q("item")).text = item

    next_actions = etree.SubElement(payload, q("recommended_next_actions"))
    for item in scout.recommended_next_actions:
        etree.SubElement(next_actions, q("item")).text = item

    if scout.cache_refs:
        cache_node = etree.SubElement(payload, q("cache_refs"))
        for item in scout.cache_refs:
            etree.SubElement(cache_node, q("item")).text = item

    if scout.candidate_files:
        candidate_files = etree.SubElement(payload, q("candidate_files"))
        for item in scout.candidate_files:
            etree.SubElement(candidate_files, q("item")).text = item

    if scout.target_files:
        target_files = etree.SubElement(payload, q("target_files"))
        for item in scout.target_files:
            etree.SubElement(target_files, q("item")).text = item

    etree.SubElement(payload, q("usability_state")).text = scout.usability_state
    etree.SubElement(payload, q("confidence")).text = scout.confidence
    etree.SubElement(payload, q("evidence_count")).text = str(scout.evidence_count)
    etree.SubElement(payload, q("open_questions_count")).text = str(
        scout.open_questions_count
    )

    etree.SubElement(payload, q("completion_state")).text = scout.completion_state
    if scout.blocked_reason:
        etree.SubElement(payload, q("blocked_reason")).text = scout.blocked_reason
    etree.SubElement(payload, q("escalation_requested")).text = (
        "true" if scout.escalation_requested else "false"
    )
    notes_node = etree.SubElement(payload, q("notes"))
    for item in scout.notes or ["exploration completed"]:
        etree.SubElement(notes_node, q("item")).text = item

    integrity = etree.SubElement(root, q("integrity"))
    etree.SubElement(integrity, q("content_sha256")).text = compute_content_hash(
        meta, refs, payload
    )
    etree.SubElement(integrity, q("parent_sha256")).text = packet.content_sha256

    return etree.ElementTree(root)


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Run read-only exploration for one packet."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--packet", type=Path, help="execution_packet input file")
    mode.add_argument("--request", type=Path, help="exploration_request input file")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=repo_root / "runtime",
        help="Runtime root directory.",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=repo_root,
        help="Workspace root to scout.",
    )
    parser.add_argument(
        "--validator",
        type=Path,
        default=repo_root / "scripts" / "pxml_validator.py",
        help="PXML validator path.",
    )
    parser.add_argument(
        "--trace-script",
        type=Path,
        default=repo_root / "scripts" / "trace_appender.py",
        help="Trace appender path.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip validator execution for the generated exploration_result.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime_root = args.runtime_root.resolve()
    workspace_root = args.workspace_root.resolve()
    validator_path = args.validator.resolve()
    trace_script = args.trace_script.resolve()

    if not workspace_root.exists():
        print(f"ERROR: workspace root not found: {workspace_root}", file=sys.stderr)
        return 2

    request_info: Optional[RequestInfo] = None
    parent_exploration: Optional[ArtifactInfo] = None
    packet_artifact_path: Path
    if args.request is not None:
        request_path = args.request.resolve()
        if not request_path.exists():
            print(f"ERROR: request not found: {request_path}", file=sys.stderr)
            return 2
        try:
            request_info = parse_request(request_path)
        except Exception as exc:
            print(f"ERROR: failed to parse exploration_request: {exc}", file=sys.stderr)
            return 2
        packet_artifact = find_artifact_by_doc_id(
            runtime_root, request_info.packet_doc_id
        )
        if packet_artifact is None:
            print(
                f"ERROR: request packet ref not found: {request_info.packet_doc_id}",
                file=sys.stderr,
            )
            return 2
        packet_artifact_path = packet_artifact.path
        parent_exploration = find_artifact_by_doc_id(
            runtime_root, request_info.baseline_exploration_doc_id
        )
        if parent_exploration is None:
            print(
                "ERROR: baseline exploration_result referenced by request was not found",
                file=sys.stderr,
            )
            return 2
    else:
        assert args.packet is not None
        packet_artifact_path = args.packet.resolve()
        if not packet_artifact_path.exists():
            print(f"ERROR: packet not found: {packet_artifact_path}", file=sys.stderr)
            return 2

    try:
        packet = parse_packet(packet_artifact_path)
    except Exception as exc:
        print(f"ERROR: failed to parse execution_packet: {exc}", file=sys.stderr)
        return 2

    if request_info is None:
        if packet.write_intent:
            print(
                "ERROR: explorer_runner requires write_intent=false packet",
                file=sys.stderr,
            )
            return 2
        if packet.execution_shape not in READ_ONLY_SHAPES:
            print(
                "ERROR: explorer_runner only supports read-only execution shapes "
                f"(got {packet.execution_shape!r})",
                file=sys.stderr,
            )
            return 2

    route_info = latest_for_task(
        runtime_root / "packets" / "manager_route", "manager_route", packet.task_id
    )
    intake_info = latest_for_task(
        runtime_root / "inbox" / "task_intake", "task_intake", packet.task_id
    )
    request_text, requested_outcome = load_intake_text(intake_info)

    try:
        append_trace_event(
            trace_script=trace_script,
            runtime_root=runtime_root,
            task_id=packet.task_id,
            event_type="explore_start",
            message=(
                "Explorer runner started focused repository scouting."
                if request_info is not None
                else "Explorer runner started read-only repository scouting."
            ),
            lineage_lock_sha256=packet.acceptance_lock_hash,
            artifact_files=[packet.path]
            + ([request_info.path] if request_info is not None else []),
        )

        scout = run_repo_scout(
            workspace_root=workspace_root,
            request_text=request_text,
            requested_outcome=requested_outcome,
            task_summary=packet.task_summary,
            execution_shape=packet.execution_shape,
            localization_targets=packet.localization_targets,
            cache_root=runtime_root / "exploration" / "cache",
            cache_ref_base=runtime_root,
            exploration_scope=(
                "focused_refresh" if request_info is not None else "baseline"
            ),
            focus_questions_override=(
                request_info.focus_questions if request_info is not None else None
            ),
            target_hints=(
                request_info.target_hints if request_info is not None else None
            ),
            request_kind=(
                request_info.request_kind if request_info is not None else None
            ),
            contract_change_suspected=(
                request_info.contract_change_suspected
                if request_info is not None
                else False
            ),
        )
        if request_info is not None and parent_exploration is not None:
            parent_bundle = load_exploration_bundle(parent_exploration.path)
            parent_findings = set(parent_bundle.key_findings)
            parent_evidence = set(parent_bundle.evidence_paths)
            parent_questions = set(parent_bundle.open_questions)
            scout.key_findings = [
                item for item in scout.key_findings if item not in parent_findings
            ]
            scout.evidence_items = [
                item
                for item in scout.evidence_items
                if item.path not in parent_evidence
            ]
            scout.open_questions = [
                item for item in scout.open_questions if item not in parent_questions
            ]
            scout.candidate_files = [
                item
                for item in scout.candidate_files
                if item not in parent_bundle.candidate_files
                and item not in parent_evidence
            ]
            scout.evidence_count = len(scout.evidence_items)
            scout.open_questions_count = len(scout.open_questions)
            if scout.evidence_count == 0 and scout.open_questions_count == 0:
                scout.usability_state = "empty"
                scout.confidence = "low"
                scout.actionability = "advisory_only"
                scout.recommended_next_actions.insert(
                    0,
                    "Focused refresh produced no net-new delta beyond the pinned baseline context.",
                )
            elif scout.evidence_count == 0:
                scout.usability_state = "weak"
                scout.confidence = "low"
    except Exception as exc:
        try:
            append_trace_event(
                trace_script=trace_script,
                runtime_root=runtime_root,
                task_id=packet.task_id,
                event_type="reject",
                message="Explorer runner failed before exploration_result generation.",
                lineage_lock_sha256=packet.acceptance_lock_hash,
                artifact_files=[packet.path],
                reason_code="system_exploration_runner_error",
            )
        except Exception:
            pass
        print(f"ERROR: exploration failed: {exc}", file=sys.stderr)
        return 1

    sequence = next_sequence(runtime_root, packet.task_id)
    doc_id = f"doc_exploration_result_{sanitize(packet.task_id)[:20]}_{sequence:04d}"
    if not re.fullmatch(r"doc_[a-z0-9][a-z0-9._-]{5,63}", doc_id):
        doc_id = (
            f"doc_exploration_result_{sequence:04d}_"
            f"{sha256_hex(packet.task_id.encode('utf-8'))[:8]}"
        )

    result_tree = build_exploration_result(
        packet=packet,
        route_info=route_info,
        intake_info=intake_info,
        scout=scout,
        doc_id=doc_id,
        sequence=sequence,
        request_info=request_info,
        parent_exploration=parent_exploration,
    )

    results_dir = runtime_root / "exploration" / "results"
    ensure_dir(results_dir)
    result_path = results_dir / f"{doc_id}.pxml"
    publish_source = result_path

    if not args.skip_validate:
        if not validator_path.exists():
            print(f"ERROR: validator not found: {validator_path}", file=sys.stderr)
            return 2
        context_files: List[Path] = [packet.path]
        if route_info is not None:
            context_files.append(route_info.path)
        if intake_info is not None:
            context_files.append(intake_info.path)
        if request_info is not None:
            context_files.append(request_info.path)
        if parent_exploration is not None:
            context_files.append(parent_exploration.path)
        with tempfile.TemporaryDirectory(prefix="pxml_explore_publish_") as temp_dir:
            draft_path = Path(temp_dir) / result_path.name
            write_xml(result_tree, draft_path)
            try:
                run_validation(validator_path, draft_path, context_files)
            except RuntimeError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 1
            shutil.copy2(draft_path, result_path)
            publish_source = result_path
    else:
        write_xml(result_tree, result_path)

    update_indexes(
        runtime_root,
        packet.task_id,
        doc_id,
        result_path,
        scout.exploration_scope,
    )

    try:
        append_trace_event(
            trace_script=trace_script,
            runtime_root=runtime_root,
            task_id=packet.task_id,
            event_type="explore_done",
            message=(
                "Explorer runner emitted focused exploration_result."
                if request_info is not None
                else "Explorer runner emitted exploration_result."
            ),
            lineage_lock_sha256=packet.acceptance_lock_hash,
            artifact_files=[result_path],
        )
    except Exception as exc:
        print(f"ERROR: failed to append explore_done trace: {exc}", file=sys.stderr)
        return 1

    print(f"Generated exploration_result: {result_path}")
    print(f"completion_state={scout.completion_state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
