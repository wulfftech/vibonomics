# Vibonomics

A self-hosted dashboard of LLM model prices, published scores and per-provider balances. It also
runs its own sandboxed tests against models that nobody has scored, so a free or brand-new model can
be compared with the paid ones next to it.

Vibonomics reads from providers and leaderboards; it does not sit in front of your inference traffic.
It stores everything in one SQLite file and sends nothing anywhere except the requests you enable.
Two containers: the app, and a sandbox sidecar that runs model-written code with no secrets and no
network.

**Status: 0.1.0, pre-1.0.** The UI and most of the API are settled; the database schema and a few
response shapes may still change between releases. There is one maintainer, and the app has been run
on Windows (development) and Linux (Docker) only.

## What it does

- **Prices from the source.** Every model from each provider you enable, with input, output and cache
  prices per 1M tokens. Prices come from provider APIs where one exists, otherwise from the official
  pricing page, and fall back to the models.dev catalogue when a page cannot be read. The refresh job
  records the failure instead of silently showing stale numbers.
- **Discounts and price changes.** Discounts are detected where a provider publishes them: OpenRouter's
  per-endpoint discount and `:free` models, Requesty's discount field, and strike-through prices or
  "50% off" notes on the OpenAI, Anthropic, Google, xAI, Mistral and OpenCode pages. A model's price
  history is kept, so a model that is new, discounted, or newly cheaper is marked as such.
- **Balances where they exist.** Credit left (DeepSeek, Moonshot, Chutes), a prepaid balance from a
  management key (OpenRouter, xAI, Requesty), spend from an admin API (OpenAI, Anthropic orgs), Claude
  subscription windows, or a stated "not exposed by the API" with a link to the console. One card per
  vendor, each with its own refresh button that re-fetches only that vendor's providers.
- **Scores from named sources.** Terminal-Bench 4.0, Arena (text, Code Arena and Agent Arena) and the
  Epoch AI hub are on by default. OpenRouter's datasets, LiveBench and Artificial Analysis are opt-in.
  Each number keeps its source, licence and date.
- **One cost figure.** A blended $/1M price and an estimated $/task for a workload profile you pick
  (agentic coding with cache hits by default). A value index ranks score per dollar, and free models
  are costed at a configurable stand-in price so "free" alone does not top the list.
- **A sortable table.** Search, quick filters (free, best value, new, discounted, scored, tool calling,
  testable), grouping by provider, creator, model, price band or value tier, and copy-ready model IDs.
  Where several providers sell the same model, the canonical model id groups them. OpenCode's two
  catalogues are folded: a model both Console and Go list appears once, marked **also on Go**.
- **Your own tests.** Pick models, pick a judge model, and run agentic coding, tool-use,
  instruction-following and accuracy tasks. Coding tasks run in the sandbox and are graded by real unit
  tests; the judge scores code quality and writes a short assessment per model.
- **Config snippets.** For each model, a copyable setup for VS Code (Copilot Chat custom endpoint,
  Continue, Cline, Roo Code, Kilo Code), OpenCode, Claude Code, Codex CLI, Aider, Zed, Crush, Goose,
  curl and the OpenAI Python SDK. Keys are referenced through environment variables or placeholders.

## Quick start

Requirements: Docker with Compose v2.24 or later, and about 1 GB of RAM free (more for a rebuild).

```sh
git clone https://github.com/wulfftech/vibonomics.git
cd vibonomics
cp .env.example .env     # optional; the defaults work as they are
docker compose up -d --build
```

Tagged releases are published to `ghcr.io/wulfftech/vibonomics`, but compose from a checkout is the
supported way to run it: the sandbox sidecar is part of the stack.

Then open `http://localhost:2289`.

The first start loads public catalogues; OpenRouter and the OpenCode Console show models without a
key. Add keys under **Settings → Providers**, press **Test** in the provider form (it tests what you
typed, before saving), then **Save**. The provider form links to where each key is issued.

What lives where:

