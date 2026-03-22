<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="verify-phase-audit-policy-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='verify_phase_audit_policy']">
      <sch:assert test="count(p:payload/p:rules/p:rule) &gt;= 1">E1067_VERIFY_AUDIT_POLICY_RULE_REQUIRED: verify_phase_audit_policy must include at least one rule.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='require_lane_phase_evidence']) = 1">E1068_VERIFY_AUDIT_POLICY_RULE_REQUIRED: require_lane_phase_evidence must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='require_post_implement_phase_evidence']) = 1">E1069_VERIFY_AUDIT_POLICY_RULE_REQUIRED: require_post_implement_phase_evidence must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='classify_unknown_legacy_for_fresh_smoke']) = 1">E1070_VERIFY_AUDIT_POLICY_RULE_REQUIRED: classify_unknown_legacy_for_fresh_smoke must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='allow_unknown_legacy_for_legacy_artifacts']) = 1">E1071_VERIFY_AUDIT_POLICY_RULE_REQUIRED: allow_unknown_legacy_for_legacy_artifacts must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='candidate_subset_phase_coverage_required']) = 1">E1072_VERIFY_AUDIT_POLICY_RULE_REQUIRED: candidate_subset_phase_coverage_required must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='audit_report_required']) = 1">E1073_VERIFY_AUDIT_POLICY_RULE_REQUIRED: audit_report_required must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='override_logging_required']) = 1">E1074_VERIFY_AUDIT_POLICY_RULE_REQUIRED: override_logging_required must appear exactly once.</sch:assert>
    </sch:rule>
  </sch:pattern>

  <sch:pattern id="verify-phase-audit-report-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='verify_phase_audit_report']">
      <sch:assert test="normalize-space(p:payload/p:policy_ref/p:doc_class)='verify_phase_audit_policy'">E1075_VERIFY_AUDIT_REPORT_POLICY_REF_CLASS_REQUIRED: policy_ref doc_class must be verify_phase_audit_policy.</sch:assert>
      <sch:assert test="count(p:payload/p:warnings/p:item[normalize-space(text())='none']) = 0 or count(p:payload/p:warnings/p:item) = 1">E1076_VERIFY_AUDIT_REPORT_WARNINGS_INVALID: warnings cannot mix 'none' with additional values.</sch:assert>
      <sch:assert test="count(p:payload/p:blockers/p:item[normalize-space(text())='none']) = 0 or count(p:payload/p:blockers/p:item) = 1">E1077_VERIFY_AUDIT_REPORT_BLOCKERS_INVALID: blockers cannot mix 'none' with additional values.</sch:assert>
      <sch:assert test="not(normalize-space(p:payload/p:result)='pass') or count(p:payload/p:blockers/p:item[normalize-space(text())='none']) = 1">E1078_VERIFY_AUDIT_REPORT_PASS_BLOCKERS_INVALID: result=pass requires blockers=['none'].</sch:assert>
      <sch:assert test="not(normalize-space(p:payload/p:result)='fail') or count(p:payload/p:blockers/p:item[normalize-space(text())!='none']) &gt;= 1">E1079_VERIFY_AUDIT_REPORT_FAIL_BLOCKERS_REQUIRED: result=fail requires non-none blockers.</sch:assert>
      <sch:assert test="not(normalize-space(p:payload/p:result)='pass') or count(p:payload/p:lane_phase_evidence_refs/p:ref) &gt;= 1">E1080_VERIFY_AUDIT_REPORT_PASS_LANE_REQUIRED: result=pass requires lane evidence refs.</sch:assert>
      <sch:assert test="not(normalize-space(p:payload/p:result)='pass') or count(p:payload/p:post_implement_phase_evidence_refs/p:ref) &gt;= 1">E1081_VERIFY_AUDIT_REPORT_PASS_POST_REQUIRED: result=pass requires post_implement evidence refs.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
