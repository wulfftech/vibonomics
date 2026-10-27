# Contributing to Vibonomics

Vibonomics is a single-maintainer project. Small, focused pull requests are welcome. For anything larger
than a bug fix, open an issue first and describe the approach, so we agree on the shape before you
spend time on it.

By contributing you agree that your work is licensed under the [AGPL-3.0](LICENSE), the same as the
rest of the project.

## What to read first

- [README.md](README.md) for what the app does and how the pieces fit.
- [AGENTS.md](AGENTS.md) for the conventions this codebase expects. It is written for automated agents
  but the rules apply to everyone: where provider secrets live, how pricing pages must degrade, how
  benchmark sources are licensed, and what must never reach a log or a response.
- [SECURITY.md](SECURITY.md) if your change touches keys, the sandbox, or authentication.

## Setting up

Backend (Python 3.12 or 3.13):

```sh
cd backend
python -m venv .venv
.venv\Scripts\activate            # or: source .venv/bin/activate
pip install -e ".[dev]"
python -m vibonomics                 # http://localhost:2289, config in ./config
```

Frontend (Node 22 or later):

```sh
cd frontend
npm ci
npm run dev                       # http://localhost:5173, proxies /api to :2289
```

The dev server and the backend can run at the same time. The backend serves whatever is in
`frontend/dist` (or `backend/vibonomics/static` in the image) when you open :2289.

## Before opening a pull request

Run all of it; CI runs the same commands and will fail the PR otherwise.

```sh
# backend
cd backend
.venv/Scripts/python.exe -m ruff check vibonomics tests
.venv/Scripts/python.exe -m ruff format --check vibonomics tests
.venv/Scripts/python.exe -m pytest -q

# frontend
cd frontend
npm run typecheck && npm run lint && npm test && npm run build
```

If you changed a router or a pydantic schema, run `npm run gen:api` and commit the regenerated
`frontend/src/api/schema.d.ts`. If you changed `backend/vibonomics/orm.py`, add a migration:
`python -m vibonomics.migrations revision "what changed"`.

## What a good change looks like

- **Tests come with it.** Provider adapters get a respx-mocked test and a small trimmed fixture saved
  from the real response or page. Benchmark sources get a parser test on a trimmed fixture and a
  recorded match rate. Eval tasks need a reference solution that passes their visible and hidden tests
  (`tests/evals/test_suites.py` proves it) and a judge rubric.
- **No secrets, no keys, no network in tests.** Real HTTP only in the fetchers. If a test would need a
  live key, mock it instead.
- **Nothing environment-specific.** Hostnames, ports, paths and model names belong in settings or
  environment variables, not in code. Vibonomics is published for other people to run.
- **Pricing failures degrade.** A scraping change must fall back to models.dev prices and leave
  `pricing_page_error` on the offer. Never let a page change break a refresh.
- **Licences are honest.** A new benchmark source needs its real licence and attribution in
  `SourceSpec`, and data that must stay opt-in stays opt-in.

## Adding a provider

1. Write `backend/vibonomics/providers/<name>.py` with a `ProviderAdapter` subclass and its `ProviderSpec`.
2. Implement `fetch_models` (models.dev offers, then the live list, then live prices), `fetch_balance`
   where the provider exposes one, `test_connection`, and `chat_target` if the provider is not
   OpenAI-compatible at the default base URL.
3. Register the class in `providers/registry.py`.
4. Add the provider to the README table and to `.env.example` if it takes a key.
5. Test with and without a key: browsing must work without one.

## Adding a benchmark source

1. Write `backend/vibonomics/benchmarks/<name>.py` with a `BenchmarkSource` subclass, `SourceSpec`
   (including licence and attribution) and metrics tagged `intelligence`, `coding` or `agentic`.
2. Emit `ScoreRecord`s with model-id hints and effort where the source splits by thinking level.
3. Register it in `benchmarks/registry.py`. Set `default_enabled=False` if the licence is unclear or
   the data may not be redistributed.
4. Run it once against the live catalogue and report the match rate in the PR.

## Commit messages

Conventional Commits, imperative mood, one logical change per commit:

```
feat(providers): read Requesty's per-key spend
fix(evals): keep judge failures out of the task score
docs: record the sandbox threat model
```

No AI-attribution trailers, and no secrets or model names in commit messages.

## Reporting bugs

Use the issue form. It asks for the version (Settings → General or `GET /api/system/status`), how you
run it (compose, bare Python, which OS), the provider involved, and the relevant log lines. Strip API
keys and tokens from anything you paste; if you cannot, describe the shape of the error instead.

Security issues go through the private channel in [SECURITY.md](SECURITY.md), not the issue tracker.

## House style

Ruff with a 120-column limit; TypeScript strict with `no-explicit-any` as an error; Prettier at 120
columns with single quotes. Mantine components only, no ad-hoc CSS files. Data fetching goes through
the TanStack Query hooks in `frontend/src/api/`. Every page has loading, empty and error states.
