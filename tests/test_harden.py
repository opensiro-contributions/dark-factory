#!/usr/bin/env python3
"""Tests of harden.py, the controls inventory of the `harden` skill: python3 tests/test_harden.py
Each test builds a real temporary git repository with the controls under test; the user-level settings
directory is a temporary one too (CLAUDE_CONFIG_DIR)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1] / "plugins" / "dark-factory" / "scripts"
sys.path.insert(0, str(HERE))
import harden  # noqa: E402

CI = """name: ci
on:
  pull_request:
# a comment at column 0 inside the trigger block
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    env:
      name: not-a-step
    steps:
      - uses: actions/checkout@v4
      - run: uv run ruff check .
      - name: tests
        run: |
          cd backend
          uv run pytest -q
  e2e:
    if: startsWith(github.ref, 'refs/tags/')
    steps:
      - run: make e2e
"""


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class Repo:
    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "proj"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@example.invalid")
        git(self.root, "config", "user.name", "t")

    def write(self, rel: str, text: str, root: Path | None = None) -> Path:
        path = (root or self.root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def commit(self, root: Path | None = None) -> None:
        git(root or self.root, "add", "-A")
        git(root or self.root, "commit", "-q", "-m", "x", "--allow-empty")

    def out(self, root: Path | None = None) -> str:
        return harden.inventory(root or self.root)


class InventoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repo()
        self.addCleanup(self.repo.tmp.cleanup)
        self.home = Path(self.repo.tmp.name) / "claude-home"
        self.home.mkdir()
        patcher = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_bare_project_reports_absence_not_failure(self) -> None:
        out = self.repo.out()
        self.assertIn("no `.claude/fences.env` here or on origin's main branch", out)
        self.assertIn("project: settings.json: absent", out)
        self.assertIn("sandbox OFF (set by: default", out)
        self.assertIn("no .github/workflows", out)
        self.assertIn("origin is not on GitHub", out)

    def test_solo_level_lists_what_the_guard_does_not_deny(self) -> None:
        self.repo.write(".claude/fences.env", "# level\nFENCE_LEVEL = solo\nQUOTA_FAIL_CLOSED=0\n")
        out = self.repo.out()
        self.assertIn("effective FENCE_LEVEL=solo", out)
        self.assertIn("force push", out)
        self.assertIn("NOT at this level (only from `standard`): denied: `gh pr merge`", out)

    def test_standard_is_the_default_level(self) -> None:
        self.repo.write(".claude/fences.env", "QUOTA_FAIL_CLOSED=1\n")
        out = self.repo.out()
        self.assertIn("effective FENCE_LEVEL=standard", out)
        self.assertIn("denied: `gh pr merge` and the merge API", out)
        self.assertNotIn("NOT at this level (only from `standard`)", out)

    def test_unknown_level_is_named_and_treated_as_standard(self) -> None:
        self.repo.write(".claude/fences.env", "FENCE_LEVEL=bogus\n")
        out = self.repo.out()
        self.assertIn("FENCE_LEVEL=bogus: unknown, the guard treats it as `standard`", out)
        self.assertIn("effective FENCE_LEVEL=standard", out)

    def test_origin_main_floor_wins_over_a_lower_working_tree(self) -> None:
        remote = Path(self.repo.tmp.name) / "remote.git"
        git(Path(self.repo.tmp.name), "init", "-q", "--bare", str(remote))
        self.repo.write(".claude/fences.env", "FENCE_LEVEL=team\n")
        self.repo.commit()
        git(self.repo.root, "remote", "add", "origin", str(remote))
        git(self.repo.root, "push", "-q", "origin", "main")
        git(self.repo.root, "fetch", "-q", "origin")
        self.repo.write(".claude/fences.env", "FENCE_LEVEL=solo\n")
        out = self.repo.out()
        self.assertIn("effective FENCE_LEVEL=team", out)
        self.assertIn("origin's main branch is stricter and wins", out)
        self.assertNotIn("NOT at this level", out)

    def test_settings_scopes_and_effective_sandbox(self) -> None:
        self.repo.write("settings.json", json.dumps({"permissions": {"deny": ["Bash(rm -rf:*)"]}}), root=self.home)
        self.repo.write(".claude/settings.json", json.dumps({
            "permissions": {"deny": ["Read(./.env*)"]}, "sandbox": {"enabled": True},
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "bash check.sh"}]}]}}))
        self.repo.write(".claude/settings.local.json", json.dumps({"sandbox": {"enabled": False}}))
        out = self.repo.out()
        self.assertIn("user: ~/.claude/settings.json", out)
        self.assertIn("deny (1): Bash(rm -rf:*)", out)
        self.assertIn("deny (1): Read(./.env*)", out)
        self.assertIn("hook PreToolUse [Bash]: bash check.sh", out)
        self.assertIn("sandbox OFF (set by: local", out)

    def test_malformed_settings_do_not_crash(self) -> None:
        self.repo.write(".claude/settings.json", json.dumps({"permissions": ["x"], "hooks": {"PreToolUse": "x"},
                                                             "enabledPlugins": []}))
        self.repo.write(".claude/settings.local.json", "[1, 2]")
        out = self.repo.out()
        self.assertIn("deny (0)", out)
        self.assertIn("local: settings.local.json: unreadable or not a JSON object", out)

    def test_plugin_hooks_listed_when_enabled_and_warned_when_not(self) -> None:
        self.repo.write(".claude/fences.env", "FENCE_LEVEL=solo\n")
        self.assertIn("the dark-factory plugin is not enabled", self.repo.out())
        self.repo.write(".claude/settings.json", json.dumps({"enabledPlugins": {"dark-factory@dark-factory": True}}))
        out = self.repo.out()
        self.assertIn("dark-factory plugin hooks:", out)
        self.assertIn("guard.py", out)

    def test_git_hook_shown_without_comments_and_flagged_when_not_executable(self) -> None:
        body = "#!/bin/sh\n# comment\n" + "".join(f"echo {i}\n" for i in range(30)) + "exit 0\n"
        hook = self.repo.write(".githooks/pre-push", body)
        hook.chmod(0o755)
        self.repo.write(".githooks/pre-commit", "echo hi\n").chmod(0o644)
        git(self.repo.root, "config", "core.hooksPath", ".githooks")
        out = self.repo.out()
        self.assertIn("hooks directory: .githooks", out)
        self.assertIn("echo 29", out)
        self.assertNotIn("# comment", out)
        self.assertIn("pre-commit (1 lines without comments) — NOT EXECUTABLE", out)
        self.assertNotIn("pre-push (32 lines without comments) — NOT", out)

    def test_default_hooks_found_from_a_worktree(self) -> None:
        self.repo.commit()
        self.repo.write(".git/hooks/pre-commit", "echo from-common-dir\n").chmod(0o755)
        wt = Path(self.repo.tmp.name) / "wt"
        git(self.repo.root, "worktree", "add", "-q", "-b", "feat/x", str(wt))
        out = self.repo.out(wt)
        self.assertIn("echo from-common-dir", out)

    def test_ci_jobs_triggers_conditions_and_multiline_run(self) -> None:
        self.repo.write(".github/workflows/ci.yml", CI)
        out = self.repo.out()
        self.assertIn("triggers: on: pull_request: branches: [main]", out)
        self.assertIn("job `test`", out)
        self.assertIn("run: uv run ruff check .", out)
        self.assertIn("run: cd backend ⏎ uv run pytest -q", out)
        self.assertIn("if: startsWith(github.ref, 'refs/tags/')", out)
        self.assertNotIn("not-a-step", out)

    def test_linters_and_rule_checks_from_tracked_files_only(self) -> None:
        self.repo.write("pyproject.toml", '[tool.ruff.lint]\nselect = ["E", "BLE"]\n')
        self.repo.write(".pre-commit-config.yaml", "repos:\n  - repo: https://github.com/astral-sh/ruff-pre-commit\n"
                                                   "    hooks:\n      - id: ruff\n")
        self.repo.write("tests/test_architecture.py", "")
        self.repo.write("scripts/check_naming.py", "")
        self.repo.write("node_modules/x/package.json", '{"scripts": {"lint": "x"}}')
        self.repo.commit()
        self.repo.write("untracked/test_boundaries.py", "")
        out = self.repo.out()
        self.assertIn('select = ["E", "BLE"]', out)
        self.assertIn("id: ruff", out)
        self.assertIn("tests/test_architecture.py", out)
        self.assertIn("scripts/check_naming.py", out)
        self.assertNotIn("node_modules", out)
        self.assertNotIn("test_boundaries", out)

    def test_tracked_file_missing_from_the_tree_does_not_crash(self) -> None:
        self.repo.write("pyproject.toml", "[tool.ruff]\n")
        self.repo.write("päckage/package.json", '{"scripts": {"lint": "eslint ."}}')
        self.repo.commit()
        (self.repo.root / "pyproject.toml").unlink()
        out = self.repo.out()
        self.assertIn("päckage/package.json", out)

    def test_secrets_never_read_and_tokens_masked(self) -> None:
        self.repo.write(".env", "DATABASE_URL=postgres://user:hunter2@db/prod\n")
        self.repo.write(".claude/settings.json", json.dumps({"permissions": {"allow": [
            "Bash(curl -H 'Authorization: Bearer abcd1234efgh' https://x)",
            "Bash(psql postgres://app:s3cretpw@db/prod)",
            "Bash(gh auth login --with-token ghp_" + "a" * 36 + ")"]}}))
        self.repo.commit()
        out = self.repo.out()
        for leaked in ("hunter2", "abcd1234efgh", "s3cretpw", "ghp_aaaa"):
            self.assertNotIn(leaked, out)
        self.assertIn("Bearer ***", out)
        self.assertIn("postgres://app:***@db/prod", out)

    def test_main_refuses_a_non_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(harden.main(["harden.py", tmp]), 1)


if __name__ == "__main__":
    unittest.main()
