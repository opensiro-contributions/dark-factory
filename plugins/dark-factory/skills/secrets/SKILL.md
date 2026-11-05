---
name: secrets
description: >
  Where the project's secrets could leak or reach the wrong hands — the agent's included. A standard scanner
  (gitleaks, redacted) for secrets in git and the working tree, then what no scanner answers: which secrets the model
  can read, what each key is allowed to do, and the holes around them (backups the same key can delete, ports open to
  the internet, the agent's user in docker or sudo). Reports risks only, never values. Use on "check secrets", "can
  anything leak", "what can the agent see", "are our keys safe", before handing a project to agents, or periodically.
  Works in any git project, with or without the factory.
---

# Skill `secrets` — risks around the project's secrets

Output is a **risk report**: no recommendations, no fixes, no edits. **Never print a secret value** — not in the
report, not in a command's output you show, not "just the first characters". Name files, variables, key kinds.
Reading a secret file's content into your context is itself the risk this skill looks for. A model reads a secret
through the Read tool, `cat`/`head`/`grep`/`source`/`env`, `git show`, `git log -p`, `git grep`, or any diff that
contains it: use none of them on a secret file. Do not open files the inventory lists as holding secret kinds; the
script classifies values without showing them.

## Steps

1. **Inventory, from the script.** `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/secrets_scan.py" <project root>` (set
   `GITLEAKS=<path>` when gitleaks is not on PATH). With gitleaks it takes minutes on a large history: run it in the
   background and write its output to a file, don't pipe it through `head`. It prints names, paths, file modes and
   value kinds only:
   - gitleaks (`--redact`) over the git history and the working tree; without gitleaks, a narrower built-in scan of
     the tracked files and a note that the history was not scanned;
   - secret files in the project — tracked, untracked-but-not-ignored (one `git add .` away), ignored — with their
     variables and kinds (a DSN's user and whether its host is local or remote, token kinds);
   - secret files around it (`~/.config`, `~/.netrc`, cloud keys, gh, ssh keys), MCP server credentials in
     `~/.claude.json`, secret-shaped strings in shell histories;
   - what the agent may read: Claude Code permissions and sandbox from user, project and local settings;
   - the machine: the user's groups (docker = root, sudo group membership), containers and compose files publishing ports
     on all interfaces; dumps and backups in the home directory and who can delete them.
   gitleaks' `generic-api-key` findings are listed apart: most are config names, not secrets — say how many,
   and judge them by the file (a test fixture, a doc, an env file, production code) — don't open the lines.
2. **Judge each secret** (this is the part no scanner does). For every secret the inventory shows:
   - **who sees it**: in git (everyone with the repository, forever in history), readable by the agent's user (the
     model can read it: a `Read(...)` deny does not stop `cat` in Bash unless the OS sandbox is on for that path),
     world-readable;
   - **what it allows**, from evidence only: names, roles and config the project itself shows — a DSN user that
     owns the schema vs a read-only role next to it, an account-wide token vs a project-scoped one, MCP tools allowed
     in settings that mutate production (rebuild, restart, env vars), deploy scripts that use the key. Say "unknown"
     when the evidence is not there; never call a provider API or connect with a key to find out;
   - **what else it reaches**: backups or dumps deletable by the same user or key, production reachable from this
     machine, the same key in several projects (compare by file and variable name only, never by looking at values).
3. **Rank.** Production write or delete reachable by the agent > a secret in git history > account-wide tokens >
   world-readable secret files and dumps > exposure of third-party keys (cost and abuse) > hygiene.

## Report

In the user's language, short:

1. One line: how many secrets, how many reachable by the agent, how many in git.
2. **Risks**, ranked: the secret (file : variable or kind, never the value) → who sees it → what it allows → why it
   matters, with the evidence.
3. What was not checked (no gitleaks → no history; managed settings; provider-side scopes).
