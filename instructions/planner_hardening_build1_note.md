Build 1 planner hardening integration note

What changed
- Added planning-mode and execution-shape contract surface (`meta_planning` vs `task_planning`, and the five execution shapes).
- Wired packet generation to emit planning-aware fields in `manager_route` and `execution_packet`.
- Added packet/reporting fields: `intended_behaviors`, `proof_requirements`, `requirement_status_matrix`, and `completion_state`.
- Added proof-category support (`structural`, `behavioral`, `regression`) in verification output and status reporting.
- Added conservative completion-state wiring (`completed_and_verified`, `implemented_but_unverified`, `partial`, `blocked`, `failed`).
- Added requirement-level PASS/FAIL/NOT-RUN reporting in implementer and status artifacts.
- Added repeated-symptom observation-first planning bias in packet generation for recurring interaction/state bug markers.

Intentionally deferred in Build 1
- Full multi-packet runtime execution for `serial_packet_chain` remains deferred; Build 1 emits shape and policy fields but still runs the existing single-packet runtime loop.
- No broad route-lane redesign was performed; existing `selected_path` semantics remain intact.
- No invasive orchestration changes were introduced beyond minimal field plumbing.

Known limitations
- Proof-category classification currently relies on explicit packet requirements and verification metadata; behavioral/regression coverage is conservative when tests are structural-only.
- Observation-first bias is currently heuristic (text-marker based) rather than full runtime symptom correlation.
- Requirement-level PASS is granted conservatively for read-only design/investigation completion and fully verified write tasks; most write tasks remain `implemented_but_unverified` unless behavioral/regression proof is explicit.
