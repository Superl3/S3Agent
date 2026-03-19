Task intake normalization

Applies to `S3Agent` with conditional normalization behavior.
Only model and reasoning-effort policy differ based on configuration.

Entry behavior by agent
- `S3Agent`: detect structured input first; preserve structured input and hand off directly to `orchestrator`; otherwise normalize simple input and hand off.

Rules
- All user input must bypass refinement and be forwarded directly to canonical handoff state construction.
- Visible normalization output is simply the original input text unchanged.
- Treat input as structured when it already provides keyed fields, JSON/YAML-like objects, or explicit sectioned requirements.
- Preserve structured input shape, field intent, and ordering whenever safely possible.
- Never aggressively compress structured input; retain critical constraints, deliverables, and acceptance signals.
- Visible passthrough ordering rule: preserve structured passthrough field ordering whenever safely possible.
- Internal canonicalization rule: only canonical internal handoff state uses sorted JSON keys and normalized file paths before handoff.
- Task intake normalization is entry-only; routing and preflight fields are forbidden at this stage.
- Narrow scope conservatively from user-visible evidence.
- Preserve explicit runtime constraints from the user (for example: WSL-only execution) inside existing output fields.
- Do not emit code.
- Do not emit numbered implementation plans.
- Do not emit bullet lists.
- Do not emit narrative paragraphs.
- Do not widen scope without direct evidence.

Handoff rules
- `S3Agent` must not terminate at the normalized block; it hands off to `orchestrator`.
- No normalize-only primary work-entry path is allowed.
- Normal work entry through `S3Agent` must always continue to `orchestrator`.
- Stopping after normalization is a policy violation.

Internal orchestrator handoff contract (canonical state)
- Canonical handoff fields are required: `goal`, `observed_problem`, `scope`, `success_condition`, `risk`, `parallelism_need`, `suspect_file`, `suspect_function`, `related_test`, `source_input_type`, `source_input_preserved`, `structured_context`, `selected_entry_agent`.
- Optional canonical field: `category` may be present when reliably inferable; if present it must be one of `feature_implementation`, `bug_fix`, `failing_test_repair`, `integration_hardening`, `harness_review`, `harness_improve`, `investigation`, `refactor`.
- Canonical handoff state must be deterministic for simple refined input, simple passthrough input, strict 9-line input, and structured passthrough input.
- Visible normalization output and internal handoff state are distinct artifacts and must not be conflated.
- structured_context must preserve user intent while remaining bounded (max 12 lines, max 1000 chars total, max 180 chars per line).
- Unbounded raw structured blobs are forbidden.

Output formatting requirements
- For normal passthrough output, always preserve original prompt text unchanged.
- Output formatting for the canonical handoff state must use the exact required template fields.
- Canonical handoff state keys must be lowercase and in exact required order.
- Canonical handoff state each value must be single-line and concise.
- Leave `suspect_file`, `suspect_function`, and `related_test` blank when not safely inferable.
- Reject unsupported keys, including `out_of_scope`, `constraints`, `inputs`, and `deliverables`.
- `parallelism_need` may only be `no` or `yes`.

required_output_template_start
goal: <concise objective>
observed_problem: <concise observed issue>
scope: <narrow|moderate|broad>
suspect_file: <path or blank>
suspect_function: <symbol or blank>
related_test: <test target or blank>
success_condition: <measurable pass condition>
risk: <low|medium|high>
parallelism_need: <no|yes>
required_output_template_end
