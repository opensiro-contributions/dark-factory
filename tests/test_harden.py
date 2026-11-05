#!/usr/bin/env python3
"""Tests of harden.py, the controls inventory of the `harden` skill: python3 tests/test_harden.py
Each test builds a real temporary git repository with the controls under test."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "plugins" / "dark-factory" / "scripts"
sys.path.insert(0, str(HERE))
import harden  # noqa: E402

CI = """name: ci
on:
  pull_request:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: uv run ruff check .
  e2e:
    if: startsWith(github.ref, 'refs/tags/')
    steps:
      - run: make e2e
"""


class Repo:
    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def add(self) -> None:
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)

    def out(self) -> str:
        return harden.inventory(self.root)


class InventoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repo()
        self.addCleanup(self.repo.tmp.cleanup)

    def test_bare_project_reports_absence_not_failure(self) -> None:
        out = self.repo.out()
        self.assertIn("no `.claude/fences.env`", out)
        self.assertIn("settings.json: absent", out)
        self.assertIn("sandbox OFF", out)
        self.assertIn("no .github/workflows", out)
        self.assertIn("origin is not on GitHub", out)

    def test_solo_level_lists_what_the_guard_does_not_deny(self) -> None:
        self.repo.write(".claude/fences.env", "# level\nFENCE_LEVEL=solo\nQUOTA_FAIL_CLOSED=0\n")
        out = self.repo.out()
        self.assertIn("FENCE_LEVEL=solo", out)
        self.assertIn("force push", out)
        self.assertIn("NOT at this level (only from `standard`): `gh pr merge`", out)

    def test_standard_is_the_default_level(self) -> None:
        self.repo.write(".claude/fences.env", "QUOTA_FAIL_CLOSED=1\n")
        out = self.repo.out()
        self.assertIn("FENCE_LEVEL=standard (default)", out)
        self.assertIn("`gh pr merge` and the merge API: denied", out)
        self.assertNotIn("NOT at this level (only from `standard`)", out)

    def test_local_settings_switch_the_sandbox_off(self) -> None:
        self.repo.write(".claude/settings.json", json.dumps({
            "permissions": {"deny": ["Read(./.env*)"]}, "sandbox": {"enabled": True},
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "bash check.sh"}]}]}}))
        self.repo.write(".claude/settings.local.json", json.dumps({"sandbox": {"enabled": False}}))
        out = self.repo.out()
        self.assertIn("deny (1): Read(./.env*)", out)
        self.assertIn("hook PreToolUse [Bash]: bash check.sh", out)
        self.assertIn("sandbox OFF (local settings win)", out)

    def test_plugin_hooks_listed_when_enabled(self) -> None:
        self.repo.write(".claude/settings.json", json.dumps({"enabledPlugins": {"dark-factory@dark-factory": True}}))
        out = self.repo.out()
        self.assertIn("dark-factory plugin hooks:", out)
        self.assertIn("guard.py", out)

    def test_git_hook_shown_in_full(self) -> None:
        body = "#!/bin/sh\n# comment\n" + "".join(f"echo {i}\n" for i in range(30)) + "exit 0\n"
        self.repo.write(".githooks/pre-push", body)
        subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=self.repo.root, check=True)
        out = self.repo.out()
        self.assertIn("core.hooksPath: .githooks", out)
        self.assertIn("echo 29", out)
        self.assertNotIn("# comment", out)

    def test_ci_jobs_with_conditions(self) -> None:
        self.repo.write(".github/workflows/ci.yml", CI)
        out = self.repo.out()
        self.assertIn("triggers: on: pull_request:", out)
        self.assertIn("job `test`", out)
        self.assertIn("run: uv run ruff check .", out)
        self.assertIn("if: startsWith(github.ref, 'refs/tags/')", out)

    def test_linters_and_rule_checks_from_tracked_files_only(self) -> None:
        self.repo.write("pyproject.toml", '[tool.ruff.lint]\nselect = ["E", "BLE"]\n')
        self.repo.write("tests/test_architecture.py", "")
        self.repo.write("scripts/check_naming.py", "")
        self.repo.write("node_modules/x/package.json", '{"scripts": {"lint": "x"}}')
        self.repo.write("untracked/test_boundaries.py", "")
        self.repo.add()
        self.repo.write("untracked/test_boundaries.py", "")
        subprocess.run(["git", "rm", "-q", "--cached", "untracked/test_boundaries.py"], cwd=self.repo.root, check=True)
        out = self.repo.out()
        self.assertIn('select = ["E", "BLE"]', out)
        self.assertIn("tests/test_architecture.py", out)
        self.assertIn("scripts/check_naming.py", out)
        self.assertNotIn("node_modules", out)
        self.assertNotIn("test_boundaries", out)

    def test_env_files_are_never_read(self) -> None:
        self.repo.write(".env", "DATABASE_URL=postgres://user:hunter2@db/prod\n")
        self.repo.write(".claude/fences.env", "FENCE_LEVEL=solo\n")
        self.repo.add()
        self.assertNotIn("hunter2", self.repo.out())

    def test_main_refuses_a_non_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(harden.main(["harden.py", tmp]), 1)


if __name__ == "__main__":
    unittest.main()
