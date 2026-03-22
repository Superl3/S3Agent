#!/usr/bin/env python3
"""Task-scoped runtime cleanup helper for deterministic smoke runs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Set

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


def discover_files(path: Path, suffix: str) -> Iterable[Path]:
    if not path.exists():
        return []
    files = [candidate for candidate in path.rglob(f"*{suffix}") if candidate.is_file()]
    files.sort()
    return files


def pxml_task_id(path: Path) -> Optional[str]:
    try:
        tree = etree.parse(str(path))
    except (etree.XMLSyntaxError, OSError):
        return None
    return text_at(tree, "/p:pxml/p:meta/p:task_id")


def json_task_id(path: Path) -> Optional[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    task_id = payload.get("task_id")
    if isinstance(task_id, str) and task_id.strip():
        return task_id.strip()
    return None


def collect_task_paths(runtime_root: Path, task_id: str) -> List[Path]:
    target: Set[Path] = set()

    pxml_dirs = [
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
    ]
    for directory in pxml_dirs:
        for file_path in discover_files(directory, ".pxml"):
            if pxml_task_id(file_path) == task_id:
                target.add(file_path)

    token = sanitize(task_id)
    trace_path = runtime_root / "traces" / "by_task" / f"{token}.pxml"
    if trace_path.exists():
        target.add(trace_path)

    latest_dir = runtime_root / "latest"
    if latest_dir.exists():
        for latest_path in discover_files(latest_dir, ".pxml"):
            if latest_path.name.startswith(f"{token}_"):
                target.add(latest_path)

    exact_json = [
        runtime_root / "index" / "tasks" / f"{token}.json",
        runtime_root / "index" / "failures" / f"{token}.json",
        runtime_root / "index" / "retries" / f"{token}.json",
        runtime_root / "index" / "escalations" / f"{token}.json",
    ]
    for path in exact_json:
        if path.exists():
            target.add(path)

    json_dirs = [
        runtime_root / "coordination",
        runtime_root / "index" / "artifacts",
    ]
    for directory in json_dirs:
        for file_path in discover_files(directory, ".json"):
            if json_task_id(file_path) == task_id:
                target.add(file_path)

    implementer_logs = runtime_root / "implementer" / "logs"
    if implementer_logs.exists():
        for log_path in discover_files(implementer_logs, ""):
            if token in log_path.name.lower():
                target.add(log_path)

    rendered_exports = runtime_root / "rendered" / "exports"
    if rendered_exports.exists():
        for export_path in discover_files(rendered_exports, ""):
            if token in export_path.name.lower():
                target.add(export_path)

    ordered = sorted(target, key=lambda p: str(p))
    return ordered


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Remove runtime artifacts for one task_id."
    )
    parser.add_argument("--task-id", required=True, help="Task id to clean.")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=repo_root / "runtime",
        help="Runtime root directory.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List candidate files without deleting them.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime_root = args.runtime_root.resolve()
    if not runtime_root.exists():
        print(f"ERROR: runtime root does not exist: {runtime_root}", file=sys.stderr)
        return 2

    paths = collect_task_paths(runtime_root, args.task_id)
    if not paths:
        print(f"No runtime artifacts found for task_id={args.task_id}")
        return 0

    mode = "DRY-RUN" if args.dry_run else "DELETE"
    print(f"{mode} task_id={args.task_id}")
    removed = 0
    for path in paths:
        print(str(path))
        if args.dry_run:
            continue
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            continue

    if args.dry_run:
        print(f"Dry-run complete. {len(paths)} file(s) matched.")
    else:
        print(f"Removed {removed} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
