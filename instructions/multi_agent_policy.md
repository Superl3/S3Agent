Multi-agent gating policy

Default
- Single-agent execution.

Read-only parallel exploration allowlist
- Read-only inspection.
- Read-only search (only for large-scale discovery).
- Read-only docs/schema/reference lookup.
- Read-only repository exploration.

Serial Consolidation Principle:
- Non-destructive exploration (reading, directory listing, searching) SHOULD be performed by the primary assigned execution agent in a single turn/process.
- Spawning parallel sub-agents for per-file reading is forbidden unless the search space is exceptionally large.
- Prioritize token efficiency and sequential context building over parallel sprawl for all "understanding" tasks.

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
