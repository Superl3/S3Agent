#!/usr/bin/env python3
"""Batch 9 operator runbook thin wrapper.

Runs cleanup -> task_executor -> operator_preflight -> final_renderer in sequence,
then emits derived session_report for operator handoff visibility.
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
class PolicyConfig:
    rule_decisions: Dict[str, str]


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
    directory: Path,
    doc_class: str,
    task_id: str,
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


def parse_task_id_from_intake(path: Path) -> str:
    tree = etree.parse(str(path))
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    if doc_class != "task_intake":
        raise ValueError(f"Input artifact must be task_intake (got {doc_class!r})")
    task_id = text_at(tree, "/p:pxml/p:meta/p:task_id")
    if task_id is None:
        raise ValueError("task_intake is missing meta/task_id")
    return task_id


def run_command(command: List[str], stage: str) -> subprocess.CompletedProcess[str]:
    print(f"[operator_runbook] stage={stage}")
    print("[operator_runbook] cmd=" + " ".join(command))
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        print(result.stderr.strip(), file=sys.stderr)
    print(f"[operator_runbook] stage={stage} exit_code={result.returncode}")
    return result


def load_policy(path: Path) -> PolicyConfig:
    if not path.exists():
        raise ValueError(f"operator runbook policy not found: {path}")
    tree = etree.parse(str(path))
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    if doc_class != "operator_runbook_policy":
        raise ValueError(f"invalid policy doc_class: {doc_class}")

    names = tree.xpath(
        "/p:pxml/p:payload/p:rules/p:rule/p:rule_name/text()",
        namespaces=XPATH_NS,
    )
    decisions = tree.xpath(
        "/p:pxml/p:payload/p:rules/p:rule/p:decision/text()",
        namespaces=XPATH_NS,
    )
    mapping: Dict[str, str] = {}
    for name, decision in zip(names, decisions):
        key = name.strip()
        value = decision.strip()
        if key and value:
            mapping[key] = value
    return PolicyConfig(rule_decisions=mapping)


def policy_decision(policy: PolicyConfig, rule_name: str, default: str) -> str:
    return policy.rule_decisions.get(rule_name, default)


def load_quarantine_refs(runtime_root: Path, task_id: str) -> List[str]:
    manifest_dir = runtime_root / "quarantine" / "manifests"
    if not manifest_dir.exists():
        return []
    refs: List[str] = []
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
            if entry.get("task_id") == task_id:
                refs.append(str(path.relative_to(runtime_root)).replace("\\", "/"))
                break
    refs = sorted(set(refs))
    return refs


def run_validation(
    validator: Path,
    report_path: Path,
    context_files: Sequence[Path],
) -> None:
    with tempfile.TemporaryDirectory(prefix="pxml_runbook_validate_") as temp_dir:
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


def cleanup_preserved_intake(path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except (FileNotFoundError, OSError):
        return


def build_ref(parent: etree._Element, info: ArtifactInfo, relation: str) -> None:
    ref = etree.SubElement(parent, q("ref"))
    etree.SubElement(ref, q("doc_id")).text = info.doc_id
    etree.SubElement(ref, q("doc_class")).text = info.doc_class
    etree.SubElement(ref, q("relation")).text = relation


def build_ref_node(
    parent: etree._Element,
    tag_name: str,
    info: ArtifactInfo,
    relation: str,
) -> None:
    node = etree.SubElement(parent, q(tag_name))
    etree.SubElement(node, q("doc_id")).text = info.doc_id
    etree.SubElement(node, q("doc_class")).text = info.doc_class
    etree.SubElement(node, q("relation")).text = relation


def latest_snapshot(
    runtime_root: Path, task_id: str
) -> Dict[str, Optional[ArtifactInfo]]:
    return {
        "task_intake": latest_for_task(
            runtime_root / "inbox" / "task_intake", "task_intake", task_id
        ),
        "manager_route": latest_for_task(
            runtime_root / "packets" / "manager_route", "manager_route", task_id
        ),
        "execution_packet": latest_for_task(
            runtime_root / "packets" / "execution_packet", "execution_packet", task_id
        ),
        "task_status_report": latest_for_task(
            runtime_root / "status" / "reports", "task_status_report", task_id
        ),
        "operator_preflight_report": latest_for_task(
            runtime_root / "preflight" / "reports", "operator_preflight_report", task_id
        ),
        "final_render_report": latest_for_task(
            runtime_root / "rendered" / "reports", "final_render_report", task_id
        ),
        "execution_trace": latest_for_task(
            runtime_root / "traces" / "by_task", "execution_trace", task_id
        ),
        "verification_result": latest_for_task(
            runtime_root / "verification" / "results", "verification_result", task_id
        ),
        "compaction_checkpoint": latest_for_task(
            runtime_root / "compaction" / "checkpoints",
            "compaction_checkpoint",
            task_id,
        ),
    }


def resolve_intake(
    runtime_root: Path,
    intake_path: Optional[Path],
    task_id: Optional[str],
) -> Tuple[str, Path]:
    if intake_path is not None:
        resolved = intake_path.resolve()
        if not resolved.exists():
            raise ValueError(f"intake file not found: {resolved}")
        intake_task_id = parse_task_id_from_intake(resolved)
        if task_id is not None and task_id != intake_task_id:
            raise ValueError(
                "--task-id does not match intake meta/task_id "
                f"({task_id} != {intake_task_id})"
            )
        return intake_task_id, resolved

    if task_id is None:
        raise ValueError("Provide either --intake or --task-id")

    latest_intake = latest_for_task(
        runtime_root / "inbox" / "task_intake", "task_intake", task_id
    )
    if latest_intake is None:
        raise ValueError(
            "--task-id was provided but no runtime task_intake artifact exists; "
            "pass --intake for a fresh run"
        )
    return task_id, latest_intake.path


def read_preflight_readiness(
    preflight_info: Optional[ArtifactInfo],
) -> Tuple[Optional[str], Optional[str]]:
    if preflight_info is None:
        return None, None
    readiness = text_at(preflight_info.tree, "/p:pxml/p:payload/p:render_readiness")
    next_action = text_at(preflight_info.tree, "/p:pxml/p:payload/p:next_action")
    return readiness, next_action


def read_status_values(
    status_info: Optional[ArtifactInfo],
) -> Tuple[Optional[str], Optional[str]]:
    if status_info is None:
        return None, None
    status = text_at(status_info.tree, "/p:pxml/p:payload/p:current_status")
    next_action = text_at(
        status_info.tree,
        "/p:pxml/p:payload/p:next_recommended_action",
    )
    return status, next_action


def read_render_mode(render_info: Optional[ArtifactInfo]) -> Optional[str]:
    if render_info is None:
        return None
    return text_at(render_info.tree, "/p:pxml/p:payload/p:render_mode")


def derive_release_readiness(
    preflight_readiness: Optional[str],
    status_value: Optional[str],
    render_decision: str,
    task_executor_exit: int,
    harness_exit: Optional[int],
    quarantine_refs: Sequence[str],
) -> str:
    if preflight_readiness is None:
        return "fail"

    if preflight_readiness == "not_ready":
        return "fail"

    if status_value in {"failed", "retry_failed", "escalated"}:
        return "fail"

    if status_value in {"blocked", "running", "pending", "inconclusive", "no_op"}:
        return "inconclusive"

    if quarantine_refs:
        return "inconclusive"

    if preflight_readiness == "caution":
        return "inconclusive"

    if preflight_readiness == "ready":
        if task_executor_exit != 0:
            return "fail"
        if render_decision not in {"rendered", "rendered_with_warning"}:
            return "fail"
        if harness_exit == 1:
            return "fail"
        if harness_exit == 2:
            return "inconclusive"
        return "pass"

    return "inconclusive"


def derive_runbook_result(
    task_executor_exit: int,
    preflight_exit: int,
    renderer_exit: Optional[int],
    status_value: Optional[str],
    preflight_readiness: Optional[str],
    render_decision: str,
    release_readiness: str,
) -> str:
    if preflight_exit != 0:
        return "failed"

    if preflight_readiness == "not_ready" or status_value in {
        "blocked",
        "retry_failed",
        "escalated",
    }:
        return "blocked"

    if task_executor_exit != 0 and status_value not in {
        "blocked",
        "retry_failed",
        "escalated",
    }:
        return "failed"

    if release_readiness == "pass":
        return "success"

    if render_decision == "denied" and preflight_readiness == "ready":
        return "failed"

    if (
        renderer_exit is not None
        and renderer_exit != 0
        and preflight_readiness == "ready"
    ):
        return "failed"

    return "partial"


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Run operator thin wrapper flow and emit session_report."
    )
    parser.add_argument(
        "--intake",
        type=Path,
        default=None,
        help="Task intake PXML path.",
    )
    parser.add_argument(
        "--task-id",
        default=None,
        help="Target task id; may reuse latest runtime task_intake.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Run cleanup_task_runtime before execution.",
    )
    parser.add_argument(
        "--run-harness",
        action="store_true",
        help="Run harness_validator after render stage.",
    )
    parser.add_argument(
        "--allow-caution-render",
        action="store_true",
        help="Allow caution render path via final_renderer --allow-caution.",
    )
    parser.set_defaults(deny_not_ready_override=True)
    parser.add_argument(
        "--deny-not-ready-override",
        dest="deny_not_ready_override",
        action="store_true",
        help="Deny not_ready override (default safe mode).",
    )
    parser.add_argument(
        "--allow-not-ready-override",
        dest="deny_not_ready_override",
        action="store_false",
        help="Allow explicit not_ready override for final_renderer.",
    )
    parser.add_argument(
        "--harness-release-readiness",
        action="store_true",
        help="Run harness_validator with --release-readiness.",
    )
    parser.add_argument(
        "--verify-policy",
        choices=["auto", "always", "never"],
        default="auto",
        help="Pass-through task_executor verification policy.",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=repo_root / "runtime",
        help="Runtime root directory.",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=repo_root,
        help="Workspace root for implementer execution.",
    )
    parser.add_argument(
        "--validator",
        type=Path,
        default=repo_root / "scripts" / "pxml_validator.py",
        help="PXML validator path.",
    )
    parser.add_argument(
        "--runbook-policy",
        type=Path,
        default=repo_root / "instructions" / "operator_runbook_policy.pxml",
        help="Operator runbook policy artifact path.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip downstream validation calls.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    runtime_root = args.runtime_root.resolve()
    workspace_root = args.workspace_root.resolve()
    validator = args.validator.resolve()

    cleanup_script = repo_root / "scripts" / "cleanup_task_runtime.py"
    task_executor_script = repo_root / "scripts" / "task_executor.py"
    preflight_script = repo_root / "scripts" / "operator_preflight.py"
    renderer_script = repo_root / "scripts" / "final_renderer.py"
    harness_script = repo_root / "scripts" / "harness_validator.py"

    if not runtime_root.exists():
        print(f"ERROR: runtime root not found: {runtime_root}", file=sys.stderr)
        return 2
    if not workspace_root.exists():
        print(f"ERROR: workspace root not found: {workspace_root}", file=sys.stderr)
        return 2

    try:
        policy = load_policy(args.runbook_policy.resolve())
    except Exception as exc:
        print(f"ERROR: failed to load operator runbook policy: {exc}", file=sys.stderr)
        return 2

    try:
        task_id, intake_path = resolve_intake(runtime_root, args.intake, args.task_id)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    preserved_intake: Optional[Path] = None

    # When task-id mode is used with cleanup, preserve intake outside runtime
    # because cleanup removes runtime/inbox/task_intake artifacts.
    if args.intake is None and args.clean:
        with tempfile.NamedTemporaryFile(
            prefix="operator_runbook_intake_",
            suffix=".pxml",
            delete=False,
        ) as handle:
            preserved_intake = Path(handle.name)
        shutil.copy2(intake_path, preserved_intake)
        intake_path = preserved_intake

    runbook_start = now_iso()
    warnings: List[str] = []

    cleanup_exit: Optional[int] = None
    if args.clean:
        cleanup_cmd = [
            sys.executable,
            str(cleanup_script),
            "--task-id",
            task_id,
            "--runtime-root",
            str(runtime_root),
        ]
        cleanup_run = run_command(cleanup_cmd, "cleanup")
        cleanup_exit = cleanup_run.returncode
        if cleanup_run.returncode != 0:
            warnings.append(f"cleanup_exit_nonzero:{cleanup_run.returncode}")

    task_executor_cmd = [
        sys.executable,
        str(task_executor_script),
        "--intake",
        str(intake_path),
        "--task-id",
        task_id,
        "--runtime-root",
        str(runtime_root),
        "--workspace-root",
        str(workspace_root),
        "--verify-policy",
        args.verify_policy,
        "--allow-no-op",
    ]
    if args.skip_validate:
        task_executor_cmd.append("--skip-validate")
    task_executor_run = run_command(task_executor_cmd, "task_executor")
    task_executor_exit = task_executor_run.returncode
    if task_executor_exit != 0:
        warnings.append(f"task_executor_exit_nonzero:{task_executor_exit}")

    snapshot = latest_snapshot(runtime_root, task_id)

    preflight_required = (
        policy_decision(policy, "preflight_required_before_render", "deny") == "deny"
    )
    preflight_exit = 1
    preflight_run: Optional[subprocess.CompletedProcess[str]] = None
    if preflight_required:
        preflight_cmd = [
            sys.executable,
            str(preflight_script),
            "--task-id",
            task_id,
            "--runtime-root",
            str(runtime_root),
        ]
        if args.skip_validate:
            preflight_cmd.append("--skip-validate")
        preflight_run = run_command(preflight_cmd, "operator_preflight")
        preflight_exit = preflight_run.returncode
        if preflight_exit != 0:
            warnings.append(f"preflight_exit_nonzero:{preflight_exit}")
    else:
        warnings.append("preflight_not_required_by_policy")

    snapshot = latest_snapshot(runtime_root, task_id)
    preflight_info = snapshot["operator_preflight_report"]
    preflight_readiness, preflight_next_action = read_preflight_readiness(
        preflight_info
    )

    renderer_exit: Optional[int] = None
    render_decision = "skipped"
    render_override_used = False

    if preflight_info is None:
        warnings.append("missing_operator_preflight_report")
    elif preflight_exit != 0:
        warnings.append("preflight_stage_failed_render_skipped")
    else:
        allow_caution_by_policy = (
            policy_decision(
                policy, "allow_caution_render_with_override", "allow_with_override"
            )
            == "allow_with_override"
        )
        allow_not_ready_override = not args.deny_not_ready_override
        renderer_cmd = [
            sys.executable,
            str(renderer_script),
            "--task-id",
            task_id,
            "--runtime-root",
            str(runtime_root),
        ]

        if preflight_readiness == "caution" and args.allow_caution_render:
            if allow_caution_by_policy:
                renderer_cmd.append("--allow-caution")
                render_override_used = True
            else:
                warnings.append("caution_override_blocked_by_policy")
        if preflight_readiness == "not_ready" and allow_not_ready_override:
            renderer_cmd.append("--override-not-ready")
            render_override_used = True
        if args.skip_validate:
            renderer_cmd.append("--skip-validate")

        renderer_run = run_command(renderer_cmd, "final_renderer")
        renderer_exit = renderer_run.returncode
        if renderer_exit != 0:
            warnings.append(f"renderer_exit_nonzero:{renderer_exit}")

    snapshot = latest_snapshot(runtime_root, task_id)
    render_info = snapshot["final_render_report"]
    render_mode = read_render_mode(render_info)
    if render_mode in {"rendered", "rendered_with_warning", "denied"}:
        render_decision = render_mode
    elif renderer_exit is None:
        render_decision = "skipped"
    elif renderer_exit == 0:
        render_decision = "rendered"
    else:
        render_decision = "denied"

    harness_exit: Optional[int] = None
    if args.run_harness:
        harness_cmd = [
            sys.executable,
            str(harness_script),
            "--task-id",
            task_id,
            "--runtime-root",
            str(runtime_root),
        ]
        if args.harness_release_readiness:
            harness_cmd.append("--release-readiness")
        harness_run = run_command(harness_cmd, "harness_validator")
        harness_exit = harness_run.returncode
        if harness_exit not in {0, 2}:
            warnings.append(f"harness_exit_nonzero:{harness_exit}")

    snapshot = latest_snapshot(runtime_root, task_id)
    intake_info = snapshot["task_intake"]
    if intake_info is None:
        fallback = parse_artifact(intake_path)
        if fallback is not None and fallback.task_id == task_id:
            intake_info = fallback

    route_info = snapshot["manager_route"]
    packet_info = snapshot["execution_packet"]
    status_info = snapshot["task_status_report"]
    preflight_info = snapshot["operator_preflight_report"]
    render_info = snapshot["final_render_report"]
    trace_info = snapshot["execution_trace"]
    verification_info = snapshot["verification_result"]
    compaction_info = snapshot["compaction_checkpoint"]

    if (
        intake_info is None
        or route_info is None
        or packet_info is None
        or status_info is None
    ):
        print(
            "ERROR: missing required source artifacts for session report "
            "(task_intake/manager_route/execution_packet/task_status_report)",
            file=sys.stderr,
        )
        cleanup_preserved_intake(preserved_intake)
        return 1
    if preflight_info is None or trace_info is None:
        print(
            "ERROR: missing required preflight/trace artifacts for session report",
            file=sys.stderr,
        )
        cleanup_preserved_intake(preserved_intake)
        return 1

    status_value, status_next_action = read_status_values(status_info)
    preflight_readiness, preflight_next_action = read_preflight_readiness(
        preflight_info
    )
    quarantine_refs = load_quarantine_refs(runtime_root, task_id)

    if preflight_readiness == "caution":
        warnings.append("preflight_readiness_caution")
    if preflight_readiness == "not_ready":
        warnings.append("preflight_readiness_not_ready")
    if render_override_used:
        warnings.append("operator_override_used")
    if quarantine_refs:
        warnings.append("quarantine_refs_present")

    release_readiness = derive_release_readiness(
        preflight_readiness=preflight_readiness,
        status_value=status_value,
        render_decision=render_decision,
        task_executor_exit=task_executor_exit,
        harness_exit=harness_exit,
        quarantine_refs=quarantine_refs,
    )

    runbook_result = derive_runbook_result(
        task_executor_exit=task_executor_exit,
        preflight_exit=preflight_exit,
        renderer_exit=renderer_exit,
        status_value=status_value,
        preflight_readiness=preflight_readiness,
        render_decision=render_decision,
        release_readiness=release_readiness,
    )

    if not warnings:
        warnings = ["none"]

    next_action = (
        preflight_next_action
        or status_next_action
        or "Review session artifacts and proceed with operator workflow guide."
    )

    sequence = next_sequence(runtime_root, task_id)
    token = sanitize(task_id)[:20]
    doc_id = f"doc_session_report_{token}_{sequence:04d}"
    if not re.fullmatch(r"doc_[a-z0-9][a-z0-9._-]{5,63}", doc_id):
        doc_id = f"doc_session_report_{sequence:04d}_{sha256_hex(task_id.encode('utf-8'))[:8]}"
    session_report_id = f"session_{token}_{sequence:04d}"

    output_dir = runtime_root / "ops" / "session_reports"
    output_path = output_dir / f"{doc_id}.pxml"
    runbook_end = now_iso()

    root = etree.Element(q("pxml"), nsmap=NSMAP)
    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = doc_id
    etree.SubElement(meta, q("doc_class")).text = "session_report"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = task_id
    etree.SubElement(meta, q("run_id")).text = f"run_operator_{sanitize(task_id)}"
    etree.SubElement(meta, q("sequence")).text = str(sequence)
    etree.SubElement(meta, q("writer_agent")).text = "system"
    etree.SubElement(meta, q("created_at")).text = runbook_end

    refs = etree.SubElement(root, q("refs"))
    build_ref(refs, intake_info, "source_intake")
    build_ref(refs, route_info, "latest_route")
    build_ref(refs, packet_info, "latest_packet")
    build_ref(refs, status_info, "latest_status_report")
    build_ref(refs, preflight_info, "latest_preflight")
    build_ref(refs, trace_info, "latest_trace")
    if render_info is not None:
        build_ref(refs, render_info, "latest_render_report")
    if verification_info is not None:
        build_ref(refs, verification_info, "latest_verification")
    if compaction_info is not None:
        build_ref(refs, compaction_info, "latest_compaction_checkpoint")

    payload = etree.SubElement(root, q("payload"))
    etree.SubElement(payload, q("session_report_id")).text = session_report_id
    etree.SubElement(payload, q("task_id")).text = task_id
    etree.SubElement(payload, q("derived")).text = "true"
    etree.SubElement(payload, q("runbook_start_time")).text = runbook_start
    etree.SubElement(payload, q("runbook_end_time")).text = runbook_end
    etree.SubElement(payload, q("cleanup_performed")).text = (
        "true" if args.clean else "false"
    )

    build_ref_node(payload, "source_intake_ref", intake_info, "source_intake")
    build_ref_node(payload, "latest_route_ref", route_info, "latest_route")
    build_ref_node(payload, "latest_packet_ref", packet_info, "latest_packet")
    build_ref_node(
        payload,
        "latest_status_report_ref",
        status_info,
        "latest_status_report",
    )
    build_ref_node(
        payload,
        "latest_preflight_ref",
        preflight_info,
        "latest_preflight",
    )
    if render_info is not None:
        build_ref_node(
            payload,
            "latest_render_report_ref",
            render_info,
            "latest_render_report",
        )
    build_ref_node(payload, "latest_trace_ref", trace_info, "latest_trace")
    if verification_info is not None:
        build_ref_node(
            payload,
            "latest_verification_ref",
            verification_info,
            "latest_verification",
        )
    if compaction_info is not None:
        build_ref_node(
            payload,
            "latest_compaction_checkpoint_ref",
            compaction_info,
            "latest_compaction_checkpoint",
        )

    if quarantine_refs:
        quarantine_node = etree.SubElement(payload, q("quarantine_refs"))
        for item in quarantine_refs:
            etree.SubElement(quarantine_node, q("item")).text = item

    etree.SubElement(payload, q("release_readiness_result")).text = release_readiness
    etree.SubElement(payload, q("render_decision")).text = render_decision
    etree.SubElement(payload, q("render_override_used")).text = (
        "true" if render_override_used else "false"
    )
    etree.SubElement(payload, q("runbook_result")).text = runbook_result

    warnings_node = etree.SubElement(payload, q("warnings"))
    for item in warnings:
        etree.SubElement(warnings_node, q("item")).text = item

    etree.SubElement(payload, q("next_action")).text = next_action
    etree.SubElement(payload, q("task_executor_exit_code")).text = str(
        task_executor_exit
    )
    etree.SubElement(payload, q("preflight_exit_code")).text = str(preflight_exit)
    if renderer_exit is not None:
        etree.SubElement(payload, q("renderer_exit_code")).text = str(renderer_exit)
    if harness_exit is not None:
        etree.SubElement(payload, q("harness_exit_code")).text = str(harness_exit)

    integrity = etree.SubElement(root, q("integrity"))
    content_sha = compute_content_hash(meta, refs, payload)
    etree.SubElement(integrity, q("content_sha256")).text = content_sha
    preflight_sha = text_at(preflight_info.tree, "/p:pxml/p:integrity/p:content_sha256")
    if preflight_sha:
        etree.SubElement(integrity, q("parent_sha256")).text = preflight_sha

    ensure_dir(output_dir)
    report_tree = etree.ElementTree(root)
    report_tree.write(
        str(output_path), encoding="UTF-8", xml_declaration=True, pretty_print=True
    )

    latest_path = runtime_root / "latest" / f"{sanitize(task_id)}_session_report.pxml"
    ensure_dir(latest_path.parent)
    shutil.copy2(output_path, latest_path)

    tasks_dir = runtime_root / "index" / "tasks"
    artifacts_dir = runtime_root / "index" / "artifacts"
    ensure_dir(tasks_dir)
    ensure_dir(artifacts_dir)

    task_index_path = tasks_dir / f"{sanitize(task_id)}.json"
    task_index: Dict[str, object] = {}
    if task_index_path.exists():
        try:
            task_index = json.loads(task_index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            task_index = {}
    task_index["task_id"] = task_id
    task_index["latest_session_report"] = str(output_path.relative_to(runtime_root))
    task_index["updated_at"] = now_iso()
    task_index_path.write_text(
        json.dumps(task_index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    artifact_index = {
        "doc_id": doc_id,
        "doc_class": "session_report",
        "task_id": task_id,
        "path": str(output_path.relative_to(runtime_root)),
        "updated_at": now_iso(),
    }
    (artifacts_dir / f"{doc_id}.json").write_text(
        json.dumps(artifact_index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    if not args.skip_validate:
        if not validator.exists():
            print(f"ERROR: validator not found: {validator}", file=sys.stderr)
            cleanup_preserved_intake(preserved_intake)
            return 2
        context_files: List[Path] = [
            intake_info.path,
            route_info.path,
            packet_info.path,
            status_info.path,
            preflight_info.path,
            trace_info.path,
        ]
        if render_info is not None:
            context_files.append(render_info.path)
        if verification_info is not None:
            context_files.append(verification_info.path)
        if compaction_info is not None:
            context_files.append(compaction_info.path)
        try:
            run_validation(validator, output_path, context_files)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            cleanup_preserved_intake(preserved_intake)
            return 1

    print(f"Generated session_report: {output_path}")
    print(f"task_id={task_id}")
    print(f"preflight_readiness={preflight_readiness or 'unknown'}")
    print(f"render_decision={render_decision}")
    print(f"release_readiness_result={release_readiness}")
    print(f"runbook_result={runbook_result}")

    cleanup_preserved_intake(preserved_intake)
    if runbook_result in {"success", "partial"}:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
