packet_class: generic_packet
phase_name: phase-01
goal: implement localized packet objective only
scope: narrow packet scope only
allowed_files: src/example.py, tests/test_example.py
forbidden_files: docs/, scripts/
success_check: {"type":"validation","target":"targeted tests","metric":"pass"}
parallel_mode: off
retry_strategy: {"max_attempts":2,"observed_vs_expected":"<delta>","next_probe":"<single next probe>","verifier_feedback":"<concise verifier feedback>"}
fast_path_attempt: {"eligible":true,"allowed_files_count":2,"budget_exempt":true,"status":"not_attempted","verifier_result":"na","validation_proof":"na"}
verifier: {"verdict":"fail","reasons":"<concise reason>","retryable":true,"validation_proof":"<concise proof only>"}
next_if_pass: phase-02
packet_exhaustion: none
