## 0. Philosophy

This harness follows:

> **Test-gated opportunistic execution with fail-down decomposition**

Principles:

* Prefer **top-down execution** over premature decomposition
* Use **tests as the only source of truth**
* Decompose **only when necessary**
* Favor **local correction over global rewrite**
* Maintain **goal consistency across decomposition**

---

## 1. Execution Strategy

### 1.1 Default Flow

```text
attempt (top-down, largest reasonable scope)
→ test (lightweight — Test Ladder §4)
→ pass → DONE
→ fail → classify failure (§2.3)
    → local failure → patch + retry (up to budget §2.1)
    → hard bug → escalate to debugger (§2.4)
    → structural failure → decompose (§3)
```

---

### 1.2 Fast Path (Required Default)

* Always attempt **largest reasonable task first**
* Do NOT decompose preemptively
* Do NOT over-plan before attempting

```text
BAD:  plan → decompose → implement
GOOD: implement → test → decide next step
```

Planning is subordinate to execution: do not expand planning depth unless
failure evidence shows that direct execution is insufficient.

---

## 2. Retry Budget Policy

### 2.1 Retry Limits

| Path                          | Max Attempts |
| ----------------------------- | ------------ |
| Non-packet (MICRO fast-path)  | 3            |
| Packet-bound (packet_runner)  | 2 (3 for failing_test_repair — see packet_runner.md) |
| Same failure pattern trigger  | 2 identical → escalate |

---

### 2.2 Retry Decision Rule

After failure:

```text
IF failure is local AND retry count < budget:
    patch + retry (carry failed_approaches forward)

IF same failure pattern repeated twice:
    escalate → debugger (hard_bug) OR decompose (structural)

IF failure is expanding across modules:
    escalate to decomposition immediately
```

---

### 2.3 Failure Classification

| Class | Description | Next Action |
|---|---|---|
| `local` | Syntax, import, minor mismatch, single-module bug | `patch_retry` |
| `hard_bug` | Logic error that repeated patching cannot resolve | escalate to `debugger` |
| `structural` | Multi-module failure, broken state flow, interface mismatch | `decompose` |
| `insufficient_tests` | Signal unclear because test coverage is too narrow | tester adds tests, then re-validate |
| `environmental` | Tooling/runtime issue unrelated to implementation logic | report `blocked` with evidence |

> **Note:** Do NOT use a general "ambiguous" class. If the signal is unclear, the cause
> is almost always `insufficient_tests` — so fix the tests first.

---

### 2.4 Debugger Escalation Gate (Hard Bug Buffer)

Before triggering task decomposition after repeated local failures:

```text
IF failure class = hard_bug:
    escalate to debugger agent
    → debugger: classify → localize → patch → retest (see patch_first.md)
    → if debugger resolves: continue
    → if debugger escalates: THEN decompose
```

This prevents over-decomposing tasks that are merely algorithmically tricky,
not structurally wrong.

---

## 3. Decomposition Policy

> Decompose only when the debugger or retry budget is exhausted AND the failure
> is clearly structural.

For decomposition rules and split checklist: see `instructions/atomic_tasks.md`.

Additional constraint for this policy:
* Decomposition is triggered by failure evidence, not by task size alone.
* Every subtask produced by decomposition MUST inherit Contract Memory (§6).

---

## 4. Test Ladder Policy

### 4.1 Ordered Validation Layers (3-tier)

```text
T1: Focused test — nearest unit or contract test to changed scope
T2: Integration — related module boundary tests (STANDARD/DEEP only)
T3: Smoke / system test (conditional — same triggers as before)
```

> L1 (Lint/Type) is **not a separate gate**. It is absorbed into T1:
> if the changed file has a lint/type error, T1 will catch it naturally.

Mode-based entry point:
* **MICRO**: T1 only — stop there if it passes
* **STANDARD**: T1 → T2
* **DEEP**: T1 → T2 → T3 (when justified)

Escalate to next tier only when:
* current tier passes AND broader coverage is needed, OR
* failure class is `insufficient_tests`

### 4.2 Smoke Test Policy (T3)

Run ONLY when:

* Core system changed
* Multiple modules affected
* Before merge / milestone
* Repeated unexplained failures

DO NOT run smoke test on every attempt.

Test selection matrix by mode: see `instructions/testing_rules.md`.

---

## 5. Patch-first Correction Policy

See canonical source: `instructions/patch_first.md`.

