export const meta = {
  name: 'feature-auto',
  description:
    'Automated development from a ready requirements source to a committed branch (optionally an open PR). Source: a phase of an approved OpenSpec change (changeDir + phase), a ready spec (specPath) or a bd bug (task description). Setup (branch <type>/<ID>-<slug> with a tracker ID, <type>/<slug> without one, from the base branch; a git worktree when appDir ≠ root) → Plan (decomposition into bd subtasks with files[]/testPlan) → Critic → Bridge (subtasks as children of the bd task, claimed by the owner) → Build (feature-core: waves → integrate → review → fix) → Integrate (merge the base, FULL pytest+ruff gates, project document sync, the PR description, bd close of subtasks) → PR (optional; merge via scripts/merge-feature.sh <branch> once CI is green). Forks → automatic defaults + assumptions. A small feature → delegated to feature-lite. buildMode session: the whole task by ONE builder agent with the stage skills, then one reviewer. One repository, GitHub.',
  phases: [
    { title: 'Session', detail: 'buildMode session: one builder runs the whole task by the stage skills' },
    { title: 'Review', detail: 'buildMode session: one reviewer, a fix only for critical or major findings' },
    { title: 'Setup' },
    { title: 'Plan' },
    { title: 'Split', detail: 'phase wider than the threshold or not agreed — split into end-to-end slices' },
    { title: 'Chunks', detail: 'split chunks via the small path on one branch; review once' },
    { title: 'Critic' },
    { title: 'Bridge' },
    { title: 'Build' },
    { title: 'Integrate' },
    { title: 'PR' },
  ],
}

// --- Input (args) ---
// { request (REQUIRED), stamp (REQUIRED — Date.now is forbidden),
//   bdId (REQUIRED — the run's parent bd task: a phase task or a bug, the FULL bd id <prefix>-<hash>[.N]; subtasks are
//   created as its children), trackerId? (the tracker ID of the change, e.g. DMP-123 or 123 — goes into the branch name),
//   slug? (the change slug; default: the changeDir folder name, else auto-<stamp>; without a trackerId a phase adds -p<K>). The branch is
//   <type>/<trackerId>-<slug> or <type>/<slug>: merge-feature.sh finds the task to close by it
//   (the tracker ID = the task's external_ref, or the slug = the epic label change:<slug>),
//   type?='feat'|'fix'|'ui'|'refactor'|'chore'|'docs' (default feat), baseBranch?='main', appDir? (default: <session dir>/.claude/worktrees/fab-<slug> — git worktree add; ONLY inside
//   the session dir, a path outside → error: external directories cause a permission prompt on every file;
//   equal to the session dir → checkout -b in it), venv? (abs. path of the
//   venv; default <root>/<appSubdir>/.venv), appSubdir?='app' (where the code lives inside the tree:
//   <tree>/<appSubdir>/src, /tests, /alembic — base keeps it in app/; '' for code at the tree root; passed on to
//   feature-core/feature-lite), changeDir?+phase? (OpenSpec change from the repo root + tasks.md phase number) | specPath?
//   (abs. path of the spec) | (neither → bug/task from the bd description), stopBeforePR?=true, noFetch?=false,
//   assignee? (owner scope; default auto-<stamp>), path?='lite'|'full', dbTests?=false, allowMigrations?=false,
//   dbShards? (db lane paths from the tree root, default ['<appSubdir>/tests/integration/db']; a lane longer than 600 s — split into shards,
//     e.g. ['<test shard glob 1>', '<test shard glob 2>']),
//   root? (abs. path of the main checkout — bd Dolt store; otherwise git rev-parse), maxRounds?=6 (feature-core build
//   waves), coreScript?, liteScript? (absolute paths of a project's own copies inside the session dir; default — the
//   plugin workflows dark-factory:feature-core and dark-factory:feature-lite),
//   ignoreSizeGate? (do not measure scope — the phase has already been split by the caller),
//   manualChunks?=false (on a split, return the proposal to the caller instead of running the chunks itself),
//   allowOverlap?=false (do not stop if the plan's files are being edited by another unmerged branch),
//   reviewDims? (which review lenses to run: 'tests' | 'invariants' | 'simplify'; all three by default.
//     On intermediate chunks of a split phase ['tests','invariants'] is reasonable — simplify looks at the shape
//     of the code, which is not final until the last chunk; it runs once, on the last chunk),
//   models? ({plan?, implement?, review?} — model ids by role: plan = the tech-lead role's calls (plan, critic, split); implement = triage, mechanics,
//     workers, integration, PR; review = feature-core's review lenses. Left out, a call keeps the model of its role (agents/<name>.md) or, for
//     mechanics, the session's model.
//     Passed on to feature-core/feature-lite),
//   projectRoles? (names of the roles the project overrides in .claude/agents/; the run finds them itself — see role(); a child run gets the list),
//   buildMode?='session' ('pipeline' — the stages below, each by its own agents, for a wide phase of independent slices; 'session' — ONE builder agent does the
//     whole task by the stage skills (branch, plan, build-tdd and integrate per slice, full gate, wrap), then one reviewer
//     and, for critical or major findings, one fix agent: 2–3 agents, no split, critic or overlap check),
//   maxAgents?=40 (agent calls the whole run may make, children included — the cost cap of an unattended run) }
// --- Result ---
// The harness status of a run is almost always "completed"; the outcome is the `error` field of the returned object.
// error: null — the branch is committed (and the PR opened when stopBeforePR=false). Any other value is a failure or
// an incomplete run: 'no request' | 'no stamp' | 'no bdId' | 'no phase' | 'bad appSubdir' | 'bad buildMode' |
// 'session build failed' |
// 'repo-root resolve failed' | 'budget exhausted' (a token target was set and is spent — resume with the same args) |
// 'agent limit reached' (maxAgents is used up — resume with the same args and a larger maxAgents) |
// 'appDir outside session dir' | 'setup failed' | 'plan failed' | 'migration required but not allowed' |
// 'overlaps in-flight branch' | 'phase too large — split proposed' | 'decomposition rejected by critic' |
// 'decomposition rejected — split proposed' | 'replan failed' | 'bridge failed' | 'core failed: …' | 'chunk <key> failed' |
// 'integrate failed' | 'incomplete: …' (subtasks not implemented, review blockers left, PR not opened). The small path
// returns the result of feature-lite, which follows the same contract. Every result carries agentsUsed and maxAgents.
// Project settings (args; a project keeps its values in .claude/factory/feature-pipeline.json and the session passes them on
// every launch — a plugin workflow has no deployment step): testCmd?, lintCmd? (gate commands; '{tree}' stands for the
// run's working tree; default pytest/ruff under <tree>/<appSubdir>), frontendDir? (a frontend with its own CI lane, path
// from the tree root), i18nFile? (the message catalog), invariantsDoc? (the product invariants for review), surfaces?
// (the project's user-facing surfaces, e.g. 'chat with the bot, mini app, web version'), models?. feature-auto passes
// them on. The plugin's /dark-factory:feature command reads the file and launches the run with them.
// The Workflow tool passes args verbatim as JSON values; a caller that sends a JSON-encoded string still works.
function parseArgs(x) {
  if (typeof x === 'string') {
    try {
      return JSON.parse(x) || {}
    } catch (e) {
      return {}
    }
  }
  return x || {}
}
const a = parseArgs(args)
const TREE_CMD = (cmd, dir) => String(cmd).split('{tree}').join(dir)
const PROJECT = { testCmd: a.testCmd, lintCmd: a.lintCmd, frontendDir: a.frontendDir, i18nFile: a.i18nFile, invariantsDoc: a.invariantsDoc, surfaces: a.surfaces, models: a.models, buildMode: a.buildMode, sessionChunk: a.sessionChunk }
const REQUEST = a.request || ''
const STAMP = a.stamp
const BD_ID = a.bdId || null
const TYPES = ['feat', 'fix', 'ui', 'refactor', 'chore', 'docs']
const TYPE = TYPES.includes(a.type) ? a.type : 'feat'
const TRACKER = a.trackerId ? String(a.trackerId).replace(/[^A-Za-z0-9]+/g, '-') : null
// The branch is the change key: <type>/<ID>-<slug> with a tracker, <type>/<slug> without. Check what
// merge-feature.sh will close: scripts/merge-feature.sh <branch> --print-bd-id.
// No slug given: the change folder's name. A phase without a tracker sub-card gets -p<K>, as merge-feature.sh reads it.
const SLUG = a.slug || (a.changeDir ? String(a.changeDir).replace(/\/+$/, '').split('/').pop() : `auto-${STAMP}`)
const PHASE_SUFFIX = !TRACKER && a.phase != null && !/-p[0-9]+$/.test(SLUG) ? '-p' + a.phase : ''
const BRANCH = TYPE + '/' + (TRACKER ? TRACKER + '-' : '') + SLUG + PHASE_SUFFIX
const BASE = a.baseBranch || 'main'
const APP_ARG = a.appDir || null
const VENV_ARG = a.venv || null
const CHANGE_DIR = a.changeDir || null
const PHASE = a.phase != null ? String(a.phase) : null
const SPEC_PATH = a.specPath || null
const STOP_BEFORE_PR = a.stopBeforePR !== false // default true: the boundary is a local commit; push/PR/merge — a human
const NO_FETCH = !!a.noFetch
const FORCE_PATH = a.path || null
const BUILD_MODE = a.buildMode == null || a.buildMode === '' ? 'session' : String(a.buildMode)
const DB_TESTS = !!a.dbTests
// Code subdirectory inside the tree (base layout: app/). S is the prefix of code paths from the tree root.
const SUB = String(a.appSubdir == null ? 'app' : a.appSubdir).replace(/^\/+|\/+$/g, '')
const S = SUB ? SUB + '/' : ''
const DB_SHARDS = Array.isArray(a.dbShards) && a.dbShards.length ? a.dbShards : [`${S}tests/integration/db`]
const ALLOW_MIGRATIONS = !!a.allowMigrations
// Build waves in feature-core. A plan deeper than a few waves is a phase to split, not to grind through.
const MAX_ROUNDS = a.maxRounds || 6
// Cost limits, checked between stages. A token target (Workflow budget.total) exists only when a human set one
// ("+500k"); an unattended or scheduled run has none, so the agent cap (args.maxAgents) is the limit it always has.
// Every agent call goes through spawn(), which counts it; a child workflow gets what is left (minus the reserve for
// Integrate and PR) and reports its agentsUsed back. The full path needs about 6 (setup, plan, overlap) + up to 6
// (critic rounds) + 1 (bridge) + ~20 (feature-core on an 8-task plan) + 2 (integrate, PR): 40 by default.
const budgetSpent = () => typeof budget !== 'undefined' && budget && budget.total != null && budget.remaining() <= 0
const MAX_AGENTS = Number(a.maxAgents) > 0 ? Math.floor(Number(a.maxAgents)) : 40
let agentsUsed = 0
let capHit = false // an agent call was refused by the cap
const agentsLeft = () => MAX_AGENTS - agentsUsed
function outOfBudget(stage, need = 1) {
  if (budgetSpent()) {
    log(`feature-auto: token budget spent (${budget.total}) before ${stage} — stop; resume with resumeFromRunId and the same args`)
    return { error: 'budget exhausted', stage, branch: BRANCH }
  }
  if (need > agentsLeft()) {
    log(`feature-auto: agent limit ${MAX_AGENTS} — ${agentsUsed} used, ${stage} needs ${need} — stop; resume with resumeFromRunId, the same args and a larger maxAgents`)
    return { error: 'agent limit reached', stage, branch: BRANCH }
  }
  return null
}
async function spawn(prompt, opts) {
  if (agentsUsed >= MAX_AGENTS) {
    capHit = true
    log(`feature-auto: agent limit ${MAX_AGENTS} reached — ${opts.label} not started`)
    return null
  }
  agentsUsed++
  return agent(prompt, opts)
}
// A child run counts against the same cap: it gets what is left minus the reserve, and its agentsUsed is added back.
async function child(ref, childArgs, reserve) {
  const r = await workflow(ref, { ...childArgs, maxAgents: Math.max(1, agentsLeft() - reserve) })
  agentsUsed += (r && Number(r.agentsUsed)) || 0
  return r
}
// Agents Integrate and PR need after the build.
const RESERVE = STOP_BEFORE_PR ? 1 : 2
// run scope: subtasks are assigned to OWNER, the core takes the children of BD_ID (not everything in_progress) — nothing foreign leaks in.
const OWNER = a.assignee || 'auto-' + STAMP

