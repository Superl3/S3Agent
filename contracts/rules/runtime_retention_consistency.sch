<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="runtime-retention-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='runtime_retention_policy']">
      <sch:assert test="count(p:payload/p:stale_artifact_detection_basis/p:item) &gt;= 1">E810_RETENTION_DETECTION_BASIS_REQUIRED: stale_artifact_detection_basis must include at least one item.</sch:assert>
      <sch:assert test="count(p:payload/p:cleanup_vs_quarantine_criteria/p:item) &gt;= 2">E811_RETENTION_CRITERIA_MIN_REQUIRED: cleanup_vs_quarantine_criteria must include at least two items.</sch:assert>
      <sch:assert test="contains(translate(normalize-space(p:payload/p:task_id_scoped_cleanup_principle),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'task_id')">E812_RETENTION_TASK_SCOPE_REQUIRED: task_id_scoped_cleanup_principle must explicitly mention task_id scope.</sch:assert>
      <sch:assert test="contains(translate(normalize-space(p:payload/p:lineage_mismatch_response),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'quarantine')">E813_RETENTION_LINEAGE_RESPONSE_REQUIRED: lineage_mismatch_response must mention quarantine handling.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
