scenario_id: <deterministic scenario identifier>
expected_selected_path: <entry_agent -> orchestrator -> execution_agent>
expected_handoff_sequence: <actual expected agent-to-agent handoffs>
expected_validation_sequence: <expected validation checkpoints>
expected_routing_validation_status: <PASS|FAIL>
expected_result: <PASS|PARTIAL|FAIL|ENV_BLOCKER>
allowed_deviation: <none|single documented deviation>
comparison_policy: compare selected_path/handoff_sequence/validation_sequence/routing_validation_status/result against actual trace

Rules
- Keep fields concise and macroscopic.
- Use this template only as expected evidence for comparison with `runtime/execution_trace_template.md`.
- `expected_selected_path` is expected intended flow; `expected_handoff_sequence` is expected observed handoff flow.
- If `expected_routing_validation_status` is `FAIL`, expected trace must enumerate invalid routing tokens.
- `allowed_deviation` must stay explicit and bounded.
