#!/usr/bin/env python3
"""Fence guard (PreToolUse hook): the agent cannot do what only a human may decide.

Instructions in CLAUDE.md ("do not push without asking", "merge only after a human's yes")
are advice; this hook is the fence. It reads the hook JSON on stdin and answers `deny` with
what to do instead, or — for a change to the fences themselves — `ask`. stdlib only, Python 3.11+.

Bash — denied at every level (`solo` and up):
  * `git push` with -f/--force/--force-with-lease/--force-if-includes/--mirror or a `+refspec`;
    a long option counts by any prefix git would expand (`--forc`, `--no-verif`, `--tag`);
  * a push to a protected branch: `main`, `master`, origin's HEAD branch, MAIN_BRANCH of
    scripts/merge-feature.sh, and the production branch (`release`, or RELEASE_BRANCH of
    scripts/release.sh) — `git push origin main`, `HEAD:heads/main`, `git push` while on it, `--all`,
    a `*` pattern, deleting it; a destination computed at run time (`feat:$B`, `HEAD:$(…)`) is denied
    too, except `$(git branch --show-current)` / `$(git rev-parse --abbrev-ref HEAD)`;
  * `--no-verify` on `git commit`/`git push`, `-n` on `git commit`, hooks re-pointed away from
    `.githooks` (git config, `git -c`, `GIT_CONFIG_*` variables, an edit of .git/config or .git/hooks/);
  * git configuration that runs or reroutes things: `git -c` / `git config` of alias.*, core.hooksPath,
    core.fsmonitor, core.pager, core.sshCommand, diff.external, *.textconv, filter.*, include*, pager.*,
    push.*, remote.*.push|mirror|receivepack; `GIT_CONFIG_*`/`GIT_EXTERNAL_DIFF`/`GIT_EXEC_PATH` set for
    a command. An existing git alias is expanded and checked like the command it stands for;
  * pushing release tags (`v*`, also `--tags`/`--follow-tags`), `gh release create|edit|delete` —
    that is scripts/release.sh;
  * the GitHub API used to do the same: `gh api` (or curl/wget/httpie to api.github.com, or any request
    carrying the gh token) that writes refs, contents, merges or releases, changes branch protection or
    rulesets; a GraphQL query read from a file; `gh alias set|import`;
  * `git branch -d/-D` of the main branch, `git reset --hard` while on the main branch; rewriting
    origin's remote-tracking refs (they hold the main branch's fence level, see below);
  * deleting, moving or overwriting .git/config or .git/hooks/ from the shell (an ancestor counts:
    `rm -rf .`), an Edit/Write of .git/hooks/* or of .git/config that touches hooks, aliases, pagers…;
  * any write into the installed plugin: `~/.claude/plugins/**`, `$CLAUDE_PLUGIN_ROOT` (unless it is
    inside the project: a plugin under development) and the plugin's state `~/.cache/dark-factory`
    (the level grants, `prompt-hook`) — the plugin changes through its canon repository and an update;
  * booking a `/dark-factory:level <level>` prompt the human did not type: in a scheduling or
    messaging tool's input (send_later, triggers, CronCreate, ScheduleWakeup, send_message…) or a
    `claude` run from Bash.
Fence changes — asked at every level (see "Ask or deny" below):
  * deleting, moving or overwriting from the shell .claude/fences.env, .claude/settings*.json,
    ~/.claude/settings*.json, .githooks/: a redirection, rm/mv/cp/tee/sed -i/awk -i inplace…, a glob or
    an ancestor directory that covers them (`rm -rf .cl*`), `find … -delete|-exec`, `git rm/mv`,
    `git checkout <rev> -- …`, `git restore --source …`, `git clean` / `git stash -u|--all` while one of
    them is untracked (or ignored, with -x/--all);
  * any other command that names them (`fences.env`, `disableAllHooks`, `.claude/settings`) unless it
    only reads (cat, grep, git diff…): an interpreter one-liner (`python3 -c "open('.claude/fences.env','w')"`)
    is not a read;
  * lowering FENCE_LEVEL (or QUOTA_FAIL_CLOSED 1 → 0) with Edit/Write/MultiEdit: the old and the new
    text are compared; an edit whose new value cannot be told counts as a change. The usual way is
    `/dark-factory:level <level>`, which the human types (scripts/level.py);
  * an edit of any Claude Code settings file that adds disableAllHooks/allowManagedHooksOnly, a
    GUARD/QUOTA_GUARD variable, sets `dark-factory@…` to false or drops it from enabledPlugins;
    `claude plugin disable|uninstall` of it from Bash; `GUARD`/`QUOTA_GUARD` set or exported for a command
    (a child `claude` would inherit it and run unfenced).
Acceptance and release decisions (module release-tag; every level), see "Release decisions" below:
  * a run of `scripts/release.sh` that tags (no `--publish`), `scripts/feedback-desk.sh release|accept`;
  * marking a feature accepted by hand: `gh pr|issue edit --add-label accepted`, `gh label create|edit|delete|clone`,
    `gh pr|issue comment` with "accepted by" (or a body the guard cannot read), and the `gh api` equivalents.
Added at `standard` (the default) and `team`:
  * `gh pr merge` (and the merge API) — merging goes through scripts/merge-feature.sh, which
    waits for green CI (and, at `team`, the PR's approval); `gh pr review --approve` (and the review API) — approvals are
    given by people — denied;
  * any edit of the fence files (.claude/settings*.json, .claude/fences.env, .githooks/,
    scripts/merge-feature.sh) with Edit/Write or a Bash write — asked.

Ask or deny: a fence change is the human's to allow. When a human can be present the guard answers
`ask`, and Claude Code shows the call in its permission dialog with the guard's reason; it runs only on
a yes. In a launched agent nobody can answer, so the guard denies it and says to leave it to the human
(write the patch and a note, keep going): a subagent (the hook input carries `agent_id`), a session our
launchers start (`DF_UNATTENDED=1`: the On-call of feedback-desk.sh), `claude -p` or another SDK run
(`CLAUDE_CODE_ENTRYPOINT=sdk-…`) other than a remote-control session (`CLAUDE_CODE_ENVIRONMENT_KIND=bridge`:
its dialogs reach the owner in the Claude app and claude.ai). These signals come from the hook's own input and environment; a miss
costs liveness, not safety (`ask` inside `claude -p` is a deny in every permission mode). When a call has
both a fence change and something denied, the deny wins: a yes never lets a denied part through.

Part of the dark-factory plugin (hooks/hooks.json); the project is $CLAUDE_PROJECT_DIR (else the
git top level of the hook's cwd). A project opts in with `.claude/fences.env` (in the working tree
or on origin's main branch): without it the guard stays silent, so a plugin installed for the user
does not fence unrelated projects. The fence level is the stricter of the working tree's
`.claude/fences.env` and the same file on origin's main branch (FENCE_LEVEL=solo|standard|team,
default standard): lowering it takes a change merged into the main branch.

How a command is read: quoted strings and heredoc bodies are data, not commands (a commit
message may say "--force" or "gh pr merge"); the line is split on && || ; | & newlines and
parentheses; `$(…)`/backticks — also inside double quotes and unquoted heredocs — `bash -c '…'`,
`eval` and a heredoc fed to a shell are checked as commands of their own; `env`/`VAR=` prefixes,
wrappers (`command`, `nohup`, `xargs`, `timeout`…), `cd`, `env -C` and `git -C dir` are followed.
A static reading has limits (a value hidden in a variable or a script file is not seen); the main
branch's fence level, permissions.deny in settings.json and GitHub branch protection are the
further layers.

Release decisions: a release takes all of main to production and acceptance is its precondition, so both are
a person's call. In a session where a human can answer the guard asks ("Yes = you accept it"); in the On-call
(`DF_ROLE=on-call`, set by feedback-desk.sh in the hook's own environment, which an agent's command cannot
reach) the wrapper's `accept` and `release` pass (the wrapper checks the sender); a direct `release.sh` (other than
the read-only `--list-unaccepted`), `--owner-accepts`, `--manual` and a hand-set label or comment are denied; the
On-call may run nothing but one plain `scripts/feedback-desk.sh accept|release …` command, the project's own script
by its path from the project root. DF_ROLE, DF_UNATTENDED, DESK_* and RELEASE_GH are fenced like GUARD: set,
exported or passed through `env` they are a fence change (a child claude would inherit the On-call's allowances); any other launched agent (subagent, pipeline, scheduled
run, `claude -p`) is denied: "leave it to the owner or the On-call".

Bypass is for a human only: restart the session with GUARD=off (the same idea as QUOTA_GUARD);
every call let through that way, every ask and every deny is logged to .claude/state/audit.jsonl.
"""

from __future__ import annotations

import fnmatch
import json
import os
import pwd
import re
import subprocess
import sys
import time
from pathlib import Path

LEVELS = ("solo", "standard", "team")
DEFAULT_LEVEL = "standard"
FENCES_FILE = ".claude/fences.env"
AUDIT_FILE = ".claude/state/audit.jsonl"
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
RELEASE_TAGS = ("v*",)
# A bare push name that looks like a release tag even when no such local tag exists.
RELEASE_TAG_SHAPE = re.compile(r"v\d[\w.\-]*")
# Always protected, whatever the project's scripts say; the production branch by default.
PROTECTED_BRANCHES = ("main", "master")
RELEASE_BRANCH = "release"
# The fences themselves, and the files that control the On-call and the release: any edit of them is asked (at standard and team).
FENCE_FILE_RE = re.compile(
    r"(?:^|/)(?:\.claude/(?:settings(?:\.local)?\.json|fences\.env)"
    r"|\.githooks/[^/]+"
    r"|scripts/(?:merge-feature|release|feedback-desk)\.sh"
    r"|\.claude/feedback-desk/.+"
    r"|\.github/workflows/release\.yml"
    r"|\.mcp\.json)$"
)
FENCES_ENV_RE = re.compile(r"(?:^|/)\.claude/fences\.env$")
# Deleted, moved or written from the shell: asked (.git/ ones denied) at every level; Edit/Write get content checks.
SELF_FILE_RE = re.compile(
    r"(?:^|/)(?:\.claude/(?:fences\.env|settings(?:\.local)?\.json)"
    r"|\.githooks(?:/.*)?"
    r"|\.git/(?:config(?:\.worktree)?|hooks(?:/.*)?|worktrees/[^/]+/config\.worktree))$"
)
GIT_INTERNALS_RE = re.compile(r"(?:^|/)\.git/(?:config(?:\.worktree)?|hooks(?:/.*)?|worktrees/[^/]+/config\.worktree)$")
GIT_HOOKS_RE = re.compile(r"(?:^|/)\.git/hooks(?:/.*)?$")
SETTINGS_RE = re.compile(r"(?:^|/)\.claude/settings(?:\.local)?\.json$")
SETTINGS_NAMES = ("settings.json", "settings.local.json")
LEVEL_RANK = {level: rank for rank, level in enumerate(LEVELS)}
# Under the home directory: the plugin's state (level grants and hint stamps of scripts/level.py).
STATE_DIR = ".cache/dark-factory"
GLOB_CHARS = re.compile(r"[*?\[]")
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
REDIR_RE = re.compile(r"(\d*)(&>>|&>|>>|>\||>&|<<<|<<-|<<|<&|<>|>|<)")
OPERATORS = ("&&", "||", "|&", ";;", ";", "|", "&", "(", ")")
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
KEYWORDS = {"!", "{", "}", "if", "then", "else", "elif", "do", "while", "until", "fi", "done", "esac", "time"}
# Wrappers that run the command after them: options that take a value, and positional
# arguments before the command (timeout DURATION).
WRAPPERS = {
    "command": set(), "builtin": set(), "nohup": set(), "exec": {"-a"},
    "sudo": {"-u", "-g", "-C", "-p", "-h", "-U", "-r", "-t"}, "doas": {"-u", "-C"},
    "nice": {"-n"}, "ionice": {"-c", "-n", "-p"}, "stdbuf": {"-i", "-o", "-e"},
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-E", "-s", "-a"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
}
WRAPPER_POSITIONAL = {"timeout": 1}
GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"}
# git's own commands (`git --list-cmds=builtins`): an alias cannot shadow them, anything else may be one.
GIT_BUILTINS = frozenset("""
    add am annotate apply archive bisect blame branch bugreport bundle cat-file check-attr check-ignore
    check-mailmap check-ref-format checkout checkout-index cherry cherry-pick clean clone column commit
    commit-graph commit-tree config count-objects credential describe diagnose diff diff-files diff-index
    diff-tree difftool fast-export fast-import fetch fetch-pack fmt-merge-msg for-each-ref for-each-repo
    format-patch fsck gc grep hash-object help hook index-pack init init-db interpret-trailers log ls-files
    ls-remote ls-tree mailinfo mailsplit maintenance merge merge-base merge-file merge-index merge-tree mktag
    mktree multi-pack-index mv name-rev notes pack-objects pack-refs patch-id prune pull push range-diff
    read-tree rebase reflog remote repack replace rerere reset restore rev-list rev-parse revert rm shortlog
    show show-branch show-index show-ref sparse-checkout stage stash status stripspace switch symbolic-ref
    tag unpack-file unpack-objects update-index update-ref update-server-info var verify-commit verify-pack
    verify-tag version whatchanged worktree write-tree gui citool gitk instaweb send-email request-pull
    submodule mergetool filter-branch replay backfill
""".split())
# The usual ways to name the current branch in a push: resolved, not treated as an unknown destination.
CURRENT_BRANCH_SUBS = {"git branch --show-current", "git rev-parse --abbrev-ref HEAD", "git symbolic-ref --short HEAD",
                       "git symbolic-ref -q --short HEAD", "git symbolic-ref --short -q HEAD"}
