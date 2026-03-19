Lazy context loading and DCP pruning policy

Default posture
- Keep DCP enabled and token-efficient.
- Protect active task-critical state from pruning.
- Prune stale and low-value context aggressively.
- Preserve runtime flow integrity: `prompt_high`/`prompt` intake processing (refine-or-bypass) -> `orchestrator` triage -> patch-first execution.

Allowed context sources
- Active agent role file under `agents/`.
- Required instruction modules only.
- Task-scoped runtime state under `runtime/`.
- Repository files directly related to the current task.

Forbidden by default
- Loading the full repository into prompt context.
- Broadcasting every policy file to every subagent.
- Copying large docs into prompts when file references are enough.

Load order
1) Role definition
2) Required policy module(s)
3) Task runtime state
4) Relevant code/test files

Context tiering for DCP pruning

Tier 1: never prune
- Current intake output (`prompt`/`prompt_high` 9-line refinement output or unchanged passthrough text).
- Current canonical internal handoff state (`source_input_type`, `source_input_preserved`, bounded `structured_context`, `selected_entry_agent`).
- Currently selected entry agent identity (`prompt_high` default or `prompt` override).
- Current `scope`, `success_condition`, `risk`, and `parallelism_need`.
- Current orchestrator triage result summary.
- Current selected mode (`MICRO`/`STANDARD`/`DEEP`) and current routing state.
- Current packet and gate state (`packet_required`, `packet_gate_status`).
- Current execution-context contract (`workspace kind`, `shell family`, and `path semantics`) when user-declared or task-critical.
- Current failure class.
- Current `suspect_file`, `suspect_function`, and `related_test` clues.
- Current patch target and active bug-localization clues.
- Current `patch_target` and `failure_class` when repair is active.
- Localized retry and escalation state for the active repair loop.
- Currently referenced failure-memory entries and failure rules.

Tier 2: prune cautiously (summarize before dropping)
- Recent execution summaries.
- Recent smoke and validator result summaries.
- Nearest relevant example-task reference.
- Selected instruction names used by the active task.
- Recent routing summary and triage rationale.

Tier 3: aggressive pruning allowed
- Stale narrative discussion.
- Repeated planning prose.
- Old completed-task chatter.
- Repeated explanations.
- Low-value historical logs.
- Duplicated descriptive context.

Explicit negative rules
- Do not prune the active intake task contract (9-line refinement output or unchanged passthrough text).
- Do not prune explicit user/runtime environment constraints (for example: WSL-only execution).
- Do not prune current bug-localization clues.
- Do not prune current triage and routing state.
- Do not prune active repair-loop state.
- Do not prune the current selected mode and routing state.
- Do not prune currently relevant failure-memory entries.

Explicit positive rules
- Prefer pruning long-form discussion history.
- Prefer pruning duplicated plan text.
- Prefer pruning superseded summaries.
- Prefer pruning completed-task context that no longer affects the active task.

Runtime-oriented verification checklist after pruning
1) The current task contract is still present and unchanged.
2) Active `suspect_file`, `related_test`, and patch target are still present.
3) Current failure clues, failure class, and failure-memory references are still present.
4) Old narrative context was pruned more aggressively than active task state.
