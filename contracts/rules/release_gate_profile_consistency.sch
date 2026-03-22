<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="release-gate-profile-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='release_gate_profile']">
      <sch:assert test="count(p:payload/p:coverage_task_ids/p:item) &gt;= 1">E1020_RELEASE_GATE_PROFILE_COVERAGE_REQUIRED: coverage_task_ids must include at least one task_id.</sch:assert>
      <sch:assert test="count(p:payload/p:candidate_gate_task_ids/p:item) &gt;= 1">E1021_RELEASE_GATE_PROFILE_CANDIDATE_REQUIRED: candidate_gate_task_ids must include at least one task_id.</sch:assert>
      <sch:assert test="count(p:payload/p:candidate_gate_task_ids/p:item[not(normalize-space(.) = /p:pxml/p:payload/p:coverage_task_ids/p:item)]) = 0">E1022_RELEASE_GATE_PROFILE_CANDIDATE_SUBSET_REQUIRED: candidate_gate_task_ids must be a subset of coverage_task_ids.</sch:assert>
      <sch:assert test="count(p:payload/p:required_lane_coverage/p:item[normalize-space(text())='direct']) &gt;= 1">E1023_RELEASE_GATE_PROFILE_DIRECT_LANE_REQUIRED: required_lane_coverage must include direct.</sch:assert>
      <sch:assert test="number(normalize-space(p:payload/p:required_ready_cases)) &gt;= 1">E1024_RELEASE_GATE_PROFILE_READY_CASES_REQUIRED: required_ready_cases must be >= 1.</sch:assert>
      <sch:assert test="count(p:payload/p:required_entrypoints/p:item) &gt;= 1">E1025_RELEASE_GATE_PROFILE_ENTRYPOINTS_REQUIRED: required_entrypoints must include at least one item.</sch:assert>
      <sch:assert test="string-length(normalize-space(p:payload/p:profile_version)) &gt; 0">E1026_RELEASE_GATE_PROFILE_VERSION_REQUIRED: profile_version is required for governance traceability.</sch:assert>
      <sch:assert test="string-length(normalize-space(p:payload/p:profile_owner)) &gt; 0">E1027_RELEASE_GATE_PROFILE_OWNER_REQUIRED: profile_owner is required.</sch:assert>
      <sch:assert test="string-length(normalize-space(p:payload/p:last_change_reason)) &gt; 0">E1028_RELEASE_GATE_PROFILE_CHANGE_REASON_REQUIRED: last_change_reason is required.</sch:assert>
      <sch:assert test="string-length(normalize-space(p:payload/p:approval_ref)) &gt; 0">E1029_RELEASE_GATE_PROFILE_APPROVAL_REQUIRED: approval_ref is required.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
