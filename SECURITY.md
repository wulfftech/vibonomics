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
on an internal-only Docker network, with no volumes, a read-only root filesystem, a tmpfs for `/tmp`,
no Linux capabilities, a non-root user, and limits on memory, CPU and process count. It has no
provider keys and cannot reach the app's database. It also has no route to the internet, so a test
cannot exfiltrate anything it reads.

What it does not do: it is not a hardened multi-tenant sandbox. A container escape or a kernel bug
would matter, and the local `Local` sandbox mode runs model-written code in the app container with no
isolation beyond the executor's limits. Do not use **Local** on a host you do not trust, and do not
share one instance with people you do not trust.

Set `VIBONOMICS_SANDBOX_TOKEN` so only the app can call the sidecar, and keep the sidecar on the internal
network. Both are the defaults in `docker-compose.yml`.

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
- Keep `VIBONOMICS_SANDBOX_URL` pointed at the sidecar, with `VIBONOMICS_SANDBOX_TOKEN` set.
- Back up the config directory, and treat a copy of it as a copy of your keys.
- If you enable Artificial Analysis, keep that instance private (see the README on its terms).
