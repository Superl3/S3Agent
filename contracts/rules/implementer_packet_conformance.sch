<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="implementer-packet-conformance">
    <sch:rule context="p:pxml[p:meta/p:doc_class='implementer_result']">
      <sch:assert test="count(p:refs/p:ref[p:doc_class='execution_packet']) = 1">E740_IMPL_PACKET_REF_REQUIRED: implementer_result must reference exactly one execution_packet artifact.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:packet_ref/p:doc_id) != ''">E741_IMPL_PACKET_REF_PAYLOAD_REQUIRED: implementer_result payload must include packet_ref doc_id.</sch:assert>
      <sch:assert test="p:payload/p:packet_ref/p:doc_class = 'execution_packet'">E742_IMPL_PACKET_REF_CLASS_REQUIRED: implementer_result payload packet_ref doc_class must be execution_packet.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:task_id) = normalize-space(p:meta/p:task_id)">E743_IMPL_TASK_ID_MISMATCH: implementer_result payload task_id must match meta task_id.</sch:assert>
      <sch:assert test="not(p:payload/p:result_status='applied') or (count(p:payload/p:modified_files/p:item) + count(p:payload/p:created_files/p:item)) &gt;= 1">E744_IMPL_APPLIED_FILES_REQUIRED: applied implementer_result must report modified_files or created_files.</sch:assert>
      <sch:assert test="not(p:payload/p:result_status='applied') or count(p:payload/p:patch_evidence_refs/p:item) &gt;= 1">E745_IMPL_APPLIED_EVIDENCE_REQUIRED: applied implementer_result must include patch evidence references.</sch:assert>
      <sch:assert test="not(p:payload/p:result_status='no_op') or ((count(p:payload/p:modified_files/p:item) + count(p:payload/p:created_files/p:item)) = 0)">E746_IMPL_NOOP_FILES_EMPTY_REQUIRED: no_op implementer_result must not report file mutations.</sch:assert>
      <sch:assert test="not(p:payload/p:result_status='blocked' or p:payload/p:result_status='retry_failed' or p:payload/p:result_status='escalated') or normalize-space(p:payload/p:blocked_reason) != ''">E747_IMPL_BLOCKED_REASON_REQUIRED: blocked/retry_failed/escalated implementer_result must include blocked_reason.</sch:assert>
      <sch:assert test="not(p:payload/p:result_status='retry_failed') or number(p:payload/p:retry_count) &gt;= 1">E748_IMPL_RETRY_COUNT_REQUIRED: retry_failed implementer_result must include retry_count &gt;= 1.</sch:assert>
      <sch:assert test="not(p:payload/p:result_status='retry_failed' or p:payload/p:result_status='escalated') or normalize-space(p:payload/p:escalation_requested)='true'">E749_IMPL_ESCALATION_FLAG_REQUIRED: retry_failed/escalated implementer_result must set escalation_requested=true.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
