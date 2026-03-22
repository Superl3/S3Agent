<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="retry-escalation-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='execution_trace']/p:payload/p:events/p:event[p:event_type='escalation']">
      <sch:assert test="normalize-space(p:reason_code) != ''">E710_ESCALATION_REASON_CODE_REQUIRED: escalation event must include reason_code.</sch:assert>
      <sch:assert test="number(p:attempt) &gt;= 1">E711_ESCALATION_ATTEMPT_REQUIRED: escalation event must include attempt >= 1.</sch:assert>
      <sch:assert test="count(p:artifact_refs/p:ref) &gt;= 1">E712_ESCALATION_ARTIFACT_REF_REQUIRED: escalation event must include artifact reference(s).</sch:assert>
      <sch:assert test="not(number(p:attempt) &gt;= 3) or count(following-sibling::p:event[p:event_type='stop']) &gt;= 1">E713_ESCALATION_STOP_REQUIRED: escalation attempt >= 3 requires a downstream stop event.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='execution_trace']/p:payload/p:events/p:event[p:event_type='stop']">
      <sch:assert test="count(p:artifact_refs/p:ref) &gt;= 1">E714_STOP_ARTIFACT_REF_REQUIRED: stop event must include artifact reference(s).</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
