<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="session-report-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='session_report']">
      <sch:assert test="normalize-space(p:payload/p:derived)='true'">E910_SESSION_REPORT_DERIVED_REQUIRED: session_report payload derived must be true.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:task_id)=normalize-space(p:meta/p:task_id)">E911_SESSION_REPORT_TASK_ID_MISMATCH: payload task_id must match meta task_id.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:source_intake_ref/p:doc_class)='task_intake'">E912_SESSION_REPORT_INTAKE_REF_CLASS_REQUIRED: source_intake_ref doc_class must be task_intake.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:latest_route_ref/p:doc_class)='manager_route'">E913_SESSION_REPORT_ROUTE_REF_CLASS_REQUIRED: latest_route_ref doc_class must be manager_route.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:latest_packet_ref/p:doc_class)='execution_packet'">E914_SESSION_REPORT_PACKET_REF_CLASS_REQUIRED: latest_packet_ref doc_class must be execution_packet.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:latest_status_report_ref/p:doc_class)='task_status_report'">E915_SESSION_REPORT_STATUS_REF_CLASS_REQUIRED: latest_status_report_ref doc_class must be task_status_report.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:latest_preflight_ref/p:doc_class)='operator_preflight_report'">E916_SESSION_REPORT_PREFLIGHT_REF_CLASS_REQUIRED: latest_preflight_ref doc_class must be operator_preflight_report.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:latest_trace_ref/p:doc_class)='execution_trace'">E917_SESSION_REPORT_TRACE_REF_CLASS_REQUIRED: latest_trace_ref doc_class must be execution_trace.</sch:assert>
      <sch:assert test="not(p:payload/p:latest_render_report_ref) or normalize-space(p:payload/p:latest_render_report_ref/p:doc_class)='final_render_report'">E918_SESSION_REPORT_RENDER_REF_CLASS_REQUIRED: latest_render_report_ref doc_class must be final_render_report when present.</sch:assert>
      <sch:assert test="not(p:payload/p:latest_verification_ref) or normalize-space(p:payload/p:latest_verification_ref/p:doc_class)='verification_result'">E919_SESSION_REPORT_VERIFICATION_REF_CLASS_REQUIRED: latest_verification_ref doc_class must be verification_result when present.</sch:assert>
      <sch:assert test="not(p:payload/p:latest_compaction_checkpoint_ref) or normalize-space(p:payload/p:latest_compaction_checkpoint_ref/p:doc_class)='compaction_checkpoint'">E920_SESSION_REPORT_COMPACTION_REF_CLASS_REQUIRED: latest_compaction_checkpoint_ref doc_class must be compaction_checkpoint when present.</sch:assert>
      <sch:assert test="not(normalize-space(p:payload/p:render_override_used)='true') or normalize-space(p:payload/p:render_decision)='rendered_with_warning'">E921_SESSION_REPORT_OVERRIDE_DECISION_INVALID: render_override_used=true requires render_decision=rendered_with_warning.</sch:assert>
      <sch:assert test="not(normalize-space(p:payload/p:render_decision)='skipped') or not(p:payload/p:latest_render_report_ref)">E922_SESSION_REPORT_SKIPPED_RENDER_REF_FORBIDDEN: render_decision=skipped must not include latest_render_report_ref.</sch:assert>
      <sch:assert test="count(p:payload/p:warnings/p:item[normalize-space(text())='none']) = 0 or count(p:payload/p:warnings/p:item) = 1">E923_SESSION_REPORT_WARNINGS_INVALID: warnings cannot mix 'none' with additional values.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