if (!REQUEST) {
  log('feature-auto: empty args.request.')
  return { error: 'no request' }
}
if (!STAMP) {
  log('feature-auto: args.stamp is required (unique suffix). Date.now() is forbidden in scripts.')
  return { error: 'no stamp' }
}
if (!BD_ID) {
  log('feature-auto: args.bdId is required — the run\'s parent bd task (the full bd id <prefix>-<hash>; its subtasks are created as children).')
  return { error: 'no bdId' }
}
if (!/^[A-Za-z0-9._/-]*$/.test(SUB) || SUB.split('/').includes('..')) {
  log(`feature-auto: appSubdir "${SUB}" is not a plain relative path (e.g. 'app', or '' for code at the tree root).`)
  return { error: 'bad appSubdir' }
}
if (BUILD_MODE !== 'pipeline' && BUILD_MODE !== 'session') {
  log(`feature-auto: buildMode "${BUILD_MODE}" is neither 'pipeline' nor 'session'.`)
  return { error: 'bad buildMode' }
}
if (CHANGE_DIR && !PHASE) {
  log('feature-auto: changeDir requires phase (the tasks.md phase number).')
  return { error: 'no phase' }
}

const STEP_SCHEMA = {
  type: 'object',
  required: ['ok', 'summary'],
  properties: { ok: { type: 'boolean' }, summary: { type: 'string' }, details: { type: 'string' } },
}
// The final integration also returns the PR description — the record of the work (## Done / ## Decisions /
// ## Open questions), which scripts/merge-feature.sh requires and writes into the merge commit.
const INTEGRATE_SCHEMA = {
  type: 'object',
  required: ['ok', 'summary', 'prBody'],
  properties: { ...STEP_SCHEMA.properties, prBody: { type: 'string' } },
}
const PLAN_SCHEMA = {
  type: 'object',
  required: ['title', 'tasks', 'assumptions', 'requirementMap', 'migrationNeeded'],
  properties: {
    title: { type: 'string' },
    tasks: {
      type: 'array',
      items: {
        type: 'object',
        required: ['key', 'title', 'files', 'testPlan', 'acceptance', 'blockedBy'],
        properties: {
          key: { type: 'string' }, // local key (e.g. "3.1") — for blockedBy before bd ids exist
          title: { type: 'string' },
          description: { type: 'string' },
          files: { type: 'array', items: { type: 'string' } },
          testPlan: { type: 'string' },
          acceptance: { type: 'string' },
          blockedBy: { type: 'array', items: { type: 'string' } },
        },
      },
    },
    assumptions: { type: 'array', items: { type: 'string' } },
    requirementMap: {
      type: 'array',
      items: {
        type: 'object',
        required: ['requirement', 'tasks'],
        properties: { requirement: { type: 'string' }, tasks: { type: 'array', items: { type: 'string' } } },
      },
    },
    migrationNeeded: { type: 'boolean' },
    details: { type: 'string' },
  },
}
const CRITIC_SCHEMA = {
  type: 'object',
  required: ['approved', 'issues'],
  properties: { approved: { type: 'boolean' }, issues: { type: 'array', items: { type: 'string' } } },
}
const BRIDGE_SCHEMA = {
  type: 'object',
  required: ['ok', 'summary', 'ids'],
  properties: { ok: { type: 'boolean' }, summary: { type: 'string' }, ids: { type: 'array', items: { type: 'string' } }, details: { type: 'string' } },
}

// Model layout. The model of a role lives in the role (agents/<name>.md). args.models is a project's deliberate override
// and wins over it: mechanics (root, branch, overlap, bd writes) and final integration (merge, gates, the PR description)
// take models.implement; the tech-lead calls (plan, critic, split) take models.plan; triage is a mechanical rule on the implement model. Without an override,
// mechanics inherit the session's model and the tech-lead calls use the role's. Pin full ids, not aliases: an alias can
// resolve to an older model. A project may put every role on a cheaper model to save tokens.
const MODELS = a.models && typeof a.models === 'object' ? a.models : {}
const pickModel = (m) => (typeof m === 'string' && m.trim() ? { model: m.trim() } : {})
const SIMPLE = { ...pickModel(MODELS.implement), effort: 'medium' }
const WORKER = pickModel(MODELS.implement)
const ARCHITECT = pickModel(MODELS.plan)
// Roles (plugins/dark-factory/agents/<name>.md) carry the prompt, effort and default model of the tech lead.
// A plugin agent is found only as dark-factory:<name>; a project's own .claude/agents/<name>.md overrides it only when
// called by the bare name. The repo-root step reports which of those files the project has (PROJECT_ROLES); the
// children get the list in args.projectRoles. models.plan stays a deliberate project override of the role's model.
let PROJECT_ROLES = Array.isArray(a.projectRoles) ? a.projectRoles.map(String) : []
const role = (name) => ({ agentType: PROJECT_ROLES.includes(name) ? name : 'dark-factory:' + name })
const TECH_LEAD = () => ({ ...role('tech-lead'), ...ARCHITECT })
// The final integration runs as the builder role, which preloads the integrate and wrap skills.
const BUILDER = () => ({ ...role('builder'), ...WORKER })
const COMMIT_STYLE = 'Commit messages by the integrate skill. No Co-Authored-By.'
const MSG_STYLE =
  'Message style (commit and PR): addressed to a reviewer and a future reader of the history, not to a machine. Subject — the essence of the change (Conventional Commits, in the project language — CODING_STANDARDS.md §Project language, default English — ≤~70 characters). Body — coherent prose: what changes and why. Forbidden: chains "A → B → C"; file paths and symbol names as the main text; JSON in the text; telegraphic style. Details (tests, assumptions) — short bullet points. No Co-Authored-By.'

