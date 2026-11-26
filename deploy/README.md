# Deployment files

Generic, site-independent files. Anything that names a host, a user, a path or a person on a real server is kept separately and is not part of a release.

| File | Purpose |
|---|---|
| `../Dockerfile`, `../.dockerignore`, `../requirements-runtime.txt` | The image: code only. Data and secrets are never in it |
| `build-export.sh [tag]` | Build the `linux/amd64` image and write `deploy/out/shelfblazer-<tag>.tar.gz` for a manual transfer (`gunzip -c ... \| docker load`) |
| `compose.yaml` | Plain-HTTP test run (port published on all interfaces, no `--https`) |
| `compose.proxy.yaml` | Production behind a reverse proxy on the same host (port bound to `127.0.0.1`, `--https`) |
| `../.github/workflows/build.yml` | CI: tests on every push; on a `v*` tag it also builds the image, pushes it to `ghcr.io` and attaches an archive |

## Checklist for a new server
1. Docker Engine with the Compose plugin; a reverse proxy with TLS in front of `127.0.0.1:8000`.
   The proxy must raise the request body limit (uploads: up to 5 files of 25 MB per request) and pass `Host`, `X-Forwarded-For` and `X-Forwarded-Proto`.
   `X-Forwarded-For` must be **overwritten** with the client address (nginx: `proxy_set_header X-Forwarded-For $remote_addr;`, not
   `$proxy_add_x_forwarded_for`): the app trusts the header and takes its first address, so an appended header lets a client choose
   its own address and get around the login throttling. Caddy's `reverse_proxy` does this by default.
   If the MCP endpoint is enabled (`SHELFBLAZER_MCP=1` in the container's environment), `/mcp` needs nothing special from the proxy: answers are plain JSON, no streaming or buffering setting. Do not enable it before the authentication review (ROADMAP).
2. A host folder for the runtime home (`data/` and `instance/`), owned by the user the container runs as (`SB_UID`, `SB_GID`).
3. Create the data once on the server: `shelfblazer init-data --empty` (then `set-password admin`), or copy an existing `data/` and `instance/`. Never copy over a live instance.
4. Load the image, set `.env`, start with `compose.proxy.yaml`, check `curl http://127.0.0.1:8000/login` returns 200.
5. Back up the runtime home (see `docs/design.md`, sections 11 and 16.5).

## Releasing
Versions follow [Semantic Versioning](https://semver.org) (see `CHANGELOG.md`). To release:
1. Set the same `version` in `app/pyproject.toml` and `packages/pyproject.toml`, and add a `## [x.y.z]` section to `CHANGELOG.md` (a test checks both).
2. Commit, then tag `vx.y.z`. On a tag, CI re-checks the tag against those files, builds the `linux/amd64` image, pushes `ghcr.io/<owner>/<repo>:x.y.z` and `:x.y`,
   and creates a GitHub Release holding `shelfblazer-x.y.z.tar.gz` with the changelog section as its notes.
3. On a server, always pin the full version in `.env` (`SB_TAG=x.y.z`), never `latest`; roll back by setting the previous one.

## Backing up the data repository
The records are a git repository (`<runtime home>/data`), so a backup is a push to a private remote. `deploy/backup-push.sh DATA_DIR` does it, run every minute from cron or a systemd timer on the host
as the user that owns the data folder:

```bash
* * * * * LFS_BUDGET_MB=1000 /path/to/backup-push.sh /srv/shelfblazer/data
```

- **Every change is pushed within a minute.** The "am I ahead?" check is local, so idle minutes cost nothing, and a failure never affects the app.
- **Git LFS quota is protected.** Attached files go up only while the local LFS store is at most `LFS_BUDGET_MB` (default 1000). Above it, commits are still pushed but without attachments, a warning is logged,
  and the skipped files are uploaded automatically once the store is under budget again. Set the budget a little under the remote's LFS quota, and back the attachments up elsewhere too (for example an encrypted archive of `data/files/`).
- **Status:** `backup-lastrun` (time of the last run, idle or not: older than a few minutes means the schedule is not running), `backup-status` (one line: time, state, details; written when something was pushed or failed) and `backup-push.log` (one line per change of state) are written next to the data folder. States: `OK`, `WARN` (pushed without attachments), `FAILED`, `NO_REMOTE`.
- **Setup:** add the remote (`git -C DATA_DIR remote add origin <url>`) and credentials the host user can use, for example an SSH deploy key with write access to that one repository
  (`git -C DATA_DIR config core.sshCommand "ssh -i ~/.ssh/<key> -o IdentitiesOnly=yes"`). The host needs `git` and `git-lfs`. `instance/` (accounts, session key) is not in the data repository: back it up separately and encrypted.

## Encrypted archive (the off-server backup)
`backup-push.sh` copies the git history to a remote. It does not cover what git does not hold: the accounts and session key in `instance/`, and, when the LFS budget is exceeded, the attached files.
`deploy/backup-archive.sh RUNTIME_HOME DEST_DIR` makes one encrypted archive a day with everything a full restore needs: `instance/`, the whole git history (a `git bundle`) and every attached file (all LFS objects).

- **Encryption is to a public key.** `GPG_RECIPIENT` names it. The server holds only the public half, so someone who breaks into the server cannot read old archives. Keep the private key off the server, and keep a copy of it in a safe place: without it the archives are worthless.
- **Retention:** every archive for `KEEP_DAILY` days (default 30), then only those made on the 1st of a month for `KEEP_MONTHLY` months (default 12). Personal data in old archives disappears when they age out: state this window in your privacy notice.
- **Where it goes:** `DEST_DIR` should be storage outside the server (a mounted share). Or write locally and mirror with `rsync -a --delete DEST_DIR/ user@host:dir/`, so the retention applies there too.
- **Setup:** on your own machine `gpg --full-generate-key`, then `gpg --armor --export <id> > backup-public.asc`; on the server, as the host user, `gpg --import backup-public.asc`; then one cron line:

```bash
10 3 * * * GPG_RECIPIENT=<fingerprint> /path/to/backup-archive.sh /srv/shelfblazer /mnt/backup
```

- **Restore:** `deploy/restore-archive.sh ARCHIVE.tar.gz.gpg TARGET_HOME` on a machine that has the private key. It rebuilds `instance/` and `data/` (history and attached files) and prints the manifest.
  Try it once on another machine before relying on the archives: a restore you have never tried is not a backup.
- **Status:** `DEST_DIR/last-archive` holds the time and size of the last archive; the script exits non-zero on any failure, so cron mails it if mail is set up.
