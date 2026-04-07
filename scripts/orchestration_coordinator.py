#!/usr/bin/env python3
"""Batch 4 conditional sidecar orchestration coordinator.

This script performs lightweight orchestration over existing manager_route and
execution_packet artifacts. It does not write code and does not modify
acceptance criteria.
"""

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

from runtime_bootstrap import bootstrap_runtime

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
LANE_MAP = {
    "direct": (False, False, False),
    "planner_pre": (True, False, False),
    "reviewer_post": (False, True, False),
    "verifier_post": (False, False, True),
    "full_lane": (True, True, True),
}


@dataclass
class RouteInfo:
    path: Path
    doc_id: str
    task_id: str
    run_id: str
    sequence: int
    created_at: str
    selected_path: str
    planner_lane: bool
    reviewer_lane: bool
    verifier_lane: bool
    risk_level: str
    acceptance_lock_sha256: str
    content_sha256: str
    intake_doc_id: Optional[str]


@dataclass
class PacketInfo:
    path: Path
    doc_id: str
    task_id: str
    run_id: str
    sequence: int
    created_at: str
    acceptance_lock_hash: str
    content_sha256: str


@dataclass
class ExplorationInfo:
    path: Path
    doc_id: str
    task_id: str
    completion_state: str
    key_findings: List[str]
    open_questions: List[str]
    evidence_paths: List[str]


@dataclass
class RetryPolicy:
    implementer_max_attempts: int = 2
    reviewer_max_attempts: int = 2
    verifier_max_attempts: int = 2
    same_cause_fast_escalation_threshold: int = 2


@dataclass
class EscalationPolicy:
    repeated_failure_threshold: int = 3
    blocker_threshold: int = 1
    inconclusive_threshold: int = 2
    stop_after_escalation: bool = True


def q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def sanitize(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9._-]", "_", lowered)
    lowered = re.sub(r"_+", "_", lowered).strip("_")
    return lowered or "id"


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


def compute_content_hash(
    meta: etree._Element, refs: Optional[etree._Element], payload: etree._Element
) -> str:
    material = etree.Element(q("hash_material"), nsmap=NSMAP)
    material.append(copy.deepcopy(meta))
    if refs is not None:
        material.append(copy.deepcopy(refs))
    material.append(copy.deepcopy(payload))
    c14n = etree.tostring(material, method="c14n", exclusive=True, with_comments=False)
    return hashlib.sha256(c14n).hexdigest()


def discover_artifacts(path: Path) -> List[Path]:
    if not path.exists():
        return []
    files = [candidate for candidate in path.rglob("*.pxml") if candidate.is_file()]
    files.sort()
    return files


def parse_route(path: Path) -> RouteInfo:
    tree = etree.parse(str(path))
    if text_at(tree, "/p:pxml/p:meta/p:doc_class") != "manager_route":
        raise ValueError(f"Not manager_route artifact: {path}")

    doc_id = text_at(tree, "/p:pxml/p:meta/p:doc_id")
    task_id = text_at(tree, "/p:pxml/p:meta/p:task_id")
    run_id = text_at(tree, "/p:pxml/p:meta/p:run_id")
    seq_text = text_at(tree, "/p:pxml/p:meta/p:sequence")
    created_at = text_at(tree, "/p:pxml/p:meta/p:created_at")
    selected_path = text_at(tree, "/p:pxml/p:payload/p:selected_path")
    planner_flag = text_at(tree, "/p:pxml/p:payload/p:lane_flags/p:planner")
    reviewer_flag = text_at(tree, "/p:pxml/p:payload/p:lane_flags/p:reviewer")
    verifier_flag = text_at(tree, "/p:pxml/p:payload/p:lane_flags/p:verifier")
    risk_level = text_at(tree, "/p:pxml/p:payload/p:risk_level")
    lock_hash = text_at(tree, "/p:pxml/p:payload/p:acceptance_lock/p:lock_sha256")
    content_sha = text_at(tree, "/p:pxml/p:integrity/p:content_sha256")

    refs = tree.xpath("/p:pxml/p:refs/p:ref", namespaces=XPATH_NS)
    intake_doc_id: Optional[str] = None
    for node in refs:
        node_tree = etree.ElementTree(node)
        doc_class = text_at(node_tree, "./p:doc_class")
        if doc_class == "task_intake":
            intake_doc_id = text_at(node_tree, "./p:doc_id")
            break

    required = {
        "doc_id": doc_id,
        "task_id": task_id,
        "run_id": run_id,
        "sequence": seq_text,
        "created_at": created_at,
        "selected_path": selected_path,
        "risk_level": risk_level,
        "acceptance_lock_sha256": lock_hash,
        "content_sha256": content_sha,
    }
    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise ValueError(f"manager_route missing required fields: {', '.join(missing)}")

    assert doc_id is not None
    assert task_id is not None
    assert run_id is not None
    assert seq_text is not None
    assert created_at is not None
    assert selected_path is not None
    assert planner_flag is not None
    assert reviewer_flag is not None
    assert verifier_flag is not None
    assert risk_level is not None
    assert lock_hash is not None
    assert content_sha is not None

    return RouteInfo(
        path=path,
        doc_id=doc_id,
        task_id=task_id,
        run_id=run_id,
        sequence=int(seq_text),
        created_at=created_at,
        selected_path=selected_path,
        planner_lane=planner_flag == "true",
        reviewer_lane=reviewer_flag == "true",
        verifier_lane=verifier_flag == "true",
        risk_level=risk_level,
        acceptance_lock_sha256=lock_hash,
        content_sha256=content_sha,
        intake_doc_id=intake_doc_id,
    )


