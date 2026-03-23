from __future__ import annotations

from pathlib import Path

from lxml import etree

NS = {"p": "urn:pxml:v1"}
NS_URI = "urn:pxml:v1"


def q(tag: str) -> str:
    return f"{{{NS_URI}}}{tag}"


def _text(tree: etree._ElementTree, expr: str) -> str | None:
    values = tree.xpath(expr, namespaces=NS)
    if not values:
        return None
    first = values[0]
    if isinstance(first, etree._Element):
        text = first.text
    else:
        text = str(first)
    if text is None:
        return None
    normalized = text.strip()
    return normalized or None


def _items(tree: etree._ElementTree, expr: str) -> list[str]:
    values = tree.xpath(expr, namespaces=NS)
    output: list[str] = []
    for item in values:
        text = str(item).strip()
        if text:
            output.append(text)
    return output


def _write_task_intake(
    path: Path,
    *,
    task_id: str,
    task_type: str,
    risk_hint: str,
    request_text: str,
    requested_outcome: str,
) -> None:
    root = etree.Element(q("pxml"), nsmap={None: NS_URI})

    meta = etree.SubElement(root, q("meta"))
    etree.SubElement(meta, q("doc_id")).text = f"doc_intake_{task_id[5:]}"
    etree.SubElement(meta, q("doc_class")).text = "task_intake"
    etree.SubElement(meta, q("schema_version")).text = "1.0.0"
    etree.SubElement(meta, q("task_id")).text = task_id
    etree.SubElement(meta, q("run_id")).text = f"run_{task_id[5:]}"
    etree.SubElement(meta, q("sequence")).text = "1"
    etree.SubElement(meta, q("writer_agent")).text = "system"
    etree.SubElement(meta, q("created_at")).text = "2026-03-23T00:00:00Z"

    etree.SubElement(root, q("refs"))

    payload = etree.SubElement(root, q("payload"))
    etree.SubElement(payload, q("request_text")).text = request_text
    etree.SubElement(payload, q("task_type")).text = task_type
    etree.SubElement(payload, q("requested_outcome")).text = requested_outcome
    constraints = etree.SubElement(payload, q("constraints"))
    etree.SubElement(constraints, q("item")).text = "keep-runtime-deterministic"
    etree.SubElement(payload, q("risk_hint")).text = risk_hint
    etree.SubElement(payload, q("acceptance_hint")).text = "pytest generated intake"

    integrity = etree.SubElement(root, q("integrity"))
    etree.SubElement(integrity, q("content_sha256")).text = "0" * 64

    path.parent.mkdir(parents=True, exist_ok=True)
    etree.ElementTree(root).write(
        str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True
    )


