task: <task_id or concise task label>
entry_agent: <prompt_high|prompt|harness_review|harness_improve>
selected_mode: <MICRO|STANDARD|DEEP>
selected_path: <entry_agent -> orchestrator -> execution_agent>
routing_validation_status: <PASS|FAIL>
invalid_routing_tokens: <none|noop,bad,read1,read2,switch,ignore,n/a,na,accidental,h,stop>
tool_sequence: read, grep, bash, apply_patch, test
handoff_sequence: <actual agent-to-agent handoffs>
validation_sequence: <major validation checkpoints>
fingerprints: policy_fp=<sha256-or-na>; task_fp=<sha256-or-na>; route_fp=<sha256-or-na>
compression_events: dcp_triggered=<yes|no>; compress_mode=<none|manual|auto>; active_state_rehydrated=<yes|no>
fast_path_attempt: status=not_attempted; budget_exempt=true; allowed_files_count=2; verifier_result=na; validation_proof=na
packet_exhaustion: <none|retry_pending|exhausted>
result: <PASS|PARTIAL|FAIL|ENV_BLOCKER>
trace_status: <partial|complete>

Rules
- Keep fields concise and macroscopic.
- Record flow only; do not include full payloads or large logs.
- Append finalized runs to `runtime/execution_trace_archive.md` (source-of-truth) and refresh `runtime/execution_trace_latest.md` (latest convenience view).
- `selected_path` is intended flow; `handoff_sequence` is actual handoff flow.
- `handoff_sequence` format is deterministic ordered chain `agent_a -> agent_b -> agent_c`.
- For scenario validation, compare this actual trace with `runtime/scenario_expectation_template.md` using selected_path/handoff_sequence/validation_sequence/routing_validation_status/result.
- Role-correctness scenario trigger is explicit in latest scenario artifact and requires all of `required_agents`, `forbidden_agents`, and `expected_handoff_order`.
- In normal work execution, `selected_path` must include ordered `entry_agent -> orchestrator -> execution_agent`.
- If `routing_validation_status` is `FAIL`, `invalid_routing_tokens` must explicitly list offending tokens.
- `tool_sequence` must use high-level tool categories only.
- Fingerprints are observational only and must not gate routing, mutation, or validation behavior.
- `fast_path_attempt` is pre-budget only and must not consume retry budget from `retry_strategy.max_attempts`.
- `packet_exhaustion` must be explicit and consistent with execution notepad and orchestrator packet state.
