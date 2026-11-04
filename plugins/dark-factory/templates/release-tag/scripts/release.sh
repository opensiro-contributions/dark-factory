#!/usr/bin/env bash
# Release to prod only from a tag (factory module release-tag). The hosting
# builds the `release` branch; only a verified release moves it, a merge into
# main does not go to prod.
#
#   scripts/release.sh               put the next tag vYYYY.MM.DD.N on
#                                    origin/main and push it, but only when every
#                                    merge since the last v* tag is accepted (see
#                                    below). Then the
#                                    release.yml workflow runs light + heavy
#                                    tests and does --publish. Run by a release
#                                    manager (the tag ruleset lets only them push
#                                    v* tags), or by the agent at their explicit word.
#   scripts/release.sh --owner-accepts
#                                    the owner, in the interactive session, accepts
#                                    every unaccepted feature listed: the tag is put on,
#                                    then each PR gets the label `accepted` and the comment
#                                    "accepted by the owner, permission dialog".
#   scripts/release.sh --include-unaccepted "<who allowed>"
#                                    ship without the mark: the name goes into the
#                                    annotated tag and the release notes, each such PR
#                                    gets a comment once the tag is pushed.
#   scripts/release.sh --security <PR>
#                                    a security release: the last v* tag plus the merge
#                                    of that one fix PR, tagged as the next vYYYY.MM.DD.N;
#                                    features on main still waiting for acceptance stay
#                                    out. The PR gets the label `accepted` with a note that
#                                    its acceptance was skipped (the project's
#                                    VULN_RELEASE=now). Refused before the first release
#                                    and when the fix does not apply on its own. A person's
#                                    decision like any tagging run: the vulnerability
#                                    check's fix session asks for it.
#   scripts/release.sh --list-unaccepted
#                                    only print the merges since the last tag that lack
#                                    the mark, one per line: <PR number or ->TAB<title>TAB
#                                    <note>; changes nothing.
#   scripts/release.sh --publish T   move `release` to tag T + a commit with
#                                    the VERSION file and create a GitHub
#                                    Release with auto-generated notes. Called by
#                                    release.yml; repeating the same tag is a
#                                    no-op (adds a missing GitHub Release).
#   scripts/release.sh --manual --reason "<why>" --by "<human name>"
#                                    EMERGENCY (Actions are down): tag + --publish
#                                    right away, no tests. VERSION gets a manual mark;
#                                    the reason and the name of the person who decided
#                                    go into the annotated tag and the release notes.
#                                    Refused at FENCE_LEVEL=team (.claude/fences.env):
#                                    there a release goes through the tag ruleset + CI only.
#
# Acceptance: a release takes all of main. Every first-parent commit since the last v* tag
# (`git log <tag>..origin/main --first-parent`; the first release: since the root) is
# listed, a merge, a squash and a rebase alike, and each PR must carry the label `accepted`
# (set after someone on the acceptance list said yes on the dev stand). The PR number comes from
# the subject, "(#N)" or "Merge pull request #N"; a PR is counted once. A commit without one
# is not a PR (a direct push, a rebase merge) and needs an override. Without the
# mark the script refuses and prints each unaccepted feature with its title, so the
# caller can ask again. --manual and --publish do not check.
#
# Tags are fetched with --force: actions/checkout on a tag creates a local
# lightweight tag, and without --force git refuses to replace it with the
# annotated one.
#
# RELEASE_DATE (YYYY.MM.DD) — date override for tests; defaults to today in RELEASE_TZ.
# RELEASE_GH — the gh command: for the test suite (a stub) and the owner's own shell, never for an agent: the
# dark-factory guard treats setting it as a fence change, and feedback-desk.sh (the On-call's way to release)
# unsets it. Actions need GH_TOKEN; the acceptance check needs gh logged in.
# RELEASE_MAIN_BRANCH, RELEASE_TZ — override the values below (for tests).
set -euo pipefail
# `set -e` does not reach into $(…) by default: without this a failing git tag / git push / gh call in a
# function called through a substitution would read as success.
shopt -s inherit_errexit
# A failure must not be silent: in Actions -q hides the cause.
trap 'echo "release: failure at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