# git configuration that runs a program, reroutes a push or hides a command behind another name.
DANGEROUS_CONFIG_RE = re.compile(
    r"^(?:alias|include|includeif|filter|pager|push)\..+"
    r"|^core\.(?:hookspath|fsmonitor|pager|sshcommand|gitproxy|askpass)$"
    r"|^diff\.external$|^.+\.textconv$|^remote\..+\.(?:push|mirror|receivepack)$",
    re.I,
)
# The same, as text of .git/config.
GIT_CONFIG_TEXT_RE = re.compile(
    r"^\s*\[\s*(?:alias|include|includeif|filter|pager|push)\b"
    r"|^\s*(?:fsmonitor|pager|sshcommand|gitproxy|askpass|textconv|external|mirror|receivepack|push)\s*="
    r"|hookspath\s*=(?!\s*\.githooks\s*$)",
    re.I | re.M,
)
DANGEROUS_ENV_RE = re.compile(r"^(?:GIT_CONFIG\w*|GIT_EXTERNAL_DIFF|GIT_EXEC_PATH)\+?=")
EXPORTERS = {"export", "declare", "typeset", "local", "readonly"}
# The guards' own switches: only the human restarts a session with them.
# DF_ROLE / DF_UNATTENDED / DESK_* / RELEASE_GH decide who the On-call is and whom feedback-desk.sh and
# release.sh trust: a child claude or a wrapper that inherits them would get the On-call's allowances.
GUARD_KEYS = r"(?:QUOTA_)?GUARD|DF_ROLE|DF_UNATTENDED|DESK_\w+|RELEASE_GH"
GUARD_ENV_RE = re.compile(rf"^(?:{GUARD_KEYS})\+?=")
# Settings that switch the plugin's hooks off, or the guard through the environment.
SETTINGS_OFF_RE = re.compile(
    r"disableAllHooks|allowManagedHooksOnly|\"dark-factory@[^\"]*\"\s*:\s*false|\"(?:QUOTA_)?GUARD|DF_ROLE|DF_UNATTENDED\"\s*:", re.I)
PLUGIN_ON_RE = re.compile(r"\"dark-factory@dark-factory\"\s*:\s*true")
# Tools that book a prompt for later or for another session: they must not carry the human's level command.
SCHEDULE_TOOL_RE = re.compile(
    r"send_later|create_trigger|update_trigger|fire_trigger|RemoteTrigger|CronCreate|ScheduleWakeup"
    r"|send_message|SendMessage", re.I)
LEVEL_TARGET_RE = re.compile(r"/(?:dark-factory:)?level\b.{0,80}?\b(?:solo|standard|team)\b", re.S)
LEVEL_CMD_RE = re.compile(r"/(?:dark-factory:)?level\b")
GH_MERGE_API_RE = re.compile(r"mergePullRequest|enablePullRequestAutoMerge")
GH_REVIEW_API_RE = re.compile(r"addPullRequestReview|submitPullRequestReview")
GH_REFS_API_RE = re.compile(r"createRef|updateRefs?|deleteRef|mergeBranch|createCommitOnBranch")
GH_PROTECTION_API_RE = re.compile(r"BranchProtectionRule|RepositoryRuleset")
GITHUB_API_RE = re.compile(r"(?:api|uploads)\.github\.com|/api/v3\b|/api/graphql\b")
GITHUB_TOKEN_RE = re.compile(r"\b(?:GH_TOKEN|GITHUB_TOKEN|GH_ENTERPRISE_TOKEN)\b|\bgh[pousr]_[A-Za-z0-9]{20,}")
HTTP_TOOLS = {"curl", "wget", "http", "https", "xh", "xhs"}
HTTP_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

BYPASS = "Only a human can bypass this: restart the session with GUARD=off."
UNATTENDED_ADVICE = "Leave it to the human: write the patch and a note, keep going."

# (what is denied, what to do instead)
MERGE = (
    "merging a PR directly is denied",
    "Merging goes through scripts/merge-feature.sh <branch> [title]: it waits for green CI "
    "(at team level also for the PR's approval) and merges.",
)
APPROVE = (
    "approving a PR from the agent is denied",
    "An approval is a person's decision in their own name; ask a reviewer.",
)
FORCE = (
    "force push is denied (it rewrites shared history)",
    "Push without force: merge the main branch into your branch, or push to a new branch. "
    "If history really must be rewritten, ask the human to do it.",
)
MAIN = (
    "pushing to the main branch '{main}' directly is denied",
    "Code reaches '{main}' through a PR: push the feature branch, and merge it with "
    "scripts/merge-feature.sh <branch>. A docs-only "
    "change — a docs/* branch through the same script, or ask the human to push it.",
)
PROTECTED = (
    "pushing to the protected branch '{branch}' directly is denied",
    "The main branch moves through a PR and scripts/merge-feature.sh; "
    "the production branch only through scripts/release.sh. Push a feature branch instead, or ask the human.",
)
DYNAMIC_PUSH = (
    "a push whose destination is computed at run time is denied ($VAR, $(…), `…`)",
    "Name the branch literally (git push origin <branch>), or push the current branch with "
    "git push origin HEAD from the branch itself.",
)
NO_VERIFY = (
    "bypassing the git hooks is denied (--no-verify, commit -n, core.hooksPath, .git/config, .git/hooks/)",
    "The hooks are the gate (tests): fix what the hook reports; if the hook itself is wrong, tell the human.",
)
GIT_CONFIG = (
    "changing git's configuration in a way that runs or reroutes commands is denied (aliases, git -c, "
    "GIT_CONFIG_* variables, hooks, pagers, push settings)",
    "Run the plain git command. If a setting really must change, describe it and ask the human to change it.",
)
REMOTE_REFS = (
    "rewriting origin's remote-tracking refs is denied (update-ref/symbolic-ref of refs/remotes, "
    "git remote rename|remove|set-head, a fetch into refs/remotes with an explicit refspec)",
    "They tell the guard what the main branch says; a plain git fetch keeps them current. "
    "If one is really wrong, ask the human.",
)
TAG = (
    "pushing release tags (v*) is denied",
    "Release tags are cut by scripts/release.sh (release-tag module) or by the human; "
    "push other tags by name.",
)
GH_RELEASE = (
    "creating, editing or deleting a GitHub release from the agent is denied",
    "Releases are cut by scripts/release.sh (release-tag module) or by the human.",
)
GH_REFS = (
    "writing git refs or contents through the GitHub API is denied (git/refs, merges, contents, branch rename, "
    "createRef/updateRef/mergeBranch/createCommitOnBranch)",
    "Push a feature branch with git push and open a PR; the main branch moves through scripts/merge-feature.sh.",
)
GH_PROTECTION = (
    "changing branch protection or rulesets is denied",
    "Branch protection is the human's setting on GitHub: describe the change and ask them to make it.",
)
GH_OPAQUE = (
    "a GitHub API call whose content cannot be read here is denied (a query or body from a file or stdin, "
    "a computed endpoint)",
    "Pass the query inline (gh api graphql -f query='…'), or use the gh command for it.",
)
GH_ALIAS = (
    "defining gh aliases is denied (gh alias set|import)",
    "Run the gh command itself; an alias would hide it from the guard.",
)
HTTP_API = (
    "calling the GitHub API with curl/wget/httpie to change something, or with the gh token, is denied",
    "Use gh (gh pr create, gh pr view, gh api for reads); merges, approvals, tags and branch protection are "
    "the human's.",
)
MAIN_BRANCH_DELETE = (
    "deleting the main branch '{main}' is denied",
    "Ask the human. To leave '{main}', switch away from it: git switch --detach origin/{main}.",
)
RESET_MAIN = (
    "git reset --hard on the main branch '{main}' is denied",
    "Work on a feature branch; to look at origin's state use git switch --detach origin/{main}. "
    "If local '{main}' really must be reset, ask the human.",
)
# The fence changes below are the human's to allow: the guard answers `ask`, and Claude Code shows the call in
# its permission dialog. Their first part names the change (not "is denied"): it reads both in the dialog and
# in the deny a launched agent gets.
FENCE_EDIT = (
    "this changes a fence file (.claude/settings*.json, .claude/fences.env, .githooks/, scripts/merge-feature.sh)",
    "These files hold the Claude Code settings, the git hooks, the fence level and the merge gate.",
)
CLEAN_FENCES = (
    "this removes untracked fence files (git clean, git stash -u/--all while .claude/fences.env, "
    ".claude/settings*.json or .githooks/ is untracked or ignored)",
    "To keep them: git clean … -e .claude -e .githooks, git stash push -u -- <paths>, or commit them first.",
)
SETTINGS_OFF = (
    "this can switch off the hooks or the dark-factory plugin (disableAllHooks, allowManagedHooksOnly, "
    "GUARD/QUOTA_GUARD in settings env, enabledPlugins, claude plugin disable|uninstall, a Claude Code settings "
    "file changed from the shell)",
    "With the plugin off nothing is fenced.",
)
LEVEL_ADVICE = (
    "The usual way to change the fence level is /dark-factory:level <level>, typed by the human; a lowered "
    "level binds only once it is merged into the main branch."
)
FENCES_ENV = (
    "this deletes, moves, truncates or rewrites .claude/fences.env",
    LEVEL_ADVICE,
)
FENCE_LOWER = (
    "this lowers the fence level in .claude/fences.env (FENCE_LEVEL, or QUOTA_FAIL_CLOSED from 1 to 0)",
    LEVEL_ADVICE,
)
# Release decisions (module release-tag). The first part names the decision; the reasons are not ASKABLE: main()
# resolves them by who runs the session (a human: ask; the On-call: its narrow allowance; a launched agent: deny).
RELEASE_ADVICE = "Leave the release to the owner or the On-call."
RELEASE = (
    "a release ships all of main since the last tag to production; Yes = you accept it",
    RELEASE_ADVICE,
)
RELEASE_OWNER = (
    "a release with --owner-accepts ships all of main since the last tag to production and marks every unaccepted "
    "feature it lists as accepted by the owner; Yes = you accept all of them",
    RELEASE_ADVICE,
)
RELEASE_INCLUDE = (
    "a release with --include-unaccepted ships all of main since the last tag to production although some features "
    "lack the acceptance mark; Yes = you allow it",
    RELEASE_ADVICE,
)
RELEASE_MANUAL = (
    "an emergency release (--manual) ships all of main to production at once, without tests and without the "
    "acceptance check; Yes = you decide it",
    RELEASE_ADVICE,
)
RELEASE_SECURITY = (
    "a security release (--security <PR>) ships the last release plus that one fix to production and marks the fix "
    "accepted, skipping its acceptance on the dev stand; Yes = you release it",
    RELEASE_ADVICE,
)
ACCEPT_MARK = (
    "marking a feature accepted by hand (the label 'accepted', a comment 'accepted by …') is the acceptance "
    "decision; Yes = you accept it",
    "Leave acceptance to the owner or the On-call (scripts/feedback-desk.sh accept).",
)
ACCEPT_WRAPPER = (
    "scripts/feedback-desk.sh accept marks a feature accepted; Yes = you accept it",
    "Leave acceptance to the owner or the On-call.",
)
# Most restrictive first: a command with several decisions gets the first one found here.
DECISIONS = (ACCEPT_MARK, RELEASE_MANUAL, RELEASE_OWNER, RELEASE_INCLUDE, RELEASE_SECURITY, RELEASE, ACCEPT_WRAPPER)
# What the On-call may do through its wrapper (feedback-desk.sh checks the sender itself).
ON_CALL_MAY = (RELEASE, RELEASE_INCLUDE, ACCEPT_WRAPPER)
ON_CALL_ONLY = (
    "the On-call runs only one plain `scripts/feedback-desk.sh accept|release …` command (no chaining, no "
    "substitutions, no redirections, no assignments), or `scripts/release.sh --list-unaccepted`; the script "
    "is the project's own, by its path from the project root",
    "Release through the wrapper: it checks who said yes.",
)


