<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="acceptance-lineage-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='execution_packet']">
      <sch:assert test="normalize-space(p:payload/p:acceptance_lock_hash) != ''">E700_PACKET_ACCEPTANCE_LOCK_HASH_REQUIRED: execution_packet must include acceptance_lock_hash.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='verification_result']">
      <sch:assert test="normalize-space(p:payload/p:acceptance_lock_sha256) != ''">E702_VERIFICATION_ACCEPTANCE_LOCK_REQUIRED: verification_result must include acceptance_lock_sha256.</sch:assert>
      <sch:assert test="count(p:refs/p:ref[p:doc_class='execution_packet']) = 1">E703_VERIFICATION_PACKET_REF_REQUIRED: verification_result must reference one execution_packet.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='execution_trace']/p:payload/p:events/p:event[p:event_type='verify_done']">
      <sch:assert test="normalize-space(p:lineage_lock_sha256) != ''">E705_TRACE_VERIFY_DONE_LINEAGE_REQUIRED: verify_done event must include lineage_lock_sha256.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
