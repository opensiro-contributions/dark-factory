#!/usr/bin/env python3
"""Secrets inventory for the `secrets` skill: where a project's secrets could leak or reach the wrong hands,
the agent's included. No model; read-only; prints names, paths, modes and classes — never a value.

    python3 secrets.py [project dir]      → markdown on stdout

Sections: the standard scanner (gitleaks, `--redact`) over the working tree and the git history, or a small
built-in scan when gitleaks is missing; secret files in and around the project (tracked? ignored? who can read);
what the agent can read (Claude Code permissions, sandbox, MCP server credentials in ~/.claude.json, shell
history); the machine (docker/sudo groups, ports published on all interfaces); database dumps and backups.
Values are read only to classify them (a DSN to a remote host, a token-shaped string) and never printed.
stdlib only, Python 3.11+.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import harden  # noqa: E402  — settings reading and masking are shared

SECRET_FILE_RE = re.compile(
    r"(^|/)(\.env(\..+)?|.+\.env|.*credentials.*|.*secrets?\.(json|ya?ml|toml|txt)|.*\.(pem|key|p12|pfx|kdbx)"
    r"|id_(rsa|ed25519|ecdsa)|\.netrc|\.pgpass|.*sa-key.*\.json|service-account.*\.json)$", re.I)
TEMPLATE_RE = re.compile(r"\.(example|sample|template|dist)$|(^|/)example\.env$", re.I)
VALUE_PATTERNS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("database URL with password", re.compile(r"\b[a-z+]+://[^:/\s@]+:[^@\s]+@[^\s/]+")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("Slack token", re.compile(r"\bxox[abp]-[A-Za-z0-9-]{10,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Telegram bot token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("assigned secret", re.compile(r"(?i)\b[\w-]*(token|secret|password|passwd|api[_-]?key)[\w-]*\s*[=:]\s*[\"']?"
                                   r"[^\s\"'$<>{}]{8,}")),
]
LOCAL_HOSTS = ("localhost", "::1", "0.0.0.0", "host.docker.internal")  # plus loopback/private ranges, dotless names
DUMP_RE = re.compile(r"(backup|dump|proddata|snapshot)", re.I)
DUMP_FILE_RE = re.compile(r"\.(sql|dump|bak|sql\.gz|dump\.gz|tar\.gz|tgz|zst)$", re.I)


def sh(args: list[str], cwd: Path | None = None, timeout: int = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return r.returncode, r.stdout


def mode_of(path: Path) -> str:
    try:
        st = path.stat()
    except OSError:
        return "unreadable"
    world = " world-readable" if st.st_mode & stat.S_IROTH else ""
    group = " group-readable" if st.st_mode & stat.S_IRGRP else ""
    me = "readable by this user" if os.access(path, os.R_OK) else "NOT readable by this user"
    owner = "this user" if st.st_uid == os.getuid() else f"uid {st.st_uid}"
    return f"{stat.filemode(st.st_mode)} owner {owner}, {me}{group}{world}"


LOOSE = {"database URL with password", "assigned secret"}
RANDOM_LITERAL_RE = re.compile(r"(?i)\b[\w-]*(token|secret|password|passwd|api[_-]?key)[\w-]*\s*[=:]\s*[\"']"
                               r"(?=[^\"']*\d)(?=[^\"']*[A-Za-z])([A-Za-z0-9+/_=-]{16,})[\"']")


def classify(text: str, strict: bool = False) -> list[str]:
    """What kinds of secrets a text holds — kinds and counts only. `strict` (code and docs): provider-shaped
    tokens, private keys and quoted random-looking literals only; DSNs are judged by dsn_classes."""
    found = []
    for name, rx in VALUE_PATTERNS:
        if strict and name in LOOSE:
            continue
        hits = list(rx.finditer(text))
        if not hits:
            continue
        if name == "assigned secret":
            keys = sorted({re.split(r"\s*[=:]", h.group(0), maxsplit=1)[0].strip().strip("\"'") for h in hits})
            found.append(f"{name} ×{len(hits)} ({', '.join(keys[:12])})")
        else:
            found.append(f"{name} ×{len(hits)}")
    if strict and (lits := RANDOM_LITERAL_RE.findall(text)):
        found.append(f"hard-coded secret literal ×{len(lits)}")
    return found


def dsn_classes(text: str) -> list[str]:
    """For each DSN with a password: scheme, user, local or remote host. Never the password or the host."""
    out = []
    for m in re.finditer(r"\b([a-z+]+)://([^:/\s@]+):[^@\s]+@([^\s/:?]+)", text):
        scheme, user, host = m.groups()
        local = host in LOCAL_HOSTS or host.startswith(("127.", "10.", "192.168.", "172.")) or "." not in host
        where = "local" if local else "REMOTE"
        out.append(f"{scheme} user `{user}` → {where} host")
    return sorted(set(out))


def env_names(text: str) -> list[str]:
    return sorted({m.group(1) for m in re.finditer(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", text, re.M)})


def read(path: Path, limit: int = 2_000_000) -> str | None:
    try:
        if path.stat().st_size > limit:
            return None
        return path.read_text(errors="replace")
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- scanner

def gitleaks(root: Path) -> list[str] | None:
    exe = os.environ.get("GITLEAKS") or shutil.which("gitleaks")
    if not exe or not os.access(exe, os.X_OK):
        return None
    lines = []
    for mode, extra in (("git", []), ("dir", [])):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "r.json"
            code, _ = sh([exe, mode, "--redact", "--no-banner", "--exit-code", "0", "--report-format", "json",
                          "--report-path", str(report), *extra, str(root)], timeout=600)
            try:
                findings = json.loads(report.read_text())
            except (OSError, ValueError):
                lines.append(f"- gitleaks {mode}: failed to run (exit {code})")
                continue
        where = "git history" if mode == "git" else "working tree, ignored files included"
        findings = [f for f in findings if not NOISE_DIR_RE.search(str(f.get("File", "")))]
        lines.append(f"- gitleaks {mode} ({where}): {len(findings)} finding(s) outside dependency and worktree dirs")
        lines += group_findings(root, findings)
    return lines


NOISE_DIR_RE = re.compile(r"(^|/)(node_modules|\.venv|venv|\.git|\.claude/worktrees|dist|build|__pycache__)/")
WEAK_RULES = {"generic-api-key"}


def group_findings(root: Path, findings: list[dict]) -> list[str]:
    """One line per file: its rules, finding count, lines and (history) commits. Provider-specific rules first;
    generic-api-key (gitleaks' noisiest rule) last and only as a count per file."""
    by_file: dict[str, dict] = {}
    for f in findings:
        path = str(f.get("File", "?"))
        try:
            path = str(Path(path).resolve().relative_to(root)) if Path(path).is_absolute() else path
        except ValueError:
            pass
        e = by_file.setdefault(path, {"rules": {}, "lines": set(), "commits": set()})
        e["rules"][f.get("RuleID", "?")] = e["rules"].get(f.get("RuleID", "?"), 0) + 1
        e["lines"].add(f.get("StartLine"))
        if f.get("Commit"):
            e["commits"].add(f["Commit"][:8])
    strong = {p: e for p, e in by_file.items() if set(e["rules"]) - WEAK_RULES}
    weak = {p: e for p, e in by_file.items() if p not in strong}
    out = []
    for group, label in ((strong, "provider-specific"), (weak, "generic-api-key only (often config names: check)")):
        if not group:
            continue
        out.append(f"  - {label}: {len(group)} file(s)")
        for path, e in sorted(group.items())[:80]:
            rules = ", ".join(f"{r} ×{n}" for r, n in sorted(e["rules"].items()))
            lns = ",".join(str(x) for x in sorted(x for x in e["lines"] if x)[:8])
            commits = f"; commits {', '.join(sorted(e['commits'])[:5])}" if e["commits"] else ""
            out.append(f"    - {path}: {rules}; lines {lns}{commits}")
        if len(group) > 80:
            out.append(f"    - … {len(group) - 80} more files")
    return out


def builtin_scan(root: Path, tracked: list[str]) -> list[str]:
    lines = ["- gitleaks is not installed: built-in scan of the tracked files only (no git history). For the "
             "history: install gitleaks (one static binary, no root) and run the skill again"]
    hits = 0
    for rel in tracked:
        if TEMPLATE_RE.search(rel) or "node_modules/" in rel or "/vendor/" in rel:
            continue
        text = read(root / rel)
        if not text:
            continue
        kinds = [k for k in classify(text, strict=True)] + [d for d in dsn_classes(text) if "REMOTE" in d]
        if kinds:
            hits += 1
            lines.append(f"  - {rel}: " + ", ".join(kinds))
            if hits >= 60:
                lines.append("  - … more")
                break
    if hits == 0:
        lines.append("  - nothing secret-shaped in tracked files")
    return lines


# ---------------------------------------------------------------- files

def project_files(root: Path, tracked: list[str]) -> list[str]:
    _, ignored = sh(["git", "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"], root)
    _, untracked = sh(["git", "ls-files", "--others", "--exclude-standard", "-z"], root)
    groups = (("TRACKED (in git)", tracked), ("untracked and NOT ignored (one `git add .` from git)",
              [p for p in untracked.split("\0") if p]), ("ignored", [p for p in ignored.split("\0") if p]))
    lines = []
    for label, paths in groups:
        for rel in paths:
            rel = rel.rstrip("/")
            if "node_modules" in rel or ".venv" in rel or rel.startswith(".claude/worktrees"):
                continue
            path = root / rel
            if path.is_dir():
                continue
            if not SECRET_FILE_RE.search(rel) or TEMPLATE_RE.search(rel):
                continue
            text = read(path) or ""
            what = classify(text) + dsn_classes(text)
            names = env_names(text) if ".env" in rel or rel.endswith(".env") else []
            lines.append(f"- {rel} — {label}; {mode_of(path)}"
                         + (f"; holds: {', '.join(what)}" if what else "")
                         + (f"; variables: {', '.join(names[:40])}" if names else ""))
    return lines or ["- no secret-named files in the project"]


def home_files() -> list[str]:
    home = Path.home()
    lines = []
    candidates: list[Path] = []
    cfg = home / ".config"
    if cfg.is_dir():
        for p in cfg.rglob("*"):
            try:
                rel_depth = len(p.relative_to(cfg).parts)
            except ValueError:
                continue
            if rel_depth > 3 or not p.is_file():
                continue
            if SECRET_FILE_RE.search(p.name) or p.suffix in (".env",) or p.name.endswith(".env"):
                candidates.append(p)
    for name in (".netrc", ".pgpass", ".git-credentials", ".docker/config.json", ".aws/credentials",
                 ".config/gh/hosts.yml", ".npmrc", ".pypirc"):
        if (home / name).is_file():
            candidates.append(home / name)
    if (home / ".ssh").is_dir():
        candidates += [p for p in (home / ".ssh").iterdir() if p.is_file() and p.name.startswith("id_")
                       and not p.name.endswith(".pub")]
    for p in sorted(set(candidates)):
        text = read(p) or ""
        what = classify(text) + dsn_classes(text)
        held = f"holds: {', '.join(what)}" if what else "no secret-shaped values found"
        lines.append(f"- ~/{p.relative_to(home)} — {mode_of(p)}; {held}")
    return lines or ["- none found"]


def claude_json() -> list[str]:
    path = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home()) / ".claude.json"
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        path = Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json"
    data = harden.load_json(path) if path.exists() else None
    if data is None:
        return [f"- {path}: absent or unreadable"]
    lines = [f"- {path}: {mode_of(path)}"]
    scopes = [("user", harden.as_dict(data.get("mcpServers")))]
    for proj, cfg in harden.as_dict(data.get("projects")).items():
        servers = harden.as_dict(harden.as_dict(cfg).get("mcpServers"))
        if servers:
            scopes.append((f"project {Path(proj).name}", servers))
    for scope, servers in scopes:
        for name, srv in servers.items():
            srv = harden.as_dict(srv)
            keys = sorted(harden.as_dict(srv.get("env")).keys()) + sorted(
                f"header {k}" for k in harden.as_dict(srv.get("headers")).keys())
            inline = classify(" ".join(map(str, harden.as_list(srv.get("args")))) + " " + str(srv.get("url", "")))
            if keys or inline:
                lines.append(f"  - MCP `{name}` ({scope}): credentials in {', '.join(keys) or 'args/url'}"
                             + (f" ({', '.join(inline)})" if inline else ""))
    return lines


