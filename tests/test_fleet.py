#!/usr/bin/env python3
"""Tests for fleet.py: python3 tests/test_fleet.py"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "dark-factory" / "scripts"))
import fleet  # noqa: E402

REGISTRY = """# Registry

| Project | Tracker id | Repository | Client |
|---|---|---|---|
| _Example (delete this row):_ Panel | `proj-a1b` | `~/panel` (github.com/o/panel) | Example Ltd |
| Shop | `shop-1` | `~/code/shop` (github.com/o/shop) | Shop Ltd |
| Audit hub | `hub` | — | Client |
| **Billing** | `bill` | `/srv/billing` | Client |

| Other table | Repository |
|---|---|
| Notes | `~/notes` |
"""


class ParseTest(unittest.TestCase):
    def test_registry_rows_with_paths_only(self):
        rows = fleet.parse_registry(REGISTRY)
        self.assertEqual(rows, [("Shop", "~/code/shop"), ("Billing", "/srv/billing"), ("Notes", "~/notes")])

    def test_projects_file(self):
        rows = fleet.parse_projects_file("# server A\n/srv/shop\n\n/srv/billing  # second\n")
        self.assertEqual(rows, [("shop", "/srv/shop"), ("billing", "/srv/billing")])

    def test_env_file_takes_shell_default(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "config.env"
            f.write_text('# c\nDESK_TMUX="${DESK_TMUX:-shop-feedback}"\nSESSION_NAME="Shop server"\nX=1 # n\n')
            self.assertEqual(fleet.read_env(f),
                             {"DESK_TMUX": "shop-feedback", "SESSION_NAME": "Shop server", "X": "1"})

    def test_duplicates_and_filter(self):
        rows = [("a", "/srv/shop"), ("b", "/srv/shop"), ("c", "/srv/billing")]
        self.assertEqual([p.slug for p in fleet.load_projects(rows)], ["shop", "billing"])
        self.assertEqual([p.slug for p in fleet.load_projects(rows, "billing")], ["billing"])


    def test_same_directory_name_in_two_places_is_refused(self):
        rows = [("Gamma", "/a/Gamma"), ("Gamma", "/b/Gamma")]
        with self.assertRaises(fleet.ProjectsError) as cm:
            fleet.load_projects(rows)
        self.assertIn("/a/Gamma", str(cm.exception))
        self.assertIn("/b/Gamma", str(cm.exception))
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d, mock.patch("sys.stderr", out):
            f = Path(d) / "projects"
            f.write_text("/a/Gamma\n/b/Gamma\n")
            self.assertEqual(fleet.main(["projects", "--projects", str(f)]), 2)
        self.assertIn("/b/Gamma", out.getvalue())

    def test_malformed_session_env_is_a_settings_error_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            for name, text in (("good", 'SESSION_NAME="Good"\n'), ("bad", "SESSION_NAME=Bob's server\n")):
                (Path(d) / name / ".claude/factory").mkdir(parents=True)
                (Path(d) / name / ".claude/factory/session.env").write_text(text)
            projects = fleet.load_projects([("good", f"{d}/good"), ("bad", f"{d}/bad")])
            self.assertEqual([p.slug for p in projects], ["good", "bad"])
            self.assertEqual(projects[0].session_name, "Good")
            self.assertIn("session.env", projects[1].settings_error)
            with self.assertRaises(fleet.SettingsError):
                fleet.units_for(projects[1], "/usr/bin/claude", "/usr/bin/tmux")
            ok, text = fleet.check_session(projects[1])
            self.assertFalse(ok)
            self.assertIn("session.env", text)

    def test_malformed_desk_config_fails_only_that_check(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "scripts").mkdir()
            (d / "scripts/feedback-desk.sh").write_text("#!/bin/sh\necho running\n")
            (d / "scripts/feedback-desk.sh").chmod(0o755)
            (d / ".claude/feedback-desk").mkdir(parents=True)
            (d / ".claude/feedback-desk/config.env").write_text("DESK_TMUX=it's\n")
            ok, text = fleet.check_desk(fleet.Project("Shop", d, "shop"))
            self.assertFalse(ok)
            self.assertIn("config.env", text)


class UnitsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "shop/.claude/factory").mkdir(parents=True)
        (self.root / "shop/.claude/factory/session.env").write_text(
            'SESSION_NAME="Shop server"\nSESSION_ARGS="--permission-mode auto"\n')
        self.p = fleet.load_projects([("Shop", str(self.root / "shop"))])[0]

    def tearDown(self):
        self.tmp.cleanup()

    def test_session_unit_runs_a_named_interactive_session_in_own_tmux(self):
        # a named interactive session keeps its name across /clear; a remote-control spawn has a derived one
        text = fleet.session_unit(self.p, "/bin/claude", "/bin/tmux")
        self.assertIn('ExecStart="/bin/tmux" -L df-shop -f /dev/null new-session -d -s session', text)
        self.assertNotIn('DF_UNATTENDED', text)  # a person drives it from claude.ai: the guard asks, not denies
        # one double-quoted word for systemd, shell quoting inside it for tmux; only the permission mode carries over
        self.assertIn(""""/bin/claude --name 'Shop server' --remote-control 'Shop server' -w dev-session"""
                      """ --permission-mode auto"\n""", text)
        self.assertNotIn("remote-control --", text)
        self.assertIn('ExecStop="/bin/tmux" -L df-shop kill-server', text)
        self.assertIn("Restart=always", text)

    def test_rc_unit_spawns_the_owners_sessions_in_worktrees(self):
        text = fleet.rc_unit(self.p, "/bin/claude", "/bin/tmux")
        self.assertIn('ExecStart="/bin/tmux" -L df-shop.rc -f /dev/null new-session -d -s session', text)
        self.assertIn(""""/bin/claude remote-control --name 'Shop server · new sessions' --no-create-session-in-dir"""
                      """ --permission-mode auto --spawn worktree --capacity 3"\n""", text)
        self.assertIn('ExecStop="/bin/tmux" -L df-shop.rc kill-server', text)
        (self.root / "shop/.claude/factory/session.env").write_text('SESSION_NAME="Shop server"\nRC_NAME="Shop new"\n')
        p = fleet.load_projects([("Shop", str(self.root / "shop"))])[0]
        self.assertIn("--name 'Shop new'", fleet.rc_unit(p, "c", "t"))
        self.assertNotIn("--permission-mode", fleet.session_unit(p, "c", "t"))

    def test_session_unit_stops_restarting_when_it_cannot_start(self):
        text = fleet.session_unit(self.p, "/bin/claude", "/bin/tmux")
        unit_section = text.split("[Service]")[0]
        self.assertIn("StartLimitIntervalSec=", unit_section)
        self.assertIn("StartLimitBurst=5", unit_section)

    def test_units_notify_on_failure_and_skip_network_online(self):
        (self.root / "shop/scripts").mkdir()
        (self.root / "shop/scripts/feedback-desk.sh").write_text("#!/bin/sh\n")
        for name, text in fleet.units_for(self.p, "c", "t").items():
            with self.subTest(unit=name):
                self.assertNotIn("network-online", text)  # a no-op in a user instance
                if name.endswith(".service"):
                    self.assertIn("OnFailure=df-notify@%n.service", text.split("[Service]")[0])

    def test_server_units_notify_and_watch(self):
        units = fleet.server_units("/usr/bin/python3", ["--projects", "/home/u/.config/dark-factory/projects"])
        self.assertEqual(sorted(units), ["df-fleet-janitor.service", "df-fleet-janitor.timer",
                                         "df-fleet-watch.service", "df-fleet-watch.timer", "df-notify@.service"])
        notify = units["df-notify@.service"]
        self.assertIn('"/usr/bin/python3" ', notify)
        self.assertIn('/fleet.py" "notify" %i', notify)
        self.assertNotIn("OnFailure", notify)  # a failing notifier must not notify about itself
        self.assertNotIn("[Install]", notify)
        watch = units["df-fleet-watch.service"]
        self.assertIn('"--projects" "/home/u/.config/dark-factory/projects" "status" "--notify"', watch)
        self.assertIn("FACTORY_CANON=", watch)
        self.assertIn("OnUnitActiveSec=15min", units["df-fleet-watch.timer"])
        self.assertIn("Unit=df-fleet-watch.service", units["df-fleet-watch.timer"])
        janitor = units["df-fleet-janitor.service"]
        self.assertIn('"--projects" "/home/u/.config/dark-factory/projects" "janitor"\n', janitor)
        self.assertIn("OnFailure=df-notify@%n.service", janitor.split("[Service]")[0])
        self.assertNotIn("[Install]", janitor)  # started by its timer
        self.assertIn("OnCalendar=*-*-* 06:30:00 UTC", units["df-fleet-janitor.timer"])
        self.assertIn("Persistent=true", units["df-fleet-janitor.timer"])
        self.assertIn("Unit=df-fleet-janitor.service", units["df-fleet-janitor.timer"])

    def test_unit_quoting_escapes_systemd_specials(self):
        self.p.settings["SESSION_NAME"] = 'Shop "50%" $HOME'
        text = fleet.session_unit(self.p, "/bin/claude", "/bin/tmux")
        self.assertIn("""--name 'Shop \\"50%%\\" $$HOME'""", text)

    def test_desk_units_only_with_feedback_desk(self):
        self.assertEqual(list(fleet.units_for(self.p, "c", "t")), ["df-shop-session.service", "df-shop-rc.service"])
        (self.root / "shop/scripts").mkdir()
        (self.root / "shop/scripts/feedback-desk.sh").write_text("#!/bin/sh\n")
        units = fleet.units_for(self.p, "c", "t")
        self.assertEqual(sorted(units), ["df-shop-desk-watch.service", "df-shop-desk-watch.timer",
                                         "df-shop-desk.service", "df-shop-rc.service", "df-shop-session.service"])
        self.assertIn("KillMode=process", units["df-shop-desk.service"])

    def test_desk_watch_reruns_idempotent_up_every_5_minutes(self):
        (self.root / "shop/scripts").mkdir()
        (self.root / "shop/scripts/feedback-desk.sh").write_text("#!/bin/sh\n")
        units = fleet.units_for(self.p, "c", "t")
        watch, timer = units["df-shop-desk-watch.service"], units["df-shop-desk-watch.timer"]
        self.assertIn('/shop/scripts/feedback-desk.sh" up', watch)
        self.assertIn("KillMode=process", watch)  # the tmux session it starts outlives the oneshot
        self.assertNotIn("[Install]", watch)
        self.assertIn("OnUnitActiveSec=5min", timer)
        self.assertIn("Unit=df-shop-desk-watch.service", timer)
        self.assertIn("WantedBy=timers.target", timer)

    def test_vulns_units_only_with_a_lock_file(self):
        self.assertNotIn("df-shop-vulns.timer", fleet.units_for(self.p, "c", "t"))
        (self.root / "shop/web/node_modules/x").mkdir(parents=True)
        (self.root / "shop/web/node_modules/x/package-lock.json").write_text("{}")  # a dependency's, not the project's
        self.assertNotIn("df-shop-vulns.timer", fleet.units_for(self.p, "c", "t"))
        (self.root / "shop/backend").mkdir()
        (self.root / "shop/backend/uv.lock").write_text("version = 1\n")
        units = fleet.units_for(self.p, "c", "t")
        service, timer = units["df-shop-vulns.service"], units["df-shop-vulns.timer"]
        self.assertIn('/scripts/vulns.py" "', service)
        self.assertIn(f'"{self.root / "shop"}"', service)
        self.assertIn("OnFailure=df-notify@%n.service", service)
        self.assertNotIn("[Install]", service)
        self.assertIn("OnCalendar=*-*-* 05:40:00 UTC", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("Unit=df-shop-vulns.service", timer)
        self.p.settings["VULN_CHECK"] = "off"
        self.assertNotIn("df-shop-vulns.timer", fleet.units_for(self.p, "c", "t"))

    def test_vulns_status_line(self):
        self.assertIsNone(fleet.check_vulns(self.p))  # no lock file, no line
        (self.root / "shop/uv.lock").write_text("version = 1\n")
        with mock.patch.object(fleet, "UNIT_DIR", self.root / "units"):
            ok, text = fleet.check_vulns(self.p)
            self.assertFalse(ok)
            self.assertIn("not provisioned", text)
            (self.root / "units").mkdir()
            (self.root / "units/df-shop-vulns.timer").write_text("")
            self.assertEqual(fleet.check_vulns(self.p), (True, "no check recorded yet"))
            state = self.root / "shop/.claude/state/vulns/state.json"
            state.parent.mkdir(parents=True)
            state.write_text(json.dumps({"latest": {"ok": True, "total": 3, "critical": 1, "at": "2026-11-04T05:40:00Z",
                                                    "text": "3 known on origin/main (1 critical), 0 new"}}))
            ok, text = fleet.check_vulns(self.p)
            self.assertFalse(ok)  # a critical one is open
            self.assertIn("1 critical", text)
            state.write_text(json.dumps({"latest": {"ok": True, "total": 2, "critical": 0, "at": "x", "text": "2 known"}}))
            self.assertTrue(fleet.check_vulns(self.p)[0])
            self.p.settings["VULN_CHECK"] = "off"
            self.assertIsNone(fleet.check_vulns(self.p))

    def test_no_project_units_for_the_queue(self):
        (self.root / "shop/.beads").mkdir()
        (self.root / "shop/.beads/metadata.json").write_text("{}")
        self.assertTrue(fleet.has_queue(self.p))  # the janitor reviews it
        self.assertEqual(list(fleet.units_for(self.p, "c", "t")), ["df-shop-session.service", "df-shop-rc.service"])

    def test_unit_path_drops_per_shell_entries(self):
        old = os.environ["PATH"]
        os.environ["PATH"] = "/run/user/1/fnm/bin:/usr/bin:/bin"
        try:
            self.assertEqual(fleet.unit_path().split(":")[:2], ["/usr/bin", "/bin"])
            self.assertNotIn("/run/", fleet.unit_path())
        finally:
            os.environ["PATH"] = old

    def test_unit_path_adds_user_bins_to_a_bare_environment(self):
        # systemd-run starts with PATH=/usr/bin:/bin, without ~/.local/bin where the claude installer puts it
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
            dirs = fleet.unit_path().split(":")
        self.assertIn(os.path.expanduser("~/.local/bin"), dirs)
        self.assertEqual(len(dirs), len(set(dirs)))

    def provision(self, projects, dry=True, linux=True, fleet_env=""):
        fleet.UNIT_DIR = self.root / "units"
        (self.root / "fleet.env").write_text(fleet_env)  # never the server's own fleet.env
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), mock.patch("sys.stderr", err), \
                mock.patch.object(fleet, "FLEET_ENV", self.root / "fleet.env"), \
                mock.patch.object(fleet, "PLUGIN_LINK", self.root / "share/plugin"), \
                mock.patch.object(fleet.shutil, "which", lambda n, path=None: f"/bin/{n}"), \
                mock.patch.object(fleet, "platform_ok", lambda: linux), \
                mock.patch.object(fleet, "run", self.fake_run):  # never the real systemctl/loginctl
            code = fleet.provision(projects, dry=dry)
        return code, out.getvalue(), err.getvalue()

    @staticmethod
    def fake_run(cmd, dry):
        print(("would run: " if dry else "$ ") + " ".join(cmd))
        return 0

    def test_dry_run_writes_nothing(self):
        code, out, _ = self.provision([self.p])
        self.assertEqual(code, 0)
        self.assertFalse((self.root / "units").exists())
        self.assertIn("would run: loginctl enable-linger", out)

    def test_enables_timer_not_watch_service(self):
        (self.root / "shop/scripts").mkdir()
        (self.root / "shop/scripts/feedback-desk.sh").write_text("#!/bin/sh\n")
        _, out, _ = self.provision([self.p])
        enable = next(ln for ln in out.splitlines() if " enable --now " in ln)
        self.assertIn("df-shop-desk-watch.timer", enable)
        self.assertNotIn("df-shop-desk-watch.service", enable)
        self.assertIn("df-fleet-watch.timer", enable)
        self.assertNotIn("df-notify@", enable)  # a template, started by OnFailure only
        self.assertIn("would write " + str(self.root / "units/df-notify@.service"), out)

    def test_provision_links_only_a_test_plugin_link(self):
        # provision repoints the fleet's plugin link; a test run must never touch the server's real one
        code, out, _ = self.provision([self.p], dry=False)
        self.assertEqual(code, 0)
        self.assertTrue((self.root / "share/plugin").is_symlink())
        self.assertIn(f"link {self.root / 'share/plugin'} -> ", out)

    def test_writes_server_units(self):
        code, out, _ = self.provision([self.p], dry=False)
        self.assertEqual(code, 0)
        self.assertIn("$ loginctl enable-linger", out)  # printed by the fake; nothing ran
        written = sorted(p.name for p in (self.root / "units").iterdir())
        self.assertEqual(written, ["df-fleet-janitor.service", "df-fleet-janitor.timer", "df-fleet-watch.service",
                                   "df-fleet-watch.timer", "df-notify@.service",
                                   "df-shop-rc.service", "df-shop-session.service"])

    def test_removes_the_units_the_janitor_replaced(self):
        (self.root / "units").mkdir(parents=True, exist_ok=True)
        old = ("df-fleet-gc.service", "df-fleet-gc.timer", "df-shop-triage.service", "df-shop-triage.timer")
        for n in old:
            (self.root / "units" / n).write_text("")
        code, out, _ = self.provision([self.p], dry=False)
        self.assertEqual(code, 0)
        self.assertFalse(any((self.root / "units" / n).exists() for n in old))
        disable = next(ln for ln in out.splitlines() if " disable --now " in ln)
        self.assertIn("df-fleet-gc.timer", disable)
        self.assertIn("df-shop-triage.timer", disable)

    def test_writes_analytics_pair_with_the_setting(self):
        code, out, _ = self.provision([self.p], dry=False, fleet_env='FLEET_ANALYTICS_REPO="/srv/canon"\n')
        self.assertEqual(code, 0)
        self.assertTrue((self.root / "units/df-fleet-analytics.timer").is_file())
        self.assertIn("df-fleet-analytics.timer", out.split("enable --now")[1])

    def test_removes_analytics_pair_without_the_setting(self):
        (self.root / "units").mkdir(parents=True, exist_ok=True)
        for n in ("df-fleet-analytics.service", "df-fleet-analytics.timer"):
            (self.root / "units" / n).write_text("")
        code, out, _ = self.provision([self.p], dry=False)
        self.assertEqual(code, 0)
        self.assertFalse((self.root / "units/df-fleet-analytics.timer").exists())
        self.assertIn("disable --now df-fleet-analytics.timer", out)

    def test_writes_feed_units_with_the_setting(self):
        code, out, _ = self.provision([self.p], dry=False, fleet_env='FLEET_FEED_ADDR="100.64.1.2:8765"\n')
        self.assertEqual(code, 0)
        for n in ("df-fleet-feed.service", "df-fleet-feed.timer", "df-fleet-serve.service"):
            self.assertTrue((self.root / "units" / n).is_file(), n)
        enable = out.split("enable --now")[1]
        self.assertIn("df-fleet-feed.timer", enable)
        self.assertIn("df-fleet-serve.service", enable)
        self.assertIn("try-restart df-fleet-serve.service", out)  # a changed address takes effect

    def test_removes_feed_units_without_the_setting(self):
        (self.root / "units").mkdir(parents=True, exist_ok=True)
        for n in ("df-fleet-feed.service", "df-fleet-feed.timer", "df-fleet-serve.service"):
            (self.root / "units" / n).write_text("")
        code, out, _ = self.provision([self.p], dry=False)
        self.assertEqual(code, 0)
        for n in ("df-fleet-feed.service", "df-fleet-feed.timer", "df-fleet-serve.service"):
            self.assertFalse((self.root / "units" / n).exists(), n)
        self.assertIn("disable --now df-fleet-feed.timer df-fleet-serve.service", out)

    def test_refuses_project_with_disallowed_session_args(self):
        (self.root / "bad/.claude/factory").mkdir(parents=True)
        (self.root / "bad/.claude/factory/session.env").write_text(
            'SESSION_ARGS="--permission-mode bypassPermissions"\n')
        bad = fleet.load_projects([("Bad", str(self.root / "bad"))])[0]
        code, out, err = self.provision([self.p, bad])
        self.assertEqual(code, 1)
        self.assertIn("Bad: not provisioned", err)
        self.assertIn("bypassPermissions", err)
        self.assertIn("df-shop-session.service", out)  # the other project is still provisioned
        self.assertNotIn("df-bad-", out)

    def test_refuses_off_linux(self):
        code, out, err = self.provision([self.p], linux=False)
        self.assertEqual(code, 1)
        self.assertIn("systemd user instance", err)
        self.assertEqual(out, "")