def parse_packet(path: Path) -> PacketInfo:
    tree = etree.parse(str(path))
    if text_at(tree, "/p:pxml/p:meta/p:doc_class") != "execution_packet":
        raise ValueError(f"Not execution_packet artifact: {path}")

    doc_id = text_at(tree, "/p:pxml/p:meta/p:doc_id")
    task_id = text_at(tree, "/p:pxml/p:meta/p:task_id")
    run_id = text_at(tree, "/p:pxml/p:meta/p:run_id")
    seq_text = text_at(tree, "/p:pxml/p:meta/p:sequence")
    created_at = text_at(tree, "/p:pxml/p:meta/p:created_at")
    lock_hash = text_at(tree, "/p:pxml/p:payload/p:acceptance_lock_hash")
    content_sha = text_at(tree, "/p:pxml/p:integrity/p:content_sha256")

    required = {
        "doc_id": doc_id,
        "task_id": task_id,
        "run_id": run_id,
        "sequence": seq_text,
        "created_at": created_at,
        "acceptance_lock_hash": lock_hash,
        "content_sha256": content_sha,
    }
    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise ValueError(
            f"execution_packet missing required fields: {', '.join(missing)}"
        )

    assert doc_id is not None
    assert task_id is not None
    assert run_id is not None
    assert seq_text is not None
    assert created_at is not None
    assert lock_hash is not None
    assert content_sha is not None

    return PacketInfo(
        path=path,
        doc_id=doc_id,
        task_id=task_id,
        run_id=run_id,
        sequence=int(seq_text),
        created_at=created_at,
        acceptance_lock_hash=lock_hash,
        content_sha256=content_sha,
    )


def parse_meta(path: Path) -> Optional[Tuple[str, str, int, str]]:
    try:
        tree = etree.parse(str(path))
    except etree.XMLSyntaxError:
        return None
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    task_id = text_at(tree, "/p:pxml/p:meta/p:task_id")
    seq_text = text_at(tree, "/p:pxml/p:meta/p:sequence")
    created_at = text_at(tree, "/p:pxml/p:meta/p:created_at")
    if doc_class is None or task_id is None or seq_text is None or created_at is None:
        return None
    try:
        sequence = int(seq_text)
    except ValueError:
        return None
    return doc_class, task_id, sequence, created_at


def latest_artifact_for_task(
    directory: Path, doc_class: str, task_id: str
) -> Optional[Path]:
    candidates: List[Tuple[int, str, Path]] = []
    for path in discover_artifacts(directory):
        parsed = parse_meta(path)
        if parsed is None:
            continue
        parsed_class, parsed_task, sequence, created_at = parsed
        if parsed_class != doc_class or parsed_task != task_id:
            continue
        candidates.append((sequence, created_at, path))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], str(item[2])))
    return candidates[-1][2]


def find_artifact_by_doc_id(runtime_root: Path, doc_id: str) -> Optional[Path]:
    priority_dirs = [
        runtime_root / "inbox" / "task_intake",
        runtime_root / "packets" / "manager_route",
        runtime_root / "packets" / "execution_packet",
        runtime_root / "sidecars" / "planner",
        runtime_root / "sidecars" / "reviewer",
        runtime_root / "verification" / "results",
    ]
    target_name = f"{doc_id}.pxml"
    for directory in priority_dirs:
        candidate = directory / target_name
        if candidate.exists():
            return candidate
    for path in discover_artifacts(runtime_root):
        if path.name != target_name:
            continue
        return path
    return None


def latest_exploration_for_task(
    runtime_root: Path, task_id: str
) -> Optional[ExplorationInfo]:
    latest_path = (
        runtime_root / "latest" / f"{sanitize(task_id)}_exploration_result.pxml"
    )
    if not latest_path.exists():
        return None
    tree = etree.parse(str(latest_path))
    if text_at(tree, "/p:pxml/p:meta/p:doc_class") != "exploration_result":
        return None
    doc_id = text_at(tree, "/p:pxml/p:meta/p:doc_id")
    parsed_task_id = text_at(tree, "/p:pxml/p:meta/p:task_id")
    completion_state = text_at(tree, "/p:pxml/p:payload/p:completion_state")
    if not doc_id or not parsed_task_id or not completion_state:
        return None
    key_findings = [
        item.strip()
        for item in tree.xpath(
            "/p:pxml/p:payload/p:key_findings/p:item/text()", namespaces=XPATH_NS
        )
        if item and item.strip()
    ]
    open_questions = [
        item.strip()
        for item in tree.xpath(
            "/p:pxml/p:payload/p:open_questions/p:item/text()", namespaces=XPATH_NS
        )
        if item and item.strip()
    ]
    evidence_paths = [
        item.strip()
        for item in tree.xpath(
            "/p:pxml/p:payload/p:evidence_items/p:evidence/p:path/text()",
            namespaces=XPATH_NS,
        )
        if item and item.strip()
    ]
    return ExplorationInfo(
        path=latest_path,
        doc_id=doc_id,
        task_id=parsed_task_id,
        completion_state=completion_state,
        key_findings=key_findings,
        open_questions=open_questions,
        evidence_paths=evidence_paths,
    )


def make_doc_id(prefix: str, task_id: str, sequence: int) -> str:
    token = sanitize(task_id)[:18]
    stamp = datetime.now(timezone.utc).strftime("%H%M%S")
    suffix = hashlib.sha256(
        f"{prefix}:{task_id}:{sequence}:{stamp}".encode("utf-8")
    ).hexdigest()[:6]
    doc_id = f"doc_{sanitize(prefix)}_{token}_{sequence:04d}_{stamp}_{suffix}"
    if len(doc_id) > 64:
        doc_id = doc_id[:64]
    if re.fullmatch(r"doc_[a-z0-9][a-z0-9._-]{5,63}", doc_id):
        return doc_id
    return f"doc_{sanitize(prefix)}_{sequence:04d}_{suffix}"


def write_xml(tree: etree._ElementTree, path: Path) -> None:
    ensure_dir(path.parent)
    tree.write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True)


def run_validator(
    validator_path: Path, artifact_path: Path, context_files: Sequence[Path]
) -> None:
    unique_paths = []
    seen = set()
    for path in [artifact_path, *context_files]:
        resolved = path.resolve()
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        unique_paths.append(resolved)

    with tempfile.TemporaryDirectory(prefix="pxml_coord_validate_") as temp_dir:
        temp_root = Path(temp_dir)
        for file_path in unique_paths:
            shutil.copy2(file_path, temp_root / file_path.name)
        cmd = [
            sys.executable,
            str(validator_path),
            str(temp_root / artifact_path.name),
            "--context-dir",
            str(temp_root),
        ]
        run = subprocess.run(cmd, check=False)
        if run.returncode != 0:
            raise RuntimeError(f"Validation failed for {artifact_path}")


