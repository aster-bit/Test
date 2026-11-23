"""ExecutionSandbox — runs untrusted candidate code in isolation.

Backends (selected automatically, strongest available first):

``docker``      ``docker run --network none --read-only --cap-drop ALL
                --security-opt no-new-privileges --memory --cpus --pids-limit
                --user nobody`` with the trusted runner mounted read-only.
``subprocess``  ``unshare -rn`` (private user+network namespace => no network)
                + ``python -I -S`` + empty environment + private temp cwd +
                rlimits (address space, CPU, file size, open files) + the
                runner's audit-hook policy.

Neither backend ever receives host environment variables, so API keys cannot
leak into candidate code. Static validation runs *before* any execution.
The report records which backend was used: the subprocess backend is weaker
than a container and is labelled as such.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RUNNER_PATH = Path(__file__).with_name("sandbox_runner.py")

ALLOWED_IMPORTS = {
    "re", "json", "math", "datetime", "decimal", "unicodedata", "itertools",
    "collections", "string", "functools", "statistics", "fractions", "operator",
    "heapq", "bisect", "random", "copy", "time", "typing", "dataclasses", "enum",
}
FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars", "input",
    "breakpoint", "help", "exit", "quit", "memoryview", "setattr", "delattr", "getattr",
}
MAX_SOURCE_BYTES = 40_000


class StaticValidationError(ValueError):
    pass


def validate_source(source: str) -> list[str]:
    """Return a list of policy violations (empty == accepted)."""
    problems: list[str] = []
    if len(source.encode()) > MAX_SOURCE_BYTES:
        problems.append(f"source exceeds {MAX_SOURCE_BYTES} bytes")
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"syntax error: {e}"]
    has_step = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                    problems.append(f"import not allowed: {a.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in ALLOWED_IMPORTS or node.level:
                problems.append(f"import not allowed: from {node.module}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            problems.append(f"forbidden name: {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
            problems.append(f"dunder attribute access: {node.attr}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("__") \
                and node.value.endswith("__") and len(node.value) > 4:
            problems.append(f"dunder string literal: {node.value}")
        elif isinstance(node, ast.Global):
            problems.append("global not allowed")
        elif isinstance(node, ast.FunctionDef) and node.name == "step":
            if [a.arg for a in node.args.args][:2] == ["state", "params"]:
                has_step = True
    if not has_step:
        problems.append("missing def step(state, params)")
    return problems


@dataclass
class SandboxLimits:
    wall_timeout_s: float = 30.0
    item_timeout_s: float = 2.0
    cpu_seconds: int = 20
    memory_mb: int = 512
    max_output_bytes: int = 20_000_000
    file_size_bytes: int = 1_000_000
    open_files: int = 64
    timer: str = "real"  # "cpu": per-item limit in CPU seconds (load-independent)


@dataclass
class ItemResult:
    state: dict[str, Any]
    error: str | None
    ms: float


@dataclass
class BatchResult:
    items: list[ItemResult]
    setup_error: str | None = None
    backend: str = ""
    wall_ms: float = 0.0
    violations: list[str] = field(default_factory=list)


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=5)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def unshare_available() -> bool:
    if not shutil.which("unshare"):
        return False
    try:
        return subprocess.run(["unshare", "-rn", "true"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class ExecutionSandbox:
    def __init__(self, backend: str = "auto", limits: SandboxLimits | None = None,
                 docker_image: str = "python:3.12-slim"):
        self.limits = limits or SandboxLimits()
        self.docker_image = docker_image
        if backend == "auto":
            backend = "docker" if docker_available() else "subprocess"
        self.backend = backend
        self.network_isolated = backend == "docker" or unshare_available()
        self.stats = {"batches": 0, "items": 0, "static_rejections": 0, "setup_errors": 0}

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "network_isolated": self.network_isolated,
            "limits": self.limits.__dict__,
            "strength": "container" if self.backend == "docker" else
            ("namespace+rlimits+audit-hook" if self.network_isolated else "rlimits+audit-hook (NO network namespace)"),
        }

    # ------------------------------------------------------------------
    def run_batch(self, segments: list[dict[str, Any]], states: list[dict[str, Any]]) -> BatchResult:
        violations: list[str] = []
        for seg in segments:
            for p in validate_source(seg["source"]):
                violations.append(f"{seg['name']}: {p}")
        if violations:
            self.stats["static_rejections"] += 1
            err = "static validation failed: " + "; ".join(violations[:5])
            return BatchResult([ItemResult(s, err, 0.0) for s in states], setup_error=err,
                               backend=self.backend, violations=violations)
        payload = json.dumps({"segments": segments, "states": states, "item_timeout_s": self.limits.item_timeout_s,
                              "timer": self.limits.timer})
        import time
        t0 = time.perf_counter()
        # process-level limits scale with the batch; the per-item limit stays fixed
        wall = max(self.limits.wall_timeout_s, self.limits.item_timeout_s * len(states) * (3 if self.limits.timer == "cpu" else 1) + 10)
        if self.backend == "docker":
            out, err_text, code = self._run_docker(payload, wall)
        else:
            out, err_text, code = self._run_subprocess(payload, wall)
        wall = (time.perf_counter() - t0) * 1000
        self.stats["batches"] += 1
        self.stats["items"] += len(states)
        try:
            doc = json.loads(out)
            items = [ItemResult(r["state"], r["error"], r["ms"]) for r in doc["results"]]
            if len(items) != len(states):
                raise ValueError("result count mismatch")
            if doc.get("setup_error"):
                self.stats["setup_errors"] += 1
            return BatchResult(items, doc.get("setup_error"), self.backend, wall)
        except (ValueError, KeyError, TypeError):
            msg = f"sandbox process failed (exit={code}): {err_text[-400:] or 'no output'}"
            return BatchResult([ItemResult(s, msg, 0.0) for s in states], setup_error=msg,
                               backend=self.backend, wall_ms=wall)

    def _preexec_for(self, wall: float):
        lim = self.limits
        cpu = max(lim.cpu_seconds, int(wall) + 5)

        def fn():  # runs in the child before exec
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (lim.memory_mb * 1024 * 1024,) * 2)
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
            resource.setrlimit(resource.RLIMIT_FSIZE, (lim.file_size_bytes,) * 2)
            resource.setrlimit(resource.RLIMIT_NOFILE, (lim.open_files,) * 2)
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            os.setsid()
        return fn

    def _preexec(self):  # legacy single-batch limits
        import resource
        lim = self.limits
        resource.setrlimit(resource.RLIMIT_AS, (lim.memory_mb * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (lim.cpu_seconds, lim.cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (lim.file_size_bytes,) * 2)
        resource.setrlimit(resource.RLIMIT_NOFILE, (lim.open_files,) * 2)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        os.setsid()

    def _run_subprocess(self, payload: str, wall: float | None = None) -> tuple[str, str, int]:
        wall = wall or self.limits.wall_timeout_s
        with tempfile.TemporaryDirectory(prefix="sf-sbx-") as work:
            runner = Path(work) / "runner.py"
            shutil.copyfile(RUNNER_PATH, runner)
            cmd = [sys.executable, "-I", "-S", str(runner)]
            if self.network_isolated:
                cmd = ["unshare", "-rn", *cmd]
            try:
                p = subprocess.run(cmd, input=payload, capture_output=True, text=True, cwd=work,
                                   env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                                   timeout=wall, preexec_fn=self._preexec_for(wall))
                return p.stdout[: self.limits.max_output_bytes], p.stderr, p.returncode
            except subprocess.TimeoutExpired:
                return "", "wall-clock timeout", -9

    def _run_docker(self, payload: str, wall: float | None = None) -> tuple[str, str, int]:
        lim = self.limits
        wall = wall or lim.wall_timeout_s
        cmd = [
            "docker", "run", "--rm", "-i", "--network", "none", "--read-only",
            "--tmpfs", "/tmp:rw,size=16m", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", f"{lim.memory_mb}m", "--cpus", "1", "--pids-limit", "64", "--user", "65534:65534",
            "--ulimit", f"cpu={max(lim.cpu_seconds, int(wall) + 5)}", "-v", f"{RUNNER_PATH}:/sandbox/runner.py:ro",
            "--env-file", "/dev/null", self.docker_image, "python", "-I", "-S", "/sandbox/runner.py",
        ]
        try:
            p = subprocess.run(cmd, input=payload, capture_output=True, text=True,
                               timeout=wall + 15, env={"PATH": os.environ.get("PATH", "")})
            return p.stdout[: lim.max_output_bytes], p.stderr, p.returncode
        except subprocess.TimeoutExpired:
            return "", "wall-clock timeout", -9
