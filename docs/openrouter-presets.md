# OpenRouter presets

The full reference for the preset manager. The [README](../README.md#best-value-presets-on-openrouter) has the short version.

A preset is a named bundle of model and routing settings that any client selects with
`model: "@preset/<slug>"`. Vibonomics can keep presets pointed at the best value of the day, so a harness
that cannot pin a provider still follows them. Turn it on under **Settings → Providers → OpenRouter**, the
second row of tabs under Providers, where settings that belong to one provider live.

## How it works

- **Several presets.** Up to 20, each with its own name, filters, schedule and write switch. Applying one
  leaves the others alone. They share one lock, so two writes never overlap.
- **Its name.** You type a name and Vibonomics puts `vibonomics-` in front of it. That prefix is how it
  knows a preset is its own, so you can't leave it off and a preset of yours without it is never touched
  (unless you adopt it, below).
- **How it picks.** The same score per dollar as the Models page, for the usage profile you choose, among
  OpenRouter models that pass your filters (minimum score, tool calling, context size, no free or batch
  offers unless you allow them), and two optional limits: the most a model may cost for one task of the usage
  profile, and the least value it may give, in score points per dollar of one task (the figure the preview
  shows; the Models page's 0 to 100 index is relative to whatever is on screen, so it makes a poor fixed
  floor). A model already in the preset keeps its place unless another beats it by a set percentage, so the
  preset does not flap between near ties. Each preset can turn the prefer-own-tests option (under Scoring) on or
  off for itself; left unset it follows that setting, and the preview says which scores it ranked with.
- **Blocklists.** A list of models and a list of providers that the update never considers: one list for every
  preset, one per preset, added together. Entries are patterns, matched in any case: `openai/*`, `*:free`, an
  exact id, `deepinfra`. A blocked model (matched on its OpenRouter id or its canonical id) is never picked and
  never kept, so it drops out of a live preset on the next update. A blocked provider (matched on the endpoint
  tag, the part before the `/`, or its name) is never pinned. A model whose every known endpoint is blocked is
  left out; a model with no endpoint data is not, because nothing says its providers are blocked. Blocking
  only narrows the choice: it writes nothing to `provider.ignore`. The preview says how many models and which
  providers the lists removed.
- **How many models.** 1 to 3, which is as many as OpenRouter lets a preset hold (it answers HTTP 400
  to a fourth). The best is the lead (`model`) and the list, lead first, is `models`:
  OpenRouter's fallbacks, tried in order when one fails. OpenRouter's docs say a preset can hold an array
  of models without naming the field, so after a write Vibonomics reads the preset back and reports any key
  OpenRouter did not keep.
- **Which providers.** The cheapest endpoints for that task, up to 10, written to `provider.only` and
  `provider.order`. With several models each takes a turn at its cheapest endpoint, so the first can't use
  every slot, and a provider two models share is listed once. A discounted endpoint wins on price.
- **Route.** `fallback` (the default) writes no `route` and takes the models in order. `sort` writes
  `route: "sort"` and `provider.sort` (price, throughput or latency; price if you pick none). Both keys are
  Vibonomics's: on `fallback` it takes them off a preset that has them. OpenRouter's docs do not describe
  `route`, so it may not keep it. After every write Vibonomics reads the preset back and the page names any
  key OpenRouter dropped, `route` included. A key that was dropped is not sent again on each run.
- **What it leaves alone.** Everything else in the preset: tools, reasoning, other provider filters, and any
  guardrail you leave unmanaged (next bullet). Each write to OpenRouter is the whole config, so Vibonomics
  reads the preset first and sends back what it found. An edit you make in the OpenRouter UI survives. A new
  preset can start from one of yours, picked from the list of presets on your account (read only).
- **Guardrails.** Per preset, each of these is in one of 3 states: **leave as it is**, **set**, or
  **remove**. Left alone, whatever is on the preset stays and is shown as passthrough. Set, Vibonomics writes
  it. Removed, Vibonomics takes it off. One key can't be both.

  | Guardrail | Range | Where OpenRouter keeps it |
  |---|---|---|
  | Temperature | 0 to 2 | `temperature` |
  | Token cap | 1 to 2,000,000 | `max_tokens` |
  | Repetition penalty | above 0 to 2 | `repetition_penalty` |
  | Frequency / presence penalty | -2 to 2 | `frequency_penalty`, `presence_penalty` |
  | Top P | above 0 to 1 | `top_p` |
  | Quantizations | int4 int8 fp4 mxfp4 nvfp4 fp6 fp8 mxfp8 fp16 bf16 fp32 unknown | `provider.quantizations` |
  | Ignored providers | up to 50 slugs | `provider.ignore` |
  | Require parameters | on / off | `provider.require_parameters` |
  | System prompt | up to 20,000 characters | the system message of the write |

  The token cap is sent as `max_tokens`: OpenRouter drops `max_completion_tokens` from a preset. A quantization
  list leaves out every endpoint whose precision OpenRouter lists as `unknown`, unless you name `unknown`
  (OpenRouter answers 404 "No endpoints found" for a model that has none left). A client's own request
  parameters win over the preset's, so a temperature or token cap here applies only where the client sends
  none. Repetition penalty is the one clients rarely send.
- **Capability.** A parameter you set only counts on an endpoint that supports it. Vibonomics pins the endpoints
  that take every one you set and allow the token cap. If a model has none, it pins the others and warns
  ("top_p will be ignored for ..."), since OpenRouter drops what an endpoint doesn't take. With
  `require_parameters` on, OpenRouter refuses the request instead, so the plan is blocked and names the model
  and the parameter. A model whose every endpoint fails your quantization or ignore list is left out of the
  ranking with a warning. The preview shows a table of which pinned endpoint takes which parameter.
- **System prompt.** OpenRouter takes a preset's system prompt from the system message of each write, and a
  write with none sets it to null. So every write carries the prompt that should be in force: the one you set,
  none if you remove it, otherwise the live one, sent back as it is. A preset with a system prompt is no longer
  refused. When a write replaces or removes the live prompt, Vibonomics first keeps the old text (up to 20,000
  characters) and the preview shows when. After the write it checks the stored prompt against what it sent
  and names `system_prompt` among the keys OpenRouter did not keep if they differ.
- **What it will write.** A slug it created itself, or one you adopted. It never deletes: OpenRouter's API has
  no delete, so remove one in the web UI, which archives it. An archived preset takes no new versions, so
  Vibonomics reports that instead of writing; restore it or choose another name.
- **Adopting a preset you made by hand.** **Inspect** shows any preset on the account: its live config, its
  history, and the raw JSON of every key Vibonomics has no setting for and would send back as it found it
  (everything but `model`, `models`, `route`, `provider.only`, `provider.order` and `provider.sort`), and its
  system prompt.
  **Adopt** is refused if the preset changed since you looked (the prompt included), is archived, is already
  managed, or sits under another key. A system prompt is no obstacle. Adopting writes nothing: the preset
  joins the list switched off, under its own name, and the first update is a separate step you turn on. Taking
  an adopted preset out of the list hands it back; Vibonomics never touches it again until you adopt it again.
  A scheduled run on an adopted preset writes only while those other keys, and the system prompt, are still
  what you reviewed. If someone edits them on OpenRouter, the run is recorded as blocked and writes nothing,
  until you preview the preset and apply it yourself. Changing which guardrails you manage changes what counts
  as reviewed, so the next scheduled run waits for an apply by hand. Saving with writing on does that.
- **Preview first.** **Preview** shows what the next update would write: it reads the preset from OpenRouter,
  ranks the models and shows the live version number, the version history, what changes and the providers.
  Previewing writes nothing; the changes are sent by **Apply**, by Save with writing switched on, or by the
  schedule. It previews unsaved changes as they
  stand, so you can try settings before keeping them; such a preview is marked, and cannot be applied. **View API call** opens the exact
  request it would send. **Apply** sends that plan, and only that plan: if anything moved since you looked,
  it refuses and asks you to preview again. After a write it reads the preset back and names any key
  OpenRouter did not keep.
- **Live check before every write.** The ranking runs on model data from the last refresh, which can be hours
  old. So the preview, and again the apply that writes, read OpenRouter's public listing for each model in the
  preset (no key is sent) and check what is about to be written against it: every provider the preset names is
  still listed for its model, and each model keeps at least one. A provider that is gone is an error: the write
  is refused, nothing is sent, and the run is recorded as blocked with the reason. A provider reporting a bad
  status or under 50% uptime, a price more than 20% off what the plan used, and a model that expires within a
  week are warnings, shown and not blocking. So is a listing OpenRouter does not answer in time. The check runs
  on a preset that is already up to date too, so the preview shows the health of what is written. It also
  reads your guardrails: a model none of whose live endpoints passes the quantization and ignore lists is a
  warning (this preset will never be served it), and an error if that is true of every model; a pinned endpoint
  that lacks a parameter you set is an error with `require_parameters` on and a warning without it.
- **Free models.** Free models are left out unless you switch on **Allow free models**. They share an
  account-wide daily request cap (1000 a day with credits, 50 without), and the OpenRouter card on the main
  page shows how much of it today's requests have used and when it resets (00:00 UTC), as of its last refresh.
  A preset whose models are all free warns about it, in the preview and in the live check. When the cap is spent, requests to that preset fail
  until it resets at 00:00 UTC. 2 choices guard against that, both only while free models are allowed:
  - **Keep a paid model as the last fallback.** When every model chosen is free, the last place goes to the
    best-value paid model that passes your filters, so OpenRouter falls through to it when the free ones fail.
    The free models keep the lead. It needs room for 2 models or more. A paid model already in the preset
    is kept over one that is only a few percent better, like any other model. If it can't be added (one place,
    or no paid model qualifies) the plan says so and warns about the cap as before.
  - **Allow a lineup of free models only.** Switched off, a plan whose models are all free is refused:
    nothing is written, and a scheduled run is recorded as blocked with the reason. With the paid fallback on
    too, the fallback is tried first, and the plan is refused only if it could not be added.
