#!/usr/bin/env python3
"""Shared helpers for manager-mediated context refresh from lane runtimes."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from context_contract import append_context_access_log, parse_context_policy

try:
    from lxml import etree
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError("lxml is required for context refresh runtime helpers") from exc


NS = {"p": "urn:pxml:v1"}


@dataclass
class ContextRefreshOutcome:
    request_path: Optional[Path]
    result_path: Optional[Path]
    request_ref: Optional[Tuple[str, str, str]]
    result_ref: Optional[Tuple[str, str, str]]
    actionability: Optional[str]
    notes: List[str]


def text_at(tree: etree._ElementTree, expr: str) -> Optional[str]:
    values = tree.xpath(expr, namespaces=NS)
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


def sanitize(value: str) -> str:
    import re

    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9._-]", "_", lowered)
    lowered = re.sub(r"_+", "_", lowered).strip("_")
    return lowered or "id"


def latest_task_artifact(
    runtime_root: Path, task_id: str, suffix: str
) -> Optional[Path]:
    candidate = runtime_root / "latest" / f"{sanitize(task_id)}_{suffix}.pxml"
    if candidate.exists():
        return candidate
    return None


def discover_pxml_files(path: Path) -> List[Path]:
    if not path.exists():
        return []
    files = [candidate for candidate in path.rglob("*.pxml") if candidate.is_file()]
    files.sort()
    return files


def find_artifact_by_doc_id(runtime_root: Path, doc_id: str) -> Optional[Path]:
    for path in discover_pxml_files(runtime_root):
        try:
            tree = etree.parse(str(path))
        except (OSError, etree.XMLSyntaxError):
            continue
        if text_at(tree, "/p:pxml/p:meta/p:doc_id") == doc_id:
            return path
    return None


def artifact_ref(path: Path) -> Optional[Tuple[str, str, str]]:
    try:
        tree = etree.parse(str(path))
    except (OSError, etree.XMLSyntaxError):
        return None
    doc_id = text_at(tree, "/p:pxml/p:meta/p:doc_id")
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    if not doc_id or not doc_class:
        return None
    relation = (
        "context_refresh_request"
        if doc_class == "exploration_request"
        else "context_refresh_result"
    )
    return (doc_id, doc_class, relation)


def run_manager_mediated_refresh(
    *,
    repo_root: Path,
    runtime_root: Path,
    workspace_root: Path,
    packet_path: Path,
    task_id: str,
    baseline_exploration_doc_id: Optional[str],
    requester_agent: str,
    request_kind: str,
    reason_code: str,
    focus_questions: Sequence[str],
    target_hints: Sequence[str],
    contract_change_suspected: bool,
    request_context_path: Optional[Path],
    blocking: bool,
    skip_validate: bool,
) -> ContextRefreshOutcome:
    notes: List[str] = []
    if not baseline_exploration_doc_id:
        return ContextRefreshOutcome(
            request_path=None,
            result_path=None,
            request_ref=None,
            result_ref=None,
            actionability=None,
            notes=[
                "No baseline exploration_result was attached to the packet; context refresh skipped."
            ],
        )

    baseline_path = find_artifact_by_doc_id(runtime_root, baseline_exploration_doc_id)
    if baseline_path is None:
        return ContextRefreshOutcome(
            request_path=None,
            result_path=None,
            request_ref=None,
            result_ref=None,
            actionability=None,
            notes=[
                "Baseline exploration_result referenced by packet was not found in runtime."
            ],
        )

    builder = repo_root / "scripts" / "exploration_request_builder.py"
    explorer = repo_root / "scripts" / "explorer_runner.py"
    builder_cmd = [
        sys.executable,
        str(builder),
        "--packet",
        str(packet_path),
        "--baseline-exploration",
        str(baseline_path),
        "--requester-agent",
        requester_agent,
        "--request-kind",
        request_kind,
        "--reason-code",
        reason_code,
        "--runtime-root",
        str(runtime_root),
    ]
    for item in focus_questions:
        builder_cmd.extend(["--focus-question", item])
    for item in target_hints:
        builder_cmd.extend(["--target-hint", item])
    if blocking:
        builder_cmd.append("--blocking")
    if contract_change_suspected:
        builder_cmd.append("--contract-change-suspected")
    if request_context_path is not None:
        builder_cmd.extend(["--request-context", str(request_context_path)])
    if skip_validate:
        builder_cmd.append("--skip-validate")

    builder_proc = subprocess.run(
        builder_cmd, check=False, capture_output=True, text=True
    )
    if builder_proc.returncode != 0:
        detail = (
            builder_proc.stderr
            or builder_proc.stdout
            or "exploration_request publication failed"
        ).strip()
        return ContextRefreshOutcome(
            request_path=None,
            result_path=None,
            request_ref=None,
            result_ref=None,
            actionability=None,
            notes=[f"Context refresh request rejected: {detail}"],
        )

    request_path = latest_task_artifact(runtime_root, task_id, "exploration_request")
    if request_path is None:
        return ContextRefreshOutcome(
            request_path=None,
            result_path=None,
            request_ref=None,
            result_ref=None,
            actionability=None,
            notes=[
                "exploration_request was published but latest pointer was not updated."
            ],
        )

    explorer_cmd = [
        sys.executable,
        str(explorer),
        "--request",
        str(request_path),
        "--runtime-root",
        str(runtime_root),
        "--workspace-root",
        str(workspace_root),
    ]
    if skip_validate:
        explorer_cmd.append("--skip-validate")
    explorer_proc = subprocess.run(
        explorer_cmd, check=False, capture_output=True, text=True
    )
    if explorer_proc.returncode != 0:
        detail = (
            explorer_proc.stderr or explorer_proc.stdout or "focused exploration failed"
        ).strip()
        request_ref = artifact_ref(request_path)
        return ContextRefreshOutcome(
            request_path=request_path,
            result_path=None,
            request_ref=request_ref,
            result_ref=None,
            actionability=None,
            notes=[f"Focused context refresh failed: {detail}"],
        )

    result_path = latest_task_artifact(
        runtime_root, task_id, "focused_exploration_result"
    )
    if result_path is None:
        request_ref = artifact_ref(request_path)
        return ContextRefreshOutcome(
            request_path=request_path,
            result_path=None,
            request_ref=request_ref,
            result_ref=None,
            actionability=None,
            notes=[
                "Focused exploration_result was expected but latest pointer was missing."
            ],
        )

    result_tree = etree.parse(str(result_path))
    actionability = text_at(result_tree, "/p:pxml/p:payload/p:actionability")
    request_ref = artifact_ref(request_path)
    result_ref = artifact_ref(result_path)
    packet_tree = etree.parse(str(packet_path))
    context_policy = parse_context_policy(packet_tree)
    if actionability:
        notes.append(
            f"Focused context refresh completed with actionability={actionability}."
        )
    append_context_access_log(
        runtime_root=runtime_root,
        task_id=task_id,
        actor=requester_agent,
        access_type="focused_refresh",
        reason=reason_code,
        packet_doc_id=packet_path.stem,
        baseline_doc_id=baseline_exploration_doc_id,
        packet_generation=context_policy.packet_generation,
        context_generation=context_policy.context_generation,
    )
    return ContextRefreshOutcome(
        request_path=request_path,
        result_path=result_path,
        request_ref=request_ref,
        result_ref=result_ref,
        actionability=actionability,
        notes=notes,
    )
