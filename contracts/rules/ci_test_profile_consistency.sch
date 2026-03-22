<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:pxml:v1">

  <sch:ns prefix="p" uri="urn:pxml:v1"/>

  <sch:pattern id="ci-test-profile-consistency">
    <sch:rule context="p:pxml[p:meta/p:doc_class='ci_test_profile']">
      <sch:assert test="count(p:payload/p:quick_smoke_targets/p:item) &gt;= 1">E1120_CI_TEST_PROFILE_QUICK_REQUIRED: ci_test_profile must include at least one quick_smoke_targets item.</sch:assert>
      <sch:assert test="count(p:payload/p:full_regression_targets/p:item) &gt;= 1">E1121_CI_TEST_PROFILE_FULL_REQUIRED: ci_test_profile must include at least one full_regression_targets item.</sch:assert>
      <sch:assert test="count(p:payload/p:release_critical_targets/p:item) &gt;= 1">E1122_CI_TEST_PROFILE_RELEASE_CRITICAL_REQUIRED: ci_test_profile must include at least one release_critical_targets item.</sch:assert>
      <sch:assert test="count(p:payload/p:default_pytest_args/p:item) &gt;= 1">E1123_CI_TEST_PROFILE_PYTEST_ARGS_REQUIRED: ci_test_profile must include at least one default_pytest_args item.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='ci_test_profile']/p:payload/p:quick_smoke_targets/p:item">
      <sch:assert test="count(../p:item[normalize-space(text())=normalize-space(current())]) = 1">E1124_CI_TEST_PROFILE_QUICK_DUPLICATE: quick_smoke_targets entries must be unique.</sch:assert>
      <sch:assert test="count(/p:pxml/p:payload/p:full_regression_targets/p:item[normalize-space(text())=normalize-space(current())]) &gt;= 1">E1125_CI_TEST_PROFILE_QUICK_SUBSET_REQUIRED: quick_smoke_targets must be subset of full_regression_targets.</sch:assert>
      <sch:assert test="starts-with(normalize-space(text()), 'tests/') and contains(normalize-space(text()), '.py')">E1126_CI_TEST_PROFILE_TARGET_FORMAT_INVALID: pytest target must start with tests/ and include .py.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='ci_test_profile']/p:payload/p:full_regression_targets/p:item">
      <sch:assert test="count(../p:item[normalize-space(text())=normalize-space(current())]) = 1">E1127_CI_TEST_PROFILE_FULL_DUPLICATE: full_regression_targets entries must be unique.</sch:assert>
      <sch:assert test="starts-with(normalize-space(text()), 'tests/') and contains(normalize-space(text()), '.py')">E1128_CI_TEST_PROFILE_TARGET_FORMAT_INVALID: pytest target must start with tests/ and include .py.</sch:assert>
    </sch:rule>

    <sch:rule context="p:pxml[p:meta/p:doc_class='ci_test_profile']/p:payload/p:release_critical_targets/p:item">
      <sch:assert test="count(../p:item[normalize-space(text())=normalize-space(current())]) = 1">E1129_CI_TEST_PROFILE_RELEASE_DUPLICATE: release_critical_targets entries must be unique.</sch:assert>
      <sch:assert test="count(/p:pxml/p:payload/p:full_regression_targets/p:item[normalize-space(text())=normalize-space(current())]) &gt;= 1">E1130_CI_TEST_PROFILE_RELEASE_SUBSET_REQUIRED: release_critical_targets must be subset of full_regression_targets.</sch:assert>
      <sch:assert test="starts-with(normalize-space(text()), 'tests/') and contains(normalize-space(text()), '.py')">E1131_CI_TEST_PROFILE_TARGET_FORMAT_INVALID: pytest target must start with tests/ and include .py.</sch:assert>
    </sch:rule>
  </sch:pattern>

</sch:schema>
