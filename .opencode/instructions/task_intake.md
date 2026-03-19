Task intake normalization

Applies to `prompt` and `prompt_high` with conditional normalization behavior.
Only model and reasoning-effort policy differ between these two entry agents.

Entry behavior by agent
- `prompt_high` (default): detect structured input first; preserve structured input and hand off directly to `orchestrator`; otherwise normalize simple input and hand off.
- `prompt` (lightweight override): detect structured input first; preserve structured input and hand off directly to `orchestrator`; otherwise normalize simple input and hand off.

Rules
- Normalize raw user input into a compact task spec only when input is simple.
- Treat input as structured when it already provides keyed fields, JSON/YAML-like objects, or explicit sectioned requirements.
- Preserve structured input shape, field intent, and ordering whenever safely possible.
- Never aggressively compress structured input; retain critical constraints, deliverables, and acceptance signals.
- Refiner-eligible simple input is limited to a single-line, single-sentence plain-text request with no structured markers, no fenced/code formatting, no explicit section headers, max 24 words, and max 160 characters.
- For refiner-eligible simple input, build a 9-line refinement candidate, then compare character length against original input.
- If original input length is less than or equal to refinement-candidate length, bypass refinement and forward original prompt text unchanged.
- Refine only when refinement-candidate length is strictly shorter than original input length.
- Any non-eligible input must bypass refiner and be forwarded unchanged.
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
- `prompt_high` and `prompt` must not terminate at the normalized block; they hand off to `orchestrator`.
- No normalize-only primary work-entry path is allowed.
- Normal work entry through `prompt_high` or `prompt` must always continue to `orchestrator`.
- Stopping after normalization is a policy violation.

Internal orchestrator handoff contract (canonical state)
- Canonical handoff fields are required: `goal`, `observed_problem`, `scope`, `success_condition`, `risk`, `parallelism_need`, `suspect_file`, `suspect_function`, `related_test`, `source_input_type`, `source_input_preserved`, `structured_context`, `selected_entry_agent`.
- Optional canonical field: `category` may be present when reliably inferable; if present it must be one of `feature_implementation`, `bug_fix`, `failing_test_repair`, `integration_hardening`, `harness_review`, `harness_improve`, `investigation`, `refactor`.
- Canonical handoff state must be deterministic for simple refined input, simple passthrough input, strict 9-line input, and structured passthrough input.
- Visible normalization output and internal handoff state are distinct artifacts and must not be conflated.
- structured_context must preserve user intent while remaining bounded (max 12 lines, max 1000 chars total, max 180 chars per line).
- Unbounded raw structured blobs are forbidden.

Output formatting requirements
- For simple-input normalization output, emit exactly 9 lines.
- For simple-input normalization output, emit no blank lines.
- For simple-input normalization output, keys must be lowercase and in exact required order.
- For simple-input normalization output, each line must be `key: value`.
- For simple-input normalization output, each value must be single-line and concise.
- For simple-input normalization output, leave `suspect_file`, `suspect_function`, and `related_test` blank when not safely inferable.
- For simple-input normalization output, reject fenced code blocks and extra keys.
- For simple-input normalization output, reject unsupported keys, including `out_of_scope`, `constraints`, `inputs`, and `deliverables`.
- For simple-input normalization output, `parallelism_need` may only be `no` or `yes`.
- For refiner-bypass output, preserve original prompt text unchanged.

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
