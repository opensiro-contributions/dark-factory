export const meta = {
  name: 'feature-lite',
  description:
    'SMALL PATH of the feature pipeline: a self-contained feature end to end with 3 agents (a 4th only to fix review findings), no openspec graph and no fan-out. (1) Build — one agent: branch <type>/<ID>-<slug> with a tracker ID, <type>/<slug> without one, from a fresh base branch + implementation from a ready spec by TDD + FULL gates (fast pytest suite + ruff) + commit. (2) Review — one reviewer, all lenses; a fix agent only for critical or major findings. (3) Wrap — one agent: bd tracing + project document sync + the PR description. (4) PR — optional (push + gh pr create; merged afterwards via scripts/merge-feature.sh <branch>). One repository: code (<appSubdir>/src, <appSubdir>/tests; appSubdir defaults to app) and harness (openspec/) in one tree; the bd store lives in the main checkout, outside git.',
  phases: [{ title: 'Build' }, { title: 'Review' }, { title: 'Wrap' }, { title: 'PR' }],
}

// --- Input (args) ---
// { request, stamp (REQUIRED — Date.now is forbidden), slug?, trackerId? (tracker ID of the change, goes into the branch name), type?='feat'|'fix'|'ui'|'refactor'|'chore'|'docs',
//   specPath? (absolute path of a ready spec), stopBeforePR?=true, noFetch?=false, assignee?, appDir? (abs. path of the tree), bdId?,
//   baseBranch?='main',
//   venv? (abs. path of a venv with pytest/ruff; default <tree>/<appSubdir>/.venv, else <root>/<appSubdir>/.venv — a worktree has no venv of its own), branchReady?=false (branch already created by the caller — feature-auto),
//   root? (abs. path of the main checkout holding the bd Dolt store; differs from appDir when using a worktree),
//   allowMigrations?=false (a chunk of a split phase that needs a migration — allowed by the calling feature-auto),
//   skipWrap?=false (a chunk of a split phase: docs, the PR description and the bd note are done once at the end by the caller),
//   appSubdir?='app' (where the code lives inside the tree: <tree>/<appSubdir>/src, /tests, /.venv — base keeps it in app/;
//     '' for code at the tree root),
//   models? ({implement?} — model id for the build, wrap-up and PR agents; left out, they inherit the session's model),
//   projectRoles? (names of the roles the project overrides in .claude/agents/; feature-auto passes them),
//   maxAgents?=5 (agent calls this run may make — build, review, a fix only for critical or major findings, wrap, PR: at most 5; feature-auto passes what is left of its cap) }
// Branch: <type>/<trackerId>-<slug> with a tracker ID, <type>/<slug> without (default slug: lite-<stamp>).
// merge-feature.sh finds the task to close from the branch name: the tracker ID equals the task's external_ref, or
// the slug is the label change:<slug> of an epic (a phase: -p<K>). When it finds none it says so — close the task
// by hand (check with `scripts/merge-feature.sh <branch> --print-bd-id`).
// --- Result ---
// { error, path:'lite', branch, appDir, build, wrap, pr, note, agentsUsed, maxAgents }. The harness status is almost always "completed";
// the outcome is `error`: null — the branch is committed (and the PR opened when stopBeforePR=false). Otherwise:
// 'no request' | 'no stamp' | 'no release line' | 'bad appSubdir' | 'budget exhausted' | 'agent limit reached' | 'build failed' |
// 'gates failed' | 'wrap failed' | 'PR not opened'.
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
const REQUEST = a.request || ''
const SPEC_PATH = a.specPath || null
const STAMP = a.stamp
const BD_ID = a.bdId || null
const TYPES = ['feat', 'fix', 'ui', 'refactor', 'chore', 'docs']
const TYPE = TYPES.includes(a.type) ? a.type : 'feat'
const TRACKER = a.trackerId ? String(a.trackerId).replace(/[^A-Za-z0-9]+/g, '-') : null
const SLUG = a.slug || 'lite-' + STAMP
const STOP_BEFORE_PR = a.stopBeforePR !== false // default true: the boundary is a local commit; push/PR/merge — the session after a human "yes"
const NO_FETCH = !!a.noFetch
const ASSIGNEE = a.assignee || null
// Working tree of the run: by default the repo root (git rev-parse); in parallel runs — the absolute worktree path.
const APP = a.appDir || null
const ROOT = a.root || null // main checkout with the bd Dolt store (differs from appDir when using a worktree)
const BASE = a.baseBranch || 'main'
const VENV = a.venv || null
const BRANCH_READY = !!a.branchReady
const ALLOW_MIGRATIONS = !!a.allowMigrations
const SKIP_WRAP = !!a.skipWrap
// Code subdirectory inside the tree (base layout: app/). S is the prefix of code paths from the tree root.
const SUB = String(a.appSubdir == null ? 'app' : a.appSubdir).replace(/^\/+|\/+$/g, '')
const S = SUB ? SUB + '/' : ''
// Model of the agents (args.models.implement); left out, they inherit the session's model. Pin full ids, not aliases.
const MODELS = a.models && typeof a.models === 'object' ? a.models : {}
const MODEL = typeof MODELS.implement === 'string' && MODELS.implement.trim() ? { model: MODELS.implement.trim() } : {}
// Build and Wrap run as the builder role (plugins/dark-factory/agents/builder.md): it preloads the build-tdd, integrate and
// wrap skills, so the prompts carry the run's facts and the know-how lives in the skills. A project's own
// .claude/agents/builder.md overrides it only by the bare name (args.projectRoles).
const PROJECT_ROLES = Array.isArray(a.projectRoles) ? a.projectRoles.map(String) : []
const BUILDER = { agentType: PROJECT_ROLES.includes('builder') ? 'builder' : 'dark-factory:builder', ...MODEL }
// Cost limits. A token target (Workflow budget.total) exists only when a human set one; the agent cap (args.maxAgents)
// is the limit an unattended run always has. A stage that would pass either stops the run cleanly.
const budgetSpent = () => typeof budget !== 'undefined' && budget && budget.total != null && budget.remaining() <= 0
const MAX_AGENTS = Number(a.maxAgents) > 0 ? Math.floor(Number(a.maxAgents)) : 5
let agentsUsed = 0
function stopBefore(stage) {
  if (budgetSpent()) {
    log(`feature-lite: token budget spent (${budget.total}) before ${stage} — stop; resume with the same args`)
    return { error: 'budget exhausted', stage, path: 'lite', branch: BRANCH, agentsUsed, maxAgents: MAX_AGENTS }
  }
  if (agentsUsed >= MAX_AGENTS) {
    log(`feature-lite: agent limit ${MAX_AGENTS} reached before ${stage} — stop; resume with the same args and a larger maxAgents`)
    return { error: 'agent limit reached', stage, path: 'lite', branch: BRANCH, agentsUsed, maxAgents: MAX_AGENTS }
  }
  return null
}
function spawn(prompt, opts) {
  agentsUsed++
  return agent(prompt, opts)
}