// === Session mode: the whole run by ONE builder with the stage skills, then one reviewer ===
// Every agent starts with the project's whole context (CLAUDE.md, skills, plugins) — tens of thousands of tokens before
// any work. In session mode the run pays that once: one builder walks the route (branch → plan → build-tdd and integrate
// per slice → full gate → wrap) in its own context. The review stays a separate agent, since a fresh context is its
// point; a fix agent runs only when the review finds critical or major issues. Splitting, the critic and the overlap
// check are pipeline mode only.
const SESSION_SCHEMA = {
  type: 'object',
  required: ['ok', 'summary', 'appDir', 'prBody'],
  properties: {
    ...STEP_SCHEMA.properties,
    appDir: { type: 'string' },
    prBody: { type: 'string' },
    slices: { type: 'array', items: { type: 'string' } },
    assumptions: { type: 'array', items: { type: 'string' } },
  },
}
const REVIEW_SCHEMA = {
  type: 'object',
  required: ['findings'],
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['file', 'summary', 'severity'],
        properties: {
          file: { type: 'string' },
          line: { type: 'number' },
          summary: { type: 'string' },
          severity: { type: 'string', enum: ['critical', 'major', 'minor'] },
          fix: { type: 'string' },
        },
      },
    },
  },
}
async function sessionRun() {
  phase('Session')
  const stop = outOfBudget('Session', STOP_BEFORE_PR ? 2 : 3)
  if (stop) return stop
  const top = '<top>' // the session dir: `git rev-parse --show-toplevel`, resolved by the builder
  const root = a.root || '<root>'
  const tree = APP_ARG || `${top}/.claude/worktrees/fab-${SLUG}`
  const venv = VENV_ARG || `${root}/${S}.venv`
  const tests = a.testCmd ? TREE_CMD(a.testCmd, tree) : `${venv}/bin/python -m pytest ${tree}/${S}tests -q --tb=short`
  const lint = a.lintCmd ? TREE_CMD(a.lintCmd, tree) : `${venv}/bin/ruff check ${tree}/${S}src ${tree}/${S}tests && ${venv}/bin/ruff format --check ${tree}/${S}src ${tree}/${S}tests`
  const source = CHANGE_DIR
    ? `the approved OpenSpec change ${root}/${String(CHANGE_DIR).replace(/^\/+|\/+$/g, '')}: proposal.md (WHEN/THEN scenarios), design.md when present, tasks.md — ONLY phase ${PHASE}. Do not rewrite the spec. A user-visible feature without the "Interaction surfaces" section in proposal.md (surfaces: ${a.surfaces || 'as listed in openspec/README.md §0'}) — stop with ok:false "no decision on interaction surfaces".`
    : SPEC_PATH
      ? `the ready spec ${SPEC_PATH}; no such file — stop with ok:false, do not invent a design.`
      : `the description of bd task ${BD_ID} (\`bd -C ${root} show ${BD_ID}\`). A bug: reproduce it with a failing test first, then fix the root cause.`
  const build = await spawn(
    [
      `Do the whole task "${REQUEST}" (bd task ${BD_ID}) yourself, in this one context, by the factory's stage skills. Source: ${source}`,
      `PATHS. ${top} = \`git rev-parse --show-toplevel\` (the session dir).` + (a.root ? '' : ` ${root} = the main checkout, the first path of \`git -C ${top} worktree list\` (the bd store).`) + ` Working tree: ${tree}. No cd: \`git -C\`, \`bd -C\`, absolute paths. Long commands (tests, lint, npm) run in the foreground only.`,
      `1. BRANCH. ${NO_FETCH ? '' : `\`git -C ${top} fetch origin ${BASE}\`, then `}` + (APP_ARG && APP_ARG === a.root ? `\`git -C ${tree} checkout -b ${BRANCH} origin/${BASE}\`.` : `\`git -C ${top} worktree add -b ${BRANCH} ${tree} origin/${BASE}\`.`) + ` Already there (a resumed run): branch ${BRANCH} exists and ${tree} is on it — keep its commits and continue; the closed bd subtasks are done. A dirty tree or someone else's branch in ${tree} — stop with ok:false.`,
      `2. PLAN by the dark-factory:plan skill (invoke it with the Skill tool): end-to-end slices with files[], testPlan, acceptance, blockedBy. Create them as bd subtasks — children of ${BD_ID}, assignee ${OWNER} (\`timeout 60 bd -C ${root} …\`). ` + (ALLOW_MIGRATIONS ? 'Migrations are allowed.' : 'Migrations are FORBIDDEN in this run: if the task cannot be done without one, stop with ok:false "migration required".'),
      `3. BUILD each slice in dependency order: build-tdd (the test first), then integrate — its scoped tests and lint, one commit, its bd subtask closed. Then the next slice.`,
      `4. FULL GATE once after the last slice (integrate): ${NO_FETCH ? '' : `\`git -C ${tree} fetch origin ${BASE}\`, `}\`git -C ${tree} merge origin/${BASE} --no-edit\` (conflicts by meaning), then \`${tests}\` and \`${lint}\`` + (a.frontendDir ? `, and the frontend checks of ${tree}/${a.frontendDir} per CLAUDE.md` : '') + (DB_TESTS ? `, and the db lane (\`-m db\`) of ${DB_SHARDS.join(', ')}` : '; the db lane is left to CI — say so in the PR description') + '. Red → fix the root cause and commit.',
      `5. WRAP by the wrap skill: a note on ${BD_ID} on where the run stopped (do NOT close it — merge-feature.sh does), the project documents in line, ` + (CHANGE_DIR ? `the done items of phase ${PHASE} ticked [x] in tasks.md, ` : '') + `an ADR for a decision that shapes the architecture; commit them. The PR description (## Done with test counters from the real run, ## Decisions with every assumption you made, ## Open questions, the last line \`Task: ${BD_ID}\`) is returned as prBody, not written to a file.`,
      `At any fork take a safe default and record it in assumptions — this run never asks. NO push, NO PR. ` + COMMIT_STYLE + ' ' + MSG_STYLE,
      'Return {ok, summary, details: test counters and touched docs, appDir: the working tree path, prBody, slices: one line per slice with its bd id, assumptions}.',
    ].join('\n'),
    { label: 'session-build', phase: 'Session', schema: SESSION_SCHEMA, ...BUILDER() },
  )
  if (!build || !build.ok) return { error: 'session build failed', path: 'session', branch: BRANCH, build }
  const appDir = build.appDir || tree
  let prBody = build.prBody

  phase('Review')
  const diff = `git -C ${appDir} diff origin/${BASE}...HEAD`
  const review = await spawn(
    `Read-only review of branch ${BRANCH} (tree ${appDir}), the diff \`${diff}\`, through all lenses at once: the test pyramid (CODING_STANDARDS.md §Test pyramid; a new rule a pure function could decide, tested only through api or integration, is major — push it down to unit; excess tests — duplicates of a lower layer, more than three api tests per route, assertions on message text, layout tests — are findings too),the project invariants (CLAUDE.md, CODING_STANDARDS.md${a.invariantsDoc ? ', ' + a.invariantsDoc : ''}), simplification, correctness. Read the code around the diff too. Findings and severity per docs/review-contract.md. Return {findings}.`,
    { label: 'review', phase: 'Review', schema: REVIEW_SCHEMA, ...role('reviewer'), ...pickModel(MODELS.review) },
  )
  const findings = (review && review.findings) || []
  const fixable = findings.filter((f) => f.severity !== 'minor')
  let fix = null
  if (fixable.length) {
    fix = await spawn(
      [
        `Apply the review fixes on branch ${BRANCH} (tree ${appDir}), or reject one deliberately with a reason (docs/review-contract.md). SCOPE: change only what each finding names, the smallest change that resolves it. A fix that would change visible behaviour, design or texts beyond what the source asked for is a product decision: reject it and name it for the human under ## Open questions. No cd: \`git -C\`, absolute paths.`,
        'Findings: ' + JSON.stringify(fixable),
        `Then the full gate once (\`${tests}\`, \`${lint}\`) and a commit. ` + COMMIT_STYLE,
        `Return the PR description updated with what was applied and rejected (under ## Decisions) as prBody. The current one: ${JSON.stringify(prBody || '')}. Return {ok, summary, details, prBody}.`,
      ].join('\n'),
      { label: 'fix', phase: 'Review', schema: INTEGRATE_SCHEMA, ...BUILDER() },
    )
    if (fix && fix.ok && fix.prBody) prBody = fix.prBody
  }
  const blockers = fix && fix.ok ? [] : findings.filter((f) => f.severity === 'critical')

  let pr = null
  if (!STOP_BEFORE_PR) {
    phase('PR')
    pr = await spawn(
      [
        `Push the branch (\`git -C ${appDir} push origin ${BRANCH}\`) and open a PR into ${BASE} with this description, passed on stdin: \`gh pr create --base ${BASE} --head ${BRANCH} --title "<title>" --body-file - <<'EOF'\` … \`EOF\`. Do NOT merge (the session merges afterwards: scripts/merge-feature.sh ${BRANCH}).`,
        `The description, as is: ${JSON.stringify(prBody || '')}. The title in human-readable prose: what changes, for a reader of the merge history.`,
        MSG_STYLE,
        'Return {ok, summary, details: link to the PR}.',
      ].join('\n'),
      { label: 'pr', phase: 'PR', schema: STEP_SCHEMA, ...WORKER },
    )
  }
  const problems = [
    !review ? 'review did not run' : null,
    fixable.length && !(fix && fix.ok) ? 'review fixes not applied' : null,
    blockers.length ? `${blockers.length} critical review blocker(s) remain` : null,
    !STOP_BEFORE_PR && (!pr || !pr.ok) ? 'PR not opened' : null,
  ].filter(Boolean)
  return {
    error: problems.length ? 'incomplete: ' + problems.join('; ') : null,
    path: 'session',
    branch: BRANCH,
    appDir,
    bdId: BD_ID,
    slices: build.slices || [],
    assumptions: build.assumptions || [],
    findings,
    fix,
    blockers,
    prBody: prBody || null,
    pr,
    note: `Automatic boundary — ${STOP_BEFORE_PR ? 'local commit' : 'open PR'} on branch ${BRANCH} (tree ${appDir}). Before merge: review the assumptions and the PR description (prBody; with stopBeforePR the session opens the PR with it: gh pr create --draft --body-file -), green CI → scripts/merge-feature.sh ${BRANCH}.`,
  }
}

