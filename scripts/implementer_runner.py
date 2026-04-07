#!/usr/bin/env python3
"""Batch 5 implementer runtime runner.

Reads execution_packet, enforces packet constraints, applies minimal patch actions,
and emits implementer_result plus standardized trace events.
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
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Sequence, Tuple

from runtime_bootstrap import bootstrap_runtime

try:
    from lxml import etree
except ModuleNotFoundError:
    print(
        "ERROR: lxml is required. Install with: python -m pip install lxml",
        file=sys.stderr,
    )
    raise SystemExit(3)

from context_refresh_runtime import run_manager_mediated_refresh


NS = "urn:pxml:v1"
NSMAP = {None: NS}
XPATH_NS = {"p": NS}
REQUIRED_RUNTIME_RULES = {
    "packet_conformance_required",
    "expected_files_guard",
    "out_of_scope_guard",
    "patch_first_default",
    "rewrite_exception_requires_approval",
    "blocked_reason_required",
    "retry_failed_after_threshold",
    "escalation_after_retry_limit",
    "write_intent_required",
}


@dataclass
class ExpectedFile:
    path: str
    mode: str


@dataclass
class ProofRequirement:
    proof_category: str
    required: bool
    proof_method: str
    minimum_evidence: str


@dataclass
class RequirementTarget:
    requirement: str
    proof_method: str
    status_target: str
    minimum_evidence: str
    next_step_if_missing: str


@dataclass
class PacketInfo:
    path: Path
    doc_id: str
    task_id: str
    run_id: str
    sequence: int
    created_at: str
    content_sha256: str
    acceptance_lock_hash: str
    write_intent: bool
    in_scope: List[str]
    out_of_scope: List[str]
    expected_files: List[ExpectedFile]
    patch_mode: str
    max_files: int
    rewrite_exception_approved: bool
    rewrite_exception_reason: Optional[str]
    acceptance_check_count: int
    intended_behaviors: List[str]
    proof_requirements: List[ProofRequirement]
    requirement_targets: List[RequirementTarget]
    completion_state: Optional[str]
    baseline_exploration_doc_id: Optional[str]
    localization_targets: List[str]


@dataclass
class RetryPolicyConfig:
    implementer_max_attempts: int = 2


@dataclass
class EscalationPolicyConfig:
    stop_after_escalation: bool = True


@dataclass
class RunResult:
    status: str
    blocked_reason: Optional[str]
    retry_count: int
    escalation_requested: bool
    modified_files: List[str]
    created_files: List[str]
    evidence_paths: List[str]
    notes: List[str]
    context_refs: List[Tuple[str, str, str]]
    context_files: List[Path]


def q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


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


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sanitize(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9._-]", "_", lowered)
    lowered = re.sub(r"_+", "_", lowered).strip("_")
    return lowered or "id"


def normalize_rel_path(value: str) -> str:
    raw = value.replace("\\", "/").strip()
    raw = re.sub(r"/+", "/", raw)
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw:
        raise ValueError("empty relative path")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"path escapes workspace: {value!r}")
    return str(pure)


def path_in_prefixes(path_value: str, prefixes: Sequence[str]) -> bool:
    normalized_path = normalize_rel_path(path_value)
    for prefix in prefixes:
        normalized_prefix = normalize_rel_path(prefix)
        normalized_prefix = normalized_prefix.rstrip("/")
        if normalized_path == normalized_prefix:
            return True
        if normalized_path.startswith(normalized_prefix + "/"):
            return True
    return False


def to_workspace_path(workspace_root: Path, relative_path: str) -> Path:
    pure = PurePosixPath(relative_path)
    return workspace_root.joinpath(*pure.parts)


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


def parse_packet(path: Path) -> PacketInfo:
    tree = etree.parse(str(path))
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    if doc_class != "execution_packet":
        raise ValueError(f"Input artifact must be execution_packet (got {doc_class!r})")

    doc_id = text_at(tree, "/p:pxml/p:meta/p:doc_id")
    task_id = text_at(tree, "/p:pxml/p:meta/p:task_id")
    run_id = text_at(tree, "/p:pxml/p:meta/p:run_id")
    seq_text = text_at(tree, "/p:pxml/p:meta/p:sequence")
    created_at = text_at(tree, "/p:pxml/p:meta/p:created_at")
    content_sha = text_at(tree, "/p:pxml/p:integrity/p:content_sha256")
    lock_hash = text_at(tree, "/p:pxml/p:payload/p:acceptance_lock_hash")
    write_intent_text = text_at(tree, "/p:pxml/p:payload/p:write_intent")

    patch_mode = text_at(tree, "/p:pxml/p:payload/p:patch_constraints/p:patch_mode")
    max_files_text = text_at(tree, "/p:pxml/p:payload/p:patch_constraints/p:max_files")
    rewrite_approved_text = text_at(
        tree,
        "/p:pxml/p:payload/p:patch_constraints/p:rewrite_exception_approved",
    )
    rewrite_reason = text_at(
        tree, "/p:pxml/p:payload/p:patch_constraints/p:rewrite_exception_reason"
    )

    required = {
        "doc_id": doc_id,
        "task_id": task_id,
        "run_id": run_id,
        "sequence": seq_text,
        "created_at": created_at,
        "content_sha256": content_sha,
        "acceptance_lock_hash": lock_hash,
        "patch_mode": patch_mode,
        "max_files": max_files_text,
        "rewrite_exception_approved": rewrite_approved_text,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise ValueError(
            "execution_packet missing required fields: " + ", ".join(sorted(missing))
        )

    in_scope_nodes = tree.xpath(
        "/p:pxml/p:payload/p:in_scope/p:item", namespaces=XPATH_NS
    )
    out_scope_nodes = tree.xpath(
        "/p:pxml/p:payload/p:out_of_scope/p:item", namespaces=XPATH_NS
    )
    expected_nodes = tree.xpath(
        "/p:pxml/p:payload/p:expected_files/p:file", namespaces=XPATH_NS
    )
    acceptance_nodes = tree.xpath(
        "/p:pxml/p:payload/p:acceptance_checks/p:check", namespaces=XPATH_NS
    )
    localization_nodes = tree.xpath(
        "/p:pxml/p:payload/p:localization_targets/p:item/text()", namespaces=XPATH_NS
    )
    intended_behavior_nodes = tree.xpath(
        "/p:pxml/p:payload/p:intended_behaviors/p:item", namespaces=XPATH_NS
    )
    proof_nodes = tree.xpath(
        "/p:pxml/p:payload/p:proof_requirements/p:proof", namespaces=XPATH_NS
    )
    requirement_nodes = tree.xpath(
        "/p:pxml/p:payload/p:requirement_status_matrix/p:requirement",
        namespaces=XPATH_NS,
    )
    packet_completion_state = text_at(tree, "/p:pxml/p:payload/p:completion_state")
    if not in_scope_nodes:
        raise ValueError("execution_packet has empty in_scope")
    if not out_scope_nodes:
        raise ValueError("execution_packet has empty out_of_scope")
    if not expected_nodes:
        raise ValueError("execution_packet has empty expected_files")
    if not acceptance_nodes:
        raise ValueError("execution_packet has empty acceptance_checks")

    in_scope: List[str] = []
    for node in in_scope_nodes:
        item = (node.text or "").strip()
        if item:
            in_scope.append(normalize_rel_path(item))

    out_scope: List[str] = []
    for node in out_scope_nodes:
        item = (node.text or "").strip()
        if item:
            out_scope.append(normalize_rel_path(item))

    expected_files: List[ExpectedFile] = []
    for node in expected_nodes:
        node_tree = etree.ElementTree(node)
        file_path = text_at(node_tree, "./p:path")
        mode = text_at(node_tree, "./p:mode")
        if file_path is None or mode is None:
            raise ValueError("expected_files entry missing path or mode")
        normalized_path = normalize_rel_path(file_path)
        if mode not in {"modify", "create"}:
            raise ValueError(f"Unsupported expected file mode: {mode}")
        expected_files.append(ExpectedFile(path=normalized_path, mode=mode))

    intended_behaviors: List[str] = []
    for node in intended_behavior_nodes:
        item = (node.text or "").strip()
        if item:
            intended_behaviors.append(item)

    proof_requirements: List[ProofRequirement] = []
    for node in proof_nodes:
        node_tree = etree.ElementTree(node)
        category = text_at(node_tree, "./p:proof_category")
        required_text = text_at(node_tree, "./p:required")
        method = text_at(node_tree, "./p:proof_method")
        evidence = text_at(node_tree, "./p:minimum_evidence")
        if (
            category is None
            or required_text is None
            or method is None
            or evidence is None
        ):
            continue
        proof_requirements.append(
            ProofRequirement(
                proof_category=category,
                required=required_text.lower() == "true",
                proof_method=method,
                minimum_evidence=evidence,
            )
        )

    requirement_targets: List[RequirementTarget] = []
    for node in requirement_nodes:
        node_tree = etree.ElementTree(node)
        requirement_text = text_at(node_tree, "./p:requirement")
        proof_method = text_at(node_tree, "./p:proof_method")
        status_target = text_at(node_tree, "./p:status_target")
        minimum_evidence = text_at(node_tree, "./p:minimum_evidence")
        next_step = text_at(node_tree, "./p:next_step_if_missing")
        if (
            requirement_text is None
            or proof_method is None
            or status_target is None
            or minimum_evidence is None
            or next_step is None
        ):
            continue
        requirement_targets.append(
            RequirementTarget(
                requirement=requirement_text,
                proof_method=proof_method,
                status_target=status_target,
                minimum_evidence=minimum_evidence,
                next_step_if_missing=next_step,
            )
        )

    assert doc_id is not None
    assert task_id is not None
    assert run_id is not None
    assert seq_text is not None
    assert created_at is not None
    assert content_sha is not None
    assert lock_hash is not None
    assert patch_mode is not None
    assert max_files_text is not None
    assert rewrite_approved_text is not None

    if not max_files_text.isdigit() or int(max_files_text) < 1:
        raise ValueError("patch_constraints/max_files must be positive integer")

    rewrite_approved = rewrite_approved_text.lower() == "true"
    write_intent = True
    if write_intent_text is not None:
        write_intent = write_intent_text.lower() == "true"
    baseline_exploration_doc_id = text_at(
        tree, "/p:pxml/p:payload/p:exploration_notes_ref/p:doc_id"
    )
    localization_targets = [
        item.strip() for item in localization_nodes if item and item.strip()
    ]
    return PacketInfo(
        path=path,
        doc_id=doc_id,
        task_id=task_id,
        run_id=run_id,
        sequence=int(seq_text),
        created_at=created_at,
        content_sha256=content_sha,
        acceptance_lock_hash=lock_hash,
        write_intent=write_intent,
        in_scope=in_scope,
        out_of_scope=out_scope,
        expected_files=expected_files,
        patch_mode=patch_mode,
        max_files=int(max_files_text),
        rewrite_exception_approved=rewrite_approved,
        rewrite_exception_reason=rewrite_reason,
        acceptance_check_count=len(acceptance_nodes),
        intended_behaviors=intended_behaviors,
        proof_requirements=proof_requirements,
        requirement_targets=requirement_targets,
        completion_state=packet_completion_state,
        baseline_exploration_doc_id=baseline_exploration_doc_id,
        localization_targets=localization_targets,
    )


def load_runtime_policy_rule_names(path: Path) -> set[str]:
    if not path.exists():
        return set()
    tree = etree.parse(str(path))
    names = tree.xpath(
        "/p:pxml/p:payload/p:rules/p:rule/p:rule_name/text()", namespaces=XPATH_NS
    )
    return {item.strip() for item in names if item and item.strip()}


def load_retry_policy(path: Path) -> RetryPolicyConfig:
    config = RetryPolicyConfig()
    if not path.exists():
        return config
    tree = etree.parse(str(path))
    nodes = tree.xpath("/p:pxml/p:payload/p:rules/p:rule", namespaces=XPATH_NS)
    for node in nodes:
        node_tree = etree.ElementTree(node)
        applies_to = node.xpath("./p:applies_to/p:item/text()", namespaces=XPATH_NS)
        applies = {item.strip().lower() for item in applies_to if item.strip()}
        attempts_text = text_at(node_tree, "./p:max_attempts")
        if "implementer" in applies and attempts_text and attempts_text.isdigit():
            config.implementer_max_attempts = int(attempts_text)
            break
    if config.implementer_max_attempts < 1:
        config.implementer_max_attempts = 1
    return config


def load_escalation_policy(path: Path) -> EscalationPolicyConfig:
    config = EscalationPolicyConfig()
    if not path.exists():
        return config
    tree = etree.parse(str(path))
    stop_after = text_at(tree, "/p:pxml/p:payload/p:stop_after_escalation")
    if stop_after is not None:
        config.stop_after_escalation = stop_after.lower() == "true"
    return config


def make_result_doc_id(task_id: str, packet_sequence: int, retry_count: int) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    task_token = sanitize(task_id)[:18]
    suffix = hashlib.sha256(
        f"{task_id}:{packet_sequence}:{retry_count}:{stamp}".encode("utf-8")
    ).hexdigest()[:8]
    doc_id = f"doc_implres_{task_token}_{packet_sequence:04d}_{stamp}_{suffix}"
    if re.fullmatch(r"doc_[a-z0-9][a-z0-9._-]{5,63}", doc_id):
        return doc_id
    return f"doc_implres_{packet_sequence:04d}_{suffix}"


def load_failure_entries(runtime_root: Path, task_id: str) -> List[Dict[str, object]]:
    index_path = runtime_root / "index" / "failures" / f"{sanitize(task_id)}.json"
    if not index_path.exists():
        return []
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    entries = payload.get("entries")
    if isinstance(entries, list):
        return [item for item in entries if isinstance(item, dict)]
    return []


def append_failure_entry(
    runtime_root: Path,
    task_id: str,
    doc_id: str,
    status: str,
    reason_code: Optional[str],
    retry_count: int,
    escalation_requested: bool,
) -> None:
    index_path = runtime_root / "index" / "failures" / f"{sanitize(task_id)}.json"
    ensure_dir(index_path.parent)
    payload: Dict[str, object] = {"task_id": task_id, "entries": []}
    if index_path.exists():
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {"task_id": task_id, "entries": []}
    entries = payload.get("entries")
    if not isinstance(entries, list):
        entries = []
    entries.append(
        {
            "timestamp": now_iso(),
            "time": now_iso(),
            "doc_id": doc_id,
            "artifact_ref": {
                "doc_id": doc_id,
                "doc_class": "implementer_result",
                "relation": "latest_implementer_result",
            },
            "status": status,
            "reason_code": reason_code,
            "retry_count": retry_count,
            "escalation_requested": escalation_requested,
        }
    )
    payload["task_id"] = task_id
    payload["entries"] = entries
    index_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def marker_for_path(path: str, task_id: str) -> str:
    if path.endswith(".md"):
        return f"<!-- implementer_runner {task_id} patch evidence -->"
    return f"# implementer_runner {task_id} patch evidence"


def build_evidence_files(
    runtime_root: Path,
    task_id: str,
    retry_count: int,
    status: str,
    blocked_reason: Optional[str],
    operations: Sequence[Tuple[str, str]],
    applied_lines: Sequence[str],
) -> List[str]:
    logs_dir = runtime_root / "implementer" / "logs"
    ensure_dir(logs_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    token = sanitize(task_id)

    json_path = logs_dir / f"{token}_attempt{retry_count}_{stamp}_evidence.json"
    json_payload = {
        "task_id": task_id,
        "time": now_iso(),
        "status": status,
        "blocked_reason": blocked_reason,
        "retry_count": retry_count,
        "operations": [{"mode": mode, "path": path} for mode, path in operations],
    }
    json_path.write_text(
        json.dumps(json_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    patch_path = logs_dir / f"{token}_attempt{retry_count}_{stamp}_patch.diff"
    patch_lines: List[str] = ["# implementer patch evidence"]
    patch_lines.extend(applied_lines)
    if blocked_reason is not None:
        patch_lines.append(f"# blocked_reason={blocked_reason}")
    patch_path.write_text("\n".join(patch_lines) + "\n", encoding="utf-8")

    return [
        str(json_path.relative_to(runtime_root)).replace("\\", "/"),
        str(patch_path.relative_to(runtime_root)).replace("\\", "/"),
    ]


def execute_packet(
    packet: PacketInfo,
    workspace_root: Path,
    runtime_root: Path,
    retry_policy: RetryPolicyConfig,
) -> RunResult:
    notes: List[str] = []
    blocked_reason: Optional[str] = None
    operations: List[Tuple[str, str, Path]] = []

    if packet.patch_mode == "full_rewrite_exception":
        if not packet.rewrite_exception_approved or not packet.rewrite_exception_reason:
            blocked_reason = "implementer_rewrite_exception_unapproved"
            notes.append(
                "packet patch_mode full_rewrite_exception is not approved by constraints"
            )

    if blocked_reason is None and len(packet.expected_files) > packet.max_files:
        blocked_reason = "implementer_max_files_exceeded"
        notes.append("expected_files exceeds patch_constraints max_files")

    if blocked_reason is None and not packet.write_intent:
        blocked_reason = "implementer_write_intent_disabled"
        notes.append(
            "execution_packet write_intent=false rejects implementer write admission"
        )

    if blocked_reason is None:
        for item in packet.expected_files:
            if not path_in_prefixes(item.path, packet.in_scope):
                blocked_reason = "implementer_expected_outside_scope"
                notes.append(f"expected file not in in_scope: {item.path}")
                break
            if path_in_prefixes(item.path, packet.out_of_scope):
                blocked_reason = "implementer_out_of_scope_violation"
                notes.append(f"expected file collides with out_of_scope: {item.path}")
                break

    if blocked_reason is None:
        for item in packet.expected_files:
            target = to_workspace_path(workspace_root, item.path)
            if item.mode == "modify" and not target.exists():
                blocked_reason = "implementer_modify_target_missing"
                notes.append(f"modify target does not exist: {item.path}")
                break
            if item.mode == "create" and target.exists():
                blocked_reason = "implementer_create_target_exists"
                notes.append(f"create target already exists: {item.path}")
                break
            operations.append((item.mode, item.path, target))

    previous_entries = load_failure_entries(runtime_root, packet.task_id)
    retry_count = 0
    escalation_requested = False
    status = "applied"

    modified_files: List[str] = []
    created_files: List[str] = []
    evidence_lines: List[str] = []

    if blocked_reason is not None:
        prior_same = 0
        for entry in previous_entries:
            entry_status = str(entry.get("status") or "")
            entry_reason = str(entry.get("reason_code") or "")
            if (
                entry_status in {"blocked", "retry_failed"}
                and entry_reason == blocked_reason
            ):
                prior_same += 1
        retry_count = prior_same + 1
        if retry_count >= retry_policy.implementer_max_attempts:
            status = "retry_failed"
            escalation_requested = True
            notes.append("retry threshold reached for same blocked reason")
        else:
            status = "blocked"
            escalation_requested = False
        evidence_paths = build_evidence_files(
            runtime_root=runtime_root,
            task_id=packet.task_id,
            retry_count=retry_count,
            status=status,
            blocked_reason=blocked_reason,
            operations=[(mode, path) for mode, path, _ in operations],
            applied_lines=evidence_lines,
        )
        return RunResult(
            status=status,
            blocked_reason=blocked_reason,
            retry_count=retry_count,
            escalation_requested=escalation_requested,
            modified_files=modified_files,
            created_files=created_files,
            evidence_paths=evidence_paths,
            notes=notes
            or ["implementer execution blocked by packet/runtime conformance guard"],
            context_refs=[],
            context_files=[],
        )

    changes_made = False
    for mode, rel_path, target_path in operations:
        ensure_dir(target_path.parent)
        marker = marker_for_path(rel_path, packet.task_id)
        if mode == "create":
            target_path.write_text(marker + "\n", encoding="utf-8")
            created_files.append(rel_path)
            evidence_lines.append(f"+++ {rel_path}")
            evidence_lines.append(f"+{marker}")
            changes_made = True
            continue

        existing_text = target_path.read_text(encoding="utf-8", errors="ignore")
        if marker in existing_text:
            notes.append(f"patch marker already present: {rel_path}")
            continue
        with target_path.open("a", encoding="utf-8") as handle:
            if existing_text and not existing_text.endswith("\n"):
                handle.write("\n")
            handle.write(marker + "\n")
        modified_files.append(rel_path)
        evidence_lines.append(f"+++ {rel_path}")
        evidence_lines.append(f"+{marker}")
        changes_made = True

    retry_count = 0
    status = "applied" if changes_made else "no_op"
    if status == "no_op" and not notes:
        notes.append("no file changes were needed for expected_files")

    evidence_paths = build_evidence_files(
        runtime_root=runtime_root,
        task_id=packet.task_id,
        retry_count=retry_count,
        status=status,
        blocked_reason=None,
        operations=[(mode, path) for mode, path, _ in operations],
        applied_lines=evidence_lines,
    )
    return RunResult(
        status=status,
        blocked_reason=None,
        retry_count=retry_count,
        escalation_requested=False,
        modified_files=modified_files,
        created_files=created_files,
        evidence_paths=evidence_paths,
        notes=notes,
        context_refs=[],
        context_files=[],
    )


def implementer_completion_state(result_status: str) -> str:
    if result_status == "blocked":
        return "blocked"
    if result_status in {"retry_failed", "escalated"}:
        return "failed"
    if result_status in {"applied", "no_op"}:
        return "implemented_but_unverified"
    return "partial"


def proof_status_from_result(
    result_status: str, proof_requirements: Sequence[ProofRequirement]
) -> Dict[str, str]:
    status: Dict[str, str] = {
        "structural": "NOT-RUN",
        "behavioral": "NOT-RUN",
        "regression": "NOT-RUN",
    }
    if result_status in {"blocked", "retry_failed", "escalated"}:
        status["structural"] = "FAIL"
        for requirement in proof_requirements:
            if requirement.required:
                status[requirement.proof_category] = "FAIL"
        return status
    return status


def requirement_matrix_from_result(
    packet: PacketInfo, result: RunResult
) -> List[Dict[str, str]]:
    targets = list(packet.requirement_targets)
    if not targets and packet.intended_behaviors:
        targets = [
            RequirementTarget(
                requirement=item,
                proof_method="post_implement_verifier",
                status_target="PASS",
                minimum_evidence="required proof categories",
                next_step_if_missing="Run verifier and update status matrix",
            )
            for item in packet.intended_behaviors
        ]
    if not targets:
        targets = [
            RequirementTarget(
                requirement="Task outcome is satisfied",
                proof_method="post_implement_verifier",
                status_target="PASS",
                minimum_evidence="required proof categories",
                next_step_if_missing="Run verifier and update status matrix",
            )
        ]

    matrix: List[Dict[str, str]] = []
    for target in targets:
        if result.status in {"blocked", "retry_failed", "escalated"}:
            status = "FAIL"
            reason = result.blocked_reason or "implementation blocked"
            current_evidence = "implementer_result indicates blocked execution"
            next_action = "Fix blocking cause and retry from updated packet"
        else:
            status = "NOT-RUN"
            reason = "behavioral verification pending"
            current_evidence = (
                "implementation applied; verifier evidence not yet attached"
            )
            next_action = target.next_step_if_missing

        matrix.append(
            {
                "requirement": target.requirement,
                "proof_method": target.proof_method,
                "status": status,
                "reason": reason,
                "current_evidence": current_evidence,
                "next_recommended_action": next_action,
            }
        )
    return matrix


def build_implementer_result(
    packet: PacketInfo,
    doc_id: str,
    result: RunResult,
) -> etree._ElementTree:
    root = etree.Element(q("pxml"), nsmap=NSMAP)

    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = doc_id
    etree.SubElement(meta, q("doc_class")).text = "implementer_result"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = packet.task_id
    etree.SubElement(meta, q("run_id")).text = packet.run_id
    etree.SubElement(meta, q("sequence")).text = str(
        packet.sequence + 1 + result.retry_count
    )
    etree.SubElement(meta, q("writer_agent")).text = "implementer"
    etree.SubElement(meta, q("created_at")).text = now_iso()

    refs = etree.SubElement(root, q("refs"))
    packet_ref = etree.SubElement(refs, q("ref"))
    etree.SubElement(packet_ref, q("doc_id")).text = packet.doc_id
    etree.SubElement(packet_ref, q("doc_class")).text = "execution_packet"
    etree.SubElement(packet_ref, q("relation")).text = "implementation_target"
    for ref_doc_id, ref_doc_class, ref_relation in result.context_refs:
        extra_ref = etree.SubElement(refs, q("ref"))
        etree.SubElement(extra_ref, q("doc_id")).text = ref_doc_id
        etree.SubElement(extra_ref, q("doc_class")).text = ref_doc_class
        etree.SubElement(extra_ref, q("relation")).text = ref_relation

    payload = etree.SubElement(root, q("payload"))
    payload_packet_ref = etree.SubElement(payload, q("packet_ref"))
    etree.SubElement(payload_packet_ref, q("doc_id")).text = packet.doc_id
    etree.SubElement(payload_packet_ref, q("doc_class")).text = "execution_packet"
    etree.SubElement(payload_packet_ref, q("relation")).text = "implementation_target"

    etree.SubElement(payload, q("task_id")).text = packet.task_id

    modified_node = etree.SubElement(payload, q("modified_files"))
    for file_path in result.modified_files:
        etree.SubElement(modified_node, q("item")).text = file_path

    created_node = etree.SubElement(payload, q("created_files"))
    for file_path in result.created_files:
        etree.SubElement(created_node, q("item")).text = file_path

    etree.SubElement(payload, q("patch_mode_used")).text = packet.patch_mode

    evidence_node = etree.SubElement(payload, q("patch_evidence_refs"))
    for evidence_ref in result.evidence_paths:
        etree.SubElement(evidence_node, q("item")).text = evidence_ref

    etree.SubElement(payload, q("result_status")).text = result.status

    proof_status = proof_status_from_result(result.status, packet.proof_requirements)
    proof_node = etree.SubElement(payload, q("proof_status"))
    etree.SubElement(proof_node, q("structural")).text = proof_status["structural"]
    etree.SubElement(proof_node, q("behavioral")).text = proof_status["behavioral"]
    etree.SubElement(proof_node, q("regression")).text = proof_status["regression"]

    requirement_matrix = requirement_matrix_from_result(packet, result)
    matrix_node = etree.SubElement(payload, q("requirement_status_matrix"))
    for item in requirement_matrix:
        req_node = etree.SubElement(matrix_node, q("requirement"))
        etree.SubElement(req_node, q("requirement")).text = item["requirement"]
        etree.SubElement(req_node, q("proof_method")).text = item["proof_method"]
        etree.SubElement(req_node, q("status")).text = item["status"]
        etree.SubElement(req_node, q("reason")).text = item["reason"]
        etree.SubElement(req_node, q("current_evidence")).text = item[
            "current_evidence"
        ]
        etree.SubElement(req_node, q("next_recommended_action")).text = item[
            "next_recommended_action"
        ]

    completion_state = implementer_completion_state(result.status)
    etree.SubElement(payload, q("completion_state")).text = completion_state

    if result.blocked_reason:
        etree.SubElement(payload, q("blocked_reason")).text = result.blocked_reason
    etree.SubElement(payload, q("retry_count")).text = str(result.retry_count)
    etree.SubElement(payload, q("escalation_requested")).text = (
        "true" if result.escalation_requested else "false"
    )

    notes_node = etree.SubElement(payload, q("notes"))
    note_items = result.notes or ["implementer runtime completed"]
    for item in note_items:
        etree.SubElement(notes_node, q("item")).text = item

    integrity = etree.SubElement(root, q("integrity"))
    content_hash = compute_content_hash(meta, refs, payload)
    etree.SubElement(integrity, q("content_sha256")).text = content_hash
    etree.SubElement(integrity, q("parent_sha256")).text = packet.content_sha256

    return etree.ElementTree(root)


def write_xml(tree: etree._ElementTree, path: Path) -> None:
    ensure_dir(path.parent)
    tree.write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True)


def update_indexes(
    runtime_root: Path,
    task_id: str,
    doc_id: str,
    result_path: Path,
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
    current["latest_implementer_result"] = str(result_path.relative_to(runtime_root))
    current["updated_at"] = now_iso()
    task_index_path.write_text(
        json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    artifact_index = {
        "doc_id": doc_id,
        "doc_class": "implementer_result",
        "task_id": task_id,
        "path": str(result_path.relative_to(runtime_root)),
        "updated_at": now_iso(),
    }
    (artifact_index_dir / f"{doc_id}.json").write_text(
        json.dumps(artifact_index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_validation(
    validator: Path, result_path: Path, context_files: Sequence[Path]
) -> None:
    with tempfile.TemporaryDirectory(prefix="pxml_impl_validate_") as temp_dir:
        temp_root = Path(temp_dir)
        copied_result = temp_root / result_path.name
        shutil.copy2(result_path, copied_result)
        for file_path in context_files:
            shutil.copy2(file_path, temp_root / file_path.name)

        command = [
            sys.executable,
            str(validator),
            str(copied_result),
            "--context-dir",
            str(temp_root),
        ]
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Validation failed for {result_path}")


def append_trace_event(
    trace_script: Path,
    runtime_root: Path,
    task_id: str,
    event_type: str,
    message: str,
    artifact_files: Sequence[Path],
    lineage_lock_sha256: str,
    reason_code: Optional[str] = None,
    attempt: Optional[int] = None,
) -> None:
    command = [
        sys.executable,
        str(trace_script),
        "--task-id",
        task_id,
        "--event-type",
        event_type,
        "--actor",
        "implementer",
        "--message",
        message,
        "--runtime-root",
        str(runtime_root),
        "--lineage-lock-sha256",
        lineage_lock_sha256,
    ]
    if reason_code:
        command.extend(["--reason-code", reason_code])
    if attempt is not None:
        command.extend(["--attempt", str(attempt)])
    for artifact in artifact_files:
        command.extend(["--artifact-file", str(artifact)])
    run = subprocess.run(command, check=False)
    if run.returncode != 0:
        raise RuntimeError(f"trace append failed for event {event_type}")


def maybe_request_context_refresh(
    *,
    repo_root: Path,
    packet: PacketInfo,
    result: RunResult,
    workspace_root: Path,
    runtime_root: Path,
    skip_validate: bool,
) -> RunResult:
    if result.blocked_reason != "implementer_modify_target_missing":
        return result
    focus_questions = [
        "Which existing file or symbol owns the intended modify target?",
        "Does the current packet localization point to the wrong ownership boundary?",
    ]
    target_hints = [
        item.path for item in packet.expected_files if item.mode == "modify"
    ] + list(packet.localization_targets)
    outcome = run_manager_mediated_refresh(
        repo_root=repo_root,
        runtime_root=runtime_root,
        workspace_root=workspace_root,
        packet_path=packet.path,
        task_id=packet.task_id,
        baseline_exploration_doc_id=packet.baseline_exploration_doc_id,
        requester_agent="implementer",
        request_kind="ownership_trace",
        reason_code=result.blocked_reason,
        focus_questions=focus_questions,
        target_hints=target_hints,
        contract_change_suspected=True,
        request_context_path=None,
        blocking=True,
        skip_validate=skip_validate,
    )
    result.notes.extend(outcome.notes)
    if outcome.request_ref is not None:
        result.context_refs.append(outcome.request_ref)
    if outcome.result_ref is not None:
        result.context_refs.append(outcome.result_ref)
    if outcome.request_path is not None:
        result.context_files.append(outcome.request_path)
    if outcome.result_path is not None:
        result.context_files.append(outcome.result_path)
    if outcome.actionability == "contract_refresh_required":
        result.escalation_requested = True
        result.notes.append(
            "Focused context refresh indicates manager packet reissue is required before implementation can continue."
        )
    return result


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Apply implementer runtime execution from execution_packet."
    )
    parser.add_argument(
        "--packet", required=True, type=Path, help="Execution packet path."
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=None,
        help="Runtime root directory.",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=repo_root,
        help="Workspace root where expected_files are materialized.",
    )
    parser.add_argument(
        "--validator",
        type=Path,
        default=repo_root / "scripts" / "pxml_validator.py",
        help="PXML validator path.",
    )
    parser.add_argument(
        "--trace-script",
        type=Path,
        default=repo_root / "scripts" / "trace_appender.py",
        help="Trace appender script path.",
    )
    parser.add_argument(
        "--runtime-policy",
        type=Path,
        default=repo_root / "instructions" / "implementer_runtime_policy.pxml",
        help="Implementer runtime policy artifact path.",
    )
    parser.add_argument(
        "--retry-policy",
        type=Path,
        default=repo_root / "instructions" / "retry_policy.pxml",
        help="Retry policy artifact path.",
    )
    parser.add_argument(
        "--escalation-policy",
        type=Path,
        default=repo_root / "instructions" / "escalation_policy.pxml",
        help="Escalation policy artifact path.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip implementer_result validation.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    packet_path = args.packet.resolve()
    workspace_root = args.workspace_root.resolve()
    if not workspace_root.exists():
        print(f"ERROR: workspace root not found: {workspace_root}", file=sys.stderr)
        return 2

    runtime_ready = bootstrap_runtime(
        cli_runtime_root=args.runtime_root,
        workspace_root=workspace_root,
    )
    if not runtime_ready.ready:
        print(f"ERROR: {runtime_ready.failure_line()}", file=sys.stderr)
        return 2
    runtime_root = runtime_ready.runtime_root
    print(runtime_ready.success_line("implementer_runner"))

    validator_path = args.validator.resolve()
    trace_script_path = args.trace_script.resolve()

    if not packet_path.exists():
        print(f"ERROR: execution_packet not found: {packet_path}", file=sys.stderr)
        return 2
    if not trace_script_path.exists():
        print(f"ERROR: trace script not found: {trace_script_path}", file=sys.stderr)
        return 2

    runtime_rule_names = load_runtime_policy_rule_names(args.runtime_policy.resolve())
    if runtime_rule_names:
        missing_rules = sorted(REQUIRED_RUNTIME_RULES - runtime_rule_names)
        if missing_rules:
            print(
                "ERROR: implementer_runtime_policy missing required rules: "
                + ", ".join(missing_rules),
                file=sys.stderr,
            )
            return 2

    retry_policy = load_retry_policy(args.retry_policy.resolve())
    escalation_policy = load_escalation_policy(args.escalation_policy.resolve())

    try:
        packet = parse_packet(packet_path)
    except Exception as exc:
        print(f"ERROR: failed to parse execution_packet: {exc}", file=sys.stderr)
        return 2

    ensure_dir(runtime_root / "implementer" / "results")
    ensure_dir(runtime_root / "implementer" / "logs")
    ensure_dir(runtime_root / "index" / "failures")

    try:
        append_trace_event(
            trace_script=trace_script_path,
            runtime_root=runtime_root,
            task_id=packet.task_id,
            event_type="implement_start",
            message="Implementer started execution from execution_packet contract.",
            artifact_files=[packet.path],
            lineage_lock_sha256=packet.acceptance_lock_hash,
        )
    except Exception as exc:
        print(f"ERROR: failed to append implement_start: {exc}", file=sys.stderr)
        return 1

    result = execute_packet(
        packet=packet,
        workspace_root=workspace_root,
        runtime_root=runtime_root,
        retry_policy=retry_policy,
    )
    result = maybe_request_context_refresh(
        repo_root=repo_root,
        packet=packet,
        result=result,
        workspace_root=workspace_root,
        runtime_root=runtime_root,
        skip_validate=args.skip_validate,
    )

    result_doc_id = make_result_doc_id(
        packet.task_id, packet.sequence, result.retry_count
    )
    result_tree = build_implementer_result(packet, result_doc_id, result)
    result_path = runtime_root / "implementer" / "results" / f"{result_doc_id}.pxml"
    write_xml(result_tree, result_path)

    if not args.skip_validate:
        if not validator_path.exists():
            print(f"ERROR: validator not found: {validator_path}", file=sys.stderr)
            return 2
        try:
            run_validation(
                validator_path,
                result_path,
                context_files=[packet.path] + result.context_files,
            )
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    latest_path = (
        runtime_root / "latest" / f"{sanitize(packet.task_id)}_implementer_result.pxml"
    )
    ensure_dir(latest_path.parent)
    shutil.copy2(result_path, latest_path)

    update_indexes(runtime_root, packet.task_id, result_doc_id, result_path)
    append_failure_entry(
        runtime_root=runtime_root,
        task_id=packet.task_id,
        doc_id=result_doc_id,
        status=result.status,
        reason_code=result.blocked_reason,
        retry_count=result.retry_count,
        escalation_requested=result.escalation_requested,
    )

    try:
        if result.status in {"applied", "no_op"}:
            append_trace_event(
                trace_script=trace_script_path,
                runtime_root=runtime_root,
                task_id=packet.task_id,
                event_type="patch_applied",
                message=f"Implementer finished with status {result.status}.",
                artifact_files=[result_path],
                lineage_lock_sha256=packet.acceptance_lock_hash,
            )
        elif result.status == "blocked":
            append_trace_event(
                trace_script=trace_script_path,
                runtime_root=runtime_root,
                task_id=packet.task_id,
                event_type="blocked",
                message=f"Implementer blocked: {result.blocked_reason}",
                artifact_files=[result_path],
                lineage_lock_sha256=packet.acceptance_lock_hash,
                reason_code=result.blocked_reason,
                attempt=result.retry_count,
            )
            if result.escalation_requested:
                append_trace_event(
                    trace_script=trace_script_path,
                    runtime_root=runtime_root,
                    task_id=packet.task_id,
                    event_type="escalation",
                    message="Implementer requested escalation after focused context refresh required packet reissue.",
                    artifact_files=[result_path] + result.context_files,
                    lineage_lock_sha256=packet.acceptance_lock_hash,
                    reason_code=result.blocked_reason,
                    attempt=result.retry_count,
                )
        elif result.status == "retry_failed":
            append_trace_event(
                trace_script=trace_script_path,
                runtime_root=runtime_root,
                task_id=packet.task_id,
                event_type="blocked",
                message=f"Implementer blocked: {result.blocked_reason}",
                artifact_files=[result_path],
                lineage_lock_sha256=packet.acceptance_lock_hash,
                reason_code=result.blocked_reason,
                attempt=result.retry_count,
            )
            append_trace_event(
                trace_script=trace_script_path,
                runtime_root=runtime_root,
                task_id=packet.task_id,
                event_type="retry_failed",
                message=f"Retry limit reached for reason {result.blocked_reason}.",
                artifact_files=[result_path],
                lineage_lock_sha256=packet.acceptance_lock_hash,
                reason_code=result.blocked_reason,
                attempt=result.retry_count,
            )
            append_trace_event(
                trace_script=trace_script_path,
                runtime_root=runtime_root,
                task_id=packet.task_id,
                event_type="escalation",
                message="Implementer requested escalation after retry limit.",
                artifact_files=[result_path],
                lineage_lock_sha256=packet.acceptance_lock_hash,
                reason_code="implementer_retry_limit_reached",
                attempt=result.retry_count,
            )
            if escalation_policy.stop_after_escalation:
                append_trace_event(
                    trace_script=trace_script_path,
                    runtime_root=runtime_root,
                    task_id=packet.task_id,
                    event_type="stop",
                    message="Execution stopped after implementer escalation.",
                    artifact_files=[result_path],
                    lineage_lock_sha256=packet.acceptance_lock_hash,
                    reason_code="implementer_retry_limit_reached",
                    attempt=result.retry_count,
                )
    except Exception as exc:
        print(f"ERROR: failed to append trace event(s): {exc}", file=sys.stderr)
        return 1

    print(f"Generated implementer_result: {result_path}")
    print(f"result_status={result.status}")
    print(f"retry_count={result.retry_count}")
    print(f"escalation_requested={str(result.escalation_requested).lower()}")
    if result.blocked_reason:
        print(f"blocked_reason={result.blocked_reason}")

    if result.status in {"blocked", "retry_failed", "escalated"}:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
