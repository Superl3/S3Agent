<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="coverage-outcome-policy-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='coverage_outcome_policy']">
      <sch:assert test="count(p:payload/p:rules/p:rule) &gt;= 1">E1030_COVERAGE_POLICY_RULE_REQUIRED: coverage_outcome_policy must include at least one rule.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:source_kind='strict_release_readiness' and p:source_value='fail' and p:classification='blocker' and p:affects_gate='true']) = 1">E1031_COVERAGE_POLICY_STRICT_FAIL_REQUIRED: strict_release_readiness fail must be a blocker affecting gate.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:classification='info']) &gt;= 1">E1036_COVERAGE_POLICY_INFO_REQUIRED: coverage_outcome_policy must include at least one info rule.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:classification='warning']) &gt;= 1">E1037_COVERAGE_POLICY_WARNING_REQUIRED: coverage_outcome_policy must include at least one warning rule.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:classification='blocker']) &gt;= 1">E1038_COVERAGE_POLICY_BLOCKER_REQUIRED: coverage_outcome_policy must include at least one blocker rule.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:classification='excluded']) &gt;= 1">E1039_COVERAGE_POLICY_EXCLUDED_REQUIRED: coverage_outcome_policy must include at least one excluded rule.</sch:assert>
    </sch:rule>
    <sch:rule context="p:pxml[p:meta/p:doc_class='coverage_outcome_policy']/p:payload/p:rules/p:rule">
      <sch:assert test="count(../p:rule[normalize-space(p:rule_name)=normalize-space(current()/p:rule_name)]) = 1">E1032_COVERAGE_POLICY_RULE_NAME_UNIQUE: rule_name must be unique.</sch:assert>
      <sch:assert test="count(../p:rule[normalize-space(p:source_kind)=normalize-space(current()/p:source_kind) and normalize-space(p:source_value)=normalize-space(current()/p:source_value)]) = 1">E1033_COVERAGE_POLICY_SOURCE_PAIR_UNIQUE: source_kind/source_value pairs must be unique.</sch:assert>
      <sch:assert test="not(normalize-space(p:classification)='blocker') or normalize-space(p:affects_gate)='true'">E1034_COVERAGE_POLICY_BLOCKER_GATE_REQUIRED: blocker classification must set affects_gate=true.</sch:assert>
      <sch:assert test="not(normalize-space(p:classification)='excluded') or normalize-space(p:affects_gate)='false'">E1035_COVERAGE_POLICY_EXCLUDED_GATE_FORBIDDEN: excluded classification must set affects_gate=false.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
