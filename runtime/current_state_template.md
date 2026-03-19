task_id: TASK-000
mode: MICRO
skill: bug_fix
agent: debugger
status: triage
selected_entry_agent: prompt_high
source_input_type: simple_nl
source_input_preserved: false
structured_context: kind=none; line_count=0; content=
goal:
observed_problem:
scope:
success_condition:
risk:
parallelism_need:
suspect_file:
suspect_function:
related_test:
selected_mode: MICRO
selected_path: prompt_high -> orchestrator -> debugger
packet_required: false
packet_gate_status: not_required
failure_class:
patch_target:
retry_state:
escalation_state: none
escalation: none
parallel: false
active_worktree: main
loaded_context:
  - agents/debugger.md
  - instructions/patch_first.md
  - instructions/bug_localization.md
notes: Keep context narrow and task-scoped.
