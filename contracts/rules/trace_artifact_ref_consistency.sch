<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="trace-artifact-ref-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='execution_trace']/p:payload/p:events/p:event[p:event_type='route']">
      <sch:assert test="count(p:artifact_refs/p:ref[p:doc_class='manager_route']) &gt;= 1">E720_TRACE_ROUTE_REF_REQUIRED: route event must reference manager_route artifact.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='execution_trace']/p:payload/p:events/p:event[p:event_type='packet_issued']">
      <sch:assert test="count(p:artifact_refs/p:ref[p:doc_class='execution_packet']) &gt;= 1">E721_TRACE_PACKET_REF_REQUIRED: packet_issued event must reference execution_packet artifact.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='execution_trace']/p:payload/p:events/p:event[p:event_type='review_done']">
      <sch:assert test="count(p:artifact_refs/p:ref[p:doc_class='review_sidecar']) &gt;= 1">E722_TRACE_REVIEW_REF_REQUIRED: review_done event must reference review_sidecar artifact.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='execution_trace']/p:payload/p:events/p:event[p:event_type='verify_done']">
      <sch:assert test="count(p:artifact_refs/p:ref[p:doc_class='verification_result']) &gt;= 1">E723_TRACE_VERIFY_REF_REQUIRED: verify_done event must reference verification_result artifact.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