def is_decision(reason: tuple | None) -> bool:
    return any(reason is r or reason == r for r in DECISIONS)


# A human may allow these in the permission dialog; every other reason is a plain deny.
ASKABLE = (FENCE_EDIT, CLEAN_FENCES, SETTINGS_OFF, FENCES_ENV, FENCE_LOWER)
LEVEL_SELF = (
    "booking a /dark-factory:level prompt is denied (a scheduled or relayed prompt, another session, claude -p)",
    LEVEL_ADVICE,
)
PLUGIN_EDIT = (
    "changing the installed dark-factory plugin or its state is denied (~/.claude/plugins, $CLAUDE_PLUGIN_ROOT, "
    "~/.cache/dark-factory)",
    "The plugin is the fence: it changes by a plugin update the human runs (/plugin update), not by an edit in "
    "place. Describe the change you propose instead.",
)
# A command that names a fence file and is not a plain read (an interpreter one-liner, find, a copied script).
MENTIONS = (
    (re.compile(r"fences\.env"), FENCES_ENV),
    (re.compile(r"level-grants|prompt-hook"), PLUGIN_EDIT),
    (re.compile(r"disableAllHooks|allowManagedHooksOnly|\.claude/settings|settings\.local\.json", re.I), SETTINGS_OFF),
    (re.compile(r"\.git/(?:hooks|config)\b"), NO_VERIFY),
)
# Commands that only read what they name (writes through their redirections are checked as paths).
READERS = {"cat", "less", "more", "head", "tail", "grep", "egrep", "fgrep", "rg", "ag", "wc", "ls", "stat", "file",
           "diff", "cmp", "jq", "echo", "printf", "test", "[", ":", "true", "false", "sha256sum", "sha1sum",
           "md5sum", "bat", "realpath", "readlink", "basename", "dirname", "cp", "cd", "pushd", "popd", "gh", "bd"}
# git subcommands that run commands of their own.
GIT_RUNS = {"filter-branch", "filter-repo", "bisect", "submodule", "rebase", "difftool", "mergetool", "config"}


def _fill(reason: tuple, **values: str) -> tuple:
    out = []
    for part in reason:
        for key, value in values.items():
            part = part.replace("{" + key + "}", value)
        out.append(part)
    return tuple(out)


def askable(reason: tuple | None) -> bool:
    """A fence change the human may allow in the permission dialog (the guard asks instead of denying)."""
    return any(reason is r or reason == r for r in ASKABLE)


def _worst(reasons) -> tuple | None:
    """The first reason that is a plain deny, else the first askable one: a yes to one fence change in a
    command must never let a denied part of it through."""
    found, decision = None, None
    for reason in reasons:
        if is_decision(reason):
            if decision is None or DECISIONS.index(reason) < DECISIONS.index(decision):
                decision = reason
        elif reason and not askable(reason):
            return reason
        else:
            found = found or reason
    # a release decision never hides a fence change: the On-call's allowance must not let it through
    return found or decision


# --- reading a shell command -------------------------------------------------


class Word(str):
    """A shell word after quote removal. dynamic — it depends on an expansion ($x, $(…)),
    quoted — part of it was quoted (matters for a heredoc delimiter), subs — the text of its
    $(…)/`…` substitutions."""

    dynamic = False
    quoted = False
    subs: tuple = ()


def _word(text: str, dynamic: bool = False, quoted: bool = False) -> Word:
    w = Word(text)
    w.dynamic, w.quoted = dynamic, quoted
    return w


class Segment:
    def __init__(self) -> None:
        self.words: list[Word] = []
        self.redirects: list[tuple[str, Word]] = []
        self.heredoc: list[str] = []  # heredoc bodies and here-strings fed to this command


class _Parser:
    """A small recursive reader of POSIX shell: words, operators, redirections, heredocs,
    substitutions. Every simple command (including those inside $(…), `…` and unquoted
    heredocs) ends up in self.segments."""

    def __init__(self, text: str) -> None:
        self.s, self.n, self.i = text, len(text), 0
        self.segments: list[Segment] = []
        self.subs: list[str] = []

    def _end(self, seg: Segment) -> Segment:
        if seg.words or seg.redirects:
            self.segments.append(seg)
        return Segment()

    def parse(self, closer: str | None = None) -> None:
        seg, depth, heredocs = Segment(), 0, []
        while self.i < self.n:
            c = self.s[self.i]
            if c in " \t":
                self.i += 1
            elif self.s.startswith("\\\n", self.i):
                self.i += 2
            elif c == "\n":
                self.i += 1
                seg = self._end(seg)
                for doc in heredocs:
                    self._heredoc_body(*doc)
                heredocs = []
            elif c == "#":
                j = self.s.find("\n", self.i)
                self.i = self.n if j < 0 else j
            elif c == ")" and closer == ")" and depth == 0:
                self.i += 1
                break
            elif c in "<>" and self.s.startswith("(", self.i + 1):
                self.i += 2  # process substitution <(…) >(…)
                self.parse(")")
                seg.words.append(_word("<(…)", dynamic=True))
            elif m := REDIR_RE.match(self.s, self.i):
                self.i = m.end()
                op = m.group(2)
                while self.i < self.n and self.s[self.i] in " \t":
                    self.i += 1
                target = self._word() if self.i < self.n and self.s[self.i] not in "\n;&|()<>" else _word("")
                seg.redirects.append((op, target))
                if op in ("<<", "<<-"):
                    heredocs.append((str(target), target.quoted, seg))
                elif op == "<<<":
                    seg.heredoc.append(str(target))
            elif op := next((o for o in OPERATORS if self.s.startswith(o, self.i)), None):
                if op == "(":
                    depth += 1
                elif op == ")":
                    depth = max(0, depth - 1)
                self.i += len(op)
                seg = self._end(seg)
            else:
                seg.words.append(self._word())
        self._end(seg)

    def _heredoc_body(self, delim: str, quoted: bool, seg: Segment) -> None:
        body = []
        while self.i < self.n:
            j = self.s.find("\n", self.i)
            j = self.n if j < 0 else j
            line = self.s[self.i:j]
            self.i = min(j + 1, self.n)
            if line.strip() == delim:
                break
            body.append(line)
        seg.heredoc.append("\n".join(body))
        if not quoted:  # an unquoted heredoc expands $(…) and `…`
            sub = _Parser("\n".join(body))
            sub._dq(None)
            self.segments.extend(sub.segments)

    def _word(self) -> Word:
        buf: list[str] = []
        dynamic = quoted = False
        mark = len(self.subs)
        while self.i < self.n:
            c = self.s[self.i]
            if c in " \t\n;&|()<>":
                break
            if c == "\\":
                if self.i + 1 < self.n and self.s[self.i + 1] != "\n":
                    buf.append(self.s[self.i + 1])
                self.i += 2
                quoted = True
            elif c == "'":
                j = self.s.find("'", self.i + 1)
                j = self.n if j < 0 else j
                buf.append(self.s[self.i + 1:j])
                self.i = j + 1
                quoted = True
            elif c == '"':
                self.i += 1
                text, dyn = self._dq('"')
                buf.append(text)
                dynamic |= dyn
                quoted = True
            elif self.s.startswith("$'", self.i):
                j, part = self.i + 2, []
                while j < self.n and self.s[j] != "'":
                    if self.s[j] == "\\" and j + 1 < self.n:
                        j += 1
                    part.append(self.s[j])
                    j += 1
                buf.append("".join(part))
                self.i = j + 1
                quoted = True
            elif c == "$":
                buf.append(self._dollar())
                dynamic = True
            elif c == "`":
                self._backtick()
                buf.append("`…`")
                dynamic = True
            else:
                buf.append(c)
                self.i += 1
        w = _word("".join(buf), dynamic, quoted)
        w.subs = tuple(self.subs[mark:])
        return w

    def _dq(self, end: str | None) -> tuple[str, bool]:
        """Double-quoted text (end='"') or an unquoted heredoc body (end=None)."""
        buf: list[str] = []
        dynamic = False
        while self.i < self.n:
            c = self.s[self.i]
            if end and c == end:
                self.i += 1
                break
            if c == "\\" and self.i + 1 < self.n:
                nxt = self.s[self.i + 1]
                if nxt != "\n":
                    buf.append(nxt if nxt in '$`"\\' else c + nxt)
                self.i += 2
            elif c == "$":
                buf.append(self._dollar())
                dynamic = True
            elif c == "`":
                self._backtick()
                buf.append("`…`")
                dynamic = True
            else:
                buf.append(c)
                self.i += 1
        return "".join(buf), dynamic

    def _skip_balanced(self, start: int, open_: str, close: str) -> int:
        depth, j = 0, start
        while j < self.n:
            if self.s[j] == open_:
                depth += 1
            elif self.s[j] == close:
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        return self.n

    def _dollar(self) -> str:
        start = self.i
        if self.s.startswith("$((", self.i):
            self.i = self._skip_balanced(self.i + 1, "(", ")")
        elif self.s.startswith("$(", self.i):
            self.i += 2
            self.parse(")")
            self.subs.append(self.s[start + 2:max(start + 2, self.i - 1)])
            return "$(…)"
        elif self.s.startswith("${", self.i):
            self.i = self._skip_balanced(self.i + 1, "{", "}")
        else:
            m = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|[0-9#?@*!$-])?").match(self.s, self.i)
            self.i = m.end()
        return self.s[start:self.i]

    def _backtick(self) -> None:
        j = self.i + 1
        while j < self.n and self.s[j] != "`":
            j += 2 if self.s[j] == "\\" else 1
        inner = self.s[self.i + 1:j].replace("\\`", "`")
        self.i = j + 1
        self.subs.append(inner)
        sub = _Parser(inner)
        sub.parse()
        self.segments.extend(sub.segments)


class Cmd:
    """A simple command: words from the command name on, its redirections, env -C dir, the
    VAR=… assignments before it, the heredoc text fed to it."""

    def __init__(self, words: list[Word], redirects: list, chdir: str | None,
                 assigns: list[Word] | None = None, heredoc: str = "") -> None:
        self.words, self.redirects, self.chdir = words, redirects, chdir
        self.assigns, self.heredoc = assigns or [], heredoc

    @property
    def name(self) -> str:
        return self.words[0].rsplit("/", 1)[-1] if self.words else ""


def _command_start(words: list[Word]) -> tuple[list[Word], str | None]:
    """Skip keywords, VAR=…, env and wrappers — to the command that actually runs."""
    rest, chdir, _ = _start(words)
    return rest, chdir


def _start(words: list[Word]) -> tuple[list[Word], str | None, list[Word]]:
    i, chdir, assigns = 0, None, []
    words = list(words)
    while i < len(words):
        w = words[i]
        base = w.rsplit("/", 1)[-1]
        if w in KEYWORDS or ASSIGN_RE.match(w):
            if ASSIGN_RE.match(w):
                assigns.append(w)
            i += 1
        elif base == "env":
            i += 1
            while i < len(words):
                a = words[i]
                if a in ("-C", "--chdir") and i + 1 < len(words):
                    chdir = words[i + 1]
                    i += 2
                elif a.startswith("--chdir="):
                    chdir = a.split("=", 1)[1]
                    i += 1
                elif a in ("-S", "--split-string") and i + 1 < len(words):
                    words[i:i + 2] = [_word(x) for x in words[i + 1].split()]
                elif a.startswith("--split-string="):
                    words[i:i + 1] = [_word(x) for x in a.split("=", 1)[1].split()]
                elif a in ("-u", "--unset"):
                    i += 2
                elif a.startswith("-") or ASSIGN_RE.match(a):
                    if ASSIGN_RE.match(a):
                        assigns.append(a)
                    i += 1
                else:
                    break
        elif base in WRAPPERS:
            i += 1
            positional = WRAPPER_POSITIONAL.get(base, 0)
            while i < len(words):
                a = words[i]
                if a == "--":
                    i += 1
                    break
                if a in WRAPPERS[base]:
                    i += 2
                elif a.startswith("-") and len(a) > 1:
                    i += 1
                elif positional:
                    positional -= 1
                    i += 1
                else:
                    break
        else:
            break
    return words[i:], chdir, assigns


def _nested(words: list[Word]) -> str | None:
    """The command string of `bash -c '…'` / `eval …`."""
    name = words[0].rsplit("/", 1)[-1] if words else ""
    if name == "eval":
        return " ".join(words[1:])
    if name in SHELLS:
        for k, w in enumerate(words[1:], start=1):
            if w.startswith("-") and not w.startswith("--") and "c" in w[1:]:
                rest = [x for x in words[k + 1:] if not x.startswith("-")]
                return rest[0] if rest else None
            if not w.startswith("-"):
                return None
    return None


def _stdin_shell(cmd: Cmd) -> bool:
    """A shell that reads its commands from stdin: `bash`, `sh -s`, `bash <<EOF`."""
    return cmd.name in SHELLS and _nested(cmd.words) is None and all(w.startswith("-") for w in cmd.words[1:])


def commands(command: str, _depth: int = 0) -> list[Cmd]:
    """Every simple command the line would run, in reading order."""
    parser = _Parser(command)
    parser.parse()
    out = []
    for seg in parser.segments:
        words, chdir, assigns = _start(seg.words)
        cmd = Cmd(words, seg.redirects, chdir, assigns, "\n".join(seg.heredoc))
        out.append(cmd)
        if _depth >= 5:
            continue
        inner = _nested(words)
        if inner is None and cmd.heredoc and _stdin_shell(cmd):
            inner = cmd.heredoc  # a heredoc fed to a shell is commands, not data
        if inner is not None:
            out.extend(commands(inner, _depth + 1))
    return out


def git_split(words: list[Word]) -> tuple[str, list[Word], list[str], list[str]]:
    """`git [global options] <sub> args…` → (sub, args, -C dirs, -c configs)."""
    i, dirs, configs = 1, [], []
    while i < len(words):
        w = words[i]
        if w in GIT_OPTS_WITH_VALUE:
            value = words[i + 1] if i + 1 < len(words) else ""
            if w == "-C":
                dirs.append(value)
            elif w in ("-c", "--config-env"):
                configs.append(value)
            i += 2
        elif w.startswith("-"):
            if w.startswith("--config-env="):
                configs.append(w.split("=", 1)[1])
            i += 1
        else:
            return w, words[i + 1:], dirs, configs
    return "", [], dirs, configs


def _short_flags(word: str, with_value: str = "") -> tuple[str, bool]:
    """Letters of a short-option cluster (-uf → "uf"); stops at a letter that takes a value.
    Returns (letters, whether the next word is that value)."""
    letters = word[1:]
    for k, ch in enumerate(letters):
        if ch in with_value:
            return letters[:k + 1], k == len(letters) - 1
    return letters, False


def _is_short(word: str) -> bool:
    return word.startswith("-") and not word.startswith("--") and len(word) > 1


def _long(word: str, options: tuple[str, ...]) -> bool:
    """word (a --long option, `=value` dropped) names one of options — exactly, or by a prefix git would
    expand (git takes any unambiguous prefix; an ambiguous one is counted too)."""
    name = word.split("=", 1)[0]
    return len(name) > 2 and name.startswith("--") and any(o.startswith(name) for o in options)


# --- the repository ----------------------------------------------------------


def _git(args: list[str], cwd: str) -> str | None:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


class GitRepo:
    """Answers about the real repository (tests use a fake with the same methods)."""

    def __init__(self, root: str) -> None:
        self.root = root
        self._main: str | None = None
        self._protected: set[str] | None = None

    def main_branch(self) -> str:
        if self._main is None:
            self._main = main_branch(self.root)
        return self._main

    def protected_branches(self) -> set[str]:
        if self._protected is None:
            self._protected = protected_branches(self.root, self.main_branch())
        return self._protected

    def current_branch(self, cwd: str) -> str | None:
        return _git(["symbolic-ref", "-q", "--short", "HEAD"], cwd) if os.path.isdir(cwd) else None

    def tag_exists(self, name: str, cwd: str) -> bool:
        return os.path.isdir(cwd) and _git(["show-ref", "--verify", "--quiet", f"refs/tags/{name}"], cwd) is not None

    def alias(self, name: str, cwd: str) -> str | None:
        return _git(["config", "--get", f"alias.{name}"], cwd) if os.path.isdir(cwd) else None

    def untracked(self, rels: list[str], cwd: str) -> dict[str, str]:
        """{path: '??' untracked | '!!' ignored} for paths relative to the top level; tracked ones are absent."""
        out = _git(["status", "--porcelain=v1", "-z", "--ignored=matching", "--untracked-files=all", "--", *rels],
                   cwd) if os.path.isdir(cwd) else None
        if out is None:
            return {rel: "??" for rel in rels}  # cannot tell: as if untracked
        entries = [(e[:2], e[3:]) for e in out.split("\0") if len(e) > 3]
        found = {}
        for rel in rels:
            for code, path in entries:
                if path == rel or (path.endswith("/") and rel.startswith(path)):
                    found[rel] = code
        return found


def _script_value(root: str, rel: str, key: str) -> str | None:
    try:
        text = (Path(root) / rel).read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(rf"""^{key}=(['"]?)([^'"\s]+)\1""", text, re.M)
    return m.group(2) if m and not re.search(r"[<$]", m.group(2)) else None


def main_branch(root: str) -> str:
    """origin/HEAD, else MAIN_BRANCH from scripts/merge-feature.sh, else main. The script is a project
    file the agent may edit at solo: it only ever adds a name to the protected set, never removes one."""
    head = _git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], root) if os.path.isdir(root) else None
    if head and "/" in head:
        return head.split("/", 1)[1]
    return _script_value(root, "scripts/merge-feature.sh", "MAIN_BRANCH") or "main"


