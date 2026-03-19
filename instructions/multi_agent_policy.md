Multi-agent gating policy

Default
- Single-agent execution.

Read-only parallel exploration allowlist
- Read-only inspection.
- Read-only search.
- Read-only docs/schema/reference lookup.
- Read-only repository exploration.

Allow parallel branches only when all conditions hold
- Module ownership boundaries are independent.
- Contracts/interfaces are defined first.
- Merge/conflict risk is acceptable.
- Coordination cost is lower than expected cycle-time savings.
- Work stays read-only.

Explicit deny cases
- Same-file edits.
- Tightly coupled refactors.
- Unresolved interface design.
- Tiny bugfixes.
- Unclear ownership boundaries.
- Patching.
- Test execution.
- Validation.
- Runtime state mutation.

Operational notes
- Parallelism is optional even in DEEP mode.
- If any deny case appears, fall back to single-agent path.
- Single-writer semantics are required for any mutating work.
- Read-only parallel policy and single-writer mutation policy are mandatory and non-negotiable.
