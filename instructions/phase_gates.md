Phase-gated execution policy

Purpose
- Prevent large-task drift by forcing bounded execution packets with explicit scope and pass/fail gates.
- Packet fast-path and packet micro-loop are core runtime capabilities; eligibility and evidence quality rules govern operation, not capability classification.

When packets are required
- Execution packets are required when any condition is true:
  - mode = DEEP
  - risk = high
  - scope = broad
  - expected_touched_files > 3
- For narrow, low-risk, non-DEEP work with expected_touched_files <= 3, packetization is not required.

expected_touched_files source rule
- expected_touched_files must come from orchestrator packet planning metadata or explicit user/task context.
- Implementer/debugger/tester/reviewer must not invent ad hoc expected_touched_files values during execution.

Preflight artifact requirements (orchestrator-side)
- Orchestrator artifacts must include `scope`, `allowed_files`, `risk`, and `test_plan` before delegation.
- `rollback_plan` is required only when `risk = high` or `scope = broad`.
- Missing or invalid preflight artifact must terminate with `invalid_task` or `review_only`.
- Preflight artifact canonicalization is required: sorted JSON keys and normalized paths.

Deterministic test-selection matrix: see `instructions/testing_rules.md`.

Execution packet format
- Packets must use `runtime/execution_packet_template.md` fields only.
- Packets must validate against `schemas/packet.schema.json`.
- `packet_class` enum is minimal: `generic_packet`, `failing_test_repair`.
- `parallel_mode` enum is minimal: `off`, `read_only`.
- `phase_name` must be a concise slug-style identifier: lowercase letters/numbers with internal hyphens only (example: `phase-01-parser-fix`).
- `success_check` must be a structured object, not free-form text.
- `retry_strategy` must be structured and non-blind; it must include `observed_vs_expected`, `next_probe`, and verifier feedback.
- `max_attempts` default is 2; 3 is allowed only when `packet_class = failing_test_repair`.
- `fast_path_attempt` is a single pre-budget probe and is not counted in `retry_strategy.max_attempts`.
- `allowed_files_count` for fast path must be computed from unique normalized paths.
- Fast-path eligibility is exactly: scope = narrow and risk != high and allowed_files_count <= 3 (unique normalized paths) and success_check present.
- Fast-path success must still record verifier result and validation proof.
- Verifier output keys are fixed: `verdict`, `reasons`, `retryable`, `validation_proof`.
- `validation_proof` must stay concise and must not include raw logs or payload blobs.
- `next_if_pass` must be exactly one packet identifier token (example: `phase-02-tests`); paragraph text or multi-step instructions are not allowed.

Packet runner scope
- `packet_runner` is packet-bound only and executes one approved packet at a time.
- `packet_runner` must not replan, expand scope, reassign category, or edit packet-external files.
- Broad/open-ended work bypasses `packet_runner` by default; no automatic packet splitting/planner expansion is allowed.
- Evidence quality/coverage remains an operational KPI and must not be used to reclassify packet capabilities as optional/deferred.

Validation gate
- A packet is complete only if all are true:
  - edits stay within `allowed_files`
  - no edit touches `forbidden_files`
  - `success_check` is met
  - no unrelated edits are introduced

Deterministic unrelated-edits rule
- Any edit outside `allowed_files` is unrelated.
- Edits inside `allowed_files` that do not support the packet `goal` or `success_check` are unrelated.
- Broad formatting-only churn is unrelated and does not count as packet-valid progress.

Advance rule
- Advance to `next_if_pass` only after gate pass.
- On gate failure, remain in current packet and continue patch-first, bug-localized repair.
- If `packet_required = true`, packetization cannot be skipped.
- If `packet_gate_status = failed`, silent advancement is forbidden and must be treated as harness failure.
- Packet exhaustion state must be explicit and consistent across trace, notepad, and orchestrator packet state.
