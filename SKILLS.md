# Skills Registry

Deterministic internal routing registry. Parse this table as policy data.
Targets are runtime endpoints, not user-facing manual agent choices.

| skill | use_when | primary_agent | fallback_agent | default_mode | parallel_allowed | requires_contract_first |
| --- | --- | --- | --- | --- | --- | --- |
| task_intake_normalization | Normalize vague or unstructured user tasks into compact task specs. | prompt_high | prompt | MICRO | false | false |
| feature_implementation | Add or extend behavior with clear acceptance criteria. | implementer | orchestrator | STANDARD | false | false |
| bug_fix | Fix localized defects with known failure signals. | debugger | implementer | MICRO | false | false |
| test_generation | Add or improve deterministic tests for existing logic. | tester | implementer | STANDARD | false | false |
| regression_repair | Repair failing tests caused by recent behavior drift. | debugger | tester | STANDARD | false | false |
| refactoring | Restructure code while preserving behavior across modules. | implementer | reviewer | DEEP | true | true |
| investigation | Perform required codebase reading, exploration, and analysis for simple questions. | reviewer | orchestrator | STANDARD | false | false |
| review | Validate quality, scope, and policy compliance of a patch. | reviewer | orchestrator | STANDARD | false | false |
| documentation | Update docs or runbooks without changing runtime logic. | reviewer | implementer | MICRO | false | false |
| task_decomposition | Break broad goals into atomic executable tasks. | orchestrator | implementer | STANDARD | false | false |
| harness_review | Diagnose harness/runtime policy issues from logs, validation summaries, and recent evidence. | harness_review | orchestrator | MICRO | false | false |
| harness_improve | Propose one minimal harness improvement plan and wait for explicit approval before apply. | harness_improve | harness_review | STANDARD | false | false |
