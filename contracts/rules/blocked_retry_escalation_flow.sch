<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="blocked-retry-escalation-flow">
    <sch:rule context="p:pxml[p:meta/p:doc_class='execution_trace']/p:payload/p:events/p:event[p:event_type='implement_start']">
      <sch:assert test="count(p:artifact_refs/p:ref[p:doc_class='execution_packet']) &gt;= 1">E750_TRACE_IMPLEMENT_START_PACKET_REF_REQUIRED: implement_start event must reference execution_packet artifact.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='execution_trace']/p:payload/p:events/p:event[p:event_type='patch_applied']">
      <sch:assert test="count(p:artifact_refs/p:ref[p:doc_class='implementer_result']) &gt;= 1">E751_TRACE_PATCH_APPLIED_IMPL_RESULT_REQUIRED: patch_applied event must reference implementer_result artifact.</sch:assert>
      <sch:assert test="normalize-space(p:lineage_lock_sha256) != ''">E752_TRACE_PATCH_APPLIED_LINEAGE_REQUIRED: patch_applied event must include lineage_lock_sha256.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='execution_trace']/p:payload/p:events/p:event[p:event_type='blocked']">
      <sch:assert test="normalize-space(p:reason_code) != ''">E753_TRACE_BLOCKED_REASON_REQUIRED: blocked event must include reason_code.</sch:assert>
      <sch:assert test="number(p:attempt) &gt;= 1">E754_TRACE_BLOCKED_ATTEMPT_REQUIRED: blocked event must include attempt &gt;= 1.</sch:assert>
      <sch:assert test="normalize-space(p:lineage_lock_sha256) != ''">E755_TRACE_BLOCKED_LINEAGE_REQUIRED: blocked event must include lineage_lock_sha256.</sch:assert>
      <sch:assert test="count(p:artifact_refs/p:ref[p:doc_class='implementer_result']) &gt;= 1">E756_TRACE_BLOCKED_IMPL_RESULT_REQUIRED: blocked event must reference implementer_result artifact.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='execution_trace']/p:payload/p:events/p:event[p:event_type='retry_failed']">
      <sch:assert test="normalize-space(p:reason_code) != ''">E757_TRACE_RETRY_FAILED_REASON_REQUIRED: retry_failed event must include reason_code.</sch:assert>
      <sch:assert test="number(p:attempt) &gt;= 1">E758_TRACE_RETRY_FAILED_ATTEMPT_REQUIRED: retry_failed event must include attempt &gt;= 1.</sch:assert>
      <sch:assert test="normalize-space(p:lineage_lock_sha256) != ''">E759_TRACE_RETRY_FAILED_LINEAGE_REQUIRED: retry_failed event must include lineage_lock_sha256.</sch:assert>
      <sch:assert test="count(p:artifact_refs/p:ref[p:doc_class='implementer_result']) &gt;= 1">E760_TRACE_RETRY_FAILED_IMPL_RESULT_REQUIRED: retry_failed event must reference implementer_result artifact.</sch:assert>
      <sch:assert test="count(preceding-sibling::p:event[p:event_type='blocked']) &gt;= 1">E761_TRACE_RETRY_FAILED_BLOCKED_PRECONDITION: retry_failed event requires a prior blocked event.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='execution_trace']/p:payload/p:events/p:event[p:event_type='escalation' and starts-with(normalize-space(p:reason_code), 'implementer_')]">
      <sch:assert test="count(p:artifact_refs/p:ref[p:doc_class='implementer_result']) &gt;= 1">E763_TRACE_IMPLEMENTER_ESCALATION_IMPL_RESULT_REQUIRED: implementer escalation event must reference implementer_result artifact.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
