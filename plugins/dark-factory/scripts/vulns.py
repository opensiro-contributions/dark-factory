#!/usr/bin/env python3
"""Daily vulnerability check of a project: what its own code pins, against the public OSV database.

df-<slug>-vulns.timer (written by `fleet.py provision`) runs this once a day for every project. No model runs:
  1. fetch origin and read the lock files on its default branch — what is merged, not what a checkout has lying around:
     uv.lock, requirements*.txt (exact `==` pins), package-lock.json, bun.lock;
  2. ask OSV (api.osv.dev) which of those package versions have known vulnerabilities; an advisory and its aliases
     (GHSA, PYSEC, CVE) are one finding, its severity is the GitHub advisory's;
  3. mark a finding critical when its severity reaches VULN_AUTOFIX_MIN (default critical) or its CVE is in CISA's
     catalogue of known exploited vulnerabilities;
  4. tell the owner once about each new finding, critical first (fleet.py notify); a finding already told is not told
     again, one that was not delivered is told the next day. A quiet day sends nothing.
What leaves the server: package names and versions (to api.osv.dev), and a download of the CISA catalogue.

The state is <project>/.claude/state/vulns/state.json: the findings already told and the latest run, which
`fleet.py status` shows. A network failure is recorded there, not raised: the unit does not fail every day.

  vulns.py <checkout> [--dry-run]
    --dry-run  print what would be told; write nothing, send nothing

Settings, from <project>/.claude/factory/session.env:
  VULN_AUTOFIX_MIN=critical|high   the severity from which a finding counts as critical (default critical)
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import time
import tomllib
import urllib.request
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fleet  # noqa: E402

OSV = "https://api.osv.dev/v1"
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
BATCH = 500  # OSV takes up to 1000 queries per batch
LEVELS = ("low", "moderate", "high", "critical")
MAX_LINES = 20  # one notice lists at most this many packages, then "and N more"
TIMEOUT = 60


class LockfileError(ValueError):
    pass


def http_json(url: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data, {"Content-Type": "application/json", "User-Agent": "dark-factory-vulns"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def notify(text: str) -> int:
    return fleet.notify(text)


# ---------- lock files ----------

def is_lockfile(path: str) -> bool:
    p = PurePosixPath(path)
    if "node_modules" in p.parts:
        return False
    return p.name in ("uv.lock", "package-lock.json", "bun.lock") or (
        p.name.startswith("requirements") and p.suffix == ".txt")


REQ_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*===?\s*([^\s;#,]+)")


def parse_lockfile(path: str, text: str) -> list[tuple[str, str, str]]:
    """(ecosystem, name, version) of every pinned registry package; raises LockfileError on a file it cannot read."""
    name = PurePosixPath(path).name
    try:
        if name == "uv.lock":
            data = tomllib.loads(text)
            return [("PyPI", p["name"], p["version"]) for p in data.get("package", [])
                    if "registry" in p.get("source", {}) and "version" in p]
        if name.startswith("requirements"):
            return [("PyPI", m[1], m[2]) for line in text.splitlines() if (m := REQ_PIN.match(line.strip()))]
        if name == "package-lock.json":
            data = json.loads(text)
            if "packages" in data:
                return [("npm", key.rsplit("node_modules/", 1)[1], v["version"])
                        for key, v in data["packages"].items()
                        if "node_modules/" in key and not v.get("link") and "version" in v]
            out: list[tuple[str, str, str]] = []

            def walk(deps: dict) -> None:
                for dep, v in deps.items():
                    if "version" in v:
                        out.append(("npm", dep, v["version"]))
                    walk(v.get("dependencies", {}))
            walk(data.get("dependencies", {}))
            return out
        if name == "bun.lock":
            data = json.loads(re.sub(r",(\s*[}\]])", r"\1", text))  # bun writes trailing commas
            out = []
            for entry in data.get("packages", {}).values():
                pkg, _, version = entry[0].rpartition("@")
                if pkg and version and ":" not in version:  # workspace:, file:, github: — not a registry package
                    out.append(("npm", pkg, version))
            return out
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as e:
        raise LockfileError(f"{path}: {e}") from e
    return []


# ---------- OSV ----------

def fixed_version(vuln: dict, eco: str, name: str) -> str:
    for a in vuln.get("affected", []):
        pkg = a.get("package", {})
        if pkg.get("ecosystem") == eco and pkg.get("name", "").lower() == name.lower():
            fixes = [e["fixed"] for r in a.get("ranges", []) for e in r.get("events", []) if "fixed" in e]
            if fixes:
                return fixes[-1]
    return ""


def kev_cves(net) -> set[str]:
    return {v["cveID"] for v in net(KEV_URL).get("vulnerabilities", []) if "cveID" in v}


def lookup(packages: list[tuple[str, str, str, str]], net=http_json, min_level: str = "critical") -> list[dict]:
    """packages: (ecosystem, name, version, lock file). One finding per package version and advisory group."""
    where: dict[tuple[str, str, str], list[str]] = {}
    for eco, name, version, path in packages:
        where.setdefault((eco, name, version), [])
        if path not in where[(eco, name, version)]:
            where[(eco, name, version)].append(path)
    keys = list(where)
    hits: dict[tuple[str, str, str], list[str]] = {}
    for i in range(0, len(keys), BATCH):
        chunk = keys[i:i + BATCH]
        res = net(f"{OSV}/querybatch", {"queries": [{"package": {"ecosystem": e, "name": n}, "version": v}
                                                     for e, n, v in chunk]})
        for key, r in zip(chunk, res.get("results", [])):
            ids = [v["id"] for v in r.get("vulns", [])]
            if ids:
                hits[key] = ids
    if not hits:
        return []
    kev = kev_cves(net)
    details: dict[str, dict] = {}
    floor = LEVELS.index(min_level)
    found = []
    for (eco, name, version), ids in hits.items():
        done: set[str] = set()
        for vid in sorted(ids, key=lambda i: (not i.startswith("GHSA-"), i)):  # the GitHub advisory leads its group
            if vid in done:
                continue
            v = details.setdefault(vid, net(f"{OSV}/vulns/{vid}"))
            group = {vid, *v.get("aliases", [])}
            done |= group
            severity = v.get("database_specific", {}).get("severity", "") or ""
            cves = sorted(a for a in group if a.startswith("CVE-"))
            in_kev = any(c in kev for c in cves)
            level = severity.lower() if severity.lower() in LEVELS else ""
            found.append({"id": vid, "cve": cves, "severity": severity.upper() or "UNKNOWN", "kev": in_kev,
                          "critical": in_kev or (bool(level) and LEVELS.index(level) >= floor),
                          "ecosystem": eco, "package": name, "version": version, "files": where[(eco, name, version)],
                          "fixed": fixed_version(v, eco, name), "summary": v.get("summary", "")})
    order = {s: i for i, s in enumerate(("CRITICAL", "HIGH", "MODERATE", "LOW", "UNKNOWN"))}
    found.sort(key=lambda f: (not f["critical"], order.get(f["severity"], 9), f["package"], f["id"]))
    return found


def finding_key(f: dict) -> str:
    return f"{f['ecosystem']}:{f['package']}@{f['version']}:{f['id']}"


# ---------- the run ----------

def state_file(project: Path) -> Path:
    return project / ".claude/state/vulns/state.json"


def read_state(project: Path) -> dict:
    try:
        state = json.loads(state_file(project).read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def settings(project: Path) -> dict:
    try:
        return fleet.read_env(project / ".claude/factory/session.env")
    except fleet.SettingsError:
        return {}


def version_key(v: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", v)) or (0,)


def group(found: list[dict]) -> list[dict]:
    """Findings of one package version together: one upgrade usually closes them all."""
    groups: dict[tuple, dict] = {}
    for f in found:
        g = groups.setdefault((f["ecosystem"], f["package"], f["version"]),
                              {"package": f["package"], "version": f["version"], "files": f["files"], "items": []})
        g["items"].append(f)
    order = {s: i for i, s in enumerate(("CRITICAL", "HIGH", "MODERATE", "LOW", "UNKNOWN"))}
    for g in groups.values():
        items = g["items"]
        g["critical"] = any(f["critical"] for f in items)
        g["kev"] = any(f["kev"] for f in items)
        g["severity"] = min((f["severity"] for f in items), key=lambda s: order.get(s, 9))
        fixes = [f["fixed"] for f in items if f["fixed"]]
        g["fixed"] = max(fixes, key=version_key) if fixes else ""
        g["unfixed"] = len(items) - len(fixes)
    return sorted(groups.values(), key=lambda g: (not g["critical"], order.get(g["severity"], 9), g["package"]))


def line(g: dict) -> str:
    items = g["items"]
    lead = [f for f in items if f["critical"]] or items[:1]
    named = "; ".join(" / ".join([f["id"], *f["cve"]]) + (" · known exploited (CISA)" if f["kev"] else "")
                      + (f" — {f['summary']}" if f["summary"] else "") for f in lead[:2])
    more = len(items) - min(len(lead), 2)
    fix = f" → {g['fixed']}" if g["fixed"] else " (no fixed version yet)"
    unfixed = f", {g['unfixed']} without a fix yet" if g["fixed"] and g["unfixed"] else ""
    count = f"{len(items)} advisories" if len(items) > 1 else "1 advisory"
    return (f"{'‼' if g['critical'] else '•'} {g['severity']} {g['package']} {g['version']}{fix} "
            f"({', '.join(g['files'])}): {count}{unfixed} — {named}" + (f" (+{more} more)" if more else ""))


def message(project: Path, ref: str, new: list[dict]) -> str:
    groups = group(new)
    crit = sum(g["critical"] for g in groups)
    head = (f"{socket.gethostname()} · {project.name}: {len(new)} new "
            f"{'vulnerability' if len(new) == 1 else 'vulnerabilities'} in {len(groups)} "
            f"{'package' if len(groups) == 1 else 'packages'} on {ref}"
            + (f"; {crit} with a critical one — needs a fix" if crit else ""))
    lines = [line(g) for g in groups[:MAX_LINES]]
    if len(groups) > MAX_LINES:
        lines.append(f"… and {len(groups) - MAX_LINES} more packages: python3 vulns.py {project} --dry-run")
    return "\n".join([head, *lines])


def check(project: Path, net=http_json, dry: bool = False) -> int:
    state = read_state(project)
    seen: dict = state.get("seen", {}) if isinstance(state.get("seen"), dict) else {}
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def save(ok: bool, text: str, total: int = 0, critical: int = 0) -> int:
        print(text)
        if dry:
            return 0
        f = state_file(project)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"seen": seen, "latest": {"ok": ok, "text": text, "total": total,
                                                          "critical": critical, "at": now}},
                                ensure_ascii=False, indent=1))
        return 0

    git = ["git", "-C", str(project)]
    fleet.sh([*git, "fetch", "-q", "origin"], timeout=120)  # a failed fetch scans the last known origin
    ref = fleet.default_branch(project)
    if not ref:
        return save(False, "no origin default branch in the main checkout")
    code, listing = fleet.sh([*git, "ls-tree", "-r", "--name-only", ref])
    if code:
        return save(False, f"cannot list {ref}: {fleet.last(listing)}")
    packages, broken = [], []
    for path in filter(is_lockfile, listing.splitlines()):
        code, text = fleet.sh([*git, "show", f"{ref}:{path}"])
        try:
            if code:
                raise LockfileError(f"{path}: {fleet.last(text)}")
            packages += [(*pkg, path) for pkg in parse_lockfile(path, text)]
        except LockfileError as e:
            broken.append(str(e))
    unread = f"; unreadable: {'; '.join(broken)}" if broken else ""
    if not packages:
        return save(not broken, f"no lock files on {ref}" if not broken else f"nothing read on {ref}{unread}")
    min_level = settings(project).get("VULN_AUTOFIX_MIN", "critical").strip().lower()
    if min_level not in ("critical", "high"):
        min_level = "critical"
    try:
        found = lookup(packages, net, min_level)
    except (OSError, ValueError, KeyError) as e:  # the next day tries again; the status line shows why
        return save(False, f"OSV check failed: {e}")
    new = [f for f in found if finding_key(f) not in seen]
    crit = sum(f["critical"] for f in found)
    summary = (f"{len(found)} known on {ref} ({crit} critical), {len(new)} new · "
               f"{len({p[:3] for p in packages})} packages{unread}") if found else \
        f"none known on {ref} · {len({p[:3] for p in packages})} packages{unread}"
    if new:
        text = message(project, ref, new)
        if dry:
            print(text)
        elif notify(text) == 0:
            seen.update({finding_key(f): now for f in new})
    seen = {k: v for k, v in seen.items() if k in {finding_key(f) for f in found}}  # fixed ones may come back
    return save(not broken, summary, len(found), crit)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if not (a.project / ".git").exists():
        print(f"✗ {a.project} is not a git checkout", file=sys.stderr)
        return 2
    return check(a.project.resolve(), dry=a.dry_run)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
