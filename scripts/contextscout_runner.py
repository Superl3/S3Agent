#!/usr/bin/env python3
"""Baseline context provisioning runner for write-intent tasks."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

from runtime_bootstrap import bootstrap_runtime

try:
    from lxml import etree
except ModuleNotFoundError:
    print(
        "ERROR: lxml is required. Install with: python -m pip install lxml",
        file=sys.stderr,
    )
    raise SystemExit(3)

from context_contract import (
    compute_context_lock_sha256,
    ensure_dir,
    load_task_index,
    normalize_target_paths,
    promote_exploration_result,
    resolve_baseline_bundle,
    sanitize,
    sha256_hex,
    write_task_index,
)
from packet_builder import (
    build_routing_signals,
    choose_execution_shape,
    default_scope,
    read_intake,
)
from repo_scout import RepoScoutResult, run_repo_scout


NS = "urn:pxml:v1"
NSMAP = {None: NS}


def q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


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


def append_trace_event(
    *,
    trace_script: Path,
    runtime_root: Path,
    task_id: str,
    event_type: str,
    message: str,
    artifact_files: Sequence[Path],
    reason_code: Optional[str] = None,
) -> None:
    command = [
        sys.executable,
        str(trace_script),
        "--task-id",
        task_id,
        "--event-type",
        event_type,
        "--actor",
        "explorer",
        "--message",
        message,
        "--runtime-root",
        str(runtime_root),
    ]
    if reason_code:
        command.extend(["--reason-code", reason_code])
    for artifact in artifact_files:
        command.extend(["--artifact-file", str(artifact)])
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"trace_appender failed for event {event_type!r}")


def git_fingerprint(workspace_root: Path) -> dict[str, object]:
    head = "unknown"
    dirty = "unknown"
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workspace_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if rev.returncode == 0 and rev.stdout.strip():
            head = rev.stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if status.returncode == 0:
            dirty = "true" if status.stdout.strip() else "false"
    except OSError:
        pass
    return {"git_head": head, "git_dirty": dirty}


def target_file_digests(workspace_root: Path, target_files: Sequence[str]) -> List[str]:
    digests: List[str] = []
    for rel_path in normalize_target_paths(target_files):
        candidate = workspace_root / rel_path
        if candidate.exists() and candidate.is_file():
            digests.append(f"{rel_path}:{sha256_hex(candidate.read_bytes())}")
        else:
            digests.append(f"{rel_path}:missing")
    return digests


def contract_fingerprint(repo_root: Path) -> str:
    schema_paths = [
        repo_root / "contracts" / "schemas" / "execution_packet.xsd",
        repo_root / "contracts" / "schemas" / "exploration_result.xsd",
    ]
    payload: List[str] = []
    for path in schema_paths:
        if path.exists():
            payload.append(f"{path.name}:{sha256_hex(path.read_bytes())}")
    return sha256_hex("|".join(payload).encode("utf-8"))


def baseline_cache_key(
    *,
    repo_root: Path,
    workspace_root: Path,
    intake_hash: str,
    execution_shape: str,
    target_files: Sequence[str],
) -> str:
    payload = {
        "intake_hash": intake_hash,
        "execution_shape": execution_shape,
        "target_files": normalize_target_paths(target_files),
        "git": git_fingerprint(workspace_root),
        "target_digests": target_file_digests(workspace_root, target_files),
        "contract_fingerprint": contract_fingerprint(repo_root),
    }
    return sha256_hex(json.dumps(payload, sort_keys=True).encode("utf-8"))


def build_result_tree(
    *,
    intake_path: Path,
    intake_doc_id: str,
    task_id: str,
    run_id: str,
    scout: RepoScoutResult,
    doc_id: str,
    sequence: int,
) -> etree._ElementTree:
    root = etree.Element(q("pxml"), nsmap=NSMAP)

    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = doc_id
    etree.SubElement(meta, q("doc_class")).text = "exploration_result"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = task_id
    etree.SubElement(meta, q("run_id")).text = run_id
    etree.SubElement(meta, q("sequence")).text = str(sequence)
    etree.SubElement(meta, q("writer_agent")).text = "explorer"
    etree.SubElement(meta, q("created_at")).text = now_iso()

    refs = etree.SubElement(root, q("refs"))
    intake_ref = etree.SubElement(refs, q("ref"))
    etree.SubElement(intake_ref, q("doc_id")).text = intake_doc_id
    etree.SubElement(intake_ref, q("doc_class")).text = "task_intake"
    etree.SubElement(intake_ref, q("relation")).text = "source_intake"

    payload = etree.SubElement(root, q("payload"))
    etree.SubElement(payload, q("task_id")).text = task_id
    etree.SubElement(payload, q("exploration_kind")).text = scout.exploration_kind
    etree.SubElement(payload, q("exploration_scope")).text = "baseline"
    etree.SubElement(payload, q("actionability")).text = scout.actionability
    etree.SubElement(payload, q("target_root")).text = scout.target_root
    etree.SubElement(payload, q("context_producer")).text = "contextscout_runner"
    etree.SubElement(payload, q("context_mode")).text = "baseline_provisioning"
    etree.SubElement(payload, q("search_scope")).text = scout.search_scope
    etree.SubElement(payload, q("budget_used")).text = scout.budget_used

    providers = etree.SubElement(payload, q("providers"))
    for provider in scout.providers:
        provider_node = etree.SubElement(providers, q("provider"))
        etree.SubElement(provider_node, q("name")).text = provider.name
        etree.SubElement(provider_node, q("used")).text = (
            "true" if provider.used else "false"
        )
        etree.SubElement(provider_node, q("success")).text = (
            "true" if provider.success else "false"
        )
        etree.SubElement(provider_node, q("notes")).text = provider.notes

    focus_questions = etree.SubElement(payload, q("focus_questions"))
    for item in scout.focus_questions:
        etree.SubElement(focus_questions, q("item")).text = item

    findings = etree.SubElement(payload, q("key_findings"))
    for item in scout.key_findings:
        etree.SubElement(findings, q("item")).text = item

    evidence_items = etree.SubElement(payload, q("evidence_items"))
    for item in scout.evidence_items:
        evidence = etree.SubElement(evidence_items, q("evidence"))
        etree.SubElement(evidence, q("source_provider")).text = item.source_provider
        etree.SubElement(evidence, q("path")).text = item.path
        if item.line_start is not None:
            etree.SubElement(evidence, q("line_start")).text = str(item.line_start)
        if item.line_end is not None:
            etree.SubElement(evidence, q("line_end")).text = str(item.line_end)
        if item.symbol:
            etree.SubElement(evidence, q("symbol")).text = item.symbol
        etree.SubElement(evidence, q("summary")).text = item.summary

    if scout.open_questions:
        open_questions = etree.SubElement(payload, q("open_questions"))
        for item in scout.open_questions:
            etree.SubElement(open_questions, q("item")).text = item

    next_actions = etree.SubElement(payload, q("recommended_next_actions"))
    for item in scout.recommended_next_actions:
        etree.SubElement(next_actions, q("item")).text = item

    if scout.cache_refs:
        cache_refs = etree.SubElement(payload, q("cache_refs"))
        for item in scout.cache_refs:
            etree.SubElement(cache_refs, q("item")).text = item

    if scout.candidate_files:
        candidate_files = etree.SubElement(payload, q("candidate_files"))
        for item in scout.candidate_files:
            etree.SubElement(candidate_files, q("item")).text = item

    if scout.target_files:
        target_files = etree.SubElement(payload, q("target_files"))
        for item in scout.target_files:
            etree.SubElement(target_files, q("item")).text = item

    etree.SubElement(payload, q("usability_state")).text = scout.usability_state
    etree.SubElement(payload, q("confidence")).text = scout.confidence
    etree.SubElement(payload, q("evidence_count")).text = str(scout.evidence_count)
    etree.SubElement(payload, q("open_questions_count")).text = str(
        scout.open_questions_count
    )
    etree.SubElement(payload, q("completion_state")).text = scout.completion_state
    etree.SubElement(payload, q("escalation_requested")).text = (
        "true" if scout.escalation_requested else "false"
    )
    notes = etree.SubElement(payload, q("notes"))
    for item in scout.notes or ["baseline context provisioning completed"]:
        etree.SubElement(notes, q("item")).text = item

    integrity = etree.SubElement(root, q("integrity"))
    etree.SubElement(integrity, q("content_sha256")).text = compute_content_hash(
        meta, refs, payload
    )
    etree.SubElement(integrity, q("parent_sha256")).text = sha256_hex(
        intake_path.read_bytes()
    )

    return etree.ElementTree(root)


def write_xml(tree: etree._ElementTree, path: Path) -> None:
    ensure_dir(path.parent)
    tree.write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True)


def run_validation(
    validator: Path, result_path: Path, context_files: Sequence[Path]
) -> None:
    with tempfile.TemporaryDirectory(prefix="pxml_contextscout_validate_") as temp_dir:
        temp_root = Path(temp_dir)
        copied_result = temp_root / result_path.name
        shutil.copy2(result_path, copied_result)
        for file_path in context_files:
            if file_path.exists():
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


def next_sequence(runtime_root: Path, task_id: str) -> int:
    max_sequence = 0
    for path in runtime_root.rglob("*.pxml"):
        if not path.is_file():
            continue
        try:
            tree = etree.parse(str(path))
        except etree.XMLSyntaxError:
            continue
        if (
            tree.xpath("string(/p:pxml/p:meta/p:task_id)", namespaces={"p": NS})
            != task_id
        ):
            continue
        seq_text = tree.xpath("string(/p:pxml/p:meta/p:sequence)", namespaces={"p": NS})
        try:
            max_sequence = max(max_sequence, int(seq_text or "0"))
        except ValueError:
            pass
    return max_sequence + 1


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Provision baseline context before final execution_packet issuance."
    )
    parser.add_argument("--intake", required=True, type=Path, help="task_intake path")
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
        help="Workspace root to scout.",
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
        help="Trace appender path.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip validation for generated baseline exploration_result.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    intake_path = args.intake.resolve()
    workspace_root = args.workspace_root.resolve()
    validator_path = args.validator.resolve()
    trace_script = args.trace_script.resolve()

    if not intake_path.exists():
        print(f"ERROR: intake not found: {intake_path}", file=sys.stderr)
        return 2
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
    print(runtime_ready.success_line("contextscout_runner"))

    try:
        intake = read_intake(intake_path)
    except Exception as exc:
        print(f"ERROR: failed to parse task_intake: {exc}", file=sys.stderr)
        return 2

    signals = build_routing_signals(intake)
    planner_decision = choose_execution_shape(intake, signals)
    if not planner_decision.write_intent:
        print(
            "ERROR: contextscout_runner only supports write-intent tasks",
            file=sys.stderr,
        )
        return 2

    _in_scope, _out_scope, expected_files, localization_targets = default_scope(
        intake.task_type
    )
    target_files = [path for path, _mode in expected_files] + localization_targets
    target_files = normalize_target_paths(target_files)
    cache_key = baseline_cache_key(
        repo_root=repo_root,
        workspace_root=workspace_root,
        intake_hash=intake.content_sha256,
        execution_shape=planner_decision.execution_shape,
        target_files=target_files,
    )

    index = load_task_index(runtime_root, intake.task_id)
    cached_key = index.get("baseline_cache_key")
    cached_doc_id = index.get("current_manager_baseline_doc_id")
    if (
        isinstance(cached_key, str)
        and cached_key == cache_key
        and isinstance(cached_doc_id, str)
    ):
        cached_bundle = resolve_baseline_bundle(
            runtime_root, task_id=intake.task_id, baseline_doc_id=cached_doc_id
        )
        if cached_bundle is not None:
            promote_exploration_result(
                runtime_root=runtime_root,
                task_id=intake.task_id,
                doc_id=cached_bundle.doc_id,
                result_path=cached_bundle.path,
                exploration_scope="baseline",
                cache_key=cache_key,
            )
            try:
                append_trace_event(
                    trace_script=trace_script,
                    runtime_root=runtime_root,
                    task_id=intake.task_id,
                    event_type="baseline_context_done",
                    message="Baseline context provisioning reused cached contextscout result.",
                    artifact_files=[cached_bundle.path],
                )
            except Exception:
                pass
            print(f"Reused baseline exploration_result: {cached_bundle.path}")
            return 0

    try:
        append_trace_event(
            trace_script=trace_script,
            runtime_root=runtime_root,
            task_id=intake.task_id,
            event_type="baseline_context_start",
            message="Baseline context provisioning started before final execution_packet issuance.",
            artifact_files=[intake_path],
        )
    except Exception:
        pass

    scout = run_repo_scout(
        workspace_root=workspace_root,
        request_text=intake.request_text,
        requested_outcome=intake.requested_outcome,
        task_summary=f"{intake.task_type} baseline context: {intake.request_text}",
        execution_shape=planner_decision.execution_shape,
        localization_targets=target_files,
        cache_root=runtime_root / "exploration" / "cache",
        cache_ref_base=runtime_root,
        exploration_scope="baseline",
    )

    sequence = next_sequence(runtime_root, intake.task_id)
    doc_id = f"doc_exploration_result_{sanitize(intake.task_id)[:20]}_{sequence:04d}"
    result_tree = build_result_tree(
        intake_path=intake_path,
        intake_doc_id=intake.doc_id,
        task_id=intake.task_id,
        run_id=intake.run_id,
        scout=scout,
        doc_id=doc_id,
        sequence=sequence,
    )
    results_dir = runtime_root / "exploration" / "results"
    ensure_dir(results_dir)
    result_path = results_dir / f"{doc_id}.pxml"
    write_xml(result_tree, result_path)

    if not args.skip_validate:
        if not validator_path.exists():
            print(f"ERROR: validator not found: {validator_path}", file=sys.stderr)
            return 2
        try:
            run_validation(validator_path, result_path, [intake_path])
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    promote_exploration_result(
        runtime_root=runtime_root,
        task_id=intake.task_id,
        doc_id=doc_id,
        result_path=result_path,
        exploration_scope="baseline",
        cache_key=cache_key,
    )
    index = load_task_index(runtime_root, intake.task_id)
    index["latest_context_lock_sha256"] = compute_context_lock_sha256(
        baseline_doc_id=doc_id,
        baseline_sha256=sha256_hex(result_path.read_bytes()),
        context_generation=int(index.get("current_context_generation") or 0),
        producer="contextscout_runner",
        mode="baseline_provisioning",
    )
    write_task_index(runtime_root, intake.task_id, index)

    try:
        append_trace_event(
            trace_script=trace_script,
            runtime_root=runtime_root,
            task_id=intake.task_id,
            event_type="baseline_context_done",
            message="Baseline context provisioning emitted exploration_result for write execution.",
            artifact_files=[result_path],
        )
    except Exception:
        pass

    print(f"Generated baseline exploration_result: {result_path}")
    print(f"cache_key={cache_key}")
    print(f"usability_state={scout.usability_state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
