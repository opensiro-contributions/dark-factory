# Feature pipeline workflows (dark-factory plugin)

> Workflow scripts the development session launches to take one bd task from a spec or an OpenSpec phase to a
> committed, reviewed branch. They ship with the plugin and register as `dark-factory:<meta.name>`; the
> project-side setup is the `feature-pipeline` module ([templates/feature-pipeline/MANIFEST.md](../templates/feature-pipeline/MANIFEST.md)),
> which has the full argument table.

**When to use them.** The pipeline is token-expensive: a planner, a critic, one worker per task, integration and
review lenses are all separate agents. It is for **unattended runs**: a big task you book for the night or leave to run
while you are away. In an interactive session do not launch it: work in the session itself with the same stage skills
the pipeline uses — `plan` → `build-tdd` per slice → `integrate` → the `reviewer` role → `wrap` — and the roles
directly (`size-task`, `feature-sanity-check`, the `tech-lead`, `product-owner` and `reviewer` roles, below).
Launch the pipeline only when you are leaving it to run alone.

| File | Registered as | Role |
|---|---|---|
| `feature-auto.js` | `dark-factory:feature-auto` | entry point: branch + worktree → the path (`path` from the approved size; triage only when none is given; small → `feature-lite`) → plan → overlap check → size gate / split → critic → bd subtasks → `feature-core` → integrate (merge the base, full gates, project documents, the PR description). Stops before the PR |
| `feature-core.js` | `dark-factory:feature-core` | the run's bd subtask graph, built in one of two modes (below), then review lenses and fix |
| `feature-lite.js` | `dark-factory:feature-lite` | the small path: one build agent (TDD + full gates + commit), one reviewer, a fix agent when needed, one wrap-up agent (bd note, project documents, the PR description) |
| `dryrun.mjs` | — (not loaded: the plugin loads `*.js` only) | stub run with fake agents — control flow without spending tokens |

## Roles

The pipeline is built from reusable roles, not from prompts hidden in the scripts. Three layers: a **skill** is know-how
(`size-task`, `wsjf`, and the stage skills `plan`, `build-tdd`, `integrate`, `wrap`), a **role** is an agent definition with its own prompt, model, effort, tools and preloaded skills
(`plugins/dark-factory/agents/<name>.md`), a **workflow** is only the order of the calls. A human decision never
becomes a role.

| Role | Does | Called by the workflows as | Also useful by hand |
|---|---|---|---|
| `tech-lead` | sizes work (preloads `size-task`), plans a phase (preloads `plan`), slices it end to end, assesses feasibility and risk, critiques a plan | `plan`, `replan`, `critic`, `split` in `feature-auto` | "size this task", "is this plan sound", "split this change" |
| `builder` | builds a slice by TDD, integrates it (tests, lint, a commit, bd close), runs the full gate, wraps up — preloads `build-tdd`, `integrate`, `wrap`; writes on the branch, never pushes | the implementers, integrators, gates and Fix in `feature-core`, Build and Wrap in `feature-lite`, the final integration in `feature-auto` | "build subtask X", "integrate and run the gates" |
| `reviewer` | fresh-context, read-only review through one lens, findings in the `docs/review-contract.md` format | the review lenses in `feature-core` | "review this diff for missing tests" |
| `product-owner` | necessity, user flow, WHEN/THEN scenarios, BV/TC/RR | not called by the pipeline (the `feature-sanity-check` skill calls it) | "look at this as the product owner" |

What stays inside the scripts is the run's mechanics: paths and gate commands, the file scope of a parallel worker
("do not commit, the integrator does"), schemas, caps, branch and bd setup, the push and PR step. Everything that is
also true in a person's session lives in the stage skills, so the two ways of working cannot drift apart.

## Build modes

`feature-auto` runs a task in one of two modes (`buildMode`, from `.claude/factory/feature-pipeline.json`, or
`/dark-factory:feature --mode` for one run). Every agent starts with the project's whole context (CLAUDE.md, skills,
plugins), tens of thousands of tokens before any work, so the number of agents is most of the cost of a run.

