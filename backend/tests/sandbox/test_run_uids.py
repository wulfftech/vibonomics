"""Runs under their own uids: what a run can and cannot reach once it is not the server's user.

The pool tests run anywhere. The rest need root (to switch user) and a POSIX system, so they run in the
sidecar's own container and are skipped on a developer's machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import threading
import time

import pytest

from vibonomics.sandbox import executor

AS_ROOT = os.name == "posix" and os.geteuid() == 0
needs_root = pytest.mark.skipif(not AS_ROOT, reason="switching user needs root")

UIDS = (65530, 65531)


@pytest.fixture
def run_uids(monkeypatch):
    monkeypatch.setenv(executor.RUN_UIDS_ENV, ",".join(str(u) for u in UIDS))
    executor.reset_run_uids()
    yield list(UIDS)
    executor.reset_run_uids()


@pytest.fixture
def shared_dir():
    """A directory under /tmp that every uid can reach, since pytest's own temp dirs are root's alone."""
    path = tempfile.mkdtemp(prefix="vibonomics-test-", dir="/tmp")
    os.chmod(path, 0o755)
    yield path
    shutil.rmtree(path, ignore_errors=True)


def python(code: str, **kwargs):
    return executor.run({"main.py": textwrap.dedent(code)}, ["python", "main.py"], **kwargs)


# -- the pool ---------------------------------------------------------------------------------


def test_no_uids_are_used_unless_root_and_configured(monkeypatch):
    monkeypatch.delenv(executor.RUN_UIDS_ENV, raising=False)
    assert executor.run_uids() == []
    monkeypatch.setenv(executor.RUN_UIDS_ENV, "65530")
    assert executor.run_uids() == ([65530] if AS_ROOT else [])


def test_the_list_ignores_junk_duplicates_and_root(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setenv(executor.RUN_UIDS_ENV, "65530, 0,abc,65531,65530,-4")
    assert executor.run_uids() == [65530, 65531]


def test_claiming_waits_for_a_free_uid_and_releasing_hands_it_on(monkeypatch):
    monkeypatch.setattr(executor, "run_uids", lambda: [11, 12])
    executor.reset_run_uids()
    first, second = executor._claim_uid(), executor._claim_uid()
    assert {first, second} == {11, 12}

    got: list[int | None] = []
    waiter = threading.Thread(target=lambda: got.append(executor._claim_uid()))
    waiter.start()
    waiter.join(timeout=0.3)
    assert waiter.is_alive(), "a third claim should wait while both uids are in use"
    executor._release_uid(first)
    waiter.join(timeout=5)
    assert got == [first]
    executor.reset_run_uids()


# -- what a run can do as its own uid ---------------------------------------------------------


@needs_root
def test_a_run_starts_under_a_run_uid_with_no_groups_and_no_capabilities(run_uids):
    result = python(
        """
        import os
        status = dict(line.split(":", 1) for line in open("/proc/self/status") if ":" in line)
        ids = (os.getuid(), os.geteuid(), os.getgid(), len(os.getgroups()))
        print(*ids, status["CapEff"].strip(), status["CapPrm"].strip())
        """
    )
    assert result.exit_code == 0, result.stderr
    uid, euid, gid, groups, cap_eff, cap_prm = result.stdout.split()
    assert int(uid) in run_uids and euid == uid and gid == uid
    assert groups == "0"
    assert cap_eff == cap_prm == "0000000000000000"


@needs_root
def test_the_workspace_belongs_to_the_run_and_tests_can_write_in_it(run_uids):
    result = python(
        """
        import os
        open("made.txt", "w").write("x")
        os.makedirs("sub/dir")
        open("sub/dir/also.txt", "w").write("y")
        print(os.stat("main.py").st_uid == os.getuid())
        """
    )
    assert (result.exit_code, result.stdout.strip()) == (0, "True"), result.stderr


@needs_root
def test_a_process_that_leaves_its_session_does_not_outlive_the_run(run_uids):
    result = python(
        """
        import subprocess, sys
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"], start_new_session=True)
        print(child.pid, flush=True)
        """
    )
    pid = int(result.stdout.split()[0])
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid), "a process in its own session survived its run"


@needs_root
def test_a_process_that_forks_and_rewrites_files_stops_when_its_run_ends(run_uids, shared_dir):
    """The review's scenario: run one leaves a daemon that keeps writing; it must not outlive the run."""
    os.chmod(shared_dir, 0o777)
    marker = os.path.join(shared_dir, "ticks")
    open(marker, "w").close()
    os.chmod(marker, 0o666)
    daemon_body = f"import time\nwhile True:\n    open({marker!r}, 'a').write('x')\n    time.sleep(0.05)\n"
    python(
        f"""
        import subprocess, sys, time
        subprocess.Popen([sys.executable, "-c", {daemon_body!r}], start_new_session=True)
        time.sleep(0.5)  # long enough for the daemon to start writing, so the check below can fail
        """
    )
    size = os.path.getsize(marker)
    assert size > 0, "the daemon never wrote, so this test would pass for the wrong reason"
    time.sleep(0.5)
    assert os.path.getsize(marker) == size, "the leftover process was still writing after its run ended"


