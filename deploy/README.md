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
