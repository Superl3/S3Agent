<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="final-render-report-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='final_render_report']">
      <sch:assert test="normalize-space(p:payload/p:derived)='true'">E860_RENDER_REPORT_DERIVED_REQUIRED: final_render_report payload derived must be true.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:source_preflight_ref/p:doc_class)='operator_preflight_report'">E861_RENDER_REPORT_PREFLIGHT_REF_CLASS_REQUIRED: source_preflight_ref doc_class must be operator_preflight_report.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:source_status_report_ref/p:doc_class)='task_status_report'">E862_RENDER_REPORT_STATUS_REF_CLASS_REQUIRED: source_status_report_ref doc_class must be task_status_report.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:source_route_ref/p:doc_class)='manager_route'">E863_RENDER_REPORT_ROUTE_REF_CLASS_REQUIRED: source_route_ref doc_class must be manager_route.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:source_packet_ref/p:doc_class)='execution_packet'">E864_RENDER_REPORT_PACKET_REF_CLASS_REQUIRED: source_packet_ref doc_class must be execution_packet.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:source_trace_ref/p:doc_class)='execution_trace'">E865_RENDER_REPORT_TRACE_REF_CLASS_REQUIRED: source_trace_ref doc_class must be execution_trace.</sch:assert>
      <sch:assert test="not(p:payload/p:source_verification_ref) or normalize-space(p:payload/p:source_verification_ref/p:doc_class)='verification_result'">E866_RENDER_REPORT_VERIFICATION_REF_CLASS_REQUIRED: source_verification_ref doc_class must be verification_result when present.</sch:assert>
      <sch:assert test="not(p:payload/p:source_compaction_checkpoint_ref) or normalize-space(p:payload/p:source_compaction_checkpoint_ref/p:doc_class)='compaction_checkpoint'">E867_RENDER_REPORT_COMPACTION_REF_CLASS_REQUIRED: source_compaction_checkpoint_ref doc_class must be compaction_checkpoint when present.</sch:assert>
      <sch:assert test="contains(normalize-space(p:payload/p:generated_exports/p:pxml_path), 'rendered')">E868_RENDER_REPORT_EXPORT_PATH_REQUIRED: generated_exports pxml_path must point into runtime rendered path.</sch:assert>
      <sch:assert test="count(p:payload/p:summary_sections/p:section[p:section_name='overview']) &gt;= 1">E869_RENDER_REPORT_SECTION_REQUIRED: summary_sections must include overview.</sch:assert>
      <sch:assert test="count(p:payload/p:summary_sections/p:section[p:section_name='path_and_lane']) &gt;= 1">E870_RENDER_REPORT_SECTION_REQUIRED: summary_sections must include path_and_lane.</sch:assert>
      <sch:assert test="count(p:payload/p:summary_sections/p:section[p:section_name='execution_outcome']) &gt;= 1">E871_RENDER_REPORT_SECTION_REQUIRED: summary_sections must include execution_outcome.</sch:assert>
      <sch:assert test="count(p:payload/p:summary_sections/p:section[p:section_name='verification_outcome']) &gt;= 1">E872_RENDER_REPORT_SECTION_REQUIRED: summary_sections must include verification_outcome.</sch:assert>
      <sch:assert test="count(p:payload/p:summary_sections/p:section[p:section_name='current_risks']) &gt;= 1">E873_RENDER_REPORT_SECTION_REQUIRED: summary_sections must include current_risks.</sch:assert>
      <sch:assert test="count(p:payload/p:summary_sections/p:section[p:section_name='next_action']) &gt;= 1">E874_RENDER_REPORT_SECTION_REQUIRED: summary_sections must include next_action.</sch:assert>
      <sch:assert test="not(normalize-space(p:payload/p:render_mode)='denied') or not(p:payload/p:generated_exports/p:markdown_path)">E875_RENDER_REPORT_DENIED_MARKDOWN_FORBIDDEN: denied render_mode must not include markdown_path export.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
