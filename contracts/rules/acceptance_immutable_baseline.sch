<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="acceptance-immutable-baseline">
    <sch:rule context="p:pxml[p:meta/p:doc_class='manager_route']">
      <sch:assert test="normalize-space(p:payload/p:acceptance_lock/p:lock_id) != ''">E330_ACCEPTANCE_LOCK_MISSING: acceptance_lock.lock_id is required.</sch:assert>
      <sch:assert test="normalize-space(p:payload/p:acceptance_lock/p:lock_sha256) != ''">E331_ACCEPTANCE_LOCK_HASH_MISSING: acceptance_lock.lock_sha256 is required.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='execution_packet']">
      <sch:assert test="count(p:payload/p:acceptance_checks/p:check) &gt;= 1">E332_ACCEPTANCE_CHECKS_EMPTY: execution_packet must contain at least one acceptance check.</sch:assert>
      <sch:assert test="count(p:payload/p:acceptance_checks/p:check[p:deterministic='true']) = count(p:payload/p:acceptance_checks/p:check)">E333_ACCEPTANCE_NON_DETERMINISTIC: all acceptance checks must be deterministic=true.</sch:assert>
      <sch:assert test="p:meta/p:writer_agent='manager'">E334_ACCEPTANCE_WRITER_INVALID: execution_packet acceptance contract must be manager-authored.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
