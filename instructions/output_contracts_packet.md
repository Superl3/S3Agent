Output contracts — Packet (packet_runner-use)

packet_runner_result (internal only)
- packet_id
- packet_class: <generic_packet|failing_test_repair>
- verdict: <pass|fail>
- retryable: <true|false>
- packet_exhaustion: <none|retry_pending|exhausted>
- failure_type: <local|hard_bug|structural|insufficient_tests|environmental>
- failed_approaches: (append-only; must be forwarded to each retry attempt)
- verifier: {verdict, reasons, retryable, validation_proof}
- retry_strategy: {max_attempts, observed_vs_expected, next_probe, verifier_feedback, carried_failed_approaches}
- fast_path_attempt: {eligible, allowed_files_count, budget_exempt=true, status, verifier_result, validation_proof}
- fast_path_attempt is pre-budget only and must not consume retry budget from retry_strategy.max_attempts
- done_when_verification: (tester-only; 1:1 evidence per done_when item before emitting complete)
- constraints: no replanning, no scope expansion, no category reassignment, no packet-external edits
