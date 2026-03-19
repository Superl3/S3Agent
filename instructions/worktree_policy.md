Worktree isolation policy

Rule
- Every approved parallel branch must use one isolated git worktree.

Required setup
- One branch per subagent.
- One worktree path per branch.
- One explicit ownership boundary per branch.

Required before execution
- Shared contracts are defined and frozen for branch duration.
- Merge plan is documented with conflict checkpoints.

Deny cases (parallel worktrees forbidden)
- Same-file edits.
- Tightly coupled refactors.
- Unresolved interface design.
- Tiny bugfixes.
- Unclear ownership boundaries.

Decision examples
- Allow: broad refactor across independent modules with stable interfaces.
- Deny: single-file bugfix with low risk and narrow scope.
