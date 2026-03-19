Planning and reasoning modes

Cheap triage is mandatory before planning.

Triage outputs
- complexity: low | medium | high
- risk: low | medium | high
- parallelization_value: low | medium | high
- needs_deep_decomposition: true | false

Mode rules

MICRO
- Tiny scope and low risk.
- Minimal planning overhead.
- Single-agent execution only.
- Reasoning effort: low.

STANDARD
- Moderate complexity with bounded decomposition.
- Parallelism discouraged unless policy gating passes.
- Reasoning effort: medium.

DEEP
- High risk, broad coupling, or unresolved interfaces.
- Contract-first planning before broad edits.
- Optional parallel branches only after gating passes.
- Reasoning effort: high.

Model and effort policy
- `auto` is the default: the system selects model variant and effort based on planning mode.
- Orchestrator mode-effort mapping for reference: MICRO → low, STANDARD → medium, DEEP → high.
- Debugger escalates to high for repeated localized hard failures.
- xhigh is allowed only when repeated localized failure persists or redesign escalation is explicitly justified.
