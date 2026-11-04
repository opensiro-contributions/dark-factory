"""Module release-tag: releasing to prod happens only from a tag.

`scripts/release.sh` puts the next date-based tag on origin/main (a release
manager), `--publish <tag>` moves the `release` branch (built by the hosting)
to the tag plus a commit with the VERSION file, `--manual --reason --by` is an
emergency release without Actions, refused at FENCE_LEVEL=team. A normal release
also checks that every PR merged since the last tag carries the label `accepted`. The tests run the script against a temporary bare repository;
`gh` (GitHub Release) is replaced by a stub that writes calls to gh.log.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # app/tests/integration/ → repo root
SCRIPT = REPO_ROOT / "scripts" / "release.sh"
DAY = "2026.01.15"
MANUAL = ("--manual", "--reason", "Actions outage", "--by", "Ann Owner")


def _isolated_env(**extra: str) -> dict[str, str]:
    # The pre-push hook runs pytest with GIT_DIR/GIT_INDEX_FILE of the real repo:
    # inherited, they redirect git from the temporary repo into the real one (a
    # test would rewrite user.name/email in the main repository's .git/config).
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return {**env, **extra}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=_isolated_env(), check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "clone", str(origin), str(work))
    _git(work, "config", "user.email", "dev@example.com")
    _git(work, "config", "user.name", "Dev")
    (work / "VERSION").write_text("dev\n")
    _git(work, "add", "VERSION")
    # every first-parent commit since the last tag is checked, so the root commit is a (squash-merged) accepted PR
    _git(work, "commit", "-m", "init (#1)")
    _git(work, "push", "origin", "main")
    (tmp_path / "pr-1.json").write_text('{"labels": [{"name": "accepted"}], "body": ""}')
    _write_gh_stub(tmp_path)
    return work


GH_STUB = """#!/usr/bin/env bash
dir="$(dirname "$0")"
if [ "$1 $2" = "pr view" ]; then
  echo "$*" >> "$dir/gh.log"
  cat "$dir/pr-$3.json" 2>/dev/null || exit 1
  exit 0
fi
if [ "$1" = "pr" ] || [ "$1" = "label" ]; then
  echo "$*" >> "$dir/gh.log"
  exit 0
fi
if [ "$1 $2" = "release view" ]; then
  grep -q "^release create $3 " "$dir/gh.log" 2>/dev/null
  exit $?
