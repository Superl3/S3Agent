from __future__ import annotations

import re

from lxml import etree


NS = {"p": "urn:pxml:v1"}


def test_active_harness_config_excludes_reviewer_agent(repo_root) -> None:
    config_text = (repo_root / "opencode.jsonc").read_text(encoding="utf-8")
    assert re.search(r'^\s*"reviewer"\s*:', config_text, re.MULTILINE) is None
    for agent_name in ("manager", "implementer", "planner", "verifier"):
        assert re.search(rf'^\s*"{agent_name}"\s*:', config_text, re.MULTILINE), (
            agent_name
        )


def test_subagents_default_to_global_deny_permissions(repo_root) -> None:
    config_text = (repo_root / "opencode.jsonc").read_text(encoding="utf-8")
    for agent_name in ("implementer", "planner", "verifier"):
        pattern = (
            rf'"{agent_name}"\s*:\s*\{{[\s\S]*?'
            rf'"permission"\s*:\s*\{{[\s\S]*?"\*"\s*:\s*"deny"'
        )
        assert re.search(pattern, config_text), agent_name


def test_manager_task_delegation_is_disabled_by_default(repo_root) -> None:
    config_text = (repo_root / "opencode.jsonc").read_text(encoding="utf-8")
    manager_task_pattern = (
        r'"manager"\s*:\s*\{[\s\S]*?'
        r'"permission"\s*:\s*\{[\s\S]*?'
        r'"task"\s*:\s*\{[\s\S]*?"\*"\s*:\s*"deny"'
    )
    assert re.search(manager_task_pattern, config_text)
    assert '"implementer": "allow"' not in config_text
    assert '"planner": "allow"' not in config_text
    assert '"verifier": "allow"' not in config_text


def test_manager_uses_direct_investigation_tools(repo_root) -> None:
    config_text = (repo_root / "opencode.jsonc").read_text(encoding="utf-8")
    manager_permission_block = re.search(
        r'"manager"\s*:\s*\{[\s\S]*?"permission"\s*:\s*\{([\s\S]*?)\}\s*\}',
        config_text,
    )
    assert manager_permission_block, "manager permission block missing"
    block_text = manager_permission_block.group(1)
    for tool_name in ("read", "glob", "grep", "bash"):
        assert re.search(rf'"{tool_name}"\s*:\s*"allow"', block_text), tool_name


def test_route_path_enum_excludes_reviewer_post(repo_root) -> None:
    enum_text = (repo_root / "contracts" / "pxml" / "common_enums.xsd").read_text(
        encoding="utf-8"
    )
    assert 'value="reviewer_post"' not in enum_text


def test_healthy_runtime_manager_routes_exclude_reviewer_lane(source_runtime) -> None:
    for task_id in ("task_impl_feature_direct_001", "task_verify_post_smoke_001"):
        route_path = source_runtime / "latest" / f"{task_id}_manager_route.pxml"
        tree = etree.parse(str(route_path))
        assert not tree.xpath(
            "/p:pxml/p:payload/p:lane_flags/p:reviewer",
            namespaces=NS,
        )


def test_manager_contract_has_noop_subagent_loop_guards(repo_root) -> None:
    contract_text = (repo_root / "agents" / "manager" / "contract.pxml").read_text(
        encoding="utf-8"
    )
    assert "Handle read-only repository investigation directly" in contract_text
    assert (
        "Do not retry the same denied or empty subagent task() payload" in contract_text
    )
