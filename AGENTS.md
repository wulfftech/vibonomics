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
- **Benchmark sources** (`backend/vibonomics/benchmarks/`): one module per source, `SourceSpec` with an
  honest licence and attribution, `ScoreRecord`s with model-id hints; register in `registry.py`. Fetch
  at runtime only. Never commit fetched benchmark data; small trimmed fixtures for tests are fine.
  Artificial Analysis data stays opt-in (`terms_warning`), uses the operator's key, and must not be
  shipped or committed.
- **Matching** (`benchmarks/matching.py`): canonical ids are models.dev `canonical_model_id`s. Add
  curated aliases in `benchmarks/aliases.yaml`; operators can override them in the UI.
- **Evals** (`backend/vibonomics/evals/`): tasks are declarative `TaskDef`s. Every agentic task needs a
  reference solution that passes its visible and hidden tests (`tests/evals/test_suites.py` proves it)
  and a judge rubric. Model-written code only runs through `sandbox_client`, never `subprocess` in the
  app process. A judge failure must be reported in the run summary, not silently scored as zero.
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
  keep its contract stable too.
- **Price maths** lives twice: `frontend/src/lib/pricing.ts` for the Models page and
  `backend/vibonomics/pricing.py` for `POST /api/models/value`. Change a formula in both, and keep
  `tests/test_value.py` checking the cases `pricing.test.ts` checks.
- **Refresh** (`jobs.py`, `api/refresh.py`, `api/providers.py`): `run_job(kind, trigger, provider_ids)`
  serialises each kind behind a lock and **returns immediately if the lock is held** — it does not queue.
  So never fire one request per provider for a set: send them together (`POST /api/providers/refresh`
  with `provider_ids`), or the second call is silently dropped. A single-provider run tags its
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
