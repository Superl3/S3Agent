from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _load_bootstrap():
    repo_root = Path(__file__).resolve().parents[1]
    scripts_dir = repo_root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from runtime_bootstrap import bootstrap_runtime  # type: ignore

    return bootstrap_runtime


def test_runtime_bootstrap_resolution_order_and_dir_creation(
    tmp_path: Path,
) -> None:
    bootstrap_runtime = _load_bootstrap()

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    cli_root = tmp_path / "runtime_cli"
    env_root = tmp_path / "runtime_env"

    cli_ready = bootstrap_runtime(
        cli_runtime_root=cli_root,
        workspace_root=workspace_root,
        env={"OPENCODE_RUNTIME_ROOT": str(env_root)},
    )
    assert cli_ready.ready
    assert cli_ready.source == "cli"
    assert cli_ready.runtime_root == cli_root.resolve()

    env_ready = bootstrap_runtime(
        cli_runtime_root=None,
        workspace_root=workspace_root,
        env={"OPENCODE_RUNTIME_ROOT": str(env_root)},
    )
    assert env_ready.ready
    assert env_ready.source == "env"
    assert env_ready.runtime_root == env_root.resolve()

    workspace_ready = bootstrap_runtime(
        cli_runtime_root=None,
        workspace_root=workspace_root,
        env={},
    )
    assert workspace_ready.ready
    assert workspace_ready.source == "workspace"
    assert workspace_ready.runtime_root == (workspace_root / "runtime").resolve()

    required = [
        workspace_ready.runtime_root,
        workspace_ready.runtime_root / "traces" / "by_task",
        workspace_ready.runtime_root / "packets",
        workspace_ready.runtime_root / "latest",
        workspace_ready.runtime_root / "index" / "tasks",
    ]
    for path in required:
        assert path.exists() and path.is_dir()


def test_trace_appender_uses_env_runtime_root_without_cli(
    tmp_path: Path,
    repo_root: Path,
) -> None:
    runtime_root = tmp_path / "runtime_from_env"
    env = os.environ.copy()
    env["OPENCODE_RUNTIME_ROOT"] = str(runtime_root)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/trace_appender.py",
            "--task-id",
            "task_runtime_bootstrap_env_001",
            "--event-type",
            "route",
            "--message",
            "bootstrap env test",
            "--skip-validate",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[trace_appender] runtime bootstrap ready" in result.stdout
    assert "source=env" in result.stdout

    trace_path = (
        runtime_root / "traces" / "by_task" / "task_runtime_bootstrap_env_001.pxml"
    )
    assert trace_path.exists()
