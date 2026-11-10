export const meta = {
  name: 'feature-core',
  description:
    'Middle of the feature pipeline: build the run\'s bd subtasks (children of the parent phase task or the assignee\'s tasks) in one of two modes — pipeline: fan-out waves by dependencies from the graph with a files[] disjointness check, integrated by a single writer; session: one builder takes the subtasks in dependency order, a fresh one every few — (scoped pytest + one commit per task + bd close of the subtask, by the build-tdd and integrate skills) → full gate (fast pytest suite + ruff, db lane by flag) → review (test pyramid / project invariants / simplify) → Fix. Returns a clean, reviewed, committed branch. One repository: code and harness in one tree; bd runs from the root of the main checkout. The core does NOT create tasks and does NOT branch (the caller does that: feature-auto or the development session).',
  phases: [{ title: 'Build' }, { title: 'Review' }, { title: 'Fix' }],
}

// --- Input (args) ---
// { appDir (ABS. path of the run's working tree — repo root or a git worktree; REQUIRED), root? (abs. path of the main
//   checkout holding the bd Dolt store; default = appDir), baseBranch?='main' (diff base branch), venv? (abs. path of a venv with pytest/ruff; default
//   <appDir>/<appSubdir>/.venv, falling back to <root>/<appSubdir>/.venv), parentId? (id of the parent task — take its in_progress children),
//   assignee? (scope by assignee when there is no parentId), changeDir?/specPath? (context for impl/review), reviewOnly?,
//   buildMode?='session' ('pipeline' — parallel waves on disjoint files, an integrator per wave; 'session' — one builder
//     builds and integrates the subtasks in dependency order, sessionChunk at a time, then a fresh builder; the stages
//     after Build are the same), sessionChunk?=4,
//   maxRounds?=6 (build waves; a deeper graph is a sign the phase should be split), skipFinalGates?,
//   dbTests?=false (run the @pytest.mark.db lane — needs Docker),
//   dbShards? (db lane paths from the tree root, default ['<appSubdir>/tests/integration/db']; a lane longer than the 600 s command limit — shards),
//   allowMigrations?=false (alembic migrations are forbidden unless the spec allows them — then true),
//   appSubdir?='app' (where the code lives inside the tree: <tree>/<appSubdir>/src, /tests, /alembic, /.venv — base keeps
//     it in app/; '' for code at the tree root),
//   models? ({implement?, review?} — model ids overriding the model of a call: implement = workers, review = the reviewer role; left out, workers inherit the session's model and the reviewer its role's),
//   projectRoles? (names of the roles the project overrides in .claude/agents/; feature-auto passes them),
//   maxAgents?=30 (agent calls this run may make; feature-auto passes what is left of its own cap) }
// --- Result ---
// { error, implemented, assumptions, reviews, applied, blockersRemaining, agentsUsed, maxAgents }. The harness status is almost
// always "completed"; the outcome is `error`: null — built, reviewed and fixed; otherwise a '; '-joined list of what
// went wrong ('no appDir', 'no scope', 'bad appSubdir', 'no subtasks in the run scope', 'budget exhausted', 'agent limit reached', 'integration of round <n> not green',
// 'bad buildMode', '<n> subtask(s) not implemented: …', 'final gates red', 'review did not run', 'review fixes not applied',
// '<n> critical review blocker(s) remain', 'bd export failed').
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
const INVARIANTS = a.invariantsDoc || 'the product invariants named in CLAUDE.md, if any'
const APP = a.appDir || null
const ROOT = a.root || APP
const BASE = a.baseBranch || 'main'
const VENV = a.venv || null
const PARENT = a.parentId || null
const ASSIGNEE = a.assignee || null
const CHANGE_DIR = a.changeDir || null
const SPEC_PATH = a.specPath || null
const REVIEW_ONLY = !!a.reviewOnly
const MAX_ROUNDS = a.maxRounds || 6
const BUILD_MODE = a.buildMode == null || a.buildMode === '' ? 'session' : String(a.buildMode)
// One builder's share of a session-mode Build: past it the context grows enough to drift, so a fresh builder continues
// from the committed branch (the guidance's own exception for subagents: measurable degradation from context bloat).
const SESSION_CHUNK = Number(a.sessionChunk) > 0 ? Math.floor(Number(a.sessionChunk)) : 4
const SKIP_FINAL_GATES = !!a.skipFinalGates // in the auto path the full gate is run by feature-auto Integrate after merging the base
const DB_TESTS = !!a.dbTests
// Code subdirectory inside the tree (base layout: app/). S is the prefix of code paths from the tree root.
const SUB = String(a.appSubdir == null ? 'app' : a.appSubdir).replace(/^\/+|\/+$/g, '')
const S = SUB ? SUB + '/' : ''
const CODE = S ? `${APP}/${SUB}` : APP
const DB_SHARDS = Array.isArray(a.dbShards) && a.dbShards.length ? a.dbShards : [`${S}tests/integration/db`]
const ALLOW_MIGRATIONS = !!a.allowMigrations
// Token budget of the run (Workflow `budget`): when the caller set a target (budget.total), the build stops between
// rounds once it is spent — the error says 'budget exhausted' and the run is resumed later with the same args.
const budgetSpent = () => typeof budget !== 'undefined' && budget && budget.total != null && budget.remaining() <= 0
// Agent cap of the run (args.maxAgents). A token target exists only when a human set one ("+500k"); an unattended or
// scheduled run has none, so the cap is the run's cost limit. Every agent call goes through spawn(), which counts it;
// a stage checks the headroom it needs before it starts and the run stops with 'agent limit reached'. The full path
// needs about 1 (graph) + tasks + waves + 1 (gates) + 3 (review) + 1 (fix) + 1 (export): 30 covers the 8-task size gate.
const MAX_AGENTS = Number(a.maxAgents) > 0 ? Math.floor(Number(a.maxAgents)) : 30
let agentsUsed = 0
let capHit = false // an agent call was refused by the cap — the run is incomplete
const agentsLeft = () => MAX_AGENTS - agentsUsed
async function spawn(prompt, opts) {
  if (agentsUsed >= MAX_AGENTS) {
    capHit = true
    log(`feature-core: agent limit ${MAX_AGENTS} reached — ${opts.label} not started`)
    return null
  }
  agentsUsed++
  return agent(prompt, opts)
}

