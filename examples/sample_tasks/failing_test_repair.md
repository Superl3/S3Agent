id: failing_test_repair_001
expected_skill: regression_repair
expected_agent: debugger
expected_mode: STANDARD
parallel_allowed: false
requires_bug_localization: true
requires_patch_first: true
risk: medium
complexity: medium
scope: moderate
goal: repair regression in cache invalidation behavior
observed_problem: failing test after recent cache key update
suspect_file: src/cache/invalidation.py
suspect_function: build_cache_key
related_test: tests/test_cache.py::test_invalidation_regression
success_condition: failing regression test passes without widening failure set
parallelism_need: no
ownership_boundaries: clear
interface_status: defined
change_coupling: medium
edit_overlap: same_file

Regression repair scenario emphasizing bug localization and minimal patching.
