from __future__ import annotations

from pathlib import Path

from lxml import etree

NS = {"p": "urn:pxml:v1"}


def _write_task_intake(
    path: Path, task_id: str, request_text: str, outcome: str
) -> None:
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
    <task_type>docs</task_type>
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


def _packet_write_intent(packet_path: Path) -> str | None:
    tree = etree.parse(str(packet_path))
    values = tree.xpath("/p:pxml/p:payload/p:write_intent/text()", namespaces=NS)
    if not values:
        return None
    return str(values[0]).strip()


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


def test_packet_builder_marks_exploration_intake_as_read_only(
    tmp_path: Path,
    run_python,
) -> None:
    runtime_root = tmp_path / "runtime"
    intake_path = tmp_path / "intake_explore.pxml"
    task_id = "task_explorer_gate_packet_001"
    _write_task_intake(
        intake_path,
        task_id,
        request_text="코드 수정 없이 탐색만 수행해서 영향 범위 조사",
        outcome="analysis only report",
    )

    result = run_python(
        "scripts/packet_builder.py",
        "--intake",
        intake_path,
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    packet_path = runtime_root / "latest" / f"{task_id}_execution_packet.pxml"
    assert packet_path.exists()
    assert _packet_write_intent(packet_path) == "false"


def test_task_executor_skips_implementer_when_write_intent_is_false(
    tmp_path: Path,
    run_python,
) -> None:
    runtime_root = tmp_path / "runtime"
    intake_path = tmp_path / "intake_task_executor_explore.pxml"
    task_id = "task_explorer_gate_executor_001"
    _write_task_intake(
        intake_path,
        task_id,
        request_text="read-only exploration only; do not modify files",
        outcome="investigate and summarize",
    )

    result = run_python(
        "scripts/task_executor.py",
        "--intake",
        intake_path,
        "--runtime-root",
        runtime_root,
        "--skip-validate",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "stage=explorer_runner" in result.stdout

    latest_impl = runtime_root / "latest" / f"{task_id}_implementer_result.pxml"
    latest_exploration = runtime_root / "latest" / f"{task_id}_exploration_result.pxml"
    assert not latest_impl.exists()
    assert latest_exploration.exists()

    exploration_tree = etree.parse(str(latest_exploration))
    assert (
        _text(exploration_tree, "/p:pxml/p:payload/p:completion_state")
        == "completed_and_verified"
    )


def test_implementer_rejects_packet_with_disabled_write_intent(
    tmp_path: Path,
    run_python,
) -> None:
    runtime_root = tmp_path / "runtime"
    intake_path = tmp_path / "intake_impl_blocked.pxml"
    task_id = "task_explorer_gate_impl_001"
    _write_task_intake(
        intake_path,
        task_id,
        request_text="exploration only and no code changes",
        outcome="research summary",
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

    packet_path = runtime_root / "latest" / f"{task_id}_execution_packet.pxml"
    assert packet_path.exists()

    impl_result = run_python(
        "scripts/implementer_runner.py",
        "--packet",
        packet_path,
        "--runtime-root",
        runtime_root,
        "--workspace-root",
        tmp_path,
        "--skip-validate",
    )
    assert impl_result.returncode == 1, impl_result.stdout + impl_result.stderr
    assert "blocked_reason=implementer_write_intent_disabled" in impl_result.stdout


def test_task_executor_read_only_flow_validates_exploration_result(
    tmp_path: Path,
    run_python,
) -> None:
    runtime_root = tmp_path / "runtime_validated"
    intake_path = tmp_path / "intake_validated.pxml"
    task_id = "task_explorer_validated_001"

    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "module.py").write_text(
        "def investigate_me():\n    return 'ok'\n",
        encoding="utf-8",
    )

    _write_task_intake(
        intake_path,
        task_id,
        request_text="Read-only exploration only; investigate module ownership without edits",
        outcome="Summarize likely touch points",
    )

    result = run_python(
        "scripts/task_executor.py",
        "--intake",
        intake_path,
        "--runtime-root",
        runtime_root,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    latest_exploration = runtime_root / "latest" / f"{task_id}_exploration_result.pxml"
    latest_status = runtime_root / "latest" / f"{task_id}_task_status_report.pxml"
    assert latest_exploration.exists()
    assert latest_status.exists()

    status_tree = etree.parse(str(latest_status))
    assert _text(status_tree, "/p:pxml/p:payload/p:current_status") == "passed"
    assert (
        _text(
            status_tree, "/p:pxml/p:payload/p:latest_exploration_result_ref/p:doc_class"
        )
        == "exploration_result"
    )
