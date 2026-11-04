#!/usr/bin/env python3
"""Tests of vulns.py, the daily vulnerability check: python3 tests/test_vulns.py
git is a real temporary repository; the OSV and CISA endpoints are a fake in-process function."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1] / "plugins" / "dark-factory" / "scripts"
sys.path.insert(0, str(HERE))
import vulns  # noqa: E402

UV_LOCK = """version = 1
requires-python = ">=3.11"

[[package]]
name = "jinja2"
version = "2.4.1"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "shop"
version = "0.1.0"
source = { editable = "." }
"""

PACKAGE_LOCK = json.dumps({"lockfileVersion": 3, "packages": {
    "": {"name": "front", "version": "1.0.0"},
    "node_modules/lodash": {"version": "4.17.15"},
    "node_modules/@scope/pkg/node_modules/left-pad": {"version": "1.0.0"},
    "node_modules/local": {"link": True, "resolved": "../local"},
}})

# bun.lock is JSON with trailing commas
BUN_LOCK = """{
  "lockfileVersion": 1,
  "workspaces": { "": { "name": "channel", "dependencies": { "hono": "^4", }, }, },
  "packages": {
    "@hono/node-server": ["@hono/node-server@2.1.5", "", {}, "sha512-x"],

    "hono": ["hono@4.6.0", "", {}, "sha512-y"],
    "ws-local": ["ws-local@workspace:packages/ws", ""],
  },
}
"""

VULNS = {
    "GHSA-crit": {"id": "GHSA-crit", "aliases": ["CVE-2026-0001", "PYSEC-2026-1"],
                  "summary": "Sandbox escape", "database_specific": {"severity": "CRITICAL"},
                  "affected": [{"package": {"ecosystem": "PyPI", "name": "jinja2"},
                                "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"},
                                                                           {"fixed": "2.11.3"}]}]}]},
    "PYSEC-2026-1": {"id": "PYSEC-2026-1", "aliases": ["GHSA-crit", "CVE-2026-0001"], "summary": "same one"},
    "GHSA-high": {"id": "GHSA-high", "aliases": ["CVE-2020-28500"], "summary": "ReDoS",
                  "database_specific": {"severity": "HIGH"}, "affected": []},
    "GHSA-kev": {"id": "GHSA-kev", "aliases": ["CVE-2021-44228"], "summary": "exploited",
                 "database_specific": {"severity": "MODERATE"}, "affected": []},
}

QUERY_RESULTS = {("PyPI", "jinja2", "2.4.1"): ["GHSA-crit", "PYSEC-2026-1"],
                 ("npm", "lodash", "4.17.15"): ["GHSA-high"],
                 ("npm", "hono", "4.6.0"): ["GHSA-kev"]}

KEV = {"vulnerabilities": [{"cveID": "CVE-2021-44228"}]}


class FakeNet:
    def __init__(self, results=None):
        self.results = QUERY_RESULTS if results is None else results
        self.batches: list[list] = []

    def __call__(self, url: str, body: dict | None = None):
        if url.endswith("/querybatch"):
            self.batches.append(body["queries"])
            out = []
            for q in body["queries"]:
                key = (q["package"]["ecosystem"], q["package"]["name"], q["version"])
                out.append({"vulns": [{"id": i} for i in self.results.get(key, [])]} if key in self.results else {})
            return {"results": out}
        if "/vulns/" in url:
            return VULNS[url.rsplit("/", 1)[1]]
        if url == vulns.KEV_URL:
            return KEV
        raise AssertionError(f"unexpected url {url}")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


class Repo:
    """origin (bare) and the project's main checkout cloned from it."""

    def __init__(self, files: dict[str, str]):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.origin, self.work, self.path = root / "origin.git", root / "work", root / "shop"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.work)], check=True, capture_output=True)
        self.commit(files)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.path)], check=True, capture_output=True)

    def commit(self, files: dict[str, str]) -> None:
        for name, text in files.items():
            f = self.work / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
        git(self.work, "add", "-A")
        git(self.work, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "c")
        git(self.work, "push", "-q", "origin", "HEAD:main")

    def close(self):
        self.tmp.cleanup()


def quiet(fn, *args, **kw):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*args, **kw)