def protected_branches(root: str, main: str | None = None) -> set[str]:
    """main, master, origin's HEAD branch, MAIN_BRANCH of merge-feature.sh, the production branch."""
    names = {*PROTECTED_BRANCHES, RELEASE_BRANCH, main or main_branch(root)}
    for rel, key in (("scripts/merge-feature.sh", "MAIN_BRANCH"), ("scripts/release.sh", "RELEASE_BRANCH")):
        if value := _script_value(root, rel, key):
            names.add(value)
    return names


def parse_fences(text: str) -> dict[str, str]:
    """KEY=value lines of a fences.env text (# comments, optional quotes); a later line wins."""
    values = {}
    for line in text.splitlines():
        m = re.match(r"""\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(['"]?)([^'"#\s]*)\2""", line)
        if m:
            values[m.group(1)] = m.group(3)
    return values


def read_fences(root: str) -> dict[str, str]:
    """KEY=value lines of .claude/fences.env."""
    try:
        return parse_fences((Path(root) / FENCES_FILE).read_text(encoding="utf-8"))
    except OSError:
        return {}


def _strict(level: str | None) -> str:
    return level if level in LEVEL_RANK else DEFAULT_LEVEL


def fences_floor(root: str) -> dict[str, str]:
    """.claude/fences.env as origin's main branch has it (origin/HEAD, origin/main, origin/master and the
    configured main branch; the strictest wins). {} when none has the file or there is no repository.
    One `git cat-file --batch` (a few ms): the working tree is the agent's, the main branch is not."""
    if not os.path.isdir(root):
        return {}
    names = dict.fromkeys(["HEAD", "main", "master", _script_value(root, "scripts/merge-feature.sh", "MAIN_BRANCH")])
    revs = "".join(f"refs/remotes/origin/{n}:{FENCES_FILE}\n" for n in names if n)
    try:
        proc = subprocess.run(["git", "cat-file", "--batch"], cwd=root, input=revs.encode(), capture_output=True,
                              timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    out, i, found = proc.stdout, 0, []
    while i < len(out):
        nl = out.find(b"\n", i)
        if nl < 0:
            break
        header = out[i:nl].split()
        i = nl + 1
        if len(header) == 3 and header[1] == b"blob" and header[2].isdigit():
            size = int(header[2])
            found.append(parse_fences(out[i:i + size].decode("utf-8", "replace")))
            i += size + 1
    if not found:
        return {}
    floor = dict(found[0])
    floor["FENCE_LEVEL"] = max((_strict(f.get("FENCE_LEVEL")) for f in found), key=LEVEL_RANK.__getitem__)
    if any(f.get("QUOTA_FAIL_CLOSED") == "1" for f in found):
        floor["QUOTA_FAIL_CLOSED"] = "1"
    return floor


def effective_fences(root: str, floor: dict[str, str] | None = None) -> dict[str, str]:
    """The fences as they bind: the working tree's .claude/fences.env, never below the main branch's
    (FENCE_LEVEL is the stricter of the two, QUOTA_FAIL_CLOSED is 1 if either says 1). Lowering a level
    takes a change merged into the main branch. {} when neither has the file."""
    floor = fences_floor(root) if floor is None else floor
    has_file = (Path(root) / FENCES_FILE).is_file()
    if not has_file and not floor:
        return {}
    values = read_fences(root)
    out = dict(values)
    levels = [_strict(values.get("FENCE_LEVEL") or DEFAULT_LEVEL)] if has_file else []
    if floor:
        levels.append(_strict(floor.get("FENCE_LEVEL")))
    out["FENCE_LEVEL"] = max(levels, key=LEVEL_RANK.__getitem__)
    if values.get("QUOTA_FAIL_CLOSED") == "1" or floor.get("QUOTA_FAIL_CLOSED") == "1":
        out["QUOTA_FAIL_CLOSED"] = "1"
    return out


def fence_level(root: str, floor: dict[str, str] | None = None) -> str:
    level = read_fences(root).get("FENCE_LEVEL", "") or DEFAULT_LEVEL
    if level not in LEVELS:
        print(f"guard: unknown FENCE_LEVEL={level!r} in {FENCES_FILE} — using '{DEFAULT_LEVEL}'", file=sys.stderr)
    return effective_fences(root, floor).get("FENCE_LEVEL") or DEFAULT_LEVEL


def project_root(cwd: str) -> str:
    """The project: $CLAUDE_PROJECT_DIR (set for every hook), else the git top level of cwd."""
    return os.environ.get("CLAUDE_PROJECT_DIR") or _git(["rev-parse", "--show-toplevel"], cwd) or cwd


def opted_in(root: str, floor: dict[str, str] | None = None) -> bool:
    """The project uses the fences: .claude/fences.env in the working tree (base and /dark-factory:init create
    it) or on origin's main branch (deleting the working copy does not opt out)."""
    return (Path(root) / FENCES_FILE).is_file() or bool(fences_floor(root) if floor is None else floor)


# --- decisions ---------------------------------------------------------------


def _join(base: str, path: str) -> str:
    return os.path.normpath(os.path.join(base, os.path.expanduser(path)))


def is_fence_path(path: str, cwd: str) -> bool:
    return bool(path) and bool(FENCE_FILE_RE.search(_join(cwd, path).replace(os.sep, "/")))


def _release_tag(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in RELEASE_TAGS)


def _protected(repo) -> set[str]:
    fn = getattr(repo, "protected_branches", None)
    return set(fn()) if fn else {*PROTECTED_BRANCHES, RELEASE_BRANCH, repo.main_branch()}


def _branch_reason(name: str | None, repo) -> tuple | None:
    if not name:
        return None
    if name == repo.main_branch():
        return MAIN
    if name in _protected(repo):
        return _fill(PROTECTED, branch=name)
    return None


PUSH_FORCE = ("--force", "--force-with-lease", "--force-if-includes", "--mirror")
PUSH_ALL = ("--all", "--branches")
PUSH_TAGS = ("--tags", "--follow-tags")
PUSH_VALUE = ("--push-option", "--repo", "--receive-pack", "--exec")
PUSH_DANGER = (*PUSH_FORCE, *PUSH_ALL, *PUSH_TAGS, "--no-verify", "--delete")


def check_push(args: list[Word], cwd: str, repo) -> tuple | None:
    force = no_verify = all_refs = all_tags = delete = False
    pos: list[Word] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            pos.extend(args[i + 1:])
            break
        if a == "-o":
            i += 2
            continue
        if a.startswith("--"):
            force |= _long(a, PUSH_FORCE)
            no_verify |= _long(a, ("--no-verify",))
            all_refs |= _long(a, PUSH_ALL)
            all_tags |= _long(a, PUSH_TAGS)
            delete |= _long(a, ("--delete",))
            if "=" not in a and _long(a, PUSH_VALUE) and not _long(a, PUSH_DANGER):
                i += 1
        elif _is_short(a):
            letters, takes_next = _short_flags(a, "o")
            force |= "f" in letters
            delete |= "d" in letters
            i += takes_next
        else:
            pos.append(a)
        i += 1
    refspecs = pos[1:]
    if force or any(r.startswith("+") for r in refspecs):
        return FORCE
    if no_verify:
        return NO_VERIFY
    if all_tags:
        return TAG
    if all_refs:
        return MAIN
    if not refspecs:
        return None if delete else _branch_reason(repo.current_branch(cwd), repo)
    j = 0
    while j < len(refspecs):
        ref = refspecs[j]
        if ref == "tag" and j + 1 < len(refspecs):
            nxt = refspecs[j + 1]
            kind, name = ("dynamic", nxt) if ("$" in nxt or "`" in nxt) else ("tag", str(nxt))
            j += 2
        else:
            kind, name = _classify_refspec(ref, delete, cwd, repo)
            j += 1
        if kind == "dynamic":
            return DYNAMIC_PUSH
        if kind == "glob":
            return TAG if name.startswith(("refs/tags/", "tags/")) else MAIN
        if kind == "branch" and (reason := _branch_reason(name, repo)):
            return reason
        if kind == "tag" and _release_tag(name):
            return TAG
    return None


def _current_idiom(ref: Word, dst: str) -> bool:
    """dst is exactly `$(git branch --show-current)` (or a like idiom): the current branch."""
    return dst in ("$(…)", "`…`") and len(ref.subs) == 1 and " ".join(ref.subs[0].split()) in CURRENT_BRANCH_SUBS


def _classify_refspec(ref: Word, delete: bool, cwd: str, repo) -> tuple[str, str | None]:
    src, sep, dst = ref.lstrip("+").partition(":")
    if not sep or not dst:
        dst = src
    if _current_idiom(ref, dst):
        return "branch", repo.current_branch(cwd)
    if "$" in dst or "`" in dst:  # computed at run time: cannot be told here
        return "dynamic", dst
    if "*" in dst:
        return "glob", dst
    if dst in ("HEAD", "@"):
        return "branch", repo.current_branch(cwd)
    # git qualifies a destination as refs/<dst> when that names a ref: heads/main is refs/heads/main
    short = dst[len("refs/"):] if dst.startswith("refs/") else dst
    if short.startswith("heads/"):
        return "branch", short[len("heads/"):]
    if short.startswith("tags/"):
        return "tag", short[len("tags/"):]
    if dst.startswith("refs/"):
        return "other", dst
    if sep and src in ("HEAD", "@"):
        return "branch", dst
    lookup = src if (sep and src) else dst
    if repo.tag_exists(lookup, cwd) or (RELEASE_TAG_SHAPE.fullmatch(dst) and (delete or not sep or not src)):
        return "tag", dst
    return "branch", dst


def check_commit(args: list[Word]) -> tuple | None:
    value_long = ("--message", "--file", "--author", "--date", "--template", "--reuse-message",
                  "--reedit-message", "--fixup", "--squash", "--trailer", "--cleanup", "--pathspec-from-file")
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            break
        if _long(a, ("--no-verify",)):
            return NO_VERIFY
        if a.startswith("--"):
            if "=" not in a and _long(a, value_long):
                i += 1
        elif _is_short(a):
            letters, takes_next = _short_flags(a, "mFCctuS")
            if "n" in letters:
                return NO_VERIFY
            i += takes_next and letters[-1] in "mFCct"
        i += 1
    return None


def _config_reason(key: str, value: str | None, unset: bool = False) -> tuple | None:
    """A git config key being set (value) or unset: NO_VERIFY for the hooks path, GIT_CONFIG for the rest."""
    key = key.lower()
    if key == "core.hookspath":
        return NO_VERIFY if unset or value != ".githooks" else None
    return GIT_CONFIG if DANGEROUS_CONFIG_RE.search(key) else None


def check_git_config(args: list[Word]) -> tuple | None:
    reading = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "-l", "--list", "get", "list"}
    unsetting = {"--unset", "--unset-all", "unset"}
    sections = {"--rename-section", "--remove-section", "rename-section", "remove-section", "-e", "--edit", "edit"}
    with_value = {"--file", "-f", "--blob", "--type", "-t", "--default", "--comment", "--value"}
    if reading & set(args):
        return None
    if sections & set(args):
        return GIT_CONFIG
    pos, i = [], 0
    while i < len(args):
        a = args[i]
        if a in with_value:
            i += 2
            continue
        if not a.startswith("-") and not (not pos and a in ("set", "unset")):
            pos.append(a)
        i += 1
    if not pos:
        return None
    unset = bool(unsetting & set(args))
    if len(pos) < 2 and not unset:
        return None  # `git config key` reads it
    return _config_reason(pos[0], pos[1] if len(pos) > 1 else None, unset)


