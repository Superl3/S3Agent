from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional

import pytest
from lxml import etree

NS = {"p": "urn:pxml:v1"}

TASK_ARTIFACT_DIRS = [
    "inbox/task_intake",
    "packets/manager_route",
    "packets/execution_packet",
    "exploration/requests",
    "exploration/results",
    "implementer/results",
    "sidecars/planner",
    "sidecars/verifier",
    "verification/results",
    "traces/by_task",
    "status/reports",
    "compaction/checkpoints",
    "preflight/reports",
    "rendered/reports",
    "ops/session_reports",
    "pruning/reports",
]

HEALTHY_TASK_IDS = (
    "task_impl_feature_direct_001",
    "task_verify_post_smoke_001",
)


def sanitize(value: str) -> str:
    lowered = value.lower()
    lowered = "".join(
        ch if ch.isalnum() or ch in {".", "_", "-"} else "_" for ch in lowered
    )
    while "__" in lowered:
        lowered = lowered.replace("__", "_")
    lowered = lowered.strip("_")
    return lowered or "id"


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
    normalized = text.strip()
    return normalized or None


def parse_artifact_metadata(path: Path) -> Optional[tuple[str, str, Optional[str]]]:
    try:
        tree = etree.parse(str(path))
    except (OSError, etree.XMLSyntaxError):
        return None

    task_id = xpath_text(tree, "/p:pxml/p:meta/p:task_id")
    doc_class = xpath_text(tree, "/p:pxml/p:meta/p:doc_class")
    if task_id is None or doc_class is None:
        return None

    markdown_path: Optional[str] = None
    if doc_class == "final_render_report":
        markdown_path = xpath_text(
            tree,
            "/p:pxml/p:payload/p:generated_exports/p:markdown_path",
        )

    return task_id, doc_class, markdown_path


def copy_runtime_file(
    source_runtime: Path, sandbox_runtime: Path, source_path: Path
) -> Path:
    relative = source_path.relative_to(source_runtime)
    target = sandbox_runtime / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target)

    if target.suffix == ".pxml":
        try:
            tree = etree.parse(str(target))
        except etree.XMLSyntaxError:
            return target
        doc_class = xpath_text(tree, "/p:pxml/p:meta/p:doc_class")
        if doc_class == "manager_route":
            lane_flag_nodes = tree.xpath(
                "/p:pxml/p:payload/p:lane_flags/*",
                namespaces=NS,
            )
            changed = False
            for node in lane_flag_nodes:
                if etree.QName(node).localname in {"planner", "verifier"}:
                    continue
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
                    changed = True
            if changed:
                tree.write(
                    str(target),
                    encoding="UTF-8",
                    xml_declaration=True,
                    pretty_print=True,
                )
    return target


def copy_task_scoped_artifacts(
    source_runtime: Path,
    sandbox_runtime: Path,
    task_ids: Iterable[str],
) -> None:
    selected = set(task_ids)
    for rel_dir in TASK_ARTIFACT_DIRS:
        source_dir = source_runtime / rel_dir
        if not source_dir.exists():
            continue
        for artifact_path in source_dir.rglob("*.pxml"):
            parsed = parse_artifact_metadata(artifact_path)
            if parsed is None:
                continue
            task_id, _doc_class, markdown_path = parsed
            if task_id not in selected:
                continue

            copy_runtime_file(source_runtime, sandbox_runtime, artifact_path)
            if markdown_path:
                source_markdown = source_runtime / markdown_path.replace(
                    "\\", "/"
                ).lstrip("/")
                if source_markdown.exists() and source_markdown.is_file():
                    copy_runtime_file(source_runtime, sandbox_runtime, source_markdown)


def copy_task_indexes_and_latest_targets(
    source_runtime: Path,
    sandbox_runtime: Path,
    task_ids: Iterable[str],
) -> None:
    index_dir = source_runtime / "index" / "tasks"
    target_index_dir = sandbox_runtime / "index" / "tasks"
    target_index_dir.mkdir(parents=True, exist_ok=True)

    for task_id in set(task_ids):
        source_index_path = index_dir / f"{sanitize(task_id)}.json"
        if not source_index_path.exists():
            continue

        try:
            payload = json.loads(source_index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue

        target_index_path = target_index_dir / source_index_path.name
        target_index_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        for key, value in payload.items():
            if not key.startswith("latest_"):
                continue
            if not isinstance(value, str):
                continue
            source_target = source_runtime / value.replace("\\", "/").lstrip("/")
            if source_target.exists() and source_target.is_file():
                copy_runtime_file(source_runtime, sandbox_runtime, source_target)


def build_runtime_sandbox(
    source_runtime: Path,
    sandbox_runtime: Path,
    task_ids: Iterable[str],
) -> None:
    sandbox_runtime.mkdir(parents=True, exist_ok=True)
    copy_task_scoped_artifacts(source_runtime, sandbox_runtime, task_ids)
    copy_task_indexes_and_latest_targets(source_runtime, sandbox_runtime, task_ids)

    for rel_dir in [
        "release/reports",
        "release/manifests",
        "release/audits",
        "release/governance",
        "latest",
        "index/tasks",
        "index/artifacts",
        "ops/session_refresh",
    ]:
        (sandbox_runtime / rel_dir).mkdir(parents=True, exist_ok=True)


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def source_runtime(repo_root: Path) -> Path:
    return repo_root / "runtime"


@pytest.fixture
def sandbox_runtime(tmp_path: Path, source_runtime: Path) -> Path:
    runtime_root = tmp_path / "runtime"
    build_runtime_sandbox(source_runtime, runtime_root, HEALTHY_TASK_IDS)
    return runtime_root


@pytest.fixture
def run_python(repo_root: Path) -> Callable[..., subprocess.CompletedProcess[str]]:
    def _run(*args: object) -> subprocess.CompletedProcess[str]:
        command = [sys.executable]
        command.extend(str(arg) for arg in args)
        return subprocess.run(command, cwd=repo_root, capture_output=True, text=True)

    return _run


@pytest.fixture
def parse_kv_lines() -> Callable[[str], Dict[str, str]]:
    def _parse(text: str) -> Dict[str, str]:
        values: Dict[str, str] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line or "=" not in line or line.startswith("["):
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or " " in key:
                continue
            values[key] = value.strip()
        return values

    return _parse


@pytest.fixture
def create_broken_candidate_index(repo_root: Path) -> Callable[[Path, str], Path]:
    template_path = (
        repo_root
        / "tests"
        / "fixtures"
        / "release_gate"
        / "broken_latest_task_index.json"
    )
    template_payload = json.loads(template_path.read_text(encoding="utf-8"))

    def _create(runtime_root: Path, task_id: str = "task_rc_broken_latest_001") -> Path:
        payload = dict(template_payload)
        payload["task_id"] = task_id
        target = runtime_root / "index" / "tasks" / f"{sanitize(task_id)}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return target

    return _create


@pytest.fixture
def create_failing_validator_script(tmp_path: Path) -> Callable[[str], Path]:
    def _create(message: str = "forced validator failure") -> Path:
        script_path = tmp_path / "failing_validator.py"
        script_path.write_text(
            f"import sys\nprint({message!r}, file=sys.stderr)\nraise SystemExit(1)\n",
            encoding="utf-8",
        )
        return script_path

    return _create
