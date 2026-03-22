<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="release-profile-governance-policy-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='release_profile_governance_policy']">
      <sch:assert test="count(p:payload/p:rules/p:rule) &gt;= 1">E1050_PROFILE_GOV_POLICY_RULE_REQUIRED: release_profile_governance_policy must include at least one rule.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='profile_version_required']) = 1">E1051_PROFILE_GOV_RULE_REQUIRED: profile_version_required must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='profile_owner_required']) = 1">E1052_PROFILE_GOV_RULE_REQUIRED: profile_owner_required must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='candidate_subset_change_requires_approval']) = 1">E1053_PROFILE_GOV_RULE_REQUIRED: candidate_subset_change_requires_approval must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='coverage_set_change_requires_documentation']) = 1">E1054_PROFILE_GOV_RULE_REQUIRED: coverage_set_change_requires_documentation must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='required_change_reason']) = 1">E1055_PROFILE_GOV_RULE_REQUIRED: required_change_reason must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='required_review_ref_or_ticket']) = 1">E1056_PROFILE_GOV_RULE_REQUIRED: required_review_ref_or_ticket must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='candidate_subset_must_remain_subset']) = 1">E1057_PROFILE_GOV_RULE_REQUIRED: candidate_subset_must_remain_subset must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='profile_hash_or_version_traceable']) = 1">E1058_PROFILE_GOV_RULE_REQUIRED: profile_hash_or_version_traceable must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='emergency_override_allowed']) = 1">E1059_PROFILE_GOV_RULE_REQUIRED: emergency_override_allowed must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='override_logging_required']) = 1">E1060_PROFILE_GOV_RULE_REQUIRED: override_logging_required must appear exactly once.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
