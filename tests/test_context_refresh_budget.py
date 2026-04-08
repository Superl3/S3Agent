from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path


def _load_context_contract(repo_root: Path):
    module_path = repo_root / "scripts" / "context_contract.py"
    spec = importlib.util.spec_from_file_location("test_context_contract", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_focused_refresh_budget_is_one_attempt_per_actor_packet_generation(
    tmp_path: Path,
    repo_root: Path,
) -> None:
    contract = _load_context_contract(repo_root)
    consume_focused_refresh_budget = contract.consume_focused_refresh_budget

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)

    allowed_first, used_first, limit = consume_focused_refresh_budget(
        runtime_root=runtime_root,
        task_id="task_budget_001",
        actor="verifier",
        packet_doc_id="doc_execution_packet_task_budget_001_0003",
        packet_generation=3,
        context_generation=1,
        max_attempts=1,
    )
    assert allowed_first
    assert used_first == 1
    assert limit == 1

    allowed_second, used_second, _ = consume_focused_refresh_budget(
        runtime_root=runtime_root,
        task_id="task_budget_001",
        actor="verifier",
        packet_doc_id="doc_execution_packet_task_budget_001_0003",
        packet_generation=3,
        context_generation=1,
        max_attempts=1,
    )
    assert not allowed_second
    assert used_second == 1

    allowed_new_generation, used_new_generation, _ = consume_focused_refresh_budget(
        runtime_root=runtime_root,
        task_id="task_budget_001",
        actor="verifier",
        packet_doc_id="doc_execution_packet_task_budget_001_0003",
        packet_generation=4,
        context_generation=2,
        max_attempts=1,
    )
    assert allowed_new_generation
    assert used_new_generation == 1

    task_index_path = runtime_root / "index" / "tasks" / "task_budget_001.json"
    payload = json.loads(task_index_path.read_text(encoding="utf-8"))
    bucket = payload.get("focused_refresh_budget")
    assert isinstance(bucket, dict)
    assert len(bucket) == 2
