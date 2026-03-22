<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="artifact-pruning-policy-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='artifact_pruning_policy']">
      <sch:assert test="count(p:payload/p:rules/p:rule) &gt;= 1">E940_PRUNING_POLICY_RULE_REQUIRED: artifact_pruning_policy must include at least one rule.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='dry_run_default']) = 1">E941_PRUNING_POLICY_DRY_RUN_RULE_REQUIRED: dry_run_default rule is required exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='never_prune_current_latest']) = 1">E942_PRUNING_POLICY_LATEST_BOUNDARY_REQUIRED: never_prune_current_latest rule is required exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='never_prune_referenced_by_latest']) = 1">E943_PRUNING_POLICY_REF_BOUNDARY_REQUIRED: never_prune_referenced_by_latest rule is required exactly once.</sch:assert>
      <sch:assert test="count(p:payload/p:rules/p:rule[p:rule_name='delete_derived_safe_only']) = 1">E944_PRUNING_POLICY_DELETE_DERIVED_REQUIRED: delete_derived_safe_only rule is required exactly once.</sch:assert>
    </sch:rule>
  </sch:pattern>

  <sch:pattern id="pruning-report-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='pruning_report']">
      <sch:assert test="normalize-space(p:payload/p:policy_ref/p:doc_class)='artifact_pruning_policy'">E945_PRUNING_REPORT_POLICY_REF_CLASS_REQUIRED: pruning_report policy_ref doc_class must be artifact_pruning_policy.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:derived)='true'">E946_PRUNING_REPORT_DERIVED_REQUIRED: pruning_report payload derived must be true.</sch:assert>
      <sch:assert test="number(p:payload/p:candidate_count) = count(p:payload/p:denied_candidates/p:candidate) + count(p:payload/p:quarantine_candidates/p:candidate) + count(p:payload/p:delete_candidates/p:candidate)">E947_PRUNING_REPORT_CANDIDATE_COUNT_MISMATCH: candidate_count must match denied+quarantine+delete candidate totals.</sch:assert>
      <sch:assert test="count(p:payload/p:warnings/p:item[normalize-space(text())='none']) = 0 or count(p:payload/p:warnings/p:item) = 1">E948_PRUNING_REPORT_WARNINGS_INVALID: warnings cannot mix 'none' with additional values.</sch:assert>
      <sch:assert test="count(p:payload/p:delete_candidates/p:candidate[p:action='delete_derived_safe']) = count(p:payload/p:delete_candidates/p:candidate)">E949_PRUNING_REPORT_DELETE_ACTION_REQUIRED: delete_candidates entries must use action=delete_derived_safe.</sch:assert>
      <sch:assert test="count(p:payload/p:quarantine_candidates/p:candidate[p:action='quarantine']) = count(p:payload/p:quarantine_candidates/p:candidate)">E950_PRUNING_REPORT_QUARANTINE_ACTION_REQUIRED: quarantine_candidates entries must use action=quarantine.</sch:assert>
      <sch:assert test="count(p:payload/p:denied_candidates/p:candidate[p:action='deny']) = count(p:payload/p:denied_candidates/p:candidate)">E951_PRUNING_REPORT_DENY_ACTION_REQUIRED: denied_candidates entries must use action=deny.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:task_scope)=normalize-space(p:meta/p:task_id) or starts-with(normalize-space(p:payload/p:task_scope),'all_tasks')">E952_PRUNING_REPORT_SCOPE_META_MISMATCH: task-scoped reports must align payload task_scope with meta task_id.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
