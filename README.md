# ShelfBlazer

A general-purpose stock manager for labs and small teams: records are text files in a git repository, with its own accounts and per-type permissions.
Item types (strains, plasmids, primers, reagents, or anything else) are defined in configuration files and edited in the app; nothing is tied to one kind of item.
Design: [docs/design.md](docs/design.md). Changes: [CHANGELOG.md](CHANGELOG.md). Roadmap: [ROADMAP.md](ROADMAP.md).

## What it does

- **Lists** per type: live search with typo tolerance (an exact ID jumps to the record), owner and archived filters, sortable columns, table or cards. Sort and view are remembered per browser.
- **Record pages:** fields, notes (a safe Markdown subset; record IDs become links), files, what links here, and the history. Each history entry opens the record as that commit saved it.
- **Forms:** reference picker for links, Save and add another, new choice options and storage places typed in place (saved with the record, in the same commit, only if the record is valid).
- **Archive, never delete.** An archived record is visible only to its owner and administrators. Git keeps every version.
- **Bulk edit** (one commit, with Undo) and **export** to a tab-separated file, from the list's *Select* mode.
- **Home:** the types as tiles and recent activity, read from the git history.
- **Types page** (administrators): create types, add and change fields, options, list columns, colour, icon, permissions, menu order. Each change is one commit.
- **Users page** (administrators): accounts, roles, set-password links, each person's entries, the sign-up passphrase.
- **Look:** *Spectrum* (a colour per type) or *Classic*, and Auto / Day / Night. `app/tests/test_look.py` checks the contrast of every colour pair.

## Quick start (Docker)

Needs Docker with Compose. From a clone of this repository:

```bash
docker build -t shelfblazer:dev .
mkdir home                                  # the runtime home: records and accounts live here, on the host

# a helper that runs any shelfblazer command in a container, as you, on ./home
sb() { docker run --rm -it --user "$(id -u):$(id -g)" -v "$PWD/home:/srv/shelfblazer" shelfblazer:dev shelfblazer "$@"; }

sb init-data --empty                        # a new instance: one starter type (Item), no records, and an `admin` account
sb set-password admin                       # asks for the administrator's password
sb set-passphrase                           # optional: lets people sign up

SB_TAG=dev SB_HOME="$PWD/home" SB_UID="$(id -u)" SB_GID="$(id -g)" docker compose -f deploy/compose.yaml up -d
```

Open <http://localhost:8000> and sign in as `admin`, then use the Types page to change the starter type or add your own.
This serves plain HTTP, which is fine on your own machine; for anything shared, put a TLS proxy in front (see [deploy/README.md](deploy/README.md)).

**Starting from existing data.** Instead of `sb init-data --empty`, `init-data --from DIR` builds the repository from a folder in the import format: `config/` (the type definitions,
`config/types/<type>.yaml`, see Type definitions), `data/` (one file per record, `data/<type>/<ID>.md`), and optionally `users_stubs.yaml` (accounts without passwords).
Mount the folder into the helper (add `-v "$PWD/import:/import:ro"` to its `docker run`) and pass `--from /import`.
The file formats are described in section 4 of the design document. Any system can be migrated by writing such a folder; `init-data --from` also writes the accounts and user IDs.
Accounts created without a password get a one-time set-password link from an administrator on the Users page.

## Where data lives

The runtime home (`./home` above, `/srv/shelfblazer` inside the container, or `var/` when running from source) holds two folders:
- `data/`: the records, a git repository (this is what you back up or push to a backup remote).
- `instance/`: `users.json` (accounts) and `secret.key` (session signing). It holds secrets: back it up separately and encrypted, never into git.

Choose the home with `--home DIR` or `$SHELFBLAZER_HOME` (the container sets it); `--data` and `--instance` override single folders.

