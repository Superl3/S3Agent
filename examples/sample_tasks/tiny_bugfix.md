id: tiny_bugfix_001
expected_skill: bug_fix
expected_agent: debugger
expected_mode: MICRO
parallel_allowed: false
requires_bug_localization: true
requires_patch_first: true
risk: low
complexity: low
scope: narrow
goal: fix off-by-one check in parser edge case
observed_problem: single function throws IndexError on empty input
suspect_file: src/parser.py
suspect_function: parse_tokens
related_test: tests/test_parser.py::test_empty_input
success_condition: parser empty-input unit test passes
parallelism_need: no
ownership_boundaries: clear
interface_status: defined
change_coupling: low
edit_overlap: same_file

Tiny localized bugfix. Expected behavior is one small patch and focused retest.
