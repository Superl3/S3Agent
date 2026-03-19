import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import smoke_runner as sr


def test_entry_agent_visibility_policy() -> None:
    result = sr.check_entry_agent_policy(sr.REPO_ROOT)
    assert result["status"] == "pass", result.get("issues")


def test_only_prompt_high_and_harness_review_are_user_facing() -> None:
    headers, issues = sr.load_agent_headers(sr.REPO_ROOT)
    assert not issues, issues

    primary_user_facing = {
        name
        for name, header in headers.items()
        if header["mode"] == "primary"
        and header["user_facing"] == "true"
        and header["hidden"] == "false"
    }
    assert primary_user_facing == {"prompt_high", "harness_review"}
    assert "intake" not in primary_user_facing


def test_non_terminal_entry_invariant_and_management_path_are_documented() -> None:
    agents_doc = (sr.REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    readme_doc = (sr.REPO_ROOT / "README.md").read_text(encoding="utf-8")

    invariant_lines = [
        "prompt_high and prompt are non-terminal entry agents.",
        "They must always normalize and immediately hand off to orchestrator.",
    ]
    for line in invariant_lines:
        assert line in agents_doc
        assert line in readme_doc

    management_line = (
        "harness_improve is internal-only and approval-gated for any mutation."
    )
    assert management_line in agents_doc
    assert management_line in readme_doc

    gate_line = (
        "Their direct tool permissions are denied; only `task -> orchestrator` "
        "handoff is allowed."
    )
    assert gate_line in agents_doc
    assert gate_line in readme_doc

    orchestrator_line = (
        "orchestrator is a non-executing control-plane delegator and must delegate "
        "normal work to execution agents."
    )
    assert orchestrator_line in agents_doc
    assert orchestrator_line in readme_doc


def test_only_management_agents_can_stop_without_orchestrator_handoff() -> None:
    config = json.loads((sr.REPO_ROOT / "opencode.jsonc").read_text(encoding="utf-8"))

    assert config["agent"]["build"]["disable"] is True
    assert config["agent"]["plan"]["disable"] is True

    for entry_agent in ["prompt_high"]:
        perms = config["agent"][entry_agent]["permission"]
        assert perms.get("*") == "deny"
        for tool_name, rule in perms.items():
            if tool_name in {"*", "task", "__originalKeys"}:
                continue
            assert rule != "allow"

        task_perms = perms["task"]
        allowed_targets = {
            name for name, decision in task_perms.items() if decision == "allow"
        }
        assert allowed_targets == {"orchestrator"}

    for management_agent in ["harness_review", "harness_improve", "prompt"]:
        task_perms = config["agent"][management_agent]["permission"]["task"]
        allowed_targets = {
            name for name, decision in task_perms.items() if decision == "allow"
        }
        assert allowed_targets == set()

    orchestrator_perms = config["agent"]["orchestrator"]["permission"]
    assert orchestrator_perms.get("*") == "deny"
    task_perms = orchestrator_perms["task"]
    allowed_targets = {
        name for name, decision in task_perms.items() if decision == "allow"
    }
    assert allowed_targets == {"implementer", "debugger", "tester", "reviewer"}


def test_no_policy_wording_allows_entry_to_end_before_orchestrator() -> None:
    paths = [
        sr.REPO_ROOT / "agents" / "prompt.md",
        sr.REPO_ROOT / "agents" / "prompt_high.md",
        sr.REPO_ROOT / "instructions" / "task_intake.md",
    ]
    forbidden = [
        "can terminate after normalization",
        "may terminate after normalization",
        "is allowed to terminate after normalization",
        "can stop after normalization",
        "may stop after normalization",
    ]

    for path in paths:
        text = path.read_text(encoding="utf-8").lower()
        for marker in forbidden:
            assert marker not in text