class SessionArgsTest(unittest.TestCase):
    def test_accepts_allowlisted(self):
        self.assertEqual(fleet.parse_session_args(""), ["--spawn", "worktree", "--capacity", "3"])
        self.assertEqual(fleet.parse_session_args("--permission-mode auto"),
                         ["--permission-mode", "auto", "--spawn", "worktree", "--capacity", "3"])
        self.assertEqual(fleet.parse_session_args("--permission-mode=acceptEdits --spawn same-dir"),
                         ["--permission-mode", "acceptEdits", "--spawn", "same-dir", "--capacity", "3"])
        for mode in ("default", "plan"):
            self.assertIn(mode, fleet.parse_session_args(f"--permission-mode {mode}"))

    def test_capacity_1_to_8_default_3(self):
        self.assertEqual(fleet.parse_session_args("--capacity 8"), ["--capacity", "8", "--spawn", "worktree"])
        self.assertEqual(fleet.parse_session_args("--capacity=1 --spawn same-dir"),
                         ["--capacity", "1", "--spawn", "same-dir"])
        self.assertEqual(fleet.parse_session_args("--spawn session"), ["--spawn", "session"])  # not used there
        for raw in ("--capacity 0", "--capacity 9", "--capacity 32", "--capacity x", "--capacity",
                    "--capacity 2 --spawn session"):
            with self.subTest(raw=raw), self.assertRaises(fleet.SettingsError):
                fleet.parse_session_args(raw)

    def test_refuses_everything_else(self):
        for raw in ("--permission-mode bypassPermissions", "--permission-mode dontAsk",
                    "--dangerously-skip-permissions", "--permission-mode", "--permission-mode auto --permission-mode plan",
                    "--model claude-opus-5-5", "--debug-file /tmp/x", "--spawn worktree; rm -rf ~", "--name x",
                    "auto", "--permission-mode 'auto", "--spawn=everywhere"):
            with self.subTest(raw=raw), self.assertRaises(fleet.SettingsError):
                fleet.parse_session_args(raw)

    def test_session_name_without_control_chars(self):
        self.assertEqual(fleet.check_session_name("Café server"), "Café server")
        for name in ("", "  ", "a\tb", "a\x1bb"):
            with self.subTest(name=name), self.assertRaises(fleet.SettingsError):
                fleet.check_session_name(name)


class SessionCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        fleet.UNIT_DIR = Path(self.tmp.name)
        (fleet.UNIT_DIR / "df-shop-session.service").write_text("")
        self.p = fleet.Project("Shop", Path("/srv/shop"), "shop")

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, active: bool, screen: str):
        def sh(cmd, cwd=None, timeout=20):
            if cmd[:2] == ["systemctl", "--user"]:
                return (0, "active") if active else (3, "failed")
            self.assertEqual(cmd, ["tmux", "-L", "df-shop", "capture-pane", "-p", "-t", "session"])
            return 0, screen
        with mock.patch.object(fleet, "sh", sh):
            return fleet.check_session(self.p)

    def test_active_and_working(self):
        self.assertEqual(self.check(True, "Remote Control active\n> ")[0], True)

    def test_active_but_waiting_for_trust_or_login(self):
        for screen in ("Do you trust the files in this folder?\n❯ 1. Yes, I trust this folder", "Please run /login"):
            with self.subTest(screen=screen):
                ok, text = self.check(True, screen)
                self.assertFalse(ok)
                self.assertIn("login or folder-trust prompt", text)

    def test_failed_points_at_start_limit(self):
        ok, text = self.check(False, "")
        self.assertFalse(ok)
        self.assertIn("reset-failed df-shop-session.service", text)

    def test_rc_line_only_once_provisioned(self):
        self.assertIsNone(fleet.check_rc(self.p))
        (fleet.UNIT_DIR / "df-shop-rc.service").write_text("")
        calls = []

        def sh(cmd, cwd=None, timeout=20):
            calls.append(cmd)
            return (0, "active") if cmd[0] == "systemctl" else (0, "")
        with mock.patch.object(fleet, "sh", sh):
            ok, text = fleet.check_rc(self.p)
        self.assertTrue(ok)
        self.assertIn("tmux -L df-shop.rc attach", text)
        self.assertIn(["systemctl", "--user", "is-active", "df-shop-rc.service"], calls)

    def test_checks_do_not_inherit_the_callers_tmux(self):
        # factory-status runs inside a development session's tmux server; the On-call is in the default server
        with mock.patch.dict(os.environ, {"TMUX": "/tmp/tmux-1/df-shop,1,0", "TMUX_PANE": "%0"}):
            self.assertEqual(fleet.sh(["sh", "-c", 'echo "${TMUX:-none} ${TMUX_PANE:-none}"']), (0, "none none"))


class UnprovisionTest(unittest.TestCase):
    def test_removes_only_this_projects_units(self):
        with tempfile.TemporaryDirectory() as d:
            fleet.UNIT_DIR = Path(d)
            for n in ("df-shop-session.service", "df-shop-desk-watch.timer", "df-shop-x-session.service"):
                (Path(d) / n).write_text("")
            calls = []
            with redirect_stdout(io.StringIO()), mock.patch.object(fleet, "run", lambda cmd, dry: calls.append(cmd) or 0):
                fleet.unprovision([fleet.Project("Shop", Path("/srv/shop"), "shop")], dry=False)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["df-shop-x-session.service"])
            self.assertEqual(calls[0], ["systemctl", "--user", "disable", "--now",
                                        "df-shop-session.service", "df-shop-desk-watch.timer"])

    def test_server_units_go_with_the_last_project(self):
        with tempfile.TemporaryDirectory() as d:
            fleet.UNIT_DIR = Path(d)
            server = ("df-notify@.service", "df-fleet-watch.service", "df-fleet-watch.timer",
                      "df-fleet-janitor.service", "df-fleet-janitor.timer")
            for n in ("df-shop-session.service", "df-bill-session.service", *server):
                (Path(d) / n).write_text("")
            calls = []
            with redirect_stdout(io.StringIO()), mock.patch.object(fleet, "run", lambda cmd, dry: calls.append(cmd) or 0):
                fleet.unprovision([fleet.Project("Shop", Path("/srv/shop"), "shop")], dry=False)
                self.assertTrue(all((Path(d) / n).exists() for n in server))  # billing still runs here
                fleet.unprovision([fleet.Project("Bill", Path("/srv/bill"), "bill")], dry=False)
            self.assertEqual(list(Path(d).iterdir()), [])
            self.assertNotIn("df-notify@.service", calls[-2])  # templates are not disabled
            self.assertIn("df-fleet-watch.timer", calls[-2])
            self.assertIn("df-fleet-janitor.timer", calls[-2])


    def test_project_removed_from_the_file_is_found_by_its_units(self):
        with tempfile.TemporaryDirectory() as d:
            fleet.UNIT_DIR = Path(d)
            for n in ("df-shop-session.service", "df-shop-desk.service", "df-bill-session.service"):
                (Path(d) / n).write_text("")
            projects = Path(d) / "projects"
            projects.write_text("/srv/bill\n")  # shop's line is already gone
            calls = []
            with redirect_stdout(io.StringIO()), mock.patch.object(fleet, "run", lambda cmd, dry: calls.append(cmd) or 0):
                self.assertEqual(fleet.main(["unprovision", "--projects", str(projects), "--project", "Shop"]), 0)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["df-bill-session.service", "projects"])
            self.assertEqual(calls[0][-2:], ["df-shop-session.service", "df-shop-desk.service"])
            with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(fleet.main(["unprovision", "--projects", str(projects), "--project", "nosuch"]), 2)


class NotifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for key in ("FLEET_NOTIFY_CMD", "FLEET_NOTIFY", "FLEET_SESSION_NAME"):
            os.environ.pop(key, None)
        self.patches = [mock.patch.object(fleet, "FLEET_ENV", self.d / "fleet.env"),
                        mock.patch.object(fleet, "FLEET_STATE", self.d / "state/fleet-watch.json")]
        for x in self.patches:
            x.start()

    def tearDown(self):
        for x in self.patches:
            x.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_sends_through_the_configured_command_on_stdin(self):
        out = self.d / "sent.txt"
        (self.d / "fleet.env").write_text(f"FLEET_NOTIFY_CMD='cat > {out}'\n")
        self.assertEqual(fleet.notify("df-shop-session.service failed"), 0)
        self.assertEqual(out.read_text(), "df-shop-session.service failed")

    def test_without_a_command_writes_to_the_journal(self):
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            self.assertEqual(fleet.notify("x failed"), 0)
        self.assertIn("dark factory: x failed", err.getvalue())

    def test_failing_command_still_reaches_the_journal(self):
        (self.d / "fleet.env").write_text("FLEET_NOTIFY_CMD='exit 3'\n")
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            self.assertEqual(fleet.notify("x failed"), 1)
        self.assertIn("dark factory: x failed", err.getvalue())

    def session_stubs(self, alive: bool, sent: bool) -> Path:
        bin_ = self.d / "bin"
        bin_.mkdir()
        (bin_ / "tmux").write_text(f"""#!/usr/bin/env bash
printf '%s\\n' "$@" >> "{bin_}/tmux.log"; echo -- >> "{bin_}/tmux.log"
if [ "$3" = list-panes ]; then {"echo '0 claude --name Factory'" if alive else "exit 1"}; fi
""")
        (bin_ / "claude").write_text(f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "{bin_}/claude.args"
echo '{{"structured_output": {{"sent": {"true" if sent else "false"}}}}}'
""")
        for name in ("tmux", "claude"):
            (bin_ / name).chmod(0o755)
        os.environ["PATH"] = f"{bin_}:{os.environ['PATH']}"
        (self.d / "fleet.env").write_text("FLEET_NOTIFY=session\nFLEET_SESSION_NAME='Factory · test'\n")
        return bin_

    def test_a_notice_opens_the_factory_session(self):
        bin_ = self.session_stubs(alive=False, sent=False)
        self.assertEqual(fleet.notify("df-shop-session.service failed"), 0)
        log = (bin_ / "tmux.log").read_text()
        self.assertIn("new-session", log)
        self.assertIn("df-factory", log)
        self.assertIn("--remote-control 'Factory · test'", log)
        self.assertIn("df-shop-session.service failed", log)
        self.assertIn("dontAsk", log)
        self.assertNotIn("Bash(systemctl --user restart", log)  # it reads, the owner acts
        self.assertFalse((bin_ / "claude.args").exists())  # nothing to deliver to: no delivery run

    def test_a_running_factory_session_gets_the_notice_as_a_message(self):
        bin_ = self.session_stubs(alive=True, sent=True)
        self.assertEqual(fleet.notify("x failed"), 0)
        args = (bin_ / "claude.args").read_text()
        self.assertIn("SendMessage,ListAgents", args)
        self.assertIn("'Factory · test'", args)
        self.assertNotIn("new-session", (bin_ / "tmux.log").read_text())

    def test_an_undeliverable_session_is_replaced(self):
        bin_ = self.session_stubs(alive=True, sent=False)
        self.assertEqual(fleet.notify("x failed"), 0)
        log = (bin_ / "tmux.log").read_text()
        self.assertLess(log.index("kill-session"), log.index("new-session"))

    def test_notify_command_line(self):
        with mock.patch.object(fleet, "notify", return_value=0) as n, mock.patch.object(fleet.socket, "gethostname",
                                                                                      return_value="vps"):
            self.assertEqual(fleet.main(["notify", "df-shop-session.service"]), 0)
        self.assertEqual(n.call_args.args[0],
                         "vps: df-shop-session.service failed — journalctl --user -u df-shop-session.service")

    def test_changes_only(self):
        self.assertEqual(fleet.changes({}, {}), [])
        self.assertEqual(fleet.changes({"Shop: On-call": "a"}, {"Shop: On-call": "b"}), [])  # still failing
        self.assertEqual(fleet.changes({}, {"Shop: On-call": "asks to log in"}), ["○ Shop: On-call — asks to log in"])
        self.assertEqual(fleet.changes({"Shop: On-call": "a"}, {}), ["● Shop: On-call — fine again"])

    def run_status(self, desk):
        p = fleet.Project("Shop", self.d, "shop")
        with mock.patch.multiple(fleet, check_quota=lambda: None, check_session=lambda p: (True, "active"),
                                 check_desk=lambda p: desk, check_monitor=lambda p, now: None,
                                 check_queue=lambda p: None, check_drift=lambda p: (False, "1 behind"),
                                 check_worktrees=lambda p, now, days: None), \
                mock.patch.object(fleet, "notify", return_value=0) as n, redirect_stdout(io.StringIO()):
            self.assertEqual(fleet.status([p], notify_owner=True), 0)  # the watch unit never fails on a ○
        return [c.args[0] for c in n.call_args_list]

    def test_failed_notification_is_retried_by_the_next_run(self):
        login = (False, "running but asks to log in — attach and run /login")
        p = fleet.Project("Shop", self.d, "shop")

        def run(rc):
            with mock.patch.multiple(fleet, check_quota=lambda: None, check_session=lambda p: (True, "active"),
                                     check_desk=lambda p: login, check_monitor=lambda p, now: None,
                                     check_queue=lambda p: None, check_drift=lambda p: None,
                                     check_worktrees=lambda p, now, days: None), \
                    mock.patch.object(fleet, "notify", return_value=rc) as n, redirect_stdout(io.StringIO()):
                fleet.status([p], notify_owner=True)
            return n.call_count

        self.assertEqual(run(1), 1)  # delivery failed
        self.assertFalse(fleet.FLEET_STATE.exists())
        self.assertEqual(run(0), 1)  # reported again, now delivered
        self.assertEqual(run(0), 0)  # and then silent

    def test_status_notify_on_state_changes_only(self):
        login = (False, "running but asks to log in — attach and run /login")
        self.assertEqual(self.run_status((True, "running")), [])  # template drift is not a page
        [msg] = self.run_status(login)
        self.assertIn("○ Shop: On-call — running but asks to log in", msg)
        self.assertEqual(self.run_status(login), [])  # unchanged — silent
        [msg] = self.run_status((True, "running"))
        self.assertIn("● Shop: On-call — fine again", msg)


class WorktreeTest(unittest.TestCase):
    """A real repository: origin (bare) → clone with worktrees in every state gc has to tell apart."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.origin, self.repo = d / "origin.git", d / "repo"
        g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]

        def git(*args, cwd=None):
            subprocess.run([*g, *args], cwd=cwd or self.repo, check=True, capture_output=True)

        subprocess.run([*g, "init", "-q", "--bare", str(self.origin)], check=True)
        subprocess.run([*g, "clone", "-q", str(self.origin), str(self.repo)], check=True, capture_output=True)
        (self.repo / "a").write_text("a")
        git("add", "-A")
        git("commit", "-qm", "a")
        for name in ("merged", "dirty-merged", "open"):
            git("worktree", "add", "-q", "-b", name, str(d / name))
        (d / "open/b").write_text("b")
        git("add", "-A", cwd=d / "open")
        git("commit", "-qm", "b", cwd=d / "open")
        (d / "dirty-merged/scratch").write_text("x")
        git("push", "-q", "origin", "main")
        git("remote", "set-head", "origin", "main")
        self.d = d
        self.p = fleet.Project("Repo", self.repo, "repo")
        self.delivered = []  # never a real claude run or the server's state file
        for patch in (mock.patch.object(fleet, "JANITOR_STATE", d / "state/janitor.json"),
                      mock.patch.object(fleet, "deliver", lambda c, name, text: self.delivered.append((name, text)) or True),
                      mock.patch.object(fleet.shutil, "which", lambda *a, **k: "/bin/claude")):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_lists_state_of_each_worktree(self):
        trees = {t.branch: t for t in fleet.list_worktrees(self.repo, time.time(), busy=[])}
        self.assertEqual(sorted(trees), ["dirty-merged", "merged", "open"])
        self.assertTrue(trees["merged"].removable)
        self.assertFalse(trees["dirty-merged"].clean)
        self.assertFalse(trees["dirty-merged"].removable)
        self.assertFalse(trees["open"].merged)
        busy = fleet.list_worktrees(self.repo, time.time(), busy=[self.d / "merged/sub"])
        self.assertFalse(next(t for t in busy if t.branch == "merged").removable)  # a live session is inside

    def test_status_reports_stale_worktrees(self):
        with mock.patch.object(fleet, "busy_paths", return_value=[]):
            ok, text = fleet.check_worktrees(self.p, time.time(), 14)
            self.assertFalse(ok)
            self.assertIn("1 clean and merged (fleet.py gc --project repo)", text)
            ok, text = fleet.check_worktrees(self.p, time.time() + 30 * 86400, 14)
            self.assertIn("without commits for over 14 days: dirty-merged, open", text)

    def test_worktrees_on_other_rules_than_main_are_reported(self):
        g = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
        (self.repo / ".claude").mkdir()
        (self.repo / ".harness").mkdir()
        (self.repo / ".claude/settings.json").write_text('{"enabledPlugins": {"dark-factory@dark-factory": true}}')
        (self.repo / ".harness/base-sha").write_text("v6\n")
        subprocess.run([*g, "-C", str(self.repo), "add", "-A"], check=True)
        subprocess.run([*g, "-C", str(self.repo), "commit", "-qm", "canon"], check=True)
        subprocess.run([*g, "-C", str(self.repo), "push", "-q", "origin", "main"], check=True, capture_output=True)
        sessions = self.repo / ".claude/worktrees"
        for name, ref in (("old", "open"), ("behind", "main")):
            subprocess.run([*g, "-C", str(self.repo), "worktree", "add", "-q", "-b", f"s-{name}", str(sessions / name),
                            ref], check=True, capture_output=True)
        (sessions / "behind/.harness/base-sha").write_text("v5\n")
        subprocess.run([*g, "-C", str(self.repo), "worktree", "lock", str(sessions / "old")], check=True)
        trees = {t.branch: t for t in fleet.list_worktrees(self.repo, time.time(), busy=[])}
        self.assertEqual(trees["s-old"].harness, "no dark-factory plugin: no guard, no quota guard")
        self.assertTrue(trees["s-old"].locked)  # a claude.ai conversation's worktree is locked, and still checked
        self.assertEqual(trees["s-behind"].harness, "canon v5, main has v6")
        self.assertEqual(trees["open"].harness, "")  # not a session worktree (e.g. the prod monitor's copy)
        with mock.patch.object(fleet, "busy_paths", return_value=[]):
            ok, text = fleet.check_worktrees(self.p, time.time(), 14)
        self.assertFalse(ok)
        self.assertIn("2 on other rules than main", text)
        self.assertIn("s-old (no dark-factory plugin", text)

    def test_a_locked_worktree_is_never_removed(self):
        subprocess.run(["git", "-C", str(self.repo), "worktree", "lock", str(self.d / "merged")], check=True)
        with mock.patch.object(fleet, "busy_paths", return_value=[]), redirect_stdout(io.StringIO()) as out:
            fleet.gc([self.p], apply=True)
        self.assertTrue((self.d / "merged").is_dir())
        self.assertIn("locked", out.getvalue())

    def test_gc_dry_run_then_apply_removes_only_clean_merged(self):
        with mock.patch.object(fleet, "busy_paths", return_value=[]), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(fleet.gc([self.p], apply=False), 0)
        self.assertIn("would remove " + str(self.d / "merged"), out.getvalue())
        self.assertTrue((self.d / "merged").is_dir())
        with mock.patch.object(fleet, "busy_paths", return_value=[]), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(fleet.gc([self.p], apply=True), 0)
        self.assertFalse((self.d / "merged").exists())
        self.assertTrue((self.d / "dirty-merged/scratch").exists())
        self.assertTrue((self.d / "open").is_dir())
        self.assertIn("dirty", out.getvalue())
        self.assertIn("not merged", out.getvalue())

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              check=True, capture_output=True, text=True).stdout

    def test_a_merged_detached_worktree_goes_only_when_a_session_put_it(self):
        agent, monitor = self.repo / ".claude/worktrees/agent-1", self.d / "monitor-copy"
        for path in (agent, monitor):
            self.git("worktree", "add", "-q", "--detach", str(path), "main")
        with mock.patch.object(fleet, "busy_paths", return_value=[]), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(fleet.gc([self.p], apply=True), 0)
        self.assertFalse(agent.exists())
        self.assertTrue(monitor.is_dir())  # e.g. the prod monitor's copy: used without a process inside
        self.assertIn(f"keep {monitor} (-: detached)", out.getvalue())

    def test_gc_deletes_merged_branches_no_worktree_has(self):
        self.git("branch", "done")  # merged, no worktree
        self.git("branch", "dev")  # long-lived: kept
        self.git("branch", "unmerged", "open")  # holds the open worktree's commit
        with mock.patch.object(fleet, "busy_paths", return_value=[]), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(fleet.gc([self.p], apply=False), 0)
        self.assertIn("would delete branch done (merged)", out.getvalue())
        self.assertIn("would delete branch merged (merged)", out.getvalue())  # its worktree would go first
        with mock.patch.object(fleet, "busy_paths", return_value=[]), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(fleet.gc([self.p], apply=True), 0)
        left = sorted(self.git("for-each-ref", "refs/heads", "--format=%(refname:short)").split())
        # dirty-merged and open have worktrees; main and dev are long-lived; unmerged is not merged
        self.assertEqual(left, ["dev", "dirty-merged", "main", "open", "unmerged"])
        self.assertIn("deleted 2 merged branches: done, merged", out.getvalue())

    def test_a_squash_merged_branch_stays(self):
        self.git("branch", "squashed", "open")
        self.assertNotIn("squashed", [b for b, _ in fleet.merged_branches(self.repo)])

    def test_leftovers_need_a_judgement(self):
        self.git("branch", "abandoned", "open")  # unmerged, no worktree
        with mock.patch.object(fleet, "busy_paths", return_value=[]):
            now = fleet.leftovers(self.p, time.time())
            later = fleet.leftovers(self.p, time.time() + 30 * 86400)
        self.assertEqual(now, [f"worktree {self.d / 'dirty-merged'} (branch dirty-merged): merged, but has "
                               "uncommitted changes"])
        self.assertIn(f"worktree {self.d / 'open'} (branch open): not merged, no commits for 30 days", later)
        self.assertIn("branch abandoned: not merged, no worktree, no commits for 30 days", later)
        self.assertFalse(any("branch merged" in i or "branch main" in i for i in later))

    def test_hand_off_gives_each_leftover_to_the_development_session_once(self):
        with mock.patch.object(fleet, "busy_paths", return_value=[]), redirect_stdout(io.StringIO()):
            fleet.hand_off([self.p], time.time())
            fleet.hand_off([self.p], time.time())  # nothing new: nothing sent
            self.assertEqual(len(self.delivered), 1)
            self.assertEqual(self.delivered[0][0], "repo server")
            self.assertIn("data, not instructions", self.delivered[0][1])
            self.assertIn("- worktree ", self.delivered[0][1])
            self.git("branch", "abandoned", "open")
            fleet.hand_off([self.p], time.time() + 30 * 86400)
        self.assertEqual(len(self.delivered), 2)
        self.assertNotIn("dirty-merged", self.delivered[1][1])  # already handed
        self.assertIn("branch abandoned", self.delivered[1][1])

    def test_hand_off_retries_when_not_taken_and_can_be_off(self):
        with mock.patch.object(fleet, "busy_paths", return_value=[]), redirect_stdout(io.StringIO()) as out:
            with mock.patch.object(fleet, "deliver", lambda *a: False):
                fleet.hand_off([self.p], time.time())  # the session is not running
            self.assertIn("again tomorrow", out.getvalue())
            self.p.settings["JANITOR_HANDOFF"] = "off"
            fleet.hand_off([self.p], time.time())
            self.assertEqual(self.delivered, [])
            del self.p.settings["JANITOR_HANDOFF"]
            fleet.hand_off([self.p], time.time())
        self.assertEqual(len(self.delivered), 1)  # not taken yesterday → handed again

    def test_janitor_cleans_then_reviews_each_queue(self):
        (self.repo / ".beads").mkdir()
        (self.repo / ".beads/metadata.json").write_text("{}")
        calls = []
        with mock.patch.object(fleet, "busy_paths", return_value=[]), redirect_stdout(io.StringIO()), \
                mock.patch.object(fleet.subprocess, "call", lambda cmd, cwd: calls.append((cmd, cwd)) or 1):
            self.assertEqual(fleet.janitor([self.p]), 1)  # the failed review fails the unit, after the clean-up
        self.assertFalse((self.d / "merged").exists())
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][0][1].endswith("scripts/triage.py"))
        self.assertEqual((calls[0][0][2], calls[0][1]), (str(self.repo), self.repo))