# Fill in at deployment: timezone of the tag date, main branch.
REMOTE="origin"
MAIN_BRANCH="${RELEASE_MAIN_BRANCH:-<main>}"
RELEASE_BRANCH="release"
RELEASE_TZ="${RELEASE_TZ:-UTC}"
GH="${RELEASE_GH:-gh}"

# KEY from .claude/fences.env (KEY=value lines, # comments) — the fence level chosen at deployment.
fence_value() {
  local env_file
  env_file="$(git rev-parse --show-toplevel)/.claude/fences.env"
  [ -f "${env_file}" ] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*[\"']\{0,1\}\([^\"'#[:space:]]*\).*/\1/p" "${env_file}" | tail -n 1
}

# One line, no surrounding blanks: the values go into a tag message and release notes.
one_line() { printf '%s' "$1" | tr -s '[:space:]' ' ' | sed 's/^ //; s/ $//'; }

next_tag() {
  local day n
  day="${RELEASE_DATE:-$(TZ="${RELEASE_TZ}" date +%Y.%m.%d)}"
  # --no-recurse-submodules on every fetch: the submodule check reads .gitmodules, which the Claude Code
  # sandbox hides behind an unreadable stub — git then warns "unable to access .gitmodules" on each fetch
  git fetch -q --no-recurse-submodules --tags --force "${REMOTE}" || return 1
  n="$(git tag -l "v${day}.*")" || return 1
  n="$(printf '%s\n' "${n}" | sed "s/^v${day}\.//" | sort -n | tail -1)" || return 1
  echo "v${day}.$(( ${n:-0} + 1 ))"
}

ACCEPT_LABEL="accepted"
# What next_tag produces; --publish accepts nothing else.
TAG_RE='^v[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]+$'

# The commit the release is about: fetched once, then checked by the gate and tagged as is.
MAIN_SHA=""
fetch_main() {
  git fetch -q --no-recurse-submodules --tags --force "${REMOTE}" || return 1
  git fetch -q --no-recurse-submodules "${REMOTE}" "${MAIN_BRANCH}" || return 1
  MAIN_SHA="$(git rev-parse FETCH_HEAD)" || return 1
}

# The newest release tag (TAG_RE) that exists on the remote and that the checked commit contains; empty before the
# first release. A tag that lives only in this clone cannot shorten the range.
last_tag() {
  local remote tags
  remote="$(git ls-remote --tags --refs "${REMOTE}" 'v*')" || return 1
  remote="$(printf '%s\n' "${remote}" | sed -n 's|.*\trefs/tags/||p')"
  tags="$(git tag -l 'v*' --merged "${MAIN_SHA}" --sort=-v:refname)" || return 1
  printf '%s\n' "${tags}" | grep -E "${TAG_RE}" | grep -Fx -f <(printf '%s\n' "${remote}") | sed -n 1p || true
}

# Prints the unaccepted merges since the last release, one per line:
#   <PR number or "-">TAB<title>TAB<note: empty for a PR, "not a pull request (<sha>)" otherwise>
# Returns 1 when git or gh cannot tell (an unverifiable change is not an accepted one): the caller refuses.
unaccepted_merges() {
  local last range sha subject num title info commits out seen=" "
  last="$(last_tag)" || { echo "release: cannot read the tags - nothing done" >&2; return 1; }
  range="${last:+${last}..}${MAIN_SHA}"
  commits="$(git log "${range}" --first-parent --format='%H%x09%s')" || {
    echo "release: cannot list the commits ${range} - acceptance is unknown, nothing done" >&2
    return 1
  }
  while IFS=$'\t' read -r sha subject; do
    [ -n "${sha}" ] || continue
    num="$(printf '%s' "${subject}" | sed -n 's/.*(#\([0-9][0-9]*\))[[:space:]]*$/\1/p; s/^Merge pull request #\([0-9][0-9]*\) .*/\1/p' | head -n 1)"
    title="$(printf '%s' "${subject}" | sed 's/[[:space:]]*(#[0-9][0-9]*)[[:space:]]*$//')"
    case "${seen}" in *" ${num:-x${sha}} "*) continue ;; esac
    seen="${seen}${num:-x${sha}} "
    if [ -z "${num}" ]; then
      printf '%s\t%s\t%s\n' "-" "${subject}" "not a pull request (${sha:0:8})"
      continue
    fi
    info="$("${GH}" pr view "${num}" --json labels </dev/null)" || {
      echo "release: cannot read PR #${num} through gh - acceptance is unknown, nothing done" >&2
      return 1
    }
    out="$(INFO="${info}" NUM="${num}" TITLE="${title}" LABEL="${ACCEPT_LABEL}" python3 - <<'PY'
import json, os
d = json.loads(os.environ["INFO"])
if any(l.get("name") == os.environ["LABEL"] for l in d.get("labels") or []):
    raise SystemExit(0)
print(f"{os.environ['NUM']}\t{os.environ['TITLE']}\t")
PY
)" || {
      echo "release: cannot read PR #${num} - acceptance is unknown, nothing done" >&2
      return 1
    }
    [ -z "${out}" ] || printf '%s\n' "${out}"
  done <<<"${commits}"
}

