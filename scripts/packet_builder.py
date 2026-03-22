#!/usr/bin/env python3
"""Batch 2 packet builder.

Reads a valid task_intake artifact and emits:
- manager_route
- execution_packet

Artifacts are written into runtime/packets and can be validated with scripts/pxml_validator.py.
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
from datetime import datetime, timedelta, timezone
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


NS = "urn:pxml:v1"
NSMAP = {None: NS}
XPATH_NS = {"p": NS}

LANE_FLAGS = {
    "direct": (False, False, False),
    "planner_pre": (True, False, False),
    "reviewer_post": (False, True, False),
    "verifier_post": (False, False, True),
    "full_lane": (True, True, True),
}

AMBIGUOUS_MARKERS = (
    "unclear",
    "ambiguous",
    "tbd",
    "to be decided",
    "to be determined",
    "?",
)

VERIFY_MARKERS = (
    "verify",
    "verification",
    "regression",
    "prove",
    "validation",
    "must pass tests",
)


def q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def now_utc_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_iso_or_now(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_sha256(value: Optional[str]) -> bool:
    if value is None:
        return False
    return re.fullmatch(r"[A-Fa-f0-9]{64}", value) is not None


def text_at(tree: etree._ElementTree, xpath_expr: str) -> Optional[str]:
    values = tree.xpath(xpath_expr, namespaces=XPATH_NS)
    if not values:
        return None
    node = values[0]
    if isinstance(node, etree._Element):
        text = node.text
    else:
        text = str(node)
    if text is None:
        return None
    text = text.strip()
    return text or None


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def sanitize_token(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9._-]", "_", lowered)
    lowered = re.sub(r"_+", "_", lowered).strip("_")
    return lowered or "id"


def make_doc_id(prefix: str, task_id: str, sequence: int) -> str:
    base = f"doc_{sanitize_token(prefix)}_{sanitize_token(task_id)}_{sequence:04d}"
    if len(base) > 64:
        base = base[:64]
    if re.fullmatch(r"doc_[a-z0-9][a-z0-9._-]{5,63}", base):
        return base
    fallback = f"doc_{sanitize_token(prefix)}_{sequence:04d}_aaaaaa"
    return fallback[:64]


def build_string_list(parent: etree._Element, tag: str, items: Sequence[str]) -> None:
    container = etree.SubElement(parent, q(tag))
    for item in items:
        node = etree.SubElement(container, q("item"))
        node.text = item


def acceptance_lock_hash(checks: Sequence[Dict[str, object]]) -> str:
    encoded = json.dumps(list(checks), sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return sha256_hex(encoded)


def compute_content_hash(
    meta: etree._Element, refs: Optional[etree._Element], payload: etree._Element
) -> str:
    material = etree.Element(q("hash_material"), nsmap=NSMAP)
    material.append(copy.deepcopy(meta))
    if refs is not None:
        material.append(copy.deepcopy(refs))
    material.append(copy.deepcopy(payload))
    content = etree.tostring(
        material, method="c14n", exclusive=True, with_comments=False
    )
    return sha256_hex(content)


def select_route(
    risk_hint: str, request_text: str, requested_outcome: str
) -> Tuple[str, str]:
    combined = f"{request_text} {requested_outcome}".lower()
    ambiguous = any(marker in combined for marker in AMBIGUOUS_MARKERS)
    verify_need = any(marker in combined for marker in VERIFY_MARKERS)

    if risk_hint == "critical":
        return "full_lane", "Critical risk task uses full lane."
    if ambiguous:
        return "planner_pre", "Ambiguity markers detected in intake text."
    if risk_hint == "high" and verify_need:
        return "full_lane", "High risk with explicit verification need uses full lane."
    if risk_hint == "high":
        return "reviewer_post", "High risk task requests reviewer post-lane."
    if verify_need:
        return "verifier_post", "Verification requirement detected from intake text."
    return "direct", "Clear low-complexity intake defaults to direct path."


def default_scope(
    task_type: str,
) -> Tuple[List[str], List[str], List[Tuple[str, str]], List[str]]:
    if task_type == "docs":
        in_scope = ["docs/"]
        out_scope = ["src/", "runtime/"]
        expected = [("docs/target_doc.md", "modify")]
        localization = []
    elif task_type == "ops":
        in_scope = ["ops/"]
        out_scope = ["src/", "docs/"]
        expected = [("ops/target_task.yml", "modify")]
        localization = []
    elif task_type == "feature":
        in_scope = ["src/"]
        out_scope = ["docs/", "runtime/"]
        expected = [("src/new_feature_module.py", "create")]
        localization = []
    elif task_type == "refactor":
        in_scope = ["src/"]
        out_scope = ["docs/", "runtime/"]
        expected = [("src/refactor_target.py", "modify")]
        localization = []
    else:
        in_scope = ["src/"]
        out_scope = ["docs/", "runtime/"]
        expected = [("src/target_bugfix.py", "modify")]
        localization = ["src/target_bugfix.py:target_function"]

    return in_scope, out_scope, expected, localization


def default_acceptance_checks(task_type: str) -> List[Dict[str, object]]:
    compile_cmd = (
        "python -m py_compile "
        "scripts/pxml_validator.py scripts/packet_builder.py scripts/trace_appender.py"
    )
    if task_type == "docs":
        command = compile_cmd
        check_type = "static_rule"
    else:
        command = compile_cmd
        check_type = "test"
    return [
        {
            "check_id": f"check_{task_type}_core_001",
            "check_type": check_type,
            "command": command,
            "pass_condition": "exit_code==0",
            "deterministic": True,
            "timeout_sec": 300,
        }
    ]


@dataclass
class IntakeData:
    path: Path
    doc_id: str
    task_id: str
    run_id: str
    sequence: int
    created_at: datetime
    request_text: str
    requested_outcome: str
    task_type: str
    risk_hint: str
    content_sha256: str


def read_intake(path: Path) -> IntakeData:
    tree = etree.parse(str(path))
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    if doc_class != "task_intake":
        raise ValueError(f"Input is not task_intake (got {doc_class!r})")

    doc_id = text_at(tree, "/p:pxml/p:meta/p:doc_id")
    task_id = text_at(tree, "/p:pxml/p:meta/p:task_id")
    run_id = text_at(tree, "/p:pxml/p:meta/p:run_id")
    seq_text = text_at(tree, "/p:pxml/p:meta/p:sequence")
    created_text = text_at(tree, "/p:pxml/p:meta/p:created_at")
    request_text = text_at(tree, "/p:pxml/p:payload/p:request_text")
    requested_outcome = text_at(tree, "/p:pxml/p:payload/p:requested_outcome")
    task_type = text_at(tree, "/p:pxml/p:payload/p:task_type")
    risk_hint = text_at(tree, "/p:pxml/p:payload/p:risk_hint")
    content_sha = text_at(tree, "/p:pxml/p:integrity/p:content_sha256")

    required = {
        "doc_id": doc_id,
        "task_id": task_id,
        "run_id": run_id,
        "sequence": seq_text,
        "created_at": created_text,
        "request_text": request_text,
        "requested_outcome": requested_outcome,
        "task_type": task_type,
        "risk_hint": risk_hint,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise ValueError(f"task_intake missing required fields: {', '.join(missing)}")

    assert seq_text is not None
    assert created_text is not None
    sequence = int(seq_text)
    created_at = parse_iso_or_now(created_text)

    if content_sha is not None and is_sha256(content_sha):
        intake_hash = content_sha
    else:
        intake_hash = sha256_hex(path.read_bytes())

    return IntakeData(
        path=path,
        doc_id=doc_id or "",
        task_id=task_id or "",
        run_id=run_id or "",
        sequence=sequence,
        created_at=created_at,
        request_text=request_text or "",
        requested_outcome=requested_outcome or "",
        task_type=task_type or "bugfix",
        risk_hint=risk_hint or "medium",
        content_sha256=intake_hash,
    )


def create_runtime_scaffold(runtime_root: Path) -> None:
    dirs = [
        runtime_root / "inbox" / "task_intake",
        runtime_root / "packets" / "manager_route",
        runtime_root / "packets" / "execution_packet",
        runtime_root / "traces" / "by_task",
        runtime_root / "latest",
        runtime_root / "index" / "tasks",
        runtime_root / "index" / "artifacts",
    ]
    for directory in dirs:
        ensure_dir(directory)


def build_manager_route(
    intake: IntakeData,
    route_doc_id: str,
    route_sequence: int,
    route_created_at: datetime,
    selected_path: str,
    route_reason: str,
    lock_hash: str,
) -> Tuple[etree._ElementTree, str]:
    planner_flag, reviewer_flag, verifier_flag = LANE_FLAGS[selected_path]

    root = etree.Element(q("pxml"), nsmap=NSMAP)
    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = route_doc_id
    etree.SubElement(meta, q("doc_class")).text = "manager_route"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = intake.task_id
    etree.SubElement(meta, q("run_id")).text = intake.run_id
    etree.SubElement(meta, q("sequence")).text = str(route_sequence)
    etree.SubElement(meta, q("writer_agent")).text = "manager"
    etree.SubElement(meta, q("created_at")).text = format_iso(route_created_at)

    refs = etree.SubElement(root, q("refs"))
    ref = etree.SubElement(refs, q("ref"))
    etree.SubElement(ref, q("doc_id")).text = intake.doc_id
    etree.SubElement(ref, q("doc_class")).text = "task_intake"
    etree.SubElement(ref, q("relation")).text = "intake"

    payload = etree.SubElement(root, q("payload"))
    etree.SubElement(payload, q("selected_path")).text = selected_path
    lane_flags = etree.SubElement(payload, q("lane_flags"))
    etree.SubElement(lane_flags, q("planner")).text = (
        "true" if planner_flag else "false"
    )
    etree.SubElement(lane_flags, q("reviewer")).text = (
        "true" if reviewer_flag else "false"
    )
    etree.SubElement(lane_flags, q("verifier")).text = (
        "true" if verifier_flag else "false"
    )
    etree.SubElement(payload, q("route_reason")).text = route_reason
    etree.SubElement(payload, q("risk_level")).text = intake.risk_hint

    lock = etree.SubElement(payload, q("acceptance_lock"))
    etree.SubElement(
        lock, q("lock_id")
    ).text = f"lock_{sanitize_token(intake.task_id)}_{route_sequence:04d}"
    etree.SubElement(lock, q("lock_sha256")).text = lock_hash
    etree.SubElement(lock, q("locked_at")).text = format_iso(route_created_at)

    integrity = etree.SubElement(root, q("integrity"))
    content_hash = compute_content_hash(meta, refs, payload)
    etree.SubElement(integrity, q("content_sha256")).text = content_hash
    etree.SubElement(integrity, q("parent_sha256")).text = intake.content_sha256

    return etree.ElementTree(root), content_hash


def build_execution_packet(
    intake: IntakeData,
    packet_doc_id: str,
    packet_sequence: int,
    packet_created_at: datetime,
    route_doc_id: str,
    route_hash: str,
    selected_path: str,
    route_reason: str,
    checks: Sequence[Dict[str, object]],
    lock_hash: str,
) -> Tuple[etree._ElementTree, str]:
    in_scope, out_scope, expected_files, localization_targets = default_scope(
        intake.task_type
    )

    root = etree.Element(q("pxml"), nsmap=NSMAP)
    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = packet_doc_id
    etree.SubElement(meta, q("doc_class")).text = "execution_packet"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = intake.task_id
    etree.SubElement(meta, q("run_id")).text = intake.run_id
    etree.SubElement(meta, q("sequence")).text = str(packet_sequence)
    etree.SubElement(meta, q("writer_agent")).text = "manager"
    etree.SubElement(meta, q("created_at")).text = format_iso(packet_created_at)

    refs = etree.SubElement(root, q("refs"))
    route_ref = etree.SubElement(refs, q("ref"))
    etree.SubElement(route_ref, q("doc_id")).text = route_doc_id
    etree.SubElement(route_ref, q("doc_class")).text = "manager_route"
    etree.SubElement(route_ref, q("relation")).text = "route"

    payload = etree.SubElement(root, q("payload"))
    summary = f"{intake.task_type} task via {selected_path}: {intake.request_text}"
    etree.SubElement(payload, q("task_summary")).text = summary

    build_string_list(payload, "in_scope", in_scope)
    build_string_list(payload, "out_of_scope", out_scope)

    expected = etree.SubElement(payload, q("expected_files"))
    for path, mode in expected_files:
        file_node = etree.SubElement(expected, q("file"))
        etree.SubElement(file_node, q("path")).text = path
        etree.SubElement(file_node, q("mode")).text = mode

    patch_constraints = etree.SubElement(payload, q("patch_constraints"))
    etree.SubElement(patch_constraints, q("patch_mode")).text = "patch_first"
    etree.SubElement(patch_constraints, q("max_files")).text = (
        "1" if intake.task_type == "bugfix" else "3"
    )
    etree.SubElement(patch_constraints, q("rewrite_exception_approved")).text = "false"

    acceptance_checks = etree.SubElement(payload, q("acceptance_checks"))
    for check in checks:
        check_node = etree.SubElement(acceptance_checks, q("check"))
        etree.SubElement(check_node, q("check_id")).text = str(check["check_id"])
        etree.SubElement(check_node, q("check_type")).text = str(check["check_type"])
        etree.SubElement(check_node, q("command")).text = str(check["command"])
        etree.SubElement(check_node, q("pass_condition")).text = str(
            check["pass_condition"]
        )
        etree.SubElement(check_node, q("deterministic")).text = (
            "true" if bool(check["deterministic"]) else "false"
        )
        etree.SubElement(check_node, q("timeout_sec")).text = str(check["timeout_sec"])

    etree.SubElement(payload, q("acceptance_lock_hash")).text = lock_hash

    build_string_list(
        payload,
        "test_guidance",
        [
            "Run acceptance checks in listed order.",
            "Escalate if deterministic check cannot execute.",
        ],
    )
    build_string_list(
        payload,
        "escalation_triggers",
        [
            "Out-of-scope change required for completion.",
            "Rewrite exception required without approval.",
            "Sidecar lane selected but unavailable in Batch 2 runtime loop.",
        ],
    )
    build_string_list(
        payload,
        "stop_conditions",
        [
            "All acceptance checks satisfy pass conditions.",
            "Escalation trigger is raised.",
            "Manager emits stop decision.",
        ],
    )

    if intake.task_type == "bugfix":
        if not localization_targets:
            localization_targets = ["src/target_bugfix.py:target_function"]
        build_string_list(payload, "localization_targets", localization_targets)

    integrity = etree.SubElement(root, q("integrity"))
    content_hash = compute_content_hash(meta, refs, payload)
    etree.SubElement(integrity, q("content_sha256")).text = content_hash
    etree.SubElement(integrity, q("parent_sha256")).text = route_hash

    _ = route_reason  # keep route_reason for deterministic builder evolution
    return etree.ElementTree(root), content_hash


def write_xml(tree: etree._ElementTree, path: Path) -> None:
    ensure_dir(path.parent)
    tree.write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True)


def update_indexes(
    runtime_root: Path,
    task_id: str,
    route_doc_id: str,
    route_path: Path,
    packet_doc_id: str,
    packet_path: Path,
) -> None:
    tasks_dir = runtime_root / "index" / "tasks"
    artifacts_dir = runtime_root / "index" / "artifacts"
    ensure_dir(tasks_dir)
    ensure_dir(artifacts_dir)

    task_index_path = tasks_dir / f"{sanitize_token(task_id)}.json"
    task_index = {
        "task_id": task_id,
        "latest_manager_route": str(route_path.relative_to(runtime_root)),
        "latest_execution_packet": str(packet_path.relative_to(runtime_root)),
        "updated_at": now_utc_iso(),
    }
    task_index_path.write_text(
        json.dumps(task_index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    route_index = {
        "doc_id": route_doc_id,
        "doc_class": "manager_route",
        "task_id": task_id,
        "path": str(route_path.relative_to(runtime_root)),
    }
    packet_index = {
        "doc_id": packet_doc_id,
        "doc_class": "execution_packet",
        "task_id": task_id,
        "path": str(packet_path.relative_to(runtime_root)),
    }
    (artifacts_dir / f"{route_doc_id}.json").write_text(
        json.dumps(route_index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (artifacts_dir / f"{packet_doc_id}.json").write_text(
        json.dumps(packet_index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_validation(
    validator_path: Path, artifact_path: Path, context_dir: Path
) -> None:
    command = [
        sys.executable,
        str(validator_path),
        str(artifact_path),
        "--context-dir",
        str(context_dir),
    ]
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Validation failed for {artifact_path}")


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Build manager_route and execution_packet from task_intake."
    )
    parser.add_argument(
        "--intake", required=True, type=Path, help="Path to task_intake PXML file."
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
        help="Validator script path.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip validator execution after artifact generation.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    intake_path = args.intake.resolve()
    runtime_root = args.runtime_root.resolve()
    validator_path = args.validator.resolve()

    if not intake_path.exists():
        print(f"ERROR: intake file not found: {intake_path}", file=sys.stderr)
        return 2

    try:
        intake = read_intake(intake_path)
    except Exception as exc:
        print(f"ERROR: failed to parse task_intake: {exc}", file=sys.stderr)
        return 2

    create_runtime_scaffold(runtime_root)

    inbox_intake_path = runtime_root / "inbox" / "task_intake" / f"{intake.doc_id}.pxml"
    shutil.copy2(intake_path, inbox_intake_path)

    selected_path, route_reason = select_route(
        risk_hint=intake.risk_hint,
        request_text=intake.request_text,
        requested_outcome=intake.requested_outcome,
    )

    checks = default_acceptance_checks(intake.task_type)
    lock_hash = acceptance_lock_hash(checks)

    route_sequence = intake.sequence + 1
    packet_sequence = intake.sequence + 2
    route_doc_id = make_doc_id("manager_route", intake.task_id, route_sequence)
    packet_doc_id = make_doc_id("execution_packet", intake.task_id, packet_sequence)
    route_created_at = intake.created_at + timedelta(seconds=1)
    packet_created_at = intake.created_at + timedelta(seconds=2)

    route_tree, route_hash = build_manager_route(
        intake=intake,
        route_doc_id=route_doc_id,
        route_sequence=route_sequence,
        route_created_at=route_created_at,
        selected_path=selected_path,
        route_reason=route_reason,
        lock_hash=lock_hash,
    )
    packet_tree, _packet_hash = build_execution_packet(
        intake=intake,
        packet_doc_id=packet_doc_id,
        packet_sequence=packet_sequence,
        packet_created_at=packet_created_at,
        route_doc_id=route_doc_id,
        route_hash=route_hash,
        selected_path=selected_path,
        route_reason=route_reason,
        checks=checks,
        lock_hash=lock_hash,
    )

    route_path = runtime_root / "packets" / "manager_route" / f"{route_doc_id}.pxml"
    packet_path = (
        runtime_root / "packets" / "execution_packet" / f"{packet_doc_id}.pxml"
    )
    write_xml(route_tree, route_path)
    write_xml(packet_tree, packet_path)

    latest_task = sanitize_token(intake.task_id)
    latest_route_path = runtime_root / "latest" / f"{latest_task}_manager_route.pxml"
    latest_packet_path = (
        runtime_root / "latest" / f"{latest_task}_execution_packet.pxml"
    )
    shutil.copy2(route_path, latest_route_path)
    shutil.copy2(packet_path, latest_packet_path)

    update_indexes(
        runtime_root=runtime_root,
        task_id=intake.task_id,
        route_doc_id=route_doc_id,
        route_path=route_path,
        packet_doc_id=packet_doc_id,
        packet_path=packet_path,
    )

    if not args.skip_validate:
        if not validator_path.exists():
            print(
                f"ERROR: validator script not found: {validator_path}",
                file=sys.stderr,
            )
            return 2
        try:
            with tempfile.TemporaryDirectory(
                prefix="pxml_batch2_validate_"
            ) as temp_dir:
                temp_root = Path(temp_dir)
                intake_for_validation = temp_root / f"{intake.doc_id}.pxml"
                route_for_validation = temp_root / f"{route_doc_id}.pxml"
                packet_for_validation = temp_root / f"{packet_doc_id}.pxml"
                shutil.copy2(inbox_intake_path, intake_for_validation)
                shutil.copy2(route_path, route_for_validation)
                shutil.copy2(packet_path, packet_for_validation)
                run_validation(validator_path, route_for_validation, temp_root)
                run_validation(validator_path, packet_for_validation, temp_root)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    print(f"Generated manager_route: {route_path}")
    print(f"Generated execution_packet: {packet_path}")
    print(f"Routing decision: {selected_path}")
    print(f"Acceptance lock hash: {lock_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
