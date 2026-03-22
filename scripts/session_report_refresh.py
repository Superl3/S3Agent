#!/usr/bin/env python3
"""Refresh derived session_report artifacts from current runtime snapshots."""

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


def q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sanitize(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9._-]", "_", lowered)
    lowered = re.sub(r"_+", "_", lowered).strip("_")
    return lowered or "id"


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
    except etree.XMLSyntaxError:
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


def latest_for_task(
    directory: Path, doc_class: str, task_id: str
) -> Optional[ArtifactInfo]:
    candidates: List[ArtifactInfo] = []
    for path in discover_pxml_files(directory):
        parsed = parse_artifact(path)
        if parsed is None:
            continue
        if parsed.task_id != task_id or parsed.doc_class != doc_class:
            continue
        candidates.append(parsed)
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


def parse_task_file(path: Path) -> Optional[Dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def resolve_runtime_path(runtime_root: Path, value: str) -> Path:
    normalized = value.replace("\\", "/").lstrip("/")
    return (runtime_root / normalized).resolve()


def load_smoke_set_file(path: Path) -> List[str]:
    task_ids: List[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        task_ids.append(line)
    return task_ids


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


def load_release_gate_profile_coverage(path: Path) -> List[str]:
    parsed = parse_artifact(path)
    if parsed is None:
        return []
    if parsed.doc_class != "release_gate_profile":
        return []
    values = parsed.tree.xpath(
        "/p:pxml/p:payload/p:coverage_task_ids/p:item/text()",
        namespaces=XPATH_NS,
    )
    return unique_preserve(
        [value.strip() for value in values if isinstance(value, str) and value.strip()]
    )


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
    return sorted(set(refs))


def derive_release_readiness(
    preflight_readiness: Optional[str],
    status_value: Optional[str],
    render_decision: str,
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
        if render_decision in {"rendered", "rendered_with_warning"}:
            return "pass"
        return "fail"
    return "inconclusive"


def derive_runbook_result(status_value: Optional[str], release_readiness: str) -> str:
    if release_readiness == "pass":
        return "success"
    if status_value in {"blocked", "retry_failed", "escalated"}:
        return "blocked"
    if release_readiness == "fail":
        return "failed"
    return "partial"


def run_validation(
    validator: Path, report_path: Path, context_files: Sequence[Path]
) -> None:
    with tempfile.TemporaryDirectory(
        prefix="pxml_session_refresh_validate_"
    ) as temp_dir:
        temp_root = Path(temp_dir)
        target_copy = temp_root / report_path.name
        shutil.copy2(report_path, target_copy)
        for source in context_files:
            if not source.exists():
                continue
            destination = temp_root / source.name
            if destination.exists():
                continue
            shutil.copy2(source, destination)

        command = [
            sys.executable,
            str(validator),
            str(target_copy),
            "--context-dir",
            str(temp_root),
        ]
        proc = subprocess.run(command, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"Validation failed for {report_path}")


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Refresh derived session_report artifacts from runtime latest snapshots."
    )
    parser.add_argument(
        "--task-id",
        action="append",
        default=[],
        help="Target task_id to refresh (can be repeated).",
    )
    parser.add_argument(
        "--coverage-set-file",
        type=Path,
        default=None,
        help="Optional line-delimited task_id file.",
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
        help="Use runtime/index/tasks as default source.",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=repo_root / "instructions" / "release_gate_profile.pxml",
        help="release_gate_profile artifact path used when no explicit tasks are provided.",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=repo_root / "runtime",
        help="Runtime root directory.",
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
        help="Skip generated session report validation.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime_root = args.runtime_root.resolve()
    validator = args.validator.resolve()
    profile_path = args.profile.resolve()

    if not runtime_root.exists():
        print(f"ERROR: runtime root not found: {runtime_root}", file=sys.stderr)
        return 2
    if not args.skip_validate and not validator.exists():
        print(f"ERROR: validator not found: {validator}", file=sys.stderr)
        return 2

    explicit_inputs = bool(
        args.task_id
        or args.coverage_set_file is not None
        or args.smoke_set_file is not None
        or args.use_default_smoke_set
    )
    task_ids: List[str] = []
    task_ids.extend(args.task_id)
    if args.coverage_set_file is not None:
        task_ids.extend(load_smoke_set_file(args.coverage_set_file.resolve()))
    if args.smoke_set_file is not None:
        task_ids.extend(load_smoke_set_file(args.smoke_set_file.resolve()))
    if args.use_default_smoke_set:
        task_ids.extend(collect_default_smoke_set(runtime_root))
    if not explicit_inputs and profile_path.exists():
        task_ids.extend(load_release_gate_profile_coverage(profile_path))
    if not explicit_inputs and not task_ids:
        task_ids.extend(collect_default_smoke_set(runtime_root))
    task_ids = unique_preserve(task_ids)

    if not task_ids:
        print("ERROR: no tasks selected for session refresh", file=sys.stderr)
        return 2

    session_reports_dir = runtime_root / "ops" / "session_reports"
    refresh_dir = runtime_root / "ops" / "session_refresh"
    latest_dir = runtime_root / "latest"
    tasks_dir = runtime_root / "index" / "tasks"
    artifacts_dir = runtime_root / "index" / "artifacts"
    ensure_dir(session_reports_dir)
    ensure_dir(refresh_dir)
    ensure_dir(latest_dir)
    ensure_dir(tasks_dir)
    ensure_dir(artifacts_dir)

    refreshed: List[Dict[str, str]] = []
    failures: List[str] = []

    for task_id in task_ids:
        intake_info = latest_for_task(
            runtime_root / "inbox" / "task_intake", "task_intake", task_id
        )
        route_info = latest_for_task(
            runtime_root / "packets" / "manager_route", "manager_route", task_id
        )
        packet_info = latest_for_task(
            runtime_root / "packets" / "execution_packet", "execution_packet", task_id
        )
        status_info = latest_for_task(
            runtime_root / "status" / "reports", "task_status_report", task_id
        )
        preflight_info = latest_for_task(
            runtime_root / "preflight" / "reports", "operator_preflight_report", task_id
        )
        trace_info = latest_for_task(
            runtime_root / "traces" / "by_task", "execution_trace", task_id
        )
        render_info = latest_for_task(
            runtime_root / "rendered" / "reports", "final_render_report", task_id
        )
        verification_info = latest_for_task(
            runtime_root / "verification" / "results", "verification_result", task_id
        )
        compaction_info = latest_for_task(
            runtime_root / "compaction" / "checkpoints",
            "compaction_checkpoint",
            task_id,
        )

        if (
            intake_info is None
            or route_info is None
            or packet_info is None
            or status_info is None
            or preflight_info is None
            or trace_info is None
        ):
            failures.append(f"{task_id}:missing_required_runtime_artifacts")
            continue

        status_value = text_at(status_info.tree, "/p:pxml/p:payload/p:current_status")
        status_next_action = text_at(
            status_info.tree,
            "/p:pxml/p:payload/p:next_recommended_action",
        )
        preflight_readiness = text_at(
            preflight_info.tree,
            "/p:pxml/p:payload/p:render_readiness",
        )
        preflight_next_action = text_at(
            preflight_info.tree,
            "/p:pxml/p:payload/p:next_action",
        )

        render_mode = (
            text_at(render_info.tree, "/p:pxml/p:payload/p:render_mode")
            if render_info is not None
            else None
        )
        if render_mode in {"rendered", "rendered_with_warning", "denied"}:
            render_decision = render_mode
        else:
            render_decision = "skipped"

        quarantine_refs = load_quarantine_refs(runtime_root, task_id)
        release_readiness = derive_release_readiness(
            preflight_readiness=preflight_readiness,
            status_value=status_value,
            render_decision=render_decision,
            quarantine_refs=quarantine_refs,
        )
        runbook_result = derive_runbook_result(status_value, release_readiness)

        warnings: List[str] = []
        if preflight_readiness == "caution":
            warnings.append("preflight_readiness_caution")
        if preflight_readiness == "not_ready":
            warnings.append("preflight_readiness_not_ready")
        if quarantine_refs:
            warnings.append("quarantine_refs_present")
        if render_decision == "skipped":
            warnings.append("render_report_missing_or_unknown")
        warnings.append("session_refresh_derived")
        if not warnings:
            warnings = ["none"]

        render_override_used = False
        if render_info is not None:
            render_warnings = {
                item.strip()
                for item in render_info.tree.xpath(
                    "/p:pxml/p:payload/p:warnings/p:item/text()",
                    namespaces=XPATH_NS,
                )
                if isinstance(item, str) and item.strip()
            }
            if (
                "override_not_ready" in render_warnings
                or "allow_caution_render" in render_warnings
            ):
                render_override_used = True

        runbook_start = preflight_info.created_at
        runbook_end = now_iso()
        next_action = (
            preflight_next_action
            or status_next_action
            or "Review refreshed session report and proceed with operator workflow guide."
        )

        sequence = next_sequence(runtime_root, task_id)
        token = sanitize(task_id)[:20]
        doc_id = f"doc_session_report_{token}_{sequence:04d}"
        if not re.fullmatch(r"doc_[a-z0-9][a-z0-9._-]{5,63}", doc_id):
            suffix = sha256_hex(task_id.encode("utf-8"))[:8]
            doc_id = f"doc_session_report_{sequence:04d}_{suffix}"
        report_id = f"session_refresh_{token}_{sequence:04d}"
        output_path = session_reports_dir / f"{doc_id}.pxml"

        root = etree.Element(q("pxml"), nsmap=NSMAP)
        meta = etree.SubElement(root, q("meta"))
        etree.SubElement(meta, q("doc_id")).text = doc_id
        etree.SubElement(meta, q("doc_class")).text = "session_report"
        etree.SubElement(meta, q("schema_version")).text = "1.0.0"
        etree.SubElement(meta, q("task_id")).text = task_id
        etree.SubElement(
            meta, q("run_id")
        ).text = f"run_session_refresh_{sanitize(task_id)}"
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
        etree.SubElement(payload, q("session_report_id")).text = report_id
        etree.SubElement(payload, q("task_id")).text = task_id
        etree.SubElement(payload, q("derived")).text = "true"
        etree.SubElement(payload, q("runbook_start_time")).text = runbook_start
        etree.SubElement(payload, q("runbook_end_time")).text = runbook_end
        etree.SubElement(payload, q("cleanup_performed")).text = "false"

        build_ref_node(payload, "source_intake_ref", intake_info, "source_intake")
        build_ref_node(payload, "latest_route_ref", route_info, "latest_route")
        build_ref_node(payload, "latest_packet_ref", packet_info, "latest_packet")
        build_ref_node(
            payload, "latest_status_report_ref", status_info, "latest_status_report"
        )
        build_ref_node(
            payload, "latest_preflight_ref", preflight_info, "latest_preflight"
        )
        if render_info is not None:
            build_ref_node(
                payload, "latest_render_report_ref", render_info, "latest_render_report"
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

        etree.SubElement(
            payload, q("release_readiness_result")
        ).text = release_readiness
        etree.SubElement(payload, q("render_decision")).text = render_decision
        etree.SubElement(payload, q("render_override_used")).text = (
            "true" if render_override_used else "false"
        )
        etree.SubElement(payload, q("runbook_result")).text = runbook_result

        warnings_node = etree.SubElement(payload, q("warnings"))
        for item in unique_preserve(warnings):
            etree.SubElement(warnings_node, q("item")).text = item

        etree.SubElement(payload, q("next_action")).text = next_action
        etree.SubElement(payload, q("task_executor_exit_code")).text = "0"
        etree.SubElement(payload, q("preflight_exit_code")).text = "0"
        if render_info is not None:
            etree.SubElement(payload, q("renderer_exit_code")).text = "0"

        integrity = etree.SubElement(root, q("integrity"))
        content_sha = compute_content_hash(meta, refs, payload)
        etree.SubElement(integrity, q("content_sha256")).text = content_sha
        preflight_sha = text_at(
            preflight_info.tree, "/p:pxml/p:integrity/p:content_sha256"
        )
        if preflight_sha:
            etree.SubElement(integrity, q("parent_sha256")).text = preflight_sha

        tree = etree.ElementTree(root)
        tree.write(
            str(output_path), encoding="UTF-8", xml_declaration=True, pretty_print=True
        )

        latest_path = latest_dir / f"{sanitize(task_id)}_session_report.pxml"
        shutil.copy2(output_path, latest_path)

        task_index_path = tasks_dir / f"{sanitize(task_id)}.json"
        task_index_payload: Dict[str, object] = {}
        if task_index_path.exists():
            loaded = parse_task_file(task_index_path)
            if loaded is not None:
                task_index_payload = loaded
        task_index_payload["task_id"] = task_id
        task_index_payload["latest_session_report"] = str(
            output_path.relative_to(runtime_root)
        ).replace("\\", "/")
        task_index_payload["updated_at"] = now_iso()
        task_index_path.write_text(
            json.dumps(task_index_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        artifact_index_payload = {
            "doc_id": doc_id,
            "doc_class": "session_report",
            "task_id": task_id,
            "path": str(output_path.relative_to(runtime_root)).replace("\\", "/"),
            "updated_at": now_iso(),
        }
        (artifacts_dir / f"{doc_id}.json").write_text(
            json.dumps(artifact_index_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        if not args.skip_validate:
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
                failures.append(f"{task_id}:validation_failed:{exc}")
                continue

        refreshed.append(
            {
                "task_id": task_id,
                "session_report": str(output_path.relative_to(runtime_root)).replace(
                    "\\", "/"
                ),
                "release_readiness_result": release_readiness,
                "runbook_result": runbook_result,
            }
        )

    summary = {
        "generated_at": now_iso(),
        "task_count": len(task_ids),
        "refreshed_count": len(refreshed),
        "failed_count": len(failures),
        "tasks": refreshed,
        "failures": failures,
    }
    summary_path = refresh_dir / f"session_refresh_{now_stamp()}.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"session_refresh_summary={summary_path}")
    print(f"task_count={len(task_ids)}")
    print(f"refreshed_count={len(refreshed)}")
    print(f"failed_count={len(failures)}")
    if failures:
        for item in failures:
            print(f"failure={item}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