**Attached files are stored with Git LFS:** the history holds small pointers, and records, type definitions and counters stay plain text (files are under `data/files/<ID>/`).
Everything that touches the repository needs `git-lfs` (the server, the image, a backup host); the server refuses to start without it.
`init-data` sets LFS up from the first commit (`--no-lfs` keeps files in plain git). For an existing repository use `lfs-setup`; `--rewrite-history` also converts old commits but changes their IDs, so do it before the first push.
The backup remote must accept LFS (GitHub does, within its storage quota).

## Type definitions

One JSON file per type, `config/types/<type>.yaml` in the data repository, edited on the Types page or by hand.
Behaviour comes from these settings, never from field names, so a new type works like the built-in ones.

```json
{ "name": "Reagent", "prefix": "RG", "color": "pink", "icon": "flask", "list_columns": ["id", "name", "supplier", "location"],
  "permissions": {"read": "all", "create": "all", "edit": "owner"},
  "fields": [{"key": "name", "label": "Name", "type": "text", "required": true},
             {"key": "supplier", "label": "Supplier", "type": "select", "options": ["Sigma", "Roth"], "addable": true},
             {"key": "location", "label": "Location", "type": "select", "source": "storage_places"}] }
```

| Setting | Effect |
|---|---|
| Type: `color` | `blue green orange pink purple teal red gold` (a fixed palette that stays readable by day and night) |
| Type: `icon` | `box bacterium dna bullseye flask table-cells layer-group file-lines` |
| Type: `permissions` | `read`: `all` / `admin`. `create`: `all` / `admin`. `edit`: `owner` (creator or administrator, the default) / `admin` / `all`. A type only administrators read is invisible everywhere else: menus, search, links, formulas, downloads |
| Type: `sequence_search` | The field the list's *sequence* search looks in: both strands, up to 5 mismatches, at least 6 bases |
| Type: `former_prefixes` | Old ID prefixes still open their records (written by `rename-prefix`) |
| Field types | `text longtext number date select multiselect bool url user ref file computed` |
| `required`, `help` | Must be filled in; a hint next to the label |
| `hidden` | Hidden everywhere, data kept (this is how a field is "removed" on the Types page) |
| `fold` | Sits in a folded section after the notes |
| `addable` | A choice field whose form takes new options |
| `source: "storage_places"` | A choice from the shared list of storage places, with *New place…* |
| `validate` | For sequence and reference data. `dna`: nucleotide letters only, shown in its own folded block, left out of word search. `doi`: DOIs only, shown as doi.org links |
| `display` (text) | `short`: a location, abbreviated (`Box 2 Well G3`, the full text underneath). `outdated`: as typed, marked `[outdated]`. `full`: as typed (the default) |
| `legacy` | A value carried over from a previous system: offered on the edit form only where a record has one, never on new records |
| `computed` + `expr` | Calculated when shown, never stored. A small safe formula language (no `eval`), e.g. `count(referenced_by('sample'))`, `join(', ', unique(flatten(values(refs('batch'), 'supplier'))))`, `age_days()`. A broken formula leaves the cell empty and is listed on the Types page |

A type definition without `color` or `icon` uses the defaults in `builtin.py` for the common lab item types it knows, else a colour derived from its name and a box.
Unknown values are ignored, with a warning in the server log and on the Types page.

## Operating it

### Maintenance commands

Run them with the `sb` helper from the quick start (or without it when running from source). Each makes one git commit.

| Command | What it does |
|---|---|
| `shelfblazer drop-field <type> <field> [--yes]` | Removes a field from a type and from all its records |
| `shelfblazer rename-prefix <type> <XX> [--yes]` | New ID prefix (AB-12 becomes CD-12): files, links and mentions are rewritten; the old IDs keep working |
| `shelfblazer lfs-setup [--rewrite-history --yes]` | Stores attached files in Git LFS in an existing repository |
| `shelfblazer erase-user <username> --yes` | Removes a person for good (see Security) |
| `shelfblazer set-password`, `set-passphrase`, `import-users` | Accounts from the command line (`import-users`: the stubs of an import folder) |

