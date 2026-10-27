from __future__ import annotations

import os
import signal
import sys
import time
from contextlib import suppress

import pytest

from vibonomics.sandbox import executor

PASSING = {
    "maths.py": "def add(a, b):\n    return a + b\n",
    "tests/test_maths.py": (
        "import unittest\n\nfrom maths import add\n\n\n"
        "class AddTests(unittest.TestCase):\n"
        "    def test_adds(self):\n        self.assertEqual(add(2, 3), 5)\n"
    ),
}

FAILING = {
    "maths.py": "def add(a, b):\n    return a - b\n",
    "tests/test_maths.py": PASSING["tests/test_maths.py"],
}


def test_runs_tests_in_a_fresh_directory():
    result = executor.run(PASSING, ["python", "-m", "unittest", "discover", "-s", "tests"])
    assert result.exit_code == 0, result.stderr
    assert result.timed_out is False
    assert result.duration_ms >= 0


def test_reports_failures():
    result = executor.run(FAILING, ["python", "-m", "unittest", "discover", "-s", "tests"])
    assert result.exit_code != 0
    assert "FAILED" in result.stderr


def test_timeout_is_enforced():
    result = executor.run({"slow.py": "import time\ntime.sleep(10)\n"}, ["python", "slow.py"], timeout=1)
    assert result.timed_out is True
    assert result.duration_ms < 8000


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX resource limits")
def test_memory_limit_kills_runaway_process():
    code = "x = bytearray(3_000_000_000)\n"
    result = executor.run({"hog.py": code}, ["python", "hog.py"], timeout=20)
    assert result.exit_code != 0


def test_rejects_unsafe_paths():
    with pytest.raises(executor.UnsafePath):
        executor.safe_relative_path("../../etc/passwd")
    with pytest.raises(executor.UnsafePath):
        executor.safe_relative_path("/etc/passwd")


def test_environment_does_not_leak_secrets(monkeypatch):
    monkeypatch.setenv("SUPER_SECRET_TOKEN", "do-not-leak")
    result = executor.run(
        {"env.py": "import os\nprint(os.environ.get('SUPER_SECRET_TOKEN', 'absent'))"}, ["python", "env.py"]
    )
    assert "do-not-leak" not in result.stdout
    assert "absent" in result.stdout


def test_output_is_truncated():
    code = "print('start')\nprint('x' * 60000)\nprint('end')\n"
    result = executor.run({"loud.py": code}, ["python", "loud.py"], timeout=20)
    assert len(result.stdout) < 25000
    assert "truncated" in result.stdout
    assert result.stdout.startswith("start")
    assert result.stdout.rstrip().endswith("end")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX resource limits")
def test_endless_output_is_capped_without_growing_this_process():
    import resource

    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    code = "while True:\n    print('x' * 1000)\n"
    result = executor.run({"loud.py": code}, ["python", "loud.py"], timeout=5)
    grown = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before
    # The file size limit stops the writer long before the timeout. Pipes had no limit, and the parent
    # buffered all of it: more than a gigabyte in five seconds.
    assert result.timed_out is False
    assert result.exit_code not in (0, None)
    assert "File too large" in result.stderr
    assert len(result.stdout) < executor.MAX_OUTPUT + 100
    assert grown < 100 * 1024 * (1024 if sys.platform == "darwin" else 1)  # ru_maxrss is bytes on macOS


def _alive(pid: int) -> bool:
    """A zombie counts as dead: the test process may have an init that never reaps it."""
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):  # reaped before the open, or between the open and the read
        return False


SPAWNS_A_SLEEPER = (
    "import subprocess, sys\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
    "print(child.pid, flush=True)\n"
)


@pytest.mark.skipif(not os.path.isdir("/proc/self"), reason="reads process state from /proc")
@pytest.mark.parametrize(
    ("rest", "timeout", "timed_out"),
    [("import time\ntime.sleep(60)\n", 3, True), ("", 20, False)],
    ids=["timeout", "normal-exit"],
)
def test_processes_a_run_starts_do_not_outlive_it(rest, timeout, timed_out):
    result = executor.run({"spawn.py": SPAWNS_A_SLEEPER + rest}, ["python", "spawn.py"], timeout=timeout)
    assert result.stdout.strip(), result.stderr
    pid = int(result.stdout.split()[0])
    try:
        assert result.timed_out is timed_out
        deadline = time.monotonic() + 5
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(pid), "a grandchild survived the run"
    finally:
        if _alive(pid):
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX resource limits")
def test_process_count_is_not_limited_per_user():
    """RLIMIT_NPROC counts every process the user owns, so other runs' processes would fail this one."""
    import resource

    code = "import resource\nprint(resource.getrlimit(resource.RLIMIT_NPROC)[0])\n"
    result = executor.run({"nproc.py": code}, ["python", "nproc.py"])
    assert int(result.stdout) == resource.getrlimit(resource.RLIMIT_NPROC)[0]


@pytest.mark.parametrize(
    ("files", "cwd", "command", "error"),
    [
        ({"../escape.txt": "x"}, None, ["python", "-c", "pass"], "UnsafePath: unsafe path"),
        ({"big.txt": "x" * (executor.MAX_FILE_BYTES + 1)}, None, ["python", "-c", "pass"], "file too large"),
        ({"tests": "x", "tests/test_a.py": "pass\n"}, None, ["python", "-c", "pass"], "FileExistsError"),
        ({"a\x00b.py": "x"}, None, ["python", "-c", "pass"], "ValueError"),
        ({"sub": "x"}, "sub", ["python", "-c", "pass"], "FileExistsError"),
        ({}, "../up", ["python", "-c", "pass"], "UnsafePath: unsafe path"),
        ({}, None, ["./no-such-command"], "FileNotFoundError"),
    ],
    ids=[
        "unsafe-path",
        "too-large",
        "file-named-like-a-folder",
        "nul-in-path",
        "cwd-is-a-file",
        "unsafe-cwd",
        "no-command",
    ],
)
def test_a_run_that_cannot_start_is_reported_not_raised(files, cwd, command, error):
    result = executor.run(files, command, cwd=cwd)
    assert result.exit_code is None
    assert result.timed_out is False
    assert error in result.stderr


def test_workspace_holds_only_the_given_files():
    code = "import os\nprint(sorted(os.listdir('.')))\n"
    result = executor.run({"list.py": code, "pkg/mod.py": ""}, ["python", "list.py"])
    assert result.stdout.strip() == "['list.py', 'pkg']"


def test_stdin_is_empty():
    result = executor.run({"read.py": "import sys\nprint(repr(sys.stdin.read()))\n"}, ["python", "read.py"])
    assert result.timed_out is False
    assert result.stdout.strip() == "''"
