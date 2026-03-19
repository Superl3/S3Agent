Patch-first repair policy

Strict repair sequence
1) classify_failure
2) localize_bug
3) minimal_patch
4) retest
5) localized_retry_if_justified
6) rewrite_or_redesign_last

Hard constraints
- Rewrite-first repair is forbidden **unless the conditional rewrite gate passes** (see below).
- Full-file regeneration is forbidden before localization attempts.
- Repeated patch attempts without scope change are forbidden.

Conditional rewrite gate
- Rewrite is allowed ONLY when ALL of the following hold:
  1. Scope is narrow (single file, single class)
  2. Rewrite produces fewer total lines than the patch alternative
  3. No unrelated logic is changed
  4. Test coverage exists for the rewritten scope
- If any condition fails, patch is mandatory.

Localized retry gate
- Retry only when a new localized hypothesis exists.
- Stop retry loop after repeated failure and escalate.

Escalate to redesign when
- Root cause spans contracts or architecture.
- Localized retries fail more than once without reducing failure scope.
