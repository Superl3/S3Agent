<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="manager-route-lane-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='manager_route']">
      <sch:assert test="
        (p:payload/p:selected_path='direct' and p:payload/p:lane_flags/p:planner='false' and p:payload/p:lane_flags/p:verifier='false') or
        (p:payload/p:selected_path='planner_pre' and p:payload/p:lane_flags/p:planner='true' and p:payload/p:lane_flags/p:verifier='false') or
        (p:payload/p:selected_path='verifier_post' and p:payload/p:lane_flags/p:planner='false' and p:payload/p:lane_flags/p:verifier='true') or
        (p:payload/p:selected_path='full_lane' and p:payload/p:lane_flags/p:planner='true' and p:payload/p:lane_flags/p:verifier='true')
      ">E320_ROUTE_LANE_MISMATCH: selected_path and lane_flags must match canonical mapping.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
