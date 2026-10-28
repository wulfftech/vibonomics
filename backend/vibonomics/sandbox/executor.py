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

Run uids close the first gap for the sidecar. Started as root with ``VIBONOMICS_SANDBOX_RUN_UIDS`` set
(``65530,65531``), each run claims one of those uids, owns its workspace under it and is killed by uid, so
nothing a run leaves behind, in or out of its process group, outlives it, and no run shares a uid with the
server or with another run. That needs CAP_SETUID, CAP_SETGID and CAP_CHOWN and no others. Anywhere else
(Local mode, a development machine) there is a single uid and the gap stays.
"""

from __future__ import annotations

import errno
import logging
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO

log = logging.getLogger(__name__)

MAX_OUTPUT = 20_000
MAX_FILE_BYTES = 1_000_000
MAX_WORKSPACE_BYTES = 2 * 1024 * 1024

# ``ExecResult.error_kind`` when the files themselves could not be laid out. The caller can tell that
# apart from a sandbox that is down: the first is the submitted work's fault, the second is nobody's.
WORKSPACE_ERROR = "workspace"

RUN_UIDS_ENV = "VIBONOMICS_SANDBOX_RUN_UIDS"
# At most this many processes (threads included) per run uid. The uid is the run's alone, so unlike a limit on
# the shared uid it cannot fail ordinary code because something else is busy.
RUN_NPROC = 128
MAX_RUN_UID = 2**31

_uid_pool: queue.Queue[int] | None = None
_uid_pool_lock = threading.Lock()


def run_uids() -> list[int]:
    """The uids runs are started under: only when this process is root and the operator listed some."""
    if os.name != "posix" or os.geteuid() != 0:
        return []
    uids: list[int] = []
    for part in os.environ.get(RUN_UIDS_ENV, "").replace(" ", "").split(","):
        # Not 0 (root), and not 2**32-1, which is (uid_t)-1: "leave the uid alone", so a run started under it
        # would stay root, and the cleanup that kills everything its uid owns would kill everything.
        if part.isdigit() and 0 < int(part) < MAX_RUN_UID and int(part) not in uids:
            uids.append(int(part))
    return uids


def reset_run_uids() -> None:
    """Forget the claimed-uid pool (it is built from the environment on first use)."""
    global _uid_pool
    with _uid_pool_lock:
        _uid_pool = None


def _claim_uid() -> int | None:
    """A free run uid, waiting for one if every uid is in use; None when runs share the server's uid."""
    global _uid_pool
    uids = run_uids()
    if not uids:
        return None
    with _uid_pool_lock:
        if _uid_pool is None:
            _uid_pool = queue.Queue()
            for uid in uids:
                _uid_pool.put(uid)
        pool = _uid_pool
    return pool.get()


def _release_uid(uid: int | None) -> None:
    if uid is not None and _uid_pool is not None:
        _uid_pool.put(uid)


# Runs as the run's uid, so it can end that uid's processes and remove its files without CAP_KILL or
# CAP_DAC_OVERRIDE. kill(-1) signals every process the caller may, except itself: here, the run's own.
_AS_RUN_UID = """
import os, shutil, signal, sys, tempfile
mode, workspace = sys.argv[1], sys.argv[2]
if os.getuid() == 0 or os.geteuid() == 0:
    sys.exit(3)  # the switch to the run's uid did not happen: kill(-1) as root would end the whole container
try:
    os.kill(-1, signal.SIGKILL)
except (ProcessLookupError, PermissionError):
    pass
if mode == "clean":
    shutil.rmtree(workspace, ignore_errors=True)
    me = os.getuid()
    for entry in os.scandir(tempfile.gettempdir()):
        try:
            if entry.stat(follow_symlinks=False).st_uid == me:
                if entry.is_dir(follow_symlinks=False):
                    shutil.rmtree(entry.path, ignore_errors=True)
                else:
                    os.unlink(entry.path)
        except OSError:
            pass
"""


def _as_run_uid(uid: int, mode: str, workspace: Path) -> None:
    """End everything the uid owns (``mode`` "kill"), and also remove its files ("clean")."""
    if not 0 < uid < MAX_RUN_UID:
        raise ValueError(f"not a run uid: {uid}")
    try:
        subprocess.run(
            [sys.executable, "-I", "-c", _AS_RUN_UID, mode, str(workspace)],
            cwd="/",
            env={"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")},
            user=uid,
            group=uid,
            extra_groups=[],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        log.exception("Could not %s run uid %d", mode, uid)


