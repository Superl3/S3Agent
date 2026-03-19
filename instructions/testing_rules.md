Testing rules

Baseline
- Every completed task must run at least one real validation command.
- Prefer targeted tests first, then broaden only if risk requires.
- Lint/type checks are NOT a separate gate; they are caught naturally by T1.

Test Ladder tiers
- T1 (Focused): nearest unit or contract test to the changed scope
- T2 (Integration): related module boundary tests
- T3 (Smoke): conditional full-system test

Required checks by mode
- MICRO: T1 only.
- STANDARD: T1 + T2 (integration guard where applicable).
- DEEP: T1 + T2 + T3 (high-risk regression guard when justified).

Determinism rules
- No placeholder tests.
- No `assert True` style pass-through tests.
- Assertions must validate behavior tied to the task goal.

Deterministic test-selection matrix
- `logic -> unit`
- `UI -> smoke`
- `configuration -> lint/typecheck`
- `mixed/cross-module -> at least two validation steps`
- `unknown -> at least one validation step OR standardized skip code {ENV_BLOCKED, NO_TESTS_DEFINED, VALIDATION_SKIPPED}`

Skip-code constraints
- Skip codes are allowed only for unknown test-selection cases.
- Skip codes are observational and must be explicit in validation reporting.

Failure handling tie-in
- When a test fails, handoff includes failure class and localized target.
