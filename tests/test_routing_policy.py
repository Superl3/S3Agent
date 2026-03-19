from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import smoke_runner as sr


def test_routing_policy_matches_examples() -> None:
    result = sr.check_routing_policy(sr.REPO_ROOT)
    assert result["status"] == "pass", result.get("issues")


def test_scope_mode_heuristic_guidance() -> None:
    registry, errors = sr.parse_skills_registry(sr.REPO_ROOT)
    assert not errors, errors
    tasks, task_errors = sr.load_sample_tasks(sr.REPO_ROOT)
    assert not task_errors, task_errors

    for metadata in tasks:
        _, triage, task = sr.route_task(metadata, registry)
        scope = task["scope"]
        mode = triage["mode"]
        candidates = sr.scope_mode_candidates(scope)
        if scope == "moderate":
            assert (
                mode in candidates
                or task["risk"] == "high"
                or metadata["complexity"] == "high"
            )
        else:
            assert mode in candidates


def test_routing_output_requires_explicit_selected_fields() -> None:
    registry, errors = sr.parse_skills_registry(sr.REPO_ROOT)
    assert not errors, errors
    tasks, task_errors = sr.load_sample_tasks(sr.REPO_ROOT)
    assert not task_errors, task_errors

    decision, _, _ = sr.route_task(tasks[0], registry)
    for key in [
        "selected_skill",
        "selected_agent",
        "selected_path",
        "handoff_sequence",
        "selected_mode",
        "packet_required",
        "packet_gate_status",
        "patch_target",
        "failure_class",
        "preflight",
        "termination_status",
        "termination_reason",
    ]:
        assert key in decision

    for missing_key in ["selected_skill", "selected_agent", "selected_path"]:
        mutated = dict(decision)
        mutated.pop(missing_key, None)
        errors = sr.validate_routing_decision(mutated, registry)
        assert "routing decision keys mismatch" in errors


def test_routing_rejects_forbidden_placeholder_tokens() -> None:
    registry, errors = sr.parse_skills_registry(sr.REPO_ROOT)
    assert not errors, errors
    tasks, task_errors = sr.load_sample_tasks(sr.REPO_ROOT)
    assert not task_errors, task_errors

    decision, _, _ = sr.route_task(tasks[0], registry)
    forbidden = sorted(
        literal
        for literal in sr.FORBIDDEN_ROUTING_LITERALS
        if " " not in literal and literal != "na"
    )
    for token in forbidden:
        mutated = dict(decision)
        mutated["patch_target"] = token
        errors = sr.validate_routing_decision(mutated, registry)
        assert any("forbidden routing token(s) detected" in err for err in errors)


def test_routing_rejects_meaningless_delegation_phrases() -> None:
    registry, errors = sr.parse_skills_registry(sr.REPO_ROOT)
    assert not errors, errors
    tasks, task_errors = sr.load_sample_tasks(sr.REPO_ROOT)
    assert not task_errors, task_errors

    decision, _, _ = sr.route_task(tasks[0], registry)
    for phrase in sorted(
        literal for literal in sr.FORBIDDEN_ROUTING_LITERALS if " " in literal
    ):
        mutated = dict(decision)
        mutated["patch_target"] = phrase
        errors = sr.validate_routing_decision(mutated, registry)
        assert any("forbidden routing token(s) detected" in err for err in errors)


def test_validate_normalized_task_rejects_meaningless_core_fields() -> None:
    task = {
        "goal": "n/a",
        "observed_problem": "Stop",
        "scope": "narrow",
        "suspect_file": "",
        "suspect_function": "",
        "related_test": "",
        "success_condition": "h",
        "risk": "low",
        "parallelism_need": "no",
    }
    errors = sr.validate_normalized_task(task)
    assert any("invalid goal" in err for err in errors)
    assert any("invalid observed_problem" in err for err in errors)
    assert any("invalid success_condition" in err for err in errors)


