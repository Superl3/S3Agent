ID: FR-001
TRIGGER: Repeated import failures after moving files.
RULE: Update all import paths in the touched module before rerunning tests.
CHECK: Search only changed files for stale imports and rerun targeted tests.
EXAMPLE: Renamed utils/time.py to core/time.py, then fixed from utils.time import now.

ID: FR-002
TRIGGER: Assertion flips during bugfix and test still fails.
RULE: Localize failing assertion inputs and patch the smallest branch first.
CHECK: Capture failing test input, patch one function, rerun the single failing test.
EXAMPLE: Patched boundary check in parser/normalize.py without rewriting parser pipeline.