@needs_root
def test_a_run_leaves_no_directory_behind(run_uids):
    def scratch_dirs() -> set[str]:
        return {name for name in os.listdir("/tmp") if name.startswith("vibonomics-run-")}

    before = scratch_dirs()
    python('import os\nos.makedirs("a/b")\nopen("a/b/c.txt", "w").write("x")\n')
    assert scratch_dirs() == before


@needs_root
def test_files_a_run_leaves_in_tmp_are_removed_with_it(run_uids):
    python("""open("/tmp/vibonomics-left-behind", "w").write("x")""")
    assert not os.path.exists("/tmp/vibonomics-left-behind")


@needs_root
def test_a_run_cannot_read_or_write_another_runs_workspace(run_uids):
    results: dict[str, executor.ExecResult] = {}
    ready, done = "/tmp/vibonomics-first-ready", "/tmp/vibonomics-second-done"

    def first():
        # Holds its uid, with a file in its workspace, until the second run has tried.
        results["first"] = python(
            f"""
            import os, pathlib, time
            pathlib.Path("secret.txt").write_text("hidden test")
            pathlib.Path({ready!r}).write_text("x")
            deadline = time.monotonic() + 30
            while not os.path.exists({done!r}) and time.monotonic() < deadline:
                time.sleep(0.05)
            """,
            timeout=40,
        )

    thread = threading.Thread(target=first)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not os.path.exists(ready) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert os.path.exists(ready), "the first run never started"
        second = python(
            """
            import os
            out = []
            for name in os.listdir("/tmp"):
                if not name.startswith("vibonomics-run-") or os.getcwd() == "/tmp/" + name + "/workspace":
                    continue  # this run's own workspace is its to open
                for mode in ("r", "w"):
                    try:
                        open("/tmp/" + name + "/workspace/secret.txt", mode)
                        out.append(mode + " opened")
                    except PermissionError:
                        out.append(mode + " refused")
                    except OSError:
                        out.append(mode + " missing")
            print(sorted(out), os.getuid())
            """
        )
        assert second.exit_code == 0, second.stderr
        assert "opened" not in second.stdout, second.stdout
        # Refused, not just absent: the first run's directory was there, and was closed to this uid.
        assert "refused" in second.stdout, second.stdout
    finally:
        open(done, "w").close()
        thread.join(timeout=40)
        for path in (ready, done):
            if os.path.exists(path):
                os.unlink(path)
    assert results["first"].exit_code == 0