def test_routing_enforces_skill_agent_matrix() -> None:
    registry, errors = sr.parse_skills_registry(sr.REPO_ROOT)
    assert not errors, errors

    decision = {
        "selected_skill": "bug_fix",
        "selected_agent": "tester",
        "selected_path": ["prompt_high", "orchestrator", "tester"],
        "selected_mode": "MICRO",
        "packet_required": False,
        "packet_gate_status": "not_required",
        "patch_target": "src/parser.py::parse_tokens",
        "failure_class": "runtime logic failure",
        "preflight": {
            "scope": "narrow",
            "allowed_files": ["src/parser.py"],
            "risk": "low",
            "test_plan": ["unit"],
            "change_type": "logic",
        },
        "skill": "bug_fix",
        "agent": "tester",
        "mode": "MICRO",
        "parallel": False,
        "escalation": "none",
        "reason_codes": ["LOW_RISK", "PATCH_FIRST_REQUIRED", "SINGLE_AGENT_DEFAULT"],
        "handoff_sequence": "prompt_high -> orchestrator -> tester",
        "termination_status": "delegated",
        "termination_reason": "none",
    }
    errors = sr.validate_routing_decision(decision, registry)
    assert any(
        "selected_skill/selected_agent matrix mismatch against SKILLS.md" in err
        for err in errors
    )


def test_selected_path_is_ordered_entry_orchestrator_execution_agent() -> None:
    registry, errors = sr.parse_skills_registry(sr.REPO_ROOT)
    assert not errors, errors
    tasks, task_errors = sr.load_sample_tasks(sr.REPO_ROOT)
    assert not task_errors, task_errors

    decision, _, _ = sr.route_task(tasks[0], registry)
    selected_path = decision["selected_path"]
    assert isinstance(selected_path, list)
    assert len(selected_path) == 3
    assert selected_path[0] in {"prompt", "prompt_high"}
    assert selected_path[1] == "orchestrator"
    assert selected_path[2] == decision["selected_agent"]


def test_repair_oriented_routes_require_non_empty_patch_target() -> None:
    registry, errors = sr.parse_skills_registry(sr.REPO_ROOT)
    assert not errors, errors
    tasks, task_errors = sr.load_sample_tasks(sr.REPO_ROOT)
    assert not task_errors, task_errors

    target = [t for t in tasks if t["id"] == "failing_test_repair_001"][0]
    decision, _, _ = sr.route_task(target, registry)
    assert decision["patch_target"].strip()


def test_environment_blocker_is_not_routed_to_code_repair_agent() -> None:
    registry, errors = sr.parse_skills_registry(sr.REPO_ROOT)
    assert not errors, errors
    tasks, task_errors = sr.load_sample_tasks(sr.REPO_ROOT)
    assert not task_errors, task_errors

    target = [t for t in tasks if t["id"] == "environment_blocker_001"][0]
    decision, _, _ = sr.route_task(target, registry)
    assert decision["selected_agent"] not in {"debugger", "implementer"}
    assert decision["selected_skill"] == "review"


def test_pre_dispatch_gate_priority_and_non_delegation() -> None:
    registry, errors = sr.parse_skills_registry(sr.REPO_ROOT)
    assert not errors, errors
    tasks, task_errors = sr.load_sample_tasks(sr.REPO_ROOT)
    assert not task_errors, task_errors

    base = dict(tasks[0])
    base["goal"] = "n/a"
    base["observed_problem"] = "no-op request"
    base["entry_agent"] = "harness_review"

    decision, _, _ = sr.route_task(base, registry)
    assert decision["termination_status"] == "terminated"
    assert decision["termination_reason"] == "invalid_task"
    assert decision["selected_agent"] is None
    assert decision["selected_path"] is None
    assert decision["handoff_sequence"] == ""


def test_pre_dispatch_gate_rejects_empty_dispatch_payload() -> None:
    registry, errors = sr.parse_skills_registry(sr.REPO_ROOT)
    assert not errors, errors
    tasks, task_errors = sr.load_sample_tasks(sr.REPO_ROOT)
    assert not task_errors, task_errors

    meta = dict(tasks[0])
    meta["dispatch_payload"] = ""
    decision, _, _ = sr.route_task(meta, registry)
    assert decision["termination_status"] == "terminated"
    assert decision["termination_reason"] == "no_op"
    assert decision["selected_agent"] is None


def test_permission_denied_dispatch_uses_original_prompt_fallback_once() -> None:
    registry, errors = sr.parse_skills_registry(sr.REPO_ROOT)
    assert not errors, errors
    tasks, task_errors = sr.load_sample_tasks(sr.REPO_ROOT)
    assert not task_errors, task_errors

    blocked = dict(tasks[0])
    blocked["dispatch_payload"] = "PermissionDeniedError: task dispatch denied"
    blocked_decision, _, _ = sr.route_task(blocked, registry)
    assert blocked_decision["termination_reason"] == "no_op"

    recovered = dict(blocked)
    recovered["original_prompt"] = "fix parser regression in narrow scope"
    recovered_decision, _, _ = sr.route_task(recovered, registry)
    assert recovered_decision["termination_reason"] != "no_op"


