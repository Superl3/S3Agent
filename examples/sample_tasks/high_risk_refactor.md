id: high_risk_refactor_001
expected_skill: refactoring
expected_agent: implementer
expected_mode: DEEP
parallel_allowed: true
requires_bug_localization: false
requires_patch_first: false
risk: high
complexity: high
scope: broad
goal: refactor payment contract usage across independent modules
observed_problem: duplicated interface code causes drift and merge risk
suspect_file: src/payments/contract.py
suspect_function: normalize_payment_contract
related_test: tests/test_payment_integration.py::test_contract_unified
success_condition: payment integration tests pass under unified contract
parallelism_need: yes
ownership_boundaries: clear
interface_status: defined
change_coupling: low
edit_overlap: independent

Broad refactor scenario that may justify parallel branches after contract-first gating.
