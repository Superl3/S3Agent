Failure classification policy

Deterministic classes
- PLAN_FAILURE
  - definition: Execution diverges from approved scope, order, or required constraints.
  - typical symptoms: scope drift, missing required step, unapproved behavior change.
  - likely harness component responsible: orchestrator or implementer.
- ROUTING_FAILURE
  - definition: Task is routed to an incorrect agent, mode, or parallelism setting.
  - typical symptoms: wrong agent selected, incorrect mode (MICRO/STANDARD/DEEP), invalid escalation.
  - likely harness component responsible: orchestrator.
- CONTEXT_LOSS
  - definition: Required local clues are dropped during active execution.
  - typical symptoms: missing suspect_file clues, forgotten related_test, repeated re-discovery.
  - likely harness component responsible: execution agent handling the active repair loop.
- REPAIR_LOOP_FAILURE
  - definition: Localized repair attempts repeat without progress past threshold.
  - typical symptoms: repeated retry with same failure signature, loop threshold exceeded.
  - likely harness component responsible: debugger or tester loop control.
- PROMPT_NORMALIZATION_ERROR
  - definition: Prompt intake output violates strict normalization contract.
  - typical symptoms: wrong key order, missing required key, non-9-line output.
  - likely harness component responsible: prompt or prompt_high.
- INSUFFICIENT_REASONING_DEPTH
  - definition: Chosen reasoning depth is too shallow for observed complexity/risk.
  - typical symptoms: superficial fixes, unresolved hard failure after policy-compliant retries.
  - likely harness component responsible: orchestrator mode selection, with debugger escalation follow-up.

Classification rule
- Use `failure_type` directly if it matches the allowed enum set.
- Otherwise classify by deterministic symptom markers in this order:
  1) normalization contract violation -> PROMPT_NORMALIZATION_ERROR
  2) routing mismatch evidence -> ROUTING_FAILURE
  3) repeated no-progress retries -> REPAIR_LOOP_FAILURE
  4) missing active localization clues -> CONTEXT_LOSS
  5) shallow-depth evidence on hard task -> INSUFFICIENT_REASONING_DEPTH
  6) fallback -> PLAN_FAILURE
