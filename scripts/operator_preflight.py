#!/usr/bin/env python3
"""Generate operator preflight report before renderer handoff."""

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
from typing import Dict, Iterable, List, Optional, Sequence

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


def build_ref(parent: etree._Element, info: ArtifactRefInfo, relation: str) -> None:
    ref = etree.SubElement(parent, q("ref"))
    etree.SubElement(ref, q("doc_id")).text = info.doc_id
    etree.SubElement(ref, q("doc_class")).text = info.doc_class
    etree.SubElement(ref, q("relation")).text = relation


def build_ref_node(
    parent: etree._Element, tag: str, info: ArtifactRefInfo, relation: str
) -> None:
    node = etree.SubElement(parent, q(tag))
    etree.SubElement(node, q("doc_id")).text = info.doc_id
    etree.SubElement(node, q("doc_class")).text = info.doc_class
    etree.SubElement(node, q("relation")).text = relation


def run_validation(
    validator: Path, report_path: Path, context_files: Sequence[Path]
) -> None:
    with tempfile.TemporaryDirectory(prefix="pxml_preflight_validate_") as temp_dir:
        temp_root = Path(temp_dir)
        copied_report = temp_root / report_path.name
        shutil.copy2(report_path, copied_report)
        for file_path in context_files:
            if file_path.exists():
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
            entry_task = entry.get("task_id")
            if entry_task != task_id:
                continue
            reasons = entry.get("reasons")
            if isinstance(reasons, list):
                for item in reasons:
                    if isinstance(item, str) and item.strip():
                        flags.append(item.strip())
            else:
                flags.append("quarantine_entry")
    return sorted(set(flags))


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Generate operator preflight report for one task."
    )
    parser.add_argument("--task-id", required=True, help="Target task id.")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=repo_root / "runtime",
        help="Runtime root directory.",
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
        help="Skip post-generation validation.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate readiness without writing artifact.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime_root = args.runtime_root.resolve()
    validator = args.validator.resolve()

    if not runtime_root.exists():
        print(f"ERROR: runtime root not found: {runtime_root}", file=sys.stderr)
        return 2

    route = latest_for_task(
        runtime_root / "packets" / "manager_route", "manager_route", args.task_id
    )
    packet = latest_for_task(
        runtime_root / "packets" / "execution_packet", "execution_packet", args.task_id
    )
    status = latest_for_task(
        runtime_root / "status" / "reports", "task_status_report", args.task_id
    )
    trace = latest_for_task(
        runtime_root / "traces" / "by_task", "execution_trace", args.task_id
    )
    verification = latest_for_task(
        runtime_root / "verification" / "results", "verification_result", args.task_id
    )

    missing = [
        name
        for name, info in [
            ("manager_route", route),
            ("execution_packet", packet),
            ("task_status_report", status),
            ("execution_trace", trace),
        ]
        if info is None
    ]
    if missing:
        print(
            "ERROR: missing required runtime artifacts for preflight: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 2

    assert route is not None
    assert packet is not None
    assert status is not None
    assert trace is not None

    route_tree = etree.parse(str(route.path))
    packet_tree = etree.parse(str(packet.path))
    status_tree = etree.parse(str(status.path))
    verification_tree = (
        etree.parse(str(verification.path)) if verification is not None else None
    )

    route_lock = text_at(
        route_tree, "/p:pxml/p:payload/p:acceptance_lock/p:lock_sha256"
    )
    packet_lock = text_at(packet_tree, "/p:pxml/p:payload/p:acceptance_lock_hash")
    status_lock = text_at(status_tree, "/p:pxml/p:payload/p:acceptance_lock_sha256")
    verification_lock = (
        text_at(verification_tree, "/p:pxml/p:payload/p:acceptance_lock_sha256")
        if verification_tree is not None
        else None
    )

    lineage_ok = bool(route_lock and packet_lock and route_lock == packet_lock)
    if lineage_ok and status_lock is not None and status_lock != packet_lock:
        lineage_ok = False
    if (
        lineage_ok
        and verification_lock is not None
        and verification_lock != packet_lock
    ):
        lineage_ok = False

    status_value = (
        text_at(status_tree, "/p:pxml/p:payload/p:current_status") or "unknown"
    )
    status_ok = status_value in {"passed", "no_op"}

    unresolved_failures: List[str] = []
    for value in status_tree.xpath(
        "/p:pxml/p:payload/p:failure_reason_codes/p:item/text()",
        namespaces=XPATH_NS,
    ):
        normalized = value.strip()
        if normalized and normalized != "none":
            unresolved_failures.append(normalized)
    unresolved_failures = sorted(set(unresolved_failures))
    unresolved_for_payload = unresolved_failures or ["none"]

    quarantine_flags = load_quarantine_flags(runtime_root, args.task_id)
    quarantine_for_payload = quarantine_flags or ["none"]

    render_readiness = "caution"
    if not lineage_ok:
        render_readiness = "not_ready"
    elif status_value in {"blocked", "retry_failed", "escalated", "failed"}:
        render_readiness = "not_ready"
    elif quarantine_flags:
        render_readiness = "caution"
    elif status_value == "passed" and not unresolved_failures:
        render_readiness = "ready"
    elif status_value == "no_op" and not unresolved_failures:
        render_readiness = "caution"
    else:
        render_readiness = "caution"

    if render_readiness == "ready":
        next_action = (
            "Preflight is ready; renderer handoff may proceed when operator approves."
        )
    elif render_readiness == "not_ready":
        next_action = (
            "Resolve lineage or failure status issues before renderer handoff."
        )
    else:
        next_action = (
            "Review caution flags and confirm operator intent before renderer handoff."
        )

    sequence = next_sequence(runtime_root, args.task_id)
    token = sanitize(args.task_id)[:20]
    doc_id = f"doc_preflight_{token}_{sequence:04d}"
    if not re.fullmatch(r"doc_[a-z0-9][a-z0-9._-]{5,63}", doc_id):
        doc_id = f"doc_preflight_{sequence:04d}_{sha256_hex(args.task_id.encode('utf-8'))[:8]}"
    report_id = f"preflight_{token}_{sequence:04d}"

    output_dir = runtime_root / "preflight" / "reports"
    output_path = output_dir / f"{doc_id}.pxml"

    if args.dry_run:
        print(f"report_id={report_id}")
        print(f"render_readiness={render_readiness}")
        print(f"lineage_ok={str(lineage_ok).lower()}")
        print(f"status_ok={str(status_ok).lower()}")
        print(f"unresolved_failures={','.join(unresolved_for_payload)}")
        print(f"quarantine_flags={','.join(quarantine_for_payload)}")
        print(f"next_action={next_action}")
        return 0

    root = etree.Element(q("pxml"), nsmap=NSMAP)
    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = doc_id
    etree.SubElement(meta, q("doc_class")).text = "operator_preflight_report"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = args.task_id
    etree.SubElement(meta, q("run_id")).text = f"run_preflight_{sanitize(args.task_id)}"
    etree.SubElement(meta, q("sequence")).text = str(sequence)
    etree.SubElement(meta, q("writer_agent")).text = "system"
    etree.SubElement(meta, q("created_at")).text = now_iso()

    refs = etree.SubElement(root, q("refs"))
    build_ref(refs, route, "latest_route")
    build_ref(refs, packet, "latest_packet")
    build_ref(refs, status, "latest_status_report")
    build_ref(refs, trace, "latest_trace")
    if verification is not None:
        build_ref(refs, verification, "latest_verification")

    payload = etree.SubElement(root, q("payload"))
    etree.SubElement(payload, q("report_id")).text = report_id
    etree.SubElement(payload, q("task_id")).text = args.task_id
    build_ref_node(payload, "latest_route_ref", route, "latest_route")
    build_ref_node(payload, "latest_packet_ref", packet, "latest_packet")
    build_ref_node(payload, "latest_status_report_ref", status, "latest_status_report")
    build_ref_node(payload, "latest_trace_ref", trace, "latest_trace")
    if verification is not None:
        build_ref_node(
            payload, "latest_verification_ref", verification, "latest_verification"
        )

    flags_node = etree.SubElement(payload, q("quarantine_flags"))
    for item in quarantine_for_payload:
        etree.SubElement(flags_node, q("item")).text = item

    etree.SubElement(payload, q("lineage_ok")).text = str(lineage_ok).lower()
    etree.SubElement(payload, q("status_ok")).text = str(status_ok).lower()

    unresolved_node = etree.SubElement(payload, q("unresolved_failures"))
    for item in unresolved_for_payload:
        etree.SubElement(unresolved_node, q("item")).text = item

    etree.SubElement(payload, q("render_readiness")).text = render_readiness
    etree.SubElement(payload, q("next_action")).text = next_action

    integrity = etree.SubElement(root, q("integrity"))
    content_sha = compute_content_hash(meta, refs, payload)
    etree.SubElement(integrity, q("content_sha256")).text = content_sha
    status_sha = text_at(status_tree, "/p:pxml/p:integrity/p:content_sha256")
    if status_sha:
        etree.SubElement(integrity, q("parent_sha256")).text = status_sha

    ensure_dir(output_dir)
    tree = etree.ElementTree(root)
    tree.write(
        str(output_path), encoding="UTF-8", xml_declaration=True, pretty_print=True
    )

    latest_path = (
        runtime_root
        / "latest"
        / f"{sanitize(args.task_id)}_operator_preflight_report.pxml"
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
    task_index["latest_operator_preflight_report"] = str(
        output_path.relative_to(runtime_root)
    )
    task_index["updated_at"] = now_iso()
    task_index_path.write_text(
        json.dumps(task_index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    artifact_index = {
        "doc_id": doc_id,
        "doc_class": "operator_preflight_report",
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
            context_files = [route.path, packet.path, status.path, trace.path]
            if verification is not None:
                context_files.append(verification.path)
            run_validation(validator, output_path, context_files)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    print(f"Generated operator_preflight_report: {output_path}")
    print(f"render_readiness={render_readiness}")
    print(f"lineage_ok={str(lineage_ok).lower()}")
    print(f"status_ok={str(status_ok).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
