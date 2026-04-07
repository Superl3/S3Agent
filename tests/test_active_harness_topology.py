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