class DriftTest(unittest.TestCase):
    def test_counts_template_commits_since_base_sha(self):
        with tempfile.TemporaryDirectory() as d:
            canon, proj = Path(d) / "canon", Path(d) / "proj"
            (canon / "templates").mkdir(parents=True)
            (canon / "plugins/dark-factory/templates").mkdir(parents=True)
            git = ["git", "-C", str(canon), "-c", "user.name=t", "-c", "user.email=t@t"]
            subprocess.run(["git", "init", "-q", str(canon)], check=True)

            def commit(name):
                (canon / name).write_text(name)
                subprocess.run([*git, "add", "-A"], check=True)
                subprocess.run([*git, "commit", "-qm", name], check=True)
                return subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

            base = commit("templates/a")
            commit("templates/b")
            commit("plugins/dark-factory/templates/c")
            commit("docs-only")
            (proj / ".harness").mkdir(parents=True)
            (proj / ".harness/base-sha").write_text(base + "\n")
            fleet.FACTORY = canon
            p = fleet.Project("p", proj, "p")
            self.assertEqual(fleet.check_drift(p), (False, "2 template commits behind (harness-diff.sh)"))
            (proj / ".harness/base-sha").write_text("0" * 40)
            self.assertFalse(fleet.check_drift(p)[0])


