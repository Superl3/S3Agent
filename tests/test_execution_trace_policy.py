from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import smoke_runner as sr
import trace_runtime as tr


def _write_runtime_templates(root: Path) -> None:
    runtime = root / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    runtime.joinpath("execution_trace_template.md").write_text(
        (sr.REPO_ROOT / "runtime" / "execution_trace_template.md").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )


def test_execution_trace_template_exists() -> None:
    path = sr.REPO_ROOT / "runtime" / "execution_trace_template.md"
    assert path.exists()


def test_execution_trace_template_required_fields_present() -> None:
    path = sr.REPO_ROOT / "runtime" / "execution_trace_template.md"
    keys, _, issues = sr._parse_template_fields(path)
    assert not issues, issues
    assert set(sr.TRACE_REQUIRED_FIELDS).issubset(set(keys))


def test_execution_trace_template_has_no_unsupported_fields() -> None:
    path = sr.REPO_ROOT / "runtime" / "execution_trace_template.md"
    keys, _, _ = sr._parse_template_fields(path)
    unsupported = [key for key in keys if key not in sr.TRACE_REQUIRED_FIELD_SET]
    assert not unsupported, unsupported


def test_execution_trace_template_entries_are_concise() -> None:
    path = sr.REPO_ROOT / "runtime" / "execution_trace_template.md"
    _, values, _ = sr._parse_template_fields(path)
    for key in sr.TRACE_REQUIRED_FIELDS:
        assert values.get(key, "").strip()
        assert len(values[key]) <= 140


def test_execution_trace_template_tracks_invalid_routing_explicitly() -> None:
    path = sr.REPO_ROOT / "runtime" / "execution_trace_template.md"
    _, values, _ = sr._parse_template_fields(path)

    assert "routing_validation_status" in values
    assert "invalid_routing_tokens" in values
    assert "fingerprints" in values

    status = values["routing_validation_status"].lower()
    assert "pass" in status and "fail" in status

    invalid_tokens = values["invalid_routing_tokens"].lower()
    for token in ["noop", "bad", "read1", "read2", "switch", "ignore"]:
        assert token in invalid_tokens

    fingerprints = values["fingerprints"].lower()
    for token in ["policy_fp=", "task_fp=", "route_fp="]:
        assert token in fingerprints

    fast_path_attempt = values["fast_path_attempt"].lower()
    for token in [
        "status=",
        "budget_exempt=",
        "allowed_files_count=",
        "verifier_result=",
        "validation_proof=",
    ]:
        assert token in fast_path_attempt

    selected_path = values["selected_path"].lower()
    assert "->" in selected_path
    assert "orchestrator" in selected_path


def test_execution_trace_policy_smoke_check_passes() -> None:
    result = sr.check_execution_trace_policy(sr.REPO_ROOT)
    assert result["status"] == "pass", result.get("issues")


def test_execution_trace_policy_outputs_termination_fields() -> None:
    result = sr.check_execution_trace_policy(sr.REPO_ROOT)
    assert "termination_status" in result
    assert "termination_reason" in result
    assert result["termination_status"]
    assert result["termination_reason"]


def test_scenario_expectation_template_exists() -> None:
    path = sr.REPO_ROOT / "runtime" / "scenario_expectation_template.md"
    assert path.exists()


def test_scenario_expectation_template_required_fields_present() -> None:
    path = sr.REPO_ROOT / "runtime" / "scenario_expectation_template.md"
    keys, _, issues = sr._parse_template_fields(path)
    assert not issues, issues
    assert set(sr.SCENARIO_EXPECTATION_REQUIRED_FIELDS).issubset(set(keys))


def test_execution_trace_scenario_policy_smoke_check_passes() -> None:
    result = sr.check_execution_trace_scenario_policy(sr.REPO_ROOT)
    assert result["status"] == "pass", result.get("issues")


def test_execution_trace_policy_marks_unverified_trace_when_actual_trace_missing(
    tmp_path: Path,
) -> None:
    _write_runtime_templates(tmp_path)
    (tmp_path / "runtime" / "scenario_expectation_latest.md").write_text(
        "\n".join(
            [
                "required_agents: prompt_high, orchestrator, debugger",
                "forbidden_agents: reviewer",
                "expected_handoff_order: prompt_high -> orchestrator -> debugger",
            ]
        ),
        encoding="utf-8",
    )

    result = sr.check_execution_trace_policy(tmp_path)
    assert result["status"] == "fail", result.get("issues")
    assert any(
        str(issue).startswith("UNVERIFIED_TRACE:") for issue in result.get("issues", [])
    )


