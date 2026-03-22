<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="ci-exit-code-policy-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='ci_exit_code_policy']">
      <sch:assert test="count(p:payload/p:rules/p:rule) &gt;= 1">E1061_CI_POLICY_RULE_REQUIRED: ci_exit_code_policy must include at least one rule.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:input_condition='rc_result=pass' and p:output_exit_code='0']) = 1">E1062_CI_POLICY_RC_PASS_REQUIRED: rc_result=pass must map to exit_code 0.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:input_condition='rc_result=fail' and p:output_exit_code='1']) = 1">E1063_CI_POLICY_RC_FAIL_REQUIRED: rc_result=fail must map to exit_code 1.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:input_condition='rc_result=caution' and number(p:output_exit_code) &gt; 0]) = 1">E1064_CI_POLICY_RC_CAUTION_REQUIRED: rc_result=caution must map to non-zero exit code.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:input_condition='error_kind=validation_usage' and p:output_exit_code='3']) = 1">E1065_CI_POLICY_VALIDATION_USAGE_REQUIRED: validation/usage error must map to exit_code 3.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:input_condition='error_kind=hard_execution' and p:output_exit_code='4']) = 1">E1066_CI_POLICY_HARD_EXEC_REQUIRED: hard execution error must map to exit_code 4.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
