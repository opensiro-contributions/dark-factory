#!/usr/bin/env python3
"""Tests of secrets_scan.py, the inventory of the `secrets` skill: python3 tests/test_secrets_scan.py
Real temporary git repositories; HOME and CLAUDE_CONFIG_DIR are temporary too; gitleaks is faked or absent.
The one property every test checks: no secret value ever reaches the output."""

from __future__ import annotations

import json
import os
import shutil
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
        real_which = shutil.which
        mock.patch.object(scan.shutil, "which", lambda n, *a, **k: None if n in ("docker", "gitleaks", "sudo")
                          else real_which(n, *a, **k)).start()
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
        self.assertIn("none of the known secret shapes found", out)
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

    def run_fake_gitleaks(self, root: Path, findings: dict, code: int = 0) -> str:
        def fake_sh(args, cwd=None, timeout=60):
            if args and args[0] == "/fake/gitleaks":
                if code == 0:
                    Path(args[args.index("--report-path") + 1]).write_text(json.dumps(findings[args[1]]))
                return code, ""
            return real_sh(args, cwd, timeout)

        real_sh = scan.sh
        with mock.patch.dict(os.environ, {"GITLEAKS": "/fake/gitleaks"}), \
                mock.patch.object(scan.os, "access", lambda p, m: True), mock.patch.object(scan, "sh", fake_sh):
            return "\n".join(scan.gitleaks(root) or [])

    def test_m1_root_under_noise_named_dirs_keeps_findings(self) -> None:
        for parts in (("build", "p"), (".claude", "worktrees", "x")):
            root = Path(self.tmp.name).joinpath("o", *parts)
            root.mkdir(parents=True)
            f = [{"RuleID": "github-pat", "File": str(root / "app/cfg.py"), "StartLine": 1},
                 {"RuleID": "github-pat", "File": str(root / "node_modules/x.js"), "StartLine": 1}]
            out = self.run_fake_gitleaks(root, {"git": f, "dir": f})
            self.assertIn("1 finding(s)", out)
            self.assertIn("app/cfg.py: github-pat", out)
            self.assertNotIn("node_modules", out)

    def test_s1_dsn_user_token_is_hidden(self) -> None:
        tok = "glpat-" + "Ab1Cd2Ef3Gh4Ij5Kl6"
        got = scan.dsn_classes(f"https://{tok}:x-oauth-basic@git.example.com/r\n"
                               f"postgres://github_pat_{'A1' * 15}:pw123456@db.example.com/x\n"
                               f"postgres://{'Ab1' * 10}:pw123456@db.example.com/x\n"
                               "postgres://app_owner:pw123456@db.example.com/x")
        joined = "\n".join(got)
        self.assertNotIn(tok, joined)
        self.assertNotIn("github_pat", joined)
        self.assertNotIn("Ab1Ab1", joined)
        self.assertIn("https user ***", joined)
        self.assertIn("postgres user ***", joined)
        self.assertEqual(joined.count("user ***"), 2)
        self.assertIn("user `app_owner`", joined)

    def test_s2_env_names_skip_base64_and_multiline(self) -> None:
        text = ('A_KEY=1\nlower_name=2\nMIIBabcDEF1234==\nMixedCase=3\n'
                'PRIVATE="-----BEGIN KEY-----\nAAAAAAAAAAAA=\nREAL_LOOKING=oops\n-----END KEY-----"\n'
                'MULTI="first\nINSIDE_NAME=x\nend"\nAFTER=1\n')
        self.assertEqual(scan.env_names(text), ["AFTER", "A_KEY", "MULTI", "PRIVATE", "lower_name"])

    def test_s3_docker_ports(self) -> None:
        ports = "0.0.0.0:8080->80/tcp, :::8080->80/tcp, 127.0.0.1:5432->5432/tcp, 10.1.2.3:9000->9000/tcp, " \
                "[::1]:7000->7000/tcp, 6000->6000/udp, 3000/tcp"
        self.assertEqual(scan.docker_exposed(ports), ["6000/udp", "8080/tcp"])
        self.assertFalse(any(ch in " ".join(scan.docker_exposed(ports)) for ch in ".:"))

    def test_c1_host_kinds(self) -> None:
        for host, want in (("localhost", "local"), ("127.0.0.1", "local"), ("[::1]", "local"), ("10.2.3.4", "local"),
                           ("172.20.0.5", "local"), ("172.32.0.5", "REMOTE"), ("169.254.1.1", "local"),
                           ("host.docker.internal", "local"), ("db.example.com", "REMOTE"), ("8.8.8.8", "REMOTE"),
                           ("db", "unknown (short name)")):
            self.assertEqual(scan.host_kind(host), want, host)
        self.assertEqual(scan.dsn_classes("postgres://u:pw123456@[::1]:5432/d"), ["postgres user `u` → local host"])

    def test_c2_files_inside_ignored_directories(self) -> None:
        self.write(".gitignore", "secrets/\nnode_modules/\n", mode=0o644)
        self.write("secrets/id_rsa", "-----BEGIN OPENSSH PRIVATE KEY-----\nx\n")
        self.write("secrets/prod.env", f"DB_PASSWORD={PW}\n")
        self.write("node_modules/p/.env", "A=1\n")
        git(self.root, "add", ".gitignore")
        git(self.root, "commit", "-q", "-m", "x")
        out = self.out()
        self.assertIn("secrets/id_rsa — ignored", out)
        self.assertIn("secrets/prod.env — ignored", out)
        self.assertNotIn("node_modules", out)

    def test_c3_shapes(self) -> None:
        pat = "github_pat_" + "A1b2C3d4E5" * 3
        text = "\n".join([f'"password": "{PW}"', f"'client_secret': '{PW}9'", "password hunter2secret",
                          "db:5432:app:user:hunter2secret", pat, "glpat-" + "a1B2" * 6, "sk_live_" + "a1B2" * 6,
                          "AIza" + "a1B2" * 9, "eyJhbGciOi.eyJzdWIiOi.abcdefgh12"])
        kinds = " | ".join(scan.classify(text))
        for k in ("GitHub fine-grained token", "GitLab token", "Stripe live key", "Google API key", "JWT",
                  ".netrc password", ".pgpass entry", "client_secret", "password"):
            self.assertIn(k, kinds)
        self.assertTrue(scan.classify(f'"password": "{PW}"', strict=True))
        import harden
        for tok in (pat, "glpat-" + "a1B2" * 6, "sk_live_" + "a1B2" * 6, "AIza" + "a1B2" * 9,
                    "eyJhbGciOi.eyJzdWIiOi.abcdefgh12"):
            self.assertNotIn(tok, harden.mask(f"echo {tok} done"))

    def test_c3_file_regex_skips_source(self) -> None:
        self.assertFalse(scan.SECRET_FILE_RE.search("src/credentials.py"))
        self.assertFalse(scan.SECRET_FILE_RE.search("web/credentials.ts"))
        self.assertTrue(scan.SECRET_FILE_RE.search("aws/credentials"))

    def test_c4_compose_ports(self) -> None:
        text = ('services:\n  a:\n    ports:\n      - "5432:5432"   # db\n      - 8080:80/tcp\n'
                '      - "0.0.0.0:9000:9000"\n      - "127.0.0.1:6379:6379"\n      - 3000\n'
                '      # - "1111:1111"\n    command:\n      - 4000:4000\n')
        bare, explicit = scan.compose_ports(text)
        self.assertEqual(bare, ["5432:5432", "8080:80", "3000"])
        self.assertEqual(explicit, ["9000:9000"])
        self.write("docker-compose.yml", 'services:\n  a:\n    ports:\n      - "7777:7777"\n', mode=0o644)
        self.assertIn("docker-compose.yml: ports without a host IP (7777:7777)", self.out())  # untracked file too

    def test_c5_big_files_reported_not_clean(self) -> None:
        self.write(".env", "A=1\n")
        with mock.patch.object(scan, "read", lambda p, limit=2_000_000: None):
            out = scan.inventory(self.root)
        self.assertIn("not scanned (too large or unreadable)", out)

    def test_c6_non_utf8_names_and_section_guard(self) -> None:
        os.mkdir(os.path.join(os.fsencode(self.root), b"d\xff"))
        with open(os.path.join(os.fsencode(self.root), b"d\xff", b".env"), "w") as fh:
            fh.write("A=1\n")
        self.assertIsInstance(self.out(), str)
        with mock.patch.object(scan, "dumps", side_effect=RuntimeError("secret text " + PW)):
            out = self.out()
        self.assertIn("section failed: RuntimeError", out)
        self.assertNotIn("secret text", out)
        self.assertIn("## The machine", out)

    def test_c7_gitleaks_null_report_and_timeout(self) -> None:
        def fake_sh(args, cwd=None, timeout=60):
            if args and args[0] == "/fake/gitleaks":
                Path(args[args.index("--report-path") + 1]).write_text("null")
                return 0, ""
            return real_sh(args, cwd, timeout)

        real_sh = scan.sh
        with mock.patch.dict(os.environ, {"GITLEAKS": "/fake/gitleaks"}), \
                mock.patch.object(scan.os, "access", lambda p, m: True), mock.patch.object(scan, "sh", fake_sh):
            out = "\n".join(scan.gitleaks(self.root))
        self.assertIn("0 finding(s)", out)
        out = self.run_fake_gitleaks(self.root, {}, code=124)
        self.assertIn("timed out", out)
        self.assertNotIn("127", out)

    def test_c8_long_blob_is_fast(self) -> None:
        import time
        for blob in ("a" * 50_000, "token" * 10_000, "x-" * 25_000, "a+" * 25_000, "password_" * 6_000):
            t = time.time()
            scan.classify(blob)
            scan.classify(blob, strict=True)
            scan.dsn_classes(blob)
            self.assertLess(time.time() - t, 2.0)

    def test_c9_dump_cap_and_unreadable_config(self) -> None:
        d = self.home / "db-backup"
        d.mkdir()
        for i in range(520):
            (d / f"{i}.sql").write_text("x")
        self.assertIn("500 dump file(s)", "\n".join(scan.dumps()))
        locked = self.home / ".config" / "locked"
        locked.mkdir()
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        self.assertIsInstance(scan.home_files(), list)
        self.assertNotIn("sudo -n", Path(scan.__file__).read_text())

    def test_main_refuses_a_non_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(scan.main(["secrets_scan.py", tmp]), 1)


if __name__ == "__main__":
    unittest.main()
