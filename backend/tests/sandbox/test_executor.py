from __future__ import annotations

import sys

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
    with pytest.raises(executor.UnsafePath):
        executor.run({"../escape.txt": "x"}, ["python", "-c", "print(1)"])


def test_environment_does_not_leak_secrets(monkeypatch):
    monkeypatch.setenv("SUPER_SECRET_TOKEN", "do-not-leak")
    result = executor.run(
        {"env.py": "import os\nprint(os.environ.get('SUPER_SECRET_TOKEN', 'absent'))"}, ["python", "env.py"]
    )
    assert "do-not-leak" not in result.stdout
    assert "absent" in result.stdout


def test_output_is_truncated():
    result = executor.run({"loud.py": "print('x' * 60000)\n"}, ["python", "loud.py"], timeout=20)
    assert len(result.stdout) < 25000
    assert "truncated" in result.stdout
