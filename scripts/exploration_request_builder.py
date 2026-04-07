#!/usr/bin/env python3
"""Manager-authored focused exploration request publisher."""

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
from typing import Dict, List, Optional, Sequence, Tuple

try:
    from lxml import etree
except ModuleNotFoundError:
    print(
        "ERROR: lxml is required. Install with: python -m pip install lxml",
        file=sys.stderr,
    )
    raise SystemExit(3)

from exploration_guards import collect_guard_errors, normalize_items


NS = "urn:pxml:v1"
NSMAP = {None: NS}
XPATH_NS = {"p": NS}
REQUEST_CONTEXT_CLASSES = {"plan_sidecar", "implementer_result", "verification_result"}


@dataclass
class ArtifactInfo:
    path: Path
    doc_id: str
    doc_class: str
    task_id: str
    run_id: str
    sequence: int
    content_sha256: str


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


def text_at(tree: etree._ElementTree, expr: str) -> Optional[str]:
    values = tree.xpath(expr, namespaces=XPATH_NS)
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


def parse_artifact(path: Path, expected_class: Optional[str] = None) -> ArtifactInfo:
    tree = etree.parse(str(path))
    doc_id = text_at(tree, "/p:pxml/p:meta/p:doc_id")
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    task_id = text_at(tree, "/p:pxml/p:meta/p:task_id")
    run_id = text_at(tree, "/p:pxml/p:meta/p:run_id")
    sequence_text = text_at(tree, "/p:pxml/p:meta/p:sequence")
    content_sha256 = text_at(tree, "/p:pxml/p:integrity/p:content_sha256")
    required = {
        "doc_id": doc_id,
        "doc_class": doc_class,
        "task_id": task_id,
        "run_id": run_id,
        "sequence": sequence_text,
        "content_sha256": content_sha256,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise ValueError(
            f"artifact missing required fields: {', '.join(sorted(missing))}"
        )
    if expected_class and doc_class != expected_class:
        raise ValueError(f"expected doc_class={expected_class}, got {doc_class}")
    return ArtifactInfo(
        path=path,
        doc_id=doc_id or "",
        doc_class=doc_class or "",
        task_id=task_id or "",
        run_id=run_id or "",
        sequence=int(sequence_text or "0"),
        content_sha256=content_sha256 or "",
    )


def latest_route_for_task(runtime_root: Path, task_id: str) -> Optional[ArtifactInfo]:
    latest_path = runtime_root / "latest" / f"{sanitize(task_id)}_manager_route.pxml"
    if not latest_path.exists():
        return None
    return parse_artifact(latest_path, expected_class="manager_route")


def discover_pxml_files(path: Path) -> List[Path]:
    if not path.exists():
        return []
    files = [candidate for candidate in path.rglob("*.pxml") if candidate.is_file()]
    files.sort()
    return files


def next_sequence(runtime_root: Path, task_id: str) -> int:
    max_sequence = 0
    for path in discover_pxml_files(runtime_root):
        try:
            artifact = parse_artifact(path)
        except Exception:
            continue
        if artifact.task_id != task_id:
            continue
        max_sequence = max(max_sequence, artifact.sequence)
    return max_sequence + 1


def build_request_tree(
    packet: ArtifactInfo,
    baseline: ArtifactInfo,
    route: Optional[ArtifactInfo],
    request_context: Optional[ArtifactInfo],
    doc_id: str,
    sequence: int,
    requester_agent: str,
    request_kind: str,
    blocking: bool,
    reason_code: str,
    focus_questions: Sequence[str],
    target_hints: Sequence[str],
    contract_change_suspected: bool,
) -> etree._ElementTree:
    root = etree.Element(q("pxml"), nsmap=NSMAP)

    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = doc_id
    etree.SubElement(meta, q("doc_class")).text = "exploration_request"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = packet.task_id
    etree.SubElement(meta, q("run_id")).text = packet.run_id
    etree.SubElement(meta, q("sequence")).text = str(sequence)
    etree.SubElement(meta, q("writer_agent")).text = "manager"
    etree.SubElement(meta, q("created_at")).text = now_iso()

    refs = etree.SubElement(root, q("refs"))
    packet_ref = etree.SubElement(refs, q("ref"))
    etree.SubElement(packet_ref, q("doc_id")).text = packet.doc_id
    etree.SubElement(packet_ref, q("doc_class")).text = "execution_packet"
    etree.SubElement(packet_ref, q("relation")).text = "request_packet"

    baseline_ref = etree.SubElement(refs, q("ref"))
    etree.SubElement(baseline_ref, q("doc_id")).text = baseline.doc_id
    etree.SubElement(baseline_ref, q("doc_class")).text = "exploration_result"
    etree.SubElement(baseline_ref, q("relation")).text = "baseline_context"

    if route is not None:
        route_ref = etree.SubElement(refs, q("ref"))
        etree.SubElement(route_ref, q("doc_id")).text = route.doc_id
        etree.SubElement(route_ref, q("doc_class")).text = "manager_route"
        etree.SubElement(route_ref, q("relation")).text = "route_context"

    if request_context is not None:
        request_ref = etree.SubElement(refs, q("ref"))
        etree.SubElement(request_ref, q("doc_id")).text = request_context.doc_id
        etree.SubElement(request_ref, q("doc_class")).text = request_context.doc_class
        etree.SubElement(request_ref, q("relation")).text = "request_context"

    payload = etree.SubElement(root, q("payload"))
    etree.SubElement(payload, q("requester_agent")).text = requester_agent
    etree.SubElement(payload, q("request_kind")).text = request_kind
    etree.SubElement(payload, q("blocking")).text = "true" if blocking else "false"
    etree.SubElement(payload, q("reason_code")).text = reason_code
    questions_node = etree.SubElement(payload, q("focus_questions"))
    for item in focus_questions:
        etree.SubElement(questions_node, q("item")).text = item
    hints_node = etree.SubElement(payload, q("target_hints"))
    for item in target_hints:
        etree.SubElement(hints_node, q("item")).text = item
    etree.SubElement(payload, q("contract_change_suspected")).text = (
        "true" if contract_change_suspected else "false"
    )

    integrity = etree.SubElement(root, q("integrity"))
    etree.SubElement(integrity, q("content_sha256")).text = compute_content_hash(
        meta, refs, payload
    )
    etree.SubElement(integrity, q("parent_sha256")).text = packet.content_sha256

    return etree.ElementTree(root)


def write_xml(tree: etree._ElementTree, path: Path) -> None:
    ensure_dir(path.parent)
    tree.write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True)


