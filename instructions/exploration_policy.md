# Exploration and Evidence Policy

## Discovery order
- Use indexed symbol lookup first, then LSP symbol queries.
- Prefer direct symbol queries before any content scan.
- Do not use grep before symbol-level index/LSP discovery.

## Evidence-first summary
Every exploration summary must include all fields:
- `task target`
- `indexed candidates`
- `LSP findings`
- `files opened`
- `why only these`
- `patch scope`

## Symbol discovery restrictions
- Must request symbols by concrete target names and normalized intent.
- Pattern-only symbols are forbidden when a concrete identifier or file target exists.
- Symbol discovery must include at least:
  - definitions
  - references
  - imports/exports
  - call chains
  - types

## File-open discipline
- Open minimal bounded ranges around known symbol locations.
- Avoid speculative broad reads and do not open uncontrolled files.
- Keep file-open count small and justified by the summary fields above.

## Safety constraints
- If LSP/symbol index is healthy and available, no directory scan or grep-led broad discovery before symbol queries.
- If LSP is unavailable, failing, or not ready for the target language:
  - This constraint is lifted; fallback to `glob` and `grep` as the primary discovery mechanisms.
  - Do not terminate the turn for lack of LSP support.
- Stop widening when Stage3 stop states are reached.

## Config/documentation file exception
- Symbol discovery restrictions apply only to source code files (.cs, .py, .js, .ts, etc).
- For configuration and documentation files (.md, .jsonc, .json, .yaml):
  - Directory scan and grep are allowed as first-class discovery methods.
  - No LSP/index prerequisite required.