def test_execution_trace_policy_marks_trace_mismatch_from_handoff_sequence(
    tmp_path: Path,
) -> None:
    _write_runtime_templates(tmp_path)
    (tmp_path / "runtime" / "execution_trace_latest.md").write_text(
        "\n".join(
            [
                "handoff_sequence: prompt_high -> orchestrator -> reviewer",
                "selected_path: prompt_high -> orchestrator -> debugger",
                "trace_status: complete",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "runtime" / "scenario_expectation_latest.md").write_text(
        "\n".join(
            [
                "required_agents: prompt_high, orchestrator, debugger",
                "forbidden_agents: reviewer",
                "expected_handoff_order: prompt_high -> orchestrator -> debugger",
            ]
        ),
        encoding="utf-8",
    )

    result = sr.check_execution_trace_policy(tmp_path)
    assert result["status"] == "fail", result.get("issues")
    assert any(
        str(issue).startswith("TRACE_MISMATCH:") for issue in result.get("issues", [])
    )


def test_execution_trace_policy_passes_when_actual_handoff_matches_expectation(
    tmp_path: Path,
) -> None:
    _write_runtime_templates(tmp_path)
    (tmp_path / "runtime" / "execution_trace_latest.md").write_text(
        "\n".join(
            [
                "handoff_sequence: prompt_high -> orchestrator -> debugger",
                "selected_path: prompt_high -> orchestrator -> reviewer",
                "trace_status: complete",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "runtime" / "scenario_expectation_latest.md").write_text(
        "\n".join(
            [
                "required_agents: prompt_high, orchestrator, debugger",
                "forbidden_agents: reviewer",
                "expected_handoff_order: prompt_high -> orchestrator -> debugger",
            ]
        ),
        encoding="utf-8",
    )

    result = sr.check_execution_trace_policy(tmp_path)
    assert result["status"] == "pass", result.get("issues")


def test_execution_trace_policy_marks_unverified_trace_when_status_is_partial(
    tmp_path: Path,
) -> None:
    _write_runtime_templates(tmp_path)
    (tmp_path / "runtime" / "execution_trace_latest.md").write_text(
        "\n".join(
            [
                "handoff_sequence: prompt_high -> orchestrator -> debugger",
                "trace_status: partial",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "runtime" / "scenario_expectation_latest.md").write_text(
        "\n".join(
            [
                "required_agents: prompt_high, orchestrator, debugger",
                "forbidden_agents: reviewer",
                "expected_handoff_order: prompt_high -> orchestrator -> debugger",
            ]
        ),
        encoding="utf-8",
    )

    result = sr.check_execution_trace_policy(tmp_path)
    assert result["status"] == "fail", result.get("issues")
    assert any(
        str(issue)
        == "UNVERIFIED_TRACE: role-correctness scenario requires complete trace_status"
        for issue in result.get("issues", [])
    )


def test_execution_trace_policy_keeps_trace_optional_for_non_role_correctness(
    tmp_path: Path,
) -> None:
    _write_runtime_templates(tmp_path)
    (tmp_path / "runtime" / "scenario_expectation_latest.md").write_text(
        "expected_result: PASS\n",
        encoding="utf-8",
    )
    result = sr.check_execution_trace_policy(tmp_path)
    assert result["status"] == "pass", result.get("issues")


def test_trace_runtime_lifecycle_append_idempotence_and_finalize_single_shot(
    tmp_path: Path,
) -> None:
    tr.entry_start(tmp_path, task="tiny_bugfix_001", entry_agent="prompt_high")
    tr.orchestrator_decision(
        tmp_path,
        selected_mode="MICRO",
        selected_path="prompt_high -> orchestrator -> debugger",
    )
    tr.append_handoff(tmp_path, "prompt_high", "orchestrator")
    tr.append_handoff(tmp_path, "prompt_high", "orchestrator")
    tr.append_handoff(tmp_path, "orchestrator", "debugger")
    tr.append_handoff(tmp_path, "orchestrator", "debugger")
    tr.task_finalize(tmp_path, result="PASS", validation_sequence="unit:test_parser")
    tr.task_finalize(
        tmp_path, result="FAIL", validation_sequence="should_not_overwrite"
    )

    trace_path = tmp_path / "runtime" / "execution_trace_latest.md"
    _, values, issues = sr._parse_template_fields(trace_path)
    assert not issues, issues
    assert values["task"] == "tiny_bugfix_001"
    assert values["entry_agent"] == "prompt_high"
    assert values["selected_mode"] == "MICRO"
    assert values["selected_path"] == "prompt_high -> orchestrator -> debugger"
    assert values["handoff_sequence"] == "prompt_high -> orchestrator -> debugger"
    assert values["result"] == "PASS"
    assert values["validation_sequence"] == "unit:test_parser"
    assert values["packet_exhaustion"] == "none"
    assert values["trace_status"] == "complete"

    archive_path = tmp_path / "runtime" / "execution_trace_archive.md"
    assert archive_path.exists()
    archive_text = archive_path.read_text(encoding="utf-8")
    assert "task: tiny_bugfix_001" in archive_text
    assert "trace_status: complete" in archive_text


def test_trace_runtime_finalize_can_record_exhaustion_state(tmp_path: Path) -> None:
    tr.entry_start(tmp_path, task="repair_loop_001", entry_agent="prompt_high")
    tr.task_finalize(
        tmp_path,
        result="FAIL",
        validation_sequence="unit:test_loop",
        packet_exhaustion="exhausted",
    )

    trace_path = tmp_path / "runtime" / "execution_trace_latest.md"
    _, values, issues = sr._parse_template_fields(trace_path)
    assert not issues, issues
    assert values["packet_exhaustion"] == "exhausted"


def test_final_renderer_output_is_markdown_and_hides_internal_fields() -> None:
    rendered = sr.render_final_user_output(
        {
            "status": "pass",
            "preflight": {"scope": "narrow"},
            "reason_codes": ["LOW_RISK", "SINGLE_AGENT_DEFAULT"],
            "selected_path": ["prompt_high", "orchestrator", "debugger"],
        }
    )
    lowered = rendered.lower()
    assert rendered.startswith("## ")
    assert "preflight" not in lowered
    assert "reason_codes" not in lowered
    assert "selected_path" not in lowered


def test_runtime_evidence_hard_fails_when_archive_missing(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    runtime.joinpath("execution_trace_latest.md").write_text(
        "task: smoke\nentry_agent: prompt_high\nresult: PASS\ntrace_status: complete\n",
        encoding="utf-8",
    )
    result = sr.check_runtime_evidence(tmp_path)
    assert result["status"] == "fail", result.get("issues")
    assert any("archive missing" in str(issue) for issue in result.get("issues", []))
