"""Run a command against a set of files in a throwaway directory.

This runs model-written code. It is only the *second* line of defence: production deployments
should use the sidecar service (``vibonomics.sandbox.server``) on an internal-only network with no
secrets and a read-only filesystem. The limits here (fresh temp dir, scrubbed environment, process
group kill on timeout, POSIX rlimits) still apply in both cases.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

MAX_OUTPUT = 20_000
MAX_FILE_BYTES = 1_000_000


@dataclass
class ExecResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int

    def to_dict(self) -> dict[str, object]:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "duration_ms": self.duration_ms,
        }


class UnsafePath(ValueError):
    pass


def safe_relative_path(name: str) -> Path:
    if not name or name.startswith(("/", "\\")) or ":" in name:
        raise UnsafePath(f"unsafe path: {name!r}")
    path = Path(name)
    if any(part in {"..", ""} for part in path.parts):
        raise UnsafePath(f"unsafe path: {name!r}")
    return path


def write_files(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        target = root / safe_relative_path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8", errors="replace")
        if len(data) > MAX_FILE_BYTES:
            raise UnsafePath(f"file too large: {name}")
        target.write_bytes(data)


def _limits() -> object | None:
    """POSIX resource limits applied in the child process."""
    if os.name != "posix":
        return None

    def apply() -> None:
        import resource

        limits = [
            (resource.RLIMIT_CPU, 60),
            (resource.RLIMIT_AS, 1_500 * 1024 * 1024),
            (resource.RLIMIT_FSIZE, 10 * 1024 * 1024),
            (resource.RLIMIT_NOFILE, 256),
            (resource.RLIMIT_NPROC, 64),
            (resource.RLIMIT_CORE, 0),
        ]
        for kind, value in limits:
            try:
                resource.setrlimit(kind, (value, value))
            except (ValueError, OSError):
                pass

    return apply


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    half = MAX_OUTPUT // 2
    return f"{text[:half]}\n…[{len(text) - MAX_OUTPUT} characters truncated]…\n{text[-half:]}"


def run(files: dict[str, str], command: list[str], timeout: float = 30.0, *, cwd: str | None = None) -> ExecResult:
    timeout = max(1.0, min(float(timeout), 120.0))
    root = Path(tempfile.mkdtemp(prefix="vibonomics-run-"))
    started = time.monotonic()
    try:
        write_files(root, files)
        workdir = root / safe_relative_path(cwd) if cwd else root
        workdir.mkdir(parents=True, exist_ok=True)
        argv = list(command)
        if argv and argv[0] in {"python", "python3"}:
            argv[0] = sys.executable
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(root),
            "TMPDIR": str(root),
            "TEMP": str(root),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONIOENCODING": "utf-8",
            "NO_COLOR": "1",
        }
        try:
            completed = subprocess.run(
                argv,
                cwd=str(workdir),
                env=env,
                capture_output=True,
                timeout=timeout,
                check=False,
                start_new_session=os.name == "posix",
                preexec_fn=_limits(),
            )
            return ExecResult(
                exit_code=completed.returncode,
                stdout=_truncate(completed.stdout.decode("utf-8", errors="replace")),
                stderr=_truncate(completed.stderr.decode("utf-8", errors="replace")),
                timed_out=False,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            return ExecResult(
                exit_code=None,
                stdout=_truncate((exc.stdout or b"").decode("utf-8", errors="replace")),
                stderr=_truncate((exc.stderr or b"").decode("utf-8", errors="replace")),
                timed_out=True,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except (OSError, ValueError) as exc:
            return ExecResult(None, "", f"{type(exc).__name__}: {exc}", False, int((time.monotonic() - started) * 1000))
    finally:
        shutil.rmtree(root, ignore_errors=True)
