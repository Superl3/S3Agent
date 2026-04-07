#!/usr/bin/env python3
"""Batch 1 PXML validator.

Validates:
1) XML well-formedness
2) doc_class-specific XSD schema
3) optional Schematron rules
4) optional semantic cross-reference checks
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from lxml import etree, isoschematron
except ModuleNotFoundError:
    print(
        "ERROR: lxml is required for XSD/Schematron validation. "
        "Install with: python -m pip install lxml",
        file=sys.stderr,
    )
    raise SystemExit(3)

from exploration_guards import ALLOWED_REQUESTERS, is_concrete_hint, looks_broad


NS = {"p": "urn:pxml:v1"}
SVRL_NS = {"svrl": "http://purl.oclc.org/dsdl/svrl"}
SUPPORTED_EXTS = {".pxml", ".xml"}

SCHEMA_MAP = {
    "task_intake": "task_intake.xsd",
    "manager_route": "manager_route.xsd",
    "execution_packet": "execution_packet.xsd",
    "plan_sidecar": "plan_sidecar.xsd",
    "review_sidecar": "review_sidecar.xsd",
    "implementer_result": "implementer_result.xsd",
    "verification_result": "verification_result.xsd",
    "exploration_request": "exploration_request.xsd",
    "exploration_result": "exploration_result.xsd",
    "execution_trace": "execution_trace.xsd",
    "hooks_registry": "hooks_registry.xsd",
    "skills_registry": "skills_registry.xsd",
    "mcp_registry": "mcp_registry.xsd",
    "extension_activation_policy": "extension_activation_policy.xsd",
    "manager_contract": "manager_contract.xsd",
    "implementer_contract": "implementer_contract.xsd",
    "planner_contract": "planner_contract.xsd",
    "reviewer_contract": "reviewer_contract.xsd",
    "verifier_contract": "verifier_contract.xsd",
    "routing_policy": "routing_policy.xsd",
    "execution_policy": "execution_policy.xsd",
    "implementer_runtime_policy": "implementer_runtime_policy.xsd",
    "compaction_policy": "compaction_policy.xsd",
    "runtime_retention_policy": "runtime_retention_policy.xsd",
    "trace_event_semantics": "trace_event_semantics.xsd",
    "operator_workflow_guide": "operator_workflow_guide.xsd",
    "operator_runbook_policy": "operator_runbook_policy.xsd",
    "rendering_policy": "rendering_policy.xsd",
    "post_implement_verification_policy": "post_implement_verification_policy.xsd",
    "failure_reason_taxonomy": "failure_reason_taxonomy.xsd",
    "task_status_report": "task_status_report.xsd",
    "compaction_checkpoint": "compaction_checkpoint.xsd",
    "operator_preflight_report": "operator_preflight_report.xsd",
    "final_render_report": "final_render_report.xsd",
    "session_report": "session_report.xsd",
    "artifact_pruning_policy": "artifact_pruning_policy.xsd",
    "pruning_report": "pruning_report.xsd",
    "release_candidate_policy": "release_candidate_policy.xsd",
    "release_candidate_report": "release_candidate_report.xsd",
    "release_bundle_manifest": "release_bundle_manifest.xsd",
    "release_gate_profile": "release_gate_profile.xsd",
    "coverage_outcome_policy": "coverage_outcome_policy.xsd",
    "release_profile_governance_policy": "release_profile_governance_policy.xsd",
    "ci_exit_code_policy": "ci_exit_code_policy.xsd",
    "verify_phase_audit_policy": "release_candidate_policy.xsd",
    "verify_phase_audit_report": "verify_phase_audit_report.xsd",
    "ci_test_profile": "ci_test_profile.xsd",
    "reason_code_catalog": "reason_code_catalog.xsd",
    "escalation_policy": "escalation_policy.xsd",
    "retry_policy": "retry_policy.xsd",
}

REQUIRED_IMPLEMENTER_RUNTIME_RULES = {
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

REQUIRED_ARTIFACT_PRUNING_POLICY_RULES = {
    "dry_run_default",
    "quarantine_first_for_ssot",
    "delete_derived_safe_only",
    "never_prune_current_latest",
    "never_prune_referenced_by_latest",
    "replacement_proof_required",
    "task_scoped_first",
    "global_prune_requires_explicit_flag",
    "lineage_mismatch_prefers_quarantine",
    "operator_override_logging_required",
}

REQUIRED_RELEASE_CANDIDATE_POLICY_RULES = {
    "require_lane_coverage",
    "require_ready_render_case",
    "allow_caution_with_documented_warning",
    "deny_not_ready_as_rc_pass",
    "require_pruning_branch_coverage",
    "require_release_readiness_strict_result",
    "require_latest_pointer_safety",
    "require_verify_phase_rollout_minimum",
    "require_handoff_manifest",
    "operator_override_logging_required",
}


@dataclass
class ValidationIssue:
    code: str
    message: str
    line: Optional[int] = None
    column: Optional[int] = None


@dataclass
class ParsedDoc:
    path: Path
    tree: Optional[etree._ElementTree] = None
    doc_id: Optional[str] = None
    doc_class: Optional[str] = None
    issues: List[ValidationIssue] = field(default_factory=list)


def xpath_text(tree: etree._ElementTree, expr: str) -> Optional[str]:
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
    text = text.strip()
    return text or None


def parse_xml(path: Path) -> ParsedDoc:
    parser = etree.XMLParser(remove_blank_text=True)
    parsed = ParsedDoc(path=path)
    try:
        parsed.tree = etree.parse(str(path), parser)
    except etree.XMLSyntaxError as exc:
        parsed.issues.append(
            ValidationIssue(
                code="E100_WELLFORMED_FAIL",
                message=str(exc).strip(),
                line=getattr(exc, "lineno", None),
                column=getattr(exc, "offset", None),
            )
        )
        return parsed

    parsed.doc_class = xpath_text(parsed.tree, "/p:pxml/p:meta/p:doc_class")
    parsed.doc_id = xpath_text(parsed.tree, "/p:pxml/p:meta/p:doc_id")
    return parsed


def discover_files(target: Path) -> List[Path]:
    if target.is_file():
        return [target]

    seen = set()
    files: List[Path] = []
    for ext in SUPPORTED_EXTS:
        for candidate in target.rglob(f"*{ext}"):
            if candidate.is_file():
                resolved = candidate.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    files.append(candidate)
    files.sort()
    return files


def compile_schema(
    schema_path: Path, cache: Dict[Path, etree.XMLSchema]
) -> etree.XMLSchema:
    cached = cache.get(schema_path)
    if cached is not None:
        return cached
    schema_doc = etree.parse(str(schema_path))
    compiled = etree.XMLSchema(schema_doc)
    cache[schema_path] = compiled
    return compiled


def compile_rules(
    rule_paths: Iterable[Path],
) -> List[Tuple[Path, isoschematron.Schematron]]:
    compiled: List[Tuple[Path, isoschematron.Schematron]] = []
    for path in sorted(rule_paths):
        rule_doc = etree.parse(str(path))
        compiled.append((path, isoschematron.Schematron(rule_doc, store_report=True)))
    return compiled


def get_refs(
    tree: etree._ElementTree,
) -> List[Tuple[str, Optional[str], Optional[str]]]:
    refs: List[Tuple[str, Optional[str], Optional[str]]] = []
    nodes = tree.xpath("/p:pxml/p:refs/p:ref", namespaces=NS)
    for node in nodes:
        doc_id = xpath_text(etree.ElementTree(node), "./p:doc_id")
        doc_class = xpath_text(etree.ElementTree(node), "./p:doc_class")
        relation = xpath_text(etree.ElementTree(node), "./p:relation")
        if doc_id:
            refs.append((doc_id, doc_class, relation))
    return refs


def semantic_checks(
    doc: ParsedDoc,
    context_index: Dict[str, ParsedDoc],
    strict_refs: bool,
) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    if doc.tree is None or doc.doc_class is None:
        return issues

    refs = get_refs(doc.tree)
    for ref_doc_id, ref_doc_class, _relation in refs:
        target = context_index.get(ref_doc_id)
        if strict_refs and target is None:
            issues.append(
                ValidationIssue(
                    code="E400_REF_NOT_FOUND",
                    message=f"Referenced doc_id '{ref_doc_id}' is not found in context index.",
                )
            )
            continue
        if (
            target is not None
            and ref_doc_class
            and target.doc_class
            and ref_doc_class != target.doc_class
        ):
            issues.append(
                ValidationIssue(
                    code="E401_REF_CLASS_MISMATCH",
                    message=(
                        f"Reference doc_id '{ref_doc_id}' declares doc_class='{ref_doc_class}', "
                        f"but indexed artifact has doc_class='{target.doc_class}'."
                    ),
                )
            )

    if doc.doc_class == "manager_route":
        issues.extend(_semantic_manager_route(doc, refs))
    elif doc.doc_class == "execution_packet":
        issues.extend(_semantic_execution_packet(doc, refs, context_index, strict_refs))
    elif doc.doc_class == "plan_sidecar":
        issues.extend(_semantic_plan_sidecar(doc, refs))
    elif doc.doc_class == "review_sidecar":
        issues.extend(_semantic_review_sidecar(doc, refs))
    elif doc.doc_class == "implementer_result":
        issues.extend(_semantic_implementer_result(doc, refs, context_index))
    elif doc.doc_class == "verification_result":
        issues.extend(_semantic_verification_result(doc, refs, context_index))
    elif doc.doc_class == "exploration_request":
        issues.extend(_semantic_exploration_request(doc, refs, context_index))
    elif doc.doc_class == "exploration_result":
        issues.extend(_semantic_exploration_result(doc, refs, context_index))
    elif doc.doc_class == "execution_trace":
        issues.extend(_semantic_execution_trace(doc))
    elif doc.doc_class == "implementer_runtime_policy":
        issues.extend(_semantic_implementer_runtime_policy(doc))
    elif doc.doc_class == "post_implement_verification_policy":
        issues.extend(_semantic_post_implement_verification_policy(doc))
    elif doc.doc_class == "failure_reason_taxonomy":
        issues.extend(_semantic_failure_reason_taxonomy(doc))
    elif doc.doc_class == "task_status_report":
        issues.extend(_semantic_task_status_report(doc, refs, context_index))
    elif doc.doc_class == "runtime_retention_policy":
        issues.extend(_semantic_runtime_retention_policy(doc))
    elif doc.doc_class == "trace_event_semantics":
        issues.extend(_semantic_trace_event_semantics(doc))
    elif doc.doc_class == "compaction_checkpoint":
        issues.extend(_semantic_compaction_checkpoint(doc, refs, context_index))
    elif doc.doc_class == "operator_preflight_report":
        issues.extend(_semantic_operator_preflight_report(doc, refs, context_index))
    elif doc.doc_class == "operator_workflow_guide":
        issues.extend(_semantic_operator_workflow_guide(doc))
    elif doc.doc_class == "operator_runbook_policy":
        issues.extend(_semantic_operator_runbook_policy(doc))
    elif doc.doc_class == "rendering_policy":
        issues.extend(_semantic_rendering_policy(doc))
    elif doc.doc_class == "final_render_report":
        issues.extend(_semantic_final_render_report(doc, refs, context_index))
    elif doc.doc_class == "session_report":
        issues.extend(_semantic_session_report(doc, refs, context_index))
    elif doc.doc_class == "artifact_pruning_policy":
        issues.extend(_semantic_artifact_pruning_policy(doc))
    elif doc.doc_class == "pruning_report":
        issues.extend(_semantic_pruning_report(doc, refs))
    elif doc.doc_class == "release_candidate_policy":
        issues.extend(_semantic_release_candidate_policy(doc))
    elif doc.doc_class == "release_candidate_report":
        issues.extend(_semantic_release_candidate_report(doc, refs))
    elif doc.doc_class == "release_bundle_manifest":
        issues.extend(_semantic_release_bundle_manifest(doc, refs))
    elif doc.doc_class == "release_gate_profile":
        issues.extend(_semantic_release_gate_profile(doc))
    elif doc.doc_class == "coverage_outcome_policy":
        issues.extend(_semantic_coverage_outcome_policy(doc))
    elif doc.doc_class == "release_profile_governance_policy":
        issues.extend(_semantic_release_profile_governance_policy(doc))
    elif doc.doc_class == "ci_exit_code_policy":
        issues.extend(_semantic_ci_exit_code_policy(doc))
    elif doc.doc_class == "verify_phase_audit_policy":
        issues.extend(_semantic_verify_phase_audit_policy(doc))
    elif doc.doc_class == "verify_phase_audit_report":
        issues.extend(_semantic_verify_phase_audit_report(doc, refs))
    elif doc.doc_class == "ci_test_profile":
        issues.extend(_semantic_ci_test_profile(doc))
    elif doc.doc_class == "reason_code_catalog":
        issues.extend(_semantic_reason_code_catalog(doc))

    issues.extend(_semantic_failure_reason_consistency(doc, context_index))

    return issues


def _semantic_manager_route(
    doc: ParsedDoc, refs: List[Tuple[str, Optional[str], Optional[str]]]
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    intake_refs = [item for item in refs if item[1] == "task_intake"]
    if len(intake_refs) != 1:
        issues.append(
            ValidationIssue(
                code="E340_MANAGER_ROUTE_REF_COUNT",
                message="manager_route must reference exactly one task_intake artifact.",
            )
        )

    selected_path = xpath_text(doc.tree, "/p:pxml/p:payload/p:selected_path")
    planner = xpath_text(doc.tree, "/p:pxml/p:payload/p:lane_flags/p:planner")
    reviewer = xpath_text(doc.tree, "/p:pxml/p:payload/p:lane_flags/p:reviewer")
    verifier = xpath_text(doc.tree, "/p:pxml/p:payload/p:lane_flags/p:verifier")

    expected = {
        "direct": ("false", "false", "false"),
        "planner_pre": ("true", "false", "false"),
        "reviewer_post": ("false", "true", "false"),
        "verifier_post": ("false", "false", "true"),
        "full_lane": ("true", "true", "true"),
    }

    if (
        selected_path in expected
        and (planner, reviewer, verifier) != expected[selected_path]
    ):
        issues.append(
            ValidationIssue(
                code="E320_ROUTE_LANE_MISMATCH",
                message="selected_path and lane_flags mismatch semantic mapping.",
            )
        )

    return issues


def _semantic_execution_packet(
    doc: ParsedDoc,
    refs: List[Tuple[str, Optional[str], Optional[str]]],
    context_index: Dict[str, ParsedDoc],
    strict_refs: bool,
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    route_refs = [item for item in refs if item[1] == "manager_route"]
    if len(route_refs) != 1:
        issues.append(
            ValidationIssue(
                code="E342_EXEC_PACKET_ROUTE_REF_COUNT",
                message="execution_packet must reference exactly one manager_route artifact.",
            )
        )

    patch_mode = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:patch_constraints/p:patch_mode"
    )
    approved = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:patch_constraints/p:rewrite_exception_approved"
    )
    reason = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:patch_constraints/p:rewrite_exception_reason"
    )
    packet_lock_hash = xpath_text(doc.tree, "/p:pxml/p:payload/p:acceptance_lock_hash")

    if packet_lock_hash is None:
        issues.append(
            ValidationIssue(
                code="E700_PACKET_ACCEPTANCE_LOCK_HASH_REQUIRED",
                message="execution_packet must include acceptance_lock_hash.",
            )
        )

    if patch_mode == "full_rewrite_exception":
        if approved != "true" or reason is None:
            issues.append(
                ValidationIssue(
                    code="E350_REWRITE_EXCEPTION_INVALID",
                    message="full_rewrite_exception requires rewrite_exception_approved=true and rewrite_exception_reason.",
                )
            )
    if patch_mode == "patch_first" and approved == "true":
        issues.append(
            ValidationIssue(
                code="E351_PATCH_FIRST_APPROVAL_INVALID",
                message="patch_first cannot set rewrite_exception_approved=true.",
            )
        )

    if not route_refs:
        return issues

    route_doc_id = route_refs[0][0]
    route_doc = context_index.get(route_doc_id)
    if route_doc is None:
        return issues
    if route_doc.doc_class != "manager_route" or route_doc.tree is None:
        return issues

    route_lock_hash = xpath_text(
        route_doc.tree, "/p:pxml/p:payload/p:acceptance_lock/p:lock_sha256"
    )
    if (
        packet_lock_hash is not None
        and route_lock_hash is not None
        and packet_lock_hash != route_lock_hash
    ):
        issues.append(
            ValidationIssue(
                code="E702_PACKET_ROUTE_LINEAGE_MISMATCH",
                message="execution_packet acceptance_lock_hash must match manager_route acceptance_lock.lock_sha256.",
            )
        )

    route_refs_nested = get_refs(route_doc.tree)
    intake_refs = [item for item in route_refs_nested if item[1] == "task_intake"]
    if len(intake_refs) != 1:
        return issues

    intake_doc_id = intake_refs[0][0]
    intake_doc = context_index.get(intake_doc_id)
    if intake_doc is None:
        if strict_refs:
            issues.append(
                ValidationIssue(
                    code="E400_REF_NOT_FOUND",
                    message=f"Task intake '{intake_doc_id}' referenced via manager_route not found.",
                )
            )
        return issues
    if intake_doc.doc_class != "task_intake" or intake_doc.tree is None:
        return issues

    task_type = xpath_text(intake_doc.tree, "/p:pxml/p:payload/p:task_type")
    if task_type == "bugfix":
        localization_items = doc.tree.xpath(
            "/p:pxml/p:payload/p:localization_targets/p:item", namespaces=NS
        )
        if len(localization_items) == 0:
            issues.append(
                ValidationIssue(
                    code="E360_BUGFIX_LOCALIZATION_REQUIRED",
                    message="Bugfix-linked execution_packet must include localization_targets.",
                )
            )

    exploration_ref_class = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:exploration_notes_ref/p:doc_class"
    )
    if exploration_ref_class and exploration_ref_class != "exploration_result":
        issues.append(
            ValidationIssue(
                code="E703_PACKET_EXPLORATION_REF_CLASS_REQUIRED",
                message="exploration_notes_ref doc_class must be exploration_result when present.",
            )
        )

    return issues


def _normalize_rel_path(value: str) -> Optional[str]:
    raw = value.replace("\\", "/").strip()
    raw = re.sub(r"/+", "/", raw)
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw:
        return None
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts:
        return None
    return str(pure)


def _path_in_prefixes(path_value: str, prefixes: Sequence[str]) -> bool:
    normalized = _normalize_rel_path(path_value)
    if normalized is None:
        return False
    for prefix in prefixes:
        p = _normalize_rel_path(prefix)
        if p is None:
            continue
        p = p.rstrip("/")
        if normalized == p or normalized.startswith(p + "/"):
            return True
    return False


def _semantic_implementer_runtime_policy(doc: ParsedDoc) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []
    names = doc.tree.xpath(
        "/p:pxml/p:payload/p:rules/p:rule/p:rule_name/text()", namespaces=NS
    )
    rule_names = {item.strip() for item in names if item and item.strip()}
    missing = sorted(REQUIRED_IMPLEMENTER_RUNTIME_RULES - rule_names)
    if missing:
        issues.append(
            ValidationIssue(
                code="E730_IMPL_RUNTIME_POLICY_RULES_MISSING",
                message=(
                    "implementer_runtime_policy is missing required rule_name entries: "
                    + ", ".join(missing)
                ),
            )
        )
    return issues


def _taxonomy_codes(context_index: Dict[str, ParsedDoc]) -> set[str]:
    codes: set[str] = set()
    for candidate in context_index.values():
        if candidate.doc_class != "failure_reason_taxonomy" or candidate.tree is None:
            continue
        values = candidate.tree.xpath(
            "/p:pxml/p:payload/p:reasons/p:reason/p:code/text()",
            namespaces=NS,
        )
        for value in values:
            normalized = value.strip()
            if normalized:
                codes.add(normalized)
    return codes


def _semantic_failure_reason_consistency(
    doc: ParsedDoc,
    context_index: Dict[str, ParsedDoc],
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []
    taxonomy_codes = _taxonomy_codes(context_index)
    if not taxonomy_codes:
        return issues

    if doc.doc_class == "implementer_result":
        status = xpath_text(doc.tree, "/p:pxml/p:payload/p:result_status")
        reason = xpath_text(doc.tree, "/p:pxml/p:payload/p:blocked_reason")
        if status in {"blocked", "retry_failed", "escalated"} and reason:
            if reason not in taxonomy_codes:
                issues.append(
                    ValidationIssue(
                        code="E792_FAILURE_CODE_TAXONOMY_MISSING",
                        message=(
                            "implementer_result blocked_reason is not present in "
                            "failure_reason_taxonomy: "
                            f"{reason}"
                        ),
                    )
                )

    if doc.doc_class == "execution_trace":
        reasons = doc.tree.xpath(
            "/p:pxml/p:payload/p:events/p:event/p:reason_code/text()",
            namespaces=NS,
        )
        for value in reasons:
            reason = value.strip()
            if not reason:
                continue
            if reason not in taxonomy_codes:
                issues.append(
                    ValidationIssue(
                        code="E793_FAILURE_CODE_TAXONOMY_MISSING",
                        message=(
                            "execution_trace event reason_code is not present in "
                            "failure_reason_taxonomy: "
                            f"{reason}"
                        ),
                    )
                )

    if doc.doc_class == "task_status_report":
        reason_nodes = doc.tree.xpath(
            "/p:pxml/p:payload/p:failure_reason_codes/p:item/text()",
            namespaces=NS,
        )
        for value in reason_nodes:
            reason = value.strip()
            if not reason or reason == "none":
                continue
            if reason not in taxonomy_codes:
                issues.append(
                    ValidationIssue(
                        code="E794_FAILURE_CODE_TAXONOMY_MISSING",
                        message=(
                            "task_status_report failure_reason_codes contains unknown "
                            "taxonomy code: "
                            f"{reason}"
                        ),
                    )
                )

    return issues


def _semantic_post_implement_verification_policy(
    doc: ParsedDoc,
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    required = {
        "auto_verify_on_result_status",
        "auto_verify_required_lane",
        "skip_verify_on_blocked",
        "skip_verify_on_retry_failed",
        "skip_verify_on_escalated",
        "verify_on_no_op",
        "verify_on_applied",
        "human_override_allowed",
    }
    names = doc.tree.xpath(
        "/p:pxml/p:payload/p:rules/p:rule/p:rule_name/text()",
        namespaces=NS,
    )
    normalized = [item.strip() for item in names if item and item.strip()]
    missing = sorted(required - set(normalized))
    if missing:
        issues.append(
            ValidationIssue(
                code="E770_POST_VERIFY_RULE_REQUIRED",
                message=(
                    "post_implement_verification_policy is missing required rule_name "
                    "entries: " + ", ".join(missing)
                ),
            )
        )

    for name in normalized:
        if normalized.count(name) > 1:
            issues.append(
                ValidationIssue(
                    code="E795_POST_VERIFY_RULE_DUPLICATE",
                    message=f"Duplicate post-implement policy rule_name: {name}",
                )
            )

    return issues


def _semantic_failure_reason_taxonomy(doc: ParsedDoc) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    taxonomy_id = xpath_text(doc.tree, "/p:pxml/p:payload/p:taxonomy_id")
    if taxonomy_id is None:
        return issues

    reason_nodes = doc.tree.xpath("/p:pxml/p:payload/p:reasons/p:reason", namespaces=NS)
    seen: Dict[str, int] = {}
    for node in reason_nodes:
        node_tree = etree.ElementTree(node)
        reason_taxonomy_id = xpath_text(node_tree, "./p:taxonomy_id")
        code = xpath_text(node_tree, "./p:code")
        if reason_taxonomy_id and reason_taxonomy_id != taxonomy_id:
            issues.append(
                ValidationIssue(
                    code="E788_FAILURE_TAXONOMY_ID_MISMATCH",
                    message=(
                        "reason taxonomy_id does not match payload taxonomy_id: "
                        f"{reason_taxonomy_id}"
                    ),
                )
            )
        if code:
            seen[code] = seen.get(code, 0) + 1

    duplicates = sorted([code for code, count in seen.items() if count > 1])
    if duplicates:
        issues.append(
            ValidationIssue(
                code="E789_FAILURE_TAXONOMY_CODE_DUPLICATE",
                message=(
                    "failure_reason_taxonomy contains duplicate code entries: "
                    + ", ".join(duplicates)
                ),
            )
        )

    return issues


def _semantic_exploration_request(
    doc: ParsedDoc,
    refs: List[Tuple[str, Optional[str], Optional[str]]],
    context_index: Dict[str, ParsedDoc],
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    requester_agent = xpath_text(doc.tree, "/p:pxml/p:payload/p:requester_agent")
    if requester_agent not in ALLOWED_REQUESTERS:
        issues.append(
            ValidationIssue(
                code="E780_EXPLORATION_REQUEST_REQUESTER_INVALID",
                message="exploration_request requester_agent must be manager/planner/implementer/verifier.",
            )
        )

    writer_agent = xpath_text(doc.tree, "/p:pxml/p:meta/p:writer_agent")
    if writer_agent != "manager":
        issues.append(
            ValidationIssue(
                code="E781_EXPLORATION_REQUEST_WRITER_MANAGER_REQUIRED",
                message="exploration_request meta.writer_agent must be manager.",
            )
        )

    packet_refs = [item for item in refs if item[2] == "request_packet"]
    baseline_refs = [item for item in refs if item[2] == "baseline_context"]
    route_refs = [item for item in refs if item[2] == "route_context"]
    context_refs = [item for item in refs if item[2] == "request_context"]
    if len(packet_refs) != 1 or packet_refs[0][1] != "execution_packet":
        issues.append(
            ValidationIssue(
                code="E782_EXPLORATION_REQUEST_PACKET_REF_REQUIRED",
                message="exploration_request must reference exactly one execution_packet with relation request_packet.",
            )
        )
    if len(baseline_refs) != 1 or baseline_refs[0][1] != "exploration_result":
        issues.append(
            ValidationIssue(
                code="E783_EXPLORATION_REQUEST_BASELINE_REF_REQUIRED",
                message="exploration_request must reference exactly one exploration_result with relation baseline_context.",
            )
        )
    if len(route_refs) > 1:
        issues.append(
            ValidationIssue(
                code="E784_EXPLORATION_REQUEST_ROUTE_REF_COUNT",
                message="exploration_request may reference at most one manager_route route_context artifact.",
            )
        )
    if len(context_refs) > 1:
        issues.append(
            ValidationIssue(
                code="E785_EXPLORATION_REQUEST_CONTEXT_REF_COUNT",
                message="exploration_request may reference at most one requester context artifact.",
            )
        )
    elif context_refs and context_refs[0][1] not in {
        "plan_sidecar",
        "implementer_result",
        "verification_result",
    }:
        issues.append(
            ValidationIssue(
                code="E786_EXPLORATION_REQUEST_CONTEXT_REF_CLASS",
                message="exploration_request request_context must be plan_sidecar, implementer_result, or verification_result.",
            )
        )

    focus_questions = [
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:focus_questions/p:item/text()", namespaces=NS
        )
        if item and item.strip()
    ]
    target_hints = [
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:target_hints/p:item/text()", namespaces=NS
        )
        if item and item.strip()
    ]
    if len(focus_questions) < 1 or len(focus_questions) > 3:
        issues.append(
            ValidationIssue(
                code="E787_EXPLORATION_REQUEST_FOCUS_COUNT",
                message="exploration_request focus_questions must contain 1 to 3 items.",
            )
        )
    if len(target_hints) < 1 or len(target_hints) > 5:
        issues.append(
            ValidationIssue(
                code="E788_EXPLORATION_REQUEST_HINT_COUNT",
                message="exploration_request target_hints must contain 1 to 5 items.",
            )
        )
    if target_hints and not any(is_concrete_hint(item) for item in target_hints):
        issues.append(
            ValidationIssue(
                code="E789_EXPLORATION_REQUEST_CONCRETE_HINT_REQUIRED",
                message="exploration_request requires at least one concrete target_hint.",
            )
        )
    if any(looks_broad(item) for item in focus_questions + target_hints):
        issues.append(
            ValidationIssue(
                code="E799_EXPLORATION_REQUEST_BROAD_FORBIDDEN",
                message="exploration_request cannot ask for broad repo rediscovery.",
            )
        )

    meta_task_id = xpath_text(doc.tree, "/p:pxml/p:meta/p:task_id")
    for ref_doc_id, _ref_class, _relation in (
        packet_refs + baseline_refs + route_refs + context_refs
    ):
        target = context_index.get(ref_doc_id)
        if target is None or target.tree is None:
            continue
        target_task_id = xpath_text(target.tree, "/p:pxml/p:meta/p:task_id")
        if meta_task_id and target_task_id and meta_task_id != target_task_id:
            issues.append(
                ValidationIssue(
                    code="E798_EXPLORATION_REQUEST_TASK_ID_MISMATCH",
                    message="exploration_request task_id must match all referenced task artifacts.",
                )
            )
            break

    return issues


def _semantic_exploration_result(
    doc: ParsedDoc,
    refs: List[Tuple[str, Optional[str], Optional[str]]],
    context_index: Dict[str, ParsedDoc],
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    payload_task_id = xpath_text(doc.tree, "/p:pxml/p:payload/p:task_id")
    meta_task_id = xpath_text(doc.tree, "/p:pxml/p:meta/p:task_id")
    if payload_task_id != meta_task_id:
        issues.append(
            ValidationIssue(
                code="E790_EXPLORATION_RESULT_TASK_ID_MISMATCH",
                message="exploration_result payload task_id must match meta task_id.",
            )
        )

    packet_refs = [item for item in refs if item[1] == "execution_packet"]
    request_refs = [item for item in refs if item[2] == "request"]
    parent_refs = [item for item in refs if item[2] == "parent_exploration"]
    if len(packet_refs) != 1:
        issues.append(
            ValidationIssue(
                code="E791_EXPLORATION_RESULT_PACKET_REF_REQUIRED",
                message="exploration_result must reference exactly one execution_packet artifact.",
            )
        )

    payload_packet_ref = xpath_text(doc.tree, "/p:pxml/p:payload/p:packet_ref/p:doc_id")
    payload_packet_class = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:packet_ref/p:doc_class"
    )
    if payload_packet_class != "execution_packet":
        issues.append(
            ValidationIssue(
                code="E792_EXPLORATION_RESULT_PACKET_REF_CLASS_REQUIRED",
                message="exploration_result payload packet_ref doc_class must be execution_packet.",
            )
        )
    if packet_refs and payload_packet_ref != packet_refs[0][0]:
        issues.append(
            ValidationIssue(
                code="E793_EXPLORATION_RESULT_PACKET_REF_MISMATCH",
                message="exploration_result payload packet_ref must match top-level execution_packet ref.",
            )
        )

    exploration_scope = xpath_text(doc.tree, "/p:pxml/p:payload/p:exploration_scope")
    actionability = xpath_text(doc.tree, "/p:pxml/p:payload/p:actionability")
    if exploration_scope == "focused_refresh" and len(request_refs) != 1:
        issues.append(
            ValidationIssue(
                code="E797_EXPLORATION_RESULT_REQUEST_REF_REQUIRED",
                message="focused exploration_result must reference exactly one exploration_request artifact.",
            )
        )
    elif request_refs and request_refs[0][1] != "exploration_request":
        issues.append(
            ValidationIssue(
                code="E798_EXPLORATION_RESULT_REQUEST_REF_CLASS",
                message="exploration_result request relation must reference exploration_request.",
            )
        )
    if len(parent_refs) > 1:
        issues.append(
            ValidationIssue(
                code="E799_EXPLORATION_RESULT_PARENT_REF_COUNT",
                message="exploration_result may reference at most one parent exploration_result artifact.",
            )
        )
    elif parent_refs and parent_refs[0][1] != "exploration_result":
        issues.append(
            ValidationIssue(
                code="E800_EXPLORATION_RESULT_PARENT_REF_CLASS",
                message="exploration_result parent_exploration relation must reference exploration_result.",
            )
        )

    completion_state = xpath_text(doc.tree, "/p:pxml/p:payload/p:completion_state")
    blocked_reason = xpath_text(doc.tree, "/p:pxml/p:payload/p:blocked_reason")
    if completion_state in {"blocked", "failed"} and blocked_reason is None:
        issues.append(
            ValidationIssue(
                code="E794_EXPLORATION_RESULT_BLOCKED_REASON_REQUIRED",
                message="exploration_result blocked/failed completion_state requires blocked_reason.",
            )
        )

    findings = doc.tree.xpath(
        "/p:pxml/p:payload/p:key_findings/p:item/text()", namespaces=NS
    )
    evidence_items = doc.tree.xpath(
        "/p:pxml/p:payload/p:evidence_items/p:evidence", namespaces=NS
    )
    if (
        completion_state == "completed_and_verified"
        and not findings
        and not evidence_items
    ):
        issues.append(
            ValidationIssue(
                code="E795_EXPLORATION_RESULT_EVIDENCE_REQUIRED",
                message="exploration_result completed_and_verified requires findings or evidence_items.",
            )
        )

    if actionability == "contract_refresh_required" and not (
        blocked_reason
        or findings
        or doc.tree.xpath("/p:pxml/p:payload/p:open_questions/p:item", namespaces=NS)
    ):
        issues.append(
            ValidationIssue(
                code="E801_EXPLORATION_RESULT_REFRESH_BASIS_REQUIRED",
                message="contract_refresh_required exploration_result must carry a blocker, findings, or open questions.",
            )
        )

    if payload_packet_ref:
        packet_doc = context_index.get(payload_packet_ref)
        if packet_doc is not None and packet_doc.tree is not None:
            packet_task_id = xpath_text(packet_doc.tree, "/p:pxml/p:meta/p:task_id")
            if packet_task_id and meta_task_id and packet_task_id != meta_task_id:
                issues.append(
                    ValidationIssue(
                        code="E796_EXPLORATION_RESULT_PACKET_TASK_ID_MISMATCH",
                        message="Referenced execution_packet task_id must match exploration_result task_id.",
                    )
                )

    if request_refs:
        request_doc = context_index.get(request_refs[0][0])
        if request_doc is not None and request_doc.tree is not None:
            request_task_id = xpath_text(request_doc.tree, "/p:pxml/p:meta/p:task_id")
            if request_task_id and meta_task_id and request_task_id != meta_task_id:
                issues.append(
                    ValidationIssue(
                        code="E802_EXPLORATION_RESULT_REQUEST_TASK_ID_MISMATCH",
                        message="Referenced exploration_request task_id must match exploration_result task_id.",
                    )
                )

    return issues


def _semantic_task_status_report(
    doc: ParsedDoc,
    refs: List[Tuple[str, Optional[str], Optional[str]]],
    context_index: Dict[str, ParsedDoc],
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    payload_task_id = xpath_text(doc.tree, "/p:pxml/p:payload/p:task_id")
    meta_task_id = xpath_text(doc.tree, "/p:pxml/p:meta/p:task_id")
    if payload_task_id != meta_task_id:
        issues.append(
            ValidationIssue(
                code="E796_STATUS_REPORT_TASK_ID_MISMATCH",
                message="task_status_report payload task_id must match meta task_id.",
            )
        )

    top_ref_ids = {doc_id for doc_id, _doc_class, _relation in refs}
    payload_ref_nodes = doc.tree.xpath(
        "/p:pxml/p:payload/*[substring(local-name(), string-length(local-name()) - 3) = '_ref']",
        namespaces=NS,
    )
    for node in payload_ref_nodes:
        node_tree = etree.ElementTree(node)
        ref_id = xpath_text(node_tree, "./p:doc_id")
        if ref_id and ref_id not in top_ref_ids:
            issues.append(
                ValidationIssue(
                    code="E797_STATUS_REPORT_REF_MISSING",
                    message=(
                        "task_status_report payload *_ref doc_id is not present in top-level refs: "
                        f"{ref_id}"
                    ),
                )
            )

    selected_path = xpath_text(doc.tree, "/p:pxml/p:payload/p:selected_path")
    current_phase = xpath_text(doc.tree, "/p:pxml/p:payload/p:current_phase")
    current_status = xpath_text(doc.tree, "/p:pxml/p:payload/p:current_status")
    verification_ref = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:latest_verification_ref/p:doc_id"
    )
    exploration_ref_class = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:latest_exploration_result_ref/p:doc_class"
    )
    if exploration_ref_class and exploration_ref_class != "exploration_result":
        issues.append(
            ValidationIssue(
                code="E799_STATUS_REPORT_EXPLORATION_REF_CLASS_REQUIRED",
                message="latest_exploration_result_ref doc_class must be exploration_result when present.",
            )
        )
    if current_phase == "verifying" and selected_path in {"verifier_post", "full_lane"}:
        if current_status == "running" and verification_ref is not None:
            verification_doc = context_index.get(verification_ref)
            if verification_doc is not None and verification_doc.tree is not None:
                verdict = xpath_text(
                    verification_doc.tree, "/p:pxml/p:payload/p:final_verdict"
                )
                if verdict in {"pass", "fail", "inconclusive"}:
                    issues.append(
                        ValidationIssue(
                            code="E798_STATUS_REPORT_PHASE_STALE",
                            message=(
                                "task_status_report current_status=running in verifying phase "
                                "is stale when latest_verification_ref has final verdict."
                            ),
                        )
                    )

    return issues


def _semantic_runtime_retention_policy(doc: ParsedDoc) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    basis = doc.tree.xpath(
        "/p:pxml/p:payload/p:stale_artifact_detection_basis/p:item/text()",
        namespaces=NS,
    )
    criteria = doc.tree.xpath(
        "/p:pxml/p:payload/p:cleanup_vs_quarantine_criteria/p:item/text()",
        namespaces=NS,
    )
    normalized_basis = [item.strip() for item in basis if item and item.strip()]
    normalized_criteria = [item.strip() for item in criteria if item and item.strip()]

    if len(normalized_basis) < 1:
        issues.append(
            ValidationIssue(
                code="E810_RETENTION_DETECTION_BASIS_REQUIRED",
                message="runtime_retention_policy must include stale_artifact_detection_basis items.",
            )
        )
    if len(normalized_criteria) < 2:
        issues.append(
            ValidationIssue(
                code="E811_RETENTION_CRITERIA_MIN_REQUIRED",
                message="runtime_retention_policy must include at least two cleanup_vs_quarantine_criteria items.",
            )
        )

    cleanup_text = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:task_id_scoped_cleanup_principle"
    )
    if cleanup_text and "task_id" not in cleanup_text:
        issues.append(
            ValidationIssue(
                code="E812_RETENTION_TASK_SCOPE_REQUIRED",
                message="task_id_scoped_cleanup_principle should explicitly mention task_id scope.",
            )
        )

    lineage_response = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:lineage_mismatch_response"
    )
    if lineage_response and "quarantine" not in lineage_response.lower():
        issues.append(
            ValidationIssue(
                code="E813_RETENTION_LINEAGE_RESPONSE_REQUIRED",
                message="lineage_mismatch_response should mention quarantine behavior.",
            )
        )

    return issues


def _semantic_artifact_pruning_policy(doc: ParsedDoc) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    names = doc.tree.xpath(
        "/p:pxml/p:payload/p:rules/p:rule/p:rule_name/text()",
        namespaces=NS,
    )
    normalized = [item.strip() for item in names if item and item.strip()]
    missing = sorted(REQUIRED_ARTIFACT_PRUNING_POLICY_RULES - set(normalized))
    if missing:
        issues.append(
            ValidationIssue(
                code="E930_PRUNING_POLICY_RULES_MISSING",
                message=(
                    "artifact_pruning_policy is missing required rule_name entries: "
                    + ", ".join(missing)
                ),
            )
        )

    for name in normalized:
        if normalized.count(name) > 1:
            issues.append(
                ValidationIssue(
                    code="E931_PRUNING_POLICY_RULE_DUPLICATE",
                    message=f"Duplicate artifact_pruning_policy rule_name: {name}",
                )
            )

    return issues


def _semantic_pruning_report(
    doc: ParsedDoc,
    refs: List[Tuple[str, Optional[str], Optional[str]]],
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    payload_task_id = xpath_text(doc.tree, "/p:pxml/p:payload/p:task_scope")
    meta_task_id = xpath_text(doc.tree, "/p:pxml/p:meta/p:task_id")
    if (
        payload_task_id
        and payload_task_id.startswith("task_")
        and payload_task_id != meta_task_id
    ):
        issues.append(
            ValidationIssue(
                code="E932_PRUNING_REPORT_TASK_SCOPE_MISMATCH",
                message="task-scoped pruning_report payload task_scope must match meta task_id.",
            )
        )

    policy_ref_class = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:policy_ref/p:doc_class"
    )
    if policy_ref_class != "artifact_pruning_policy":
        issues.append(
            ValidationIssue(
                code="E933_PRUNING_REPORT_POLICY_REF_CLASS_REQUIRED",
                message="pruning_report policy_ref doc_class must be artifact_pruning_policy.",
            )
        )

    candidate_count_text = xpath_text(doc.tree, "/p:pxml/p:payload/p:candidate_count")
    try:
        candidate_count = int(candidate_count_text) if candidate_count_text else -1
    except ValueError:
        candidate_count = -1
    denied_count = len(
        doc.tree.xpath(
            "/p:pxml/p:payload/p:denied_candidates/p:candidate",
            namespaces=NS,
        )
    )
    quarantine_count = len(
        doc.tree.xpath(
            "/p:pxml/p:payload/p:quarantine_candidates/p:candidate",
            namespaces=NS,
        )
    )
    delete_count = len(
        doc.tree.xpath(
            "/p:pxml/p:payload/p:delete_candidates/p:candidate",
            namespaces=NS,
        )
    )
    total = denied_count + quarantine_count + delete_count
    if candidate_count != total:
        issues.append(
            ValidationIssue(
                code="E935_PRUNING_REPORT_CANDIDATE_COUNT_MISMATCH",
                message=(
                    "pruning_report candidate_count must equal denied+quarantine+delete candidates "
                    f"({candidate_count} != {total})."
                ),
            )
        )

    if (
        len(
            doc.tree.xpath(
                "/p:pxml/p:payload/p:warnings/p:item[normalize-space(text())='none']",
                namespaces=NS,
            )
        )
        > 0
        and len(doc.tree.xpath("/p:pxml/p:payload/p:warnings/p:item", namespaces=NS))
        > 1
    ):
        issues.append(
            ValidationIssue(
                code="E936_PRUNING_REPORT_WARNINGS_INVALID",
                message="pruning_report warnings cannot mix 'none' with additional values.",
            )
        )

    return issues


def _semantic_release_candidate_policy(doc: ParsedDoc) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    names = doc.tree.xpath(
        "/p:pxml/p:payload/p:rules/p:rule/p:rule_name/text()",
        namespaces=NS,
    )
    normalized = [item.strip() for item in names if item and item.strip()]
    missing = sorted(REQUIRED_RELEASE_CANDIDATE_POLICY_RULES - set(normalized))
    if missing:
        issues.append(
            ValidationIssue(
                code="E998_RC_POLICY_RULES_MISSING",
                message=(
                    "release_candidate_policy is missing required rule_name entries: "
                    + ", ".join(missing)
                ),
            )
        )

    for name in normalized:
        if normalized.count(name) > 1:
            issues.append(
                ValidationIssue(
                    code="E999_RC_POLICY_RULE_DUPLICATE",
                    message=f"Duplicate release_candidate_policy rule_name: {name}",
                )
            )

    return issues


def _semantic_release_candidate_report(
    doc: ParsedDoc,
    refs: List[Tuple[str, Optional[str], Optional[str]]],
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    policy_ref_class = xpath_text(
        doc.tree,
        "/p:pxml/p:payload/p:policy_ref/p:doc_class",
    )
    if policy_ref_class != "release_candidate_policy":
        issues.append(
            ValidationIssue(
                code="E1000_RC_REPORT_POLICY_REF_CLASS_REQUIRED",
                message="release_candidate_report policy_ref doc_class must be release_candidate_policy.",
            )
        )

    smoke_refs = doc.tree.xpath(
        "/p:pxml/p:payload/p:smoke_task_refs/p:ref",
        namespaces=NS,
    )
    if len(smoke_refs) == 0:
        issues.append(
            ValidationIssue(
                code="E1001_RC_REPORT_SMOKE_REFS_REQUIRED",
                message="release_candidate_report must include smoke_task_refs entries.",
            )
        )

    harness_refs = doc.tree.xpath(
        "/p:pxml/p:payload/p:harness_version_refs/p:ref",
        namespaces=NS,
    )
    if len(harness_refs) == 0:
        issues.append(
            ValidationIssue(
                code="E1002_RC_REPORT_HARNESS_REFS_REQUIRED",
                message="release_candidate_report must include harness_version_refs entries.",
            )
        )

    profile_harness_refs = doc.tree.xpath(
        "/p:pxml/p:payload/p:harness_version_refs/p:ref[p:doc_class='release_gate_profile']",
        namespaces=NS,
    )
    if len(profile_harness_refs) == 0:
        issues.append(
            ValidationIssue(
                code="E1018_RC_REPORT_PROFILE_REF_REQUIRED",
                message=(
                    "release_candidate_report harness_version_refs must include "
                    "release_gate_profile."
                ),
            )
        )

    coverage_policy_harness_refs = doc.tree.xpath(
        "/p:pxml/p:payload/p:harness_version_refs/p:ref[p:doc_class='coverage_outcome_policy']",
        namespaces=NS,
    )
    if len(coverage_policy_harness_refs) == 0:
        issues.append(
            ValidationIssue(
                code="E1019_RC_REPORT_COVERAGE_POLICY_REF_REQUIRED",
                message=(
                    "release_candidate_report harness_version_refs must include "
                    "coverage_outcome_policy."
                ),
            )
        )

    coverage_refs = doc.tree.xpath(
        "/p:pxml/p:payload/p:coverage_task_refs/p:ref",
        namespaces=NS,
    )
    candidate_refs = doc.tree.xpath(
        "/p:pxml/p:payload/p:candidate_gate_task_refs/p:ref",
        namespaces=NS,
    )
    basis = xpath_text(doc.tree, "/p:pxml/p:payload/p:rc_result_basis")
    if basis == "candidate_gate_subset" and len(candidate_refs) == 0:
        issues.append(
            ValidationIssue(
                code="E1014_RC_REPORT_CANDIDATE_REFS_REQUIRED",
                message=(
                    "release_candidate_report rc_result_basis=candidate_gate_subset "
                    "requires candidate_gate_task_refs entries."
                ),
            )
        )

    coverage_summary = doc.tree.xpath(
        "/p:pxml/p:payload/p:coverage_summary/p:item",
        namespaces=NS,
    )
    gate_summary = doc.tree.xpath(
        "/p:pxml/p:payload/p:gate_summary/p:item",
        namespaces=NS,
    )
    if len(coverage_refs) > 0 and len(coverage_summary) == 0:
        issues.append(
            ValidationIssue(
                code="E1015_RC_REPORT_COVERAGE_SUMMARY_REQUIRED",
                message=(
                    "release_candidate_report coverage_task_refs requires coverage_summary entries."
                ),
            )
        )
    if len(candidate_refs) > 0 and len(gate_summary) == 0:
        issues.append(
            ValidationIssue(
                code="E1016_RC_REPORT_GATE_SUMMARY_REQUIRED",
                message=(
                    "release_candidate_report candidate_gate_task_refs requires gate_summary entries."
                ),
            )
        )

    warning_nodes = doc.tree.xpath(
        "/p:pxml/p:payload/p:warnings/p:item/text()",
        namespaces=NS,
    )
    warnings = [item.strip() for item in warning_nodes if item and item.strip()]
    if "none" in warnings and len(warnings) > 1:
        issues.append(
            ValidationIssue(
                code="E1003_RC_REPORT_WARNINGS_INVALID",
                message="release_candidate_report warnings cannot mix 'none' with additional values.",
            )
        )

    blocker_nodes = doc.tree.xpath(
        "/p:pxml/p:payload/p:blockers/p:item/text()",
        namespaces=NS,
    )
    blockers = [item.strip() for item in blocker_nodes if item and item.strip()]
    if "none" in blockers and len(blockers) > 1:
        issues.append(
            ValidationIssue(
                code="E1004_RC_REPORT_BLOCKERS_INVALID",
                message="release_candidate_report blockers cannot mix 'none' with additional values.",
            )
        )

    rc_result = xpath_text(doc.tree, "/p:pxml/p:payload/p:rc_result")
    latest_pointer_safety = xpath_text(
        doc.tree,
        "/p:pxml/p:payload/p:latest_pointer_safety",
    )
    lineage_safety = xpath_text(doc.tree, "/p:pxml/p:payload/p:lineage_safety")
    if rc_result == "pass":
        if blockers != ["none"]:
            issues.append(
                ValidationIssue(
                    code="E1005_RC_REPORT_PASS_BLOCKERS_INVALID",
                    message="release_candidate_report rc_result=pass requires blockers=['none'].",
                )
            )
        if latest_pointer_safety != "true":
            issues.append(
                ValidationIssue(
                    code="E1006_RC_REPORT_PASS_POINTER_SAFETY_REQUIRED",
                    message="release_candidate_report rc_result=pass requires latest_pointer_safety=true.",
                )
            )
        if lineage_safety != "true":
            issues.append(
                ValidationIssue(
                    code="E1007_RC_REPORT_PASS_LINEAGE_SAFETY_REQUIRED",
                    message="release_candidate_report rc_result=pass requires lineage_safety=true.",
                )
            )

    if rc_result == "fail" and (not blockers or blockers == ["none"]):
        issues.append(
            ValidationIssue(
                code="E1008_RC_REPORT_FAIL_BLOCKERS_REQUIRED",
                message="release_candidate_report rc_result=fail requires non-none blocker entries.",
            )
        )

    top_ref_ids = {doc_id for doc_id, _doc_class, _relation in refs}
    for node_name in [
        "policy_ref",
    ]:
        ref_id = xpath_text(doc.tree, f"/p:pxml/p:payload/p:{node_name}/p:doc_id")
        if ref_id and top_ref_ids and ref_id not in top_ref_ids:
            issues.append(
                ValidationIssue(
                    code="E1009_RC_REPORT_REF_MISSING",
                    message=f"release_candidate_report payload {node_name} doc_id should be present in top-level refs.",
                )
            )

    return issues


def _semantic_release_bundle_manifest(
    doc: ParsedDoc,
    refs: List[Tuple[str, Optional[str], Optional[str]]],
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    source_class = xpath_text(
        doc.tree,
        "/p:pxml/p:payload/p:source_release_candidate_report_ref/p:doc_class",
    )
    if source_class != "release_candidate_report":
        issues.append(
            ValidationIssue(
                code="E1010_RC_BUNDLE_SOURCE_REF_CLASS_REQUIRED",
                message="release_bundle_manifest source_release_candidate_report_ref doc_class must be release_candidate_report.",
            )
        )

    warning_nodes = doc.tree.xpath(
        "/p:pxml/p:payload/p:known_warnings/p:item/text()",
        namespaces=NS,
    )
    warnings = [item.strip() for item in warning_nodes if item and item.strip()]
    if "none" in warnings and len(warnings) > 1:
        issues.append(
            ValidationIssue(
                code="E1011_RC_BUNDLE_WARNINGS_INVALID",
                message="release_bundle_manifest known_warnings cannot mix 'none' with additional values.",
            )
        )

    profile_policy_refs = doc.tree.xpath(
        "/p:pxml/p:payload/p:key_policy_refs/p:ref[p:doc_class='release_gate_profile']",
        namespaces=NS,
    )
    if len(profile_policy_refs) == 0:
        issues.append(
            ValidationIssue(
                code="E1026_RC_BUNDLE_PROFILE_POLICY_REQUIRED",
                message=(
                    "release_bundle_manifest key_policy_refs must include "
                    "release_gate_profile."
                ),
            )
        )

    coverage_policy_refs = doc.tree.xpath(
        "/p:pxml/p:payload/p:key_policy_refs/p:ref[p:doc_class='coverage_outcome_policy']",
        namespaces=NS,
    )
    if len(coverage_policy_refs) == 0:
        issues.append(
            ValidationIssue(
                code="E1027_RC_BUNDLE_COVERAGE_POLICY_REQUIRED",
                message=(
                    "release_bundle_manifest key_policy_refs must include "
                    "coverage_outcome_policy."
                ),
            )
        )

    required_entrypoints = {
        "cleanup_task_runtime",
        "task_executor",
        "operator_preflight",
        "final_renderer",
        "operator_runbook",
        "runtime_prune",
        "session_report_refresh",
        "release_candidate_check",
        "release_ops_gate",
    }
    entry_values = doc.tree.xpath(
        "/p:pxml/p:payload/p:operator_entrypoints/p:item/text()",
        namespaces=NS,
    )
    normalized = {item.strip() for item in entry_values if item and item.strip()}
    missing = sorted(required_entrypoints - normalized)
    if missing:
        issues.append(
            ValidationIssue(
                code="E1012_RC_BUNDLE_ENTRYPOINTS_MISSING",
                message=(
                    "release_bundle_manifest operator_entrypoints missing required items: "
                    + ", ".join(missing)
                ),
            )
        )

    top_ref_ids = {doc_id for doc_id, _doc_class, _relation in refs}
    source_ref = xpath_text(
        doc.tree,
        "/p:pxml/p:payload/p:source_release_candidate_report_ref/p:doc_id",
    )
    if source_ref and top_ref_ids and source_ref not in top_ref_ids:
        issues.append(
            ValidationIssue(
                code="E1013_RC_BUNDLE_SOURCE_REF_MISSING",
                message="release_bundle_manifest source_release_candidate_report_ref doc_id should be present in top-level refs.",
            )
        )

    smoke_task_ids = {
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:smoke_task_ids/p:item/text()",
            namespaces=NS,
        )
        if item and item.strip()
    }
    candidate_task_ids = {
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:candidate_gate_task_ids/p:item/text()",
            namespaces=NS,
        )
        if item and item.strip()
    }
    if candidate_task_ids and not candidate_task_ids.issubset(smoke_task_ids):
        missing = sorted(candidate_task_ids - smoke_task_ids)
        issues.append(
            ValidationIssue(
                code="E1017_RC_BUNDLE_CANDIDATE_SUBSET_REQUIRED",
                message=(
                    "release_bundle_manifest candidate_gate_task_ids must be subset of "
                    "smoke_task_ids: " + ", ".join(missing)
                ),
            )
        )

    return issues


def _semantic_trace_event_semantics(doc: ParsedDoc) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    required = {
        "implement_start",
        "patch_applied",
        "blocked",
        "retry_failed",
        "review_done",
        "verify_done",
        "escalation",
        "stop",
    }
    event_types = doc.tree.xpath(
        "/p:pxml/p:payload/p:events/p:event/p:event_type/text()",
        namespaces=NS,
    )
    normalized = [item.strip() for item in event_types if item and item.strip()]
    missing = sorted(required - set(normalized))
    if missing:
        issues.append(
            ValidationIssue(
                code="E820_TRACE_SEMANTICS_EVENT_REQUIRED",
                message=(
                    "trace_event_semantics is missing required event definitions: "
                    + ", ".join(missing)
                ),
            )
        )

    lane_sem = xpath_text(doc.tree, "/p:pxml/p:payload/p:lane_verifier_semantics")
    post_sem = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:post_implement_verifier_semantics"
    )
    if lane_sem is None:
        issues.append(
            ValidationIssue(
                code="E828_TRACE_SEMANTICS_LANE_VERIFY_REQUIRED",
                message="trace_event_semantics must include lane_verifier_semantics.",
            )
        )
    if post_sem is None:
        issues.append(
            ValidationIssue(
                code="E829_TRACE_SEMANTICS_POST_VERIFY_REQUIRED",
                message="trace_event_semantics must include post_implement_verifier_semantics.",
            )
        )

    verify_nodes = doc.tree.xpath(
        "/p:pxml/p:payload/p:events/p:event[p:event_type='verify_done']",
        namespaces=NS,
    )
    for node in verify_nodes:
        node_tree = etree.ElementTree(node)
        verify_phase = xpath_text(node_tree, "./p:verify_phase_hint")
        if verify_phase != "either":
            issues.append(
                ValidationIssue(
                    code="E830_TRACE_SEMANTICS_VERIFY_PHASE_REQUIRED",
                    message="verify_done semantics must set verify_phase_hint=either.",
                )
            )

    return issues


def _semantic_compaction_checkpoint(
    doc: ParsedDoc,
    refs: List[Tuple[str, Optional[str], Optional[str]]],
    context_index: Dict[str, ParsedDoc],
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    payload_task_id = xpath_text(doc.tree, "/p:pxml/p:payload/p:task_id")
    meta_task_id = xpath_text(doc.tree, "/p:pxml/p:meta/p:task_id")
    if payload_task_id != meta_task_id:
        issues.append(
            ValidationIssue(
                code="E831_COMPACTION_TASK_ID_MISMATCH",
                message="compaction_checkpoint payload task_id must match meta task_id.",
            )
        )

    source_trace_doc_id = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:source_trace_ref/p:doc_id"
    )
    source_trace_class = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:source_trace_ref/p:doc_class"
    )
    if source_trace_class != "execution_trace":
        issues.append(
            ValidationIssue(
                code="E832_COMPACTION_TRACE_CLASS_REQUIRED",
                message="source_trace_ref doc_class must be execution_trace.",
            )
        )

    top_ref_ids = {doc_id for doc_id, _doc_class, _relation in refs}
    if source_trace_doc_id and source_trace_doc_id not in top_ref_ids:
        issues.append(
            ValidationIssue(
                code="E833_COMPACTION_SOURCE_TRACE_REF_MISSING",
                message="source_trace_ref doc_id must be present in top-level refs.",
            )
        )

    source_seq_text = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:source_trace_last_sequence"
    )
    from_seq_text = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:included_event_range/p:from_event_seq"
    )
    to_seq_text = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:included_event_range/p:to_event_seq"
    )
    try:
        source_seq = int(source_seq_text) if source_seq_text else -1
        from_seq = int(from_seq_text) if from_seq_text else -1
        to_seq = int(to_seq_text) if to_seq_text else -1
    except ValueError:
        source_seq = -1
        from_seq = -1
        to_seq = -1

    if source_seq != to_seq:
        issues.append(
            ValidationIssue(
                code="E834_COMPACTION_LAST_SEQUENCE_MISMATCH",
                message="source_trace_last_sequence must equal included_event_range/to_event_seq.",
            )
        )
    if from_seq > to_seq:
        issues.append(
            ValidationIssue(
                code="E835_COMPACTION_EVENT_RANGE_INVALID",
                message="included_event_range from_event_seq must be <= to_event_seq.",
            )
        )

    lineage = xpath_text(doc.tree, "/p:pxml/p:payload/p:lineage_lock_sha256")
    if lineage is None:
        issues.append(
            ValidationIssue(
                code="E836_COMPACTION_LINEAGE_REQUIRED",
                message="compaction_checkpoint must include lineage_lock_sha256.",
            )
        )

    for node_name, expected_class, code in [
        ("created_from_status_report_ref", "task_status_report", "E837"),
        ("created_from_latest_packet_ref", "execution_packet", "E838"),
        ("created_from_latest_route_ref", "manager_route", "E839"),
    ]:
        value = xpath_text(doc.tree, f"/p:pxml/p:payload/p:{node_name}/p:doc_class")
        if value != expected_class:
            issues.append(
                ValidationIssue(
                    code=f"{code}_COMPACTION_CREATED_FROM_REF_CLASS_REQUIRED",
                    message=f"{node_name} doc_class must be {expected_class}.",
                )
            )

    verification_class = xpath_text(
        doc.tree,
        "/p:pxml/p:payload/p:created_from_latest_verification_ref/p:doc_class",
    )
    if verification_class and verification_class != "verification_result":
        issues.append(
            ValidationIssue(
                code="E840_COMPACTION_VERIFICATION_REF_CLASS_REQUIRED",
                message="created_from_latest_verification_ref doc_class must be verification_result.",
            )
        )

    if source_trace_doc_id:
        source_doc = context_index.get(source_trace_doc_id)
        if source_doc is not None and source_doc.tree is not None:
            source_meta_seq = xpath_text(source_doc.tree, "/p:pxml/p:meta/p:sequence")
            if (
                source_meta_seq
                and source_meta_seq.isdigit()
                and source_seq > int(source_meta_seq)
            ):
                issues.append(
                    ValidationIssue(
                        code="E841_COMPACTION_TRACE_SEQUENCE_EXCEEDS_SOURCE",
                        message="source_trace_last_sequence exceeds source execution_trace meta sequence.",
                    )
                )

    return issues


def _semantic_operator_preflight_report(
    doc: ParsedDoc,
    refs: List[Tuple[str, Optional[str], Optional[str]]],
    context_index: Dict[str, ParsedDoc],
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    payload_task_id = xpath_text(doc.tree, "/p:pxml/p:payload/p:task_id")
    meta_task_id = xpath_text(doc.tree, "/p:pxml/p:meta/p:task_id")
    if payload_task_id != meta_task_id:
        issues.append(
            ValidationIssue(
                code="E842_PREFLIGHT_TASK_ID_MISMATCH",
                message="operator_preflight_report payload task_id must match meta task_id.",
            )
        )

    top_ref_ids = {doc_id for doc_id, _doc_class, _relation in refs}
    for node_name, expected_class, code in [
        ("latest_route_ref", "manager_route", "E843"),
        ("latest_packet_ref", "execution_packet", "E844"),
        ("latest_status_report_ref", "task_status_report", "E845"),
        ("latest_trace_ref", "execution_trace", "E846"),
    ]:
        doc_id = xpath_text(doc.tree, f"/p:pxml/p:payload/p:{node_name}/p:doc_id")
        doc_class = xpath_text(doc.tree, f"/p:pxml/p:payload/p:{node_name}/p:doc_class")
        if doc_class != expected_class:
            issues.append(
                ValidationIssue(
                    code=f"{code}_PREFLIGHT_REF_CLASS_REQUIRED",
                    message=f"{node_name} doc_class must be {expected_class}.",
                )
            )
        if doc_id and doc_id not in top_ref_ids:
            issues.append(
                ValidationIssue(
                    code=f"{code}_PREFLIGHT_REF_MISSING",
                    message=f"{node_name} doc_id must be present in top-level refs.",
                )
            )

    verification_doc_class = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:latest_verification_ref/p:doc_class"
    )
    if verification_doc_class and verification_doc_class != "verification_result":
        issues.append(
            ValidationIssue(
                code="E847_PREFLIGHT_VERIFICATION_REF_CLASS_REQUIRED",
                message="latest_verification_ref doc_class must be verification_result.",
            )
        )

    quarantine_flags = [
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:quarantine_flags/p:item/text()", namespaces=NS
        )
        if item and item.strip()
    ]
    unresolved = [
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:unresolved_failures/p:item/text()", namespaces=NS
        )
        if item and item.strip()
    ]
    if "none" in quarantine_flags and len(quarantine_flags) > 1:
        issues.append(
            ValidationIssue(
                code="E848_PREFLIGHT_QUARANTINE_FLAGS_INVALID",
                message="quarantine_flags cannot mix 'none' with other flags.",
            )
        )
    if "none" in unresolved and len(unresolved) > 1:
        issues.append(
            ValidationIssue(
                code="E849_PREFLIGHT_UNRESOLVED_FAILURES_INVALID",
                message="unresolved_failures cannot mix 'none' with other values.",
            )
        )

    readiness = xpath_text(doc.tree, "/p:pxml/p:payload/p:render_readiness")
    lineage_ok = xpath_text(doc.tree, "/p:pxml/p:payload/p:lineage_ok")
    status_ok = xpath_text(doc.tree, "/p:pxml/p:payload/p:status_ok")

    if readiness == "ready":
        if lineage_ok != "true" or status_ok != "true":
            issues.append(
                ValidationIssue(
                    code="E850_PREFLIGHT_READY_STATE_INVALID",
                    message="render_readiness=ready requires lineage_ok=true and status_ok=true.",
                )
            )
        if unresolved != ["none"]:
            issues.append(
                ValidationIssue(
                    code="E851_PREFLIGHT_READY_UNRESOLVED_INVALID",
                    message="render_readiness=ready requires unresolved_failures=['none'].",
                )
            )
        if quarantine_flags != ["none"]:
            issues.append(
                ValidationIssue(
                    code="E852_PREFLIGHT_READY_QUARANTINE_INVALID",
                    message="render_readiness=ready requires quarantine_flags=['none'].",
                )
            )

    status_ref_doc_id = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:latest_status_report_ref/p:doc_id"
    )
    if status_ref_doc_id:
        status_doc = context_index.get(status_ref_doc_id)
        if status_doc is not None and status_doc.tree is not None:
            status_value = xpath_text(
                status_doc.tree, "/p:pxml/p:payload/p:current_status"
            )
            if readiness == "ready" and status_value not in {"passed", "no_op"}:
                issues.append(
                    ValidationIssue(
                        code="E853_PREFLIGHT_READY_STATUS_INVALID",
                        message="render_readiness=ready requires task_status_report current_status passed/no_op.",
                    )
                )

    return issues


def _semantic_operator_workflow_guide(doc: ParsedDoc) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    phase_names = doc.tree.xpath(
        "/p:pxml/p:payload/p:phases/p:phase/p:phase_name/text()",
        namespaces=NS,
    )
    normalized = [item.strip() for item in phase_names if item and item.strip()]
    required = {
        "clean_start",
        "normal_execution",
        "blocked_retry_failed_response",
        "stale_suspicion_handling",
        "compaction_checkpoint",
        "renderer_preflight",
        "release_profile_governance_change_control",
        "coverage_outcome_classification_review",
        "ci_release_ops_gate_execution",
        "verify_phase_audit_execution",
        "ci_quick_smoke_regression",
        "ci_full_regression",
        "ci_release_critical_regression",
        "reason_code_catalog_maintenance",
        "latest_pointer_guard_response",
    }
    missing = sorted(required - set(normalized))
    if missing:
        issues.append(
            ValidationIssue(
                code="E854_WORKFLOW_PHASE_REQUIRED",
                message=(
                    "operator_workflow_guide is missing required phase_name entries: "
                    + ", ".join(missing)
                ),
            )
        )

    phase_ids = doc.tree.xpath(
        "/p:pxml/p:payload/p:phases/p:phase/p:phase_id/text()",
        namespaces=NS,
    )
    id_counts: Dict[str, int] = {}
    for item in phase_ids:
        normalized_id = item.strip()
        if not normalized_id:
            continue
        id_counts[normalized_id] = id_counts.get(normalized_id, 0) + 1
    duplicates = sorted([item for item, count in id_counts.items() if count > 1])
    if duplicates:
        issues.append(
            ValidationIssue(
                code="E855_WORKFLOW_PHASE_ID_DUPLICATE",
                message=(
                    "operator_workflow_guide contains duplicate phase_id values: "
                    + ", ".join(duplicates)
                ),
            )
        )

    operator_entrypoints = {
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:release_handoff_appendix/p:operator_entrypoints/p:item/text()",
            namespaces=NS,
        )
        if item and item.strip()
    }
    release_ops_required_entrypoints = {"session_report_refresh", "release_ops_gate"}
    missing_ops_entrypoints = sorted(
        release_ops_required_entrypoints - operator_entrypoints
    )
    if missing_ops_entrypoints:
        issues.append(
            ValidationIssue(
                code="E856_WORKFLOW_RELEASE_OPS_ENTRYPOINT_REQUIRED",
                message=(
                    "operator_workflow_guide release_handoff_appendix/operator_entrypoints "
                    "is missing release-ops entrypoints: "
                    + ", ".join(missing_ops_entrypoints)
                ),
            )
        )

    appendix_nodes = doc.tree.xpath(
        "/p:pxml/p:payload/p:release_ops_appendix",
        namespaces=NS,
    )
    if appendix_nodes:
        profile_policy_items = doc.tree.xpath(
            "/p:pxml/p:payload/p:release_ops_appendix/p:profile_and_policy_sources/p:item/text()",
            namespaces=NS,
        )
        profile_policy_text = " ".join(
            [item.strip() for item in profile_policy_items if item and item.strip()]
        )
        if "release_gate_profile.pxml" not in profile_policy_text:
            issues.append(
                ValidationIssue(
                    code="E857_WORKFLOW_RELEASE_GATE_PROFILE_SOURCE_REQUIRED",
                    message=(
                        "release_ops_appendix profile_and_policy_sources must mention "
                        "release_gate_profile.pxml."
                    ),
                )
            )
        if "coverage_outcome_policy.pxml" not in profile_policy_text:
            issues.append(
                ValidationIssue(
                    code="E858_WORKFLOW_COVERAGE_POLICY_SOURCE_REQUIRED",
                    message=(
                        "release_ops_appendix profile_and_policy_sources must mention "
                        "coverage_outcome_policy.pxml."
                    ),
                )
            )
        if "release_profile_governance_policy.pxml" not in profile_policy_text:
            issues.append(
                ValidationIssue(
                    code="E861_WORKFLOW_PROFILE_GOV_POLICY_SOURCE_REQUIRED",
                    message=(
                        "release_ops_appendix profile_and_policy_sources must mention "
                        "release_profile_governance_policy.pxml."
                    ),
                )
            )
        if "ci_exit_code_policy.pxml" not in profile_policy_text:
            issues.append(
                ValidationIssue(
                    code="E862_WORKFLOW_CI_POLICY_SOURCE_REQUIRED",
                    message=(
                        "release_ops_appendix profile_and_policy_sources must mention "
                        "ci_exit_code_policy.pxml."
                    ),
                )
            )
        if "verify_phase_audit_policy.pxml" not in profile_policy_text:
            issues.append(
                ValidationIssue(
                    code="E863_WORKFLOW_VERIFY_AUDIT_POLICY_SOURCE_REQUIRED",
                    message=(
                        "release_ops_appendix profile_and_policy_sources must mention "
                        "verify_phase_audit_policy.pxml."
                    ),
                )
            )
        if "ci_test_profile.pxml" not in profile_policy_text:
            issues.append(
                ValidationIssue(
                    code="E1151_WORKFLOW_CI_TEST_PROFILE_SOURCE_REQUIRED",
                    message=(
                        "release_ops_appendix profile_and_policy_sources must mention "
                        "ci_test_profile.pxml."
                    ),
                )
            )
        if "reason_code_catalog.pxml" not in profile_policy_text:
            issues.append(
                ValidationIssue(
                    code="E1152_WORKFLOW_REASON_CODE_CATALOG_SOURCE_REQUIRED",
                    message=(
                        "release_ops_appendix profile_and_policy_sources must mention "
                        "reason_code_catalog.pxml."
                    ),
                )
            )

        refresh_items = doc.tree.xpath(
            "/p:pxml/p:payload/p:release_ops_appendix/p:session_refresh_procedure/p:item/text()",
            namespaces=NS,
        )
        refresh_text = " ".join(
            [item.strip() for item in refresh_items if item and item.strip()]
        )
        if "session_report_refresh.py" not in refresh_text:
            issues.append(
                ValidationIssue(
                    code="E859_WORKFLOW_SESSION_REFRESH_COMMAND_REQUIRED",
                    message=(
                        "release_ops_appendix session_refresh_procedure must mention "
                        "session_report_refresh.py."
                    ),
                )
            )

        gate_items = doc.tree.xpath(
            "/p:pxml/p:payload/p:release_ops_appendix/p:ci_gate_wrapper/p:item/text()",
            namespaces=NS,
        )
        gate_text = " ".join(
            [item.strip() for item in gate_items if item and item.strip()]
        )
        if "release_ops_gate.py" not in gate_text:
            issues.append(
                ValidationIssue(
                    code="E860_WORKFLOW_RELEASE_OPS_GATE_COMMAND_REQUIRED",
                    message=(
                        "release_ops_appendix ci_gate_wrapper must mention release_ops_gate.py."
                    ),
                )
            )
        if "--ci-policy" not in gate_text:
            issues.append(
                ValidationIssue(
                    code="E864_WORKFLOW_RELEASE_OPS_CI_POLICY_FLAG_REQUIRED",
                    message=(
                        "release_ops_appendix ci_gate_wrapper should mention --ci-policy usage."
                    ),
                )
            )

        verify_items = doc.tree.xpath(
            "/p:pxml/p:payload/p:release_ops_appendix/p:verification_commands/p:item/text()",
            namespaces=NS,
        )
        verify_text = " ".join(
            [item.strip() for item in verify_items if item and item.strip()]
        )
        if "verify_phase_audit.py" not in verify_text:
            issues.append(
                ValidationIssue(
                    code="E865_WORKFLOW_VERIFY_AUDIT_COMMAND_REQUIRED",
                    message=(
                        "release_ops_appendix verification_commands must mention "
                        "verify_phase_audit.py."
                    ),
                )
            )
        if "ci_release_check.py" not in verify_text:
            issues.append(
                ValidationIssue(
                    code="E1153_WORKFLOW_CI_RELEASE_CHECK_COMMAND_REQUIRED",
                    message=(
                        "release_ops_appendix verification_commands must mention "
                        "ci_release_check.py."
                    ),
                )
            )
        if "--mode quick" not in verify_text:
            issues.append(
                ValidationIssue(
                    code="E1154_WORKFLOW_CI_RELEASE_CHECK_QUICK_REQUIRED",
                    message=(
                        "release_ops_appendix verification_commands should mention "
                        "ci_release_check --mode quick."
                    ),
                )
            )
        if "--mode full" not in verify_text:
            issues.append(
                ValidationIssue(
                    code="E1155_WORKFLOW_CI_RELEASE_CHECK_FULL_REQUIRED",
                    message=(
                        "release_ops_appendix verification_commands should mention "
                        "ci_release_check --mode full."
                    ),
                )
            )
        if "--mode release-critical" not in verify_text:
            issues.append(
                ValidationIssue(
                    code="E1156_WORKFLOW_CI_RELEASE_CHECK_CRITICAL_REQUIRED",
                    message=(
                        "release_ops_appendix verification_commands should mention "
                        "ci_release_check --mode release-critical."
                    ),
                )
            )
        if "test_latest_pointer_guard.py" not in verify_text:
            issues.append(
                ValidationIssue(
                    code="E1157_WORKFLOW_LATEST_POINTER_GUARD_TEST_REQUIRED",
                    message=(
                        "release_ops_appendix verification_commands should mention "
                        "test_latest_pointer_guard.py."
                    ),
                )
            )
        if "test_reason_code_catalog_consistency.py" not in verify_text:
            issues.append(
                ValidationIssue(
                    code="E1158_WORKFLOW_REASON_CATALOG_TEST_REQUIRED",
                    message=(
                        "release_ops_appendix verification_commands should mention "
                        "test_reason_code_catalog_consistency.py."
                    ),
                )
            )

    return issues


def _semantic_release_gate_profile(doc: ParsedDoc) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    coverage_task_ids = [
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:coverage_task_ids/p:item/text()",
            namespaces=NS,
        )
        if item and item.strip()
    ]
    candidate_task_ids = [
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:candidate_gate_task_ids/p:item/text()",
            namespaces=NS,
        )
        if item and item.strip()
    ]
    if not coverage_task_ids:
        issues.append(
            ValidationIssue(
                code="E1036_RELEASE_GATE_PROFILE_COVERAGE_REQUIRED",
                message="release_gate_profile must include at least one coverage_task_ids item.",
            )
        )
    if not candidate_task_ids:
        issues.append(
            ValidationIssue(
                code="E1037_RELEASE_GATE_PROFILE_CANDIDATE_REQUIRED",
                message=(
                    "release_gate_profile must include at least one "
                    "candidate_gate_task_ids item."
                ),
            )
        )

    coverage_set = set(coverage_task_ids)
    missing_candidates = sorted(set(candidate_task_ids) - coverage_set)
    if missing_candidates:
        issues.append(
            ValidationIssue(
                code="E1038_RELEASE_GATE_PROFILE_CANDIDATE_SUBSET_REQUIRED",
                message=(
                    "release_gate_profile candidate_gate_task_ids must be subset of "
                    "coverage_task_ids: " + ", ".join(missing_candidates)
                ),
            )
        )

    lane_values = {
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:required_lane_coverage/p:item/text()",
            namespaces=NS,
        )
        if item and item.strip()
    }
    if "direct" not in lane_values:
        issues.append(
            ValidationIssue(
                code="E1039_RELEASE_GATE_PROFILE_DIRECT_LANE_REQUIRED",
                message="release_gate_profile required_lane_coverage must include direct.",
            )
        )

    ready_cases_text = xpath_text(
        doc.tree,
        "/p:pxml/p:payload/p:required_ready_cases",
    )
    try:
        ready_cases = int(ready_cases_text) if ready_cases_text else 0
    except ValueError:
        ready_cases = 0
    if ready_cases < 1:
        issues.append(
            ValidationIssue(
                code="E1040_RELEASE_GATE_PROFILE_READY_CASES_REQUIRED",
                message="release_gate_profile required_ready_cases must be >= 1.",
            )
        )

    profile_version = xpath_text(doc.tree, "/p:pxml/p:payload/p:profile_version")
    profile_owner = xpath_text(doc.tree, "/p:pxml/p:payload/p:profile_owner")
    change_reason = xpath_text(doc.tree, "/p:pxml/p:payload/p:last_change_reason")
    approval_ref = xpath_text(doc.tree, "/p:pxml/p:payload/p:approval_ref")
    if profile_version is None:
        issues.append(
            ValidationIssue(
                code="E1047_RELEASE_GATE_PROFILE_VERSION_REQUIRED",
                message="release_gate_profile payload profile_version is required.",
            )
        )
    if profile_owner is None:
        issues.append(
            ValidationIssue(
                code="E1048_RELEASE_GATE_PROFILE_OWNER_REQUIRED",
                message="release_gate_profile payload profile_owner is required.",
            )
        )
    if change_reason is None:
        issues.append(
            ValidationIssue(
                code="E1049_RELEASE_GATE_PROFILE_CHANGE_REASON_REQUIRED",
                message="release_gate_profile payload last_change_reason is required.",
            )
        )
    if approval_ref is None:
        issues.append(
            ValidationIssue(
                code="E1050_RELEASE_GATE_PROFILE_APPROVAL_REF_REQUIRED",
                message="release_gate_profile payload approval_ref is required.",
            )
        )

    return issues


def _semantic_coverage_outcome_policy(doc: ParsedDoc) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    rule_nodes = doc.tree.xpath(
        "/p:pxml/p:payload/p:rules/p:rule",
        namespaces=NS,
    )
    if not rule_nodes:
        issues.append(
            ValidationIssue(
                code="E1041_COVERAGE_OUTCOME_POLICY_RULE_REQUIRED",
                message="coverage_outcome_policy must include at least one rule.",
            )
        )
        return issues

    seen_rule_names: Dict[str, int] = {}
    seen_pairs: Dict[Tuple[str, str], int] = {}
    strict_fail_present = False
    for node in rule_nodes:
        node_tree = etree.ElementTree(node)
        rule_name = xpath_text(node_tree, "./p:rule_name")
        source_kind = xpath_text(node_tree, "./p:source_kind")
        source_value = xpath_text(node_tree, "./p:source_value")
        classification = xpath_text(node_tree, "./p:classification")
        affects_gate = xpath_text(node_tree, "./p:affects_gate")

        if rule_name:
            seen_rule_names[rule_name] = seen_rule_names.get(rule_name, 0) + 1
        if source_kind and source_value:
            pair = (source_kind, source_value)
            seen_pairs[pair] = seen_pairs.get(pair, 0) + 1
            if (
                pair == ("strict_release_readiness", "fail")
                and classification == "blocker"
                and affects_gate == "true"
            ):
                strict_fail_present = True

        if classification == "blocker" and affects_gate != "true":
            issues.append(
                ValidationIssue(
                    code="E1042_COVERAGE_OUTCOME_POLICY_BLOCKER_GATE_REQUIRED",
                    message="coverage_outcome_policy blocker rules must set affects_gate=true.",
                )
            )
        if classification == "excluded" and affects_gate != "false":
            issues.append(
                ValidationIssue(
                    code="E1043_COVERAGE_OUTCOME_POLICY_EXCLUDED_GATE_FORBIDDEN",
                    message="coverage_outcome_policy excluded rules must set affects_gate=false.",
                )
            )

    duplicate_names = sorted(
        [name for name, count in seen_rule_names.items() if count > 1]
    )
    if duplicate_names:
        issues.append(
            ValidationIssue(
                code="E1044_COVERAGE_OUTCOME_POLICY_RULE_DUPLICATE",
                message=(
                    "coverage_outcome_policy contains duplicate rule_name values: "
                    + ", ".join(duplicate_names)
                ),
            )
        )

    duplicate_pairs = sorted(
        [f"{kind}:{value}" for (kind, value), count in seen_pairs.items() if count > 1]
    )
    if duplicate_pairs:
        issues.append(
            ValidationIssue(
                code="E1045_COVERAGE_OUTCOME_POLICY_SOURCE_PAIR_DUPLICATE",
                message=(
                    "coverage_outcome_policy contains duplicate source_kind/source_value "
                    "pairs: " + ", ".join(duplicate_pairs)
                ),
            )
        )

    if not strict_fail_present:
        issues.append(
            ValidationIssue(
                code="E1046_COVERAGE_OUTCOME_POLICY_STRICT_FAIL_REQUIRED",
                message=(
                    "coverage_outcome_policy must define strict_release_readiness/fail "
                    "as classification=blocker with affects_gate=true."
                ),
            )
        )

    return issues


def _semantic_release_profile_governance_policy(
    doc: ParsedDoc,
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    rule_nodes = doc.tree.xpath(
        "/p:pxml/p:payload/p:rules/p:rule",
        namespaces=NS,
    )
    if not rule_nodes:
        issues.append(
            ValidationIssue(
                code="E1050_PROFILE_GOV_POLICY_RULE_REQUIRED",
                message=(
                    "release_profile_governance_policy must include at least one rule."
                ),
            )
        )
        return issues

    required_rules = {
        "profile_version_required",
        "profile_owner_required",
        "candidate_subset_change_requires_approval",
        "coverage_set_change_requires_documentation",
        "required_change_reason",
        "required_review_ref_or_ticket",
        "candidate_subset_must_remain_subset",
        "profile_hash_or_version_traceable",
        "emergency_override_allowed",
        "override_logging_required",
    }

    seen_rule_names: Dict[str, int] = {}
    for node in rule_nodes:
        node_tree = etree.ElementTree(node)
        rule_name = xpath_text(node_tree, "./p:rule_name")
        requirement = xpath_text(node_tree, "./p:requirement")
        enforcement_level = xpath_text(node_tree, "./p:enforcement_level")
        applies_to = {
            item.strip()
            for item in node_tree.xpath("./p:applies_to/p:item/text()", namespaces=NS)
            if item and item.strip()
        }

        if rule_name:
            seen_rule_names[rule_name] = seen_rule_names.get(rule_name, 0) + 1

        if rule_name == "candidate_subset_must_remain_subset":
            if requirement != "deny_unapproved_change":
                issues.append(
                    ValidationIssue(
                        code="E1088_PROFILE_GOV_SUBSET_REQUIREMENT_INVALID",
                        message=(
                            "candidate_subset_must_remain_subset must use "
                            "requirement=deny_unapproved_change."
                        ),
                    )
                )
            if enforcement_level != "hard":
                issues.append(
                    ValidationIssue(
                        code="E1089_PROFILE_GOV_SUBSET_ENFORCEMENT_INVALID",
                        message=(
                            "candidate_subset_must_remain_subset must use "
                            "enforcement_level=hard."
                        ),
                    )
                )
            if "candidate_gate_task_ids" not in applies_to:
                issues.append(
                    ValidationIssue(
                        code="E1090_PROFILE_GOV_SUBSET_APPLIES_TO_REQUIRED",
                        message=(
                            "candidate_subset_must_remain_subset must include "
                            "candidate_gate_task_ids in applies_to."
                        ),
                    )
                )

        if rule_name == "emergency_override_allowed":
            if requirement != "allow_emergency_override_with_log":
                issues.append(
                    ValidationIssue(
                        code="E1091_PROFILE_GOV_EMERGENCY_REQUIREMENT_INVALID",
                        message=(
                            "emergency_override_allowed must use "
                            "requirement=allow_emergency_override_with_log."
                        ),
                    )
                )

        if rule_name == "override_logging_required":
            if requirement != "require_documentation":
                issues.append(
                    ValidationIssue(
                        code="E1092_PROFILE_GOV_OVERRIDE_LOG_REQUIREMENT_INVALID",
                        message=(
                            "override_logging_required must use "
                            "requirement=require_documentation."
                        ),
                    )
                )
            if enforcement_level != "hard":
                issues.append(
                    ValidationIssue(
                        code="E1093_PROFILE_GOV_OVERRIDE_LOG_ENFORCEMENT_INVALID",
                        message=(
                            "override_logging_required must use enforcement_level=hard."
                        ),
                    )
                )

    missing = sorted(required_rules - set(seen_rule_names.keys()))
    if missing:
        issues.append(
            ValidationIssue(
                code="E1086_PROFILE_GOV_RULE_REQUIRED",
                message=(
                    "release_profile_governance_policy is missing required rule_name "
                    "entries: " + ", ".join(missing)
                ),
            )
        )

    duplicates = sorted([name for name, count in seen_rule_names.items() if count > 1])
    if duplicates:
        issues.append(
            ValidationIssue(
                code="E1087_PROFILE_GOV_RULE_DUPLICATE",
                message=(
                    "release_profile_governance_policy contains duplicate rule_name "
                    "values: " + ", ".join(duplicates)
                ),
            )
        )

    return issues


def _semantic_ci_exit_code_policy(doc: ParsedDoc) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    rule_nodes = doc.tree.xpath(
        "/p:pxml/p:payload/p:rules/p:rule",
        namespaces=NS,
    )
    if not rule_nodes:
        issues.append(
            ValidationIssue(
                code="E1061_CI_POLICY_RULE_REQUIRED",
                message="ci_exit_code_policy must include at least one rule.",
            )
        )
        return issues

    input_to_code: Dict[str, int] = {}
    input_counts: Dict[str, int] = {}
    for node in rule_nodes:
        node_tree = etree.ElementTree(node)
        input_condition = xpath_text(node_tree, "./p:input_condition")
        output_exit_code = xpath_text(node_tree, "./p:output_exit_code")
        if not input_condition or not output_exit_code:
            continue
        input_counts[input_condition] = input_counts.get(input_condition, 0) + 1
        try:
            code = int(output_exit_code)
        except ValueError:
            continue
        input_to_code[input_condition] = code

    duplicates = sorted([item for item, count in input_counts.items() if count > 1])
    if duplicates:
        issues.append(
            ValidationIssue(
                code="E1082_CI_POLICY_INPUT_CONDITION_DUPLICATE",
                message=(
                    "ci_exit_code_policy contains duplicate input_condition values: "
                    + ", ".join(duplicates)
                ),
            )
        )

    required_exact = {
        "rc_result=pass": 0,
        "rc_result=fail": 1,
        "error_kind=validation_usage": 3,
        "error_kind=hard_execution": 4,
    }
    for input_condition, expected_code in required_exact.items():
        actual = input_to_code.get(input_condition)
        if actual is None:
            issues.append(
                ValidationIssue(
                    code="E1083_CI_POLICY_REQUIRED_MAPPING_MISSING",
                    message=(
                        "ci_exit_code_policy is missing required mapping: "
                        f"{input_condition} -> {expected_code}."
                    ),
                )
            )
        elif actual != expected_code:
            issues.append(
                ValidationIssue(
                    code="E1084_CI_POLICY_REQUIRED_MAPPING_INVALID",
                    message=(
                        "ci_exit_code_policy mapping mismatch: "
                        f"{input_condition} should map to {expected_code}, got {actual}."
                    ),
                )
            )

    caution_code = input_to_code.get("rc_result=caution")
    if caution_code is None:
        issues.append(
            ValidationIssue(
                code="E1085_CI_POLICY_CAUTION_MAPPING_REQUIRED",
                message="ci_exit_code_policy must include rc_result=caution mapping.",
            )
        )
    elif caution_code == 0:
        issues.append(
            ValidationIssue(
                code="E1085_CI_POLICY_CAUTION_MAPPING_REQUIRED",
                message="rc_result=caution must map to a non-zero exit code.",
            )
        )

    return issues


def _semantic_verify_phase_audit_policy(doc: ParsedDoc) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    rule_nodes = doc.tree.xpath(
        "/p:pxml/p:payload/p:rules/p:rule",
        namespaces=NS,
    )
    if not rule_nodes:
        issues.append(
            ValidationIssue(
                code="E1067_VERIFY_AUDIT_POLICY_RULE_REQUIRED",
                message="verify_phase_audit_policy must include at least one rule.",
            )
        )
        return issues

    required_rules = {
        "require_lane_phase_evidence": "pass_requirement",
        "require_post_implement_phase_evidence": "pass_requirement",
        "classify_unknown_legacy_for_fresh_smoke": "fail_requirement",
        "allow_unknown_legacy_for_legacy_artifacts": "warning_allowed",
        "candidate_subset_phase_coverage_required": "pass_requirement",
        "audit_report_required": "require_documentation",
        "override_logging_required": "require_documentation",
    }

    seen_rule_names: Dict[str, int] = {}
    for node in rule_nodes:
        node_tree = etree.ElementTree(node)
        rule_name = xpath_text(node_tree, "./p:rule_name")
        decision = xpath_text(node_tree, "./p:decision")
        applies_to = {
            item.strip()
            for item in node_tree.xpath("./p:applies_to/p:item/text()", namespaces=NS)
            if item and item.strip()
        }

        if not rule_name:
            continue
        seen_rule_names[rule_name] = seen_rule_names.get(rule_name, 0) + 1

        expected_decision = required_rules.get(rule_name)
        if expected_decision is not None and decision != expected_decision:
            issues.append(
                ValidationIssue(
                    code="E1094_VERIFY_AUDIT_POLICY_DECISION_INVALID",
                    message=(
                        "verify_phase_audit_policy decision mismatch for "
                        f"{rule_name}: expected {expected_decision}, got {decision}."
                    ),
                )
            )

        if (
            rule_name
            in {
                "require_lane_phase_evidence",
                "require_post_implement_phase_evidence",
                "classify_unknown_legacy_for_fresh_smoke",
                "allow_unknown_legacy_for_legacy_artifacts",
                "candidate_subset_phase_coverage_required",
                "override_logging_required",
            }
            and "verify_phase_audit_report" not in applies_to
        ):
            issues.append(
                ValidationIssue(
                    code="E1095_VERIFY_AUDIT_POLICY_APPLIES_TO_REQUIRED",
                    message=(
                        f"{rule_name} should include verify_phase_audit_report in applies_to."
                    ),
                )
            )

    missing = sorted(required_rules.keys() - set(seen_rule_names.keys()))
    if missing:
        issues.append(
            ValidationIssue(
                code="E1074_VERIFY_AUDIT_POLICY_RULE_REQUIRED",
                message=(
                    "verify_phase_audit_policy is missing required rule_name entries: "
                    + ", ".join(missing)
                ),
            )
        )

    duplicates = sorted([name for name, count in seen_rule_names.items() if count > 1])
    if duplicates:
        issues.append(
            ValidationIssue(
                code="E1096_VERIFY_AUDIT_POLICY_RULE_DUPLICATE",
                message=(
                    "verify_phase_audit_policy contains duplicate rule_name values: "
                    + ", ".join(duplicates)
                ),
            )
        )

    trace_refs = doc.tree.xpath(
        "/p:pxml/p:refs/p:ref[p:doc_class='trace_event_semantics']",
        namespaces=NS,
    )
    if len(trace_refs) == 0:
        issues.append(
            ValidationIssue(
                code="E1097_VERIFY_AUDIT_POLICY_TRACE_REF_REQUIRED",
                message=(
                    "verify_phase_audit_policy should reference trace_event_semantics "
                    "for phase semantics lineage."
                ),
            )
        )

    return issues


def _semantic_verify_phase_audit_report(
    doc: ParsedDoc,
    refs: List[Tuple[str, Optional[str], Optional[str]]],
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    policy_ref_class = xpath_text(
        doc.tree,
        "/p:pxml/p:payload/p:policy_ref/p:doc_class",
    )
    if policy_ref_class != "verify_phase_audit_policy":
        issues.append(
            ValidationIssue(
                code="E1075_VERIFY_AUDIT_REPORT_POLICY_REF_CLASS_REQUIRED",
                message=(
                    "verify_phase_audit_report policy_ref doc_class must be "
                    "verify_phase_audit_policy."
                ),
            )
        )

    top_ref_ids = {doc_id for doc_id, _doc_class, _relation in refs}
    policy_ref_id = xpath_text(doc.tree, "/p:pxml/p:payload/p:policy_ref/p:doc_id")
    if policy_ref_id and top_ref_ids and policy_ref_id not in top_ref_ids:
        issues.append(
            ValidationIssue(
                code="E1098_VERIFY_AUDIT_REPORT_POLICY_REF_MISSING",
                message=(
                    "verify_phase_audit_report policy_ref doc_id should be present "
                    "in top-level refs."
                ),
            )
        )

    warnings = [
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:warnings/p:item/text()",
            namespaces=NS,
        )
        if item and item.strip()
    ]
    blockers = [
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:blockers/p:item/text()",
            namespaces=NS,
        )
        if item and item.strip()
    ]
    missing_requirements = [
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:missing_phase_requirements/p:item/text()",
            namespaces=NS,
        )
        if item and item.strip()
    ]

    if "none" in warnings and len(warnings) > 1:
        issues.append(
            ValidationIssue(
                code="E1076_VERIFY_AUDIT_REPORT_WARNINGS_INVALID",
                message="verify_phase_audit_report warnings cannot mix 'none' with additional values.",
            )
        )
    if "none" in blockers and len(blockers) > 1:
        issues.append(
            ValidationIssue(
                code="E1077_VERIFY_AUDIT_REPORT_BLOCKERS_INVALID",
                message="verify_phase_audit_report blockers cannot mix 'none' with additional values.",
            )
        )
    if "none" in missing_requirements and len(missing_requirements) > 1:
        issues.append(
            ValidationIssue(
                code="E1099_VERIFY_AUDIT_REPORT_MISSING_REQ_INVALID",
                message=(
                    "verify_phase_audit_report missing_phase_requirements cannot mix "
                    "'none' with additional values."
                ),
            )
        )

    lane_refs = doc.tree.xpath(
        "/p:pxml/p:payload/p:lane_phase_evidence_refs/p:ref",
        namespaces=NS,
    )
    post_refs = doc.tree.xpath(
        "/p:pxml/p:payload/p:post_implement_phase_evidence_refs/p:ref",
        namespaces=NS,
    )
    result = xpath_text(doc.tree, "/p:pxml/p:payload/p:result")

    if result == "pass":
        if blockers != ["none"]:
            issues.append(
                ValidationIssue(
                    code="E1078_VERIFY_AUDIT_REPORT_PASS_BLOCKERS_INVALID",
                    message="verify_phase_audit_report result=pass requires blockers=['none'].",
                )
            )
        if len(lane_refs) < 1:
            issues.append(
                ValidationIssue(
                    code="E1080_VERIFY_AUDIT_REPORT_PASS_LANE_REQUIRED",
                    message="verify_phase_audit_report result=pass requires lane evidence refs.",
                )
            )
        if len(post_refs) < 1:
            issues.append(
                ValidationIssue(
                    code="E1081_VERIFY_AUDIT_REPORT_PASS_POST_REQUIRED",
                    message=(
                        "verify_phase_audit_report result=pass requires post_implement "
                        "evidence refs."
                    ),
                )
            )
    if result == "fail" and (not blockers or blockers == ["none"]):
        issues.append(
            ValidationIssue(
                code="E1079_VERIFY_AUDIT_REPORT_FAIL_BLOCKERS_REQUIRED",
                message=(
                    "verify_phase_audit_report result=fail requires non-none blocker "
                    "entries."
                ),
            )
        )

    audited_task_ids = doc.tree.xpath(
        "/p:pxml/p:payload/p:audited_task_ids/p:item/text()",
        namespaces=NS,
    )
    normalized_task_ids = [
        item.strip() for item in audited_task_ids if item and item.strip()
    ]
    if not normalized_task_ids:
        issues.append(
            ValidationIssue(
                code="E1100_VERIFY_AUDIT_REPORT_TASKS_REQUIRED",
                message="verify_phase_audit_report must include at least one audited_task_ids item.",
            )
        )

    return issues


def _semantic_ci_test_profile(doc: ParsedDoc) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    quick_targets = [
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:quick_smoke_targets/p:item/text()",
            namespaces=NS,
        )
        if item and item.strip()
    ]
    full_targets = [
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:full_regression_targets/p:item/text()",
            namespaces=NS,
        )
        if item and item.strip()
    ]
    release_targets = [
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:release_critical_targets/p:item/text()",
            namespaces=NS,
        )
        if item and item.strip()
    ]
    default_args = [
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:default_pytest_args/p:item/text()",
            namespaces=NS,
        )
        if item and item.strip()
    ]

    if not quick_targets:
        issues.append(
            ValidationIssue(
                code="E1101_CI_TEST_PROFILE_QUICK_REQUIRED",
                message="ci_test_profile must include at least one quick_smoke_targets item.",
            )
        )
    if not full_targets:
        issues.append(
            ValidationIssue(
                code="E1102_CI_TEST_PROFILE_FULL_REQUIRED",
                message="ci_test_profile must include at least one full_regression_targets item.",
            )
        )
    if not release_targets:
        issues.append(
            ValidationIssue(
                code="E1103_CI_TEST_PROFILE_RELEASE_REQUIRED",
                message=(
                    "ci_test_profile must include at least one "
                    "release_critical_targets item."
                ),
            )
        )

    for label, values, code in [
        (
            "quick_smoke_targets",
            quick_targets,
            "E1104_CI_TEST_PROFILE_QUICK_DUPLICATE",
        ),
        (
            "full_regression_targets",
            full_targets,
            "E1105_CI_TEST_PROFILE_FULL_DUPLICATE",
        ),
        (
            "release_critical_targets",
            release_targets,
            "E1106_CI_TEST_PROFILE_RELEASE_DUPLICATE",
        ),
    ]:
        duplicates = sorted([item for item in set(values) if values.count(item) > 1])
        if duplicates:
            issues.append(
                ValidationIssue(
                    code=code,
                    message=f"{label} contains duplicate entries: {', '.join(duplicates)}",
                )
            )

    full_set = set(full_targets)
    quick_missing = sorted(set(quick_targets) - full_set)
    if quick_missing:
        issues.append(
            ValidationIssue(
                code="E1107_CI_TEST_PROFILE_QUICK_SUBSET_REQUIRED",
                message=(
                    "quick_smoke_targets must be subset of full_regression_targets: "
                    + ", ".join(quick_missing)
                ),
            )
        )

    release_missing = sorted(set(release_targets) - full_set)
    if release_missing:
        issues.append(
            ValidationIssue(
                code="E1108_CI_TEST_PROFILE_RELEASE_SUBSET_REQUIRED",
                message=(
                    "release_critical_targets must be subset of full_regression_targets: "
                    + ", ".join(release_missing)
                ),
            )
        )

    target_pattern = re.compile(
        r"^tests/[A-Za-z0-9_./-]+\.py(?:::[A-Za-z0-9_./\-\[\]]+)?$"
    )
    invalid_targets = sorted(
        {
            target
            for target in quick_targets + full_targets + release_targets
            if not target_pattern.fullmatch(target)
        }
    )
    if invalid_targets:
        issues.append(
            ValidationIssue(
                code="E1109_CI_TEST_PROFILE_TARGET_FORMAT_INVALID",
                message=(
                    "ci_test_profile contains invalid pytest target format entries: "
                    + ", ".join(invalid_targets)
                ),
            )
        )

    default_arg_duplicates = sorted(
        [item for item in set(default_args) if default_args.count(item) > 1]
    )
    if default_arg_duplicates:
        issues.append(
            ValidationIssue(
                code="E1110_CI_TEST_PROFILE_ARG_DUPLICATE",
                message=(
                    "ci_test_profile default_pytest_args contains duplicate values: "
                    + ", ".join(default_arg_duplicates)
                ),
            )
        )

    if not default_args:
        issues.append(
            ValidationIssue(
                code="E1111_CI_TEST_PROFILE_ARGS_REQUIRED",
                message="ci_test_profile must include at least one default_pytest_args item.",
            )
        )

    required_release_targets = {
        "tests/test_release_candidate_check_failure_paths.py::test_release_candidate_check_pass_on_healthy_candidate_subset",
        "tests/test_release_candidate_check_failure_paths.py::test_release_candidate_check_missing_candidate_returns_fail_without_crash",
        "tests/test_release_candidate_check_failure_paths.py::test_release_candidate_check_broken_latest_chain_returns_fail_without_crash",
        "tests/test_latest_pointer_guard.py::test_release_candidate_check_validation_failure_does_not_promote_latest",
        "tests/test_verify_phase_audit_latest_guard.py::test_verify_phase_audit_does_not_promote_latest_on_validation_failure",
        "tests/test_release_candidate_check_failure_paths.py::test_harness_validator_strict_release_readiness_regression",
    }
    missing_release_targets = sorted(required_release_targets - set(release_targets))
    if missing_release_targets:
        issues.append(
            ValidationIssue(
                code="E1112_CI_TEST_PROFILE_RELEASE_TARGET_REQUIRED",
                message=(
                    "ci_test_profile release_critical_targets is missing required entries: "
                    + ", ".join(missing_release_targets)
                ),
            )
        )

    return issues


def _semantic_reason_code_catalog(doc: ParsedDoc) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    reason_nodes = doc.tree.xpath(
        "/p:pxml/p:payload/p:reasons/p:reason",
        namespaces=NS,
    )
    if not reason_nodes:
        issues.append(
            ValidationIssue(
                code="E1113_REASON_CODE_CATALOG_ENTRY_REQUIRED",
                message="reason_code_catalog must include at least one reason entry.",
            )
        )
        return issues

    required_codes = {
        "rc_candidate_task_missing",
        "rc_candidate_latest_missing",
        "rc_candidate_required_artifact_missing",
        "rc_candidate_ref_broken",
        "rc_coverage_task_missing",
        "rc_coverage_ref_broken",
        "implementer_modify_target_missing",
    }

    code_to_category: Dict[str, str] = {}
    category_counts: Dict[str, int] = {
        "rc": 0,
        "implementer": 0,
        "verifier": 0,
        "coordinator": 0,
        "planner": 0,
        "reviewer": 0,
        "system": 0,
    }

    legacy_coordinator_codes = {
        "acceptance_lineage_mismatch",
        "verification_runner_error",
        "verification_lineage_mismatch",
    }

    for node in reason_nodes:
        node_tree = etree.ElementTree(node)
        code = xpath_text(node_tree, "./p:code")
        category = xpath_text(node_tree, "./p:category")
        default_classification = xpath_text(node_tree, "./p:default_classification")
        affects_gate_default = xpath_text(node_tree, "./p:affects_gate_default")
        source_layer = xpath_text(node_tree, "./p:source_layer")

        if code is None or category is None:
            continue

        if code in code_to_category:
            issues.append(
                ValidationIssue(
                    code="E1114_REASON_CODE_CATALOG_CODE_DUPLICATE",
                    message=f"reason_code_catalog duplicate code entry: {code}",
                )
            )
        else:
            code_to_category[code] = category

        if category in category_counts:
            category_counts[category] += 1

        if category == "rc" and not code.startswith("rc_"):
            issues.append(
                ValidationIssue(
                    code="E1115_REASON_CODE_CATALOG_PREFIX_MISMATCH",
                    message=f"rc category code must start with rc_: {code}",
                )
            )
        if category == "implementer" and not code.startswith("implementer_"):
            issues.append(
                ValidationIssue(
                    code="E1115_REASON_CODE_CATALOG_PREFIX_MISMATCH",
                    message=f"implementer category code must start with implementer_: {code}",
                )
            )
        if category == "verifier" and not code.startswith("verifier_"):
            issues.append(
                ValidationIssue(
                    code="E1115_REASON_CODE_CATALOG_PREFIX_MISMATCH",
                    message=f"verifier category code must start with verifier_: {code}",
                )
            )
        if category == "planner" and not code.startswith("planner_"):
            issues.append(
                ValidationIssue(
                    code="E1115_REASON_CODE_CATALOG_PREFIX_MISMATCH",
                    message=f"planner category code must start with planner_: {code}",
                )
            )
        if category == "reviewer" and not (
            code.startswith("reviewer_") or code.startswith("review_")
        ):
            issues.append(
                ValidationIssue(
                    code="E1115_REASON_CODE_CATALOG_PREFIX_MISMATCH",
                    message=(
                        "reviewer category code must start with reviewer_ or review_: "
                        f"{code}"
                    ),
                )
            )
        if category == "system" and not code.startswith("system_"):
            issues.append(
                ValidationIssue(
                    code="E1115_REASON_CODE_CATALOG_PREFIX_MISMATCH",
                    message=f"system category code must start with system_: {code}",
                )
            )
        if category == "coordinator" and not (
            code.startswith("coordinator_") or code in legacy_coordinator_codes
        ):
            issues.append(
                ValidationIssue(
                    code="E1115_REASON_CODE_CATALOG_PREFIX_MISMATCH",
                    message=(
                        "coordinator category code must start with coordinator_ or be "
                        f"legacy coordinator code: {code}"
                    ),
                )
            )

        if default_classification == "blocker" and affects_gate_default != "true":
            issues.append(
                ValidationIssue(
                    code="E1116_REASON_CODE_CATALOG_BLOCKER_GATE_REQUIRED",
                    message=(
                        "reason_code_catalog blocker default_classification must set "
                        f"affects_gate_default=true: {code}"
                    ),
                )
            )
        if default_classification == "excluded" and affects_gate_default != "false":
            issues.append(
                ValidationIssue(
                    code="E1117_REASON_CODE_CATALOG_EXCLUDED_GATE_FORBIDDEN",
                    message=(
                        "reason_code_catalog excluded default_classification must set "
                        f"affects_gate_default=false: {code}"
                    ),
                )
            )

        if source_layer is None:
            issues.append(
                ValidationIssue(
                    code="E1118_REASON_CODE_CATALOG_SOURCE_LAYER_REQUIRED",
                    message=f"reason_code_catalog source_layer is required for code: {code}",
                )
            )

    missing_codes = sorted(required_codes - set(code_to_category.keys()))
    if missing_codes:
        issues.append(
            ValidationIssue(
                code="E1119_REASON_CODE_CATALOG_REQUIRED_CODE_MISSING",
                message=(
                    "reason_code_catalog is missing required reason codes: "
                    + ", ".join(missing_codes)
                ),
            )
        )

    missing_categories = sorted(
        [category for category, count in category_counts.items() if count < 1]
    )
    if missing_categories:
        issues.append(
            ValidationIssue(
                code="E1120_REASON_CODE_CATALOG_CATEGORY_REQUIRED",
                message=(
                    "reason_code_catalog is missing required categories: "
                    + ", ".join(missing_categories)
                ),
            )
        )

    return issues


def _semantic_operator_runbook_policy(doc: ParsedDoc) -> List[ValidationIssue]:
    assert doc.tree is not None
    tree = doc.tree
    issues: List[ValidationIssue] = []

    required = {
        "cleanup_before_run_default",
        "preflight_required_before_render",
        "allow_caution_render_with_override",
        "deny_not_ready_render_without_override",
        "run_harness_after_render_default",
        "session_report_required",
        "operator_override_logging_required",
    }

    names = tree.xpath(
        "/p:pxml/p:payload/p:rules/p:rule/p:rule_name/text()",
        namespaces=NS,
    )
    normalized = [item.strip() for item in names if item and item.strip()]
    missing = sorted(required - set(normalized))
    if missing:
        issues.append(
            ValidationIssue(
                code="E900_RUNBOOK_POLICY_RULE_REQUIRED",
                message=(
                    "operator_runbook_policy is missing required rule_name entries: "
                    + ", ".join(missing)
                ),
            )
        )

    duplicates = sorted(
        [name for name in set(normalized) if normalized.count(name) > 1]
    )
    if duplicates:
        issues.append(
            ValidationIssue(
                code="E901_RUNBOOK_POLICY_RULE_DUPLICATE",
                message=(
                    "operator_runbook_policy contains duplicate rule_name values: "
                    + ", ".join(duplicates)
                ),
            )
        )

    def _decision_for(rule_name: str) -> Optional[str]:
        values = tree.xpath(
            "/p:pxml/p:payload/p:rules/p:rule[p:rule_name=$rule_name]/p:decision/text()",
            namespaces=NS,
            rule_name=rule_name,
        )
        if not values:
            return None
        value = str(values[0]).strip()
        return value or None

    preflight_decision = _decision_for("preflight_required_before_render")
    if preflight_decision and preflight_decision != "deny":
        issues.append(
            ValidationIssue(
                code="E902_RUNBOOK_POLICY_PREFLIGHT_DECISION_INVALID",
                message="preflight_required_before_render decision should be deny.",
            )
        )

    caution_decision = _decision_for("allow_caution_render_with_override")
    if caution_decision and caution_decision != "allow_with_override":
        issues.append(
            ValidationIssue(
                code="E903_RUNBOOK_POLICY_CAUTION_DECISION_INVALID",
                message="allow_caution_render_with_override decision should be allow_with_override.",
            )
        )

    not_ready_decision = _decision_for("deny_not_ready_render_without_override")
    if not_ready_decision and not_ready_decision != "deny":
        issues.append(
            ValidationIssue(
                code="E904_RUNBOOK_POLICY_NOT_READY_DECISION_INVALID",
                message="deny_not_ready_render_without_override decision should be deny.",
            )
        )

    session_decision = _decision_for("session_report_required")
    if session_decision and session_decision not in {"require_logging", "deny"}:
        issues.append(
            ValidationIssue(
                code="E905_RUNBOOK_POLICY_SESSION_DECISION_INVALID",
                message="session_report_required decision should be require_logging or deny.",
            )
        )

    override_decision = _decision_for("operator_override_logging_required")
    if override_decision and override_decision != "require_logging":
        issues.append(
            ValidationIssue(
                code="E906_RUNBOOK_POLICY_OVERRIDE_LOGGING_DECISION_INVALID",
                message="operator_override_logging_required decision should be require_logging.",
            )
        )

    return issues


def _semantic_session_report(
    doc: ParsedDoc,
    refs: List[Tuple[str, Optional[str], Optional[str]]],
    context_index: Dict[str, ParsedDoc],
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    payload_task_id = xpath_text(doc.tree, "/p:pxml/p:payload/p:task_id")
    meta_task_id = xpath_text(doc.tree, "/p:pxml/p:meta/p:task_id")
    if payload_task_id != meta_task_id:
        issues.append(
            ValidationIssue(
                code="E907_SESSION_REPORT_TASK_ID_MISMATCH",
                message="session_report payload task_id must match meta task_id.",
            )
        )

    derived = xpath_text(doc.tree, "/p:pxml/p:payload/p:derived")
    if derived != "true":
        issues.append(
            ValidationIssue(
                code="E908_SESSION_REPORT_DERIVED_REQUIRED",
                message="session_report payload derived must be true.",
            )
        )

    top_ref_ids = {doc_id for doc_id, _doc_class, _relation in refs}

    required_refs = {
        "source_intake_ref": "task_intake",
        "latest_route_ref": "manager_route",
        "latest_packet_ref": "execution_packet",
        "latest_status_report_ref": "task_status_report",
        "latest_preflight_ref": "operator_preflight_report",
        "latest_trace_ref": "execution_trace",
    }
    optional_refs = {
        "latest_render_report_ref": "final_render_report",
        "latest_verification_ref": "verification_result",
        "latest_compaction_checkpoint_ref": "compaction_checkpoint",
    }

    for node_name, expected_class in required_refs.items():
        ref_id = xpath_text(doc.tree, f"/p:pxml/p:payload/p:{node_name}/p:doc_id")
        ref_class = xpath_text(
            doc.tree,
            f"/p:pxml/p:payload/p:{node_name}/p:doc_class",
        )
        if ref_class != expected_class:
            issues.append(
                ValidationIssue(
                    code="E909_SESSION_REPORT_REF_CLASS_REQUIRED",
                    message=f"{node_name} doc_class must be {expected_class}.",
                )
            )
        if ref_id and ref_id not in top_ref_ids:
            issues.append(
                ValidationIssue(
                    code="E924_SESSION_REPORT_REF_MISSING",
                    message=f"{node_name} doc_id must be present in top-level refs.",
                )
            )

    for node_name, expected_class in optional_refs.items():
        ref_id = xpath_text(doc.tree, f"/p:pxml/p:payload/p:{node_name}/p:doc_id")
        ref_class = xpath_text(
            doc.tree,
            f"/p:pxml/p:payload/p:{node_name}/p:doc_class",
        )
        if ref_class and ref_class != expected_class:
            issues.append(
                ValidationIssue(
                    code="E909_SESSION_REPORT_REF_CLASS_REQUIRED",
                    message=f"{node_name} doc_class must be {expected_class} when present.",
                )
            )
        if ref_id and ref_id not in top_ref_ids:
            issues.append(
                ValidationIssue(
                    code="E924_SESSION_REPORT_REF_MISSING",
                    message=f"{node_name} doc_id must be present in top-level refs.",
                )
            )

    start_text = xpath_text(doc.tree, "/p:pxml/p:payload/p:runbook_start_time")
    end_text = xpath_text(doc.tree, "/p:pxml/p:payload/p:runbook_end_time")
    if start_text and end_text:
        try:
            start_dt = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end_text.replace("Z", "+00:00"))
            if end_dt < start_dt:
                issues.append(
                    ValidationIssue(
                        code="E925_SESSION_REPORT_TIME_ORDER_INVALID",
                        message="runbook_end_time must be greater than or equal to runbook_start_time.",
                    )
                )
        except ValueError:
            pass

    warnings = [
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:warnings/p:item/text()",
            namespaces=NS,
        )
        if item and item.strip()
    ]
    if "none" in warnings and len(warnings) > 1:
        issues.append(
            ValidationIssue(
                code="E926_SESSION_REPORT_WARNINGS_INVALID",
                message="warnings cannot mix 'none' with additional values.",
            )
        )

    quarantine_refs = [
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:quarantine_refs/p:item/text()",
            namespaces=NS,
        )
        if item and item.strip()
    ]
    if "none" in quarantine_refs and len(quarantine_refs) > 1:
        issues.append(
            ValidationIssue(
                code="E927_SESSION_REPORT_QUARANTINE_REFS_INVALID",
                message="quarantine_refs cannot mix 'none' with additional values.",
            )
        )

    render_decision = xpath_text(doc.tree, "/p:pxml/p:payload/p:render_decision")
    override_used = xpath_text(doc.tree, "/p:pxml/p:payload/p:render_override_used")
    release_readiness = xpath_text(
        doc.tree,
        "/p:pxml/p:payload/p:release_readiness_result",
    )
    runbook_result = xpath_text(doc.tree, "/p:pxml/p:payload/p:runbook_result")

    render_ref_id = xpath_text(
        doc.tree,
        "/p:pxml/p:payload/p:latest_render_report_ref/p:doc_id",
    )
    if render_decision in {"rendered", "rendered_with_warning", "denied"}:
        if render_ref_id is None:
            issues.append(
                ValidationIssue(
                    code="E928_SESSION_REPORT_RENDER_REF_REQUIRED",
                    message=(
                        "render_decision rendered/rendered_with_warning/denied "
                        "requires latest_render_report_ref."
                    ),
                )
            )
    if render_decision == "skipped" and render_ref_id is not None:
        issues.append(
            ValidationIssue(
                code="E928_SESSION_REPORT_RENDER_REF_REQUIRED",
                message="render_decision=skipped must not include latest_render_report_ref.",
            )
        )

    if override_used == "true" and render_decision != "rendered_with_warning":
        issues.append(
            ValidationIssue(
                code="E921_SESSION_REPORT_OVERRIDE_DECISION_INVALID",
                message="render_override_used=true requires render_decision=rendered_with_warning.",
            )
        )

    if release_readiness == "pass":
        if runbook_result != "success":
            issues.append(
                ValidationIssue(
                    code="E930_RELEASE_READINESS_PASS_RUNBOOK_REQUIRED",
                    message="release_readiness_result=pass requires runbook_result=success.",
                )
            )
        if render_decision not in {"rendered", "rendered_with_warning"}:
            issues.append(
                ValidationIssue(
                    code="E931_RELEASE_READINESS_PASS_RENDER_REQUIRED",
                    message=(
                        "release_readiness_result=pass requires render_decision "
                        "rendered or rendered_with_warning."
                    ),
                )
            )

    if release_readiness == "fail" and runbook_result == "success":
        issues.append(
            ValidationIssue(
                code="E932_RELEASE_READINESS_FAIL_RESULT_REQUIRED",
                message="release_readiness_result=fail cannot pair with runbook_result=success.",
            )
        )

    if render_decision == "denied" and runbook_result == "success":
        issues.append(
            ValidationIssue(
                code="E933_RELEASE_READINESS_DENIED_SUCCESS_INVALID",
                message="render_decision=denied cannot pair with runbook_result=success.",
            )
        )

    preflight_ref_id = xpath_text(
        doc.tree,
        "/p:pxml/p:payload/p:latest_preflight_ref/p:doc_id",
    )
    if preflight_ref_id:
        preflight_doc = context_index.get(preflight_ref_id)
        if preflight_doc is not None and preflight_doc.tree is not None:
            preflight_readiness = xpath_text(
                preflight_doc.tree,
                "/p:pxml/p:payload/p:render_readiness",
            )
            if preflight_readiness == "ready" and render_decision == "denied":
                issues.append(
                    ValidationIssue(
                        code="E929_SESSION_REPORT_RENDER_DECISION_INVALID",
                        message="ready preflight should not produce render_decision=denied.",
                    )
                )
            if preflight_readiness == "not_ready" and render_decision == "rendered":
                issues.append(
                    ValidationIssue(
                        code="E929_SESSION_REPORT_RENDER_DECISION_INVALID",
                        message="not_ready preflight cannot produce render_decision=rendered.",
                    )
                )

    if render_ref_id:
        render_doc = context_index.get(render_ref_id)
        if render_doc is not None and render_doc.tree is not None:
            mode = xpath_text(render_doc.tree, "/p:pxml/p:payload/p:render_mode")
            if mode and render_decision and mode != render_decision:
                issues.append(
                    ValidationIssue(
                        code="E935_SESSION_REPORT_RENDER_MODE_MISMATCH",
                        message="render_decision must match referenced final_render_report render_mode.",
                    )
                )

    return issues


def _semantic_rendering_policy(doc: ParsedDoc) -> List[ValidationIssue]:
    assert doc.tree is not None
    tree = doc.tree
    issues: List[ValidationIssue] = []

    required = {
        "render_allowed_when_ready",
        "render_allowed_when_caution",
        "render_allowed_when_not_ready",
        "include_trace_summary",
        "include_compaction_checkpoint_if_present",
        "include_failure_index_summary",
        "include_quarantine_flags",
        "include_next_action",
        "render_format_policy",
        "operator_override_allowed",
    }

    names = tree.xpath(
        "/p:pxml/p:payload/p:rules/p:rule/p:rule_name/text()",
        namespaces=NS,
    )
    normalized = [item.strip() for item in names if item and item.strip()]
    missing = sorted(required - set(normalized))
    if missing:
        issues.append(
            ValidationIssue(
                code="E876_RENDER_POLICY_RULE_REQUIRED",
                message=(
                    "rendering_policy is missing required rule_name entries: "
                    + ", ".join(missing)
                ),
            )
        )

    duplicates = sorted(
        [name for name in set(normalized) if normalized.count(name) > 1]
    )
    if duplicates:
        issues.append(
            ValidationIssue(
                code="E877_RENDER_POLICY_RULE_DUPLICATE",
                message=(
                    "rendering_policy contains duplicate rule_name values: "
                    + ", ".join(duplicates)
                ),
            )
        )

    def _decision_for(rule_name: str) -> Optional[str]:
        nodes = tree.xpath(
            "/p:pxml/p:payload/p:rules/p:rule[p:rule_name=$name]/p:decision/text()",
            namespaces=NS,
            name=rule_name,
        )
        if not nodes:
            return None
        decision = str(nodes[0]).strip()
        return decision or None

    ready_decision = _decision_for("render_allowed_when_ready")
    caution_decision = _decision_for("render_allowed_when_caution")
    not_ready_decision = _decision_for("render_allowed_when_not_ready")

    if ready_decision and ready_decision in {"deny_render", "require_override"}:
        issues.append(
            ValidationIssue(
                code="E878_RENDER_POLICY_READY_DECISION_INVALID",
                message="render_allowed_when_ready decision should allow rendering for ready preflight.",
            )
        )
    if caution_decision and caution_decision == "deny_render":
        issues.append(
            ValidationIssue(
                code="E879_RENDER_POLICY_CAUTION_DECISION_INVALID",
                message="render_allowed_when_caution should not be deny_render in baseline policy.",
            )
        )
    if not_ready_decision and not_ready_decision not in {
        "deny_render",
        "require_override",
    }:
        issues.append(
            ValidationIssue(
                code="E880_RENDER_POLICY_NOT_READY_DECISION_INVALID",
                message="render_allowed_when_not_ready decision should be deny_render or require_override.",
            )
        )

    return issues


def _semantic_final_render_report(
    doc: ParsedDoc,
    refs: List[Tuple[str, Optional[str], Optional[str]]],
    context_index: Dict[str, ParsedDoc],
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    payload_task_id = xpath_text(doc.tree, "/p:pxml/p:payload/p:task_id")
    meta_task_id = xpath_text(doc.tree, "/p:pxml/p:meta/p:task_id")
    if payload_task_id != meta_task_id:
        issues.append(
            ValidationIssue(
                code="E881_RENDER_REPORT_TASK_ID_MISMATCH",
                message="final_render_report payload task_id must match meta task_id.",
            )
        )

    derived_value = xpath_text(doc.tree, "/p:pxml/p:payload/p:derived")
    if derived_value != "true":
        issues.append(
            ValidationIssue(
                code="E882_RENDER_REPORT_DERIVED_REQUIRED",
                message="final_render_report payload derived must be true.",
            )
        )

    top_ref_ids = {doc_id for doc_id, _doc_class, _relation in refs}
    expected_ref_classes = {
        "source_preflight_ref": "operator_preflight_report",
        "source_status_report_ref": "task_status_report",
        "source_route_ref": "manager_route",
        "source_packet_ref": "execution_packet",
        "source_trace_ref": "execution_trace",
    }
    for node_name, expected_class in expected_ref_classes.items():
        ref_id = xpath_text(doc.tree, f"/p:pxml/p:payload/p:{node_name}/p:doc_id")
        ref_class = xpath_text(doc.tree, f"/p:pxml/p:payload/p:{node_name}/p:doc_class")
        if ref_class != expected_class:
            issues.append(
                ValidationIssue(
                    code="E883_RENDER_REPORT_REF_CLASS_REQUIRED",
                    message=f"{node_name} doc_class must be {expected_class}.",
                )
            )
        if ref_id and ref_id not in top_ref_ids:
            issues.append(
                ValidationIssue(
                    code="E884_RENDER_REPORT_REF_MISSING",
                    message=f"{node_name} doc_id must be present in top-level refs.",
                )
            )

    verification_class = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:source_verification_ref/p:doc_class"
    )
    if verification_class and verification_class != "verification_result":
        issues.append(
            ValidationIssue(
                code="E885_RENDER_REPORT_VERIFICATION_REF_CLASS_REQUIRED",
                message="source_verification_ref doc_class must be verification_result.",
            )
        )

    checkpoint_class = xpath_text(
        doc.tree,
        "/p:pxml/p:payload/p:source_compaction_checkpoint_ref/p:doc_class",
    )
    if checkpoint_class and checkpoint_class != "compaction_checkpoint":
        issues.append(
            ValidationIssue(
                code="E886_RENDER_REPORT_CHECKPOINT_REF_CLASS_REQUIRED",
                message="source_compaction_checkpoint_ref doc_class must be compaction_checkpoint.",
            )
        )

    readiness_basis = xpath_text(doc.tree, "/p:pxml/p:payload/p:render_readiness_basis")
    render_mode = xpath_text(doc.tree, "/p:pxml/p:payload/p:render_mode")
    markdown_path = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:generated_exports/p:markdown_path"
    )
    pxml_path = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:generated_exports/p:pxml_path"
    )
    if pxml_path and _normalize_rel_path(pxml_path) is None:
        issues.append(
            ValidationIssue(
                code="E887_RENDER_REPORT_EXPORT_PATH_INVALID",
                message="generated_exports pxml_path must be a relative normalized path.",
            )
        )
    if markdown_path and _normalize_rel_path(markdown_path) is None:
        issues.append(
            ValidationIssue(
                code="E888_RENDER_REPORT_EXPORT_PATH_INVALID",
                message="generated_exports markdown_path must be a relative normalized path.",
            )
        )
    if render_mode == "denied" and markdown_path is not None:
        issues.append(
            ValidationIssue(
                code="E889_RENDER_REPORT_DENIED_MARKDOWN_FORBIDDEN",
                message="render_mode=denied must not include markdown_path export.",
            )
        )
    if readiness_basis == "not_ready" and render_mode == "rendered":
        issues.append(
            ValidationIssue(
                code="E890_RENDER_REPORT_NOT_READY_MODE_INVALID",
                message="render_readiness_basis=not_ready cannot use render_mode=rendered.",
            )
        )

    section_names = doc.tree.xpath(
        "/p:pxml/p:payload/p:summary_sections/p:section/p:section_name/text()",
        namespaces=NS,
    )
    normalized_sections = {
        item.strip() for item in section_names if item and item.strip()
    }
    required_sections = {
        "overview",
        "path_and_lane",
        "execution_outcome",
        "verification_outcome",
        "current_risks",
        "next_action",
    }
    missing_sections = sorted(required_sections - normalized_sections)
    if missing_sections:
        issues.append(
            ValidationIssue(
                code="E891_RENDER_REPORT_SECTION_REQUIRED",
                message=(
                    "final_render_report summary_sections missing required sections: "
                    + ", ".join(missing_sections)
                ),
            )
        )

    warnings = [
        item.strip()
        for item in doc.tree.xpath(
            "/p:pxml/p:payload/p:warnings/p:item/text()", namespaces=NS
        )
        if item and item.strip()
    ]
    if "none" in warnings and len(warnings) > 1:
        issues.append(
            ValidationIssue(
                code="E892_RENDER_REPORT_WARNINGS_INVALID",
                message="warnings cannot mix 'none' with additional warning entries.",
            )
        )

    preflight_ref_id = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:source_preflight_ref/p:doc_id"
    )
    if preflight_ref_id:
        preflight_doc = context_index.get(preflight_ref_id)
        if preflight_doc is not None and preflight_doc.tree is not None:
            preflight_readiness = xpath_text(
                preflight_doc.tree, "/p:pxml/p:payload/p:render_readiness"
            )
            if preflight_readiness and readiness_basis != preflight_readiness:
                issues.append(
                    ValidationIssue(
                        code="E893_RENDER_REPORT_READINESS_BASIS_MISMATCH",
                        message=(
                            "final_render_report render_readiness_basis must match source preflight render_readiness."
                        ),
                    )
                )

    packet_ref_id = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:source_packet_ref/p:doc_id"
    )
    acceptance_lock = xpath_text(doc.tree, "/p:pxml/p:payload/p:acceptance_lock_sha256")
    if packet_ref_id and acceptance_lock:
        packet_doc = context_index.get(packet_ref_id)
        if packet_doc is not None and packet_doc.tree is not None:
            packet_lock = xpath_text(
                packet_doc.tree, "/p:pxml/p:payload/p:acceptance_lock_hash"
            )
            if packet_lock and packet_lock != acceptance_lock:
                issues.append(
                    ValidationIssue(
                        code="E894_RENDER_REPORT_LINEAGE_LOCK_MISMATCH",
                        message="final_render_report acceptance_lock_sha256 must match source execution_packet acceptance_lock_hash.",
                    )
                )

    return issues


def _semantic_implementer_result(
    doc: ParsedDoc,
    refs: List[Tuple[str, Optional[str], Optional[str]]],
    context_index: Dict[str, ParsedDoc],
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    packet_refs = [item for item in refs if item[1] == "execution_packet"]
    if len(packet_refs) != 1:
        issues.append(
            ValidationIssue(
                code="E740_IMPL_PACKET_REF_REQUIRED",
                message="implementer_result must reference exactly one execution_packet artifact.",
            )
        )
        return issues

    payload_packet_doc_id = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:packet_ref/p:doc_id"
    )
    payload_packet_doc_class = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:packet_ref/p:doc_class"
    )
    payload_task_id = xpath_text(doc.tree, "/p:pxml/p:payload/p:task_id")
    meta_task_id = xpath_text(doc.tree, "/p:pxml/p:meta/p:task_id")
    patch_mode_used = xpath_text(doc.tree, "/p:pxml/p:payload/p:patch_mode_used")
    status = xpath_text(doc.tree, "/p:pxml/p:payload/p:result_status")
    blocked_reason = xpath_text(doc.tree, "/p:pxml/p:payload/p:blocked_reason")
    retry_count_text = xpath_text(doc.tree, "/p:pxml/p:payload/p:retry_count")
    escalation_requested = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:escalation_requested"
    )

    modified_items = doc.tree.xpath(
        "/p:pxml/p:payload/p:modified_files/p:item/text()", namespaces=NS
    )
    created_items = doc.tree.xpath(
        "/p:pxml/p:payload/p:created_files/p:item/text()", namespaces=NS
    )
    evidence_items = doc.tree.xpath(
        "/p:pxml/p:payload/p:patch_evidence_refs/p:item/text()", namespaces=NS
    )

    if payload_packet_doc_id != packet_refs[0][0]:
        issues.append(
            ValidationIssue(
                code="E741_IMPL_PACKET_REF_PAYLOAD_REQUIRED",
                message="implementer_result payload packet_ref doc_id must match refs execution_packet doc_id.",
            )
        )
    if payload_packet_doc_class != "execution_packet":
        issues.append(
            ValidationIssue(
                code="E742_IMPL_PACKET_REF_CLASS_REQUIRED",
                message="implementer_result payload packet_ref doc_class must be execution_packet.",
            )
        )
    if payload_task_id != meta_task_id:
        issues.append(
            ValidationIssue(
                code="E743_IMPL_TASK_ID_MISMATCH",
                message="implementer_result payload task_id must match meta task_id.",
            )
        )

    changed_count = len([item for item in modified_items if item.strip()]) + len(
        [item for item in created_items if item.strip()]
    )
    if status == "applied" and changed_count < 1:
        issues.append(
            ValidationIssue(
                code="E744_IMPL_APPLIED_FILES_REQUIRED",
                message="applied implementer_result must include modified_files or created_files.",
            )
        )
    if (
        status == "applied"
        and len([item for item in evidence_items if item.strip()]) < 1
    ):
        issues.append(
            ValidationIssue(
                code="E745_IMPL_APPLIED_EVIDENCE_REQUIRED",
                message="applied implementer_result must include patch evidence refs.",
            )
        )
    if status == "no_op" and changed_count != 0:
        issues.append(
            ValidationIssue(
                code="E746_IMPL_NOOP_FILES_EMPTY_REQUIRED",
                message="no_op implementer_result cannot declare modified_files or created_files.",
            )
        )
    if status in {"blocked", "retry_failed", "escalated"} and blocked_reason is None:
        issues.append(
            ValidationIssue(
                code="E747_IMPL_BLOCKED_REASON_REQUIRED",
                message="blocked/retry_failed/escalated implementer_result must include blocked_reason.",
            )
        )

    retry_count = 0
    try:
        retry_count = int(retry_count_text) if retry_count_text is not None else 0
    except ValueError:
        retry_count = -1

    if status == "retry_failed" and retry_count < 1:
        issues.append(
            ValidationIssue(
                code="E748_IMPL_RETRY_COUNT_REQUIRED",
                message="retry_failed implementer_result must include retry_count >= 1.",
            )
        )
    if status in {"retry_failed", "escalated"} and escalation_requested != "true":
        issues.append(
            ValidationIssue(
                code="E749_IMPL_ESCALATION_FLAG_REQUIRED",
                message="retry_failed/escalated implementer_result must set escalation_requested=true.",
            )
        )

    packet_doc = context_index.get(packet_refs[0][0])
    if packet_doc is None or packet_doc.tree is None:
        return issues

    packet_patch_mode = xpath_text(
        packet_doc.tree, "/p:pxml/p:payload/p:patch_constraints/p:patch_mode"
    )
    if patch_mode_used is not None and packet_patch_mode is not None:
        if patch_mode_used != packet_patch_mode:
            issues.append(
                ValidationIssue(
                    code="E752_IMPL_PATCH_MODE_MISMATCH",
                    message="implementer_result patch_mode_used must match execution_packet patch_constraints.patch_mode.",
                )
            )

    expected_nodes = packet_doc.tree.xpath(
        "/p:pxml/p:payload/p:expected_files/p:file", namespaces=NS
    )
    expected_by_mode: Dict[str, set[str]] = {"modify": set(), "create": set()}
    for node in expected_nodes:
        node_tree = etree.ElementTree(node)
        path_value = xpath_text(node_tree, "./p:path")
        mode = xpath_text(node_tree, "./p:mode")
        if path_value is None or mode is None:
            continue
        normalized = _normalize_rel_path(path_value)
        if normalized is None:
            continue
        if mode in expected_by_mode:
            expected_by_mode[mode].add(normalized)

    in_scope_items = packet_doc.tree.xpath(
        "/p:pxml/p:payload/p:in_scope/p:item/text()", namespaces=NS
    )
    out_scope_items = packet_doc.tree.xpath(
        "/p:pxml/p:payload/p:out_of_scope/p:item/text()", namespaces=NS
    )
    in_scope = [item.strip() for item in in_scope_items if item and item.strip()]
    out_scope = [item.strip() for item in out_scope_items if item and item.strip()]

    for value in modified_items:
        normalized = _normalize_rel_path(value)
        if normalized is None:
            issues.append(
                ValidationIssue(
                    code="E753_IMPL_MODIFIED_PATH_INVALID",
                    message=f"Invalid modified_files path: {value!r}",
                )
            )
            continue
        if normalized not in expected_by_mode["modify"]:
            issues.append(
                ValidationIssue(
                    code="E754_IMPL_MODIFIED_EXPECTED_MISMATCH",
                    message=f"modified_files entry not declared as expected modify target: {normalized}",
                )
            )
        if not _path_in_prefixes(normalized, in_scope):
            issues.append(
                ValidationIssue(
                    code="E755_IMPL_MODIFIED_SCOPE_VIOLATION",
                    message=f"modified_files entry is outside packet in_scope: {normalized}",
                )
            )
        if _path_in_prefixes(normalized, out_scope):
            issues.append(
                ValidationIssue(
                    code="E756_IMPL_MODIFIED_OUT_SCOPE_VIOLATION",
                    message=f"modified_files entry intersects packet out_of_scope: {normalized}",
                )
            )

    for value in created_items:
        normalized = _normalize_rel_path(value)
        if normalized is None:
            issues.append(
                ValidationIssue(
                    code="E757_IMPL_CREATED_PATH_INVALID",
                    message=f"Invalid created_files path: {value!r}",
                )
            )
            continue
        if normalized not in expected_by_mode["create"]:
            issues.append(
                ValidationIssue(
                    code="E758_IMPL_CREATED_EXPECTED_MISMATCH",
                    message=f"created_files entry not declared as expected create target: {normalized}",
                )
            )
        if not _path_in_prefixes(normalized, in_scope):
            issues.append(
                ValidationIssue(
                    code="E759_IMPL_CREATED_SCOPE_VIOLATION",
                    message=f"created_files entry is outside packet in_scope: {normalized}",
                )
            )
        if _path_in_prefixes(normalized, out_scope):
            issues.append(
                ValidationIssue(
                    code="E760_IMPL_CREATED_OUT_SCOPE_VIOLATION",
                    message=f"created_files entry intersects packet out_of_scope: {normalized}",
                )
            )

    return issues


def _semantic_execution_trace(doc: ParsedDoc) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    events = doc.tree.xpath("/p:pxml/p:payload/p:events/p:event", namespaces=NS)
    if not events:
        issues.append(
            ValidationIssue(
                code="E370_TRACE_EVENTS_EMPTY",
                message="execution_trace must contain at least one event.",
            )
        )
        return issues

    seen_event_types: List[str] = []

    previous_hash: Optional[str] = None
    for idx, event in enumerate(events, start=1):
        event_tree = etree.ElementTree(event)
        seq_text = xpath_text(event_tree, "./p:event_seq")
        if seq_text is None or seq_text != str(idx):
            issues.append(
                ValidationIssue(
                    code="E371_TRACE_SEQUENCE_GAP",
                    message="execution_trace event_seq must be contiguous and start at 1.",
                )
            )
            break

        prev_text = xpath_text(event_tree, "./p:prev_event_sha256")
        if idx == 1:
            if prev_text is not None:
                issues.append(
                    ValidationIssue(
                        code="E372_TRACE_FIRST_PREV_PRESENT",
                        message="First trace event must not define prev_event_sha256.",
                    )
                )
        else:
            if prev_text is None:
                issues.append(
                    ValidationIssue(
                        code="E373_TRACE_PREV_MISSING",
                        message="Trace event after first must include prev_event_sha256.",
                    )
                )
            elif previous_hash is not None and prev_text != previous_hash:
                issues.append(
                    ValidationIssue(
                        code="E374_TRACE_PREV_HASH_MISMATCH",
                        message="prev_event_sha256 does not match previous event hash.",
                    )
                )

        event_type = xpath_text(event_tree, "./p:event_type") or ""
        event_time = xpath_text(event_tree, "./p:event_time") or ""
        actor = xpath_text(event_tree, "./p:actor") or ""
        message = xpath_text(event_tree, "./p:message") or ""
        reason_code = xpath_text(event_tree, "./p:reason_code")
        attempt_text = xpath_text(event_tree, "./p:attempt")
        lineage_lock = xpath_text(event_tree, "./p:lineage_lock_sha256")
        verify_phase = xpath_text(event_tree, "./p:verify_phase")

        refs: List[Dict[str, str]] = []
        ref_nodes = event.xpath("./p:artifact_refs/p:ref", namespaces=NS)
        for ref_node in ref_nodes:
            ref_tree = etree.ElementTree(ref_node)
            refs.append(
                {
                    "doc_id": xpath_text(ref_tree, "./p:doc_id") or "",
                    "doc_class": xpath_text(ref_tree, "./p:doc_class") or "",
                    "relation": xpath_text(ref_tree, "./p:relation") or "",
                }
            )

        expected_hash_payload = {
            "event_seq": idx,
            "event_time": event_time,
            "event_type": event_type,
            "actor": actor,
            "message": message,
            "refs": refs,
            "prev_event_sha256": prev_text or "",
        }
        if reason_code:
            expected_hash_payload["reason_code"] = reason_code
        if attempt_text:
            expected_hash_payload["attempt"] = attempt_text
        if lineage_lock:
            expected_hash_payload["lineage_lock_sha256"] = lineage_lock
        if verify_phase:
            expected_hash_payload["verify_phase"] = verify_phase
        expected_hash = hashlib.sha256(
            json.dumps(
                expected_hash_payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()

        event_hash = xpath_text(event_tree, "./p:event_sha256")
        if event_hash is None or event_hash != expected_hash:
            issues.append(
                ValidationIssue(
                    code="E376_TRACE_EVENT_HASH_INVALID",
                    message="event_sha256 does not match deterministic event hash payload.",
                )
            )

        ref_classes = {ref["doc_class"] for ref in refs}
        if event_type == "route" and "manager_route" not in ref_classes:
            issues.append(
                ValidationIssue(
                    code="E720_TRACE_ROUTE_REF_REQUIRED",
                    message="route event must reference manager_route artifact.",
                )
            )
        if event_type == "packet_issued" and "execution_packet" not in ref_classes:
            issues.append(
                ValidationIssue(
                    code="E721_TRACE_PACKET_REF_REQUIRED",
                    message="packet_issued event must reference execution_packet artifact.",
                )
            )
        if event_type == "explore_start":
            if "execution_packet" not in ref_classes:
                issues.append(
                    ValidationIssue(
                        code="E725_TRACE_EXPLORE_START_PACKET_REF_REQUIRED",
                        message="explore_start event must reference execution_packet artifact.",
                    )
                )
        if event_type == "implement_start":
            if "execution_packet" not in ref_classes:
                issues.append(
                    ValidationIssue(
                        code="E750_TRACE_IMPLEMENT_START_PACKET_REF_REQUIRED",
                        message="implement_start event must reference execution_packet artifact.",
                    )
                )
        if event_type == "patch_applied":
            if "implementer_result" not in ref_classes:
                issues.append(
                    ValidationIssue(
                        code="E751_TRACE_PATCH_APPLIED_IMPL_RESULT_REQUIRED",
                        message="patch_applied event must reference implementer_result artifact.",
                    )
                )
            if not lineage_lock:
                issues.append(
                    ValidationIssue(
                        code="E752_TRACE_PATCH_APPLIED_LINEAGE_REQUIRED",
                        message="patch_applied event must include lineage_lock_sha256.",
                    )
                )
        if event_type == "blocked":
            if "implementer_result" not in ref_classes:
                issues.append(
                    ValidationIssue(
                        code="E756_TRACE_BLOCKED_IMPL_RESULT_REQUIRED",
                        message="blocked event must reference implementer_result artifact.",
                    )
                )
            if not reason_code:
                issues.append(
                    ValidationIssue(
                        code="E753_TRACE_BLOCKED_REASON_REQUIRED",
                        message="blocked event must include reason_code.",
                    )
                )
            if not attempt_text:
                issues.append(
                    ValidationIssue(
                        code="E754_TRACE_BLOCKED_ATTEMPT_REQUIRED",
                        message="blocked event must include attempt value.",
                    )
                )
            elif not attempt_text.isdigit() or int(attempt_text) < 1:
                issues.append(
                    ValidationIssue(
                        code="E754_TRACE_BLOCKED_ATTEMPT_REQUIRED",
                        message="blocked event attempt must be positive integer.",
                    )
                )
            if not lineage_lock:
                issues.append(
                    ValidationIssue(
                        code="E755_TRACE_BLOCKED_LINEAGE_REQUIRED",
                        message="blocked event must include lineage_lock_sha256.",
                    )
                )
        if event_type == "retry_failed":
            if "implementer_result" not in ref_classes:
                issues.append(
                    ValidationIssue(
                        code="E760_TRACE_RETRY_FAILED_IMPL_RESULT_REQUIRED",
                        message="retry_failed event must reference implementer_result artifact.",
                    )
                )
            if not reason_code:
                issues.append(
                    ValidationIssue(
                        code="E757_TRACE_RETRY_FAILED_REASON_REQUIRED",
                        message="retry_failed event must include reason_code.",
                    )
                )
            if not attempt_text:
                issues.append(
                    ValidationIssue(
                        code="E758_TRACE_RETRY_FAILED_ATTEMPT_REQUIRED",
                        message="retry_failed event must include attempt value.",
                    )
                )
            elif not attempt_text.isdigit() or int(attempt_text) < 1:
                issues.append(
                    ValidationIssue(
                        code="E758_TRACE_RETRY_FAILED_ATTEMPT_REQUIRED",
                        message="retry_failed event attempt must be positive integer.",
                    )
                )
            if not lineage_lock:
                issues.append(
                    ValidationIssue(
                        code="E759_TRACE_RETRY_FAILED_LINEAGE_REQUIRED",
                        message="retry_failed event must include lineage_lock_sha256.",
                    )
                )
            if "blocked" not in seen_event_types:
                issues.append(
                    ValidationIssue(
                        code="E761_TRACE_RETRY_FAILED_BLOCKED_PRECONDITION",
                        message="retry_failed event requires at least one prior blocked event.",
                    )
                )
        if event_type == "review_done":
            if "review_sidecar" not in ref_classes:
                issues.append(
                    ValidationIssue(
                        code="E722_TRACE_REVIEW_REF_REQUIRED",
                        message="review_done event must reference review_sidecar artifact.",
                    )
                )
            if not lineage_lock:
                issues.append(
                    ValidationIssue(
                        code="E704_TRACE_REVIEW_DONE_LINEAGE_REQUIRED",
                        message="review_done event must include lineage_lock_sha256.",
                    )
                )
        if event_type == "verify_done":
            if "verification_result" not in ref_classes:
                issues.append(
                    ValidationIssue(
                        code="E723_TRACE_VERIFY_REF_REQUIRED",
                        message="verify_done event must reference verification_result artifact.",
                    )
                )
            if not lineage_lock:
                issues.append(
                    ValidationIssue(
                        code="E705_TRACE_VERIFY_DONE_LINEAGE_REQUIRED",
                        message="verify_done event must include lineage_lock_sha256.",
                    )
                )
            if verify_phase and verify_phase not in {
                "lane",
                "post_implement",
                "unknown_legacy",
            }:
                issues.append(
                    ValidationIssue(
                        code="E724_TRACE_VERIFY_PHASE_INVALID",
                        message=(
                            "verify_done event verify_phase must be lane/post_implement/unknown_legacy "
                            f"(got {verify_phase!r})."
                        ),
                    )
                )
        if event_type == "explore_done":
            if "exploration_result" not in ref_classes:
                issues.append(
                    ValidationIssue(
                        code="E726_TRACE_EXPLORE_DONE_RESULT_REF_REQUIRED",
                        message="explore_done event must reference exploration_result artifact.",
                    )
                )
            if not lineage_lock:
                issues.append(
                    ValidationIssue(
                        code="E727_TRACE_EXPLORE_DONE_LINEAGE_REQUIRED",
                        message="explore_done event must include lineage_lock_sha256.",
                    )
                )
        if event_type == "escalation":
            if not reason_code:
                issues.append(
                    ValidationIssue(
                        code="E710_ESCALATION_REASON_CODE_REQUIRED",
                        message="escalation event must include reason_code.",
                    )
                )
            if not attempt_text:
                issues.append(
                    ValidationIssue(
                        code="E711_ESCALATION_ATTEMPT_REQUIRED",
                        message="escalation event must include attempt value.",
                    )
                )
            elif not attempt_text.isdigit() or int(attempt_text) < 1:
                issues.append(
                    ValidationIssue(
                        code="E711_ESCALATION_ATTEMPT_REQUIRED",
                        message="escalation event attempt must be positive integer.",
                    )
                )

        seen_event_types.append(event_type)
        previous_hash = event_hash

    meta_sequence = xpath_text(doc.tree, "/p:pxml/p:meta/p:sequence")
    if meta_sequence is not None and meta_sequence != str(len(events)):
        issues.append(
            ValidationIssue(
                code="E375_TRACE_META_SEQUENCE_MISMATCH",
                message="meta/sequence must equal current trace event count.",
            )
        )

    return issues


def _semantic_plan_sidecar(
    doc: ParsedDoc, refs: List[Tuple[str, Optional[str], Optional[str]]]
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    intake_refs = [item for item in refs if item[1] == "task_intake"]
    route_refs = [item for item in refs if item[1] == "manager_route"]
    exploration_refs = [item for item in refs if item[1] == "exploration_result"]
    if len(intake_refs) != 1:
        issues.append(
            ValidationIssue(
                code="E620_PLAN_REF_TASK_INTAKE",
                message="plan_sidecar must reference exactly one task_intake artifact.",
            )
        )
    if len(route_refs) != 1:
        issues.append(
            ValidationIssue(
                code="E621_PLAN_REF_MANAGER_ROUTE",
                message="plan_sidecar must reference exactly one manager_route artifact.",
            )
        )
    if len(exploration_refs) > 1:
        issues.append(
            ValidationIssue(
                code="E624_PLAN_REF_EXPLORATION_COUNT",
                message="plan_sidecar may reference at most one exploration_result artifact.",
            )
        )

    assumptions = doc.tree.xpath(
        "/p:pxml/p:payload/p:assumptions/p:item", namespaces=NS
    )
    proposed = doc.tree.xpath(
        "/p:pxml/p:payload/p:proposed_steps/p:item", namespaces=NS
    )
    if len(assumptions) == 0:
        issues.append(
            ValidationIssue(
                code="E622_PLAN_ASSUMPTIONS_REQUIRED",
                message="plan_sidecar must include assumptions entries.",
            )
        )
    if len(proposed) == 0:
        issues.append(
            ValidationIssue(
                code="E623_PLAN_STEPS_REQUIRED",
                message="plan_sidecar must include proposed_steps entries.",
            )
        )

    return issues


def _semantic_review_sidecar(
    doc: ParsedDoc, refs: List[Tuple[str, Optional[str], Optional[str]]]
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    packet_refs = [item for item in refs if item[1] == "execution_packet"]
    if len(packet_refs) != 1:
        issues.append(
            ValidationIssue(
                code="E624_REVIEW_REF_EXEC_PACKET",
                message="review_sidecar must reference exactly one execution_packet artifact.",
            )
        )

    target_packet_refs = doc.tree.xpath(
        "/p:pxml/p:payload/p:review_target_refs/p:ref[p:doc_class='execution_packet']",
        namespaces=NS,
    )
    if len(target_packet_refs) == 0:
        issues.append(
            ValidationIssue(
                code="E625_REVIEW_TARGET_EXEC_PACKET",
                message="review_sidecar review_target_refs must include execution_packet reference.",
            )
        )

    decision = xpath_text(doc.tree, "/p:pxml/p:payload/p:decision")
    blocking_count_text = xpath_text(doc.tree, "/p:pxml/p:payload/p:blocking_count")
    blocker_findings = doc.tree.xpath(
        "/p:pxml/p:payload/p:findings/p:finding[p:severity='blocker']",
        namespaces=NS,
    )
    try:
        blocking_count = (
            int(blocking_count_text) if blocking_count_text is not None else 0
        )
    except ValueError:
        blocking_count = 0

    if decision == "approve" and (blocking_count > 0 or len(blocker_findings) > 0):
        issues.append(
            ValidationIssue(
                code="E627_REVIEW_APPROVE_BLOCKING_INVALID",
                message="approve decision cannot include blocking findings.",
            )
        )
    if decision == "escalate" and blocking_count == 0 and len(blocker_findings) == 0:
        issues.append(
            ValidationIssue(
                code="E628_REVIEW_ESCALATE_BASIS_MISSING",
                message="escalate decision requires at least one blocking basis.",
            )
        )

    return issues


def _semantic_verification_result(
    doc: ParsedDoc,
    refs: List[Tuple[str, Optional[str], Optional[str]]],
    context_index: Dict[str, ParsedDoc],
) -> List[ValidationIssue]:
    assert doc.tree is not None
    issues: List[ValidationIssue] = []

    packet_refs = [item for item in refs if item[1] == "execution_packet"]
    if len(packet_refs) != 1:
        issues.append(
            ValidationIssue(
                code="E629_VERIFY_REF_EXEC_PACKET",
                message="verification_result must reference exactly one execution_packet artifact.",
            )
        )

    verify_phase = xpath_text(doc.tree, "/p:pxml/p:payload/p:verify_phase")
    if verify_phase and verify_phase not in {
        "lane",
        "post_implement",
        "unknown_legacy",
    }:
        issues.append(
            ValidationIssue(
                code="E996_VERIFICATION_VERIFY_PHASE_INVALID",
                message=(
                    "verification_result verify_phase must be lane/post_implement/unknown_legacy "
                    f"(got {verify_phase!r})."
                ),
            )
        )

    result_lock_hash = xpath_text(
        doc.tree, "/p:pxml/p:payload/p:acceptance_lock_sha256"
    )
    if result_lock_hash is None:
        issues.append(
            ValidationIssue(
                code="E702_VERIFICATION_ACCEPTANCE_LOCK_REQUIRED",
                message="verification_result must include acceptance_lock_sha256.",
            )
        )

    if len(packet_refs) == 1:
        packet_doc = context_index.get(packet_refs[0][0])
        if packet_doc is not None and packet_doc.tree is not None:
            packet_lock_hash = xpath_text(
                packet_doc.tree, "/p:pxml/p:payload/p:acceptance_lock_hash"
            )
            if (
                packet_lock_hash is not None
                and result_lock_hash is not None
                and packet_lock_hash != result_lock_hash
            ):
                issues.append(
                    ValidationIssue(
                        code="E706_VERIFICATION_LINEAGE_MISMATCH",
                        message="verification_result acceptance_lock_sha256 must match execution_packet acceptance_lock_hash.",
                    )
                )

            if verify_phase == "lane":
                packet_route_refs = get_refs(packet_doc.tree)
                manager_route_refs = [
                    item for item in packet_route_refs if item[1] == "manager_route"
                ]
                if len(manager_route_refs) == 1:
                    route_doc = context_index.get(manager_route_refs[0][0])
                    if route_doc is not None and route_doc.tree is not None:
                        selected_path = xpath_text(
                            route_doc.tree, "/p:pxml/p:payload/p:selected_path"
                        )
                        if selected_path not in {"verifier_post", "full_lane"}:
                            issues.append(
                                ValidationIssue(
                                    code="E997_VERIFICATION_VERIFY_PHASE_LANE_MISMATCH",
                                    message=(
                                        "verification_result verify_phase=lane requires manager_route selected_path "
                                        "verifier_post/full_lane."
                                    ),
                                )
                            )

    tests = doc.tree.xpath("/p:pxml/p:payload/p:tests_run/p:test", namespaces=NS)
    if len(tests) == 0:
        issues.append(
            ValidationIssue(
                code="E610_VERIFICATION_TESTS_EMPTY",
                message="verification_result must contain at least one tests_run entry.",
            )
        )
        return issues

    counts = {"pass": 0, "fail": 0, "error": 0, "skipped": 0}
    for test in tests:
        result = xpath_text(etree.ElementTree(test), "./p:result")
        if result in counts:
            counts[result] += 1

    outcome_nodes = {
        "pass": xpath_text(doc.tree, "/p:pxml/p:payload/p:outcomes/p:passed"),
        "fail": xpath_text(doc.tree, "/p:pxml/p:payload/p:outcomes/p:failed"),
        "error": xpath_text(doc.tree, "/p:pxml/p:payload/p:outcomes/p:errored"),
        "skipped": xpath_text(doc.tree, "/p:pxml/p:payload/p:outcomes/p:skipped"),
    }
    outcome_code = {
        "pass": "E611_VERIFICATION_OUTCOME_PASS_COUNT",
        "fail": "E612_VERIFICATION_OUTCOME_FAIL_COUNT",
        "error": "E613_VERIFICATION_OUTCOME_ERROR_COUNT",
        "skipped": "E614_VERIFICATION_OUTCOME_SKIPPED_COUNT",
    }
    for key, expected_text in outcome_nodes.items():
        try:
            expected = int(expected_text) if expected_text is not None else -1
        except ValueError:
            expected = -1
        if counts[key] != expected:
            issues.append(
                ValidationIssue(
                    code=outcome_code[key],
                    message=f"verification_result outcomes mismatch for {key}: expected {counts[key]}",
                )
            )

    verdict = xpath_text(doc.tree, "/p:pxml/p:payload/p:final_verdict")
    unresolved = doc.tree.xpath(
        "/p:pxml/p:payload/p:unverified_areas/p:item[normalize-space(text())!='none']",
        namespaces=NS,
    )

    if verdict == "pass":
        if counts["fail"] > 0 or counts["error"] > 0 or counts["skipped"] > 0:
            issues.append(
                ValidationIssue(
                    code="E615_VERDICT_PASS_COUNTS_INVALID",
                    message="pass verdict requires all checks to pass.",
                )
            )
        if len(unresolved) > 0:
            issues.append(
                ValidationIssue(
                    code="E616_VERDICT_PASS_UNVERIFIED_INVALID",
                    message="pass verdict cannot have unresolved unverified areas.",
                )
            )
    elif verdict == "fail":
        if counts["fail"] == 0:
            issues.append(
                ValidationIssue(
                    code="E617_VERDICT_FAIL_COUNTS_INVALID",
                    message="fail verdict requires at least one failed check.",
                )
            )
    elif verdict == "inconclusive":
        if counts["error"] == 0 and counts["skipped"] == 0 and len(unresolved) == 0:
            issues.append(
                ValidationIssue(
                    code="E618_VERDICT_INCONCLUSIVE_BASIS_MISSING",
                    message="inconclusive verdict requires error skipped or unresolved areas.",
                )
            )

    return issues


def validate_doc(
    doc: ParsedDoc,
    schema_root: Path,
    compiled_schemas: Dict[Path, etree.XMLSchema],
    compiled_rules: List[Tuple[Path, isoschematron.Schematron]],
    run_rules: bool,
    context_index: Dict[str, ParsedDoc],
    strict_refs: bool,
) -> List[ValidationIssue]:
    issues = list(doc.issues)
    if doc.tree is None:
        return issues

    if not doc.doc_class:
        issues.append(
            ValidationIssue(
                code="E110_DOC_CLASS_MISSING",
                message="/pxml/meta/doc_class is missing.",
            )
        )
        return issues

    schema_name = SCHEMA_MAP.get(doc.doc_class)
    if schema_name is None:
        issues.append(
            ValidationIssue(
                code="E120_SCHEMA_NOT_MAPPED",
                message=f"No schema mapping configured for doc_class '{doc.doc_class}'.",
            )
        )
        return issues

    schema_path = schema_root / schema_name
    if not schema_path.exists():
        issues.append(
            ValidationIssue(
                code="E121_SCHEMA_FILE_MISSING",
                message=f"Schema file not found: {schema_path}",
            )
        )
        return issues

    try:
        schema = compile_schema(schema_path, compiled_schemas)
    except (etree.XMLSyntaxError, etree.XMLSchemaParseError) as exc:
        issues.append(
            ValidationIssue(
                code="E130_SCHEMA_LOAD_FAIL",
                message=f"Failed to load XSD '{schema_path}': {exc}",
            )
        )
        return issues

    if not schema.validate(doc.tree):
        for error in schema.error_log:
            issues.append(
                ValidationIssue(
                    code="E200_XSD_FAIL",
                    message=error.message,
                    line=error.line,
                    column=error.column,
                )
            )

    if run_rules:
        for rule_path, schematron in compiled_rules:
            is_valid = schematron.validate(doc.tree)
            if is_valid:
                continue
            report = schematron.validation_report
            if report is not None:
                failed_asserts = report.xpath(
                    "//svrl:failed-assert", namespaces=SVRL_NS
                )
                for failed in failed_asserts:
                    text_parts = failed.xpath("./svrl:text/text()", namespaces=SVRL_NS)
                    text = " ".join(part.strip() for part in text_parts if part.strip())
                    if not text:
                        text = failed.get("test", "rule assertion failed")
                    location = failed.get("location")
                    location_text = f" [location: {location}]" if location else ""
                    issues.append(
                        ValidationIssue(
                            code="E300_RULE_FAIL",
                            message=f"{rule_path.name}: {text}{location_text}",
                        )
                    )
            else:
                issues.append(
                    ValidationIssue(
                        code="E300_RULE_FAIL",
                        message=f"{rule_path.name}: rule validation failed.",
                    )
                )

    issues.extend(
        semantic_checks(doc, context_index=context_index, strict_refs=strict_refs)
    )
    return issues


def build_context_index(
    parsed_docs: Iterable[ParsedDoc],
) -> Tuple[Dict[str, ParsedDoc], Dict[str, List[Path]]]:
    index: Dict[str, ParsedDoc] = {}
    duplicates: Dict[str, List[Path]] = {}

    for doc in parsed_docs:
        if doc.tree is None or not doc.doc_id:
            continue
        existing = index.get(doc.doc_id)
        if existing is None:
            index[doc.doc_id] = doc
            continue
        duplicates.setdefault(doc.doc_id, [existing.path]).append(doc.path)
    return index, duplicates


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Validate PXML artifacts against harness contracts."
    )
    parser.add_argument("target", type=Path, help="File or directory to validate.")
    parser.add_argument(
        "--schema-root",
        type=Path,
        default=repo_root / "contracts" / "schemas",
        help="Directory containing XSD schemas.",
    )
    parser.add_argument(
        "--rules-dir",
        type=Path,
        default=repo_root / "contracts" / "rules",
        help="Directory containing Schematron rules.",
    )
    parser.add_argument(
        "--context-dir",
        type=Path,
        default=None,
        help="Optional directory used to resolve cross-document refs.",
    )
    parser.add_argument(
        "--no-rules",
        action="store_true",
        help="Disable Schematron rule validation.",
    )
    parser.add_argument(
        "--strict-refs",
        action="store_true",
        help="Require all refs/doc_id targets to exist in context index.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = args.target.resolve()
    schema_root = args.schema_root.resolve()
    rules_dir = args.rules_dir.resolve()
    context_dir = args.context_dir.resolve() if args.context_dir else None

    if not target.exists():
        print(f"ERROR: target does not exist: {target}", file=sys.stderr)
        return 2

    files = discover_files(target)
    if not files:
        print(f"ERROR: no XML/PXML files found under: {target}", file=sys.stderr)
        return 2

    parsed_docs = [parse_xml(path) for path in files]

    if context_dir:
        context_files = discover_files(context_dir)
        context_docs = [parse_xml(path) for path in context_files]
    else:
        context_docs = parsed_docs

    context_index, duplicate_doc_ids = build_context_index(context_docs)

    schema_cache: Dict[Path, etree.XMLSchema] = {}
    run_rules = not args.no_rules and rules_dir.exists()
    compiled_rules: List[Tuple[Path, isoschematron.Schematron]] = []
    if run_rules:
        try:
            compiled_rules = compile_rules(rules_dir.glob("*.sch"))
        except Exception as exc:
            print(f"ERROR: failed to compile Schematron rules: {exc}", file=sys.stderr)
            return 2

    strict_refs = args.strict_refs or target.is_dir() or context_dir is not None

    invalid_count = 0
    valid_count = 0

    for doc in parsed_docs:
        issues = validate_doc(
            doc=doc,
            schema_root=schema_root,
            compiled_schemas=schema_cache,
            compiled_rules=compiled_rules,
            run_rules=run_rules,
            context_index=context_index,
            strict_refs=strict_refs,
        )

        if doc.doc_id and doc.doc_id in duplicate_doc_ids:
            issues.append(
                ValidationIssue(
                    code="E141_DUPLICATE_DOC_ID",
                    message=(
                        "Duplicate doc_id in context index: "
                        f"{doc.doc_id} -> {', '.join(str(p) for p in duplicate_doc_ids[doc.doc_id])}"
                    ),
                )
            )

        label = doc.doc_class if doc.doc_class else "unknown"
        if issues:
            invalid_count += 1
            print(f"INVALID {doc.path} (doc_class={label})")
            for issue in issues:
                loc = ""
                if issue.line is not None:
                    loc = f" [line {issue.line}"
                    if issue.column is not None:
                        loc += f", col {issue.column}"
                    loc += "]"
                print(f"  - {issue.code}: {issue.message}{loc}")
        else:
            valid_count += 1
            print(f"VALID   {doc.path} (doc_class={label})")

    total = valid_count + invalid_count
    print(f"Summary: {valid_count} valid, {invalid_count} invalid, {total} total")
    return 0 if invalid_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
