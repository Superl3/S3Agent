#!/usr/bin/env python3
"""Runtime execution-trace append/finalize helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

TRACE_FIELDS = [
    "task",
    "entry_agent",
    "selected_mode",
    "selected_path",
    "routing_validation_status",
    "invalid_routing_tokens",
    "tool_sequence",
    "handoff_sequence",
    "validation_sequence",
    "fingerprints",
    "compression_events",
    "fast_path_attempt",
    "packet_exhaustion",
    "result",
    "trace_status",
]

TRACE_DEFAULTS = {
    "task": "",
    "entry_agent": "",
    "selected_mode": "",
    "selected_path": "",
    "routing_validation_status": "PASS",
    "invalid_routing_tokens": "none",
    "tool_sequence": "",
    "handoff_sequence": "",
    "validation_sequence": "",
    "fingerprints": "policy_fp=na; task_fp=na; route_fp=na",
    "compression_events": "dcp_triggered=no; compress_mode=none; active_state_rehydrated=no",
    "fast_path_attempt": "status=not_attempted; budget_exempt=true; allowed_files_count=0; verifier_result=na; validation_proof=na",
    "packet_exhaustion": "none",
    "result": "PARTIAL",
    "trace_status": "partial",
}


def _latest_trace_path(root: Path) -> Path:
    return root / "runtime" / "execution_trace_latest.md"


def _archive_trace_path(root: Path) -> Path:
    return root / "runtime" / "execution_trace_archive.md"


def _parse(path: Path) -> Dict[str, str]:
    if not path.exists():
        return dict(TRACE_DEFAULTS)
    values = dict(TRACE_DEFAULTS)
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in values:
            values[key] = value.strip()
    return values


def _write(path: Path, values: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}: {values.get(key, '').strip()}" for key in TRACE_FIELDS]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append_archive(path: Path, values: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    archive_lines: List[str] = []
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            archive_lines.append("")
    else:
        archive_lines.extend(
            [
                "# Runtime Execution Trace Archive",
                "",
                "Append-only source-of-truth runtime evidence.",
                "`runtime/execution_trace_latest.md` is convenience latest view only.",
                "",
            ]
        )

    archive_lines.append("---")
    archive_lines.append(
        "archived_at_utc: "
        + datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    archive_lines.extend(
        [f"{key}: {values.get(key, '').strip()}" for key in TRACE_FIELDS]
    )

    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(archive_lines) + "\n")


def _split_handoffs(text: str) -> List[str]:
    return [token.strip().lower() for token in text.split("->") if token.strip()]


def _join_handoffs(tokens: List[str]) -> str:
    return " -> ".join(tokens)


def entry_start(root: Path, task: str, entry_agent: str) -> None:
    path = _latest_trace_path(root)
    values = dict(TRACE_DEFAULTS)
    values["task"] = task.strip()
    values["entry_agent"] = entry_agent.strip().lower()
    values["handoff_sequence"] = values["entry_agent"]
    values["trace_status"] = "partial"
    _write(path, values)


def orchestrator_decision(
    root: Path,
    selected_mode: str,
    selected_path: str,
    routing_validation_status: str = "PASS",
    invalid_routing_tokens: str = "none",
) -> None:
    path = _latest_trace_path(root)
    values = _parse(path)
    if values.get("trace_status") == "complete":
        return
    values["selected_mode"] = selected_mode.strip().upper()
    values["selected_path"] = selected_path.strip()
    values["routing_validation_status"] = routing_validation_status.strip().upper()
    values["invalid_routing_tokens"] = invalid_routing_tokens.strip().lower() or "none"
    _write(path, values)


def append_handoff(root: Path, from_agent: str, to_agent: str) -> None:
    path = _latest_trace_path(root)
    values = _parse(path)
    if values.get("trace_status") == "complete":
        return

    source = from_agent.strip().lower()
    target = to_agent.strip().lower()
    if not source or not target:
        return

    sequence = _split_handoffs(values.get("handoff_sequence", ""))
    if not sequence:
        sequence = [source, target]
    elif len(sequence) >= 2 and sequence[-2] == source and sequence[-1] == target:
        pass
    elif sequence[-1] == source:
        sequence.append(target)
    else:
        sequence.extend([source, target])

    values["handoff_sequence"] = _join_handoffs(sequence)
    _write(path, values)


def task_finalize(
    root: Path,
    result: str,
    validation_sequence: str = "",
    packet_exhaustion: str = "none",
) -> None:
    path = _latest_trace_path(root)
    values = _parse(path)
    if values.get("trace_status") == "complete":
        return
    values["result"] = result.strip().upper()
    if validation_sequence.strip():
        values["validation_sequence"] = validation_sequence.strip()
    normalized_exhaustion = packet_exhaustion.strip().lower()
    if normalized_exhaustion in {"none", "retry_pending", "exhausted"}:
        values["packet_exhaustion"] = normalized_exhaustion
    values["trace_status"] = "complete"
    _write(path, values)
    _append_archive(_archive_trace_path(root), values)
