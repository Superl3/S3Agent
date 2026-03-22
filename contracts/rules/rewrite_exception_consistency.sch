<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="execution-packet-rewrite-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='execution_packet']">
      <sch:assert test="
        not(p:payload/p:patch_constraints/p:patch_mode='full_rewrite_exception') or
        (p:payload/p:patch_constraints/p:rewrite_exception_approved='true' and normalize-space(p:payload/p:patch_constraints/p:rewrite_exception_reason) != '')
      ">E350_REWRITE_EXCEPTION_INVALID: full_rewrite_exception requires approval=true and non-empty reason.</sch:assert>

      <sch:assert test="
        not(p:payload/p:patch_constraints/p:patch_mode='patch_first') or
        p:payload/p:patch_constraints/p:rewrite_exception_approved='false'
      ">E351_PATCH_FIRST_APPROVAL_INVALID: patch_first must not be marked as approved rewrite exception.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
