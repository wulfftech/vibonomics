"""Run a command against a set of files in a throwaway directory.

This runs model-written code. It is only the *second* line of defence: production deployments
should use the sidecar service (``vibonomics.sandbox.server``) on an internal-only network with no
secrets and a read-only filesystem. The limits here (fresh temp dir, scrubbed environment, output
written to size-capped files, process group kill when the run ends, POSIX rlimits) still apply in both
cases.

Two things the executor cannot bound on its own. A process that starts its own session leaves the
process group, so the group kill does not reach it. And there is no per-run process count: the
container's ``pids_limit`` caps that (the sidecar's, or the app's in Local mode), and its ``init`` reaps
the processes a run orphans.
"""

from __future__ import annotations

import errno
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO

MAX_OUTPUT = 20_000
MAX_FILE_BYTES = 1_000_000
MAX_WORKSPACE_BYTES = 2 * 1024 * 1024

# ``ExecResult.error_kind`` when the files themselves could not be laid out. The caller can tell that
# apart from a sandbox that is down: the first is the submitted work's fault, the second is nobody's.
WORKSPACE_ERROR = "workspace"


@dataclass
class ExecResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int
    error_kind: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "duration_ms": self.duration_ms,
            "error_kind": self.error_kind,
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


def workspace_size(files: dict[str, str]) -> int:
    return sum(len(content.encode("utf-8", errors="replace")) for content in files.values())


def blames_workspace(exc: BaseException) -> bool:
    """Whether laying out the files failed because of what the files are, not because of this machine."""
    if isinstance(exc, ValueError | FileExistsError | NotADirectoryError | IsADirectoryError):
        return True  # includes UnsafePath and a NUL in a path
    return isinstance(exc, OSError) and exc.errno in {errno.ENAMETOOLONG, errno.EINVAL, errno.ELOOP}


def write_files(root: Path, files: dict[str, str]) -> None:
    if (size := workspace_size(files)) > MAX_WORKSPACE_BYTES:
        raise UnsafePath(f"workspace too large: {size} bytes")
    for name, content in files.items():
        target = root / safe_relative_path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8", errors="replace")
        if len(data) > MAX_FILE_BYTES:
            raise UnsafePath(f"file too large: {name}")
        target.write_bytes(data)


def _limits() -> Callable[[], None] | None:
    """POSIX resource limits applied in the child process.

    RLIMIT_FSIZE also caps the stdout and stderr files. There is deliberately no RLIMIT_NPROC: it counts
    every process and thread the user owns, across the host, so a low value fails ordinary code whenever
    that user is busy and a high one does not bound a run.
    """
    if os.name != "posix":
        return None

    def apply() -> None:
        import resource

        limits = [
            (resource.RLIMIT_CPU, 60),
            (resource.RLIMIT_AS, 1_500 * 1024 * 1024),
            (resource.RLIMIT_FSIZE, 10 * 1024 * 1024),
            (resource.RLIMIT_NOFILE, 256),
            (resource.RLIMIT_CORE, 0),
        ]
        for kind, value in limits:
            try:
                resource.setrlimit(kind, (value, value))
            except (ValueError, OSError):
                pass

    return apply


def _kill(process: subprocess.Popen[bytes]) -> None:
    """Kill the child and everything it started in its process group, then reap the child."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass  # nothing left in the group that we can signal
    else:
        process.kill()
    process.wait()


def _read_output(stream: IO[bytes]) -> str:
    """Decode what the child wrote, keeping only the start and the end when there is too much."""
    size = stream.seek(0, os.SEEK_END)
    stream.seek(0)
    if size <= MAX_OUTPUT:
        return stream.read(MAX_OUTPUT).decode("utf-8", errors="replace")
    half = MAX_OUTPUT // 2
    head = stream.read(half).decode("utf-8", errors="replace")
    stream.seek(size - half)
    tail = stream.read(half).decode("utf-8", errors="replace")
    return f"{head}\n…[{size - 2 * half} bytes truncated]…\n{tail}"


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def run(files: dict[str, str], command: list[str], timeout: float = 30.0, *, cwd: str | None = None) -> ExecResult:
    """Run ``command`` in a fresh workspace holding ``files``.

    Never raises for anything the files, command or cwd contain: a workspace that cannot be written or a
    command that cannot start comes back as a result with ``exit_code=None`` and the reason in stderr. When
    the files are to blame, ``error_kind`` is ``"workspace"``.
    """
    timeout = max(1.0, min(float(timeout), 120.0))
    started = time.monotonic()
    try:
        scratch = Path(tempfile.mkdtemp(prefix="vibonomics-run-"))
    except OSError as exc:
        return ExecResult(None, "", f"{type(exc).__name__}: {exc}", False, _elapsed_ms(started))
    try:
        # The workspace holds only the model's files; the output files sit beside it.
        root = scratch / "workspace"
        root.mkdir()
        try:
            write_files(root, files)
            workdir = root / safe_relative_path(cwd) if cwd else root
            workdir.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError) as exc:
            if not blames_workspace(exc):
                raise
            return ExecResult(
                None, "", f"{type(exc).__name__}: {exc}", False, _elapsed_ms(started), error_kind=WORKSPACE_ERROR
            )
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
        # Files rather than pipes: RLIMIT_FSIZE caps what the child can write, and only a bounded head and
        # tail is read back, so a test that prints in a loop cannot grow this process.
        with (
            tempfile.TemporaryFile(dir=scratch, buffering=0) as stdout,
            tempfile.TemporaryFile(dir=scratch, buffering=0) as stderr,
        ):
            process = subprocess.Popen(
                argv,
                cwd=str(workdir),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=os.name == "posix",
                preexec_fn=_limits(),
            )
            timed_out = False
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                # Also on a normal exit: anything the run left behind would otherwise outlive it.
                _kill(process)
            duration_ms = _elapsed_ms(started)
            return ExecResult(
                exit_code=None if timed_out else process.returncode,
                stdout=_read_output(stdout),
                stderr=_read_output(stderr),
                timed_out=timed_out,
                duration_ms=duration_ms,
            )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # An unsafe or oversized file, a file where a directory is needed, a NUL in a path, a missing command.
        return ExecResult(None, "", f"{type(exc).__name__}: {exc}", False, _elapsed_ms(started))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
