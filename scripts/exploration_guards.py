#!/usr/bin/env python3
"""Guard helpers for focused exploration requests."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from lxml import etree
except ModuleNotFoundError as exc:  # pragma: no cover - mirrored by callers
    raise RuntimeError("lxml is required for exploration guards") from exc


NS = {"p": "urn:pxml:v1"}
BROAD_REQUEST_MARKERS = (
    "whole repo",
    "entire repo",
    "whole project",
    "entire project",
    "entire codebase",
    "whole codebase",
    "find anything relevant",
    "all relevant files",
    "all relevant symbols",
    "anything related",
    "everything relevant",
    "rediscover the repo",
    "search everything",
)
ALLOWED_REQUESTERS = {"manager", "planner", "implementer", "verifier"}


@dataclass
class RequestSnapshot:
    doc_id: str
    task_id: str
    packet_doc_id: str
    requester_agent: str
    request_kind: str
    focus_questions: List[str]
    target_hints: List[str]


@dataclass
class RequestValidation:
    errors: List[str]
    dedupe_key: str


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


def normalize_items(items: Sequence[str]) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for item in items:
        value = item.strip()
        if not value:
            continue
        key = re.sub(r"\s+", " ", value).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(value)
    return normalized


def is_concrete_hint(value: str) -> bool:
    hint = value.strip()
    if not hint:
        return False
    lowered = hint.lower()
    if "/" in hint or "\\" in hint:
        return True
    if re.search(r"\.[A-Za-z0-9]{1,8}$", hint):
        return True
    if hint.startswith("@"):
        return True
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{2,}", hint):
        return True
    if "test" in lowered or "spec" in lowered:
        return True
    return False


def looks_broad(value: str) -> bool:
    lowered = re.sub(r"\s+", " ", value.strip().lower())
    return any(marker in lowered for marker in BROAD_REQUEST_MARKERS)


def build_dedupe_key(
    packet_doc_id: str,
    requester_agent: str,
    request_kind: str,
    focus_questions: Sequence[str],
    target_hints: Sequence[str],
) -> str:
    payload = {
        "packet_doc_id": packet_doc_id,
        "requester_agent": requester_agent,
        "request_kind": request_kind,
        "focus_questions": [item.lower() for item in normalize_items(focus_questions)],
        "target_hints": [item.lower() for item in normalize_items(target_hints)],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def discover_pxml_files(path: Path) -> List[Path]:
    if not path.exists():
        return []
    files = [candidate for candidate in path.rglob("*.pxml") if candidate.is_file()]
    files.sort()
    return files


def parse_request_snapshot(path: Path) -> Optional[RequestSnapshot]:
    try:
        tree = etree.parse(str(path))
    except (OSError, etree.XMLSyntaxError):
        return None
    if text_at(tree, "/p:pxml/p:meta/p:doc_class") != "exploration_request":
        return None
    doc_id = text_at(tree, "/p:pxml/p:meta/p:doc_id")
    task_id = text_at(tree, "/p:pxml/p:meta/p:task_id")
    requester_agent = text_at(tree, "/p:pxml/p:payload/p:requester_agent")
    request_kind = text_at(tree, "/p:pxml/p:payload/p:request_kind")
    packet_ref = text_at(
        tree, "/p:pxml/p:refs/p:ref[p:relation='request_packet']/p:doc_id"
    )
    focus_questions = [
        item.strip()
        for item in tree.xpath(
            "/p:pxml/p:payload/p:focus_questions/p:item/text()", namespaces=NS
        )
        if item and item.strip()
    ]
    target_hints = [
        item.strip()
        for item in tree.xpath(
            "/p:pxml/p:payload/p:target_hints/p:item/text()", namespaces=NS
        )
        if item and item.strip()
    ]
    if not all([doc_id, task_id, requester_agent, request_kind, packet_ref]):
        return None
    return RequestSnapshot(
        doc_id=doc_id,
        task_id=task_id,
        packet_doc_id=packet_ref,
        requester_agent=requester_agent,
        request_kind=request_kind,
        focus_questions=focus_questions,
        target_hints=target_hints,
    )


def request_resolution_by_doc_id(results_dir: Path) -> Dict[str, List[str]]:
    resolved: Dict[str, List[str]] = {}
    for path in discover_pxml_files(results_dir):
        try:
            tree = etree.parse(str(path))
        except (OSError, etree.XMLSyntaxError):
            continue
        if text_at(tree, "/p:pxml/p:meta/p:doc_class") != "exploration_result":
            continue
        request_id = text_at(
            tree, "/p:pxml/p:refs/p:ref[p:relation='request']/p:doc_id"
        )
        if not request_id:
            continue
        completion_state = (
            text_at(tree, "/p:pxml/p:payload/p:completion_state") or "partial"
        )
        resolved.setdefault(request_id, []).append(completion_state)
    return resolved


def active_requests_for_task(runtime_root: Path, task_id: str) -> List[RequestSnapshot]:
    requests_dir = runtime_root / "exploration" / "requests"
    results_dir = runtime_root / "exploration" / "results"
    resolved = request_resolution_by_doc_id(results_dir)
    active: List[RequestSnapshot] = []
    for path in discover_pxml_files(requests_dir):
        snapshot = parse_request_snapshot(path)
        if snapshot is None or snapshot.task_id != task_id:
            continue
        if snapshot.doc_id in resolved:
            continue
        active.append(snapshot)
    return active


def exhausted_attempts_for_key(
    runtime_root: Path, task_id: str, dedupe_key: str
) -> bool:
    requests_dir = runtime_root / "exploration" / "requests"
    results_dir = runtime_root / "exploration" / "results"
    request_keys: Dict[str, str] = {}
    for path in discover_pxml_files(requests_dir):
        snapshot = parse_request_snapshot(path)
        if snapshot is None or snapshot.task_id != task_id:
            continue
        request_keys[snapshot.doc_id] = build_dedupe_key(
            snapshot.packet_doc_id,
            snapshot.requester_agent,
            snapshot.request_kind,
            snapshot.focus_questions,
            snapshot.target_hints,
        )

    unsuccessful = 0
    for path in discover_pxml_files(results_dir):
        try:
            tree = etree.parse(str(path))
        except (OSError, etree.XMLSyntaxError):
            continue
        if text_at(tree, "/p:pxml/p:meta/p:doc_class") != "exploration_result":
            continue
        if text_at(tree, "/p:pxml/p:meta/p:task_id") != task_id:
            continue
        request_id = text_at(
            tree, "/p:pxml/p:refs/p:ref[p:relation='request']/p:doc_id"
        )
        if not request_id or request_keys.get(request_id) != dedupe_key:
            continue
        completion_state = (
            text_at(tree, "/p:pxml/p:payload/p:completion_state") or "partial"
        )
        if completion_state in {"partial", "blocked", "failed"}:
            unsuccessful += 1
    return unsuccessful >= 2


def validate_request_shape(
    packet_doc_id: str,
    requester_agent: str,
    request_kind: str,
    focus_questions: Sequence[str],
    target_hints: Sequence[str],
) -> RequestValidation:
    questions = normalize_items(focus_questions)
    hints = normalize_items(target_hints)
    errors: List[str] = []
    if requester_agent not in ALLOWED_REQUESTERS:
        errors.append(f"unsupported requester_agent={requester_agent}")
    if len(questions) < 1 or len(questions) > 3:
        errors.append("focus_questions must contain 1 to 3 items")
    if len(hints) < 1 or len(hints) > 5:
        errors.append("target_hints must contain 1 to 5 items")
    if hints and not any(is_concrete_hint(item) for item in hints):
        errors.append("at least one target_hint must be concrete")
    if any(looks_broad(item) for item in questions + hints):
        errors.append("broad rediscovery requests are not allowed")
    dedupe_key = build_dedupe_key(
        packet_doc_id=packet_doc_id,
        requester_agent=requester_agent,
        request_kind=request_kind,
        focus_questions=questions,
        target_hints=hints,
    )
    return RequestValidation(errors=errors, dedupe_key=dedupe_key)


def runtime_guard_errors(
    runtime_root: Path,
    task_id: str,
    dedupe_key: str,
    packet_doc_id: str,
) -> List[str]:
    errors: List[str] = []
    for snapshot in active_requests_for_task(runtime_root, task_id):
        existing_key = build_dedupe_key(
            snapshot.packet_doc_id,
            snapshot.requester_agent,
            snapshot.request_kind,
            snapshot.focus_questions,
            snapshot.target_hints,
        )
        if existing_key == dedupe_key:
            errors.append(
                "duplicate active exploration_request for same packet and focus"
            )
            break
    if exhausted_attempts_for_key(runtime_root, task_id, dedupe_key):
        errors.append("request retry budget exhausted for the same focus")
    if not packet_doc_id:
        errors.append("request_packet ref is required")
    return errors


def collect_guard_errors(
    runtime_root: Path,
    task_id: str,
    packet_doc_id: str,
    requester_agent: str,
    request_kind: str,
    focus_questions: Sequence[str],
    target_hints: Sequence[str],
) -> Tuple[List[str], str]:
    validation = validate_request_shape(
        packet_doc_id=packet_doc_id,
        requester_agent=requester_agent,
        request_kind=request_kind,
        focus_questions=focus_questions,
        target_hints=target_hints,
    )
    errors = list(validation.errors)
    errors.extend(
        runtime_guard_errors(
            runtime_root=runtime_root,
            task_id=task_id,
            dedupe_key=validation.dedupe_key,
            packet_doc_id=packet_doc_id,
        )
    )
    return errors, validation.dedupe_key