print_unaccepted() {
  local num title who
  while IFS=$'\t' read -r num title who; do
    if [ "${num}" = "-" ]; then echo "  - ${title}: ${who}"; else echo "  - #${num} ${title}"; fi
  done <<<"$1"
}

# Marks each listed PR; "-" (a merge that is not a PR) has nothing to mark.
mark_accepted() {
  local list="$1" comment="$2" mark="${3:-1}" num title who
  if [ "${mark}" = 1 ]; then
    "${GH}" label create "${ACCEPT_LABEL}" --color 0e8a16 --description "accepted on the dev stand" --force </dev/null >/dev/null 2>&1 || true
  fi
  while IFS=$'\t' read -r num title who; do
    [ "${num}" != "-" ] || continue
    if [ "${mark}" = 1 ]; then
      "${GH}" pr edit "${num}" --add-label "${ACCEPT_LABEL}" </dev/null >/dev/null || {
        echo "release: could not mark PR #${num} accepted" >&2
        return 1
      }
    fi
    "${GH}" pr comment "${num}" --body "${comment}" </dev/null >/dev/null || {
      echo "release: could not comment on PR #${num}" >&2
      return 1
    }
  done <<<"${list}"
}

# Acceptance gate of the normal release. $1 = owner|include|"" , $2 = who allowed.
# Runs in the caller's shell (not a substitution) and leaves the result in GATE_LIST (the PRs to mark)
# and GATE_NOTE (the tag note, may be empty); messages go to stderr. Marking waits for the pushed tag.
GATE_LIST="" GATE_NOTE=""
acceptance_gate() {
  local mode="$1" who="$2" list
  fetch_main || exit 1
  list="$(unaccepted_merges)" || exit 1
  [ -n "${list}" ] || return 0
  case "${mode}" in
    owner)
      echo "release: the owner accepts these features:" >&2
      print_unaccepted "${list}" >&2
      ;;
    include)
      echo "release: shipping WITHOUT acceptance, allowed by ${who}:" >&2
      print_unaccepted "${list}" >&2
      GATE_NOTE="$(printf 'Shipped without acceptance, allowed by %s: %s' "${who}" \
        "$(while IFS=$'\t' read -r n t w; do if [ "${n}" = "-" ]; then printf '%s; ' "${t}"; else printf '#%s %s; ' "${n}" "${t}"; fi; done <<<"${list}" | sed 's/; $//')")"
      ;;
    *)
      echo "release: not every merge since the last release is accepted - nothing done:" >&2
      print_unaccepted "${list}" >&2
      echo "release: ask someone on the acceptance list, then mark the PR accepted; or release with --owner-accepts (the owner) / --include-unaccepted \"<who allowed>\"" >&2
      exit 1
      ;;
  esac
  GATE_LIST="${list}"
}

