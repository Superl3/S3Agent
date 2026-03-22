#!/usr/bin/env python3
"""Generate operator-facing final render report from runtime SSOT artifacts."""

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
class RenderRule:
    rule_name: str
    decision: str
    output_mode: str
    enforcement_level: str


@dataclass
class RenderPolicy:
    rules: Dict[str, RenderRule]


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
    meta: etree._Element, refs: Optional[etree._Element], payload: etree._Element
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


def next_sequence(runtime_root: Path, task_id: str) -> int:
    maximum = 0
    for path in discover_pxml_files(runtime_root):
        artifact = parse_artifact(path)
        if artifact is None or artifact.task_id != task_id:
            continue
        maximum = max(maximum, artifact.sequence)
    return maximum + 1


def latest_for_task(
    directory: Path, doc_class: str, task_id: str
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


def find_by_doc_id(
    runtime_root: Path,
    doc_id: str,
    expected_class: Optional[str] = None,
) -> Optional[ArtifactInfo]:
    index_path = runtime_root / "index" / "artifacts" / f"{doc_id}.json"
    if index_path.exists():
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        path_text = payload.get("path")
        if isinstance(path_text, str) and path_text.strip():
            candidate_path = runtime_root / path_text
            if candidate_path.exists():
                artifact = parse_artifact(candidate_path)
                if artifact and artifact.doc_id == doc_id:
                    if expected_class and artifact.doc_class != expected_class:
                        return None
                    return artifact

    for path in discover_pxml_files(runtime_root):
        artifact = parse_artifact(path)
        if artifact is None:
            continue
        if artifact.doc_id != doc_id:
            continue
        if expected_class and artifact.doc_class != expected_class:
            continue
        return artifact
    return None


def load_policy(path: Path) -> RenderPolicy:
    if not path.exists():
        raise ValueError(f"rendering policy not found: {path}")
    tree = etree.parse(str(path))
    doc_class = text_at(tree, "/p:pxml/p:meta/p:doc_class")
    if doc_class != "rendering_policy":
        raise ValueError(f"invalid rendering policy doc_class: {doc_class}")

    rules: Dict[str, RenderRule] = {}
    nodes = tree.xpath("/p:pxml/p:payload/p:rules/p:rule", namespaces=XPATH_NS)
    for node in nodes:
        node_tree = etree.ElementTree(node)
        rule_name = text_at(node_tree, "./p:rule_name")
        decision = text_at(node_tree, "./p:decision")
        output_mode = text_at(node_tree, "./p:output_mode")
        enforcement_level = text_at(node_tree, "./p:enforcement_level")
        if (
            rule_name is None
            or decision is None
            or output_mode is None
            or enforcement_level is None
        ):
            continue
        rules[rule_name] = RenderRule(
            rule_name=rule_name,
            decision=decision,
            output_mode=output_mode,
            enforcement_level=enforcement_level,
        )
    return RenderPolicy(rules=rules)


def rule_decision(policy: RenderPolicy, name: str, default: str) -> str:
    rule = policy.rules.get(name)
    if rule is None:
        return default
    return rule.decision


def rule_output_mode(policy: RenderPolicy, name: str, default: str) -> str:
    rule = policy.rules.get(name)
    if rule is None:
        return default
    return rule.output_mode


def parse_bool(text: Optional[str]) -> bool:
    if text is None:
        return False
    return text.strip().lower() == "true"


def summarize_trace_events(
    trace_tree: etree._ElementTree,
    include_trace_summary: bool,
) -> List[str]:
    if not include_trace_summary:
        return ["trace_summary_disabled_by_policy"]
    items: List[str] = []
    event_nodes = trace_tree.xpath(
        "/p:pxml/p:payload/p:events/p:event", namespaces=XPATH_NS
    )
    for node in event_nodes[-8:]:
        node_tree = etree.ElementTree(node)
        seq = text_at(node_tree, "./p:event_seq") or "?"
        event_type = text_at(node_tree, "./p:event_type") or "unknown"
        reason = text_at(node_tree, "./p:reason_code")
        if reason:
            items.append(f"{seq}:{event_type}:{reason}")
        else:
            items.append(f"{seq}:{event_type}")
    return items or ["none"]


def load_failure_index_summary(runtime_root: Path, task_id: str) -> List[str]:
    token = sanitize(task_id)
    index_path = runtime_root / "index" / "failures" / f"{token}.json"
    if not index_path.exists():
        return []
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return []

    counts: Dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        reason = entry.get("reason_code")
        if not isinstance(reason, str) or not reason.strip():
            continue
        key = reason.strip()
        counts[key] = counts.get(key, 0) + 1
    return [f"{reason}:count={count}" for reason, count in sorted(counts.items())]


def load_compaction_for_task(
    runtime_root: Path, task_id: str
) -> Optional[ArtifactInfo]:
    return latest_for_task(
        runtime_root / "compaction" / "checkpoints", "compaction_checkpoint", task_id
    )


def evaluate_gate(
    readiness: str,
    policy: RenderPolicy,
    allow_caution_flag: bool,
    override_not_ready_flag: bool,
) -> Tuple[bool, str, str, List[str]]:
    warnings: List[str] = []

    if readiness == "ready":
        decision = rule_decision(policy, "render_allowed_when_ready", "allow_render")
        output_mode = rule_output_mode(
            policy, "render_allowed_when_ready", "pxml_and_markdown"
        )
        if decision == "deny_render":
            warnings.append("ready_render_denied_by_policy")
            return False, "denied", output_mode, warnings
        if decision == "allow_render_with_warning":
            warnings.append("ready_render_warning_policy")
            return True, "rendered_with_warning", output_mode, warnings
        return True, "rendered", output_mode, warnings

    if readiness == "caution":
        decision = rule_decision(
            policy, "render_allowed_when_caution", "allow_render_with_warning"
        )
        output_mode = rule_output_mode(
            policy, "render_allowed_when_caution", "pxml_and_markdown"
        )
        if decision == "deny_render":
            warnings.append("caution_render_denied_by_policy")
            return False, "denied", output_mode, warnings
        if decision in {"allow_render_with_warning", "require_override"}:
            if not allow_caution_flag:
                warnings.append("caution_requires_allow_caution_flag")
                return False, "denied", output_mode, warnings
            warnings.append("caution_rendered_with_warning")
            return True, "rendered_with_warning", output_mode, warnings
        return True, "rendered", output_mode, warnings

    decision = rule_decision(
        policy, "render_allowed_when_not_ready", "require_override"
    )
    output_mode = rule_output_mode(
        policy, "render_allowed_when_not_ready", "summary_only"
    )
    override_decision = rule_decision(
        policy, "operator_override_allowed", "deny_render"
    )
    override_allowed = override_decision in {
        "allow_render",
        "allow_render_with_warning",
        "require_override",
    }
    if decision == "deny_render":
        warnings.append("not_ready_denied_by_policy")
        return False, "denied", output_mode, warnings
    if not override_not_ready_flag:
        warnings.append("not_ready_requires_override")
        return False, "denied", output_mode, warnings
    if not override_allowed:
        warnings.append("operator_override_disabled_by_policy")
        return False, "denied", output_mode, warnings
    warnings.append("override_not_ready")
    return True, "rendered_with_warning", output_mode, warnings


def build_markdown_from_report(report_tree: etree._ElementTree) -> str:
    task_id = text_at(report_tree, "/p:pxml/p:payload/p:task_id") or "unknown"
    selected_path = (
        text_at(report_tree, "/p:pxml/p:payload/p:selected_path") or "unknown"
    )
    current_status = (
        text_at(report_tree, "/p:pxml/p:payload/p:current_status") or "unknown"
    )
    verdict = (
        text_at(report_tree, "/p:pxml/p:payload/p:final_verdict_candidate") or "unknown"
    )
    readiness = (
        text_at(report_tree, "/p:pxml/p:payload/p:render_readiness_basis") or "unknown"
    )
    mode = text_at(report_tree, "/p:pxml/p:payload/p:render_mode") or "unknown"
    next_action = (
        text_at(report_tree, "/p:pxml/p:payload/p:next_recommended_action") or "unknown"
    )

    def list_items(xpath_expr: str) -> List[str]:
        values = report_tree.xpath(xpath_expr, namespaces=XPATH_NS)
        return [
            value.strip()
            for value in values
            if isinstance(value, str) and value.strip()
        ]

    notable_events = list_items("/p:pxml/p:payload/p:notable_events/p:item/text()")
    notable_failures = list_items("/p:pxml/p:payload/p:notable_failures/p:item/text()")
    quarantine_flags = list_items("/p:pxml/p:payload/p:quarantine_flags/p:item/text()")
    warnings = list_items("/p:pxml/p:payload/p:warnings/p:item/text()")

    lines = [
        f"# Final Render Summary: {task_id}",
        "",
        f"- Render Readiness Basis: `{readiness}`",
        f"- Render Mode: `{mode}`",
        f"- Selected Path: `{selected_path}`",
        f"- Current Status: `{current_status}`",
        f"- Final Verdict Candidate: `{verdict}`",
        "",
        "## Notable Events",
    ]
    for item in notable_events:
        lines.append(f"- {item}")

    lines.append("")
    lines.append("## Notable Failures")
    for item in notable_failures:
        lines.append(f"- {item}")

    lines.append("")
    lines.append("## Quarantine Flags")
    for item in quarantine_flags:
        lines.append(f"- {item}")

    lines.append("")
    lines.append("## Warnings")
    for item in warnings:
        lines.append(f"- {item}")

    lines.append("")
    lines.append("## Next Recommended Action")
    lines.append(next_action)
    lines.append("")
    lines.append("---")
    lines.append("Derived view only. Runtime PXML artifacts remain SSOT.")
    return "\n".join(lines) + "\n"


def run_validation(
    validator: Path, report_path: Path, context_files: Sequence[Path]
) -> None:
    with tempfile.TemporaryDirectory(prefix="pxml_render_validate_") as temp_dir:
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


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Render final operator-facing summary from runtime artifacts."
    )
    parser.add_argument("--task-id", required=True, help="Target task id.")
    parser.add_argument(
        "--allow-caution",
        action="store_true",
        help="Allow rendering when preflight readiness is caution.",
    )
    parser.add_argument(
        "--override-not-ready",
        action="store_true",
        help="Override not_ready preflight gate when policy permits.",
    )
    parser.add_argument(
        "--format",
        choices=["pxml", "markdown", "both"],
        default="both",
        help="Requested render output format.",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=repo_root / "runtime",
        help="Runtime root directory.",
    )
    parser.add_argument(
        "--rendering-policy",
        type=Path,
        default=repo_root / "instructions" / "rendering_policy.pxml",
        help="Rendering policy artifact path.",
    )
    parser.add_argument(
        "--validator",
        type=Path,
        default=repo_root / "scripts" / "pxml_validator.py",
        help="PXML validator path.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip post-generation validator call.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime_root = args.runtime_root.resolve()
    validator = args.validator.resolve()

    if not runtime_root.exists():
        print(f"ERROR: runtime root not found: {runtime_root}", file=sys.stderr)
        return 2

    try:
        policy = load_policy(args.rendering_policy.resolve())
    except Exception as exc:
        print(f"ERROR: failed to load rendering policy: {exc}", file=sys.stderr)
        return 2

    preflight_info = latest_for_task(
        runtime_root / "preflight" / "reports",
        "operator_preflight_report",
        args.task_id,
    )
    if preflight_info is None:
        print(
            "ERROR: operator_preflight_report missing; run operator_preflight before final rendering.",
            file=sys.stderr,
        )
        return 2

    preflight_tree = preflight_info.tree
    readiness = (
        text_at(preflight_tree, "/p:pxml/p:payload/p:render_readiness") or "not_ready"
    )
    quarantine_flags = [
        item.strip()
        for item in preflight_tree.xpath(
            "/p:pxml/p:payload/p:quarantine_flags/p:item/text()", namespaces=XPATH_NS
        )
        if isinstance(item, str) and item.strip() and item.strip() != "none"
    ]
    unresolved_failures = [
        item.strip()
        for item in preflight_tree.xpath(
            "/p:pxml/p:payload/p:unresolved_failures/p:item/text()", namespaces=XPATH_NS
        )
        if isinstance(item, str) and item.strip() and item.strip() != "none"
    ]
    lineage_ok = parse_bool(text_at(preflight_tree, "/p:pxml/p:payload/p:lineage_ok"))
    status_ok = parse_bool(text_at(preflight_tree, "/p:pxml/p:payload/p:status_ok"))

    route_doc_id = text_at(
        preflight_tree, "/p:pxml/p:payload/p:latest_route_ref/p:doc_id"
    )
    packet_doc_id = text_at(
        preflight_tree, "/p:pxml/p:payload/p:latest_packet_ref/p:doc_id"
    )
    status_doc_id = text_at(
        preflight_tree, "/p:pxml/p:payload/p:latest_status_report_ref/p:doc_id"
    )
    trace_doc_id = text_at(
        preflight_tree, "/p:pxml/p:payload/p:latest_trace_ref/p:doc_id"
    )
    verification_doc_id = text_at(
        preflight_tree, "/p:pxml/p:payload/p:latest_verification_ref/p:doc_id"
    )

    required_ids = {
        "route": route_doc_id,
        "packet": packet_doc_id,
        "status": status_doc_id,
        "trace": trace_doc_id,
    }
    missing_ref = [name for name, value in required_ids.items() if value is None]
    if missing_ref:
        print(
            "ERROR: preflight report missing required source refs: "
            + ", ".join(missing_ref),
            file=sys.stderr,
        )
        return 2

    route_info = find_by_doc_id(runtime_root, route_doc_id or "", "manager_route")
    packet_info = find_by_doc_id(runtime_root, packet_doc_id or "", "execution_packet")
    status_info = find_by_doc_id(
        runtime_root, status_doc_id or "", "task_status_report"
    )
    trace_info = find_by_doc_id(runtime_root, trace_doc_id or "", "execution_trace")
    verification_info = (
        find_by_doc_id(runtime_root, verification_doc_id, "verification_result")
        if verification_doc_id
        else None
    )
    if (
        route_info is None
        or packet_info is None
        or status_info is None
        or trace_info is None
    ):
        print(
            "ERROR: one or more preflight source artifacts are missing from runtime index.",
            file=sys.stderr,
        )
        return 2
    if verification_doc_id and verification_info is None:
        print(
            "ERROR: preflight references verification_result that is not available.",
            file=sys.stderr,
        )
        return 2

    include_checkpoint_decision = rule_decision(
        policy, "include_compaction_checkpoint_if_present", "allow_render"
    )
    include_checkpoint = include_checkpoint_decision != "deny_render"
    compaction_info = load_compaction_for_task(runtime_root, args.task_id)
    if not include_checkpoint:
        compaction_info = None

    allowed, render_mode, readiness_output_mode, gate_warnings = evaluate_gate(
        readiness=readiness,
        policy=policy,
        allow_caution_flag=args.allow_caution,
        override_not_ready_flag=args.override_not_ready,
    )

    selected_path = (
        text_at(route_info.tree, "/p:pxml/p:payload/p:selected_path") or "direct"
    )
    current_status = (
        text_at(status_info.tree, "/p:pxml/p:payload/p:current_status") or "unknown"
    )
    final_verdict_candidate = (
        text_at(status_info.tree, "/p:pxml/p:payload/p:final_verdict_candidate")
        or "unknown"
    )
    acceptance_lock = text_at(
        status_info.tree, "/p:pxml/p:payload/p:acceptance_lock_sha256"
    ) or text_at(packet_info.tree, "/p:pxml/p:payload/p:acceptance_lock_hash")
    if acceptance_lock is None:
        print(
            "ERROR: missing acceptance lock in status report and execution packet.",
            file=sys.stderr,
        )
        return 2

    include_trace_summary = (
        rule_decision(policy, "include_trace_summary", "allow_render") != "deny_render"
    )
    include_failure_index_summary = (
        rule_decision(policy, "include_failure_index_summary", "allow_render")
        != "deny_render"
    )
    include_quarantine_flags = (
        rule_decision(policy, "include_quarantine_flags", "allow_render_with_warning")
        != "deny_render"
    )

    notable_events = summarize_trace_events(trace_info.tree, include_trace_summary)
    notable_failures = list(unresolved_failures)
    if include_failure_index_summary:
        for item in load_failure_index_summary(runtime_root, args.task_id):
            if item not in notable_failures:
                notable_failures.append(item)
    if not notable_failures:
        notable_failures = ["none"]

    quarantine_for_payload = list(quarantine_flags) if include_quarantine_flags else []
    if not quarantine_for_payload:
        quarantine_for_payload = ["none"]

    warnings = list(gate_warnings)
    if not lineage_ok:
        warnings.append("lineage_not_ok")
    if not status_ok:
        warnings.append("status_not_ok")
    if quarantine_flags:
        warnings.extend([f"quarantine:{flag}" for flag in quarantine_flags])
    if unresolved_failures:
        warnings.extend([f"unresolved_failure:{item}" for item in unresolved_failures])
    if not allowed:
        warnings.append("render_denied_by_preflight_gate")

    next_action = (
        text_at(status_info.tree, "/p:pxml/p:payload/p:next_recommended_action")
        or text_at(preflight_tree, "/p:pxml/p:payload/p:next_action")
        or "Review runtime artifacts and preflight status before rendering."
    )

    sequence = next_sequence(runtime_root, args.task_id)
    token = sanitize(args.task_id)[:20]
    doc_id = f"doc_final_render_{token}_{sequence:04d}"
    if not re.fullmatch(r"doc_[a-z0-9][a-z0-9._-]{5,63}", doc_id):
        doc_id = f"doc_final_render_{sequence:04d}_{sha256_hex(args.task_id.encode('utf-8'))[:8]}"
    report_id = f"final_render_{token}_{sequence:04d}"

    report_dir = runtime_root / "rendered" / "reports"
    export_dir = runtime_root / "rendered" / "exports"
    report_path = report_dir / f"{doc_id}.pxml"
    markdown_path = export_dir / f"{doc_id}.md"

    format_mode = args.format
    if render_mode == "denied":
        format_mode = "pxml"

    markdown_allowed_by_policy = readiness_output_mode == "pxml_and_markdown"
    write_markdown = (
        allowed and format_mode in {"markdown", "both"} and markdown_allowed_by_policy
    )
    if allowed and format_mode in {"markdown", "both"} and not write_markdown:
        warnings.append("markdown_export_suppressed_by_policy")

    if not warnings:
        warnings = ["none"]

    root = etree.Element(q("pxml"), nsmap=NSMAP)
    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = doc_id
    etree.SubElement(meta, q("doc_class")).text = "final_render_report"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = args.task_id
    etree.SubElement(meta, q("run_id")).text = f"run_renderer_{sanitize(args.task_id)}"
    etree.SubElement(meta, q("sequence")).text = str(sequence)
    etree.SubElement(meta, q("writer_agent")).text = "system"
    etree.SubElement(meta, q("created_at")).text = now_iso()

    refs = etree.SubElement(root, q("refs"))
    for info, relation in [
        (preflight_info, "source_preflight"),
        (status_info, "source_status_report"),
        (route_info, "source_route"),
        (packet_info, "source_packet"),
        (trace_info, "source_trace"),
    ]:
        ref = etree.SubElement(refs, q("ref"))
        etree.SubElement(ref, q("doc_id")).text = info.doc_id
        etree.SubElement(ref, q("doc_class")).text = info.doc_class
        etree.SubElement(ref, q("relation")).text = relation
    if verification_info is not None:
        ref = etree.SubElement(refs, q("ref"))
        etree.SubElement(ref, q("doc_id")).text = verification_info.doc_id
        etree.SubElement(ref, q("doc_class")).text = "verification_result"
        etree.SubElement(ref, q("relation")).text = "source_verification"
    if compaction_info is not None:
        ref = etree.SubElement(refs, q("ref"))
        etree.SubElement(ref, q("doc_id")).text = compaction_info.doc_id
        etree.SubElement(ref, q("doc_class")).text = "compaction_checkpoint"
        etree.SubElement(ref, q("relation")).text = "source_compaction_checkpoint"

    payload = etree.SubElement(root, q("payload"))
    etree.SubElement(payload, q("report_id")).text = report_id
    etree.SubElement(payload, q("task_id")).text = args.task_id
    etree.SubElement(payload, q("derived")).text = "true"

    def build_ref_node(tag_name: str, info: ArtifactInfo, relation: str) -> None:
        node = etree.SubElement(payload, q(tag_name))
        etree.SubElement(node, q("doc_id")).text = info.doc_id
        etree.SubElement(node, q("doc_class")).text = info.doc_class
        etree.SubElement(node, q("relation")).text = relation

    build_ref_node("source_preflight_ref", preflight_info, "source_preflight")
    build_ref_node("source_status_report_ref", status_info, "source_status_report")
    build_ref_node("source_route_ref", route_info, "source_route")
    build_ref_node("source_packet_ref", packet_info, "source_packet")
    build_ref_node("source_trace_ref", trace_info, "source_trace")
    if verification_info is not None:
        build_ref_node(
            "source_verification_ref", verification_info, "source_verification"
        )
    if compaction_info is not None:
        build_ref_node(
            "source_compaction_checkpoint_ref",
            compaction_info,
            "source_compaction_checkpoint",
        )

    etree.SubElement(payload, q("render_mode")).text = render_mode
    etree.SubElement(payload, q("render_readiness_basis")).text = readiness
    etree.SubElement(payload, q("selected_path")).text = selected_path
    etree.SubElement(payload, q("current_status")).text = current_status
    etree.SubElement(
        payload, q("final_verdict_candidate")
    ).text = final_verdict_candidate
    etree.SubElement(payload, q("acceptance_lock_sha256")).text = acceptance_lock

    events_node = etree.SubElement(payload, q("notable_events"))
    for item in notable_events:
        etree.SubElement(events_node, q("item")).text = item

    failures_node = etree.SubElement(payload, q("notable_failures"))
    for item in notable_failures:
        etree.SubElement(failures_node, q("item")).text = item

    quarantine_node = etree.SubElement(payload, q("quarantine_flags"))
    for item in quarantine_for_payload:
        etree.SubElement(quarantine_node, q("item")).text = item

    sections_node = etree.SubElement(payload, q("summary_sections"))
    sections: List[Tuple[str, str]] = [
        (
            "overview",
            "Derived operator-facing view generated from runtime SSOT artifacts; it does not replace source truth.",
        ),
        (
            "path_and_lane",
            f"selected_path={selected_path}; renderer_mode={render_mode}; preflight_readiness={readiness}.",
        ),
        (
            "execution_outcome",
            f"current_status={current_status}; final_verdict_candidate={final_verdict_candidate}; lineage_ok={str(lineage_ok).lower()}.",
        ),
        (
            "verification_outcome",
            (
                f"verification_ref={verification_info.doc_id}"
                if verification_info is not None
                else "verification_result is not present for this task snapshot."
            ),
        ),
        (
            "current_risks",
            "notable_failures="
            + ", ".join(notable_failures)
            + "; quarantine_flags="
            + ", ".join(quarantine_for_payload)
            + "; unresolved_failures="
            + (", ".join(unresolved_failures) if unresolved_failures else "none"),
        ),
        ("next_action", next_action),
    ]
    for section_name, content in sections:
        section = etree.SubElement(sections_node, q("section"))
        etree.SubElement(section, q("section_name")).text = section_name
        etree.SubElement(section, q("content")).text = content

    etree.SubElement(payload, q("next_recommended_action")).text = next_action

    warnings_node = etree.SubElement(payload, q("warnings"))
    for item in warnings:
        etree.SubElement(warnings_node, q("item")).text = item

    exports_node = etree.SubElement(payload, q("generated_exports"))
    report_rel = report_path.relative_to(runtime_root).as_posix()
    etree.SubElement(exports_node, q("pxml_path")).text = report_rel

    integrity = etree.SubElement(root, q("integrity"))
    content_sha = compute_content_hash(meta, refs, payload)
    etree.SubElement(integrity, q("content_sha256")).text = content_sha
    preflight_sha = text_at(preflight_tree, "/p:pxml/p:integrity/p:content_sha256")
    if preflight_sha:
        etree.SubElement(integrity, q("parent_sha256")).text = preflight_sha

    ensure_dir(report_dir)
    ensure_dir(export_dir)
    report_tree = etree.ElementTree(root)
    report_tree.write(
        str(report_path), encoding="UTF-8", xml_declaration=True, pretty_print=True
    )

    markdown_rel: Optional[str] = None
    if write_markdown:
        markdown_text = build_markdown_from_report(report_tree)
        markdown_path.write_text(markdown_text, encoding="utf-8")
        markdown_rel = markdown_path.relative_to(runtime_root).as_posix()

        # Update generated_exports/markdown_path and recompute integrity hash.
        exports_node = root.find("p:payload/p:generated_exports", namespaces=XPATH_NS)
        assert exports_node is not None
        etree.SubElement(exports_node, q("markdown_path")).text = markdown_rel
        integrity_node = root.find("p:integrity", namespaces=XPATH_NS)
        assert integrity_node is not None
        for child in list(integrity_node):
            integrity_node.remove(child)
        new_content_sha = compute_content_hash(meta, refs, payload)
        etree.SubElement(integrity_node, q("content_sha256")).text = new_content_sha
        if preflight_sha:
            etree.SubElement(integrity_node, q("parent_sha256")).text = preflight_sha
        report_tree.write(
            str(report_path), encoding="UTF-8", xml_declaration=True, pretty_print=True
        )

    latest_path = (
        runtime_root / "latest" / f"{sanitize(args.task_id)}_final_render_report.pxml"
    )
    ensure_dir(latest_path.parent)
    shutil.copy2(report_path, latest_path)

    tasks_dir = runtime_root / "index" / "tasks"
    artifacts_dir = runtime_root / "index" / "artifacts"
    ensure_dir(tasks_dir)
    ensure_dir(artifacts_dir)

    task_index_path = tasks_dir / f"{sanitize(args.task_id)}.json"
    task_index: Dict[str, object] = {}
    if task_index_path.exists():
        try:
            task_index = json.loads(task_index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            task_index = {}
    task_index["task_id"] = args.task_id
    task_index["latest_final_render_report"] = report_rel
    if markdown_rel:
        task_index["latest_final_render_markdown"] = markdown_rel
    task_index["updated_at"] = now_iso()
    task_index_path.write_text(
        json.dumps(task_index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    artifact_index = {
        "doc_id": doc_id,
        "doc_class": "final_render_report",
        "task_id": args.task_id,
        "path": report_rel,
        "updated_at": now_iso(),
    }
    (artifacts_dir / f"{doc_id}.json").write_text(
        json.dumps(artifact_index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    if not args.skip_validate:
        if not validator.exists():
            print(f"ERROR: validator not found: {validator}", file=sys.stderr)
            return 2
        context_files = [
            preflight_info.path,
            status_info.path,
            route_info.path,
            packet_info.path,
            trace_info.path,
        ]
        if verification_info is not None:
            context_files.append(verification_info.path)
        if compaction_info is not None:
            context_files.append(compaction_info.path)
        try:
            run_validation(validator, report_path, context_files)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    print(f"Generated final_render_report: {report_path}")
    print(f"render_mode={render_mode}")
    print(f"render_readiness_basis={readiness}")
    if markdown_rel:
        print(f"markdown_export={runtime_root / markdown_rel}")

    if not allowed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
