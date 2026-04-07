<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="reason-code-catalog-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='reason_code_catalog']">
      <sch:assert test="count(p:payload/p:reasons/p:reason) &gt;= 1">E1132_REASON_CODE_CATALOG_ENTRY_REQUIRED: reason_code_catalog must include at least one reason entry.</sch:assert>
      <sch:assert test="count(p:payload/p:reasons/p:reason[p:code='rc_candidate_task_missing']) = 1">E1133_REASON_CODE_REQUIRED: rc_candidate_task_missing must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:reasons/p:reason[p:code='rc_candidate_latest_missing']) = 1">E1134_REASON_CODE_REQUIRED: rc_candidate_latest_missing must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:reasons/p:reason[p:code='rc_candidate_required_artifact_missing']) = 1">E1135_REASON_CODE_REQUIRED: rc_candidate_required_artifact_missing must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:reasons/p:reason[p:code='rc_candidate_ref_broken']) = 1">E1136_REASON_CODE_REQUIRED: rc_candidate_ref_broken must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:reasons/p:reason[p:code='rc_coverage_task_missing']) = 1">E1137_REASON_CODE_REQUIRED: rc_coverage_task_missing must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:reasons/p:reason[p:code='rc_coverage_ref_broken']) = 1">E1138_REASON_CODE_REQUIRED: rc_coverage_ref_broken must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:reasons/p:reason[p:code='implementer_modify_target_missing']) = 1">E1139_REASON_CODE_REQUIRED: implementer_modify_target_missing must appear exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:reasons/p:reason[p:category='rc']) &gt;= 1">E1140_REASON_CODE_CATEGORY_REQUIRED: reason_code_catalog must include rc category entries.</sch:assert>
      <sch:assert test="count(p:payload/p:reasons/p:reason[p:category='implementer']) &gt;= 1">E1141_REASON_CODE_CATEGORY_REQUIRED: reason_code_catalog must include implementer category entries.</sch:assert>
      <sch:assert test="count(p:payload/p:reasons/p:reason[p:category='verifier']) &gt;= 1">E1142_REASON_CODE_CATEGORY_REQUIRED: reason_code_catalog must include verifier category entries.</sch:assert>
      <sch:assert test="count(p:payload/p:reasons/p:reason[p:category='coordinator']) &gt;= 1">E1143_REASON_CODE_CATEGORY_REQUIRED: reason_code_catalog must include coordinator category entries.</sch:assert>
      <sch:assert test="count(p:payload/p:reasons/p:reason[p:category='planner']) &gt;= 1">E1144_REASON_CODE_CATEGORY_REQUIRED: reason_code_catalog must include planner category entries.</sch:assert>
      <sch:assert test="count(p:payload/p:reasons/p:reason[p:category='system']) &gt;= 1">E1146_REASON_CODE_CATEGORY_REQUIRED: reason_code_catalog must include system category entries.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='reason_code_catalog']/p:payload/p:reasons/p:reason">
      <sch:assert test="count(../p:reason[normalize-space(p:code)=normalize-space(current()/p:code)]) = 1">E1147_REASON_CODE_DUPLICATE: reason code values must be unique.</sch:assert>
      <sch:assert test="(normalize-space(p:category)='rc' and starts-with(normalize-space(p:code), 'rc_'))
                        or (normalize-space(p:category)='implementer' and starts-with(normalize-space(p:code), 'implementer_'))
                        or (normalize-space(p:category)='verifier' and starts-with(normalize-space(p:code), 'verifier_'))
                        or (normalize-space(p:category)='planner' and starts-with(normalize-space(p:code), 'planner_'))
                        or (normalize-space(p:category)='coordinator' and (starts-with(normalize-space(p:code), 'coordinator_') or normalize-space(p:code)='acceptance_lineage_mismatch' or normalize-space(p:code)='verification_runner_error' or normalize-space(p:code)='verification_lineage_mismatch'))
                        or (normalize-space(p:category)='system' and starts-with(normalize-space(p:code), 'system_'))">E1148_REASON_CODE_PREFIX_CATEGORY_MISMATCH: reason code prefix/category mapping is invalid.</sch:assert>
      <sch:assert test="not(normalize-space(p:default_classification)='blocker') or normalize-space(p:affects_gate_default)='true'">E1149_REASON_CODE_BLOCKER_GATE_REQUIRED: blocker default_classification must set affects_gate_default=true.</sch:assert>
      <sch:assert test="not(normalize-space(p:default_classification)='excluded') or normalize-space(p:affects_gate_default)='false'">E1150_REASON_CODE_EXCLUDED_GATE_FORBIDDEN: excluded default_classification must set affects_gate_default=false.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
