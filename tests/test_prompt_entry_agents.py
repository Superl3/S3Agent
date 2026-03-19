from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import smoke_runner as sr


def test_prompt_entry_contract_policy() -> None:
    result = sr.check_entry_agent_policy(sr.REPO_ROOT)
    assert result["status"] == "pass", result.get("issues")


def test_prompt_output_validator_accepts_valid_output() -> None:
    payload = "\n".join(
        [
            "goal: fix parser edge case",
            "observed_problem: empty input crashes parser",
            "scope: narrow",
            "suspect_file: src/parser.py",
            "suspect_function: parse_tokens",
            "related_test: tests/test_parser.py::test_empty_input",
            "success_condition: empty-input test passes",
            "risk: low",
            "parallelism_need: no",
        ]
    )
    assert sr.validate_prompt_output(payload) == []


def test_prompt_output_validator_rejects_invalid_output() -> None:
    with_code_block = "\n".join(
        [
            "goal: fix parser edge case",
            "observed_problem: empty input crashes parser",
            "scope: narrow",
            "suspect_file: ",
            "suspect_function: ",
            "related_test: ",
            "success_condition: parser test passes",
            "risk: low",
            "parallelism_need: no",
            "```python",
        ]
    )
    with_numbered_plan = "\n".join(
        [
            "1. gather files",
            "goal: fix parser edge case",
            "observed_problem: empty input crashes parser",
            "scope: narrow",
            "suspect_file: ",
            "suspect_function: ",
            "related_test: ",
            "success_condition: parser test passes",
            "risk: low",
            "parallelism_need: no",
        ]
    )
    with_unsupported_key = "\n".join(
        [
            "goal: fix parser edge case",
            "observed_problem: empty input crashes parser",
            "scope: narrow",
            "out_of_scope: docs",
            "suspect_file: ",
            "suspect_function: ",
            "related_test: ",
            "success_condition: parser test passes",
            "risk: low",
            "parallelism_need: no",
        ]
    )
    with_invalid_parallelism = "\n".join(
        [
            "goal: fix parser edge case",
            "observed_problem: empty input crashes parser",
            "scope: narrow",
            "suspect_file: src/parser.py",
            "suspect_function: parse_tokens",
            "related_test: tests/test_parser.py::test_empty_input",
            "success_condition: parser test passes",
            "risk: low",
            "parallelism_need: medium",
        ]
    )

    assert sr.validate_prompt_output(with_code_block)
    assert sr.validate_prompt_output(with_numbered_plan)
    assert sr.validate_prompt_output(with_unsupported_key)
    assert sr.validate_prompt_output(with_invalid_parallelism)


def test_agent_based_entry_mode_split_policy_exists() -> None:
    task_intake = (sr.REPO_ROOT / "instructions" / "task_intake.md").read_text(
        encoding="utf-8"
    )
    lowered = task_intake.lower()
    assert (
        "applies to `prompt` and `prompt_high` with conditional normalization behavior."
        in lowered
    )
    assert (
        "`prompt_high` (default): detect structured input first; preserve structured input and hand off directly to `orchestrator`; otherwise normalize simple input and hand off."
        in lowered
    )
    assert (
        "`prompt` (lightweight override): detect structured input first; preserve structured input and hand off directly to `orchestrator`; otherwise normalize simple input and hand off."
        in lowered
    )
    assert (
        "normalize raw user input into a compact task spec only when input is simple."
        in lowered
    )
    assert (
        "never aggressively compress structured input; retain critical constraints, deliverables, and acceptance signals."
        in lowered
    )
    assert "no normalize-only primary work-entry path is allowed." in lowered
    assert (
        "normal work entry through `prompt_high` or `prompt` must always continue to `orchestrator`."
        in lowered
    )
    assert "stopping after normalization is a policy violation." in lowered
    assert "canonical handoff fields are required" in lowered
    assert (
        "visible normalization output and internal handoff state are distinct artifacts"
        in lowered
    )
    assert (
        "structured_context must preserve user intent while remaining bounded"
        in lowered
    )


def test_entry_agent_handoff_split_markers_exist() -> None:
    prompt = (sr.REPO_ROOT / "agents" / "prompt.md").read_text(encoding="utf-8").lower()
    prompt_high = (
        (sr.REPO_ROOT / "agents" / "prompt_high.md").read_text(encoding="utf-8").lower()
    )

    assert "delta-only wrapper relative to `agents/prompt_high.md`." in prompt
    assert (
        "inherits shared intake behavior from `instructions/task_intake.md`." in prompt
    )
    assert (
        "inherits entry-agent invariants from `agents/prompt_high.md` and `agents.md`."
        in prompt
    )
    assert "immediately pass canonical handoff state to `orchestrator`." in prompt

    for marker in [
        "never terminate after emitting only the normalized block.",
        "prompt_high and prompt are non-terminal entry agents.",
        "they must always normalize and immediately hand off to orchestrator.",
        "they must never terminate after emitting the normalized contract.",
        "they must never behave like direct build agents.",
        "they must never act as normalization-only agents.",
        "always build one canonical internal handoff state for `orchestrator`.",
        "keep `structured_context` preserved but bounded (never unbounded raw blobs).",
    ]:
        assert marker in prompt_high

    assert (
        "if input is structured, preserve source context and pass canonical handoff state to `orchestrator`."
        in prompt_high
    )
    assert (
        "if input is simple or already 9-line normalized, pass canonical handoff state to `orchestrator`."
        in prompt_high
    )
    assert not (sr.REPO_ROOT / "agents" / "intake.md").exists()