def test_pre_dispatch_gate_review_only_and_improve_only() -> None:
    registry, errors = sr.parse_skills_registry(sr.REPO_ROOT)
    assert not errors, errors
    tasks, task_errors = sr.load_sample_tasks(sr.REPO_ROOT)
    assert not task_errors, task_errors

    review_meta = dict(tasks[0])
    review_meta["entry_agent"] = "harness_review"
    review_meta["goal"] = "inspect harness behavior"
    review_meta["observed_problem"] = "review-only diagnostics"
    review_decision, _, _ = sr.route_task(review_meta, registry)
    assert review_decision["termination_reason"] == "review_only"
    assert review_decision["selected_agent"] is None
    assert review_decision["handoff_sequence"] == ""

    improve_meta = dict(tasks[0])
    improve_meta["entry_agent"] = "harness_improve"
    improve_meta["goal"] = "prepare harness improvement"
    improve_meta["observed_problem"] = "improve-only planning"
    improve_decision, _, _ = sr.route_task(improve_meta, registry)
    assert improve_decision["termination_reason"] == "improve_only"
    assert improve_decision["selected_agent"] is None
    assert improve_decision["handoff_sequence"] == ""


def test_check_routing_policy_allows_pre_dispatch_terminated_examples(
    monkeypatch,
) -> None:
    tasks, task_errors = sr.load_sample_tasks(sr.REPO_ROOT)
    assert not task_errors, task_errors

    terminated_meta = dict(tasks[0])
    terminated_meta["dispatch_payload"] = ""

    def _stub_load_sample_tasks(_root: Path):
        return [terminated_meta], []

    monkeypatch.setattr(sr, "load_sample_tasks", _stub_load_sample_tasks)
    result = sr.check_routing_policy(sr.REPO_ROOT)
    assert result["status"] == "pass", result.get("issues")


def test_category_is_optional_and_accepts_allowed_values() -> None:
    task = {
        "goal": "fix parser behavior",
        "observed_problem": "assertion failure in parser test",
        "scope": "narrow",
        "suspect_file": "src/parser.py",
        "suspect_function": "parse_tokens",
        "related_test": "tests/test_parser.py::test_parse_tokens",
        "success_condition": "parser tests pass",
        "risk": "low",
        "parallelism_need": "no",
    }
    assert not sr.validate_normalized_task(task)

    for category in sorted(sr.TASK_OPTIONAL_CATEGORIES):
        with_category = dict(task)
        with_category["category"] = category
        assert not sr.validate_normalized_task(with_category), category


def test_category_driven_routing_is_deterministic() -> None:
    registry, errors = sr.parse_skills_registry(sr.REPO_ROOT)
    assert not errors, errors

    metadata = {
        "id": "category_probe_001",
        "goal": "stabilize integration path",
        "observed_problem": "intermittent integration failure",
        "scope": "moderate",
        "suspect_file": "src/integration.py",
        "suspect_function": "",
        "related_test": "tests/test_integration.py::test_happy_path",
        "success_condition": "integration tests pass",
        "parallelism_need": "no",
        "risk": "medium",
        "complexity": "medium",
        "requires_bug_localization": "false",
        "requires_patch_first": "true",
        "parallel_allowed": "false",
        "expected_skill": "regression_repair",
        "expected_agent": "debugger",
        "expected_mode": "STANDARD",
        "category": "integration_hardening",
    }

    d1, _, _ = sr.route_task(metadata, registry)
    d2, _, _ = sr.route_task(metadata, registry)
    assert d1 == d2
    assert d1["selected_skill"] == "regression_repair"
    assert d1["selected_agent"] == "debugger"
    assert d1["selected_mode"] == "STANDARD"


def test_execution_notepad_policy_and_parallel_doc_policy_smoke_checks() -> None:
    notepad_result = sr.check_execution_notepad_policy(sr.REPO_ROOT)
    assert notepad_result["status"] == "pass", notepad_result.get("issues")

    parallel_result = sr.check_parallelization_policy(sr.REPO_ROOT)
    assert parallel_result["status"] == "pass", parallel_result.get("issues")


