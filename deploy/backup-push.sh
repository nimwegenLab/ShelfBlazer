#!/usr/bin/env bash
# Push the data repository to its backup remote whenever it is ahead. Meant to run every minute from cron or a systemd timer,
# on the host, as the user that owns the data folder (needs git, git-lfs and the remote's credentials, e.g. an SSH deploy key).
#
#   deploy/backup-push.sh DATA_DIR
#
# DATA_DIR is the data repository (<runtime home>/data). Settings (environment):
#   REMOTE          name of the git remote to push to              (default: origin)
#   BRANCH          branch to push                                 (default: the checked-out branch)
#   LFS_BUDGET_MB   attached files (Git LFS) are uploaded only while the local LFS store is at most this size.
#                   Above it, commits are still pushed but without their attachments (a loud warning is logged), so the text
#                   backup stays current and the remote's LFS quota is not used up. Default: 1000.
#   STATE_DIR       where the log and the status file go           (default: the folder above DATA_DIR)
#
# The check "am I ahead?" is local (no network), so idle minutes cost nothing. A failure never touches the app: it only runs git push.
# Files skipped for being over budget are uploaded later by `git lfs push --all` once the store is back under budget (done by this script).
# Output: STATE_DIR/backup-push.log (one line per change of state), STATE_DIR/backup-status (one line: time, state, details; written only
# when something was pushed or went wrong) and STATE_DIR/backup-lastrun (the time of the last run, even an idle one: if it is older than
# a few minutes, the schedule is not running).
set -uo pipefail

DATA="${1:?usage: backup-push.sh DATA_DIR}"
[ -d "$DATA/.git" ] || { echo "backup-push: $DATA is not a git repository" >&2; exit 2; }
DATA="$(cd "$DATA" && pwd)"
REMOTE="${REMOTE:-origin}"
BUDGET_MB="${LFS_BUDGET_MB:-1000}"
STATE="${STATE_DIR:-$(dirname "$DATA")}"
LOG="$STATE/backup-push.log"; STATUS="$STATE/backup-status"; LOCK="$STATE/backup-push.lock"; SKIPPED="$STATE/backup-lfs-skipped"
mkdir -p "$STATE"

# one run at a time (a directory is an atomic lock that works on every system); a lock left by a dead run is removed
if ! mkdir "$LOCK" 2>/dev/null; then
  OLD="$(cat "$LOCK/pid" 2>/dev/null || true)"
  if [ -n "$OLD" ] && kill -0 "$OLD" 2>/dev/null; then exit 0; fi
  rm -rf "$LOCK"; mkdir "$LOCK" 2>/dev/null || exit 0
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT
date -u +%FT%TZ > "$STATE/backup-lastrun"                         # heartbeat: proves the schedule runs, even when there is nothing to push

# write the status line; append to the log only when the state changed
report() {  # report STATE DETAILS
  local state="$1" details="${2:-}" last=""
  [ -f "$STATUS" ] && last="$(cut -d' ' -f2 "$STATUS")"
  printf '%s %s %s\n' "$(date -u +%FT%TZ)" "$state" "$details" > "$STATUS"
  if [ "$state" != "$last" ]; then
    printf '%s %s %s\n' "$(date -u +%FT%TZ)" "$state" "$details" >> "$LOG"
  fi
}

cd "$DATA" || exit 2
BRANCH="${BRANCH:-$(git symbolic-ref --short -q HEAD || true)}"
[ -n "$BRANCH" ] || { report FAILED "no branch checked out"; exit 1; }
git remote get-url "$REMOTE" >/dev/null 2>&1 || { report NO_REMOTE "no remote named $REMOTE; add one with: git -C $DATA remote add $REMOTE <url>"; exit 0; }

# ahead? (local refs only; a branch the remote has never seen counts every commit)
if git rev-parse -q --verify "refs/remotes/$REMOTE/$BRANCH" >/dev/null; then
  AHEAD="$(git rev-list --count "refs/remotes/$REMOTE/$BRANCH..HEAD")"
else
  AHEAD="$(git rev-list --count HEAD)"
fi

# size of the local LFS store, in MB (rounded up)
LFS_KB=0; [ -d .git/lfs/objects ] && LFS_KB="$(du -sk .git/lfs/objects | cut -f1)"
LFS_MB=$(( (LFS_KB + 1023) / 1024 ))
OVER=0; [ "$LFS_MB" -gt "$BUDGET_MB" ] && OVER=1

if [ "$AHEAD" -eq 0 ] && { [ "$OVER" -eq 1 ] || [ ! -f "$SKIPPED" ]; }; then
  exit 0                                                         # nothing to push (and no skipped attachments that now fit)
fi

if [ "$OVER" -eq 1 ]; then
  OUT="$(GIT_LFS_SKIP_PUSH=1 git push "$REMOTE" "HEAD:refs/heads/$BRANCH" 2>&1)" || { report FAILED "git push failed: $(echo "$OUT" | tail -2 | tr '\n' ' ')"; exit 1; }
  : > "$SKIPPED"
  report WARN "pushed $AHEAD commit(s) WITHOUT attachments: LFS store ${LFS_MB} MB is over the budget of ${BUDGET_MB} MB"
  exit 0
fi

OUT="$(git push "$REMOTE" "HEAD:refs/heads/$BRANCH" 2>&1)" || { report FAILED "git push failed: $(echo "$OUT" | tail -2 | tr '\n' ' ')"; exit 1; }
if [ -f "$SKIPPED" ]; then                                        # attachments skipped earlier: upload them now that there is room
  OUT="$(git lfs push --all "$REMOTE" 2>&1)" || { report FAILED "git lfs push --all failed: $(echo "$OUT" | tail -2 | tr '\n' ' ')"; exit 1; }
  rm -f "$SKIPPED"
  report OK "pushed $AHEAD commit(s) and the attachments skipped earlier (LFS store ${LFS_MB} MB)"
else
  report OK "pushed $AHEAD commit(s) (LFS store ${LFS_MB} MB of ${BUDGET_MB} MB)"
fi
exit 0