def load_retry_policy(path: Path) -> RetryPolicy:
    policy = RetryPolicy()
    if not path.exists():
        return policy
    tree = etree.parse(str(path))
    threshold_text = text_at(
        tree, "/p:pxml/p:payload/p:same_cause_fast_escalation_threshold"
    )
    if threshold_text and threshold_text.isdigit():
        policy.same_cause_fast_escalation_threshold = int(threshold_text)

    rule_nodes = tree.xpath("/p:pxml/p:payload/p:rules/p:rule", namespaces=XPATH_NS)
    for node in rule_nodes:
        node_tree = etree.ElementTree(node)
        applies = node.xpath("./p:applies_to/p:item/text()", namespaces=XPATH_NS)
        applies_l = {item.strip().lower() for item in applies if item.strip()}
        attempts_text = text_at(node_tree, "./p:max_attempts")
        if attempts_text is None or not attempts_text.isdigit():
            continue
        attempts = int(attempts_text)
        if "verifier" in applies_l:
            policy.verifier_max_attempts = attempts
        elif "reviewer" in applies_l:
            policy.reviewer_max_attempts = attempts
        elif "implementer" in applies_l:
            policy.implementer_max_attempts = attempts
    return policy


def load_escalation_policy(path: Path) -> EscalationPolicy:
    policy = EscalationPolicy()
    if not path.exists():
        return policy
    tree = etree.parse(str(path))
    repeated = text_at(tree, "/p:pxml/p:payload/p:repeated_failure_threshold")
    blocker = text_at(tree, "/p:pxml/p:payload/p:blocker_threshold")
    inconclusive = text_at(tree, "/p:pxml/p:payload/p:inconclusive_threshold")
    stop_after = text_at(tree, "/p:pxml/p:payload/p:stop_after_escalation")
    if repeated and repeated.isdigit():
        policy.repeated_failure_threshold = int(repeated)
    if blocker and blocker.isdigit():
        policy.blocker_threshold = int(blocker)
    if inconclusive and inconclusive.isdigit():
        policy.inconclusive_threshold = int(inconclusive)
    if stop_after is not None:
        policy.stop_after_escalation = stop_after.lower() == "true"
    return policy


def append_trace_event(
    trace_script: Path,
    runtime_root: Path,
    task_id: str,
    event_type: str,
    actor: str,
    message: str,
    artifact_files: Sequence[Path],
    reason_code: Optional[str] = None,
    attempt: Optional[int] = None,
    lineage_lock_sha256: Optional[str] = None,
    verify_phase: Optional[str] = None,
) -> None:
    cmd = [
        sys.executable,
        str(trace_script),
        "--task-id",
        task_id,
        "--event-type",
        event_type,
        "--actor",
        actor,
        "--message",
        message,
        "--runtime-root",
        str(runtime_root),
    ]
    if reason_code:
        cmd.extend(["--reason-code", reason_code])
    if attempt is not None:
        cmd.extend(["--attempt", str(attempt)])
    if lineage_lock_sha256:
        cmd.extend(["--lineage-lock-sha256", lineage_lock_sha256])
    if verify_phase:
        cmd.extend(["--verify-phase", verify_phase])
    for artifact in artifact_files:
        cmd.extend(["--artifact-file", str(artifact)])
    run = subprocess.run(cmd, check=False)
    if run.returncode != 0:
        raise RuntimeError(f"trace_appender failed for event_type={event_type}")


def load_trace_events(trace_path: Path) -> List[etree._Element]:
    if not trace_path.exists():
        return []
    tree = etree.parse(str(trace_path))
    return tree.xpath("/p:pxml/p:payload/p:events/p:event", namespaces=XPATH_NS)


def event_types_in_trace(trace_path: Path) -> List[str]:
    events = load_trace_events(trace_path)
    values: List[str] = []
    for event in events:
        value = text_at(etree.ElementTree(event), "./p:event_type")
        if value:
            values.append(value)
    return values


def count_reason_occurrences(
    trace_path: Path, event_type: str, reason_code: str
) -> int:
    events = load_trace_events(trace_path)
    count = 0
    for event in events:
        node_tree = etree.ElementTree(event)
        kind = text_at(node_tree, "./p:event_type")
        reason = text_at(node_tree, "./p:reason_code")
        if kind == event_type and reason == reason_code:
            count += 1
    return count


def append_retry_index(
    runtime_root: Path, task_id: str, stage: str, reason_code: str, attempt: int
) -> None:
    index_path = runtime_root / "index" / "retries" / f"{sanitize(task_id)}.json"
    ensure_dir(index_path.parent)
    payload: Dict[str, object] = {"task_id": task_id, "entries": []}
    if index_path.exists():
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {"task_id": task_id, "entries": []}
    entries = payload.get("entries")
    if not isinstance(entries, list):
        entries = []
    entries.append(
        {
            "time": now_iso(),
            "stage": stage,
            "reason_code": reason_code,
            "attempt": attempt,
        }
    )
    payload["task_id"] = task_id
    payload["entries"] = entries
    index_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def append_escalation_index(
    runtime_root: Path, task_id: str, stage: str, reason_code: str, attempt: int
) -> None:
    index_path = runtime_root / "index" / "escalations" / f"{sanitize(task_id)}.json"
    ensure_dir(index_path.parent)
    payload: Dict[str, object] = {"task_id": task_id, "entries": []}
    if index_path.exists():
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {"task_id": task_id, "entries": []}
    entries = payload.get("entries")
    if not isinstance(entries, list):
        entries = []
    entries.append(
        {
            "time": now_iso(),
            "stage": stage,
            "reason_code": reason_code,
            "attempt": attempt,
        }
    )
    payload["task_id"] = task_id
    payload["entries"] = entries
    index_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def next_sidecar_sequence(runtime_root: Path, task_id: str) -> int:
    max_seq = 0
    for path in discover_artifacts(runtime_root):
        meta = parse_meta(path)
        if meta is None:
            continue
        _doc_class, parsed_task, sequence, _created_at = meta
        if parsed_task != task_id:
            continue
        max_seq = max(max_seq, sequence)
    return max_seq + 1


