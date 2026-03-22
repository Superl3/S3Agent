#!/usr/bin/env python3
"""Batch 3 verification runner.

Reads an execution_packet artifact and emits verification_result artifacts.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

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
class AcceptanceCheck:
    check_id: str
    check_type: str
    command: str
    pass_condition: str
    deterministic: bool
    timeout_sec: int


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
    checks: List[AcceptanceCheck]


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


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def sanitize(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9._-]", "_", lowered)
    lowered = re.sub(r"_+", "_", lowered).strip("_")
    return lowered or "id"


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
    packet_lock_hash = text_at(tree, "/p:pxml/p:payload/p:acceptance_lock_hash")

    required = {
        "doc_id": doc_id,
        "task_id": task_id,
        "run_id": run_id,
        "sequence": seq_text,
        "created_at": created_at,
        "content_sha256": content_sha,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise ValueError(
            f"execution_packet missing required meta/integrity fields: {', '.join(missing)}"
        )

    check_nodes = tree.xpath(
        "/p:pxml/p:payload/p:acceptance_checks/p:check", namespaces=XPATH_NS
    )
    if not check_nodes:
        raise ValueError("execution_packet has no acceptance_checks entries")

    checks: List[AcceptanceCheck] = []
    for node in check_nodes:
        node_tree = etree.ElementTree(node)
        check_id = text_at(node_tree, "./p:check_id")
        check_type = text_at(node_tree, "./p:check_type")
        command = text_at(node_tree, "./p:command")
        pass_condition = text_at(node_tree, "./p:pass_condition")
        deterministic_text = text_at(node_tree, "./p:deterministic")
        timeout_text = text_at(node_tree, "./p:timeout_sec")
        if (
            check_id is None
            or check_type is None
            or command is None
            or pass_condition is None
            or deterministic_text is None
            or timeout_text is None
        ):
            raise ValueError("acceptance_checks entry is missing required fields")
        checks.append(
            AcceptanceCheck(
                check_id=check_id,
                check_type=check_type,
                command=command,
                pass_condition=pass_condition,
                deterministic=deterministic_text.lower() == "true",
                timeout_sec=int(timeout_text),
            )
        )

    assert doc_id is not None
    assert task_id is not None
    assert run_id is not None
    assert seq_text is not None
    assert created_at is not None
    assert content_sha is not None

    if packet_lock_hash is None:
        normalized_checks = [
            {
                "check_id": item.check_id,
                "check_type": item.check_type,
                "command": item.command,
                "pass_condition": item.pass_condition,
                "deterministic": item.deterministic,
                "timeout_sec": item.timeout_sec,
            }
            for item in checks
        ]
        packet_lock_hash = hashlib.sha256(
            json.dumps(normalized_checks, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()

    return PacketInfo(
        path=path,
        doc_id=doc_id,
        task_id=task_id,
        run_id=run_id,
        sequence=int(seq_text),
        created_at=created_at,
        content_sha256=content_sha,
        acceptance_lock_hash=packet_lock_hash,
        checks=checks,
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


def eval_pass_condition(pass_condition: str, returncode: int) -> bool:
    condition = pass_condition.strip()
    eq_match = re.fullmatch(r"exit_code==(-?\d+)", condition)
    ne_match = re.fullmatch(r"exit_code!=(-?\d+)", condition)
    if eq_match:
        return returncode == int(eq_match.group(1))
    if ne_match:
        return returncode != int(ne_match.group(1))
    # Default conservative behavior
    return returncode == 0


def run_check(
    check: AcceptanceCheck,
    logs_dir: Path,
    dry_run: bool,
) -> Tuple[Dict[str, object], Optional[str], Dict[str, str]]:
    ensure_dir(logs_dir)
    started = time.monotonic()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = logs_dir / f"{sanitize(check.check_id)}_{timestamp}.log"

    if dry_run:
        result = "skipped"
        duration_ms = int((time.monotonic() - started) * 1000)
        log_content = (
            f"mode=dry_run\n"
            f"check_id={check.check_id}\n"
            f"command={check.command}\n"
            f"result={result}\n"
            f"reason=verification_runner dry-run mode enabled\n"
        )
        log_path.write_text(log_content, encoding="utf-8")
        test_record = {
            "check_id": check.check_id,
            "check_type": check.check_type,
            "command": check.command,
            "result": result,
            "duration_ms": duration_ms,
            "evidence_ref": str(log_path),
        }
        risk = {
            "severity": "medium",
            "description": f"Check {check.check_id} was skipped in dry-run mode.",
            "mitigation": "Run verification_runner without --dry-run for executable verdict.",
        }
        return test_record, "dry_run_skipped", risk

    try:
        completed = subprocess.run(
            check.command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=check.timeout_sec,
            check=False,
        )
        passed = eval_pass_condition(check.pass_condition, completed.returncode)
        result = "pass" if passed else "fail"
        error_reason = None
        stderr_text = completed.stderr or ""
        stdout_text = completed.stdout or ""
        returncode = completed.returncode
    except subprocess.TimeoutExpired as exc:
        result = "error"
        error_reason = f"timeout_after_{check.timeout_sec}s"
        stdout_text = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr_text = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        returncode = None
    except OSError as exc:
        result = "error"
        error_reason = f"os_error:{exc.__class__.__name__}"
        stdout_text = ""
        stderr_text = str(exc)
        returncode = None

    duration_ms = int((time.monotonic() - started) * 1000)
    log_content = (
        f"check_id={check.check_id}\n"
        f"check_type={check.check_type}\n"
        f"command={check.command}\n"
        f"pass_condition={check.pass_condition}\n"
        f"timeout_sec={check.timeout_sec}\n"
        f"returncode={returncode}\n"
        f"result={result}\n"
        f"error_reason={error_reason or ''}\n"
        "--- stdout ---\n"
        f"{stdout_text}\n"
        "--- stderr ---\n"
        f"{stderr_text}\n"
    )
    log_path.write_text(log_content, encoding="utf-8")

    test_record = {
        "check_id": check.check_id,
        "check_type": check.check_type,
        "command": check.command,
        "result": result,
        "duration_ms": duration_ms,
        "evidence_ref": str(log_path),
    }

    if result == "pass":
        risk = {
            "severity": "low",
            "description": f"Check {check.check_id} passed.",
            "mitigation": "None required.",
        }
    elif result == "fail":
        risk = {
            "severity": "high",
            "description": f"Check {check.check_id} failed pass_condition {check.pass_condition}.",
            "mitigation": "Investigate failure logs and re-run check after fix.",
        }
    else:
        risk = {
            "severity": "high",
            "description": f"Check {check.check_id} errored: {error_reason or 'unknown'}.",
            "mitigation": "Fix environment or command execution issue and re-run verification.",
        }

    return test_record, error_reason, risk


def summarize_outcomes(tests: Sequence[Dict[str, object]]) -> Dict[str, int]:
    summary = {"passed": 0, "failed": 0, "errored": 0, "skipped": 0}
    for item in tests:
        result = item.get("result")
        if result == "pass":
            summary["passed"] += 1
        elif result == "fail":
            summary["failed"] += 1
        elif result == "error":
            summary["errored"] += 1
        elif result == "skipped":
            summary["skipped"] += 1
    return summary


def final_verdict(
    outcomes: Dict[str, int], unverified_areas: Sequence[str]
) -> Tuple[str, str]:
    if outcomes["failed"] > 0:
        return "fail", "One or more acceptance checks failed."
    if outcomes["errored"] > 0 or outcomes["skipped"] > 0:
        return (
            "inconclusive",
            "Checks errored or were skipped; verification is incomplete.",
        )
    if any(item.strip().lower() != "none" for item in unverified_areas):
        return "inconclusive", "Unverified areas remain unresolved."
    if outcomes["passed"] > 0:
        return "pass", "All declared acceptance checks passed deterministically."
    return "inconclusive", "No executable checks produced a decisive outcome."


def make_result_doc_id(task_id: str, packet_sequence: int) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    task_token = sanitize(task_id)[:20]
    suffix = hashlib.sha256(
        f"{task_id}:{packet_sequence}:{stamp}".encode("utf-8")
    ).hexdigest()[:8]
    doc_id = f"doc_verifres_{task_token}_{packet_sequence:04d}_{stamp}_{suffix}"
    if re.fullmatch(r"doc_[a-z0-9][a-z0-9._-]{5,63}", doc_id):
        return doc_id
    return f"doc_verifres_{packet_sequence:04d}_{suffix}"


def build_verification_result(
    packet: PacketInfo,
    doc_id: str,
    tests: Sequence[Dict[str, object]],
    outcomes: Dict[str, int],
    unverified_areas: Sequence[str],
    residual_risks: Sequence[Dict[str, str]],
    verdict: str,
    verdict_reason: str,
    logs_ref: Optional[str],
    environment_fingerprint: str,
    review_sidecar_ref: Optional[Tuple[str, str]],
    acceptance_lock_sha256: str,
    verify_phase: Optional[str],
) -> etree._ElementTree:
    root = etree.Element(q("pxml"), nsmap=NSMAP)

    created_at = now_iso()
    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = doc_id
    etree.SubElement(meta, q("doc_class")).text = "verification_result"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = packet.task_id
    etree.SubElement(meta, q("run_id")).text = packet.run_id
    etree.SubElement(meta, q("sequence")).text = str(packet.sequence + 1)
    etree.SubElement(meta, q("writer_agent")).text = "verifier"
    etree.SubElement(meta, q("created_at")).text = created_at

    refs = etree.SubElement(root, q("refs"))
    packet_ref = etree.SubElement(refs, q("ref"))
    etree.SubElement(packet_ref, q("doc_id")).text = packet.doc_id
    etree.SubElement(packet_ref, q("doc_class")).text = "execution_packet"
    etree.SubElement(packet_ref, q("relation")).text = "verification_target"
    if review_sidecar_ref is not None:
        review_ref = etree.SubElement(refs, q("ref"))
        etree.SubElement(review_ref, q("doc_id")).text = review_sidecar_ref[0]
        etree.SubElement(review_ref, q("doc_class")).text = review_sidecar_ref[1]
        etree.SubElement(review_ref, q("relation")).text = "review_context"

    payload = etree.SubElement(root, q("payload"))
    tests_run = etree.SubElement(payload, q("tests_run"))
    for test in tests:
        test_node = etree.SubElement(tests_run, q("test"))
        etree.SubElement(test_node, q("check_id")).text = str(test["check_id"])
        etree.SubElement(test_node, q("check_type")).text = str(test["check_type"])
        etree.SubElement(test_node, q("command")).text = str(test["command"])
        etree.SubElement(test_node, q("result")).text = str(test["result"])
        etree.SubElement(test_node, q("duration_ms")).text = str(test["duration_ms"])
        etree.SubElement(test_node, q("evidence_ref")).text = str(test["evidence_ref"])

    outcomes_node = etree.SubElement(payload, q("outcomes"))
    etree.SubElement(outcomes_node, q("passed")).text = str(outcomes["passed"])
    etree.SubElement(outcomes_node, q("failed")).text = str(outcomes["failed"])
    etree.SubElement(outcomes_node, q("errored")).text = str(outcomes["errored"])
    etree.SubElement(outcomes_node, q("skipped")).text = str(outcomes["skipped"])

    unverified = etree.SubElement(payload, q("unverified_areas"))
    for area in unverified_areas:
        etree.SubElement(unverified, q("item")).text = area

    risks_node = etree.SubElement(payload, q("residual_risks"))
    for risk in residual_risks:
        risk_node = etree.SubElement(risks_node, q("risk"))
        etree.SubElement(risk_node, q("severity")).text = risk["severity"]
        etree.SubElement(risk_node, q("description")).text = risk["description"]
        etree.SubElement(risk_node, q("mitigation")).text = risk["mitigation"]

    etree.SubElement(payload, q("acceptance_lock_sha256")).text = acceptance_lock_sha256
    if verify_phase is not None:
        etree.SubElement(payload, q("verify_phase")).text = verify_phase
    etree.SubElement(payload, q("final_verdict")).text = verdict
    etree.SubElement(payload, q("verdict_reason")).text = verdict_reason
    if logs_ref:
        etree.SubElement(payload, q("logs_ref")).text = logs_ref
    etree.SubElement(
        payload, q("environment_fingerprint")
    ).text = environment_fingerprint

    integrity = etree.SubElement(root, q("integrity"))
    content_hash = compute_content_hash(meta, refs, payload)
    etree.SubElement(integrity, q("content_sha256")).text = content_hash
    etree.SubElement(integrity, q("parent_sha256")).text = packet.content_sha256

    return etree.ElementTree(root)


def write_xml(tree: etree._ElementTree, path: Path) -> None:
    ensure_dir(path.parent)
    tree.write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True)


def update_indexes(
    runtime_root: Path, task_id: str, doc_id: str, result_path: Path
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
    current["latest_verification_result"] = str(result_path.relative_to(runtime_root))
    current["updated_at"] = now_iso()
    task_index_path.write_text(
        json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    artifact_index = {
        "doc_id": doc_id,
        "doc_class": "verification_result",
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
    with tempfile.TemporaryDirectory(prefix="pxml_verify_validate_") as temp_dir:
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


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Generate verification_result from execution_packet."
    )
    parser.add_argument(
        "--packet", required=True, type=Path, help="Execution packet path."
    )
    parser.add_argument(
        "--review-sidecar",
        type=Path,
        default=None,
        help="Optional review_sidecar context artifact path.",
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
        "--trace-script",
        type=Path,
        default=repo_root / "scripts" / "trace_appender.py",
        help="Trace appender script path.",
    )
    parser.add_argument(
        "--append-trace",
        action="store_true",
        help="Append verify_done event after successful result generation.",
    )
    parser.add_argument(
        "--verify-phase",
        choices=["lane", "post_implement", "unknown_legacy"],
        default="unknown_legacy",
        help="verify_phase metadata for verification_result and optional verify_done trace event.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not execute commands, mark checks as skipped.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip post-generation validator call.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    packet_path = args.packet.resolve()
    runtime_root = args.runtime_root.resolve()
    validator_path = args.validator.resolve()
    trace_script_path = args.trace_script.resolve()

    if not packet_path.exists():
        print(f"ERROR: execution_packet not found: {packet_path}", file=sys.stderr)
        return 2

    if args.review_sidecar is not None and not args.review_sidecar.resolve().exists():
        print(
            f"ERROR: review_sidecar not found: {args.review_sidecar}", file=sys.stderr
        )
        return 2

    try:
        packet = parse_packet(packet_path)
    except Exception as exc:
        print(f"ERROR: failed to parse execution_packet: {exc}", file=sys.stderr)
        return 2

    results_dir = runtime_root / "verification" / "results"
    logs_dir = runtime_root / "verification" / "logs"
    ensure_dir(results_dir)
    ensure_dir(logs_dir)

    tests: List[Dict[str, object]] = []
    unverified_areas: List[str] = []
    residual_risks: List[Dict[str, str]] = []
    shared_log_refs: List[str] = []

    for check in packet.checks:
        test_record, error_reason, risk = run_check(
            check, logs_dir=logs_dir, dry_run=args.dry_run
        )
        tests.append(test_record)
        shared_log_refs.append(str(test_record["evidence_ref"]))
        residual_risks.append(risk)
        if test_record["result"] in {"error", "skipped"}:
            reason = error_reason or f"{test_record['result']}:{check.check_id}"
            unverified_areas.append(f"{check.check_id}:{reason}")

    if not unverified_areas:
        unverified_areas = ["none"]

    outcomes = summarize_outcomes(tests)
    verdict, verdict_reason = final_verdict(outcomes, unverified_areas)

    if verdict == "pass":
        residual_risks = [
            {
                "severity": "low",
                "description": "Declared acceptance checks passed.",
                "mitigation": "Monitor regression in normal CI pipeline.",
            }
        ]

    environment_fingerprint = f"python={platform.python_version()};platform={platform.platform()};dry_run={str(args.dry_run).lower()}"
    doc_id = make_result_doc_id(packet.task_id, packet.sequence)

    review_ref: Optional[Tuple[str, str]] = None
    context_files: List[Path] = [packet_path]
    if args.review_sidecar is not None:
        review_path = args.review_sidecar.resolve()
        review_tree = etree.parse(str(review_path))
        review_doc_id = text_at(review_tree, "/p:pxml/p:meta/p:doc_id")
        review_doc_class = text_at(review_tree, "/p:pxml/p:meta/p:doc_class")
        if review_doc_id and review_doc_class:
            review_ref = (review_doc_id, review_doc_class)
        context_files.append(review_path)

    logs_ref = shared_log_refs[-1] if shared_log_refs else None
    result_tree = build_verification_result(
        packet=packet,
        doc_id=doc_id,
        tests=tests,
        outcomes=outcomes,
        unverified_areas=unverified_areas,
        residual_risks=residual_risks,
        verdict=verdict,
        verdict_reason=verdict_reason,
        logs_ref=logs_ref,
        environment_fingerprint=environment_fingerprint,
        review_sidecar_ref=review_ref,
        acceptance_lock_sha256=packet.acceptance_lock_hash,
        verify_phase=args.verify_phase,
    )

    result_path = results_dir / f"{doc_id}.pxml"
    write_xml(result_tree, result_path)

    latest_path = (
        runtime_root / "latest" / f"{sanitize(packet.task_id)}_verification_result.pxml"
    )
    ensure_dir(latest_path.parent)
    shutil.copy2(result_path, latest_path)
    update_indexes(runtime_root, packet.task_id, doc_id, result_path)

    if not args.skip_validate:
        if not validator_path.exists():
            print(f"ERROR: validator not found: {validator_path}", file=sys.stderr)
            return 2
        try:
            run_validation(validator_path, result_path, context_files=context_files)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    if args.append_trace:
        if not trace_script_path.exists():
            print(
                f"ERROR: trace script not found: {trace_script_path}", file=sys.stderr
            )
            return 2
        cmd = [
            sys.executable,
            str(trace_script_path),
            "--task-id",
            packet.task_id,
            "--event-type",
            "verify_done",
            "--actor",
            "verifier",
            "--message",
            f"Verification completed with verdict {verdict}.",
            "--lineage-lock-sha256",
            packet.acceptance_lock_hash,
            "--verify-phase",
            args.verify_phase,
            "--artifact-file",
            str(result_path),
            "--runtime-root",
            str(runtime_root),
        ]
        trace_proc = subprocess.run(cmd, check=False)
        if trace_proc.returncode != 0:
            print(
                "ERROR: verification_result generated but trace append failed",
                file=sys.stderr,
            )
            return 1

    print(f"Generated verification_result: {result_path}")
    print(f"Final verdict: {verdict}")
    print(f"Verdict reason: {verdict_reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
