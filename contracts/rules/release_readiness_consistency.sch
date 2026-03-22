<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="release-readiness-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='session_report']">
      <sch:assert test="not(normalize-space(p:payload/p:release_readiness_result)='pass') or normalize-space(p:payload/p:runbook_result)='success'">E930_RELEASE_READINESS_PASS_RUNBOOK_REQUIRED: pass release_readiness_result requires runbook_result=success.</sch:assert>
      <sch:assert test="not(normalize-space(p:payload/p:release_readiness_result)='pass') or normalize-space(p:payload/p:render_decision)='rendered' or normalize-space(p:payload/p:render_decision)='rendered_with_warning'">E931_RELEASE_READINESS_PASS_RENDER_REQUIRED: pass release_readiness_result requires rendered or rendered_with_warning decision.</sch:assert>
      <sch:assert test="not(normalize-space(p:payload/p:release_readiness_result)='fail') or normalize-space(p:payload/p:runbook_result)='blocked' or normalize-space(p:payload/p:runbook_result)='failed' or normalize-space(p:payload/p:render_decision)='denied'">E932_RELEASE_READINESS_FAIL_RESULT_REQUIRED: fail release_readiness_result requires blocked/failed runbook_result or denied render_decision.</sch:assert>
      <sch:assert test="not(normalize-space(p:payload/p:render_decision)='denied') or normalize-space(p:payload/p:runbook_result)!='success'">E933_RELEASE_READINESS_DENIED_SUCCESS_INVALID: denied render_decision cannot pair with runbook_result=success.</sch:assert>
      <sch:assert test="not(normalize-space(p:payload/p:render_override_used)='true') or count(p:payload/p:warnings/p:item[contains(.,'override')]) &gt;= 1">E934_RELEASE_READINESS_OVERRIDE_WARNING_REQUIRED: render_override_used=true requires at least one override warning item.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