def build_plan_sidecar(
    runtime_root: Path,
    route: RouteInfo,
    packet: PacketInfo,
    intake_doc_id: str,
    validator_path: Path,
    validate_artifacts: bool,
) -> Path:
    sequence = next_sidecar_sequence(runtime_root, route.task_id)
    doc_id = make_doc_id("plan_sidecar", route.task_id, sequence)
    created_at = now_iso()
    prior_exploration = latest_exploration_for_task(runtime_root, route.task_id)

    root = etree.Element(q("pxml"), nsmap=NSMAP)
    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = doc_id
    etree.SubElement(meta, q("doc_class")).text = "plan_sidecar"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = route.task_id
    etree.SubElement(meta, q("run_id")).text = route.run_id
    etree.SubElement(meta, q("sequence")).text = str(sequence)
    etree.SubElement(meta, q("writer_agent")).text = "planner"
    etree.SubElement(meta, q("created_at")).text = created_at

    refs = etree.SubElement(root, q("refs"))
    ref_intake = etree.SubElement(refs, q("ref"))
    etree.SubElement(ref_intake, q("doc_id")).text = intake_doc_id
    etree.SubElement(ref_intake, q("doc_class")).text = "task_intake"
    etree.SubElement(ref_intake, q("relation")).text = "intake"
    ref_route = etree.SubElement(refs, q("ref"))
    etree.SubElement(ref_route, q("doc_id")).text = route.doc_id
    etree.SubElement(ref_route, q("doc_class")).text = "manager_route"
    etree.SubElement(ref_route, q("relation")).text = "route"
    if prior_exploration is not None:
        ref_exploration = etree.SubElement(refs, q("ref"))
        etree.SubElement(ref_exploration, q("doc_id")).text = prior_exploration.doc_id
        etree.SubElement(ref_exploration, q("doc_class")).text = "exploration_result"
        etree.SubElement(ref_exploration, q("relation")).text = "prior_exploration"

    payload = etree.SubElement(root, q("payload"))
    ambiguities = etree.SubElement(payload, q("ambiguities"))
    if prior_exploration is not None and prior_exploration.open_questions:
        for item in prior_exploration.open_questions[:3]:
            etree.SubElement(ambiguities, q("item")).text = item
    else:
        etree.SubElement(
            ambiguities, q("item")
        ).text = "Planner lane selected; scope assumptions were clarified for execution packet safety."
    assumptions = etree.SubElement(payload, q("assumptions"))
    etree.SubElement(
        assumptions, q("item")
    ).text = "Execution remains bound by existing packet in_scope and out_of_scope constraints."
    if prior_exploration is not None and prior_exploration.key_findings:
        etree.SubElement(assumptions, q("item")).text = (
            "Prior exploration_result findings remain relevant: "
            + "; ".join(prior_exploration.key_findings[:2])
        )
    steps = etree.SubElement(payload, q("proposed_steps"))
    etree.SubElement(
        steps, q("item")
    ).text = "Apply packet-defined implementation path after ambiguity resolution."
    if prior_exploration is not None and prior_exploration.evidence_paths:
        etree.SubElement(steps, q("item")).text = (
            "Start review or implementation from exploration-backed files: "
            + ", ".join(prior_exploration.evidence_paths[:3])
        )
    risk_map = etree.SubElement(payload, q("risk_map"))
    risk = etree.SubElement(risk_map, q("risk"))
    etree.SubElement(risk, q("target")).text = "execution_packet"
    etree.SubElement(risk, q("risk_level")).text = route.risk_level
    etree.SubElement(risk, q("rationale")).text = (
        "Planner confirms manager route assumptions before downstream lanes."
        if prior_exploration is None
        else "Planner reused prior exploration_result to reduce ambiguity before downstream lanes."
    )
    etree.SubElement(payload, q("plan_status")).text = "ready"
    if prior_exploration is not None and prior_exploration.open_questions:
        open_questions = etree.SubElement(payload, q("open_questions"))
        for item in prior_exploration.open_questions[:3]:
            etree.SubElement(open_questions, q("item")).text = item

    integrity = etree.SubElement(root, q("integrity"))
    content_hash = compute_content_hash(meta, refs, payload)
    etree.SubElement(integrity, q("content_sha256")).text = content_hash
    etree.SubElement(integrity, q("parent_sha256")).text = packet.content_sha256

    output_path = runtime_root / "sidecars" / "planner" / f"{doc_id}.pxml"
    write_xml(etree.ElementTree(root), output_path)

    intake_path = find_artifact_by_doc_id(runtime_root, intake_doc_id)
    context = [route.path, packet.path]
    if intake_path is not None:
        context.append(intake_path)
    if prior_exploration is not None:
        context.append(prior_exploration.path)
    if validate_artifacts:
        run_validator(validator_path, output_path, context)
    return output_path


