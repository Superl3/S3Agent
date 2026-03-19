# Search Discovery Policy

## Stage order
- Discovery must be strict staged: `Stage0 -> Stage1 -> Stage2 -> Stage3/Stop`.
- Stage0 (exact-path first) is mandatory before Stage1 and Stage2.
- If Stage0 success-finds a concrete target, Stage1/Stage2 are skipped and search stops.

## Stage0 exact-path probe
- Before any index/LSP scans, run exact-path probe for concrete filename/identifier candidates.
- Stage0 must test normalized targets and explicit relative/absolute file candidates.
- If Stage0 succeeds, return immediately and skip Stage1/Stage2.
- If Stage0 is inconclusive, continue to Stage1.
- If Stage0 is the explicit fallback for tool failure or inconclusive LSP/index results, do not advance to the next stage until Stage0 completes.

## Discovery principles (pre-search)
- Pre-search layer is `index-first`, then `LSP-first`.
- Symbol discovery must include: definitions, references, imports/exports, call chains, and types.
- No directory scan is allowed before index/LSP attempts.
- No grep/search is allowed before LSP for symbol discovery.

## Stage1
- Stage1 roots only:
  - workspace root
  - agents/
  - instructions/
  - schemas/
  - runtime/
  - scripts/
  - tests/
  - root config/docs

## Stage2
- Stage2 roots only:
  - ~/.config/opencode/
  - <workspace>/.config/opencode/
  - <workspace>/.opencode/

## Stage3 stop and stop states
- Stop after Stage1/2 budget completion or on deterministic stop-state hit.
- Stop states are deterministic: `MISSING_EXPECTED_FILE`, `LOCATION_UNCLEAR`, `SEARCH_BUDGET_EXCEEDED`.
- `LOCATION_UNCLEAR` requires a short disambiguation hint.

## Fallback and gating
- If index/LSP are unavailable, unprepared, or failing (e.g., initial setup, server errors):
  - Do not terminate the task; fallback immediately to Stage1/Stage2 discovery tools (`glob`, `find`, `grep`).
  - Stage0 exact-path probing is still preferred first, but if the exact path is unknown, proceed directly to directory scan.
- No Stage3/Stop is reached before Stage1/2 budget completion except via explicit stop state transitions.
- Wildcard discovery is forbidden when a concrete target exists.
- Pattern-only search is forbidden when concrete target exists.

## Limits and budgets
- `max_search_commands_total = 12`
- `max_glob = 4`
- `max_find = 4`
- `max_search = 4`
- `max_retries_per_intent = 2`

## Intent cache and dedupe
- Cache is strictly per-request/per-run.
- Normalize intent key as `(normalized_target_name, stage, normalized_root_scope, file_type)`.
- Dedupe identical intent/pattern within the same request/run and reuse cached summary.

## Result summarization bands
- If matches `<=6`: return full list.
- If matches are `7-100`: return `total_matches`, `best_candidates(5)`, `discarded_count`, `roots_coverage`.
- If matches are `>100`: return `top_candidate`, `directory_summary`, `total_matches`; no raw large list.

## File-open discipline
- File-open is bounded and minimal.
- Open only bounded regions, no speculative whole-file reads.
- No uncontrolled search widening beyond Stage2 roots.

## Non-code file exception
- For non-code files (markdown, JSON, JSONC, YAML, config files):
  - Skip index/LSP stages entirely (no useful symbol data available).
  - Start directly from Stage0 exact-path or Stage1 directory scan.
  - Glob and find are allowed as primary discovery tools.
