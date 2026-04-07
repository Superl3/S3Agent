from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_repo_scout_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "repo_scout.py"
    spec = importlib.util.spec_from_file_location("repo_scout_module", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_collect_serena_evidence_parses_symbol_and_pattern_results(
    monkeypatch, tmp_path
):
    repo_scout = _load_repo_scout_module()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "task_status_report.py").write_text(
        "def build_status_report():\n    return 'ok'\n",
        encoding="utf-8",
    )

    class FakeSession:
        def __init__(self, workspace_root: Path) -> None:
            self.workspace_root = workspace_root

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def call_tool(self, name: str, arguments: dict[str, object]) -> str:
            if name == "get_symbols_overview":
                return json.dumps({"Function": ["build_status_report"]})
            if name == "find_symbol":
                return json.dumps(
                    [
                        {
                            "name_path": "build_status_report",
                            "kind": "Function",
                            "relative_path": "scripts\\task_status_report.py",
                            "body_location": {"start_line": 643, "end_line": 936},
                        }
                    ]
                )
            if name == "find_referencing_symbols":
                return json.dumps(
                    {
                        "scripts\\task_status_report.py": {
                            "Variable": [
                                {
                                    "name_path": "main/report_tree",
                                    "body_location": {
                                        "start_line": 1060,
                                        "end_line": 1060,
                                    },
                                    "content_around_reference": "...",
                                }
                            ]
                        }
                    }
                )
            if name == "search_for_pattern":
                return json.dumps(
                    {
                        "scripts\\task_status_report.py": [
                            "  > 643:def build_status_report(",
                        ]
                    }
                )
            if name == "activate_project":
                return "ok"
            raise AssertionError(f"Unexpected tool name: {name}")

    monkeypatch.setattr(
        repo_scout, "serena_is_available", lambda workspace_root: (True, "ready")
    )
    monkeypatch.setattr(repo_scout, "SerenaMcpSession", FakeSession)

    evidence, notes, provider = repo_scout.collect_serena_evidence(
        workspace_root=tmp_path,
        identifier_tokens=["build_status_report"],
        broad_tokens=["read_only_investigation"],
        localization_targets=["scripts/task_status_report.py"],
    )

    assert provider.used is True
    assert provider.success is True
    assert any(item.symbol == "build_status_report" for item in evidence)
    assert any(item.symbol == "main/report_tree" for item in evidence)
    assert any(item.path == "scripts/task_status_report.py" for item in evidence)
    assert any("Serena overview" in note for note in notes)
    assert "reference hits" in provider.notes


def test_collect_context7_evidence_fetches_and_caches_docs(monkeypatch, tmp_path):
    repo_scout = _load_repo_scout_module()

    def fake_json_request(url: str):
        assert "context7.com/api/v2/libs/search" in url
        return {
            "results": [
                {
                    "id": "/websites/react_dev_reference",
                    "title": "React Reference",
                    "description": "Hooks and component APIs",
                }
            ]
        }

    def fake_text_request(url: str) -> str:
        assert "context7.com/api/v2/context" in url
        return "### useState\nUse state in function components."

    monkeypatch.setattr(repo_scout, "maybe_json_request", fake_json_request)
    monkeypatch.setattr(repo_scout, "text_request", fake_text_request)

    evidence, findings, cache_refs, provider = repo_scout.collect_context7_evidence(
        request_text="Check current React hooks API",
        requested_outcome="Summarize useState usage",
        workspace_root=tmp_path,
        cache_root=tmp_path / "runtime" / "exploration" / "cache",
        cache_ref_base=tmp_path / "runtime",
        has_internal_evidence=False,
    )

    assert provider.used is True
    assert provider.success is True
    assert cache_refs
    assert evidence
    cache_file = tmp_path / "runtime" / cache_refs[0]
    assert cache_file.exists()
    assert "useState" in cache_file.read_text(encoding="utf-8")
    assert any(
        "React" in item.summary or "react" in item.summary.lower() for item in evidence
    )
    assert any("Context7" in finding for finding in findings)


