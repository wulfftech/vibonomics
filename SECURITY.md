# Security policy

## Reporting a vulnerability

Use GitHub's private vulnerability reporting on this repository (**Security → Report a
vulnerability**). If that is unavailable, open a minimal issue asking for a private channel and do not
describe the problem in it.

Include the version (`GET /api/system/status`), how Vibonomics is deployed, and the steps that reproduce
the issue. Please leave out real API keys; say which provider's key was involved instead. Expect an
acknowledgement within a few days. There is no bounty.

## What counts

- Reading, extracting or using a stored provider key without the operator's action.
- Escaping the sandbox, or reaching the app, its database or `/config` from code run inside it.
- Bypassing `VIBONOMICS_PASSWORD`, or reading another instance's data through the API.
- Server-side request forgery through provider base URLs, pricing pages, benchmark sources or snippet
  generation.
- Stored or reflected cross-site scripting in the UI, the transcript viewer or the snippet output.
- A dependency with a known exploit that is reachable from Vibonomics's code.

## How Vibonomics handles secrets

- Provider keys are encrypted at rest with Fernet (`crypto.py`). The encryption key comes from
  `VIBONOMICS_SECRET_KEY` when set, otherwise from `secret.key` in the config directory, created on first
  run with owner-only permissions.
- The API returns `*_set` flags and a last-four hint, never a key. Provider connection tests accept
  values from the unsaved form, so a new key can be checked before it is stored.
- Keys are never written to logs or to error messages; request headers are never logged.
- The subscription ingest endpoint accepts only usage snapshots and is authenticated with its own
  token, compared in constant time. It is the one `/api` path that does not require a session, because
  the caller (a status-line command) has none.

## Threat model for the sandbox

The sandbox exists because Vibonomics runs code written by a language model. It is a separate container
with a read-only root filesystem, a tmpfs for `/tmp`, no Linux capabilities, a non-root user, and limits
on memory, CPU and process count. It has no provider keys and cannot reach the app's database.

In the default compose layout it has no network at all: the app reaches it through a Unix socket on a
small in-memory volume they share, which is the only thing mounted. Code under test cannot call the
app's API, the host or the internet, so it cannot use a stored key or exfiltrate anything it reads.

The TCP layout (`docker/compose.sandbox-tcp.yml`) puts the sandbox on an internal Docker network with the
app instead, still with no route to the internet. Because the two now share a network, the app refuses
every request that arrives from it (`VIBONOMICS_SANDBOX_GUARD`, on by default). The guard compares the
local address a connection arrived on, which a client cannot forge, so `X-Forwarded-For` and similar
headers do not get round it. It turns itself off, with a warning in the log, when the sidecar's network
also carries other traffic (loopback, or the interface of the default route), because refusing it there
would lock everyone out. `docker/check-sandbox-isolation.sh` checks both layouts.

What it does not do: it is not a hardened multi-tenant sandbox. A container escape or a kernel bug
would matter, and the local `Local` sandbox mode runs model-written code in the app container with no
isolation beyond the executor's limits (time, memory, file size, open files and output size; the
number of processes is capped only by the container's `pids_limit`: 256 on the sidecar, 1024 on the app
container that **Local** runs in, and nothing outside Docker). The
executor kills a run's process group when the run ends, but a process that starts its own session
leaves that group. Do not use **Local** on a host you do not trust, and do not share one instance with
people you do not trust.

Prefer the default socket layout. If you use the TCP layout, keep the guard on, keep the sidecar on its
own internal network, and set `VIBONOMICS_SANDBOX_TOKEN` so only the app can call the sidecar (it is
empty by default).

## What is out of scope

- Wrong or outdated prices, scores or model metadata coming from a provider or benchmark. Report those
  as data bugs with the source and date, not as vulnerabilities.
- A model in a Vibonomics test producing hostile or incorrect output. It is model output running in the
  sandbox; that is the point of the sandbox.
- Anything that depends on the deployment ignoring the documented defaults: a publicly exposed instance
  with no `VIBONOMICS_PASSWORD`, a sandbox mode set to `Local` on a shared host, or `secret.key` and the
  database copied somewhere else together.

## Hardening checklist for operators

- Set `VIBONOMICS_PASSWORD`, or put the instance behind a reverse proxy that authenticates.
- Set `VIBONOMICS_SECRET_KEY` from a secret store rather than relying on the generated file.
- Keep the compose default for the sandbox (no network, reached through its socket). With the TCP
  layout, keep `VIBONOMICS_SANDBOX_GUARD` on and set `VIBONOMICS_SANDBOX_TOKEN`.
- Back up the config directory, and treat a copy of it as a copy of your keys.
- If you enable Artificial Analysis, keep that instance private (see the README on its terms).