| | `pipeline` | `session` (default) |
|---|---|---|
| The run | each stage by its own agents: repo root, branch, sizing, plan, critic, overlap check, bridge, build waves, integration, review lenses, fix, wrap-up; a small task goes to `feature-lite` | ONE `builder` does the whole task by the stage skills — branch, `plan`, `build-tdd` and `integrate` per slice, the full gate, `wrap` — then one `reviewer`; a `fix` agent only for critical or major findings |
| Agents | 5–6 for a small task (`feature-lite`: build, review, a fix when needed, wrap), about 30 for an 8-subtask phase | 2, or 3 with a fix (plus `pr` when `stopBeforePR` is false) |
| Fits | a wide phase of independent slices, where parallel wall time matters | most tasks: one writer sees every earlier decision |

Session mode has no split of a too-wide phase, no plan critic and no overlap check with other branches: those are
pipeline mode only. The result carries `path: "session"`, `slices`, `findings` and `prBody`.

`feature-core` called directly keeps its own `buildMode` for its Build stage: `pipeline` builds in parallel waves,
`session` lets one `builder` take the subtasks in dependency order, a fresh one every `sessionChunk` (default 4). A
failed subtask there takes the subtasks that depend on it out of the run; the rest go on.

**Calling a role.** A plugin agent is found only by its full name `dark-factory:<name>`. The scripts call
`agent(task, { agentType })`; the prompt is a task, the role supplies the standing instructions and the model.

**Overriding a role.** A project (or a person's own factory) replaces a role by putting `.claude/agents/<name>.md` in
the project: same name, its own prompt, model and tools. Keep the `skills:` list of the plugin's role in the copy: the
workflow prompts name the stage skills' steps and rely on them being preloaded (`tech-lead`: `plan`; `builder`:
`build-tdd`, `integrate`, `wrap`). A project agent overrides a plugin agent only when called by the
bare name, so the scripts check: `feature-auto`'s first step lists `<repo root>/.claude/agents/` and the run uses the bare
name for every role the project has there, `dark-factory:<name>` for the rest. It passes the list to `feature-core` as
`projectRoles`; a `feature-core` started by hand without `projectRoles` uses the plugin's roles.

## Launch

The usual way is the plugin command: `/dark-factory:feature <task>` reads `.claude/factory/feature-pipeline.json`, asks for
what the task does not give (release line, bd id, source) and launches `dark-factory:feature-auto` (or `feature-lite`)
with the file's settings plus the run's own arguments, so the settings are never re-typed.

By hand: the Workflow tool by name, `args` as an object — the run's own arguments plus the project's settings from
`.claude/factory/feature-pipeline.json` (test and lint commands, frontend dir, message catalog, invariants doc, surfaces,
models, maxAgents):

```
Workflow({ name: "dark-factory:feature-auto", args: {
  "request": "Phase 3 — outbound delivery log", "stamp": "20261021-p3", "trackerId": "DMP-123",
  "slug": "delivery-log", "bdId": "proj-t4sk.3", "changeDir": "openspec/changes/delivery-log", "phase": 3 } })
```

