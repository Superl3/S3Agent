#!/usr/bin/env python3
"""Batch 6 task execution driver wrapper."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

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
class PolicyConfig:
    rule_decisions: Dict[str, str]


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


def run_command(command: List[str], stage: str) -> subprocess.CompletedProcess[str]:
    print(f"[task_executor] stage={stage}")
    print("[task_executor] cmd=" + " ".join(command))
    proc = subprocess.run(command, check=False, text=True, capture_output=True)
    if proc.stdout:
        print(proc.stdout.strip())
    if proc.stderr:
        print(proc.stderr.strip(), file=sys.stderr)
    print(f"[task_executor] stage={stage} exit_code={proc.returncode}")
    return proc


def extract_task_id(intake_path: Path) -> str:
    tree = etree.parse(str(intake_path))
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    if doc_class != "task_intake":
        raise ValueError(f"intake artifact must be task_intake (got {doc_class!r})")
    task_id = text_at(tree, "/p:pxml/p:meta/p:task_id")
    if not task_id:
        raise ValueError("task_intake is missing meta/task_id")
    return task_id


def load_policy(path: Path) -> PolicyConfig:
    tree = etree.parse(str(path))
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    if doc_class != "post_implement_verification_policy":
        raise ValueError(
            "policy artifact must be post_implement_verification_policy "
            f"(got {doc_class!r})"
        )
    names = tree.xpath(
        "/p:pxml/p:payload/p:rules/p:rule/p:rule_name/text()",
        namespaces=NS,
    )
    decisions = tree.xpath(
        "/p:pxml/p:payload/p:rules/p:rule/p:decision/text()",
        namespaces=NS,
    )
    mapping: Dict[str, str] = {}
    for name, decision in zip(names, decisions):
        key = name.strip()
        value = decision.strip()
        if key and value:
            mapping[key] = value
    return PolicyConfig(rule_decisions=mapping)


def latest_task_artifact(
    runtime_root: Path, task_id: str, suffix: str
) -> Optional[Path]:
    candidate = runtime_root / "latest" / f"{sanitize(task_id)}_{suffix}.pxml"
    if candidate.exists():
        return candidate
    return None


def latest_implementer_status(runtime_root: Path, task_id: str) -> Optional[str]:
    result_path = latest_task_artifact(runtime_root, task_id, "implementer_result")
    if result_path is None:
        return None
    tree = etree.parse(str(result_path))
    return text_at(tree, "/p:pxml/p:payload/p:result_status")


def latest_selected_path(runtime_root: Path, task_id: str) -> Optional[str]:
    route_path = latest_task_artifact(runtime_root, task_id, "manager_route")
    if route_path is None:
        return None
    tree = etree.parse(str(route_path))
    return text_at(tree, "/p:pxml/p:payload/p:selected_path")


def packet_write_intent(packet_path: Path) -> bool:
    tree = etree.parse(str(packet_path))
    value = text_at(tree, "/p:pxml/p:payload/p:write_intent")
    if value is None:
        return True
    return value.lower() == "true"


def should_run_verifier_auto(
    policy: PolicyConfig,
    result_status: Optional[str],
    selected_path: Optional[str],
) -> bool:
    if result_status is None:
        return False

    decision = policy.rule_decisions.get(
        "auto_verify_on_result_status", "defer_verifier"
    )
    if result_status == "blocked":
        decision = policy.rule_decisions.get("skip_verify_on_blocked", decision)
    elif result_status == "retry_failed":
        decision = policy.rule_decisions.get("skip_verify_on_retry_failed", decision)
    elif result_status == "escalated":
        decision = policy.rule_decisions.get("skip_verify_on_escalated", decision)
    elif result_status == "no_op":
        decision = policy.rule_decisions.get("verify_on_no_op", decision)
    elif result_status == "applied":
        decision = policy.rule_decisions.get("verify_on_applied", decision)

    if result_status in {"blocked", "retry_failed", "escalated"}:
        return False

    lane_decision = policy.rule_decisions.get("auto_verify_required_lane")
    if (
        selected_path in {"verifier_post", "full_lane"}
        and lane_decision == "run_verifier"
    ):
        return True
    return decision == "run_verifier"


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Run Batch 6 task executor flow.")
    parser.add_argument(
        "--intake", required=True, type=Path, help="task_intake input file"
    )
    parser.add_argument("--task-id", default=None, help="Optional explicit task_id")
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
        help="Workspace root for implementer runner.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Run task-scoped runtime cleanup before executing pipeline.",
    )
    parser.add_argument(
        "--verify-policy",
        choices=["auto", "always", "never"],
        default="auto",
        help="Post-implement verifier execution mode.",
    )
    parser.add_argument(
        "--run-harness",
        action="store_true",
        help="Run harness validator after status report generation.",
    )
    parser.add_argument(
        "--allow-no-op",
        action="store_true",
        help="Treat implementer_result status=no_op as successful terminal state.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Pass skip validation flags to underlying scripts.",
    )
    parser.add_argument(
        "--post-verify-policy",
        type=Path,
        default=repo_root / "instructions" / "post_implement_verification_policy.pxml",
        help="Policy artifact used when --verify-policy=auto.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    runtime_root = args.runtime_root.resolve()
    workspace_root = args.workspace_root.resolve()
    intake_path = args.intake.resolve()

    packet_builder = repo_root / "scripts" / "packet_builder.py"
    coordinator = repo_root / "scripts" / "orchestration_coordinator.py"
    implementer = repo_root / "scripts" / "implementer_runner.py"
    verifier = repo_root / "scripts" / "verification_runner.py"
    status_report = repo_root / "scripts" / "task_status_report.py"
    cleanup = repo_root / "scripts" / "cleanup_task_runtime.py"
    harness = repo_root / "scripts" / "harness_validator.py"

    if not intake_path.exists():
        print(f"ERROR: intake file not found: {intake_path}", file=sys.stderr)
        return 2
    if not workspace_root.exists():
        print(f"ERROR: workspace root not found: {workspace_root}", file=sys.stderr)
        return 2

    try:
        derived_task_id = extract_task_id(intake_path)
    except Exception as exc:
        print(f"ERROR: failed to parse intake: {exc}", file=sys.stderr)
        return 2

    task_id = args.task_id or derived_task_id
    if args.task_id and args.task_id != derived_task_id:
        print(
            "ERROR: --task-id does not match task_intake meta/task_id "
            f"({args.task_id} != {derived_task_id})",
            file=sys.stderr,
        )
        return 2

    if args.clean:
        cleanup_cmd = [
            sys.executable,
            str(cleanup),
            "--task-id",
            task_id,
            "--runtime-root",
            str(runtime_root),
        ]
        cleanup_proc = run_command(cleanup_cmd, "cleanup")
        if cleanup_proc.returncode != 0:
            return cleanup_proc.returncode

    packet_cmd = [
        sys.executable,
        str(packet_builder),
        "--intake",
        str(intake_path),
        "--runtime-root",
        str(runtime_root),
    ]
    if args.skip_validate:
        packet_cmd.append("--skip-validate")
    packet_proc = run_command(packet_cmd, "packet_builder")
    if packet_proc.returncode != 0:
        return packet_proc.returncode

    coord_cmd = [
        sys.executable,
        str(coordinator),
        "--task-id",
        task_id,
        "--runtime-root",
        str(runtime_root),
    ]
    if args.skip_validate:
        coord_cmd.append("--skip-validate")
    coord_proc = run_command(coord_cmd, "coordinator")
    if coord_proc.returncode != 0:
        return coord_proc.returncode

    packet_path = latest_task_artifact(runtime_root, task_id, "execution_packet")
    if packet_path is None:
        print("ERROR: execution_packet missing after packet builder", file=sys.stderr)
        return 1

    try:
        write_intent = packet_write_intent(packet_path)
    except Exception as exc:
        print(
            f"ERROR: failed to parse execution_packet write_intent: {exc}",
            file=sys.stderr,
        )
        return 2
    impl_proc: Optional[subprocess.CompletedProcess[str]] = None
    if write_intent:
        impl_cmd = [
            sys.executable,
            str(implementer),
            "--packet",
            str(packet_path),
            "--runtime-root",
            str(runtime_root),
            "--workspace-root",
            str(workspace_root),
        ]
        if args.skip_validate:
            impl_cmd.append("--skip-validate")
        impl_proc = run_command(impl_cmd, "implementer_runner")
    else:
        print(
            "[task_executor] stage=explorer_handoff"
            " write_intent=false -> skipping implementer runner"
        )

    result_status = (
        latest_implementer_status(runtime_root, task_id)
        if write_intent
        else "explore_only"
    )
    selected_path = latest_selected_path(runtime_root, task_id)
    if result_status is None:
        print(
            "ERROR: implementer_result missing after implementer runner",
            file=sys.stderr,
        )
        return 1

    run_verifier = False
    policy_reason = ""
    if not write_intent:
        run_verifier = False
        policy_reason = "write_intent=false"
    elif args.verify_policy == "always":
        run_verifier = True
        policy_reason = "override=always"
    elif args.verify_policy == "never":
        run_verifier = False
        policy_reason = "override=never"
    else:
        policy_path = args.post_verify_policy.resolve()
        try:
            policy = load_policy(policy_path)
        except Exception as exc:
            print(f"ERROR: failed to load post-implement verification policy: {exc}")
            return 1
        run_verifier = should_run_verifier_auto(
            policy=policy,
            result_status=result_status,
            selected_path=selected_path,
        )
        policy_reason = (
            f"auto status={result_status} selected_path={selected_path or 'unknown'}"
        )

    print(
        f"[task_executor] verification_decision run={str(run_verifier).lower()} ({policy_reason})"
    )

    if run_verifier:
        verify_cmd = [
            sys.executable,
            str(verifier),
            "--packet",
            str(packet_path),
            "--runtime-root",
            str(runtime_root),
            "--append-trace",
            "--verify-phase",
            "post_implement",
        ]
        review_path = latest_task_artifact(runtime_root, task_id, "review_sidecar")
        if review_path is not None:
            verify_cmd.extend(["--review-sidecar", str(review_path)])
        if args.skip_validate:
            verify_cmd.append("--skip-validate")
        verify_proc = run_command(verify_cmd, "verification_runner")
        if verify_proc.returncode != 0:
            return verify_proc.returncode

    status_cmd = [
        sys.executable,
        str(status_report),
        "--task-id",
        task_id,
        "--runtime-root",
        str(runtime_root),
    ]
    if args.skip_validate:
        status_cmd.append("--skip-validate")
    status_proc = run_command(status_cmd, "task_status_report")
    if status_proc.returncode != 0:
        return status_proc.returncode

    if args.run_harness:
        harness_cmd = [
            sys.executable,
            str(harness),
            "--task-id",
            task_id,
            "--runtime-root",
            str(runtime_root),
        ]
        harness_proc = run_command(harness_cmd, "harness_validator")
        if harness_proc.returncode not in {0, 2}:
            return harness_proc.returncode

    if result_status == "no_op" and not args.allow_no_op:
        print(
            "ERROR: implementer produced no_op and --allow-no-op was not set.",
            file=sys.stderr,
        )
        return 1

    if result_status in {"blocked", "retry_failed", "escalated"}:
        return 1

    if impl_proc is not None and impl_proc.returncode != 0:
        return impl_proc.returncode

    print(f"[task_executor] completed task_id={task_id} status={result_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
