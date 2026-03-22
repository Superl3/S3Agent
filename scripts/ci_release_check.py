#!/usr/bin/env python3
"""Run CI pytest targets from ci_test_profile in quick/full/release-critical modes."""

from __future__ import annotations

import argparse
import os
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
    raise SystemExit(2)


NS = {"p": "urn:pxml:v1"}


@dataclass
class CiTestProfile:
    profile_id: str
    profile_name: str
    quick_smoke_targets: List[str]
    full_regression_targets: List[str]
    release_critical_targets: List[str]
    default_pytest_args: List[str]
    fail_fast_default: bool
    runtime_isolation_required: bool


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


def read_items(tree: etree._ElementTree, xpath_expr: str) -> List[str]:
    values = tree.xpath(xpath_expr, namespaces=NS)
    output: List[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized:
            output.append(normalized)
    return output


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


def load_profile(path: Path) -> CiTestProfile:
    tree = etree.parse(str(path))
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    if doc_class != "ci_test_profile":
        raise ValueError(f"invalid ci_test_profile doc_class: {doc_class}")

    profile_id = text_at(tree, "/p:pxml/p:payload/p:profile_id")
    profile_name = text_at(tree, "/p:pxml/p:payload/p:profile_name")
    if profile_id is None or profile_name is None:
        raise ValueError("ci_test_profile is missing profile_id/profile_name")

    quick_smoke_targets = read_items(
        tree,
        "/p:pxml/p:payload/p:quick_smoke_targets/p:item/text()",
    )
    full_regression_targets = read_items(
        tree,
        "/p:pxml/p:payload/p:full_regression_targets/p:item/text()",
    )
    release_critical_targets = read_items(
        tree,
        "/p:pxml/p:payload/p:release_critical_targets/p:item/text()",
    )
    default_pytest_args = read_items(
        tree,
        "/p:pxml/p:payload/p:default_pytest_args/p:item/text()",
    )
    fail_fast_text = text_at(tree, "/p:pxml/p:payload/p:fail_fast_default")
    runtime_isolation_text = text_at(
        tree,
        "/p:pxml/p:payload/p:runtime_isolation_required",
    )

    return CiTestProfile(
        profile_id=profile_id,
        profile_name=profile_name,
        quick_smoke_targets=quick_smoke_targets,
        full_regression_targets=full_regression_targets,
        release_critical_targets=release_critical_targets,
        default_pytest_args=default_pytest_args,
        fail_fast_default=fail_fast_text == "true",
        runtime_isolation_required=runtime_isolation_text == "true",
    )


def normalize_targets(targets: List[str]) -> List[str]:
    unique_targets = unique_preserve(targets)
    file_targets = {item for item in unique_targets if "::" not in item}

    normalized: List[str] = []
    for target in unique_targets:
        if "::" in target:
            file_part = target.split("::", 1)[0].strip()
            if file_part in file_targets:
                continue
        normalized.append(target)
    return normalized


def verify_targets_exist(repo_root: Path, targets: List[str]) -> List[str]:
    missing: List[str] = []
    for target in targets:
        file_part = target.split("::", 1)[0].strip()
        if not file_part:
            missing.append(target)
            continue
        candidate = repo_root / file_part
        if not candidate.exists() or not candidate.is_file():
            missing.append(target)
    return sorted(set(missing))


def parse_pytest_counts(stdout: str, stderr: str) -> Dict[str, Optional[int]]:
    merged = f"{stdout}\n{stderr}"
    out: Dict[str, Optional[int]] = {"passed": None, "failed": None, "skipped": None}
    for key in out:
        match = re.search(rf"(\d+)\s+{key}", merged)
        if match:
            out[key] = int(match.group(1))
    return out


def map_exit_code(pytest_return_code: int) -> int:
    if pytest_return_code == 0:
        return 0
    if pytest_return_code == 1:
        return 1
    if pytest_return_code in {4, 5}:
        return 2
    return 3


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Run CI release regression targets using ci_test_profile.",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=repo_root / "instructions" / "ci_test_profile.pxml",
        help="ci_test_profile artifact path.",
    )
    parser.add_argument(
        "--mode",
        choices=["quick", "full", "release-critical"],
        default="quick",
        help="Regression mode selector.",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=repo_root / "runtime",
        help="Runtime root path (reported and exported for test commands).",
    )
    parser.add_argument(
        "--extra-pytest-arg",
        action="append",
        default=[],
        help="Additional pytest arg (repeatable).",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Force fail-fast behavior even if profile default is false.",
    )
    parser.add_argument(
        "--no-fail-fast",
        action="store_true",
        help="Disable fail-fast even if profile default is true.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    profile_path = args.profile.resolve()
    runtime_root = args.runtime_root.resolve()

    if not profile_path.exists():
        print(f"ERROR: ci test profile not found: {profile_path}", file=sys.stderr)
        return 2
    if not runtime_root.exists():
        print(f"ERROR: runtime root not found: {runtime_root}", file=sys.stderr)
        return 2

    try:
        profile = load_profile(profile_path)
    except Exception as exc:
        print(f"ERROR: failed to load ci_test_profile: {exc}", file=sys.stderr)
        return 2

    if args.mode == "quick":
        selected = list(profile.quick_smoke_targets)
    elif args.mode == "full":
        selected = list(profile.full_regression_targets)
    else:
        selected = list(profile.release_critical_targets)

    selected = normalize_targets(selected)
    if not selected:
        print(
            f"ERROR: no pytest targets selected for mode={args.mode}", file=sys.stderr
        )
        return 2

    missing_targets = verify_targets_exist(repo_root, selected)
    if missing_targets:
        print(
            "ERROR: selected pytest target file(s) are missing: "
            + ", ".join(missing_targets),
            file=sys.stderr,
        )
        return 2

    pytest_args = list(profile.default_pytest_args)
    for extra_arg in args.extra_pytest_arg:
        normalized = extra_arg.strip()
        if normalized:
            pytest_args.append(normalized)

    if args.fail_fast and args.no_fail_fast:
        print(
            "ERROR: --fail-fast and --no-fail-fast are mutually exclusive",
            file=sys.stderr,
        )
        return 2

    fail_fast = profile.fail_fast_default
    if args.fail_fast:
        fail_fast = True
    if args.no_fail_fast:
        fail_fast = False

    if (
        fail_fast
        and "-x" not in pytest_args
        and not any(arg.startswith("--maxfail") for arg in pytest_args)
    ):
        pytest_args.append("-x")

    command = [sys.executable, "-m", "pytest"] + pytest_args + selected

    env = os.environ.copy()
    env["PXML_CI_MODE"] = args.mode
    env["PXML_RUNTIME_ROOT"] = str(runtime_root)

    try:
        run = subprocess.run(
            command, cwd=repo_root, capture_output=True, text=True, env=env
        )
    except OSError as exc:
        print(f"ERROR: failed to execute pytest: {exc}", file=sys.stderr)
        return 3

    if run.stdout:
        print(run.stdout.strip())
    if run.stderr:
        print(run.stderr.strip(), file=sys.stderr)

    counts = parse_pytest_counts(run.stdout or "", run.stderr or "")
    mapped_exit = map_exit_code(run.returncode)

    print(f"mode={args.mode}")
    print(f"profile_id={profile.profile_id}")
    print(f"profile_name={profile.profile_name}")
    print(f"runtime_root={runtime_root}")
    print(
        f"runtime_isolation_required={str(profile.runtime_isolation_required).lower()}"
    )
    print("selected_targets=" + ",".join(selected))
    print(f"selected_target_count={len(selected)}")
    print(f"pytest_return_code={run.returncode}")
    print(
        "passed="
        + (str(counts["passed"]) if counts["passed"] is not None else "unknown")
    )
    print(
        "failed="
        + (str(counts["failed"]) if counts["failed"] is not None else "unknown")
    )
    print(
        "skipped="
        + (str(counts["skipped"]) if counts["skipped"] is not None else "unknown")
    )
    print(f"exit_code={mapped_exit}")
    return mapped_exit


if __name__ == "__main__":
    raise SystemExit(main())
