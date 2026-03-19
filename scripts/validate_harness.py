#!/usr/bin/env python3
"""Repository-level validator for the OpenCode harness."""

from __future__ import annotations

import argparse
import json
import sys

import smoke_runner

STABLE_KEYS = [
    "structure",
    "runtime_evidence",
    "skills",
    "entry_agents",
    "routing",
    "parallel_policy",
    "search_policy",
    "execution_notepad_policy",
    "phase_gate_policy",
    "patch_policy",
    "bug_localization_policy",
    "failure_memory",
    "structured_input_preservation",
    "execution_trace_policy",
    "execution_trace_scenario_policy",
    "budgets",
    "duplicate_blocks",
    "overall",
]


def _print_bucket(title: str, items: list[str]) -> None:
    print(f"{title}:")
    if not items:
        print("  - none")
        return
    for item in items:
        print(f"  - {item}")


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _stable_json_payload(results: dict[str, dict[str, object]]) -> dict[str, str]:
    payload: dict[str, str] = {}
    for key in STABLE_KEYS:
        if key == "overall":
            payload[key] = str(results.get("overall", {}).get("status", "fail"))
        else:
            payload[key] = str(results.get(key, {}).get("status", "fail"))
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run harness validation checks.")
    parser.add_argument("--json", action="store_true", help="Print JSON status map")
    args = parser.parse_args(argv)

    results = smoke_runner.run_all_checks(smoke_runner.REPO_ROOT)

    if args.json:
        print(json.dumps(_stable_json_payload(results), indent=2, sort_keys=True))
        return 0 if results.get("overall", {}).get("status") == "pass" else 1

    missing_files = _as_str_list(results.get("structure", {}).get("missing", []))
    budget_issues = _as_str_list(results.get("budgets", {}).get("violations", []))
    budget_issues.extend(
        _as_str_list(results.get("duplicate_blocks", {}).get("duplicates", []))
    )

    evidence_checks = [
        "runtime_evidence",
        "execution_trace_policy",
        "execution_trace_scenario_policy",
    ]
    evidence_issues: list[str] = []
    for check_name in evidence_checks:
        issues = _as_str_list(results.get(check_name, {}).get("issues", []))
        evidence_issues.extend([f"{check_name}: {issue}" for issue in issues])

    policy_checks = [
        "skills",
        "entry_agents",
        "routing",
        "parallel_policy",
        "search_policy",
        "execution_notepad_policy",
        "phase_gate_policy",
        "patch_policy",
        "bug_localization_policy",
        "failure_memory",
        "structured_input_preservation",
    ]
    policy_issues: list[str] = []
    for check_name in policy_checks:
        issues = _as_str_list(results.get(check_name, {}).get("issues", []))
        policy_issues.extend([f"{check_name}: {issue}" for issue in issues])

    routing_issues = _as_str_list(results.get("routing", {}).get("issues", []))
    if any("forbidden routing token" in issue for issue in routing_issues):
        policy_issues.append(
            "routing: control-plane contract failure (forbidden routing token)"
        )

    warnings: list[str] = []
    readme = smoke_runner.REPO_ROOT / "README.md"
    if readme.exists() and "@tarquinen/opencode-dcp@latest" not in readme.read_text(
        encoding="utf-8"
    ):
        warnings.append("README is missing DCP plugin guidance")

    overall_pass = results.get("overall", {}).get("status") == "pass"
    evidence_pass = all(
        results.get(name, {}).get("status") == "pass" for name in evidence_checks
    )
    print(f"VALIDATION: {'PASS' if overall_pass else 'FAIL'}")
    print("HEALTH_PRECEDENCE: evidence > tests > docs")
    _print_bucket("missing_files", missing_files)
    _print_bucket("budget_issues", budget_issues)
    _print_bucket("evidence_issues", evidence_issues)
    _print_bucket("policy_issues", policy_issues)

    print("smoke_tests:")
    for key in STABLE_KEYS:
        if key == "overall":
            continue
        status = str(results.get(key, {}).get("status", "fail")).upper()
        print(f"  - {key}: {status}")

    _print_bucket("warnings", warnings)

    has_failure = bool(
        (not evidence_pass)
        or evidence_issues
        or missing_files
        or budget_issues
        or policy_issues
        or results.get("overall", {}).get("status") != "pass"
    )
    return 1 if has_failure else 0


if __name__ == "__main__":
    sys.exit(main())