- **On a schedule.** A separate switch updates a preset every N hours (as little as 0.5, which is every
  30 minutes) with no one watching, each on its own timer. Off by default, and it needs the write switch
  too. After a restart, any preset that is overdue runs once.
- **Saving sends it.** With the write switch on, **Save** on this page also sends the update straight away:
  it plans from what was just saved and applies that plan, so a change reaches OpenRouter without a second
  step. If the saved settings leave the preset as it is, nothing is sent and the page says so.

## The management key

Since 2026-11-07 OpenRouter turns an ordinary API key away from the
call that creates or updates a preset (HTTP 401 "Invalid management key"; their docs now say it needs a
read-write management key). So the OpenRouter provider needs a **management key** (the same field the spend
history uses; create it at openrouter.ai/settings/management-keys, which offers a name and an expiry but no
read or write setting: OpenRouter's docs call these keys unscoped). The write goes out
under that key, and under it alone; reading the preset, the catalogue, the ranking and the live check use the
API key, and requests to `@preset/<name>` are ordinary calls. Without a management key the preview says so as a
live-check error, Apply stays off, and a scheduled run is recorded as blocked; a preset that is already up to
date needs none. The management key is taken from the same provider row as the API key, never from another row,
because a management key belongs to an account. The write names the workspace: the preset's own for an update,
the API key's (read from the last balance refresh) for a new one, so the preset lands where `@preset/` requests
look for it. If OpenRouter answers from a different workspace than the one asked for, nothing is recorded.

## Preset catalogue

Vibonomics keeps a local copy of every preset on your OpenRouter account, up to 100,
archived ones included: the models, the providers, the live version and the config keys it has no setting for.
It reads the account every 30 minutes, at startup and when you press Refresh, with the API key (not the
management key the update is written with), and only reads. A preset's config can hold anything you put in it, so a visitor to an instance that is
open to read does not get the catalogue. If one preset can't be read, its last copy stays and the page says the
sync was partial.
