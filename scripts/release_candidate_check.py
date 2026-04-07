#!/usr/bin/env python3
"""Batch 11 release candidate aggregation and handoff manifest builder."""

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
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

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

DEFAULT_ENTRYPOINTS = [
    "cleanup_task_runtime",
    "task_executor",
    "operator_preflight",
    "final_renderer",
    "operator_runbook",
    "runtime_prune",
    "session_report_refresh",
    "release_candidate_check",
    "release_ops_gate",
]

DEFAULT_CANDIDATE_TASK_IDS = [
    "task_impl_feature_direct_001",
    "task_verify_post_smoke_001",
]

REQUIRED_CANDIDATE_LATEST_KEYS = [
    "latest_manager_route",
    "latest_execution_packet",
    "latest_task_status_report",
    "latest_operator_preflight_report",
    "latest_execution_trace",
]


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
class HarnessResult:
    task_id: str
    result: str
    details: str


@dataclass
class TaskSnapshot:
    task_id: str
    latest_paths: Dict[str, Path]
    route_ref: Optional[Tuple[str, str]]
    selected_path: str
    status_value: str
    readiness: str
    render_mode: str
    lineage_ok: Optional[bool]
    verify_phases: set[str]
    pruning_quarantine_count: int
    pruning_delete_count: int
    missing_latest_keys: List[str]
    broken_latest_keys: List[str]


@dataclass
class ReleaseGateProfile:
    doc_id: str
    profile_id: str
    profile_name: str
    profile_version: Optional[str]
    profile_owner: Optional[str]
    last_change_reason: Optional[str]
    approval_ref: Optional[str]
    override_reason: Optional[str]
    coverage_task_ids: List[str]
    candidate_gate_task_ids: List[str]
    allow_caution_rc: bool
    required_lane_coverage: List[str]
    required_ready_cases: int
    required_pruning_branches: List[str]
    required_release_artifacts: List[str]
    required_entrypoints: List[str]
    notes: List[str]


@dataclass
class CoveragePolicyRule:
    rule_name: str
    source_kind: str
    source_value: str
    classification: str
    affects_gate: bool
    rationale: str
    recommended_operator_action: str


@dataclass
class CoverageOutcomePolicy:
    doc_id: str
    policy_name: str
    rules: Dict[Tuple[str, str], CoveragePolicyRule]


@dataclass
class CoverageOutcome:
    task_id: str
    source_kind: str
    source_value: str
    classification: str
    affects_gate: bool
    rule_name: str
    recommended_operator_action: str


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


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


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
    normalized = text.strip()
    return normalized or None


def discover_pxml_files(path: Path) -> List[Path]:
    if not path.exists():
        return []
    files = [candidate for candidate in path.rglob("*.pxml") if candidate.is_file()]
    files.sort()
    return files


def parse_artifact(path: Path) -> Optional[ArtifactInfo]:
    try:
        tree = etree.parse(str(path))
    except (OSError, etree.XMLSyntaxError):
        return None

    doc_id = text_at(tree, "/p:pxml/p:meta/p:doc_id")
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    task_id = text_at(tree, "/p:pxml/p:meta/p:task_id")
    sequence_text = text_at(tree, "/p:pxml/p:meta/p:sequence")
    created_at = text_at(tree, "/p:pxml/p:meta/p:created_at")
    if (
        doc_id is None
        or doc_class is None
        or task_id is None
        or sequence_text is None
        or created_at is None
    ):
        return None
    try:
        sequence = int(sequence_text)
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


def to_runtime_rel(path: Path, runtime_root: Path) -> str:
    try:
        rel = path.relative_to(runtime_root)
        return str(rel).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def resolve_runtime_path(runtime_root: Path, value: str) -> Path:
    normalized = value.replace("\\", "/").lstrip("/")
    return (runtime_root / normalized).resolve()


def parse_doc_ref(path: Path) -> Optional[Tuple[str, str]]:
    artifact = parse_artifact(path)
    if artifact is None:
        return None
    return artifact.doc_id, artifact.doc_class


def next_sequence(runtime_root: Path, task_id: str) -> int:
    maximum = 0
    for path in discover_pxml_files(runtime_root):
        parsed = parse_artifact(path)
        if parsed is None or parsed.task_id != task_id:
            continue
        maximum = max(maximum, parsed.sequence)
    return maximum + 1


def make_doc_id(prefix: str, token: str, sequence: int) -> str:
    candidate = f"doc_{prefix}_{token[:20]}_{sequence:04d}"
    if re.fullmatch(r"doc_[a-z0-9][a-z0-9._-]{5,63}", candidate):
        return candidate
    suffix = sha256_hex(f"{prefix}:{token}:{sequence}".encode("utf-8"))[:10]
    return f"doc_{prefix}_{suffix}_{sequence:04d}"