def update_indexes(
    runtime_root: Path, task_id: str, doc_id: str, result_path: Path
) -> None:
    task_index_dir = runtime_root / "index" / "tasks"
    artifact_index_dir = runtime_root / "index" / "artifacts"
    ensure_dir(task_index_dir)
    ensure_dir(artifact_index_dir)

    task_index_path = task_index_dir / f"{sanitize(task_id)}.json"
    current: Dict[str, object] = {}
    if task_index_path.exists():
        try:
            current = json.loads(task_index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            current = {}
    current["task_id"] = task_id
    current["latest_exploration_request"] = str(result_path.relative_to(runtime_root))
    current["updated_at"] = now_iso()
    task_index_path.write_text(
        json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    artifact_index = {
        "doc_id": doc_id,
        "doc_class": "exploration_request",
        "task_id": task_id,
        "path": str(result_path.relative_to(runtime_root)),
        "updated_at": now_iso(),
    }
    (artifact_index_dir / f"{doc_id}.json").write_text(
        json.dumps(artifact_index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_validation(
    validator: Path, request_path: Path, context_files: Sequence[Path]
) -> None:
    with tempfile.TemporaryDirectory(
        prefix="pxml_explore_request_validate_"
    ) as temp_dir:
        temp_root = Path(temp_dir)
        copied_request = temp_root / request_path.name
        shutil.copy2(request_path, copied_request)
        for file_path in context_files:
            if not file_path.exists():
                continue
            shutil.copy2(file_path, temp_root / file_path.name)
        command = [
            sys.executable,
            str(validator),
            str(copied_request),
            "--context-dir",
            str(temp_root),
        ]
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Validation failed for {request_path}")


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Publish a manager-authored focused exploration request."
    )
    parser.add_argument(
        "--packet", required=True, type=Path, help="Execution packet path."
    )
    parser.add_argument(
        "--baseline-exploration",
        required=True,
        type=Path,
        help="Baseline exploration_result path.",
    )
    parser.add_argument(
        "--requester-agent",
        required=True,
        choices=["manager", "planner", "implementer", "verifier"],
        help="Agent on whose behalf the request is being published.",
    )
    parser.add_argument(
        "--request-kind",
        required=True,
        choices=[
            "symbol_reference_trace",
            "ownership_trace",
            "impact_boundary",
            "test_discovery",
            "config_lookup",
            "external_api_doc",
            "repro_context",
            "design_constraints",
        ],
        help="Focused request kind.",
    )
    parser.add_argument("--reason-code", required=True, help="Request reason code.")
    parser.add_argument(
        "--focus-question",
        action="append",
        default=[],
        help="Focus question. Repeat up to three times.",
    )
    parser.add_argument(
        "--target-hint",
        action="append",
        default=[],
        help="Target hint. Repeat up to five times.",
    )
    parser.add_argument(
        "--route",
        type=Path,
        default=None,
        help="Optional manager_route path. Defaults to latest route for the task if present.",
    )
    parser.add_argument(
        "--request-context",
        type=Path,
        default=None,
        help="Optional plan_sidecar/implementer_result/verification_result path.",
    )
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
        "--blocking",
        action="store_true",
        help="Mark the request as blocking the current lane.",
    )
    parser.add_argument(
        "--contract-change-suspected",
        action="store_true",
        help="Set contract_change_suspected=true.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip validator execution for the generated exploration_request.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime_root = args.runtime_root.resolve()
    packet_path = args.packet.resolve()
    baseline_path = args.baseline_exploration.resolve()
    validator_path = args.validator.resolve()

    if not packet_path.exists():
        print(f"ERROR: execution_packet not found: {packet_path}", file=sys.stderr)
        return 2
    if not baseline_path.exists():
        print(
            f"ERROR: baseline exploration_result not found: {baseline_path}",
            file=sys.stderr,
        )
        return 2

    try:
        packet = parse_artifact(packet_path, expected_class="execution_packet")
        baseline = parse_artifact(baseline_path, expected_class="exploration_result")
    except Exception as exc:
        print(f"ERROR: failed to parse context artifacts: {exc}", file=sys.stderr)
        return 2

    if packet.task_id != baseline.task_id:
        print(
            "ERROR: baseline exploration_result task_id does not match execution_packet task_id",
            file=sys.stderr,
        )
        return 2

    route: Optional[ArtifactInfo] = None
    if args.route is not None:
        try:
            route = parse_artifact(args.route.resolve(), expected_class="manager_route")
        except Exception as exc:
            print(f"ERROR: failed to parse manager_route: {exc}", file=sys.stderr)
            return 2
    else:
        route = latest_route_for_task(runtime_root, packet.task_id)

    request_context: Optional[ArtifactInfo] = None
    if args.request_context is not None:
        try:
            request_context = parse_artifact(args.request_context.resolve())
        except Exception as exc:
            print(
                f"ERROR: failed to parse request context artifact: {exc}",
                file=sys.stderr,
            )
            return 2
        if request_context.doc_class not in REQUEST_CONTEXT_CLASSES:
            print(
                "ERROR: request_context must be one of plan_sidecar/implementer_result/verification_result",
                file=sys.stderr,
            )
            return 2

    focus_questions = normalize_items(args.focus_question)
    target_hints = normalize_items(args.target_hint)
    errors, _dedupe_key = collect_guard_errors(
        runtime_root=runtime_root,
        task_id=packet.task_id,
        packet_doc_id=packet.doc_id,
        requester_agent=args.requester_agent,
        request_kind=args.request_kind,
        focus_questions=focus_questions,
        target_hints=target_hints,
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    requests_dir = runtime_root / "exploration" / "requests"
    ensure_dir(requests_dir)
    sequence = next_sequence(runtime_root, packet.task_id)
    doc_id = f"doc_exploration_request_{sanitize(packet.task_id)[:18]}_{sequence:04d}"
    if not re.fullmatch(r"doc_[a-z0-9][a-z0-9._-]{5,63}", doc_id):
        doc_id = (
            f"doc_exploration_request_{sequence:04d}_"
            f"{sha256_hex(packet.task_id.encode('utf-8'))[:8]}"
        )

    tree = build_request_tree(
        packet=packet,
        baseline=baseline,
        route=route,
        request_context=request_context,
        doc_id=doc_id,
        sequence=sequence,
        requester_agent=args.requester_agent,
        request_kind=args.request_kind,
        blocking=args.blocking,
        reason_code=args.reason_code,
        focus_questions=focus_questions,
        target_hints=target_hints,
        contract_change_suspected=args.contract_change_suspected,
    )
    request_path = requests_dir / f"{doc_id}.pxml"
    publish_source = request_path

    if not args.skip_validate:
        if not validator_path.exists():
            print(f"ERROR: validator not found: {validator_path}", file=sys.stderr)
            return 2
        with tempfile.TemporaryDirectory(
            prefix="pxml_explore_request_publish_"
        ) as temp_dir:
            draft_path = Path(temp_dir) / request_path.name
            write_xml(tree, draft_path)
            context_files: List[Path] = [packet.path, baseline.path]
            if route is not None:
                context_files.append(route.path)
            if request_context is not None:
                context_files.append(request_context.path)
            try:
                run_validation(validator_path, draft_path, context_files)
            except RuntimeError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 1
            shutil.copy2(draft_path, request_path)
            publish_source = request_path
    else:
        write_xml(tree, request_path)

    latest_path = (
        runtime_root / "latest" / f"{sanitize(packet.task_id)}_exploration_request.pxml"
    )
    ensure_dir(latest_path.parent)
    shutil.copy2(publish_source, latest_path)
    update_indexes(runtime_root, packet.task_id, doc_id, request_path)

    print(f"Generated exploration_request: {request_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
