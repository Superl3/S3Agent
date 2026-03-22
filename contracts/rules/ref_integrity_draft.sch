<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="reference-integrity-draft">
    <sch:rule context="p:pxml[p:meta/p:doc_class='manager_route']">
      <sch:assert test="count(p:refs/p:ref[p:doc_class='task_intake']) = 1">E340_MANAGER_ROUTE_REF_COUNT: manager_route must reference exactly one task_intake.</sch:assert>
      <sch:assert test="count(p:refs/p:ref[p:doc_class='task_intake' and p:relation='intake']) = 1">E341_MANAGER_ROUTE_REF_RELATION: manager_route task_intake reference must have relation='intake'.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='execution_packet']">
      <sch:assert test="count(p:refs/p:ref[p:doc_class='manager_route']) = 1">E342_EXEC_PACKET_ROUTE_REF_COUNT: execution_packet must reference exactly one manager_route.</sch:assert>
      <sch:assert test="count(p:refs/p:ref[p:doc_class='manager_route' and p:relation='route']) = 1">E343_EXEC_PACKET_ROUTE_REF_RELATION: manager_route reference must have relation='route'.</sch:assert>
      <sch:assert test="not(p:payload/p:planner_notes_ref) or p:payload/p:planner_notes_ref/p:doc_class='plan_sidecar'">E344_EXEC_PACKET_PLANNER_REF_CLASS: planner_notes_ref must point to doc_class=plan_sidecar.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
