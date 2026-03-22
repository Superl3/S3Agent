<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="compaction-checkpoint-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='compaction_checkpoint']">
      <sch:assert test="normalize-space(p:payload/p:source_trace_ref/p:doc_class)='execution_trace'">E800_COMPACTION_SOURCE_TRACE_CLASS_REQUIRED: source_trace_ref doc_class must be execution_trace.</sch:assert>
      <sch:assert test="number(p:payload/p:included_event_range/p:from_event_seq) &lt;= number(p:payload/p:included_event_range/p:to_event_seq)">E801_COMPACTION_EVENT_RANGE_INVALID: included_event_range from_event_seq must be less than or equal to to_event_seq.</sch:assert>
      <sch:assert test="number(p:payload/p:source_trace_last_sequence) = number(p:payload/p:included_event_range/p:to_event_seq)">E802_COMPACTION_LAST_SEQUENCE_MISMATCH: source_trace_last_sequence must match included_event_range to_event_seq.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:created_from_status_report_ref/p:doc_class)='task_status_report'">E803_COMPACTION_STATUS_REF_CLASS_REQUIRED: created_from_status_report_ref doc_class must be task_status_report.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:created_from_latest_packet_ref/p:doc_class)='execution_packet'">E804_COMPACTION_PACKET_REF_CLASS_REQUIRED: created_from_latest_packet_ref doc_class must be execution_packet.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:created_from_latest_route_ref/p:doc_class)='manager_route'">E805_COMPACTION_ROUTE_REF_CLASS_REQUIRED: created_from_latest_route_ref doc_class must be manager_route.</sch:assert>
      <sch:assert test="not(p:payload/p:created_from_latest_verification_ref) or normalize-space(p:payload/p:created_from_latest_verification_ref/p:doc_class)='verification_result'">E806_COMPACTION_VERIFICATION_REF_CLASS_REQUIRED: created_from_latest_verification_ref doc_class must be verification_result when present.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