def check_git(words: list[Word], cwd: str, repo) -> tuple | None:
    sub, args, dirs, configs = git_split(words)
    for d in dirs:
        cwd = _join(cwd, d)
    for c in configs:
        key, _, value = c.partition("=")
        if reason := _config_reason(key, value):
            return reason
    if sub == "push":
        return check_push(args, cwd, repo)
    if sub == "commit":
        return check_commit(args)
    if sub == "branch":
        deleting = any(_long(a, ("--delete",)) or (_is_short(a) and set(a[1:]) & {"d", "D"}) for a in args)
        names = [a.removeprefix("refs/heads/") for a in args if not a.startswith("-")]
        if deleting and repo.main_branch() in names:
            return MAIN_BRANCH_DELETE
    if sub == "reset" and "--hard" in args and repo.current_branch(cwd) == repo.main_branch():
        return RESET_MAIN
    if sub == "config":
        return check_git_config(args)
    if sub in ("update-ref", "symbolic-ref") and any(a.startswith("refs/remotes/") for a in args):
        return REMOTE_REFS
    if sub == "remote" and args[:1] and args[0] in ("rename", "remove", "rm", "set-head"):
        return REMOTE_REFS
    if sub == "fetch" and any(":" in a and "remotes/" in a.split(":", 1)[1] for a in args if not a.startswith("-")):
        return REMOTE_REFS
    return None


def _gh_api(args: list[Word]) -> tuple[tuple | None, bool]:
    """`gh api …` → (reason, whether it is denied only at standard and team)."""
    method, fields, opaque, endpoint = None, [], False, None
    i = 0
    while i < len(args):
        a = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if a in ("-X", "--method"):
            method, i = nxt, i + 2
            continue
        if a in ("-f", "-F", "--field", "--raw-field"):
            fields.append(nxt)
            i += 2
            continue
        if a == "--input":
            opaque, i = True, i + 2
            continue
        if a in ("-H", "--header", "-q", "--jq", "-t", "--template", "--hostname", "-p", "--preview", "--cache"):
            i += 2
            continue
        if a.startswith("--method="):
            method = a.split("=", 1)[1]
        elif a.startswith("-X") and len(a) > 2:
            method = a[2:]
        elif a.startswith(("--field=", "--raw-field=")):
            fields.append(a.split("=", 1)[1])
        elif a.startswith(("-f", "-F")) and len(a) > 2:
            fields.append(a[2:])
        elif a.startswith("--input="):
            opaque = True
        elif not a.startswith("-") and endpoint is None:
            endpoint = a
        i += 1
    text = " ".join([*args, *(s for a in args for s in a.subs)])
    files = any(f.partition("=")[2].startswith("@") for f in fields)
    method = (method or ("POST" if fields or opaque else "GET")).upper()
    if endpoint is None:
        return None, False
    if endpoint.strip("/") == "graphql":
        if opaque or files:
            return GH_OPAQUE, False
        if GH_PROTECTION_API_RE.search(text):
            return GH_PROTECTION, False
        if GH_REFS_API_RE.search(text):
            return GH_REFS, False
        if GH_LABEL_GRAPHQL_RE.search(text) or (GH_COMMENT_GRAPHQL_RE.search(text) and ACCEPTED_BY_RE.search(text)):
            return ACCEPT_MARK, False
        if GH_MERGE_API_RE.search(text):
            return MERGE, True
        if GH_REVIEW_API_RE.search(text) and "APPROVE" in text:
            return APPROVE, True
        return None, False
    if method == "GET":
        return None, False
    path = "/" + endpoint.split("?", 1)[0].strip("/")
    if "$" in path or "`" in path:
        return GH_OPAQUE, False
    if re.search(r"/branches/[^/]+/protection|/rulesets\b|/rules/", path):
        return GH_PROTECTION, False
    if re.search(r"/git/refs\b|/merges$|/contents/|/branches/[^/]+/rename$", path):
        return GH_REFS, False
    if re.search(r"/releases\b", path):
        return GH_RELEASE, False
    unreadable = opaque or files
    if GH_LABELS_API_RE.search(path) or re.search(r"/issues/[^/]+$", path):
        if unreadable or ACCEPTED_RE.search(text) or (GH_LABELS_API_RE.search(path) and method != "DELETE"
                                                      and "/issues/" in path):
            return ACCEPT_MARK, False
    if GH_COMMENTS_API_RE.search(path) and (unreadable or ACCEPTED_BY_RE.search(text)):
        return ACCEPT_MARK, False
    if re.search(r"/pulls/[^/]+/merge$", path):
        return MERGE, True
    if re.search(r"/pulls/[^/]+/reviews\b", path) and ("APPROVE" in text or opaque or files):
        return APPROVE, True
    return None, False


ACCEPTED_RE = re.compile(r"accepted", re.I)
ACCEPTED_BY_RE = re.compile(r"accepted\s+by", re.I)
GH_LABELS_API_RE = re.compile(r"/labels\b")
GH_COMMENTS_API_RE = re.compile(r"/comments(?:/\d+)?$")
GH_LABEL_GRAPHQL_RE = re.compile(r"addLabelsToLabelable|removeLabelsFromLabelable|createLabel|updateLabel|deleteLabel", re.I)
GH_COMMENT_GRAPHQL_RE = re.compile(r"addComment|updateIssueComment", re.I)


def _flag_values(args: list[Word], longs: tuple[str, ...], shorts: str = "") -> list[Word]:
    """The values of `--flag v`, `--flag=v` and `-f v` among args."""
    out = []
    for k, a in enumerate(args):
        nxt = args[k + 1] if k + 1 < len(args) else _word("")
        for flag in longs:
            if a == flag:
                out.append(nxt)
            elif a.startswith(flag + "="):
                out.append(_word(a.split("=", 1)[1], a.dynamic))
        if shorts and _is_short(a) and a[1:2] in shorts:
            if len(a) == 2:
                out.append(nxt)
            else:  # `-b"text"`, `-btext`, `-b=text`: the value is attached
                v = _word(a[2:].removeprefix("="), a.dynamic)
                v.subs = a.subs
                out.append(v)
    return out


def _text(word: str) -> str:
    return " ".join([str(word), *getattr(word, "subs", ())])


def check_gh_acceptance(pos: list[str], words: list[Word]) -> tuple | None:
    """Acceptance by hand: `gh pr|issue edit --add-label accepted`, `gh label …`, a comment "accepted by …"."""
    if pos[:1] == ["label"] and pos[1:2] in (["create"], ["edit"], ["delete"], ["clone"]):
        return ACCEPT_MARK
    if pos[:1] in (["pr"], ["issue"]) and pos[1:2] == ["edit"]:
        for value in _flag_values(words, ("--add-label", "--remove-label")):
            if value.dynamic or ACCEPTED_RE.search(_text(value)):
                return ACCEPT_MARK
    if pos[:1] in (["pr"], ["issue"]) and pos[1:2] == ["comment"]:
        if _flag_values(words, ("--body-file", "--editor"), "Fe"):
            return ACCEPT_MARK  # a body the guard cannot read
        for value in _flag_values(words, ("--body",), "b"):
            if value.dynamic or ACCEPTED_BY_RE.search(_text(value)):
                return ACCEPT_MARK
    return None


def check_gh(words: list[Word], strict: bool = True) -> tuple | None:
    pos, i = [], 1
    while i < len(words):
        w = words[i]
        if w in ("-R", "--repo"):
            i += 2
            continue
        if not w.startswith("-"):
            pos.append(w)
        i += 1
    if pos[:1] == ["alias"] and pos[1:2] in (["set"], ["import"]):
        return GH_ALIAS
    if mark := check_gh_acceptance(pos, words):
        return mark
    if pos[:1] == ["release"] and pos[1:2] in (["create"], ["edit"], ["delete"]):
        return GH_RELEASE
    if pos[:1] == ["api"]:
        k = words.index("api")
        reason, strict_only = _gh_api(words[k + 1:])
        if reason and (strict or not strict_only):
            return reason
        return None
    if not strict:
        return None
    if pos[:2] == ["pr", "merge"]:
        return MERGE
    if pos[:2] == ["pr", "review"] and any(
        w == "--approve" or (_is_short(w) and "a" in w[1:]) for w in words[3:]
    ):
        return APPROVE
    return None


