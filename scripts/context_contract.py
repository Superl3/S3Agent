#!/usr/bin/env python3
"""Shared baseline context contract helpers."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Sequence

try:
    from lxml import etree
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError("lxml is required for context contract helpers") from exc


NS = {"p": "urn:pxml:v1"}


@dataclass(frozen=True)
class ContextPolicy:
    baseline_required: bool
    baseline_doc_id: Optional[str]
    baseline_sha256: Optional[str]
    baseline_scope: Optional[str]
    context_lock_sha256: Optional[str]
    packet_generation: int
    context_generation: int
    context_producer: Optional[str]
    context_mode: Optional[str]
    baseline_usability_state: Optional[str]
    baseline_confidence: Optional[str]


@dataclass(frozen=True)
class ExplorationBundle:
    path: Path
    doc_id: str
    task_id: str
    content_sha256: str
    exploration_scope: Optional[str]
    actionability: Optional[str]
    context_producer: Optional[str]
    context_mode: Optional[str]
    usability_state: Optional[str]
    confidence: Optional[str]
    key_findings: List[str]
    evidence_paths: List[str]
    open_questions: List[str]
    recommended_next_actions: List[str]
    candidate_files: List[str]
    target_files: List[str]
    evidence_count: int
    open_questions_count: int


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


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def sanitize(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9._-]", "_", lowered)
    lowered = re.sub(r"_+", "_", lowered).strip("_")
    return lowered or "id"


def sha256_hex(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def discover_pxml_files(path: Path) -> List[Path]:
    if not path.exists():
        return []
    files = [candidate for candidate in path.rglob("*.pxml") if candidate.is_file()]
    files.sort()
    return files


def latest_task_artifact(
    runtime_root: Path, task_id: str, suffix: str
) -> Optional[Path]:
    candidate = runtime_root / "latest" / f"{sanitize(task_id)}_{suffix}.pxml"
    if candidate.exists():
        return candidate
    return None


def load_task_index(runtime_root: Path, task_id: str) -> Dict[str, object]:
    index_path = runtime_root / "index" / "tasks" / f"{sanitize(task_id)}.json"
    if not index_path.exists():
        return {"task_id": task_id}
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["task_id"] = task_id
    return payload


def write_task_index(
    runtime_root: Path, task_id: str, payload: Dict[str, object]
) -> None:
    index_path = runtime_root / "index" / "tasks" / f"{sanitize(task_id)}.json"
    ensure_dir(index_path.parent)
    payload = dict(payload)
    payload["task_id"] = task_id
    index_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def find_artifact_by_doc_id(runtime_root: Path, doc_id: str) -> Optional[Path]:
    target_name = f"{doc_id}.pxml"
    for path in discover_pxml_files(runtime_root):
        if path.name == target_name:
            return path
    return None


def normalize_target_path(value: str) -> Optional[str]:
    raw = value.replace("\\", "/").strip()
    if not raw:
        return None
    if ":" in raw:
        raw = raw.split(":", 1)[0].strip()
    raw = re.sub(r"/+", "/", raw)
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw:
        return None
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts:
        return None
    return str(pure)


def normalize_target_paths(values: Sequence[str]) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for value in values:
        item = normalize_target_path(value)
        if item is None or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def compute_context_lock_sha256(
    *,
    baseline_doc_id: str,
    baseline_sha256: str,
    context_generation: int,
    producer: str,
    mode: str,
) -> str:
    payload = {
        "baseline_doc_id": baseline_doc_id,
        "baseline_sha256": baseline_sha256,
        "context_generation": context_generation,
        "producer": producer,
        "mode": mode,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_hex(encoded)


def parse_context_policy(tree: etree._ElementTree) -> ContextPolicy:
    baseline_doc_id = text_at(
        tree, "/p:pxml/p:payload/p:context_policy/p:baseline_doc_id"
    )
    if baseline_doc_id is None:
        baseline_doc_id = text_at(
            tree, "/p:pxml/p:payload/p:exploration_notes_ref/p:doc_id"
        )
    packet_generation_text = text_at(
        tree, "/p:pxml/p:payload/p:context_policy/p:packet_generation"
    )
    context_generation_text = text_at(
        tree, "/p:pxml/p:payload/p:context_policy/p:context_generation"
    )
    baseline_required_text = text_at(
        tree, "/p:pxml/p:payload/p:context_policy/p:baseline_required"
    )
    return ContextPolicy(
        baseline_required=(baseline_required_text or "false").lower() == "true",
        baseline_doc_id=baseline_doc_id,
        baseline_sha256=text_at(
            tree, "/p:pxml/p:payload/p:context_policy/p:baseline_sha256"
        ),
        baseline_scope=text_at(
            tree, "/p:pxml/p:payload/p:context_policy/p:baseline_scope"
        ),
        context_lock_sha256=text_at(
            tree, "/p:pxml/p:payload/p:context_policy/p:context_lock_sha256"
        ),
        packet_generation=(
            int(packet_generation_text)
            if packet_generation_text and packet_generation_text.isdigit()
            else 0
        ),
        context_generation=(
            int(context_generation_text)
            if context_generation_text and context_generation_text.isdigit()
            else 0
        ),
        context_producer=text_at(
            tree, "/p:pxml/p:payload/p:context_policy/p:context_producer"
        ),
        context_mode=text_at(tree, "/p:pxml/p:payload/p:context_policy/p:context_mode"),
        baseline_usability_state=text_at(
            tree,
            "/p:pxml/p:payload/p:context_policy/p:baseline_usability_state",
        ),
        baseline_confidence=text_at(
            tree, "/p:pxml/p:payload/p:context_policy/p:baseline_confidence"
        ),
    )


def load_exploration_bundle(path: Path) -> ExplorationBundle:
    tree = etree.parse(str(path))
    doc_id = text_at(tree, "/p:pxml/p:meta/p:doc_id")
    task_id = text_at(tree, "/p:pxml/p:meta/p:task_id")
    content_sha256 = text_at(tree, "/p:pxml/p:integrity/p:content_sha256")
    if not doc_id or not task_id or not content_sha256:
        raise ValueError(
            f"exploration_result missing required meta/integrity fields: {path}"
        )
    key_findings = [
        item.strip()
        for item in tree.xpath(
            "/p:pxml/p:payload/p:key_findings/p:item/text()", namespaces=NS
        )
        if item and item.strip()
    ]
    evidence_paths = [
        item.strip()
        for item in tree.xpath(
            "/p:pxml/p:payload/p:evidence_items/p:evidence/p:path/text()",
            namespaces=NS,
        )
        if item and item.strip()
    ]
    open_questions = [
        item.strip()
        for item in tree.xpath(
            "/p:pxml/p:payload/p:open_questions/p:item/text()", namespaces=NS
        )
        if item and item.strip()
    ]
    next_actions = [
        item.strip()
        for item in tree.xpath(
            "/p:pxml/p:payload/p:recommended_next_actions/p:item/text()",
            namespaces=NS,
        )
        if item and item.strip()
    ]
    candidate_files = [
        item.strip()
        for item in tree.xpath(
            "/p:pxml/p:payload/p:candidate_files/p:item/text()", namespaces=NS
        )
        if item and item.strip()
    ]
    target_files = [
        item.strip()
        for item in tree.xpath(
            "/p:pxml/p:payload/p:target_files/p:item/text()", namespaces=NS
        )
        if item and item.strip()
    ]
    evidence_count_text = text_at(tree, "/p:pxml/p:payload/p:evidence_count")
    open_questions_count_text = text_at(
        tree, "/p:pxml/p:payload/p:open_questions_count"
    )
    return ExplorationBundle(
        path=path,
        doc_id=doc_id,
        task_id=task_id,
        content_sha256=content_sha256,
        exploration_scope=text_at(tree, "/p:pxml/p:payload/p:exploration_scope"),
        actionability=text_at(tree, "/p:pxml/p:payload/p:actionability"),
        context_producer=text_at(tree, "/p:pxml/p:payload/p:context_producer"),
        context_mode=text_at(tree, "/p:pxml/p:payload/p:context_mode"),
        usability_state=text_at(tree, "/p:pxml/p:payload/p:usability_state"),
        confidence=text_at(tree, "/p:pxml/p:payload/p:confidence"),
        key_findings=key_findings,
        evidence_paths=evidence_paths,
        open_questions=open_questions,
        recommended_next_actions=next_actions,
        candidate_files=candidate_files,
        target_files=target_files,
        evidence_count=(
            int(evidence_count_text)
            if evidence_count_text and evidence_count_text.isdigit()
            else len(evidence_paths)
        ),
        open_questions_count=(
            int(open_questions_count_text)
            if open_questions_count_text and open_questions_count_text.isdigit()
            else len(open_questions)
        ),
    )


def resolve_baseline_bundle(
    runtime_root: Path,
    *,
    task_id: Optional[str] = None,
    baseline_doc_id: Optional[str] = None,
) -> Optional[ExplorationBundle]:
    path: Optional[Path] = None
    if baseline_doc_id:
        path = find_artifact_by_doc_id(runtime_root, baseline_doc_id)
    elif task_id:
        index = load_task_index(runtime_root, task_id)
        rel = index.get("latest_baseline_exploration_result")
        if isinstance(rel, str) and rel.strip():
            candidate = runtime_root / rel
            if candidate.exists():
                path = candidate
        if path is None:
            path = latest_task_artifact(
                runtime_root, task_id, "baseline_exploration_result"
            )
    if path is None or not path.exists():
        return None
    return load_exploration_bundle(path)


def promote_exploration_result(
    *,
    runtime_root: Path,
    task_id: str,
    doc_id: str,
    result_path: Path,
    exploration_scope: str,
    cache_key: Optional[str] = None,
) -> None:
    tasks_dir = runtime_root / "index" / "tasks"
    artifacts_dir = runtime_root / "index" / "artifacts"
    ensure_dir(tasks_dir)
    ensure_dir(artifacts_dir)

    task_index = load_task_index(runtime_root, task_id)
    rel_path = str(result_path.relative_to(runtime_root)).replace("\\", "/")
    task_index["latest_exploration_result"] = rel_path
    if exploration_scope == "baseline":
        task_index["latest_baseline_exploration_result"] = rel_path
        task_index["current_manager_baseline_doc_id"] = doc_id
        if cache_key:
            task_index["baseline_cache_key"] = cache_key
    else:
        task_index["latest_focused_exploration_result"] = rel_path
    write_task_index(runtime_root, task_id, task_index)

    artifact_index = {
        "doc_id": doc_id,
        "doc_class": "exploration_result",
        "task_id": task_id,
        "path": rel_path,
    }
    (artifacts_dir / f"{doc_id}.json").write_text(
        json.dumps(artifact_index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    latest_dir = runtime_root / "latest"
    ensure_dir(latest_dir)
    alias_path = latest_dir / f"{sanitize(task_id)}_exploration_result.pxml"
    shutil.copy2(result_path, alias_path)
    if exploration_scope == "baseline":
        shutil.copy2(
            result_path,
            latest_dir / f"{sanitize(task_id)}_baseline_exploration_result.pxml",
        )
    else:
        shutil.copy2(
            result_path,
            latest_dir / f"{sanitize(task_id)}_focused_exploration_result.pxml",
        )


def append_context_access_log(
    *,
    runtime_root: Path,
    task_id: str,
    actor: str,
    access_type: str,
    reason: str,
    packet_doc_id: str,
    baseline_doc_id: Optional[str],
    packet_generation: int,
    context_generation: int,
    file_path: Optional[str] = None,
) -> None:
    log_path = (
        runtime_root / "context_access" / "by_task" / f"{sanitize(task_id)}.jsonl"
    )
    ensure_dir(log_path.parent)
    entry = {
        "task_id": task_id,
        "actor": actor,
        "access_type": access_type,
        "reason": reason,
        "packet_doc_id": packet_doc_id,
        "baseline_doc_id": baseline_doc_id,
        "packet_generation": packet_generation,
        "context_generation": context_generation,
    }
    if file_path:
        entry["file_path"] = file_path
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