The scripts find the project at run time (the session's git top level), not from where they are installed. Do not pass
`appDir`: the run creates its worktree at `<session tree>/.claude/worktrees/fab-<slug>`. Code at the tree root rather
than in base's `app/` — add `"appSubdir": ""`. Nesting is one level — `feature-auto` calls
`workflow('dark-factory:feature-core', …)` / `workflow('dark-factory:feature-lite', …)`, they call nothing. A project
that keeps its own edited copy passes `coreScript` / `liteScript` (absolute paths inside the session tree).

**Cost cap.** `maxAgents` limits the agent calls of a whole run, children included (defaults: `feature-auto` 40,
`feature-core` 30, `feature-lite` 5). A token target (`budget.total`) exists only when a human typed one ("+500k"), so
an unattended or scheduled run is bounded by `maxAgents` alone. Each stage checks the headroom it needs before it
starts; when it is short, the run stops cleanly with `error: 'agent limit reached'` (or `'budget exhausted'` for a
token target) and `stage`. Every result carries `agentsUsed` and `maxAgents`.

**Models.** The model of a role lives in the role (`agents/<name>.md`), so the scripts carry no model ids.
`models: {plan, implement, review}` is a project's deliberate override and wins over the role: plan = the `tech-lead`
calls (plan, critic, split), implement = triage, mechanics, workers, integration, PR, review = the `reviewer` calls. A
call with no override uses its role's model, and the mechanical steps without a role inherit the session's model.
Pin full ids, not aliases, when you pin.

**Read `error`, not the status.** The harness reports "completed" for almost every run. `error: null` — the branch is
committed and green; anything else — the run stopped (`overlaps in-flight branch`, `plan failed`, `chunk <key> failed`,
`phase too large — split proposed`, `integrate failed`, `incomplete: …`). Each script lists its values in the result
comment at the top. Resume a stopped run with `resumeFromRunId` and the same args (same `stamp`).

After `error: null` the session shows the branch, the assumptions and the PR description (`prBody`: `## Done`,
`## Decisions`, `## Open questions`) to the owner, opens the PR with it (`gh pr create --draft --body-file -` when the
run stopped before the PR) and, once CI is green and the change is reviewed, runs `scripts/merge-feature.sh <branch>` —
which refuses to merge without the three sections, writes the description into the merge commit and closes the task
after the merge; then `git worktree remove <worktree>`. When CI cannot run (Actions disabled: billing, limits, an
incident) the owner may also say to add `--skip-ci "<reason>"`: the script runs the local gate instead and marks the
merge as made without CI (refused at `FENCE_LEVEL=team`).

The branch is the change key: `<type>/<trackerId>-<slug>` with a tracker ID, `<type>/<slug>` without
(`type`: `feat|fix|ui|refactor|chore|docs`; a phase of a spec without sub-cards: slug ending in `-p<K>`). The merge
finds the task to close from it — the tracker ID equal to the task's `external_ref`, or the slug as the epic's
`change:<slug>` label (and `phase:<K>`); no match or several gives "close it by hand".
`scripts/merge-feature.sh <branch> --print-bd-id` prints the id it would use. `feature-lite` run by hand without
`bdId` names the branch `<type>/lite-<stamp>` unless `slug` is given; the task its Wrap step created
(`wrap.details`) is then closed by hand.

## Dry run

From the canon checkout (`plugins/dark-factory/workflows/`):

```bash
node dryrun.mjs feature-auto.js '{"request":"x","stamp":"s1","bdId":"proj-x","path":"full","buildMode":"pipeline","allowMigrations":true}'
node dryrun.mjs feature-lite.js '{"request":"x","stamp":"s1","bdId":"proj-x"}'
node dryrun.mjs feature-core.js '{"appDir":"/r/.claude/worktrees/fab-x","root":"/r","parentId":"proj-x","buildMode":"pipeline"}'
# session mode: one builder in dependency order; a builder per subtask; a failed subtask
node dryrun.mjs feature-core.js '{"appDir":"/r/.claude/worktrees/fab-x","root":"/r","parentId":"proj-x","buildMode":"session"}'
node dryrun.mjs feature-core.js '{"appDir":"/r/.claude/worktrees/fab-x","root":"/r","parentId":"proj-x","buildMode":"session","sessionChunk":1}'
DRYRUN_EXPECT_ERROR='not implemented' node dryrun.mjs feature-core.js '{"appDir":"/r/.claude/worktrees/fab-x","root":"/r","parentId":"proj-x","buildMode":"session"}' '{"build:1":{"results":[{"bdId":"proj-x.2","status":"failed","filesChanged":[],"summary":"x"},{"bdId":"proj-x.3","status":"done","filesChanged":["f"],"summary":"s"}]}}'
# code at the tree root, a hand-run small path without a bd task, a spent token budget, the agent cap:
node dryrun.mjs feature-auto.js '{"request":"x","stamp":"s1","bdId":"proj-x","path":"full","buildMode":"pipeline","allowMigrations":true,"appSubdir":""}'
node dryrun.mjs feature-lite.js '{"request":"x","stamp":"s1"}'
DRYRUN_BUDGET=3000 DRYRUN_EXPECT_ERROR='budget exhausted' node dryrun.mjs feature-auto.js '{"request":"x","stamp":"s1","bdId":"proj-x","path":"full","buildMode":"pipeline","allowMigrations":true}'
DRYRUN_EXPECT_ERROR='agent limit reached' node dryrun.mjs feature-auto.js '{"request":"x","stamp":"s1","bdId":"proj-x","path":"full","buildMode":"pipeline","allowMigrations":true,"maxAgents":8}'
```

The stub exits 1 when the run's `agentsUsed` differs from the number of agent calls it saw, when the calls pass
`maxAgents`, or when `DRYRUN_EXPECT_ERROR` is set and the result's `error` does not contain it (`null` — must succeed).
`DRYRUN_EXPECT_TYPES='plan=dark-factory:tech-lead,review:combined=dark-factory:reviewer'` checks the `agentType` of the
first call with each label; a third argument such as `'{"repo-root":{"ok":true,"summary":"/r","details":"tech-lead"}}'`
simulates a project that overrides a role, and the label then expects the bare name.

Pass bare script names: scripts are loaded from next to `dryrun.mjs`, and a nested `workflow('dark-factory:<name>')`
resolves to `<name>.js` there. An optional third argument overrides agent answers by label, which exercises rare
branches — e.g. `'{"integrate#1":{"ok":false,"summary":"red"}}'` (core failure) or
`'{"critic:recheck":{"approved":false,"issues":["x"]}}'` (critic rounds exhausted → split). The stub also fails if a
child script calls `workflow()`.

## Run rules

- **A phase is split automatically.** Wider than the threshold, or not accepted by the critic in three rounds → the
  architect splits it into 2–4 end-to-end chunks, built in sequence on **one branch** by `feature-lite` (`skipWrap`);
  review (`feature-core` with `reviewOnly`) and integration run once for the whole branch. `manualChunks: true` returns
  the split to the caller.
- **Split by end-to-end slices, not by layers**: each chunk, after its commit, works from the entry point to the result
  and is covered by a test at the entry level.
- **Parallel features with shared code — one after another.** An overlap between the plan's code files and an unmerged
  local branch stops the run (`overlaps in-flight branch`); `allowOverlap: true` continues deliberately.
- **The critic checks switch-off, reuse and surfaces**: behaviour with the flag off is defined, existing helpers are
  reused, a user-visible feature has a confirmed "Interaction surfaces" section (base `openspec/README.md` §0).
- **One heavy gate per run; long lanes in shards.** One agent command is limited to 600 s; a longer db lane is split with
  `dbShards`, each shard a synchronous command. Fix in `feature-core` runs only what was touched plus the fast suite;
  Integrate runs the full lanes once.
- **`bd` commands run with `timeout 60`**: bd can hang on a database lock.
- The run's subtasks are children of `bdId` (label `auto-run`) and are closed by the core; the parent stays `in_progress`
  until `merge-feature.sh` closes it after the merge. The queue is the project's local store in the main checkout
  (`bd -C <root>`), outside git: nothing about it is committed.

## Engine rules (do not change)

- `Date.now()` is forbidden in Workflow scripts (it breaks resume) — pass `stamp` as an argument.
- A token target set by the session (Workflow `budget.total`) is checked between stages and build rounds; when it is
  spent the run returns `error: 'budget exhausted'` instead of starting the next stage.
- Every agent call goes through `spawn()`, which counts it against `maxAgents`; a child gets what is left of the
  parent's cap (`maxAgents` in its args) and returns `agentsUsed`, which the parent adds to its own count.
- `args` arrive verbatim as JSON values; `parseArgs()` also accepts a JSON-encoded string from an older caller.
- Launch by name (`dark-factory:feature-auto`); nested runs by name too. A `scriptPath` (a project's own copy) must stay
  inside the session's tree.
- `export const meta = {…}` stays a plain literal (no template strings): the plugin loader parses it to register the
  name. Files are `.js` — the plugin loader skips other extensions.
- Every exit returns an object with `error` (`null` on success).
- Task status finalisation lives in the script code, not in an instruction to an agent.
- A call to a role is a task (what to do with this input), not an instruction that overrides the role: the `agent()`
  prompt beats the role's system prompt. Standing know-how (how to slice, how to critique) lives in the role, the
  run-specific facts in the call.
- Agent message style — human-readable prose (`MSG_STYLE`), no telegraphic style.
- Long commands (pytest, ruff, npm) — only synchronously (the `SYNC` constant in every `TOPO`): an answer "waiting for a
  background run" is a failed step.
- No model id in the scripts: the model of a role is in `agents/<name>.md`, an `agent()` call without a role and without
  `model` inherits the session's model. A project overrides through `models`; check after a run: `grep -o '"model":"[^"]*"' <transcript>/agent-*.jsonl | sort | uniq -c`.