def _script_args(cmd: Cmd, script: str) -> list[Word] | None:
    """The arguments of `<…/>script`, run directly or by a shell, `source` or `.`; None if cmd is not that."""
    words = cmd.words
    if not words:
        return None
    if words[0].rsplit("/", 1)[-1] == script:
        return words[1:]
    if cmd.name in SHELLS or cmd.name in ("source", "."):
        for k, w in enumerate(words[1:], start=1):
            if not w.startswith("-"):
                return words[k + 1:] if w.rsplit("/", 1)[-1] == script else None
    return None


def check_release(cmd: Cmd) -> tuple | None:
    """A run of scripts/release.sh that tags, or of the On-call wrapper's accept/release, is a decision of a person."""
    args = _script_args(cmd, "release.sh")
    if args is not None:
        publish = len(args) == 2 and args[0] == "--publish" and RELEASE_TAG_SHAPE.fullmatch(args[1])
        if publish or args == ["--list-unaccepted"]:
            return None  # CI moves `release` to a tag that already passed the gate; a listing changes nothing
        if "--manual" in args:
            return RELEASE_MANUAL
        if "--owner-accepts" in args:
            return RELEASE_OWNER
        if "--include-unaccepted" in args:
            return RELEASE_INCLUDE
        if "--security" in args:
            return RELEASE_SECURITY
        return RELEASE
    args = _script_args(cmd, "feedback-desk.sh")
    if args and args[0] == "accept":
        return ACCEPT_WRAPPER
    if args and args[0] == "release":
        return RELEASE_INCLUDE if "--include-unaccepted" in args else RELEASE
    return None


def check_http(cmd: Cmd) -> tuple | None:
    """curl/wget/httpie against the GitHub API with a non-GET method, or carrying the gh token anywhere."""
    words = cmd.words
    subs = [" ".join(s.split()) for w in words for s in w.subs]
    text = " ".join([*words, *subs])
    if GITHUB_TOKEN_RE.search(text) or any("gh auth token" in s for s in subs):
        return HTTP_API
    if not GITHUB_API_RE.search(text):
        return None
    writes = False
    for k, w in enumerate(words[1:], start=1):
        nxt = words[k + 1] if k + 1 < len(words) else ""
        if cmd.name == "curl":
            if w in ("-X", "--request"):
                writes |= nxt.upper() != "GET"
            elif (w.startswith("-X") and len(w) > 2) or w.startswith("--request="):
                writes |= w.split("=", 1)[-1].removeprefix("-X").upper() != "GET"
            elif w in ("-d", "-F", "-T", "--json", "--form", "--upload-file") or w.startswith(("--data", "--form", "--json")) \
                    or (w.startswith(("-d", "-F", "-T")) and len(w) > 2):
                writes = True
        elif cmd.name == "wget":
            writes |= w.startswith(("--method", "--post-", "--body-")) and "GET" not in w.upper()
        else:  # httpie, xh: `http PUT url field=value`
            writes |= w.upper() in HTTP_METHODS or (not w.startswith("-") and re.match(r"^[\w.\-\[\]]+:?=", w) is not None)
    return HTTP_API if writes else None


WRITE_ANY_ARG = {"mv", "rm", "rmdir", "tee", "truncate", "touch", "chmod", "chown", "unlink", "shred", "patch"}
WRITE_LAST_ARG = {"cp", "install", "ln", "rsync"}
WRITE_IN_PLACE = {"sed", "perl", "awk", "gawk"}
# These take the whole tree with them: removing or moving an ancestor of a fenced path counts.
REMOVE_ARG = {"mv", "rm", "rmdir", "unlink", "shred"}
# git subcommands that change files in the working tree they run in.
GIT_WRITE_SUBS = {"checkout", "switch", "reset", "apply", "am", "pull", "merge", "rebase", "restore", "stash",
                  "commit", "rm", "mv", "clean", "cherry-pick", "revert"}


def _in_place(cmd: Cmd) -> bool:
    words = cmd.words[1:]
    if cmd.name == "sed":
        return any(a.startswith("-i") or a.startswith("--in-place") or (_is_short(a) and "i" in a) for a in words)
    if cmd.name == "perl":
        return any(_is_short(a) and "i" in a for a in words)
    if cmd.name in ("awk", "gawk"):
        return any(a in ("-iinplace", "--include=inplace") or (a in ("-i", "--include") and k + 1 < len(words)
                   and words[k + 1] == "inplace") for k, a in enumerate(words))
    return False


def written_paths(cmd: Cmd) -> list[tuple[str, bool]]:
    """The paths a simple command writes: (path, whether it removes or moves the whole path)."""
    out: list[tuple[str, bool]] = []
    for op, target in cmd.redirects:
        if ">" in op and not (op == ">&" and (target.isdigit() or target == "-")):
            out.append((target, False))
    name = cmd.name
    target_dir = None
    if name in ("cp", "mv", "install", "ln"):
        for k, w in enumerate(cmd.words[1:], start=1):
            if w in ("-t", "--target-directory") and k + 1 < len(cmd.words):
                target_dir = cmd.words[k + 1]
            elif w.startswith("--target-directory="):
                target_dir = w.split("=", 1)[1]
    args = [a for a in cmd.words[1:] if not a.startswith("-")]
    if name in WRITE_ANY_ARG or (name in WRITE_IN_PLACE and _in_place(cmd)):
        last = len(args) - 1 if name == "mv" and len(args) >= 2 and target_dir is None else None
        out.extend((a, name in REMOVE_ARG and k != last and a != target_dir) for k, a in enumerate(args))
    if name in WRITE_LAST_ARG and (len(args) >= 2 or target_dir):
        dest = target_dir or args[-1]
        sources = [a for a in args if a != dest] if target_dir else args[:-1]
        out.append((dest, False))
        out.extend((os.path.join(dest, os.path.basename(a)), False) for a in sources)
    if name == "mv" and target_dir:
        out.extend((os.path.join(target_dir, os.path.basename(a)), False) for a in args if a != target_dir)
    if name == "dd":
        out.extend((a[3:], False) for a in cmd.words[1:] if a.startswith("of="))
    return out


def check_fence_write(cmd: Cmd, cwd: str) -> tuple | None:
    """A Bash write to a fence file (standard and team, asked): an output redirection, mv/rm/tee/cp…, sed -i."""
    if any(path and is_fence_path(path, cwd) for path, _ in written_paths(cmd)):
        return FENCE_EDIT
    return None


# --- the fences themselves and the installed plugin: at every level ---------------


def _homes() -> list[str]:
    """The home directory as $HOME says and as the password database says (HOME can be re-pointed)."""
    homes = {os.path.expanduser("~")}
    try:
        homes.add(pwd.getpwuid(os.getuid()).pw_dir)
    except (KeyError, OSError):
        pass
    return sorted(h for h in homes if h and h != "/")


def _expand_home(path: str) -> str:
    for var in ("${HOME}", "$HOME"):
        if path == var or path.startswith(var + "/"):
            return os.path.expanduser("~") + path[len(var):]
    return os.path.expanduser(path)


def _real(path: str) -> str:
    return os.path.realpath(path)


def _inside(path: str, base: str) -> bool:
    """path is base or below it (both real, absolute)."""
    return path == base or path.startswith(base.rstrip("/") + "/")


def _braces(word: str, limit: int = 64) -> list[str]:
    """Brace expansion as the shell does it before running the command: a{b,c} → ab ac."""
    m = re.search(r"\{([^{}]*,[^{}]*)\}", word)
    if not m:
        return [word]
    out: list[str] = []
    for part in m.group(1).split(","):
        out.extend(_braces(word[:m.start()] + part + word[m.end():], limit))
        if len(out) >= limit:
            break
    return out[:limit]


def protected_dirs(root: str) -> list[str]:
    """Where the installed plugin and its state live: ~/.claude/plugins (and $CLAUDE_CONFIG_DIR/plugins),
    $CLAUDE_PLUGIN_ROOT, ~/.cache/dark-factory. A plugin root inside the project is a plugin under
    development (claude --plugin-dir): its files are the project's own and are not fenced here."""
    dirs = []
    for home in _homes():
        dirs += [os.path.join(home, ".claude", "plugins"), os.path.join(home, STATE_DIR)]
    if config := os.environ.get("CLAUDE_CONFIG_DIR"):
        dirs.append(os.path.join(os.path.expanduser(config), "plugins"))
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root and not _inside(_real(plugin_root), _real(root)):
        dirs.append(plugin_root)
    return sorted({_real(d) for d in dirs})


def self_paths(root: str) -> list[str]:
    """The fence files a removal must not cover (real, absolute): the project's fences.env, settings,
    .githooks/, .git/config and .git/hooks/, the user's Claude Code settings."""
    r = _real(root)
    # .git/ first: a removal that covers both reports the plain deny (see _covers)
    paths = [os.path.join(r, rel) for rel in (".git/config", ".git/hooks", FENCES_FILE, ".claude/settings.json",
                                               ".claude/settings.local.json", ".githooks")]
    for home in _homes():
        paths += [os.path.join(_real(home), ".claude", name) for name in SETTINGS_NAMES]
    if config := os.environ.get("CLAUDE_CONFIG_DIR"):
        paths += [os.path.join(_real(os.path.expanduser(config)), name) for name in SETTINGS_NAMES]
    return paths


def _is_settings(full: str) -> bool:
    if SETTINGS_RE.search(full.replace(os.sep, "/")):
        return True
    config = os.environ.get("CLAUDE_CONFIG_DIR")
    return bool(config) and os.path.basename(full) in SETTINGS_NAMES and \
        os.path.dirname(full) == _real(os.path.expanduser(config))


def _is_fences_env(full: str) -> bool:
    return bool(FENCES_ENV_RE.search(full.replace(os.sep, "/")))


def _self_reason(full: str) -> tuple:
    p = full.replace(os.sep, "/")
    if _is_fences_env(p):
        return FENCES_ENV
    if _is_settings(full):
        return SETTINGS_OFF
    if GIT_INTERNALS_RE.search(p):
        return NO_VERIFY  # .git/config, .git/hooks/: outside version control, a way around the hooks
    return FENCE_EDIT  # .githooks/


def _glob_hits(pattern: str, path: str, removes: bool) -> bool:
    """The shell glob pattern (absolute) matches path, or — for a removal — one of its ancestors.
    `*` stays within one path segment and does not match a leading dot, as in bash."""
    pp, tp = pattern.strip("/").split("/"), path.strip("/").split("/")
    if len(pp) > len(tp) or (len(pp) < len(tp) and not removes):
        return False
    for pat, part in zip(pp, tp):
        if part.startswith(".") and not pat.startswith(".") and pat != part:
            return False
        if not fnmatch.fnmatchcase(part, pat):
            return False
    return True


def _covers(word: str, cwd: str, removes: bool, paths: list[str]) -> str | None:
    """The fence file that writing (or, removes, deleting) the path word would hit, or None."""
    for one in _braces(_expand_home(word)):
        if not one:
            continue
        if GLOB_CHARS.search(one):
            pattern = os.path.normpath(os.path.join(_real(cwd), one))
            if hit := next((p for p in paths if _glob_hits(pattern, p, removes)), None):
                return hit
            continue
        full = _real(_join(cwd, one))
        if SELF_FILE_RE.search(full.replace(os.sep, "/")) or _is_settings(full):
            return full
        if removes and (hit := next((p for p in paths if _inside(p, full)), None)):
            return hit
    return None


def _covers_dirs(word: str, cwd: str, removes: bool, dirs: list[str]) -> bool:
    for one in _braces(_expand_home(word)):
        if not one:
            continue
        if GLOB_CHARS.search(one):
            pattern = os.path.normpath(os.path.join(_real(cwd), one))
            if any(_glob_hits(pattern, d, True) for d in dirs):
                return True
            continue
        full = _real(_join(cwd, one))
        if any(_inside(full, d) or (removes and _inside(d, full)) for d in dirs):
            return True
    return False


def _is_level_grant_hook(cmd: Cmd) -> bool:
    """`level.py prompt-hook` records the human's /dark-factory:level grant; only the hook may run it."""
    return "prompt-hook" in cmd.words and any(w.rsplit("/", 1)[-1] == "level.py" for w in cmd.words)


