#!/usr/bin/env python3
"""Tests of secrets_scan.py, the inventory of the `secrets` skill: python3 tests/test_secrets_scan.py
Real temporary git repositories; HOME and CLAUDE_CONFIG_DIR are temporary too; gitleaks is faked or absent.
The one property every test checks: no secret value ever reaches the output."""

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
import secrets_scan as scan  # noqa: E402

PW = "Zq8vN3rT7wX2pL5m"                       # the fake secrets the output must never contain
GH = "ghp_" + "Ab1" * 12
SK = "sk-" + "Xy9" * 10
REMOTE_DSN = f"postgresql://app_owner:{PW}@db.example.com:5432/prod"
LOCAL_DSN = "postgresql://postgres:postgres@localhost:5432/test"


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class ScanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home = base / "home"
        (self.home / ".config").mkdir(parents=True)
        self.root = base / "proj"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@example.invalid")
        git(self.root, "config", "user.name", "t")
        env = {"HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.home / ".claude"), "GITLEAKS": "",
               "PATH": "/usr/bin:/bin"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        mock.patch.object(Path, "home", lambda: self.home).start()
        self.addCleanup(mock.patch.stopall)

    def write(self, rel: str, text: str, base: Path | None = None, mode: int = 0o600) -> Path:
        p = (base or self.root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        p.chmod(mode)
        return p

    def out(self) -> str:
        text = scan.inventory(self.root)
        for value in (PW, GH, SK, "hunter2secret"):
            self.assertNotIn(value, text)
        return text

    def test_env_file_states_and_classes(self) -> None:
        self.write(".gitignore", ".env\n", mode=0o644)
        self.write(".env", f"DATABASE_URL={REMOTE_DSN}\nOPENAI_API_KEY={SK}\nDEBUG=1\n")
        self.write("config/prod.env", f"GITHUB_TOKEN={GH}\n", mode=0o644)
        self.write("docker/.env.example", "DATABASE_URL=\n", mode=0o644)
        git(self.root, "add", ".gitignore")
        git(self.root, "commit", "-q", "-m", "x")
        out = self.out()
        self.assertIn(".env — ignored", out)
        self.assertIn("postgresql user `app_owner` → REMOTE host", out)
        self.assertIn("variables: DATABASE_URL, DEBUG, OPENAI_API_KEY", out)
        self.assertIn("config/prod.env — untracked and NOT ignored", out)
        self.assertIn("world-readable", out)
        self.assertNotIn(".env.example", out)
        self.assertNotIn("db.example.com", out)

    def test_builtin_scan_without_gitleaks_is_strict(self) -> None:
        self.write("src/settings.py", f'api_key = settings.OPENAI_KEY\nTOKEN = "{PW}1"\nURL = "{LOCAL_DSN}"\n',
                   mode=0o644)
        self.write("deploy/run.sh", f"psql {REMOTE_DSN}\n", mode=0o644)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "x")
        out = self.out()
        self.assertIn("gitleaks is not installed", out)
        self.assertIn("src/settings.py: hard-coded secret literal ×1", out)
        self.assertIn("deploy/run.sh: postgresql user `app_owner` → REMOTE host", out)
        self.assertNotIn("postgres` → local", out.split("## Secret files")[0])

    def test_gitleaks_findings_grouped_and_noise_dirs_dropped(self) -> None:
        findings = {
            "git": [{"RuleID": "github-pat", "File": "app/cfg.py", "StartLine": 3, "Commit": "abcdef1234"},
                    {"RuleID": "generic-api-key", "File": "docs/a.md", "StartLine": 9, "Commit": "1234567890"}],
            "dir": [{"RuleID": "generic-api-key", "File": str(self.root / ".venv/lib/x.py"), "StartLine": 1},
                    {"RuleID": "openai-api-key", "File": str(self.root / ".env"), "StartLine": 2}],
        }

        def fake_sh(args, cwd=None, timeout=60):
            if args and args[0] == "/fake/gitleaks":
                mode = args[1]
                Path(args[args.index("--report-path") + 1]).write_text(json.dumps(findings[mode]))
                return 0, ""
            return real_sh(args, cwd, timeout)

        real_sh = scan.sh
        with mock.patch.dict(os.environ, {"GITLEAKS": "/fake/gitleaks"}), \
                mock.patch.object(scan.os, "access", lambda p, m: True), mock.patch.object(scan, "sh", fake_sh):
            out = self.out()
        self.assertIn("gitleaks git (git history): 2 finding(s)", out)
        self.assertIn("app/cfg.py: github-pat ×1; lines 3; commits abcdef12", out)
        self.assertIn("generic-api-key only (often config names: check): 1 file(s)", out)
        self.assertIn("working tree, ignored files included): 1 finding(s)", out)
        self.assertIn(".env: openai-api-key ×1", out)
        self.assertNotIn(".venv", out)

    def test_home_files_mcp_and_history(self) -> None:
        self.write(".config/app/prod-db.env", f"DSN={REMOTE_DSN}\n", base=self.home)
        self.write(".config/app/flags.env", "FEATURE_X=1\n", base=self.home, mode=0o664)
        self.write(".claude/.claude.json", json.dumps({"projects": {str(self.root): {"mcpServers": {
            "host": {"type": "http", "url": "https://x", "headers": {"Authorization": f"Bearer {PW}"}},
            "db": {"command": "x", "env": {"DB_PASSWORD": "hunter2secret"}}}}}}), base=self.home)
        self.write(".bash_history", f"export GITHUB_TOKEN={GH}\nls\n", base=self.home)
        out = self.out()
        self.assertIn("~/.config/app/prod-db.env", out)
        self.assertIn("app_owner` → REMOTE host", out)
        self.assertIn("~/.config/app/flags.env", out)
        self.assertIn("no secret-shaped values found", out)
        self.assertIn("MCP `host` (project proj): credentials in header Authorization", out)
        self.assertIn("MCP `db` (project proj): credentials in DB_PASSWORD", out)
        self.assertIn("~/.bash_history: GitHub token ×1", out)

    def test_compose_ports_and_dumps(self) -> None:
        self.write("docker-compose.yml", 'services:\n  db:\n    ports:\n      - "5432:5432"\n      - "127.0.0.1:6379:6379"\n',
                   mode=0o644)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "x")
        self.write("prod-dumps/db.sql.gz", "x", base=self.home, mode=0o644)
        out = self.out()
        self.assertIn("docker-compose.yml: ports without a host IP (5432:5432)", out)
        self.assertNotIn("6379:6379)", out)
        self.assertIn("~/prod-dumps/: 1 dump file(s), 1 owned by this user (deletable by it), 1 world-readable", out)

    def test_classify_names_keys_never_values(self) -> None:
        kinds = scan.classify(f"API_TOKEN={PW}\npassword: {PW}x\n")
        self.assertTrue(any("API_TOKEN" in k and "password" in k for k in kinds), kinds)
        self.assertNotIn(PW, " ".join(kinds))

    def test_main_refuses_a_non_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(scan.main(["secrets_scan.py", tmp]), 1)


if __name__ == "__main__":
    unittest.main()
