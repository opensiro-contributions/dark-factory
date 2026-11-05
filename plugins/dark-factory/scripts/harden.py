#!/usr/bin/env python3
"""Controls inventory for the `harden` skill: what a project really enforces, with no model.

    python3 harden.py [project dir]      → markdown on stdout

The skill matches the project's written rules (CLAUDE.md, standards) against this list and reports the
rules nothing enforces. Read-only; works in any git project, with or without the dark-factory fences.
It never reads values of env files or secrets. stdlib only, Python 3.11+.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

# What scripts/guard.py denies at each fence level (its docstring is the source of truth; this is the summary
# the skill matches rules against).
GUARD_LEVELS = {
    "solo": [
        "force push, push to main/master/the production branch, deleting or hard-resetting the main branch",
        "--no-verify / commit -n, hooks re-pointed away from .githooks, git config that reroutes commands",
        "pushing release tags, gh release create|edit|delete, GitHub API writes to refs/merges/protection",
        "writes into the installed plugin",
        "fence files (.claude/fences.env, .claude/settings*.json, .githooks/) changed from the shell: asked",
        "release and acceptance decisions (release.sh, accepted label): asked or denied",
    ],
    "standard": [
        "`gh pr merge` and the merge API: denied (merge through scripts/merge-feature.sh)",
        "`gh pr review --approve`: denied",
        "any edit of the fence files incl. scripts/merge-feature.sh: asked",
    ],
    "team": ["a GitHub approval before merge (merge-feature.sh); --skip-ci refused"],
}
LEVEL_ORDER = ["solo", "standard", "team"]

LINT_KEYS = ("[tool.ruff", "select", "extend-select", "ignore", "[tool.mypy", "strict", "[tool.pytest",
             "[tool.importlinter", "[tool.pyright", "no-restricted", "eslint", '"lint', '"typecheck"', '"test"')
RULE_CHECK_WORDS = ("invariant", "architect", "boundar", "import", "lint", "contract", "naming", "pyramid",
                    "guard", "standard", "rule", "check")


def sh(args: list[str], cwd: Path) -> str:
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def load_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def env_keys(path: Path) -> dict[str, str]:
    """KEY=value lines of a fences.env-style file (only the fence file: it holds no secrets)."""
    out: dict[str, str] = {}
    for line in path.read_text(errors="replace").splitlines():
        m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)=\s*['\"]?([^'\"#\s]*)", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def fences(root: Path) -> list[str]:
    fe = root / ".claude/fences.env"
    if not fe.exists():
        return ["- no `.claude/fences.env`: the dark-factory guard and quota guard are silent in this project"]
    keys = env_keys(fe)
    level = keys.get("FENCE_LEVEL", "standard") or "standard"
    lines = [f"- FENCE_LEVEL={level}" + ("" if "FENCE_LEVEL" in keys else " (default)")]
    lines += [f"- {k}={v}" for k, v in keys.items() if k != "FENCE_LEVEL"]
    lines.append(f"- the guard denies at `{level}`:")
    for lvl in LEVEL_ORDER[: LEVEL_ORDER.index(level) + 1 if level in LEVEL_ORDER else 2]:
        lines += [f"  - {item}" for item in GUARD_LEVELS[lvl]]
    above = LEVEL_ORDER[LEVEL_ORDER.index(level) + 1:] if level in LEVEL_ORDER else []
    for lvl in above:
        lines += [f"  - NOT at this level (only from `{lvl}`): {item}" for item in GUARD_LEVELS[lvl]]
    return lines


def hook_lines(hooks: dict) -> list[str]:
    out = []
    for event, groups in (hooks or {}).items():
        for group in groups or []:
            for h in group.get("hooks", []):
                out.append(f"- hook {event} [{group.get('matcher', '*')}]: {str(h.get('command', ''))[:200]}")
    return out


def settings(root: Path) -> list[str]:
    lines: list[str] = []
    sandbox: dict[str, object] = {}
    plugins: dict[str, bool] = {}
    for name in ("settings.json", "settings.local.json"):
        path = root / ".claude" / name
        data = load_json(path) if path.exists() else None
        if data is None:
            lines.append(f"### {name}: {'absent' if not path.exists() else 'unreadable'}\n")
            continue
        lines.append(f"### {name}\n")
        perms = data.get("permissions") or {}
        if "defaultMode" in perms:
            lines.append(f"- permissions.defaultMode: {perms['defaultMode']}")
        for key in ("deny", "ask", "allow"):
            vals = perms.get(key) or []
            lines.append(f"- {key} ({len(vals)}): " + "; ".join(map(str, vals)))
        if isinstance(data.get("sandbox"), dict):
            sandbox.update(data["sandbox"])
            lines.append(f"- sandbox: {json.dumps(data['sandbox'])[:400]}")
        lines += hook_lines(data.get("hooks") or {})
        plugins.update({k: bool(v) for k, v in (data.get("enabledPlugins") or {}).items()})
        lines.append("")
    on = [k for k, v in plugins.items() if v]
    lines.append(f"- effective: sandbox {'ON' if sandbox.get('enabled') else 'OFF'} (local settings win); "
                 f"enabled plugins: {', '.join(on) or 'none'}")
    if any(p.startswith("dark-factory@") for p in on):
        hooks = load_json(PLUGIN_ROOT / "hooks" / "hooks.json") or {}
        lines.append("- dark-factory plugin hooks:")
        lines += ["  " + line for line in hook_lines(hooks.get("hooks") or {})]
    return lines


def git_hooks(root: Path) -> list[str]:
    path = sh(["git", "config", "core.hooksPath"], root) or ".git/hooks"
    hook_dir = root / path
    lines = [f"- core.hooksPath: {path}"]
    if not hook_dir.is_dir():
        return lines + ["- no hooks directory"]
    for f in sorted(hook_dir.iterdir()):
        if f.is_file() and not f.name.endswith(".sample"):
            body = [ln.rstrip() for ln in f.read_text(errors="replace").splitlines()
                    if ln.strip() and not ln.strip().startswith("#")]
            shown = body[:80]
            more = f"\n  … {len(body) - 80} more lines" if len(body) > 80 else ""
            lines.append(f"- {f.name} ({len(body)} lines):\n  ```\n  " + "\n  ".join(shown) + f"\n  ```{more}")
    return lines


def ci(root: Path) -> list[str]:
    wf = root / ".github/workflows"
    if not wf.is_dir():
        return ["- no .github/workflows"]
    lines: list[str] = []
    for f in sorted(wf.glob("*.y*ml")):
        lines.append(f"### {f.name}\n")
        text = f.read_text(errors="replace")
        on = re.search(r"^(?:on|\"on\"|'on'):.*?(?=^\S)", text, re.S | re.M)
        if on:
            lines.append("- triggers: " + " ".join(on.group(0).split())[:300])
        in_jobs = False
        for ln in text.splitlines():
            if re.match(r"^jobs:\s*$", ln):
                in_jobs = True
                continue
            if not in_jobs:
                continue
            if m := re.match(r"^  ([\w-]+):\s*$", ln):
                lines.append(f"- job `{m.group(1)}`")
            elif m := re.match(r"^\s*(?:-\s*)?(if|run|uses|name|needs|continue-on-error):\s*(.+)$", ln):
                lines.append(f"  - {m.group(1)}: {m.group(2).strip()[:200]}")
        lines.append("")
    return lines


def linters(root: Path, tracked: list[str]) -> list[str]:
    names = {"pyproject.toml", "ruff.toml", ".ruff.toml", "mypy.ini", "setup.cfg", "package.json",
             ".pre-commit-config.yaml", ".importlinter", "Makefile", "biome.json", "tsconfig.json"}
    lines = []
    for rel in tracked:
        base = rel.rsplit("/", 1)[-1]
        if not (base in names or base.startswith(("eslint.config.", ".eslintrc"))) or "node_modules/" in rel:
            continue
        text = (root / rel).read_text(errors="replace")
        hits = [ln.strip()[:160] for ln in text.splitlines() if any(k in ln for k in LINT_KEYS)]
        if base == "Makefile":
            hits = [ln.split(":")[0] + ":" for ln in text.splitlines() if re.match(r"^[\w.-]+:", ln)]
        if base.startswith(("eslint.config.", ".eslintrc")):
            hits = hits or ["(eslint config present)"]
        if hits:
            lines.append(f"- {rel}: " + " | ".join(dict.fromkeys(hits[:30])))
    return lines or ["- none found"]


def rule_checks(tracked: list[str]) -> list[str]:
    out = []
    for rel in tracked:
        base = rel.rsplit("/", 1)[-1].lower()
        if "node_modules/" in rel or not base.endswith((".py", ".ts", ".tsx", ".js", ".mjs", ".sh")):
            continue
        is_test = base.startswith("test") or ".test." in base or ".spec." in base
        is_script = rel.startswith(("scripts/", "tools/", "bin/"))
        if (is_test or is_script) and any(w in base for w in RULE_CHECK_WORDS):
            out.append(f"- {rel}")
    return out or ["- none found"]


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
        data = json.loads(prot)
        checks = (data.get("required_status_checks") or {}).get("contexts") or []
        reviews = data.get("required_pull_request_reviews") or {}
        lines.append(f"  - required checks: {', '.join(checks) or 'none'}; required approvals: "
                     f"{reviews.get('required_approving_review_count', 0)}; "
                     f"admins enforced: {(data.get('enforce_admins') or {}).get('enabled', False)}")
    lines.append(f"- rulesets: {rules or 'none (or not visible)'}")
    return lines


def inventory(root: Path) -> str:
    root = root.resolve()
    tracked = sh(["git", "ls-files"], root).splitlines()
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
    if not (root / ".git").exists() and not sh(["git", "rev-parse", "--git-dir"], root):
        print(f"{root}: not a git repository", file=sys.stderr)
        return 1
    sys.stdout.write(inventory(root))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
