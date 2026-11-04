# Design: vuln-watch

## Decided by the owner

1. **Scope rule = control.** What the project's repository pins is the factory's to fix; what it does not control is
   only reported. Layer does not matter (a base image the project's Dockerfile names is "controlled"; the hosting's
   OpenSSL is not).
2. **Auto-fix only for critical.** Everything else informs and never forces.
3. **When tests break, the agent adapts the code** to the new version and re-runs the tests.
4. **Release after the fix is per project:** through acceptance on the dev stand, or at once.
5. **The full automated test suite is mandatory** on every path. No setting turns it off.
6. **A release stays a human decision** (owner, 2026-11-04, option b). For `VULN_RELEASE=now` the factory prepares the
   security release and asks once; it never tags on its own. The fences get no exception.
7. **Only what the code declares is watched.** The product has no access to production machines; no OS package
   collection, no external probing. Production = the last release tag, scanned like main.

## Decided by the builder (Claude) — the owner can override

- **Scanner: the OSV API directly** (`api.osv.dev` querybatch + vuln details), lock files read by `vulns.py` itself
  (stdlib only: uv.lock, requirements*.txt `==` pins, package-lock.json, bun.lock). Changed during phase 1 from the
  osv-scanner binary: nothing to install on the server (the kit's requirements stay as they are), and a downloaded
  binary is code from outside that the session may not run. Seam: `parse_lockfile` + `lookup`; another stack adds a
  reader, no adapter layer. Severity is the GitHub advisory's (`database_specific.severity`); a PYSEC/CVE id that
  aliases a GHSA collapses into it.
- **Notices group by package version** ("pypdf 6.21.2 → 6.21.4: 7 advisories"): one upgrade closes them together;
  the first run on a real project listed 85 advisories in 20 packages.
- **"Critical"** = an advisory with severity CRITICAL (CVSS ≥ 9.0), or any severity listed in CISA's Known Exploited
  Vulnerabilities catalogue. Project setting `VULN_AUTOFIX_MIN=critical` (default; owner 2026-11-04: critical by
  default, the other levels — high, moderate, low — by the project's setting). A fix raises a package to the version
  that closes its advisories at the threshold and up (trial: starlette 1.0.1 for the critical one, not 1.3.1).
- **Release setting:** `VULN_RELEASE=accept|now` in `.claude/factory/session.env`; default `accept` (the safe one).
- **Attempt limit:** 3 build-and-test rounds, then stop and notify.
- **Schedule:** daily, a per-project `df-<slug>-vulns.timer` written by `fleet.py provision` for a project with lock
  files, next to the janitor; a random delay so projects do not scan at the same minute.
- **No new notification path.** Notices go through `fleet.py notify` (factory session / FLEET_NOTIFY_CMD / journal),
  the monitor's findings through its existing channel to the On-call.

## Flow

```
df-<slug>-vulns.timer (daily)
  └─ vulns.py <checkout>            no model: osv-scanner over lock files, Dockerfiles, workflow pins
       ├─ state: .claude/state/vulns/seen.json  — findings already reported or being fixed (no repeats)
       ├─ new non-critical / outdated  → one fleet notify, recorded as seen
       └─ new critical + controlled    → bd task (label security) + a fix session
             fix session = a Claude session with Remote Control in its own worktree (like the triage review),
             one builder with the build-tdd / integrate / wrap skills, spend cap:
               branch fix/<ID>-<slug> → raise the version → full gate → adapt code ≤ 3 rounds
               → merge-feature.sh (CI green) → then by VULN_RELEASE:
                    accept: dev stand updates itself (stand timer); waits for a yes
                    now:    ask the owner / On-call "security fix ready, release?" → on yes:
                            release.sh --security <PR>  (below), run by whoever may release
               stuck → stop, fleet notify with branch + failure; task stays open
```

### Security release (`VULN_RELEASE=now`, on a human's yes)

`release.sh` today tags `origin/main` and refuses while any merge since the last tag lacks `accepted`. A security
release must not carry those merges, so `release.sh --security <PR>`:

- cuts a branch from the last `v*` tag, cherry-picks the fix's merge, tags it `vYYYY.MM.DD.N` as usual;
- the release workflow runs the same light + heavy tests on that commit before `--publish`;
- the tag annotation and release notes say "security release: <advisory id>, fix PR #N, acceptance skipped by project
  setting";
- the next normal release from main contains the fix anyway (it is on main), so nothing diverges.

Refused when the cherry-pick conflicts: then the fix waits for the normal path and the owner is told.

### Production

The scan runs over two trees: `origin/main` and the last `v*` release tag (what the hosting builds from `release`).
A critical finding present in the release tag also raises an incident through the prod-monitor → On-call chain when
the project has that module (otherwise the owner notice says "in production"). Nothing reads or probes production
itself.

### Settled after the builder check (builder's calls)

- **Acceptance state after a security release.** The fix PR gets the label `accepted` with the comment "shipped in
  security release vX, acceptance skipped by the project's VULN_RELEASE=now"; the next normal release from main then
  counts it as accepted. The project chose to skip acceptance for these fixes, so this records that choice, not a yes.
- **Fenced files are report-only.** A critical finding whose fix needs a file the guard protects (`.github/workflows/*`
  used by release, `scripts/{merge-feature,release,feedback-desk}.sh`, …) is not auto-fixed: the owner is told, the fix
  needs their permission dialog. vuln-watch adds no exception to the fences for these files.
- **Team level.** At `FENCE_LEVEL=team` the fix PR still needs the GitHub approval `merge-feature.sh` requires; the
  fix session stops at "waiting for approval" and the owner is told. No bypass.

## Left to the builder

- How the fix session is started (reuse the triage review-session launcher or the feature-lite entry).
- The exact OSV / KEV caching and how often the DB is refreshed.
- How "outdated" is computed per ecosystem, and a sane limit on how many outdated packages one notice lists.
- Wording of the notices.

## Open for the owner

- Whether an auto-merged security fix also needs the reviewer agent's pass before merge (the default here: yes, the
  same single reviewer feature-lite runs).