if (!APP) {
  log('feature-core: args.appDir is required (absolute path of the run\'s working tree).')
  return { error: 'no appDir' }
}
if (!/^[A-Za-z0-9._/-]*$/.test(SUB) || SUB.split('/').includes('..')) {
  log(`feature-core: appSubdir "${SUB}" is not a plain relative path (e.g. 'app', or '' for code at the tree root).`)
  return { error: 'bad appSubdir' }
}
if (BUILD_MODE !== 'pipeline' && BUILD_MODE !== 'session') {
  log(`feature-core: buildMode "${BUILD_MODE}" is neither 'pipeline' nor 'session'.`)
  return { error: 'bad buildMode', agentsUsed, maxAgents: MAX_AGENTS }
}
if (!PARENT && !ASSIGNEE) {
  log('feature-core: the run needs a scope — args.parentId (children of the phase task) or args.assignee (assignee).')
  return { error: 'no scope' }
}

// Model layout (a project decision, args.models): mechanics (graph fetch, export) and the per-subtask workers
// (impl/integrate/fix/gates) take models.implement; review — models.review over the reviewer role, with a fresh read-only
// context. Without an override a worker inherits the session's model and the reviewer the model of its role; pass full
// ids, not aliases, when you pin one.
const MODELS = a.models && typeof a.models === 'object' ? a.models : {}
const pickModel = (m) => (typeof m === 'string' && m.trim() ? { model: m.trim() } : {})
const SIMPLE = { ...pickModel(MODELS.implement), effort: 'medium' }
// Roles (plugins/dark-factory/agents/<name>.md): the reviewer lens runs as the reviewer role, which carries the prompt and
// effort; models.review stays a deliberate project override of its model. A plugin agent is found only as
// dark-factory:<name>; a project's own .claude/agents/<name>.md overrides it only by the bare name. feature-auto passes
// args.projectRoles (the project's override files); a run started by hand passes it too, or gets the plugin's role.
const PROJECT_ROLES = Array.isArray(a.projectRoles) ? a.projectRoles.map(String) : []
const role = (name) => ({ agentType: PROJECT_ROLES.includes(name) ? name : 'dark-factory:' + name })
const REVIEWER = { ...role('reviewer'), ...pickModel(MODELS.review) }
// The implementers, integrators, gates and Fix run as the builder role: it preloads the build-tdd, integrate and wrap
// skills, so the prompts below carry only the run's facts (paths, commands, scope) and the know-how lives in the skills.
const BUILDER = { ...role('builder'), ...pickModel(MODELS.implement) }

