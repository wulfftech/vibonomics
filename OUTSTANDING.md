# Outstanding work

Known follow-ups after the 2026-10-27/28 review fixes. Each entry says what it is, how it was
verified, who it affects, and what fixing it would take. Nothing here blocks normal use of the
instance at `192.168.10.78`.

Last reviewed 2026-10-28, against `main` at `efe7e10`.

## 1. Local sandbox mode has no process cap, and leaks zombies

**Verified.** In `docker-compose.yml` the `sandbox` service has `init: true` and `pids_limit: 256`;
the `vibonomics` service has neither. The executor sets no `RLIMIT_NPROC` — removed deliberately,
because that limit counts every process the uid owns across the host, so a low value fails ordinary
code on a busy machine.

**Impact.** **Local** sandbox mode (documented development-only) therefore has no cap on how many
processes model-written code can start. Because the app is PID 1 in its container and nothing reaps
there, processes a run orphans stay as zombies until the container restarts. The sidecar is
unaffected — its `pids_limit` bounds it and its `init` reaps it.

**Fix.** Add `init: true` to the `vibonomics` service, and optionally a generous `pids_limit`. Neither
changes behaviour in the default sidecar layout.

## 2. The sandbox guard stops retrying after five minutes

**Verified.** `backend/vibonomics/sandbox_guard.py` sets `_RETRIES = 60` and `_RETRY_SECONDS = 5`.
`keep_trying()` therefore gives up after 300 s and logs "the sandbox guard off: the sidecar's name
never resolved", leaving `_refused` empty.

**Impact.** Only the opt-in TCP layout (`docker/compose.sandbox-tcp.yml`), which is the one that has a
shared network to guard. If the sidecar starts more than five minutes after the app — a slow first
build on a constrained host is the realistic case — the app serves sandbox-originated requests
unguarded until the next restart, with only a log warning. The default socket layout has no network
to guard and is unaffected.

**Fix.** Retry with a longer backoff instead of stopping, or fail closed (keep refusing the sandbox
network until the address is known). Failing closed is the safer default but risks locking users out,
which is why the guard currently errs the other way.

## 3. A curated alias is now dead weight

**Verified.** `backend/vibonomics/benchmarks/aliases.yaml` maps both `"GPT-4o (Nov 2024)"` and
`"gpt-4o-2024-11-20"` to `openai/gpt-4o-2024-11-20`. A full-dated name now resolves to its exact
canonical id before any alias is consulted, so the second entry never fires.

**Impact.** None on behaviour — an unused alias is harmless. It is misleading to whoever maintains the
file next, because it implies the date would not otherwise match.

**Fix.** Drop the `"gpt-4o-2024-11-20"` line once a benchmarks refresh confirms the same match without
it. Low priority.

## 4. The benchmark unmatched rate is worth a look

**Verified on the live instance** after `rematch_scores`: `arena` 389/1030 rows unmatched (37.8%),
`epoch` 93/313 (29.7%), `terminal-bench` 0/21. The counts were identical before and after the rematch,
so this predates the matching changes and is not a regression from them.

**Impact.** Unmatched rows carry no canonical id and so contribute nothing to a model's score. This is
not a defect — a benchmark name that maps to no catalogue model is simply unmapped — but 37.8% is high
enough to be worth a look for names that are mappable but not yet covered by an alias.

**Fix.** Review `GET /api/benchmarks/rows?unmatched_only=true` and add curated aliases where the
mapping is obvious. Do this with the source's licence and attribution in mind, as usual.

## Not outstanding (checked, so they do not get re-raised)

- **CRLF in a Windows checkout.** `.gitattributes` now pins LF for every file type
  `npm run format:check` reads (`907671c`), and `npm run format:check` passes on this clone. The
  committed blobs were already LF, so the commit changed no content. The attribute only governs the
  next checkout: a clone that already has CRLF files needs `rm <file> && git checkout -- <file>` once
  per file (find them with `git ls-files --eol | grep w/crlf`). `.env.example` is not pinned and still
  checks out as CRLF under `core.autocrlf=true`.
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
