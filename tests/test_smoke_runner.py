import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import smoke_runner as sr


def test_smoke_runner_function_api() -> None:
    results = sr.run_all_checks(sr.REPO_ROOT)
    assert results["overall"]["status"] == "pass", results


def test_smoke_runner_json_cli() -> None:
    script = ROOT / "scripts" / "smoke_runner.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    expected_keys = {
        "structure",
        "runtime_evidence",
        "skills",
        "entry_agents",
        "routing",
        "parallel_policy",
        "search_policy",
        "phase_gate_policy",
        "patch_policy",
        "bug_localization_policy",
        "failure_memory",
        "execution_trace_policy",
        "execution_trace_scenario_policy",
        "budgets",
        "duplicate_blocks",
        "overall",
    }
    assert expected_keys.issubset(payload.keys())
    assert payload["overall"] == "pass"


def test_routing_schema_requires_explicit_selected_fields() -> None:
    schema_path = ROOT / "schemas" / "routing.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    required = set(schema.get("required", []))
    for key in [
        "selected_skill",
        "selected_agent",
        "selected_path",
        "selected_mode",
        "packet_required",
        "packet_gate_status",
        "patch_target",
        "failure_class",
        "preflight",
    ]:
        assert key in required


def test_schema_drives_forbidden_literals_and_termination_enums() -> None:
    schema_path = ROOT / "schemas" / "routing.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    defs = schema.get("$defs", {})

    forbidden = {
        str(item).strip().lower()
        for item in defs.get("forbiddenRoutingLiteral", {}).get("enum", [])
        if str(item).strip()
    }
    termination_status = {
        str(item).strip().lower()
        for item in defs.get("terminationStatus", {}).get("enum", [])
        if str(item).strip()
    }
    termination_reason = {
        str(item).strip().lower()
        for item in defs.get("terminationReason", {}).get("enum", [])
        if str(item).strip()
    }

    assert forbidden == sr.FORBIDDEN_ROUTING_LITERALS
    assert termination_status == sr.TERMINATION_STATUS_VALUES
    assert termination_reason == sr.TERMINATION_REASON_VALUES


def test_handoff_schema_exists_and_has_canonical_fields() -> None:
    schema_path = ROOT / "schemas" / "handoff_state.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    required = set(schema.get("required", []))
    for key in [
        "goal",
        "observed_problem",
        "scope",
        "success_condition",
        "risk",
        "parallelism_need",
        "suspect_file",
        "suspect_function",
        "related_test",
        "source_input_type",
        "source_input_preserved",
        "structured_context",
        "selected_entry_agent",
    ]:
        assert key in required
