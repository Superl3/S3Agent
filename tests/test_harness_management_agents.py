from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import smoke_runner as sr


def _extract_contract_keys(text: str) -> list[str]:
    keys: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("- ") or ":" not in line:
            continue
        key = line[2:].split(":", 1)[0].strip()
        keys.append(key)
    return keys


def test_harness_management_agents_are_primary_user_facing() -> None:
    headers, issues = sr.load_agent_headers(sr.REPO_ROOT)
    assert not issues, issues

    review = headers["harness_review"]
    assert review["mode"] == "primary"
    assert review["user_facing"] == "true"
    assert review["hidden"] == "false"

    improve = headers["harness_improve"]
    assert improve["mode"] == "subagent"
    assert improve["user_facing"] == "false"
    assert improve["hidden"] == "true"


def test_harness_review_contract_and_non_mutating_markers() -> None:
    text = (sr.REPO_ROOT / "agents" / "harness_review.md").read_text(encoding="utf-8")
    lowered = text.lower()

    assert "do not modify files." in lowered
    assert "execution trace summaries" in lowered
    assert (
        "supporting evidence only; correlate with failure logs and validation outputs before recommending a fix."
        in lowered
    )
    assert "role-boundary collapse" in lowered
    keys = _extract_contract_keys(lowered)
    contract = [
        "problem_class",
        "evidence",
        "minimal_fix",
        "risk",
        "expected_effect",
        "recommended_next_action",
        "improve_input_draft",
    ]
    start = keys.index("problem_class")
    assert keys[start : start + len(contract)] == contract
    assert (
        "improve_input_draft: {proposed_change, touched_files, why_minimal, expected_effect, risk, ready_for_apply}"
        in lowered
    )


def test_harness_improve_contract_plan_only_and_approval_wait() -> None:
    text = (sr.REPO_ROOT / "agents" / "harness_improve.md").read_text(encoding="utf-8")
    lowered = text.lower()

    assert "improvement planning only by default." in lowered
    assert "do not modify files until explicit user approval." in lowered
    assert "internal-only subagent." in lowered
    assert (
        "role-boundary collapse evidence as a first-class harness failure signal."
        in lowered
    )
    assert text.strip().endswith("Awaiting explicit user approval.")

    keys = _extract_contract_keys(lowered)
    contract = [
        "proposed_change",
        "touched_files",
        "why_minimal",
        "expected_effect",
        "risk",
        "ready_for_apply",
    ]
    start = keys.index("proposed_change")
    assert keys[start : start + len(contract)] == contract


def test_no_intake_user_facing_expectations_remain() -> None:
    assert not (sr.REPO_ROOT / "agents" / "intake.md").exists()
    assert not (sr.REPO_ROOT / ".opencode" / "agents" / "intake.md").exists()

    agents_doc = (sr.REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8").lower()
    readme_doc = (sr.REPO_ROOT / "README.md").read_text(encoding="utf-8").lower()
    config = (sr.REPO_ROOT / "opencode.jsonc").read_text(encoding="utf-8").lower()

    assert "`intake`" not in agents_doc
    assert "`intake`" not in readme_doc
    assert '"intake"' not in config


def test_harness_review_missing_evidence_uses_insufficient_template() -> None:
    rendered = sr.render_final_user_output(
        {
            "problem_class": "missing_evidence_bundle_for_harness_diagnosis",
            "status": "fail",
        }
    )
    lowered = rendered.lower()
    assert rendered.startswith("## ")
    assert "harness review" in lowered
    assert "insufficient evidence" in lowered


def test_harness_improve_proposal_uses_improve_template() -> None:
    rendered = sr.render_final_user_output(
        {
            "improve_input_draft": {
                "proposed_change": "Tighten one routing guard.",
                "expected_effect": "Reduce repeated routing misclassification.",
            }
        }
    )
    lowered = rendered.lower()
    assert rendered.startswith("## ")
    assert "improvement proposal" in lowered
    assert "next step" in lowered


def test_harness_review_success_uses_review_success_template() -> None:
    rendered = sr.render_final_user_output(
        {
            "entry_agent": "harness_review",
            "status": "pass",
        }
    )
    lowered = rendered.lower()
    assert rendered.startswith("## ")
    assert "harness review" in lowered


def test_harness_review_with_improve_draft_stays_review_terminal_markdown() -> None:
    rendered = sr.render_final_user_output(
        {
            "entry_agent": "harness_review",
            "status": "pass",
            "improve_input_draft": {
                "proposed_change": "Tighten one routing guard.",
                "expected_effect": "Allow review-to-improve transition.",
            },
        }
    )
    lowered = rendered.lower()
    assert rendered.startswith("## ")
    assert "harness review" in lowered


def test_harness_improve_intermediate_output_is_not_final_rendered() -> None:
    rendered = sr.render_final_user_output(
        {
            "entry_agent": "harness_improve",
            "improve_input_draft": {
                "proposed_change": "Tighten one routing guard.",
                "expected_effect": "Reduce repeated routing misclassification.",
            },
        }
    )
    assert rendered == ""


def test_review_to_improve_transition_allows_delegation_with_draft() -> None:
    registry, errors = sr.parse_skills_registry(sr.REPO_ROOT)
    assert not errors, errors
    tasks, task_errors = sr.load_sample_tasks(sr.REPO_ROOT)
    assert not task_errors, task_errors

    improve_meta = dict(tasks[0])
    improve_meta["entry_agent"] = "harness_improve"
    improve_meta["category"] = "harness_improve"
    improve_meta["improve_input_draft"] = "{proposed_change: tighten one guard}"

    decision, _, _ = sr.route_task(improve_meta, registry)
    assert decision["termination_status"] == "delegated"
    assert decision["termination_reason"] == "none"
    assert decision["selected_agent"] == "reviewer"
