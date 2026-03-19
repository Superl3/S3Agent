Bug-localization-first diagnosis

Failure classes
- syntax/import
- type/signature
- assertion failure
- runtime logic failure
- integration mismatch

Localization procedure
- Reproduce the failure with a focused command.
- Identify smallest failing unit (function/module/test).
- Capture failing input or call path.
- Confirm one likely fault site before editing.

What not to do
- Do not start with broad rewrites.
- Do not edit multiple modules before first localized retest.

Escalation trigger
- If localization points to contract mismatch across modules, escalate to DEEP planning.
