#!/usr/bin/env bash
# Encrypted, time-limited archive of everything a full restore needs, including what the git remote does not hold
# (accounts and the session key in instance/, and every attached file in Git LFS). Run once a day from cron, on the host,
# as the user that owns the runtime home.
#
#   deploy/backup-archive.sh RUNTIME_HOME DEST_DIR
#
# RUNTIME_HOME holds data/ (the git repository) and instance/. DEST_DIR receives shelfblazer-YYYY-MM-DD.tar.gz.gpg (one per day;
# a second run on the same day replaces it). Put DEST_DIR on storage OUTSIDE the server (a mounted share), or mirror it with
# rsync --delete so the retention below applies there too.
#
# Settings (environment):
#   GPG_RECIPIENT   REQUIRED. The PUBLIC key the archive is encrypted to (key id, fingerprint or e-mail). The server needs only the
#                   public half: whoever breaks into the server cannot read old archives. Keep the private key OFF the server, and keep
#                   a copy of it somewhere safe: without it the archives are worthless.
#   KEEP_DAILY      days to keep every archive                      (default: 30)
#   KEEP_MONTHLY    months to keep the archive made on the 1st      (default: 12)
#
# Inside the archive: instance/, data.bundle (the whole git history, `git bundle`), data/.git/lfs/objects (all attached files,
# every version) and MANIFEST.txt. Restore with deploy/restore-archive.sh. Personal data in old archives disappears when they age out of
# the retention window: state the window in your privacy notice.
set -euo pipefail

die() { echo "backup-archive: $*" >&2; exit 1; }

HOME_DIR="${1:?usage: backup-archive.sh RUNTIME_HOME DEST_DIR}"
DEST="${2:?usage: backup-archive.sh RUNTIME_HOME DEST_DIR}"
KEEP_DAILY="${KEEP_DAILY:-30}"; KEEP_MONTHLY="${KEEP_MONTHLY:-12}"
[ -n "${GPG_RECIPIENT:-}" ] || die "GPG_RECIPIENT is required (the public key to encrypt to)"
command -v gpg >/dev/null || die "gpg is not installed"
[ -d "$HOME_DIR/data/.git" ] || die "$HOME_DIR/data is not a git repository"
[ -d "$HOME_DIR/instance" ] || die "$HOME_DIR/instance not found"
gpg --batch --list-keys "$GPG_RECIPIENT" >/dev/null 2>&1 || die "no public key for '$GPG_RECIPIENT' in this user's gpg keyring (gpg --import it)"
mkdir -p "$DEST"

HOME_DIR="$(cd "$HOME_DIR" && pwd)"; DEST="$(cd "$DEST" && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE:?}"; rm -f "${TMP:-}"' EXIT
TODAY="$(date -u +%F)"
NAME="shelfblazer-$TODAY.tar.gz.gpg"
TMP="$DEST/.$NAME.partial"

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
mv "$TMP" "$DEST/$NAME"
echo "$(date -u +%FT%TZ) $NAME $(du -k "$DEST/$NAME" | cut -f1) KB" > "$DEST/last-archive"

# retention: every archive for KEEP_DAILY days, then only those made on the 1st of a month, for KEEP_MONTHLY months
days_ago() { date -u -d "$1 days ago" +%F 2>/dev/null || date -u -v-"$1"d +%F; }
CUT_DAILY="$(days_ago "$KEEP_DAILY")"; CUT_MONTHLY="$(days_ago $((KEEP_MONTHLY * 31)))"
for f in "$DEST"/shelfblazer-????-??-??.tar.gz.gpg; do
  [ -e "$f" ] || continue
  d="$(basename "$f" | sed 's/^shelfblazer-\(.*\)\.tar\.gz\.gpg$/\1/')"
  if [[ "$d" < "$CUT_MONTHLY" ]] || { [[ "$d" < "$CUT_DAILY" ]] && [ "${d##*-}" != "01" ]; }; then rm -f "$f"; fi
done
echo "backup-archive: wrote $DEST/$NAME"