def test_no_new_agents_added() -> None:
    headers, issues = sr.load_agent_headers(sr.REPO_ROOT)
    assert not issues, issues
    assert set(headers.keys()) == sr.EXPECTED_AGENT_SET
    packet_runner = headers["packet_runner"]
    assert packet_runner["mode"] == "subagent"
    assert packet_runner["user_facing"] == "false"
    assert packet_runner["hidden"] == "true"


def test_broad_open_work_bypasses_packet_runner() -> None:
    registry, errors = sr.parse_skills_registry(sr.REPO_ROOT)
    assert not errors, errors
    tasks, task_errors = sr.load_sample_tasks(sr.REPO_ROOT)
    assert not task_errors, task_errors

    target = [t for t in tasks if t["id"] == "high_risk_refactor_001"][0]
    decision, _, _ = sr.route_task(target, registry)
    assert decision["selected_agent"] != "packet_runner"


def test_preflight_requires_rollback_for_high_risk_or_broad_scope() -> None:
    high_risk = {
        "scope": "narrow",
        "allowed_files": ["src/parser.py"],
        "risk": "high",
        "test_plan": ["unit"],
        "change_type": "logic",
    }
    assert any(
        "rollback_plan" in issue for issue in sr.validate_preflight_artifact(high_risk)
    )

    broad_scope = {
        "scope": "broad",
        "allowed_files": ["src/parser.py"],
        "risk": "low",
        "test_plan": ["unit", "smoke"],
        "change_type": "mixed/cross-module",
    }
    assert any(
        "rollback_plan" in issue
        for issue in sr.validate_preflight_artifact(broad_scope)
    )


def test_mixed_cross_module_requires_at_least_two_validations() -> None:
    artifact = {
        "scope": "moderate",
        "allowed_files": ["src/parser.py"],
        "risk": "medium",
        "test_plan": ["unit"],
        "change_type": "mixed/cross-module",
    }
    errors = sr.validate_preflight_artifact(artifact)
    assert any("at least two validation steps" in issue for issue in errors)


def test_preflight_fast_path_eligibility_uses_unique_allowed_files_count() -> None:
    assert sr.allowed_files_unique_count(["src/a.py", "src\\a.py", "src/a.py"]) == 1

    artifact = {
        "scope": "narrow",
        "allowed_files": ["src/a.py"],
        "allowed_files_count": 1,
        "fast_path_eligible": True,
        "success_check_present": True,
        "risk": "low",
        "test_plan": ["unit"],
        "change_type": "logic",
    }
    assert not sr.validate_preflight_artifact(artifact)


def test_preflight_rejects_invalid_fast_path_eligibility_count() -> None:
    artifact = {
        "scope": "narrow",
        "allowed_files": ["src/a.py", "src/b.py", "src/c.py", "src/d.py"],
        "allowed_files_count": 4,
        "fast_path_eligible": True,
        "success_check_present": True,
        "risk": "low",
        "test_plan": ["unit"],
        "change_type": "logic",
    }
    errors = sr.validate_preflight_artifact(artifact)
    assert any(
        "preflight fast_path_eligible requires scope=narrow, risk!=high, unique allowed_files_count<=3, and success_check_present=true"
        in issue
        for issue in errors
    )


def test_preflight_rejects_fast_path_eligibility_when_scope_risk_or_success_check_do_not_match() -> (
    None
):
    base = {
        "scope": "narrow",
        "allowed_files": ["src/a.py"],
        "allowed_files_count": 1,
        "fast_path_eligible": True,
        "success_check_present": True,
        "risk": "low",
        "test_plan": ["unit"],
        "change_type": "logic",
    }

    moderate_scope = dict(base)
    moderate_scope["scope"] = "moderate"
    errors = sr.validate_preflight_artifact(moderate_scope)
    assert any(
        "preflight fast_path_eligible requires scope=narrow, risk!=high, unique allowed_files_count<=3, and success_check_present=true"
        in issue
        for issue in errors
    )

    high_risk = dict(base)
    high_risk["risk"] = "high"
    errors = sr.validate_preflight_artifact(high_risk)
    assert any(
        "preflight fast_path_eligible requires scope=narrow, risk!=high, unique allowed_files_count<=3, and success_check_present=true"
        in issue
        for issue in errors
    )

    missing_success_check = dict(base)
    missing_success_check["success_check_present"] = False
    errors = sr.validate_preflight_artifact(missing_success_check)
    assert any(
        "preflight fast_path_eligible requires scope=narrow, risk!=high, unique allowed_files_count<=3, and success_check_present=true"
        in issue
        for issue in errors
    )
