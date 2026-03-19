id: environment_blocker_001
expected_skill: review
expected_agent: reviewer
expected_mode: MICRO
parallel_allowed: false
requires_bug_localization: false
requires_patch_first: false
risk: low
complexity: low
scope: narrow
goal: identify and report runtime dependency blocker before code edits
observed_problem: environment blocker prevents dependency bootstrap due network unreachable host
suspect_file: runtime/environment
suspect_function: diagnose_environment
related_test: tests/test_environment.py::test_blocker_route
success_condition: blocker is classified and no direct code-repair route is selected
parallelism_need: no
ownership_boundaries: clear
interface_status: defined
change_coupling: low
edit_overlap: same_file

Environment blocker scenario that must not be routed into direct code repair.
