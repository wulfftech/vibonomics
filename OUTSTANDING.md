# Outstanding work

This file records what was checked and decided, so a settled question does not get raised again, and it is
where a new follow-up goes: what it is, how it was verified, who it affects, and what fixing it would take.

Last reviewed 2026-10-28, against `main` at `96384c3`.

## Open

- **The sandbox's run uids have not run in Docker yet.** They were tested as root with only `SETUID`,
  `SETGID` and `CHOWN` and `no-new-privileges` (`setpriv` in WSL, which is the capability set the compose
  file gives the sidecar), and the unit tests pass there. The compose file itself (`entrypoint`, `user: 0:0`,
  the root-owned socket volume, the read-only root with a tmpfs `/tmp`) was never started, because the
  Docker engine would not start on the machine the work was done on. Before deploying it, run
  `docker/check-sandbox-isolation.sh` from the repository root on a machine with Docker, and look at
  `docker compose logs sandbox` for "Running as root so each run starts under its own uid". The server
  refuses to start, with a critical log line, if the uids do not work.
- **The live instance has no password and no allowed-host list** (`/api/auth/status` answers
  `enabled: false`). The HTTPS name is behind Authelia, but `http://192.168.10.78:2289` is not, so anything
  on the LAN, and any web page a LAN browser opens (DNS rebinding), can use the whole API. The code now
  supports `VIBONOMICS_ALLOWED_HOSTS` and withholds saved keys from a different host, but neither is set
  there: that is a decision for the operator (a password, the allowed hosts, or the port reachable only from
  the proxy).