@needs_root
def test_a_run_cannot_create_or_replace_a_file_in_a_root_owned_directory(run_uids, shared_dir):
    """The socket directory is root's: nothing a run does can unlink or replace the sandbox socket."""
    socket_dir = os.path.join(shared_dir, "run")
    os.mkdir(socket_dir, 0o755)
    open(os.path.join(socket_dir, "sandbox.sock"), "w").close()
    result = python(
        f"""
        import os
        d = {socket_dir!r}
        assert os.path.isdir(d), "the directory is not reachable, so a refusal would mean nothing"
        out = []
        for action in (
            lambda: os.unlink(d + "/sandbox.sock"),
            lambda: open(d + "/other", "w"),
            lambda: os.rename(d + "/sandbox.sock", d + "/moved"),
        ):
            try:
                action()
                out.append("allowed")
            except OSError:
                out.append("refused")
        print(out)
        """
    )
    assert result.stdout.strip() == "['refused', 'refused', 'refused']", result.stdout + result.stderr
    assert os.path.exists(os.path.join(socket_dir, "sandbox.sock"))


@needs_root
def test_a_run_has_a_process_limit_of_its_own(run_uids):
    result = python("import resource\nprint(resource.getrlimit(resource.RLIMIT_NPROC))\n")
    assert result.stdout.strip() == f"({executor.RUN_NPROC}, {executor.RUN_NPROC})"


@needs_root
def test_a_fork_bomb_stops_at_the_run_limit_and_is_cleaned_up(run_uids):
    result = python(
        """
        import os, time
        made = 0
        try:
            while True:
                if os.fork() == 0:
                    time.sleep(60)
                    os._exit(0)
                made += 1
        except OSError:
            pass
        print("forks", made, flush=True)
        """,
        timeout=20,
    )
    made = int(result.stdout.split()[1])
    assert executor.RUN_NPROC - 5 <= made <= executor.RUN_NPROC
    # Nothing of those uids is left, so the next run starts with a clean count.
    survivors = subprocess.run(["pgrep", "-c", "-u", ",".join(str(u) for u in UIDS)], capture_output=True, text=True)
    assert survivors.stdout.strip() in {"0", ""}


@needs_root
def test_the_selftest_passes_when_run_uids_work(run_uids):
    assert executor.selftest() is None


def test_the_selftest_reports_a_run_that_fails(monkeypatch):
    monkeypatch.setattr(executor, "run_uids", lambda: [65530])
    failing = executor.ExecResult(None, "", "PermissionError: [Errno 1] Operation not permitted", False, 0)
    monkeypatch.setattr(executor, "run", lambda *args, **kwargs: failing)
    problem = executor.selftest()
    assert problem is not None and "Operation not permitted" in problem


def test_the_selftest_reports_a_run_that_started_under_the_wrong_uid(monkeypatch):
    monkeypatch.setattr(executor, "run_uids", lambda: [65530])
    wrong = executor.ExecResult(0, "0 0\n", "", False, 0)
    monkeypatch.setattr(executor, "run", lambda *args, **kwargs: wrong)
    problem = executor.selftest()
    assert problem is not None and "uid 0" in problem


def test_a_uid_that_would_leave_the_run_as_root_is_never_used(monkeypatch):
    # 2**32-1 is (uid_t)-1, which setuid treats as "do not change", so the run and the cleanup that kills
    # everything its uid owns would both stay root. Neither it nor anything near it may be accepted.
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setenv(executor.RUN_UIDS_ENV, "0,4294967295,4294967294,2147483648,-1,65530")
    assert executor.run_uids() == [65530]
    for bad in (0, -1, 2**32 - 1, 2**31):
        with pytest.raises(ValueError):
            executor._as_run_uid(bad, "kill", executor.Path("/nonexistent"))


def test_the_cleanup_helper_refuses_to_run_as_root():
    # The helper ends every process its uid may signal. If the switch away from root ever did not happen,
    # that would be every process in the container, so it checks before it signals anything.
    assert "os.getuid() == 0 or os.geteuid() == 0" in executor._AS_RUN_UID
    assert executor._AS_RUN_UID.index("sys.exit(3)") < executor._AS_RUN_UID.index("os.kill(-1")


@pytest.mark.skipif(os.name != "posix", reason="reads the uid")
def test_without_uids_nothing_changes(monkeypatch):
    monkeypatch.delenv(executor.RUN_UIDS_ENV, raising=False)
    executor.reset_run_uids()
    result = python("import os\nprint(os.getuid() == os.geteuid())\n")
    assert result.stdout.strip() == "True"
    assert executor.selftest() is None


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False
