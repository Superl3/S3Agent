Failure log template

Use this deterministic template whenever one of the trigger conditions occurs.

Triggers
- validation fails
- retry occurs
- repair loop exceeds threshold
- plan deviates from expected scope

Allowed enums
- failure_type: PLAN_FAILURE | ROUTING_FAILURE | CONTEXT_LOSS | REPAIR_LOOP_FAILURE | PROMPT_NORMALIZATION_ERROR | INSUFFICIENT_REASONING_DEPTH
- stage: prompt | orchestrator | implementer | debugger | tester | reviewer | validation

Template
task:
mode:
stage:
failure_type:
symptoms:
suspected_causes:
repair_attempt:
result:
