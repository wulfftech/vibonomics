# Vibonomics — repository conventions

Read this before changing code. It is the working agreement for this repo, for people and for
automated agents alike.

## Commands

Backend (`backend/`, Python 3.12+):

```sh
.venv/Scripts/python.exe -m pytest -q                     # or .venv/bin/python on Linux
.venv/Scripts/python.exe -m ruff check vibonomics tests
.venv/Scripts/python.exe -m ruff format --check vibonomics tests
```

Frontend (`frontend/`, Node 22+):

```sh
npm run typecheck && npm run lint && npm test && npm run build
npm run gen:api          # after any change to an API router or schema
```

Docker (repo root):

```sh
docker compose up -d --build
docker compose logs -f vibonomics
```

## Conventions

- **Provider adapters** (`backend/vibonomics/providers/`): subclass `ProviderAdapter`, set a `spec`,
  implement `fetch_models` / `fetch_balance` / `chat_target` as needed, register the class in
  `registry.py`. Prices are USD per 1M tokens after discounts; `list_price` holds the pre-discount
  price. Pricing-page scraping must degrade to models.dev prices and set
  `offer.extra["pricing_page_error"]` rather than fail the refresh. No API key may appear in a
  message, a log or a response.
- **Provider config** (`api/providers.py`): secrets are encrypted (`api_key_enc`, `mgmt_key_enc`,
  `extra_key_enc`). `extra_key` is a second optional secret (Anthropic uses it for a Claude Code login
  token). Tests must use the values in the unsaved form: `POST /api/providers/test` for a draft and
  `POST /api/providers/{id}/test` with overrides for an existing provider, never the stored key alone.
  Return `*_set` and `*_hint`, never a key.
- **Config snippets** (`backend/vibonomics/snippets.py`): one builder per harness, each checked against that
  harness's docs, and none may carry a key. OpenRouter routing (`Routing`): a `:floor` or `:nitro` variant rides
  on the model id and needs nothing from a builder. Pinning a provider is a `provider` request block, so a
  builder reads `_pinned(ctx)` only where the harness documents a place for extra body fields, and is listed in
  `_CAN_PIN`; `generate` tells every other harness it can't pin. Don't add a harness to `_CAN_PIN` on a guess.
  A model with `client_restricted` set (the OpenCode Console's free tier, 403 from every other client) gets a note on
  every snippet but the allowed client's; that is advice only, and nothing here may add client headers, a proxy or
  a pass-through to get round it. The per-model data-use text (`FREE_DATA_USE` in `providers/opencode.py`) is
  transcribed from OpenCode's docs: re-check it against them before editing.
- **Best-value presets** (`presets.py`, `api/presets.py`): the only code that writes to an OpenRouter account.
  `settings.presets` is `{items: [PresetSettings], blocked_models, blocked_providers}`: at most 20 items, unique
  slugs, each with its own filters, schedule and write switch; a document stored for the single-preset release
  is migrated in `settings_store._migrate_presets` (the old preset becomes `items[0]`, an untouched default
  becomes `[]`) and a bad value in one item drops that value, not the item or its neighbours. `make_plan`,
  `apply` and `sync` take a slug; one global lock covers every write. `preset_state` is per slug:
  `owned`, `owners`, `pins`, `applied`, `last` and `reviewed` are each `{slug: ...}` (the old single `applied` /
  `last` dict is read as the record of the slug it names, in `_normalise_state`). A write goes only to a slug
  that starts with `vibonomics-` and that it created itself (`owned`), or to one the owner adopted (below),
  never to any other preset, and never deletes (the API cannot). Ownership is tied to the key and host that
  created it (`owners`: a fingerprint of both, never the key) and to the preset's id and creation time when the
  response carries them; a different key, or a preset deleted and made again, blocks the plan, and a legacy
  `owned` slug with no record is trusted once. Every preset call goes to `OpenRouterAdapter.spec.default_base_url`,
  never to the provider row's Base URL override. OpenRouter allows at most 3 models in a preset
  (`PRESET_MAX_MODELS`). `allow_fallbacks` is not written, and unchanged-or-update is decided on the owned keys
  alone (`owned_view`: model, models, route, provider only/order/sort), so a key OpenRouter does not keep cannot
  write a new version every sync; one it did not keep last time (`applied[slug].unsaved`, for `route` and
  `provider.sort`) is left out of that comparison. A write counts only when the answer carries a version and a
  config, and a lost answer is read back before it is called a failure. Saving the settings in the UI with writing
  allowed also plans from the saved settings and applies that plan. The prefix is added to the name the operator
  types (`preset_slug()`), never asked for, so it can't be left off. An archived preset takes no new versions and
  blocks the plan. OpenRouter keeps no partial update, so every write is the whole config: read the live one,
  change only the owned keys, send the rest (the passthrough: `passthrough_view`) back, then read the response
  and report the keys it dropped. `route` is `fallback` (no `route` key, and one found live is removed) or `sort`
  (`route: "sort"` and `provider.sort`); OpenRouter does not document `route`, which is why the read-back matters.
  Blocklists are case-insensitive `fnmatch` patterns, the global list and the preset's added together: models
  match the OpenRouter and canonical ids and never become candidates or incumbents; providers match an endpoint's
  tag, its part before `/` and its name, are never pinned, and take out a model whose every known endpoint they
  match (not one with no endpoint data). They narrow the search and never write `provider.ignore`; the plan
  reports `excluded`. `PresetSettings.prefer_own_tests` (None follows `scoring.prefer_own_tests`) is resolved by
  `own_tests_preferred()`, which `rank` hands to `compute_scores` and the plan reports as `own_tests_preferred`.
  **Guardrails** (`PresetSettings.guardrails: PresetGuardrails`): temperature, `max_tokens` (never
  `max_completion_tokens`, which OpenRouter drops from a preset), the three penalties, `top_p`, `quantizations`,
  `ignore`, `require_parameters` and `system_prompt`. A key is unset (not managed: the live value stays and is in
  the passthrough), a value (written) or in `clear` (taken off); both at once is refused. A guardrail key is owned
  exactly when it is set or cleared, and `owned_view`, `diff_config`, `unsaved_keys` (which also names a cleared
  key still on the preset), `passthrough_view` and `passthrough_digest` follow that. A write is the whole config
  and OpenRouter takes the system prompt from the system message of the write (none nulls it), so every write
  carries `prompt_in_force()`: the managed text, none when cleared, else the live prompt resent. The prompt is a
  pseudo-key in the owned view, in the diff (as text), in the plan digest and in the passthrough digest of an
  adopted preset (so an edit made on OpenRouter blocks an unattended run); there is no refusal for a preset that
  has one, and adopt no longer blocks on it. Before a write that replaces or removes the live prompt the old text
  goes to `preset_state.system_backups[slug]` (`text` capped at 20,000, `version`, `at`); the read-back compares
  `designated_version.system_prompt` with what was sent and reports `system_prompt` among the unsaved keys. The
  plan pins capability-aware: per pick `choose_endpoints` gets the set numeric parameters (`require_params`) and
  the cap; with none left it falls back to the endpoints without them and warns, or, when the plan's
  `provider.require_parameters` is true, blocks (`_pin_for`). Endpoint filters read the config the plan writes
  (`apply_guardrails`), not the live one, and an endpoint with no listed precision is `unknown`, which a
  quantization list excludes. `rank` leaves out a model none of whose endpoints pass the managed quantization and
  ignore lists (`Excluded.unserved`). `validate_live` takes the same requirements: an unservable model is a
  warning, an error when every model is; a pinned endpoint lacking a parameter is an error with
  `require_parameters` and a warning without. A plan of free models only warns about the daily cap
  (`FREE_TIER_WARNING`, also a `free` live check). The plan reports `guardrails`, `system_prompt`,
  `capabilities` and `system_backup_at`; inspect reports the live `system_prompt`. Changing which guardrails are
  managed changes the reviewed passthrough digest, so an adopted preset's next unattended run is blocked until a
  manual apply.
  A model already in the preset is favoured by the switch margin (`select_models`), so near
  ties do not rewrite it daily. The dry run and the apply build the plan with the same function, and apply takes
  the plan's digest and refuses a plan that has changed. `plan` may be given unsaved settings to preview
  (`draft`, which is also how a preset not saved yet is planned); apply never is, and only plans a saved item, so a
  draft's digest applies only if it equals the saved plan, and the UI does not offer it. The create call is never
  retried: a retry would leave a second version. Every plan, `apply`'s own included, ends with
  `preset_validation.validate_live`: the public `/models/<id>/endpoints` listing of each model in the config (no key,
  four at a time) against the live pins, health, price drift and expiry (`plan.validation`, outside the digest);
  an error makes a create or update `apply` record `blocked` and raise `PresetInvalid` (409; `sync` only logs it),
  a warning or a listing that cannot be read never blocks, and the adapter stores `params`, `max_completion_tokens`
  and `status` per endpoint for `choose_endpoints(require_params=, max_tokens=)`.
  **Adopting** a preset made by hand: `inspect` reads any preset on the account (config, passthrough, blockers,
  a digest of slug, version, config and system prompt); `adopt` refuses a changed digest, an archived preset, a
  preset already managed or recorded under another key, and otherwise records `owners[slug].adopted`, puts the
  slug in `owned`, stores `reviewed[slug]` (a digest of the passthrough the owner was shown) and appends an item
  with `adopted: true`, switched off, in one transaction. `adopted` in the settings grants nothing without that
  record (`is_adopted`): the plan is blocked. An adopted slug keeps its name as typed, is never created, and a
  scheduled or startup run on it writes only while the live passthrough digest equals `reviewed[slug]` (else it
  records `blocked`); a manual apply updates `reviewed`. Removing an adopted item from the settings releases it
  (`release_adopted`, called from the settings PUT). One `preset-sync:<slug>` job runs per item that is on and
  automatic, and the startup catch-up runs each item that is overdue by its own last run. The routes (`plan`,
  `apply`, `inspect`, `adopt` and `list`, which fills the template picker) are POSTs, so a visitor to an
  open-read instance cannot make them call out with the operator's key.
- **Preset catalogue** (`preset_catalog.py`, `api/preset_catalog.py`): a copy of every preset on the account
  (up to 100, archived included) in the `preset_catalog` setting: `GET {base}/presets`, then one read per preset,
  six at a time, to the same host and with the same key as the preset update. It writes nothing to OpenRouter.
  One run at a time (`single_flight`); callers who arrive during it share it. A preset whose read fails keeps its
  last entry, and the sync says `partial` in `error`; a failed list keeps the whole catalogue. `managed` is worked
  out from `preset_state.owned` each time the catalogue is read, never stored. Timer `preset-catalog-sync`
  (30 minutes, added by `scheduler.start`, so a settings save never moves it) and one run after the start-up
  work. `GET /api/presets/catalog` checks `is_authenticated` itself, because a preset's config is the owner's and
  the GET is open to a visitor otherwise. A test that runs `_startup_work` must stub `scheduler.sync_catalogue`,
  or it calls OpenRouter for real when an earlier test left a key in the database.
- **Usage history** (`usage.py`, `api/usage.py`, the `usage` refresh kind): OpenRouter's activity API needs a
  management key and returns 30 finished UTC days, so every day it returns is stored in `usage_days` and the
  history outgrows that. `key_hash` is empty for whole-account rows and a key's hash for that key's rows: sum one
  kind, never both. Tokens are prompt plus completion; reasoning is inside completion and cached is inside prompt.
  A new refresh kind goes in `jobs.KINDS`, the `Kind` literal in `api/refresh.py`, the scheduler's intervals and the
  UI's refresh labels.
- **Benchmark sources** (`backend/vibonomics/benchmarks/`): one module per source, `SourceSpec` with an
  honest licence and attribution, `ScoreRecord`s with model-id hints; register in `registry.py`. Fetch
  at runtime only. Never commit fetched benchmark data; small trimmed fixtures for tests are fine.
  Artificial Analysis data stays opt-in (`terms_warning`), uses the operator's key, and must not be
  shipped or committed.
- **Matching** (`benchmarks/matching.py`): canonical ids are models.dev `canonical_model_id`s. Add
  curated aliases in `benchmarks/aliases.yaml`; operators can override them in the UI. An alias to an id the
  catalogue does not list is refused when saved and ignored (logged once) if the catalogue loses the id later.
  With no catalogue (`degraded`), the benchmark refresh and a rematch change nothing.
- **Evals** (`backend/vibonomics/evals/`): tasks are declarative `TaskDef`s. Every agentic task needs a
  reference solution that passes its visible and hidden tests (`tests/evals/test_suites.py` proves it)
  and a judge rubric. Model-written code only runs through `sandbox_client`, never `subprocess` in the
  app process. New suites go in `suites.py`; import the suite module before a `tasks_*` module in tests (the task
  modules import `suites`, which builds the suites from them). Chat tasks can use per-line rules (`lines` in a
  check) as well as `equals`/`contains_all`/`regex`/`json`. A judge failure must be reported in the run summary,
  not silently scored as zero.
  A task's error is never a score: errored and ungraded results stay out of averages. A `429` is retried
  with `RATE_LIMIT_BACKOFF` (shared per model) before it counts as an error. The runner never calls a model a
  provider flags `client_restricted`. OpenCode Go (not the Console tier) gets its own user agent and a
  per-task `x-opencode-session` through the `conversation_id` context variable; do not extend that to the
  restricted tier or to any other client's headers.
- **Subscriptions** (`subscriptions.py`, `api/subscriptions.py`): Claude Code status-line snapshots are
  posted to `/api/subscription/ingest/claude` with an ingest token — the one `/api` path exempt from the
  login guard, because the caller has no session, so the token is compared with `hmac.compare_digest`.
  Snapshots are normalised, kept to a short history, and carry usage numbers only. The login-token usage
  endpoint (`api.anthropic.com/api/oauth/usage`) is read-only and unofficial, and must never be called
  with a refreshed token; Vibonomics does not refresh it, so Claude Code's copy stays valid.
- **API**: routers in `backend/vibonomics/api/`, schemas are pydantic models, and the frontend consumes
  generated types (`npm run gen:api`). Update the types with the router change. Keep the evals API
  contract stable; the UI is built against it. Give a schema a name no other router uses: two classes of
  one name are both renamed in the generated types. With `VIBONOMICS_OPEN_READ` a visitor may call every GET
  and the one read-only POST, so a new route that changes something is refused until someone lists it in
  `api/auth.py`; a GET that returns a secret must withhold it from a visitor (`is_authenticated`). Keep
  `tests/test_open_read.py` passing: it walks every route. `POST /api/models/value` is for programs, not the UI;
  keep its contract stable too. A route that publishes an SSE event calls `session.commit()` first
  (`get_session` commits only after the handler returns), or a client that refetches on the event reads the old state.
- **Price maths** lives twice: `frontend/src/lib/pricing.ts` for the Models page and
  `backend/vibonomics/pricing.py` for `POST /api/models/value`. Change a formula in both, and keep
  `tests/test_value.py` checking the cases `pricing.test.ts` checks.
- **Refresh** (`jobs.py`, `api/refresh.py`, `api/providers.py`): `run_job(kind, trigger, provider_ids)`
  serialises each kind behind a lock and **returns immediately if the lock is held** — a whole-kind refresh
  does not queue. A provider-scoped call is the exception: its ids are kept and run together, once, when the
  lock frees (the run in flight read the provider list before the save). Still send a set together
  (`POST /api/providers/refresh` with `provider_ids`), which answers `queued` rather than `started` when it
  has to wait. A single-provider run tags its
  `refresh.finished` event with `provider` so the UI can stay quiet; a whole-kind run does not.
- **Database**: SQLAlchemy models in `orm.py`. After a change, run
  `python -m vibonomics.migrations revision "..."` from `backend/`. Migrations run at startup.
- **Frontend**: Mantine components only, no ad-hoc CSS files (small inline styles are fine). Data
  fetching goes through TanStack Query hooks in `src/api/`. Every fetch URL is relative so the app works
  under `VIBONOMICS_URL_BASE`. Dark and light follow the OS, with a toggle. Text and semantic colours must
  clear WCAG AA in both schemes; use `tone()` in `lib/tone.ts` for coloured text.
- **Balance cards** (`components/models/`): one card per vendor, not per provider row. Anthropic's Admin
  API spend and the Claude subscription's windows describe one account; Console credit and the Go plan's
  windows describe one vendor. `BalancesStrip` merges the rows, `BalanceBody` renders a provider's
  figures, and `BalanceFooter` carries the timestamp and the per-card refresh button. A card that stands
  for two providers passes both ids to one refresh request.
- **Secrets**: encrypted with Fernet (`crypto.py`); `VIBONOMICS_*_API_KEY` environment values take
  precedence and render as locked. Never log request headers.
- **README screenshots** (`docs/screenshots/`): taken from a throwaway instance with a fresh config
  directory and no keys, so they hold public data only. No balances, no key hints, and nothing from
  Artificial Analysis or any other source whose licence forbids shipping its data. Show each source's
  attribution, and put the capture date in the README caption. Retake one when its screen changes.
- **Style**: Ruff, 120 columns. TypeScript strict, `no-explicit-any` an error. Prettier, 120 columns,
  single quotes. Conventional Commits, imperative mood, no AI attribution trailers.

## Not to commit

`config/`, `data/`, `*.db*`, `secret.key`, `.env`, `backend/vibonomics/static/`, `frontend/dist/`,
`node_modules/`, `.venv/`.

## Deployment records

The instance at `192.168.10.78` (LXC 178 on px3) is recorded in the homelab repo, not here:
`D:/Code/homelab/deploy/vibonomics/README.md`, plus its `inventory.toml` entry and estate map. Change the
app here; record a change to where or how it runs there.