def _git_paths(sub: str, args: list[Word]) -> list[str]:
    """Paths `git rm|mv`, `git checkout <rev> -- …` and `git restore --source …` delete, move or roll back."""
    if sub in ("rm", "mv"):
        return [a for a in args if not a.startswith("-")]
    if sub == "checkout":
        if "--" in args:
            k = args.index("--")
            before = [a for a in args[:k] if not a.startswith("-")]
            return list(args[k + 1:]) if before else []
        pos, i = [], 0
        while i < len(args):
            if args[i] in ("-b", "-B", "--orphan", "--conflict"):
                i += 2
                continue
            if not args[i].startswith("-"):
                pos.append(args[i])
            i += 1
        return pos[1:] if len(pos) >= 2 else []
    if sub == "restore":
        source, pos, i = False, [], 0
        while i < len(args):
            a = args[i]
            if a in ("-s", "--source"):
                source, i = True, i + 2
                continue
            if a.startswith("--source=") or (a.startswith("-s") and not a.startswith("--") and len(a) > 2):
                source = True
            elif not a.startswith("-"):
                pos.append(a)
            i += 1
        return pos if source else []
    return []


def _removes_untracked(sub: str, args: list[Word]) -> tuple[list[str], set[str], list[str]] | None:
    """`git clean` / `git stash -u|-a`: (pathspecs, statuses removed: '??' untracked, '!!' ignored, excludes)."""
    kinds: set[str] = set()
    specs, excludes, i = [], [], 0
    if sub == "clean":
        kinds = {"??"}
        while i < len(args):
            a = args[i]
            if a == "--":
                specs.extend(args[i + 1:])
                break
            if a in ("-e", "--exclude"):
                excludes.append(args[i + 1] if i + 1 < len(args) else "")
                i += 2
                continue
            if a.startswith("--exclude="):
                excludes.append(a.split("=", 1)[1])
            elif _long(a, ("--dry-run", "--interactive")):
                return None
            elif _is_short(a):
                letters, takes_next = _short_flags(a, "e")
                if "n" in letters or "i" in letters:
                    return None
                if "x" in letters:
                    kinds = {"??", "!!"}
                elif "X" in letters:
                    kinds = {"!!"}
                if takes_next:
                    excludes.append(args[i + 1] if i + 1 < len(args) else "")
                    i += 1
                elif letters.endswith("e") or "e" in letters:
                    excludes.append(a[a.index("e") + 1:])
            elif not a.startswith("-"):
                specs.append(a)
            i += 1
        return specs or ["."], kinds, excludes
    if sub == "stash":
        rest = list(args)
        if rest and not rest[0].startswith("-"):
            if rest[0] not in ("push", "save"):
                return None
            rest = rest[1:]
        while i < len(rest):
            a = rest[i]
            if a == "--":
                specs.extend(rest[i + 1:])
                break
            if a in ("-m", "--message", "--pathspec-from-file"):
                i += 2
                continue
            if _long(a, ("--include-untracked",)):
                kinds.add("??")
            elif _long(a, ("--all",)):
                kinds |= {"??", "!!"}
            elif _is_short(a):
                letters, takes_next = _short_flags(a, "m")
                if "u" in letters:
                    kinds.add("??")
                if "a" in letters:
                    kinds |= {"??", "!!"}
                i += takes_next
            elif not a.startswith("-") and args[:1] == ["push"]:
                specs.append(a)
            i += 1
        return (specs or ["."], kinds, []) if kinds else None
    return None


def _excluded(rel: str, excludes: list[str]) -> bool:
    parts = rel.split("/")
    for pat in excludes:
        pat = pat.strip("/")
        if pat and (fnmatch.fnmatchcase(rel, pat) or any(fnmatch.fnmatchcase(p, pat) for p in parts)
                    or rel.startswith(pat + "/")):
            return True
    return False


def check_git_untracked(words: list[Word], cwd: str, root: str, repo) -> tuple | None:
    """git clean / git stash -u|--all over a fence file that is untracked (or ignored, with -x/--all)."""
    sub, args, dirs, _ = git_split(words)
    found = _removes_untracked(sub, args)
    if found is None:
        return None
    for d in dirs:
        cwd = _join(cwd, _expand_home(d))
    specs, kinds, excludes = found
    r = _real(root)
    candidates = [os.path.join(r, rel) for rel in (FENCES_FILE, ".claude/settings.json", ".claude/settings.local.json")]
    hooks = Path(r) / ".githooks"
    if hooks.is_dir():
        candidates += [str(p) for p in sorted(hooks.rglob("*")) if p.is_file()]
    covered = [p for p in candidates if os.path.exists(p) and any(_covers(s, cwd, True, [p]) for s in specs)]
    rels = [os.path.relpath(p, r) for p in covered]
    rels = [rel for rel in rels if not _excluded(rel, excludes)]
    if not rels:
        return None
    fn = getattr(repo, "untracked", None)
    status = fn(rels, root) if fn else {rel: "??" for rel in rels}
    return CLEAN_FENCES if any(status.get(rel) in kinds for rel in rels) else None


def check_find(cmd: Cmd, cwd: str, paths: list[str]) -> tuple | None:
    """`find … -delete` / `-exec …` from a start point that covers a fence file, unless every -name test
    rules the fence files out."""
    words = cmd.words[1:]
    if not {"-delete", "-exec", "-execdir", "-ok", "-okdir"} & set(words):
        return None
    k = 0
    while k < len(words) and words[k] in ("-H", "-L", "-P"):
        k += 1
    starts = []
    while k < len(words) and not (words[k].startswith("-") or words[k] in ("(", "!", ",")):
        starts.append(words[k])
        k += 1
    expr = words[k:]
    names, opaque = [], False
    for j, w in enumerate(expr):
        if w in ("-name", "-iname") and j + 1 < len(expr):
            names.append((expr[j + 1], w == "-iname"))
        elif w in ("-o", "-or", "!", "-not", "-path", "-ipath", "-wholename", "-iwholename", "-regex", "-iregex"):
            opaque = True
    hits = []
    for start in starts or ["."]:
        for p in paths:
            hit = _covers(start, cwd, True, [p])
            if not hit or not _inside(p, _real(hit)):  # a start that is itself a fence path covers only what is in it
                continue
            base = _real(_join(cwd, _expand_home(start))) if not GLOB_CHARS.search(start) else os.path.dirname(p)
            parts = os.path.relpath(p, base).split(os.sep) if _inside(p, base) else [os.path.basename(p)]
            parts += _names_below(p)  # `find . -name "pre-*" -delete` reaches .githooks/pre-commit
            if opaque or not names or any(
                fnmatch.fnmatchcase(part.lower() if ci else part, pat.lower() if ci else pat)
                for pat, ci in names for part in parts
            ):
                hits.append(_self_reason(p))
    return _worst(hits)


def _names_below(path: str, limit: int = 500) -> list[str]:
    """The names of what lies inside a fence directory (none for a file), so a -name test can be matched."""
    names: list[str] = []
    if os.path.isdir(path):
        for _, dirs, files in os.walk(path):
            names += dirs + files
            if len(names) >= limit:
                break
    return names


def check_self(cmd: Cmd, cwd: str, root: str, protected: list[str], repo=None) -> tuple | None:
    """A Bash command against the fence files or the installed plugin, at every level."""
    return _worst(_self_reasons(cmd, cwd, root, protected, repo))


def _self_reasons(cmd: Cmd, cwd: str, root: str, protected: list[str], repo=None):
    if _is_level_grant_hook(cmd):
        yield PLUGIN_EDIT
    paths = self_paths(root)
    fences = _real(os.path.join(root, FENCES_FILE))
    for path, removes in written_paths(cmd):
        if not path:
            continue
        if _covers_dirs(path, cwd, removes, protected):
            yield PLUGIN_EDIT
        elif hit := _covers(path, cwd, removes, paths):
            yield _self_reason(hit)
        elif _real(_join(cwd, _expand_home(path))) == fences:
            yield FENCES_ENV
    if cmd.name == "find":
        yield check_find(cmd, cwd, paths)
        starts = [w for w in cmd.words[1:] if not w.startswith("-")][:1]
        if {"-delete", "-exec", "-execdir"} & set(cmd.words) and starts and _covers_dirs(starts[0], cwd, True, protected):
            yield PLUGIN_EDIT
    if cmd.name == "git":
        sub, args, dirs, _ = git_split(cmd.words)
        here = cwd
        for d in dirs:
            here = _join(here, _expand_home(d))
        tree = [p for p in paths if not GIT_INTERNALS_RE.search(p.replace(os.sep, "/"))]  # git leaves .git/ alone
        for a in _git_paths(sub, args):
            if hit := _covers(a, here, True, tree):
                yield _self_reason(hit)
        if repo is not None:
            yield check_git_untracked(cmd.words, cwd, root, repo)
        if sub in GIT_WRITE_SUBS and any(_inside(_real(here), d) for d in protected):
            yield PLUGIN_EDIT


def check_assigns(cmd: Cmd) -> tuple | None:
    """GIT_CONFIG_*, GIT_EXTERNAL_DIFF, GIT_EXEC_PATH set for a command or exported: git config by the back door."""
    exported = cmd.words[1:] if cmd.name in EXPORTERS else []
    if any(DANGEROUS_ENV_RE.match(w) for w in [*cmd.assigns, *exported]):
        return GIT_CONFIG
    env_words = cmd.words[1:] if cmd.name == "env" else []
    if any(GUARD_ENV_RE.match(w) for w in [*cmd.assigns, *exported, *env_words]):
        return SETTINGS_OFF  # a child claude would inherit it and run unfenced
    return None


def check_claude(cmd: Cmd, command: str) -> tuple | None:
    """`claude` from Bash: no /dark-factory:level prompt (it would mint the human's grant), no plugin switch-off."""
    if cmd.name != "claude":
        return None
    if LEVEL_CMD_RE.search(command):
        return LEVEL_SELF
    text = " ".join(cmd.words)
    if "/plugin" in text or ("plugin" in cmd.words and {"disable", "uninstall", "remove", "rm"} & set(cmd.words)
                             and "dark-factory" in text):
        return SETTINGS_OFF
    return None


def _reads_only(cmd: Cmd) -> bool:
    """The command only reads what it names (its writes, if any, are redirections checked as paths)."""
    if not cmd.words or cmd.name in READERS or (cmd.name == "sed" and not _in_place(cmd)):
        return True
    if cmd.name == "eval" or (cmd.name in SHELLS and (_nested(cmd.words) is not None or (cmd.heredoc and _stdin_shell(cmd)))):
        return True  # the inner commands are checked on their own
    if cmd.name == "git":
        return git_split(cmd.words)[0] not in GIT_RUNS
    return False


def check_mentions(command: str, cmds: list[Cmd]) -> tuple | None:
    """A line that names a fence file and runs anything but plain reads: an interpreter one-liner, find,
    a copied level.py — whatever writes it cannot be told, so it is denied."""
    if all(_reads_only(c) for c in cmds):
        return None
    return _worst(reason for rx, reason in MENTIONS if rx.search(command))


def _edited_text(tool: str, inp: dict, old: str | None) -> str | None:
    """The file's text after an Edit/Write/MultiEdit, or None when it cannot be told."""
    if tool == "Write":
        content = inp.get("content")
        return content if isinstance(content, str) else None
    if tool == "Edit":
        edits = [inp]
    elif tool == "MultiEdit":
        edits = inp.get("edits")
        if not isinstance(edits, list):
            return None
    else:
        return None
    text = old
    for e in edits:
        if text is None or not isinstance(e, dict):
            return None
        before, after = e.get("old_string"), e.get("new_string")
        if not isinstance(before, str) or not isinstance(after, str) or (before and before not in text):
            return None
        if not before:
            text = after if not text else None
            continue
        text = text.replace(before, after) if e.get("replace_all") else text.replace(before, after, 1)
    return text


def _new_text(inp: dict) -> str:
    """Everything an Edit/Write/MultiEdit/NotebookEdit would put into the file."""
    parts = [inp.get(k) for k in ("content", "new_string", "new_source")]
    edits = inp.get("edits")
    if isinstance(edits, list):
        parts += [e.get("new_string") for e in edits if isinstance(e, dict)]
    return "\n".join(p for p in parts if isinstance(p, str))


def lowers_fences(old: str, new: str) -> bool:
    """The new fences.env text is less strict than the old: a lower FENCE_LEVEL, QUOTA_FAIL_CLOSED
    from 1 to something else, or a level that cannot be read."""
    before, after = parse_fences(old), parse_fences(new)
    old_level = before.get("FENCE_LEVEL") or DEFAULT_LEVEL
    new_level = after.get("FENCE_LEVEL") or DEFAULT_LEVEL
    if new_level not in LEVEL_RANK:
        return True
    if LEVEL_RANK[new_level] < LEVEL_RANK.get(old_level, LEVEL_RANK[DEFAULT_LEVEL]):
        return True
    return before.get("QUOTA_FAIL_CLOSED") == "1" and after.get("QUOTA_FAIL_CLOSED") != "1"


