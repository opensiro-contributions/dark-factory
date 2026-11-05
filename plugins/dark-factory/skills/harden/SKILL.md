---
name: harden
description: >
  Find the project's rules that nothing enforces. Reads the agent instruction files (CLAUDE.md and its @imports,
  AGENTS.md, the coding standards they point to), matches every rule against the controls that really exist
  (permissions, hooks, git hooks, CI, linters, rule-checking tests, branch protection) and reports the rules that live
  only as text, as risks with evidence. Use on "harden", "which rules are not enforced", "audit CLAUDE.md", "are our
  rules real", after a big edit of CLAUDE.md, or periodically. Works in any git project, with or without the factory.
---

# Skill `harden` — rules that nothing enforces

A rule in CLAUDE.md is advice: the model weighs it against the task in front of it. A rule is real only when a control
blocks or fails on the violation. This skill finds the gap and shows it as **risks**. It does not fix, propose fixes or
edit anything; what to do with each risk is the owner's call (make it a control, drop the rule, or accept the risk).

## Steps

1. **Controls, from the script.** `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/harden.py" .` from the project root. It lists,
   without a model: the effective fence level as the guard reads it (the stricter of the working tree and origin's
   main branch) and what it does and does NOT enforce at it; Claude Code permissions, sandbox and hooks from the user,
   project and local settings (managed settings and start-up flags are not read: say so when it matters); the
   plugin's own hooks; git hooks without comments (flagged when not executable); CI jobs with their triggers, `if:`
   conditions and `run:` bodies; linter, type-checker and pre-commit config; branch protection. This is the evidence
   of what is enforced; open a control's file to confirm a match or when the listing is cut. The list of rule-check
   scripts and tests is found by file name: a hint where to look, not evidence.
2. **Rules.** Read CLAUDE.md (follow `@path` imports), CLAUDE.local.md, nested CLAUDE.md files of the directories
   the rules are about, `.claude/rules/`, AGENTS.md, the user-level `~/.claude/CLAUDE.md`, and the standards files
   they point to (`CODING_STANDARDS.md` and the like). Extract each imperative rule (never / always / must / only via / don't /
   forbidden …) as one line with file:line. A rule repeated in several files counts once (cite the first). Group rules
   that one control would cover (all "commit messages …" rules → one line).
3. **Match.** For each rule decide, for **the agent, at the moment it would break the rule**:
   - **ENFORCED** — a control blocks or fails on the violation in time;
   - **PARTIAL** — a control covers some forms, or fires too late, or can be skipped. Typical: a CI check while the
     default branch has no protection and the agent may merge (`gh pr merge` allowed at `solo`, `--skip-ci`); a
     pre-push gate that runs only on pushes the guard already forbids the agent; a `Read(.env)` deny that `cat` in Bash
     walks around; a check that runs only on release tags;
   - **TEXT-ONLY** — no control;
   - **JUDGEMENT** — not mechanically checkable (taste, product decisions); not a risk, list it and move on.
   Cite the control (file:line, settings key or CI job) for ENFORCED and PARTIAL.
4. **Evidence, bounded (about 10 commands).** For greppable TEXT-ONLY and PARTIAL rules count current violations in
   tracked files (`git grep -n` / `-c`); look through `git log --oneline -300` for the same thing fixed more than once.
   A rule already broken, or fixed again and again, is the strongest risk.
5. **Contradictions.** Where the text and the controls disagree: the text says "denied" and the level allows it; local
   settings switch off what shared settings promise; a naming or process rule the scripts and history don't follow.

Commands that name the fence files (`.claude/settings*.json`, `.claude/fences.env`) in a grep are fine but may be asked
about by the guard; the script already read them — prefer its output.

## Report

In the user's language, short:

1. One line: `N rules — E enforced, P partial, T text-only, J judgement`.
2. **Risks**, ranked: already broken or fixed repeatedly > safety and secrets > process > style. Each: the rule
   (file:line) → what exists → what it lets through → the evidence (violations with file:line, repeat-fix commits).
3. **Contradictions.**
4. The full table at the end, folded (`<details>`).

Never print secret values; for a rule about secrets name files and variables only.