Without `--yes`, `drop-field` and `rename-prefix` only show what they would change.
A running server picks up commits made by these commands, or by anything else, on its next page and before its next save. There is no need to restart it.

### Deployment and versions

[deploy/README.md](deploy/README.md) covers the container image, the compose files, running behind a reverse proxy, and how to release.
Versions follow [Semantic Versioning](https://semver.org); see [CHANGELOG.md](CHANGELOG.md).
Run one server process only: the file lock protects git, and the search index lives in memory (about 2,500 records).

## Security

- **People appear in git by user ID only** (`U` + 7 characters). Records store the ID, commits are authored `<ID> <ID@shelfblazer.invalid>`, and names stay in `users.json`.
  `erase-user` deletes the account and keeps its ID as a tombstone, so records then show "former member" without rewriting history. Text people typed and files they uploaded are not covered (design document, section 16).
- **Sign-in throttling:** 5 wrong passwords for one username from one address lock that pair for 5 minutes, and 30 from one address lock that address. Failures elsewhere never lock anyone out.
  Wrong sign-up passphrases: 5 per address per 5 minutes. Unknown usernames take as long to refuse as known ones.
- **Behind a reverse proxy**, the proxy must *overwrite* `X-Forwarded-For` with the client address (nginx: `proxy_set_header X-Forwarded-For $remote_addr;`), and the app runs with `--forwarded-allow-ips`.
  An appended header (`$proxy_add_x_forwarded_for`) lets a client choose its own address and get around the throttling. See [deploy/README.md](deploy/README.md).
- **Sessions live on the server** (at most 20 browsers per person, 14 days). Signing out ends the session even for a copy of the cookie; changing a password or deactivating an account ends all of them.
  The look, colour mode and list preferences are separate cookies and stay.
- **Every check happens before any work:** permissions before a form or an upload is read, request size before the body is read.
  The limit is 4 MB for ordinary forms, and 5 files of 25 MB on record forms and uploads, which must state their size. Put the same limit in the proxy.
- **Pages:** a strict Content-Security-Policy, CSRF tokens on every form, escaped output, and downloads always sent as attachments.
  Programs, scripts and web pages (`.exe .sh .js .py .html .svg …`) are refused as uploads.

## Development

### Run from source

Needs Python 3.10 or newer and `git` with `git-lfs`.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e packages -e app          # provides the `shelfblazer` command

.venv/bin/shelfblazer init-data --empty           # var/data with one starter type, and an `admin` account
.venv/bin/shelfblazer set-password admin
.venv/bin/shelfblazer run --port 8000             # add --https behind an HTTPS proxy
```

### Layout

| Path | What it is |
|---|---|
| `packages/shelfblazer/` | **Core package** (no web code). `store.py` (records, type definitions, counters, git commits under a file lock), `recordfile.py` (the file format), `fields.py` (what each field setting does, value checks), `perms.py` (permission rules), `schema_edit.py` (editing type definitions), `formula.py` (calculated fields), `auth.py` (accounts), `search.py`, `seqsearch.py`, `export.py`, `uploads.py`, `markdown.py`, `builtin.py` (default icons and colours for common lab item types) |
| `app/shelfblazer_app/` | **Web app**. `web.py` (lists, records, forms, search), `accounts.py` (sign-in, sign-up, your account), `users_admin.py`, `types_admin.py`, `bulk.py`, `export.py`, `files.py`, `cli.py` (the `shelfblazer` command), `templates/`, `static/` |
| `packages/tests/`, `app/tests/` | Core unit tests; app tests (HTTP and command line) |
| `deploy/`, `Dockerfile` | Container image and compose files; see [deploy/README.md](deploy/README.md) |
| `var/` | The runtime home when running from source (git-ignored) |

### Tests

```bash
.venv/bin/python -W error::ResourceWarning -m unittest discover -s packages/tests
.venv/bin/python -W error::ResourceWarning -m unittest discover -s app/tests
```