if (!REQUEST) {
  log('feature-lite: empty args.request. Pass { request, stamp }.')
  return { error: 'no request' }
}
if (!STAMP) {
  log('feature-lite: args.stamp is required (unique suffix). Date.now() is forbidden in scripts.')
  return { error: 'no stamp' }
}
if (!/^[A-Za-z0-9._/-]*$/.test(SUB) || SUB.split('/').includes('..')) {
  log(`feature-lite: appSubdir "${SUB}" is not a plain relative path (e.g. 'app', or '' for code at the tree root).`)
  return { error: 'bad appSubdir' }
}
const BRANCH = TYPE + '/' + (TRACKER ? TRACKER + '-' : '') + SLUG

const STEP_SCHEMA = {
  type: 'object',
  required: ['ok', 'summary'],
  properties: { ok: { type: 'boolean' }, summary: { type: 'string' }, details: { type: 'string' } },
}
// The wrap-up also returns the PR description — the record of the work (## Done / ## Decisions / ## Open questions),
// which scripts/merge-feature.sh requires and writes into the merge commit.
const WRAP_SCHEMA = {
  type: 'object',
  required: ['ok', 'summary', 'prBody'],
  properties: { ...STEP_SCHEMA.properties, prBody: { type: 'string' } },
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
const BUILD_SCHEMA = {
  type: 'object',
  required: ['ok', 'branch', 'filesChanged', 'testsAdded', 'gatesPassed', 'summary'],
  properties: {
    ok: { type: 'boolean' },
    branch: { type: 'string' },
    filesChanged: { type: 'array', items: { type: 'string' } },
    testsAdded: { type: 'number' },
    gatesPassed: { type: 'boolean' },
    assumptions: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
    details: { type: 'string' },
  },
}

// Human-readable style for commit/PR messages.
const MSG_STYLE =
  'Message style (commit and PR): the message is addressed to a reviewer and to a future reader of the history, not to a machine. The subject states the essence of the change (Conventional Commits, in the project language — CODING_STANDARDS.md §Project language, default English — ≤~70 characters). The body is coherent prose: what changes and why. Forbidden: chains like "A → B → C"; file paths and symbol names as the main text; JSON in the text; telegraphic style with slashes and semicolons. Supporting details (tests, assumptions) go in short bullet points. No Co-Authored-By.'
const STANDARDS =
  'Standards: by the build-tdd skill (CLAUDE.md, CODING_STANDARDS.md and naming.yaml read first); any user-facing text only through the project message catalog' + (a.i18nFile ? ' (' + a.i18nFile + ')' : '') + '.'
const COMMIT_STYLE = 'Commit messages by the integrate skill. No Co-Authored-By.'
const MIGRATIONS_RULE = (allowed) =>
  allowed
    ? ' An Alembic migration is allowed by the caller: additive and reversible only (new nullable columns/tables), with a db test for upgrade/downgrade.'
    : ' The small path does not create alembic migrations: if the phase needs one, return ok:false — such work goes through the full path.'
const NOCD = ' Changing directory is FORBIDDEN: no `cd` at all — not bare, not in a subshell `(cd … && …)`, not `cd … &&` — it triggers permission prompts and breaks the CWD. Always use absolute paths: `git -C`, `bd -C`, `pytest <abs path>`, `ruff check <abs path>`, `grep -rn … <abs path>`, `rg`, `find <abs path>`, `sed -n … <abs path>`, `head <abs path>`.'
// Writes go only into appDir: a dirty main checkout (ROOT) blocks scripts/merge-feature.sh,
// which runs from it. The prohibition is stated explicitly and identically for both agents
// rather than relying on the feature request author to remember it.
const WRITE_SCOPE = (dir, root) =>
  root && root !== dir
    ? ` FILE WRITES (code, docs, tasks.md — anything via Write/Edit) — ONLY inside ${dir}. The main checkout ${root} is used EXCLUSIVELY for reading and for \`bd -C ${root} …\` (the bd Dolt store lives there); it is also the working tree of scripts/merge-feature.sh, and any file edit there (even the same tasks.md already written in ${dir}) leaves it dirty and blocks other merges. If a tool asks for a path — always ${dir}/…, never ${root}/….`
    : ` FILE WRITES — ONLY inside ${dir}.`
// A background run returns before its outcome is known — such a step counts as failed.
const SYNC = ' LONG COMMANDS (pytest, ruff, npm) — ONLY synchronously: background runs (run_in_background, `&`, nohup) are forbidden; do not return a result until the run has finished and you have read its outcome. Answering "waiting for a background run" is a failed step.'
// Seam: the feature execution queue (docs/functions.md) = beads — every `bd -C <root> …` in these prompts (create,
// claim, note, close). To use another queue, rewrite those calls.
const TOPO = (dir) =>
  `TOPOLOGY: one repository, the working tree of this run is ${dir} (code ${dir}/${S}src, ${dir}/${S}tests; harness openspec/ — in the same tree). All git commands — \`git -C ${dir} …\`. bd commands — \`bd -C ${ROOT || dir} …\` (the project's local bd store is the main checkout's, outside git: nothing about it is committed).`+NOCD+SYNC+WRITE_SCOPE(dir, ROOT)
// No venv given: the tree's own, else the main checkout's (a session worktree has none), else the system's
const VENV_OF = (dir) => `$(for v in ${dir}/${S}.venv${ROOT ? ` ${ROOT}/${S}.venv` : ''}; do [ -x $v/bin/python ] && echo $v && break; done)`
const PYBIN = (dir) => VENV ? `${VENV}/bin/python` : `$(v=${VENV_OF(dir)}; [ -n "$v" ] && echo $v/bin/python || echo python)`
const RUFFBIN = (dir) => VENV ? `${VENV}/bin/ruff` : `$(v=${VENV_OF(dir)}; [ -n "$v" ] && echo $v/bin/ruff || echo ruff)`
const TEST_CMD = (dir) => a.testCmd ? TREE_CMD(a.testCmd, dir) : `${PYBIN(dir)} -m pytest ${dir}/${S}tests -q --tb=short`
// A frontend with its own CI lane (if the project has one) lives in frontendDir (arg, path from the tree root, e.g.
// app/frontend); without it there is no frontend lane. npm commands take the working directory via --prefix — no cd needed.
const FE = a.frontendDir ? String(a.frontendDir).replace(/^\/+|\/+$/g, '') : null
const FRONTEND_CMD = (dir) => `npm --prefix ${dir}/${FE} run typecheck && npm --prefix ${dir}/${FE} run lint && npm --prefix ${dir}/${FE} run test:run`
const LINT_CMD = (dir) => a.lintCmd ? TREE_CMD(a.lintCmd, dir) : `${RUFFBIN(dir)} check ${dir}/${S}src ${dir}/${S}tests && ${RUFFBIN(dir)} format --check ${dir}/${S}src ${dir}/${S}tests`

// === 1. Build — ONE agent builds the whole feature by TDD ===
phase('Build')
const stopBuild = stopBefore('Build')
if (stopBuild) return stopBuild
const build = await spawn(
  [
    APP ? `Working tree of the run: ${APP} (absolute path).` : 'STEP -1: determine the repo root: `git rev-parse --show-toplevel` — from now on this is ${DIR}; all commands use the absolute path.',
    SPEC_PATH
      ? `The feature spec is READY and is a PRECONDITION (written by a human BEFORE the run). Read it by the ABSOLUTE path ${SPEC_PATH}. Your job is IMPLEMENTATION strictly per the spec, by TDD (code+tests). Do NOT rewrite and do NOT commit the spec. Forks not covered by the spec → KISS/YAGNI, record them in assumptions[]. If there is NO file at the spec path — do NOT invent a design: return ok:false (the task is not ready for implementation).`
      : 'Implement the feature ENTIRELY on your own, by TDD, like an experienced developer: a short brainstorm-lite on safe defaults (forks → KISS/YAGNI, record them in assumptions[]), then code+tests.',
    TOPO(APP || '${DIR}'),
    `Request: "${REQUEST}".${BD_ID ? ` bd task: ${BD_ID}.` : ''}`,
    BRANCH_READY
      ? `STEP 0 — branch ${BRANCH} has ALREADY been created by the caller in tree ${APP || '${DIR}'}: make sure \`git -C ${APP || '${DIR}'} rev-parse --abbrev-ref HEAD\` = ${BRANCH} and the tree is clean. Do not branch again.`
      : `STEP 0 — branch: ${NO_FETCH ? `WITHOUT fetch (branch from the local origin/${BASE}): ` : `git fetch origin ${BASE}; then `}git checkout -b ${BRANCH} origin/${BASE} (in tree ${APP || '${DIR}'}; base — ${BASE}). Make sure: HEAD is on ${BRANCH}, the tree is clean. If the branch already exists — ok:false with an explanation.`,
    `IMPLEMENTATION by the build-tdd skill, the slices in order if the spec has several (failing test first, then code): each test at the lowest layer of CODING_STANDARDS.md §Test pyramid that can check it (build-tdd step 4); tests of the db lane (@pytest.mark.db) are not in the fast suite — a separate lane and CI run them. Scoped tests as you go: \`${PYBIN(APP || '${DIR}')} -m pytest <abs path of test file> -q\`.`,
    STANDARDS + MIGRATIONS_RULE(ALLOW_MIGRATIONS),
    `FINAL — the full gate of the integrate skill: \`${TEST_CMD(APP || '${DIR}')}\` (fast suite, as in pre-push) + \`${LINT_CMD(APP || '${DIR}')}\`. ` + (FE ? `IF you touched the frontend (${FE}) — additionally run the frontend lane: \`${FRONTEND_CMD(APP || '${DIR}')}\` (if node_modules is missing, first \`npm --prefix ${APP || '${DIR}'}/${FE} ci\`); return the frontend test counters in details. ` : '') + `If green — commit to the branch (git add + commit). NO push, NO PR. Commit ONLY code/tests/code docs — the project documents and the PR description are the next step\'s.`,
    COMMIT_STYLE,
    'Return {ok, branch, filesChanged, testsAdded, gatesPassed, assumptions, summary, details: test counters from the real run}.',
  ].join('\n'),
  { label: 'lite-build', phase: 'Build', schema: BUILD_SCHEMA, ...BUILDER },
)
if (!build || !build.ok) return { error: 'build failed', branch: BRANCH, build, agentsUsed, maxAgents: MAX_AGENTS }
log(`Build: ${(build.filesChanged || []).length} files, +${build.testsAdded || 0} tests, gates ${build.gatesPassed ? 'green' : 'RED'}`)
if (!build.gatesPassed) return { error: 'gates failed', branch: BRANCH, build, agentsUsed, maxAgents: MAX_AGENTS }
if (SKIP_WRAP) {
  log('skipWrap: the caller does the wrap-up (docs, the PR description, bd) — this is a chunk of a split phase.')
  return { error: null, path: 'lite', branch: BRANCH, appDir: APP, build, wrap: null, pr: null, agentsUsed, maxAgents: MAX_AGENTS }
}

// === 2. Review — ONE reviewer in a fresh context, all lenses at once; a fix only for critical or major findings ===
// The builder does not see its own blind spots: a duplicate of existing code passes its gates. A chunk of a split phase
// (skipWrap) is reviewed once by the caller, on the whole branch.
phase('Review')
const stopReview = stopBefore('Review')
if (stopReview) return { ...stopReview, build }
const TREE = APP || '<repo root, git rev-parse --show-toplevel>'
const review = await spawn(
  `Read-only review of branch ${BRANCH} (tree ${TREE}), the diff \`git -C ${TREE} diff origin/${BASE}...HEAD\`, through all lenses at once: the test pyramid (CODING_STANDARDS.md §Test pyramid), the project invariants (CLAUDE.md, CODING_STANDARDS.md${a.invariantsDoc ? ', ' + a.invariantsDoc : ''}), simplification and duplication of code that already exists, correctness. Read the code around the diff too. Findings and severity per docs/review-contract.md. Return {findings}.`,
  { label: 'lite-review', phase: 'Review', schema: REVIEW_SCHEMA, agentType: PROJECT_ROLES.includes('reviewer') ? 'reviewer' : 'dark-factory:reviewer', ...(typeof MODELS.review === 'string' && MODELS.review.trim() ? { model: MODELS.review.trim() } : {}) },
)
const findings = (review && review.findings) || []
const fixable = findings.filter((f) => f.severity !== 'minor')
let fix = null
if (fixable.length) {
  const stopFix = stopBefore('Fix')
  if (stopFix) return { ...stopFix, build, findings }
  fix = await spawn(
    [
      `Apply the review fixes on branch ${BRANCH}, or reject one deliberately with a reason (docs/review-contract.md). SCOPE: change only what each finding names, the smallest change that resolves it. A fix that would change visible behaviour, design or texts beyond what the source asked for is a product decision: reject it and name it for the human under ## Open questions.`,
      TOPO(TREE),
      'Findings: ' + JSON.stringify(fixable),
      `Then the full gate once (\`${TEST_CMD(TREE)}\`, \`${LINT_CMD(TREE)}\`) and a commit. NO push. ` + COMMIT_STYLE,
      'Return {ok, summary, details: what was applied, what was rejected and why}.',
    ].join('\n'),
    { label: 'lite-fix', phase: 'Review', schema: STEP_SCHEMA, ...BUILDER },
  )
}
const blockers = fix && fix.ok ? [] : findings.filter((f) => f.severity === 'critical')
log(`Review: ${findings.length} findings, ${fixable.length} critical/major${fix ? (fix.ok ? ', fixed' : ', fix FAILED') : ''}`)

// === 3. Wrap — ONE agent: tracing (bd) + doc sync + the PR description ===
phase('Wrap')
const stopWrap = stopBefore('Wrap')
if (stopWrap) return { ...stopWrap, build }
const wrap = await spawn(
  [
    `Do the wrap-up of the finished feature by the wrap skill (the code is already on branch ${BRANCH}, gates are green). Everything goes into the SAME branch, in the same tree (one repository).`,
    TOPO(APP || '<repo root, git rev-parse --show-toplevel>'),
    `Feature request: "${REQUEST}". What was done: ${build.summary}. Files: ${JSON.stringify(build.filesChanged)}. Assumptions: ${JSON.stringify(build.assumptions || [])}. Review: ${JSON.stringify(findings.map((f) => f.severity + ': ' + f.summary))}; fixes: ${fix ? fix.summary : 'none needed'}${blockers.length ? '; critical blockers left — name them under ## Open questions' : ''}.`,
    BD_ID || ASSIGNEE
      ? `1) bd TRACING: task ${BD_ID || '(see assignee ' + ASSIGNEE + ')'} has already been claimed by the caller. Do NOT create a duplicate and do NOT close the task: scripts/merge-feature.sh closes it after the merge, finding it by the branch name (tracker ID or slug). Leave it in_progress and add a note about what was done (\`bd -C ${ROOT || APP || '${DIR}'} update <id> --append-notes "…"\`).`
      : `1) bd TRACING: create ONE bd task for the feature (\`bd -C ${ROOT || APP || '${DIR}'} create --type=feature --acceptance="…"\`) — do NOT close it; leave it in_progress with a note (bd update <id> --notes). Put its id first in details; give it the label change:<slug> of the branch slug (and external_ref = the tracker ID, if any) so scripts/merge-feature.sh finds it, or close it by hand after the merge.`,
    '2) The project documents and tasks.md — wrap steps 2–3 (test counters from the REAL run, not "by analogy"; nothing touched — say "project documents need no changes").',
    '3) THE PR DESCRIPTION — wrap step 4, returned as prBody (not a file, not committed). ## Decisions names what the review found and what was fixed or rejected.',
    'Commit the wrap-up (docs, tasks.md, an ADR if any) as a separate commit on the same branch. NO push, NO PR.',
    COMMIT_STYLE,
    'Return {ok, summary, details: bd task id + which docs were touched + test counters; prBody: the PR description}.',
  ].join('\n'),
  { label: 'lite-wrap', phase: 'Wrap', schema: WRAP_SCHEMA, ...BUILDER },
)
if (!wrap || !wrap.ok) return { error: 'wrap failed', path: 'lite', branch: BRANCH, appDir: APP, build, wrap, agentsUsed, maxAgents: MAX_AGENTS }

// === 4. PR — optional (boundary: open PR; merged afterwards via scripts/merge-feature.sh <branch>) ===
let pr = null
if (!STOP_BEFORE_PR) {
  phase('PR')
  const stopPR = stopBefore('PR')
  if (stopPR) return { ...stopPR, build, wrap }
  pr = await spawn(
    [
      `Push branch ${BRANCH} (git -C ${APP || '${DIR}'} push origin ${BRANCH}) and open a PR into ${BASE} with this description, passed on stdin: \`gh pr create --base ${BASE} --head ${BRANCH} --title "<title>" --body-file - <<'EOF'\` … \`EOF\`. Do NOT merge (the session merges afterwards: scripts/merge-feature.sh ${BRANCH}).`,
      'The description, as is: ' + JSON.stringify(wrap.prBody || '') + ' — add the board umbrella card line `Part of <umbrella card id>` if it is known from the bd task. The title in human-readable prose: what changes, for a reader of the merge history.',
      MSG_STYLE,
      'Return {ok, summary, details: link to the PR}.',
    ].join('\n'),
    { label: 'lite-pr', phase: 'PR', schema: STEP_SCHEMA, ...MODEL },
  )
} else {
  log('stopBeforePR: PR skipped — branch ' + BRANCH + ' is committed locally; push → PR → merge is done by a human.')
}

const problems = [
  !review ? 'review did not run' : null,
  fixable.length && !(fix && fix.ok) ? 'review fixes not applied' : null,
  blockers.length ? `${blockers.length} critical review blocker(s) remain` : null,
  !STOP_BEFORE_PR && (!pr || !pr.ok) ? 'PR not opened' : null,
].filter(Boolean)
return {
  error: problems.length ? 'incomplete: ' + problems.join('; ') : null,
  path: 'lite',
  branch: BRANCH,
  bdId: BD_ID,
  appDir: APP,
  build,
  findings,
  fix,
  blockers,
  wrap,
  prBody: (wrap && wrap.prBody) || null,
  pr,
  agentsUsed,
  maxAgents: MAX_AGENTS,
  note:
    'Small path (feature-lite): feature on branch ' +
    BRANCH +
    '. Boundary — ' +
    (STOP_BEFORE_PR ? 'local commit (no push)' : 'open PR') +
    '. Before merge, for the human: review the assumptions and the PR description (prBody; with stopBeforePR the session opens the PR with it: gh pr create --draft --body-file -), green CI (full db/e2e matrix on CI) → scripts/merge-feature.sh ' +
    BRANCH +
    (BD_ID ? ' (it finds the task to close from the branch name; if it cannot, close ' + BD_ID + ' by hand).' : '. If merge-feature.sh finds no task, close the task Wrap created (its id is in wrap.details) by hand after the merge.') +
    ' If CI cannot run (Actions disabled: billing, limits, an incident), the human may add --skip-ci "<reason>": the local gate runs instead and the merge is marked as made without CI (refused at FENCE_LEVEL=team).',
}
