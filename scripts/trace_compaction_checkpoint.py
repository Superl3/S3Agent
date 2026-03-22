#!/usr/bin/env python3
"""Create compaction checkpoint artifacts from latest task runtime state."""

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


def next_sequence(runtime_root: Path, task_id: str) -> int:
    maximum = 0
    for path in discover_pxml_files(runtime_root):
        parsed = parse_artifact(path)
        if parsed is None or parsed.task_id != task_id:
            continue
        maximum = max(maximum, parsed.sequence)
    return maximum + 1


def build_ref(
    parent: etree._Element, info: ArtifactRefInfo, relation: str
) -> etree._Element:
    ref = etree.SubElement(parent, q("ref"))
    etree.SubElement(ref, q("doc_id")).text = info.doc_id
    etree.SubElement(ref, q("doc_class")).text = info.doc_class
    etree.SubElement(ref, q("relation")).text = relation
    return ref


def build_ref_node(
    parent: etree._Element, tag: str, info: ArtifactRefInfo, relation: str
) -> None:
    node = etree.SubElement(parent, q(tag))
    etree.SubElement(node, q("doc_id")).text = info.doc_id
    etree.SubElement(node, q("doc_class")).text = info.doc_class
    etree.SubElement(node, q("relation")).text = relation


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


def load_compaction_defaults(path: Path) -> Tuple[str, int]:
    default_reason = "operator_checkpoint_for_long_trace_reconstruction"
    max_events = 50
    if not path.exists():
        return default_reason, max_events
    try:
        tree = etree.parse(str(path))
    except etree.XMLSyntaxError:
        return default_reason, max_events
    reason = text_at(tree, "/p:pxml/p:payload/p:checkpoint_reason_default")
    max_text = text_at(tree, "/p:pxml/p:payload/p:max_events_before_checkpoint")
    if reason:
        default_reason = reason
    if max_text and max_text.isdigit():
        max_events = int(max_text)
    return default_reason, max_events