class ParseTest(unittest.TestCase):
    def test_uv_lock_takes_registry_packages_only(self):
        self.assertEqual(vulns.parse_lockfile("backend/uv.lock", UV_LOCK), [("PyPI", "jinja2", "2.4.1")])

    def test_requirements_pins_only(self):
        text = "# deps\nrequests==2.31.0\nuvicorn[standard]==0.30.1 ; python_version>='3.11'\nflask>=2\n-r base.txt\n"
        self.assertEqual(vulns.parse_lockfile("requirements.txt", text),
                         [("PyPI", "requests", "2.31.0"), ("PyPI", "uvicorn", "0.30.1")])

    def test_package_lock_nested_and_links(self):
        self.assertEqual(vulns.parse_lockfile("frontend/package-lock.json", PACKAGE_LOCK),
                         [("npm", "lodash", "4.17.15"), ("npm", "left-pad", "1.0.0")])

    def test_package_lock_v1(self):
        text = json.dumps({"lockfileVersion": 1, "dependencies": {
            "a": {"version": "1.0.0", "dependencies": {"b": {"version": "2.0.0"}}}}})
        self.assertEqual(vulns.parse_lockfile("package-lock.json", text), [("npm", "a", "1.0.0"), ("npm", "b", "2.0.0")])

    def test_bun_lock_trailing_commas_scoped_names_workspaces(self):
        self.assertEqual(vulns.parse_lockfile("channel/bun.lock", BUN_LOCK),
                         [("npm", "@hono/node-server", "2.1.5"), ("npm", "hono", "4.6.0")])

    def test_broken_lockfile_raises(self):
        with self.assertRaises(vulns.LockfileError):
            vulns.parse_lockfile("uv.lock", "not = [toml")

    def test_lockfile_names(self):
        self.assertTrue(vulns.is_lockfile("app/requirements-dev.txt"))
        self.assertTrue(vulns.is_lockfile("uv.lock"))
        self.assertFalse(vulns.is_lockfile("web/node_modules/x/package-lock.json"))
        self.assertFalse(vulns.is_lockfile("pyproject.toml"))


class ClassifyTest(unittest.TestCase):
    def test_aliases_collapse_and_severity_comes_from_the_ghsa(self):
        found = vulns.lookup([("PyPI", "jinja2", "2.4.1", "backend/uv.lock")], FakeNet())
        self.assertEqual(len(found), 1)
        f = found[0]
        self.assertEqual((f["id"], f["severity"], f["fixed"]), ("GHSA-crit", "CRITICAL", "2.11.3"))
        self.assertEqual(f["cve"], ["CVE-2026-0001"])
        self.assertTrue(f["critical"])

    def test_kev_makes_any_severity_critical(self):
        found = vulns.lookup([("npm", "hono", "4.6.0", "bun.lock"), ("npm", "lodash", "4.17.15", "package-lock.json")],
                             FakeNet())
        by_id = {f["id"]: f for f in found}
        self.assertTrue(by_id["GHSA-kev"]["critical"] and by_id["GHSA-kev"]["kev"])
        self.assertFalse(by_id["GHSA-high"]["critical"])

    def test_min_level_high(self):
        found = vulns.lookup([("npm", "lodash", "4.17.15", "package-lock.json")], FakeNet(), min_level="high")
        self.assertTrue(found[0]["critical"])

    def test_one_line_per_package_with_the_highest_fix(self):
        f = {"ecosystem": "PyPI", "package": "pypdf", "version": "6.21.2", "files": ["uv.lock"], "cve": [],
             "summary": "", "kev": False, "critical": False}
        found = [{**f, "id": "GHSA-a", "severity": "HIGH", "fixed": "6.21.3"},
                 {**f, "id": "GHSA-b", "severity": "MODERATE", "fixed": "6.21.10"},
                 {**f, "id": "GHSA-c", "severity": "LOW", "fixed": ""}]
        text = vulns.message(Path("/srv/shop"), "origin/main", found)
        self.assertEqual(len(text.splitlines()), 2)
        self.assertIn("HIGH pypdf 6.21.2 → 6.21.10", text)  # 10 > 3: compared as numbers
        self.assertIn("3 advisories, 1 without a fix yet", text)
        self.assertIn("in 1 package", text)

    def test_queries_are_batched_and_deduplicated(self):
        net = FakeNet()
        with mock.patch.object(vulns, "BATCH", 2):
            vulns.lookup([("npm", "a", "1", "x"), ("npm", "a", "1", "y"), ("npm", "b", "1", "x"),
                          ("npm", "c", "1", "x")], net)
        self.assertEqual([len(b) for b in net.batches], [2, 1])


