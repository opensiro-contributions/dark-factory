"""The published side: letters instead of repositories, the weekly report, the summary, the leak check.

Nothing here reads a transcript: it renders the server store. A repository gets a letter once and keeps it
(aliases.json in the store, never published); the canon is the one name shown ("canon"). Before anything is written
the leak check looks for every name the sources know — repository, checkout path, host, the owner's handles, a work
branch's slug — as a whole word in every rendered file.
"""
from __future__ import annotations

import hashlib
import json
import re
import socket
import string
from datetime import timedelta
from pathlib import Path

from . import codehost, store
from .transcripts import KINDS, SPLITS

KIND_LABEL = {"owner": "started by the owner", "on_call": "started by the On-call", "monitor": "monitor",
              "unattended": "self-driven"}
SHORT_PROMPT = 20  # characters: "yes", "go on", "what about the stand"
SPLIT_LABEL = {"direct": "direct", "helpers": "helper subagents", "pipeline": "pipeline"}
CLASSES = ("backend", "frontend", "mixed", "other")
GENERIC = {"main", "master", "develop", "release", "head", "feat", "fix", "docs", "chore", "refactor", "home",
           "user", "users", "root", "projects", "tmp", "local", "github", "com", "origin", "claude", "worktrees",
           "work", "factory", "canon", "test", "tests", "app", "data", "analytics", "summary", "week", "opt", "srv",
           "code", "repos", "src"}
MIN_TERM = 4

READING_NOTES = """**How to read.** One ISO week (Monday to Sunday, UTC) of the factory's own work, counted from the server's
session transcripts and the code host without a model: how far the factory takes the human out of the work. Projects
are letters; the same letter is the same project in every week. The factory itself (the plugin repository and the
factory's own repository) is not among them. Tokens are input + cache write + cache read + output; cache reads
are most of every total (a long session re-reads its context each turn), so compare totals with each other, not with a
price list. Turns are counted by who started them: the owner, the On-call (a client or colleague in the chat),
the production monitor, or nothing human — *self-driven* (scheduled runs, finished background tasks, pipelines,
messages between sessions). A turn's tokens are all the work up to the next prompt, so a one-word "yes" can start
hours of work: *started by the owner* is not work the owner did; the owner's involvement section shows what the owner
actually typed. Approximate, and said so: the On-call share (a request the On-call hands to a development session
counts as the On-call's, the owner's later prompts on it as the owner's); tokens per PR (a turn belongs to the PR its
session opened next, or whose branch it named); test cost inside sessions (the messages that ran the tests, not the
reading of their output). Sessions run in the cloud leave no transcript here and are not
counted."""


def letter(n: int) -> str:
    out = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        out = string.ascii_uppercase[r] + out
    return out


def letter_index(s: str) -> int:
    n = 0
    for ch in s:
        n = n * 26 + string.ascii_uppercase.index(ch) + 1
    return n - 1


def aliases(repos: list[str], path: Path, canon: str | None, write: bool = True,
            first: dict[str, str] | None = None) -> dict[str, str]:
    """Repository → label. Every repository keeps one letter for good (stored, in order of first appearance, ties by
    a hash — never by name, which would let anyone who knows the names map them); the canon is labelled at render
    time, so changing it never makes two labels equal."""
    stored = json.loads(path.read_text()) if path.is_file() else {}
    known = {r: v for r, v in stored.items() if v and set(v) <= set(string.ascii_uppercase)}
    nxt = max((letter_index(v) for v in known.values()), default=-1) + 1
    first = first or {}
    # the canon takes no letter while it is the canon; one that stops being it gets the next letter then
    for r in sorted(set(repos) - set(known) - {canon}, key=lambda r: (first.get(r) or "9999",
                                                            hashlib.sha256(r.encode()).hexdigest())):
        known[r] = letter(nxt)
        nxt += 1
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(known, indent=1, sort_keys=True))
    labels = {r: v for r, v in known.items() if r != canon}
    if canon in set(repos) | set(stored):
        labels[canon] = "canon"
    return dict(sorted(labels.items()))


def total(t: dict | None) -> int:
    return sum((t or {}).values())


