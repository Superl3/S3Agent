Output contracts — Execution (implementer/debugger/tester/reviewer-use)

triage
- complexity: <low|medium|high>
- risk: <low|medium|high>
- parallelization_value: <low|medium|high>
- needs_deep_decomposition: <true|false>

plan
- mode: <MICRO|STANDARD|DEEP>
- steps: [atomic steps]
- escalation: <none|targeted|architectural>

task_spec
- id
- goal
- observed_problem
- scope
- suspect_file
- suspect_function
- related_test
- success_condition
- risk
- parallelism_need

failure_report
- failure_class  (alias: failure_type; enum: local|hard_bug|structural|insufficient_tests|environmental)
- localized_target
- attempted_patch
- failed_approaches  (append-only list of prior attempts and why each failed)
- retest_result
- next_action  (alias: suggested_next_strategy; enum: patch_retry|escalate_debugger|decompose|add_tests|blocked)
- policy_fp
- task_fp
- route_fp

completion_report
- changed_files
- validation_commands
- outcome
- done_when_verification  (tester-only: 1:1 evidence for each done_when item)
- follow_up_tasks
- policy_fp
- task_fp
- route_fp

reason_codes enum
- LOW_RISK
- HIGH_RISK
- TINY_SCOPE
- BROAD_SCOPE
- PATCH_FIRST_REQUIRED
- BUG_LOCALIZATION_REQUIRED
- PARALLEL_NOT_JUSTIFIED
- CONTRACT_REQUIRED
- WORKTREE_REQUIRED
- SINGLE_AGENT_DEFAULT
