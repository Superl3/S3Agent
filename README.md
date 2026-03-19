# OpenCode Harness

Lean deterministic harness for patch-first coding workflows.

## Runtime topology

User -> `prompt_high` or `prompt` -> `orchestrator` -> `implementer`/`debugger`/`tester`/`reviewer`
User -> `harness_review`

Final user-visible responses are rendered by `scripts/final_renderer.py` using lazy-loaded markdown templates in `instructions/render_templates/`.
Internal structured outputs stay unchanged and are passed directly to the final renderer (no intermediate refine/summarize output transform).

## Entry agent behaviors

- `prompt_high` (default): run intake processing (deterministic refine-or-bypass), then immediately hand off to `orchestrator`.
- `prompt` (lightweight override): run intake processing (deterministic refine-or-bypass), then immediately hand off to `orchestrator`.
- `harness_review`: diagnosis only; never modifies files.
- `harness_improve`: internal-only plan proposal until explicit approval.
- prompt_high and prompt are non-terminal entry agents.
- They must always normalize and immediately hand off to orchestrator.
- Their direct tool permissions are denied; only `task -> orchestrator` handoff is allowed.
- orchestrator is a non-executing control-plane delegator and must delegate normal work to execution agents.
- harness_improve is internal-only and approval-gated for any mutation.

Normal user input defaults to `prompt_high` and never stops at intake processing.

Default entry policy
- Default entry is `prompt_high`; use `prompt` only when users explicitly request minimal or faster reasoning.
- Use `prompt_high` when ambiguity, stakes, or normalization quality concerns justify extra effort.
- Manual user entry should target entry agents only; all other agents are runtime-internal subagents.

## Why this harness exists

- Deterministic routing from structured policy data.
- Compact prompts and lean context loading.
- Patch-first and bug-localization-first repair posture.
- Single-agent default unless escalation is justified.
- Parallel exploration is read-only only (inspection/search/docs/schema/reference/repo exploration).
- Parallel mutation is forbidden (patching, test execution, validation, runtime state mutation).
- Single-writer semantics are required for any mutating work.
- Deterministic test matrix: logic->unit, UI->smoke, configuration->lint/typecheck, mixed/cross-module->at least two validations, unknown->one validation or {ENV_BLOCKED, NO_TESTS_DEFINED, VALIDATION_SKIPPED}.
- Global runtime templates support expected-vs-actual validation using execution trace and scenario expectation artifacts.
- Runtime evidence source-of-truth is append-only `runtime/execution_trace_archive.md`; `runtime/execution_trace_latest.md` is convenience latest view.
- Health precedence is strict: runtime evidence > tests > docs; missing required runtime evidence hard-fails validation.

## Category-driven routing

- Optional normalized field: `category`.
- Optional normalized `category` can provide deterministic routing preference when present.
- Allowed values: `feature_implementation`, `bug_fix`, `failing_test_repair`, `integration_hardening`, `harness_review`, `harness_improve`, `investigation`, `refactor`.
- When category is present and valid, orchestrator applies a deterministic preferred route.
- When category is missing, deterministic fallback uses infer-from-task routing.

## Task-local execution notepad

- Use `runtime/execution_notepad_template.md` as an append-only task scratchpad.
- Keep notes concise and human-readable.
- Do not store large logs or full tool outputs in the notepad.

## Deterministic routing model

`route = f(canonical_handoff_state, triage_result, skills_registry)`

- `canonical_handoff_state` is produced by `prompt` or `prompt_high` under shared `task_intake.md` rules.
- `triage_result` is produced by `orchestrator`.
- `skills_registry` maps to internal runtime endpoints.

Routing output contract stays fixed:
- `selected_skill`
- `selected_agent`
- `selected_path`
- `selected_mode`
- `packet_required`
- `packet_gate_status`
- `patch_target`
- `failure_class`
- `preflight`
- `skill`
- `agent`
- `mode`
- `parallel`
- `escalation`
- `reason_codes`

Preflight artifact rules:
- Required fields: `scope`, `allowed_files`, `risk`, `test_plan`
- Fast-path eligibility must be exactly: `scope == narrow AND risk != high AND allowed_files_count <= 3 (UNIQUE normalized paths) AND success_check present`
- Conditional field: `rollback_plan` only when `risk=high` or `scope=broad`
- Missing/invalid preflight must terminate with `invalid_task` or `review_only`
- Canonicalization required: sorted JSON keys + normalized paths
- Canonicalization is required for orchestrator decision artifacts.

Fingerprint fields:
- `policy_fp`, `task_fp`, `route_fp` are observational-only metadata on trace/report surfaces.
- Fingerprints must not gate routing, mutation, or validation behavior.

Packet runner fast-path rules:
- `fast_path_attempt` is pre-budget only and does not consume `retry_strategy.max_attempts`
- Fast-path success still records verifier result and validation proof

Core packet capability note:
- Packet fast-path and packet micro-loop are core architecture capabilities; preflight/eligibility gates control when they run, and evidence quality/coverage is a KPI (not a core-classification gate).

## Validation commands

Run from repository root:

```bash
python scripts/smoke_runner.py
python scripts/smoke_runner.py --json
python scripts/validate_harness.py
python scripts/validate_harness.py --json
pytest -q
```

## Runtime efficiency plugin

Recommended optimization dependency: `@tarquinen/opencode-dcp@latest`.

## Extension guardrails

- Keep the harness lean; avoid new orchestration layers.
- Prefer deterministic checks over prose-only guidance.
- Do not duplicate large instruction blocks across files.
