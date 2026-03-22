<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="failure-reason-taxonomy-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='failure_reason_taxonomy']">
      <sch:assert test="count(p:payload/p:reasons/p:reason) &gt;= 1">E781_FAILURE_TAXONOMY_EMPTY: failure_reason_taxonomy must include at least one reason entry.</sch:assert>
      <sch:assert test="count(p:payload/p:reasons/p:reason[p:category='implementer']) &gt;= 1">E782_FAILURE_TAXONOMY_CATEGORY_REQUIRED: implementer category is required.</sch:assert>
      <sch:assert test="count(p:payload/p:reasons/p:reason[p:category='verifier']) &gt;= 1">E783_FAILURE_TAXONOMY_CATEGORY_REQUIRED: verifier category is required.</sch:assert>
      <sch:assert test="count(p:payload/p:reasons/p:reason[p:category='coordinator']) &gt;= 1">E784_FAILURE_TAXONOMY_CATEGORY_REQUIRED: coordinator category is required.</sch:assert>
      <sch:assert test="count(p:payload/p:reasons/p:reason[p:category='reviewer']) &gt;= 1">E785_FAILURE_TAXONOMY_CATEGORY_REQUIRED: reviewer category is required.</sch:assert>
      <sch:assert test="count(p:payload/p:reasons/p:reason[p:category='planner']) &gt;= 1">E786_FAILURE_TAXONOMY_CATEGORY_REQUIRED: planner category is required.</sch:assert>
      <sch:assert test="count(p:payload/p:reasons/p:reason[p:category='system']) &gt;= 1">E787_FAILURE_TAXONOMY_CATEGORY_REQUIRED: system category is required.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='failure_reason_taxonomy']/p:payload/p:reasons/p:reason">
      <sch:assert test="normalize-space(p:taxonomy_id)=normalize-space(../../p:taxonomy_id)">E788_FAILURE_TAXONOMY_ID_MISMATCH: reason taxonomy_id must match payload taxonomy_id.</sch:assert>
      <sch:assert test="count(../p:reason[p:code = current()/p:code]) = 1">E789_FAILURE_TAXONOMY_CODE_DUPLICATE: reason code must be unique within taxonomy.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='implementer_result'][p:payload/p:result_status='blocked' or p:payload/p:result_status='retry_failed' or p:payload/p:result_status='escalated']">
      <sch:assert test="starts-with(normalize-space(p:payload/p:blocked_reason), 'implementer_')">E790_FAILURE_CODE_PREFIX_INVALID: implementer_result blocked_reason must use implementer_* taxonomy code.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='execution_trace']/p:payload/p:events/p:event[p:event_type='blocked' or p:event_type='retry_failed']">
      <sch:assert test="starts-with(normalize-space(p:reason_code), 'implementer_')">E791_FAILURE_CODE_PREFIX_INVALID: blocked/retry_failed trace events must use implementer_* taxonomy codes.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