fi
[ -e "$dir/gh.fail" ] && exit 1
echo "$*" >> "$dir/gh.log"
"""


def _write_gh_stub(tmp_path: Path) -> None:
    stub = tmp_path / "gh"
    stub.write_text(GH_STUB)
    stub.chmod(0o755)


def _gh_calls(clone: Path) -> list[str]:
    log = clone.parent / "gh.log"
    # the fixture's root commit is PR #1: its acceptance lookup is not what the tests count
    return [c for c in log.read_text().splitlines() if not c.startswith("pr view 1 ")] if log.exists() else []


def _release(clone: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    env = _isolated_env(
        **{"RELEASE_DATE": DAY, "RELEASE_GH": str(clone.parent / "gh"), "RELEASE_MAIN_BRANCH": "main", **extra}
    )
    return subprocess.run(
        ["bash", str(SCRIPT), *args], cwd=clone, env=env, capture_output=True, text=True
    )


def _remote_tags(clone: Path) -> list[str]:
    out = _git(clone, "ls-remote", "--tags", "--refs", "origin")
    return sorted(line.split("refs/tags/")[1] for line in out.splitlines())


def _remote_release_version(clone: Path) -> str:
    _git(clone, "fetch", "-q", "origin", "release")
    return _git(clone, "show", "FETCH_HEAD:VERSION")


def test_first_release_of_the_day_tags_origin_main(clone: Path) -> None:
    result = _release(clone)

    assert result.returncode == 0, result.stderr
    assert _remote_tags(clone) == [f"v{DAY}.1"]
    main_sha = _git(clone, "rev-parse", "origin/main")
    assert _git(clone, "rev-list", "-n1", f"v{DAY}.1") == main_sha


def test_next_release_same_day_increments_counter(clone: Path) -> None:
    assert _release(clone).returncode == 0

    result = _release(clone)

    assert result.returncode == 0, result.stderr
    assert _remote_tags(clone) == [f"v{DAY}.1", f"v{DAY}.2"]


def test_tag_alone_does_not_touch_release_branch(clone: Path) -> None:
    # Only a verified release moves the release branch (tag workflow or --manual).
    _release(clone)

    assert "refs/heads/release" not in _git(clone, "ls-remote", "origin")


def test_publish_points_release_at_tag_plus_version_commit(clone: Path) -> None:
    _release(clone)
    tag = f"v{DAY}.1"
    tag_sha = _git(clone, "rev-list", "-n1", tag)

    result = _release(clone, "--publish", tag)

    assert result.returncode == 0, result.stderr
    _git(clone, "fetch", "-q", "origin", "release")
    assert _git(clone, "rev-parse", "FETCH_HEAD^") == tag_sha
    assert _remote_release_version(clone) == f"{tag} ({tag_sha[:8]})"


def _new_main_commit(clone: Path, name: str) -> None:
    """A squash-merged, accepted PR (subject "<name> (#N)")."""
    number = 100 + sum(map(ord, name)) % 800
    (clone.parent / f"pr-{number}.json").write_text('{"labels": [{"name": "accepted"}], "body": ""}')
    (clone / name).write_text(name)
    _git(clone, "add", name)
    _git(clone, "commit", "-m", f"{name} (#{number})")
    _git(clone, "push", "-q", "origin", "main")


def test_successive_releases_merge_cleanly_into_hosting_master(clone: Path) -> None:
    # Some hostings do not build the branch as is — they MERGE release into their
    # own master. If release were recreated from scratch, the second release would
    # conflict on VERSION. So release grows forward: a new release descends from
    # the previous one.
    base = _git(clone, "rev-parse", "HEAD")
    _release(clone)
    _release(clone, "--publish", f"v{DAY}.1")
    _git(clone, "fetch", "-q", "origin", "release")
    first = _git(clone, "rev-parse", "FETCH_HEAD")
    _new_main_commit(clone, "feature.txt")
    _release(clone)

    result = _release(clone, "--publish", f"v{DAY}.2")

    assert result.returncode == 0, result.stderr
    _git(clone, "fetch", "-q", "origin", "release")
    second = _git(clone, "rev-parse", "FETCH_HEAD")
    _git(clone, "checkout", "-q", "-b", "hosting-master", base)
    _git(clone, "merge", "-q", "--no-edit", first)
    _git(clone, "merge", "-q", "--no-edit", second)
    tag2_sha = _git(clone, "rev-list", "-n1", f"v{DAY}.2")
    assert (clone / "VERSION").read_text().strip() == f"v{DAY}.2 ({tag2_sha[:8]})"
    assert (clone / "feature.txt").exists()


def test_release_content_equals_tag_plus_version(clone: Path) -> None:
    _release(clone)
    _release(clone, "--publish", f"v{DAY}.1")
    _new_main_commit(clone, "feature.txt")
    _release(clone)
    _release(clone, "--publish", f"v{DAY}.2")

    _git(clone, "fetch", "-q", "origin", "release")
    changed = _git(clone, "diff", "--name-only", f"v{DAY}.2", "FETCH_HEAD")

    assert changed == "VERSION"


def test_publish_when_local_tag_differs_from_remote(clone: Path) -> None:
    # actions/checkout on a tag creates a local lightweight tag while the tag on
    # origin is annotated — `git fetch --tags` without --force refuses to
    # overwrite it, and publish must not fail silently.
    _release(clone)
    tag = f"v{DAY}.1"
    tag_sha = _git(clone, "rev-list", "-n1", tag)
    _git(clone, "tag", "-d", tag)
    _git(clone, "tag", tag, tag_sha)

    result = _release(clone, "--publish", tag)

    assert result.returncode == 0, result.stderr
    assert _remote_release_version(clone) == f"{tag} ({tag_sha[:8]})"


def test_publish_same_tag_twice_is_noop(clone: Path) -> None:
    _release(clone)
    tag = f"v{DAY}.1"
    _release(clone, "--publish", tag)
    _git(clone, "fetch", "-q", "origin", "release")
    first = _git(clone, "rev-parse", "FETCH_HEAD")

    result = _release(clone, "--publish", tag)

    assert result.returncode == 0, result.stderr
    _git(clone, "fetch", "-q", "origin", "release")
    assert _git(clone, "rev-parse", "FETCH_HEAD") == first


def test_publish_unknown_tag_fails(clone: Path) -> None:
    result = _release(clone, "--publish", "v1999.01.01.1")

    assert result.returncode != 0


def test_manual_release_tags_and_publishes_marked_version(clone: Path) -> None:
    result = _release(clone, *MANUAL)

    assert result.returncode == 0, result.stderr
    tag = f"v{DAY}.1"
    assert _remote_tags(clone) == [tag]
    tag_sha = _git(clone, "rev-list", "-n1", tag)
    assert _remote_release_version(clone) == f"{tag} ({tag_sha[:8]}, manual)"


def test_main_carries_dev_version() -> None:
    # On the main branch VERSION = dev; only --publish writes the release tag,
    # on the release branch. The image takes the version from the file: .git is not in it.
    assert (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip() == "dev"


def test_publish_creates_github_release_with_generated_notes(clone: Path) -> None:
    _release(clone)
    tag = f"v{DAY}.1"

    result = _release(clone, "--publish", tag)

    assert result.returncode == 0, result.stderr
    assert _gh_calls(clone) == [f"release create {tag} --verify-tag --title {tag} --generate-notes"]


def test_publish_again_does_not_duplicate_github_release(clone: Path) -> None:
    _release(clone)
    tag = f"v{DAY}.1"
    _release(clone, "--publish", tag)

    _release(clone, "--publish", tag)

    assert len(_gh_calls(clone)) == 1


def test_republish_adds_missing_github_release(clone: Path) -> None:
    # A tag published while GitHub Release creation failed: release is already at
    # the tag, a repeated --publish only adds the missing GitHub Release.
    _release(clone)
    tag = f"v{DAY}.1"
    (clone.parent / "gh.fail").touch()
    _release(clone, "--publish", tag)
    (clone.parent / "gh.fail").unlink()

    result = _release(clone, "--publish", tag)

    assert result.returncode == 0, result.stderr
    assert _gh_calls(clone) == [f"release create {tag} --verify-tag --title {tag} --generate-notes"]


def test_github_release_failure_does_not_fail_deploy(clone: Path) -> None:
    _release(clone)
    (clone.parent / "gh.fail").touch()

    result = _release(clone, "--publish", f"v{DAY}.1")

    assert result.returncode == 0, result.stderr
    assert "GitHub Release" in result.stderr
    assert _remote_release_version(clone).startswith(f"v{DAY}.1 ")


def test_manual_release_notes_say_tests_were_skipped(clone: Path) -> None:
    _release(clone, *MANUAL)

    [call] = _gh_calls(clone)
    assert call.startswith(
        f"release create v{DAY}.1 --verify-tag --title v{DAY}.1 --generate-notes"
    )
    assert "--notes Emergency release without tests" in call
    assert "by Ann Owner: Actions outage" in call


def test_manual_release_records_reason_and_name_in_tag(clone: Path) -> None:
    _release(clone, *MANUAL)

    message = _git(clone, "tag", "-l", "--format=%(contents)", f"v{DAY}.1")
    assert "by Ann Owner: Actions outage" in message


@pytest.mark.parametrize(
    "args",
    [
        ("--manual",),
        ("--manual", "--reason", "Actions outage"),
        ("--manual", "--by", "Ann Owner"),
        ("--manual", "--reason", " ", "--by", "Ann Owner"),
        ("--manual", "--reason", "Actions outage", "--by"),
    ],
)
def test_manual_release_needs_reason_and_name(clone: Path, args: tuple[str, ...]) -> None:
    result = _release(clone, *args)

    assert result.returncode != 0
    assert _remote_tags(clone) == []
    assert _gh_calls(clone) == []


def test_manual_release_refused_at_team_level(clone: Path) -> None:
    (clone / ".claude").mkdir()
    (clone / ".claude" / "fences.env").write_text("FENCE_LEVEL=team\n")

    result = _release(clone, *MANUAL)

    assert result.returncode != 0
    assert "team" in result.stderr
    assert _remote_tags(clone) == []
    assert "refs/heads/release" not in _git(clone, "ls-remote", "origin")


# --- acceptance: every merge since the last tag must be accepted ---------------------------


def _pr_merge(clone: Path, number: int, title: str, labels: tuple[str, ...] = (), requested_by: str = "") -> None:
    """A PR merged into main as GitHub does it (merge commit, subject "<title> (#N)"), plus the gh stub's answer."""
    branch = f"feat/pr{number}"
    _git(clone, "checkout", "-q", "-b", branch, "origin/main")
    (clone / f"pr{number}.txt").write_text(title)
    _git(clone, "add", ".")
    _git(clone, "commit", "-q", "-m", f"work for {title}")
    _git(clone, "checkout", "-q", "main")
    _git(clone, "pull", "-q", "origin", "main")
    _git(clone, "merge", "-q", "--no-ff", "-m", f"{title} (#{number})", branch)
    _git(clone, "push", "-q", "origin", "main")
    body = "## Done\nthings\n"
    if requested_by:
        body += f"\n## Requested by\n{requested_by}\n\n## Open questions\nnone\n"
    import json

    (clone.parent / f"pr-{number}.json").write_text(
        json.dumps({"labels": [{"name": n} for n in labels], "body": body})
    )


