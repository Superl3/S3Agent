<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="sidecar-ref-integrity-draft">
    <sch:rule context="p:pxml[p:meta/p:doc_class='plan_sidecar']">
      <sch:assert test="count(p:refs/p:ref[p:doc_class='task_intake']) = 1">E620_PLAN_REF_TASK_INTAKE: plan_sidecar must reference exactly one task_intake.</sch:assert>
      <sch:assert test="count(p:refs/p:ref[p:doc_class='manager_route']) = 1">E621_PLAN_REF_MANAGER_ROUTE: plan_sidecar must reference exactly one manager_route.</sch:assert>
      <sch:assert test="count(p:payload/p:assumptions/p:item) &gt;= 1">E622_PLAN_ASSUMPTIONS_REQUIRED: plan_sidecar must include at least one assumption.</sch:assert>
      <sch:assert test="count(p:payload/p:proposed_steps/p:item) &gt;= 1">E623_PLAN_STEPS_REQUIRED: plan_sidecar must include at least one proposed step.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='review_sidecar']">
      <sch:assert test="count(p:refs/p:ref[p:doc_class='execution_packet']) = 1">E624_REVIEW_REF_EXEC_PACKET: review_sidecar must reference exactly one execution_packet.</sch:assert>
      <sch:assert test="count(p:payload/p:review_target_refs/p:ref[p:doc_class='execution_packet']) &gt;= 1">E625_REVIEW_TARGET_EXEC_PACKET: review_target_refs must include execution_packet reference.</sch:assert>
      <sch:assert test="count(p:payload/p:findings/p:finding) &gt;= 1">E626_REVIEW_FINDINGS_REQUIRED: review_sidecar must contain at least one finding.</sch:assert>
      <sch:assert test="not(p:payload/p:decision='approve') or (number(p:payload/p:blocking_count)=0 and count(p:payload/p:findings/p:finding[p:severity='blocker'])=0)">E627_REVIEW_APPROVE_BLOCKING_INVALID: approve decision cannot carry blocking findings.</sch:assert>
      <sch:assert test="not(p:payload/p:decision='escalate') or (number(p:payload/p:blocking_count) &gt; 0 or count(p:payload/p:findings/p:finding[p:severity='blocker']) &gt; 0)">E628_REVIEW_ESCALATE_BASIS_MISSING: escalate decision requires blocking basis.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='verification_result']">
      <sch:assert test="count(p:refs/p:ref[p:doc_class='execution_packet']) = 1">E629_VERIFY_REF_EXEC_PACKET: verification_result must reference exactly one execution_packet.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
