# Tasks: vuln-watch

## Phase 1 — see: daily scan and notices

- [x] `vulns.py`: lock files of origin's default branch → OSV; severity + KEV → critical / other; seen-state
- [x] run by the nightly janitor for a project with lock files (first a timer of its own, folded in); status line in `fleet.py status`
- [x] notices through `fleet.py notify`, grouped by package, once per new finding, none on a quiet day
- [x] tests for parsing, classification, seen-state, the quiet day, a broken lock file, OSV down
- [ ] Dockerfile base images and GitHub Actions pins (OSV's package query does not cover them; carried over)
- [ ] versions merely behind, with no known vulnerability: one notice (asked for at the start; not built)
- [x] the `/dark-factory:vulns` skill: check and fix in any session; the nightly fix session follows it

## Phase 2 — fix: critical and controlled

- [x] fix session launcher: one Remote Control session (tmux `vulnfix`, own worktree, the project's permission mode),
      not doubled while one runs, once per finding
- [x] attempt limit (3 gate rounds) and the stuck report, in the session's instructions; `vulns.py tell` to report
- [x] `VULN_RELEASE` / `VULN_FIX` / `VULN_AUTOFIX_MIN` settings, documented in fleet.py and the provision-server skill
- [x] `release.sh --security <PR>`: release from the last tag + the fix; tag note; the fix PR labelled `accepted` with
      the security-release note; refusal before the first release, for a PR not on main, for a fix that does not apply
- [x] guard: `release.sh --security` is its own release decision (asked in a session a human answers, denied otherwise)

## Phase 3 — production view

- [x] scan the newest release tag as well as main; IN PRODUCTION marked in the notice, "already fixed on main: release
      it" when only the release has it
- [x] no incident through the On-call: it speaks in the client chat, where an open vulnerability must not be announced
      (changed from the design; the owner's notice carries IN PRODUCTION instead)
- [x] the "release?" question for `VULN_RELEASE=now` is the guard's permission dialog in the fix session, which the
      owner answers in claude.ai (no separate message path)
- [x] skill texts (provision-server, fleet.py settings, release.sh usage) updated
