#!/usr/bin/env python3
"""Quarantine suspicious or stale runtime artifacts."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
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


NS = {"p": "urn:pxml:v1"}


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
class QuarantineCandidate:
    path: Path
    reasons: List[str]


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


def text_at(tree: etree._ElementTree, xpath_expr: str) -> Optional[str]:
    values = tree.xpath(xpath_expr, namespaces=NS)
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
    return Artifact(
        path=path,
        doc_id=doc_id,
        doc_class=doc_class,
        task_id=task_id,
        sequence=sequence,
        created_at=created_at,
        tree=tree,
    )


def load_retention_policy(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        tree = etree.parse(str(path))
    except etree.XMLSyntaxError:
        return False
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    return doc_class == "runtime_retention_policy"


def gather_task_artifacts(runtime_root: Path, task_id: str) -> List[Artifact]:
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
    ]
    artifacts: List[Artifact] = []
    for directory in scan_dirs:
        for path in discover_pxml_files(directory):
            parsed = parse_artifact(path)
            if parsed is None or parsed.task_id != task_id:
                continue
            artifacts.append(parsed)
    return artifacts


def latest_by_class(artifacts: Sequence[Artifact]) -> Dict[str, Artifact]:
    latest: Dict[str, Artifact] = {}
    for artifact in artifacts:
        current = latest.get(artifact.doc_class)
        if current is None:
            latest[artifact.doc_class] = artifact
            continue
        if (artifact.sequence, artifact.created_at, str(artifact.path)) > (
            current.sequence,
            current.created_at,
            str(current.path),
        ):
            latest[artifact.doc_class] = artifact
    return latest


def detect_stale_candidates(
    runtime_root: Path, task_id: str
) -> List[QuarantineCandidate]:
    artifacts = gather_task_artifacts(runtime_root, task_id)
    if not artifacts:
        return []

    latest = latest_by_class(artifacts)
    candidates: Dict[Path, QuarantineCandidate] = {}

    for artifact in artifacts:
        latest_item = latest.get(artifact.doc_class)
        if latest_item is not None and latest_item.path != artifact.path:
            candidate = candidates.get(artifact.path)
            if candidate is None:
                candidate = QuarantineCandidate(path=artifact.path, reasons=[])
                candidates[artifact.path] = candidate
            candidate.reasons.append("non_latest_version")

    route = latest.get("manager_route")
    lock_value = (
        text_at(route.tree, "/p:pxml/p:payload/p:acceptance_lock/p:lock_sha256")
        if route is not None
        else None
    )
    if lock_value:
        for artifact in artifacts:
            if artifact.doc_class == "execution_packet":
                packet_lock = text_at(
                    artifact.tree, "/p:pxml/p:payload/p:acceptance_lock_hash"
                )
                if packet_lock and packet_lock != lock_value:
                    candidate = candidates.get(artifact.path)
                    if candidate is None:
                        candidate = QuarantineCandidate(path=artifact.path, reasons=[])
                        candidates[artifact.path] = candidate
                    candidate.reasons.append("lineage_mismatch_packet")
            if artifact.doc_class == "verification_result":
                verification_lock = text_at(
                    artifact.tree, "/p:pxml/p:payload/p:acceptance_lock_sha256"
                )
                if verification_lock and verification_lock != lock_value:
                    candidate = candidates.get(artifact.path)
                    if candidate is None:
                        candidate = QuarantineCandidate(path=artifact.path, reasons=[])
                        candidates[artifact.path] = candidate
                    candidate.reasons.append("lineage_mismatch_verification")

    return sorted(candidates.values(), key=lambda item: str(item.path))


def build_destination(
    runtime_root: Path, quarantine_root: Path, path: Path, task_id: str
) -> Path:
    try:
        rel = path.relative_to(runtime_root)
    except ValueError:
        rel = Path(path.name)
    return quarantine_root / sanitize(task_id) / rel


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Quarantine stale or suspicious runtime artifacts."
    )
    parser.add_argument("--task-id", default=None, help="Target task id for detection.")
    parser.add_argument(
        "--artifact",
        dest="artifacts",
        action="append",
        default=[],
        help="Explicit artifact path to quarantine (can be repeated).",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=repo_root / "runtime",
        help="Runtime root directory.",
    )
    parser.add_argument(
        "--retention-policy",
        type=Path,
        default=repo_root / "instructions" / "runtime_retention_policy.pxml",
        help="Retention policy artifact path.",
    )
    parser.add_argument(
        "--mode",
        choices=["move", "mark"],
        default="move",
        help="move: relocate files to quarantine, mark: create manifest only.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List candidates without changing files.",
    )
    parser.add_argument(
        "--reason",
        default=None,
        help="Optional explicit reason tag for explicit artifact selection.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime_root = args.runtime_root.resolve()
    policy_path = args.retention_policy.resolve()

    if not runtime_root.exists():
        print(f"ERROR: runtime root not found: {runtime_root}", file=sys.stderr)
        return 2
    if not load_retention_policy(policy_path):
        print(f"ERROR: invalid retention policy: {policy_path}", file=sys.stderr)
        return 2
    if args.task_id is None and not args.artifacts:
        print("ERROR: provide --task-id or --artifact", file=sys.stderr)
        return 2

    candidates: Dict[Path, QuarantineCandidate] = {}

    if args.task_id:
        for candidate in detect_stale_candidates(runtime_root, args.task_id):
            candidates[candidate.path] = candidate

    for artifact_value in args.artifacts:
        artifact_path = Path(artifact_value).resolve()
        if not artifact_path.exists():
            print(
                f"WARN: explicit artifact not found: {artifact_path}", file=sys.stderr
            )
            continue
        existing = candidates.get(artifact_path)
        if existing is None:
            existing = QuarantineCandidate(path=artifact_path, reasons=[])
            candidates[artifact_path] = existing
        existing.reasons.append(args.reason or "explicit_operator_selection")

    if not candidates:
        task_label = args.task_id or "manual"
        print(f"No quarantine candidates found for {task_label}")
        return 0

    stamp = now_stamp()
    task_label = sanitize(args.task_id) if args.task_id else "manual"
    quarantine_root = runtime_root / "quarantine" / stamp
    manifest_dir = runtime_root / "quarantine" / "manifests"
    manifest_path = manifest_dir / f"quarantine_{task_label}_{stamp}.json"

    manifest_entries: List[Dict[str, object]] = []
    moved_count = 0

    print(f"quarantine_mode={args.mode}")
    print(f"dry_run={str(args.dry_run).lower()}")
    for candidate in sorted(candidates.values(), key=lambda item: str(item.path)):
        reasons = sorted(set(candidate.reasons))
        target_task = args.task_id
        parsed = parse_artifact(candidate.path)
        if parsed is not None:
            target_task = parsed.task_id
        destination = build_destination(
            runtime_root,
            quarantine_root,
            candidate.path,
            target_task or "manual",
        )
        print(f"candidate={candidate.path}")
        print(f"  reasons={','.join(reasons)}")
        print(f"  destination={destination}")

        action = "marked"
        if args.mode == "move" and not args.dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(candidate.path), str(destination))
            moved_count += 1
            action = "moved"

        manifest_entries.append(
            {
                "source": str(candidate.path),
                "destination": str(destination),
                "task_id": target_task,
                "reasons": reasons,
                "action": action,
            }
        )

    manifest_payload = {
        "manifest_id": f"quarantine_{task_label}_{stamp}",
        "created_at": now_iso(),
        "runtime_root": str(runtime_root),
        "task_id": args.task_id,
        "mode": args.mode,
        "dry_run": args.dry_run,
        "retention_policy_ref": str(policy_path),
        "entries": manifest_entries,
    }

    if not args.dry_run:
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"manifest={manifest_path}")
    else:
        print("manifest=dry_run_not_written")

    print(f"candidate_count={len(manifest_entries)}")
    print(f"moved_count={moved_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
