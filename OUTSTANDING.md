# Outstanding work

This file records what was checked and decided, so a settled question does not get raised again, and it is
where a new follow-up goes: what it is, how it was verified, who it affects, and what fixing it would take.

Last reviewed 2026-11-06, against `main` after the full-repository code review of 2026-11-05 was worked
through (see "Code review of 2026-11-05" below).

## Open

- **The live instance has a password but no allowed-host list.** `VIBONOMICS_PASSWORD` and
  `VIBONOMICS_OPEN_READ=1` have been set since 2026-10-29, so changes need the login. Reads are open, and
  `http://192.168.10.78:2289` is not behind Authelia, so a web page a LAN browser opens (DNS rebinding) can
  still read what a visitor can. Writes are also refused when the request comes from another site (the
  `auth_guard` cross-site check, 2026-11-05). `VIBONOMICS_ALLOWED_HOSTS` would close the read gap; it is the
  operator's decision.
- **Four concerns the second review could not confirm.** Each says what would settle it.
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
  - The Epoch archive is decompressed and parsed on the event loop, with no cap on its decompressed size.
- **Concurrent sandbox runs share `/tmp`'s 256 MB.** One run can fill it and leave the other's workspace
  unwritable, which now reads as a task left ungraded. Per-run quotas would need a tmpfs per run.
- **Terminal-Bench still replaces all of its rows when some submission files fail to parse.** Unlike
  Arena and OpenRouter, its failures are per model, not per metric, so there is no metric to keep.
- **Epoch can still lose a metric's rows.** `read_csv` returns `[]` for a run CSV missing from the archive
  while the ECI file is present, so that metric's rows are deleted and the refresh counts as `ok`. It is the
  same class as the OpenRouter sources, which now raise on an unreadable payload.
- **The code review's deferred and declined items** are listed under "Code review of 2026-11-05".

## Code review of 2026-11-05

A full-repository review at `23822cb` (about 85 findings), worked through in three waves of fixes with
the tests that reproduce each. `CODE_REVIEW.md` has been deleted; this section is where what it left lives.

**Fixed.** Wave 1 (secrets, auth, data integrity): PR-1, PR-2, PR-3, PR-8, PR-9, PR-11, PR-14, PR-15, A-1
to A-4, A-6, A-8, J-1 to J-4, J-8 to J-11, J-13, V-6, P-15. Wave 2 (correctness): the preset ownership,
write-verification, provider and churn findings P-1 to P-8 and P-10, the usage findings P-9 and P-11 to P-14,
the pricing and value findings PR-4, PR-6, PR-7, PR-10, PR-12, PR-13, V-1 to V-5, V-7, V-9, V-10, and the
benchmark and job findings J-5 to J-7, J-12, J-14, J-15, plus the event-before-commit sweep and single-flight
for the models.dev fetch. Wave 3 (frontend): F-1 to F-14 (F-3 was already fixed), PR-5, V-8, V-11.
CI-1 was fixed in the review's own PR. B-3 was already fixed on `main`.

**What the fixes taught, for the next change.**
- `get_session` commits before the response is sent (`Depends(..., scope="function")`), so a handler that
  publishes an event must commit first. Migrations run with foreign keys off and are checked afterwards.
- The `follow_redirects` concern from the second review was confirmed (PR-3) and is fixed: a cross-origin
  redirect now loses every key header (`http.drop_secrets_on_redirect`).
- The sandbox concurrency, fork-bomb and leftover-file cases did run, as root in a Linux container, and
  passed; the fork-bomb test failed there 4 of 4 times because of S-1 below.
- OpenRouter keeps `allow_fallbacks` in a preset (version 6 of the live preset held it), so the churn P-4
  described did not occur on a real account. The code stopped writing it anyway.

**Open or decided against, with the reason.**
- **S-1: the fork-bomb sandbox test races the reaper.** It checks for survivors at once, and under a slow
  PID 1 the zombies still count; the executor also frees the uid before its process count reaches 0. Passes
  with tini (the sidecar and CI). The fix is to poll in the test and wait, bounded, before `_release_uid`.
  Not done: it cannot be run on Windows.