const READY_SCHEMA = {
  type: 'object',
  required: ['tasks'],
  properties: {
    tasks: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'title', 'files', 'status'],
        properties: {
          id: { type: 'string' },
          title: { type: 'string' },
          status: { type: 'string', enum: ['open', 'in_progress', 'closed'] }, // closed = already implemented and integrated (run resume)
          files: { type: 'array', items: { type: 'string' } },
          testPlan: { type: 'string' },
          acceptance: { type: 'string' },
          blockedBy: { type: 'array', items: { type: 'string' } }, // ids of NOT-yet-closed dependencies inside the run
        },
      },
    },
  },
}
const IMPL_SCHEMA = {
  type: 'object',
  required: ['bdId', 'status', 'filesChanged', 'summary'],
  properties: {
    bdId: { type: 'string' },
    title: { type: 'string' },
    status: { type: 'string', enum: ['done', 'partial', 'failed'] },
    filesChanged: { type: 'array', items: { type: 'string' } },
    testFiles: { type: 'array', items: { type: 'string' } },
    testsAdded: { type: 'number' },
    assumptions: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
  },
}
const STEP_SCHEMA = {
  type: 'object',
  required: ['ok', 'summary'],
  properties: { ok: { type: 'boolean' }, summary: { type: 'string' }, details: { type: 'string' } },
}
const FINDING_SCHEMA = {
  type: 'object',
  required: ['dimension', 'findings'],
  properties: {
    dimension: { type: 'string' },
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

// --- Gate commands (same as in .githooks/pre-push) ---
const PY = VENV ? `${VENV}/bin/python` : `$([ -x ${CODE}/.venv/bin/python ] && echo ${CODE}/.venv/bin/python || echo ${ROOT}/${S}.venv/bin/python)`
const RUFF = VENV ? `${VENV}/bin/ruff` : `$([ -x ${CODE}/.venv/bin/ruff ] && echo ${CODE}/.venv/bin/ruff || echo ${ROOT}/${S}.venv/bin/ruff)`
const TEST_FAST = a.testCmd ? TREE_CMD(a.testCmd, APP) : `${PY} -m pytest ${CODE}/tests -q --tb=short`
// The limit of one agent command is 600 s: a longer lane is cut off. Shards — each as a separate synchronous command.
const TEST_DB = DB_SHARDS.map((s) => `\`${PY} -m pytest ${APP}/${s} -m db -q --tb=short\``).join(', ') + (DB_SHARDS.length > 1 ? ' — shards, EACH as a separate synchronous command' : '')
const LINT = a.lintCmd ? TREE_CMD(a.lintCmd, APP) : `${RUFF} check ${CODE}/src ${CODE}/tests && ${RUFF} format --check ${CODE}/src ${CODE}/tests`
const SCOPED = (files) => `${PY} -m pytest ${files} -q --tb=short` + ' (test files — as ABSOLUTE paths under ' + APP + '; for db tests add `-m db`, without it addopts excludes them)'
const FULL_GATES = `fast suite \`${TEST_FAST}\` + lint \`${LINT}\`` + (DB_TESTS ? ` + db lane ${TEST_DB} (Docker/testcontainers)` : ' (the @pytest.mark.db lane is NOT run in this run — CI will run it)')

const STANDARDS =
  'STANDARDS: by the build-tdd skill (CLAUDE.md and CODING_STANDARDS.md read in full first). Product invariants for review — ' + INVARIANTS + '.' +
  (ALLOW_MIGRATIONS
    ? ` Alembic migrations (${S}alembic/versions/) are allowed`+' per the spec: a revision following neighbouring files, up/down are mandatory.'
    : ' CREATING Alembic migrations is FORBIDDEN in this run: if the task needs one — return status:"failed" with an explanation.')
const CONTEXT = [
  CHANGE_DIR ? `OpenSpec change of the run: ${CHANGE_DIR} (proposal.md — with the WHEN/THEN acceptance scenarios, design.md when present — decisions and transition tree, tasks.md).` : '',
  SPEC_PATH ? `Spec of the run: ${SPEC_PATH} (absolute path).` : '',
]
  .filter(Boolean)
  .join(' ')
const NOCD = ' Changing directory is FORBIDDEN: no `cd` at all — not bare, not in a subshell `(cd … && …)`, not `cd … &&` — it triggers permission prompts and breaks the CWD. Always use absolute paths: `git -C`, `bd -C`, `pytest <abs path>`, `ruff check <abs path>`, `grep -rn … <abs path>`, `rg`, `find <abs path>`, `sed -n … <abs path>`, `head <abs path>`.'
// A background run returns before its outcome is known — such a step counts as failed.
const SYNC = ' LONG COMMANDS (pytest, ruff, npm) — ONLY synchronously: background runs (run_in_background, `&`, nohup) are forbidden; do not return a result until the run has finished and you have read its outcome. Answering "waiting for a background run" is a failed step.'
// Seam: the feature execution queue (docs/functions.md) = beads — every `bd -C <root> …` in these prompts (create,
// claim, note, close). To use another queue, rewrite those calls.
const TOPO = `TOPOLOGY: one repository. The working tree of this run is ${APP} (absolute path; code ${S}src/, ${S}tests/, ${S}alembic/ — paths from the tree root; harness openspec/ — in the same tree). All git commands — \`git -C ${APP} …\`. bd commands — \`bd -C ${ROOT} …\` (the project's local bd store is the main checkout's, outside git: nothing about it is committed).`+NOCD+SYNC
const MSG_STYLE = 'Commit messages by the integrate skill. No Co-Authored-By.'

// Deterministic parallel-safety check: pairwise disjoint files[].
function pairwiseDisjoint(sets) {
  const seen = new Set()
  for (const s of sets) {
    // Unknown scope is not evidence of disjoint writes.
    if (!Array.isArray(s) || s.length === 0) return false
    for (const f of s) {
      if (typeof f !== 'string' || !f.trim()) return false
      const path = f.replace(/\\/g, '/')
      if (path.startsWith('/') || /^[A-Za-z]:/.test(path) || /[*?{}]/.test(path)) return false
      const parts = []
      for (const part of path.split('/')) {
        if (!part || part === '.') continue
        if (part === '..') {
          if (parts.length === 0) return false
          parts.pop()
        } else parts.push(part)
      }
      const key = parts.join('/')
      if (!key || seen.has(key)) return false
      seen.add(key)
    }
  }
  return true
}

function implPrompt(t) {
  return [
    `Implement ONE bd subtask by the build-tdd skill (failing test first, then code). Your files (paths from the repo root): ${JSON.stringify(t.files)} — other agents are editing OTHER files in parallel, do NOT touch theirs. New files are allowed only next to yours (same package/test directory).`,
    TOPO,
    `bd subtask ${t.id}: ${t.title}. Full text (FILES / TESTS / ACCEPTANCE) — \`bd -C ${ROOT} show ${t.id}\`.`,
    CONTEXT,
    t.testPlan ? `Test plan: ${t.testPlan}` : 'No TESTS line — cover it as build-tdd step 4 says: each test at the lowest layer of CODING_STANDARDS.md §Test pyramid that can check it.',
    t.acceptance ? `Acceptance: ${t.acceptance}` : '',
    `IMPORTANT: do NOT commit and do NOT run the full suite (other agents are still writing to the tree). For RED→GREEN run ONLY your own test files: ${SCOPED('<your test files>')}.` +
      (DB_TESTS ? ' db tests may be run scoped with `-m db` (Docker is available).' : ` db tests are NOT run in this run (no Docker) — write them following neighbours in ${DB_SHARDS.join(', ')}, CI will run them.`),
    'Do NOT touch bd (claim/close is not your job).',
    STANDARDS,
    `Return {bdId:"${t.id}", status, filesChanged, testFiles, testsAdded, assumptions, summary}. status:"partial" — if part is done and the rest is described in summary; "failed" — if it cannot be done (missing precondition, needs a migration while forbidden, etc.).`,
  ]
    .filter(Boolean)
    .join('\n')
}

// Session-mode Build: one builder takes the subtasks in dependency order and, being the only writer, builds and integrates
// each one before the next (build-tdd → integrate). No files[] disjointness check: overlapping files never force a
// sequential wave. SESSION_CHUNK subtasks per builder, then a fresh one continues from the committed branch.
const SESSION_SCHEMA = { type: 'object', required: ['results'], properties: { results: { type: 'array', items: IMPL_SCHEMA } } }

function sessionPrompt(chunk) {
  return [
    `Build these ${chunk.length} bd subtasks ONE AFTER ANOTHER, in the order given: each by the build-tdd skill, then by the integrate skill ("after each slice": its scoped tests, lint, one commit, \`bd -C ${ROOT} close <id> --reason "…"\`) before the next one starts. You are the only writer to git and bd in this run.`,
    TOPO,
    CONTEXT,
    'Subtasks, in order: ' + JSON.stringify(chunk.map((t) => ({ id: t.id, title: t.title, files: t.files, testPlan: t.testPlan || '', acceptance: t.acceptance || '' }))) + ` Full text of each — \`bd -C ${ROOT} show <id>\`.`,
    `Scoped tests: ${SCOPED("<the subtask's test files>")}. Lint: \`${LINT}\`. Do NOT run the full suite — the run does it once after Build.` +
      (DB_TESTS ? ' db tests may be run scoped with `-m db` (Docker is available).' : ` db tests are NOT run in this run (no Docker) — write them following neighbours in ${DB_SHARDS.join(', ')}, CI will run them.`),
    `A subtask you cannot finish: restore ITS files to the last commit (\`git -C ${APP} restore <files>\`, remove the new files it added), do not commit or close it, report it "failed" with the reason, and go on — skip the ones that depend on it.`,
    STANDARDS,
    "Commit ONLY code/tests/migrations/code docs; the project documents and the PR description are the final integration's. " + MSG_STYLE,
    'Return {results: [{bdId, title, status, filesChanged, testFiles, testsAdded, assumptions, summary}]} — one entry per subtask given, in order. status "done" — committed and closed; "partial" — committed, left open with a note on what is left (bd update <id> --append-notes), the rest in summary; "failed" — not committed, not closed.',
  ]
    .filter(Boolean)
    .join('\n')
}

// Dependency order of the open subtasks: a dependency counts as met when it is done, placed earlier, or outside the run.
function topoOrder(tasks, done, runIds) {
  const ids = new Set(tasks.map((t) => t.id))
  const placed = new Set()
  const order = []
  for (;;) {
    const next = tasks.filter((t) => !placed.has(t.id) && (t.blockedBy || []).every((d) => placed.has(d) || done.has(d) || !runIds.has(d) || !ids.has(d)))
    if (next.length === 0) return order // all placed, or a cycle among the rest
    for (const t of next) {
      placed.add(t.id)
      order.push(t)
    }
  }
}

async function buildSession(all, done) {
  const runIds = new Set(all.map((t) => t.id))
  const failed = new Set()
  for (let n = 1; ; n++) {
    if (budgetSpent()) {
      log(`Build: token budget spent (${budget.total}) before builder ${n} — stop; resume with the same args`)
      problems.push('budget exhausted')
      return
    }
    // a failed subtask takes everything that depends on it, directly or not, out of this run
    const skip = new Set(failed)
    for (let grew = true; grew; ) {
      grew = false
      for (const t of all) {
        if (!skip.has(t.id) && !done.has(t.id) && (t.blockedBy || []).some((d) => skip.has(d))) {
          skip.add(t.id)
          grew = true
        }
      }
    }
    const open = all.filter((t) => !done.has(t.id) && !skip.has(t.id))
    const queue = topoOrder(open, done, runIds)
    if (queue.length === 0) {
      if (open.length) log(`Build: none of the remaining subtasks is unblocked (cycle/broken dependency): ${JSON.stringify(open.map((t) => t.id))} — stop`)
      return
    }
    // a builder needs itself, and the gates, review and fix after Build at least three more
    if (4 > agentsLeft()) {
      log(`Build: agent limit ${MAX_AGENTS} — ${agentsUsed} used, builder ${n} needs 1 plus review — stop; resume with the same args and a larger maxAgents`)
      problems.push('agent limit reached')
      return
    }
    const chunk = queue.slice(0, SESSION_CHUNK)
    log(`Build: builder ${n} takes ${chunk.length} of ${queue.length} ready subtasks: ${JSON.stringify(chunk.map((t) => t.id))}`)
    const res = await spawn(sessionPrompt(chunk), { label: `build:${n}`, phase: 'Build', schema: SESSION_SCHEMA, ...BUILDER })
    const byId = new Map(((res && res.results) || []).map((r) => [r.bdId, r]))
    let progress = false
    for (const t of chunk) {
      const r = byId.get(t.id)
      if (r && r.status !== 'failed') {
        done.add(t.id)
        implemented.push({ ...r, bdId: t.id, title: r.title || t.title })
        progress = true
      } else {
        failed.add(t.id)
        log(`Build: ${t.id} not built — ${r ? r.summary : 'no result from the builder'}`)
      }
    }
    if (!progress) {
      log(`Build: builder ${n} finished none of its subtasks — stop, triage by the orchestrator`)
      return
    }
  }
}

function integratePrompt(done) {
  const testFiles = done.flatMap((d) => d.testFiles || []).join(' ')
  return [
    'Integrate this batch by the integrate skill ("after each slice or wave"). You are the only writer to git and bd for it.',
    TOPO,
    `Steps in order: (1) run ONLY the batch's scoped tests (the full suite runs once at the end of the run): ${SCOPED(testFiles || '<batch test files>')}${DB_TESTS ? ' and, if there are db tests among them, the same list with `-m db`' : ''}. ` +
      `(2) Green → \`${LINT}\` (ruff format — you may apply auto-formatting to the batch files). (3) Commit EACH task as a separate commit of its files (\`git -C ${APP} add <files>\` + \`git -C ${APP} commit\`), then \`bd -C ${ROOT} close <id> --reason "…"\` for a done task; a partial one stays open: \`bd -C ${ROOT} update <id> --append-notes "<what is left>"\`. (4) Red → fix small things and repeat; if it cannot be fixed — return ok:false with details (which tests, why).`,
    'Batch tasks (id → files): ' + JSON.stringify(done.map((d) => ({ id: d.bdId, files: d.filesChanged, testFiles: d.testFiles || [] }))),
    'Commit ONLY code/tests/migrations/code docs; the project documents and the PR description are the final integration\'s.',
    MSG_STYLE,
    'Return {ok, summary, details: test counters from the real run and the closed ids}.',
  ].join('\n')
}

function finalGatesPrompt() {
  return [
    `The full gate of the integrate skill, ONCE over the accumulated branch (the rounds ran only scoped tests): fast suite \`${TEST_FAST}\` + lint \`${LINT}\`.` +
      (DB_TESTS ? ' Do NOT run the db lane here — it already passed scoped in the rounds and will pass in full on the final pass (Fix/integration); a repeated run wastes ~5 minutes.' : ''),
    TOPO,
    `Red → fix by the root cause and commit the fix (\`git -C ${APP} commit\`). If something large cannot be fixed — return ok:false with details. ` + MSG_STYLE,
    'Do NOT touch bd (subtasks were closed by the rounds). Return {ok, summary, details: final test counters}.',
  ].join('\n')
}

const DIFF = `git -C ${APP} diff origin/${BASE}...HEAD`
const ALL_REVIEW_DIMS = [
  {
    key: 'tests',
    prompt:
      `Test pyramid audit of the diff \`${DIFF}\` (CODING_STANDARDS.md §Test pyramid — the project's layers, directories and profile): (a) LAYER — every NEW test against the project's layer table: a rule a pure function could decide, tested only through api or integration, is a major finding (fix: extract the rule into a pure function, test it in unit, keep the upper test only if it checks the seam); (b) EXCESS — a new test duplicating one at a lower layer, more than three api tests per route, a rule's branches tested through api, an error asserted by message text, a layout test (classes, sizes, CSS), data built by HTTP chains, an obsolete test left in place: each is a finding (fix: delete or move down); (c) CASES — the edge, error and negative cases of a rule exist at the rule's layer; (d) GAPS — logic without unit tests; repository/SQL without a test on a live database; a test that mocks a repository entirely = mocks the hot path and gives no signal; a callsite parsing structured LLM output without a unit test for coercing invalid output. Findings with severity per docs/review-contract.md and a fix.`,
  },
  {
    key: 'invariants',
    prompt:
      `Project invariants in the diff \`${DIFF}\`: (a) anti-patterns from CLAUDE.md and the prohibitions of CODING_STANDARDS.md — e.g. strict types without a default over LLM output; bare RuntimeError / except Exception; configuration hard-coded instead of going through settings; SQL/prompts/user-facing texts not where the standards put them; log language not as the standards require; (b) product invariants from ${INVARIANTS} and the review contract docs/review-contract.md; (c) train flags default to false (a green close without the feature), the old path is kept behind the flag. Findings with a fix.`,
  },
  {
    key: 'simplify',
    prompt:
      `/simplify over the diff \`${DIFF}\`: REUSE — code duplicating existing helpers/repositories/services (grep neighbouring code in the source tree, name what to reuse); SIMPLIFICATION — dead parameters, copy-paste, needless abstractions; EFFICIENCY — extra DB queries in a loop, repeated connections; ALTITUDE — band-aid fixes instead of generalisation, magic values instead of settings. NOT bugs. Findings with a fix.`,
  },
]
// The lenses can be narrowed by an input parameter. Rationale: dimensions that catch DEFECTS
// (invariants, test pyramid) are needed on every chunk of a split phase — a blocker in the
// integration branch costs more than running the lens. The dimension that catches FORM (simplify)
// is useless on an intermediate chunk: the next chunk rewrites the same code and the simplification
// would be thrown away. It runs once, on the last chunk of the phase.
const REVIEW_DIMS = Array.isArray(a.reviewDims) && a.reviewDims.length
  ? ALL_REVIEW_DIMS.filter((d) => a.reviewDims.includes(d.key))
  : ALL_REVIEW_DIMS

// === 1. Build: ONE upfront graph fetch (Sonnet/medium) + wave planning in JS ===
//    Completion is tracked in memory (integrate closes in bd); bd is not polled per round.
const implemented = []
const problems = [] // collected into the result's `error`
let gatesRed = false
if (!REVIEW_ONLY) {
  const graph = await spawn(
    [
      'Return the FULL subtask graph of this run in ONE answer (wave planning happens in code afterwards, bd is not queried again).',
      `bd commands — \`bd -C ${ROOT} …\` (no cd).`,
      PARENT
        ? `(1) \`bd -C ${ROOT} list --parent ${PARENT} --status open,in_progress,closed --json\` — ONLY the children of task ${PARENT} (do NOT include ${PARENT} itself); include closed ones too with status:"closed" — these are subtasks already implemented when the run is restarted;`
        : `(1) \`bd -C ${ROOT} list --status in_progress --assignee ${ASSIGNEE} --json\` — ONLY this assignee's tasks (do NOT touch others'), status:"in_progress";`,
      '(2) for each one, via `bd -C '+ROOT+' show <id>`, collect files[] (the "FILES:" line in the description — paths from the repo root), testPlan ("TESTS:"), acceptance (Acceptance criteria) and blockedBy[] = ids of its dependencies (blocks/depends-on) that are NOT yet closed (do not include closed ones or ones external to the run);',
      '(3) return {tasks:[{id,title,status,files,testPlan,acceptance,blockedBy}]}. If the list is empty — return {tasks:[]}.',
    ].join(' '),
    { label: 'plan', phase: 'Build', schema: READY_SCHEMA, ...SIMPLE },
  )
  const all = (graph && graph.tasks) || []
  if (all.length === 0) {
    log('Build: no subtasks in the run scope — nothing to implement')
    problems.push('no subtasks in the run scope')
  } else {
    const ids = new Set(all.map((t) => t.id))
    const done = new Set()
    // run resume: closed subtasks were already implemented and integrated by earlier rounds — do not redo them
    for (const t of all.filter((x) => x.status === 'closed')) {
      done.add(t.id)
      implemented.push({ bdId: t.id, title: t.title, status: 'done', filesChanged: t.files || [], testFiles: [], testsAdded: 0, assumptions: [], summary: 'already closed in bd (previous round/resume) — not redone' })
    }
    if (done.size) log(`Build: ${done.size} of ${all.length} subtasks already closed (resume) — skipping`)
    log(`Build: ${all.length - done.size} subtasks, mode ${BUILD_MODE}`)
    if (BUILD_MODE === 'session') await buildSession(all, done)
    let round = 0
    while (BUILD_MODE === 'pipeline' && done.size < all.length && round < MAX_ROUNDS) {
      if (budgetSpent()) {
        log(`Build: token budget spent (${budget.total}) before round ${round + 1} — stop; resume with the same args`)
        problems.push('budget exhausted')
        break
      }
      round++
      const wave = all.filter((t) => !done.has(t.id) && (t.blockedBy || []).every((d) => done.has(d) || !ids.has(d)))
      if (wave.length === 0) {
        const remaining = all.filter((t) => !done.has(t.id)).map((t) => t.id)
        log(`Build round ${round}: none of the remaining tasks is unblocked (cycle/broken dependency): ${JSON.stringify(remaining)} — stop`)
        break
      }
      // A wave needs one agent per task plus its integrator, and the review after it at least two more.
      if (wave.length + 3 > agentsLeft()) {
        log(`Build: agent limit ${MAX_AGENTS} — ${agentsUsed} used, round ${round} needs ${wave.length + 1} plus review — stop; resume with the same args and a larger maxAgents`)
        problems.push('agent limit reached')
        break
      }
      const disjoint = pairwiseDisjoint(wave.map((t) => t.files))
      log(`Build round ${round}: wave of ${wave.length} tasks — files ${disjoint ? 'disjoint → parallel' : 'OVERLAP → sequential (parallel-safety)'}`)

      let res
      if (disjoint) {
        res = await parallel(wave.map((t) => () => spawn(implPrompt(t), { label: `impl:${t.id}`, phase: 'Build', schema: IMPL_SCHEMA, ...BUILDER })))
      } else {
        res = []
        for (const t of wave) res.push(await spawn(implPrompt(t), { label: `impl:${t.id}`, phase: 'Build', schema: IMPL_SCHEMA, ...BUILDER }))
      }
      const ok = res.filter(Boolean).filter((d) => d.status !== 'failed')
      const failed = res.filter(Boolean).filter((d) => d.status === 'failed')
      if (failed.length) log(`Build round ${round}: failed ${JSON.stringify(failed.map((d) => d.bdId + ': ' + d.summary))}`)
      if (ok.length === 0) {
        log('Round without progress (all failed) — stop, triage by the orchestrator')
        break
      }
      const integ = await spawn(integratePrompt(ok), { label: `integrate#${round}`, phase: 'Build', schema: STEP_SCHEMA, ...BUILDER })
      implemented.push(...ok)
      for (const d of ok) done.add(d.bdId)
      if (!integ || !integ.ok) {
        log(`Build round ${round}: integration not green — stop: ${integ && integ.summary}`)
        problems.push(`integration of round ${round} not green`)
        break
      }
    }
    const notDone = all.filter((t) => !done.has(t.id)).map((t) => t.id)
    if (notDone.length) {
      log(`Build: subtasks NOT implemented ${JSON.stringify(notDone)} — they stay in_progress, triage by a human`)
      problems.push(`${notDone.length} subtask(s) not implemented: ${notDone.join(', ')}`)
    }
  }

  // Budget spent mid-build: gates, review and fix would spend more of it — stop here; a resume picks up the open subtasks.
  if (problems.includes('budget exhausted') || problems.includes('agent limit reached')) {
    return { error: problems.join('; '), implemented, assumptions: implemented.flatMap((d) => d.assumptions || []), reviews: [], applied: null, blockersRemaining: [], agentsUsed, maxAgents: MAX_AGENTS }
  }
  if (!SKIP_FINAL_GATES && implemented.length) {
    const finalGate = await spawn(finalGatesPrompt(), { label: 'gates-final', phase: 'Build', schema: STEP_SCHEMA, ...BUILDER })
    if (!finalGate || !finalGate.ok) {
      gatesRed = true
      log(`Build: final full gate not green — ${finalGate && finalGate.summary} (review continues, Fix repairs)`)
    }
  }
}

// === 2. Review — read the branch diff against the base ===
// On a small diff (≤3 tasks) a fan-out is disproportionate → one combined reviewer. On a large feature — parallel lenses.
const SMALL_DIFF = implemented.length > 0 && implemented.length <= 3
// Review + fix need their agents together: a partial review would read as a clean branch.
const REVIEW_NEED = (SMALL_DIFF ? 1 : REVIEW_DIMS.length) + 1
if (REVIEW_NEED > agentsLeft()) {
  log(`Review: agent limit ${MAX_AGENTS} — ${agentsUsed} used, review needs ${REVIEW_NEED} — stop; resume with a larger maxAgents`)
  problems.push('agent limit reached')
  return { error: problems.join('; '), implemented, assumptions: implemented.flatMap((d) => d.assumptions || []), reviews: [], applied: null, blockersRemaining: [], agentsUsed, maxAgents: MAX_AGENTS }
}
let reviews
if (SMALL_DIFF) {
  log(`Review: small diff (${implemented.length} tasks) → one combined reviewer (${REVIEW_DIMS.length} dimensions)`)
  const combinedPrompt =
    `Review the diff \`${DIFF}\` (tree ${APP}; read-only, change nothing) ACROSS all dimensions at once and collect the findings into one list (each with a dimension field: ` +
    REVIEW_DIMS.map((d) => d.key).join(' / ') +
    '):\n' +
    REVIEW_DIMS.map((d, i) => `${i + 1}) [${d.key}] ${d.prompt}`).join('\n') +
    '\nA finding is a proposal; severity follows the proven consequence, scale and finding format per docs/review-contract.md.'
  const one = await spawn(combinedPrompt, { label: 'review:combined', phase: 'Review', schema: FINDING_SCHEMA, ...REVIEWER })
  reviews = [one].filter(Boolean)
} else {
  log('Review: fan-out (test pyramid / invariants / simplify)')
  reviews = (
    await parallel(
      REVIEW_DIMS.map((d) => () =>
        spawn(`Read-only review (tree ${APP}, change nothing), dimension [${d.key}]. ${d.prompt} Severity honestly per docs/review-contract.md. Return {dimension:"${d.key}", findings}.`, {
          label: `review:${d.key}`,
          phase: 'Review',
          schema: FINDING_SCHEMA,
          ...REVIEWER,
        }),
      ),
    )
  ).filter(Boolean)
}

// === 3. Fix: apply critical/major (or reject deliberately), gates, commit ===
const fixable = reviews.flatMap((r) => (r.findings || []).filter((f) => f.severity !== 'minor'))
let applied = null
if (fixable.length) {
  applied = await spawn(
    [
      `Apply the review fixes to the working tree ${APP} (or reject deliberately with a justification — per docs/review-contract.md) — a quality pass within the task scope, not an expansion. SCOPE: change only what each finding names, the smallest change that resolves it. A fix that would change visible behaviour, design or texts beyond what the source asked for is a product decision: reject it and name it for the human.`,
      TOPO,
      'Findings: ' + JSON.stringify(fixable),
      STANDARDS,
      (SKIP_FINAL_GATES
        ? `After the fixes — scoped tests of what was touched (${SCOPED('<test files of the edited modules>')}) + fast suite \`${TEST_FAST}\` + lint \`${LINT}\`. Do NOT run the full db lane here — Integrate runs it once after merging the base`
        : `After the fixes — FULL gates ONCE (${FULL_GATES})${DB_TESTS ? '; this is the only full db lane run in the run, do not repeat it' : ''}`) + `, then commit (\`git -C ${APP} commit\`, Conventional Commits, subject in the project language). ` + MSG_STYLE,
      'Return {ok, summary, details: what was applied / what was rejected and why}.',
    ].join('\n'),
    { label: 'fix', phase: 'Fix', schema: STEP_SCHEMA, ...BUILDER },
  )
}

// A critical finding stays a blocker only while no Fix has applied it (a rejected one is named in applied.details)
const blockersRemaining = applied && applied.ok ? [] : reviews.flatMap((r) => (r.findings || []).filter((f) => f.severity === 'critical'))
const reviewMinor = reviews.flatMap((r) => (r.findings || []).filter((f) => f.severity === 'minor').map((f) => f.summary))
const assumptions = implemented.flatMap((d) => d.assumptions || [])
if (gatesRed && !(applied && applied.ok)) problems.push('final gates red')
if (reviews.length === 0) problems.push('review did not run')
if (applied && !applied.ok) problems.push('review fixes not applied')
if (blockersRemaining.length) problems.push(`${blockersRemaining.length} critical review blocker(s) remain`)
log(`Done: ${implemented.length} tasks implemented, ${reviews.length} reviews, ${blockersRemaining.length} blockers remaining${problems.length ? ' — ' + problems.join('; ') : ''}`)

if (capHit) problems.push('agent limit reached')
return { error: problems.length ? [...new Set(problems)].join('; ') : null, implemented, assumptions, reviews, applied, blockersRemaining, reviewMinor, agentsUsed, maxAgents: MAX_AGENTS }
