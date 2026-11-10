# Contributing

## Ground rules

- **English only** — prose, comments, identifiers, string literals, LLM prompts, test names. The only exception is
  deliberate non-ASCII test data that exercises UTF-8 handling (mark it with a short comment).
- **Canon, not instance.** This repository holds the canon: the methodology (`docs/`) and the templates
  (`plugins/dark-factory/templates/`). Your own factory instance — the registry (`registry.md`, created from
  [the factory template](plugins/dark-factory/templates/factory/registry.md)), filled-in skill configurations, project lists, client names, hosts —
  lives in your own private factory repository and never comes back here. Improvements found in an instance or a
  project are carried over into `plugins/dark-factory/templates/` by hand, in generic form, without names of people, clients or projects.
- **One exception: `analytics/`.** It holds the weekly numbers of the maintainer's own factory, written by its
  analytics timer (`scripts/analytics.py`) through a PR: aggregated counts with projects as letters, no names. It is
  the evidence for the method, not an instance's configuration; nothing else of an instance comes here.
- `plugins/dark-factory/templates/base/scripts/merge-feature.sh` is the one merge script: `/dark-factory:init` and
  `/dark-factory:add base` both copy it from the plugin, so there is nothing to keep in step.
- Everything a user needs ships inside `plugins/dark-factory/` (commands, scripts, templates, workflows): a change must
  not make an installed plugin depend on a file outside that directory. The repository root holds only what is about
  the repository itself (docs, checks, CI).
- Keep changes to the canon behaviour-preserving unless the change is the point; a new module or a change of its
  "when it is time" signal is also a row in [plugins/dark-factory/templates/base/.harness/modules.md](plugins/dark-factory/templates/base/.harness/modules.md)
  in the same commit.

## Running the tests

