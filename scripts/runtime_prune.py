#!/usr/bin/env python3
"""Policy-driven runtime pruning with dry-run default safety."""

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

DERIVED_DELETE_CLASSES = {
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
class Candidate:
    artifact: Artifact
    reason: str
    action: str
    replacement_doc_id: Optional[str]


def q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sanitize(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9._-]", "_", lowered)
    lowered = re.sub(r"_+", "_", lowered).strip("_")
    return lowered or "id"


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


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
        return None

    return Artifact(
        path=path,
        doc_id=doc_id,
        doc_class=doc_class,
        task_id=task_id,
        sequence=sequence,
        created_at=created_at,
        tree=tree,
    )


def refs_of(tree: etree._ElementTree) -> List[Tuple[str, Optional[str]]]:
    refs: List[Tuple[str, Optional[str]]] = []
    nodes = tree.xpath("/p:pxml/p:refs/p:ref", namespaces=XPATH_NS)
    for node in nodes:
        node_tree = etree.ElementTree(node)
        doc_id = text_at(node_tree, "./p:doc_id")
        doc_class = text_at(node_tree, "./p:doc_class")
        if doc_id:
            refs.append((doc_id, doc_class))
    return refs


def load_pruning_policy(path: Path) -> str:
    if not path.exists():
        raise ValueError(f"artifact pruning policy not found: {path}")
    tree = etree.parse(str(path))
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    if doc_class != "artifact_pruning_policy":
        raise ValueError(f"invalid policy doc_class: {doc_class}")
    doc_id = text_at(tree, "/p:pxml/p:meta/p:doc_id")
    if doc_id is None:
        raise ValueError("artifact pruning policy is missing meta/doc_id")
    return doc_id


def gather_artifacts(runtime_root: Path, task_scope: Optional[str]) -> List[Artifact]:
    scan_dirs = [
        runtime_root / "packets" / "manager_route",
        runtime_root / "packets" / "execution_packet",
        runtime_root / "exploration" / "requests",
        runtime_root / "exploration" / "results",
        runtime_root / "implementer" / "results",
        runtime_root / "sidecars" / "planner",
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
    artifacts: List[Artifact] = []
    for directory in scan_dirs:
        for path in discover_pxml_files(directory):
            artifact = parse_artifact(path)
            if artifact is None:
                continue
            if task_scope and artifact.task_id != task_scope:
                continue
            artifacts.append(artifact)
    artifacts.sort(
        key=lambda item: (item.task_id, item.doc_class, item.sequence, str(item.path))
    )
    return artifacts


def latest_map(artifacts: Sequence[Artifact]) -> Dict[Tuple[str, str], Artifact]:
    latest: Dict[Tuple[str, str], Artifact] = {}
    for artifact in artifacts:
        key = (artifact.task_id, artifact.doc_class)
        current = latest.get(key)
        if current is None:
            latest[key] = artifact
            continue
        if (artifact.sequence, artifact.created_at, str(artifact.path)) > (
            current.sequence,
            current.created_at,
            str(current.path),
        ):
            latest[key] = artifact
    return latest


def lineage_value(artifact: Artifact) -> Optional[str]:
    probes = [
        "/p:pxml/p:payload/p:acceptance_lock_hash",
        "/p:pxml/p:payload/p:acceptance_lock_sha256",
        "/p:pxml/p:payload/p:lineage_lock_sha256",
    ]
    for probe in probes:
        value = text_at(artifact.tree, probe)
        if value:
            return value
    return None


def latest_lineage_by_task(
    latest: Dict[Tuple[str, str], Artifact],
) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}
    for (task_id, doc_class), artifact in latest.items():
        if doc_class != "execution_packet":
            continue
        out[task_id] = lineage_value(artifact)
    return out


def latest_artifacts_for_task(runtime_root: Path, task_id: str) -> List[Artifact]:
    files = [
        path
        for path in discover_pxml_files(runtime_root / "latest")
        if path.name.startswith(f"{sanitize(task_id)}_")
    ]
    items: List[Artifact] = []
    for path in files:
        parsed = parse_artifact(path)
        if parsed is not None and parsed.task_id == task_id:
            items.append(parsed)
    items.sort(key=lambda item: (item.doc_class, item.sequence, str(item.path)))
    return items


def protected_doc_closure(
    task_id: str, docs_by_id: Dict[str, Artifact], runtime_root: Path
) -> Set[str]:
    protected: Set[str] = set()
    queue: List[str] = []

    for latest_doc in latest_artifacts_for_task(runtime_root, task_id):
        protected.add(latest_doc.doc_id)
        queue.append(latest_doc.doc_id)
        docs_by_id.setdefault(latest_doc.doc_id, latest_doc)

    while queue:
        current_id = queue.pop(0)
        current = docs_by_id.get(current_id)
        if current is None:
            continue
        if current.doc_class == "pruning_report":
            continue
        for ref_doc_id, _ in refs_of(current.tree):
            if ref_doc_id in protected:
                continue
            protected.add(ref_doc_id)
            if ref_doc_id in docs_by_id:
                queue.append(ref_doc_id)
    return protected


def detect_candidates(
    artifacts: Sequence[Artifact],
    latest: Dict[Tuple[str, str], Artifact],
    protected_by_task: Dict[str, Set[str]],
    lineage_by_task: Dict[str, Optional[str]],
    apply_mode: str,
) -> List[Candidate]:
    candidates: List[Candidate] = []
    for artifact in artifacts:
        latest_item = latest.get((artifact.task_id, artifact.doc_class))
        if latest_item is None:
            continue
        if artifact.path == latest_item.path:
            continue

        reasons: List[str] = ["non_latest_version"]
        action = "quarantine"
        replacement_doc_id = latest_item.doc_id

        protected = protected_by_task.get(artifact.task_id, set())
        if artifact.doc_id in protected:
            reasons.append("referenced_by_latest")
            action = "deny"
            replacement_doc_id = None
        else:
            latest_lock = lineage_by_task.get(artifact.task_id)
            candidate_lock = lineage_value(artifact)
            if latest_lock and candidate_lock and latest_lock != candidate_lock:
                reasons.append("lineage_mismatch")
                action = "quarantine"
            elif (
                apply_mode == "delete-derived-safe"
                and artifact.doc_class in DERIVED_DELETE_CLASSES
            ):
                reasons.append("derived_safe_delete")
                action = "delete_derived_safe"

        candidates.append(
            Candidate(
                artifact=artifact,
                reason=",".join(sorted(set(reasons))),
                action=action,
                replacement_doc_id=replacement_doc_id,
            )
        )

    candidates.sort(
        key=lambda item: (
            item.artifact.task_id,
            item.artifact.doc_class,
            item.artifact.sequence,
            str(item.artifact.path),
        )
    )
    return candidates


def build_destination(runtime_root: Path, quarantine_root: Path, source: Path) -> Path:
    try:
        rel = source.relative_to(runtime_root)
    except ValueError:
        rel = Path(source.name)
    return quarantine_root / rel


def next_sequence(runtime_root: Path, task_id: str) -> int:
    maximum = 0
    for path in discover_pxml_files(runtime_root):
        artifact = parse_artifact(path)
        if artifact is None or artifact.task_id != task_id:
            continue
        maximum = max(maximum, artifact.sequence)
    return maximum + 1


def build_ref(
    parent: etree._Element, doc_id: str, doc_class: str, relation: str
) -> None:
    ref = etree.SubElement(parent, q("ref"))
    etree.SubElement(ref, q("doc_id")).text = doc_id
    etree.SubElement(ref, q("doc_class")).text = doc_class
    etree.SubElement(ref, q("relation")).text = relation


def build_candidate_node(
    parent: etree._Element, candidate: Candidate, runtime_root: Path
) -> None:
    node = etree.SubElement(parent, q("candidate"))
    etree.SubElement(node, q("doc_id")).text = candidate.artifact.doc_id
    etree.SubElement(node, q("doc_class")).text = candidate.artifact.doc_class
    etree.SubElement(node, q("task_id")).text = candidate.artifact.task_id
    try:
        rel = candidate.artifact.path.relative_to(runtime_root)
        rel_text = str(rel).replace("\\", "/")
    except ValueError:
        rel_text = str(candidate.artifact.path)
    etree.SubElement(node, q("path")).text = rel_text
    etree.SubElement(node, q("reason")).text = candidate.reason
    etree.SubElement(node, q("action")).text = candidate.action


def run_validation(
    validator: Path,
    report_path: Path,
    context_files: Sequence[Path],
) -> None:
    with tempfile.TemporaryDirectory(prefix="pxml_runtime_prune_validate_") as temp_dir:
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


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Prune stale runtime artifacts with policy-enforced safety."
    )
    parser.add_argument("--task-id", default=None, help="Task scope for pruning.")
    parser.add_argument(
        "--all-tasks",
        action="store_true",
        help="Allow all-task scope (dry-run by default).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply pruning actions; without this flag the run is dry-run only.",
    )
    parser.add_argument(
        "--mode",
        "--apply-mode",
        dest="apply_mode",
        choices=["quarantine-first", "delete-derived-safe"],
        default="quarantine-first",
        help="quarantine-first keeps non-destructive moves; delete-derived-safe allows derived deletions only.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Force dry-run mode even when apply would otherwise run.",
    )
    parser.add_argument(
        "--allow-global-apply",
        action="store_true",
        help="Required to apply when --all-tasks is used.",
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
        default=repo_root / "instructions" / "artifact_pruning_policy.pxml",
        help="Artifact pruning policy path.",
    )
    parser.add_argument(
        "--validator",
        type=Path,
        default=repo_root / "scripts" / "pxml_validator.py",
        help="PXML validator script path.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip pruning report validation.",
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
    if args.task_id is None and not args.all_tasks:
        print("ERROR: provide --task-id or --all-tasks", file=sys.stderr)
        return 2
    if args.task_id is not None and args.all_tasks:
        print("ERROR: choose either --task-id or --all-tasks", file=sys.stderr)
        return 2
    if args.apply and args.dry_run:
        print("ERROR: --apply and --dry-run are mutually exclusive", file=sys.stderr)
        return 2
    if args.apply and args.all_tasks and not args.allow_global_apply:
        print(
            "ERROR: --all-tasks apply requires --allow-global-apply",
            file=sys.stderr,
        )
        return 2
    if not args.skip_validate and not validator.exists():
        print(f"ERROR: validator not found: {validator}", file=sys.stderr)
        return 2

    try:
        policy_doc_id = load_pruning_policy(policy_path)
    except Exception as exc:
        print(f"ERROR: failed to load pruning policy: {exc}", file=sys.stderr)
        return 2

    task_scope = args.task_id
    meta_task_id = args.task_id if args.task_id else "task_runtime_prune_global"
    scope_label = args.task_id if args.task_id else "all_tasks"
    dry_run = args.dry_run or not args.apply

    artifacts = gather_artifacts(runtime_root, task_scope)
    if not artifacts:
        print(f"No pruning candidates found for scope={scope_label}")
        return 0

    latest = latest_map(artifacts)
    docs_by_id = {artifact.doc_id: artifact for artifact in artifacts}
    lineage_by_task = latest_lineage_by_task(latest)

    protected_by_task: Dict[str, Set[str]] = {}
    task_ids = sorted({artifact.task_id for artifact in artifacts})
    for task_id in task_ids:
        protected_by_task[task_id] = protected_doc_closure(
            task_id, docs_by_id, runtime_root
        )

    candidates = detect_candidates(
        artifacts=artifacts,
        latest=latest,
        protected_by_task=protected_by_task,
        lineage_by_task=lineage_by_task,
        apply_mode=args.apply_mode,
    )

    denied = [item for item in candidates if item.action == "deny"]
    quarantine = [item for item in candidates if item.action == "quarantine"]
    delete = [item for item in candidates if item.action == "delete_derived_safe"]

    warnings: List[str] = []
    if dry_run:
        warnings.append("dry_run_default")
    if args.all_tasks and args.apply:
        warnings.append("global_apply_override_used")
    if not warnings:
        warnings = ["none"]

    if denied and (quarantine or delete):
        result = "partial"
    elif denied:
        result = "denied"
    else:
        result = "success"

    lineage_safety_ok = True
    latest_pointer_safety_ok = True
    for item in delete:
        if "lineage_mismatch" in item.reason:
            lineage_safety_ok = False
        protected = protected_by_task.get(item.artifact.task_id, set())
        if item.artifact.doc_id in protected:
            latest_pointer_safety_ok = False

    stamp = now_stamp()
    run_started_at = now_iso()
    quarantine_root = runtime_root / "quarantine" / stamp / sanitize(scope_label)
    manifest_dir = runtime_root / "pruning" / "manifests"
    manifest_path = manifest_dir / f"prune_{sanitize(scope_label)}_{stamp}.json"

    manifest_entries: List[Dict[str, object]] = []
    quarantined_count = 0
    deleted_count = 0

    for item in quarantine:
        destination = build_destination(
            runtime_root, quarantine_root, item.artifact.path
        )
        action = "planned_quarantine"
        if not dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(item.artifact.path), str(destination))
            action = "quarantined"
            quarantined_count += 1
        manifest_entries.append(
            {
                "doc_id": item.artifact.doc_id,
                "doc_class": item.artifact.doc_class,
                "task_id": item.artifact.task_id,
                "source": str(item.artifact.path),
                "destination": str(destination),
                "reason": item.reason,
                "planned_action": item.action,
                "applied_action": action,
            }
        )

    for item in delete:
        action = "planned_delete"
        if not dry_run:
            item.artifact.path.unlink(missing_ok=True)
            action = "deleted"
            deleted_count += 1
        manifest_entries.append(
            {
                "doc_id": item.artifact.doc_id,
                "doc_class": item.artifact.doc_class,
                "task_id": item.artifact.task_id,
                "source": str(item.artifact.path),
                "destination": None,
                "reason": item.reason,
                "planned_action": item.action,
                "applied_action": action,
            }
        )

    for item in denied:
        manifest_entries.append(
            {
                "doc_id": item.artifact.doc_id,
                "doc_class": item.artifact.doc_class,
                "task_id": item.artifact.task_id,
                "source": str(item.artifact.path),
                "destination": None,
                "reason": item.reason,
                "planned_action": item.action,
                "applied_action": "denied",
            }
        )

    if not dry_run:
        ensure_dir(manifest_dir)
        manifest_payload = {
            "manifest_id": f"prune_{sanitize(scope_label)}_{stamp}",
            "created_at": now_iso(),
            "scope": scope_label,
            "dry_run": dry_run,
            "apply_mode": args.apply_mode,
            "policy": str(policy_path),
            "entries": manifest_entries,
        }
        manifest_path.write_text(
            json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    sequence = next_sequence(runtime_root, meta_task_id)
    token = sanitize(scope_label)[:20]
    doc_id = f"doc_pruning_report_{token}_{sequence:04d}"
    if not re.fullmatch(r"doc_[a-z0-9][a-z0-9._-]{5,63}", doc_id):
        doc_id = (
            "doc_pruning_report_"
            + sha256_hex(scope_label.encode("utf-8"))[:12]
            + f"_{sequence:04d}"
        )
    report_id = f"prune_{token}_{sequence:04d}"
    output_dir = runtime_root / "pruning" / "reports"
    output_path = output_dir / f"{doc_id}.pxml"
    run_finished_at = now_iso()

    root = etree.Element(q("pxml"), nsmap=NSMAP)
    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = doc_id
    etree.SubElement(meta, q("doc_class")).text = "pruning_report"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = meta_task_id
    etree.SubElement(
        meta, q("run_id")
    ).text = f"run_runtime_prune_{sanitize(scope_label)}"
    etree.SubElement(meta, q("sequence")).text = str(sequence)
    etree.SubElement(meta, q("writer_agent")).text = "system"
    etree.SubElement(meta, q("created_at")).text = run_finished_at

    refs: Optional[etree._Element] = None

    payload = etree.SubElement(root, q("payload"))
    etree.SubElement(payload, q("pruning_report_id")).text = report_id
    etree.SubElement(payload, q("task_scope")).text = scope_label

    policy_ref = etree.SubElement(payload, q("policy_ref"))
    etree.SubElement(policy_ref, q("doc_id")).text = policy_doc_id
    etree.SubElement(policy_ref, q("doc_class")).text = "artifact_pruning_policy"
    etree.SubElement(policy_ref, q("relation")).text = "policy_ref"

    etree.SubElement(payload, q("derived")).text = "true"
    etree.SubElement(payload, q("dry_run")).text = "true" if dry_run else "false"
    etree.SubElement(payload, q("run_started_at")).text = run_started_at
    etree.SubElement(payload, q("run_finished_at")).text = run_finished_at
    etree.SubElement(payload, q("candidate_count")).text = str(len(candidates))

    kept_refs = etree.SubElement(payload, q("kept_refs"))
    protected_ids = sorted(
        {doc_id for ids in protected_by_task.values() for doc_id in ids}
    )
    for doc_id_value in protected_ids:
        etree.SubElement(kept_refs, q("item")).text = doc_id_value

    denied_node = etree.SubElement(payload, q("denied_candidates"))
    for item in denied:
        build_candidate_node(denied_node, item, runtime_root)

    quarantine_node = etree.SubElement(payload, q("quarantine_candidates"))
    for item in quarantine:
        build_candidate_node(quarantine_node, item, runtime_root)

    delete_node = etree.SubElement(payload, q("delete_candidates"))
    for item in delete:
        build_candidate_node(delete_node, item, runtime_root)

    proofs_node = etree.SubElement(payload, q("replacement_proofs"))
    for item in delete:
        if item.replacement_doc_id is None:
            continue
        proof_item = etree.SubElement(proofs_node, q("proof_item"))
        etree.SubElement(proof_item, q("candidate_doc_id")).text = item.artifact.doc_id
        etree.SubElement(
            proof_item, q("replacement_doc_id")
        ).text = item.replacement_doc_id
        etree.SubElement(
            proof_item, q("proof")
        ).text = "non-latest derived artifact with latest replacement doc_id available"

    etree.SubElement(payload, q("lineage_safety_ok")).text = (
        "true" if lineage_safety_ok else "false"
    )
    etree.SubElement(payload, q("latest_pointer_safety_ok")).text = (
        "true" if latest_pointer_safety_ok else "false"
    )
    etree.SubElement(payload, q("result")).text = result

    warnings_node = etree.SubElement(payload, q("warnings"))
    for item in warnings:
        etree.SubElement(warnings_node, q("item")).text = item

    if dry_run:
        next_action = (
            "Review pruning report then rerun with --apply for safe execution."
        )
    elif args.apply_mode == "delete-derived-safe":
        next_action = "Apply complete; verify harness and latest pointer stability."
    else:
        next_action = "Quarantine apply complete; verify harness and review manifest."
    etree.SubElement(payload, q("next_action")).text = next_action

    integrity = etree.SubElement(root, q("integrity"))
    content_sha = compute_content_hash(meta, refs, payload)
    etree.SubElement(integrity, q("content_sha256")).text = content_sha

    ensure_dir(output_dir)
    report_tree = etree.ElementTree(root)
    report_tree.write(
        str(output_path),
        encoding="UTF-8",
        xml_declaration=True,
        pretty_print=True,
    )

    if not args.skip_validate:
        context_files = [
            policy_path,
            output_path,
        ] + [item.artifact.path for item in candidates]
        try:
            run_validation(validator, output_path, context_files)
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    latest_path = (
        runtime_root / "latest" / f"{sanitize(meta_task_id)}_pruning_report.pxml"
    )
    ensure_dir(latest_path.parent)
    shutil.copy2(output_path, latest_path)

    artifacts_dir = runtime_root / "index" / "artifacts"
    ensure_dir(artifacts_dir)
    artifact_index_path = artifacts_dir / f"{doc_id}.json"
    artifact_payload = {
        "doc_class": "pruning_report",
        "doc_id": doc_id,
        "path": str(output_path.relative_to(runtime_root)).replace("\\", "/"),
        "task_id": meta_task_id,
        "updated_at": run_finished_at,
    }
    artifact_index_path.write_text(
        json.dumps(artifact_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if args.task_id is not None:
        tasks_dir = runtime_root / "index" / "tasks"
        ensure_dir(tasks_dir)
        task_index_path = tasks_dir / f"{sanitize(args.task_id)}.json"
        task_index: Dict[str, object] = {}
        if task_index_path.exists():
            try:
                task_index = json.loads(task_index_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                task_index = {}
        task_index["task_id"] = args.task_id
        task_index["latest_pruning_report"] = str(
            output_path.relative_to(runtime_root)
        ).replace("\\", "/")
        task_index["updated_at"] = run_finished_at
        task_index_path.write_text(
            json.dumps(task_index, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(f"scope={scope_label}")
    print(f"dry_run={str(dry_run).lower()}")
    print(f"apply_mode={args.apply_mode}")
    print(f"candidate_count={len(candidates)}")
    print(f"denied_count={len(denied)}")
    print(f"quarantine_count={len(quarantine)}")
    print(f"delete_count={len(delete)}")
    print(f"quarantined_count={quarantined_count}")
    print(f"deleted_count={deleted_count}")
    print(f"lineage_safety_ok={str(lineage_safety_ok).lower()}")
    print(f"latest_pointer_safety_ok={str(latest_pointer_safety_ok).lower()}")
    print(f"pruning_report={output_path}")
    if dry_run:
        print("manifest=dry_run_not_written")
    else:
        print(f"manifest={manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
