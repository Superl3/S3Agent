<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="post-implement-verification-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='post_implement_verification_policy']">
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='auto_verify_on_result_status']) &gt;= 1">E770_POST_VERIFY_RULE_REQUIRED: auto_verify_on_result_status rule is required.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='auto_verify_required_lane']) &gt;= 1">E771_POST_VERIFY_RULE_REQUIRED: auto_verify_required_lane rule is required.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='skip_verify_on_blocked']) &gt;= 1">E772_POST_VERIFY_RULE_REQUIRED: skip_verify_on_blocked rule is required.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='skip_verify_on_retry_failed']) &gt;= 1">E773_POST_VERIFY_RULE_REQUIRED: skip_verify_on_retry_failed rule is required.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='skip_verify_on_escalated']) &gt;= 1">E774_POST_VERIFY_RULE_REQUIRED: skip_verify_on_escalated rule is required.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='verify_on_no_op']) &gt;= 1">E775_POST_VERIFY_RULE_REQUIRED: verify_on_no_op rule is required.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='verify_on_applied']) &gt;= 1">E776_POST_VERIFY_RULE_REQUIRED: verify_on_applied rule is required.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='human_override_allowed']) &gt;= 1">E777_POST_VERIFY_RULE_REQUIRED: human_override_allowed rule is required.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='post_implement_verification_policy']/p:payload/p:rules/p:rule[p:rule_name='skip_verify_on_blocked' or p:rule_name='skip_verify_on_retry_failed' or p:rule_name='skip_verify_on_escalated']">
      <sch:assert test="normalize-space(p:decision)='skip_verifier'">E778_POST_VERIFY_SKIP_DECISION_INVALID: skip_verify_on_* rules must use decision=skip_verifier.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='post_implement_verification_policy']/p:payload/p:rules/p:rule[p:rule_name='auto_verify_required_lane' or p:rule_name='verify_on_applied']">
      <sch:assert test="normalize-space(p:decision)='run_verifier'">E779_POST_VERIFY_RUN_DECISION_INVALID: lane-required and applied verification rules must use decision=run_verifier.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='post_implement_verification_policy']/p:payload/p:rules/p:rule[p:rule_name='human_override_allowed']">
      <sch:assert test="normalize-space(p:decision)='require_human_decision'">E780_POST_VERIFY_OVERRIDE_DECISION_INVALID: human_override_allowed must use decision=require_human_decision.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
