#!/usr/bin/env python3
"""Batch 2 trace appender.

Append-only utility for execution_trace artifacts.
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
from typing import List, Optional, Sequence

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

EVENT_TYPES = {
    "route",
    "packet_issued",
    "implement_start",
    "patch_applied",
    "blocked",
    "retry_failed",
    "review_done",
    "verify_done",
    "escalation",
    "stop",
    "reject",
}


def q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_iso(value: Optional[str]) -> str:
    if not value:
        return now_iso()
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return now_iso()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (
        parsed.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sanitize(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9._-]", "_", lowered)
    lowered = re.sub(r"_+", "_", lowered).strip("_")
    return lowered or "id"


def make_doc_id(task_id: str) -> str:
    doc_id = f"doc_execution_trace_{sanitize(task_id)}"
    if len(doc_id) > 64:
        doc_id = doc_id[:64]
    if re.fullmatch(r"doc_[a-z0-9][a-z0-9._-]{5,63}", doc_id):
        return doc_id
    return "doc_execution_trace_default_0001"


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


@dataclass
class ArtifactRef:
    doc_id: str
    doc_class: str
    relation: Optional[str] = None


def parse_ref_token(token: str) -> ArtifactRef:
    parts = token.split(":")
    if len(parts) < 2:
        raise ValueError(
            f"Invalid --artifact format: {token!r}; expected doc_id:doc_class[:relation]"
        )
    doc_id = parts[0].strip()
    doc_class = parts[1].strip()
    relation = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
    if not doc_id or not doc_class:
        raise ValueError(f"Invalid --artifact token: {token!r}")
    return ArtifactRef(doc_id=doc_id, doc_class=doc_class, relation=relation)


def parse_ref_from_file(path: Path) -> ArtifactRef:
    tree = etree.parse(str(path))
    doc_id = text_at(tree, "/p:pxml/p:meta/p:doc_id")
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    if not doc_id or not doc_class:
        raise ValueError(f"Artifact file missing doc_id/doc_class: {path}")
    return ArtifactRef(doc_id=doc_id, doc_class=doc_class, relation="evidence")


def ensure_runtime_scaffold(runtime_root: Path) -> None:
    required = [
        runtime_root / "traces" / "by_task",
        runtime_root / "latest",
        runtime_root / "index" / "tasks",
        runtime_root / "index" / "artifacts",
    ]
    for directory in required:
        directory.mkdir(parents=True, exist_ok=True)


def ensure_refs(
    parent: etree._Element, refs: Sequence[ArtifactRef]
) -> Optional[etree._Element]:
    if not refs:
        return None
    refs_node = etree.SubElement(parent, q("artifact_refs"))
    for ref in refs:
        ref_node = etree.SubElement(refs_node, q("ref"))
        etree.SubElement(ref_node, q("doc_id")).text = ref.doc_id
        etree.SubElement(ref_node, q("doc_class")).text = ref.doc_class
        if ref.relation:
            etree.SubElement(ref_node, q("relation")).text = ref.relation
    return refs_node


def event_hash(
    event_seq: int,
    event_type: str,
    event_time: str,
    actor: str,
    message: str,
    reason_code: Optional[str],
    attempt: Optional[int],
    lineage_lock_sha256: Optional[str],
    verify_phase: Optional[str],
    refs: Sequence[ArtifactRef],
    prev_event_sha256: Optional[str],
) -> str:
    payload = {
        "event_seq": event_seq,
        "event_time": event_time,
        "event_type": event_type,
        "actor": actor,
        "message": message,
        "refs": [
            {
                "doc_id": ref.doc_id,
                "doc_class": ref.doc_class,
                "relation": ref.relation or "",
            }
            for ref in refs
        ],
        "prev_event_sha256": prev_event_sha256 or "",
    }
    if reason_code:
        payload["reason_code"] = reason_code
    if attempt is not None:
        payload["attempt"] = str(attempt)
    if lineage_lock_sha256:
        payload["lineage_lock_sha256"] = lineage_lock_sha256
    if verify_phase:
        payload["verify_phase"] = verify_phase
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_hex(encoded)


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


def write_xml(tree: etree._ElementTree, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True)


def build_new_trace(task_id: str, run_id: str, created_at: str) -> etree._ElementTree:
    root = etree.Element(q("pxml"), nsmap=NSMAP)
    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = make_doc_id(task_id)
    etree.SubElement(meta, q("doc_class")).text = "execution_trace"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = task_id
    etree.SubElement(meta, q("run_id")).text = run_id
    etree.SubElement(meta, q("sequence")).text = "0"
    etree.SubElement(meta, q("writer_agent")).text = "system"
    etree.SubElement(meta, q("created_at")).text = created_at

    etree.SubElement(root, q("refs"))
    payload = etree.SubElement(root, q("payload"))
    etree.SubElement(payload, q("events"))

    integrity = etree.SubElement(root, q("integrity"))
    content_hash = compute_content_hash(meta, root.find(q("refs")), payload)
    etree.SubElement(integrity, q("content_sha256")).text = content_hash
    return etree.ElementTree(root)


def append_event(
    tree: etree._ElementTree,
    event_type: str,
    actor: str,
    message: str,
    event_time: str,
    reason_code: Optional[str],
    attempt: Optional[int],
    lineage_lock_sha256: Optional[str],
    verify_phase: Optional[str],
    refs: Sequence[ArtifactRef],
) -> None:
    root = tree.getroot()
    events_parent = root.find(f"./{q('payload')}/{q('events')}")
    if events_parent is None:
        raise ValueError("Invalid execution_trace: missing payload/events")

    existing_events = events_parent.findall(q("event"))
    next_seq = len(existing_events) + 1
    previous_hash: Optional[str] = None
    if existing_events:
        previous_hash = text_at(
            etree.ElementTree(existing_events[-1]), "./p:event_sha256"
        )

    event_sha = event_hash(
        event_seq=next_seq,
        event_type=event_type,
        event_time=event_time,
        actor=actor,
        message=message,
        reason_code=reason_code,
        attempt=attempt,
        lineage_lock_sha256=lineage_lock_sha256,
        verify_phase=verify_phase,
        refs=refs,
        prev_event_sha256=previous_hash,
    )

    event_node = etree.SubElement(events_parent, q("event"))
    etree.SubElement(event_node, q("event_seq")).text = str(next_seq)
    etree.SubElement(event_node, q("event_type")).text = event_type
    etree.SubElement(event_node, q("event_time")).text = event_time
    etree.SubElement(event_node, q("actor")).text = actor
    etree.SubElement(event_node, q("message")).text = message
    if reason_code is not None:
        etree.SubElement(event_node, q("reason_code")).text = reason_code
    if attempt is not None:
        etree.SubElement(event_node, q("attempt")).text = str(attempt)
    if lineage_lock_sha256 is not None:
        etree.SubElement(
            event_node, q("lineage_lock_sha256")
        ).text = lineage_lock_sha256
    if verify_phase is not None:
        etree.SubElement(event_node, q("verify_phase")).text = verify_phase
    ensure_refs(event_node, refs)
    etree.SubElement(event_node, q("event_sha256")).text = event_sha
    if previous_hash:
        etree.SubElement(event_node, q("prev_event_sha256")).text = previous_hash

    meta = root.find(q("meta"))
    if meta is None:
        raise ValueError("Invalid execution_trace: missing meta")
    sequence_node = meta.find(q("sequence"))
    if sequence_node is None:
        raise ValueError("Invalid execution_trace: missing meta/sequence")
    sequence_node.text = str(next_seq)

    refs_node = root.find(q("refs"))
    payload_node = root.find(q("payload"))
    integrity = root.find(q("integrity"))
    if payload_node is None or integrity is None:
        raise ValueError("Invalid execution_trace: missing payload/integrity")

    old_content = text_at(tree, "/p:pxml/p:integrity/p:content_sha256")
    content_sha = compute_content_hash(meta, refs_node, payload_node)

    content_node = integrity.find(q("content_sha256"))
    if content_node is None:
        content_node = etree.SubElement(integrity, q("content_sha256"))
    content_node.text = content_sha

    if old_content:
        parent_node = integrity.find(q("parent_sha256"))
        if parent_node is None:
            parent_node = etree.SubElement(integrity, q("parent_sha256"))
        parent_node.text = old_content


def update_indexes(
    runtime_root: Path, task_id: str, trace_path: Path, trace_doc_id: str
) -> None:
    tasks_dir = runtime_root / "index" / "tasks"
    artifacts_dir = runtime_root / "index" / "artifacts"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    task_index_path = tasks_dir / f"{sanitize(task_id)}.json"
    current: dict = {}
    if task_index_path.exists():
        try:
            current = json.loads(task_index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            current = {}
    current["task_id"] = task_id
    current["latest_execution_trace"] = str(trace_path.relative_to(runtime_root))
    current["updated_at"] = now_iso()
    task_index_path.write_text(
        json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    artifact_index = {
        "doc_id": trace_doc_id,
        "doc_class": "execution_trace",
        "task_id": task_id,
        "path": str(trace_path.relative_to(runtime_root)),
        "updated_at": now_iso(),
    }
    (artifacts_dir / f"{trace_doc_id}.json").write_text(
        json.dumps(artifact_index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_validation(validator: Path, trace_path: Path, context_dir: Path) -> None:
    command = [
        sys.executable,
        str(validator),
        str(trace_path),
        "--context-dir",
        str(context_dir),
    ]
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Validation failed for trace artifact: {trace_path}")


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Append an event to execution_trace.")
    parser.add_argument("--task-id", required=True, help="Task id for trace key.")
    parser.add_argument(
        "--event-type",
        required=True,
        choices=sorted(EVENT_TYPES),
        help="Trace event type.",
    )
    parser.add_argument(
        "--actor",
        default="manager",
        choices=["manager", "implementer", "planner", "reviewer", "verifier", "system"],
        help="Event actor.",
    )
    parser.add_argument(
        "--message", required=True, help="Human-readable event message."
    )
    parser.add_argument(
        "--run-id", default=None, help="Run id when creating a new trace."
    )
    parser.add_argument(
        "--event-time", default=None, help="Event timestamp in ISO-8601."
    )
    parser.add_argument(
        "--reason-code",
        default=None,
        help="Optional structured reason code for retry/escalation lineage.",
    )
    parser.add_argument(
        "--attempt",
        type=int,
        default=None,
        help="Optional positive attempt number for retry/escalation lineage.",
    )
    parser.add_argument(
        "--lineage-lock-sha256",
        default=None,
        help="Optional acceptance lineage lock hash for review_done/verify_done.",
    )
    parser.add_argument(
        "--verify-phase",
        choices=["lane", "post_implement", "unknown_legacy"],
        default=None,
        help="Optional verify phase metadata for verify_done events.",
    )
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        help="Artifact ref token doc_id:doc_class[:relation].",
    )
    parser.add_argument(
        "--artifact-file",
        action="append",
        default=[],
        help="PXML artifact path to auto-reference.",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=None,
        help="Runtime root directory.",
    )
    parser.add_argument(
        "--validator",
        type=Path,
        default=repo_root / "scripts" / "pxml_validator.py",
        help="Validator script path.",
    )
    parser.add_argument(
        "--skip-validate", action="store_true", help="Skip validator execution."
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime_ready = bootstrap_runtime(cli_runtime_root=args.runtime_root)
    if not runtime_ready.ready:
        print(f"ERROR: {runtime_ready.failure_line()}", file=sys.stderr)
        return 2
    runtime_root = runtime_ready.runtime_root
    print(runtime_ready.success_line("trace_appender"))

    validator = args.validator.resolve()
    ensure_runtime_scaffold(runtime_root)

    event_time = parse_iso(args.event_time)
    run_id = args.run_id or f"run_trace_{sanitize(args.task_id)}"

    refs: List[ArtifactRef] = []
    if args.attempt is not None and args.attempt < 1:
        print("ERROR: --attempt must be >= 1", file=sys.stderr)
        return 2
    try:
        for token in args.artifact:
            refs.append(parse_ref_token(token))
        for artifact_file in args.artifact_file:
            refs.append(parse_ref_from_file(Path(artifact_file).resolve()))
    except Exception as exc:
        print(f"ERROR: failed to parse artifact refs: {exc}", file=sys.stderr)
        return 2

    trace_path = runtime_root / "traces" / "by_task" / f"{sanitize(args.task_id)}.pxml"
    if trace_path.exists():
        try:
            tree = etree.parse(str(trace_path))
        except etree.XMLSyntaxError as exc:
            print(f"ERROR: existing trace is invalid XML: {exc}", file=sys.stderr)
            return 2
        doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
        if doc_class != "execution_trace":
            print(
                f"ERROR: existing trace file doc_class mismatch ({doc_class!r})",
                file=sys.stderr,
            )
            return 2
    else:
        tree = build_new_trace(
            task_id=args.task_id, run_id=run_id, created_at=event_time
        )

    try:
        append_event(
            tree=tree,
            event_type=args.event_type,
            actor=args.actor,
            message=args.message,
            event_time=event_time,
            reason_code=args.reason_code,
            attempt=args.attempt,
            lineage_lock_sha256=args.lineage_lock_sha256,
            verify_phase=args.verify_phase,
            refs=refs,
        )
    except Exception as exc:
        print(f"ERROR: failed to append event: {exc}", file=sys.stderr)
        return 2

    write_xml(tree, trace_path)
    latest_path = (
        runtime_root / "latest" / f"{sanitize(args.task_id)}_execution_trace.pxml"
    )
    shutil.copy2(trace_path, latest_path)

    trace_doc_id = text_at(tree, "/p:pxml/p:meta/p:doc_id") or make_doc_id(args.task_id)
    update_indexes(
        runtime_root=runtime_root,
        task_id=args.task_id,
        trace_path=trace_path,
        trace_doc_id=trace_doc_id,
    )

    if not args.skip_validate:
        if not validator.exists():
            print(f"ERROR: validator script not found: {validator}", file=sys.stderr)
            return 2
        try:
            with tempfile.TemporaryDirectory(prefix="pxml_trace_validate_") as temp_dir:
                temp_root = Path(temp_dir)
                trace_for_validation = temp_root / trace_path.name
                shutil.copy2(trace_path, trace_for_validation)
                run_validation(validator, trace_for_validation, temp_root)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    print(f"Appended event '{args.event_type}' to {trace_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