def test_release_with_all_prs_accepted_tags(clone: Path) -> None:
    _pr_merge(clone, 11, "Voice input", ("accepted",), "Vlad (tg 42)")
    _pr_merge(clone, 12, "Dark theme", ("accepted", "ui"))

    result = _release(clone)

    assert result.returncode == 0, result.stderr
    assert _remote_tags(clone) == [f"v{DAY}.1"]
    assert "pr view 11 --json labels" in _gh_calls(clone)


def test_unaccepted_pr_blocks_release_and_is_named(clone: Path) -> None:
    _pr_merge(clone, 11, "Voice input", ("accepted",))
    _pr_merge(clone, 12, "Dark theme", (), "Vlad (tg 42, https://t.me/c/1/9)")

    result = _release(clone)

    assert result.returncode == 1
    assert _remote_tags(clone) == []
    assert "#12 Dark theme" in result.stderr
    assert "Voice input" not in result.stderr


def test_only_merges_since_the_last_tag_are_checked(clone: Path) -> None:
    _pr_merge(clone, 11, "Old feature", ("accepted",))
    assert _release(clone).returncode == 0
    _pr_merge(clone, 12, "New feature", ("accepted",))

    # PR 11 would now answer "unaccepted", but it is before the last tag
    (clone.parent / "pr-11.json").write_text('{"labels": [], "body": ""}')
    result = _release(clone)

    assert result.returncode == 0, result.stderr
    assert _remote_tags(clone) == [f"v{DAY}.1", f"v{DAY}.2"]