# After the tag is on origin: the marks must not outlive a failed push. A failed mark is a warning,
# the release is out; the PR is listed again at the next one.
mark_after_tag() {
  local mode="$1" who="$2"
  [ -n "${GATE_LIST}" ] || return 0
  case "${mode}" in
    owner) mark_accepted "${GATE_LIST}" "accepted by the owner, permission dialog" 1 ;;
    include) mark_accepted "${GATE_LIST}" "shipped without acceptance, allowed by ${who}" 0 ;;
  esac || echo "release: ⚠️ the tag is pushed but the acceptance marks are incomplete - mark the PRs by hand" >&2
}

create_tag() {
  local tag note="${1:-}" msg
  [ -n "${MAIN_SHA}" ] || fetch_main || return 1
  tag="$(next_tag)" || return 1
  msg=(-m "release ${tag}")
  [ -z "${note}" ] || msg+=(-m "${note}")
  git tag -a "${tag}" "${msg[@]}" "${MAIN_SHA}" || return 1
  git push -q "${REMOTE}" "refs/tags/${tag}" || {
    echo "release: pushing tag ${tag} failed - nothing released" >&2
    return 1
  }
  echo "${tag}"
}

# A GitHub Release is the release's showcase, not part of the deploy: by now
# `release` has already been pushed, so a failure here is a loud warning, not a failure.
github_release() {
  local tag="$1" mark="${2:-}"
  if "${GH}" release view "${tag}" >/dev/null 2>&1; then
    echo "release: GitHub Release ${tag} already exists"
    return 0
  fi
  local args=(release create "${tag}" --verify-tag --title "${tag}" --generate-notes)
  [ -z "${mark}" ] || args+=(--notes "${MANUAL_NOTE:-Emergency release without tests (scripts/release.sh --manual).}")
  # A release that went out without acceptance says so in its notes (the line is in the annotated tag).
  local unaccepted
  unaccepted="$(git tag -l --format='%(contents)' "${tag}" | grep '^Shipped without acceptance' || true)"
  [ -z "${unaccepted}" ] || args+=(--notes "${unaccepted}")
  if "${GH}" "${args[@]}" >/dev/null; then
    echo "release: GitHub Release ${tag} created"
  else
    echo "release: ⚠️ GitHub Release ${tag} not created — the deploy went out; retry: $0 --publish ${tag}" >&2
  fi
}

publish() {
  local tag="$1" mark="${2:-}" tag_sha prev="" version blob tree commit index
  if ! [[ "${tag}" =~ ${TAG_RE} ]]; then
    echo "release: ${tag} is not a release tag (vYYYY.MM.DD.N) - nothing done" >&2
    exit 1
  fi
  git fetch -q --no-recurse-submodules --tags --force "${REMOTE}"
  if [ -z "$(git ls-remote --tags --refs "${REMOTE}" "${tag}")" ]; then
    echo "release: tag ${tag} is not on ${REMOTE} - nothing done" >&2
    exit 1
  fi
  tag_sha="$(git rev-list -n1 "${tag}" 2>/dev/null)" || {
    echo "release: tag ${tag} not found" >&2
    exit 1
  }
  if git fetch -q --no-recurse-submodules "${REMOTE}" "${RELEASE_BRANCH}" 2>/dev/null; then
    prev="$(git rev-parse FETCH_HEAD)"
    if git show "${prev}:VERSION" 2>/dev/null | grep -q "^${tag} ("; then
      echo "release: ${RELEASE_BRANCH} already at ${tag}"
      github_release "${tag}" "${mark}"
      return 0
    fi
  fi
  version="${tag} (${tag_sha:0:8}${mark:+, ${mark}})"
  # Content is exactly the tag + VERSION. The commit is built in a temporary
  # index; the working tree is not touched.
  index="$(mktemp)"
  trap 'rm -f "${index}"' RETURN
  blob="$(printf '%s\n' "${version}" | git hash-object -w --stdin)"
  GIT_INDEX_FILE="${index}" git read-tree "${tag_sha}"
  GIT_INDEX_FILE="${index}" git update-index --add --cacheinfo "100644,${blob},VERSION"
  tree="$(GIT_INDEX_FILE="${index}" git write-tree)"
  # A hosting may not build the branch as is but MERGE it into its own master:
  # release must grow forward, otherwise the second release conflicts on VERSION.
  # This is also correct for a hosting that builds the branch as is.
  # First parent — the previous release, second — the tag; push without --force.
  if [ -n "${prev}" ]; then
    commit="$(git commit-tree "${tree}" -p "${prev}" -p "${tag_sha}" -m "release: ${version}")"
  else
    commit="$(git commit-tree "${tree}" -p "${tag_sha}" -m "release: ${version}")"
  fi
  git push -q "${REMOTE}" "${commit}:refs/heads/${RELEASE_BRANCH}"
  echo "release: ${RELEASE_BRANCH} → ${version}"
  github_release "${tag}" "${mark}"
}

