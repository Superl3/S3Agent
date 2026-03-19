Output contracts — Routing (orchestrator-use)

routing_output
- selected_skill
- selected_agent
- selected_path: [entry_agent, orchestrator, execution_agent]
- handoff_sequence
- selected_mode
- packet_required
- packet_gate_status
- patch_target
- failure_class
- preflight: {scope, allowed_files, risk, test_plan, rollback_plan?}
- preflight fast-path eligibility: see `instructions/phase_gates.md`.
- termination_status
- termination_reason
- skill
- agent
- mode
- parallel
- escalation
- reason_codes
- packet exhaustion is represented by `packet_gate_status=failed`; this state must match execution trace/notepad packet exhaustion state.
- `max_orchestrator_invocations`: maximum orchestrator calls per user prompt is 2 (1 initial routing + 1 re-route on failure). Exceeding this cap is a harness failure regardless of cause.

routing hard-fail rules
- Forbidden placeholder routing tokens: noop, bad, read1, read2, switch, ignore.
- Forbidden meaningless delegation content: implement input-priority mode, show changed files, run syntax checks, n/a, accidental, h, stop.
- Pre-dispatch gate priority is fixed: invalid_task > no_op > review_only > improve_only.
- For delegated routing (`termination_status=delegated`), selected_skill, selected_agent, and selected_path are required.
- selected_path must be an ordered structured path including `entry_agent -> orchestrator -> execution_agent`.
- For pre-dispatch gate hits (`termination_status=terminated`), selected_agent must be unset and handoff_sequence must be empty.
- `selected_mode`, `packet_required`, `packet_gate_status`, `patch_target`, and `failure_class` are required.
- `termination_status` and `termination_reason` are required.
- selected_skill and selected_agent must match a valid skill-agent mapping from `SKILLS.md`.
- For repair-oriented routing, `patch_target` must be non-empty.
- Preflight artifact required with `scope`, `allowed_files`, `risk`, and `test_plan`.
- `rollback_plan` required only when `risk=high` or `scope=broad`.
- Canonicalization required: sorted JSON keys and normalized paths.
- Fingerprints are observational-only metadata and must not change routing/test gating behavior.
- Any invalid routing output must trigger immediate harness failure.
- Broad/open-ended work bypasses packet_runner by default.
- Orchestrator must not invoke itself recursively or re-enter within the same user prompt turn beyond the `max_orchestrator_invocations` cap.

invalid-task handling
- Empty/invalid task input must not produce delegation calls.
- Empty/invalid task input must not emit user-facing chatter.
- Stop/termination on invalid input must leave one normal final report only.