def test_unreadable_pr_blocks_release(clone: Path) -> None:
    _pr_merge(clone, 11, "Voice input", ("accepted",))
    (clone.parent / "pr-11.json").unlink()

    result = _release(clone)

    assert result.returncode != 0
    assert _remote_tags(clone) == []


def test_owner_accepts_labels_each_pr_then_tags(clone: Path) -> None:
    _pr_merge(clone, 11, "Voice input", ("accepted",))
    _pr_merge(clone, 12, "Dark theme")
    _pr_merge(clone, 13, "Export")

    result = _release(clone, "--owner-accepts")

    assert result.returncode == 0, result.stderr
    calls = _gh_calls(clone)
    assert "pr edit 12 --add-label accepted" in calls
    assert "pr edit 13 --add-label accepted" in calls
    assert "pr comment 12 --body accepted by the owner, permission dialog" in calls
    assert not any(c.startswith("pr edit 11") for c in calls)
    assert _remote_tags(clone) == [f"v{DAY}.1"]


def test_include_unaccepted_records_the_name_in_tag_and_notes(clone: Path) -> None:
    _pr_merge(clone, 12, "Dark theme", (), "Vlad")

    result = _release(clone, "--include-unaccepted", "Pasha (tg 7), https://t.me/c/1/10")

    assert result.returncode == 0, result.stderr
    assert not any(c.startswith("pr edit") for c in _gh_calls(clone))
    assert any(
        c.startswith("pr comment 12 --body shipped without acceptance, allowed by Pasha")
        for c in _gh_calls(clone)
    )
    message = _git(clone, "tag", "-l", "--format=%(contents)", f"v{DAY}.1")
    assert "allowed by Pasha (tg 7), https://t.me/c/1/10" in message
    assert "#12 Dark theme" in message
    _release(clone, "--publish", f"v{DAY}.1")
    [release] = [c for c in _gh_calls(clone) if c.startswith("release create")]
    assert "--notes Shipped without acceptance, allowed by Pasha" in release