def history_files() -> list[str]:
    home = Path.home()
    lines = []
    for name in (".bash_history", ".zsh_history", ".local/share/fish/fish_history", ".python_history",
                 ".psql_history", ".mysql_history"):
        p = home / name
        if p.is_file():
            kinds = classify(read(p, limit=20_000_000) or "")
            if kinds:
                lines.append(f"- ~/{name}: {', '.join(kinds)} — {mode_of(p)}")
    return lines or ["- no secret-shaped strings in shell histories"]


# ---------------------------------------------------------------- agent

def agent_access(root: Path) -> list[str]:
    lines = harden.settings(root)
    lines.append("- note: a `Read(...)` deny binds the Read tool only; `cat`, an interpreter or a script in Bash "
                 "reads the same file unless the OS sandbox is ON with denyRead for it, or file permissions stop it")
    return lines


# ---------------------------------------------------------------- machine

def machine(root: Path, tracked: list[str]) -> list[str]:
    _, groups = sh(["id", "-nG"])
    gs = groups.split()
    lines = [f"- this user's groups: {', '.join(gs)}"]
    if "docker" in gs:
        lines.append("  - in `docker`: the docker socket gives root on this host")
    if "sudo" in gs or "wheel" in gs or "admin" in gs:
        code, _ = sh(["sudo", "-n", "true"], timeout=5)
        lines.append(f"  - in a sudo group; passwordless sudo: {'YES' if code == 0 else 'no'}")
    if shutil.which("docker"):
        code, out = sh(["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"], timeout=20)
        if code == 0:
            exposed = []
            for row in out.splitlines():
                name, _, ports = row.partition("\t")
                pub = [p.strip() for p in ports.split(",") if "->" in p and not p.strip().startswith(("127.", "[::1]"))]
                if pub:
                    exposed.append(f"  - container `{name}`: {', '.join(pub)} (all interfaces; docker bypasses ufw)")
            lines += exposed or ["- running containers: no port published on all interfaces"]
        else:
            lines.append("- docker: not reachable for this user")
    for rel in tracked:
        base = rel.rsplit("/", 1)[-1]
        if not re.match(r"(docker-)?compose.*\.ya?ml$", base):
            continue
        text = read(root / rel) or ""
        bad = re.findall(r"^\s*-\s*[\"']?(\d{2,5}:\d{2,5})[\"']?\s*$", text, re.M)
        if bad:
            lines.append(f"- {rel}: ports without a host IP ({', '.join(bad)}) publish on all interfaces")
    return lines