Python 3.11+ with `pytest` (and the packages from the module's `requirements.txt` where present), Node 20+, bash.

```bash
# dark-factory plugin: guard, quota guard, solo kit scaffolding, level detection, module install (stdlib only)
python3 tests/test_guard.py
python3 tests/test_quota.py
python3 tests/test_init_kit.py
python3 tests/test_level.py
python3 tests/test_vulns.py
python3 tests/test_add_module.py

# Plugin and marketplace manifests (Claude Code CLI)
claude plugin validate plugins/dark-factory && claude plugin validate .

# Repository wiring: the plugin hooks and skills point at real files, base ships none of what the plugin
# carries, deploy-base.sh writes the starting fence level, nothing at the repository root is needed by the plugin
python3 scripts/test_repo.py

# Factory fleet tooling (the plugin's code; its tests, like all the plugin's tests, are in tests/)
python3 tests/test_fleet.py
python3 tests/test_analytics.py
python3 tests/test_feedback_desk.py

# base app: unit + integration tests (e2e needs a running dev stand)
(cd plugins/dark-factory/templates/base/app && python3 -m pytest tests -m "not e2e")

# release-tag module: release.sh against a temporary bare repo
(cd plugins/dark-factory/templates/release-tag && python3 -m pytest app/tests/integration/test_release_script.py)

# prod-monitor module
(cd plugins/dark-factory/templates/prod-monitor && python3 -m pytest scripts/ops/prod_monitor)

# User guide links (run from a project root; exits 0 when there is no guide yet)
python3 plugins/dark-factory/templates/base/scripts/check_doc_links.py --root plugins/dark-factory/templates/base

# Shell syntax of every script
find . -name '*.sh' -not -path './node_modules/*' -print0 | xargs -0 -n1 bash -n

# feature pipeline workflows (in the plugin): control-flow dry runs with fake agents, no tokens spent (bare script
# names); DRYRUN_EXPECT_ERROR makes a run fail unless its error matches — null, a spent budget, the agent cap
# Pure first-party S2 file-scope guard (does not run or simulate agents)
node --test tests/test_feature_core_disjointness.mjs

AUTO='{"request":"x","stamp":"s1","bdId":"proj-x","path":"full","allowMigrations":true'
DRYRUN_EXPECT_ERROR=null node plugins/dark-factory/workflows/dryrun.mjs feature-auto.js "$AUTO}"
DRYRUN_EXPECT_ERROR=null node plugins/dark-factory/workflows/dryrun.mjs feature-lite.js '{"request":"x","stamp":"s1","bdId":"proj-x"}'
DRYRUN_EXPECT_ERROR=null node plugins/dark-factory/workflows/dryrun.mjs feature-core.js '{"appDir":"/r/.claude/worktrees/fab-x","root":"/r","parentId":"proj-x"}'
DRYRUN_BUDGET=3000 DRYRUN_EXPECT_ERROR='budget exhausted' node plugins/dark-factory/workflows/dryrun.mjs feature-auto.js "$AUTO}"
DRYRUN_EXPECT_ERROR='agent limit reached' node plugins/dark-factory/workflows/dryrun.mjs feature-auto.js "$AUTO,\"maxAgents\":8}"
```

All of the above must stay green before a pull request. CI ([.github/workflows/test.yml](.github/workflows/test.yml))
runs the same list on every push and pull request; a new suite is added to both in the same commit.

## Versioning

- Releases are sequential: `v1`, `v2`, … A release is the tag `v<N>` on main, and the plugin's version in
  [plugins/dark-factory/.claude-plugin/plugin.json](plugins/dark-factory/.claude-plugin/plugin.json) is `<N>.0.0`. It is
  bumped in the same commit that prepares the release; Claude Code updates installed copies only when that string
  changes. The marketplace entry carries no `version` of its own. (A project's own releases from the `release-tag`
  module are `vYYYY.MM.DD.N` and unrelated.)
- The same commit pins the marketplace in the templates (`"ref": "v<N>"` in
  [base](plugins/dark-factory/templates/base/.claude/settings.json) and
  [solo](plugins/dark-factory/templates/solo/.claude/settings.json) and
  [factory](plugins/dark-factory/templates/factory/.claude/settings.json) `.claude/settings.json`);
  `scripts/test_repo.py` checks that the version and the pins agree. There is no CHANGELOG file: what changed is the
  merged pull requests, and what to do about it is their `## Upgrade` sections. `.harness/base-sha` in a project holds `v<N>` when the files were copied by an installed plugin; run from a git
  checkout of the canon it records the commit SHA instead. `/dark-factory:factory-status` compares it with the
  installed plugin.
- Projects pin the plugin by the marketplace's `ref` in `.claude/settings.json`, or by adding the marketplace at the
  tag — `/plugin marketplace add ruslantrachuk/dark-factory#v<N>` — since the plugin's relative source in the
  marketplace cannot carry a ref of its own. Moving a project to a newer release is a deliberate change of that pin.
- **A pull request's `## Upgrade` section** ([template](.github/pull_request_template.md)) says what a live project
  or the server must do after updating to the release that carries it: a `fleet.py provision` run, a line in a config
  file, a one-time step. It is omitted when there is nothing to do. A change that breaks a deployed project's files or
  settings always has one; fences get stricter only in a release, never silently.
- **Releasing** (the owner merges; CI does the rest). Every night
  [release-pr.yml](.github/workflows/release-pr.yml) checks whether `plugins/dark-factory/` changed since the last
  tag. If it did, it opens `chore/release-v<N>` (`scripts/release_pr.py`: the version, the pins, the README status)
  with the merged pull requests and the upgrade notes (`scripts/release_notes.py v<N-1>`) in its description, and
  starts the tests on it; an open one gets its notes refreshed. Merging that pull request is the release decision:
  [release.yml](.github/workflows/release.yml) tags the merge `v<N>`, runs
  `gh release create v<N> --generate-notes --notes-file <the notes> --latest` and moves the `release` branch to the tag
  (a fast-forward). Closing it means "not now": the next one comes once the branch is deleted. GitHub lists the
  merged pull requests; the notes add **How to upgrade**, which `/dark-factory:upgrade` reads in each project.
  `scripts/release.sh v<N>` does the same tagging by hand when the workflow could not. The repository setting
  "Allow GitHub Actions to create and approve pull requests" must be on for the nightly pull request.
- **Rolling it out on a server** (the owner): `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fleet.py" upgrade v<N>`
  (`--dry-run` first). It moves the owner's pin in `~/.claude/settings.json` and the marketplace to the tag, puts
  every project's main checkout on origin's default branch and updates its plugin, runs `provision` from the new
  plugin and restarts the live feed. It names the projects that still pin an older release (their
  `/dark-factory:upgrade` PR first, then run it again). Restarting the sessions and On-calls asks first, because it
  ends them.
- **The `release` branch** holds the latest release and moves only in that last step. The Claude plugin directory
  follows it, so a merge to `main` does not reach the directory until it is released. Nothing is committed to
  `release` directly.