def check_fences_edit(tool: str, inp: dict, full: str) -> tuple | None:
    """Edit/Write of a fences.env: raising or keeping the level is the edit's business; lowering it,
    emptying the file, or an edit whose result cannot be told is a fence change (asked)."""
    try:
        old = Path(full).read_text(encoding="utf-8")
    except FileNotFoundError:
        old = None
    except OSError:
        return FENCES_ENV
    new = _edited_text(tool, inp, old)
    if new is None or not new.strip():
        return FENCES_ENV
    if old is not None and lowers_fences(old, new):
        return FENCE_LOWER
    return None


def check_settings_edit(tool: str, inp: dict, full: str) -> tuple | None:
    """Edit/Write of a Claude Code settings file (project, local, user), at every level: a change that
    switches the hooks or the plugin off, or sets GUARD/QUOTA_GUARD for the session, is asked."""
    if SETTINGS_OFF_RE.search(_new_text(inp)):
        return SETTINGS_OFF
    try:
        old = Path(full).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return SETTINGS_OFF
    if PLUGIN_ON_RE.search(old):
        new = _edited_text(tool, inp, old)
        if new is None or not PLUGIN_ON_RE.search(new):
            return SETTINGS_OFF
    return None


def check_git_internals_edit(inp: dict, full: str) -> tuple | None:
    """Edit/Write of .git/hooks/* (always denied) or .git/config (denied when it touches hooks, aliases,
    pagers, push settings…): the SessionStart repair only comes next session."""
    if GIT_HOOKS_RE.search(full.replace(os.sep, "/")):
        return NO_VERIFY
    text = _new_text(inp)
    if m := GIT_CONFIG_TEXT_RE.search(text):
        return NO_VERIFY if "hookspath" in m.group(0).lower() else GIT_CONFIG
    return None


def _alias(repo, name: str, cwd: str) -> str | None:
    fn = getattr(repo, "alias", None)
    return fn(name, cwd) if fn else None


def check_alias(cmd: Cmd, cwd: str, repo, level: str, root: str, depth: int) -> tuple | None:
    """`git <alias> …`: the expansion is checked as the command it stands for (a `!shell` alias as a shell line)."""
    sub, args, _, _ = git_split(cmd.words)
    if not sub or sub in GIT_BUILTINS or depth >= 3:
        return None
    expansion = _alias(repo, sub, cwd)
    if not expansion:
        return None
    import shlex  # only here: most calls never need it

    tail = " ".join(shlex.quote(a) for a in args)
    if expansion.startswith("!"):
        line = f"{expansion[1:]} {tail}"
    else:
        head = cmd.words[:len(cmd.words) - len(args) - 1]
        line = f"{' '.join(shlex.quote(w) for w in head)} {expansion} {tail}"
    return check_command(line, cwd, repo, level, root, _depth=depth + 1)


def check_command(command: str, cwd: str, repo, level: str, root: str | None = None, _depth: int = 0) -> tuple | None:
    strict = level != "solo"
    root = root or getattr(repo, "root", None) or cwd
    protected = protected_dirs(root)
    cmds = commands(command)

    def reasons():
        nonlocal cwd
        for cmd in cmds:
            if not cmd.words and not cmd.redirects:
                continue
            if cmd.name in ("cd", "pushd"):
                target = cmd.words[1] if len(cmd.words) > 1 else Word("~")
                if not target.dynamic or target.startswith(("$HOME", "${HOME}")):
                    if target != "-":
                        cwd = _join(cwd, _expand_home(target))
                continue
            here = _join(cwd, cmd.chdir) if cmd.chdir else cwd
            yield check_self(cmd, here, root, protected, repo)
            yield check_assigns(cmd)
            yield check_claude(cmd, command)
            yield check_release(cmd)
            if cmd.name == "git":
                yield check_git(cmd.words, here, repo)
                yield check_alias(cmd, here, repo, level, root, _depth)
            elif cmd.name == "gh":
                yield check_gh(cmd.words, strict)
            elif cmd.name in HTTP_TOOLS:
                yield check_http(cmd)
            if strict:
                yield check_fence_write(cmd, here)
        yield check_mentions(command, cmds)

    # A plain deny anywhere in the line wins over a fence change the human could allow.
    return _worst(reasons())


def decide(hook: dict, repo, level: str, cwd: str, root: str | None = None) -> tuple | None:
    tool = str(hook.get("tool_name") or "")
    inp = hook.get("tool_input") or {}
    inp = inp if isinstance(inp, dict) else {}
    root = root or getattr(repo, "root", None) or cwd
    if SCHEDULE_TOOL_RE.search(tool) and LEVEL_TARGET_RE.search(json.dumps(inp, ensure_ascii=False)):
        return LEVEL_SELF
    if tool == "Bash":
        return check_command(str(inp.get("command") or ""), cwd, repo, level, root)
    if tool in EDIT_TOOLS:
        path = inp.get("file_path") or inp.get("notebook_path") or ""
        if not path:
            return None
        full = _real(_join(cwd, path))
        if any(_inside(full, d) for d in protected_dirs(root)):
            return PLUGIN_EDIT
        if GIT_INTERNALS_RE.search(full.replace(os.sep, "/")) and (reason := check_git_internals_edit(inp, full)):
            return reason
        if _is_settings(full) and (reason := check_settings_edit(tool, inp, full)):
            return reason
        if _is_fences_env(full) and (reason := check_fences_edit(tool, inp, full)):
            return reason
        if level != "solo" and is_fence_path(path, cwd):
            return FENCE_EDIT
    return None


def launched(hook: dict) -> str | None:
    """Why no human can answer a permission dialog for this call, or None when one can be present: a subagent
    (the hook input carries agent_id), a session our launchers mark (DF_UNATTENDED=1, the On-call), `claude -p` or another
    SDK run (CLAUDE_CODE_ENTRYPOINT=sdk-…) that is not a remote-control session. Read from the hook's own input and environment, which the agent's
    commands do not reach. A miss costs liveness, not safety: `ask` in `claude -p` is a deny anyway."""
    if hook.get("agent_id"):
        return "a subagent"
    if os.environ.get("DF_UNATTENDED", "") not in ("", "0"):
        return "an unattended session, DF_UNATTENDED"
    entry = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")
    # A session served by `claude remote-control` is an SDK run too, but its permission dialogs reach the owner in
    # the Claude app and claude.ai: a human can answer them.
    if entry.startswith("sdk") and os.environ.get("CLAUDE_CODE_ENVIRONMENT_KIND") != "bridge":
        return f"a non-interactive run, CLAUDE_CODE_ENTRYPOINT={entry}"
    return None


def _payload(decision: str, text: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": text,
        }
    }


def deny(reason: tuple, main: str = "main", who: str | None = None) -> dict:
    """A plain deny; for a fence change (askable) in a launched agent (who), the deny that leaves it to the human."""
    what, advice = (part.replace("{main}", main) for part in reason)
    if who:
        return _payload("deny", f"⛔ guard: {what} — no human here to allow it ({who}). {UNATTENDED_ADVICE} {advice}")
    return _payload("deny", f"⛔ guard: {what}. {advice} {BYPASS}")


def ask(reason: tuple, main: str = "main") -> dict:
    """A fence change: Claude Code shows the call in its permission dialog and runs it only on the human's yes."""
    what, advice = (part.replace("{main}", main) for part in reason)
    return _payload("ask", f"🛡 guard: {what}. The agent may change the fences only with your yes: allow it only "
                           f"if you want this change. {advice}")


def decision_payload(reason: tuple, hook: dict) -> tuple[str, dict] | None:
    """A release decision, resolved by who runs the session: None = let it through (the On-call's wrapper
    commands), else ("ask" | "deny", payload). The On-call is told apart by DF_ROLE in the hook's own
    environment, which an agent's Bash command cannot set for the hook."""
    what, advice = reason
    if os.environ.get("DF_ROLE") == "on-call" and not hook.get("agent_id"):
        if any(reason is r for r in ON_CALL_MAY):
            return None
        return "deny", _payload("deny", f"⛔ guard: {what}. The On-call may not do this: only the owner may "
                                        f"(--owner-accepts, --manual, a label or comment set by hand). {advice} {BYPASS}")
    who = launched(hook)
    if who:
        return "deny", _payload("deny", f"⛔ guard: {what} — no human here to answer ({who}). {advice} {BYPASS}")
    return "ask", _payload("ask", f"🛡 guard: {what}. {advice}")


def _pinned(word: str, script: str, root: str) -> bool:
    """word names the project's own scripts/<script>: relative to the project root, or its absolute path
    under the project root (the real path). `/tmp/x/feedback-desk.sh` is not the wrapper."""
    if word in (f"scripts/{script}", f"./scripts/{script}"):
        return True
    roots = {os.path.realpath(r) for r in (root, os.environ.get("CLAUDE_PROJECT_DIR")) if r}
    return os.path.isabs(word) and os.path.realpath(word) in {os.path.join(r, "scripts", script) for r in roots}


def oncall_shape(hook: dict, root: str = "") -> tuple | None:
    """In the On-call session a Bash call is exactly one plain `scripts/feedback-desk.sh accept|release …`
    (or the read-only `scripts/release.sh --list-unaccepted`); both pinned to the project's own scripts."""
    if os.environ.get("DF_ROLE") != "on-call" or hook.get("agent_id") or hook.get("tool_name") != "Bash":
        return None
    inp = hook.get("tool_input") or {}
    cmds = commands(str(inp.get("command") or "") if isinstance(inp, dict) else "")
    if len(cmds) == 1:
        cmd = cmds[0]
        w = cmd.words
        listed = bool(w) and (
            (_pinned(w[0], "feedback-desk.sh", root) and len(w) > 1 and w[1] in ("accept", "release"))
            or (_pinned(w[0], "release.sh", root) and list(w[1:]) == ["--list-unaccepted"]))
        if (listed and not cmd.redirects and not cmd.heredoc and not cmd.assigns and not cmd.chdir
                and not any(x.dynamic or x.subs for x in w)):
            return None
    return ON_CALL_ONLY


def audit(root: str, event: str, hook: dict, why: str = "") -> None:
    """One JSON line per deny, per ask and per GUARD=off pass in .claude/state/audit.jsonl; best effort, never fails."""
    try:
        path = Path(root) / AUDIT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": event,
            "tool": hook.get("tool_name"),
            "input": json.dumps(hook.get("tool_input"), ensure_ascii=False)[:500],
            "why": why,
            "session": hook.get("session_id"),
        }
        if hook.get("agent_id"):
            entry["agent"] = hook.get("agent_id")
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — the log must never break the hook
        pass


def main() -> int:
    raw = sys.stdin.read()
    try:
        hook = json.loads(raw)
        if not isinstance(hook, dict):
            raise ValueError("not a JSON object")
    except ValueError as exc:  # fail closed, like a crash: in an opted-in project nothing passes unread
        root = project_root(os.getcwd())
        if not opted_in(root):
            return 0
        print(f"⛔ guard: unreadable hook input ({exc}) — denied. Retry the call; if it keeps failing, tell the "
              f"human. {BYPASS}", file=sys.stderr)
        audit(root, "deny", {}, f"unreadable hook input: {exc}")
        return 2
    cwd = hook.get("cwd") or os.getcwd()
    root = project_root(cwd)
    floor = fences_floor(root)
    if not opted_in(root, floor):
        return 0
    if os.environ.get("GUARD") == "off":  # the session's own environment: set by the human who started it
        print(f"guard: GUARD=off — {hook.get('tool_name')} not checked (logged to {AUDIT_FILE})", file=sys.stderr)
        audit(root, "guard-off", hook)
        return 0
    repo = GitRepo(root)
    try:
        reason = decide(hook, repo, fence_level(root, floor), cwd, root)
    except Exception as exc:  # a fence that crashes must not open: deny and say why
        reason = (
            f"the guard could not read this command ({type(exc).__name__}: {exc})",
            "Rewrite it in a plainer form, or ask the human.",
        )
    if reason is None or is_decision(reason):
        shape = oncall_shape(hook, root)
        if shape:
            reason = shape  # not the wrapper: a plain deny, whatever else the command holds
    if reason and is_decision(reason):
        resolved = decision_payload(reason, hook)
        if resolved is None:
            return 0
        event, payload = resolved
        print(json.dumps(payload, ensure_ascii=False))
        audit(root, event, hook, payload["hookSpecificOutput"]["permissionDecisionReason"][:300])
        return 0
    if reason:
        who = launched(hook) if askable(reason) else None
        if askable(reason) and not who:
            event, payload = "ask", ask(reason, repo.main_branch())
        else:
            event, payload = "deny", deny(reason, repo.main_branch(), who)
        print(json.dumps(payload, ensure_ascii=False))
        audit(root, event, hook, payload["hookSpecificOutput"]["permissionDecisionReason"][:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())
