<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="trace-event-semantics-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='trace_event_semantics']">
      <sch:assert test="count(p:payload/p:events/p:event[p:event_type='implement_start']) &gt;= 1">E820_TRACE_SEMANTICS_EVENT_REQUIRED: implement_start semantics entry is required.</sch:assert>
      <sch:assert test="count(p:payload/p:events/p:event[p:event_type='patch_applied']) &gt;= 1">E821_TRACE_SEMANTICS_EVENT_REQUIRED: patch_applied semantics entry is required.</sch:assert>
      <sch:assert test="count(p:payload/p:events/p:event[p:event_type='blocked']) &gt;= 1">E822_TRACE_SEMANTICS_EVENT_REQUIRED: blocked semantics entry is required.</sch:assert>
      <sch:assert test="count(p:payload/p:events/p:event[p:event_type='retry_failed']) &gt;= 1">E823_TRACE_SEMANTICS_EVENT_REQUIRED: retry_failed semantics entry is required.</sch:assert>
      <sch:assert test="count(p:payload/p:events/p:event[p:event_type='verify_done']) &gt;= 1">E825_TRACE_SEMANTICS_EVENT_REQUIRED: verify_done semantics entry is required.</sch:assert>
      <sch:assert test="count(p:payload/p:events/p:event[p:event_type='escalation']) &gt;= 1">E826_TRACE_SEMANTICS_EVENT_REQUIRED: escalation semantics entry is required.</sch:assert>
      <sch:assert test="count(p:payload/p:events/p:event[p:event_type='stop']) &gt;= 1">E827_TRACE_SEMANTICS_EVENT_REQUIRED: stop semantics entry is required.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:lane_verifier_semantics) != ''">E828_TRACE_SEMANTICS_LANE_VERIFY_REQUIRED: lane_verifier_semantics must be non-empty.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:post_implement_verifier_semantics) != ''">E829_TRACE_SEMANTICS_POST_VERIFY_REQUIRED: post_implement_verifier_semantics must be non-empty.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='trace_event_semantics']/p:payload/p:events/p:event[p:event_type='verify_done']">
      <sch:assert test="normalize-space(p:verify_phase_hint)='either'">E830_TRACE_SEMANTICS_VERIFY_PHASE_REQUIRED: verify_done semantics must set verify_phase_hint=either.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