Summary for this policy's context:
* Locate the minimal failing area → patch → retest.
* Changing unrelated modules during local correction is forbidden.
* Full-file rewrite is **conditionally allowed** (see below) — not the default.

### 5.1 Conditional Rewrite Gate

Rewrite is allowed ONLY when ALL conditions hold:
1. Scope is narrow (single file, single class)
2. Rewrite produces fewer total lines than the patch alternative
3. No unrelated logic is changed
4. Test coverage exists for the rewritten scope

If any condition fails → mandatory patch, not rewrite.

---

## 6. Contract Memory Policy

### 6.1 Persistent Context

Every task MUST carry these fields through every retry and decomposition step:

```json
{
  "goal": "...",
  "acceptance_criteria": [...],
  "constraints": [...],
  "output_contract": {...},
  "failed_approaches": ["attempt N: what was tried and why it failed", "..."]
}
```

`failed_approaches` MUST be forwarded to the next attempt to prevent repeating the same solution.

**Compression policy** (to prevent context bloat on high-performance models):
* Keep at most the **3 most recent** entries
* Each entry must be **≤ 2 lines** (approach summary + reason for failure)
* On decomposition, inherit only entries **relevant to the child subtask scope**
* Older entries beyond the 3-entry limit may be summarized into a single line

### 6.2 During Decomposition

Each subtask MUST inherit:

* Parent `goal`
* Relevant `acceptance_criteria`
* Applicable `constraints`
* `output_contract` requirements

Child packets may narrow scope but MUST NOT discard parent success conditions
that remain applicable.

### 6.3 Subtask Exit Condition

Each subtask MUST define explicit `done_when` conditions:

```json
{
  "done_when": [
    "targeted test passes",
    "output contract field X satisfied"
  ]
}
```

**`done_when` verification is the tester's responsibility — not the implementer's.**
The implementer MUST NOT self-certify completion. The tester MUST verify each
`done_when` item 1:1 and attach evidence before emitting `complete`.

---

## 7. Error-driven Refinement Loop

### 7.1 Correction Input

On failure, the correction agent MUST receive ALL of:

* Failing test output / error message
* Relevant diff context (what changed)
* `failed_approaches` from Contract Memory (§6.1)
* Applicable rules from `memory/failure_rules.md` (failure_memory)

### 7.2 Correction Behavior

DO:
* Fix based on error signal
* Minimize changes (patch-first — see `instructions/patch_first.md`)
* Preserve working code

DO NOT:
* Re-plan entire solution
* Ignore failure signal
* Expand scope unnecessarily
* Repeat an approach already listed in `failed_approaches`

---

## 8. Completion Criteria

A task is COMPLETE ONLY IF:

* All `done_when` conditions verified and evidence attached
* Output contract satisfied
* No regression introduced

**Judge rule**: Verification is the tester's responsibility — the implementer MUST NOT self-certify.

**MICRO fast-path exception**: If ALL of the following hold, implementer may perform self-verification:
* Mode is MICRO
* Single file changed
* T1 test command is explicit and deterministic
* Validation proof (command + output) is attached to the handoff

In all other modes (STANDARD/DEEP), tester must verify independently.

NOT sufficient in any mode:
* "Implementation complete"
* "Looks correct"
* "No errors observed manually"

---

## 9. Planning Escalation (Integrated with MICRO/STANDARD/DEEP)

Planning depth escalates with failure evidence — it does not start high.

| Mode | Default Posture | Escalation Trigger |
|---|---|---|
| MICRO | Direct fast-path attempt | Local failure × 2 → promote to STANDARD |
| STANDARD | Attempt → patch retry → decompose | Structural failure → promote to DEEP |
| DEEP | Contract-first → packetize | Already in max-depth; packet_runner handles retry |

Do NOT begin in DEEP mode unless:
* task is clearly multi-component and stateful, OR
* change spans multiple modules/interfaces, OR
* prior attempts already failed structurally

---

## 10. Summary

This harness:

* Does NOT enforce strict TDD
* DOES enforce test-gated validation with structured failure classification
* Prefers: speed first → correctness via convergence → decomposition only when needed

Key safeguards:
* `failed_approaches` prevents repeated identical mistakes
* `hard_bug` class routes to debugger before decomposing
* `done_when` must be verified by tester, not self-certified by implementer
* `failure_memory` is always consulted before correction attempts
* `insufficient_tests` replaces `validation_ambiguous` to force actionable response
