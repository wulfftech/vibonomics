# Outstanding work

Nothing is outstanding. This file records what was checked and decided, so a settled question does not get
raised again, and it is where a new follow-up goes: what it is, how it was verified, who it affects, and
what fixing it would take.

Last reviewed 2026-10-28, against `main` at `3ca416a`.

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
  in the name defeated the matcher; they are now curated, which should take `arena` to 180 of 460
  (39.1%) after the next rematch. Left unmatched on purpose:
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
