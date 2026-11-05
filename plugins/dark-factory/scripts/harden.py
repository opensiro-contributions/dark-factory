#!/usr/bin/env python3
"""Controls inventory for the `harden` skill: what a project really enforces, with no model.

    python3 harden.py [project dir]      → markdown on stdout

The skill matches the project's written rules (CLAUDE.md, standards) against this list and reports the
rules nothing enforces. Read-only; works in any git project, with or without the dark-factory fences.
It never opens env or secret files, and masks token-like strings in what it prints (permission rules,
hook commands, CI steps). stdlib only, Python 3.11+.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import guard  # noqa: E402  — the fence level is read exactly as the guard reads it

# What the guard (and merge-feature.sh) enforce at each fence level. guard.py's docstring is the source of truth;
# this is the summary the skill matches rules against.
GUARD_LEVELS = {
    "solo": [
        "denied: force push, push to main/master/the production branch, deleting or hard-resetting the main branch",
        "denied: --no-verify / commit -n, hooks re-pointed away from .githooks, git config that reroutes commands",
        "denied: pushing release tags, gh release create|edit|delete, GitHub API writes to refs/merges/protection",
        "denied: writes into the installed plugin; booking a /dark-factory:level prompt the human did not type",
        "asked (denied to launched agents): changing the fence files from the shell, lowering FENCE_LEVEL, "
        "settings edits that disable hooks or the plugin, GUARD/QUOTA_GUARD variables",
        "asked or denied: release and acceptance decisions (release.sh, the accepted label)",
    ],
    "standard": [
        "denied: `gh pr merge` and the merge API (merging goes through scripts/merge-feature.sh)",
        "denied: `gh pr review --approve`",
        "asked: any edit of the fence files, incl. scripts/merge-feature.sh",
    ],
    "team": ["merge-feature.sh (not the guard): a GitHub approval before merging; --skip-ci refused"],
}

LINT_KEYS = ("[tool.ruff", "select", "extend-select", "ignore", "[tool.mypy", "strict", "[tool.pytest",
             "[tool.importlinter", "[tool.pyright", "no-restricted", "eslint", '"lint', '"typecheck"', '"test"',
             "id:", "repo:", "run:")
LINT_FILES = {"pyproject.toml", "ruff.toml", ".ruff.toml", "mypy.ini", "setup.cfg", "package.json",
              ".pre-commit-config.yaml", ".importlinter", "Makefile", "biome.json", "lefthook.yml", "lefthook.yaml"}
RULE_CHECK_WORDS = ("invariant", "architect", "boundar", "import", "lint", "contract", "naming", "pyramid",
                    "guard", "standard", "rule", "check")
SECRET_RE = re.compile(
    r"(?i)((?:token|secret|password|passwd|api[_-]?key|authorization)[\"']?\s*[=:]\s*[\"']?(?:bearer\s+)?|bearer\s+)"
    r"[^\s\"';&|)]{4,}"
    r"|\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{16,}"
    r"|[sr]k_live_[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_-]{30,}"
    r"|eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"
    r"|sk-[A-Za-z0-9_-]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})"
    r"|(://[^:/\s@]+:)[^@\s]+@")


def mask(text: str) -> str:
    """Token-like strings replaced by ***: a printed permission rule or hook command may carry one."""
    def sub(m: re.Match) -> str:
        if m.group(1):
            return m.group(1) + "***"
        if m.group(2):
            return m.group(2) + "***@"
        return "***"
    return SECRET_RE.sub(sub, text)


def sh(args: list[str], cwd: Path) -> str:
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def read(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace")
    except (OSError, ValueError):
        return None


def load_json(path: Path) -> dict | None:
    text = read(path)
    try:
        data = json.loads(text) if text is not None else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def fences(root: Path) -> list[str]:
    local = guard.read_fences(str(root))
    floor = guard.fences_floor(str(root))
    eff = guard.effective_fences(str(root), floor)
    if not eff:
        return ["- no `.claude/fences.env` here or on origin's main branch: the dark-factory guard and quota guard "
                "are silent in this project"]
    level = eff["FENCE_LEVEL"]
    written = local.get("FENCE_LEVEL")
    lines = [f"- effective FENCE_LEVEL={level} (what the guard applies)"]
    if written and written not in guard.LEVELS:
        lines.append(f"  - the working tree says FENCE_LEVEL={written}: unknown, the guard treats it as "
                     f"`{guard.DEFAULT_LEVEL}`")
    elif written and written != level:
        lines.append(f"  - the working tree says {written}; origin's main branch is stricter and wins")
    elif not written:
        lines.append(f"  - no FENCE_LEVEL in the working tree: `{guard.DEFAULT_LEVEL}` by default, or the main "
                     "branch's")
    if floor and floor.get("FENCE_LEVEL") and floor["FENCE_LEVEL"] != written:
        lines.append(f"  - origin's main branch says FENCE_LEVEL={floor['FENCE_LEVEL']}")
    if "QUOTA_FAIL_CLOSED" in eff:
        lines.append(f"- QUOTA_FAIL_CLOSED={eff['QUOTA_FAIL_CLOSED']}")
    rank = guard.LEVEL_RANK[level]
    lines.append(f"- enforced at `{level}`:")
    for lvl in guard.LEVELS[: rank + 1]:
        lines += [f"  - {item}" for item in GUARD_LEVELS[lvl]]
    for lvl in guard.LEVELS[rank + 1:]:
        lines += [f"  - NOT at this level (only from `{lvl}`): {item}" for item in GUARD_LEVELS[lvl]]
    return lines


def hook_lines(hooks: object) -> list[str]:
    out = []
    for event, groups in as_dict(hooks).items():
        for group in as_list(groups):
            group = as_dict(group)
            for h in as_list(group.get("hooks")):
                cmd = str(as_dict(h).get("command", ""))[:200]
                out.append(f"- hook {event} [{group.get('matcher', '*')}]: {mask(cmd)}")
    return out


def settings(root: Path) -> list[str]:
    lines: list[str] = []
    sandbox: dict[str, tuple[object, str]] = {}
    plugins: dict[str, bool] = {}
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    files = [("user", home / "settings.json"), ("project", root / ".claude/settings.json"),
             ("local", root / ".claude/settings.local.json")]
    for scope, path in files:
        data = load_json(path) if path.exists() else None
        label = f"{scope}: {path.name if scope != 'user' else '~/.claude/settings.json'}"
        if data is None:
            lines.append(f"### {label}: {'absent' if not path.exists() else 'unreadable or not a JSON object'}\n")
            continue
        lines.append(f"### {label}\n")
        perms = as_dict(data.get("permissions"))
        if "defaultMode" in perms:
            lines.append(f"- permissions.defaultMode: {perms['defaultMode']}")
        for key in ("deny", "ask", "allow"):
            vals = as_list(perms.get(key))
            lines.append(f"- {key} ({len(vals)}): " + mask("; ".join(map(str, vals))))
        sb = data.get("sandbox")
        if isinstance(sb, dict):
            if "enabled" in sb:
                sandbox["enabled"] = (sb["enabled"], scope)
            lines.append(f"- sandbox: {mask(json.dumps(sb))[:400]}")
        lines += hook_lines(data.get("hooks"))
        plugins.update({k: bool(v) for k, v in as_dict(data.get("enabledPlugins")).items()})
        lines.append("")
    on = [k for k, v in plugins.items() if v]
    sb_value, sb_scope = sandbox.get("enabled", (False, "default"))
    lines.append(f"- effective: sandbox {'ON' if sb_value else 'OFF'} (set by: {sb_scope}; local > project > user); "
                 f"enabled plugins: {', '.join(on) or 'none'}")
    lines.append("- not read: managed (enterprise) settings, and `--settings` / flags a session was started with")
    if any(p.startswith("dark-factory@") for p in on):
        hooks = load_json(PLUGIN_ROOT / "hooks" / "hooks.json") or {}
        lines.append("- dark-factory plugin hooks:")
        lines += ["  " + line for line in hook_lines(hooks.get("hooks"))]
    elif (root / ".claude/fences.env").exists():
        lines.append("- WARNING: `.claude/fences.env` exists but the dark-factory plugin is not enabled in these "
                     "settings: the guard runs only where the plugin is enabled")
    return lines


def git_hooks(root: Path) -> list[str]:
    configured = sh(["git", "config", "core.hooksPath"], root)
    hook_dir = Path(sh(["git", "rev-parse", "--path-format=absolute", "--git-path", "hooks"], root)
                    or root / ".git/hooks")
    lines = [f"- hooks directory: {configured or '(default)'} → {hook_dir}"]
    if not hook_dir.is_dir():
        return lines + ["- no hooks directory"]
    found = False
    for f in sorted(hook_dir.iterdir()):
        if not f.is_file() or f.name.endswith(".sample"):
            continue
        found = True
        text = read(f)
        if text is None:
            lines.append(f"- {f.name}: unreadable")
            continue
        body = [ln.rstrip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        runs = "" if os.access(f, os.X_OK) else " — NOT EXECUTABLE: git does not run it"
        shown = [mask(ln) for ln in body[:120]]
        more = f"\n  … {len(body) - 120} more lines" if len(body) > 120 else ""
        lines.append(f"- {f.name} ({len(body)} lines without comments){runs}:\n  ```\n  "
                     + "\n  ".join(shown) + f"\n  ```{more}")
    return lines if found else lines + ["- no hooks"]


def ci_workflow(text: str) -> list[str]:
    lines: list[str] = []
    rows = text.splitlines()
    top = None          # current top-level key
    job_indent = None
    i = 0
    while i < len(rows):
        ln = rows[i]
        stripped = ln.strip()
        i += 1
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(ln) - len(ln.lstrip())
        if indent == 0:
            top = re.sub(r"[\"']", "", stripped.split(":")[0])
            if top == "on":
                trig = [stripped]
                while i < len(rows) and (not rows[i].strip() or rows[i].startswith((" ", "\t", "#"))):
                    if rows[i].strip() and not rows[i].strip().startswith("#"):
                        trig.append(rows[i].strip())
                    i += 1
                lines.append("- triggers: " + " ".join(trig)[:300])
            continue
        if top != "jobs":
            continue
        if job_indent is None:
            job_indent = indent
        if indent == job_indent and (m := re.match(r"^([\w-]+):\s*$", stripped)):
            lines.append(f"- job `{m.group(1)}`")
            continue
        m = re.match(r"^(?:-\s*)?(if|run|uses|needs|continue-on-error):\s*(.*)$", stripped)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key == "run" and value in ("|", ">", "|-", ">-", ""):
            body = []
            while i < len(rows) and (not rows[i].strip() or len(rows[i]) - len(rows[i].lstrip()) > indent):
                if rows[i].strip() and not rows[i].strip().startswith("#"):
                    body.append(rows[i].strip())
                i += 1
            value = " ⏎ ".join(body)
        lines.append(f"  - {key}: {mask(value)[:300]}")
    return lines


def ci(root: Path) -> list[str]:
    wf = root / ".github/workflows"
    if not wf.is_dir():
        return ["- no .github/workflows"]
    lines: list[str] = []
    for f in sorted(wf.glob("*.y*ml")):
        text = read(f)
        lines.append(f"### {f.name}\n")
        lines += ci_workflow(text) if text is not None else ["- unreadable"]
        lines.append("")
    return lines


def linters(root: Path, tracked: list[str]) -> list[str]:
    lines = []
    for rel in tracked:
        base = rel.rsplit("/", 1)[-1]
        is_eslint = base.startswith(("eslint.config.", ".eslintrc"))
        if not (base in LINT_FILES or is_eslint) or "node_modules/" in rel:
            continue
        text = read(root / rel)
        if text is None:
            continue
        hits = [ln.strip()[:160] for ln in text.splitlines() if any(k in ln for k in LINT_KEYS)]
        if base == "Makefile":
            hits = [ln.split(":")[0] + ":" for ln in text.splitlines() if re.match(r"^[\w.-]+:", ln)]
        if is_eslint:
            hits = hits or ["(eslint config present)"]
        if hits:
            lines.append(f"- {rel}: " + mask(" | ".join(dict.fromkeys(hits[:30]))))
    for d in (".husky",):
        if any(t.startswith(d + "/") for t in tracked):
            lines.append(f"- {d}/: git hooks managed by husky (see its files)")
    return lines or ["- none found"]


def rule_checks(tracked: list[str]) -> list[str]:
    out = []
    for rel in tracked:
        base = rel.rsplit("/", 1)[-1].lower()
        if "node_modules/" in rel or not base.endswith((".py", ".ts", ".tsx", ".js", ".mjs", ".sh")):
            continue
        in_tests = base.startswith("test") or ".test." in base or ".spec." in base or "/tests/" in f"/{rel}" \
            or "/__tests__/" in f"/{rel}"
        is_script = rel.startswith(("scripts/", "tools/", "bin/", "ci/", ".github/scripts/"))
        if (in_tests or is_script) and any(w in base for w in RULE_CHECK_WORDS):
            out.append(f"- {rel}")
    return (["(by file name only: a hint where to look, not evidence)"] + out) if out else ["- none found by name"]


def branch_protection(root: Path) -> list[str]:
    url = sh(["git", "remote", "get-url", "origin"], root)
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?/?$", url)
    if not m:
        return ["- origin is not on GitHub: check the host's own protection by hand"]
    repo = m.group(1)
    default = sh(["gh", "api", f"repos/{repo}", "--jq", ".default_branch"], root)
    if not default:
        return ["- not visible (no `gh`, not logged in, or no access): treat as unknown, not as present"]
    prot = sh(["gh", "api", f"repos/{repo}/branches/{default}/protection"], root)
    rules = sh(["gh", "api", f"repos/{repo}/rulesets", "--jq", '[.[]|.name+" ("+.enforcement+")"]|join(", ")'], root)
    lines = [f"- default branch `{default}` protection: " + ("present" if prot else "none (or not visible)")]
    if prot:
        try:
            data = as_dict(json.loads(prot))
        except ValueError:
            data = {}
        checks = as_dict(data.get("required_status_checks")).get("contexts") or []
        reviews = as_dict(data.get("required_pull_request_reviews"))
        lines.append(f"  - required checks: {', '.join(map(str, checks)) or 'none'}; required approvals: "
                     f"{reviews.get('required_approving_review_count', 0)}; "
                     f"admins enforced: {as_dict(data.get('enforce_admins')).get('enabled', False)}")
    lines.append(f"- rulesets: {rules or 'none (or not visible)'}")
    return lines


def tracked_files(root: Path) -> list[str]:
    try:
        r = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [p for p in r.stdout.decode("utf-8", "replace").split("\0") if p] if r.returncode == 0 else []


def inventory(root: Path) -> str:
    root = root.resolve()
    tracked = tracked_files(root)
    parts = [f"# Controls inventory: {root.name}"]
    for title, body in (
        ("Fence level (dark-factory guard)", fences(root)),
        ("Claude Code settings and hooks", settings(root)),
        ("Git hooks", git_hooks(root)),
        ("CI workflows", ci(root)),
        ("Linters, type checkers, test config", linters(root, tracked)),
        ("Scripts and tests that check project rules", rule_checks(tracked)),
        ("Branch protection (GitHub)", branch_protection(root)),
    ):
        parts.append(f"\n## {title}\n")
        parts.extend(body)
    return "\n".join(parts) + "\n"


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    top = sh(["git", "rev-parse", "--show-toplevel"], root) if root.is_dir() else ""
    if not top:
        print(f"{root}: not a git repository", file=sys.stderr)
        return 1
    sys.stdout.write(inventory(Path(top)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