def parse_task_file(path: Path) -> Optional[Dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def collect_default_smoke_set(runtime_root: Path) -> List[str]:
    tasks_dir = runtime_root / "index" / "tasks"
    task_ids: List[str] = []
    for path in sorted(tasks_dir.glob("task_*.json")):
        payload = parse_task_file(path)
        if payload is None:
            continue
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id.startswith("task_"):
            continue
        if task_id.startswith("task_release_candidate"):
            continue
        if (
            "latest_manager_route" not in payload
            or "latest_execution_packet" not in payload
        ):
            continue
        task_ids.append(task_id)
    return sorted(set(task_ids))


def load_smoke_set_file(path: Path) -> List[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"failed to read task set file '{path}': {exc}") from exc

    task_ids: List[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        task_ids.append(line)
    return task_ids


def unique_preserve(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def load_latest_artifact(
    latest_paths: Dict[str, Path],
    key: str,
    expected_doc_class: str,
    broken_latest_keys: List[str],
) -> Optional[ArtifactInfo]:
    path = latest_paths.get(key)
    if path is None or not path.exists():
        return None
    artifact = parse_artifact(path)
    if artifact is None:
        broken_latest_keys.append(key)
        return None
    if artifact.doc_class != expected_doc_class:
        broken_latest_keys.append(key)
        return None
    return artifact


def load_task_snapshot(runtime_root: Path, task_id: str) -> Optional[TaskSnapshot]:
    task_index_path = runtime_root / "index" / "tasks" / f"{sanitize(task_id)}.json"
    if not task_index_path.exists():
        return None

    payload = parse_task_file(task_index_path)
    if payload is None:
        return None

    latest_paths = pick_latest_paths(payload, runtime_root)
    missing_latest_keys = sorted(
        [key for key, path in latest_paths.items() if not path.exists()]
    )
    broken_latest_keys: List[str] = []

    route_ref: Optional[Tuple[str, str]] = None
    selected_path = "unknown"
    status_value = "unknown"
    route_doc = load_latest_artifact(
        latest_paths,
        "latest_manager_route",
        "manager_route",
        broken_latest_keys,
    )
    if route_doc is not None:
        route_ref = (route_doc.doc_id, route_doc.doc_class)
        selected_path = (
            text_at(route_doc.tree, "/p:pxml/p:payload/p:selected_path") or "unknown"
        )

    readiness = "unknown"
    lineage_ok: Optional[bool] = None
    status_doc = load_latest_artifact(
        latest_paths,
        "latest_task_status_report",
        "task_status_report",
        broken_latest_keys,
    )
    if status_doc is not None:
        status_value = (
            text_at(status_doc.tree, "/p:pxml/p:payload/p:current_status") or "unknown"
        )

    preflight_doc = load_latest_artifact(
        latest_paths,
        "latest_operator_preflight_report",
        "operator_preflight_report",
        broken_latest_keys,
    )
    if preflight_doc is not None:
        readiness = (
            text_at(preflight_doc.tree, "/p:pxml/p:payload/p:render_readiness")
            or "unknown"
        )
        lineage_ok_text = text_at(
            preflight_doc.tree,
            "/p:pxml/p:payload/p:lineage_ok",
        )
        if lineage_ok_text is not None:
            lineage_ok = lineage_ok_text == "true"

    render_mode = "none"
    render_doc = load_latest_artifact(
        latest_paths,
        "latest_final_render_report",
        "final_render_report",
        broken_latest_keys,
    )
    if render_doc is not None:
        render_mode = (
            text_at(render_doc.tree, "/p:pxml/p:payload/p:render_mode") or "unknown"
        )

    verify_phases: set[str] = set()
    verification_doc = load_latest_artifact(
        latest_paths,
        "latest_verification_result",
        "verification_result",
        broken_latest_keys,
    )
    if verification_doc is not None:
        verify_phase = text_at(
            verification_doc.tree,
            "/p:pxml/p:payload/p:verify_phase",
        )
        if verify_phase:
            verify_phases.add(verify_phase)

    trace_doc = load_latest_artifact(
        latest_paths,
        "latest_execution_trace",
        "execution_trace",
        broken_latest_keys,
    )
    if trace_doc is not None:
        rows = trace_doc.tree.xpath(
            "/p:pxml/p:payload/p:events/p:event[p:event_type='verify_done']/p:verify_phase/text()",
            namespaces=XPATH_NS,
        )
        for item in rows:
            value = item.strip()
            if value:
                verify_phases.add(value)

    pruning_quarantine_count = 0
    pruning_delete_count = 0
    pruning_doc = load_latest_artifact(
        latest_paths,
        "latest_pruning_report",
        "pruning_report",
        broken_latest_keys,
    )
    if pruning_doc is not None:
        pruning_quarantine_count = len(
            pruning_doc.tree.xpath(
                "/p:pxml/p:payload/p:quarantine_candidates/p:candidate",
                namespaces=XPATH_NS,
            )
        )
        pruning_delete_count = len(
            pruning_doc.tree.xpath(
                "/p:pxml/p:payload/p:delete_candidates/p:candidate",
                namespaces=XPATH_NS,
            )
        )

    return TaskSnapshot(
        task_id=task_id,
        latest_paths=latest_paths,
        route_ref=route_ref,
        selected_path=selected_path,
        status_value=status_value,
        readiness=readiness,
        render_mode=render_mode,
        lineage_ok=lineage_ok,
        verify_phases=verify_phases,
        pruning_quarantine_count=pruning_quarantine_count,
        pruning_delete_count=pruning_delete_count,
        missing_latest_keys=missing_latest_keys,
        broken_latest_keys=sorted(set(broken_latest_keys)),
    )


def ref_node(
    parent: etree._Element, doc_id: str, doc_class: str, relation: str
) -> None:
    ref = etree.SubElement(parent, q("ref"))
    etree.SubElement(ref, q("doc_id")).text = doc_id
    etree.SubElement(ref, q("doc_class")).text = doc_class
    etree.SubElement(ref, q("relation")).text = relation


def add_items(parent: etree._Element, values: Sequence[str]) -> None:
    for value in values:
        etree.SubElement(parent, q("item")).text = value


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


def run_harness(
    harness_validator: Path,
    runtime_root: Path,
    task_id: str,
    release_readiness: bool,
) -> HarnessResult:
    cmd = [
        sys.executable,
        str(harness_validator),
        "--task-id",
        task_id,
        "--runtime-root",
        str(runtime_root),
    ]
    if release_readiness:
        cmd.append("--release-readiness")
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    result = "error"
    for line in (proc.stdout or "").splitlines():
        normalized = line.strip()
        if normalized.startswith("Result:"):
            value = normalized.split(":", 1)[1].strip().lower()
            if value in {"pass", "fail", "inconclusive"}:
                result = value
            break
    if result == "error":
        if proc.returncode == 0:
            result = "pass"
        elif proc.returncode == 2:
            result = "inconclusive"
        else:
            result = "fail"
    return HarnessResult(task_id=task_id, result=result, details=output)


def read_text_list(
    tree: etree._ElementTree,
    xpath_expr: str,
) -> List[str]:
    values = tree.xpath(xpath_expr, namespaces=XPATH_NS)
    output: List[str] = []
    for item in values:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if normalized:
            output.append(normalized)
    return output


def load_release_gate_profile(path: Path) -> ReleaseGateProfile:
    parsed = parse_artifact(path)
    if parsed is None:
        raise ValueError(f"failed to parse release gate profile: {path}")
    if parsed.doc_class != "release_gate_profile":
        raise ValueError(f"invalid release gate profile doc_class: {parsed.doc_class}")

    profile_id = text_at(parsed.tree, "/p:pxml/p:payload/p:profile_id")
    profile_name = text_at(parsed.tree, "/p:pxml/p:payload/p:profile_name")
    if profile_id is None or profile_name is None:
        raise ValueError("release gate profile is missing profile_id/profile_name")

    coverage_task_ids = read_text_list(
        parsed.tree,
        "/p:pxml/p:payload/p:coverage_task_ids/p:item/text()",
    )
    profile_version = text_at(parsed.tree, "/p:pxml/p:payload/p:profile_version")
    profile_owner = text_at(parsed.tree, "/p:pxml/p:payload/p:profile_owner")
    last_change_reason = text_at(
        parsed.tree,
        "/p:pxml/p:payload/p:last_change_reason",
    )
    approval_ref = text_at(parsed.tree, "/p:pxml/p:payload/p:approval_ref")
    override_reason = text_at(parsed.tree, "/p:pxml/p:payload/p:override_reason")
    candidate_task_ids = read_text_list(
        parsed.tree,
        "/p:pxml/p:payload/p:candidate_gate_task_ids/p:item/text()",
    )
    allow_caution_text = text_at(
        parsed.tree,
        "/p:pxml/p:payload/p:allow_caution_rc",
    )
    allow_caution_rc = allow_caution_text == "true"
    required_lane_coverage = read_text_list(
        parsed.tree,
        "/p:pxml/p:payload/p:required_lane_coverage/p:item/text()",
    )
    required_ready_cases_text = text_at(
        parsed.tree,
        "/p:pxml/p:payload/p:required_ready_cases",
    )
    try:
        required_ready_cases = (
            int(required_ready_cases_text) if required_ready_cases_text else 1
        )
    except ValueError:
        required_ready_cases = 1

    required_pruning_branches = read_text_list(
        parsed.tree,
        "/p:pxml/p:payload/p:required_pruning_branches/p:item/text()",
    )
    required_release_artifacts = read_text_list(
        parsed.tree,
        "/p:pxml/p:payload/p:required_release_artifacts/p:item/text()",
    )
    required_entrypoints = read_text_list(
        parsed.tree,
        "/p:pxml/p:payload/p:required_entrypoints/p:item/text()",
    )
    notes = read_text_list(
        parsed.tree,
        "/p:pxml/p:payload/p:notes/p:item/text()",
    )

    return ReleaseGateProfile(
        doc_id=parsed.doc_id,
        profile_id=profile_id,
        profile_name=profile_name,
        profile_version=profile_version,
        profile_owner=profile_owner,
        last_change_reason=last_change_reason,
        approval_ref=approval_ref,
        override_reason=override_reason,
        coverage_task_ids=unique_preserve(coverage_task_ids),
        candidate_gate_task_ids=unique_preserve(candidate_task_ids),
        allow_caution_rc=allow_caution_rc,
        required_lane_coverage=unique_preserve(required_lane_coverage),
        required_ready_cases=max(required_ready_cases, 1),
        required_pruning_branches=unique_preserve(required_pruning_branches),
        required_release_artifacts=unique_preserve(required_release_artifacts),
        required_entrypoints=unique_preserve(required_entrypoints),
        notes=notes,
    )


def load_coverage_outcome_policy(path: Path) -> CoverageOutcomePolicy:
    parsed = parse_artifact(path)
    if parsed is None:
        raise ValueError(f"failed to parse coverage outcome policy: {path}")
    if parsed.doc_class != "coverage_outcome_policy":
        raise ValueError(
            f"invalid coverage outcome policy doc_class: {parsed.doc_class}"
        )

    policy_name = text_at(parsed.tree, "/p:pxml/p:payload/p:policy_name")
    if policy_name is None:
        raise ValueError("coverage outcome policy is missing payload/policy_name")

    rules: Dict[Tuple[str, str], CoveragePolicyRule] = {}
    rule_nodes = parsed.tree.xpath(
        "/p:pxml/p:payload/p:rules/p:rule",
        namespaces=XPATH_NS,
    )
    for node in rule_nodes:
        node_tree = etree.ElementTree(node)
        rule_name = text_at(node_tree, "./p:rule_name")
        source_kind = text_at(node_tree, "./p:source_kind")
        source_value = text_at(node_tree, "./p:source_value")
        classification = text_at(node_tree, "./p:classification")
        affects_gate_text = text_at(node_tree, "./p:affects_gate")
        rationale = text_at(node_tree, "./p:rationale")
        recommended_action = text_at(node_tree, "./p:recommended_operator_action")
        if (
            rule_name is None
            or source_kind is None
            or source_value is None
            or classification is None
            or affects_gate_text is None
            or rationale is None
            or recommended_action is None
        ):
            continue
        key = (source_kind, source_value)
        if key in rules:
            continue
        rules[key] = CoveragePolicyRule(
            rule_name=rule_name,
            source_kind=source_kind,
            source_value=source_value,
            classification=classification,
            affects_gate=affects_gate_text == "true",
            rationale=rationale,
            recommended_operator_action=recommended_action,
        )

    return CoverageOutcomePolicy(
        doc_id=parsed.doc_id, policy_name=policy_name, rules=rules
    )


def normalize_task_status(value: str) -> str:
    mapping = {
        "passed": "pass",
        "failed": "fail",
        "inconclusive": "inconclusive",
        "blocked": "blocked",
        "retry_failed": "retry_failed",
        "no_op": "no_op",
        "running": "inconclusive",
        "pending": "inconclusive",
        "escalated": "fail",
    }
    return mapping.get(value, value)


def classify_coverage_outcome(
    policy: CoverageOutcomePolicy,
    task_id: str,
    source_kind: str,
    source_value: str,
) -> CoverageOutcome:
    rule = policy.rules.get((source_kind, source_value))
    if rule is None:
        fallback_classification = "info"
        fallback_affects_gate = False
        if source_value in {"fail", "blocked", "retry_failed", "not_ready", "denied"}:
            fallback_classification = "blocker"
            fallback_affects_gate = True
        elif source_value in {"inconclusive", "caution", "rendered_with_warning"}:
            fallback_classification = "warning"
            fallback_affects_gate = True
        return CoverageOutcome(
            task_id=task_id,
            source_kind=source_kind,
            source_value=source_value,
            classification=fallback_classification,
            affects_gate=fallback_affects_gate,
            rule_name="fallback_default",
            recommended_operator_action="Review coverage source and update coverage_outcome_policy if needed.",
        )

    return CoverageOutcome(
        task_id=task_id,
        source_kind=source_kind,
        source_value=source_value,
        classification=rule.classification,
        affects_gate=rule.affects_gate,
        rule_name=rule.rule_name,
        recommended_operator_action=rule.recommended_operator_action,
    )


def load_policy_refs(paths: Sequence[Path]) -> Dict[str, Tuple[str, str]]:
    refs: Dict[str, Tuple[str, str]] = {}
    for path in paths:
        parsed = parse_doc_ref(path)
        if parsed is None:
            continue
        refs[str(path)] = parsed
    return refs


def pick_latest_paths(
    task_payload: Dict[str, object], runtime_root: Path
) -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    for key, value in task_payload.items():
        if not key.startswith("latest_"):
            continue
        if not isinstance(value, str):
            continue
        out[key] = resolve_runtime_path(runtime_root, value)
    return out


def parse_entrypoints_from_workflow(path: Path) -> List[str]:
    if not path.exists():
        return list(DEFAULT_ENTRYPOINTS)
    parsed = parse_artifact(path)
    if parsed is None:
        return list(DEFAULT_ENTRYPOINTS)
    values = parsed.tree.xpath(
        "/p:pxml/p:payload/p:release_handoff_appendix/p:operator_entrypoints/p:item/text()",
        namespaces=XPATH_NS,
    )
    cleaned = [
        item.strip() for item in values if isinstance(item, str) and item.strip()
    ]
    if not cleaned:
        return list(DEFAULT_ENTRYPOINTS)
    return sorted(set(cleaned), key=cleaned.index)


def run_validation(
    validator: Path,
    target: Path,
    context_files: Sequence[Path],
) -> None:
    with tempfile.TemporaryDirectory(
        prefix="pxml_release_candidate_validate_"
    ) as temp_dir:
        temp_root = Path(temp_dir)
        target_copy = temp_root / target.name
        shutil.copy2(target, target_copy)
        for source in context_files:
            if not source.exists():
                continue
            destination = temp_root / source.name
            if destination.exists():
                continue
            shutil.copy2(source, destination)

        cmd = [
            sys.executable,
            str(validator),
            str(target_copy),
            "--context-dir",
            str(temp_root),
        ]
        proc = subprocess.run(cmd, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"Validation failed for {target}")


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description=(
            "Generate release_candidate_report and release_bundle_manifest from smoke evidence."
        )
    )
    parser.add_argument(
        "--coverage-task-id",
        action="append",
        default=[],
        help="Coverage set task_id to include (can be repeated).",
    )
    parser.add_argument(
        "--coverage-set-file",
        type=Path,
        default=None,
        help="Optional line-delimited coverage task_id list.",
    )
    parser.add_argument(
        "--candidate-task-id",
        action="append",
        default=[],
        help="RC candidate gate task_id (can be repeated).",
    )
    parser.add_argument(
        "--candidate-set-file",
        type=Path,
        default=None,
        help="Optional line-delimited candidate gate task_id list.",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        default=[],
        help="Legacy alias for --coverage-task-id (can be repeated).",
    )
    parser.add_argument(
        "--smoke-set-file",
        type=Path,
        default=None,
        help="Legacy alias for --coverage-set-file.",
    )
    parser.add_argument(
        "--use-default-smoke-set",
        action="store_true",
        help="Use runtime/index/tasks as default smoke set source.",
    )
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
        help="release_candidate_policy artifact path.",
    )
    parser.add_argument(
        "--workflow-guide",
        type=Path,
        default=repo_root / "instructions" / "operator_workflow_guide.pxml",
        help="operator_workflow_guide artifact path.",
    )
    parser.add_argument(
        "--runbook-policy",
        type=Path,
        default=repo_root / "instructions" / "operator_runbook_policy.pxml",
        help="operator_runbook_policy artifact path.",
    )
    parser.add_argument(
        "--pruning-policy",
        type=Path,
        default=repo_root / "instructions" / "artifact_pruning_policy.pxml",
        help="artifact_pruning_policy artifact path.",
    )
    parser.add_argument(
        "--trace-semantics",
        type=Path,
        default=repo_root / "instructions" / "trace_event_semantics.pxml",
        help="trace_event_semantics artifact path.",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=repo_root / "instructions" / "release_gate_profile.pxml",
        help="release gate profile artifact path.",
    )
    parser.add_argument(
        "--coverage-policy",
        type=Path,
        default=repo_root / "instructions" / "coverage_outcome_policy.pxml",
        help="coverage outcome policy artifact path.",
    )
    parser.add_argument(
        "--profile-governance-policy",
        type=Path,
        default=repo_root / "instructions" / "release_profile_governance_policy.pxml",
        help="release profile governance policy artifact path.",
    )
    parser.add_argument(
        "--ci-policy",
        type=Path,
        default=repo_root / "instructions" / "ci_exit_code_policy.pxml",
        help="ci exit code policy artifact path.",
    )
    parser.add_argument(
        "--verify-phase-policy",
        type=Path,
        default=repo_root / "instructions" / "verify_phase_audit_policy.pxml",
        help="verify phase audit policy artifact path.",
    )
    parser.add_argument(
        "--harness-validator",
        type=Path,
        default=repo_root / "scripts" / "harness_validator.py",
        help="Harness validator path.",
    )
    parser.add_argument(
        "--validator",
        type=Path,
        default=repo_root / "scripts" / "pxml_validator.py",
        help="PXML validator path.",
    )
    parser.add_argument(
        "--allow-caution-rc",
        action="store_true",
        help="Allow warnings-only outcome to resolve as rc_result=caution.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip generated artifact validation.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime_root = args.runtime_root.resolve()
    validator = args.validator.resolve()
    harness_validator = args.harness_validator.resolve()
    policy_path = args.policy.resolve()
    workflow_path = args.workflow_guide.resolve()
    runbook_policy_path = args.runbook_policy.resolve()
    pruning_policy_path = args.pruning_policy.resolve()
    trace_semantics_path = args.trace_semantics.resolve()
    profile_path = args.profile.resolve()
    coverage_policy_path = args.coverage_policy.resolve()
    profile_governance_path = args.profile_governance_policy.resolve()
    ci_policy_path = args.ci_policy.resolve()
    verify_phase_policy_path = args.verify_phase_policy.resolve()

    if not runtime_root.exists():
        print(f"ERROR: runtime root not found: {runtime_root}", file=sys.stderr)
        return 2
    if not policy_path.exists():
        print(
            f"ERROR: release_candidate_policy not found: {policy_path}",
            file=sys.stderr,
        )
        return 2
    if not harness_validator.exists():
        print(
            f"ERROR: harness validator not found: {harness_validator}", file=sys.stderr
        )
        return 2
    if not profile_path.exists():
        print(
            f"ERROR: release gate profile not found: {profile_path}",
            file=sys.stderr,
        )
        return 2
    try:
        profile = load_release_gate_profile(profile_path)
    except Exception as exc:
        print(f"ERROR: failed to load release gate profile: {exc}", file=sys.stderr)
        return 2
    if coverage_policy_path.exists():
        try:
            coverage_policy = load_coverage_outcome_policy(coverage_policy_path)
        except Exception as exc:
            print(
                f"ERROR: failed to load coverage outcome policy: {exc}",
                file=sys.stderr,
            )
            return 2
    else:
        print(
            f"ERROR: coverage outcome policy not found: {coverage_policy_path}",
            file=sys.stderr,
        )
        return 2
    if not args.skip_validate and not validator.exists():
        print(f"ERROR: validator not found: {validator}", file=sys.stderr)
        return 2

    explicit_coverage_inputs = bool(
        args.coverage_task_id
        or args.task_id
        or args.coverage_set_file is not None
        or args.smoke_set_file is not None
        or args.use_default_smoke_set
    )
    explicit_candidate_inputs = bool(
        args.candidate_task_id or args.candidate_set_file is not None
    )

    coverage_task_ids: List[str] = []
    if explicit_coverage_inputs:
        coverage_task_ids.extend(args.coverage_task_id)
        coverage_task_ids.extend(args.task_id)
        if args.coverage_set_file is not None:
            try:
                coverage_task_ids.extend(
                    load_smoke_set_file(args.coverage_set_file.resolve())
                )
            except ValueError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 2
        if args.smoke_set_file is not None:
            try:
                coverage_task_ids.extend(
                    load_smoke_set_file(args.smoke_set_file.resolve())
                )
            except ValueError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 2
        if args.use_default_smoke_set:
            coverage_task_ids.extend(collect_default_smoke_set(runtime_root))
    elif profile is not None and profile.coverage_task_ids:
        coverage_task_ids.extend(profile.coverage_task_ids)
    else:
        coverage_task_ids.extend(collect_default_smoke_set(runtime_root))
    coverage_task_ids = unique_preserve(coverage_task_ids)

    if not coverage_task_ids:
        print(
            "ERROR: no coverage set tasks selected. Provide coverage tasks or --use-default-smoke-set.",
            file=sys.stderr,
        )
        return 2

    candidate_task_ids: List[str] = []
    if explicit_candidate_inputs:
        candidate_task_ids.extend(args.candidate_task_id)
        if args.candidate_set_file is not None:
            try:
                candidate_task_ids.extend(
                    load_smoke_set_file(args.candidate_set_file.resolve())
                )
            except ValueError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 2
    elif profile is not None and profile.candidate_gate_task_ids:
        candidate_task_ids.extend(profile.candidate_gate_task_ids)
    else:
        candidate_task_ids = list(DEFAULT_CANDIDATE_TASK_IDS)
    candidate_task_ids = unique_preserve(candidate_task_ids)

    allow_caution_rc = args.allow_caution_rc or (
        profile.allow_caution_rc if profile is not None else False
    )

    coverage_set = set(coverage_task_ids)
    coverage_auto_added: List[str] = []
    for task_id in candidate_task_ids:
        if task_id in coverage_set:
            continue
        coverage_task_ids.append(task_id)
        coverage_set.add(task_id)
        coverage_auto_added.append(task_id)

    release_reports_dir = runtime_root / "release" / "reports"
    release_manifests_dir = runtime_root / "release" / "manifests"
    ensure_dir(release_reports_dir)
    ensure_dir(release_manifests_dir)

    policy_refs = load_policy_refs(
        [
            policy_path,
            workflow_path,
            runbook_policy_path,
            pruning_policy_path,
            trace_semantics_path,
            profile_path,
            coverage_policy_path,
            profile_governance_path,
            ci_policy_path,
            verify_phase_policy_path,
        ]
    )
    policy_doc_ref = policy_refs.get(str(policy_path))
    if policy_doc_ref is None:
        print(
            "ERROR: could not parse doc metadata from release_candidate_policy",
            file=sys.stderr,
        )
        return 2

    harness_refs: List[Tuple[str, str, str]] = []
    for path_key, relation in [
        (str(policy_path), "policy_ref"),
        (str(workflow_path), "workflow_ref"),
        (str(runbook_policy_path), "runbook_policy_ref"),
        (str(pruning_policy_path), "pruning_policy_ref"),
        (str(trace_semantics_path), "trace_semantics_ref"),
        (str(profile_path), "release_gate_profile_ref"),
        (str(coverage_policy_path), "coverage_outcome_policy_ref"),
        (str(profile_governance_path), "release_profile_governance_policy_ref"),
        (str(ci_policy_path), "ci_exit_code_policy_ref"),
        (str(verify_phase_policy_path), "verify_phase_audit_policy_ref"),
    ]:
        parsed = policy_refs.get(path_key)
        if parsed is None:
            continue
        harness_refs.append((parsed[0], parsed[1], relation))

    snapshots: Dict[str, TaskSnapshot] = {}
    harness_default_by_task: Dict[str, HarnessResult] = {}
    harness_strict_by_task: Dict[str, HarnessResult] = {}
    outcomes_by_task: Dict[str, List[CoverageOutcome]] = {}

    coverage_refs: List[Tuple[str, str, str]] = []
    candidate_refs: List[Tuple[str, str, str]] = []
    lane_counter: Dict[str, int] = {}
    coverage_render_summary: List[str] = []
    pruning_branch_summary: List[str] = []
    coverage_info: List[str] = []
    coverage_excluded: List[str] = []

    coverage_warnings: List[str] = []
    coverage_blockers: List[str] = []
    gate_warnings: List[str] = []
    gate_blockers: List[str] = []
    gate_summary: List[str] = []

    coverage_latest_pointer_safety = True
    coverage_lineage_safety = True
    latest_pointer_safety = True
    lineage_safety = True
    coverage_has_quarantine_evidence = False
    coverage_has_delete_evidence = False
    has_quarantine_evidence = False
    has_delete_evidence = False
    has_ready_render_case = False
    candidate_ready_render_case_count = 0
    coverage_verify_phases_seen: set[str] = set()
    verify_phases_seen: set[str] = set()
    readiness_counts: Dict[str, int] = {}

    profile_required_lanes = (
        profile.required_lane_coverage
        if profile is not None and profile.required_lane_coverage
        else ["direct"]
    )
    profile_required_ready_cases = (
        profile.required_ready_cases if profile is not None else 1
    )
    profile_required_pruning_branches = (
        profile.required_pruning_branches
        if profile is not None and profile.required_pruning_branches
        else ["quarantine_first", "delete_derived_safe"]
    )
    profile_required_release_artifacts = (
        profile.required_release_artifacts if profile is not None else []
    )
    profile_required_entrypoints = (
        profile.required_entrypoints
        if profile is not None and profile.required_entrypoints
        else [
            "task_executor",
            "operator_preflight",
            "final_renderer",
            "release_candidate_check",
        ]
    )
    operator_entrypoints = parse_entrypoints_from_workflow(workflow_path)

    for task_id in coverage_task_ids:
        snapshot = load_task_snapshot(runtime_root, task_id)
        if snapshot is None:
            coverage_latest_pointer_safety = False
            coverage_warnings.append(f"rc_coverage_task_missing:{task_id}")
            coverage_render_summary.append(f"{task_id}:missing_snapshot")
            continue
        snapshots[task_id] = snapshot

        if snapshot.route_ref is not None:
            lane_counter[snapshot.selected_path] = (
                lane_counter.get(snapshot.selected_path, 0) + 1
            )
            coverage_refs.append(
                (
                    snapshot.route_ref[0],
                    snapshot.route_ref[1],
                    f"coverage_route:{task_id}",
                )
            )
        else:
            coverage_warnings.append(
                f"rc_coverage_ref_broken:{task_id}:latest_manager_route"
            )

        for key in snapshot.missing_latest_keys:
            coverage_latest_pointer_safety = False
            coverage_warnings.append(f"rc_coverage_ref_broken:{task_id}:{key}:missing")

        for key in snapshot.broken_latest_keys:
            coverage_latest_pointer_safety = False
            coverage_warnings.append(
                f"rc_coverage_ref_broken:{task_id}:{key}:unreadable"
            )

        readiness_counts[snapshot.readiness] = (
            readiness_counts.get(snapshot.readiness, 0) + 1
        )
        if snapshot.readiness == "ready":
            if snapshot.render_mode in {"rendered", "rendered_with_warning"}:
                has_ready_render_case = True
            coverage_render_summary.append(f"{task_id}:ready/{snapshot.render_mode}")
        elif snapshot.readiness == "caution":
            coverage_render_summary.append(f"{task_id}:caution/{snapshot.render_mode}")
            coverage_warnings.append(f"coverage_caution:{task_id}")
        elif snapshot.readiness == "not_ready":
            coverage_render_summary.append(
                f"{task_id}:not_ready/{snapshot.render_mode}"
            )
            coverage_warnings.append(f"coverage_not_ready:{task_id}")
        else:
            coverage_render_summary.append(
                f"{task_id}:{snapshot.readiness}/{snapshot.render_mode}"
            )
            coverage_warnings.append(f"coverage_preflight_unknown:{task_id}")

        if snapshot.lineage_ok is False:
            coverage_lineage_safety = False
            coverage_warnings.append(
                f"rc_coverage_ref_broken:{task_id}:latest_operator_preflight_report:lineage_not_ok"
            )

        coverage_verify_phases_seen.update(snapshot.verify_phases)

        if snapshot.pruning_quarantine_count > 0:
            coverage_has_quarantine_evidence = True
        if snapshot.pruning_delete_count > 0:
            coverage_has_delete_evidence = True
        if snapshot.pruning_quarantine_count > 0 or snapshot.pruning_delete_count > 0:
            pruning_branch_summary.append(
                f"{task_id}:quarantine={snapshot.pruning_quarantine_count},delete={snapshot.pruning_delete_count}"
            )

        default_result = run_harness(
            harness_validator=harness_validator,
            runtime_root=runtime_root,
            task_id=task_id,
            release_readiness=False,
        )
        strict_result = run_harness(
            harness_validator=harness_validator,
            runtime_root=runtime_root,
            task_id=task_id,
            release_readiness=True,
        )
        harness_default_by_task[task_id] = default_result
        harness_strict_by_task[task_id] = strict_result

        outcome_sources = [
            ("default_harness", default_result.result),
            ("strict_release_readiness", strict_result.result),
            ("preflight_render_readiness", snapshot.readiness),
            ("task_status", normalize_task_status(snapshot.status_value)),
        ]
        if snapshot.render_mode in {"rendered", "rendered_with_warning", "denied"}:
            outcome_sources.append(("preflight_render_readiness", snapshot.render_mode))

        task_outcomes: List[CoverageOutcome] = []
        for source_kind, source_value in outcome_sources:
            outcome = classify_coverage_outcome(
                policy=coverage_policy,
                task_id=task_id,
                source_kind=source_kind,
                source_value=source_value,
            )
            task_outcomes.append(outcome)
            outcome_key = f"{task_id}:{outcome.source_kind}:{outcome.source_value}:{outcome.rule_name}"
            if outcome.classification == "blocker":
                coverage_blockers.append(outcome_key)
            elif outcome.classification == "warning":
                coverage_warnings.append(outcome_key)
            elif outcome.classification == "excluded":
                coverage_excluded.append(outcome_key)
            else:
                coverage_info.append(outcome_key)
        outcomes_by_task[task_id] = task_outcomes

    for required_lane in profile_required_lanes:
        if required_lane not in lane_counter:
            coverage_blockers.append(f"lane_coverage_missing:{required_lane}")

    if not {
        key
        for key in lane_counter
        if key in {"planner_pre", "verifier_post", "full_lane"}
    }:
        coverage_blockers.append("lane_coverage_missing:conditional_sidecar")
    if not has_ready_render_case:
        coverage_blockers.append("coverage_ready_render_case_missing")
    if (
        "quarantine_first" in profile_required_pruning_branches
        and not coverage_has_quarantine_evidence
    ):
        coverage_blockers.append("coverage_pruning_branch_missing:quarantine_first")
    if (
        "delete_derived_safe" in profile_required_pruning_branches
        and not coverage_has_delete_evidence
    ):
        coverage_warnings.append("coverage_pruning_branch_missing:delete_derived_safe")
    if coverage_auto_added:
        for task_id in coverage_auto_added:
            coverage_warnings.append(f"candidate_auto_added_to_coverage:{task_id}")

    for task_id in candidate_task_ids:
        snapshot = snapshots.get(task_id)
        if snapshot is None:
            snapshot = load_task_snapshot(runtime_root, task_id)
            if snapshot is not None:
                snapshots[task_id] = snapshot
        if snapshot is None:
            gate_blockers.append(f"rc_candidate_task_missing:{task_id}")
            latest_pointer_safety = False
            gate_summary.append(
                f"{task_id};state=missing_snapshot;blocker=rc_candidate_task_missing"
            )
            continue

        missing_required_latest_keys = sorted(
            set(snapshot.missing_latest_keys)
            | {
                key
                for key in REQUIRED_CANDIDATE_LATEST_KEYS
                if key not in snapshot.latest_paths
            }
        )
        for key in missing_required_latest_keys:
            gate_blockers.append(f"rc_candidate_latest_missing:{task_id}:{key}")
            latest_pointer_safety = False

        for key in snapshot.broken_latest_keys:
            gate_blockers.append(f"rc_candidate_ref_broken:{task_id}:{key}")
            latest_pointer_safety = False

        if snapshot.route_ref is not None:
            candidate_refs.append(
                (
                    snapshot.route_ref[0],
                    snapshot.route_ref[1],
                    f"candidate_route:{task_id}",
                )
            )
        else:
            gate_blockers.append(
                f"rc_candidate_ref_broken:{task_id}:latest_manager_route"
            )

        strict_result = harness_strict_by_task.get(task_id)
        default_result = harness_default_by_task.get(task_id)
        if strict_result is None:
            strict_result = run_harness(
                harness_validator=harness_validator,
                runtime_root=runtime_root,
                task_id=task_id,
                release_readiness=True,
            )
            harness_strict_by_task[task_id] = strict_result
        if default_result is None:
            default_result = run_harness(
                harness_validator=harness_validator,
                runtime_root=runtime_root,
                task_id=task_id,
                release_readiness=False,
            )
            harness_default_by_task[task_id] = default_result

        for outcome in outcomes_by_task.get(task_id, []):
            if not outcome.affects_gate:
                continue
            outcome_key = f"{task_id}:{outcome.source_kind}:{outcome.source_value}:{outcome.rule_name}"
            if outcome.classification == "blocker":
                gate_blockers.append(outcome_key)
            elif outcome.classification == "warning":
                gate_warnings.append(outcome_key)

        if snapshot.readiness == "ready":
            if snapshot.render_mode not in {"rendered", "rendered_with_warning"}:
                gate_blockers.append(f"candidate_ready_render_invalid:{task_id}")
            else:
                candidate_ready_render_case_count += 1

        if snapshot.lineage_ok is False:
            lineage_safety = False
            gate_blockers.append(f"candidate_lineage_not_ok:{task_id}")

        if not snapshot.verify_phases:
            gate_warnings.append(f"candidate_verify_phase_missing:{task_id}")
        else:
            verify_phases_seen.update(snapshot.verify_phases)

        if snapshot.pruning_quarantine_count > 0:
            has_quarantine_evidence = True
        if snapshot.pruning_delete_count > 0:
            has_delete_evidence = True

        gate_summary.append(
            ";".join(
                [
                    task_id,
                    f"readiness={snapshot.readiness}",
                    f"render_mode={snapshot.render_mode}",
                    f"default_harness={default_result.result}",
                    f"strict_harness={strict_result.result}",
                    "verify_phases="
                    + (
                        ",".join(sorted(snapshot.verify_phases))
                        if snapshot.verify_phases
                        else "none"
                    ),
                    "missing_latest="
                    + (
                        ",".join(missing_required_latest_keys)
                        if missing_required_latest_keys
                        else "none"
                    ),
                    "broken_latest="
                    + (
                        ",".join(sorted(snapshot.broken_latest_keys))
                        if snapshot.broken_latest_keys
                        else "none"
                    ),
                ]
            )
        )

        for artifact_name in profile_required_release_artifacts:
            latest_key = f"latest_{artifact_name}"
            path = snapshot.latest_paths.get(latest_key)
            if path is None or not path.exists():
                gate_blockers.append(
                    f"rc_candidate_required_artifact_missing:{task_id}:{artifact_name}"
                )

    if not candidate_refs and candidate_task_ids:
        candidate_refs.append(
            (
                policy_doc_ref[0],
                policy_doc_ref[1],
                "candidate_gate_fallback:no_candidate_route_refs",
            )
        )

    if candidate_ready_render_case_count < profile_required_ready_cases:
        gate_blockers.append(
            "required_ready_cases_not_met:"
            f"{candidate_ready_render_case_count}<{profile_required_ready_cases}"
        )

    missing_entrypoints = sorted(
        set(profile_required_entrypoints) - set(operator_entrypoints)
    )
    for entrypoint in missing_entrypoints:
        gate_blockers.append(f"required_entrypoint_missing:{entrypoint}")

    if candidate_ready_render_case_count < 1:
        gate_blockers.append("ready_render_case_missing_in_candidate_subset")
    if not latest_pointer_safety:
        gate_blockers.append("latest_pointer_safety=false")
    if not lineage_safety:
        gate_blockers.append("lineage_safety=false")
    if "lane" not in verify_phases_seen:
        gate_blockers.append("verify_phase_rollout_missing:lane")
    if "post_implement" not in verify_phases_seen:
        gate_blockers.append("verify_phase_rollout_missing:post_implement")
    if (
        "quarantine_first" in profile_required_pruning_branches
        and not has_quarantine_evidence
    ):
        gate_blockers.append("pruning_branch_missing:quarantine_first")
    if (
        "delete_derived_safe" in profile_required_pruning_branches
        and not has_delete_evidence
    ):
        gate_warnings.append("pruning_branch_missing:delete_derived_safe")

    coverage_warnings = sorted(set(coverage_warnings))
    coverage_blockers = sorted(set(coverage_blockers))
    gate_warnings = sorted(set(gate_warnings))
    gate_blockers = sorted(set(gate_blockers))

    if gate_blockers:
        rc_result = "fail"
        next_action = (
            "Resolve candidate gate blockers and rerun release_candidate_check."
        )
    elif gate_warnings:
        if allow_caution_rc:
            rc_result = "caution"
            next_action = "Proceed with explicit warning acknowledgment and operator override logging."
        else:
            rc_result = "fail"
            gate_blockers = sorted(
                set(gate_blockers + ["warnings_present_without_allow_caution_rc"])
            )
            next_action = "Rerun with --allow-caution-rc or resolve candidate warnings."
    else:
        rc_result = "pass"
        next_action = "Proceed with normal release handoff workflow."

    lane_coverage_summary = [
        f"{key}:{lane_counter[key]}" for key in sorted(lane_counter.keys())
    ]
    if not lane_coverage_summary:
        lane_coverage_summary = ["none"]
    if not coverage_render_summary:
        coverage_render_summary = ["none"]
    if not pruning_branch_summary:
        pruning_branch_summary = ["none"]

    verify_phase_values = sorted(verify_phases_seen) if verify_phases_seen else ["none"]
    verify_phase_summary = [f"present:{value}" for value in verify_phase_values]
    coverage_verify_phase_values = (
        sorted(coverage_verify_phases_seen) if coverage_verify_phases_seen else ["none"]
    )

    coverage_harness_counts = {"pass": 0, "inconclusive": 0, "fail": 0}
    coverage_default_harness_counts = {"pass": 0, "inconclusive": 0, "fail": 0}
    for task_id in coverage_task_ids:
        strict_result = harness_strict_by_task.get(task_id)
        if strict_result is not None:
            if strict_result.result in coverage_harness_counts:
                coverage_harness_counts[strict_result.result] += 1
            elif strict_result.result == "error":
                coverage_harness_counts["fail"] += 1

        default_result = harness_default_by_task.get(task_id)
        if default_result is None:
            continue
        if default_result.result in coverage_default_harness_counts:
            coverage_default_harness_counts[default_result.result] += 1
        elif default_result.result == "error":
            coverage_default_harness_counts["fail"] += 1

    readiness_summary = ",".join(
        f"{key}:{readiness_counts[key]}" for key in sorted(readiness_counts.keys())
    )
    if not readiness_summary:
        readiness_summary = "none"

    coverage_summary: List[str] = [
        f"coverage_task_count={len(coverage_task_ids)}",
        f"candidate_task_count={len(candidate_task_ids)}",
        "lane_counts=" + ",".join(lane_coverage_summary),
        "readiness_counts=" + readiness_summary,
        (
            "coverage_strict_harness="
            f"pass:{coverage_harness_counts['pass']},"
            f"inconclusive:{coverage_harness_counts['inconclusive']},"
            f"fail:{coverage_harness_counts['fail']}"
        ),
        (
            "coverage_default_harness="
            f"pass:{coverage_default_harness_counts['pass']},"
            f"inconclusive:{coverage_default_harness_counts['inconclusive']},"
            f"fail:{coverage_default_harness_counts['fail']}"
        ),
        f"coverage_latest_pointer_safety={str(coverage_latest_pointer_safety).lower()}",
        f"coverage_lineage_safety={str(coverage_lineage_safety).lower()}",
        "coverage_verify_phases_seen=" + ",".join(coverage_verify_phase_values),
    ]
    if profile.profile_version:
        coverage_summary.append(f"profile_version={profile.profile_version}")
    if profile.profile_owner:
        coverage_summary.append(f"profile_owner={profile.profile_owner}")
    if profile.approval_ref:
        coverage_summary.append(f"profile_approval_ref={profile.approval_ref}")
    if coverage_info:
        coverage_summary.append("coverage_info=" + ",".join(sorted(set(coverage_info))))
    if coverage_excluded:
        coverage_summary.append(
            "coverage_excluded=" + ",".join(sorted(set(coverage_excluded)))
        )
    if coverage_warnings:
        coverage_summary.append("coverage_warnings=" + ",".join(coverage_warnings))
    if coverage_blockers:
        coverage_summary.append("coverage_blockers=" + ",".join(coverage_blockers))

    if not gate_summary:
        gate_summary = ["none"]

    report_warnings = gate_warnings if gate_warnings else ["none"]
    report_blockers = gate_blockers if gate_blockers else ["none"]

    release_task_id = args.release_task_id.strip()
    if not release_task_id.startswith("task_"):
        print("ERROR: --release-task-id must start with task_", file=sys.stderr)
        return 2

    report_sequence = next_sequence(runtime_root, release_task_id)
    report_doc_id = make_doc_id(
        "release_candidate_report",
        sanitize(release_task_id),
        report_sequence,
    )
    report_created_at = now_iso()

    report_root = etree.Element(q("pxml"), nsmap=NSMAP)
    report_meta = etree.SubElement(report_root, q("meta"))
    etree.SubElement(report_meta, q("doc_id")).text = report_doc_id
    etree.SubElement(report_meta, q("doc_class")).text = "release_candidate_report"
    etree.SubElement(report_meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(report_meta, q("task_id")).text = release_task_id
    etree.SubElement(report_meta, q("run_id")).text = "run_release_candidate_check"
    etree.SubElement(report_meta, q("sequence")).text = str(report_sequence)
    etree.SubElement(report_meta, q("writer_agent")).text = "system"
    etree.SubElement(report_meta, q("created_at")).text = report_created_at

    report_refs = etree.SubElement(report_root, q("refs"))
    for doc_id, doc_class, relation in harness_refs:
        ref_node(report_refs, doc_id, doc_class, relation)
    for doc_id, doc_class, relation in coverage_refs:
        ref_node(report_refs, doc_id, doc_class, relation)
    for doc_id, doc_class, relation in candidate_refs:
        ref_node(report_refs, doc_id, doc_class, relation)

    report_payload = etree.SubElement(report_root, q("payload"))
    etree.SubElement(
        report_payload, q("release_candidate_report_id")
    ).text = f"rc_report_{sanitize(release_task_id)}_{report_sequence:04d}"
    etree.SubElement(report_payload, q("generated_at")).text = report_created_at
    etree.SubElement(report_payload, q("derived")).text = "true"

    policy_ref_node = etree.SubElement(report_payload, q("policy_ref"))
    etree.SubElement(policy_ref_node, q("doc_id")).text = policy_doc_ref[0]
    etree.SubElement(policy_ref_node, q("doc_class")).text = policy_doc_ref[1]
    etree.SubElement(policy_ref_node, q("relation")).text = "policy_ref"

    harness_version_refs = etree.SubElement(report_payload, q("harness_version_refs"))
    for doc_id, doc_class, relation in harness_refs:
        ref_node(harness_version_refs, doc_id, doc_class, relation)

    smoke_task_refs = etree.SubElement(report_payload, q("smoke_task_refs"))
    if coverage_refs:
        for doc_id, doc_class, relation in coverage_refs:
            ref_node(smoke_task_refs, doc_id, doc_class, relation)
    else:
        ref_node(smoke_task_refs, policy_doc_ref[0], policy_doc_ref[1], "fallback")

    if coverage_refs:
        coverage_refs_node = etree.SubElement(report_payload, q("coverage_task_refs"))
        for doc_id, doc_class, relation in coverage_refs:
            ref_node(coverage_refs_node, doc_id, doc_class, relation)

    if candidate_refs:
        candidate_refs_node = etree.SubElement(
            report_payload, q("candidate_gate_task_refs")
        )
        for doc_id, doc_class, relation in candidate_refs:
            ref_node(candidate_refs_node, doc_id, doc_class, relation)

    coverage_summary_node = etree.SubElement(report_payload, q("coverage_summary"))
    add_items(coverage_summary_node, coverage_summary)

    gate_summary_node = etree.SubElement(report_payload, q("gate_summary"))
    add_items(gate_summary_node, gate_summary)

    lane_node = etree.SubElement(report_payload, q("lane_coverage_summary"))
    add_items(lane_node, lane_coverage_summary)
    etree.SubElement(
        report_payload,
        q("operator_runbook_coverage"),
    ).text = "WF-001..WF-011 with release_handoff_appendix; rc_result uses candidate_gate_subset"

    render_node = etree.SubElement(report_payload, q("render_gate_summary"))
    add_items(render_node, sorted(coverage_render_summary))

    pruning_node = etree.SubElement(report_payload, q("pruning_branch_summary"))
    add_items(pruning_node, sorted(pruning_branch_summary))

    etree.SubElement(report_payload, q("latest_pointer_safety")).text = (
        "true" if latest_pointer_safety else "false"
    )
    etree.SubElement(report_payload, q("lineage_safety")).text = (
        "true" if lineage_safety else "false"
    )

    verify_node = etree.SubElement(report_payload, q("verify_phase_rollout_summary"))
    add_items(verify_node, verify_phase_summary)

    warning_node = etree.SubElement(report_payload, q("warnings"))
    add_items(warning_node, report_warnings)
    blocker_node = etree.SubElement(report_payload, q("blockers"))
    add_items(blocker_node, report_blockers)

    etree.SubElement(report_payload, q("rc_result")).text = rc_result
    etree.SubElement(
        report_payload, q("rc_result_basis")
    ).text = "candidate_gate_subset"
    etree.SubElement(report_payload, q("next_action")).text = next_action

    report_integrity = etree.SubElement(report_root, q("integrity"))
    report_hash = compute_content_hash(report_meta, report_refs, report_payload)
    etree.SubElement(report_integrity, q("content_sha256")).text = report_hash

    report_tree = etree.ElementTree(report_root)
    report_path = release_reports_dir / f"{report_doc_id}.pxml"
    report_tree.write(
        str(report_path), encoding="UTF-8", xml_declaration=True, pretty_print=True
    )

    manifest_sequence = report_sequence + 1
    manifest_doc_id = make_doc_id(
        "release_bundle_manifest",
        sanitize(release_task_id),
        manifest_sequence,
    )
    manifest_created_at = now_iso()

    key_schema_refs = [
        "contracts/schemas/release_candidate_policy.xsd",
        "contracts/schemas/release_candidate_report.xsd",
        "contracts/schemas/release_bundle_manifest.xsd",
        "contracts/schemas/release_gate_profile.xsd",
        "contracts/schemas/coverage_outcome_policy.xsd",
        "contracts/schemas/release_profile_governance_policy.xsd",
        "contracts/schemas/ci_exit_code_policy.xsd",
        "contracts/schemas/verify_phase_audit_report.xsd",
        "contracts/schemas/execution_trace.xsd",
        "contracts/schemas/verification_result.xsd",
    ]
    key_script_refs = [
        "scripts/release_candidate_check.py",
        "scripts/release_ops_gate.py",
        "scripts/session_report_refresh.py",
        "scripts/verify_phase_audit.py",
        "scripts/harness_validator.py",
        "scripts/pxml_validator.py",
        "scripts/task_executor.py",
        "scripts/orchestration_coordinator.py",
        "scripts/verification_runner.py",
        "scripts/trace_appender.py",
    ]
    key_runtime_refs = [
        "runtime/release/reports",
        "runtime/release/manifests",
        "runtime/release/governance",
        "runtime/release/audits",
        "runtime/latest",
        "runtime/index/tasks",
    ]

    latest_report_path = (
        runtime_root
        / "latest"
        / f"{sanitize(release_task_id)}_release_candidate_report.pxml"
    )
    latest_manifest_path = (
        runtime_root
        / "latest"
        / f"{sanitize(release_task_id)}_release_bundle_manifest.pxml"
    )

    manifest_known_warnings: List[str] = []
    manifest_known_warnings.extend(gate_warnings)
    manifest_known_warnings.extend([f"coverage:{item}" for item in coverage_warnings])
    manifest_known_warnings = sorted(set(manifest_known_warnings))
    if not manifest_known_warnings:
        manifest_known_warnings = ["none"]

    manifest_root = etree.Element(q("pxml"), nsmap=NSMAP)
    manifest_meta = etree.SubElement(manifest_root, q("meta"))
    etree.SubElement(manifest_meta, q("doc_id")).text = manifest_doc_id
    etree.SubElement(manifest_meta, q("doc_class")).text = "release_bundle_manifest"
    etree.SubElement(manifest_meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(manifest_meta, q("task_id")).text = release_task_id
    etree.SubElement(manifest_meta, q("run_id")).text = "run_release_candidate_check"
    etree.SubElement(manifest_meta, q("sequence")).text = str(manifest_sequence)
    etree.SubElement(manifest_meta, q("writer_agent")).text = "system"
    etree.SubElement(manifest_meta, q("created_at")).text = manifest_created_at

    manifest_refs = etree.SubElement(manifest_root, q("refs"))
    ref_node(
        manifest_refs,
        report_doc_id,
        "release_candidate_report",
        "source_release_candidate_report",
    )
    for doc_id, doc_class, relation in harness_refs:
        ref_node(manifest_refs, doc_id, doc_class, relation)

    manifest_payload = etree.SubElement(manifest_root, q("payload"))
    etree.SubElement(
        manifest_payload, q("manifest_id")
    ).text = f"rc_bundle_{sanitize(release_task_id)}_{manifest_sequence:04d}"
    etree.SubElement(manifest_payload, q("generated_at")).text = manifest_created_at
    etree.SubElement(manifest_payload, q("derived")).text = "true"

    source_ref = etree.SubElement(
        manifest_payload, q("source_release_candidate_report_ref")
    )
    etree.SubElement(source_ref, q("doc_id")).text = report_doc_id
    etree.SubElement(source_ref, q("doc_class")).text = "release_candidate_report"
    etree.SubElement(source_ref, q("relation")).text = "source_report"

    key_policy_refs = etree.SubElement(manifest_payload, q("key_policy_refs"))
    for doc_id, doc_class, relation in harness_refs:
        ref_node(key_policy_refs, doc_id, doc_class, relation)

    schema_node = etree.SubElement(manifest_payload, q("key_schema_refs"))
    add_items(schema_node, key_schema_refs)
    script_node = etree.SubElement(manifest_payload, q("key_script_refs"))
    add_items(script_node, key_script_refs)
    runtime_node = etree.SubElement(manifest_payload, q("key_runtime_refs"))
    add_items(runtime_node, key_runtime_refs)

    smoke_ids_node = etree.SubElement(manifest_payload, q("smoke_task_ids"))
    add_items(smoke_ids_node, coverage_task_ids)
    candidate_ids_node = etree.SubElement(
        manifest_payload,
        q("candidate_gate_task_ids"),
    )
    add_items(candidate_ids_node, candidate_task_ids)

    latest_refs_node = etree.SubElement(manifest_payload, q("latest_release_refs"))
    add_items(
        latest_refs_node,
        [
            to_runtime_rel(latest_report_path, runtime_root),
            to_runtime_rel(latest_manifest_path, runtime_root),
        ],
    )

    entrypoints_node = etree.SubElement(manifest_payload, q("operator_entrypoints"))
    add_items(entrypoints_node, operator_entrypoints)
    known_warnings_node = etree.SubElement(manifest_payload, q("known_warnings"))
    add_items(known_warnings_node, manifest_known_warnings)

    handoff_notes = etree.SubElement(manifest_payload, q("handoff_notes"))
    add_items(
        handoff_notes,
        [
            "rc_result_basis=candidate_gate_subset",
            f"rc_result={rc_result}",
            f"coverage_task_count={len(coverage_task_ids)}",
            f"candidate_task_count={len(candidate_task_ids)}",
            f"profile_version={profile.profile_version or 'unknown'}",
            f"profile_owner={profile.profile_owner or 'unknown'}",
            "release_handoff_appendix=operator_workflow_guide",
            next_action,
        ],
    )

    manifest_integrity = etree.SubElement(manifest_root, q("integrity"))
    manifest_hash = compute_content_hash(manifest_meta, manifest_refs, manifest_payload)
    etree.SubElement(manifest_integrity, q("content_sha256")).text = manifest_hash

    manifest_tree = etree.ElementTree(manifest_root)
    manifest_path = release_manifests_dir / f"{manifest_doc_id}.pxml"
    manifest_tree.write(
        str(manifest_path), encoding="UTF-8", xml_declaration=True, pretty_print=True
    )

    if not args.skip_validate:
        validation_context: List[Path] = [
            policy_path,
            workflow_path,
            runbook_policy_path,
            pruning_policy_path,
            trace_semantics_path,
            profile_path,
            coverage_policy_path,
            profile_governance_path,
            ci_policy_path,
            verify_phase_policy_path,
            report_path,
            manifest_path,
        ]
        validation_task_ids = unique_preserve(coverage_task_ids + candidate_task_ids)
        for task_id in validation_task_ids:
            task_index_path = (
                runtime_root / "index" / "tasks" / f"{sanitize(task_id)}.json"
            )
            payload = parse_task_file(task_index_path)
            if payload is None:
                continue
            latest_paths = pick_latest_paths(payload, runtime_root)
            validation_context.extend(list(latest_paths.values()))

        try:
            run_validation(validator, report_path, validation_context)
            run_validation(validator, manifest_path, validation_context)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    ensure_dir(latest_report_path.parent)
    shutil.copy2(report_path, latest_report_path)
    shutil.copy2(manifest_path, latest_manifest_path)

    index_tasks_dir = runtime_root / "index" / "tasks"
    index_artifacts_dir = runtime_root / "index" / "artifacts"
    ensure_dir(index_tasks_dir)
    ensure_dir(index_artifacts_dir)

    release_task_index_path = index_tasks_dir / f"{sanitize(release_task_id)}.json"
    task_index_payload: Dict[str, object] = {}
    if release_task_index_path.exists():
        try:
            loaded = json.loads(release_task_index_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                task_index_payload = loaded
        except json.JSONDecodeError:
            task_index_payload = {}
    task_index_payload["task_id"] = release_task_id
    task_index_payload["latest_release_candidate_report"] = to_runtime_rel(
        report_path, runtime_root
    )
    task_index_payload["latest_release_bundle_manifest"] = to_runtime_rel(
        manifest_path, runtime_root
    )
    task_index_payload["updated_at"] = now_iso()
    release_task_index_path.write_text(
        json.dumps(task_index_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    for doc_id, doc_class, path in [
        (report_doc_id, "release_candidate_report", report_path),
        (manifest_doc_id, "release_bundle_manifest", manifest_path),
    ]:
        artifact_payload = {
            "doc_id": doc_id,
            "doc_class": doc_class,
            "task_id": release_task_id,
            "path": to_runtime_rel(path, runtime_root),
            "updated_at": now_iso(),
        }
        (index_artifacts_dir / f"{doc_id}.json").write_text(
            json.dumps(artifact_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(f"coverage_task_count={len(coverage_task_ids)}")
    print("coverage_tasks=" + ",".join(coverage_task_ids))
    print(f"candidate_task_count={len(candidate_task_ids)}")
    print("candidate_tasks=" + ",".join(candidate_task_ids))
    print("rc_result_basis=candidate_gate_subset")
    print(f"rc_result={rc_result}")
    print("gate_warnings=" + ",".join(report_warnings))
    print("gate_blockers=" + ",".join(report_blockers))
    print(
        "coverage_warnings="
        + (",".join(coverage_warnings) if coverage_warnings else "none")
    )
    print(
        "coverage_blockers="
        + (",".join(coverage_blockers) if coverage_blockers else "none")
    )
    print(f"latest_pointer_safety={str(latest_pointer_safety).lower()}")
    print(f"lineage_safety={str(lineage_safety).lower()}")
    print(
        "verify_phases_seen="
        + (",".join(sorted(verify_phases_seen)) if verify_phases_seen else "none")
    )
    print(f"release_candidate_report={report_path}")
    print(f"release_bundle_manifest={manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
