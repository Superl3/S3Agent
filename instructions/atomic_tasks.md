Atomic task policy

Goal
- When decomposition is triggered by failure or structural evidence, break work into
  smallest meaningful units that can be validated independently.
- Decomposition is NOT the default; it is triggered by retry budget exhaustion or
  structural failure (see `instructions/test_gated_execution_policy.md` §2, §3).

Rules
- One task should target one clear acceptance condition.
- Keep file touch-set as small as possible before expanding scope.
- Split by ownership boundaries, not by arbitrary line counts.
- Encode dependencies explicitly (`blocked_by`) for queued tasks.
- Prefer patch-oriented subtasks over rewrite-oriented subtasks.

Task split checklist
- Is the success condition testable in one command or short command list?
- Can failure be localized to a specific module if this task fails?
- Can this task be reviewed without loading unrelated files?

Stop splitting when
- Further split would add coordination cost without lowering risk.
- The task already has narrow scope and deterministic acceptance checks.
- The failure that triggered decomposition is now isolated to one unit.