def test_detect_context7_libraries_uses_dependency_aliases(tmp_path):
    repo_scout = _load_repo_scout_module()
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "@tanstack/react-query": "^5.0.0",
                    "next": "^15.0.0",
                }
            }
        ),
        encoding="utf-8",
    )

    libraries = repo_scout.detect_context7_libraries(
        request_text="Investigate stale query cache behavior",
        requested_outcome="Need current app router and query docs",
        workspace_root=tmp_path,
    )

    assert ("tanstack-query", "tanstack query") in libraries
    assert ("nextjs", "nextjs") in libraries


def test_collect_text_evidence_biases_entrypoints_and_tests(tmp_path):
    repo_scout = _load_repo_scout_module()

    src_dir = tmp_path / "src"
    tests_dir = tmp_path / "tests"
    docs_dir = tmp_path / "docs"
    src_dir.mkdir()
    tests_dir.mkdir()
    docs_dir.mkdir()

    (src_dir / "router.py").write_text(
        "def auth_router():\n    return 'router'\n",
        encoding="utf-8",
    )
    (tests_dir / "test_router.py").write_text(
        "def test_auth_router():\n    assert True\n",
        encoding="utf-8",
    )
    (docs_dir / "router.md").write_text(
        "router auth overview\n",
        encoding="utf-8",
    )

    evidence, notes, provider = repo_scout.collect_text_evidence(
        workspace_root=tmp_path,
        tokens=["router", "auth"],
        localization_targets=[],
    )

    assert provider.success is True
    assert evidence[0].path == "src/router.py"
    assert any(item.path == "tests/test_router.py" for item in evidence)
    assert any("entrypoint-like path" in item.summary for item in evidence)
    assert any("test coverage candidate" in item.summary for item in evidence)
    assert notes


def test_run_repo_scout_merges_context7_and_serena(monkeypatch, tmp_path):
    repo_scout = _load_repo_scout_module()

    monkeypatch.setattr(
        repo_scout,
        "collect_serena_evidence",
        lambda **kwargs: (
            [
                repo_scout.EvidenceItem(
                    source_provider="serena",
                    path="src/main.py",
                    line_start=10,
                    line_end=20,
                    symbol="main",
                    summary="Serena symbol match",
                )
            ],
            ["serena note"],
            repo_scout.ProviderUsage("serena", True, True, "serena ok"),
        ),
    )
    monkeypatch.setattr(
        repo_scout,
        "collect_text_evidence",
        lambda **kwargs: (
            [
                repo_scout.EvidenceItem(
                    source_provider="text_search",
                    path="tests/test_main.py",
                    line_start=1,
                    line_end=1,
                    symbol=None,
                    summary="test candidate",
                )
            ],
            ["text note"],
            repo_scout.ProviderUsage("text_search", True, True, "text ok"),
        ),
    )
    monkeypatch.setattr(
        repo_scout,
        "collect_context7_evidence",
        lambda **kwargs: (
            [
                repo_scout.EvidenceItem(
                    source_provider="context7",
                    path="exploration/cache/context7/react/hooks.md",
                    line_start=None,
                    line_end=None,
                    symbol=None,
                    summary="Context7 docs",
                )
            ],
            ["Fetched external docs for react via Context7."],
            ["exploration/cache/context7/react/hooks.md"],
            repo_scout.ProviderUsage("context7", True, True, "context7 ok"),
        ),
    )

    result = repo_scout.run_repo_scout(
        workspace_root=tmp_path,
        request_text="Check React main entrypoint",
        requested_outcome="Summarize hooks and ownership",
        task_summary="Investigate startup flow",
        execution_shape="read_only_investigation",
        localization_targets=[],
        cache_root=tmp_path / "runtime" / "exploration" / "cache",
        cache_ref_base=tmp_path / "runtime",
    )

    assert result.completion_state == "completed_and_verified"
    assert [item.source_provider for item in result.evidence_items[:3]] == [
        "serena",
        "text_search",
        "context7",
    ]
    assert result.cache_refs == ["exploration/cache/context7/react/hooks.md"]
    assert len(result.providers) == 3