def _run_packet_builder(
    run_python,
    *,
    intake_path: Path,
    runtime_root: Path,
) -> None:
    result = run_python(
        "scripts/packet_builder.py",
        "--intake",
        intake_path,
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _latest(runtime_root: Path, task_id: str, suffix: str) -> Path:
    return runtime_root / "latest" / f"{task_id}_{suffix}.pxml"


def test_mode_routing_distinguishes_meta_planning_and_task_planning(
    tmp_path: Path,
    run_python,
) -> None:
    meta_task_id = "task_mode_meta_001"
    task_task_id = "task_mode_task_001"

    meta_intake = tmp_path / "intake" / f"{meta_task_id}.pxml"
    task_intake = tmp_path / "intake" / f"{task_task_id}.pxml"

    _write_task_intake(
        meta_intake,
        task_id=meta_task_id,
        task_type="docs",
        risk_hint="low",
        request_text="Revise planner policy and routing policy behavior.",
        requested_outcome="Produce design artifact for planner hardening prompt.",
    )
    _write_task_intake(
        task_intake,
        task_id=task_task_id,
        task_type="feature",
        risk_hint="low",
        request_text="Implement a bounded helper in one module.",
        requested_outcome="Deliver a small local feature update.",
    )

    meta_runtime = tmp_path / "runtime" / "meta"
    task_runtime = tmp_path / "runtime" / "task"
    _run_packet_builder(run_python, intake_path=meta_intake, runtime_root=meta_runtime)
    _run_packet_builder(run_python, intake_path=task_intake, runtime_root=task_runtime)

    meta_route = etree.parse(str(_latest(meta_runtime, meta_task_id, "manager_route")))
    meta_packet = etree.parse(
        str(_latest(meta_runtime, meta_task_id, "execution_packet"))
    )
    assert _text(meta_route, "/p:pxml/p:payload/p:planning_mode") == "meta_planning"
    assert (
        _text(meta_route, "/p:pxml/p:payload/p:execution_shape")
        == "read_only_design_artifact"
    )
    assert _text(meta_packet, "/p:pxml/p:payload/p:write_intent") == "false"

    task_route = etree.parse(str(_latest(task_runtime, task_task_id, "manager_route")))
    assert _text(task_route, "/p:pxml/p:payload/p:planning_mode") == "task_planning"
    assert (
        _text(task_route, "/p:pxml/p:payload/p:execution_shape")
        == "direct_single_packet"
    )


def test_bounded_tasks_default_to_direct_single_packet(
    tmp_path: Path, run_python
) -> None:
    scenarios = [
        (
            "task_small_bounded_001",
            "feature",
            "Implement one small helper function in a bounded file.",
            "Add a local feature without touching shared interfaces.",
        ),
        (
            "task_medium_bounded_001",
            "feature",
            "Update several bounded helpers in one subsystem without interface changes.",
            "Deliver a medium bounded implementation in one packet.",
        ),
    ]

    for task_id, task_type, request_text, outcome in scenarios:
        intake = tmp_path / "intake" / f"{task_id}.pxml"
        runtime_root = tmp_path / "runtime" / task_id
        _write_task_intake(
            intake,
            task_id=task_id,
            task_type=task_type,
            risk_hint="medium",
            request_text=request_text,
            requested_outcome=outcome,
        )
        _run_packet_builder(run_python, intake_path=intake, runtime_root=runtime_root)

        route_tree = etree.parse(str(_latest(runtime_root, task_id, "manager_route")))
        assert (
            _text(route_tree, "/p:pxml/p:payload/p:execution_shape")
            == "direct_single_packet"
        )
        assert _text(route_tree, "/p:pxml/p:payload/p:selected_path") == "direct"


def test_large_shared_interface_refactor_uses_serial_packet_chain(
    tmp_path: Path,
    run_python,
) -> None:
    task_id = "task_large_refactor_shape_001"
    intake = tmp_path / "intake" / f"{task_id}.pxml"
    runtime_root = tmp_path / "runtime"
    _write_task_intake(
        intake,
        task_id=task_id,
        task_type="refactor",
        risk_hint="medium",
        request_text="Refactor shared interface across modules with many call sites.",
        requested_outcome="Migrate public contract safely.",
    )

    _run_packet_builder(run_python, intake_path=intake, runtime_root=runtime_root)
    route_tree = etree.parse(str(_latest(runtime_root, task_id, "manager_route")))
    assert (
        _text(route_tree, "/p:pxml/p:payload/p:execution_shape")
        == "serial_packet_chain"
    )


def test_research_and_design_only_requests_use_read_only_shapes(
    tmp_path: Path,
    run_python,
) -> None:
    research_task_id = "task_research_only_shape_001"
    design_task_id = "task_design_only_shape_001"

    research_intake = tmp_path / "intake" / f"{research_task_id}.pxml"
    design_intake = tmp_path / "intake" / f"{design_task_id}.pxml"
    _write_task_intake(
        research_intake,
        task_id=research_task_id,
        task_type="docs",
        risk_hint="low",
        request_text="Read-only exploration only; do not modify files.",
        requested_outcome="Investigate and summarize impact.",
    )
    _write_task_intake(
        design_intake,
        task_id=design_task_id,
        task_type="docs",
        risk_hint="low",
        request_text="Design only proposal for architecture routing.",
        requested_outcome="Deliver design artifact only.",
    )

    research_runtime = tmp_path / "runtime" / "research"
    design_runtime = tmp_path / "runtime" / "design"
    _run_packet_builder(
        run_python,
        intake_path=research_intake,
        runtime_root=research_runtime,
    )
    _run_packet_builder(
        run_python,
        intake_path=design_intake,
        runtime_root=design_runtime,
    )

    research_route = etree.parse(
        str(_latest(research_runtime, research_task_id, "manager_route"))
    )
    design_route = etree.parse(
        str(_latest(design_runtime, design_task_id, "manager_route"))
    )
    assert (
        _text(research_route, "/p:pxml/p:payload/p:execution_shape")
        == "read_only_investigation"
    )
    assert (
        _text(design_route, "/p:pxml/p:payload/p:execution_shape")
        == "read_only_design_artifact"
    )


def test_behavior_changing_bugfix_with_structural_only_proof_is_not_completed_verified(
    tmp_path: Path,
    run_python,
) -> None:
    task_id = "task_behavior_proof_cap_001"
    intake_path = tmp_path / "intake" / f"{task_id}.pxml"
    runtime_root = tmp_path / "runtime"
    workspace_root = tmp_path / "workspace"

    target_file = workspace_root / "src" / "target_bugfix.py"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("def target_function():\n    return 1\n", encoding="utf-8")

    _write_task_intake(
        intake_path,
        task_id=task_id,
        task_type="bugfix",
        risk_hint="medium",
        request_text="Fix runtime interaction bug symptom in state handling.",
        requested_outcome="Remove user-visible bug symptom with verification evidence.",
    )

    result = run_python(
        "scripts/task_executor.py",
        "--intake",
        intake_path,
        "--runtime-root",
        runtime_root,
        "--workspace-root",
        workspace_root,
        "--verify-policy",
        "always",
        "--skip-validate",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    status_tree = etree.parse(str(_latest(runtime_root, task_id, "task_status_report")))
    assert (
        _text(status_tree, "/p:pxml/p:payload/p:completion_state")
        == "implemented_but_unverified"
    )
    assert _text(status_tree, "/p:pxml/p:payload/p:proof_status/p:structural") == "PASS"
    assert (
        _text(status_tree, "/p:pxml/p:payload/p:proof_status/p:behavioral") == "NOT-RUN"
    )
    assert (
        _text(status_tree, "/p:pxml/p:payload/p:proof_status/p:regression") == "NOT-RUN"
    )


def test_requirement_status_matrix_supports_pass_fail_and_not_run(
    tmp_path: Path,
    run_python,
) -> None:
    # PASS: read-only design task
    pass_task = "task_matrix_pass_001"
    pass_intake = tmp_path / "intake" / f"{pass_task}.pxml"
    pass_runtime = tmp_path / "runtime" / "pass"
    _write_task_intake(
        pass_intake,
        task_id=pass_task,
        task_type="docs",
        risk_hint="low",
        request_text="Planner policy design only update.",
        requested_outcome="Design artifact only.",
    )
    pass_result = run_python(
        "scripts/task_executor.py",
        "--intake",
        pass_intake,
        "--runtime-root",
        pass_runtime,
        "--skip-validate",
    )
    assert pass_result.returncode == 0, pass_result.stdout + pass_result.stderr
    pass_tree = etree.parse(str(_latest(pass_runtime, pass_task, "task_status_report")))
    pass_statuses = _items(
        pass_tree,
        "/p:pxml/p:payload/p:requirement_status_matrix/p:requirement/p:status/text()",
    )
    assert "PASS" in pass_statuses

    # FAIL: blocked bugfix due missing modify target
    fail_task = "task_matrix_fail_001"
    fail_intake = tmp_path / "intake" / f"{fail_task}.pxml"
    fail_runtime = tmp_path / "runtime" / "fail"
    _write_task_intake(
        fail_intake,
        task_id=fail_task,
        task_type="bugfix",
        risk_hint="medium",
        request_text="Fix state synchronization bug.",
        requested_outcome="Apply bugfix patch with behavior correction.",
    )
    fail_workspace = tmp_path / "workspace" / "fail"
    fail_workspace.mkdir(parents=True, exist_ok=True)
    fail_result = run_python(
        "scripts/task_executor.py",
        "--intake",
        fail_intake,
        "--runtime-root",
        fail_runtime,
        "--workspace-root",
        fail_workspace,
        "--skip-validate",
    )
    assert fail_result.returncode == 1, fail_result.stdout + fail_result.stderr
    fail_tree = etree.parse(str(_latest(fail_runtime, fail_task, "task_status_report")))
    fail_statuses = _items(
        fail_tree,
        "/p:pxml/p:payload/p:requirement_status_matrix/p:requirement/p:status/text()",
    )
    assert "FAIL" in fail_statuses

    # NOT-RUN: behavior-changing bugfix with only structural proof
    not_run_task = "task_matrix_not_run_001"
    not_run_intake = tmp_path / "intake" / f"{not_run_task}.pxml"
    not_run_runtime = tmp_path / "runtime" / "not_run"
    not_run_workspace = tmp_path / "workspace" / "not_run"
    target_file = not_run_workspace / "src" / "target_bugfix.py"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("def target_function():\n    return 1\n", encoding="utf-8")
    _write_task_intake(
        not_run_intake,
        task_id=not_run_task,
        task_type="bugfix",
        risk_hint="medium",
        request_text="Fix runtime interaction bug symptom.",
        requested_outcome="Remove bug symptom and keep behavior stable.",
    )
    not_run_result = run_python(
        "scripts/task_executor.py",
        "--intake",
        not_run_intake,
        "--runtime-root",
        not_run_runtime,
        "--workspace-root",
        not_run_workspace,
        "--verify-policy",
        "always",
        "--skip-validate",
    )
    assert not_run_result.returncode == 0, not_run_result.stdout + not_run_result.stderr
    not_run_tree = etree.parse(
        str(_latest(not_run_runtime, not_run_task, "task_status_report"))
    )
    not_run_statuses = _items(
        not_run_tree,
        "/p:pxml/p:payload/p:requirement_status_matrix/p:requirement/p:status/text()",
    )
    assert "NOT-RUN" in not_run_statuses


def test_repeated_symptom_bug_biases_observation_first(
    tmp_path: Path, run_python
) -> None:
    task_id = "task_observation_bias_001"
    intake = tmp_path / "intake" / f"{task_id}.pxml"
    runtime_root = tmp_path / "runtime"
    _write_task_intake(
        intake,
        task_id=task_id,
        task_type="bugfix",
        risk_hint="medium",
        request_text="Recurring async interaction race bug still happening again.",
        requested_outcome="Fix state bug and stop guess-and-patch retries.",
    )

    _run_packet_builder(run_python, intake_path=intake, runtime_root=runtime_root)
    packet_tree = etree.parse(str(_latest(runtime_root, task_id, "execution_packet")))

    assert (
        _text(packet_tree, "/p:pxml/p:payload/p:execution_shape")
        == "serial_packet_chain"
    )
    guidance = _items(packet_tree, "/p:pxml/p:payload/p:test_guidance/p:item/text()")
    assert any("observation-first" in item.lower() for item in guidance)

    behavioral_methods = _items(
        packet_tree,
        (
            "/p:pxml/p:payload/p:proof_requirements/p:proof"
            "[p:proof_category='behavioral']/p:proof_method/text()"
        ),
    )
    assert behavioral_methods
    assert any("observation-first" in item.lower() for item in behavioral_methods)