// The run itself; every result it returns gets the agent count of the whole run (children included).
const result = await (async () => {
if (BUILD_MODE === 'session') return await sessionRun()
// === 0. Setup: repo root → working tree → branch from a fresh base ===
phase('Setup')
// The session directory (its cwd) is the only zone where agents work without permission prompts and where Workflow
// runs nested scripts. feature-core/feature-lite are taken from it and the run's working tree is created in it.
const topStep = await spawn(
  'Run `git rev-parse --show-toplevel` (no cd) and return {ok:true, summary: absolute path of the repository root, details: the file names without the .md extension, comma-separated, that `ls <that root>/.claude/agents` lists (empty when the folder does not exist)}.',
  { label: 'repo-root', phase: 'Setup', schema: STEP_SCHEMA, ...SIMPLE },
)
if (!topStep || !topStep.ok) return { error: 'repo-root resolve failed' }
const SESSION_TOP = topStep.summary.trim().replace(/\/+$/, '')
if (!PROJECT_ROLES.length) PROJECT_ROLES = String(topStep.details || '').split(/[\s,]+/).map((x) => x.trim().split('/').pop().replace(/\.md$/, '')).filter(Boolean)
const ROOT = (a.root || SESSION_TOP).replace(/\/+$/, '')
const APP = (APP_ARG || `${SESSION_TOP}/.claude/worktrees/fab-${SLUG}`).replace(/\/+$/, '')
if (APP !== SESSION_TOP && !APP.startsWith(SESSION_TOP + '/')) {
  log(`feature-auto: appDir ${APP} is outside the session dir ${SESSION_TOP} — agents would get a permission prompt on every file. Do not pass appDir (default ${SESSION_TOP}/.claude/worktrees/fab-<slug>) or give a path inside the session.`)
  return { error: 'appDir outside session dir', appDir: APP, sessionDir: SESSION_TOP }
}
const USE_WORKTREE = APP !== ROOT && APP !== SESSION_TOP
const VENV = VENV_ARG || `${ROOT}/${S}.venv`
const CODE = S ? `${APP}/${SUB}` : APP // code root of the run's tree
const CHANGE_ABS = CHANGE_DIR ? `${ROOT}/${String(CHANGE_DIR).replace(/^\/+/, '').replace(/\/+$/, '')}` : null
// Children by plugin workflow name (workflow('<plugin>:<meta.name>')), or a project's own copy by path.
const CORE_SCRIPT = a.coreScript ? { scriptPath: a.coreScript } : 'dark-factory:feature-core'
const LITE_SCRIPT = a.liteScript ? { scriptPath: a.liteScript } : 'dark-factory:feature-lite'

const NOCD = ' Changing directory is FORBIDDEN: no `cd` at all — not bare, not in a subshell `(cd … && …)`, not `cd … &&` — it triggers permission prompts and breaks the CWD. Always use absolute paths: `git -C`, `bd -C`, `pytest <abs path>`, `ruff check <abs path>`, `grep -rn … <abs path>`, `rg`, `find <abs path>`, `sed -n … <abs path>`, `head <abs path>`.'
// A background run returns before its outcome is known — such a step counts as failed.
const SYNC = ' LONG COMMANDS (pytest, ruff, npm) — ONLY synchronously: background runs (run_in_background, `&`, nohup) are forbidden; do not return a result until the run has finished and you have read its outcome. Answering "waiting for a background run" is a failed step.'
// Seam: the feature execution queue (docs/functions.md) = beads — every `bd -C <root> …` in these prompts (create,
// claim, note, close). To use another queue, rewrite those calls.
const TOPO = `TOPOLOGY: one repository. The run's working tree is ${APP} (code ${S}src/, ${S}tests/, ${S}alembic/ — paths from the tree root; harness openspec/ — in the same tree). git — \`git -C ${APP} …\`, tests — \`${VENV}/bin/python -m pytest ${CODE}/tests/… -q\`. bd — \`bd -C ${ROOT} …\`.`+NOCD+SYNC
// A user-visible feature covers every project surface, not only the one the request came from.
// The surface completeness gate is openspec/README.md §0.
// The list of surfaces is per project: the surfaces arg, else openspec/README.md §0 and CLAUDE.md name them.
const SURFACE_LIST = a.surfaces ? String(a.surfaces) : 'as listed in openspec/README.md §0 and the CLAUDE.md operating contract'
const SURFACES = ' SURFACES: a user-visible feature must have an "Interaction surfaces" section in proposal.md — for each project surface (' + SURFACE_LIST + ') the entry point, action and result, or an explicit "not here", confirmed by the requester or the owner; and a "Transition tree" in design.md. No section or no confirmation — STOP with the error "no decision on interaction surfaces"; do not plan and do not fill in the gaps.'
const SOURCE = CHANGE_ABS
  ? `REQUIREMENTS SOURCE — the approved OpenSpec change ${CHANGE_ABS}: proposal.md (with the WHEN/THEN acceptance scenarios), design.md when present (decisions, transition tree, as-is with file:line), tasks.md — section "Phase ${PHASE}" (items ${PHASE}.1, ${PHASE}.2, …). ONLY this phase is implemented; do not touch other phases. Do NOT rewrite the spec.`+SURFACES
  : SPEC_PATH
    ? `REQUIREMENTS SOURCE — the ready spec ${SPEC_PATH} (written by a human BEFORE the run; a precondition). If the file does not exist — return an error, do not invent a design.`+SURFACES
    : `REQUIREMENTS SOURCE — the description of bd task ${BD_ID} (\`bd -C ${ROOT} show ${BD_ID}\`): a bug/task with evidence and repro; there is no spec. For a bug — systematic-debugging: reproduce with a test (RED), find the root cause, fix.`

const setupGit = USE_WORKTREE
  ? `${NO_FETCH ? '' : `git -C ${SESSION_TOP} fetch origin ${BASE}; then `}create a SEPARATE git worktree: \`git -C ${SESSION_TOP} worktree add -b ${BRANCH} ${APP} origin/${BASE}\`. Make sure: HEAD in ${APP} is on ${BRANCH}, the tree is clean (\`git -C ${APP} status --porcelain\` is empty, except gitignored files).`
  : `${NO_FETCH ? '' : `git -C ${APP} fetch origin ${BASE}; then `}\`git -C ${APP} checkout -b ${BRANCH} origin/${BASE}\`. Make sure: HEAD is on ${BRANCH}, the tree is clean.`
const setup = await spawn(
  [
    `Create feature branch ${BRANCH} from the fresh base origin/${BASE} (CLAUDE.md canon: <type>/<ID>-<slug> with a tracker, <type>/<slug> without — merge-feature.sh closes the task by that change key).`,
    setupGit,
    `IDEMPOTENCY (run resume): if branch ${BRANCH} already exists AND tree ${APP} is already on it (\`git -C ${APP} rev-parse --abbrev-ref HEAD\`) AND the tree is clean — this is a repeated Setup: create nothing, return ok:true.`,
    // A tree left over from the previous chunk of the run is normal, not a breakage: after
    // the merge it is switched to detached on the base branch and reused.
    `TREE REUSE: if path ${APP} is already registered as a worktree, branch ${BRANCH} does not exist yet, and the tree is CLEAN (\`git -C ${APP} status --porcelain\` is empty) and sits on a detached HEAD or on the base branch — do NOT delete it and do not ask for another path: just create the branch in place, \`git -C ${APP} switch -c ${BRANCH} origin/${BASE}\`. This is a normal case: the tree is left over from the previous chunk of the same train.`,
    `ok:false with an explanation — only if the tree is DIRTY, or is occupied by SOMEONE ELSE'S feature branch (neither ${BRANCH} nor the base), or branch ${BRANCH} exists but the tree is on a different branch. No cd — only git -C.`,
    `Return {ok, summary, details: branch name, base SHA, tree path}.`,
  ].join('\n'),
  { label: 'setup', phase: 'Setup', schema: STEP_SCHEMA, ...SIMPLE },
)
if (!setup || !setup.ok) return { error: 'setup failed', branch: BRANCH, setup }
log(`Setup: branch ${BRANCH} from origin/${BASE}, tree ${APP}${USE_WORKTREE ? ' (worktree)' : ''}`)

// === 0b. Scale gating: a small self-contained task → feature-lite (the small path is ~5× cheaper). An explicit path skips triage. ===
if (FORCE_PATH !== 'full') {
  let route = FORCE_PATH
  if (!route) {
    const triage = await spawn(
      [
        `Size this work for the pipeline: "small path" (lite) or "full" (orchestration). Request: "${REQUEST}". ${SOURCE}`,
        `Look at the ACTUAL code in ${CODE}/src and estimate: how many files it will really touch; is there a contract change (DB migration / API / user flow / new service); does it split into several parallel tasks with disjoint files.`,
        'Rule: lite — if ALL are "yes": ≤~5 files (with tests), no migration and no contract change, does not split into parallel tasks. Otherwise full.',
        'Return {ok:true, summary:"lite" OR "full", details: justification}. No cd — absolute paths.',
      ].join('\n'),
      { label: 'triage', phase: 'Setup', schema: STEP_SCHEMA, ...SIMPLE }  // a mechanical lite/full rule: the cheap model,
    )
    route = triage && /^\s*lite\b/i.test(triage.summary) ? 'lite' : 'full'
    log('Scale gating: path "' + route + '"' + (triage && triage.details ? ' — ' + triage.details : ''))
  }
  if (route === 'lite') {
    log('Delegating to feature-lite (small path) — the branch already exists, feature-lite reuses it (branchReady).')
    const stopLite = outOfBudget('feature-lite')
    if (stopLite) return stopLite
    const lite = await child(
      LITE_SCRIPT,
      {
        request: REQUEST + (CHANGE_ABS ? ` (OpenSpec ${CHANGE_ABS}, phase ${PHASE})` : ''),
        stamp: STAMP,
        slug: SLUG,
        type: TYPE,
        trackerId: TRACKER,
        baseBranch: BASE,
        specPath: SPEC_PATH || (CHANGE_ABS ? `${CHANGE_ABS}/tasks.md` : null),
        stopBeforePR: STOP_BEFORE_PR,
        noFetch: true,
        branchReady: true,
        assignee: OWNER,
        appDir: APP,
        root: ROOT,
        venv: VENV,
        bdId: BD_ID,
        appSubdir: SUB,
        projectRoles: PROJECT_ROLES,
        ...PROJECT,
      },
      0,
    )
    return lite || { error: 'feature-lite returned no result', branch: BRANCH }
  }
}

// === 1. Plan — decompose the source into run subtasks (files[] disjoint within a wave, TDD pairs test+code) ===
phase('Plan')
const stopPlan = outOfBudget('Plan', 2)
if (stopPlan) return stopPlan
const plan = await spawn(
  [
    `Plan the implementation of "${REQUEST}" (bd task ${BD_ID}) as a graph of subtasks by the plan skill (steps 1–7; step 8 — return, the run creates the subtasks). Read CLAUDE.md and CODING_STANDARDS.md (${APP}), then the source. ${SOURCE}`,
    TOPO,
    `The code lives in ${CODE}/src (migrations ${S}alembic/versions). For the parallel build: within one wave (tasks without mutual blockedBy) files[] do NOT overlap — express overlap via blockedBy.`,
    'Each task: key (e.g. "3.1"), title (in the project language, ≤70), description (what exactly to do, grounded in the spec requirements — requirement id/name), files[] (paths from the repo root, including NEW files and test files), testPlan (each test with its layer from CODING_STANDARDS.md §Test pyramid and what exactly it verifies; a rule at the lowest layer that can check it — a pure function in unit, api only for the HTTP contract), acceptance (verifiable criteria), blockedBy[] (keys).',
    // Test paths written from memory and method names that do not exist in the code are the
    // commonest critic remarks. The check is mandatory and cheap.
    `THE CHECK OF STEP 6 IS MANDATORY (otherwise the plan is rejected): paths with \`ls\` from ${APP}, test files with \`find ${CODE}/tests -name "<name>"\`, methods with \`grep -rn "def <name>" ${CODE}/src\`; message catalog keys added by a task that edits ${a.i18nFile || "the project's message catalog"}, covering ALL snapshots and branches named in the source.`,
    'At ANY fork — a safe default recorded in assumptions[]. Do NOT ask — this is automatic mode. Do NOT write code and do NOT commit.',
    `migrationNeeded=true if an alembic migration is needed. ${ALLOW_MIGRATIONS ? 'Migrations are allowed (per the spec).' : 'Migrations are FORBIDDEN in this run — if it cannot be done without one, still return the plan with migrationNeeded=true (the orchestrator will stop).'}`,
    'requirementMap: requirement (a MUST from the spec / a tasks.md item / a bug acceptance criterion) → keys of the tasks that close it. Return {title, tasks, assumptions, requirementMap, migrationNeeded, details}.',
  ].join('\n'),
  { label: 'plan', phase: 'Plan', schema: PLAN_SCHEMA, ...TECH_LEAD() },
)
if (!plan || !plan.tasks || plan.tasks.length === 0) return { error: 'plan failed', branch: BRANCH, plan }
log(`Plan: "${plan.title}", ${plan.tasks.length} tasks, ${plan.requirementMap.length} requirements, ${plan.assumptions.length} assumptions${plan.migrationNeeded ? ', migration needed' : ''}`)
if (plan.migrationNeeded && !ALLOW_MIGRATIONS) {
  log('feature-auto: the plan needs an alembic migration while allowMigrations=false — stop; rerun with allowMigrations:true if the spec allows a migration.')
  return { error: 'migration required but not allowed', branch: BRANCH, plan }
}

// === 1a. Overlap with parallel work. ===
// Two features built at the same time on shared code introduce the same schema or helper twice —
// a merge conflict and manual cleanup of duplicates. The shared layer is built first, the second
// feature builds on top of it. We compare code (<appSubdir>/src, tests, alembic), not docs:
// naming, the message catalog and openspec merge normally.
const OVERLAP_SCHEMA = {
  type: 'object',
  required: ['overlaps'],
  properties: {
    overlaps: {
      type: 'array',
      items: { type: 'object', required: ['branch', 'files'], properties: { branch: { type: 'string' }, files: { type: 'array', items: { type: 'string' } } } },
    },
  },
}
const PLAN_CODE_FILES = [...new Set(plan.tasks.flatMap((t) => t.files || []))].filter((f) => f.startsWith(S) && /^(src|tests|alembic)\//.test(f.slice(S.length)) && !/resources\/messages\//.test(f))
if (!a.allowOverlap && PLAN_CODE_FILES.length) {
  const ov = await spawn(
    [
      `Find local branches NOT merged into origin/${BASE}, other than ${BRANCH}: \`git -C ${APP} for-each-ref --format='%(refname:short)' refs/heads/\` and for each one \`git -C ${APP} merge-base --is-ancestor <branch> origin/${BASE}\` (exit code 0 — already in the base, skip) and \`git -C ${APP} merge-base --is-ancestor <branch> HEAD\` (exit code 0 — already merged into this branch, skip).`,
      `For each unmerged one: \`git -C ${APP} diff --name-only origin/${BASE}...<branch>\` and its intersection with the plan files: ${JSON.stringify(PLAN_CODE_FILES)}.`,
      'Return {overlaps:[{branch, files: intersection}]} only for branches with a non-empty intersection; none — {overlaps:[]}. Change nothing.' + NOCD,
    ].join('\n'),
    { label: 'overlap', phase: 'Plan', schema: OVERLAP_SCHEMA, ...SIMPLE },
  )
  const overlaps = (ov && ov.overlaps) || []
  if (overlaps.length) {
    log(`Overlap with unmerged work: ${overlaps.map((o) => `${o.branch} (${o.files.join(', ')})`).join('; ')} — merge that branch first (or merge it into this one), then restart; to continue deliberately — allowOverlap:true`)
    return { error: 'overlaps in-flight branch', branch: BRANCH, overlaps, plan }
  }
}

// === 1b. Phase size. ===
// The critic can say "the plan is bad" but not "the task is the wrong size": on a wide phase
// every round finds a NEW behaviour branch, and no plan lists them all in one go. So the scope
// is measured before the critic and, if the phase is too wide, the phase is split.
const SPLIT_SCHEMA = {
  type: 'object',
  required: ['chunks'],
  properties: {
    chunks: {
      type: 'array',
      items: {
        type: 'object',
        required: ['key', 'title', 'request', 'files'],
        properties: {
          key: { type: 'string' },
          title: { type: 'string' },
          request: { type: 'string' },
          files: { type: 'array', items: { type: 'string' } },
          dependsOn: { type: 'array', items: { type: 'string' } },
        },
      },
    },
    rationale: { type: 'string' },
  },
}
const planFiles = new Set()
for (const t of plan.tasks) for (const f of t.files || []) planFiles.add(f)
// Only the amount of work is measured — tasks and files. The number of requirements is not a size:
// a small chunk can close many requirements with a few tasks.
const OVERSIZED = plan.tasks.length > 8 || planFiles.size > 25
if (OVERSIZED && !a.ignoreSizeGate) {
  phase('Split')
  const stopSplit = outOfBudget('Split')
  if (stopSplit) return { ...stopSplit, plan }
  log(`Phase size: ${plan.tasks.length} tasks, ${planFiles.size} files, ${(plan.requirementMap || []).length} requirements — above the threshold, proposing a split`)
  const split = await spawn(
    [
      `Phase "${plan.title}" for "${REQUEST}" is too wide for one run: ${plan.tasks.length} tasks, ${planFiles.size} files, ${(plan.requirementMap || []).length} requirements. ${SOURCE}`,
      TOPO,
      'Plan: ' + JSON.stringify({ tasks: plan.tasks, requirementMap: plan.requirementMap }),
      'Propose a split of the phase into 2–4 SEQUENTIAL chunks built on one branch, sliced end to end: each chunk, once committed, works from the entry point to the result with an entry-level test (never by layers).',
      'For each chunk: key (e.g. "5a"), title (in the project language), request (the full request text for a separate run — self-contained, with the source requirements this chunk closes), files[] (which files it touches), dependsOn[] (keys of chunks that must be merged earlier).',
      'Chunk boundaries by file should overlap minimally: a file edited by two chunks goes to the earlier one. Return {chunks, rationale}.',
    ].join('\n'),
    { label: 'split', phase: 'Split', schema: SPLIT_SCHEMA, ...TECH_LEAD() },
  )
  if (split && split.chunks && split.chunks.length > 1) {
    if (a.manualChunks) {
      log(`Split: ${split.chunks.map((c) => c.key).join(', ')} — manualChunks: the caller starts the runs one by one`)
      return { error: 'phase too large — split proposed', branch: BRANCH, plan, split }
    }
    return await runChunks(split.chunks, 'phase wider than the threshold')
  }
  log('Split failed — going to the critic as is')
}

// === 2. Critic — replaces the human approval of the breakdown. Small plan (≤2 tasks, ≤3 assumptions, no migration) → skip. ===
phase('Critic')
const SMALL_PLAN = plan.tasks.length <= 2 && plan.assumptions.length <= 3 && !plan.migrationNeeded
let critic = SMALL_PLAN ? { approved: true, issues: [] } : null
if (SMALL_PLAN) log(`Critic: small plan (${plan.tasks.length} tasks, ${plan.assumptions.length} assumptions) → critic skipped`)
let planFinal = plan
// Three rounds: the critic finds something new on every pass, and one re-plan is rarely enough.
const CRITIC_ROUNDS = 3
for (let attempt = 0; !SMALL_PLAN && attempt < CRITIC_ROUNDS; attempt++) {
  const stopCritic = outOfBudget('Critic', 2) // a critic round, then a re-plan or the rescue split
  if (stopCritic) return { ...stopCritic, plan: planFinal }
  critic = await spawn(
    [
      `Independent review of the plan "${planFinal.title}" for "${REQUEST}" (replaces a human approval). ${SOURCE}`,
      TOPO,
      'Plan: ' + JSON.stringify({ tasks: planFinal.tasks, requirementMap: planFinal.requirementMap, assumptions: planFinal.assumptions }),
      'Check: (1) COVERAGE — every MUST requirement of the phase/spec and every tasks.md item of the phase is closed by a task; the acceptance criteria of the bd task (`bd show`) are covered by testPlan; (2) GRANULARITY — no monoliths and no empty tasks, TDD pairs test+code in one task; (3) FILES — within a wave (tasks without mutual blockedBy) files[] do not overlap, paths are real (existing files exist, new ones are in the right layer); (4) DEPENDENCIES are correct and acyclic; (5) PREMISE — the plan does not contradict the actual code and the anti-patterns of CLAUDE.md / CODING_STANDARDS.md, flags default to false, the old path is kept; (6) assumptions do not override decisions already made in design.md/proposal; (7) SWITCH-OFF AND REUSE — for every endpoint and every piece of stored user data, the behaviour with the flag off/on rollback is defined (revoking and deleting what the user saved — secrets, connections — works regardless of the flag); for limiters, clients, helpers the plan uses the ones that already exist in the code (find them by search) rather than writing its own; (8)' + SURFACES + ' A plan that covers only part of the surfaces in the section — do not approve.',
      'Return {approved, issues[]}. approved=false on any substantial problem (issues — concretely, what to fix and how).',
    ].join('\n'),
    { label: attempt ? 'critic:recheck' : 'critic', phase: 'Critic', schema: CRITIC_SCHEMA, ...TECH_LEAD() },
  )
  if (!critic || critic.approved) break
  if (attempt === CRITIC_ROUNDS - 1) {
    // The critic used up its rounds and found something NEW every time — a sign not of a bad plan
    // but of a task that is too wide. Return a split to the caller instead of a bare refusal.
    phase('Split')
    log('The critic did not accept the plan within the allotted rounds — proposing a phase split')
    const rescue = await spawn(
      [
        `Phase "${planFinal.title}" for "${REQUEST}" was not agreed in ${CRITIC_ROUNDS} rounds: the critic kept finding new unaccounted behaviour branches. ${SOURCE}`,
        TOPO,
        'Last plan: ' + JSON.stringify({ tasks: planFinal.tasks, requirementMap: planFinal.requirementMap }),
        'Remarks of the last round: ' + JSON.stringify(critic.issues),
        'Propose a split of the phase into 2–4 SEQUENTIAL chunks built on one branch, sliced end to end: each chunk, once committed, works from the entry point to the result with an entry-level test (never by layers). Distribute the critic\'s remarks verbatim to the chunks they concern.',
        'For each chunk: key, title (in the project language), request (the full self-contained request text for a separate run, including the critic remarks relevant to the chunk), files[], dependsOn[]. Return {chunks, rationale}.',
      ].join('\n'),
      { label: 'split:rescue', phase: 'Split', schema: SPLIT_SCHEMA, ...TECH_LEAD() },
    )
    if (rescue && rescue.chunks && rescue.chunks.length > 1 && !a.manualChunks) return await runChunks(rescue.chunks, 'the critic did not accept the plan as a whole')
    return {
      error: rescue && rescue.chunks && rescue.chunks.length > 1 ? 'decomposition rejected — split proposed' : 'decomposition rejected by critic',
      branch: BRANCH,
      issues: critic.issues,
      split: rescue || null,
    }
  }
  log(`Critic rejected (round ${attempt + 1} of ${CRITIC_ROUNDS - 1}): ${JSON.stringify(critic.issues)}`)
  const replan = await spawn(
    [
      `Rework the plan according to the critic's remarks: ${JSON.stringify(critic.issues)}. ${SOURCE}`,
      TOPO,
      'Current plan: ' + JSON.stringify(planFinal),
      `Same rules (granularity, disjoint waves, TDD pairs, assumptions, requirementMap). Before returning, re-check on disk every path in files[] (\`ls\`, \`find ${CODE}/tests -name "<name>"\`) and every method or field name (\`grep -rn\` over ${CODE}/src) — critic remarks about non-existent paths and methods must not repeat. If the problem is in the premise (it cannot be implemented) — return tasks:[] and explain in details. Do NOT write code.`,
    ].join('\n'),
    { label: 'replan', phase: 'Critic', schema: PLAN_SCHEMA, ...TECH_LEAD() },
  )
  if (!replan || !replan.tasks || replan.tasks.length === 0) return { error: 'replan failed', branch: BRANCH, issues: critic.issues, replan }
  planFinal = replan
}

// Critic remarks with approved=true (non-critical, "take into account during implementation") are not lost: into subtask notes and the PR description.
const CRITIC_NOTES = (critic && critic.approved && critic.issues) || []
function notesFor(key) {
  // the key is mentioned inside square brackets at the start of a remark: "[3.1 testPlan]", "[3.4/3.6, …]"
  return CRITIC_NOTES.filter((n) => new RegExp('^\\[[^\\]]*(?<![0-9.])' + key.replace('.', '\\.') + '(?![0-9.])[^\\]]*\\]').test(n))
}

// === 3. Bridge — plan → bd subtasks (children of BD_ID) with dependencies, claimed by OWNER ===
phase('Bridge')
const stopBridge = outOfBudget('Bridge', 1 + 4 + RESERVE) // bridge, the smallest core run, Integrate and PR
if (stopBridge) return { ...stopBridge, plan: planFinal }
const bridge = await spawn(
  [
    `Create the run's subtasks in bd as CHILDREN of task ${BD_ID}. All bd commands — \`bd -C ${ROOT} …\` (no cd). Do NOT close or reassign the parent.`,
    `IDEMPOTENCY (run resume): FIRST \`bd -C ${ROOT} list --parent ${BD_ID} --status open,in_progress,closed --json\` (without --status bd shows only open ones). If children with the auto-run label already exist and their design contains "key: <key>" for the plan keys — do NOT create duplicates: return the existing ids (in the order of the plan keys, in any status), and create the missing keys. If there are no children — create them all.`,
    'For EACH plan task: `bd -C ' + ROOT + ' create --parent=' + BD_ID + ' --type=task --priority=1 --title="<title>" --description="<description>\\n\\nFILES: <comma-separated files>\\nTESTS: <testPlan>" --acceptance="<acceptance>" --design="key: <key>" --labels=auto-run` — then `bd -C ' + ROOT + ' update <new-id> --assignee=' + OWNER + ' --claim` (status in_progress, run scope). Creation order — by key.',
    'Dependencies: for each blockedBy — `bd -C ' + ROOT + ' dep add <task id> <dependency id>` (the task depends on the dependency).',
    'Plan: ' + JSON.stringify(planFinal.tasks.map((t) => ({ key: t.key, title: t.title, description: t.description || '', files: t.files, testPlan: t.testPlan, acceptance: t.acceptance, blockedBy: t.blockedBy, criticNotes: notesFor(t.key) }))),
    'If a task has criticNotes — add them to the description as a block "CRITIC REMARKS (take into account during implementation):".',
    `Check: \`bd -C ${ROOT} list --parent ${BD_ID} --status in_progress\` shows all the created ones (the queue is a local store: nothing to commit).`,
    'Return {ok, summary, ids: [created ids in key order], details: key → id mapping}.',
  ].join('\n'),
  { label: 'bridge', phase: 'Bridge', schema: BRIDGE_SCHEMA, ...SIMPLE },
)
if (!bridge || !bridge.ok) return { error: 'bridge failed', branch: BRANCH, bridge }
log(`Bridge: ${(bridge.ids || []).length} subtasks under ${BD_ID}`)

// === 4. Build — feature-core (children of BD_ID → waves → integrate → review → fix) ===
phase('Build')
const core = await child(
  CORE_SCRIPT,
  {
    appDir: APP,
    root: ROOT,
    baseBranch: BASE,
    venv: VENV,
    parentId: BD_ID,
    assignee: OWNER,
    changeDir: CHANGE_ABS,
    specPath: SPEC_PATH,
    projectRoles: PROJECT_ROLES,
    skipFinalGates: true,
    dbTests: DB_TESTS,
    dbShards: DB_SHARDS,
    allowMigrations: ALLOW_MIGRATIONS,
    maxRounds: MAX_ROUNDS,
    reviewDims: a.reviewDims,
    appSubdir: SUB,
    ...PROJECT,
  },
  RESERVE,
)
const blockers = (core && core.blockersRemaining) || []
const reviewOutcome = { fixes: (core && core.applied && core.applied.details) || null, minor: (core && core.reviewMinor) || [] }
const coreImplemented = (core && core.implemented) || []
const assumptions = [...planFinal.assumptions, ...((core && core.assumptions) || [])]
log(`Build: ${coreImplemented.length} of ${planFinal.tasks.length} implemented, ${blockers.length} blockers remaining`)
if (coreImplemented.length === 0) {
  if (core && core.error) return { error: 'core failed: ' + core.error, branch: BRANCH, core }
  return { error: 'core failed: zero implemented tasks — check the bd graph and the feature-core logs', branch: BRANCH, core }
}

return await finish({
  planFinal,
  coreImplemented,
  blockers,
  reviewOutcome,
  assumptions,
  CRITIC_NOTES,
  subtasks: bridge.ids || [],
  criticApproved: !critic || critic.approved,
  chunks: null,
  incomplete: (core && core.error) || null,
})

// === 1c. Split phase — chunks in sequence on ONE branch, via the small path; review once ===
// A full run per chunk (plan, critic, bridge, review each time) costs several times more tokens.
// The split is made by the architect and the chunks are self-contained, so each one is built by
// feature-lite (build + full gates, no wrap-up), and review and integration run once over the
// whole branch — which also catches defects that no single chunk shows.
async function runChunks(chunks, why) {
  phase('Chunks')
  log(`Split (${why}): ${chunks.map((c) => c.key).join(', ')} — chunks via the small path on branch ${BRANCH}, review once over the whole branch`)
  const done = []
  for (const c of chunks) {
    // a chunk's build, then the branch review (up to 3 lenses + fix), Integrate and PR
    const stopChunk = outOfBudget(`chunk ${c.key}`, 1 + 4 + RESERVE)
    if (stopChunk) return { ...stopChunk, chunksDone: done.map((d) => d.key) }
    const r = await child(
      LITE_SCRIPT,
      {
        request: `${c.request}\n\nCHUNK ${c.key} (${done.length + 1} of ${chunks.length}) of phase "${plan.title}". Already committed to this branch: ${done.map((d) => d.key).join(', ') || 'nothing'} — build on them, do not duplicate.`,
        stamp: `${STAMP}-${c.key}`,
        slug: SLUG,
        type: TYPE,
        trackerId: TRACKER,
        baseBranch: BASE,
        specPath: SPEC_PATH || (CHANGE_ABS ? `${CHANGE_ABS}/tasks.md` : null),
        stopBeforePR: true,
        noFetch: true,
        branchReady: true,
        skipWrap: true,
        allowMigrations: ALLOW_MIGRATIONS,
        assignee: OWNER,
        appDir: APP,
        root: ROOT,
        venv: VENV,
        bdId: BD_ID,
        appSubdir: SUB,
        projectRoles: PROJECT_ROLES,
        ...PROJECT,
      },
      4 + RESERVE,
    )
    if (!r || r.error || !r.build || !r.build.ok || !r.build.gatesPassed) {
      log(`Chunk ${c.key} did not build — stop; finished chunks: ${done.map((d) => d.key).join(', ') || 'none'}`)
      return { error: `chunk ${c.key} failed`, branch: BRANCH, chunk: c, result: r, chunksDone: done.map((d) => d.key) }
    }
    done.push({ key: c.key, title: c.title, build: r.build })
  }
  phase('Build')
  // reviewOnly without parentId/assignee makes feature-core exit without a review — pass both.
  const core = await child(
    CORE_SCRIPT,
    {
      appDir: APP,
      root: ROOT,
      baseBranch: BASE,
      venv: VENV,
      parentId: BD_ID,
      assignee: OWNER,
      reviewOnly: true,
      projectRoles: PROJECT_ROLES,
      changeDir: CHANGE_ABS,
      specPath: SPEC_PATH,
      skipFinalGates: true,
      dbTests: DB_TESTS,
      dbShards: DB_SHARDS,
      allowMigrations: ALLOW_MIGRATIONS,
      reviewDims: a.reviewDims,
      appSubdir: SUB,
      ...PROJECT,
    },
    RESERVE,
  )
  if (!core || core.error) log(`Branch review: ${core ? core.error : 'no result'} — integrating anyway; the run result carries it as incomplete`)
  return await finish({
    planFinal: plan,
    coreImplemented: done.map((d) => ({ bdId: `${BD_ID}:${d.key}`, title: d.title, status: 'done', filesChanged: d.build.filesChanged, testsAdded: d.build.testsAdded, assumptions: d.build.assumptions || [], summary: d.build.summary })),
    blockers: (core && core.blockersRemaining) || [],
    reviewOutcome: { fixes: (core && core.applied && core.applied.details) || null, minor: (core && core.reviewMinor) || [] },
    assumptions: [...(plan.assumptions || []), ...done.flatMap((d) => d.build.assumptions || [])],
    CRITIC_NOTES: [],
    subtasks: [],
    criticApproved: true,
    chunks: done.map((d) => d.key),
    incomplete: !core ? 'branch review did not run' : core.error || null,
  })
}

// === 5. Integrate — fresh base, FULL gates, project documents, the PR description, bd ===
// A function: called both by the normal path (after feature-core) and by the split phase (after the chunks).
async function finish({ planFinal, coreImplemented, blockers, reviewOutcome, assumptions, CRITIC_NOTES, subtasks, criticApproved, chunks, incomplete }) {
phase('Integrate')
const stopIntegrate = outOfBudget('Integrate', RESERVE)
if (stopIntegrate) return { ...stopIntegrate, implemented: coreImplemented, chunks }
const TEST_FAST = a.testCmd ? TREE_CMD(a.testCmd, APP) : `${VENV}/bin/python -m pytest ${CODE}/tests -q --tb=short`
// The limit of one agent command is 600 s. A longer lane is cut off and the run repeats —
// split it into shards with the dbShards argument (paths from the tree root), each as its own command.
const TEST_DB = DB_SHARDS.map((s) => `\`${VENV}/bin/python -m pytest ${APP}/${s} -m db -q --tb=short\``).join(', ') + (DB_SHARDS.length > 1 ? ' — db lane shards, EACH as a separate synchronous command' : '')
const LINT = a.lintCmd ? TREE_CMD(a.lintCmd, APP) : `${VENV}/bin/ruff check ${CODE}/src ${CODE}/tests && ${VENV}/bin/ruff format --check ${CODE}/src ${CODE}/tests`
const integrate = await spawn(
  [
    `Final integration of "${planFinal.title}" (branch ${BRANCH}, tree ${APP}, base ${BASE}) by the integrate skill (the full gate) and the wrap skill.`,
    TOPO,
    (NO_FETCH ? '' : `git -C ${APP} fetch origin ${BASE}; `) + `git -C ${APP} merge origin/${BASE} --no-edit. Resolve conflicts by meaning.`,
    `FULL gates (mandatory, a real run): \`${TEST_FAST}\`, \`${LINT}\`` + (DB_TESTS ? `, ${TEST_DB} (Docker/testcontainers).` : '. The db lane (@pytest.mark.db) is not run here — CI will run it; say so in the PR description.') + ' Red → fix (root cause, not adjusting) and commit the fix.',
    `bd: the run's subtasks (\`timeout 60 bd -C ${ROOT} list --parent ${BD_ID}\`; all bd commands — with \`timeout 60\`: bd can hang on a database lock; on timeout — retry once, then skip and name it under Open questions) — close the done ones (\`bd -C ${ROOT} close <id> --reason\`), leave the undone ones open with a note (\`bd -C ${ROOT} update <id> --notes\`). Do NOT close the parent ${BD_ID} (scripts/merge-feature.sh closes it after the merge); add a note to it on where the run stopped (\`bd -C ${ROOT} update ${BD_ID} --append-notes \"…\"\`). The queue is a local store outside git: nothing about it is committed.`,
    'The project documents — wrap step 2 (test counters from the real run; nothing touched — say so in the PR description).',
    `THE PR DESCRIPTION — wrap step 4, returned as prBody (not a file, not committed). \`## Done\` — behaviour and test counters from the real run. \`## Decisions\` — the choices and why, including the assumptions ("decisions the machine made for you": ${JSON.stringify(assumptions)}) the critic remarks accepted as known consequences (${JSON.stringify(CRITIC_NOTES)}) and the review outcome — what Fix applied or rejected and the minor findings left as they are (${JSON.stringify(reviewOutcome || {})}); a decision that shapes the architecture also gets an ADR in the branch. \`## Open questions\` — unimplemented subtasks and remaining review blockers (${JSON.stringify(blockers.map((b) => b.summary))}), each follow-up as a bd task with its id; "none" if there are none. End with the line \`Task: ${BD_ID}\`. scripts/merge-feature.sh refuses a PR without these sections and writes the description into the merge commit.` +
      (CHANGE_ABS ? ` In ${CHANGE_ABS}/tasks.md mark [x] the completed items of phase ${PHASE}.` : ''),
    `Commit the docs, openspec and an ADR (if any) as a separate commit on ${BRANCH} (\`git -C ${APP} commit\`). NO push, NO PR. ` + COMMIT_STYLE,
    'Return {ok, summary, details: test counters (fast/db/ruff), closed subtasks, touched docs; prBody: the PR description}.',
  ].join('\n'),
  { label: 'integrate', phase: 'Integrate', schema: INTEGRATE_SCHEMA, ...BUILDER() },
)
if (!integrate || !integrate.ok) return { error: 'integrate failed', branch: BRANCH, integrate, blockers, implemented: coreImplemented, chunks }

// === 6. PR — optional (boundary: open PR into the base; merge via scripts/merge-feature.sh <branch> once CI is green) ===
let pr = null
if (!STOP_BEFORE_PR) {
  phase('PR')
  pr = await spawn(
    [
      `Push the branch (\`git -C ${APP} push origin ${BRANCH}\`) and open a PR into ${BASE} with this description, passed on stdin: \`gh pr create --base ${BASE} --head ${BRANCH} --title "<title>" --body-file - <<'EOF'\` … \`EOF\`. Do NOT merge (the session merges afterwards: scripts/merge-feature.sh ${BRANCH}).`,
      `The description, as is: ${JSON.stringify(integrate.prBody || '')}. The title in human-readable prose: what changes, for a reader of the merge history.`,
      MSG_STYLE,
      'Return {ok, summary, details: link to the PR}.',
    ].join('\n'),
    { label: 'pr', phase: 'PR', schema: STEP_SCHEMA, ...WORKER },
  )
} else {
  log('stopBeforePR: PR skipped — branch ' + BRANCH + ' is committed locally; push → PR → merge is done by a human.')
}

const problems = [...new Set([
  ...(incomplete ? String(incomplete).split('; ') : []),
  blockers.length ? `${blockers.length} critical review blocker(s) remain` : null,
  !STOP_BEFORE_PR && (!pr || !pr.ok) ? 'PR not opened' : null,
].filter(Boolean))]

return {
  error: problems.length ? 'incomplete: ' + problems.join('; ') : null,
  path: 'full',
  title: planFinal.title,
  branch: BRANCH,
  appDir: APP,
  bdId: BD_ID,
  subtasks,
  chunks,
  assumptions,
  criticApproved,
  implemented: coreImplemented,
  blockers,
  integrate,
  prBody: integrate.prBody || null,
  pr,
  note:
    'Automatic boundary — ' +
    (STOP_BEFORE_PR ? 'local commit' : 'open PR') +
    ' on branch ' +
    BRANCH +
    ' (tree ' +
    APP +
    '). Before merge, for the human: review the assumptions and the PR description (prBody; with stopBeforePR the session opens the PR with it: gh pr create --draft --body-file -), green CI (full db/e2e matrix) → scripts/merge-feature.sh ' +
    BRANCH +
    ' (it finds the task to close from the branch name; if it cannot, close ' +
    BD_ID +
    ' by hand).' +
    ' If CI cannot run (Actions disabled: billing, limits, an incident), the human may add --skip-ci "<reason>": the local gate runs instead and the merge is marked as made without CI (refused at FENCE_LEVEL=team).' +
    (USE_WORKTREE ? ` After the merge remove the worktree: git -C ${SESSION_TOP} worktree remove ${APP}.` : ''),
}
}
})()
if (capHit && !String(result.error || '').includes('agent limit reached')) result.error = 'agent limit reached' + (result.error ? '; ' + result.error : '')
return { ...result, agentsUsed, maxAgents: MAX_AGENTS }
