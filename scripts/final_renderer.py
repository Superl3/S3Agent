#!/usr/bin/env python3
"""Final user-facing markdown rendering helpers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Mapping

MISSING_EVIDENCE_CLASS = "missing_evidence_bundle_for_harness_diagnosis"


def _as_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        try:
            return int(text)
        except ValueError:
            return 0
    return 0


def _status_token(payload: Mapping[str, object]) -> str:
    status = _as_text(payload.get("status", "")).lower()
    if status:
        return status

    overall = payload.get("overall")
    if isinstance(overall, Mapping):
        return _as_text(overall.get("status", "")).lower()
    return ""


def _is_blocked(payload: Mapping[str, object]) -> bool:
    if bool(payload.get("blocked", False)):
        return True
    if _as_text(payload.get("termination_status", "")).lower() == "terminated":
        return True
    return _status_token(payload) in {
        "blocked",
        "fail",
        "failed",
        "error",
        "env_blocked",
    }


def _is_success(payload: Mapping[str, object]) -> bool:
    if bool(payload.get("successful", False)):
        return True
    return _status_token(payload) in {"pass", "success", "succeeded", "ok", "complete"}


def _has_improve_input_draft(payload: Mapping[str, object]) -> bool:
    draft = payload.get("improve_input_draft")
    if isinstance(draft, Mapping):
        return bool(draft)
    return bool(_as_text(draft))


def select_template_name(payload: Mapping[str, object]) -> str:
    if _as_text(payload.get("problem_class", "")).lower() == MISSING_EVIDENCE_CLASS:
        return "harness_review_insufficient_evidence.md"
    if _as_text(
        payload.get("entry_agent", "")
    ).lower() == "harness_review" and _is_success(payload):
        return "harness_review_success.md"
    if _has_improve_input_draft(payload):
        return "harness_improve_proposal.md"
    if _is_success(payload):
        return "general_success.md"
    if _is_blocked(payload):
        return "general_blocked.md"
    return "general_success.md"


@lru_cache(maxsize=16)
def _load_template(root_text: str, template_name: str) -> str:
    path = Path(root_text) / "instructions" / "render_templates" / template_name
    return path.read_text(encoding="utf-8")


def _state_summary(payload: Mapping[str, object], template_name: str) -> str:
    custom = _as_text(payload.get("state_summary", ""))
    if custom:
        return custom
    if template_name == "harness_review_insufficient_evidence.md":
        return "Insufficient evidence for harness diagnosis."
    if template_name == "harness_improve_proposal.md":
        return "A minimal harness improvement proposal is ready for review."
    if template_name == "general_blocked.md":
        return "Execution is currently blocked."
    return "Request completed successfully."


def _details(payload: Mapping[str, object], template_name: str) -> str:
    custom = _as_text(payload.get("details", ""))
    if custom:
        return custom

    if template_name == "harness_improve_proposal.md":
        draft = payload.get("improve_input_draft")
        if isinstance(draft, Mapping):
            change = _as_text(draft.get("proposed_change", ""))
            effect = _as_text(draft.get("expected_effect", ""))
            text = change or "A focused minimal change has been prepared."
            if effect:
                return f"{text} Expected effect: {effect}."
            return text
        return "A focused minimal change has been prepared."

    if template_name == "harness_review_insufficient_evidence.md":
        return "Provide the required trace and validation evidence so diagnosis can continue."

    failed_count = _as_int(payload.get("failed_count", 0))
    passed_count = _as_int(payload.get("passed_count", 0))
    total_count = _as_int(payload.get("total_count", 0))
    if template_name == "general_blocked.md":
        if total_count:
            return (
                f"{failed_count} of {total_count} checks are failing "
                f"({passed_count} currently passing)."
            )
        return "One or more required checks are failing."

    if total_count:
        return f"{passed_count} of {total_count} checks are passing."
    return "All required checks passed."


def _next_steps(payload: Mapping[str, object], template_name: str) -> str:
    custom = _as_text(payload.get("next_steps", ""))
    if custom:
        return f"- Next step: {custom}"

    if template_name == "harness_review_insufficient_evidence.md":
        return (
            "- Next step: provide a complete evidence bundle and rerun harness review."
        )
    if template_name == "harness_improve_proposal.md":
        return "- Next step: reply with approval to apply the proposal."
    if template_name == "general_blocked.md":
        return "- Next step: resolve blockers, then rerun the validation flow."
    return ""


def render_markdown_response(payload: Mapping[str, object], root: Path) -> str:
    template_name = select_template_name(payload)
    template = _load_template(str(root), template_name)
    rendered = template.format(
        state_summary=_state_summary(payload, template_name),
        details=_details(payload, template_name),
        next_steps=_next_steps(payload, template_name),
    ).strip()
    return rendered + "\n"
