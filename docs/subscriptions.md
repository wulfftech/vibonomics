# Subscription usage: Claude and OpenCode

## Claude Pro and Max

Pro and Max subscriptions have no dollar balance, which makes "how much have I got left?" harder than it
sounds. Usage is a rolling 5-hour window and a weekly window, and Claude Code cloud sessions count
against the same windows; usage credits (optional overage) are the only money-based figure.

The Models page shows one **Anthropic (Claude)** card for the whole account: the Admin API's
month-to-date spend, then the subscription's plan windows and session figures when Claude Code is
posting them. The two used to be separate cards, which read as if the subscription were another vendor.

- **Live windows.** A status line is a Claude Code setting, not a Vibonomics one: Claude Code runs the
  command you configure every time it redraws its footer, with the session's JSON on stdin, and prints
  whatever the command returns. That JSON carries the rate-limit windows. In **Settings → Providers → Claude**, copy
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

## OpenCode Go and Console

OpenCode Go is the other subscription with usable numbers. Its workspace key reads
`opencode.ai/zen/go/v1/usage`, which returns the rolling, weekly and monthly percentages of the plan's
included allowance. OpenCode documents no usage API, so the card labels the figures as unofficial. Console
(pay-as-you-go) credits stay console-only: that endpoint wants a browser session cookie, not an API key.

Console and Go are one vendor with two billing models, so they share a card on the Models page: the Go
plan's windows sit above a line saying Console credit is only visible in the console. They stay two
providers underneath, because their catalogues differ (Go covers a subset of Console's models) and each
needs its own key. Where both list the same model at the same per-token rate, the table shows one row
marked **also on Go** rather than the same model twice.

Console's free models are OpenCode-client-only: OpenCode's docs say every other harness gets HTTP 403, key or
not. The table badges them **OpenCode only**, the model details state what the docs say each one does with your
data (some collect prompts to improve the model; the Nemotron ones are trial use, no personal or confidential
data), and every other harness's config tab warns that the model will 403. Vibonomics has no way to call them
from elsewhere, on purpose.