class PluginCheckTest(unittest.TestCase):
    """Enabled in settings is not loaded: without an install entry for the checkout there is no guard."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.proj, self.config = root / "proj", root / "config"
        (self.proj / ".claude").mkdir(parents=True)
        (self.config / "plugins").mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.config)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)
        self.p = fleet.Project("p", self.proj, "p")

    def settings(self, enabled, local=None):
        (self.proj / ".claude/settings.json").write_text(json.dumps({"enabledPlugins": {fleet.PLUGIN_ID: enabled}}))
        if local is not None:
            (self.proj / ".claude/settings.local.json").write_text(json.dumps({"enabledPlugins": {fleet.PLUGIN_ID: local}}))

    def installs(self, *entries):
        (self.config / "plugins/installed_plugins.json").write_text(
            json.dumps({"version": 2, "plugins": {fleet.PLUGIN_ID: list(entries)}}))

    def test_no_settings_no_line(self):
        self.assertIsNone(fleet.check_plugin(self.p))

    def test_enabled_but_installed_only_for_another_checkout(self):
        self.settings(True)
        self.installs({"scope": "project", "projectPath": "/srv/other"})
        ok, msg = fleet.check_plugin(self.p)
        self.assertFalse(ok)
        self.assertIn("guard and quota guard off", msg)
        self.assertIn(f"cd {self.proj} && claude plugin install {fleet.PLUGIN_ID} --scope project", msg)

    def test_no_install_record_at_all(self):
        self.settings(True)
        self.assertFalse(fleet.check_plugin(self.p)[0])

    def test_project_or_user_install_is_fine(self):
        self.settings(True)
        self.installs({"scope": "project", "projectPath": str(self.proj)})
        self.assertEqual(fleet.check_plugin(self.p), (True, "installed and enabled"))
        self.installs({"scope": "user"})
        self.assertTrue(fleet.check_plugin(self.p)[0])

    def test_not_enabled_or_disabled_locally(self):
        self.installs({"scope": "user"})
        self.settings(False)
        self.assertIn("not enabled", fleet.check_plugin(self.p)[1])
        self.settings(True, local=False)
        self.assertFalse(fleet.check_plugin(self.p)[0])

    def test_status_shows_and_notifies_the_line(self):
        self.assertIn("plugin", fleet.NOTIFY_LABELS)
        self.settings(True)
        with mock.patch.object(fleet, "check_quota", return_value=None), \
                mock.patch.object(fleet, "check_units", return_value=None), \
                mock.patch.object(fleet, "check_session", return_value=(True, "active")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                fleet.status([self.p])
        self.assertIn("○ plugin — not installed for this checkout", buf.getvalue())


class ReleaseTagDriftTest(unittest.TestCase):
    def test_compares_the_deployed_release_with_the_plugin_version(self):
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            (proj / ".harness").mkdir()
            p = fleet.Project("p", proj, "p")
            with mock.patch.object(fleet, "plugin_version", return_value="v2"):
                (proj / ".harness/base-sha").write_text("v2\n")
                self.assertEqual(fleet.check_drift(p), (True, "on the plugin's template v2"))
                (proj / ".harness/base-sha").write_text("v1\n")
                ok, msg = fleet.check_drift(p)
                self.assertFalse(ok)
                self.assertIn("v1", msg)

    def test_plugin_version_reads_the_manifest(self):
        self.assertRegex(fleet.plugin_version(), r"^v\d+$")


class UnitsPathTest(unittest.TestCase):
    def test_units_call_the_script_through_the_plugin_link(self):
        with tempfile.TemporaryDirectory() as d:
            link = Path(d) / "share/plugin"
            with mock.patch.object(fleet, "PLUGIN_LINK", link):
                self.assertIn(f"{link}/scripts/fleet.py", fleet.fleet_cmd("/usr/bin/python3", "notify"))
                self.assertNotIn(str(fleet.PLUGIN), fleet.fleet_cmd("/usr/bin/python3", "notify"))

    def test_provision_creates_and_refreshes_the_link(self):
        with tempfile.TemporaryDirectory() as d:
            link = Path(d) / "share/plugin"
            with mock.patch.object(fleet, "PLUGIN_LINK", link):
                fleet.link_plugin(dry=True)
                self.assertFalse(link.is_symlink())
                link.parent.mkdir()
                link.symlink_to("/old/cache/0.2.0")
                fleet.link_plugin(dry=False)
                self.assertEqual(link.resolve(), fleet.PLUGIN)

    def test_status_checks_the_link_target(self):
        with tempfile.TemporaryDirectory() as d:
            link = Path(d) / "plugin"
            with mock.patch.object(fleet, "UNIT_DIR", Path(d)), mock.patch.object(fleet, "PLUGIN_LINK", link):
                self.assertIsNone(fleet.check_units())  # not provisioned: nothing to compare
                unit = Path(d) / f"{fleet.FLEET_WATCH}.service"
                unit.write_text(f"ExecStart=python3 {link}/scripts/fleet.py status --notify\n")
                self.assertFalse(fleet.check_units()[0])  # no link
                link.symlink_to("/old/cache/0.2.0")
                self.assertFalse(fleet.check_units()[0])  # dangling
                link.unlink()
                link.symlink_to(fleet.PLUGIN)
                self.assertTrue(fleet.check_units()[0])
                unit.write_text("ExecStart=python3 /old/cache/0.2.0/scripts/fleet.py status --notify\n")
                self.assertFalse(fleet.check_units()[0])  # an old-style unit

    def test_session_start_hook_repoints_an_existing_link_only(self):
        hook = Path(fleet.PLUGIN) / "hooks/refresh-fleet-link.sh"
        with tempfile.TemporaryDirectory() as d:
            link = Path(d) / "plugin"
            env = {**os.environ, "FLEET_PLUGIN_LINK": str(link), "CLAUDE_PLUGIN_ROOT": "/new/cache/1.0.0"}
            run = lambda: subprocess.run(["bash", str(hook)], env=env, capture_output=True, text=True)
            r = run()
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
            self.assertFalse(link.is_symlink())  # never created
            link.symlink_to("/old/cache/0.2.0")
            self.assertEqual(run().returncode, 0)
            self.assertEqual(os.readlink(link), "/new/cache/1.0.0")
            self.assertEqual(run().stdout, "")

    def test_session_start_hook_never_takes_the_link_to_an_older_plugin(self):
        hook = Path(fleet.PLUGIN) / "hooks/refresh-fleet-link.sh"
        with tempfile.TemporaryDirectory() as d:
            def plugin(version):
                root = Path(d) / version
                (root / ".claude-plugin").mkdir(parents=True)
                (root / ".claude-plugin/plugin.json").write_text(f'{{\n  "name": "dark-factory",\n  "version": "{version}"\n}}\n')
                return str(root)
            old, new, newer = plugin("9.0.0"), plugin("10.0.0"), plugin("11.0.0")
            link = Path(d) / "plugin"
            link.symlink_to(new)
            for root, expected in ((old, new), (newer, newer), (new, newer)):  # 10 < 9 as text: compared as versions
                env = {**os.environ, "FLEET_PLUGIN_LINK": str(link), "CLAUDE_PLUGIN_ROOT": root}
                r = subprocess.run(["bash", str(hook)], env=env, capture_output=True, text=True)
                self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
                self.assertEqual(os.readlink(link), expected)


class QuotaTest(unittest.TestCase):
    def test_reads_quota_from_this_plugin_not_the_project(self):
        with tempfile.TemporaryDirectory() as d:
            plugin = Path(d)
            with mock.patch.object(fleet, "PLUGIN", plugin):
                self.assertIsNone(fleet.check_quota())  # no quota script in this plugin dir
                script = plugin / "scripts/quota.py"
                script.parent.mkdir(parents=True)
                script.write_text("")
                with mock.patch.object(fleet, "sh", return_value=(0, "5h 42% (resets 21:30 UTC) · 7d 10%")) as sh:
                    self.assertEqual(fleet.check_quota(), (True, "5h 42% (resets 21:30 UTC) · 7d 10%"))
                self.assertEqual(sh.call_args.args[0][1:], [str(script), "status"])
                with mock.patch.object(fleet, "sh", return_value=(0, "5h 85% (resets 21:30 UTC) · 7d 10%")):
                    self.assertFalse(fleet.check_quota()[0])
                with mock.patch.object(fleet, "sh", return_value=(2, "no quota data")):
                    self.assertFalse(fleet.check_quota()[0])

    def test_the_real_plugin_ships_the_quota_script(self):
        self.assertTrue((Path(fleet.__file__).resolve().parent / "quota.py").is_file())


class FeedUnitsTest(unittest.TestCase):
    SRC = ["--projects", "/home/u/.config/dark-factory/projects"]

    def test_routines_check_goes_with_the_feed(self):
        self.assertNotIn("df-fleet-routines.timer", fleet.server_units("/usr/bin/python3", self.SRC))
        units = fleet.server_units("/usr/bin/python3", self.SRC, feed_addr="100.64.1.2:8765")
        self.assertIn('/scripts/fleet.py" "routines"', units["df-fleet-routines.service"])
        self.assertIn("OnCalendar=*-*-* 07:00:00 UTC", units["df-fleet-routines.timer"])
        self.assertIn("df-fleet-routines.timer", fleet.FEED_UNITS)  # removed with FLEET_FEED_ADDR

    def test_feed_units_only_with_setting(self):
        units = fleet.server_units("/usr/bin/python3", self.SRC)
        self.assertFalse({"df-fleet-feed.service", "df-fleet-feed.timer", "df-fleet-serve.service"} & set(units))
        units = fleet.server_units("/usr/bin/python3", self.SRC, feed_addr="100.64.1.2:8765")
        service, timer, serve = (units["df-fleet-feed.service"], units["df-fleet-feed.timer"],
                                 units["df-fleet-serve.service"])
        self.assertIn("Type=oneshot", service)
        self.assertIn('/scripts/fleet.py" "--projects" "/home/u/.config/dark-factory/projects" "feed"', service)
        self.assertNotIn("[Install]", service)
        self.assertIn("OnUnitActiveSec=15min", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("Unit=df-fleet-feed.service", timer)
        self.assertIn("[Install]", timer)
        # serve watches the projects' sessions, so it reads the same project list as feed
        self.assertIn('/scripts/fleet.py" "--projects" "/home/u/.config/dark-factory/projects" "serve"', serve)
        self.assertIn("Restart=on-failure", serve)
        self.assertIn("RestartPreventExitStatus=2", serve)  # a refused address is not retried forever
        self.assertIn("OnFailure=df-notify@%n.service", service.split("[Service]")[0])
        self.assertIn("OnFailure=df-notify@%n.service", serve.split("[Service]")[0])
        self.assertIn("[Install]", serve)

    def test_serve_gets_the_projects_and_the_address(self):
        import fleet_feed
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "shop").mkdir()
            projects = Path(d) / "projects"
            projects.write_text(f"{d}/shop\n")
            env = Path(d) / "fleet.env"
            env.write_text("FLEET_FEED_ADDR=100.64.1.2:8765\n")
            got = []
            serve = lambda addr, ps, token=None, page=False: got.append((addr, ps, token, page)) or 0  # noqa: E731
            with mock.patch.object(fleet, "FLEET_ENV", env), mock.patch.object(fleet_feed, "serve", serve):
                self.assertEqual(fleet.main(["serve", "--projects", str(projects)]), 0)
                env.write_text("FLEET_FEED_ADDR=100.64.1.2:8765\nFLEET_FEED_TOKEN=" + "k" * 40
                               + "\nFLEET_FEED_PAGE=on\n")
                self.assertEqual(fleet.main(["serve", "--projects", str(projects)]), 0)
        self.assertEqual(got[0][0], "100.64.1.2:8765")
        self.assertEqual([p.name for p in got[0][1]], ["shop"])
        self.assertEqual((got[0][2], got[1][2]), (None, "k" * 40))
        self.assertEqual((got[0][3], got[1][3]), (False, True))  # the phone page only with FLEET_FEED_PAGE=on

    def test_unprovision_removes_feed_units(self):
        with tempfile.TemporaryDirectory() as d:
            fleet.UNIT_DIR = Path(d)
            for n in ("df-shop-session.service", "df-fleet-feed.service", "df-fleet-feed.timer",
                      "df-fleet-serve.service"):
                (Path(d) / n).write_text("")
            calls = []
            with redirect_stdout(io.StringIO()), mock.patch.object(fleet, "run", lambda cmd, dry: calls.append(cmd) or 0):
                fleet.unprovision([fleet.Project("Shop", Path("/srv/shop"), "shop")], dry=False)
            self.assertEqual(list(Path(d).iterdir()), [])
            self.assertIn("df-fleet-feed.timer", calls[0])
            self.assertIn("df-fleet-serve.service", calls[0])

    def test_status_feed_line(self):
        with tempfile.TemporaryDirectory() as d:
            env, feed = Path(d) / "fleet.env", Path(d) / "status.json"
            with mock.patch.object(fleet, "FLEET_ENV", env), mock.patch.dict(os.environ, {"FLEET_FEED": str(feed)}):
                self.assertIsNone(fleet.check_feed())
                env.write_text('FLEET_FEED_ADDR="100.64.1.2:8765"\n')
                ok, text = fleet.check_feed()
                self.assertFalse(ok)
                self.assertIn("not written", text)
                feed.write_text("{}")
                ok, text = fleet.check_feed()
                self.assertTrue(ok)
                self.assertIn("served on 100.64.1.2:8765", text)
                old = time.time() - 46 * 60
                os.utime(feed, (old, old))
                ok, text = fleet.check_feed()
                self.assertFalse(ok)
                self.assertIn("not written for 46 min", text)

    def test_stale_feed_is_notified(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(fleet, "check_feed", lambda: (False, "not written for 50 min")), \
                    mock.patch.object(fleet, "check_quota", lambda: None), \
                    mock.patch.object(fleet, "check_analytics", lambda: None), \
                    mock.patch.object(fleet, "check_units", lambda: None), \
                    mock.patch.object(fleet, "FLEET_STATE", Path(d) / "state.json"), \
                    mock.patch.object(fleet, "notify", lambda msg: sent.append(msg) or 0):
                sent = []
                with redirect_stdout(io.StringIO()):
                    fleet.status([], notify_owner=True)
            self.assertTrue(any("feed" in m for m in sent), sent)


class AnalyticsUnitsTest(unittest.TestCase):
    SRC = ["--projects", "/home/u/.config/dark-factory/projects"]

    def test_analytics_units_only_with_setting(self):
        self.assertNotIn("df-fleet-analytics.timer", fleet.server_units("/usr/bin/python3", self.SRC))
        units = fleet.server_units("/usr/bin/python3", self.SRC, analytics_repo="/home/u/canon")
        self.assertIn("df-fleet-analytics.service", units)
        self.assertIn("df-fleet-analytics.timer", units)

    def test_analytics_timer_monday(self):
        timer = fleet.server_units("p", self.SRC, analytics_repo="/r")["df-fleet-analytics.timer"]
        self.assertIn("OnCalendar=Mon *-*-* 05:20:00 UTC", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("Unit=df-fleet-analytics.service", timer)
        self.assertIn("[Install]", timer)

    def test_analytics_service_runs_collect_then_publish(self):
        service = fleet.server_units("/usr/bin/python3", self.SRC, analytics_repo="/home/u/canon")[
            "df-fleet-analytics.service"]
        execs = [l for l in service.splitlines() if l.startswith("ExecStart=")]
        self.assertEqual(len(execs), 2)
        self.assertIn('/scripts/analytics.py" "collect" "--since-last" "--canon" "/home/u/canon"', execs[0])
        self.assertIn('/scripts/analytics.py" "publish" "--repo" "/home/u/canon"', execs[1])
        self.assertIn("OnFailure=df-notify@%n.service", service.split("[Service]")[0])
        self.assertIn("Type=oneshot", service)
        self.assertNotIn("[Install]", service)

    def test_unprovision_removes_analytics_pair(self):
        with tempfile.TemporaryDirectory() as d:
            fleet.UNIT_DIR = Path(d)
            for n in ("df-shop-session.service", "df-fleet-watch.timer", "df-fleet-analytics.service",
                      "df-fleet-analytics.timer"):
                (Path(d) / n).write_text("")
            calls = []
            with redirect_stdout(io.StringIO()), mock.patch.object(fleet, "run", lambda cmd, dry: calls.append(cmd) or 0):
                fleet.unprovision([fleet.Project("Shop", Path("/srv/shop"), "shop")], dry=False)
            self.assertEqual(list(Path(d).iterdir()), [])
            self.assertIn("df-fleet-analytics.timer", calls[0])

    def test_status_analytics_line(self):
        with tempfile.TemporaryDirectory() as d:
            env, state = Path(d) / "fleet.env", Path(d) / "state"
            with mock.patch.object(fleet, "FLEET_ENV", env), mock.patch.dict(os.environ, {"ANALYTICS_STATE": str(state)}):
                self.assertIsNone(fleet.check_analytics())
                env.write_text('FLEET_ANALYTICS_REPO="/r"\n')
                ok, text = fleet.check_analytics()
                self.assertFalse(ok)
                self.assertIn("nothing collected", text)
                state.mkdir()
                week = time.strftime("%G-W%V", time.gmtime())
                (state / f"{week}.json").write_text("{}")
                ok, text = fleet.check_analytics()
                self.assertTrue(ok)
                self.assertIn(week, text)
                (state / f"{week}.json").unlink()
                (state / "2026-W01.json").write_text("{}")
                self.assertFalse(fleet.check_analytics()[0])


class StandTest(unittest.TestCase):
    """A real repository: origin (bare) → the main checkout the stand runs, and a second clone that pushes to main."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.origin, self.repo, self.dev = d / "origin.git", d / "shop", d / "dev"
        self.g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]
        subprocess.run([*self.g, "init", "-q", "--bare", str(self.origin)], check=True)
        subprocess.run([*self.g, "clone", "-q", str(self.origin), str(self.dev)], check=True, capture_output=True)
        (self.dev / "scripts").mkdir()
        # the project's updater records what it ran on, and fails while a file says so
        (self.dev / "scripts/stand-update.sh").write_text(
            '#!/bin/sh\necho "built $STAND_SHA" >> "$HOME_LOG"\n[ ! -f fail ] || { echo "migration broke"; exit 3; }\n')
        (self.dev / "scripts/stand-update.sh").chmod(0o755)
        self.commit("a")
        self.git("remote", "set-head", "origin", "main", cwd=self.dev)
        subprocess.run([*self.g, "clone", "-q", str(self.origin), str(self.repo)], check=True, capture_output=True)
        self.git("switch", "-q", "--detach", "origin/main")  # a stand checkout is detached on main
        self.log = d / "built.log"
        self.p = fleet.Project("Shop", self.repo, "shop")
        for patch in (mock.patch.object(fleet, "FLEET_STATE", d / "state/fleet-watch.json"),
                      mock.patch.object(fleet, "UNIT_DIR", d / "units"),
                      mock.patch.dict(os.environ, {"HOME_LOG": str(self.log)})):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, cwd=None):
        return subprocess.run([*self.g, *args], cwd=cwd or self.repo, check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(self, name, fail=False):
        (self.dev / name).write_text(name)
        if fail:
            (self.dev / "fail").write_text("")
        elif (self.dev / "fail").exists():
            (self.dev / "fail").unlink()
        self.git("add", "-A", cwd=self.dev)
        self.git("commit", "-qm", name, cwd=self.dev)
        self.git("push", "-q", "origin", "main", cwd=self.dev)
        return self.git("rev-parse", "HEAD", cwd=self.dev)

    def run_stand(self, retry=False):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(fleet.stand(self.p, retry), 0)
        return fleet.read_stand_state(self.p)

    def built(self):
        return self.log.read_text().split() if self.log.exists() else []

    def test_units_only_with_the_script(self):
        self.assertNotIn("df-shop-stand.timer", fleet.units_for(fleet.Project("Shop", Path(self.tmp.name), "shop"),
                                                                "c", "t"))
        units = fleet.units_for(self.p, "c", "t")
        service, timer = units["df-shop-stand.service"], units["df-shop-stand.timer"]
        self.assertIn(f'"stand" "{self.repo}"', service)
        self.assertIn("KillMode=process", service)  # the stand outlives the oneshot
        self.assertIn("OnFailure=df-notify@%n.service", service.split("[Service]")[0])
        self.assertNotIn("[Install]", service)
        self.assertIn("OnUnitActiveSec=1min", timer)
        self.assertIn("Unit=df-shop-stand.service", timer)

    def test_first_run_on_main_records_without_restarting(self):
        state = self.run_stand()
        self.assertTrue(state["ok"])
        self.assertEqual(self.built(), [])  # a stand someone may be using is not restarted for nothing
        self.run_stand()
        self.assertEqual(self.built(), [])

    def test_a_new_commit_moves_the_checkout_and_runs_the_script(self):
        self.run_stand()
        sha = self.commit("b")
        state = self.run_stand()
        self.assertTrue(state["ok"], state)
        self.assertEqual(self.git("rev-parse", "HEAD"), sha)
        self.assertEqual(self.built(), ["built", sha])
        self.run_stand()  # nothing new: no second build
        self.assertEqual(self.built(), ["built", sha])

    def test_a_branch_checkout_on_main_is_fast_forwarded(self):
        self.git("switch", "-q", "main")
        self.run_stand()
        sha = self.commit("b")
        self.assertTrue(self.run_stand()["ok"])
        self.assertEqual(self.git("rev-parse", "main"), sha)

    def test_a_failed_update_waits_for_the_next_commit(self):
        self.run_stand()
        bad = self.commit("b", fail=True)
        state = self.run_stand()
        self.assertFalse(state["ok"])
        self.assertIn("migration broke", state["text"])
        self.assertIn("journalctl --user -u df-shop-stand", state["text"])
        self.run_stand()
        self.assertEqual(self.built(), ["built", bad])  # not retried every minute
        self.run_stand(retry=True)
        self.assertEqual(self.built(), ["built", bad, "built", bad])
        good = self.commit("c")
        self.assertTrue(self.run_stand()["ok"])
        self.assertEqual(self.built()[-1], good)

    def test_a_checkout_with_changes_or_on_another_branch_is_left_alone(self):
        self.run_stand()
        before = self.git("rev-parse", "HEAD")
        self.commit("b")
        (self.repo / "a").write_text("edited by a session in same-dir")
        state = self.run_stand()
        self.assertFalse(state["ok"])
        self.assertIn("uncommitted changes", state["text"])
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.git("checkout", "-q", "--", "a")
        self.git("switch", "-q", "-c", "feature")
        state = self.run_stand()
        self.assertIn("on branch feature", state["text"])
        self.assertEqual(self.built(), [])

    def test_status_line_and_notice(self):
        self.assertIsNone(fleet.check_stand(fleet.Project("Shop", Path(self.tmp.name), "shop")))  # no script, no line
        ok, text = fleet.check_stand(self.p)
        self.assertFalse(ok)
        self.assertIn("not provisioned", text)
        (Path(self.tmp.name) / "units").mkdir()
        (Path(self.tmp.name) / "units/df-shop-stand.timer").write_text("")
        self.assertEqual(fleet.check_stand(self.p), (True, "no update recorded yet"))
        self.run_stand()
        self.commit("b", fail=True)
        self.run_stand()
        ok, text = fleet.check_stand(self.p)
        self.assertFalse(ok)
        self.assertIn("failed", text)
        self.assertIn("dev stand", fleet.NOTIFY_LABELS)

    def test_main_runs_stand_for_a_checkout(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(fleet.main(["stand", str(self.repo)]), 0)
        self.assertTrue(fleet.read_stand_state(self.p)["ok"])
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(fleet.main(["stand", self.tmp.name]), 2)  # no script there

    def test_provision_removes_the_units_when_the_script_is_gone(self):
        units = Path(self.tmp.name) / "units"
        units.mkdir()
        for n in ("df-shop-stand.service", "df-shop-stand.timer"):
            (units / n).write_text("")
        p = fleet.Project("Shop", Path(self.tmp.name), "shop")
        out = io.StringIO()
        with redirect_stdout(out), mock.patch("sys.stderr", io.StringIO()), \
                mock.patch.object(fleet, "FLEET_ENV", Path(self.tmp.name) / "fleet.env"), \
                mock.patch.object(fleet, "PLUGIN_LINK", Path(self.tmp.name) / "share/plugin"), \
                mock.patch.object(fleet.shutil, "which", lambda n, path=None: f"/bin/{n}"), \
                mock.patch.object(fleet, "platform_ok", lambda: True), \
                mock.patch.object(fleet, "run", UnitsTest.fake_run):
            self.assertEqual(fleet.provision([p], dry=False), 0)
        self.assertFalse((units / "df-shop-stand.timer").exists())
        self.assertIn("disable --now df-shop-stand.timer df-shop-stand.service", out.getvalue())


class UpgradeTest(unittest.TestCase):
    """`fleet.py upgrade v<N>`: the owner's pin, the marketplace, the projects, the new plugin's units, sessions on yes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.claude = self.tmp / "claude"
        self.units = self.tmp / "units"
        self.units.mkdir()
        self.pin(self.claude / "settings.json", "v12")
        self.shop = fleet.Project("shop", self.tmp / "shop", "shop")
        self.pin(self.shop.path / ".claude/settings.json", "v13")
        new = self.claude / "plugins/cache/dark-factory/dark-factory/13.0.0/scripts/fleet.py"
        new.parent.mkdir(parents=True)
        new.write_text("")
        (self.units / "df-fleet-serve.service").write_text("")
        (self.units / "df-shop-session.service").write_text("")
        (self.units / "df-shop-desk.service").write_text("")
        (self.units / "df-shop-stand.service").write_text("")
        self.calls = []

    def tearDown(self):
        self._tmp.cleanup()

    def pin(self, path, ref, others=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**(others or {}), "extraKnownMarketplaces": {"dark-factory": {"source": {
            "source": "github", "repo": "o/dark-factory", "ref": ref}}}}))

    def sh(self, cmd, cwd=None, timeout=20):
        self.calls.append((cmd, cwd))
        if cmd[:2] == ["git", "ls-remote"]:
            return 0, "abc\trefs/tags/v13" if self.tag_exists else ""
        return 0, ""

    def upgrade(self, tag="v13", dry=False, answer="n", tty=True, tag_exists=True):
        self.tag_exists = tag_exists
        out = io.StringIO()
        with mock.patch.object(fleet, "CLAUDE_DIR", self.claude), mock.patch.object(fleet, "UNIT_DIR", self.units), \
                mock.patch.object(fleet, "sh", self.sh), \
                mock.patch.object(fleet, "move_checkout", lambda p: "on origin/main"), \
                mock.patch.object(fleet.shutil, "which", lambda name, path=None: f"/bin/{name}"), \
                mock.patch.object(fleet.sys.stdin, "isatty", lambda: tty), redirect_stdout(out):
            code = fleet.upgrade([self.shop], tag, dry, ["--projects", "/p"], ask=lambda q: answer)
        return code, out.getvalue()

    def cmds(self):
        return [c for c, _ in self.calls]

    def test_not_a_tag_or_no_such_release(self):
        self.assertEqual(self.upgrade("13")[0], 2)
        self.assertEqual(self.upgrade(tag_exists=False)[0], 2)
        self.assertEqual(fleet.pin_of(self.claude / "settings.json"), ("o/dark-factory", "v12"))  # untouched

    def test_moves_the_pin_keeping_the_rest_of_the_settings(self):
        self.pin(self.claude / "settings.json", "v12", {"env": {"QUOTA_5H": "80"}})
        code, _ = self.upgrade()
        self.assertEqual(code, 0)
        doc = json.loads((self.claude / "settings.json").read_text())
        self.assertEqual(doc["extraKnownMarketplaces"]["dark-factory"]["source"]["ref"], "v13")
        self.assertEqual(doc["env"], {"QUOTA_5H": "80"})

    def test_runs_the_steps_in_order_with_the_new_plugin(self):
        self.upgrade()
        cmds = self.cmds()
        add = cmds.index(["/bin/claude", "plugin", "marketplace", "add", "o/dark-factory#v13"])
        update = cmds.index(["/bin/claude", "plugin", "update", "dark-factory@dark-factory", "--scope", "project"])
        new = str(self.claude / "plugins/cache/dark-factory/dark-factory/13.0.0/scripts/fleet.py")
        provision = cmds.index([sys.executable, new, "provision", "--projects", "/p"])
        serve = cmds.index(["systemctl", "--user", "restart", "df-fleet-serve.service"])
        self.assertLess(add, update)
        self.assertLess(update, provision)
        self.assertLess(provision, serve)
        self.assertEqual(self.calls[update][1], self.shop.path)

    def test_sessions_restart_only_on_yes(self):
        _, out = self.upgrade(answer="n")
        self.assertFalse(any(c[:3] == ["systemctl", "--user", "restart"] and "df-shop-session.service" in c
                             for c in self.cmds()))
        self.assertIn("skipped", out)
        self.calls = []
        self.upgrade(answer="y")
        self.assertIn(["systemctl", "--user", "restart", "df-shop-desk.service", "df-shop-session.service"],
                      self.cmds())  # not the stand

    def test_without_a_terminal_sessions_are_left_with_the_command(self):
        _, out = self.upgrade(answer="y", tty=False)
        self.assertIn("restart by hand", out)
        self.assertFalse(any("df-shop-session.service" in c for c in self.cmds()))

    def test_dry_run_changes_nothing(self):
        code, out = self.upgrade(dry=True, answer="y")
        self.assertEqual(code, 0)
        self.assertEqual(fleet.pin_of(self.claude / "settings.json")[1], "v12")
        self.assertEqual([c for c in self.cmds() if c[:2] != ["git", "ls-remote"]], [])
        self.assertIn("would run:", out)

    def test_a_project_on_an_older_pin_is_named(self):
        self.pin(self.shop.path / ".claude/settings.json", "v12")
        _, out = self.upgrade()
        self.assertIn("shop (pins v12)", out)

    def test_missing_new_plugin_stops_before_provision(self):
        (self.claude / "plugins/cache/dark-factory/dark-factory/13.0.0/scripts/fleet.py").unlink()
        code, _ = self.upgrade()
        self.assertEqual(code, 1)
        self.assertFalse(any("provision" in c for c in self.cmds()))


if __name__ == "__main__":
    unittest.main()
