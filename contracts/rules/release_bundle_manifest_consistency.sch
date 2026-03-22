<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="release-bundle-manifest-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='release_bundle_manifest']">
      <sch:assert test="normalize-space(p:payload/p:derived)='true'">E981_RC_BUNDLE_DERIVED_REQUIRED: release_bundle_manifest payload derived must be true.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:source_release_candidate_report_ref/p:doc_class)='release_candidate_report'">E982_RC_BUNDLE_SOURCE_REPORT_CLASS_REQUIRED: source_release_candidate_report_ref doc_class must be release_candidate_report.</sch:assert>
      <sch:assert test="count(p:payload/p:key_policy_refs/p:ref) &gt;= 1">E983_RC_BUNDLE_POLICY_REFS_REQUIRED: key_policy_refs must include at least one ref.</sch:assert>
      <sch:assert test="count(p:payload/p:key_schema_refs/p:item) &gt;= 1">E984_RC_BUNDLE_SCHEMA_REFS_REQUIRED: key_schema_refs must include at least one item.</sch:assert>
      <sch:assert test="count(p:payload/p:key_script_refs/p:item) &gt;= 1">E985_RC_BUNDLE_SCRIPT_REFS_REQUIRED: key_script_refs must include at least one item.</sch:assert>
      <sch:assert test="count(p:payload/p:key_runtime_refs/p:item) &gt;= 1">E986_RC_BUNDLE_RUNTIME_REFS_REQUIRED: key_runtime_refs must include at least one item.</sch:assert>
      <sch:assert test="count(p:payload/p:latest_release_refs/p:item) &gt;= 1">E987_RC_BUNDLE_LATEST_REFS_REQUIRED: latest_release_refs must include at least one item.</sch:assert>
      <sch:assert test="count(p:payload/p:operator_entrypoints/p:item[normalize-space(text())='cleanup_task_runtime']) &gt;= 1">E988_RC_BUNDLE_ENTRYPOINT_REQUIRED: operator_entrypoints must include cleanup_task_runtime.</sch:assert>
      <sch:assert test="count(p:payload/p:operator_entrypoints/p:item[normalize-space(text())='task_executor']) &gt;= 1">E989_RC_BUNDLE_ENTRYPOINT_REQUIRED: operator_entrypoints must include task_executor.</sch:assert>
      <sch:assert test="count(p:payload/p:operator_entrypoints/p:item[normalize-space(text())='operator_preflight']) &gt;= 1">E990_RC_BUNDLE_ENTRYPOINT_REQUIRED: operator_entrypoints must include operator_preflight.</sch:assert>
      <sch:assert test="count(p:payload/p:operator_entrypoints/p:item[normalize-space(text())='final_renderer']) &gt;= 1">E991_RC_BUNDLE_ENTRYPOINT_REQUIRED: operator_entrypoints must include final_renderer.</sch:assert>
      <sch:assert test="count(p:payload/p:operator_entrypoints/p:item[normalize-space(text())='operator_runbook']) &gt;= 1">E992_RC_BUNDLE_ENTRYPOINT_REQUIRED: operator_entrypoints must include operator_runbook.</sch:assert>
      <sch:assert test="count(p:payload/p:operator_entrypoints/p:item[normalize-space(text())='runtime_prune']) &gt;= 1">E993_RC_BUNDLE_ENTRYPOINT_REQUIRED: operator_entrypoints must include runtime_prune.</sch:assert>
      <sch:assert test="count(p:payload/p:operator_entrypoints/p:item[normalize-space(text())='release_candidate_check']) &gt;= 1">E994_RC_BUNDLE_ENTRYPOINT_REQUIRED: operator_entrypoints must include release_candidate_check.</sch:assert>
      <sch:assert test="count(p:payload/p:operator_entrypoints/p:item[normalize-space(text())='session_report_refresh']) &gt;= 1">E996_RC_BUNDLE_ENTRYPOINT_REQUIRED: operator_entrypoints must include session_report_refresh.</sch:assert>
      <sch:assert test="count(p:payload/p:operator_entrypoints/p:item[normalize-space(text())='release_ops_gate']) &gt;= 1">E997_RC_BUNDLE_ENTRYPOINT_REQUIRED: operator_entrypoints must include release_ops_gate.</sch:assert>
      <sch:assert test="count(p:payload/p:known_warnings/p:item[normalize-space(text())='none']) = 0 or count(p:payload/p:known_warnings/p:item) = 1">E995_RC_BUNDLE_WARNINGS_INVALID: known_warnings cannot mix 'none' with additional values.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