def test_terminal_or_build_like_wording_is_forbidden_for_work_entry_agents() -> None:
    prompt = (sr.REPO_ROOT / "agents" / "prompt.md").read_text(encoding="utf-8").lower()
    prompt_high = (
        (sr.REPO_ROOT / "agents" / "prompt_high.md").read_text(encoding="utf-8").lower()
    )

    forbidden_markers = [
        "can terminate after normalization",
        "may terminate after normalization",
        "is allowed to terminate after normalization",
        "can stop after normalization",
        "may stop after normalization",
        "is a normalization-only agent",
        "acts as a normalization-only agent",
        "is a direct build agent",
        "acts as a direct build agent",
    ]

    for text in [prompt, prompt_high]:
        for marker in forbidden_markers:
            assert marker not in text


def test_mirrored_prompt_high_and_task_intake_keep_handoff_invariant() -> None:
    mirrored_prompt_high = sr.REPO_ROOT / ".opencode" / "agents" / "prompt_high.md"
    mirrored_task_intake = (
        sr.REPO_ROOT / ".opencode" / "instructions" / "task_intake.md"
    )

    if mirrored_prompt_high.exists():
        text = mirrored_prompt_high.read_text(encoding="utf-8").lower()
        assert "prompt_high and prompt are non-terminal entry agents." in text
        assert (
            "they must always normalize and immediately hand off to orchestrator."
            in text
        )
        assert (
            "they must never terminate after emitting the normalized contract." in text
        )
        assert "they must never behave like direct build agents." in text
        assert "they must never act as normalization-only agents." in text
        assert (
            "always build one canonical internal handoff state for `orchestrator`."
            in text
        )
        assert (
            "keep `structured_context` preserved but bounded (never unbounded raw blobs)."
            in text
        )

    if mirrored_task_intake.exists():
        text = mirrored_task_intake.read_text(encoding="utf-8").lower()
        assert (
            "normal work entry through `prompt_high` or `prompt` must always continue to `orchestrator`."
            in text
        )
        assert "stopping after normalization is a policy violation." in text


def test_rejects_all_unsupported_intake_keys() -> None:
    unsupported_keys = ["out_of_scope", "constraints", "inputs", "deliverables"]
    for bad_key in unsupported_keys:
        payload = "\n".join(
            [
                "goal: fix parser edge case",
                "observed_problem: empty input crashes parser",
                "scope: narrow",
                f"{bad_key}: ignored",
                "suspect_file: ",
                "suspect_function: ",
                "related_test: ",
                "success_condition: parser test passes",
                "risk: low",
                "parallelism_need: no",
            ]
        )
        assert sr.validate_prompt_output(payload)


def test_orchestrator_and_debugger_effort_policies_exist() -> None:
    orchestrator = (
        (sr.REPO_ROOT / "agents" / "orchestrator.md")
        .read_text(encoding="utf-8")
        .lower()
    )
    debugger = (
        (sr.REPO_ROOT / "agents" / "debugger.md").read_text(encoding="utf-8").lower()
    )

    assert "micro: `gpt-5.4` with `low` reasoning effort." in orchestrator
    assert "standard: `gpt-5.4` with `medium` reasoning effort." in orchestrator
    assert "deep: `gpt-5.4` with `high` reasoning effort." in orchestrator
    assert (
        "xhigh is allowed only for repeated hard failures or redesign escalation."
        in debugger
    )


def test_prompt_entry_agents_can_only_handoff_to_orchestrator_first() -> None:
    config = json.loads((sr.REPO_ROOT / "opencode.jsonc").read_text(encoding="utf-8"))

    prompt_high_perms = config["agent"]["prompt_high"]["permission"]
    assert prompt_high_perms.get("*") == "deny"
    for tool_name, rule in prompt_high_perms.items():
        if tool_name in {"*", "task", "__originalKeys"}:
            continue
        assert rule != "allow"
    task_perms = prompt_high_perms["task"]
    assert task_perms.get("*") == "deny"
    allowed_targets = {
        name for name, decision in task_perms.items() if decision == "allow"
    }
    assert allowed_targets == {"orchestrator"}

    prompt_task_perms = config["agent"]["prompt"]["permission"]["task"]
    prompt_allowed_targets = {
        name for name, decision in prompt_task_perms.items() if decision == "allow"
    }
    assert prompt_allowed_targets == {"orchestrator"}


