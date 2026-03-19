CONTEXT LIMIT WARNING: preserve active task state first.

Do not interrupt active work with broad compression directives.

Compression policy for this condition:
- Only compress small, closed, summary-safe ranges.
- Never compress the active working slice.
- Preserve Tier-1 active-state checklist explicitly and verbatim.
- Do not replace the active-state checklist with a summary.
- Prioritize pruning stale narrative history over active task state.

Tier-1 active-state checklist:
- normalized task contract
- scope
- success_condition
- risk
- parallelism_need
- suspect_file
- suspect_function
- related_test
- selected_mode
- failure_class
- patch_target
- retry_state
- escalation_state

Range selection guidance:
- Start from older, resolved, low-value history.
- Prefer multiple small independent compressions over one large compression.
- Skip any range that contains current triage, current patch target, or active retry/escalation state.
