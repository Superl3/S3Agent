Failure memory policy

Format
- Store short append-only entries using keys:
  - ID
  - TRIGGER
  - RULE
  - CHECK
  - EXAMPLE

When to append
- Add a rule after the same failure pattern appears at least twice.
- Keep each rule concise and action-oriented.

Guardrails
- Append only; do not rewrite prior rules unless incorrect.
- Long retrospective prose is forbidden in the rule store.
- Rule text must map to a deterministic check step.
- If rule reports include `policy_fp`, `task_fp`, or `route_fp`, treat them as observational metadata only.