def build_review_sidecar(
    runtime_root: Path,
    route: RouteInfo,
    packet: PacketInfo,
    validator_path: Path,
    decision: str,
    validate_artifacts: bool,
) -> Path:
    sequence = next_sidecar_sequence(runtime_root, route.task_id)
    doc_id = make_doc_id("review_sidecar", route.task_id, sequence)
    created_at = now_iso()

    if decision not in {"approve", "revise", "escalate"}:
        raise ValueError(f"Invalid reviewer decision: {decision}")
    blocking_count = 1 if decision == "escalate" else 0
    severity = "blocker" if decision == "escalate" else "minor"

    root = etree.Element(q("pxml"), nsmap=NSMAP)
    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = doc_id
    etree.SubElement(meta, q("doc_class")).text = "review_sidecar"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = route.task_id
    etree.SubElement(meta, q("run_id")).text = route.run_id
    etree.SubElement(meta, q("sequence")).text = str(sequence)
    etree.SubElement(meta, q("writer_agent")).text = "reviewer"
    etree.SubElement(meta, q("created_at")).text = created_at

    refs = etree.SubElement(root, q("refs"))
    packet_ref = etree.SubElement(refs, q("ref"))
    etree.SubElement(packet_ref, q("doc_id")).text = packet.doc_id
    etree.SubElement(packet_ref, q("doc_class")).text = "execution_packet"
    etree.SubElement(packet_ref, q("relation")).text = "review_target"

    payload = etree.SubElement(root, q("payload"))
    target_refs = etree.SubElement(payload, q("review_target_refs"))
    target_ref = etree.SubElement(target_refs, q("ref"))
    etree.SubElement(target_ref, q("doc_id")).text = packet.doc_id
    etree.SubElement(target_ref, q("doc_class")).text = "execution_packet"
    etree.SubElement(target_ref, q("relation")).text = "primary_target"

    findings = etree.SubElement(payload, q("findings"))
    finding = etree.SubElement(findings, q("finding"))
    etree.SubElement(finding, q("finding_id")).text = f"finding_{sequence:04d}"
    etree.SubElement(finding, q("severity")).text = severity
    if decision == "approve":
        message = (
            "Packet scope and acceptance lineage are consistent for implementation."
        )
    elif decision == "revise":
        message = "Revision requested: add stronger evidence refs before approval."
    else:
        message = (
            "Escalation requested: blocker risk remains unresolved for current scope."
        )
    etree.SubElement(finding, q("message")).text = message

    etree.SubElement(payload, q("decision")).text = decision
    etree.SubElement(payload, q("blocking_count")).text = str(blocking_count)
    etree.SubElement(
        payload, q("acceptance_lock_sha256")
    ).text = packet.acceptance_lock_hash

    integrity = etree.SubElement(root, q("integrity"))
    content_hash = compute_content_hash(meta, refs, payload)
    etree.SubElement(integrity, q("content_sha256")).text = content_hash
    etree.SubElement(integrity, q("parent_sha256")).text = packet.content_sha256

    output_path = runtime_root / "sidecars" / "reviewer" / f"{doc_id}.pxml"
    write_xml(etree.ElementTree(root), output_path)
    if validate_artifacts:
        run_validator(validator_path, output_path, [route.path, packet.path])
    return output_path


def latest_verification_result_for_task(
    runtime_root: Path, task_id: str
) -> Optional[Path]:
    return latest_artifact_for_task(
        runtime_root / "verification" / "results", "verification_result", task_id
    )


def parse_verification_verdict(path: Path) -> Optional[str]:
    tree = etree.parse(str(path))
    return text_at(tree, "/p:pxml/p:payload/p:final_verdict")


def parse_verification_lock(path: Path) -> Optional[str]:
    tree = etree.parse(str(path))
    return text_at(tree, "/p:pxml/p:payload/p:acceptance_lock_sha256")


def write_coordination_record(
    runtime_root: Path,
    task_id: str,
    route: RouteInfo,
    packet: PacketInfo,
    planner_path: Optional[Path],
    reviewer_path: Optional[Path],
    verification_path: Optional[Path],
    status: str,
    note: str,
) -> None:
    coord_dir = runtime_root / "coordination"
    ensure_dir(coord_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    file_path = coord_dir / f"{sanitize(task_id)}_{stamp}.json"
    payload = {
        "time": now_iso(),
        "task_id": task_id,
        "route_doc_id": route.doc_id,
        "packet_doc_id": packet.doc_id,
        "selected_path": route.selected_path,
        "status": status,
        "note": note,
        "planner_artifact": str(planner_path) if planner_path else None,
        "reviewer_artifact": str(reviewer_path) if reviewer_path else None,
        "verification_artifact": str(verification_path) if verification_path else None,
    }
    file_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def ensure_trace_core_events(
    runtime_root: Path,
    trace_script: Path,
    route: RouteInfo,
    packet: PacketInfo,
) -> None:
    trace_path = runtime_root / "traces" / "by_task" / f"{sanitize(route.task_id)}.pxml"
    existing = set(event_types_in_trace(trace_path))
    if "route" not in existing:
        append_trace_event(
            trace_script=trace_script,
            runtime_root=runtime_root,
            task_id=route.task_id,
            event_type="route",
            actor="manager",
            message="Route selected by manager and observed by coordinator.",
            artifact_files=[route.path],
        )
    if "packet_issued" not in existing:
        append_trace_event(
            trace_script=trace_script,
            runtime_root=runtime_root,
            task_id=route.task_id,
            event_type="packet_issued",
            actor="manager",
            message="Execution packet issued and accepted for orchestration.",
            artifact_files=[packet.path],
        )


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Conditional sidecar orchestration coordinator."
    )
    parser.add_argument("--task-id", default=None, help="Task id to orchestrate.")
    parser.add_argument(
        "--route", type=Path, default=None, help="Explicit manager_route artifact path."
    )
    parser.add_argument(
        "--packet",
        type=Path,
        default=None,
        help="Explicit execution_packet artifact path.",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=None,
        help="Runtime root directory.",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=repo_root,
        help="Workspace root to pass to focused verifier context refresh.",
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
        help="Trace appender script path.",
    )
    parser.add_argument(
        "--verification-runner",
        type=Path,
        default=repo_root / "scripts" / "verification_runner.py",
        help="Verification runner script path.",
    )
    parser.add_argument(
        "--retry-policy",
        type=Path,
        default=repo_root / "instructions" / "retry_policy.pxml",
        help="Retry policy artifact path.",
    )
    parser.add_argument(
        "--escalation-policy",
        type=Path,
        default=repo_root / "instructions" / "escalation_policy.pxml",
        help="Escalation policy artifact path.",
    )
    parser.add_argument(
        "--review-decision",
        choices=["approve", "revise", "escalate"],
        default="approve",
        help="Decision for auto-generated reviewer stub artifact.",
    )
    parser.add_argument(
        "--review-decision-after-retry",
        choices=["approve", "revise", "escalate"],
        default="approve",
        help="Decision used on reviewer retry attempts.",
    )
    parser.add_argument(
        "--dry-run-verifier",
        action="store_true",
        help="Pass --dry-run to verification_runner.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip validator checks for generated sidecar artifacts.",
    )
    return parser.parse_args()