def dumps() -> list[str]:
    home = Path.home()
    lines = []
    for p in sorted(home.iterdir()):
        if not p.is_dir() or not DUMP_RE.search(p.name):
            continue
        files = [f for f in p.rglob("*") if f.is_file() and DUMP_FILE_RE.search(f.name)][:500]
        if files:
            world = sum(1 for f in files if f.stat().st_mode & stat.S_IROTH)
            mine = sum(1 for f in files if f.stat().st_uid == os.getuid())
            lines.append(f"- ~/{p.name}/: {len(files)} dump file(s), {mine} owned by this user (deletable by it), "
                         f"{world} world-readable")
    return lines or ["- no dump or backup directories in the home directory"]


def inventory(root: Path) -> str:
    root = root.resolve()
    tracked = harden.tracked_files(root)
    scan = gitleaks(root)
    parts = [f"# Secrets inventory: {root.name}", "", "Names, paths and kinds only — no values."]
    for title, body in (
        ("Secrets in git and the working tree", scan if scan is not None else builtin_scan(root, tracked)),
        ("Secret files in the project", project_files(root, tracked)),
        ("Secret files around it (home directory)", home_files()),
        ("MCP server credentials (~/.claude.json)", claude_json()),
        ("Shell histories", history_files()),
        ("What the agent may read: Claude Code settings", agent_access(root)),
        ("The machine", machine(root, tracked)),
        ("Dumps and backups", dumps()),
    ):
        parts.append(f"\n## {title}\n")
        parts.extend(body)
    return harden.mask("\n".join(parts)) + "\n"


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    code, top = sh(["git", "rev-parse", "--show-toplevel"], root) if root.is_dir() else (1, "")
    if code != 0 or not top.strip():
        print(f"{root}: not a git repository", file=sys.stderr)
        return 1
    sys.stdout.write(inventory(Path(top.strip())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