- **Five concerns the second review could not confirm.** Each says what would settle it.
  - TCP layout only: code in the sandbox might reach the app through the internal network's gateway address
    and the published port, which arrives on an address the guard does not refuse. Run
    `curl http://<sandboxnet gateway>:2289/ping` from the sidecar. The default socket layout, which is what
    runs live, has no network to reach it from.
  - Eval concurrency can be 8 while the sidecar runs 2 at a time and the client waits `test_timeout + 20`
    seconds, so with three or more slow or looping solutions queued requests may time out and be left
    ungraded instead of scoring 0. Run concurrency 8 with looping solutions.
  - Synchronous SQLite writes (`ingest_claude`, the eval runner's progress and result writes) run on the
    event loop and may stall every request while a refresh holds the write lock. Time `/ping` during a large
    `persist_scores`.
  - `follow_redirects=True` on both HTTP clients: httpx drops only `Authorization` on a cross-origin
    redirect, so `x-api-key` and `x-goog-api-key` would follow one.
  - The Epoch archive is decompressed and parsed on the event loop, with no cap on its decompressed size.
- **Concurrent sandbox runs share `/tmp`'s 256 MB.** One run can fill it and leave the other's workspace
  unwritable, which now reads as a task left ungraded. Per-run quotas would need a tmpfs per run.
- **Terminal-Bench still replaces all of its rows when some submission files fail to parse.** Unlike
  Arena and OpenRouter, its failures are per model, not per metric, so there is no metric to keep.

## Fixed on 2026-10-28, second pass (an independent review of the first)

An Opus review re-read the earlier fixes and the code around them. These are what it found, with the commit
that fixed each. Findings are numbered as in that review.

- **Grading trusted what the code under test controls** (F1, F2; `0b40fef`). `exit 0` scored 100, so
  `os._exit(0)` at import, or a workspace `unittest.py`, passed any task. Graded runs now go through
  `evals/unittest_harness.py` and a per-run nonce; a run without its report scores 0. A workspace that
  cannot be laid out is the submitted work's fault (score 0 with the reason), not an outage (ungraded).
  What this does not stop: code that reads the process arguments and prints the nonce. It stops accident
  and the obvious tricks, not a determined forger in the same process.
- **Refresh jobs** (F4, F5, F11, F12, F14, F16; `8c40de5`). A provider failing while saving no longer fails
  the others or frees the job lock early; an alias saved during a refresh is neither lost nor applied to the
  wrong row (rematch updates by natural key; `persist_scores` reads aliases inside its transaction); a
  source that loads only some leaderboards keeps the rest and reports partial; timeouts say so; unpriced
  models stop adding a price-history row per refresh; runs cut off by a restart are marked failed.
- **The API's open doors** (F6, F7, F8, F9, F10, F13; `861fcba`). `VIBONOMICS_ALLOWED_HOSTS`; saved keys are
  not sent to a different host; the ingest token is checked before the body is read, and the body is capped
  while streaming; the eval client scrubs echoed keys; a session ends when the password changes; compose pins
  the container port; `/ping` exists under a URL base.
- **Smaller ones** (F15, F17, F18; `acf5847`). A curated alias to a model the catalogue lacks no longer counts
  as a match; the model details Tests tab shows a dash for ungraded results; the transcript trim takes effect;
  `.env.example` is pinned to LF.
- **Model code ran as the sidecar's own user** (F3; `96384c3`). A process that started
  its own session survived its run and could rewrite later runs' workspaces, hidden tests included, or
  replace the socket. The sidecar now runs as root with three capabilities and starts each run under one of
  two unprivileged uids (`VIBONOMICS_SANDBOX_RUN_UIDS`), kills by uid and removes the uid's files when the
  run ends, closes each workspace to the other uid, and gives each run its own process limit. The socket
  volume is root's. The README and SECURITY.md describe the new shape, including that the server is no longer
  a non-root user.

## Fixed on 2026-10-28, with what to know

- **Local sandbox mode had no process cap and leaked zombies** (`34f97f9`). The `vibonomics` service now
  has `init: true` and `pids_limit: 1024`, so the processes a **Local** run orphans are reaped and a run
  cannot start more than the cap. The cap counts threads too, which is why it is far looser than the
  sidecar's 256. The executor still sets no `RLIMIT_NPROC`: that limit counts every process the uid owns
  across the host, so a low value fails ordinary code on a busy machine. A process that starts its own
  session still leaves the run's process group.
- **The sandbox guard gave up after five minutes** (`94a6305`). `keep_trying()` now retries for as long as
  the sidecar's name does not resolve, backing off from 5 s to a ceiling of 60 s, and warns once after
  five minutes that the guard is still off. It still fails open until the name resolves. Failing closed
  would refuse everyone's requests while a slow sidecar builds, and only the opt-in TCP layout
  (`docker/compose.sandbox-tcp.yml`) has a network to guard at all.
- **A curated alias that never fired** (`3ca416a`). `"gpt-4o-2024-11-20"` in the `epoch` aliases is gone.
  With it removed, the live models.dev catalogue still resolves that name to `openai/gpt-4o-2024-11-20`
  by name, because a full-dated name reaches its exact id before any alias is consulted.
- **The benchmark unmatched rate** (`3ca416a`). Counted by model name on the live instance, `arena` had
  187 of 460 unmatched (40.7%), `epoch` 91 of 312 (29.2%) and `terminal-bench` 0 of 21. The earlier 37.8%
  and 29.7% counted score rows, not models. 256 of the 278 unmatched names have no counterpart in
  models.dev at all, mostly open models from before 2025, so most of this rate is catalogue coverage and
  not a matcher defect. Seven arena names were the same release as a catalogue id and only a qualifier
  in the name defeated the matcher; they are now curated, which took `arena` to 180 of 460
  (39.1%); confirmed on the live instance after the rematch that followed the `7a10050` deploy. Left
  unmatched on purpose:
  - Previews that are not the released model: `QwQ-32B-Preview`, `Qwen3.7-Max-Preview`,
    `MiniMax-M2.1 Preview`, the DeepSeek V4 previews, both `GPT-4 Turbo Preview` snapshots,
    `Gemini 2.0 Flash-Lite Preview (02-05)` and `Gemini 2.5 Flash Preview (09-2025)` (the catalogue
    has only the 05-20 preview and the release).
  - Names for an older model than anything the catalogue lists under a similar name: `Gemini Pro` (the
    2023 model, not `gemini-pro-latest`), `Mercury`, `Mistral 7B Instruct` (the catalogue has only v0.3),
    `Step-3`, `GLM-4 (0520)`, `Reka Flash (20240904)`.
  - Names that could mean more than one catalogue model: `Muse Spark` (1.1, 1.2, 1.3 or open), `Grok 4.1`
    (the catalogue lists only the fast variants), epoch's `Grok 4 Fast` (reasoning or not), and any
    `Grok 4`, `Phi-4` or `Qwen2.5-Coder` size the catalogue does not list.

  Revisit a name when models.dev adds the model it should map to.

## Not outstanding (checked, so they do not get re-raised)

- **CRLF in a Windows checkout.** `.gitattributes` now pins LF for every file type
  `npm run format:check` reads (`907671c`), and `npm run format:check` passes on this clone. The
  committed blobs were already LF, so the commit changed no content. The attribute only governs the
  next checkout: a clone that already has CRLF files needs `rm <file> && git checkout -- <file>` once
  per file (find them with `git ls-files --eol | grep w/crlf`). `.env.example` is pinned as well now
  (`acf5847`), so a fresh checkout is LF.
- **Three socket tests cannot pass on Windows.** `tests/sandbox/test_isolation.py` fails
  `test_code_runs_through_the_socket`, `test_healthcheck_uses_the_socket` and
  `test_healthcheck_fails_without_a_sidecar` with `AttributeError: module 'socket' has no attribute
  'AF_UNIX'`. Windows Python has no Unix-domain sockets, so this is a platform limit, not a defect:
  verified on a clean tree at `efe7e10` with the docs changes stashed, and the same three fail. CI on
  Linux passes. Run the suite in Docker or WSL to cover them locally.
- **`backend/.venv/pyvenv.cfg` still names the old checkout.** Its `command` line reads
  `... -m venv D:\Code\catwalk\backend\.venv`, from before the rename. Harmless — `home`, `executable`
  and the editable `.pth` all point at the right places, and imports resolve from
  `D:\Code\vibonomics\backend`. Recreating the venv clears it; nothing depends on the line.
- **Eval rows from before the ungraded fix.** The live database has **0 eval runs**, so there is no
  historical `failed`/`0.0` data to reinterpret.
- **Stale Artificial Analysis hints in `extra.model_ids`.** Artificial Analysis is **disabled** on this
  instance (`aa_terms_acknowledged: false`, no source configured), so no AA rows exist to rematch.
- **An `ungraded` status needs no migration.** `EvalResult.status` is `String(20)` and
  `EvalResultOut.status` is a plain `str`, so the new value fits both without a schema change.
- **The review PRs lost their per-fix history.** They were rebase-merged, so each of the 16 fixes in
  the largest PR is its own commit on `main` and can be reverted alone.
- **Two `style(tests):` commits** (`4636b98`, `e8452e6`) exist only to restore blank-line spacing after
  conflict resolution during the rebase merge. Cosmetic history noise, not real changes.
