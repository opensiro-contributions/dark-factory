# The factory watches its stack for vulnerabilities and patches the critical ones itself

## Why

A factory project runs unattended for days: nobody reads security advisories for its dependencies, and nothing in the
kit looks at them. The template CI scans for leaked secrets (trufflehog) and pins its actions by SHA, but no step and
no routine compares the versions in use with known vulnerabilities, and the production monitor watches data and
releases, not versions. A critical vulnerability in a library the factory itself installed can sit in production until
someone happens to notice.

The factory already has what a fix needs: a branch per change, the full test suite in CI, merges only on green, a dev
stand with acceptance, tagged releases, an On-call channel and owner notifications. What is missing is the routine that
looks, and the rule for what it may do on its own.

## What changes

- **A daily vulnerability check per project.** A timer on the server scans what the project's own code pins — the
  application's lock files (Python, JavaScript/Bun), its Dockerfiles' base images, the GitHub Actions it uses — against
  a public vulnerability database, and checks which versions are behind. No model runs for the scan itself.
- **One rule decides what the factory may do: who controls the component.**
  - *Controlled by the project* (the version is set in the project's repository) **and critical** → the factory fixes it
    itself: a task, a `fix/<ID>-<slug>` branch, the version raised, the code adapted until the **full** test suite is
    green, merged through `scripts/merge-feature.sh`. Then the project's setting decides: wait for acceptance on the dev
    stand like any change, or ask for a release at once.
  - *Everything else* — a non-critical finding, an outdated version with no known vulnerability, anything the project
    does not control — is **reported, never pushed**: one notice per new finding, nothing changed.
- **The full test suite is never skipped.** No path merges or releases a security fix without it, whatever the
  project's release setting.
- **A security release ships only the fix, and a human presses the button.** A release normally takes all of main,
  accepted or not yet. For a project set to "release at once" the factory prepares a release from the last release tag
  plus the fix, so features still waiting for acceptance do not ride along, and asks for it in one message ("security
  fix ready, release?") to the owner or the On-call. The release itself stays a human decision: the fences are not
  changed.
- **Production is judged by what the code says it runs.** The factory has no access to the production machines, so it
  scans what the repository declares: the scan covers the last release tag (what production runs) as well as main. A
  critical finding in the released version is an incident through the On-call in addition to the fix.
- **Stuck means a human.** When the fix cannot be brought to a green suite within a fixed number of attempts, the
  factory stops and tells the owner, with what it tried.

## Not in this change

- Updating anything the project does not control (the hosting's machines, managed databases, third-party services).
- Probing production from outside or reading its machines (OS packages, TLS, open ports): the product has no access
  there; only what the repository declares is checked.
- Forcing non-critical updates, or opening update pull requests the owner did not ask for (no Dependabot-style PR
  stream).
- Making CI fail on a newly published advisory: a vulnerability disclosed today must not turn every open pull request
  red; the routine acts on it instead.
- Scanning the owner's workstation, Claude Code itself, or other plugins.
- Static analysis of the project's own code for vulnerabilities (a different job: the code-audit module).

## Acceptance scenarios

- WHEN a critical advisory appears for a package pinned in a project's lock file THEN by the next daily run a `fix/`
  branch raises it, the full suite runs, and the PR is merged on green; the owner is told what was fixed.
- WHEN raising the version breaks tests THEN the agent changes the code until the full suite is green, inside the
  attempt limit.
- WHEN the attempt limit is reached THEN nothing is merged and the owner gets one notice with the branch and what failed.
- WHEN the project is set to wait for acceptance THEN the fix lands on the dev stand and waits for a yes like any change.
- WHEN the project is set to release at once THEN the owner or the On-call gets one "release?" message; on yes a release
  made of the last release tag plus the fix only is tagged and goes through the release workflow's tests; unaccepted
  features on main stay out of it.
- WHEN a finding is high, medium or low, or a version is merely behind THEN the owner gets one notice and nothing else
  happens; the same finding is not reported again the next day.
- WHEN the last release tag carries a critical finding THEN an incident goes through the On-call as well.
- WHEN nothing new is found THEN nothing is sent.
