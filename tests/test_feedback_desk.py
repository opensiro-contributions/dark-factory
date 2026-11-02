#!/usr/bin/env python3
"""Tests of templates/feedback-desk/scripts/feedback-desk.sh: the permission rules and the On-call's two Bash
subcommands (accept, release). gh and release.sh are stubs; stdlib only."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
import tempfile
import unittest
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "plugins" / "dark-factory" / "templates" / "feedback-desk"
LINK = "https://t.me/c/123/45"

GH_STUB = """#!/usr/bin/env bash
dir="$(dirname "$0")"
echo "$*" >> "$dir/gh.log"
if [ "$1 $2" = "pr view" ]; then cat "$dir/pr-$3.json" 2>/dev/null || exit 1; fi
exit 0
"""

RELEASE_STUB = """#!/usr/bin/env bash
dir="$(dirname "$0")/.."
if [ "$1" = "--list-unaccepted" ]; then cat "$dir/unaccepted.tsv" 2>/dev/null; exit 0; fi
echo "DF_ROLE=${DF_ROLE:-} $*" >> "$dir/release.log"
echo "${RELEASE_GH:-} ${RELEASE_MAIN_BRANCH:-}" >> "$dir/release-env.log"
"""


class DeskCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "scripts").mkdir()
        (self.root / ".claude/feedback-desk").mkdir(parents=True)
        shutil.copy(TEMPLATE / "scripts/feedback-desk.sh", self.root / "scripts/feedback-desk.sh")
        shutil.copy(TEMPLATE / ".claude/feedback-desk/config.env", self.root / ".claude/feedback-desk/config.env")
        (self.root / ".claude/feedback-desk/ROLE.md").write_text("role\n")
        # who may accept and which gh runs come from the config file, not from the environment
        with open(self.root / ".claude/feedback-desk/config.env", "a") as f:
            f.write(f'DESK_GH="{self.root / "gh"}"\nDESK_OWNER_IDS="1, 2"\n')
        for path, text in ((self.root / "gh", GH_STUB), (self.root / "scripts/release.sh", RELEASE_STUB)):
            path.write_text(text)
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        self.env = {
            **{k: v for k, v in os.environ.items() if not k.startswith(("DESK_", "DF_"))},
            "DESK_WORKDIR": str(self.root),
            "DESK_STATE_DIR": str(self.root / "state"),
            "DESK_PLUGIN": "telegram@claude-plugins-official",
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def desk(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(self.root / "scripts/feedback-desk.sh"), *args],
                              env={**self.env, **env}, capture_output=True, text=True, cwd=self.root)

    def pr(self, number: int, *, state: str = "MERGED", labels: tuple[str, ...] = (), requested: str | None = None) -> None:
        body = "## Done\nx\n"
        if requested is not None:
            body += f"\n## Requested by\n{requested}\n\n## Open questions\nnone\n"
        (self.root / f"pr-{number}.json").write_text(
            json.dumps({"state": state, "labels": [{"name": n} for n in labels], "body": body}))

    def gh_calls(self) -> list[str]:
        log = self.root / "gh.log"
        return log.read_text().splitlines() if log.exists() else []

    def release_calls(self) -> list[str]:
        log = self.root / "release.log"
        return log.read_text().splitlines() if log.exists() else []

    def unaccepted(self, *rows: str) -> None:
        (self.root / "unaccepted.tsv").write_text("".join(r + "\n" for r in rows))


class RulesTest(DeskCase):
    def allow(self, **env: str) -> list[str]:
        out = self.desk("rules", **env)
        self.assertEqual(out.returncode, 0, out.stderr)
        line = next(x for x in out.stdout.splitlines() if x.startswith("allow: "))
        return line[len("allow: "):].split(",")

    def test_telegram_tools_are_allowed_by_name_next_to_the_server_rule(self) -> None:
        rules = self.allow()
        server = "mcp__plugin_telegram_telegram"
        self.assertIn(server, rules)
        for tool in ("reply", "react", "edit_message", "download_attachment"):
            self.assertIn(f"{server}__{tool}", rules)

    def test_bash_is_allowed_only_for_accept_and_release(self) -> None:
        bash = [r for r in self.allow() if r.startswith("Bash")]
        self.assertEqual(bash, ["Bash(scripts/feedback-desk.sh accept *)", "Bash(scripts/feedback-desk.sh release)",
                                "Bash(scripts/feedback-desk.sh release *)"])

    def test_board_tools_come_from_the_config(self) -> None:
        self.assertFalse([r for r in self.allow() if "Linear" in r])
        rules = self.allow(DESK_BOARD_TOOLS="mcp__claude_ai_Linear__save_issue,mcp__claude_ai_Linear__save_comment")
        self.assertIn("mcp__claude_ai_Linear__save_issue", rules)
        self.assertIn("mcp__claude_ai_Linear__save_comment", rules)

    def test_session_gets_the_on_call_role_in_both_environments(self) -> None:
        text = (TEMPLATE / "scripts/feedback-desk.sh").read_text()
        self.assertIn("DF_UNATTENDED=1 DF_ROLE=on-call", text)  # the env prefix
        self.assertIn('\\"DF_ROLE\\":\\"on-call\\"', text)  # --settings env

    def test_session_enables_the_channel_plugin_for_itself(self) -> None:
        # The plugin stays off user-wide (MANIFEST): only the On-call turns it on, in its --settings layer.
        text = (TEMPLATE / "scripts/feedback-desk.sh").read_text()
        self.assertIn('\\"enabledPlugins\\":{\\"$DESK_PLUGIN\\":true}', text)


class AcceptTest(DeskCase):
    def test_anyone_on_the_list_accepts_any_merged_pr(self) -> None:
        self.pr(12, requested="Vlad (tg 42)")
        self.pr(13)

        for number in ("12", "13"):
            self.assertEqual(self.desk("accept", number, "2", LINK).returncode, 0)

        calls = self.gh_calls()
        self.assertIn("pr edit 12 --add-label accepted", calls)
        self.assertIn(f"pr comment 13 --body accepted by tg 2, {LINK}", calls)

    def test_a_message_id_stands_for_a_link_in_a_plain_group(self) -> None:
        self.pr(12)

        out = self.desk("accept", "12", "1", "325")

        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("pr comment 12 --body accepted by tg 1, 325", self.gh_calls())

    def test_someone_off_the_list_is_refused_even_as_the_requester(self) -> None:
        self.pr(12, requested="Vlad (tg 42)")

        out = self.desk("accept", "12", "42", LINK)

        self.assertEqual(out.returncode, 1)
        self.assertIn("not in DESK_OWNER_IDS", out.stdout)
        self.assertFalse([c for c in self.gh_calls() if c.startswith(("pr edit", "pr comment"))])

    def test_unmerged_or_unreadable_pr_is_refused(self) -> None:
        self.pr(12, state="OPEN")
        self.assertEqual(self.desk("accept", "12", "1", LINK).returncode, 1)
        self.assertEqual(self.desk("accept", "77", "1", LINK).returncode, 1)
        self.assertFalse([c for c in self.gh_calls() if c.startswith(("pr edit", "pr comment"))])

    def test_already_accepted_is_a_noop(self) -> None:
        self.pr(12, labels=("accepted",))

        out = self.desk("accept", "12", "1", LINK)

        self.assertEqual(out.returncode, 0)
        self.assertIn("already accepted", out.stdout)
        self.assertFalse([c for c in self.gh_calls() if c.startswith(("pr edit", "pr comment"))])

    def test_malformed_arguments_are_refused(self) -> None:
        self.pr(12, requested="Vlad (tg 42)")
        for args in (("12", "1"), ("x", "1", LINK), ("12", "4 2", LINK), ("12", "1", "not a link"),
                     ("12", "1", "https://t.me/x; rm -rf /"), ("12", "1", "$(id)")):
            self.assertNotEqual(self.desk("accept", *args).returncode, 0, args)
        self.assertFalse([c for c in self.gh_calls() if c.startswith(("pr edit", "pr comment"))])


class InheritedEnvironmentTest(DeskCase):
    """DESK_OWNER_IDS, DESK_GH and RELEASE_GH inherited from the process do not stand in for the config."""

    def test_inherited_owner_ids_do_not_make_an_owner(self) -> None:
        self.pr(12, requested="Vlad (tg 42)")

        out = self.desk("accept", "12", "99", LINK, DESK_OWNER_IDS="99")

        self.assertEqual(out.returncode, 1)
        self.assertFalse([c for c in self.gh_calls() if c.startswith(("pr edit", "pr comment"))])

    def test_inherited_gh_command_is_not_run(self) -> None:
        self.pr(12, requested="Vlad (tg 42)")
        fake = self.root / "fake-gh"
        fake.write_text(f'#!/usr/bin/env bash\necho "$*" >> {self.root}/fake.log\nexit 0\n')
        fake.chmod(0o755)

        out = self.desk("accept", "12", "1", LINK, DESK_GH=str(fake))

        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse((self.root / "fake.log").exists())
        self.assertIn("pr edit 12 --add-label accepted", self.gh_calls())

    def test_release_does_not_pass_release_gh_on(self) -> None:
        out = self.desk("release", RELEASE_GH="/tmp/evil", RELEASE_MAIN_BRANCH="other")

        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual((self.root / "release-env.log").read_text().strip(), "")

    def test_a_failing_gh_edit_is_a_failure(self) -> None:
        self.pr(12, requested="Vlad (tg 42)")
        bad = self.root / "bad-gh"
        bad.write_text(f'#!/usr/bin/env bash\n[ "$1 $2" = "pr view" ] && {{ cat {self.root}/pr-12.json; exit 0; }}\n[ "$1 $2" = "pr edit" ] && exit 1\nexit 0\n')
        bad.chmod(0o755)
        with open(self.root / ".claude/feedback-desk/config.env", "a") as f:
            f.write(f'DESK_GH="{bad}"\n')

        self.assertEqual(self.desk("accept", "12", "1", LINK).returncode, 1)

    def test_a_failing_listing_refuses_the_release(self) -> None:
        (self.root / "scripts/release.sh").write_text('#!/usr/bin/env bash\n[ "$1" = --list-unaccepted ] && { echo "12\tx\tVlad (tg 42)"; exit 1; }\n')

        out = self.desk("release", "--include-unaccepted", "1", LINK)

        self.assertEqual(out.returncode, 1)


class ReleaseTest(DeskCase):
    def test_plain_release_runs_the_script_as_the_on_call(self) -> None:
        out = self.desk("release")

        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.release_calls(), ["DF_ROLE=on-call "])

    def test_anyone_on_the_list_may_include_anything_even_a_non_pr_merge(self) -> None:
        self.unaccepted("12\tDark theme\tPasha (tg 7)", "-\tMerge branch hotfix\tnot a pull request (abc12345)")

        out = self.desk("release", "--include-unaccepted", "2", LINK)

        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.release_calls(), [f"DF_ROLE=on-call --include-unaccepted tg 2, {LINK}"])

    def test_someone_off_the_list_may_not_include_even_their_own_request(self) -> None:
        self.unaccepted("12\tDark theme\tVlad (tg 42)")

        self.assertEqual(self.desk("release", "--include-unaccepted", "42", LINK).returncode, 1)
        self.assertEqual(self.release_calls(), [])

    def test_nothing_unaccepted_means_a_plain_release(self) -> None:
        self.unaccepted()

        out = self.desk("release", "--include-unaccepted", "42", LINK)

        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.release_calls(), ["DF_ROLE=on-call "])

    def test_other_options_are_refused(self) -> None:
        for args in (("--owner-accepts",), ("--manual",), ("--include-unaccepted", "42"),
                     ("--include-unaccepted", "x", LINK), ("--include-unaccepted", "1", "no pe")):
            self.assertNotEqual(self.desk("release", *args).returncode, 0, args)
        self.assertEqual(self.release_calls(), [])


@unittest.skipUnless(shutil.which("tmux"), "needs tmux")
class StatusTest(DeskCase):
    """`status` (and `up`, the watch timer) must not take a restored empty window of the same name for the On-call."""

    def setUp(self) -> None:
        super().setUp()
        self.env.pop("TMUX", None)
        self.env["TMUX_TMPDIR"] = str(self.root)  # a tmux server of its own
        self.env["DESK_TMUX"] = "desk"
        claude = self.root / "bin/claude"
        claude.parent.mkdir()
        claude.write_text("#!/bin/sh\nexec sleep 60\n")
        claude.chmod(0o755)

    def tearDown(self) -> None:
        subprocess.run(["tmux", "kill-server"], env=self.env, capture_output=True)
        super().tearDown()

    def session(self, *cmd: str) -> None:
        subprocess.run(["tmux", "-f", "/dev/null", "new-session", "-d", "-s", "desk", *cmd], env=self.env, check=True)

    def test_a_window_without_claude_is_not_the_on_call(self) -> None:
        self.session("sleep", "60")
        self.assertEqual(self.desk("status").returncode, 1)

    def test_a_claude_session_is_up(self) -> None:
        self.session(str(self.root / "bin/claude"), "--name", "x")
        self.assertEqual(self.desk("status").returncode, 0)

    def test_no_session_is_down(self) -> None:
        self.assertEqual(self.desk("status").returncode, 1)

    @unittest.skipUnless(shutil.which("flock"), "needs flock")
    def test_up_waits_for_a_start_in_progress(self) -> None:
        # the desk unit and its watch timer start together at boot; the second `up` must not start a duplicate
        self.env["XDG_RUNTIME_DIR"] = str(self.root)
        lock = self.root / "feedback-desk-desk.lock"
        first = subprocess.Popen(["flock", "-o", str(lock), "sh", "-c", "sleep 1; " + " ".join(
            ["tmux", "-f", "/dev/null", "new-session", "-d", "-s", "desk", str(self.root / "bin/claude")])],
            env=self.env)
        time.sleep(0.3)
        out = self.desk("up")
        first.wait()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("already running", out.stdout)

    def test_run_from_inside_another_tmux_server(self) -> None:
        # a development session's pane: TMUX points at its own server, the On-call is in the default one
        self.session(str(self.root / "bin/claude"), "--name", "x")
        other = f"{self.root}/df-shop-socket,1,0"
        self.assertEqual(self.desk("status", TMUX=other, TMUX_PANE="%0").returncode, 0)


if __name__ == "__main__":
    unittest.main()
