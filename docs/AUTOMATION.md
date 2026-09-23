# Automation (`journeydrive-automation`)

`journeydrive-automation` runs a JSON script of goals on one machine, start to
finish, with no one at the keyboard. Each step is a plain-language goal plus a
success check. An agent (Claude) works out the clicks and keystrokes from
screenshots, and a separate reviewer confirms each step from the screen before
the run moves on.

It runs on the controller next to the broker and talks to the broker's HTTP API
directly, through the same `JourneyDriveClient` the MCP server uses. It doesn't
go through the MCP server, so the MCP server doesn't need to be running.

## Setup and running

One-time setup:

```
uv sync --extra automation
```

The automation needs a Claude API credential. The broker and MCP server never
need one, since only JourneyDrive's own keys pass between them, and the MCP
client (Claude Code, Claude Desktop) brings its own Claude access. Here the
automation *is* the agent, so it calls the Claude API itself, billed per token
to that key. Use any one of these:

- **A `.env` file** in the repo root, containing `ANTHROPIC_API_KEY=sk-ant-...`.
  It's gitignored. The command loads it from the directory it's run from, or
  the nearest parent.
- **`ANTHROPIC_API_KEY` exported in your shell.** This takes precedence over
  `.env`.
- **`ant auth login`**, run once. The Anthropic SDK picks up the profile
  itself.

Without one, the command stops before connecting to the broker and says how to
set one.

It reaches the broker with the same connection config as the MCP server:
`broker_host`, `broker_port`, `broker_scheme`, `broker_api_key`, and
`broker_cert_fingerprint` if TLS is on. If you've set up the MCP server, you
already have this file.

To run a script:

```
uv run journeydrive-automation examples/automation.example.json --config scripts/mcp/mcp_config.json --machine office-pc
```

The command exits with 0 if every step passed and 1 otherwise. It also exits 1
without running anything if the config or script is invalid, or the machine
isn't connected to the broker. It prints one line per step, then the result,
the estimated cost and the run's log folder.

| Flag | Meaning |
|---|---|
| `SCRIPT` | Path to the script JSON. |
| `--config PATH` | Broker connection config. If omitted, the `JOURNEYDRIVE_BROKER_*` environment variables are used, the same as the MCP server. |
| `--machine ID` | Target `machine_id`. Overrides the script's `machine`. |
| `--log-dir DIR` | Where everything the automation writes goes (default `logs/`, which is gitignored). See "The log folder" below. |

## Script format

```json
{
  "name": "hello-notepad",
  "machine": "office-pc",
  "steps": [
    {"goal": "Open Notepad", "success": "An empty Notepad window is in front"},
    {"goal": "Type 'Hello from JourneyDrive' into Notepad",
     "success": "The text 'Hello from JourneyDrive' is visible in the Notepad window"}
  ]
}
```

| Field | Default | Meaning |
|---|---|---|
| `name` | required | Used in the run log's folder name and the summary. |
| `machine` | — | Target `machine_id`. Can be left out if `--machine` is always passed. |
| `steps[].goal` | required | What to do, in plain language. |
| `steps[].success` | required | What the screen looks like when the goal is done. The reviewer judges this **from a screenshot alone**, so describe something visible, not an action ("Notepad is in front", not "Notepad was opened"). |
| `steps[].max_actions` | — | Overrides `max_actions_per_step` for this step. |
| `max_actions_per_step` | `15` | Action budget per attempt at a step. |
| `max_attempts_per_step` | `2` | Attempts per step before the run fails. |
| `model` | `claude-opus-5` | The Claude model for both the agent and the reviewer. |
| `monitor` | primary | The monitor index to capture and aim at (see `list_monitors`). |

Unknown keys are an error, not silently ignored. The same fail-fast-on-typo
rule applies to every other config in this repo.

**Writing good steps:** keep each step small, one screen change or so. Write the
success check as something you could confirm by glancing at the screen. A step
like "set up the whole project" gives the agent room to wander, and the
reviewer nothing concrete to check.

**Examples** (both for Windows; each is tested to be a valid script):

- `examples/automation.example.json` has 2 steps: open Notepad and type a line.
- `examples/automation.browse-and-note.example.json` has 7 steps. It opens
  Chrome, visits example.com and follows its link to iana.org, then writes a
  two-line note in Notepad. It finishes by closing the Notepad tab without saving
  and closing the Chrome tab, so repeated runs don't pile up leftover tabs. It
  covers two apps, a link click, multi-line typing, a save dialog, and cleanup.

## How a run works

`journeydrive_automation.graph` builds a LangGraph state machine:

```
start_step ─► observe ─► guard ─► agent ─┬─ UI action ─────► act ─► observe
    ▲                                    ├─ step_complete ─► verify ─┬─ met ─────► next step / finish
    │                                    │                           └─ not met ─► observe (with the reviewer's evidence)
    └──── retry (fresh context) ◄────────┴─ step_failed / out of budget / stuck ─► attempt_failed ─► fail
```

- **`start_step`** starts a fresh conversation for the step, with a briefing: the
  whole task as a checklist, one-line summaries of the steps already done, and
  the current goal, success check and budget.
- **`observe`** takes a fresh screenshot after each action and restates the
  goal, the success check, and how many actions are used.
- **`guard`** enforces the budget and detects when the agent is stuck.
- **`agent`** is one Claude call that picks one action. Parallel tool calls are
  off, so each action gets its own screenshot.
- **`act`** runs the action through the broker.
- **`verify`** has a separate Claude call judge the success check from a fresh
  screenshot.

The agent's tools:

- **UI actions:** `click`, `move`, `scroll`, `type_text`, `send_keys`,
  `set_clipboard` and `wait` (at most 10s).