def test_include_unaccepted_with_everything_accepted_is_a_plain_release(clone: Path) -> None:
    _pr_merge(clone, 11, "Voice input", ("accepted",))

    result = _release(clone, "--include-unaccepted", "Pasha")

    assert result.returncode == 0, result.stderr
    assert "Shipped without acceptance" not in _git(clone, "tag", "-l", "--format=%(contents)", f"v{DAY}.1")


def test_non_pr_merge_is_listed_and_needs_an_override(clone: Path) -> None:
    _git(clone, "checkout", "-q", "-b", "hotfix", "origin/main")
    (clone / "h.txt").write_text("h")
    _git(clone, "add", ".")
    _git(clone, "commit", "-q", "-m", "hotfix work")
    _git(clone, "checkout", "-q", "main")
    _git(clone, "merge", "-q", "--no-ff", "-m", "Merge branch hotfix", "hotfix")
    _git(clone, "push", "-q", "origin", "main")

    blocked = _release(clone)
    assert blocked.returncode == 1
    assert "Merge branch hotfix: not a pull request" in blocked.stderr
    assert _remote_tags(clone) == []

    allowed = _release(clone, "--include-unaccepted", "Ann Owner")
    assert allowed.returncode == 0, allowed.stderr
    assert _remote_tags(clone) == [f"v{DAY}.1"]


def test_owner_accepts_and_include_unaccepted_are_alternatives(clone: Path) -> None:
    result = _release(clone, "--owner-accepts", "--include-unaccepted", "Ann")

    assert result.returncode == 2
    assert _remote_tags(clone) == []


def test_manual_release_skips_the_acceptance_check(clone: Path) -> None:
    _pr_merge(clone, 12, "Dark theme")

    result = _release(clone, *MANUAL)

    assert result.returncode == 0, result.stderr
    assert not any(c.startswith("pr view") for c in _gh_calls(clone))


def test_list_unaccepted_prints_the_features_and_changes_nothing(clone: Path) -> None:
    _pr_merge(clone, 11, "Voice input", ("accepted",))
    _pr_merge(clone, 12, "Dark theme", (), "Vlad (tg 42), https://t.me/c/1/9")

    result = _release(clone, "--list-unaccepted")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["12\tDark theme\t"]
    assert _remote_tags(clone) == []
    assert not any(c.startswith("pr edit") for c in _gh_calls(clone))


# --- squash and rebase merges, failures ---------------------------------------------------


