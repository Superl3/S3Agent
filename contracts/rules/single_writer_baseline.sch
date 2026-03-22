<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="single-writer-baseline">
    <sch:rule context="p:pxml[p:meta/p:doc_class='task_intake']">
      <sch:assert test="p:meta/p:writer_agent='manager' or p:meta/p:writer_agent='system'">E310_TASK_INTAKE_WRITER_INVALID: task_intake writer_agent must be manager or system.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='manager_route']">
      <sch:assert test="p:meta/p:writer_agent='manager'">E311_MANAGER_ROUTE_WRITER_INVALID: manager_route writer_agent must be manager.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='execution_packet']">
      <sch:assert test="p:meta/p:writer_agent='manager'">E312_EXEC_PACKET_WRITER_INVALID: execution_packet writer_agent must be manager.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