- **CI-2: `publish.yml` does not wait for CI.** A `v*` tag ships even with `main` red. Gate it on CI.
- **CI-3: version drift.** CI tests Python 3.12 while the image runs 3.13, and `AGENTS.md` says Node 22+
  while CI and the image use 24.
- **A-5: no login rate limit, and a session cannot be revoked** except by changing the password. Accepted for
  a LAN tool.
- **A-7: `GET /api/subscription/claude` mints a token** for an authenticated caller (a GET with a write).
  Harmless today.
- **The Claude OAuth `utilization` unit** (0 to 1 or 0 to 100) was never confirmed against the live
  endpoint, so it is still rescaled by a heuristic. `used_percentage` from the status line is not rescaled.
- **`VIBONOMICS_CUSTOM_OPENAI_API_KEY` goes to every custom endpoint** on every host (`provider_service.resolve`
  applies env keys per type). A decision: key per row, or a host check.
- **`crypto.py`:** after the secret key changes, every stored key reads as "not set" with no log line.
- **PR-16: the pricing-page glue is copied in seven adapters,** and five of them discard good page prices when
  the live `/models` list fails. A shared helper would fix both; it is a refactor and was left out.
- **Presets.** A key rotation blocks the existing preset name until it is renamed; a "re-claim for this
  key" action would need a route and a button. The ownership check also compares the preset's `id` and
  `created_at`, whose behaviour on a live account is unverified: if a plan ever reads "no longer the one
  Vibonomics created", look there first. A template's provider allow-list applies only when a preset is
  created. A create whose stored config differs from what was sent (OpenRouter dropping a key) is not
  adopted after a lost answer. `pick_discounted_endpoint` still uses the model's listed cache price.
  Unverified without a second look at OpenRouter: that `provider.only` applies to every entry in `models`
  (the code assumes it does), and that `GET /keys` returns `last_used_at`.
- **Usage.** A period with only own-key (BYOK) traffic shows no cost per million tokens; the activity API
  gives no per-row token split.
- **Matching.** `guess_canonical` now returns nothing when two models tie. Against the live catalogue it
  changed 16 of 617 answers and lost none.
