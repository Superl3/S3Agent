<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="verification-result-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='verification_result']">
      <sch:assert test="count(p:payload/p:tests_run/p:test) &gt;= 1">E610_VERIFICATION_TESTS_EMPTY: verification_result must contain at least one tests_run entry.</sch:assert>

      <sch:assert test="count(p:payload/p:tests_run/p:test[p:result='pass']) = number(p:payload/p:outcomes/p:passed)">E611_VERIFICATION_OUTCOME_PASS_COUNT: outcomes.passed must match tests_run pass count.</sch:assert>
      <sch:assert test="count(p:payload/p:tests_run/p:test[p:result='fail']) = number(p:payload/p:outcomes/p:failed)">E612_VERIFICATION_OUTCOME_FAIL_COUNT: outcomes.failed must match tests_run fail count.</sch:assert>
      <sch:assert test="count(p:payload/p:tests_run/p:test[p:result='error']) = number(p:payload/p:outcomes/p:errored)">E613_VERIFICATION_OUTCOME_ERROR_COUNT: outcomes.errored must match tests_run error count.</sch:assert>
      <sch:assert test="count(p:payload/p:tests_run/p:test[p:result='skipped']) = number(p:payload/p:outcomes/p:skipped)">E614_VERIFICATION_OUTCOME_SKIPPED_COUNT: outcomes.skipped must match tests_run skipped count.</sch:assert>

      <sch:assert test="not(p:payload/p:final_verdict='pass') or (number(p:payload/p:outcomes/p:failed)=0 and number(p:payload/p:outcomes/p:errored)=0 and number(p:payload/p:outcomes/p:skipped)=0)">E615_VERDICT_PASS_COUNTS_INVALID: pass verdict requires zero failed errored skipped counts.</sch:assert>
      <sch:assert test="not(p:payload/p:final_verdict='pass') or count(p:payload/p:unverified_areas/p:item[normalize-space(.)!='none'])=0">E616_VERDICT_PASS_UNVERIFIED_INVALID: pass verdict cannot contain unresolved unverified areas.</sch:assert>

      <sch:assert test="not(p:payload/p:final_verdict='fail') or number(p:payload/p:outcomes/p:failed) &gt; 0">E617_VERDICT_FAIL_COUNTS_INVALID: fail verdict requires at least one failed check.</sch:assert>

      <sch:assert test="not(p:payload/p:final_verdict='inconclusive') or (number(p:payload/p:outcomes/p:errored) &gt; 0 or number(p:payload/p:outcomes/p:skipped) &gt; 0 or count(p:payload/p:unverified_areas/p:item[normalize-space(.)!='none']) &gt; 0)">E618_VERDICT_INCONCLUSIVE_BASIS_MISSING: inconclusive verdict requires errored or skipped checks or unresolved unverified areas.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
