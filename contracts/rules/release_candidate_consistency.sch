<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="release-candidate-policy-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='release_candidate_policy']">
      <sch:assert test="count(p:payload/p:rules/p:rule) &gt;= 1">E960_RC_POLICY_RULE_REQUIRED: release_candidate_policy must include at least one rule.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='require_lane_coverage']) = 1">E961_RC_POLICY_RULE_MISSING: require_lane_coverage must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='require_ready_render_case']) = 1">E962_RC_POLICY_RULE_MISSING: require_ready_render_case must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='allow_caution_with_documented_warning']) = 1">E963_RC_POLICY_RULE_MISSING: allow_caution_with_documented_warning must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='deny_not_ready_as_rc_pass']) = 1">E964_RC_POLICY_RULE_MISSING: deny_not_ready_as_rc_pass must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='require_pruning_branch_coverage']) = 1">E965_RC_POLICY_RULE_MISSING: require_pruning_branch_coverage must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='require_release_readiness_strict_result']) = 1">E966_RC_POLICY_RULE_MISSING: require_release_readiness_strict_result must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='require_latest_pointer_safety']) = 1">E967_RC_POLICY_RULE_MISSING: require_latest_pointer_safety must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='require_verify_phase_rollout_minimum']) = 1">E968_RC_POLICY_RULE_MISSING: require_verify_phase_rollout_minimum must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='require_handoff_manifest']) = 1">E969_RC_POLICY_RULE_MISSING: require_handoff_manifest must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='operator_override_logging_required']) = 1">E970_RC_POLICY_RULE_MISSING: operator_override_logging_required must appear exactly once.</sch:assert>
    </sch:rule>
  </sch:pattern>

  <sch:pattern id="release-candidate-report-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='release_candidate_report']">
      <sch:assert test="normalize-space(p:payload/p:derived)='true'">E971_RC_REPORT_DERIVED_REQUIRED: release_candidate_report payload derived must be true.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:policy_ref/p:doc_class)='release_candidate_policy'">E972_RC_REPORT_POLICY_REF_CLASS_REQUIRED: policy_ref doc_class must be release_candidate_policy.</sch:assert>
      <sch:assert test="count(p:payload/p:harness_version_refs/p:ref) &gt;= 1">E973_RC_REPORT_HARNESS_REFS_REQUIRED: harness_version_refs must include at least one ref.</sch:assert>
      <sch:assert test="count(p:payload/p:harness_version_refs/p:ref[p:doc_class='release_gate_profile']) &gt;= 1">E981_RC_REPORT_PROFILE_REF_REQUIRED: harness_version_refs must include release_gate_profile.</sch:assert>
      <sch:assert test="count(p:payload/p:harness_version_refs/p:ref[p:doc_class='coverage_outcome_policy']) &gt;= 1">E982_RC_REPORT_COVERAGE_POLICY_REF_REQUIRED: harness_version_refs must include coverage_outcome_policy.</sch:assert>
      <sch:assert test="count(p:payload/p:smoke_task_refs/p:ref) &gt;= 1">E974_RC_REPORT_SMOKE_REFS_REQUIRED: smoke_task_refs must include at least one ref.</sch:assert>
      <sch:assert test="count(p:payload/p:warnings/p:item[normalize-space(text())='none']) = 0 or count(p:payload/p:warnings/p:item) = 1">E975_RC_REPORT_WARNINGS_INVALID: warnings cannot mix 'none' with additional values.</sch:assert>
      <sch:assert test="count(p:payload/p:blockers/p:item[normalize-space(text())='none']) = 0 or count(p:payload/p:blockers/p:item) = 1">E976_RC_REPORT_BLOCKERS_INVALID: blockers cannot mix 'none' with additional values.</sch:assert>
      <sch:assert test="not(normalize-space(p:payload/p:rc_result)='pass') or count(p:payload/p:blockers/p:item[normalize-space(text())='none']) = 1">E977_RC_REPORT_PASS_BLOCKERS_INVALID: rc_result=pass requires blockers=['none'].</sch:assert>
      <sch:assert test="not(normalize-space(p:payload/p:rc_result)='fail') or count(p:payload/p:blockers/p:item[normalize-space(text())!='none']) &gt;= 1">E978_RC_REPORT_FAIL_BLOCKERS_REQUIRED: rc_result=fail requires at least one non-none blocker.</sch:assert>
      <sch:assert test="not(normalize-space(p:payload/p:rc_result)='pass') or normalize-space(p:payload/p:latest_pointer_safety)='true'">E979_RC_REPORT_PASS_POINTER_SAFETY_REQUIRED: rc_result=pass requires latest_pointer_safety=true.</sch:assert>
      <sch:assert test="not(normalize-space(p:payload/p:rc_result)='pass') or normalize-space(p:payload/p:lineage_safety)='true'">E980_RC_REPORT_PASS_LINEAGE_SAFETY_REQUIRED: rc_result=pass requires lineage_safety=true.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
