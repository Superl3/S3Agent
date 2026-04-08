#!/usr/bin/env python3
"""Repository scouting helpers for read-only exploration flows."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "only",
    "without",
    "about",
    "after",
    "before",
    "where",
    "what",
    "which",
    "when",
    "while",
    "under",
    "over",
    "need",
    "needs",
    "required",
    "report",
    "summary",
    "analysis",
    "investigate",
    "investigation",
    "read",
    "read-only",
    "read_only",
    "explore",
    "exploration",
    "research",
    "design",
    "artifact",
    "task",
    "outcome",
    "request",
    "requested",
    "should",
    "must",
    "would",
    "could",
    "have",
    "has",
    "had",
    "your",
    "user",
    "files",
    "file",
    "code",
    "edits",
    "modify",
    "modifying",
}

TEXT_FILE_SUFFIXES = {
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".gradle",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".lua",
    ".md",
    ".mjs",
    ".php",
    ".properties",
    ".py",
    ".pxml",
    ".rb",
    ".rs",
    ".scss",
    ".sh",
    ".sql",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}

CODE_FILE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".kts",
    ".mjs",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".ts",
    ".tsx",
    ".vue",
}

ROOT_FINGERPRINT_FILES = (
    "package.json",
    "pnpm-lock.yaml",
    "bun.lock",
    "package-lock.json",
    "tsconfig.json",
    "pyproject.toml",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "README.md",
)

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".next",
    ".nuxt",
    ".turbo",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "target",
    "runtime",
}

ENTRYPOINT_HINTS = (
    "main",
    "index",
    "app",
    "server",
    "router",
    "route",
    "handler",
    "controller",
    "service",
    "store",
    "state",
    "provider",
)

TEST_HINTS = ("test", "tests", "spec", "__tests__")
DOC_HINTS = {
    "api",
    "docs",
    "documentation",
    "current",
    "latest",
    "version",
    "migration",
    "setup",
    "config",
    "configuration",
    "library",
    "libraries",
    "framework",
    "frameworks",
    "pattern",
    "patterns",
    "usage",
    "hook",
    "hooks",
    "router",
    "query",
}

CONTEXT7_LIBRARIES = {
    "drizzle": ("drizzle", ("drizzle", "drizzle orm", "drizzle-orm")),
    "prisma": ("prisma", ("prisma",)),
    "better-auth": ("better-auth", ("better auth", "better-auth")),
    "nextauth": ("nextauth", ("nextauth", "next auth", "next-auth")),
    "clerk": ("clerk", ("clerk",)),
    "nextjs": ("nextjs", ("nextjs", "next.js", "next js")),
    "react": ("react", ("react", "react dom", "react-dom")),
    "tanstack-query": (
        "tanstack query",
        ("tanstack query", "react-query", "react query"),
    ),
    "tanstack-router": (
        "tanstack router",
        ("tanstack router",),
    ),
    "tanstack-start": (
        "tanstack start",
        ("tanstack start",),
    ),
    "cloudflare-workers": (
        "cloudflare workers",
        ("cloudflare workers", "cloudflare worker"),
    ),
    "aws-lambda": ("aws lambda", ("aws lambda",)),
    "vercel": ("vercel", ("vercel",)),
    "shadcn": ("shadcn", ("shadcn", "shadcn/ui")),
    "radix": ("radix", ("radix", "radix ui")),
    "tailwind": ("tailwind", ("tailwind", "tailwindcss")),
    "zustand": ("zustand", ("zustand",)),
    "jotai": ("jotai", ("jotai",)),
    "zod": ("zod", ("zod",)),
    "react-hook-form": (
        "react hook form",
        ("react hook form", "react-hook-form", "rhf"),
    ),
    "vitest": ("vitest", ("vitest",)),
    "playwright": ("playwright", ("playwright",)),
}

SPECIAL_DEPENDENCY_LIBRARY_NAMES = {
    "next": ("nextjs", "nextjs"),
    "next-auth": ("nextauth", "nextauth"),
    "nextauth": ("nextauth", "nextauth"),
    "react-dom": ("react", "react"),
    "tailwindcss": ("tailwind", "tailwind"),
    "@prisma/client": ("prisma", "prisma"),
    "drizzle-orm": ("drizzle", "drizzle"),
    "@tanstack/react-query": ("tanstack-query", "tanstack query"),
    "@tanstack/query-core": ("tanstack-query", "tanstack query"),
    "@tanstack/react-router": ("tanstack-router", "tanstack router"),
    "@tanstack/router-core": ("tanstack-router", "tanstack router"),
    "@tanstack/start": ("tanstack-start", "tanstack start"),
    "@clerk/nextjs": ("clerk", "clerk"),
    "@clerk/clerk-react": ("clerk", "clerk"),
    "@radix-ui/react-slot": ("radix", "radix"),
    "@radix-ui/react-dialog": ("radix", "radix"),
    "@radix-ui/react-popover": ("radix", "radix"),
    "@hookform/resolvers": ("react-hook-form", "react hook form"),
}


@dataclass
class ProviderUsage:
    name: str
    used: bool
    success: bool
    notes: str


@dataclass
class EvidenceItem:
    source_provider: str
    path: str
    line_start: Optional[int]
    line_end: Optional[int]
    symbol: Optional[str]
    summary: str


@dataclass
class RepoScoutResult:
    exploration_kind: str
    exploration_scope: str
    actionability: str
    target_root: str
    context_producer: str
    context_mode: str
    providers: List[ProviderUsage]
    focus_questions: List[str]
    key_findings: List[str]
    evidence_items: List[EvidenceItem]
    open_questions: List[str]
    recommended_next_actions: List[str]
    cache_refs: List[str]
    candidate_files: List[str]
    target_files: List[str]
    search_scope: str
    budget_used: str
    usability_state: str
    confidence: str
    evidence_count: int
    open_questions_count: int
    completion_state: str
    blocked_reason: Optional[str]
    escalation_requested: bool
    notes: List[str]


def normalize_rel_path(path: Path, workspace_root: Path) -> str:
    relative = path.resolve().relative_to(workspace_root.resolve())
    return str(PurePosixPath(*relative.parts))


def normalize_any_path(path: Path, base_root: Optional[Path]) -> str:
    if base_root is None:
        return str(PurePosixPath(*path.resolve().parts))
    try:
        relative = path.resolve().relative_to(base_root.resolve())
        return str(PurePosixPath(*relative.parts))
    except ValueError:
        return str(PurePosixPath(*path.resolve().parts))


def normalize_items(items: Sequence[str]) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for item in items:
        value = item.strip()
        if not value:
            continue
        key = re.sub(r"\s+", " ", value).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(value)
    return normalized
    try:
        relative = path.resolve().relative_to(base_root.resolve())
        return str(PurePosixPath(*relative.parts))
    except ValueError:
        return str(PurePosixPath(*path.resolve().parts))


def tokenize(text: str) -> List[str]:
    candidates = re.findall(r"[A-Za-z0-9_./-]{2,}|[가-힣]{2,}", text)
    seen: set[str] = set()
    tokens: List[str] = []
    for candidate in candidates:
        lowered = candidate.strip("._-/").lower()
        if not lowered or lowered in STOPWORDS:
            continue
        if lowered.isdigit():
            continue
        if len(lowered) < 2:
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        tokens.append(lowered)
    return tokens[:14]


def identifier_candidates(text: str) -> List[str]:
    raw = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)
    ranked: List[str] = []
    seen: set[str] = set()
    for candidate in raw:
        lowered = candidate.lower()
        if lowered in STOPWORDS or lowered in seen:
            continue
        seen.add(lowered)
        ranked.append(candidate)
    return ranked[:8]


def build_focus_questions(
    request_text: str,
    requested_outcome: str,
    task_summary: str,
    localization_targets: Sequence[str],
    exploration_kind: str,
    override_questions: Optional[Sequence[str]] = None,
) -> List[str]:
    if override_questions:
        return normalize_items(list(override_questions))[:3]
    summary_seed = (
        requested_outcome.strip() or task_summary.strip() or request_text.strip()
    )
    questions = [
        f"Which files and tests are most relevant to: {summary_seed}".strip(),
        f"Where is the likely entrypoint or ownership boundary for: {task_summary.strip() or request_text.strip()}".strip(),
    ]
    if localization_targets:
        joined = ", ".join(localization_targets[:3])
        questions.append(
            f"How do localization targets relate to the requested outcome: {joined}"
        )
    elif exploration_kind == "design":
        questions.append(
            "What existing structure or constraints should shape the proposed design?"
        )
    else:
        questions.append(
            "What existing structure would constrain a future patch or investigation follow-up?"
        )
    return [item for item in questions if item]


def workspace_fingerprint(workspace_root: Path) -> List[str]:
    findings: List[str] = []
    present = [
        name for name in ROOT_FINGERPRINT_FILES if (workspace_root / name).exists()
    ]
    if present:
        findings.append("Root fingerprint files detected: " + ", ".join(present[:6]))
    if (workspace_root / "package.json").exists():
        findings.append("JavaScript or TypeScript project markers are present.")
    if (workspace_root / "pyproject.toml").exists() or (
        workspace_root / "requirements.txt"
    ).exists():
        findings.append("Python project markers are present.")
    if (workspace_root / "Cargo.toml").exists():
        findings.append("Rust project markers are present.")
    if (workspace_root / ".serena" / "project.yml").exists():
        findings.append(
            "Serena project configuration is present for symbolic scouting."
        )
    return findings


def should_skip_dir(name: str) -> bool:
    return name in IGNORED_DIRS


def is_probably_text(path: Path) -> bool:
    if path.suffix.lower() in TEXT_FILE_SUFFIXES:
        return True
    if path.suffix and path.suffix.lower() not in TEXT_FILE_SUFFIXES:
        return False
    try:
        sample = path.read_bytes()[:2048]
    except OSError:
        return False
    if b"\x00" in sample:
        return False
    return True


def iter_candidate_files(base: Path) -> Iterable[Path]:
    stack = [base]
    while stack:
        current = stack.pop()
        if current.is_dir():
            try:
                entries = sorted(current.iterdir(), key=lambda item: item.name)
            except OSError:
                continue
            for entry in reversed(entries):
                if entry.is_dir():
                    if should_skip_dir(entry.name):
                        continue
                    stack.append(entry)
                elif entry.is_file() and is_probably_text(entry):
                    stack.append(entry)
            continue
        yield current


def prioritize_bases(
    workspace_root: Path, localization_targets: Sequence[str]
) -> List[Path]:
    prioritized: List[Path] = []
    for target in localization_targets:
        candidate = workspace_root / Path(target.replace("/", os.sep))
        if candidate.exists():
            prioritized.append(candidate)
    prioritized.append(workspace_root)
    seen: set[Path] = set()
    ordered: List[Path] = []
    for item in prioritized:
        resolved = item.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(item)
    return ordered


def dependency_names(workspace_root: Path) -> List[str]:
    package_path = workspace_root / "package.json"
    if not package_path.exists():
        return []
    try:
        payload = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    names: List[str] = []
    for key in (
        "dependencies",
        "devDependencies",
        "peerDependencies",
        "optionalDependencies",
    ):
        block = payload.get(key)
        if isinstance(block, dict):
            for item in block.keys():
                if isinstance(item, str):
                    names.append(item.lower())
    return names


def dependency_context7_candidates(
    workspace_root: Path,
    combined_text: str,
) -> List[Tuple[str, str]]:
    deps = dependency_names(workspace_root)
    matches: List[Tuple[str, str]] = []
    seen: set[Tuple[str, str]] = set()
    for dep in deps:
        mapped = SPECIAL_DEPENDENCY_LIBRARY_NAMES.get(dep)
        if mapped is not None:
            key, library_name = mapped
            pair = (key, library_name)
            if pair not in seen:
                seen.add(pair)
                matches.append(pair)
            continue

        base = dep.split("/")[-1].replace("-", " ").replace("_", " ")
        normalized_base = re.sub(r"\s+", " ", base).strip()
        if not normalized_base:
            continue
        if normalized_base in combined_text or dep in combined_text:
            pair = (sanitize_context7_key(dep), normalized_base)
            if pair not in seen:
                seen.add(pair)
                matches.append(pair)
    return matches


def sanitize_context7_key(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return lowered or "library"


def score_file(
    path: Path, content: str, tokens: Sequence[str]
) -> tuple[int, List[int], List[str], bool, bool]:
    lowered_path = str(path).replace("\\", "/").lower()
    lines = content.splitlines()
    matched_tokens: List[str] = []
    matched_lines: List[int] = []
    score = 0
    for token in tokens:
        matched = False
        if token in lowered_path:
            score += 3
            matched = True
        for idx, line in enumerate(lines, start=1):
            lowered_line = line.lower()
            if token in lowered_line:
                score += 2
                matched = True
                matched_lines.append(idx)
                if len(matched_lines) >= 4:
                    break
        if matched:
            matched_tokens.append(token)

    is_entrypoint = any(hint in lowered_path for hint in ENTRYPOINT_HINTS)
    is_test = any(hint in lowered_path for hint in TEST_HINTS)
    if is_entrypoint:
        score += 5
    if is_test:
        score += 4
    if path.suffix.lower() == ".md":
        score -= 1

    deduped_lines: List[int] = []
    seen_lines: set[int] = set()
    for line_no in matched_lines:
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        deduped_lines.append(line_no)
    return score, deduped_lines[:3], matched_tokens[:6], is_entrypoint, is_test


def collect_text_evidence(
    workspace_root: Path,
    tokens: Sequence[str],
    localization_targets: Sequence[str],
) -> tuple[List[EvidenceItem], List[str], ProviderUsage]:
    evidence: List[EvidenceItem] = []
    notes: List[str] = []
    scanned_files = 0
    provider = ProviderUsage(
        name="text_search",
        used=True,
        success=True,
        notes="Workspace text search completed.",
    )

    bases = prioritize_bases(workspace_root, localization_targets)
    ranked: List[tuple[int, Path, List[int], List[str], bool, bool]] = []
    seen_files: set[Path] = set()
    for base in bases:
        for file_path in iter_candidate_files(base):
            if file_path in seen_files:
                continue
            seen_files.add(file_path)
            scanned_files += 1
            if scanned_files > 1400:
                notes.append(
                    "Text search hit scan limit and kept top-ranked matches only."
                )
                break
            try:
                if file_path.stat().st_size > 500_000:
                    continue
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            score, lines, matched_tokens, is_entrypoint, is_test = score_file(
                file_path, content, tokens
            )
            if score <= 0:
                continue
            ranked.append(
                (score, file_path, lines, matched_tokens, is_entrypoint, is_test)
            )
        if scanned_files > 1400:
            break

    ranked.sort(key=lambda item: (-item[0], str(item[1])))
    for score, file_path, lines, matched_tokens, is_entrypoint, is_test in ranked[:10]:
        summary_parts = ["Matched tokens: " + ", ".join(matched_tokens[:4])]
        if is_entrypoint:
            summary_parts.append("entrypoint-like path")
        if is_test:
            summary_parts.append("test coverage candidate")
        if score >= 10:
            summary_parts.append("high-signal match")
        evidence.append(
            EvidenceItem(
                source_provider="text_search",
                path=normalize_rel_path(file_path, workspace_root),
                line_start=lines[0] if lines else None,
                line_end=lines[-1] if lines else None,
                symbol=None,
                summary="; ".join(summary_parts),
            )
        )

    if evidence:
        top_paths = ", ".join(item.path for item in evidence[:3])
        notes.append(f"Text search ranked top matches around: {top_paths}")
    else:
        provider.success = False
        provider.notes = "Workspace text search did not find high-confidence matches."
        notes.append(
            "No high-confidence text matches were found for the current focus tokens."
        )

    return evidence, notes, provider


def determine_actionability(
    exploration_scope: str,
    request_kind: Optional[str],
    contract_change_suspected: bool,
    evidence_items: Sequence[EvidenceItem],
    completion_state: str,
) -> str:
    if exploration_scope == "baseline":
        return "manager_reusable"
    if contract_change_suspected:
        return "contract_refresh_required"
    if completion_state in {"blocked", "failed"}:
        return "contract_refresh_required"
    if not evidence_items:
        return "advisory_only"
    if request_kind in {"ownership_trace", "impact_boundary", "design_constraints"}:
        return "manager_reusable"
    return "advisory_only"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def parse_mcp_streaming_body(raw: str) -> Dict[str, object]:
    data_lines: List[str] = []
    for line in raw.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if not data_lines:
        raise ValueError("MCP HTTP response did not contain data lines")
    return json.loads("\n".join(data_lines))


def mcp_post_json(
    url: str,
    payload: Dict[str, object],
    headers: Dict[str, str],
    timeout: float,
) -> tuple[Dict[str, object], Dict[str, str]]:
    encoded = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
        response_headers = {
            key.lower(): value for key, value in response.headers.items()
        }
    return parse_mcp_streaming_body(body), response_headers


class SerenaMcpSession:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.process: Optional[subprocess.Popen[bytes]] = None
        self.port = free_port()
        self.session_id: Optional[str] = None
        self.url = f"http://127.0.0.1:{self.port}/mcp"

    def __enter__(self) -> "SerenaMcpSession":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            [
                "serena",
                "start-mcp-server",
                "--transport",
                "streamable-http",
                "--port",
                str(self.port),
            ],
            cwd=str(self.workspace_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self._initialize()
        self.call_tool("activate_project", {"project": str(self.workspace_root)})
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json, text/event-stream"}
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        return headers

    def _initialize(self) -> None:
        last_error: Optional[Exception] = None
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "repo-scout", "version": "0.1"},
                "capabilities": {},
            },
        }
        for _ in range(30):
            try:
                response, headers = mcp_post_json(
                    self.url, payload, self._headers(), timeout=10
                )
                self.session_id = headers.get("mcp-session-id")
                if not self.session_id:
                    raise RuntimeError(
                        "MCP session id missing from initialize response"
                    )
                break
            except (OSError, urllib.error.URLError, ValueError, RuntimeError) as exc:
                last_error = exc
                time.sleep(0.5)
        else:
            raise RuntimeError(f"Failed to initialize Serena MCP server: {last_error}")

        notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        try:
            mcp_post_json(self.url, notification, self._headers(), timeout=10)
        except Exception:
            pass

    def call_tool(self, name: str, arguments: Dict[str, object]) -> str:
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000) % 1_000_000,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        response, _headers = mcp_post_json(
            self.url, payload, self._headers(), timeout=30
        )
        if "error" in response:
            error = response["error"]
            raise RuntimeError(str(error))
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Unexpected MCP tool response shape")
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            text = structured.get("result")
            if isinstance(text, str):
                return text
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    return item["text"]
        raise RuntimeError("MCP tool response did not contain textual output")


def parse_json_result(raw: str) -> object:
    stripped = raw.strip()
    return json.loads(stripped)


def serena_is_available(workspace_root: Path) -> tuple[bool, str]:
    if shutil.which("serena") is None:
        return False, "Serena CLI is not installed in PATH."
    if not (workspace_root / ".serena" / "project.yml").exists():
        return (
            False,
            "Serena project config was not found; skipped to avoid mutating the workspace.",
        )
    return True, "Serena MCP transport is available."


def collect_serena_evidence(
    workspace_root: Path,
    identifier_tokens: Sequence[str],
    broad_tokens: Sequence[str],
    localization_targets: Sequence[str],
) -> tuple[List[EvidenceItem], List[str], ProviderUsage]:
    available, message = serena_is_available(workspace_root)
    if not available:
        return [], [message], ProviderUsage("serena", False, False, message)

    evidence: List[EvidenceItem] = []
    notes: List[str] = []
    symbol_hits = 0
    pattern_hits = 0
    reference_hits = 0
    provider = ProviderUsage("serena", True, True, "Serena MCP session completed.")
    restrict_relative = ""
    for target in localization_targets:
        candidate = workspace_root / Path(target.replace("/", os.sep))
        if candidate.exists():
            try:
                restrict_relative = normalize_rel_path(candidate, workspace_root)
            except ValueError:
                restrict_relative = ""
            break

    try:
        with SerenaMcpSession(workspace_root) as session:
            overview_targets = []
            for target in localization_targets:
                candidate = workspace_root / Path(target.replace("/", os.sep))
                if (
                    candidate.is_file()
                    and candidate.suffix.lower() in CODE_FILE_SUFFIXES
                ):
                    overview_targets.append(candidate)
            for candidate in overview_targets[:2]:
                relative = normalize_rel_path(candidate, workspace_root)
                try:
                    raw = session.call_tool(
                        "get_symbols_overview",
                        {"relative_path": relative, "depth": 1},
                    )
                    parsed = parse_json_result(raw)
                    if isinstance(parsed, dict) and parsed:
                        names: List[str] = []
                        for values in parsed.values():
                            if isinstance(values, list):
                                names.extend(str(item) for item in values[:3])
                        if names:
                            notes.append(
                                f"Serena overview for {relative}: "
                                + ", ".join(names[:5])
                            )
                except Exception as exc:
                    notes.append(
                        f"Serena symbols overview skipped for {relative}: {exc}"
                    )

            for token in identifier_tokens[:4]:
                try:
                    raw = session.call_tool(
                        "find_symbol",
                        {
                            "name_path_pattern": token,
                            "substring_matching": True,
                            "max_matches": 5,
                            "relative_path": restrict_relative,
                        },
                    )
                    parsed = parse_json_result(raw)
                    if not isinstance(parsed, list):
                        continue
                    for item in parsed[:3]:
                        if not isinstance(item, dict):
                            continue
                        relative_path = item.get("relative_path")
                        if not isinstance(relative_path, str):
                            continue
                        body_location = item.get("body_location")
                        start_line = None
                        end_line = None
                        if isinstance(body_location, dict):
                            start = body_location.get("start_line")
                            end = body_location.get("end_line")
                            if isinstance(start, int):
                                start_line = start + 1
                            if isinstance(end, int):
                                end_line = end + 1
                        name_path = item.get("name_path")
                        kind = item.get("kind")
                        summary = f"Serena symbol match for token '{token}'"
                        if kind:
                            summary += f" ({kind})"
                        evidence.append(
                            EvidenceItem(
                                source_provider="serena",
                                path=str(
                                    PurePosixPath(relative_path.replace("\\", "/"))
                                ),
                                line_start=start_line,
                                line_end=end_line,
                                symbol=str(name_path)
                                if isinstance(name_path, str)
                                else None,
                                summary=summary,
                            )
                        )
                        symbol_hits += 1
                        if isinstance(name_path, str):
                            try:
                                raw_refs = session.call_tool(
                                    "find_referencing_symbols",
                                    {
                                        "name_path": name_path,
                                        "relative_path": str(
                                            PurePosixPath(
                                                relative_path.replace("\\", "/")
                                            )
                                        ),
                                    },
                                )
                                parsed_refs = parse_json_result(raw_refs)
                                if isinstance(parsed_refs, dict):
                                    for ref_path, grouped in list(parsed_refs.items())[
                                        :2
                                    ]:
                                        if not isinstance(
                                            ref_path, str
                                        ) or not isinstance(grouped, dict):
                                            continue
                                        for kind_name, entries in grouped.items():
                                            if not isinstance(entries, list):
                                                continue
                                            for entry in entries[:2]:
                                                if not isinstance(entry, dict):
                                                    continue
                                                body_location = entry.get(
                                                    "body_location"
                                                )
                                                ref_start = None
                                                ref_end = None
                                                if isinstance(body_location, dict):
                                                    start = body_location.get(
                                                        "start_line"
                                                    )
                                                    end = body_location.get("end_line")
                                                    if isinstance(start, int):
                                                        ref_start = start + 1
                                                    if isinstance(end, int):
                                                        ref_end = end + 1
                                                ref_name = entry.get("name_path")
                                                evidence.append(
                                                    EvidenceItem(
                                                        source_provider="serena",
                                                        path=str(
                                                            PurePosixPath(
                                                                ref_path.replace(
                                                                    "\\", "/"
                                                                )
                                                            )
                                                        ),
                                                        line_start=ref_start,
                                                        line_end=ref_end,
                                                        symbol=str(ref_name)
                                                        if isinstance(ref_name, str)
                                                        else None,
                                                        summary=(
                                                            f"Serena reference to symbol '{name_path}'"
                                                            f" via {kind_name}"
                                                        ),
                                                    )
                                                )
                                                reference_hits += 1
                            except Exception as exc:
                                notes.append(
                                    f"Serena reference tracing skipped for symbol '{name_path}': {exc}"
                                )
                except Exception as exc:
                    notes.append(
                        f"Serena symbol lookup skipped for token '{token}': {exc}"
                    )

            for token in broad_tokens[:3]:
                try:
                    raw = session.call_tool(
                        "search_for_pattern",
                        {
                            "substring_pattern": token,
                            "relative_path": restrict_relative,
                            "restrict_search_to_code_files": False,
                            "context_lines_before": 0,
                            "context_lines_after": 0,
                        },
                    )
                    parsed = parse_json_result(raw)
                    if not isinstance(parsed, dict):
                        continue
                    for relative_path, hits in list(parsed.items())[:3]:
                        if not isinstance(relative_path, str) or not isinstance(
                            hits, list
                        ):
                            continue
                        line_match = re.search(r"(\d+)", hits[0]) if hits else None
                        start_line = int(line_match.group(1)) if line_match else None
                        evidence.append(
                            EvidenceItem(
                                source_provider="serena",
                                path=str(
                                    PurePosixPath(relative_path.replace("\\", "/"))
                                ),
                                line_start=start_line,
                                line_end=start_line,
                                symbol=None,
                                summary=f"Serena pattern search matched token '{token}'",
                            )
                        )
                        pattern_hits += 1
                except Exception as exc:
                    notes.append(
                        f"Serena pattern search skipped for token '{token}': {exc}"
                    )
    except Exception as exc:
        provider.success = False
        provider.notes = f"Serena MCP provider failed: {exc}"
        notes.append(provider.notes)
        return [], notes, provider

    if symbol_hits or pattern_hits or reference_hits:
        provider.notes = (
            "Serena MCP produced "
            f"{symbol_hits} symbol matches, {reference_hits} reference hits, "
            f"and {pattern_hits} pattern hits."
        )
        notes.append(provider.notes)
    else:
        provider.success = False
        provider.notes = (
            "Serena MCP was reachable but did not return actionable matches."
        )
        notes.append(provider.notes)
    return evidence, notes, provider


def maybe_json_request(url: str) -> Optional[Dict[str, object]]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "repo-scout/0.1"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read().decode("utf-8")
    loaded = json.loads(data)
    return loaded if isinstance(loaded, dict) else None


def text_request(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"Accept": "text/plain", "User-Agent": "repo-scout/0.1"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def detect_context7_libraries(
    request_text: str,
    requested_outcome: str,
    workspace_root: Path,
) -> List[Tuple[str, str]]:
    combined = f"{request_text} {requested_outcome}".lower()
    matches: List[Tuple[str, str]] = []
    seen: set[Tuple[str, str]] = set()
    for key, (search_name, aliases) in CONTEXT7_LIBRARIES.items():
        if any(alias in combined for alias in aliases):
            pair = (key, search_name)
            if pair not in seen:
                seen.add(pair)
                matches.append(pair)
    for pair in dependency_context7_candidates(workspace_root, combined):
        if pair not in seen:
            seen.add(pair)
            matches.append(pair)
    return matches[:2]


def should_fetch_context7(
    request_text: str,
    requested_outcome: str,
    has_internal_evidence: bool,
    libraries: Sequence[Tuple[str, str]],
) -> bool:
    if not libraries:
        return False
    combined = f"{request_text} {requested_outcome}".lower()
    if any(hint in combined for hint in DOC_HINTS):
        return True
    if any(
        token in combined for token in ("error", "deprecated", "migration", "upgrade")
    ):
        return True
    return not has_internal_evidence


def cache_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def fetch_context7_docs(
    key: str,
    library_name: str,
    query: str,
    cache_root: Path,
    cache_ref_base: Optional[Path],
) -> tuple[Optional[EvidenceItem], Optional[str], Optional[str], Optional[str]]:
    topic_slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-") or "overview"
    cache_path = cache_root / "context7" / key / f"{topic_slug}.md"
    cache_ref = normalize_any_path(cache_path, cache_ref_base)
    if cache_path.exists():
        summary = f"Context7 cache hit for {library_name} ({topic_slug})."
        return (
            EvidenceItem(
                source_provider="context7",
                path=cache_ref,
                line_start=None,
                line_end=None,
                symbol=None,
                summary=summary,
            ),
            f"Fetched external docs for {library_name} from cache.",
            cache_ref,
            None,
        )

    search_url = (
        "https://context7.com/api/v2/libs/search?libraryName="
        + urllib.parse.quote(library_name)
        + "&query="
        + urllib.parse.quote(query)
    )
    payload = maybe_json_request(search_url)
    if not payload:
        return (
            None,
            None,
            None,
            f"Context7 search returned no payload for {library_name}.",
        )
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return (
            None,
            None,
            None,
            f"Context7 search returned no matches for {library_name}.",
        )
    first = results[0]
    if not isinstance(first, dict):
        return (
            None,
            None,
            None,
            f"Context7 search returned unexpected result for {library_name}.",
        )
    library_id = first.get("id")
    title = first.get("title")
    description = first.get("description")
    if not isinstance(library_id, str) or not library_id:
        return (
            None,
            None,
            None,
            f"Context7 search did not include a library id for {library_name}.",
        )

    context_url = (
        "https://context7.com/api/v2/context?libraryId="
        + urllib.parse.quote(library_id, safe="/")
        + "&query="
        + urllib.parse.quote(query)
        + "&type=txt"
    )
    text = text_request(context_url)
    header = [
        f"source: Context7 API",
        f"library: {library_name}",
        f"library_id: {library_id}",
        f"topic: {query}",
    ]
    if isinstance(title, str) and title:
        header.append(f"title: {title}")
    cache_text_file(cache_path, "---\n" + "\n".join(header) + "\n---\n\n" + text)
    summary = f"Context7 docs fetched for {library_name}"
    if isinstance(title, str) and title:
        summary += f" ({title})"
    if isinstance(description, str) and description:
        summary += "; " + description[:120]
    return (
        EvidenceItem(
            source_provider="context7",
            path=cache_ref,
            line_start=None,
            line_end=None,
            symbol=None,
            summary=summary,
        ),
        f"Fetched external docs for {library_name} via Context7.",
        cache_ref,
        None,
    )


def collect_context7_evidence(
    request_text: str,
    requested_outcome: str,
    workspace_root: Path,
    cache_root: Optional[Path],
    cache_ref_base: Optional[Path],
    has_internal_evidence: bool,
) -> tuple[List[EvidenceItem], List[str], List[str], ProviderUsage]:
    libraries = detect_context7_libraries(
        request_text, requested_outcome, workspace_root
    )
    if cache_root is None:
        return (
            [],
            [],
            [],
            ProviderUsage(
                name="context7",
                used=False,
                success=False,
                notes="Context7 cache root is unavailable.",
            ),
        )
    if not should_fetch_context7(
        request_text, requested_outcome, has_internal_evidence, libraries
    ):
        note = "No external-library cue required Context7 fetches."
        return [], [], [], ProviderUsage("context7", False, False, note)

    evidence: List[EvidenceItem] = []
    findings: List[str] = []
    cache_refs: List[str] = []
    notes: List[str] = []
    provider = ProviderUsage(
        name="context7",
        used=True,
        success=True,
        notes="Context7 fetches completed.",
    )
    query = (requested_outcome.strip() or request_text.strip() or "overview")[:120]

    for key, library_name in libraries[:2]:
        try:
            item, finding, cache_ref, error = fetch_context7_docs(
                key=key,
                library_name=library_name,
                query=query,
                cache_root=cache_root,
                cache_ref_base=cache_ref_base,
            )
        except Exception as exc:
            item = None
            finding = None
            cache_ref = None
            error = f"Context7 fetch failed for {library_name}: {exc}"
        if item is not None:
            evidence.append(item)
        if finding is not None:
            findings.append(finding)
        if cache_ref is not None:
            cache_refs.append(cache_ref)
        if error is not None:
            provider.success = False
            notes.append(error)

    if evidence:
        provider.notes = f"Context7 fetched or reused {len(evidence)} external documentation artifact(s)."
    elif notes:
        provider.notes = notes[0]
    else:
        provider.success = False
        provider.notes = (
            "Context7 provider was selected but produced no cacheable documentation."
        )

    return evidence, findings, cache_refs, provider


def merge_evidence(*groups: Sequence[EvidenceItem]) -> List[EvidenceItem]:
    merged: List[EvidenceItem] = []
    seen: set[Tuple[str, str, Optional[int], Optional[str]]] = set()
    for group in groups:
        for item in group:
            key = (item.source_provider, item.path, item.line_start, item.symbol)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    priority = {"serena": 0, "text_search": 1, "context7": 2}
    merged.sort(
        key=lambda item: (
            priority.get(item.source_provider, 9),
            item.path,
            item.line_start or 0,
            item.symbol or "",
        )
    )
    return merged[:12]


def run_repo_scout(
    workspace_root: Path,
    request_text: str,
    requested_outcome: str,
    task_summary: str,
    execution_shape: str,
    localization_targets: Sequence[str],
    cache_root: Optional[Path] = None,
    cache_ref_base: Optional[Path] = None,
    exploration_scope: str = "baseline",
    focus_questions_override: Optional[Sequence[str]] = None,
    target_hints: Optional[Sequence[str]] = None,
    request_kind: Optional[str] = None,
    contract_change_suspected: bool = False,
) -> RepoScoutResult:
    exploration_kind = (
        "design" if execution_shape == "read_only_design_artifact" else "investigation"
    )
    focus_questions = build_focus_questions(
        request_text=request_text,
        requested_outcome=requested_outcome,
        task_summary=task_summary,
        localization_targets=localization_targets,
        exploration_kind=exploration_kind,
        override_questions=focus_questions_override,
    )

    normalized_hints = normalize_items(list(target_hints or []))
    hint_paths = [
        item
        for item in normalized_hints
        if "/" in item or "\\" in item or re.search(r"\.[A-Za-z0-9]{1,8}$", item)
    ]
    scout_localization = list(localization_targets) + hint_paths
    combined_text = " ".join(
        [
            request_text,
            requested_outcome,
            task_summary,
            " ".join(focus_questions),
            " ".join(normalized_hints),
        ]
    )
    text_tokens = tokenize(combined_text)
    symbol_tokens = identifier_candidates(combined_text)

    serena_evidence, serena_notes, serena_provider = collect_serena_evidence(
        workspace_root=workspace_root,
        identifier_tokens=symbol_tokens,
        broad_tokens=text_tokens,
        localization_targets=scout_localization,
    )
    text_evidence, text_notes, text_provider = collect_text_evidence(
        workspace_root=workspace_root,
        tokens=text_tokens,
        localization_targets=scout_localization,
    )
    context7_evidence, context7_findings, cache_refs, context7_provider = (
        collect_context7_evidence(
            request_text=request_text,
            requested_outcome=requested_outcome,
            workspace_root=workspace_root,
            cache_root=cache_root,
            cache_ref_base=cache_ref_base,
            has_internal_evidence=bool(serena_evidence or text_evidence),
        )
    )

    evidence_items = merge_evidence(serena_evidence, text_evidence, context7_evidence)
    findings = workspace_fingerprint(workspace_root)
    findings.extend(context7_findings)
    if evidence_items:
        findings.append(
            "Top evidence aligns with the request scope: "
            + ", ".join(item.path for item in evidence_items[:3])
        )
    else:
        findings.append(
            "RepoScout did not collect high-confidence evidence for the current request."
        )

    open_questions: List[str] = []
    if not evidence_items:
        open_questions.append(
            "No high-confidence repo evidence matched the current request text; additional localization hints would improve accuracy."
        )

    recommended_next_actions: List[str] = []
    if evidence_items:
        recommended_next_actions.append(
            "Inspect the top evidence files first and confirm whether they are the intended scouting targets."
        )
    else:
        recommended_next_actions.append(
            "Add concrete file names, symbol names, or localization targets and rerun RepoScout."
        )
    if serena_provider.used and serena_provider.success:
        recommended_next_actions.append(
            "Use Serena symbol or reference queries next if the first-pass evidence still leaves ownership ambiguous."
        )
    if context7_provider.used and context7_provider.success:
        recommended_next_actions.append(
            "Review the cached Context7 docs before making library-specific implementation decisions."
        )

    completion_state = (
        "completed_and_verified" if evidence_items and not open_questions else "partial"
    )
    actionability = determine_actionability(
        exploration_scope=exploration_scope,
        request_kind=request_kind,
        contract_change_suspected=contract_change_suspected,
        evidence_items=evidence_items,
        completion_state=completion_state,
    )
    if actionability == "contract_refresh_required":
        recommended_next_actions.insert(
            0,
            "Escalate to manager and refresh the packet before continuing because the new context implies contract pressure.",
        )
    elif actionability == "advisory_only" and exploration_scope == "focused_refresh":
        recommended_next_actions.insert(
            0,
            "Use this focused result as advisory context only; do not rebuild the packet from it.",
        )
    notes = (
        text_notes
        + serena_notes
        + [text_provider.notes, serena_provider.notes, context7_provider.notes]
    )

    candidate_files = []
    seen_candidate_paths: set[str] = set()
    for item in evidence_items:
        if item.path in seen_candidate_paths:
            continue
        seen_candidate_paths.add(item.path)
        candidate_files.append(item.path)
    target_files = normalize_items(list(scout_localization) + list(normalized_hints))
    target_files = [
        item.split(":", 1)[0] if ":" in item else item for item in target_files
    ]
    target_files = normalize_items(target_files)
    evidence_count = len(evidence_items)
    open_questions_count = len(open_questions)
    if evidence_count > 0 and open_questions_count == 0:
        usability_state = "usable"
        confidence = "high"
    elif evidence_count > 0:
        usability_state = "weak"
        confidence = "medium"
    else:
        usability_state = "empty"
        confidence = "low"
    search_scope = (
        f"execution_shape={execution_shape};localization_targets={len(localization_targets)};"
        f"target_hints={len(normalized_hints)}"
    )
    provider_budget = [
        provider.name
        for provider in [serena_provider, text_provider, context7_provider]
        if provider.used
    ]
    budget_used = (
        "providers="
        + ",".join(provider_budget)
        + ";text_search_file_cap=1400;max_evidence=12"
    )
    context_mode = (
        "focused_refresh"
        if exploration_scope == "focused_refresh"
        else "baseline_provisioning"
    )

    return RepoScoutResult(
        exploration_kind=exploration_kind,
        exploration_scope=exploration_scope,
        actionability=actionability,
        target_root=str(workspace_root),
        context_producer="repo_scout",
        context_mode=context_mode,
        providers=[serena_provider, text_provider, context7_provider],
        focus_questions=focus_questions,
        key_findings=findings,
        evidence_items=evidence_items,
        open_questions=open_questions,
        recommended_next_actions=recommended_next_actions,
        cache_refs=cache_refs,
        candidate_files=candidate_files,
        target_files=target_files,
        search_scope=search_scope,
        budget_used=budget_used,
        usability_state=usability_state,
        confidence=confidence,
        evidence_count=evidence_count,
        open_questions_count=open_questions_count,
        completion_state=completion_state,
        blocked_reason=None,
        escalation_requested=False,
        notes=notes,
    )