def test_structured_passthrough_detection_and_preservation() -> None:
    structured_payload = "\n".join(
        [
            "goal: preserve user-provided structure",
            "observed_problem: strict normalization is lossy",
            "constraints: keep original fields",
            "deliverables: pass through to orchestrator",
        ]
    )
    normalized_payload = "\n".join(
        [
            "goal: preserve structure",
            "observed_problem: compression occurs",
            "scope: moderate",
            "suspect_file: agents/prompt_high.md",
            "suspect_function: ",
            "related_test: tests/test_prompt_entry_agents.py::test_structured_passthrough_detection_and_preservation",
            "success_condition: structured input remains unchanged",
            "risk: medium",
            "parallelism_need: no",
        ]
    )

    assert sr.detect_structured_input(structured_payload) is True
    assert (
        sr.apply_prompt_entry_intake(structured_payload, normalized_payload)
        == structured_payload
    )


def test_simple_input_still_uses_normalization_path() -> None:
    simple_payload = "fix parser edge case in narrow scope"
    normalized_payload = "\n".join(
        [
            "goal: fix parser edge case",
            "observed_problem: parser fails on empty input",
            "scope: narrow",
            "suspect_file: src/parser.py",
            "suspect_function: parse_tokens",
            "related_test: tests/test_parser.py::test_empty_input",
            "success_condition: empty-input parser test passes",
            "risk: low",
            "parallelism_need: no",
        ]
    )

    assert sr.detect_structured_input(simple_payload) is False
    assert (
        sr.apply_prompt_entry_intake(simple_payload, normalized_payload)
        == normalized_payload
    )


def test_canonical_handoff_state_is_complete_and_bounded() -> None:
    structured_payload = "\n".join(
        [
            "goal: preserve structure",
            "observed_problem: role contracts drift",
            "scope: moderate",
            "success_condition: route remains deterministic",
            "risk: medium",
            "parallelism_need: no",
            "suspect_file: agents/prompt_high.md",
            "suspect_function: build_entry_handoff_state",
            "related_test: tests/test_prompt_entry_agents.py::test_canonical_handoff_state_is_complete_and_bounded",
            "constraints: keep context bounded",
        ]
    )
    normalized_payload = "\n".join(
        [
            "goal: preserve structure",
            "observed_problem: role contracts drift",
            "scope: moderate",
            "suspect_file: agents/prompt_high.md",
            "suspect_function: build_entry_handoff_state",
            "related_test: tests/test_prompt_entry_agents.py::test_canonical_handoff_state_is_complete_and_bounded",
            "success_condition: route remains deterministic",
            "risk: medium",
            "parallelism_need: no",
        ]
    )

    handoff = sr.build_entry_handoff_state(
        structured_payload,
        normalized_payload,
        "prompt_high",
    )
    assert sr.validate_handoff_state(handoff) == []
    assert handoff["source_input_type"] == "structured_passthrough"
    assert handoff["source_input_preserved"] is True
    structured_context = handoff["structured_context"]
    assert structured_context["kind"] == "bounded_text"
    assert structured_context["line_count"] <= sr.STRUCTURED_CONTEXT_MAX_LINES
    assert len(structured_context["content"]) <= sr.STRUCTURED_CONTEXT_MAX_CHARS


def test_already_normalized_9line_input_keeps_normalized_source_type() -> None:
    normalized_payload = "\n".join(
        [
            "goal: preserve normalized shape",
            "observed_problem: avoid structured reclassification",
            "scope: narrow",
            "suspect_file: scripts/smoke_runner.py",
            "suspect_function: build_entry_handoff_state",
            "related_test: tests/test_prompt_entry_agents.py::test_already_normalized_9line_input_keeps_normalized_source_type",
            "success_condition: source type remains normalized_9line",
            "risk: low",
            "parallelism_need: no",
        ]
    )

    handoff = sr.build_entry_handoff_state(
        normalized_payload,
        normalized_payload,
        "prompt_high",
    )
    assert handoff["source_input_type"] == "normalized_9line"
    assert handoff["source_input_preserved"] is False
    assert handoff["structured_context"]["kind"] == "none"


def test_task_schema_stays_entry_only_without_routing_preflight_fields() -> None:
    schema = json.loads(
        (sr.REPO_ROOT / "schemas" / "task.schema.json").read_text(encoding="utf-8")
    )
    properties = schema.get("properties", {})
    for forbidden in ["selected_agent", "selected_path", "preflight", "rollback_plan"]:
        assert forbidden not in properties
