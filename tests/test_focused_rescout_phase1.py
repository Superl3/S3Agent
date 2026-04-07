from __future__ import annotations

from pathlib import Path

from lxml import etree

NS = {"p": "urn:pxml:v1"}
NS_URI = "urn:pxml:v1"


def q(tag: str) -> str:
    return f"{{{NS_URI}}}{tag}"


def _write_task_intake(
    path: Path, task_id: str, request_text: str, outcome: str, task_type: str = "docs"
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<pxml xmlns=\"urn:pxml:v1\">
  <meta>
    <doc_id>doc_task_intake_{task_id[5:]}</doc_id>
    <doc_class>task_intake</doc_class>
    <schema_version>1.0.0</schema_version>
    <task_id>{task_id}</task_id>
    <run_id>run_{task_id[5:]}</run_id>
    <sequence>1</sequence>
    <writer_agent>manager</writer_agent>
    <created_at>2026-03-23T00:00:00Z</created_at>
  </meta>
  <payload>
    <request_text>{request_text}</request_text>
    <task_type>{task_type}</task_type>
    <requested_outcome>{outcome}</requested_outcome>
    <constraints>
      <item>keep-runtime-safe</item>
    </constraints>
    <risk_hint>low</risk_hint>
  </payload>
  <integrity>
    <content_sha256>0000000000000000000000000000000000000000000000000000000000000000</content_sha256>
  </integrity>
</pxml>
"""
    path.write_text(content, encoding="utf-8")


def _text(tree: etree._ElementTree, expr: str) -> str | None:
    values = tree.xpath(expr, namespaces=NS)
    if not values:
        return None
    value = values[0]
    if isinstance(value, etree._Element):
        text = value.text
    else:
        text = str(value)
    if text is None:
        return None
    normalized = text.strip()
    return normalized or None


def _items(tree: etree._ElementTree, expr: str) -> list[str]:
    values = tree.xpath(expr, namespaces=NS)
    result: list[str] = []
    for value in values:
        if isinstance(value, etree._Element):
            text = value.text
        else:
            text = str(value)
        if text is None:
            continue
        normalized = text.strip()
        if normalized:
            result.append(normalized)
    return result


def _latest(runtime_root: Path, task_id: str, suffix: str) -> Path:
    return runtime_root / "latest" / f"{task_id}_{suffix}.pxml"


def _build_packet_and_baseline(
    tmp_path: Path,
    run_python,
    task_id: str,
) -> tuple[Path, Path, Path]:
    runtime_root = tmp_path / "runtime"
    workspace_root = tmp_path / "workspace"
    intake_path = tmp_path / "intake" / f"{task_id}.pxml"
    (workspace_root / "src").mkdir(parents=True, exist_ok=True)
    (workspace_root / "src" / "router.py").write_text(
        "def auth_router():\n    return 'ok'\n",
        encoding="utf-8",
    )
    _write_task_intake(
        intake_path,
        task_id,
        request_text="read-only exploration only; do not modify files",
        outcome="investigate auth routing without edits",
    )
    packet_result = run_python(
        "scripts/packet_builder.py",
        "--intake",
        intake_path,
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert packet_result.returncode == 0, packet_result.stdout + packet_result.stderr
    packet_path = _latest(runtime_root, task_id, "execution_packet")
    baseline_result = run_python(
        "scripts/explorer_runner.py",
        "--packet",
        packet_path,
        "--runtime-root",
        runtime_root,
        "--workspace-root",
        workspace_root,
        "--skip-validate",
    )
    assert baseline_result.returncode == 0, (
        baseline_result.stdout + baseline_result.stderr
    )
    baseline_path = _latest(runtime_root, task_id, "exploration_result")
    return runtime_root, workspace_root, baseline_path


def _write_exploration_request(
    path: Path,
    *,
    task_id: str,
    packet_doc_id: str,
    baseline_doc_id: str | None,
    requester_agent: str,
    target_hints: list[str],
) -> None:
    root = etree.Element(q("pxml"), nsmap={None: NS_URI})
    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = "doc_exploration_request_manual_0001"
    etree.SubElement(meta, q("doc_class")).text = "exploration_request"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = task_id
    etree.SubElement(meta, q("run_id")).text = f"run_{task_id[5:]}"
    etree.SubElement(meta, q("sequence")).text = "4"
    etree.SubElement(meta, q("writer_agent")).text = "manager"
    etree.SubElement(meta, q("created_at")).text = "2026-03-23T00:00:04Z"

    refs = etree.SubElement(root, q("refs"))
    packet_ref = etree.SubElement(refs, q("ref"))
    etree.SubElement(packet_ref, q("doc_id")).text = packet_doc_id
    etree.SubElement(packet_ref, q("doc_class")).text = "execution_packet"
    etree.SubElement(packet_ref, q("relation")).text = "request_packet"
    if baseline_doc_id is not None:
        baseline_ref = etree.SubElement(refs, q("ref"))
        etree.SubElement(baseline_ref, q("doc_id")).text = baseline_doc_id
        etree.SubElement(baseline_ref, q("doc_class")).text = "exploration_result"
        etree.SubElement(baseline_ref, q("relation")).text = "baseline_context"

    payload = etree.SubElement(root, q("payload"))
    etree.SubElement(payload, q("requester_agent")).text = requester_agent
    etree.SubElement(payload, q("request_kind")).text = "ownership_trace"
    etree.SubElement(payload, q("blocking")).text = "true"
    etree.SubElement(
        payload, q("reason_code")
    ).text = "implementer_modify_target_missing"
    questions = etree.SubElement(payload, q("focus_questions"))
    etree.SubElement(questions, q("item")).text = "Which file owns auth routing?"
    hints = etree.SubElement(payload, q("target_hints"))
    for item in target_hints:
        etree.SubElement(hints, q("item")).text = item
    etree.SubElement(payload, q("contract_change_suspected")).text = "false"

    integrity = etree.SubElement(root, q("integrity"))
    etree.SubElement(integrity, q("content_sha256")).text = "1" * 64
    etree.SubElement(integrity, q("parent_sha256")).text = "2" * 64

    etree.ElementTree(root).write(
        str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True
    )


def _write_manual_exploration_result(
    path: Path,
    *,
    task_id: str,
    packet_doc_id: str,
    doc_id: str,
    sequence: int,
    actionability: str,
    exploration_scope: str,
    evidence_path: str,
) -> None:
    root = etree.Element(q("pxml"), nsmap={None: NS_URI})
    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = doc_id
    etree.SubElement(meta, q("doc_class")).text = "exploration_result"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = task_id
    etree.SubElement(meta, q("run_id")).text = f"run_{task_id[5:]}"
    etree.SubElement(meta, q("sequence")).text = str(sequence)
    etree.SubElement(meta, q("writer_agent")).text = "explorer"
    etree.SubElement(meta, q("created_at")).text = f"2026-03-23T00:00:{sequence:02d}Z"

    refs = etree.SubElement(root, q("refs"))
    packet_ref = etree.SubElement(refs, q("ref"))
    etree.SubElement(packet_ref, q("doc_id")).text = packet_doc_id
    etree.SubElement(packet_ref, q("doc_class")).text = "execution_packet"
    etree.SubElement(packet_ref, q("relation")).text = "exploration_target"

    payload = etree.SubElement(root, q("payload"))
    payload_packet = etree.SubElement(payload, q("packet_ref"))
    etree.SubElement(payload_packet, q("doc_id")).text = packet_doc_id
    etree.SubElement(payload_packet, q("doc_class")).text = "execution_packet"
    etree.SubElement(payload_packet, q("relation")).text = "exploration_target"
    etree.SubElement(payload, q("task_id")).text = task_id
    etree.SubElement(payload, q("exploration_kind")).text = "investigation"
    etree.SubElement(payload, q("exploration_scope")).text = exploration_scope
    etree.SubElement(payload, q("actionability")).text = actionability
    etree.SubElement(payload, q("target_root")).text = "C:/tmp/workspace"
    providers = etree.SubElement(payload, q("providers"))
    provider = etree.SubElement(providers, q("provider"))
    etree.SubElement(provider, q("name")).text = "text_search"
    etree.SubElement(provider, q("used")).text = "true"
    etree.SubElement(provider, q("success")).text = "true"
    etree.SubElement(provider, q("notes")).text = "manual"
    focus_questions = etree.SubElement(payload, q("focus_questions"))
    etree.SubElement(focus_questions, q("item")).text = "Which file owns auth routing?"
    findings = etree.SubElement(payload, q("key_findings"))
    etree.SubElement(findings, q("item")).text = f"Relevant file: {evidence_path}"
    evidence_items = etree.SubElement(payload, q("evidence_items"))
    evidence = etree.SubElement(evidence_items, q("evidence"))
    etree.SubElement(evidence, q("source_provider")).text = "text_search"
    etree.SubElement(evidence, q("path")).text = evidence_path
    etree.SubElement(evidence, q("summary")).text = "manual evidence"
    next_actions = etree.SubElement(payload, q("recommended_next_actions"))
    etree.SubElement(next_actions, q("item")).text = "review evidence"
    etree.SubElement(payload, q("completion_state")).text = "completed_and_verified"
    etree.SubElement(payload, q("escalation_requested")).text = "false"
    notes = etree.SubElement(payload, q("notes"))
    etree.SubElement(notes, q("item")).text = "manual exploration"

    integrity = etree.SubElement(root, q("integrity"))
    etree.SubElement(integrity, q("content_sha256")).text = str(sequence) * 64
    etree.SubElement(integrity, q("parent_sha256")).text = "a" * 64
    etree.ElementTree(root).write(
        str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True
    )


def test_manager_authored_request_builder_writes_valid_request(
    tmp_path: Path, run_python
) -> None:
    task_id = "task_rescout_req_valid_001"
    runtime_root, _workspace_root, baseline_path = _build_packet_and_baseline(
        tmp_path, run_python, task_id
    )
    packet_path = _latest(runtime_root, task_id, "execution_packet")

    result = run_python(
        "scripts/exploration_request_builder.py",
        "--packet",
        packet_path,
        "--baseline-exploration",
        baseline_path,
        "--requester-agent",
        "planner",
        "--request-kind",
        "ownership_trace",
        "--reason-code",
        "planner_needs_owner_boundary",
        "--focus-question",
        "Which file owns auth routing?",
        "--target-hint",
        "src/router.py",
        "--runtime-root",
        runtime_root,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    request_path = _latest(runtime_root, task_id, "exploration_request")
    assert request_path.exists()
    request_tree = etree.parse(str(request_path))
    assert _text(request_tree, "/p:pxml/p:meta/p:writer_agent") == "manager"
    assert _text(request_tree, "/p:pxml/p:payload/p:requester_agent") == "planner"


def test_request_validator_rejects_missing_baseline_ref(
    tmp_path: Path, run_python
) -> None:
    task_id = "task_rescout_missing_base_001"
    runtime_root, _workspace_root, baseline_path = _build_packet_and_baseline(
        tmp_path, run_python, task_id
    )
    packet_path = _latest(runtime_root, task_id, "execution_packet")
    packet_tree = etree.parse(str(packet_path))
    packet_doc_id = _text(packet_tree, "/p:pxml/p:meta/p:doc_id")
    assert packet_doc_id is not None
    invalid_request = tmp_path / "invalid_missing_baseline.pxml"
    _write_exploration_request(
        invalid_request,
        task_id=task_id,
        packet_doc_id=packet_doc_id,
        baseline_doc_id=None,
        requester_agent="planner",
        target_hints=["src/router.py"],
    )
    result = run_python(
        "scripts/pxml_validator.py",
        invalid_request,
        "--context-dir",
        tmp_path,
    )
    assert result.returncode == 1, result.stdout + result.stderr


def test_request_validator_rejects_unsupported_requester_agent(
    tmp_path: Path, run_python
) -> None:
    task_id = "task_rescout_bad_agent_001"
    runtime_root, _workspace_root, baseline_path = _build_packet_and_baseline(
        tmp_path, run_python, task_id
    )
    packet_path = _latest(runtime_root, task_id, "execution_packet")
    packet_tree = etree.parse(str(packet_path))
    baseline_tree = etree.parse(str(baseline_path))
    packet_doc_id = _text(packet_tree, "/p:pxml/p:meta/p:doc_id")
    baseline_doc_id = _text(baseline_tree, "/p:pxml/p:meta/p:doc_id")
    assert packet_doc_id is not None and baseline_doc_id is not None
    invalid_request = tmp_path / "invalid_requester_agent.pxml"
    _write_exploration_request(
        invalid_request,
        task_id=task_id,
        packet_doc_id=packet_doc_id,
        baseline_doc_id=baseline_doc_id,
        requester_agent="reviewer",
        target_hints=["src/router.py"],
    )
    result = run_python(
        "scripts/pxml_validator.py",
        invalid_request,
        "--context-dir",
        tmp_path,
    )
    assert result.returncode == 1, result.stdout + result.stderr


def test_request_builder_rejects_non_concrete_target_hint(
    tmp_path: Path, run_python
) -> None:
    task_id = "task_rescout_hint_guard_001"
    runtime_root, _workspace_root, baseline_path = _build_packet_and_baseline(
        tmp_path, run_python, task_id
    )
    packet_path = _latest(runtime_root, task_id, "execution_packet")
    result = run_python(
        "scripts/exploration_request_builder.py",
        "--packet",
        packet_path,
        "--baseline-exploration",
        baseline_path,
        "--requester-agent",
        "planner",
        "--request-kind",
        "ownership_trace",
        "--reason-code",
        "planner_needs_owner_boundary",
        "--focus-question",
        "Which file owns auth routing?",
        "--target-hint",
        "unclear area",
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert result.returncode == 1, result.stdout + result.stderr


def test_request_builder_rejects_duplicate_active_request(
    tmp_path: Path, run_python
) -> None:
    task_id = "task_rescout_duplicate_001"
    runtime_root, _workspace_root, baseline_path = _build_packet_and_baseline(
        tmp_path, run_python, task_id
    )
    packet_path = _latest(runtime_root, task_id, "execution_packet")
    args = [
        "scripts/exploration_request_builder.py",
        "--packet",
        packet_path,
        "--baseline-exploration",
        baseline_path,
        "--requester-agent",
        "planner",
        "--request-kind",
        "ownership_trace",
        "--reason-code",
        "planner_needs_owner_boundary",
        "--focus-question",
        "Which file owns auth routing?",
        "--target-hint",
        "src/router.py",
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    ]
    first = run_python(*args)
    assert first.returncode == 0, first.stdout + first.stderr
    second = run_python(*args)
    assert second.returncode == 1, second.stdout + second.stderr


def test_request_builder_issues_distinct_doc_ids_for_distinct_focuses(
    tmp_path: Path, run_python
) -> None:
    task_id = "task_rescout_distinct_docids_001"
    runtime_root, _workspace_root, baseline_path = _build_packet_and_baseline(
        tmp_path, run_python, task_id
    )
    packet_path = _latest(runtime_root, task_id, "execution_packet")

    first = run_python(
        "scripts/exploration_request_builder.py",
        "--packet",
        packet_path,
        "--baseline-exploration",
        baseline_path,
        "--requester-agent",
        "planner",
        "--request-kind",
        "ownership_trace",
        "--reason-code",
        "planner_needs_owner_boundary",
        "--focus-question",
        "Which file owns auth routing?",
        "--target-hint",
        "src/router.py",
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert first.returncode == 0, first.stdout + first.stderr

    second = run_python(
        "scripts/exploration_request_builder.py",
        "--packet",
        packet_path,
        "--baseline-exploration",
        baseline_path,
        "--requester-agent",
        "planner",
        "--request-kind",
        "test_discovery",
        "--reason-code",
        "planner_needs_owner_boundary",
        "--focus-question",
        "Which tests cover auth routing?",
        "--target-hint",
        "tests/test_router.py",
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert second.returncode == 0, second.stdout + second.stderr

    request_paths = sorted((runtime_root / "exploration" / "requests").glob("*.pxml"))
    assert len(request_paths) == 2
    request_doc_ids = {
        _text(etree.parse(str(path)), "/p:pxml/p:meta/p:doc_id")
        for path in request_paths
    }
    assert len(request_doc_ids) == 2


def test_explorer_runner_request_mode_writes_focused_result(
    tmp_path: Path, run_python
) -> None:
    task_id = "task_rescout_focus_result_001"
    runtime_root, workspace_root, baseline_path = _build_packet_and_baseline(
        tmp_path, run_python, task_id
    )
    packet_path = _latest(runtime_root, task_id, "execution_packet")
    request_result = run_python(
        "scripts/exploration_request_builder.py",
        "--packet",
        packet_path,
        "--baseline-exploration",
        baseline_path,
        "--requester-agent",
        "planner",
        "--request-kind",
        "ownership_trace",
        "--reason-code",
        "planner_needs_owner_boundary",
        "--focus-question",
        "Which file owns auth routing?",
        "--target-hint",
        "src/router.py",
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert request_result.returncode == 0, request_result.stdout + request_result.stderr
    request_path = _latest(runtime_root, task_id, "exploration_request")

    focused = run_python(
        "scripts/explorer_runner.py",
        "--request",
        request_path,
        "--runtime-root",
        runtime_root,
        "--workspace-root",
        workspace_root,
        "--skip-validate",
    )
    assert focused.returncode == 0, focused.stdout + focused.stderr
    result_path = _latest(runtime_root, task_id, "exploration_result")
    tree = etree.parse(str(result_path))
    assert _text(tree, "/p:pxml/p:payload/p:exploration_scope") == "focused_refresh"
    assert _text(tree, "/p:pxml/p:payload/p:actionability") in {
        "advisory_only",
        "manager_reusable",
        "contract_refresh_required",
    }
    relations = _items(tree, "/p:pxml/p:refs/p:ref/p:relation/text()")
    assert "request" in relations
    assert "parent_exploration" in relations


def test_packet_builder_ignores_advisory_only_focused_result_for_reuse(
    tmp_path: Path, run_python
) -> None:
    task_id = "task_rescout_reuse_filter_001"
    runtime_root, _workspace_root, baseline_path = _build_packet_and_baseline(
        tmp_path, run_python, task_id
    )
    packet_path = _latest(runtime_root, task_id, "execution_packet")
    packet_tree = etree.parse(str(packet_path))
    packet_doc_id = _text(packet_tree, "/p:pxml/p:meta/p:doc_id")
    baseline_tree = etree.parse(str(baseline_path))
    baseline_doc_id = _text(baseline_tree, "/p:pxml/p:meta/p:doc_id")
    assert packet_doc_id is not None and baseline_doc_id is not None

    manual_advisory = (
        runtime_root
        / "exploration"
        / "results"
        / "doc_exploration_result_advisory_0005.pxml"
    )
    _write_manual_exploration_result(
        manual_advisory,
        task_id=task_id,
        packet_doc_id=packet_doc_id,
        doc_id="doc_exploration_result_advisory_0005",
        sequence=5,
        actionability="advisory_only",
        exploration_scope="focused_refresh",
        evidence_path="docs/advisory.md",
    )
    shutil_target = _latest(runtime_root, task_id, "exploration_result")
    shutil_target.write_text(
        manual_advisory.read_text(encoding="utf-8"), encoding="utf-8"
    )

    intake_path = tmp_path / "intake_rerun" / f"{task_id}.pxml"
    _write_task_intake(
        intake_path,
        task_id,
        request_text="read-only exploration only; do not modify files",
        outcome="investigate auth routing without edits",
    )
    rerun = run_python(
        "scripts/packet_builder.py",
        "--intake",
        intake_path,
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr

    rerun_packet = etree.parse(str(_latest(runtime_root, task_id, "execution_packet")))
    assert (
        _text(rerun_packet, "/p:pxml/p:payload/p:exploration_notes_ref/p:doc_id")
        == baseline_doc_id
    )
