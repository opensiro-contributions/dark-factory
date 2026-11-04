# Design: vuln-watch

## Decided by the owner

1. **Scope rule = control.** What the project's repository pins is the factory's to fix; what it does not control is
   only reported.
2. **Auto-fix from a threshold:** critical by default; high, moderate or low by the project's setting
   (`VULN_AUTOFIX_MIN`). Below it a finding informs and is never forced.
3. **When tests break, the agent adapts the code** to the new version and re-runs the tests.
4. **Release after the fix is per project** (`VULN_RELEASE`): through acceptance on the dev stand, or at once.
5. **The full automated test suite is mandatory** on every path. No setting turns it off.
6. **A release stays a human decision.** With `VULN_RELEASE=now` the release question is the guard's permission dialog
   for `release.sh --security`; the factory never tags on its own. The fences get no exception.
7. **Only what the code declares is watched.** No access to production machines: no OS package collection, no external
   probing. Production = the newest release tag, scanned like main.
8. **One nightly pass.** The check runs inside the janitor, not on a timer of its own; a new nightly check joins it.
9. **One text for the fix steps:** the `/dark-factory:vulns` skill, used by a person's session and the nightly fix
   session alike.

## Decided by the builder — the owner can override

- **Scanner: the OSV API directly** (`api.osv.dev` querybatch + vuln details), lock files read by `vulns.py` itself
  (stdlib only: uv.lock, requirements*.txt `==` pins, package-lock.json, bun.lock). Not the osv-scanner binary: nothing
  to install, and a downloaded binary is code from outside that a session may not run. Seam: `parse_lockfile` +
  `lookup`; another stack adds a reader, no adapter layer.
- **Severity** is the GitHub advisory's (`database_specific.severity`); a PYSEC/CVE id that aliases a GHSA collapses
  into it. A CVE in CISA's known-exploited catalogue makes any severity critical.
- **The fix version** is the one on the release line the pinned version is in (`introduced ≤ pinned < fixed`); for a
  package the notice shows the highest of those across its advisories, the fix session the highest across the
  advisories at or above the threshold.
- **Notices group by package version** ("pypdf 6.21.2 → 6.21.4: 7 advisories"): one upgrade closes them together.
- **Defaults:** `VULN_RELEASE=accept`, `VULN_FIX` on, `VULN_CHECK` on; attempt limit 3 gate rounds.
- **No new notification path.** Notices and the fix session's report go through `fleet.py notify`.
- **No incident through the On-call:** it speaks in the client chat, where an open vulnerability must not be announced.

## Flow

```
fleet.py janitor (nightly): gc → hand-off → per project: queue review, then
  vulns.py <checkout>               no model: lock files of origin/main and the newest release tag → OSV + CISA
       ├─ state: .claude/state/vulns/state.json — findings told, findings handed to a fix session, the latest run
       ├─ new finding below the threshold → one fleet notify, recorded as told
       └─ new finding at/above it on main → one fix session, unless one runs (closed after 24 h)
             = a Claude session with Remote Control in its own worktree (tmux `vulnfix` in df-<slug>), the project's
               permission mode, started without a shell; first message: the findings as data
             follows /dark-factory:vulns, section Fix:
               bd bug → fix/ branch → raise versions → full gate, adapt code ≤ 3 rounds → reviewer
               → merge-feature.sh (CI green) → by VULN_RELEASE:
                    accept: the dev stand follows main; waits for acceptance
                    now:    release.sh --security <PR>; the guard asks the owner
               stuck → draft PR, stop; report with `vulns.py tell`
```

### Security release (`release.sh --security <PR>`)

- builds on the newest release tag on the remote, an earlier security release included (it is not on main; building on
  main's last tag would drop that earlier fix from production);
- cherry-picks the PR's merge (`-m 1` for a merge commit), tags the next `vYYYY.MM.DD.N`; the release workflow runs the
  same tests on that commit before `--publish`;
- the tag note says "Security release: <base> + the fix of PR #N only; acceptance of the fix skipped by the project's
  VULN_RELEASE=now"; the PR gets the label `accepted` with that note, so the next normal release counts it;
- refused before the first release, for a PR not on main, and when the fix does not apply on its own (it builds on
  unreleased work): then it goes the normal way;
- the guard treats it as its own release decision: asked where a human answers, denied in launched agents.

### Settled after the builder check

- **Fenced files are report-only.** A fix that needs a file the guard protects (`.github/workflows/release.yml`,
  `scripts/{merge-feature,release}.sh`, `.claude/settings*.json`, `.claude/fences.env`, `.githooks/`) stops and asks
  the owner. No exception to the fences.
- **Team level.** At `FENCE_LEVEL=team` the fix PR still needs the GitHub approval `merge-feature.sh` requires.
- **The reviewer pass** before the merge is part of the fix steps (one `dark-factory:reviewer` run).

## Evidence

- A live trial on one of the owner's projects: 29 advisories, 5 critical, all in production. The fix session raised four
  packages (and the web framework one of them required), regenerated the API client types, passed the full suite first
  time (943 tests), got a clean review and merged on green CI in 16 minutes; the dev stand followed.
- The skill against a no-skill baseline on the same scenario: without it an agent fixed below-threshold packages, merged
  a partial fix and invented a release rule; with it, it fixed only the critical ones and stopped after 3 red rounds.
