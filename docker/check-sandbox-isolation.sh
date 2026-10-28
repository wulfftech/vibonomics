#!/bin/sh
# Check that model-written code cannot reach the app, in both compose layouts:
#
#   socket (default)   the sandbox has no network at all; the app runs code through the Unix socket
#   TCP (override)     docker/compose.sandbox-tcp.yml: the app refuses every request from the sandbox
#
# Uses the vibonomics:local image (`docker compose build`). Run it from the repository root; it starts
# and stops the stack, so do not run it next to a live instance.
set -eu

VIBONOMICS_CONFIG="$(mktemp -d)"
export VIBONOMICS_CONFIG
unset COMPOSE_FILE
trap 'docker compose down --volumes >/dev/null 2>&1 || true' EXIT

fail() {
  echo "FAIL: $1" >&2
  docker compose ps >&2 || true
  docker compose logs --tail 30 >&2 || true
  exit 1
}

up() {
  docker compose down --volumes >/dev/null 2>&1 || true
  docker compose up -d --no-build >/dev/null 2>&1 || fail "docker compose up failed"
  for _ in $(seq 1 45); do
    [ "$(docker compose ps --format '{{.Health}}' | sort -u)" = "healthy" ] && return 0
    sleep 2
  done
  fail "the stack did not become healthy"
}

app_runs_code() {
  docker compose exec -T -u 1000:1000 vibonomics python -c '
import asyncio
from vibonomics.evals import sandbox_client
from vibonomics.settings_store import AppSettings

settings = AppSettings()
settings.evals.sandbox_mode = "sidecar"
result = asyncio.run(sandbox_client.run_code({"main.py": "print(6 * 7)"}, ["python", "main.py"], 20, settings))
assert result is not None and result.exit_code == 0 and result.stdout.strip() == "42", result
' || fail "$1: the app could not run code in the sandbox"
  echo "ok   $1: the app runs code in the sandbox"
}

# Code under test must not run as root, or as the server's own user, and must not be able to replace the
# socket the app talks to.
code_runs_unprivileged() {
  docker compose exec -T -u 1000:1000 vibonomics python -c '
import asyncio
import textwrap

from vibonomics.evals import sandbox_client
from vibonomics.settings_store import AppSettings

code = textwrap.dedent("""
    import os
    status = dict(line.split(":", 1) for line in open("/proc/self/status") if ":" in line)
    try:
        os.unlink("/run/vibonomics-sandbox/sandbox.sock")
        socket = "unlinked"
    except OSError:
        socket = "kept"
    print(os.getuid(), os.geteuid(), os.getgid(), len(os.getgroups()), status["CapEff"].strip(), socket)
""")
settings = AppSettings()
settings.evals.sandbox_mode = "sidecar"
result = asyncio.run(sandbox_client.run_code({"main.py": code}, ["python", "main.py"], 20, settings))
assert result is not None and result.exit_code == 0, result
uid, euid, gid, groups, caps, socket = result.stdout.split()
assert uid == euid == gid and int(uid) in (65530, 65531), result.stdout
assert groups == "0" and set(caps) == {"0"} and socket == "kept", result.stdout
' || fail "$1: test code does not run as an unprivileged uid of its own"
  echo "ok   $1: test code runs as its own unprivileged uid and cannot touch the socket"
}

users_reach_app() {
  docker compose exec -T vibonomics python -c '
import urllib.request
assert urllib.request.urlopen("http://127.0.0.1:2289/ping", timeout=5).read() == b"pong"
' || fail "$1: the app does not answer its own users"
  echo "ok   $1: the app still answers everyone else"
}

echo "== socket layout (default)"
up
app_runs_code socket
code_runs_unprivileged socket
app_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' vibonomics)"
docker compose exec -T sandbox python - $app_ip <<'EOF' || fail "socket: the sandbox has a network"
import socket
import sys

for host, port in [("vibonomics", 2289), *((ip, 2289) for ip in sys.argv[1:]), ("1.1.1.1", 443)]:
    try:
        socket.create_connection((host, port), timeout=3).close()
    except OSError:
        continue
    raise SystemExit(f"the sandbox reached {host}:{port}")
EOF
echo "ok   socket: the sandbox cannot reach the app, the host or the internet"
users_reach_app socket

echo "== TCP layout (docker/compose.sandbox-tcp.yml)"
# Windows' docker splits COMPOSE_FILE on ";" unless told otherwise, so a script run from Git Bash needs this.
export COMPOSE_PATH_SEPARATOR=:
export COMPOSE_FILE=docker-compose.yml:docker/compose.sandbox-tcp.yml
up
app_runs_code tcp
code_runs_unprivileged tcp
docker compose exec -T sandbox python - <<'EOF' || fail "tcp: the app served a request from the sandbox"
import urllib.error
import urllib.request

for path in ("/ping", "/api/system/status"):
    request = urllib.request.Request(f"http://vibonomics:2289{path}", headers={"X-Forwarded-For": "127.0.0.1"})
    try:
        urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            continue
        raise
    raise SystemExit(f"{path} was served to the sandbox")
EOF
echo "ok   tcp: the app refuses the sandbox, forwarded headers or not"
users_reach_app tcp

echo "sandbox isolation checks passed"
