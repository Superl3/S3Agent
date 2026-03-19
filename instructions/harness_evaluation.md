Harness evaluation policy

Purpose
- Analyze execution failures and major inefficiency events.
- Propose at most one minimal harness adjustment.
- Use task-local execution notepad summaries to preserve debugger/reviewer/harness_review context.

Task-local execution notepad
- Use `runtime/execution_notepad_template.md` as the task-local append-only scratchpad.
- Keep entries small and human-readable.
- Do not paste large logs or full tool outputs.
- Preferred usage: debugger (repair notes), reviewer (quality findings), harness_review (diagnostic evidence trail).

Evidence model
- Correlate failure logs, validation outputs, and execution trace summaries.
- `runtime/execution_trace_archive.md` is source-of-truth evidence; `runtime/execution_trace_latest.md` is latest convenience view.
- Execution trace is evidence, not standalone proof.
- Treat role-boundary collapse (entry/orchestrator/executor contract breaks) as high-priority evidence.

Execution trace semantics
- Capture entry agent, orchestrator decision, subagent routing, major tool invocations, validation events, compression signals, and final result.
- Distinguish intended path from observed runtime handoffs:
  - `selected_path` = intended execution path.
  - `handoff_sequence` = actual agent-to-agent handoffs.

Actual trace vs expected scenario comparison
- Compare actual trace (`runtime/execution_trace_template.md`) against expected scenario (`runtime/scenario_expectation_template.md`).
- Always compare at minimum: `selected_path`, `handoff_sequence`, `validation_sequence`, `routing_validation_status`, and `result`.
- Treat mismatch as supporting evidence for deterministic diagnosis, not as automatic mutation trigger.
- If mismatch is allowed, `allowed_deviation` must be explicit and single-scope.

Trace verbosity limits
- Capture only macroscopic steps.
- Allowed: agent transitions, tool categories, validation checkpoints, compression signals.
- Forbidden: full stack traces, full tool outputs, detailed debug logs, raw command payloads.

Bounded rules
- Do not propose large redesigns.
- Do not propose new orchestration layers.
- Do not propose speculative changes.
- `minimal_fix` must be one concise rule-style recommendation on a single line.
- `minimal_fix` must not contain multiple recommendations.

Required evaluation output format
problem_class:
evidence:
minimal_fix:
risk:
expected_effect:

Advisory-only
- Evaluation is advisory only.
- The harness must never mutate its own structure automatically.
