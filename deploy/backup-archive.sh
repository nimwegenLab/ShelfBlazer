#!/usr/bin/env bash
# Encrypted, time-limited archive of everything a full restore needs, including what the git remote does not hold
# (accounts and the session key in instance/, and every attached file in Git LFS). Run once a day from cron, on the host,
# as the user that owns the runtime home.
#
#   deploy/backup-archive.sh RUNTIME_HOME DEST
#
# RUNTIME_HOME holds data/ (the git repository) and instance/. DEST receives shelfblazer-YYYY-MM-DD.tar.gz.gpg (one per day;
# a second run on the same day replaces it). DEST is either
#   - a directory, ideally on storage OUTSIDE the server (a mounted share), or
#   - a remote location  user@host:path  (an HPC login host, an SFTP/rsync server): the archive is built in a temporary directory
#     on this machine, uploaded with rsync over ssh, and the retention below is applied over there too. Everything goes through
#     rsync, so the ssh key can be restricted to rsync in one folder (rrsync in authorized_keys). The folder must exist already.
#
# Settings (environment):
#   GPG_RECIPIENT   REQUIRED. The PUBLIC key the archive is encrypted to (key id, fingerprint or e-mail). The server needs only the
#                   public half: whoever breaks into the server cannot read old archives. Keep the private key OFF the server, and keep
#                   a copy of it somewhere safe: without it the archives are worthless.
#   KEEP_DAILY      days to keep every archive                      (default: 30)
#   KEEP_MONTHLY    months to keep the archive made on the 1st      (default: 12)
#   RSYNC_RSH       remote DEST only: the ssh command, e.g. "ssh -i ~/.ssh/archive_key -o IdentitiesOnly=yes -o BatchMode=yes"
#                   (default: ssh -o BatchMode=yes). The host key must be in known_hosts already.
#   TMPDIR          remote DEST only: where the archive is built before the upload (default /tmp; as large as one archive).
#
# Inside the archive: instance/, data.bundle (the whole git history, `git bundle`), data/.git/lfs/objects (all attached files,
# every version) and MANIFEST.txt. Restore with deploy/restore-archive.sh. Personal data in old archives disappears when they age out of
# the retention window: state the window in your privacy notice.
set -euo pipefail

die() { echo "backup-archive: $*" >&2; exit 1; }

HOME_DIR="${1:?usage: backup-archive.sh RUNTIME_HOME DEST}"
DEST="${2:?usage: backup-archive.sh RUNTIME_HOME DEST}"
KEEP_DAILY="${KEEP_DAILY:-30}"; KEEP_MONTHLY="${KEEP_MONTHLY:-12}"
[ -n "${GPG_RECIPIENT:-}" ] || die "GPG_RECIPIENT is required (the public key to encrypt to)"
command -v gpg >/dev/null || die "gpg is not installed"
[ -d "$HOME_DIR/data/.git" ] || die "$HOME_DIR/data is not a git repository"
[ -d "$HOME_DIR/instance" ] || die "$HOME_DIR/instance not found"
gpg --batch --list-keys "$GPG_RECIPIENT" >/dev/null 2>&1 || die "no public key for '$GPG_RECIPIENT' in this user's gpg keyring (gpg --import it)"