def resolve_route_packet(
    args: argparse.Namespace, runtime_root: Path
) -> Tuple[RouteInfo, PacketInfo]:
    if args.route is not None and args.packet is not None:
        route_path = args.route.resolve()
        packet_path = args.packet.resolve()
    else:
        if args.task_id is None:
            raise ValueError("Provide --task-id or both --route and --packet")
        route_path = latest_artifact_for_task(
            runtime_root / "packets" / "manager_route", "manager_route", args.task_id
        )
        packet_path = latest_artifact_for_task(
            runtime_root / "packets" / "execution_packet",
            "execution_packet",
            args.task_id,
        )
        if route_path is None or packet_path is None:
            raise ValueError(
                "Could not find latest manager_route/execution_packet for task"
            )

    route = parse_route(route_path)
    packet = parse_packet(packet_path)
    if route.task_id != packet.task_id:
        raise ValueError("manager_route and execution_packet task_id mismatch")
    return route, packet


def ensure_runtime_scaffold(runtime_root: Path) -> None:
    dirs = [
        runtime_root / "sidecars" / "planner",
        runtime_root / "sidecars" / "reviewer",
        runtime_root / "sidecars" / "verifier",
        runtime_root / "verification" / "results",
        runtime_root / "verification" / "logs",
        runtime_root / "coordination",
        runtime_root / "index" / "retries",
        runtime_root / "index" / "escalations",
    ]
    for directory in dirs:
        ensure_dir(directory)


