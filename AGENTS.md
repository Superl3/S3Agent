# Agent Topology

Deterministic, lean harness with strict role boundaries and internal routing.

## Runtime pipeline

```
User → S3Agent → orchestrator → implementer/debugger/tester/reviewer
User → harness_review (diagnosis only)
orchestrator → packet_runner (packetized large-task path only)
```

- Final user-visible replies are rendered through `scripts/final_renderer.py` + `instructions/render_templates/`.
- `runtime/execution_trace_archive.md` is the append-only source-of-truth runtime evidence log.
- Self-diagnosis is advisory-only; it cannot bypass core policies or auto-mutate the harness.

## Role layers

| Role | Agent(s) | User-facing |
|---|---|---|
| Primary entry | `S3Agent`, `harness_review` | yes |
| Orchestration | `orchestrator` | no |
| Execution | `implementer`, `debugger`, `tester`, `reviewer`, `packet_runner` | no |
| Harness ops | `harness_improve` | no |

## Manual entry policy

- Default entry: `S3Agent`.
- `S3Agent` must always normalize then hand off to `orchestrator` — never terminal.
- `harness_review` is diagnosis-only, read-only.
- `harness_improve` is internal-only and approval-gated for any mutation.

## Key policy references

| Topic | Canonical location |
|---|---|
| Task intake normalization | `instructions/task_intake.md` |
| Planning modes (MICRO/STANDARD/DEEP) | `instructions/planning_modes.md` |
| Phase gates & preflight | `instructions/phase_gates.md` |
| Multi-agent & single-writer | `instructions/multi_agent_policy.md` |
| Test-selection matrix | `instructions/testing_rules.md` |
| Search discovery policy | `instructions/search_policy.md` |
| Execution strategy & retry | `instructions/test_gated_execution_policy.md` |
| Patch-first repair | `instructions/patch_first.md` |
| Bug localization | `instructions/bug_localization.md` |
| Context/DCP pruning | `instructions/lazy_context.md` |
| Output contracts & schemas | `instructions/output_contracts.md` |
| Packet execution | `instructions/phase_gates.md` + `agents/packet_runner.md` |
| Failure memory | `instructions/failure_memory.md` + `memory/failure_rules.md` |

## Execution posture

- Single-agent execution is the default; parallelization requires multi_agent_policy gating.
- Parallel exploration is read-only only.
- Single-writer semantics are mandatory for any mutating work.
- Large-task packetization is required when: `mode=DEEP`, `risk=high`, `scope=broad`, or `expected_touched_files > 3`.

## Workspace invariants

- Treat user-declared runtime context as binding task state (e.g., WSL-required execution).
- Preserve the active execution-context contract across turns (`workspace kind`, `shell family`, `path semantics`).

## Global UX conventions

- Window/panel/popup size must remain stable during in-window interactions.
- Keep non-moving anchor areas (title/header/footer/actions) fixed; expose overflow via internal scrolling.
- For settings UIs: top context fixed, primary action row fixed at bottom.
- Exceptions allowed only when explicitly requested by the user for that task.
