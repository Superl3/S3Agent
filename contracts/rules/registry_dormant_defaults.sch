<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="registry-dormant-defaults">
    <sch:rule context="p:pxml[p:meta/p:doc_class='hooks_registry']">
      <sch:assert test="count(p:payload/p:entries/p:hook[p:enabled_by_default='false']) = count(p:payload/p:entries/p:hook)">E510_HOOKS_DEFAULT_ENABLED: hooks_registry entries must be enabled_by_default=false in Batch 1.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='skills_registry']">
      <sch:assert test="count(p:payload/p:entries/p:skill[p:enabled_by_default='false']) = count(p:payload/p:entries/p:skill)">E511_SKILLS_DEFAULT_ENABLED: skills_registry entries must be enabled_by_default=false in Batch 1.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='mcp_registry']">
      <sch:assert test="count(p:payload/p:entries/p:mcp[p:enabled_by_default='false']) = count(p:payload/p:entries/p:mcp)">E512_MCP_DEFAULT_ENABLED: mcp_registry entries must be enabled_by_default=false in Batch 1.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='extension_activation_policy']">
      <sch:assert test="count(p:payload/p:policies/p:policy[p:write_access='false']) = count(p:payload/p:policies/p:policy)">E513_POLICY_WRITE_ACCESS: write_access must be false for all entries in Batch 1.</sch:assert>
      <sch:assert test="count(p:payload/p:policies/p:policy[p:default_state='disabled']) = count(p:payload/p:policies/p:policy)">E514_POLICY_DEFAULT_STATE: default_state must be disabled in Batch 1.</sch:assert>
      <sch:assert test="count(p:payload/p:policies/p:policy[p:activation_mode='disabled' or p:activation_mode='advisory' or p:activation_mode='read_only']) = count(p:payload/p:policies/p:policy)">E515_POLICY_ACTIVATION_MODE: activation_mode must stay dormant in Batch 1.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
