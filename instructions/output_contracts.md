Output contracts index

Use the role-specific contract file that matches your agent role.
Do NOT load contract files for other roles.

| Role | Contract file |
|---|---|
| orchestrator | `instructions/output_contracts_routing.md` |
| implementer, debugger, tester, reviewer | `instructions/output_contracts_execution.md` |
| packet_runner | `instructions/output_contracts_packet.md` |

Common output rule
- Use compact structured outputs instead of essays.
- Fingerprints (policy_fp, task_fp, route_fp) are observational metadata only.
