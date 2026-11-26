# ShelfBlazer

**Your shelves in your pocket – backed by git, filed by AI, checked by rules.**

ShelfBlazer keeps track of what a lab or a small team has on its shelves: strains, reagents, samples, tools, anything you define.
Find things in the browser, at your desk or at the bench on your phone, and scan a barcode to jump to the record. Tell your AI assistant what you just made, and it files it for you: it checks for duplicates, follows your group's conventions, and asks when it is unsure.
The app applies the same rules to every entry, typed or filed, and nothing is ever deleted.
Self-hosted, with its own accounts; every record is a text file in a git repository, and every change is a commit. The assistant is optional: the app works without it.

![License: AGPL-3.0-or-later](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![MCP server](https://img.shields.io/badge/MCP-server-purple)

[Why ShelfBlazer](#why-shelfblazer) · [Quick start](#quick-start-docker) · [What it does](#what-it-does) · [Connect an AI assistant](#connecting-an-agent-mcp-off-by-default) · [Design](docs/design.md) · [Changelog](CHANGELOG.md) · [Roadmap](ROADMAP.md)

## Why ShelfBlazer

1. **The AI assistant does the data entry.** Each person connects their own Claude Code (or any MCP client) with a personal token, then says what they have: *"add these twenty samples from my table"*. The assistant fills in the fields, follows the conventions your group writes down, checks for duplicates and asks when unsure. It works with that person's rights, and its changes are marked in the history. People search in the app.
2. **Duplicates are caught before they are saved.** Every type gets a same-name check out of the box; per-type rules say what else counts as "the same" and suggest fixes for misspellings. It warns and never blocks: a second stock can be legitimate.
3. **Nothing is lost.** Entries are archived, never deleted. Every change is a git commit under the person's name, any past version can be opened, and a backup is a `git push`.
4. **Plain text, no database.** Each record is a Markdown file in a git repository, readable and scriptable without the app. Moving away from ShelfBlazer means keeping a folder of files.
5. **Your types, your rules.** Types, fields, links between records, calculated fields and permissions (who may read, create and edit) are set on the Types page. Nothing is tied to one kind of item. IDs such as `AB-12` are never reused, so they can go on labels.
6. **Shared and personal collections** (optional, per type). People keep their own items apart from the group's, with their own IDs, and move them to the shared collection when they are ready.

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
To let people connect their AI assistant, start the server with `SHELFBLAZER_MCP=1` ([Connecting an agent](#connecting-an-agent-mcp-off-by-default)).

**Starting from existing data.** Instead of `sb init-data --empty`, `init-data --from DIR` builds the repository from a folder in the import format: `config/` (the type definitions,
`config/types/<type>.yaml`, see Type definitions), `data/` (one file per record, `data/<type>/<ID>.md`), and optionally `users_stubs.yaml` (accounts without passwords).
Mount the folder into the helper (add `-v "$PWD/import:/import:ro"` to its `docker run`) and pass `--from /import`.
The file formats are described in section 4 of the design document. Any system can be migrated by writing such a folder; `init-data --from` also writes the accounts and user IDs.
Accounts created without a password get a one-time set-password link from an administrator on the Users page.

## What it does

- **Your AI assistant as a colleague** (optional, `SHELFBLAZER_MCP=1`): an MCP endpoint lets each person's own Claude Code search, add, edit, archive and import records as that person, reading the group's conventions and checking for duplicates first. Personal tokens, set up in four steps on the account page. [Details](#connecting-an-agent-mcp-off-by-default).
- **Checks before saving:** possible duplicates and broken naming conventions, on the form, in the import preview and for the assistant, with suggested fixes. [Details](#checks-before-saving).
- **Help page** for the people using it, with examples from your own types and a section your group writes. [Details](#help-page).
- **Item types are yours:** fields, choices, links between records, calculated fields, colours, icons and permissions, all edited on the Types page. Nothing is tied to one kind of item.
- **A place is a line of text, not a map:** a record says what you have and where it is (`Box 2 Well G3`); there are no freezers, racks or well grids to maintain. Barcode scanning is optional, per type.
- **Lists** per type: live search with typo tolerance (an exact ID jumps to the record), owner and archived filters, sortable columns, table or cards. Sort and view are remembered per browser.
- **Record pages:** fields, notes (a safe Markdown subset; record IDs become links), files, what links here, and the history. Each history entry opens the record as that commit saved it.
- **Forms:** reference picker for links, Save and add another, new choice options and storage places typed in place (saved with the record, in the same commit, only if the record is valid).
- **Archive, never delete.** An archived record is visible only to its owner and administrators. Git keeps every version.
- **Bulk edit** (one commit, with Undo) and **export** to a tab-separated file, from the list's *Select* mode.
- **Import from CSV/TSV** (*Import…* beside *+ New* on a list, for anyone who may add records of the type; into the lab collection or into one's own). A row with an ID updates that record only if the person could edit it by hand. Upload a file with a header line, say what each column is (titles that match a field are chosen for you), see every row checked with the form's rules, then import it as one commit with Undo. A row with an ID updates that record, a row without one creates a new record; a blank cell leaves the field as it is; one bad row stops the whole import. Up to 5,000 rows and 1 MB per file; Excel files are saved as CSV or TSV first (the import page offers an empty template with the type's column titles). Person fields take a user name or ID.
- **Personal collections** (optional, per type: `permissions.personal`). Besides the lab collection, each person can keep their own items of a type, with the same fields and rules and their own part of the sidebar. A personal item has an ID such as `ST-P3`, and each person counts from 1: yours is `ST-P3`, someone else's shows as `ST-P3[Alice A]`. Items move to the lab collection (one page that says what will happen; the personal items they link to come along, or are linked to a matching lab item), never back. Only the owner and administrators edit a personal item. Each person chooses per type whether the others can see their items (*Visible to others* on the Me page, on by default). A lab record cannot link to a personal item. Types are still managed by administrators only. An administrator can move someone's own lab records into their collection with `move-to-personal`.
- **Home:** the types as tiles and recent activity, read from the git history.
- **Record picture** (optional, per type with `thumbnail`, chosen as *Record picture* on the Types page): the first image in the chosen File field (jpg, png, webp, gif or avif) shows beside the fields on the record page (above them on phones) and as the right third of each card. Photos are shrunk in the browser to at most 1000 px (JPEG) before upload, so a 4 MB phone photo becomes about 100 KB; formats the browser cannot decode (HEIC on a desktop) are uploaded unchanged and stay plain file links. To replace the picture, remove the old file first.
- **Barcode scanning** (optional, per type with `scan_fields`): the *Scan* button (in the sidebar, or next to the menu button on phones) reads a code with the camera, decoded **in the browser** (the picture never leaves the device). One record carrying the code opens; several are listed; none offers *Add as <type>* with the code filled in, or to add the code to an entry that is already there (search for it, tick the codes to keep: lot numbers start unticked; only for entries the person may edit). A USB or Bluetooth reader typing into the home search works the same way. On the edit form, a *Scan* button beside each scan field adds the codes it reads to that field, keeping only the product part of a GS1 code; when a label carries several codes, it lists them with the product codes ticked. Codes are compared ignoring case, spaces, dots, hyphens and slashes, and the UPC, EAN-13 and 14-digit forms of one product match; from GS1 codes only the product part counts (GTIN, elements 240 and 91), never the lot or expiry. The camera needs HTTPS (or localhost). The decoder is zxing-wasm 2.2.4 (MIT; its compiled zxing-cpp engine is Apache-2.0), served from `static/vendor/zxing/` with its licences, which is why the security policy allows `'wasm-unsafe-eval'`.
- **Types page** (administrators): create types, add and change fields, options, list columns, colour, icon, permissions, menu order. Each change is one commit.
- **Users page** (administrators): accounts, roles, set-password links, each person's entries, the sign-up passphrase.
- **Look:** *Spectrum* (a colour per type) or *Classic*, and Auto / Day / Night. `app/tests/test_look.py` checks the contrast of every colour pair.

## Where data lives

The runtime home (`./home` above, `/srv/shelfblazer` inside the container, or `var/` when running from source) holds two folders:
- `data/`: the records, a git repository (this is what you back up or push to a backup remote).
- `instance/`: `users.json` (accounts) and `secret.key` (session signing). It holds secrets: back it up separately and encrypted, never into git.

Choose the home with `--home DIR` or `$SHELFBLAZER_HOME` (the container sets it); `--data` and `--instance` override single folders.
To push `data/` to a backup remote after every change, with a guard for the remote's LFS quota, see Backing up the data repository in [deploy/README.md](deploy/README.md).

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
| Type: `permissions.personal` | `off` (the default), `all` (everyone may keep personal items of the type) or `admin` (only administrators). A type only administrators read has no personal collections |
| Type: `sequence_search` | The field the list's *sequence* search looks in: both strands, up to 5 mismatches, at least 6 bases |
| Field: `pictures_only` | `true` on a File field (also a checkbox on the Types page): only pictures (jpg, png, webp, gif, avif, heic, tif, bmp) are accepted, checked on the server; the file chooser filters to images |
| Type: `thumbnail` | (also set on the Types page) A File field of the type whose first image is the record's picture (record page and cards); its file chooser takes one image and shrinks it before upload |
| Type: `scan_fields` | The text fields holding the codes a product is known by (several per field, separated by commas, semicolons or new lines); switches on barcode scanning for the type; the first one receives the code on a new record |
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

A type definition without `color` or `icon` gets a colour derived from its name and a box icon. Types not placed on the Types page follow the saved ones alphabetically.
Unknown values are ignored, with a warning in the server log and on the Types page.

## Checks before saving

When a record is created, edited or imported, the app looks for **possible duplicates** among the records you can see (archived ones included, marked as such) and, where the group has written rules, for **names and values that break its conventions**. Findings appear once, in a *Check before saving* panel on the form (and per row in the import preview): a duplicate links to the existing record, a suggested fix has an *Apply* button, and *Save anyway* saves. Nothing is ever blocked: a repeat can be legitimate.

Without any setup, every type reports records with the same name (compared without case, accents or repeated spaces). A group refines this per type with an optional file `config/checks/<type>.yaml` in the data repository (JSON-compatible, like the type definitions):

```json
{ "tables": {"spelling": [["(?i)\\bdh5alfa\\b", "DH5alpha"]]},
  "extract": {"background": {"field": "name", "pattern": "^(?P<value>\\S+)"}},
  "duplicate_keys": [{"label": "same background and changes", "min_filled": 2,
                      "parts": [{"extract": "background", "normalise": ["spelling", "casefold"], "required": true},
                                {"field": "genotype", "set": true}]}],
  "fields": {"name": {"fix": ["spelling"]}, "genotype": {"max_length": 120}},
  "guidance": "# Strains\n- Write the background first, then the changes." }
```

| Setting | Effect |
|---|---|
| `tables` | Named lists of `[regular expression, replacement]`, applied in order. Used to compare (`normalise`) and to suggest (`fix`) |
| `extract` | A named part of a field: the `value` group of a pattern |
| `duplicate_keys` | Two records are possible duplicates when every part of one key is equal. A part is a `field` or an `extract`, with `normalise` (your tables, or `casefold`, `accents`, `space`, `alnum`, `dna`); `set` compares words in any order; a linked-record field compares the linked IDs; `required` and `min_filled` stop a key from matching on too little |
| `fields.<key>.fix` | Tables whose result is offered as a suggested value |
| `fields.<key>.warn`, `max_length` | A warning when a pattern matches, an extract's value or a linked record's name is found in the field, or the value is long |
| `mentions.<link field>` | Records named in a field (or between square brackets) but not linked; a name spelled differently from the record's |
| `agree` | Two extracts that are both present must be equal |
| `guidance` | Markdown with the group's conventions, for people and for [agents](#connecting-an-agent-mcp-off-by-default) |

A file that cannot be read is reported on the Types page and the type falls back to the name check. Format details: `packages/shelfblazer/checks.py` and the [design document](docs/design.md), section 19.

## Operating it

### Maintenance commands

Run them with the `sb` helper from the quick start (or without it when running from source). Each makes one git commit.

| Command | What it does |
|---|---|
| `shelfblazer drop-field <type> <field> [--yes]` | Removes a field from a type and from all its records |
| `shelfblazer rename-prefix <type> <XX> [--yes]` | New ID prefix (AB-12 becomes CD-12): files, links and mentions are rewritten; the old IDs keep working |
| `shelfblazer lfs-setup [--rewrite-history --yes]` | Stores attached files in Git LFS in an existing repository |
| `shelfblazer move-to-personal <username> <IDs> [--from FILE] [--yes]` | Moves a person's own lab records into their personal collection (IDs or ranges such as `ST-34..ST-756`): new personal IDs, the lab IDs keep opening the records |
| `shelfblazer erase-user <username> --yes [--leave-personal]` | Removes a person for good (see Security). Refuses while the person still has personal items (archived ones count): move them to the lab collection or archive them first |
| `shelfblazer set-password`, `set-passphrase`, `import-users` | Accounts from the command line (`import-users`: the stubs of an import folder) |

Without `--yes`, `drop-field`, `rename-prefix` and `move-to-personal` only show what they would change.
A running server picks up commits made by these commands, or by anything else, on its next page and before its next save. There is no need to restart it.

### Help page

*Help* (above the person's name in the sidebar) explains the app to its users: finding, adding, linking, importing and exporting records, who can change what, and, where switched on, personal collections, scanning and the assistant (MCP). Examples are taken from the instance's own types. An instance adds its own section, shown right after the summary under the title of its first heading, by writing `config/help.md` in the data repository, or in the app on *Admin → Help text* (a preview, one commit per save). It is the place for what only the group knows: what each type is for, where samples are kept, who to ask.

### Connecting an agent (MCP, off by default)

With `SHELFBLAZER_MCP=1` in the server's environment, the app also serves the Model Context Protocol at `/mcp`, so your own [Claude Code](https://claude.com/claude-code) (or another MCP client) can search, create, edit and archive records **as you, with your rights**.
1. Open *AI assistant* (in the menu above Help, or the account page's tab `/me/assistant`) and follow its steps: give the token a name (the computer), confirm with your password, and copy it: it is shown once.
2. Connect, in two commands (the account page shows them): `read -rs SBT` (paste the token at the hidden prompt), then `claude mcp add --transport http shelfblazer https://<your server>/mcp --header "Authorization: Bearer $SBT"; unset SBT`. The token is never typed into a command line, so it stays out of the shell history. Claude Code keeps it in its own configuration file (`~/.claude.json`): protect that file like an SSH key.
3. Ask for what you need ("add these three strains", "find the plasmids with Kan resistance"). The agent reads the group's conventions first and checks for duplicates before it saves; changes are committed under your name with "(via MCP)" in the subject, and an import can be undone.

What an agent reads goes to your Claude account: keep nothing confidential in the records. Revoke a token on the same page; administrators' own tokens also have member rights only. Not available to agents: moving a record to the lab collection, bulk edit, files, account settings, scan, export. Details: [design document](docs/design.md), section 18.

### Deployment and versions

[deploy/README.md](deploy/README.md) covers the container image, the compose files, running behind a reverse proxy, and how to release.
Versions follow [Semantic Versioning](https://semver.org); see [CHANGELOG.md](CHANGELOG.md).
Run one server process only: the file lock protects git, and the search index lives in memory (about 2,500 records).

## Security

- **People appear in git by user ID only** (`U` + 7 characters). Records store the ID, commits are authored `<ID> <ID@shelfblazer.invalid>`, and names stay in `users.json`.
  `erase-user` deletes the account and keeps its ID as a tombstone, so records then show "former member" without rewriting history. The command refuses while the person still has personal items; `--leave-personal` erases anyway, and those items stay for administrators only. Text people typed and files they uploaded are not covered (design document, section 16).
- **Sign-in throttling:** 5 wrong passwords for one username from one address lock that pair for 5 minutes, and 30 from one address lock that address. Failures elsewhere never lock anyone out.
  Wrong sign-up passphrases: 5 per address per 5 minutes. Unknown usernames take as long to refuse as known ones.
- **Behind a reverse proxy**, the proxy must *overwrite* `X-Forwarded-For` with the client address (nginx: `proxy_set_header X-Forwarded-For $remote_addr;`), and the app runs with `--forwarded-allow-ips`.
  An appended header (`$proxy_add_x_forwarded_for`) lets a client choose its own address and get around the throttling. See [deploy/README.md](deploy/README.md).
- **Sessions live on the server** (at most 20 browsers per person, 14 days). Signing out ends the session even for a copy of the cookie; changing a password or deactivating an account ends all of them.
  The look, colour mode and list preferences are separate cookies and stay.
- **Every check happens before any work:** permissions before a form or an upload is read, request size before the body is read.
  The limit is 4 MB for ordinary forms, and 5 files of 25 MB on record forms and uploads, which must state their size. Put the same limit in the proxy.
- **Access tokens (MCP, only when enabled):** stored as SHA-256 hashes, shown once, at most 5 per person, 90 days by default (a year at most), created only with the password, revoked with the account and when the password changes; wrong ones are throttled without ever locking a valid token or a sign-in. An agent has its owner's member rights, never more.
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
| `packages/shelfblazer/` | **Core package** (no web code). `store.py` (records, type definitions, counters, git commits under a file lock), `recordfile.py` (the file format), `fields.py` (what each field setting does, value checks), `perms.py` (permission rules), `schema_edit.py` (editing type definitions), `formula.py` (calculated fields), `auth.py` (accounts), `search.py`, `seqsearch.py`, `export.py`, `uploads.py`, `markdown.py`, `checks.py` (checks before saving), `helptext.py` (the group's Help section) |
| `app/shelfblazer_app/` | **Web app**. `web.py` (builds the app and the helpers every page uses), `lists.py` (home, search, list pages), `records.py` (record pages, forms), `cells.py` (how values show on pages and in forms), `middleware.py` (body limit, security headers, error pages), `views.py` (icons, colours, times), `accounts.py` (sign-in, sign-up, your account), `help.py` (the Help page and its editor), `users_admin.py`, `types_admin.py`, `bulk.py`, `records_import.py`, `export.py`, `files.py`, `cli.py` (the `shelfblazer` command), `templates/`, `static/` |
| `packages/tests/`, `app/tests/` | Core unit tests; app tests (HTTP and command line) |
| `deploy/`, `Dockerfile` | Container image and compose files; see [deploy/README.md](deploy/README.md) |
| `var/` | The runtime home when running from source (git-ignored) |

### Tests

```bash
.venv/bin/python run_tests.py                 # both suites, one process per test file, as many at a time as there are cores
.venv/bin/python run_tests.py import look     # only the test files whose name contains "import" or "look"
```

`run_tests.py` runs the same tests with the same flags as the two plain commands below, about four times faster on an 8-core machine
(every test uses its own temporary folder and git repository, so files can run side by side; on a Mac whose `git` is the `/usr/bin/git` shim it also uses the real git, which
roughly halves the run). The plain commands still work:

```bash
.venv/bin/python -W error::ResourceWarning -m unittest discover -s packages/tests
.venv/bin/python -W error::ResourceWarning -m unittest discover -s app/tests
```

## License

ShelfBlazer is free software, released under the [GNU Affero General Public License, version 3 or later](LICENSE).
You may use, change and share it; if you run a changed version for other people over a network, offer them its source code (section 13 of the license).
Bundled third-party files keep their own licences, kept next to them in `app/shelfblazer_app/static/`: Font Awesome Free icons (CC BY 4.0), zxing-wasm (MIT) and zxing-cpp (Apache-2.0).