def _commit_on_main(clone: Path, subject: str, name: str) -> None:
    (clone / name).write_text(name)
    _git(clone, "add", name)
    _git(clone, "commit", "-q", "-m", subject)
    _git(clone, "push", "-q", "origin", "main")


def _pr_json(clone: Path, number: int, labels: tuple[str, ...] = ()) -> None:
    import json

    (clone.parent / f"pr-{number}.json").write_text(
        json.dumps({"labels": [{"name": n} for n in labels], "body": "## Requested by\nVlad (tg 42)\n"})
    )


def test_squash_merged_unaccepted_pr_blocks_release(clone: Path) -> None:
    _pr_json(clone, 21)
    _commit_on_main(clone, "Dark theme (#21)", "dark.txt")

    result = _release(clone)

    assert result.returncode == 1
    assert "#21 Dark theme" in result.stderr
    assert _remote_tags(clone) == []


def test_squash_merged_accepted_pr_releases(clone: Path) -> None:
    _pr_json(clone, 21, ("accepted",))
    _commit_on_main(clone, "Dark theme (#21)", "dark.txt")

    assert _release(clone).returncode == 0
    assert _remote_tags(clone) == [f"v{DAY}.1"]


def test_rebase_or_direct_commit_without_pr_number_needs_an_override(clone: Path) -> None:
    _commit_on_main(clone, "fix typo", "typo.txt")

    blocked = _release(clone)
    assert blocked.returncode == 1
    assert "fix typo: not a pull request" in blocked.stderr
    assert _remote_tags(clone) == []

    assert _release(clone, "--include-unaccepted", "Ann Owner").returncode == 0


def test_a_merge_commit_and_its_pr_are_counted_once(clone: Path) -> None:
    _pr_merge(clone, 31, "Voice input", (), "Vlad (tg 42)")

    result = _release(clone, "--list-unaccepted")

    assert result.stdout.splitlines() == ["31\tVoice input\t"]
    assert [c for c in _gh_calls(clone) if c.startswith("pr view 31")] == ["pr view 31 --json labels"]


def test_a_failing_git_log_refuses_the_release(clone: Path) -> None:
    result = _release(clone, "--list-unaccepted", RELEASE_MAIN_BRANCH="no-such-branch")

    assert result.returncode != 0
    assert result.stdout == ""
    assert _remote_tags(clone) == []


def test_a_failing_tag_push_is_not_reported_as_released(clone: Path) -> None:
    origin_hooks = clone.parent / "origin.git" / "hooks"
    (origin_hooks / "pre-receive").write_text("#!/bin/sh\nwhile read o n ref; do case $ref in refs/tags/*) exit 1;; esac; done\n")
    (origin_hooks / "pre-receive").chmod(0o755)

    result = _release(clone)

    assert result.returncode != 0
    assert "pushed" not in result.stdout
    assert _remote_tags(clone) == []


def test_a_failing_label_edit_after_the_tag_is_a_loud_warning(clone: Path) -> None:
    # The marks are posted only after the tag is on origin: a failed push leaves the PRs untouched,
    # a failed mark leaves a shipped release and the PR listed again at the next one.
    _pr_json(clone, 21)
    _commit_on_main(clone, "Dark theme (#21)", "dark.txt")
    (clone.parent / "gh").write_text(
        GH_STUB.replace('if [ "$1" = "pr" ]', 'if [ "$1 $2" = "pr edit" ]; then exit 1; fi\nif [ "$1" = "pr" ]')
    )

    result = _release(clone, "--owner-accepts")

    assert _remote_tags(clone) == [f"v{DAY}.1"]
    assert "could not mark PR #21" in result.stderr


