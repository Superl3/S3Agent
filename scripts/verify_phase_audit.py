#!/usr/bin/env python3
"""Generate verify_phase audit report from smoke/runtime evidence."""

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
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

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

DEFAULT_AUDIT_TASK_IDS = [
    "task_verify_post_smoke_001",
    "task_impl_feature_direct_001",
]

DEFAULT_FRESH_TASK_IDS = {
    "task_verify_post_smoke_001",
    "task_impl_feature_direct_001",
    "task_full_lane_candidate_001",
}


@dataclass
class ArtifactInfo:
    path: Path
    doc_id: str
    doc_class: str
    task_id: str
    sequence: int
    created_at: str
    tree: etree._ElementTree


@dataclass
class VerifyEvent:
    event_type: str
    event_seq: int
    verify_phase: Optional[str]


@dataclass
class EvidenceRef:
    doc_id: str
    doc_class: str
    relation: str


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


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


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


def parse_task_file(path: Path) -> Optional[Dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def load_list_file(path: Path) -> List[str]:
    task_ids: List[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        task_ids.append(line)
    return task_ids


def unique_preserve(values: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    ordered: List[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


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
    sequence_text = text_at(tree, "/p:pxml/p:meta/p:sequence")
    created_at = text_at(tree, "/p:pxml/p:meta/p:created_at")
    if (
        doc_id is None
        or doc_class is None
        or task_id is None
        or sequence_text is None
        or created_at is None
    ):
        return None
    try:
        sequence = int(sequence_text)
    except ValueError:
        return None

    return ArtifactInfo(
        path=path,
        doc_id=doc_id,
        doc_class=doc_class,
        task_id=task_id,
        sequence=sequence,
        created_at=created_at,
        tree=tree,
    )


def latest_for_task(
    directory: Path, doc_class: str, task_id: str
) -> Optional[ArtifactInfo]:
    candidates: List[ArtifactInfo] = []
    for path in discover_pxml_files(directory):
        artifact = parse_artifact(path)
        if artifact is None:
            continue
        if artifact.task_id != task_id or artifact.doc_class != doc_class:
            continue
        candidates.append(artifact)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item.sequence, item.created_at, str(item.path)))
    return candidates[-1]


def next_sequence(runtime_root: Path, task_id: str) -> int:
    maximum = 0
    for path in discover_pxml_files(runtime_root):
        artifact = parse_artifact(path)
        if artifact is None or artifact.task_id != task_id:
            continue
        maximum = max(maximum, artifact.sequence)
    return maximum + 1


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_content_hash(
    meta: etree._Element,
    refs: Optional[etree._Element],
    payload: etree._Element,
) -> str:
    material = etree.Element(q("hash_material"), nsmap=NSMAP)
    material.append(copy.deepcopy(meta))
    if refs is not None:
        material.append(copy.deepcopy(refs))
    material.append(copy.deepcopy(payload))
    c14n = etree.tostring(material, method="c14n", exclusive=True, with_comments=False)
    return sha256_hex(c14n)


def ref_node(
    parent: etree._Element, doc_id: str, doc_class: str, relation: str
) -> None:
    ref = etree.SubElement(parent, q("ref"))
    etree.SubElement(ref, q("doc_id")).text = doc_id
    etree.SubElement(ref, q("doc_class")).text = doc_class
    etree.SubElement(ref, q("relation")).text = relation


def parse_trace_events(trace_info: ArtifactInfo) -> List[VerifyEvent]:
    events: List[VerifyEvent] = []
    nodes = trace_info.tree.xpath(
        "/p:pxml/p:payload/p:events/p:event",
        namespaces=XPATH_NS,
    )
    for index, node in enumerate(nodes, start=1):
        node_tree = etree.ElementTree(node)
        event_type = text_at(node_tree, "./p:event_type")
        if event_type is None:
            continue
        seq_text = text_at(node_tree, "./p:event_seq")
        event_seq = int(seq_text) if seq_text and seq_text.isdigit() else index
        verify_phase = text_at(node_tree, "./p:verify_phase")
        events.append(
            VerifyEvent(
                event_type=event_type,
                event_seq=event_seq,
                verify_phase=verify_phase,
            )
        )
    return events


def add_unique_ref(
    out: List[EvidenceRef],
    seen: Set[Tuple[str, str, str]],
    ref: EvidenceRef,
) -> None:
    key = (ref.doc_id, ref.doc_class, ref.relation)
    if key in seen:
        return
    seen.add(key)
    out.append(ref)


def run_validation(
    validator: Path,
    report_path: Path,
    context_files: Sequence[Path],
) -> None:
    with tempfile.TemporaryDirectory(
        prefix="pxml_verify_phase_audit_validate_"
    ) as temp_dir:
        temp_root = Path(temp_dir)
        target_copy = temp_root / report_path.name
        shutil.copy2(report_path, target_copy)
        for source in context_files:
            if not source.exists():
                continue
            destination = temp_root / source.name
            if destination.exists():
                continue
            shutil.copy2(source, destination)

        command = [
            sys.executable,
            str(validator),
            str(target_copy),
            "--context-dir",
            str(temp_root),
        ]
        proc = subprocess.run(command, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"Validation failed for {report_path}")


def promote_audit_report(
    runtime_root: Path,
    report_path: Path,
    report_doc_id: str,
    release_task_id: str,
    audited_task_ids: Sequence[str],
) -> None:
    latest_dir = runtime_root / "latest"
    ensure_dir(latest_dir)
    scoped_latest_path = (
        latest_dir / f"{sanitize(release_task_id)}_verify_phase_audit_report.pxml"
    )
    shared_latest_path = latest_dir / "release_verify_phase_audit_report.pxml"
    shutil.copy2(report_path, scoped_latest_path)
    shutil.copy2(report_path, shared_latest_path)
    for task_id in audited_task_ids:
        task_latest = latest_dir / f"{sanitize(task_id)}_verify_phase_audit_report.pxml"
        shutil.copy2(report_path, task_latest)

    tasks_dir = runtime_root / "index" / "tasks"
    artifacts_dir = runtime_root / "index" / "artifacts"
    ensure_dir(tasks_dir)
    ensure_dir(artifacts_dir)

    updated_at = now_iso()
    relative_report_path = str(report_path.relative_to(runtime_root)).replace("\\", "/")

    release_index_path = tasks_dir / f"{sanitize(release_task_id)}.json"
    release_payload: Dict[str, object] = {}
    if release_index_path.exists():
        loaded = parse_task_file(release_index_path)
        if loaded is not None:
            release_payload = loaded
    release_payload["task_id"] = release_task_id
    release_payload["latest_verify_phase_audit_report"] = relative_report_path
    release_payload["updated_at"] = updated_at
    release_index_path.write_text(
        json.dumps(release_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    for task_id in audited_task_ids:
        task_index_path = tasks_dir / f"{sanitize(task_id)}.json"
        task_payload: Dict[str, object] = {}
        if task_index_path.exists():
            loaded = parse_task_file(task_index_path)
            if loaded is not None:
                task_payload = loaded
        task_payload["task_id"] = task_id
        task_payload["latest_verify_phase_audit_report"] = relative_report_path
        task_payload["updated_at"] = updated_at
        task_index_path.write_text(
            json.dumps(task_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    artifact_payload = {
        "doc_id": report_doc_id,
        "doc_class": "verify_phase_audit_report",
        "task_id": release_task_id,
        "path": relative_report_path,
        "updated_at": updated_at,
    }
    (artifacts_dir / f"{report_doc_id}.json").write_text(
        json.dumps(artifact_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Generate verify_phase audit report from runtime evidence.",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        default=[],
        help="Audited task id (can be repeated).",
    )
    parser.add_argument(
        "--audit-set-file",
        type=Path,
        default=None,
        help="Optional line-delimited audited task list.",
    )
    parser.add_argument(
        "--use-default-audit-set",
        action="store_true",
        help="Use built-in default audited tasks.",
    )
    parser.add_argument(
        "--release-task-id",
        default="task_release_candidate_batch10",
        help="Release task id used for audit artifact task scope.",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=repo_root / "runtime",
        help="Runtime root directory.",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=repo_root / "instructions" / "verify_phase_audit_policy.pxml",
        help="verify_phase_audit_policy artifact path.",
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
        help="Skip generated report validation.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime_root = args.runtime_root.resolve()
    policy_path = args.policy.resolve()
    validator = args.validator.resolve()

    if not runtime_root.exists():
        print(f"ERROR: runtime root not found: {runtime_root}", file=sys.stderr)
        return 2
    if not policy_path.exists():
        print(
            f"ERROR: verify phase audit policy not found: {policy_path}",
            file=sys.stderr,
        )
        return 2
    policy_doc = parse_artifact(policy_path)
    if policy_doc is None or policy_doc.doc_class != "verify_phase_audit_policy":
        print(
            f"ERROR: invalid verify phase audit policy artifact: {policy_path}",
            file=sys.stderr,
        )
        return 2
    if not args.skip_validate and not validator.exists():
        print(f"ERROR: validator not found: {validator}", file=sys.stderr)
        return 2

    explicit_inputs = bool(args.task_id or args.audit_set_file is not None)
    audited_task_ids: List[str] = []
    audited_task_ids.extend(args.task_id)
    if args.audit_set_file is not None:
        audited_task_ids.extend(load_list_file(args.audit_set_file.resolve()))
    if args.use_default_audit_set or not explicit_inputs:
        audited_task_ids.extend(DEFAULT_AUDIT_TASK_IDS)
    audited_task_ids = unique_preserve(audited_task_ids)

    if not audited_task_ids:
        print("ERROR: no audited task ids were selected", file=sys.stderr)
        return 2

    release_task_id = args.release_task_id.strip()
    if not release_task_id.startswith("task_"):
        print("ERROR: --release-task-id must start with task_", file=sys.stderr)
        return 2

    lane_refs: List[EvidenceRef] = []
    post_refs: List[EvidenceRef] = []
    unknown_refs: List[EvidenceRef] = []
    lane_seen: Set[Tuple[str, str, str]] = set()
    post_seen: Set[Tuple[str, str, str]] = set()
    unknown_seen: Set[Tuple[str, str, str]] = set()

    warnings: List[str] = []
    blockers: List[str] = []
    missing_requirements: List[str] = []
    context_files: List[Path] = [policy_path]

    required_lane_task_ids = {"task_verify_post_smoke_001"}
    required_post_task_ids = {"task_impl_feature_direct_001"}

    for task_id in audited_task_ids:
        route_info = latest_for_task(
            runtime_root / "packets" / "manager_route",
            "manager_route",
            task_id,
        )
        verification_info = latest_for_task(
            runtime_root / "verification" / "results",
            "verification_result",
            task_id,
        )
        trace_info = latest_for_task(
            runtime_root / "traces" / "by_task",
            "execution_trace",
            task_id,
        )

        phases_for_task: Set[str] = set()
        if route_info is not None:
            context_files.append(route_info.path)
        if verification_info is not None:
            context_files.append(verification_info.path)
            phase = text_at(
                verification_info.tree,
                "/p:pxml/p:payload/p:verify_phase",
            )
            if phase in {"lane", "post_implement", "unknown_legacy"}:
                phases_for_task.add(phase)
                relation = f"verify_phase:{phase}:verification:{task_id}"
                evidence = EvidenceRef(
                    doc_id=verification_info.doc_id,
                    doc_class=verification_info.doc_class,
                    relation=relation,
                )
                if phase == "lane":
                    add_unique_ref(lane_refs, lane_seen, evidence)
                elif phase == "post_implement":
                    add_unique_ref(post_refs, post_seen, evidence)
                else:
                    add_unique_ref(unknown_refs, unknown_seen, evidence)

        trace_events: List[VerifyEvent] = []
        if trace_info is not None:
            context_files.append(trace_info.path)
            trace_events = parse_trace_events(trace_info)

            phase_set_from_trace = {
                event.verify_phase
                for event in trace_events
                if event.event_type == "verify_done"
                and event.verify_phase in {"lane", "post_implement", "unknown_legacy"}
            }
            for phase in sorted(phase_set_from_trace):
                if phase is None:
                    continue
                phases_for_task.add(phase)
                relation = f"verify_phase:{phase}:trace:{task_id}"
                evidence = EvidenceRef(
                    doc_id=trace_info.doc_id,
                    doc_class=trace_info.doc_class,
                    relation=relation,
                )
                if phase == "lane":
                    add_unique_ref(lane_refs, lane_seen, evidence)
                elif phase == "post_implement":
                    add_unique_ref(post_refs, post_seen, evidence)
                else:
                    add_unique_ref(unknown_refs, unknown_seen, evidence)

            patch_sequences = [
                event.event_seq
                for event in trace_events
                if event.event_type == "patch_applied"
            ]
            post_sequences = [
                event.event_seq
                for event in trace_events
                if event.event_type == "verify_done"
                and event.verify_phase == "post_implement"
            ]
            if patch_sequences and post_sequences:
                if min(post_sequences) <= min(patch_sequences):
                    blockers.append(f"post_implement_event_order_invalid:{task_id}")

        if "lane" in phases_for_task and route_info is not None:
            selected_path = text_at(
                route_info.tree,
                "/p:pxml/p:payload/p:selected_path",
            )
            if selected_path not in {"verifier_post", "full_lane"}:
                warnings.append(
                    f"lane_phase_on_non_verifier_route:{task_id}:{selected_path}"
                )

        if task_id in required_lane_task_ids and "lane" not in phases_for_task:
            missing_requirements.append(f"task_lane_phase_missing:{task_id}")
            blockers.append(f"task_lane_phase_missing:{task_id}")
        if (
            task_id in required_post_task_ids
            and "post_implement" not in phases_for_task
        ):
            missing_requirements.append(f"task_post_implement_phase_missing:{task_id}")
            blockers.append(f"task_post_implement_phase_missing:{task_id}")

        if not phases_for_task:
            warnings.append(f"task_verify_phase_evidence_missing:{task_id}")
        elif phases_for_task == {"unknown_legacy"}:
            if task_id in DEFAULT_FRESH_TASK_IDS:
                blockers.append(f"unknown_legacy_only_fresh_smoke:{task_id}")
            else:
                warnings.append(f"unknown_legacy_only_legacy:{task_id}")

    if not lane_refs:
        missing_requirements.append("lane_phase_evidence_minimum_missing")
        blockers.append("lane_phase_evidence_minimum_missing")
    if not post_refs:
        missing_requirements.append("post_implement_phase_evidence_minimum_missing")
        blockers.append("post_implement_phase_evidence_minimum_missing")

    warnings = sorted(set(warnings))
    blockers = sorted(set(blockers))
    missing_requirements = sorted(set(missing_requirements))

    if blockers:
        result = "fail"
        next_action = "Resolve verify_phase blockers and rerun verify_phase_audit."
    elif warnings:
        result = "caution"
        next_action = "Review verify_phase warnings and document override decisions if proceeding."
    else:
        result = "pass"
        next_action = (
            "verify_phase evidence is complete for lane and post_implement coverage."
        )

    report_sequence = next_sequence(runtime_root, release_task_id)
    token = sanitize(release_task_id)
    report_doc_id = f"doc_verify_phase_audit_{token[:20]}_{report_sequence:04d}"
    if not re.fullmatch(r"doc_[a-z0-9][a-z0-9._-]{5,63}", report_doc_id):
        suffix = sha256_hex(f"{release_task_id}:{report_sequence}".encode("utf-8"))[:10]
        report_doc_id = f"doc_verify_phase_audit_{suffix}_{report_sequence:04d}"
    generated_at = now_iso()

    output_dir = runtime_root / "release" / "audits"
    ensure_dir(output_dir)
    report_path = output_dir / f"{report_doc_id}.pxml"

    root = etree.Element(q("pxml"), nsmap=NSMAP)
    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = report_doc_id
    etree.SubElement(meta, q("doc_class")).text = "verify_phase_audit_report"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = release_task_id
    etree.SubElement(meta, q("run_id")).text = "run_verify_phase_audit"
    etree.SubElement(meta, q("sequence")).text = str(report_sequence)
    etree.SubElement(meta, q("writer_agent")).text = "system"
    etree.SubElement(meta, q("created_at")).text = generated_at

    refs = etree.SubElement(root, q("refs"))
    ref_node(
        refs, policy_doc.doc_id, policy_doc.doc_class, "verify_phase_audit_policy_ref"
    )
    for evidence in lane_refs + post_refs + unknown_refs:
        ref_node(refs, evidence.doc_id, evidence.doc_class, evidence.relation)

    payload = etree.SubElement(root, q("payload"))
    etree.SubElement(
        payload, q("audit_report_id")
    ).text = f"verify_phase_audit_{sanitize(release_task_id)}_{report_sequence:04d}"
    etree.SubElement(payload, q("generated_at")).text = generated_at

    audited_node = etree.SubElement(payload, q("audited_task_ids"))
    for task_id in audited_task_ids:
        etree.SubElement(audited_node, q("item")).text = task_id

    policy_ref_node = etree.SubElement(payload, q("policy_ref"))
    etree.SubElement(policy_ref_node, q("doc_id")).text = policy_doc.doc_id
    etree.SubElement(policy_ref_node, q("doc_class")).text = policy_doc.doc_class
    etree.SubElement(policy_ref_node, q("relation")).text = "policy_ref"

    lane_node = etree.SubElement(payload, q("lane_phase_evidence_refs"))
    for evidence in lane_refs:
        ref_node(lane_node, evidence.doc_id, evidence.doc_class, evidence.relation)

    post_node = etree.SubElement(payload, q("post_implement_phase_evidence_refs"))
    for evidence in post_refs:
        ref_node(post_node, evidence.doc_id, evidence.doc_class, evidence.relation)

    unknown_node = etree.SubElement(payload, q("unknown_legacy_refs"))
    for evidence in unknown_refs:
        ref_node(unknown_node, evidence.doc_id, evidence.doc_class, evidence.relation)

    missing_node = etree.SubElement(payload, q("missing_phase_requirements"))
    if missing_requirements:
        for item in missing_requirements:
            etree.SubElement(missing_node, q("item")).text = item
    else:
        etree.SubElement(missing_node, q("item")).text = "none"

    warnings_node = etree.SubElement(payload, q("warnings"))
    if warnings:
        for item in warnings:
            etree.SubElement(warnings_node, q("item")).text = item
    else:
        etree.SubElement(warnings_node, q("item")).text = "none"

    blockers_node = etree.SubElement(payload, q("blockers"))
    if blockers:
        for item in blockers:
            etree.SubElement(blockers_node, q("item")).text = item
    else:
        etree.SubElement(blockers_node, q("item")).text = "none"

    etree.SubElement(payload, q("result")).text = result
    etree.SubElement(payload, q("next_action")).text = next_action

    integrity = etree.SubElement(root, q("integrity"))
    content_sha = compute_content_hash(meta, refs, payload)
    etree.SubElement(integrity, q("content_sha256")).text = content_sha

    report_tree = etree.ElementTree(root)
    report_tree.write(
        str(report_path),
        encoding="UTF-8",
        xml_declaration=True,
        pretty_print=True,
    )

    if not args.skip_validate:
        context_values = unique_preserve([str(path) for path in context_files])
        context_paths = [Path(path) for path in context_values]
        try:
            run_validation(validator, report_path, context_paths)
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    try:
        promote_audit_report(
            runtime_root=runtime_root,
            report_path=report_path,
            report_doc_id=report_doc_id,
            release_task_id=release_task_id,
            audited_task_ids=audited_task_ids,
        )
    except Exception as exc:
        print(
            f"ERROR: failed to promote verify_phase_audit report: {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"verify_phase_audit_result={result}")
    print(f"verify_phase_audit_report={report_path}")
    print(f"audited_task_count={len(audited_task_ids)}")
    print(f"lane_evidence_count={len(lane_refs)}")
    print(f"post_implement_evidence_count={len(post_refs)}")
    print(f"unknown_legacy_evidence_count={len(unknown_refs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