def _chown_tree(path: Path, uid: int) -> None:
    # Bottom-up, so a directory is listed before it stops being readable to us: without CAP_DAC_OVERRIDE the
    # server cannot read a directory the uid owns. (os.walk skips one it cannot read, without an error.)
    for directory, names, files in os.walk(path, topdown=False, onerror=_raise):
        for name in (*names, *files):
            os.chown(os.path.join(directory, name), uid, uid, follow_symlinks=False)
    os.chown(path, uid, uid, follow_symlinks=False)


def _raise(exc: OSError) -> None:
    raise exc


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


def _limits(own_uid: bool = False) -> Callable[[], None] | None:
    """POSIX resource limits applied in the child process.

    RLIMIT_FSIZE also caps the stdout and stderr files. RLIMIT_NPROC is set only when the run has a uid of
    its own: it counts every process and thread the user owns, across the host, so on a shared uid a low
    value fails ordinary code whenever that user is busy and a high one does not bound a run.
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
        if own_uid:
            limits.append((resource.RLIMIT_NPROC, RUN_NPROC))
        for kind, value in limits:
            try:
                resource.setrlimit(kind, (value, value))
            except (ValueError, OSError):
                pass

    return apply


def _kill(process: subprocess.Popen[bytes], uid: int | None = None, workspace: Path | None = None) -> None:
    """Kill the child and everything it started, then reap the child.

    By process group, or, when the run has a uid, everything that uid owns, whichever session it started.
    """
    if uid is not None and workspace is not None:
        _as_run_uid(uid, "kill", workspace)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # The helper could not run, so nothing has ended it. Without CAP_KILL there is no other way to.
            log.error("Run uid %d could not be killed; its process %d is still running", uid, process.pid)
        return
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
    uid = _claim_uid()
    try:
        return _run(files, command, timeout, cwd, uid, started)
    finally:
        _release_uid(uid)


def _run(
    files: dict[str, str], command: list[str], timeout: float, cwd: str | None, uid: int | None, started: float
) -> ExecResult:
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
        if uid is not None:
            # The run reaches its workspace through scratch (execute, not list); nothing else of the run's
            # uid, or of the other run's, can write beside it. The workspace itself is the uid's alone.
            os.chmod(scratch, 0o711)
            # Closed to every other uid, including the other run's. Group root keeps execute on the top directory,
            # which is all the server needs (to start the run in it) without CAP_DAC_OVERRIDE. The mode is set
            # first: once the uid owns the directory, changing it would need CAP_FOWNER.
            os.chmod(root, 0o710)
            _chown_tree(root, uid)
            os.chown(root, uid, 0)
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
                preexec_fn=_limits(own_uid=uid is not None),
                **({"user": uid, "group": uid, "extra_groups": []} if uid is not None else {}),
            )
            timed_out = False
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                # Also on a normal exit: anything the run left behind would otherwise outlive it.
                _kill(process, uid, root)
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
        if uid is not None:
            _as_run_uid(uid, "clean", root)  # the workspace is the uid's, so only the uid can empty it
            try:
                os.rmdir(root)  # now empty; rmtree would fail to list it, which only the uid and group root can enter
            except OSError:
                pass
        shutil.rmtree(scratch, ignore_errors=True)


def selftest() -> str | None:
    """None when runs work as configured, else why not. For the sidecar to check at startup.

    With run uids configured, a missing capability or an unmapped uid would otherwise show only as every
    task being left ungraded.
    """
    uids = run_uids()
    if not uids:
        return None
    result = run({"ok.py": "import os\nprint(os.getuid(), os.getgid())\n"}, ["python", "ok.py"], timeout=20)
    if result.exit_code != 0:
        return f"a test run failed (exit {result.exit_code}): {result.stderr.strip()[-300:] or 'no output'}"
    if result.stdout.split()[:1] and int(result.stdout.split()[0]) not in uids:
        return f"a test run started as uid {result.stdout.split()[0]}, not one of {uids}"
    return None
