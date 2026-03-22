#!/usr/bin/env python3
"""Thin release-ops gate wrapper with CI exit-code policy mapping."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from lxml import etree
except ModuleNotFoundError:
    print(
        "ERROR: lxml is required. Install with: python -m pip install lxml",
        file=sys.stderr,
    )
    raise SystemExit(3)


NS = {"p": "urn:pxml:v1"}


def run_stage(command: List[str], stage: str) -> subprocess.CompletedProcess[str]:
    print(f"[release_ops_gate] stage={stage}")
    print("[release_ops_gate] cmd=" + " ".join(command))
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        print(result.stderr.strip(), file=sys.stderr)
    print(f"[release_ops_gate] stage={stage} exit_code={result.returncode}")
    return result


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
    normalized = text.strip()
    return normalized or None


def load_ci_policy(path: Path) -> Dict[str, Tuple[int, str]]:
    tree = etree.parse(str(path))
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    if doc_class != "ci_exit_code_policy":
        raise ValueError(f"invalid ci policy doc_class: {doc_class}")

    mapping: Dict[str, Tuple[int, str]] = {}
    rule_nodes = tree.xpath("/p:pxml/p:payload/p:rules/p:rule", namespaces=NS)
    for node in rule_nodes:
        node_tree = etree.ElementTree(node)
        input_condition = text_at(node_tree, "./p:input_condition")
        output_exit_code = text_at(node_tree, "./p:output_exit_code")
        ci_label = text_at(node_tree, "./p:ci_label")
        if input_condition is None or output_exit_code is None or ci_label is None:
            continue
        try:
            code = int(output_exit_code)
        except ValueError:
            continue
        mapping[input_condition] = (code, ci_label)
    return mapping


def map_ci_exit(
    ci_policy: Dict[str, Tuple[int, str]],
    key: str,
    fallback_code: int,
    fallback_label: str,
) -> Tuple[int, str]:
    value = ci_policy.get(key)
    if value is None:
        return fallback_code, fallback_label
    return value


def parse_rc_outputs(stdout: str) -> Tuple[str, str, str]:
    rc_result = "unknown"
    report_path = "unknown"
    manifest_path = "unknown"
    for raw in stdout.splitlines():
        line = raw.strip()
        if line.startswith("rc_result="):
            rc_result = line.split("=", 1)[1].strip()
        elif line.startswith("release_candidate_report="):
            report_path = line.split("=", 1)[1].strip()
        elif line.startswith("release_bundle_manifest="):
            manifest_path = line.split("=", 1)[1].strip()
    return rc_result, report_path, manifest_path


def parse_audit_outputs(stdout: str) -> Tuple[str, str]:
    audit_result = "unknown"
    audit_report_path = "none"
    for raw in stdout.splitlines():
        line = raw.strip()
        if line.startswith("verify_phase_audit_result="):
            audit_result = line.split("=", 1)[1].strip()
        elif line.startswith("verify_phase_audit_report="):
            audit_report_path = line.split("=", 1)[1].strip()
    return audit_result, audit_report_path


def parse_refresh_outputs(
    stdout: str,
) -> Tuple[Optional[int], Optional[int], List[str]]:
    task_count: Optional[int] = None
    failed_count: Optional[int] = None
    failures: List[str] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if line.startswith("task_count="):
            value = line.split("=", 1)[1].strip()
            if value.isdigit():
                task_count = int(value)
        elif line.startswith("failed_count="):
            value = line.split("=", 1)[1].strip()
            if value.isdigit():
                failed_count = int(value)
        elif line.startswith("failure="):
            failures.append(line.split("=", 1)[1].strip())
    return task_count, failed_count, failures


def unique_preserve(values: List[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Run session refresh + release candidate + optional verify-phase audit with CI exit-code policy mapping."
    )
    parser.add_argument("--coverage-task-id", action="append", default=[])
    parser.add_argument("--coverage-set-file", type=Path, default=None)
    parser.add_argument("--candidate-task-id", action="append", default=[])
    parser.add_argument("--candidate-set-file", type=Path, default=None)
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--smoke-set-file", type=Path, default=None)
    parser.add_argument("--use-default-smoke-set", action="store_true")
    parser.add_argument(
        "--release-task-id",
        default="task_release_candidate_batch10",
        help="Task id used for generated release artifacts.",
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
        default=repo_root / "instructions" / "release_candidate_policy.pxml",
    )
    parser.add_argument(
        "--workflow-guide",
        type=Path,
        default=repo_root / "instructions" / "operator_workflow_guide.pxml",
    )
    parser.add_argument(
        "--runbook-policy",
        type=Path,
        default=repo_root / "instructions" / "operator_runbook_policy.pxml",
    )
    parser.add_argument(
        "--pruning-policy",
        type=Path,
        default=repo_root / "instructions" / "artifact_pruning_policy.pxml",
    )
    parser.add_argument(
        "--trace-semantics",
        type=Path,
        default=repo_root / "instructions" / "trace_event_semantics.pxml",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=repo_root / "instructions" / "release_gate_profile.pxml",
    )
    parser.add_argument(
        "--coverage-policy",
        type=Path,
        default=repo_root / "instructions" / "coverage_outcome_policy.pxml",
    )
    parser.add_argument(
        "--profile-governance-policy",
        type=Path,
        default=repo_root / "instructions" / "release_profile_governance_policy.pxml",
    )
    parser.add_argument(
        "--harness-validator",
        type=Path,
        default=repo_root / "scripts" / "harness_validator.py",
    )
    parser.add_argument(
        "--validator",
        type=Path,
        default=repo_root / "scripts" / "pxml_validator.py",
    )
    parser.add_argument(
        "--ci-policy",
        type=Path,
        default=repo_root / "instructions" / "ci_exit_code_policy.pxml",
        help="CI exit-code policy consumed by release_ops_gate.",
    )
    parser.add_argument(
        "--verify-phase-policy",
        type=Path,
        default=repo_root / "instructions" / "verify_phase_audit_policy.pxml",
        help="Verify phase audit policy path.",
    )
    parser.add_argument(
        "--run-verify-phase-audit",
        action="store_true",
        help="Run verify_phase_audit.py after release candidate aggregation.",
    )
    parser.add_argument("--allow-caution-rc", action="store_true")
    parser.add_argument(
        "--skip-session-refresh",
        action="store_true",
        help="Skip running session_report_refresh.py before release candidate check.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Pass --skip-validate through child scripts.",
    )
    return parser.parse_args()


def append_refresh_selector_args(command: List[str], args: argparse.Namespace) -> None:
    for task_id in args.coverage_task_id:
        command.extend(["--task-id", task_id])
    for task_id in args.task_id:
        command.extend(["--task-id", task_id])
    if args.coverage_set_file is not None:
        command.extend(["--coverage-set-file", str(args.coverage_set_file.resolve())])
    if args.smoke_set_file is not None:
        command.extend(["--smoke-set-file", str(args.smoke_set_file.resolve())])
    if args.use_default_smoke_set:
        command.append("--use-default-smoke-set")


def append_release_selector_args(command: List[str], args: argparse.Namespace) -> None:
    for task_id in args.coverage_task_id:
        command.extend(["--coverage-task-id", task_id])
    for task_id in args.task_id:
        command.extend(["--task-id", task_id])
    if args.coverage_set_file is not None:
        command.extend(["--coverage-set-file", str(args.coverage_set_file.resolve())])
    if args.smoke_set_file is not None:
        command.extend(["--smoke-set-file", str(args.smoke_set_file.resolve())])
    if args.use_default_smoke_set:
        command.append("--use-default-smoke-set")


def append_audit_selector_args(command: List[str], args: argparse.Namespace) -> None:
    selector_task_ids = unique_preserve(
        list(args.candidate_task_id) + list(args.coverage_task_id) + list(args.task_id)
    )
    for task_id in selector_task_ids:
        command.extend(["--task-id", task_id])

    if args.candidate_set_file is not None:
        command.extend(["--audit-set-file", str(args.candidate_set_file.resolve())])
    elif args.coverage_set_file is not None:
        command.extend(["--audit-set-file", str(args.coverage_set_file.resolve())])
    elif args.smoke_set_file is not None:
        command.extend(["--audit-set-file", str(args.smoke_set_file.resolve())])


def classify_stage_error(returncode: int) -> str:
    if returncode in {2, 3}:
        return "error_kind=validation_usage"
    return "error_kind=hard_execution"


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    runtime_root = args.runtime_root.resolve()

    session_refresh_script = repo_root / "scripts" / "session_report_refresh.py"
    release_candidate_script = repo_root / "scripts" / "release_candidate_check.py"
    verify_phase_audit_script = repo_root / "scripts" / "verify_phase_audit.py"

    ci_policy_path = args.ci_policy.resolve()
    try:
        ci_policy = load_ci_policy(ci_policy_path)
    except Exception as exc:
        print(f"ERROR: failed to load ci exit code policy: {exc}", file=sys.stderr)
        return 3

    if not runtime_root.exists():
        print(f"ERROR: runtime root not found: {runtime_root}", file=sys.stderr)
        code, _label = map_ci_exit(
            ci_policy,
            "error_kind=validation_usage",
            3,
            "usage_error",
        )
        return code
    if not session_refresh_script.exists():
        print(
            f"ERROR: session refresh script not found: {session_refresh_script}",
            file=sys.stderr,
        )
        code, _label = map_ci_exit(
            ci_policy,
            "error_kind=validation_usage",
            3,
            "usage_error",
        )
        return code
    if not release_candidate_script.exists():
        print(
            f"ERROR: release candidate script not found: {release_candidate_script}",
            file=sys.stderr,
        )
        code, _label = map_ci_exit(
            ci_policy,
            "error_kind=validation_usage",
            3,
            "usage_error",
        )
        return code
    if args.run_verify_phase_audit and not verify_phase_audit_script.exists():
        print(
            f"ERROR: verify_phase_audit script not found: {verify_phase_audit_script}",
            file=sys.stderr,
        )
        code, _label = map_ci_exit(
            ci_policy,
            "error_kind=validation_usage",
            3,
            "usage_error",
        )
        return code

    if not args.skip_session_refresh:
        refresh_cmd: List[str] = [
            sys.executable,
            str(session_refresh_script),
            "--runtime-root",
            str(runtime_root),
            "--profile",
            str(args.profile.resolve()),
            "--validator",
            str(args.validator.resolve()),
        ]
        append_refresh_selector_args(refresh_cmd, args)
        if args.skip_validate:
            refresh_cmd.append("--skip-validate")
        refresh_run = run_stage(refresh_cmd, "session_refresh")
        if refresh_run.returncode != 0:
            task_count, failed_count, failures = parse_refresh_outputs(
                refresh_run.stdout or ""
            )
            if (
                refresh_run.returncode == 1
                and task_count is not None
                and failed_count is not None
            ):
                print(
                    "[release_ops_gate] note=session_refresh reported task-level failures; continuing to release_candidate_check for RC classification"
                )
                for item in failures:
                    if item:
                        print(f"[release_ops_gate] session_refresh_failure={item}")
            else:
                error_key = classify_stage_error(refresh_run.returncode)
                code, _label = map_ci_exit(ci_policy, error_key, 4, "infra_error")
                return code

    release_cmd: List[str] = [
        sys.executable,
        str(release_candidate_script),
        "--release-task-id",
        args.release_task_id,
        "--runtime-root",
        str(runtime_root),
        "--policy",
        str(args.policy.resolve()),
        "--workflow-guide",
        str(args.workflow_guide.resolve()),
        "--runbook-policy",
        str(args.runbook_policy.resolve()),
        "--pruning-policy",
        str(args.pruning_policy.resolve()),
        "--trace-semantics",
        str(args.trace_semantics.resolve()),
        "--profile",
        str(args.profile.resolve()),
        "--coverage-policy",
        str(args.coverage_policy.resolve()),
        "--profile-governance-policy",
        str(args.profile_governance_policy.resolve()),
        "--ci-policy",
        str(ci_policy_path),
        "--verify-phase-policy",
        str(args.verify_phase_policy.resolve()),
        "--harness-validator",
        str(args.harness_validator.resolve()),
        "--validator",
        str(args.validator.resolve()),
    ]
    append_release_selector_args(release_cmd, args)
    for task_id in args.candidate_task_id:
        release_cmd.extend(["--candidate-task-id", task_id])
    if args.candidate_set_file is not None:
        release_cmd.extend(
            ["--candidate-set-file", str(args.candidate_set_file.resolve())]
        )
    if args.allow_caution_rc:
        release_cmd.append("--allow-caution-rc")
    if args.skip_validate:
        release_cmd.append("--skip-validate")

    release_run = run_stage(release_cmd, "release_candidate_check")
    rc_result, report_path, manifest_path = parse_rc_outputs(release_run.stdout or "")
    if release_run.returncode != 0 and rc_result in {"pass", "caution", "fail"}:
        ci_key = f"rc_result={rc_result}"
        ci_exit_code, ci_label = map_ci_exit(ci_policy, ci_key, 1, "failure")
        print(
            "[release_ops_gate] note=release_candidate_check exited non-zero but provided rc_result; treating as structured RC outcome"
        )
        print(f"release_ops_gate_result={rc_result}")
        print(f"rc_result={rc_result}")
        print(f"ci_label={ci_label}")
        print(f"ci_exit_code={ci_exit_code}")
        print(f"release_candidate_report_ref={report_path}")
        print(f"release_bundle_manifest_ref={manifest_path}")
        print("verify_phase_audit_report_ref=none")
        return ci_exit_code

    if release_run.returncode != 0:
        error_key = classify_stage_error(release_run.returncode)
        code, _label = map_ci_exit(ci_policy, error_key, 4, "infra_error")
        return code

    if rc_result not in {"pass", "caution", "fail"}:
        code, _label = map_ci_exit(
            ci_policy, "error_kind=hard_execution", 4, "infra_error"
        )
        return code

    release_ops_gate_result = rc_result
    verify_phase_audit_report_path = "none"

    if args.run_verify_phase_audit:
        audit_cmd: List[str] = [
            sys.executable,
            str(verify_phase_audit_script),
            "--release-task-id",
            args.release_task_id,
            "--runtime-root",
            str(runtime_root),
            "--policy",
            str(args.verify_phase_policy.resolve()),
            "--validator",
            str(args.validator.resolve()),
        ]
        append_audit_selector_args(audit_cmd, args)
        if args.skip_validate:
            audit_cmd.append("--skip-validate")

        audit_run = run_stage(audit_cmd, "verify_phase_audit")
        if audit_run.returncode != 0:
            error_key = classify_stage_error(audit_run.returncode)
            code, _label = map_ci_exit(ci_policy, error_key, 4, "infra_error")
            return code

        audit_result, verify_phase_audit_report_path = parse_audit_outputs(
            audit_run.stdout or ""
        )
        if audit_result == "fail":
            release_ops_gate_result = "fail"
        elif audit_result == "caution" and release_ops_gate_result == "pass":
            release_ops_gate_result = "caution"

    ci_key = f"rc_result={release_ops_gate_result}"
    ci_exit_code, ci_label = map_ci_exit(ci_policy, ci_key, 1, "failure")

    print(f"release_ops_gate_result={release_ops_gate_result}")
    print(f"rc_result={rc_result}")
    print(f"ci_label={ci_label}")
    print(f"ci_exit_code={ci_exit_code}")
    print(f"release_candidate_report_ref={report_path}")
    print(f"release_bundle_manifest_ref={manifest_path}")
    print(f"verify_phase_audit_report_ref={verify_phase_audit_report_path}")
    return ci_exit_code


if __name__ == "__main__":
    raise SystemExit(main())