# A security release: the last release tag plus the merge of one fix PR, so features on main still waiting for
# acceptance stay out. The fix PR is marked accepted with a note (the project chose to skip acceptance for security
# fixes, VULN_RELEASE=now); the next normal release from main contains the fix anyway.
SECURITY_TMP=""
security_release() {
  local num="$1" last base sha parents tmp picked tag
  fetch_main || exit 1
  last="$(last_tag)" || { echo "release: cannot read the tags - nothing done" >&2; exit 1; }
  [ -n "${last}" ] || { echo "release: no release yet to build a security release on - make a normal release" >&2; exit 1; }
  sha="$(git log "${last}..${MAIN_SHA}" --first-parent --format='%H%x09%s' | NUM="${num}" python3 -c '
import os, re, sys
n = os.environ["NUM"]
for row in sys.stdin:
    sha, _, subject = row.rstrip("\n").partition("\t")
    if re.search(rf"\(#{n}\)\s*$", subject) or subject.startswith(f"Merge pull request #{n} "):
        print(sha)
        break
')"
  [ -n "${sha}" ] || { echo "release: PR #${num} is not merged into ${MAIN_BRANCH} after ${last} - nothing done" >&2; exit 1; }
  # Build on what production runs: the newest release tag on the remote, an earlier security release included (it is
  # not on main, so last_tag skips it; building on main's last tag would drop that earlier fix from production).
  base="$(git ls-remote --tags --refs "${REMOTE}" 'v*' | sed -n 's|.*\trefs/tags/||p' | grep -E "${TAG_RE}" | sort -V | tail -n 1)"
  [ -n "${base}" ] || base="${last}"
  parents="$(git rev-list --parents -n1 "${sha}" | wc -w)"
  SECURITY_TMP="$(mktemp -d)"
  tmp="${SECURITY_TMP}"
  # the trap runs after this function returns, when its locals are gone: it reads the global
  trap 'git worktree remove --force "${SECURITY_TMP}" >/dev/null 2>&1 || true; rm -rf "${SECURITY_TMP}"' EXIT
  git worktree add -q --detach "${tmp}" "${base}" 2>/dev/null
  # -m 1: a merge commit's change is its diff against main's side
  if ! git -C "${tmp}" cherry-pick -x $([ "${parents}" -gt 2 ] && echo "-m 1") "${sha}" >/dev/null 2>&1; then
    git -C "${tmp}" cherry-pick --abort >/dev/null 2>&1 || true
    echo "release: the fix of PR #${num} does not apply to ${base} on its own (it builds on unreleased work) - nothing done; release it the normal way" >&2
    exit 1
  fi
  picked="$(git -C "${tmp}" rev-parse HEAD)"
  tag="$(next_tag)" || exit 1
  git tag -a "${tag}" -m "release ${tag}" \
    -m "Security release: ${base} + the fix of PR #${num} only; acceptance of the fix skipped by the project's VULN_RELEASE=now" \
    "${picked}"
  git push -q "${REMOTE}" "refs/tags/${tag}" || {
    git tag -d "${tag}" >/dev/null 2>&1 || true  # a tag that never shipped must not look like a release here
    echo "release: pushing tag ${tag} failed - nothing released" >&2
    exit 1
  }
  mark_accepted "$(printf '%s\t%s\t\n' "${num}" "security fix")" \
    "shipped in security release ${tag} (${base} + this fix only); acceptance skipped by the project's VULN_RELEASE=now" 1 \
    || echo "release: ⚠️ the tag is pushed but PR #${num} is not marked - mark it accepted by hand" >&2
  echo "release: security tag ${tag} pushed (${base} + PR #${num}); deploy follows a green release.yml"
}