def run_validation(
    validator: Path, checkpoint_path: Path, context_files: Sequence[Path]
) -> None:
    with tempfile.TemporaryDirectory(prefix="pxml_compaction_validate_") as temp_dir:
        temp_root = Path(temp_dir)
        copied_checkpoint = temp_root / checkpoint_path.name
        shutil.copy2(checkpoint_path, copied_checkpoint)
        for file_path in context_files:
            if file_path.exists():
                shutil.copy2(file_path, temp_root / file_path.name)
        command = [
            sys.executable,
            str(validator),
            str(copied_checkpoint),
            "--context-dir",
            str(temp_root),
        ]
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Validation failed for {checkpoint_path}")


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Create compaction checkpoint artifact for one task."
    )
    parser.add_argument("--task-id", required=True, help="Target task id.")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=repo_root / "runtime",
        help="Runtime root directory.",
    )
    parser.add_argument(
        "--compaction-policy",
        type=Path,
        default=repo_root / "instructions" / "compaction_policy.pxml",
        help="Compaction policy artifact path.",
    )
    parser.add_argument(
        "--validator",
        type=Path,
        default=repo_root / "scripts" / "pxml_validator.py",
        help="PXML validator path.",
    )
    parser.add_argument(
        "--checkpoint-reason",
        default=None,
        help="Optional explicit checkpoint reason override.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip post-generation validation.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print checkpoint plan without writing files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime_root = args.runtime_root.resolve()
    validator = args.validator.resolve()

    if not runtime_root.exists():
        print(f"ERROR: runtime root not found: {runtime_root}", file=sys.stderr)
        return 2

    trace_info = latest_for_task(
        runtime_root / "traces" / "by_task", "execution_trace", args.task_id
    )
    status_info = latest_for_task(
        runtime_root / "status" / "reports", "task_status_report", args.task_id
    )
    route_info = latest_for_task(
        runtime_root / "packets" / "manager_route", "manager_route", args.task_id
    )
    packet_info = latest_for_task(
        runtime_root / "packets" / "execution_packet", "execution_packet", args.task_id
    )
    verify_info = latest_for_task(
        runtime_root / "verification" / "results", "verification_result", args.task_id
    )
    intake_info = latest_for_task(
        runtime_root / "inbox" / "task_intake", "task_intake", args.task_id
    )

    if (
        trace_info is None
        or status_info is None
        or route_info is None
        or packet_info is None
    ):
        print(
            "ERROR: required latest artifacts missing (trace/status/route/packet)",
            file=sys.stderr,
        )
        return 2

    default_reason, max_events = load_compaction_defaults(
        args.compaction_policy.resolve()
    )
    checkpoint_reason = args.checkpoint_reason or default_reason

    trace_tree = etree.parse(str(trace_info.path))
    status_tree = etree.parse(str(status_info.path))
    route_tree = etree.parse(str(route_info.path))
    packet_tree = etree.parse(str(packet_info.path))

    trace_seq = text_at(trace_tree, "/p:pxml/p:meta/p:sequence")
    trace_last_seq = int(trace_seq) if trace_seq and trace_seq.isdigit() else 1
    from_seq = max(1, trace_last_seq - max_events + 1)
    to_seq = trace_last_seq

    requested_outcome = None
    if intake_info is not None:
        intake_tree = etree.parse(str(intake_info.path))
        requested_outcome = text_at(
            intake_tree, "/p:pxml/p:payload/p:requested_outcome"
        )
    final_goal = (
        requested_outcome
        or "Complete task to acceptable state with immutable lineage and deterministic evidence."
    )

    patch_mode = text_at(
        packet_tree, "/p:pxml/p:payload/p:patch_constraints/p:patch_mode"
    )
    max_files = text_at(
        packet_tree, "/p:pxml/p:payload/p:patch_constraints/p:max_files"
    )
    rewrite_approved = text_at(
        packet_tree,
        "/p:pxml/p:payload/p:patch_constraints/p:rewrite_exception_approved",
    )
    hard_constraints: List[str] = [
        "single_writer_implementer_runner",
        "acceptance_lineage_immutable",
        "execution_trace_append_only",
    ]
    if patch_mode:
        hard_constraints.append(f"patch_mode={patch_mode}")
    if max_files:
        hard_constraints.append(f"max_files={max_files}")
    if rewrite_approved:
        hard_constraints.append(f"rewrite_exception_approved={rewrite_approved}")

    selected_path = text_at(route_tree, "/p:pxml/p:payload/p:selected_path")
    status_value = text_at(status_tree, "/p:pxml/p:payload/p:current_status")
    phase_value = text_at(status_tree, "/p:pxml/p:payload/p:current_phase")
    verdict_candidate = text_at(
        status_tree, "/p:pxml/p:payload/p:final_verdict_candidate"
    )
    retry_count = text_at(status_tree, "/p:pxml/p:payload/p:retry_count")

    established_facts = [
        f"selected_path={selected_path or 'unknown'}",
        f"current_phase={phase_value or 'unknown'}",
        f"current_status={status_value or 'unknown'}",
        f"trace_last_sequence={trace_last_seq}",
        f"status_verdict_candidate={verdict_candidate or 'unknown'}",
    ]
    if retry_count:
        established_facts.append(f"retry_count={retry_count}")

    failed_attempts: List[str] = []
    reason_items = status_tree.xpath(
        "/p:pxml/p:payload/p:failure_reason_codes/p:item/text()",
        namespaces=XPATH_NS,
    )
    for value in reason_items:
        normalized = value.strip()
        if normalized and normalized != "none":
            failed_attempts.append(f"status_reason={normalized}")
    for entry in load_failure_entries(runtime_root, args.task_id):
        reason = str(entry.get("reason_code") or "").strip()
        if not reason:
            continue
        attempt = entry.get("retry_count")
        if isinstance(attempt, int):
            failed_attempts.append(f"failure_index_attempt={attempt}:{reason}")
        else:
            failed_attempts.append(f"failure_index:{reason}")
    if not failed_attempts:
        failed_attempts = ["none"]

    next_action = text_at(status_tree, "/p:pxml/p:payload/p:next_recommended_action")
    if not next_action:
        next_action = (
            "Review latest status report and continue task execution per policy."
        )

    lineage_lock = text_at(packet_tree, "/p:pxml/p:payload/p:acceptance_lock_hash")
    if not lineage_lock:
        print("ERROR: packet acceptance lock hash is missing", file=sys.stderr)
        return 2

    done_condition = "task_status_report current_status in {passed,no_op} and failure_reason_codes contains only none"

    sequence = next_sequence(runtime_root, args.task_id)
    token = sanitize(args.task_id)[:20]
    doc_id = f"doc_compact_ckpt_{token}_{sequence:04d}"
    if not re.fullmatch(r"doc_[a-z0-9][a-z0-9._-]{5,63}", doc_id):
        doc_id = f"doc_compact_ckpt_{sequence:04d}_{sha256_hex(args.task_id.encode('utf-8'))[:8]}"
    checkpoint_id = f"ckpt_{token}_{sequence:04d}"

    root = etree.Element(q("pxml"), nsmap=NSMAP)
    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = doc_id
    etree.SubElement(meta, q("doc_class")).text = "compaction_checkpoint"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = args.task_id
    etree.SubElement(
        meta, q("run_id")
    ).text = f"run_compaction_{sanitize(args.task_id)}"
    etree.SubElement(meta, q("sequence")).text = str(sequence)
    etree.SubElement(meta, q("writer_agent")).text = "system"
    etree.SubElement(meta, q("created_at")).text = now_iso()

    refs = etree.SubElement(root, q("refs"))
    build_ref(refs, trace_info, "source_trace")
    build_ref(refs, status_info, "source_status_report")
    build_ref(refs, route_info, "source_route")
    build_ref(refs, packet_info, "source_packet")
    if verify_info is not None:
        build_ref(refs, verify_info, "source_verification")

    payload = etree.SubElement(root, q("payload"))
    etree.SubElement(payload, q("checkpoint_id")).text = checkpoint_id
    etree.SubElement(payload, q("task_id")).text = args.task_id
    build_ref_node(payload, "source_trace_ref", trace_info, "source_trace")
    etree.SubElement(payload, q("source_trace_last_sequence")).text = str(
        trace_last_seq
    )
    etree.SubElement(payload, q("final_goal")).text = final_goal

    hard_constraints_node = etree.SubElement(payload, q("hard_constraints"))
    for item in hard_constraints:
        etree.SubElement(hard_constraints_node, q("item")).text = item

    etree.SubElement(payload, q("done_condition")).text = done_condition

    established_node = etree.SubElement(payload, q("established_facts"))
    for item in established_facts:
        etree.SubElement(established_node, q("item")).text = item

    failed_node = etree.SubElement(payload, q("failed_attempts_and_causes"))
    for item in failed_attempts:
        etree.SubElement(failed_node, q("item")).text = item

    etree.SubElement(payload, q("next_recommended_action")).text = next_action
    etree.SubElement(payload, q("lineage_lock_sha256")).text = lineage_lock

    range_node = etree.SubElement(payload, q("included_event_range"))
    etree.SubElement(range_node, q("from_event_seq")).text = str(from_seq)
    etree.SubElement(range_node, q("to_event_seq")).text = str(to_seq)

    etree.SubElement(payload, q("checkpoint_reason")).text = checkpoint_reason
    build_ref_node(
        payload,
        "created_from_status_report_ref",
        status_info,
        "source_status_report",
    )
    build_ref_node(
        payload,
        "created_from_latest_packet_ref",
        packet_info,
        "source_packet",
    )
    build_ref_node(
        payload,
        "created_from_latest_route_ref",
        route_info,
        "source_route",
    )
    if verify_info is not None:
        build_ref_node(
            payload,
            "created_from_latest_verification_ref",
            verify_info,
            "source_verification",
        )

    integrity = etree.SubElement(root, q("integrity"))
    content_sha = compute_content_hash(meta, refs, payload)
    etree.SubElement(integrity, q("content_sha256")).text = content_sha
    status_sha = text_at(status_tree, "/p:pxml/p:integrity/p:content_sha256")
    if status_sha:
        etree.SubElement(integrity, q("parent_sha256")).text = status_sha

    output_dir = runtime_root / "compaction" / "checkpoints"
    output_path = output_dir / f"{doc_id}.pxml"

    if args.dry_run:
        print(f"DRY-RUN checkpoint path: {output_path}")
        print(f"checkpoint_id={checkpoint_id}")
        print(f"included_event_range={from_seq}-{to_seq}")
        print(f"checkpoint_reason={checkpoint_reason}")
        return 0

    ensure_dir(output_dir)
    tree = etree.ElementTree(root)
    tree.write(
        str(output_path), encoding="UTF-8", xml_declaration=True, pretty_print=True
    )

    latest_path = (
        runtime_root / "latest" / f"{sanitize(args.task_id)}_compaction_checkpoint.pxml"
    )
    ensure_dir(latest_path.parent)
    shutil.copy2(output_path, latest_path)

    tasks_dir = runtime_root / "index" / "tasks"
    artifacts_dir = runtime_root / "index" / "artifacts"
    ensure_dir(tasks_dir)
    ensure_dir(artifacts_dir)

    task_index_path = tasks_dir / f"{sanitize(args.task_id)}.json"
    task_index: Dict[str, object] = {}
    if task_index_path.exists():
        try:
            task_index = json.loads(task_index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            task_index = {}
    task_index["task_id"] = args.task_id
    task_index["latest_compaction_checkpoint"] = str(
        output_path.relative_to(runtime_root)
    )
    task_index["updated_at"] = now_iso()
    task_index_path.write_text(
        json.dumps(task_index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    artifact_index = {
        "doc_id": doc_id,
        "doc_class": "compaction_checkpoint",
        "task_id": args.task_id,
        "path": str(output_path.relative_to(runtime_root)),
        "updated_at": now_iso(),
    }
    (artifacts_dir / f"{doc_id}.json").write_text(
        json.dumps(artifact_index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    if not args.skip_validate:
        if not validator.exists():
            print(f"ERROR: validator not found: {validator}", file=sys.stderr)
            return 2
        try:
            context_files: List[Path] = [
                trace_info.path,
                status_info.path,
                route_info.path,
                packet_info.path,
            ]
            if verify_info is not None:
                context_files.append(verify_info.path)
            run_validation(validator, output_path, context_files)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    print(f"Generated compaction_checkpoint: {output_path}")
    print(f"checkpoint_id={checkpoint_id}")
    print(f"included_event_range={from_seq}-{to_seq}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