- **Frontend.** A draft in an open Settings section is lost when the session expires (the login card
  replaces the page); a save that hits a 401 now says so. Logout removes only the cached data nobody is
  watching and refetches the rest, so a mounted page keeps updating. The value index and tiers rank among the
  rows shown, so a search changes them (the Models page's index was already relative to the screen).
  `--mantine-color-error` (form error text, about 3.6:1 in light mode) is below AA and was left alone. The
  per-card balance spinner ends when a balance is written or after 120 s; a `refresh.finished` hook would be
  exact.
- **A source switched off mid-refresh** can reappear in the database after the settings purge. The rows
  route hides it and the next refresh deletes it.

## OpenRouter's preset API, measured on 2026-11-06

`backend/scripts/preset_probe.py` was run against a throwaway workspace (two presets, six free-model calls).
What it showed, so a settled question is not asked again:
- A write stores `model`, `models`, `provider.only/order/sort/quantizations/ignore/require_parameters`,
  `temperature`, `top_p`, `repetition_penalty`, `frequency_penalty`, `presence_penalty`, `route` and `max_tokens`.
  `max_completion_tokens` is dropped, unknown keys (top level and inside `provider`) are accepted and dropped, and
  `provider.sort` sent as a string comes back as a string (the `{by, partition}` form is what the web UI writes).
- The system prompt is stored from a system-role message. **A write with no system message sets it to null**, so every
  write resends the prompt in force. Every accepted write answers with the version it made.
- `provider.quantizations: ["fp8"]` excludes endpoints whose quantization is `unknown` (HTTP 404). With
  `require_parameters: true` an endpoint without a requested parameter is HTTP 404 "No endpoints found that can handle
  the requested parameters"; there is no fallback to another provider of that model.
- `stealth` is accepted as a `provider.only` entry (a real write, version 17 of the live preset).
- **Changed since (2026-11-07):** the same write, made on 2026-11-06 with an ordinary API key, now answers HTTP 401
  "Invalid management key" for any key that is not a management key, even for a body that fails validation. OpenRouter's
  presets guide says the create endpoints need a read-write management key. The app now writes under the OpenRouter
  provider's management key (see the README). The management key stored on the live provider was checked on
  2026-11-07: `GET /key` calls it a management key in the same workspace as the API key, and a deliberately invalid
  write under it got a validation 400 (the API key gets the 401 first). Real writes under it then went through on
  2026-11-07: `vibonomics-1` version 18 at 09:41 UTC and `vibonomics-stonks` version 2 at 11:13 UTC (applied by
  hand), both read from the live preset state.
- **Measured on 2026-11-08** (Workspace2, a fresh management key in `OPENROUTER_MANAGEMENT_KEY` and the workspace's
  API key; 41 steps, 11 writes, 6 free-model calls):
  - All 11 writes answered HTTP 200 under the management key with `?workspace_id=` set to the API key's workspace,
    and the answers named that workspace. A management key made on the key page has no read or write setting, so
    this is the kind the live provider holds.
  - **A client's own parameters win over the preset's:** a preset with `max_tokens` 3 and a request with 8 gave an
    8-token answer. (Taken from `max_tokens`; the other parameters are assumed to behave the same.)
  - **A client's `provider` block replaces the preset's whole `provider` block, it does not merge:** a preset with
    `require_parameters: true` refused its request on its own (HTTP 404), and was served once the request carried a
    `provider` block that did not mention `require_parameters`.
  - An unknown key inside `provider` is accepted and dropped too (read from the stored version, not the read-back).
  - **A read-back right after a write is often stale.** `GET /presets/{slug}` returned the previous designated
    version for 7 of the 11 writes (the old temperature, system prompt, model or `require_parameters`), and a few
    minutes later it returned the newest one for both presets. The write's own answer was right every time, and the
    stored versions (`/presets/{slug}/versions`) hold what was sent. So the probe's read-back comparisons for those
    steps (1a, 2b, 3b, 5i, 5ii, 5iii, 5v) are not evidence of what OpenRouter stores. How long the lag lasts is
    unmeasured. Both ends now allow for it: the probe repeats a read-back (waits of 1 to 5 s) until it shows the
    version the write made, reports how many reads were stale in its summary, and compares nothing from a read that
    never catches up; and the app's `_read_back` (after a lost write answer) reads up to four times, 1, 2 and 3 s
    apart (`READ_BACK_WAITS`), before it calls the write not done. The app's normal path never reads after a write:
    it uses the write's own answer. A plan made seconds after an Apply can still show the old version.
  - Not measured: a 429 from a spent free-model cap (the cap was nowhere near spent), and what `route: "sort"` does
    at request time (it is stored as sent).
- Free models share an account-wide daily request cap (the response said 1000 a day with credits). The live preset's
  models are all free, so spending the cap makes requests to it fail until the reset; plans now warn about this.
  Two per-preset options now guard against it: a paid model kept as the last fallback, and a refusal to write a
  lineup of free models only. Neither is switched on for the live preset yet. Whether OpenRouter really falls
  through from a rate-limited free model to the next one in `models` is documented but unmeasured.

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
  a non-root user. The unit tests ran as root with only `SETUID`, `SETGID` and `CHOWN` and `no-new-privileges`
  (`setpriv` in WSL). The compose file itself was then run in Docker Desktop 29.8.2:
  `docker/check-sandbox-isolation.sh` passed in both layouts (code runs, as its own unprivileged uid, cannot
  touch the socket, cannot reach the app), and the sidecar came up healthy with `user 0:0`, only `CHOWN`,
  `SETGID` and `SETUID`, `no-new-privileges`, a read-only root and no network, logging "Running as root so
  each run starts under its own uid". Not exercised there: the concurrency, fork-bomb and leftover-file
  cases, which only the WSL run covered.

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
