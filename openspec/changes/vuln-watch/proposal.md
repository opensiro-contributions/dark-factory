# The factory watches its stack for vulnerabilities and patches the critical ones itself

## Why

A factory project runs unattended for days: nobody reads security advisories for its dependencies, and nothing in the
kit looked at them. The template CI scans for leaked secrets (trufflehog) and pins its actions by SHA, but no step and
no routine compared the versions in use with known vulnerabilities, and the production monitor watches data and
releases, not versions. A critical vulnerability in a library the factory itself installed could sit in production
until someone happened to notice.

The factory already has what a fix needs: a branch per change, the full test suite in CI, merges only on green, a dev
stand with acceptance, tagged releases and owner notifications. What was missing is the routine that looks, and the
rule for what it may do on its own.

## What changes

- **A nightly vulnerability check of every project.** The janitor's nightly pass runs `vulns.py` for each project with
  a lock file: the lock files of origin's default branch and of the newest release tag (uv.lock, requirements*.txt,
  package-lock.json, bun.lock) against the public OSV database and CISA's catalogue of known exploited
  vulnerabilities. No model runs for the scan itself.
- **One rule decides what the factory may do: who controls the component, and how severe it is.**
  - *Pinned in the project's repository* **and at or above the project's threshold** (`VULN_AUTOFIX_MIN`: critical by
    default; high, moderate or low by setting; anything CISA lists as exploited counts as critical) → the factory fixes
    it itself: a fix session raises the version on a `fix/` branch, adapts the code until the **full** test suite is
    green, has it reviewed and merges through `scripts/merge-feature.sh`. Then the project's setting decides: wait for
    acceptance on the dev stand like any change, or ask for a security release.
  - *Everything else* — below the threshold, or not pinned by the project — is **reported, never pushed**: one notice
    per new finding, nothing changed.
- **The full test suite is never skipped.** No path merges or releases a security fix without it.
- **A security release ships only the fix, and a human presses the button.** A release normally takes all of main,
  accepted or not yet. `scripts/release.sh --security <PR>` tags the newest release plus that one fix, so features
  still waiting for acceptance do not ride along. The guard asks the owner before it runs: the fences are not changed.
- **Production is judged by what the code says it runs.** The factory has no access to the production machines, so it
  scans the newest release tag as well as main and marks what production carries (IN PRODUCTION; "already fixed on
  main: release it" when only the release has it).
- **Nothing goes to the client chat.** An open vulnerability is told to the owner only.
- **Stuck means a human.** When the fix cannot be brought to a green suite within 3 rounds, the session stops with a
  draft PR and tells the owner what it tried.
- **The same steps by hand.** The `/dark-factory:vulns` skill checks and fixes in any session, a factory server or not;
  the nightly fix session follows the same skill.

## Not in this change

- Updating anything the project does not control (the hosting's machines, managed databases, third-party services).
- Probing production from outside or reading its machines (OS packages, TLS, open ports).
- Forcing updates below the threshold, or a Dependabot-style stream of update pull requests.
- Making CI fail on a newly published advisory: a vulnerability disclosed today must not turn every open pull request
  red; the routine acts on it instead.
- Telling the owner about versions that are merely behind, with no known vulnerability (not built; see tasks.md).
- Dockerfile base images and GitHub Actions pins (OSV's package query does not cover them; see tasks.md).
- Scanning the owner's workstation, Claude Code itself, or other plugins.
- Static analysis of the project's own code for vulnerabilities (a different job: the code-audit module).

## Acceptance scenarios

- WHEN an advisory at or above the threshold appears for a package pinned on main THEN the next nightly run opens a fix
  session; it raises the version on a `fix/` branch, runs the full suite and merges the PR on green; the owner is told.
- WHEN raising the version breaks tests THEN the session changes the code until the full suite is green, within 3
  rounds.
- WHEN the 3 rounds are used up THEN nothing is merged: a draft PR, and one report with what failed.
- WHEN the project is set to wait for acceptance (`VULN_RELEASE=accept`, the default) THEN the fix lands on the dev
  stand and waits for a yes like any change.
- WHEN the project is set to release at once (`VULN_RELEASE=now`) THEN the session runs `release.sh --security <PR>`,
  which the guard asks the owner about; on yes a release of the newest release tag plus the fix only is tagged and goes
  through the release workflow's tests; unaccepted features on main stay out of it.
- WHEN a finding is below the threshold THEN the owner gets one notice and nothing else happens; the same finding is not
  told again, unless it later becomes critical (CISA lists it).
- WHEN the release tag carries a finding THEN the notice marks it IN PRODUCTION.
- WHEN nothing new is found THEN nothing is sent.
