<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="execution-trace-append-only-draft">
    <sch:rule context="p:pxml[p:meta/p:doc_class='execution_trace']">
      <sch:assert test="count(p:payload/p:events/p:event) &gt;= 1">E376_TRACE_MIN_EVENTS: execution_trace must have at least one event.</sch:assert>
      <sch:assert test="count(p:payload/p:events/p:event[p:event_seq=position()]) = count(p:payload/p:events/p:event)">E377_TRACE_SEQ_POSITION: event_seq must be contiguous and position-aligned.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