def test_a_failing_tag_push_leaves_the_prs_unmarked(clone: Path) -> None:
    _pr_json(clone, 21)
    _commit_on_main(clone, "Dark theme (#21)", "dark.txt")
    hook = clone.parent / "origin.git" / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nwhile read o n ref; do case $ref in refs/tags/*) exit 1;; esac; done\n")
    hook.chmod(0o755)

    result = _release(clone, "--owner-accepts")

    assert result.returncode != 0
    assert not any(c.startswith("pr edit") or c.startswith("pr comment") for c in _gh_calls(clone))


def test_the_tag_lands_on_the_commit_the_gate_checked(clone: Path) -> None:
    # a merge that arrives while the gate talks to gh must not ride along unchecked
    _pr_json(clone, 21, ("accepted",))
    _commit_on_main(clone, "Dark theme (#21)", "dark.txt")
    checked = _git(clone, "rev-parse", "origin/main")
    hook = clone.parent / "gh"
    hook.write_text(
        GH_STUB.replace(
            'if [ "$1 $2" = "pr view" ]; then',
            'if [ "$1 $2" = "pr view" ]; then\n  if [ ! -e "$dir/raced" ]; then touch "$dir/raced"; '
            f'git -C {clone} commit -q --allow-empty -m "late (#99)" && git -C {clone} push -q origin main; fi',
            1,
        )
    )

    result = _release(clone)

    assert result.returncode == 0, result.stderr
    assert _git(clone, "rev-list", "-n1", f"v{DAY}.1") == checked


def test_a_local_only_tag_does_not_shorten_the_checked_range(clone: Path) -> None:
    _pr_merge(clone, 11, "Voice input", (), "Vlad (tg 42)")
    _git(clone, "tag", "v9999", "origin/main")

    result = _release(clone)

    assert result.returncode == 1
    assert "#11 Voice input" in result.stderr
    assert _remote_tags(clone) == []


def test_a_remote_tag_outside_the_release_scheme_does_not_shorten_the_range(clone: Path) -> None:
    _pr_merge(clone, 11, "Voice input", (), "Vlad (tg 42)")
    _git(clone, "tag", "v999", "origin/main")
    _git(clone, "push", "-q", "origin", "refs/tags/v999")

    result = _release(clone)

    assert result.returncode == 1
    assert "#11 Voice input" in result.stderr


def test_list_unaccepted_ignores_a_local_only_tag(clone: Path) -> None:
    _pr_merge(clone, 11, "Voice input", (), "Vlad (tg 42)")
    _git(clone, "tag", "v9999", "origin/main")

    result = _release(clone, "--list-unaccepted")

    assert result.stdout.startswith("11\tVoice input")


@pytest.mark.parametrize("tag", ["junk", "v1", "v2026.01.15", "v2026.01.15.1x"])
def test_publish_refuses_a_ref_that_is_not_a_release_tag(clone: Path, tag: str) -> None:
    _git(clone, "tag", tag, "HEAD")
    _git(clone, "push", "-q", "origin", f"refs/tags/{tag}")

    result = _release(clone, "--publish", tag)

    assert result.returncode != 0
    assert "refs/heads/release" not in _git(clone, "ls-remote", "origin")


def test_publish_refuses_a_tag_that_is_not_on_the_remote(clone: Path) -> None:
    _git(clone, "tag", f"v{DAY}.7", "HEAD")

    result = _release(clone, "--publish", f"v{DAY}.7")

    assert result.returncode != 0
    assert "refs/heads/release" not in _git(clone, "ls-remote", "origin")


@pytest.mark.parametrize(
    "args",
    [
        ("--manual", "--reason", "--publish", "--by", "Ann"),
        ("--manual", "--reason", "outage", "--by", "--publish"),
        ("--include-unaccepted", "--publish"),
    ],
)
def test_a_flag_is_never_taken_as_an_option_value(clone: Path, args: tuple[str, ...]) -> None:
    result = _release(clone, *args)

    assert result.returncode == 2
    assert _remote_tags(clone) == []


# --- security release: the last release plus one fix, on a human's yes ---------------------


def _tree(clone: Path, ref: str) -> list[str]:
    return _git(clone, "ls-tree", "--name-only", ref).splitlines()


def test_security_release_ships_the_last_release_plus_the_fix_only(clone: Path) -> None:
    _pr_merge(clone, 11, "Voice input", ("accepted",))
    assert _release(clone).returncode == 0
    _pr_merge(clone, 12, "Dark theme")  # merged, waiting for acceptance
    _pr_merge(clone, 13, "fix: raise pyjwt to 2.15.0 (GHSA-ffc3)")

    result = _release(clone, "--security", "13")

    assert result.returncode == 0, result.stderr
    tag = f"v{DAY}.2"
    assert _remote_tags(clone) == [f"v{DAY}.1", tag]
    files = _tree(clone, tag)
    assert "pr13.txt" in files and "pr11.txt" in files
    assert "pr12.txt" not in files  # the unaccepted feature stays out
    note = _git(clone, "tag", "-l", "--format=%(contents)", tag)
    assert "Security release" in note and "#13" in note
    calls = _gh_calls(clone)
    assert "pr edit 13 --add-label accepted" in calls
    assert any(c.startswith("pr comment 13 ") and "security release" in c for c in calls)
    assert not any(c.startswith("pr edit 12") for c in calls)


def test_security_release_publishes_like_any_tag(clone: Path) -> None:
    _pr_merge(clone, 11, "Voice input", ("accepted",))
    assert _release(clone).returncode == 0
    assert _release(clone, "--publish", f"v{DAY}.1").returncode == 0
    _pr_merge(clone, 13, "fix: raise certifi")
    assert _release(clone, "--security", "13").returncode == 0

    result = _release(clone, "--publish", f"v{DAY}.2")

    assert result.returncode == 0, result.stderr
    assert _remote_release_version(clone).startswith(f"v{DAY}.2 (")


def test_security_release_needs_a_release_to_build_on(clone: Path) -> None:
    _pr_merge(clone, 13, "fix: raise certifi")

    result = _release(clone, "--security", "13")

    assert result.returncode == 1
    assert "no release yet" in result.stderr
    assert _remote_tags(clone) == []


def test_security_release_refuses_a_pr_not_on_main(clone: Path) -> None:
    assert _release(clone).returncode == 0

    result = _release(clone, "--security", "99")

    assert result.returncode == 1
    assert "#99" in result.stderr
    assert _remote_tags(clone) == [f"v{DAY}.1"]


def test_security_release_refuses_when_the_fix_does_not_apply(clone: Path) -> None:
    assert _release(clone).returncode == 0
    (clone / "shared.txt").write_text("feature\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-q", "-m", "Feature (#12)")
    _git(clone, "push", "-q", "origin", "main")
    (clone / "shared.txt").write_text("feature + fix\n")  # the fix edits what the unreleased feature added
    _git(clone, "add", ".")
    _git(clone, "commit", "-q", "-m", "fix: security (#13)")
    _git(clone, "push", "-q", "origin", "main")

    result = _release(clone, "--security", "13")

    assert result.returncode == 1
    assert "does not apply" in result.stderr
    assert _remote_tags(clone) == [f"v{DAY}.1"]
    assert _git(clone, "worktree", "list").count("\n") == 0  # the temporary worktree is gone


def test_a_second_security_release_keeps_the_first_fix(clone: Path) -> None:
    _pr_merge(clone, 11, "Voice input", ("accepted",))
    assert _release(clone).returncode == 0
    _pr_merge(clone, 12, "Dark theme")  # waiting for acceptance
    _pr_merge(clone, 13, "fix: raise certifi")
    assert _release(clone, "--security", "13").returncode == 0
    _pr_merge(clone, 14, "fix: raise pyjwt")

    result = _release(clone, "--security", "14")

    assert result.returncode == 0, result.stderr
    files = _tree(clone, f"v{DAY}.3")
    assert "pr13.txt" in files and "pr14.txt" in files  # the first fix stays in production
    assert "pr12.txt" not in files


@pytest.mark.parametrize("args", [("--security",), ("--security", "x13"), ("--security", "13", "14")])
def test_security_release_needs_one_pr_number(clone: Path, args: tuple[str, ...]) -> None:
    assert _release(clone, *args).returncode == 2
