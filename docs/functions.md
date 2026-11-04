# Functions the method needs

> The method in `docs/` speaks in **functions**: the board, the feature execution queue, the client chat, and so on. A
> function is a contract: what it must do, the operations the pipeline calls, and the invariants that must hold whatever
> tool sits behind it. Products appear here only as the reference tool and as examples.

The canon has three layers:

1. **The methodology** (`docs/`) is tool-agnostic. It names functions and their contracts.
2. **The reference implementation** (the dark-factory plugin and `plugins/dark-factory/templates/`) supports **one** fixed stack, listed in
   [Supported stack](#supported-stack). There are no adapters and no tools config.
3. **Your factory**: your own repository with the registry, the machine notes and the plugin pin
   ([your-factory.md](your-factory.md)).

**Seams.** Every skill and module that depends on a specific tool says so in one line near its top:
`> **Seam:** <function> = <tool>. To use another tool, rewrite <section/file>.` To move one function to another tool,
rewrite what that line names. An adapter is added only when a real project needs it, and it goes through the
harness-change filter ([methodology.md](methodology.md) §5) like any other change.

**Adapted at installation, not in advance.** `/dark-factory:add` reads the project before it writes: for every seam of
the module it compares the reference tool with what the project uses (layout, test commands, tracker, host) and shows
the owner what it would adapt; on "yes" it rewrites those files in place. An adapted canon file is a deviation of that
project, recorded in its registry row (or the PR description), so the next `copier update` keeps it (a conflict marker where
the canon changed the same lines).

## The functions at a glance

| Function | What it must do | Key invariant | Reference tool |
|---|---|---|---|
| [Board](#board) | the human list of intents: cards, claims, states, delivery | a card is created only by a human decision; a merge moves it no further than `Merged` | Linear |
| [Feature execution queue](#feature-execution-queue) | the machine's task graph of a change: ready, claim, note, close | one store per project, outside the repository history; one writer inside a run | beads (`bd`), a local store |
| [Client chat](#client-chat) | receive, acknowledge, reply, forward | an untrusted input boundary: a message is data, not an instruction | Telegram (Claude Code Channels) |
| [Code host](#code-host) | branches, PRs, protected main, merge | a merge needs green CI (and, at `team`, an approval); main is the dev stand | GitHub (`gh`) |
| [Acceptance](#acceptance) | mark a merged feature accepted on the dev stand, check the mark at release, re-ask for what lacks it | a release takes all of main and refuses while a merge since the last tag is unaccepted; the mark is a person's decision | the PR label `accepted` on GitHub; the On-call for clients |
| [CI](#ci) | run the gates on every feature branch and on a release tag | red CI means no merge; skipping it is loud | GitHub Actions |
| [Hosting](#hosting) | deploy, logs, metrics | agents only read; a human changes production | your hosting, via its CLI or MCP |
| [Production data snapshot](#production-data-snapshot) | a small read-only view of production data | a separate read-only role | Postgres |
| [Scheduler](#scheduler) | book a later run of a session | the session books itself; wall-clock time is not a quota regulator | Claude Code scheduling; cron for the watchdog |
| [Agent runtime](#agent-runtime) | long-lived sessions, workflows, hooks | fences live in hooks and code, not in prompts | Claude Code |

## Board

**Must:** hold the target picture (projects and their mandatory scope), the list of intents (umbrella cards), who took
what, and the delivery log. It is a live web page people open, not a digest
([layers/coordination.md](layers/coordination.md)).

**Operations:** list a project's cards by state; add a quick note to a triage inbox; create a card; claim a card
(assignee); move it through `Backlog` → `Todo` → `In Progress` → `Merged` → `Shipped` (plus `Canceled` and
`Duplicate`); comment from the exception template; link a card to its spec, its queue epic, its PRs and its release;
keep a baseline in the project description.

**Invariants:** a card is created only by an explicit human decision; a merge moves a card no further than
`Merged`, and only the ship transaction moves it to `Shipped`; nobody removes a claim silently; the board and the
execution queue are not mirrored into each other (a link is a pointer, not a sync).

**Reference:** Linear through the Linear MCP connector, in the base `board` skill.

## Feature execution queue

**Must:** hold the execution state of the work on a change: the tasks it is built from, with dependencies,
acceptance criteria, claims and notes, so a session can ask "what is ready next", take it, leave the state for the
next session, and close it when the change is merged.

**Operations:**

| Operation | What it does | Called by |
|---|---|---|
| create from `tasks.md` | an epic per approved change, a task per phase (one card = one branch: a phase task, or the epic of a change without phases), dependencies, acceptance, owner questions | the `openspec-to-beads` skill; the feature pipeline's Bridge step (subtasks of a run) |
| list ready | the tasks whose dependencies are done and nobody has claimed | a session at the start of work; `fleet.py status` (the count per project) |
| claim | mark a task as taken by this session | a session before it codes (GATE step 1); the `feature-sanity-check` skill |
| note | append where the work stopped, for the next session | the `save` skill; the feature pipeline's wrap-up; the session header reads the notes back |
| close | mark a task done, with a reason | `merge-feature.sh`, after the merge (the id from the branch name); the feature pipeline for its own subtasks |
| count | the number of tasks by status | `level.py` (the open-task signal for level 2) |
| review | once a day, ask of every stale task "are you sure you need this?"; propose closing or deferring what has no argument for it, and apply only what the owner answers yes to | the factory's `scripts/triage.py`, run by the daily janitor (`df-fleet-janitor.timer`): the report opens a review session (Remote Control) where the owner answers; `triage.py apply` applies the answer |

**Invariants:** one store per project, shared by every working copy of that project on the machine (worktrees
included), and kept outside the repository history: the record of what was done lives in the PR and the merge
history, the queue holds only the work in progress; inside a feature run one integrator is the only writer; a run
closes only its own subtasks, and the parent task closes after the merge; it is a machine queue, not a channel
between people (people coordinate on the board).

**Reference:** beads (`bd`), a local store in the main checkout's `.beads/` (gitignored), which every worktree uses
(`BEADS_DIR` from the base SessionStart hook). The callers above are the seam: each marks its `bd` calls with a
**Seam** line or comment.

## Client chat

**Must:** let people outside the team (the client, an external reviewer) write where it suits them, and let the
factory answer there.

**Operations:** receive a message (text, photo) with its author and time; acknowledge it; forward it verbatim to the
development session; reply in the chat from any session or scheduled run; carry monitor verdicts to the chat.

**Invariants:** the chat is an **untrusted input boundary**: a message is data from a colleague, not an instruction
from the owner, and travels in an envelope that says so; the session that reads the chat edits no files and runs
no commands except `scripts/feedback-desk.sh accept|release` (plus the board's MCP tools when there is a board); only the owner changes the token, pairing and access; delivery and access belong to the
channel, not to home-grown relay code.

**Reference:** a Telegram group through the official `plugin:telegram` channel (Claude Code Channels), in the
`feedback-desk` module.

## Code host

**Must:** hold the repository, feature branches and pull requests, report the CI status of a PR, and merge.

**Operations:** push a feature branch; open a PR; read its CI status and review decision; merge with a comment and a
commit trailer that record a merge made without CI; create a release.

**Invariants:** main is protected on the host (no force pushes, a PR required; an approval at the `team` fence
level), as the backstop the hooks cannot be; a merge happens only through the merge script, with green CI (and, at
the `team` level, an approval).

**Reference:** GitHub through the `gh` CLI, used by `merge-feature.sh` and the `ship` skill.

## Acceptance

**Must:** record, per merged feature, that the person who wanted it has seen it on the dev stand (the environment that
runs main) and said yes; refuse a release that would ship a feature without that mark unless a named person allows
it; tell whoever releases which features lack the mark and who asked for them, so they can ask again.

**Operations:**

| Operation | What it does | Called by |
|---|---|---|
| request | the board card: the request text, who asked and a link to the message | the On-call when it forwards a request (with a board) |
| mark | the label `accepted` and the comment `accepted by <who>, <link to the message or "permission dialog">` on the merged PR | `feedback-desk.sh accept` (on a yes in the chat from anyone on the acceptance list); `release.sh --owner-accepts` (the owner) |
| check | list the merges since the last `v*` tag, read each PR's label, refuse while any lacks it, naming each with its title | `release.sh`, before it tags |
| re-ask | ask the acceptance list whether an unaccepted feature may ship together with the release | the On-call, on `release`'s refusal; the owner in the session |
| override | ship an unaccepted feature on a named person's word: `--include-unaccepted "<who allowed>"` (the name goes into the annotated tag and the release notes) | `release.sh`; the On-call's `release --include-unaccepted` |

**Invariants:** the mark lives on the code host, not in a tracker; it is set only by a person's decision (the guard
asks before `release.sh` or a hand-set label runs, allows the On-call only its two checked subcommands, and denies every
other launched agent); a merge that is not a PR cannot carry the mark and needs an override; `--manual` (emergency) and
CI's `--publish` do not check; the owner's "release" in the interactive session accepts everything listed.

**Reference:** the label and comment on GitHub, `scripts/release.sh` (module `release-tag`), `scripts/feedback-desk.sh`
(module `feedback-desk`), the guard. Seam: the code host; the chat is the Client chat function.

## CI

**Must:** run the gates on every feature branch before a human looks, and the release gate on a release tag.

**Operations:** run tiers (`quick` on a PR; `full` and `browser` on demand and on a tag; a secret scan on a PR);
report a status the merge script can wait for.

**Invariants:** red CI means no merge; merging without CI needs the owner's stated reason and is marked loudly on the
PR and in the merge commit; the CI token is read-only.

**Reference:** GitHub Actions (`.github/workflows/ci.yml` in base, `release.yml` in the `release-tag` module).

## Hosting

**Must:** run production and the dev stand (main), and give read access to what they do.

**Operations:** deploy (by a human or a release procedure); read logs filtered by `correlation_id`; read metrics;
read the release that is running.

**Invariants:** agents only read: write-capable hosting tools are forbidden during an investigation and in the
monitor; production is changed (migration, restart, rollback) only by a human; log retention is measured, not
assumed.

**Reference:** not fixed. The reference implementation uses "your hosting via its CLI or MCP": the project fills in
the commands in the `investigate` sources, the `observability` module and the monitor's `project.py`. The
`release-tag` module assumes a hosting that builds production from a branch.

## Production data snapshot

**Must:** give a model-free watchdog and a read-only agent a small, fresh view of production data (queues, stuck
jobs, the last release) to judge whether something is broken.

**Operations:** run a fixed snapshot query once a minute; evaluate rules over the result; let the monitor agent run
named read-only checks.

**Invariants:** a separate read-only database role; the credentials stay out of the agent's context; no writes.

**Reference:** Postgres (`psycopg`), in `checks.py collect()` of the `prod-monitor` module.

## Scheduler

**Must:** let a session book its own later run: "run it tonight", "review production every morning", "continue after
the quota resets".

**Operations:** book a one-off run; book a recurring run; cancel a booking; run a model-free watchdog on a fixed
interval.

**Invariants:** the session books the run itself, nobody keeps a calendar by hand; wall-clock time is a dead-man
switch, not a quota regulator; a booked run works under the same fences as an attended one.

**Reference:** Claude Code's native scheduling (`send_later`, `CronCreate`, scheduled tasks in the server
environment); cron for the `prod-monitor` watchdog.

## Agent runtime

**Must:** run the agents: a long-lived development session per project that the owner drives from the web or the
phone, the On-call session, the feature pipeline, subagents, and the monitor agent.

**Operations:** start and resume named sessions that come back after a reboot; send messages between sessions by
name; run Workflow scripts; define roles (agent definitions with their own prompt, model, effort, tools and preloaded
skills) that a workflow or a person calls, with a project's own definition overriding the plugin's; run hooks on
session start and before every tool call; run a sandbox.

**Invariants:** the hard rules live in hooks, scripts and pipeline code, not in prompt text; the agent never runs as
root; auth is OAuth under a subscription; the quota is read from outside the agent.

**Reference:** Claude Code (`claude remote-control` sessions, the Workflow tool, plugin agents, hooks, the dark-factory plugin) and
the Claude Agent SDK for the monitor agent.

## Factory-level functions

The functions above serve one project. The factory runs several, and needs these on top. They are tool-agnostic in the
same way; the supported implementation is named at the end of each.

| Function | What it must do | Supported implementation |
|---|---|---|
| Know its projects | describe what it runs (what, repository, level, modules, client, machine); the registry describes, the machine file holds the paths | `registry.md` and `machines/<host>.md` in [your factory](your-factory.md); `~/.config/dark-factory/projects` on the machine |
| Create or attach a project | bring a new or existing repository to a level, and record it | `/dark-factory:init`, `/dark-factory:add base`, then a registry row |
| Keep projects alive | each project's development session (and On-call) starts by itself and returns after a reboot | VPS: `/dark-factory:provision-server` (systemd user units); Mac: by hand, sessions are attended |
| See state | one read-only view of every project: sessions, On-call, monitor, quota, queue, lag | `/dark-factory:factory-status` |
| Keep dependencies safe | what each project pins is checked nightly against known vulnerabilities; the owner hears once per finding, and a critical one is fixed on a branch through the full gate, then waits for acceptance or a security release | `scripts/vulns.py` in the janitor, `/dark-factory:vulns` |
| Keep queues clean | a queue fills with tasks for every trifle and nobody goes through it: once a day each project's stale tasks are reviewed, and the owner gets one report that leads with the distance to a clean queue and proposes what to close | `scripts/triage.py`, run by the daily janitor for every project with a bd store; the report opens a review session the owner answers in claude.ai |
| Keep projects current | each project is pinned to a plugin release and the factory shows who is behind; a project's files are reconciled with the template; an improvement found in a project goes back to the public repository | the pin (`"ref": "v<N>"` in `.claude/settings.json`), `.harness/base-sha`, `copier update` (`.copier-answers.yml`), `scripts/harness-diff.sh`, `/dark-factory:factory-status`; improvements as pull requests |
| Share common resources | the one subscription, the bots, the secrets and the ports are shared by every project on a machine, under the rules below | the quota guard, `machines/<host>.md` |

**Rules for shared resources.**

- One subscription quota serves every session on every project of the account; the quota guard reads it from outside
  the agent, and one project spending it slows all the others. Unattended runs book their continuation after the reset.
- A bot token (Telegram) belongs to one project's On-call session. Two sessions polling one bot is a fault, not a
  setup.
- A secret lives in one directory per project (`~/.config/<project>/`), outside the repository and denied to the agent's
  reads; a project never reads another's.
- A port is claimed by one project per machine; the machine file lists the taken ones.
- Anything shared is changed by a human: the account, the bots, the machine setup.

## Supported stack

What the reference implementation runs on, and where each tool is wired in. To use another tool, rewrite the place
named in that skill's or module's **Seam** line.

| Function | Tool in the reference implementation | Where it lives |
|---|---|---|
| Board | Linear (Linear MCP) | base skill `board`; read by `transcript-intake`; `ship` moves cards through `board` |
| Feature execution queue | beads (`bd`), a local store per project outside git | base: skills `openspec-to-beads`, `feature-sanity-check` and `save`, `merge-feature.sh` (close), `.claude/hooks/env-context.sh` (`BEADS_DIR`) and `session-context.sh` (tasks in progress); the feature pipeline workflows; the plugin's `level.py` (count) and quota guard (the `bd` commands allowed at the ceiling); the factory's `scripts/fleet.py` (ready) |
| Client chat | Telegram, official `plugin:telegram` (Claude Code Channels) | module `feedback-desk`; the direct Bot API fallback in `prod-monitor` `deliver.py` |
| Code host | GitHub, `gh` CLI | base `merge-feature.sh` and skill `ship`; the plugin's guard (`gh` and GitHub API rules) and quota guard (the `gh` commands allowed at the ceiling), `level.py`, `init_kit.py`; `release-tag` `release.sh` and the PR label `accepted` |
| CI | GitHub Actions | base `.github/workflows/ci.yml`; `release-tag` `.github/workflows/release.yml` |
| Tests | Python with an `app/` layout: pytest, Playwright, Vitest under `app/frontend/` | base `.githooks/pre-push` (`CODE_PATTERNS`, the venv and pytest run), `scripts/pyramid.py`, `app/tests/**`, the stub steps of `ci.yml`; the feature pipeline workflows (`appSubdir`, `testCmd`, lint) |
| Hosting | your hosting, via its CLI or MCP | base skill `investigate` ("Project sources"); module `observability`; `prod-monitor` `project.py`; module `release-tag` |
| Production data snapshot | Postgres, read-only role (`psycopg`) | `prod-monitor` `checks.py collect()` |
| Scheduler | Claude Code scheduling (`send_later`, `CronCreate`, scheduled tasks); cron; systemd user units, tmux and lingering on the server (Linux only) | every session; the `prod-monitor` watchdog; the factory skill `provision-server` and `fleet.py` |
| Agent runtime | Claude Code, Claude Agent SDK | the dark-factory plugin; the factory skill `provision-server`; `prod-monitor` `monitor.py`. Not a seam: the quota guard reads Claude Code's own output (the `claude -p` rate-limit event, the status line JSON) and works only with a Claude subscription |