def main() -> int:
    args = parse_args()
    runtime_ready = bootstrap_runtime(cli_runtime_root=args.runtime_root)
    if not runtime_ready.ready:
        print(f"ERROR: {runtime_ready.failure_line()}", file=sys.stderr)
        return 2
    runtime_root = runtime_ready.runtime_root
    print(runtime_ready.success_line("orchestration_coordinator"))

    validator_path = args.validator.resolve()
    trace_script = args.trace_script.resolve()
    verification_runner = args.verification_runner.resolve()

    ensure_runtime_scaffold(runtime_root)

    if not trace_script.exists():
        print(f"ERROR: trace_appender not found: {trace_script}", file=sys.stderr)
        return 2
    if not verification_runner.exists():
        print(
            f"ERROR: verification_runner not found: {verification_runner}",
            file=sys.stderr,
        )
        return 2
    if not args.skip_validate and not validator_path.exists():
        print(f"ERROR: validator not found: {validator_path}", file=sys.stderr)
        return 2

    retry_policy = load_retry_policy(args.retry_policy.resolve())
    escalation_policy = load_escalation_policy(args.escalation_policy.resolve())

    try:
        route, packet = resolve_route_packet(args, runtime_root)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    expected_flags = LANE_MAP.get(route.selected_path)
    if expected_flags is None:
        print(
            f"ERROR: unsupported selected_path {route.selected_path}", file=sys.stderr
        )
        return 2
    actual_flags = (route.planner_lane, route.reviewer_lane, route.verifier_lane)
    if actual_flags != expected_flags:
        print("ERROR: manager_route lane flags mismatch selected_path", file=sys.stderr)
        return 2

    # Strong lineage guard: route -> packet
    if route.acceptance_lock_sha256 != packet.acceptance_lock_hash:
        try:
            append_trace_event(
                trace_script=trace_script,
                runtime_root=runtime_root,
                task_id=route.task_id,
                event_type="escalation",
                actor="manager",
                message="Acceptance lineage mismatch between manager_route and execution_packet.",
                artifact_files=[route.path, packet.path],
                reason_code="acceptance_lineage_mismatch",
                attempt=1,
                lineage_lock_sha256=route.acceptance_lock_sha256,
            )
            append_trace_event(
                trace_script=trace_script,
                runtime_root=runtime_root,
                task_id=route.task_id,
                event_type="stop",
                actor="manager",
                message="Execution stopped due to acceptance lineage mismatch.",
                artifact_files=[route.path, packet.path],
                reason_code="acceptance_lineage_mismatch",
                attempt=1,
                lineage_lock_sha256=route.acceptance_lock_sha256,
            )
        except Exception:
            pass
        print(
            "ERROR: acceptance lineage mismatch (route lock != packet lock)",
            file=sys.stderr,
        )
        return 1

    ensure_trace_core_events(runtime_root, trace_script, route, packet)

    planner_artifact: Optional[Path] = None
    reviewer_artifact: Optional[Path] = None
    verification_artifact: Optional[Path] = None

    # Planner lane
    if route.planner_lane:
        try:
            intake_doc_id = route.intake_doc_id
            if not intake_doc_id:
                raise RuntimeError("manager_route has no task_intake reference")
            planner_artifact = build_plan_sidecar(
                runtime_root=runtime_root,
                route=route,
                packet=packet,
                intake_doc_id=intake_doc_id,
                validator_path=validator_path,
                validate_artifacts=not args.skip_validate,
            )
        except Exception as exc:
            reason = "planner_artifact_generation_failed"
            count = (
                count_reason_occurrences(
                    runtime_root
                    / "traces"
                    / "by_task"
                    / f"{sanitize(route.task_id)}.pxml",
                    "escalation",
                    reason,
                )
                + 1
            )
            append_retry_index(runtime_root, route.task_id, "planner", reason, count)
            append_escalation_index(
                runtime_root, route.task_id, "planner", reason, count
            )
            append_trace_event(
                trace_script=trace_script,
                runtime_root=runtime_root,
                task_id=route.task_id,
                event_type="escalation",
                actor="manager",
                message=f"Planner sidecar generation failed: {exc}",
                artifact_files=[route.path, packet.path],
                reason_code=reason,
                attempt=count,
                lineage_lock_sha256=packet.acceptance_lock_hash,
            )
            if escalation_policy.stop_after_escalation:
                append_trace_event(
                    trace_script=trace_script,
                    runtime_root=runtime_root,
                    task_id=route.task_id,
                    event_type="stop",
                    actor="manager",
                    message="Coordinator stopped after planner escalation.",
                    artifact_files=[route.path, packet.path],
                    reason_code=reason,
                    attempt=count,
                    lineage_lock_sha256=packet.acceptance_lock_hash,
                )
            write_coordination_record(
                runtime_root,
                route.task_id,
                route,
                packet,
                planner_artifact,
                reviewer_artifact,
                verification_artifact,
                status="failed",
                note="planner lane failed",
            )
            print(f"ERROR: planner lane failed: {exc}", file=sys.stderr)
            return 1

    # Reviewer lane
    if route.reviewer_lane:
        decision = args.review_decision
        for attempt in range(1, retry_policy.reviewer_max_attempts + 1):
            try:
                reviewer_artifact = build_review_sidecar(
                    runtime_root=runtime_root,
                    route=route,
                    packet=packet,
                    validator_path=validator_path,
                    decision=decision,
                    validate_artifacts=not args.skip_validate,
                )
            except Exception as exc:
                reason = "review_sidecar_generation_failed"
                append_retry_index(
                    runtime_root, route.task_id, "reviewer", reason, attempt
                )
                if attempt >= retry_policy.reviewer_max_attempts:
                    append_escalation_index(
                        runtime_root, route.task_id, "reviewer", reason, attempt
                    )
                    append_trace_event(
                        trace_script=trace_script,
                        runtime_root=runtime_root,
                        task_id=route.task_id,
                        event_type="escalation",
                        actor="manager",
                        message=f"Reviewer sidecar generation failed: {exc}",
                        artifact_files=[route.path, packet.path],
                        reason_code=reason,
                        attempt=attempt,
                        lineage_lock_sha256=packet.acceptance_lock_hash,
                    )
                    if escalation_policy.stop_after_escalation:
                        append_trace_event(
                            trace_script=trace_script,
                            runtime_root=runtime_root,
                            task_id=route.task_id,
                            event_type="stop",
                            actor="manager",
                            message="Coordinator stopped after reviewer generation failure.",
                            artifact_files=[route.path, packet.path],
                            reason_code=reason,
                            attempt=attempt,
                            lineage_lock_sha256=packet.acceptance_lock_hash,
                        )
                    write_coordination_record(
                        runtime_root,
                        route.task_id,
                        route,
                        packet,
                        planner_artifact,
                        reviewer_artifact,
                        verification_artifact,
                        status="failed",
                        note="reviewer lane failed",
                    )
                    print(f"ERROR: reviewer lane failed: {exc}", file=sys.stderr)
                    return 1
                decision = args.review_decision_after_retry
                continue

            decision_text = text_at(
                etree.parse(str(reviewer_artifact)), "/p:pxml/p:payload/p:decision"
            )
            blocking_text = text_at(
                etree.parse(str(reviewer_artifact)),
                "/p:pxml/p:payload/p:blocking_count",
            )
            blocking_count = (
                int(blocking_text) if blocking_text and blocking_text.isdigit() else 0
            )

            if decision_text == "approve" and blocking_count == 0:
                assert reviewer_artifact is not None
                review_refs: List[Path] = [reviewer_artifact]
                if planner_artifact is not None:
                    review_refs.append(planner_artifact)
                append_trace_event(
                    trace_script=trace_script,
                    runtime_root=runtime_root,
                    task_id=route.task_id,
                    event_type="review_done",
                    actor="reviewer",
                    message="Reviewer completed artifact with approve decision.",
                    artifact_files=review_refs,
                    lineage_lock_sha256=packet.acceptance_lock_hash,
                )
                break

            reason = "reviewer_decision_requires_escalation"
            append_retry_index(runtime_root, route.task_id, "reviewer", reason, attempt)
            if (
                decision_text == "revise"
                and attempt < retry_policy.reviewer_max_attempts
            ):
                assert reviewer_artifact is not None
                append_trace_event(
                    trace_script=trace_script,
                    runtime_root=runtime_root,
                    task_id=route.task_id,
                    event_type="escalation",
                    actor="manager",
                    message="Reviewer returned revise; retrying according to retry policy.",
                    artifact_files=[reviewer_artifact],
                    reason_code="reviewer_revise",
                    attempt=attempt,
                    lineage_lock_sha256=packet.acceptance_lock_hash,
                )
                decision = args.review_decision_after_retry
                continue

            append_escalation_index(
                runtime_root, route.task_id, "reviewer", reason, attempt
            )
            assert reviewer_artifact is not None
            append_trace_event(
                trace_script=trace_script,
                runtime_root=runtime_root,
                task_id=route.task_id,
                event_type="escalation",
                actor="manager",
                message=f"Reviewer decision={decision_text} blocking_count={blocking_count} triggered escalation.",
                artifact_files=[reviewer_artifact],
                reason_code=reason,
                attempt=attempt,
                lineage_lock_sha256=packet.acceptance_lock_hash,
            )
            if escalation_policy.stop_after_escalation:
                append_trace_event(
                    trace_script=trace_script,
                    runtime_root=runtime_root,
                    task_id=route.task_id,
                    event_type="stop",
                    actor="manager",
                    message="Coordinator stopped after reviewer escalation.",
                    artifact_files=[reviewer_artifact],
                    reason_code=reason,
                    attempt=attempt,
                    lineage_lock_sha256=packet.acceptance_lock_hash,
                )
                write_coordination_record(
                    runtime_root,
                    route.task_id,
                    route,
                    packet,
                    planner_artifact,
                    reviewer_artifact,
                    verification_artifact,
                    status="failed",
                    note="reviewer escalated",
                )
                return 1

    # Verifier lane
    if route.verifier_lane:
        for attempt in range(1, retry_policy.verifier_max_attempts + 1):
            cmd = [
                sys.executable,
                str(verification_runner),
                "--packet",
                str(packet.path),
                "--runtime-root",
                str(runtime_root),
                "--workspace-root",
                str(args.workspace_root.resolve()),
                "--validator",
                str(validator_path),
                "--verify-phase",
                "lane",
            ]
            if reviewer_artifact is not None:
                cmd.extend(["--review-sidecar", str(reviewer_artifact)])
            if args.dry_run_verifier:
                cmd.append("--dry-run")

            run = subprocess.run(cmd, check=False)
            if run.returncode != 0:
                reason = "verification_runner_error"
                append_retry_index(
                    runtime_root, route.task_id, "verifier", reason, attempt
                )
                if attempt < retry_policy.verifier_max_attempts:
                    append_trace_event(
                        trace_script=trace_script,
                        runtime_root=runtime_root,
                        task_id=route.task_id,
                        event_type="escalation",
                        actor="manager",
                        message="verification_runner returned non-zero; retrying verifier lane.",
                        artifact_files=[packet.path],
                        reason_code=reason,
                        attempt=attempt,
                        lineage_lock_sha256=packet.acceptance_lock_hash,
                    )
                    continue
                append_escalation_index(
                    runtime_root, route.task_id, "verifier", reason, attempt
                )
                append_trace_event(
                    trace_script=trace_script,
                    runtime_root=runtime_root,
                    task_id=route.task_id,
                    event_type="escalation",
                    actor="manager",
                    message="verification_runner failed after retry budget.",
                    artifact_files=[packet.path],
                    reason_code=reason,
                    attempt=attempt,
                    lineage_lock_sha256=packet.acceptance_lock_hash,
                )
                if escalation_policy.stop_after_escalation:
                    append_trace_event(
                        trace_script=trace_script,
                        runtime_root=runtime_root,
                        task_id=route.task_id,
                        event_type="stop",
                        actor="manager",
                        message="Coordinator stopped after verifier runner failure.",
                        artifact_files=[packet.path],
                        reason_code=reason,
                        attempt=attempt,
                        lineage_lock_sha256=packet.acceptance_lock_hash,
                    )
                write_coordination_record(
                    runtime_root,
                    route.task_id,
                    route,
                    packet,
                    planner_artifact,
                    reviewer_artifact,
                    verification_artifact,
                    status="failed",
                    note="verifier runner failure",
                )
                return 1

            verification_path = latest_verification_result_for_task(
                runtime_root, route.task_id
            )
            if verification_path is None:
                print(
                    "ERROR: verifier lane expected verification_result artifact but none found",
                    file=sys.stderr,
                )
                return 1
            verification_artifact = verification_path

            if not args.skip_validate:
                context = [route.path, packet.path]
                if reviewer_artifact is not None:
                    context.append(reviewer_artifact)
                run_validator(validator_path, verification_artifact, context)

            result_lock = parse_verification_lock(verification_artifact)
            if result_lock != packet.acceptance_lock_hash:
                reason = "verification_lineage_mismatch"
                append_escalation_index(
                    runtime_root, route.task_id, "verifier", reason, attempt
                )
                append_trace_event(
                    trace_script=trace_script,
                    runtime_root=runtime_root,
                    task_id=route.task_id,
                    event_type="escalation",
                    actor="manager",
                    message="verification_result lineage hash mismatch against execution_packet lock.",
                    artifact_files=[verification_artifact, packet.path],
                    reason_code=reason,
                    attempt=attempt,
                    lineage_lock_sha256=packet.acceptance_lock_hash,
                )
                if escalation_policy.stop_after_escalation:
                    append_trace_event(
                        trace_script=trace_script,
                        runtime_root=runtime_root,
                        task_id=route.task_id,
                        event_type="stop",
                        actor="manager",
                        message="Coordinator stopped after verification lineage mismatch.",
                        artifact_files=[verification_artifact, packet.path],
                        reason_code=reason,
                        attempt=attempt,
                        lineage_lock_sha256=packet.acceptance_lock_hash,
                    )
                write_coordination_record(
                    runtime_root,
                    route.task_id,
                    route,
                    packet,
                    planner_artifact,
                    reviewer_artifact,
                    verification_artifact,
                    status="failed",
                    note="verification lineage mismatch",
                )
                return 1

            verdict = parse_verification_verdict(verification_artifact)
            append_trace_event(
                trace_script=trace_script,
                runtime_root=runtime_root,
                task_id=route.task_id,
                event_type="verify_done",
                actor="verifier",
                message=f"Verifier produced verdict {verdict or 'unknown'}.",
                artifact_files=[verification_artifact],
                lineage_lock_sha256=packet.acceptance_lock_hash,
                verify_phase="lane",
            )

            if verdict == "pass":
                break

            if verdict == "inconclusive":
                break

            reason = "verifier_fail"
            append_retry_index(runtime_root, route.task_id, "verifier", reason, attempt)
            append_escalation_index(
                runtime_root, route.task_id, "verifier", reason, attempt
            )
            append_trace_event(
                trace_script=trace_script,
                runtime_root=runtime_root,
                task_id=route.task_id,
                event_type="escalation",
                actor="manager",
                message=f"Verifier verdict {verdict} triggered escalation.",
                artifact_files=[verification_artifact],
                reason_code=reason,
                attempt=attempt,
                lineage_lock_sha256=packet.acceptance_lock_hash,
            )

            if escalation_policy.stop_after_escalation:
                append_trace_event(
                    trace_script=trace_script,
                    runtime_root=runtime_root,
                    task_id=route.task_id,
                    event_type="stop",
                    actor="manager",
                    message="Coordinator stopped after verifier escalation.",
                    artifact_files=[verification_artifact],
                    reason_code=reason,
                    attempt=attempt,
                    lineage_lock_sha256=packet.acceptance_lock_hash,
                )
            write_coordination_record(
                runtime_root,
                route.task_id,
                route,
                packet,
                planner_artifact,
                reviewer_artifact,
                verification_artifact,
                status="failed",
                note=f"verifier verdict={verdict}",
            )
            return 1

    write_coordination_record(
        runtime_root,
        route.task_id,
        route,
        packet,
        planner_artifact,
        reviewer_artifact,
        verification_artifact,
        status="completed",
        note="coordinator completed conditional lane orchestration",
    )

    print(f"Coordinator completed task {route.task_id}")
    print(f"selected_path={route.selected_path}")
    print(f"planner_artifact={planner_artifact}")
    print(f"reviewer_artifact={reviewer_artifact}")
    print(f"verification_artifact={verification_artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