class RunTest(unittest.TestCase):
    def setUp(self):
        self.repo = Repo({"backend/uv.lock": UV_LOCK, "frontend/package-lock.json": PACKAGE_LOCK,
                          "README.md": "shop\n"})
        self.p = self.repo.path
        self.sent: list[str] = []
        patcher = mock.patch.object(vulns, "notify", lambda text: self.sent.append(text) or 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.repo.close)

    def run_check(self, net=None, **kw):
        return quiet(vulns.check, self.p, net or FakeNet(), **kw)

    def test_new_findings_are_told_once(self):
        self.assertEqual(self.run_check(), 0)
        self.assertEqual(len(self.sent), 1)
        text = self.sent[0]
        self.assertIn("critical", text.lower())
        self.assertIn("jinja2 2.4.1", text)
        self.assertIn("jinja2 2.4.1 → 2.11.3", text)
        self.assertIn("backend/uv.lock", text)
        self.assertIn("lodash 4.17.15", text)
        self.assertLess(text.index("jinja2"), text.index("lodash"))  # critical first
        self.run_check()
        self.assertEqual(len(self.sent), 1)  # the same findings are not told again
        state = vulns.read_state(self.p)
        self.assertEqual((state["latest"]["critical"], state["latest"]["total"]), (1, 2))

    def test_a_new_finding_later_is_told_alone(self):
        self.run_check(FakeNet({("PyPI", "jinja2", "2.4.1"): ["GHSA-crit"]}))
        self.run_check()
        self.assertEqual(len(self.sent), 2)
        self.assertIn("lodash", self.sent[1])
        self.assertNotIn("jinja2", self.sent[1])

    def test_quiet_day_sends_nothing(self):
        self.assertEqual(self.run_check(FakeNet({})), 0)
        self.assertEqual(self.sent, [])
        self.assertEqual(vulns.read_state(self.p)["latest"]["total"], 0)

    def test_scans_origin_main_not_the_checkout(self):
        self.repo.commit({"backend/uv.lock": UV_LOCK.replace("2.4.1", "3.1.6")})  # fixed on main, checkout behind
        self.run_check()
        self.assertNotIn("jinja2", self.sent[0])
        self.assertIn("lodash", self.sent[0])

    def test_dry_run_writes_and_sends_nothing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            vulns.check(self.p, FakeNet(), dry=True)
        self.assertIn("jinja2", out.getvalue())
        self.assertEqual(self.sent, [])
        self.assertEqual(vulns.read_state(self.p), {})

    def test_undelivered_notice_is_retried(self):
        with mock.patch.object(vulns, "notify", lambda text: 1):
            self.run_check()
        self.run_check()
        self.assertEqual(len(self.sent), 1)

    def test_osv_down_keeps_state_and_says_so(self):
        def down(url, body=None):
            raise OSError("network unreachable")
        self.run_check()
        self.assertEqual(self.run_check(down), 0)  # not a unit failure every day: the status line shows it
        state = vulns.read_state(self.p)
        self.assertFalse(state["latest"]["ok"])
        self.assertIn("network unreachable", state["latest"]["text"])
        self.assertTrue(state["seen"])

    def test_broken_lockfile_is_reported_and_others_still_scanned(self):
        self.repo.commit({"backend/uv.lock": "broken = ["})
        self.run_check()
        self.assertIn("lodash", self.sent[0])
        self.assertIn("backend/uv.lock", vulns.read_state(self.p)["latest"]["text"])

    def test_no_lockfiles_is_fine(self):
        repo = Repo({"README.md": "x\n"})
        self.addCleanup(repo.close)
        self.assertEqual(quiet(vulns.check, repo.path, FakeNet()), 0)
        self.assertEqual(vulns.read_state(repo.path)["latest"]["text"], "no lock files on origin/main")

    def test_min_level_from_session_env(self):
        env = self.p / ".claude/factory/session.env"
        env.parent.mkdir(parents=True)
        env.write_text('VULN_AUTOFIX_MIN="high"\n')
        self.run_check()
        self.assertEqual(vulns.read_state(self.p)["latest"]["critical"], 2)


if __name__ == "__main__":
    unittest.main()