- **Aiming:** `preview_click` draws the magenta marker without clicking, the
  same idea as the MCP server's `preview_click`.
- **Control:** `step_complete` and `step_failed`.

Positions are always fractions of the screen (`fx`/`fy`), never pixels, for the
reasons in CLAUDE.md's "Never assume the screen resolution". Fractions are
converted to pixels with the same `journeydrive_mcp.geometry` helpers the MCP
server uses.

## Keeping the model on track

LLM agents tend to drift: they chase a side problem, forget the original task,
or loop on an approach that isn't working. Each guardrail below targets one of
those failure modes.

1. **Fresh context per step.** The agent never sees earlier steps' transcripts,
   only one-line summaries. A tangent in step 2 can't carry into step 3.
2. **The goal restated on every turn.** Every observation repeats the step's
   goal, its success check and the remaining budget, so they're never buried
   under a long history.
3. **Hard budgets.** Each attempt gets `max_actions_per_step` actions. When
   they run out, the agent gets one last chance to call `step_complete` or
   `step_failed`. After that the attempt fails, and the step is retried from a
   clean context with a one-line note on why the last attempt failed. After
   `max_attempts_per_step` attempts, the run fails. A run can't wander forever.
4. **An independent reviewer.** `step_complete` is only a claim. The step passes
   only once a separate Claude call agrees. That call sees just the goal, the
   success check and a fresh screenshot, not the agent's history or reasoning.
   If it disagrees, its evidence goes back to the agent.
5. **Stuck detection.** The same action three times in a row counts as stuck.
   Repeated scrolls are exempt, since scrolling a long list is normal. So does
   a screen that hasn't visibly changed across three actions, which is checked
   on a coarse fingerprint so a ticking clock doesn't count as a change. The
   first time, the agent gets a "you appear stuck" note. The second time, the
   attempt fails.
6. **A clean way to give up.** The prompt tells the agent to call
   `step_failed` rather than try ever more elaborate workarounds.
7. **A narrow tool surface.** There are only UI actions for the current step,
   with nothing open-ended to explore with.
8. **Bounded screenshot history.** Server-side context editing keeps the last
   three tool results and drops older screenshots, so the model reasons about
   the current screen.

## The log folder

Everything the automation writes goes into `logs/` (gitignored):

```
logs/
  journeydrive-automation.log      every run's console output (rotating, 5 MB x 3)
  <utc-timestamp>_<script-name>/   one folder per run
```

`journeydrive-automation.log` also records runs that never got going: a bad
config or script, a missing Claude key, or an unreachable machine. It also
records the traceback of any unexpected crash.

Each run's folder contains:

- **`run.log`** holds this run's full console output: every node, every broker
  request, the reviewer's verdicts, and the final result lines.
- **`events.jsonl`** has one JSON line per event: `run_start`, `step_start`,
  `observe`, `agent` (the tool chosen, its input, any text the model wrote, and
  token usage), `act` (the result), `stall`, `verify` (the verdict, evidence and
  the agent's claim), `step_passed`, `attempt_failed`, `refusal`, `run_end`.
- **`NNN_observe.png` / `NNN_verify.png` / `NNN_preview.png`** are every image
  the agent or reviewer saw, numbered in the same sequence as the events.
- **`summary.json`** holds the per-step outcome, attempts and actions, the error
  if the run aborted, total token usage, and the estimated cost.

`type_text` and `set_clipboard` inputs are logged as a character count only,
never the text. This is the same carve-out as the thin clients and the MCP
server, since typed text could be a password. **Screenshots are saved as-is**,
so treat a run folder as being as sensitive as whatever was on screen.

## Model and cost

The agent and reviewer both use `claude-opus-5` with adaptive thinking. The
agent runs at effort `high` and the reviewer at effort `low`.

- **Prompt caching:** the system prompt and tool list are fixed, so they're
  cached across turns.
- **Refusal fallbacks:** server-side refusal fallbacks are on
  (`fallbacks: "default"`). If a request is declined, it's re-run on
  Anthropic's recommended fallback model. If that also declines, the attempt
  fails with the reason logged.

Every action costs one agent call, and a screenshot is the bulk of each call's
input. A short step takes a handful of calls. `summary.json` and the final
console line show the estimated cost of each run, so check a few runs before
scheduling anything large.

If the machine or the Claude API becomes unreachable mid-run, the run stops with
the error recorded, rather than retrying steps that can't succeed.

## Known issues

**The verifier can reject a step that removes something.** Found on a live run of
`automation.browse-and-note.example.json`, on step 6: "Close the Notepad tab you
just typed in without saving it".

- **Attempt 1 actually succeeded.** The tab was gone and no dialog was showing,
  which is exactly the success check. The verifier still answered "not met"
  three times, saying "no tab was closed". It judged the *goal*, an action it
  can't see in a single screenshot, instead of the *success check*, a state it
  can see.
- **Each rejection cost an action.** Stuck detection doesn't count repeated
  `step_complete` calls, so nothing stopped the loop early.
- **The retry drifted.** To have something to close, it recreated the note, which
  redid step 5's work. The action budget stopped it at the save dialog, which
  was left open on the machine.

Planned fixes:

1. The verifier gets only the success check, with explicit rules for checks
   that something is absent.
2. A second rejection on an unchanged screen counts as stuck.
3. The agent is told never to redo an earlier step's work to satisfy a check.
4. Screenshots from a rejected verification are saved as `*_verify.png`, not
   `*_preview.png`.

**Workaround until then:** phrase a removal step's goal as the end state, not
the action. For example, "Make sure no Notepad tab containing 'JourneyDrive demo'
is open, closing it without saving if needed". If a run stops on such a step,
check the machine for a dialog left open.