# "user@host:path" is remote; a path with a slash before any colon is a local directory
REMOTE=0
if [[ "$DEST" == *:* && "${DEST%%:*}" != */* ]]; then
  REMOTE=1
  command -v rsync >/dev/null || die "rsync is not installed (needed for a remote DEST)"
  export RSYNC_RSH="${RSYNC_RSH:-ssh -o BatchMode=yes}"
  remote() { case "$DEST" in *:) printf '%s%s' "$DEST" "$1";; *) printf '%s/%s' "${DEST%/}" "$1";; esac; }
else
  mkdir -p "$DEST"
  DEST="$(cd "$DEST" && pwd)"
fi

HOME_DIR="$(cd "$HOME_DIR" && pwd)"
STAGE="$(mktemp -d)"
TMP=""
trap 'rm -rf "${STAGE:?}"; [ -z "$TMP" ] || rm -f "$TMP"' EXIT
TODAY="$(date -u +%F)"
NAME="shelfblazer-$TODAY.tar.gz.gpg"
if [ "$REMOTE" = 1 ]; then TMP="$STAGE/$NAME"; else TMP="$DEST/.$NAME.partial"; fi

# the git history as one consistent file (the repository may change while we run; a bundle is a snapshot)
git -C "$HOME_DIR/data" bundle create "$STAGE/data.bundle" --all >/dev/null 2>&1 || die "git bundle failed"
git bundle verify "$STAGE/data.bundle" >/dev/null 2>&1 || die "the git bundle does not verify"
{
  echo "created: $(date -u +%FT%TZ)"
  echo "head: $(git -C "$HOME_DIR/data" rev-parse HEAD)"
  echo "commits: $(git -C "$HOME_DIR/data" rev-list --all --count)"
  echo "lfs_objects: $( [ -d "$HOME_DIR/data/.git/lfs/objects" ] && find "$HOME_DIR/data/.git/lfs/objects" -type f | wc -l | tr -d ' ' || echo 0)"
} > "$STAGE/MANIFEST.txt"

# attached files: LFS objects are immutable and content-addressed, so copying them while the app runs is safe
PATHS=(instance)
[ -d "$HOME_DIR/data/.git/lfs/objects" ] && PATHS+=(data/.git/lfs/objects)
tar czf - -C "$HOME_DIR" "${PATHS[@]}" -C "$STAGE" data.bundle MANIFEST.txt \
  | gpg --batch --yes --trust-model always --encrypt --recipient "$GPG_RECIPIENT" -o "$TMP"
[ -s "$TMP" ] || die "the encrypted archive is empty"
chmod 600 "$TMP"
STATUS_LINE="$(date -u +%FT%TZ) $NAME $(du -k "$TMP" | cut -f1) KB"
if [ "$REMOTE" = 1 ]; then
  rsync -t "$TMP" "$(remote "$NAME")" || die "the upload to $DEST failed"           # rsync writes a temporary file and renames it: no half archive
  echo "$STATUS_LINE" > "$STAGE/last-archive"
  rsync -t "$STAGE/last-archive" "$(remote last-archive)" || die "the upload of last-archive to $DEST failed"
else
  mv "$TMP" "$DEST/$NAME"; TMP=""
  echo "$STATUS_LINE" > "$DEST/last-archive"
fi

# retention: every archive for KEEP_DAILY days, then only those made on the 1st of a month, for KEEP_MONTHLY months
days_ago() { date -u -d "$1 days ago" +%F 2>/dev/null || date -u -v-"$1"d +%F; }
CUT_DAILY="$(days_ago "$KEEP_DAILY")"; CUT_MONTHLY="$(days_ago $((KEEP_MONTHLY * 31)))"
expired() {                                                  # stdin: file names; stdout: those outside the retention window
  local f d
  while read -r f; do
    [[ "$f" =~ ^shelfblazer-[0-9]{4}-[0-9]{2}-[0-9]{2}\.tar\.gz\.gpg$ ]] || continue
    d="${f#shelfblazer-}"; d="${d%.tar.gz.gpg}"
    if [[ "$d" < "$CUT_MONTHLY" ]] || { [[ "$d" < "$CUT_DAILY" ]] && [ "${d##*-}" != "01" ]; }; then echo "$f"; fi
  done
}
if [ "$REMOTE" = 1 ]; then
  # list and delete with rsync only: syncing an empty folder with --delete removes exactly the files named by --include
  # (everything else is excluded, so protected)
  OLD="$(rsync --list-only "$(remote "")" | awk '{print $NF}' | expired || true)"
  if [ -n "$OLD" ]; then
    mkdir "$STAGE/empty"
    ARGS=(); while read -r f; do ARGS+=("--include=$f"); done <<< "$OLD"
    rsync -r --delete "${ARGS[@]}" --exclude='*' "$STAGE/empty/" "$(remote "")" || echo "backup-archive: could not remove expired archives on $DEST" >&2
  fi
else
  (cd "$DEST" && ls -1) | expired | while read -r f; do rm -f "$DEST/$f"; done
fi
echo "backup-archive: wrote $NAME to $DEST"
