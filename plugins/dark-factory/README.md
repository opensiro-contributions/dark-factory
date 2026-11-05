# dark-factory plugin

The part of the [dark factory](https://github.com/ruslantrachuk/dark-factory/blob/main/README.md) that is always on, packaged as a Claude Code plugin. Its hooks,
not written instructions, keep the agent away from the decisions that belong to a human:

- **Guard** (`scripts/guard.py`, PreToolUse on `Bash|Edit|Write|MultiEdit|NotebookEdit` and the scheduling tools).
  Denies force pushes and `+refspec`, a push to the main branch, `--no-verify` and other ways around the git hooks,
  release-tag pushes, GitHub API writes of refs, releases and branch protection, deleting or hard-resetting the main
  branch, any write into the installed plugin (`~/.claude/plugins/**`, `$CLAUDE_PLUGIN_ROOT`, the plugin's state in
  `~/.cache/dark-factory`: the plugin changes through this repository and an update), and booking a
  `/dark-factory:level` prompt you did not type. At `standard` and `team` it also denies `gh pr merge` outside
  `scripts/merge-feature.sh` and approvals by the agent. Every denial says what to do instead.
  A change to the fences themselves is **asked**, not denied: at every level, deleting, moving or rewriting
  `.claude/fences.env`, lowering `FENCE_LEVEL` (or `QUOTA_FAIL_CLOSED` from 1 to 0), settings that switch off the
  hooks or the plugin (`disableAllHooks`, `GUARD`/`QUOTA_GUARD`, `enabledPlugins`, `claude plugin disable|uninstall`),
  shell writes to `.claude/settings*.json` and `.githooks/`; at `standard` and `team`, any edit of the fence files
  (`.claude/settings*.json`, `.claude/fences.env`, `.githooks/`, `scripts/merge-feature.sh`). Claude Code then shows
  the call in its permission dialog and runs it only on your yes. Where nobody can answer — a subagent, a session
  with `DF_UNATTENDED=1` (set by the On-call launcher), `claude -p` — it is denied with "leave it to the human:
  write the patch and a note, keep going".
  A **release is a person's decision** too: a run of `scripts/release.sh` that tags, and marking a feature accepted by
  hand (`gh pr edit --add-label accepted`, a comment "accepted by …", the API equivalents), is asked ("a release ships
  all of main since the last tag to production; Yes = you accept it"). In the On-call (`DF_ROLE=on-call`) a plain
  release and `--include-unaccepted "<name>"` pass, `--owner-accepts`, `--manual` and hand-set marks are denied, and its
  Bash is limited to one plain `scripts/feedback-desk.sh accept|release` command; any other launched agent is denied:
  "leave the release to the owner or the On-call". Every ask and deny is logged to `.claude/state/audit.jsonl`. The guard is
  a seatbelt, not a sandbox: see [What the guard is and is not](#what-the-guard-is-and-is-not).
- **Quota guard** (`scripts/quota.py`). Watches the subscription's 5-hour and 7-day windows: at 80% no new
  subagents or workflows, at 85% wrap up, at 90% only saving work, and the message names when work resumes so an
  autonomous session schedules its own continuation. Details: [docs/quota-guard.md](docs/quota-guard.md).
- **Git-hook repair** (`hooks/ensure-git-hooks.sh`, SessionStart). Points `core.hooksPath` back at `.githooks`
  when a worktree tool moved it; does nothing in a project without `.githooks/`.
- **`/dark-factory:init`**. Scaffolds the solo kit into the current project (below).
- **`/dark-factory:add <module>`**. Installs the full harness (`base`, level 1) or a module (`on-call`, `monitor`,
  `release`, `observability`, `code-audit`, `feature-pipeline`) from this plugin's [templates/](templates/README.md):
  the steps only you can do first, then the files, the filling in, the tests, the server units and a status check.
- **`/dark-factory:upgrade [v<N>]`**. Moves the project to a newer release on a work branch: `copier update` of the
  template files, the conflict markers resolved, the project steps of each release's "How to upgrade" notes applied,
  the server steps listed for you in the PR.
- **`/dark-factory:provision-server`**, **`/dark-factory:factory-status`** (`scripts/fleet.py`). Keep every project's
  sessions running on a Linux server across reboots, and show the state of all of them.
- **`/dark-factory:vulns`** (`scripts/vulns.py`). Known vulnerabilities in what a project's lock files pin (OSV,
  CISA's known-exploited catalogue), checked now in any session; the ones at or above the project's threshold fixed
  on a branch through the full gate and `merge-feature.sh`. On a factory server the nightly janitor runs the check and
  opens a fix session for a critical finding by itself.
- **`/dark-factory:harden`** (`scripts/harden.py`). The project's written rules (CLAUDE.md, AGENTS.md, the coding
  standards) that nothing enforces: the script lists the controls that really exist (fence level, permissions,
  hooks, git hooks, CI, linters, rule tests, branch protection), the skill matches every rule against them and reports
  the text-only ones as risks, with violations and repeat fixes from the code and history. Read-only; works without
  the rest of the factory.
- **`/dark-factory:secrets`** (`scripts/secrets_scan.py`). Where the project's secrets could leak or reach the wrong
  hands, the agent's included: gitleaks (redacted) over the history and the working tree, secret files in and around
  the project and who can read them, MCP credentials, shell histories, the agent's permissions, docker/sudo and open
  ports, dumps the same user can delete; the skill judges what each key allows and reports risks — names and paths,
  never values. Read-only, sends nothing; works without the rest of the factory.
- **`/dark-factory:level`** (`scripts/level.py`). Shows the project's level on the ladder
  ([docs/levels.md](https://github.com/ruslantrachuk/dark-factory/blob/main/docs/levels.md)), the signals for the next one and what would change, and writes
  `FENCE_LEVEL` on your yes (below). A SessionStart hook prints one line when it sees a next-level signal.
- **`/dark-factory:statusline`** (`scripts/statusline.py`). Installs a statusLine wrapper that feeds the quota
  guard exact numbers and keeps your existing statusline; dry run first, `--uninstall` restores it.
- **`/dark-factory:feature`**. Runs the feature pipeline (`workflows/`) with the project's
  `.claude/factory/feature-pipeline.json`, so the settings are not passed by hand. The pipeline is token-expensive and is for
  unattended runs; in an interactive session work directly with the skills and roles below. `buildMode`
  (`--mode session|pipeline`) picks how a run is done: `session` (default) — one builder does the whole task by the stage
  skills, then one reviewer; `pipeline` — every stage by its own agents, parallel build waves, for a wide phase of
  independent slices. The run's path (lite or full) is the size the owner approved; an agent sizes it only when none
  is given.
- **Stage skills**: `plan` (a phase into end-to-end slices with files, tests and dependencies), `build-tdd` (one slice,
  test first), `integrate` (its commit, bd close, the full gate), `wrap` (bd note, project documents, the PR
  description). The same rules in a person's session and in the pipeline; each names the next.
- **Roles** (`agents/`): `dark-factory:tech-lead` (sizes work, slices it end to end, assesses risk, critiques a plan),
  `dark-factory:builder` (builds and integrates planned slices with the stage skills; the pipeline's writer),
  `dark-factory:product-owner` (necessity, user flow, acceptance scenarios, business value) and `dark-factory:reviewer`
  (read-only review through one lens). Call them by hand ("size this task as the tech lead") or let the workflows and the
  `feature-sanity-check` skill call them. A project replaces one by putting `.claude/agents/<name>.md` in the project.
  The `wsjf` skill holds the priority formula and scales they share.

The hooks read the project from `$CLAUDE_PROJECT_DIR`. A project **opts in** with `.claude/fences.env`; in a
project without it the guards stay silent (one hint line at session start), so installing the plugin for your
user does not fence unrelated repositories.

## Install

```
/plugin marketplace add ruslantrachuk/dark-factory
/plugin install dark-factory@dark-factory
```

From a shell: `claude plugin marketplace add ruslantrachuk/dark-factory && claude plugin install dark-factory@dark-factory`
(`--scope project` records it in the project's `.claude/settings.json` instead of your user settings).
Requirements: Claude Code, `python3` 3.11+, `git`; `gh` and `jq` for `merge-feature.sh`.

## `/dark-factory:init` — the solo kit

Run it in a project. It shows a dry run, asks for your "yes", never overwrites a file, and adds:

| File | What it is |
|---|---|
| `CLAUDE.md` | a short operating contract: feature branches, merge only through `merge-feature.sh`, the PR description as the record of work (`## Done` / `## Decisions` / `## Open questions`). Appended once to an existing file |
| `scripts/merge-feature.sh` | the merge transaction: push, PR, check the PR description, wait for CI, merge with the description as the merge commit message, clean up; re-runnable after any failure. Main branch and merge strategy filled in |
| `.claude/fences.env` | `FENCE_LEVEL=solo`; the file also opts the project in |
| `.githooks/pre-push` | runs your test command once per push (`TEST_CMD`), otherwise a no-op with a hint |
| `.claude/settings.json` | `extraKnownMarketplaces` + `enabledPlugins`, so collaborators who open the project are offered the plugin. Only missing keys are added |

It also sets `git config core.hooksPath .githooks` unless another hooks path is set. Nothing is committed or
pushed. The kit does not need beads, OpenSpec or a board; the full harness is the `base` template
([templates/](templates/README.md)), installed with `/dark-factory:add base`; it enables this plugin too.

## `/dark-factory:level` — the ladder

`/dark-factory:level` (`scripts/level.py`) reads the project's level from evidence in the project (it is never
stored), reports gaps at the current level and the signals for the next one, and changes `FENCE_LEVEL` (and
`QUOTA_FAIL_CLOSED`) up or down on your "yes". It writes only after you typed `/dark-factory:level <target>`
yourself: a UserPromptSubmit hook records a one-shot grant for that target, valid for 10 minutes; a bare
`/dark-factory:level` only reports. Lowering takes effect only once the lowered `fences.env` is merged into main, because
`origin/main` sets the floor. Structural steps (`/dark-factory:add`, `/dark-factory:provision-server`) it names and never runs.
The SessionStart hint is one line, at most once a day unless the signals change; `DARK_FACTORY_LEVEL_HINT=off`
silences it.

The ladder, the evidence for each level, the signals and the full fence-level table are in
[docs/levels.md](https://github.com/ruslantrachuk/dark-factory/blob/main/docs/levels.md).

## What the guard is and is not

The guard is a static reader of tool calls. It stops an agent's mistakes and the common shortcuts; it is not a
sandbox against a determined or hostile agent. It cannot see through a script written to a file and then run, an
alias defined elsewhere, a subprocess, or an arbitrary interpreter. The real gates are GitHub branch protection or
rulesets on main, required reviews and the Claude Code sandbox; turn on branch protection at every level.
At `solo` and `standard` the agent merges itself once CI is green and its own review is done; at `team` the PR also
needs a GitHub approval. See [SECURITY.md](https://github.com/ruslantrachuk/dark-factory/blob/main/SECURITY.md) and the [threat model](https://github.com/ruslantrachuk/dark-factory/blob/main/docs/threat-model.md).
Bypass is for a human only: restart the session with `GUARD=off claude` (quota: `QUOTA_GUARD=off claude`).

## Uninstall

```
/plugin uninstall dark-factory@dark-factory
/plugin marketplace remove dark-factory
```

The files `init` wrote stay in the project; delete them yourself if you want them gone (`.claude/fences.env`
first — without it nothing is fenced even while the plugin is installed; the guard asks you before the agent
deletes it).

When you develop the plugin itself with `claude --plugin-dir <checkout>/plugins/dark-factory` inside an opted-in
checkout, `$CLAUDE_PLUGIN_ROOT` is inside the project and is not fenced; the installed copies under
`~/.claude/plugins` still are.

## Development

The plugin's single source is this directory: commands (`skills/`), scripts, `templates/` (the `base` template, the
modules and the small `solo` kit `init` uses) and `workflows/`. `init` takes `merge-feature.sh` from
`templates/base/scripts/`, so there is one copy. Nothing in here may depend on a file outside this directory. The
plugin's tests live in the repository's `tests/` (run from the repository root), so they do not ship with it.

```bash
python3 tests/test_guard.py
python3 tests/test_quota.py
python3 tests/test_init_kit.py
python3 tests/test_level.py
python3 tests/test_add_module.py
python3 tests/test_fleet.py
claude plugin validate plugins/dark-factory && claude plugin validate .
```
