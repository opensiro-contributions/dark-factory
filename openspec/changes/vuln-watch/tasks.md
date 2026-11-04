# Tasks: vuln-watch

## Phase 1 — see: daily scan and notices

- [x] `vulns.py`: lock files of origin's default branch → OSV; severity + KEV → critical / other; seen-state
- [x] `fleet.py provision`: `df-<slug>-vulns.timer` for a project with lock files; status line in `fleet.py status`
- [x] notices through `fleet.py notify`, grouped by package, once per new finding, none on a quiet day
- [x] tests for parsing, classification, seen-state, the quiet day, a broken lock file, OSV down
- [ ] Dockerfile base images and GitHub Actions pins (OSV's package query does not cover them; carried over)

## Phase 2 — fix: critical and controlled

- [ ] fix session launcher (worktree, Remote Control, spend cap) with the build-tdd / integrate / wrap skills
- [ ] attempt limit and the stuck notice
- [ ] `VULN_RELEASE` / `VULN_AUTOFIX_MIN` settings, documented in the session.env template
- [ ] `release.sh --security <PR>` (run on a human's yes): release from the last tag + the fix; notes and tag annotation;
      the fix PR labelled `accepted` with the security-release note; conflict refusal

## Phase 3 — production view

- [ ] scan the last release tag as well as main; "in production" marked in the notice
- [ ] critical in the release tag → incident through prod-monitor / On-call when the module is present
- [ ] the "release?" message for `VULN_RELEASE=now`: to the On-call when present, else the owner notice
- [ ] skill texts (prod-monitor, session.env template, `/dark-factory:add`) updated
