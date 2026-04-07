<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="sidecar-authority-draft">
    <sch:rule context="p:pxml[p:meta/p:doc_class='plan_sidecar']">
      <sch:assert test="p:meta/p:writer_agent='planner'">E630_PLAN_WRITER_AGENT: plan_sidecar writer_agent must be planner.</sch:assert>
      <sch:assert test="count(.//p:patch_constraints)=0">E631_PLAN_PATCH_AUTHORITY: plan_sidecar must not include patch authority fields.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='verification_result']">
      <sch:assert test="p:meta/p:writer_agent='verifier'">E634_VERIFY_WRITER_AGENT: verification_result writer_agent must be verifier.</sch:assert>
      <sch:assert test="count(.//p:patch_constraints)=0">E635_VERIFY_PATCH_AUTHORITY: verification_result must not include patch authority fields.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='plan_sidecar' or p:meta/p:doc_class='verification_result']">
      <sch:assert test="count(p:refs/p:ref[p:doc_class='hooks_registry' or p:doc_class='skills_registry' or p:doc_class='mcp_registry']) = 0">E636_SIDECAR_ECC_RUNTIME_REF_FORBIDDEN: sidecar runtime artifacts must not reference ECC registries for execution control.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