case "${1:-}" in
  --security)
    { [ $# -eq 2 ] && [[ "$2" =~ ^[0-9]+$ ]]; } || { echo "usage: $0 --security <PR number>" >&2; exit 2; }
    security_release "$2"
    ;;
  --list-unaccepted)
    [ $# -eq 1 ] || { echo "usage: $0 --list-unaccepted" >&2; exit 2; }
    fetch_main || exit 1
    unaccepted_merges || exit 1
    ;;
  ""|--owner-accepts|--include-unaccepted)
    mode="" who="" owner=0
    while [ $# -gt 0 ]; do
      case "$1" in
        --owner-accepts) owner=1; mode=owner; shift ;;
        --include-unaccepted)
          { [ $# -ge 2 ] && [[ "$2" != --* ]]; } || { echo "release: --include-unaccepted needs the name of who allowed it" >&2; exit 2; }
          who="$(one_line "$2")"
          [ -n "${who}" ] || { echo "release: --include-unaccepted needs the name of who allowed it" >&2; exit 2; }
          mode=include; shift 2 ;;
        *) echo "release: unknown argument $1" >&2; exit 2 ;;
      esac
    done
    if [ "${owner}" = 1 ] && [ -n "${who}" ]; then
      echo "release: --owner-accepts and --include-unaccepted are alternatives" >&2; exit 2
    fi
    acceptance_gate "${mode}" "${who}"
    tag="$(create_tag "${GATE_NOTE}")" || exit 1
    mark_after_tag "${mode}" "${who}"
    echo "release: tag ${tag} pushed; deploy follows a green release.yml"
    ;;
  --publish)
    [ -n "${2:-}" ] || { echo "usage: $0 --publish <tag>" >&2; exit 2; }
    publish "$2"
    ;;
  --manual)
    shift
    reason="" by=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --reason|--by)
          { [ $# -ge 2 ] && [[ "$2" != --* ]]; } || { echo "release: $1 needs a value" >&2; exit 2; }
          if [ "$1" = "--reason" ]; then reason="$2"; else by="$2"; fi
          shift 2 ;;
        *) echo "release: unknown argument $1 — usage: $0 --manual --reason \"<why>\" --by \"<human name>\"" >&2; exit 2 ;;
      esac
    done
    reason="$(one_line "${reason}")"
    by="$(one_line "${by}")"
    if [ -z "${reason}" ] || [ -z "${by}" ]; then
      echo "release: --manual needs --reason \"<why>\" and --by \"<human name>\" (the person who decided to release without tests) — nothing done" >&2
      exit 2
    fi
    if [ "$(fence_value FENCE_LEVEL)" = "team" ]; then
      echo "release: --manual is refused at FENCE_LEVEL=team — releases go through the tag ruleset + CI only; nothing done" >&2
      exit 1
    fi
    MANUAL_NOTE="Emergency release without tests (scripts/release.sh --manual) by ${by}: ${reason}"
    tag="$(create_tag "${MANUAL_NOTE}")" || exit 1
    publish "${tag}" manual
    ;;
  *)
    echo "usage: $0 [--security <PR> | --list-unaccepted | --owner-accepts | --include-unaccepted \"<who allowed>\" | --publish <tag> | --manual --reason \"<why>\" --by \"<human name>\"]" >&2
    exit 2
    ;;
esac
