#!/usr/bin/env bash
# Restore a runtime home from an archive made by backup-archive.sh. Run it where the PRIVATE key is (not on the server).
#
#   deploy/restore-archive.sh ARCHIVE.tar.gz.gpg TARGET_HOME
#
# TARGET_HOME must not exist or must be empty. It gets instance/ (accounts, session key) and data/ (the git repository, history and
# every attached file). Needs gpg (with the private key), git and git-lfs. Afterwards: start ShelfBlazer with SHELFBLAZER_HOME=TARGET_HOME,
# and set the git remote again if you want pushes (git -C TARGET_HOME/data remote add origin <url>).
# A restore you have never tried is not a backup: do this once on another machine before you rely on the archives.
set -euo pipefail

die() { echo "restore-archive: $*" >&2; exit 1; }

ARCHIVE="${1:?usage: restore-archive.sh ARCHIVE.tar.gz.gpg TARGET_HOME}"
TARGET="${2:?usage: restore-archive.sh ARCHIVE.tar.gz.gpg TARGET_HOME}"
[ -f "$ARCHIVE" ] || die "$ARCHIVE not found"
command -v gpg >/dev/null || die "gpg is not installed"
if [ -e "$TARGET" ] && [ -n "$(ls -A "$TARGET")" ]; then die "$TARGET exists and is not empty"; fi
mkdir -p "$TARGET"; TARGET="$(cd "$TARGET" && pwd)"

STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE:?}"' EXIT
gpg --batch --decrypt "$ARCHIVE" | tar xzf - -C "$STAGE"
[ -f "$STAGE/data.bundle" ] && [ -d "$STAGE/instance" ] || die "the archive does not look like a ShelfBlazer backup"

mv "$STAGE/instance" "$TARGET/instance"
# clone without downloading LFS files (they are in the archive), then put the LFS objects back and check the files out
GIT_LFS_SKIP_SMUDGE=1 git clone -q "$STAGE/data.bundle" "$TARGET/data"
git -C "$TARGET/data" remote remove origin
if [ -d "$STAGE/data/.git/lfs/objects" ]; then
  mkdir -p "$TARGET/data/.git/lfs/objects"                      # may exist already: copy the CONTENTS (cp -R src dst would nest)
  cp -R "$STAGE/data/.git/lfs/objects/." "$TARGET/data/.git/lfs/objects/"
  command -v git-lfs >/dev/null || die "git-lfs is needed to check the attached files out"
  git -C "$TARGET/data" lfs install --local >/dev/null
  git -C "$TARGET/data" lfs checkout >/dev/null
elif command -v git-lfs >/dev/null; then
  git -C "$TARGET/data" lfs install --local >/dev/null
fi
echo "restore-archive: restored to $TARGET"
cat "$STAGE/MANIFEST.txt" 2>/dev/null | sed 's/^/  /'
