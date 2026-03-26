#!/usr/bin/env python3
"""Shared runtime root/bootstrap readiness helper."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional, Sequence

DEFAULT_REQUIRED_SUBDIRS: tuple[str, ...] = (
    "traces/by_task",
    "packets",
    "latest",
    "index/tasks",
)


@dataclass(frozen=True)
class RuntimeBootstrapResult:
    ready: bool
    runtime_root: Path
    source: str
    created_dirs: List[Path]
    diagnostics: List[str]
    errors: List[str]

    def success_line(self, script_name: str) -> str:
        return (
            f"[{script_name}] runtime bootstrap ready "
            f"root={self.runtime_root} source={self.source}"
        )

    def failure_line(self) -> str:
        reason = "; ".join(self.errors) if self.errors else "unknown runtime error"
        return (
            f"runtime bootstrap failed: runtime_root={self.runtime_root} "
            f"source={self.source}; reason={reason}"
        )


def _resolve_runtime_root(
    cli_runtime_root: Optional[Path],
    workspace_root: Optional[Path],
    env: Mapping[str, str],
) -> tuple[Path, str]:
    if cli_runtime_root is not None:
        return cli_runtime_root.expanduser().resolve(strict=False), "cli"

    env_value = env.get("OPENCODE_RUNTIME_ROOT", "").strip()
    if env_value:
        return Path(env_value).expanduser().resolve(strict=False), "env"

    if workspace_root is not None:
        return (workspace_root / "runtime").expanduser().resolve(strict=False), "workspace"

    return (Path.cwd() / "runtime").expanduser().resolve(strict=False), "cwd"


def _nearest_existing_parent(path: Path) -> Path:
    current = path
    while not current.exists():
        if current.parent == current:
            break
        current = current.parent
    return current


def _probe_write(path: Path) -> Optional[str]:
    marker = path / f".opencode_write_probe_{uuid.uuid4().hex}.tmp"
    try:
        marker.write_text("probe", encoding="utf-8")
    except OSError as exc:
        return f"not writable: {path} ({exc})"
    try:
        marker.unlink()
    except OSError:
        pass
    return None


def bootstrap_runtime(
    cli_runtime_root: Optional[Path],
    workspace_root: Optional[Path] = None,
    required_subdirs: Sequence[str] = DEFAULT_REQUIRED_SUBDIRS,
    env: Optional[Mapping[str, str]] = None,
) -> RuntimeBootstrapResult:
    env_map: Mapping[str, str] = os.environ if env is None else env
    runtime_root, source = _resolve_runtime_root(
        cli_runtime_root=cli_runtime_root,
        workspace_root=workspace_root,
        env=env_map,
    )

    diagnostics: List[str] = [f"resolved runtime root via {source}: {runtime_root}"]
    errors: List[str] = []
    created_dirs: List[Path] = []

    if runtime_root.exists() and not runtime_root.is_dir():
        errors.append(f"runtime root path exists and is not a directory: {runtime_root}")
        return RuntimeBootstrapResult(
            ready=False,
            runtime_root=runtime_root,
            source=source,
            created_dirs=created_dirs,
            diagnostics=diagnostics,
            errors=errors,
        )

    nearest = _nearest_existing_parent(runtime_root)
    parent_probe_error = _probe_write(nearest)
    if parent_probe_error is not None:
        errors.append(
            "runtime root or parent is not writable: "
            f"runtime_root={runtime_root}; nearest_existing_parent={nearest}; {parent_probe_error}"
        )
        return RuntimeBootstrapResult(
            ready=False,
            runtime_root=runtime_root,
            source=source,
            created_dirs=created_dirs,
            diagnostics=diagnostics,
            errors=errors,
        )

    required_paths = [runtime_root]
    required_paths.extend(runtime_root / item for item in required_subdirs)

    for path in required_paths:
        try:
            existed = path.exists()
            path.mkdir(parents=True, exist_ok=True)
            if not existed:
                created_dirs.append(path)
        except OSError as exc:
            errors.append(f"failed to create required directory {path}: {exc}")
            return RuntimeBootstrapResult(
                ready=False,
                runtime_root=runtime_root,
                source=source,
                created_dirs=created_dirs,
                diagnostics=diagnostics,
                errors=errors,
            )

    for path in required_paths:
        probe_error = _probe_write(path)
        if probe_error is not None:
            errors.append(f"required directory not writable: {probe_error}")

    if created_dirs:
        diagnostics.append(
            "created directories: "
            + ", ".join(str(item) for item in created_dirs)
        )
    else:
        diagnostics.append("all required runtime directories already existed")

    return RuntimeBootstrapResult(
        ready=not errors,
        runtime_root=runtime_root,
        source=source,
        created_dirs=created_dirs,
        diagnostics=diagnostics,
        errors=errors,
    )