def fmt(n: float) -> str:
    n = float(n)
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(n) >= size:
            return f"{n / size:.1f}{suffix}"
    return f"{n:.0f}" if n == int(n) else f"{n:.1f}"


def pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "—"


def table(head: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def anonymise(data: dict, alias: dict[str, str]) -> dict:
    labels = [alias.get(r, "?") for r, p in data["projects"].items() if not p.get("factory_repo")]
    if len(set(labels)) != len(labels):
        raise ValueError(f"two repositories share a label in {data['week']}: {sorted(labels)}")
    projects = {}
    for repo, p in data["projects"].items():
        if p.get("factory_repo"):
            continue  # the factory itself (the canon, a factory repository): measured, not reported
        q = {k: v for k, v in p.items() if k not in ("prs", "factory_repo")}
        q["prs"] = None if p.get("prs") is None else [
            {k: v for k, v in pr.items() if k not in ("number", "head")} for pr in p["prs"]]
        projects[alias.get(repo, "?")] = q
    return {"week": data["week"], "collected_at": data["collected_at"], "final": data["final"],
            "projects": dict(sorted(projects.items())), "factory": data.get("factory", {})}


def week_title(week: str) -> str:
    start, end = store.week_bounds(week)
    return f"{week} ({start:%d %b} – {end - timedelta(days=1):%d %b %Y})"


def render_week(data: dict, alias: dict[str, str]) -> tuple[str, dict]:
    anon = anonymise(data, alias)
    w, P = anon["week"], anon["projects"]
    out = [f"# Factory analytics — {week_title(w)}", ""]
    if not anon["final"]:
        out += [f"_Partial week: collected {anon['collected_at']}._", ""]
    out += [READING_NOTES, ""]
    new = [k for k, p in P.items() if p.get("first_week") == w]
    if new:
        out += [f"Projects new this week: {', '.join(new)}.", ""]

    out += ["## Who drove the work", ""]
    rows = []
    for k, p in P.items():
        tot = sum(total(t) for t in p["tokens"].values())
        rows.append([k, *(f"{fmt(total(p['tokens'].get(x)))} ({p['turns'][x]})" if p["turns"].get(x) else "—"
                          for x in KINDS), fmt(tot)])
    out += [table(["project", *(f"{KIND_LABEL[x]}: tokens (turns)" for x in KINDS), "total"], rows), ""]
    all_tok = {x: sum(total(p["tokens"].get(x)) for p in P.values()) for x in KINDS}
    whole = sum(all_tok.values())
    out += ["Share of tokens: " + ", ".join(f"{KIND_LABEL[x]} {pct(all_tok[x], whole)}" for x in KINDS) + ".", ""]

    out += ["## Pipeline against direct work", ""]
    rows = [[k, *(fmt(total(p["split"][s])) for s in SPLITS)] for k, p in P.items()]
    out += [table(["project", *(SPLIT_LABEL[s] for s in SPLITS)], rows), ""]
    prs = [pr for p in P.values() for pr in p["prs"] or []]
    costed = [pr for pr in prs if total(pr["tokens"])]
    piped = [total(pr["tokens"]) for pr in costed if pr["pipeline"]]
    plain = [total(pr["tokens"]) for pr in costed if not pr["pipeline"]]
    attributed = sum(total(pr["tokens"]) for pr in costed)
    rest = sum(total(p["unattributed"]) for p in P.values())
    out += [f"Merged PRs: {len(prs)}; with tokens traced to them: {len(costed)} "
            f"({pct(attributed, attributed + rest)} of the week's tokens). "
            f"Tokens per PR, medians — with the pipeline: {median(piped)}; without: {median(plain)}.", ""]

    out += ["## Backend against frontend", ""]
    rows = []
    for k, p in P.items():
        if p["prs"] is None:
            rows.append([k, *(["not available"] * (len(CLASSES) + 2))])
            continue
        n = {c: sum(1 for pr in p["prs"] if pr["cls"] == c) for c in CLASSES}
        lines = {s: sum(pr["lines"].get(s, 0) for pr in p["prs"]) for s in ("backend", "frontend")}
        rows.append([k, *(n[c] for c in CLASSES), fmt(lines["backend"]), fmt(lines["frontend"])])
    out += [table(["project", "backend PRs", "frontend PRs", "mixed", "other", "backend lines", "frontend lines"],
                  rows), ""]
    by_cls = {c: [total(pr["tokens"]) for pr in costed if pr["cls"] == c] for c in CLASSES}
    out += ["Tokens per PR (median, traced PRs): " + ", ".join(f"{c} {median(v)}" for c, v in by_cls.items()) + ".",
            ""]

    out += ["## Cost of tests", ""]
    rows = []
    for k, p in P.items():
        ci = p.get("ci")
        rows.append([k, ci["runs"] if ci else "—", fmt(ci["minutes"]) if ci else "—", p["tests"]["calls"],
                     fmt(total(p["tests"]["tokens"]))])
    out += [table(["project", "CI runs", "CI minutes", "test commands in sessions", "their tokens (approx.)"], rows), ""]

    out += ["## Delivery", ""]
    rows = []
    for k, p in P.items():
        d = p.get("delivery") or {}
        if p.get("prs") is None:
            rows.append([k, *(["n/a"] * 4)])
            continue
        lines = d.get("lines", {})
        rows.append([k, d.get("merged", 0), fmt(lines.get("backend", 0)), fmt(lines.get("frontend", 0)),
                     fmt(lines.get("tests", 0))])
    out += [table(["project", "merged PRs", "backend lines", "frontend lines", "test lines"], rows), "",
            "Lines: added + deleted, lock files and generated code left out.", ""]

    out += ["## The owner's involvement", ""]
    rows = []
    for k, p in P.items():
        lengths = (p.get("involvement") or {}).get("lengths", [])
        n_prs = len(p["prs"]) if p.get("prs") is not None else None
        rows.append([k, len(lengths), median_plain(lengths), sum(1 for x in lengths if x <= SHORT_PROMPT),
                     f"{len(lengths) / n_prs:.1f}" if n_prs else "—"])
    out += [table(["project", "owner prompts", "median length (chars)", f"short (≤{SHORT_PROMPT} chars)",
                   "prompts per merged PR"], rows), ""]

    out += ["## The owner's friction", ""]
    rows = []
    for k, p in P.items():
        f = p["friction"]
        per = lambda n: f"{100 * n / f['prompts']:.1f}" if f["prompts"] else "—"  # noqa: E731
        rows.append([k, f["prompts"], f["swear"], f["interrupts"], f["rejected"],
                     per(f["swear"] + f["interrupts"] + f["rejected"])])
    out += [table(["project", "owner prompts", "with swearing", "interrupts", "rejected tool calls",
                   "friction per 100 prompts"], rows), ""]

    out += ["## Parts", ""]
    rows = [[k, ", ".join(f"{part} {date}" for part, date in sorted(p.get("parts", {}).items(), key=lambda x: x[1]))
             or "—", (p.get("pins") or [["", "—"]])[-1][1]] for k, p in P.items()]
    out += [table(["project", "parts (first added)", "plugin release pinned"], rows), ""]
    events = week_events(anon)
    if events:
        out += ["This week: " + "; ".join(events) + ".", ""]
    return "\n".join(out), anon


MIN_COMPARE = 3


def median(values: list[int]) -> str:
    if not values:
        return "—"
    if len(values) < MIN_COMPARE:
        return f"too few to compare (n={len(values)})"
    v = sorted(values)
    mid = len(v) // 2
    return f"{fmt(v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2)} (n={len(v)})"


def median_plain(values: list[int]) -> str:
    if not values:
        return "—"
    v = sorted(values)
    mid = len(v) // 2
    return fmt(v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2)


def in_week(date: str, week: str) -> bool:
    try:
        return store.iso_week(f"{date}T12:00:00Z") == week
    except ValueError:
        return False


def tag_runs(tags: list[str]) -> list[str]:
    """v1 v2 v3 v5 → ["v1–v3", "v5"]."""
    nums = sorted(int(t[1:]) for t in tags)
    runs, out = [], []
    for n in nums:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    for a, b in runs:
        out.append(f"v{a}" if a == b else f"v{a}–v{b}")
    return out


def week_events(anon: dict) -> list[str]:
    w = anon["week"]
    ev = []
    for k, p in anon["projects"].items():
        items = ["first week"] if p.get("first_week") == w else []
        items += [part for part, date in sorted(p.get("parts", {}).items()) if in_week(date, w)]
        if items:
            ev.append(f"{k}: {', '.join(items)}")
    f = anon.get("factory", {})
    tags = [t for t, date in f.get("releases", {}).items() if in_week(date, w)]
    if tags:
        ev.append("plugin " + ", ".join(tag_runs(tags)))
    server = [part for part, date in sorted(f.get("server", {}).items()) if in_week(date, w)]
    if server:
        ev.append("server: " + ", ".join(server))
    return ev


def known(value, projects, field_name: str) -> str:
    """A sum over projects, marked where the code host was not available for some (never shown as a zero)."""
    missing = sum(1 for p in projects if p.get(field_name) is None)
    if not missing:
        return str(value)
    return "n/a" if missing == len(list(projects)) else f"{value} + n/a"


def render_summary(weeks: list[dict], alias: dict[str, str]) -> str:
    anon = [anonymise(d, alias) for d in sorted(weeks, key=lambda d: d["week"])]
    out = ["# Factory analytics — summary", "",
           "Every week the factory counts its own work; one file per week in this directory, this page is the series. "
           "How each number is counted: the top of any weekly file.", ""]
    rows = []
    for a in anon:
        P = a["projects"].values()
        kinds = {x: sum(total(p["tokens"].get(x)) for p in P) for x in KINDS}
        whole = sum(kinds.values())
        prs = [pr for p in P for pr in p["prs"] or []]
        fr = {k: sum(p["friction"][k] for p in P) for k in ("prompts", "swear", "interrupts", "rejected")}
        ci = sum((p.get("ci") or {}).get("minutes", 0) for p in P)
        rows.append([a["week"] + ("" if a["final"] else " (partial)"), fmt(whole),
                     *(pct(kinds[x], whole) for x in KINDS), known(len(prs), P, "prs"),
                     known(sum(1 for pr in prs if pr["cls"] == "backend"), P, "prs"),
                     known(sum(1 for pr in prs if pr["cls"] == "frontend"), P, "prs"),
                     known(fmt(ci), P, "ci"), fr["prompts"],
                     f"{fr['prompts'] / len(prs):.1f}" if prs else "—",
                     f"{100 * (fr['swear'] + fr['interrupts'] + fr['rejected']) / fr['prompts']:.1f}"
                     if fr["prompts"] else "—",
                     "; ".join(week_events(a)) or ""])
    out += [table(["week", "tokens", *(f"{KIND_LABEL[x]} share" for x in KINDS), "merged PRs", "backend",
                   "frontend", "CI minutes", "owner prompts", "owner prompts per merged PR", "friction / 100",
                   "what changed"], rows), ""]
    out += by_project(anon)
    first: dict[str, dict[str, str]] = {}
    for a in anon:
        for k, p in a["projects"].items():
            row = first.setdefault(k, {"first week": p.get("first_week") or a["week"]})
            for part, date in p.get("parts", {}).items():
                row[part] = store.iso_week(f"{date}T12:00:00Z")
    cols = sorted({c for r in first.values() for c in r if c != "first week"})
    out += ["## Parts", "", "The week each part first appeared in a project (installed, not necessarily in use).", "",
            table(["project", "first week", *cols], [[k, r["first week"], *(r.get(c, "—") for c in cols)]
                                                       for k, r in sorted(first.items())]), ""]
    rel = anon[-1].get("factory", {}).get("releases", {}) if anon else {}
    if rel:
        out += ["Plugin releases: " + ", ".join(f"{t} {d}" for t, d in sorted(rel.items(), key=lambda x: x[1])) + ".",
                ""]
    return "\n".join(out)


def project_week(first: str | None, week: str) -> int:
    if not first:
        return 1
    return (store.week_bounds(week)[0] - store.week_bounds(first)[0]).days // 7 + 1


def shares(p: dict) -> tuple[float, float, float, float]:
    """owner, On-call, self-driven (the monitor counted in) as fractions of the project's week, and its total."""
    t = {x: total(p["tokens"].get(x)) for x in KINDS}
    whole = sum(t.values())
    if not whole:
        return 0.0, 0.0, 0.0, 0
    return t["owner"] / whole, t["on_call"] / whole, (t["unattended"] + t["monitor"]) / whole, whole


def share_cell(x: float) -> str:
    return f"{100 * x:.0f}%" if x else "—"


def by_project(anon: list[dict]) -> list[str]:
    out = ["## By project", "",
           "How involvement changes as a project matures: each project's weeks, numbered from its first one. Shares "
           "are of the project's tokens that week (the monitor counted as self-driven).", ""]
    series: dict[str, list] = {}
    for a in anon:
        for k, p in a["projects"].items():
            series.setdefault(k, []).append((a, p))
    ages: dict[int, list] = {}
    for k in sorted(series):
        rows = []
        for a, p in series[k]:
            age = project_week(p.get("first_week"), a["week"])
            if age < 1:
                continue  # before the first transcript: the work is not observed, only its PRs
            own, call, self_driven, whole = shares(p)
            prompts = len((p.get("involvement") or {}).get("lengths", []))
            merged = len(p["prs"]) if p.get("prs") is not None else None
            per_pr = prompts / merged if merged else None
            d = p.get("delivery") or {}
            lines = sum(n for c, n in (d.get("lines") or {}).items() if c != "other")
            events = [e.split(": ", 1)[1] for e in week_events({**a, "projects": {k: p}, "factory": {}})]
            rows.append([a["week"], age, share_cell(own), share_cell(call), share_cell(self_driven),
                         f"{per_pr:.1f}" if per_pr is not None else "—", "n/a" if merged is None else merged,
                         fmt(lines), "; ".join(events)])
            if whole:
                ages.setdefault(age, []).append((own, call, self_driven, per_pr))
        out += [f"### {k}", "", table(["week", "project week", "started by the owner", "started by the On-call",
                                        "self-driven", "owner prompts per merged PR", "merged PRs",
                                        "code lines", "what changed"], rows), ""]
    out += ["## By project week", "",
            "All projects lined up by their age: the mean of each project's share in its N-th week.", ""]
    rows = []
    for age in sorted(ages):
        v = ages[age]
        mean = lambda i: sum(x[i] for x in v) / len(v)  # noqa: E731
        pr = [x[3] for x in v if x[3] is not None]
        rows.append([age, len(v), share_cell(mean(0)), share_cell(mean(1)), share_cell(mean(2)),
                     f"{sum(pr) / len(pr):.1f}" if pr else "—"])
    out += [table(["project week", "projects", "started by the owner", "started by the On-call", "self-driven",
                   "owner prompts per merged PR"], rows), ""]
    return out


def clean_terms(terms: set[str]) -> set[str]:
    return {t.lower() for t in terms if t and len(t) >= MIN_TERM and t.lower() not in GENERIC}


def leak_terms(checkouts: list[Path], weeks: list[dict], settings: store.Settings, canon_repo: str | None = None
               ) -> set[str]:
    """Every name the sources know, except the canon's own."""
    terms: set[str] = set()
    for c in checkouts:
        terms |= {c.name, *c.parts[1:]}
        repo = codehost.repo_of(c)
        if repo:
            terms |= {repo, *repo.split("/")}
    for d in weeks:
        for repo, p in d.get("projects", {}).items():
            terms |= {repo, *repo.split("/")}
            if repo == canon_repo:  # the canon's own branches name its own parts (feat/janitor), not a secret
                continue
            for pr in p.get("prs") or []:
                head = pr.get("head") or ""
                if "/" in head:
                    terms.add(head.split("/", 1)[1])
    host = socket.gethostname()
    terms |= {host, host.split(".")[0], *settings.owner}
    if canon_repo:
        terms -= {canon_repo, canon_repo.split("/")[1]}
    return clean_terms(terms)


def leak_check(files: dict[str, str], terms: set[str]) -> list[tuple[str, str]]:
    found = []
    for name, text in files.items():
        for t in sorted(terms):
            if re.search(r"(?<![\w-])" + re.escape(t) + r"(?![\w-])", text, re.I):
                found.append((name, t))
    return found