| Path | Holds |
|---|---|
| `./config` (bind mount, `/config` in the container) | SQLite database, price history, `secret.key`, HTTP cache |
| `.env` | optional settings and provider keys (see [Configuration](#configuration)) |

Back up `./config`. It holds your encrypted keys, and the key file sits next to the database unless
you set `VIBONOMICS_SECRET_KEY` from your own secret store.

## Providers

A key is optional for browsing: prices and models still load from models.dev and public pricing pages.
A key adds the live model list, whatever balance the provider exposes, and the ability to run tests.

| Type | Live prices without a key | Balance |
|---|---|---|
| OpenRouter | yes (public list, per-endpoint discounts, `:free`) | key spend and limits with a normal key; account credits need a management key |
| OpenCode Console / Go | yes (public list; models.dev prices) | Go plan usage windows with the workspace key (rolling, weekly, monthly); Console credits are console-only |
| Anthropic | yes (pricing page) | no prepaid balance or credit-balance API; an org Admin key reports month-to-date and 30-day API spend; a Claude Code login token adds subscription usage |
| OpenAI | yes (pricing page) | no balance; an org Admin key reports 30-day spend |
| Google Gemini | yes (pricing page, promos, free-tier flags) | none (AI Studio only) |
| DeepSeek | yes (pricing page, peak and off-peak) | account balance |
| xAI | yes (pricing page) | prepaid credit, with a management key |
| Mistral | yes (pricing page) | none (enterprise admin API only) |
| Groq | yes (docs table) | none |
| Moonshot (Kimi) | catalogue only; the price page needs a browser | account balance |
| Z.ai (GLM) | yes (pricing page) | none |
| Together, Fireworks | yes | usage, where the account allows it |
| Cerebras | yes (public API, includes prices) | none |
| Requesty | yes (public API, includes discounts) | org balance, with a manage-scoped key |
| Chutes | yes (public API, includes prices) | account balance |
| NVIDIA NIM | free trial catalogue | none |
| Custom (OpenAI-compatible) | your endpoint, or models.dev prices | none |

Keys set as environment variables (`VIBONOMICS_OPENROUTER_API_KEY` and so on) take precedence and show
as locked in the UI. `/api/providers/types` lists every type with its key URL and balance support.
Compose passes everything in `.env` to the app, so keys can live there instead of in the database.

## Scores and their sources

Every metric is converted to a percentile inside its own leaderboard, which makes Elo ratings, pass
rates and indices comparable. Available percentiles form an intelligence, coding and agentic subtotal,
and those form a weighted composite. The weights are in **Settings → Scoring**.

| Source | Default | Licence | What it contributes |
|---|---|---|---|
| Terminal-Bench 4.0 | on | Apache-2.0 | agentic coding accuracy, cost and tokens per trial |
| Arena (LMArena) | on | CC BY 4.0 | text overall and coding, Code Arena, Agent Arena |
| Epoch AI Benchmarking Hub | on | CC BY 4.0 for Epoch's own data; `*_external.csv` files keep theirs | capability index, Epoch's SWE-bench Verified runs, MirrorCode |
| OpenRouter datasets | on, needs an OpenRouter key | CC BY 4.0 | OpenRouter's own evals, median agent session cost, programming usage share |
| LiveBench 2.0 | off | not stated for the data | `long_agentic`, `coding`, `reasoning`, cost per task |
| Artificial Analysis (direct) | off | Artificial Analysis terms | intelligence, coding and agentic indices |
| Artificial Analysis and Design Arena via OpenRouter | off | republished third-party data | the same indices without an AA key |

**Artificial Analysis is opt-in on purpose.** Its terms restrict using its data in products for model
selection or comparison and require attribution. Vibonomics fetches it only when an operator enables the
source and accepts the notice in Settings, with that operator's own key. No AA data is shipped with the
app, and none is committed to the repository. Leave it off on a shared or public deployment. This is
not legal advice.

Attribution for every enabled source appears in the table footer and in each model's detail view. The
matching between a source's model name and a canonical model is reviewed in **Settings → Benchmarks →
Review unmatched**; corrections are stored as aliases.

## Cost, value and the numbers you see

- **Blended $/1M** weights input and output for the selected profile. The default profile is agentic
  coding: 20 input tokens per output token, 80% cache hits, 600k input and 20k output per task. Balanced
  3:1 and output-heavy presets are included, and you can add your own.
- **$/task** comes from the measured average when you have run Vibonomics's tests, then from a benchmark
  that reports its own cost per task, then from the profile's token counts. The column marks which one
  it is.
- **Value** is composite score per $/task, shown as a 0–100 index on a log scale. Free models use the
  stand-in price in **Settings → Scoring** (default $0.01 per 1M) so they get a finite value. Set it to
  0 to restore the older behaviour, where any free model sorted first.
- **Discount column** shows the percentage; the blended price shows the list price in brackets.

## Testing models

Tests make real API calls on the account behind the selected key. Free models cost nothing; a paid
judge costs a few tenths of a cent per task.

Two suites ship: **Quick** (seven tasks) for triage and **Standard** (fourteen) for a clearer ranking.

| Task family | How it is graded |
|---|---|
| Agentic coding | the model edits a small Python workspace through tools, then the task's unit tests run in the sandbox; hidden tests and the judge's code-quality score are blended with the pass rate |
| Tool use | exact tool name and arguments, a two-call sequence, and a case where calling a tool is wrong |
| Instruction following | output format checked exactly (JSON schema, word limits) |
| Accuracy | answers taken from a supplied passage, plus a trap where the answer is absent, to catch invention |

The judge model administers the tasks: it scores code quality against the task's rubric and writes the
per-model assessment. Models Vibonomics cannot call (free OpenCode models, OpenCode's Gemini route) are
not offered as judges, and free models are labelled because they rate-limit often. A run with a broken
judge still completes, scored by the deterministic checks, and says so.

**Sandbox.** Model-written code never runs beside your keys. The compose file starts `vibonomics-sandbox`
with no network at all, a read-only filesystem, no capabilities, a non-root user and limits on CPU, memory
and processes. The app reaches it through a Unix socket on a small in-memory volume they share, so code
under test cannot call the app, the host or the internet.

If your Docker host cannot share a Unix socket between containers, add the TCP layout in `.env`:
`COMPOSE_FILE=docker-compose.yml:docker/compose.sandbox-tcp.yml` (separate the files with `;` on Windows).
The sandbox then sits on an internal network with the app, still with no route to the internet, and the
app refuses every request that arrives from that network (`VIBONOMICS_SANDBOX_GUARD`). The guard works
from the local address a connection arrives on, so a forwarded header cannot get round it, and it turns
itself off with a warning if the sandbox's network also carries other traffic. To check either layout,
run `docker/check-sandbox-isolation.sh` (it starts and stops the stack).

Set the mode in **Settings → Testing**:

- **Auto** (default) uses the sidecar when `VIBONOMICS_SANDBOX_URL` is set, otherwise disables execution.
- **Local** runs code inside the app container. Development only.
- **Disabled** grades coding tasks from the transcript, with no tests executed.

## Claude subscription usage

Pro and Max subscriptions have no dollar balance. Usage is a rolling five-hour window and a weekly
window, and Claude Code cloud sessions count against the same windows; usage credits (optional
overage) are the only money-based figure.

The Models page shows one **Anthropic (Claude)** card for the whole account: the Admin API's
month-to-date spend, then the subscription's plan windows and session figures when Claude Code is
posting them. The two used to be separate cards, which read as if the subscription were another vendor.

- **Live windows.** A status line is a Claude Code setting, not a Vibonomics one: Claude Code runs the
  command you configure every time it redraws its footer, with the session's JSON on stdin, and prints
  whatever the command returns. That JSON carries the rate-limit windows. In **Settings → Claude**, copy
  the generated `statusLine` snippet into your user settings file and merge the key into whatever is
  already there:

  | OS | File |
  |---|---|
  | Windows | `C:\Users\<you>\.claude\settings.json` |
  | macOS | `/Users/<you>/.claude/settings.json` |
  | Linux | `/home/<you>/.claude/settings.json` |

  `/statusline` in a session also writes it, if you would rather describe the command than edit JSON. The
  same snapshot carries the session's estimated cost, context-window use and cache hit ratio, which is what
  the card shows between window resets. The command needs a POSIX shell (Git Bash on Windows, which Claude
  Code requires anyway), carries an ingest token, can only submit usage snapshots, and the token can be
  rotated on that page.
- **Usage credits.** Add your Claude Code login token under **Providers → Anthropic → Claude Code login
  token**. Vibonomics calls `api.anthropic.com/api/oauth/usage` read-only. Use the `accessToken` from
  `~/.claude/.credentials.json` after an interactive login; `claude setup-token` tokens lack the profile
  scope. Vibonomics never refreshes the token, so Claude Code's copy stays valid. The endpoint is not
  documented for third parties and may change.
- **Org seats.** Anthropic's Admin API has a daily Claude Code usage report
  (`/v1/organizations/usage_report/claude_code`) with per-user tokens and estimated cost. It reports Team
  and Enterprise seats only, so it returns nothing for an individual Pro/Max plan; Vibonomics leaves it
  uncalled. The status line covers those plans.

OpenCode Go is the other subscription with usable numbers. Its workspace key reads
`opencode.ai/zen/go/v1/usage`, which returns the rolling, weekly and monthly percentages of the plan's
included allowance. OpenCode documents no usage API, so the card labels the figures as unofficial. Console
(pay-as-you-go) credits stay console-only: that endpoint wants a browser session cookie, not an API key.

Console and Go are one vendor with two billing models, so they share a card on the Models page: the Go
plan's windows sit above a line saying Console credit is only visible in the console. They stay two
providers underneath, because their catalogues differ (Go covers a subset of Console's models) and each
needs its own key. Where both list the same model at the same per-token rate, the table shows one row
marked **also on Go** rather than the same model twice.

## Refreshing

The **Refresh** menu on the Models page runs a whole kind — models and prices, balances, or benchmarks —
across every enabled provider. Each balance card also has its own refresh button, which re-fetches only
that card's providers: Anthropic's card refreshes Anthropic, OpenCode's refreshes both Console and Go,
and a provider with its own card refreshes itself.

A card sends one request for its whole set (`POST /api/providers/refresh` with `provider_ids`), because
the refresh jobs are serialised per kind: two concurrent single-provider calls would leave the second
waiting on the lock and silently do nothing. `POST /api/providers/{id}/refresh` remains for a single
provider and delegates to the same path. A single-provider run is quiet unless it fails; the card's own
spinner and timestamp are the feedback.

## Configuration

Environment variables for the app and the compose stack:

| Variable | Default | Purpose |
|---|---|---|
| `VIBONOMICS_CONFIG_DIR` | `./config` | database, key file, cache |
| `VIBONOMICS_PORT`, `VIBONOMICS_HOST` | `2289`, `0.0.0.0` | listen address |
| `VIBONOMICS_URL_BASE` | empty | serve under a sub-path such as `/vibonomics` |
| `VIBONOMICS_PASSWORD` | empty | password gate on the UI (signed cookie session) |
| `VIBONOMICS_SECRET_KEY` | generated file | encrypts stored provider keys and signs sessions |
| `VIBONOMICS_SANDBOX_URL` | empty | sidecar address: `unix:///run/vibonomics-sandbox/sandbox.sock` (compose default) or `http://sandbox:2290` |
| `VIBONOMICS_SANDBOX_SOCKET` | empty | on the sidecar: serve on this Unix socket instead of TCP port `VIBONOMICS_SANDBOX_PORT` (`2290`) |
| `VIBONOMICS_SANDBOX_GUARD` | `on` | refuse requests that reach the app from a TCP sidecar's network |
| `VIBONOMICS_SANDBOX_TOKEN` | empty | shared token between app and sidecar |
| `VIBONOMICS_STATIC_DIR` | packaged UI | override the built frontend location |
| `VIBONOMICS_LOG_LEVEL` | `info` | `debug`, `info`, `warning` |
| `PUID`, `PGID`, `UMASK`, `TZ` | `1000`, `1000`, `002`, `Etc/UTC` | file ownership and time zone inside the container |

Provider secrets: `VIBONOMICS_<TYPE>_API_KEY`, `VIBONOMICS_<TYPE>_MGMT_KEY` and `VIBONOMICS_<TYPE>_EXTRA_KEY`,
where `<TYPE>` is the provider type in upper case with dashes as underscores (`VIBONOMICS_XAI_API_KEY`,
`VIBONOMICS_OPENCODE_GO_API_KEY`). Compose loads `.env` into the app container, so any variable listed
there works without changing the compose file. Refresh intervals, highlight windows, scoring weights,
usage profiles, judge defaults and benchmark sources live in Settings and are stored in the database.

Behind a reverse proxy, set `VIBONOMICS_URL_BASE` to the sub-path and forward `X-Forwarded-Proto` and
`X-Forwarded-For`. The app reads both. Put your proxy's own authentication in front of it if you want
more than `VIBONOMICS_PASSWORD`; Vibonomics has no user accounts.

Proxy authentication and the status line do not mix. The ingest endpoint is exempt from Vibonomics' own
login guard because its caller has no browser session, but a proxy authenticator in front of it will still
answer that caller with a redirect to a login portal, and the snapshot is lost. Point the status line at an
address that reaches the app without passing the proxy — a LAN address and port is the simple choice.

## Development

Backend, Python 3.12 or 3.13:

```sh
cd backend
python -m venv .venv
.venv\Scripts\activate            # or: source .venv/bin/activate
pip install -e ".[dev]"
python -m vibonomics                 # http://localhost:2289
python -m pytest -q
ruff check vibonomics tests && ruff format --check vibonomics tests
```

Frontend, Node 22 or later:

```sh
cd frontend
npm ci
npm run dev                       # proxies /api to localhost:2289
npm run gen:api                   # regenerate API types after changing a router
npm run typecheck && npm run lint && npm test && npm run build
```

The Docker build runs the frontend tests and copies `frontend/dist` into the image, which the backend
serves (including under a URL base). Database changes: edit `backend/vibonomics/orm.py`, then
`python -m vibonomics.migrations revision "..."` from `backend/`; migrations run at startup.

```
backend/vibonomics/
  api/            HTTP routers
  providers/      one adapter per provider; base.py is the contract
  benchmarks/     one module per score source; matching.py maps names to models
  evals/          suites, LLM client, judge, runner, sandbox client
  sandbox/        code executor and the sidecar service
  catalog/        models.dev catalogue and canonical ids
  jobs.py         refresh jobs (models and prices, balances, benchmarks)
  scoring.py      percentiles to dimension scores to composite
frontend/src/     React 19, Mantine 9
docker/           Dockerfile and entrypoint
```

Known follow-ups, and questions already checked and settled, are recorded in
[OUTSTANDING.md](OUTSTANDING.md), each with how it was verified.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, and [SECURITY.md](SECURITY.md) for
how to report a vulnerability and what the threat model covers. Bug reports go through the issue form,
which asks for the version, deployment method and logs with keys removed.

## Licence

Vibonomics is licensed under the GNU Affero General Public License v3.0; the full text is in
[LICENSE](LICENSE). In short: you can run, modify and redistribute it, and if you run a modified
version as a network service, you must offer its source to the people using it.

The benchmark and price data that Vibonomics fetches stays under its own licences, listed in the table
above and shown in the UI. Models.dev data is MIT-licensed. Nothing from Artificial Analysis is
included in the repository.
