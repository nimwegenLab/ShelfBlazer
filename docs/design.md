# ShelfBlazer: plan

*ShelfBlazer is a general-purpose stock manager; the lab record types (strains, plasmids, primers, chemicals, plates, masters, documents) are the first set.*

A small, self-hosted lab stock manager replacing LabCollector. Records are files in a git
repository; a thin web app provides login, search, forms and permissions on top.

Status (2026-11-10): **built and in use for testing.** Import, core store, accounts, lists, forms, search, bulk edit, type admin, per-type
permissions, files, calculated fields and the UI are done (see README.md for what exists, and the status column in section 14).
Not built yet: backup job, live static export, production deployment (a Docker draft exists in `deploy/`), and the personal-data layers of section 16.
This file keeps the original design and the decisions; where the build differs from it, the text says so ("built as: ...").

---

## 1. Goals and non-goals

### Goals
- Every item has a **unique, human-readable identifier** (`ST-1261`) that is written on the
  physical tube or plate. An object is found in the lab from its identifier.
- Simple, fast **search**: one global search, per-type fuzzy search, sorting, per-field filters.
- **Version control** of all content (who changed what, when; revert).
- **Two roles**: `user` and `admin`. A record is editable only by its creator and by admins.
  Everyone logged in can read everything.
- **Custom record types** created from the UI, with the same machinery as the built-in ones.
- Usable at the **bench** (phone/tablet, scanner or typed ID).
- Small enough to be maintained by one person. No external services except a git remote for backup.

### Non-goals (v1)
- No model of physical storage (no freezer/rack/box/well grids). Exception: a short, admin-managed
  list of storage places for chemicals/reagents, and a free-text `storage` field kept from the import.
- No per-field permissions, no workflows, no ordering/budget/equipment modules.
- No formal cross-type relations except the generic `ref` field (section 4.4).
- No label/QR printing (possible later). Reading barcodes is built (the *Scan* button); printing them is not.
  (v1 had no in-app import either; the CSV/TSV import is built (D44), and migrating from another system is still a script, section 13.)

The storage non-goal is deliberate, not a gap. A freezer/rack/box model is only useful if someone keeps it true, and for a group of
fewer than about 20 people that upkeep costs more than the time saved looking for a tube. A record carries a short place
(`Box 2 Well G3`) and the physical item carries its ID.

### Related work (checked November 2026)
This comparison was made after the design, from each project's repository page and README only (the tools were not installed or run),
so "not stated" below means the README does not say, not that the feature is absent.

| Tool | Storage | What it is for | Why it is not a drop-in answer here |
|---|---|---|---|
| Onyo | Text files and folders in git (YAML) | IT assets: where a device is, who has it | Command line only; no web interface, accounts or permissions stated; the GitHub repository is archived and development moved elsewhere |
| smallFactory | Plain files in git (YAML/JSON) | Parts, bills of materials and revisions for small hardware teams | Has a phone-friendly web UI, QR labels and inventory, but no login, roles or permissions stated; custom fields rather than user-defined item types; fixed categories (entities, BOMs, revisions) |
| eLabFTW | SQL database | Electronic lab notebook with a resource database (reagents, plasmids, cell lines) | The closest lab fit and a large community; a notebook-first system with a database behind it, so records are not plain files and history is not git |
| Part-DB, InvenTree | SQL database | Electronic components and manufacturing stock | Part-centred; storage locations, labels and event logs, but no lab item types or sequence search stated |

What this leaves open is a narrow niche: **git as the only store of record, plus a multi-user web interface with accounts,
per-type permissions and user-defined item types**, for a small group that wants records it can read, diff and back up as files.
Neither git-based tool covers that, and the database-backed ones, however capable, give up the plain-file property.
Choosing ShelfBlazer over them is justified by that property, not by features: if a group does not need its records as
files in git, an established tool with a community is the safer choice.

**Where it is suited:** groups of about 5 to 30 people and a few thousand records; labs that already keep identifiers on tubes and plates;
people who want a record's full history and an archive instead of deletion.
**Where it is not:** thousands of concurrent users or a very large record count (one server process, in-memory index),
physical storage mapping, electronic notebook features, and any setting that needs a vendor-supported product.

---

## 2. Decisions log

| # | Decision | Reason |
|---|---|---|
| D1 | Source of truth = one Markdown file (YAML front matter + notes) per record, in git | History, blame, revert, survivability; ~2,500 records is tiny |
| D2 | Disposable search index, rebuilt from files (built **in memory**, not SQLite: see D19) | Search/filter/sort without a second source of truth |
| D3 | Custom Python web app (FastAPI + Jinja2 + a little vanilla JS; htmx was not needed), no JS build step | Exact permission rules; small; easy to host |
| D4 | Built-in types are schema files like custom types | Type management costs no extra code |
| D5 | IDs: `PREFIX-N`, **no zero padding**, N = legacy LabCollector `count` for imported records; new records continue from `max+1`; never reused | Existing notebook references (`PL-142`) stay valid |
| D6 | Personal accounts + shared sign-up passphrase; passwords never imported | Ownership needs identities |
| D7 | Initial admin = the existing `admin` account; other imported accounts become regular users | User request |
| D8 | Read access open to everyone | User request |
| D9 | LabCollector `secret=1` means **archived** (history shows `Archive Record` for all 207 strains) -> `archived: true`, hidden from default lists | Verified in dump |
| D10 | Plates stay single items with a note (one item per plate, not per well) | User request |
| D11 | `status` of masters stays one free-text field (values copied verbatim, HTML stripped) | User request |
| D12 | Strains owned by the pseudo-contact "common", and a few ranges, are reassigned by an explicit ownership mapping during the one-off import | User mapping |
| D13 | Backup: push to a private GitHub repo (deploy key) + nightly encrypted archive off the VM; static read-only HTML export | User request |
| D14 | Hosting: small VM provided by institute IT | User request |
| D15 | LabCollector `history` / `strains_log` are not imported; archived once as CSV | Git history starts at import |
| D16 | Hosting: institute VM with HTTPS, reachable only behind the firewall / VPN | IT answer (confirmed by user) |
| D17 | Login: the app's **own accounts** (no institute SSO). Passwords hashed with scrypt/argon2; sign-up with shared passphrase; **password reset by an admin-generated one-time link** (no email server assumed) | User decision; VPN-only access lowers exposure but auth is still enforced |
| D18 | v1 scope keeps **user admin, bulk edit and a simple type admin** (UI, not CLI). Order of delivery below; none is dropped | Usability: admins are not expected to use a CLI |
| D19 | Deviations of the build from sections 3 and 10: the search index is **in memory**, not SQLite (about 2,500 records, rebuilt at start); passwords use stdlib scrypt, not argon2; users live in `instance/users.json`; no htmx. What exists is described in README.md | Fewer dependencies |
| D20 | Code layout: `packages/shelfblazer/` is the core library (no web code, installable); `app/shelfblazer_app/` is the web app and the `shelfblazer` CLI; both installed editable. One-off import tools live outside the release | User decision; core logic testable and reusable without FastAPI |
| D21 | Everything the server needs besides the installed code lives under one **runtime home** (`var/` locally, e.g. `/srv/shelfblazer` on the VM): `data/` (records git repo), `instance/` (users, secret key), later `backups/`. Selected with `--home` or `$SHELFBLAZER_HOME`; `--data`/`--instance` override. Tracked deployment files (Caddy, systemd, backup script) will go in a separate `deploy/` folder | User decision: back up and restore one directory; the code stays replaceable |
| D22 | **Superseded by D31 and D34.** UI: a single flat look, Classic structure and system typeface with a sunny, geeky palette (cream and sun-yellow by day, navy and yellow by night), mono record IDs, left sidebar menu and hamburger on narrow screens, Auto/Day/Night control. Bright, Soft and the look switcher were dropped | User decision after comparing looks |
| D23 | (Still valid; the palette itself changed in D31.) Per-type colours are back on the sunny base: eight fixed palette names, defaults per built-in type, optional `"color"` in the type definition (names only, validated; unknown ignored with a warning) | User decision; fixed palette keeps contrast guaranteed |
| D24 | Lists: clickable rows; sort/direction/view remembered per type in a cookie; storage displayed as `Box N Well X` (stored text unchanged); `drop-field` maintenance command; documents have no category | User requests |
| D25 | Storage display is a per-field setting in the type definition (`short` default, `full`, `outdated`); strains and plasmids short, primers and plates `outdated` (original text plus `[outdated]`) | User decision after seeing examples |
| D26 | Per-type permissions are **roles only** (no named people, no groups yet): optional `permissions` block in the type definition with `read` (`all`/`admin`), `create` (`all`/`admin`), `edit` (`owner`/`admin`/`all`); defaults equal the original rule. A hidden type is invisible to non-admins everywhere and answers 404 like a missing record; deleting is never opened to everybody (creator or admin; admins only when `edit` is `admin`). Enforced in `perms.py` and the store; admins always allowed | User decision ("roles only") |
| D27 | Uploads: 25 MB each, up to 50 files per file field, everything except programs and scripts (blocked extension list); stored in `data/files/<ID>/` (in Git LFS), one commit each, download only where the record is readable (see D37 for where files can be attached) | User decision |
| D28 | Bulk edit actions: set a field, add or remove an option, change owner (admin), archive or restore (no delete since D40); preview then apply as one commit; undo by `git revert` for the person who did it or an admin | User decision |
| D29 | Calculated fields: type `computed` with an `expr` in a small whitelisted `ast` language; evaluated on display, never stored; shown, sorted, not searchable; formulas see only records the viewer may read; none enabled by default | User: "not sure yet" about which fields, so examples only |
| D30 | Type administration for admins only: roles-only permissions editable on the type page; removing a field only hides it; prefix fixed; option in use cannot be removed; every change is one validated git commit of `config/types/<slug>.yaml` | User decision |
| D31 | UI restyle (from the design session): "quiet notebook" replaces the sunny yellow look of D22: warm paper tokens, hairlines, 14px/13px dense type, 28px controls, one accent colour per type (dot, current row, 2px title rule, primary button, IDs, chips, home tiles), no zebra, no `>_` logo or `#` heading marks. Structure, Auto/Day/Night and per-type `"color"` (D23) are unchanged | Handoff from the design session working for the user (not confirmed with the user in this session) |
| D32 | Display names: Chemical is renamed Reagent and Plate Microplate (type names only; slugs `chemical`/`plate` and prefixes `CH`/`MP` stay); the menu uses plurals like page titles (name + "s"); the old free-text `storage` is not offered when creating records | User request |
| D33 | Storage places: no admin page. The dropdown offers the places in use plus "New place…" (becomes a text field; a new name is appended to `storage_places.yaml`, reused if it exists ignoring case); an unused place just stops being offered. Renaming or merging is a file edit | User decision ("only if it is not bloating the code") |
| D34 | UI round 2 (design session, approved by the user): two looks, Spectrum (type colours, default) and Classic (one accent), independent of Auto/Day/Night, each a cookie (`look`, `appearance`); lighter skeleton (sidebar in page colour, unboxed tables, dots in the menu, icons only in home tiles and the type page title); Admin entry at the end of the menu | Design session handoff, approved by the user |
| D35 | Home page per the design system (title row with counts, search, "Record types", "Recent activity" from the git log); archived records greyed out (shown only to their owner and administrators, see D40); optional `"fold": true` on a field folds it on forms (used for Related); free-text `storage` only on records that already have a value | User request |
| D36 | The order of types (menu, home tiles) is configurable: up/down on the Types admin page, stored as a list of slugs in `config/type_order.yaml`; it used to be fixed in code (built-in sequence, then alphabetical), which stays the default | User request |
| D37 | Files: only File fields hold files (`type: file`, several allowed). The importer defines one, "Files", on Plasmid and Document (as LabCollector had), and users can add more on the Types page; files chosen on a form are attached after the record is saved (per-field list `fields[key]`, folder `data/files/<ID>/`). There is no generic per-record Files section | User decision (the generic section was a mistake) |
| D38 | Document: the label "Stored file (LabCollector)" became "Stored file name" (not LabCollector-specific; key `stored_file` unchanged) | User request |
| D39 | **Layer 1 built (2026-11-10); layers 2-4 proposed.** Personal data stays out of what git keeps forever: opaque user IDs in records and commits (names only in `users.json`), encrypted personal fields with one key per person (erasure = delete the key), a tested purge procedure for accidents, backups that follow the same rules. See section 16 | Security audit (2026-11-10); user asked for the design |
| D40 | **Archive replaces delete (built 2026-11-11).** Git keeps every version, so "delete" only hid the record and promised more than it did. One state remains, `archived`: the record stays in the repository, and only its owner and administrators can see, search, open, link to or download it; the owner or an administrator restores it. Removed: the delete button and route, bulk delete, `perms.can_delete`, `Store.delete`. History links (`/r/<ID>/at/<commit>`) show a record as a commit saved it, read-only, with that commit's type definition. Real removal (history rewrite) stays the planned `shelfblazer purge` CLI (16.4), not a button: it changes every later commit ID, needs a force-push, and cannot promise erasure on GitHub. A type that still has records, archived ones included, cannot be removed until purge exists | User decision |
| D41 | **History links (built 2026-11-11).** Each History entry on a record page, except the newest (the record as it is now), opens `/r/<ID>/at/<commit>`: the record as that commit saved it, read-only, rendered with the type definition from the same commit. Read with `git show` (no checkout, no branch, no extra storage); the commit must have touched that record's file, so the URL cannot be used to read anything else. Same visibility as the record today (an archived record's versions: owner and administrators). Left out of an old version: calculated fields (they depend on today's date and on other records) and attachment contents (names only). Not built: diff against the previous version, a "restore this version" button, more than 30 entries. Purge (16.4) changes commit IDs, so old links to versions stop working | User request |
| D42 | **Personal collections (proposed 2026-11-22, decided 2026-11-24, built 2026-11-25 for 0.3).** Each type can have, besides the lab collection, one personal collection per person, using the type's own definition (people never manage types); off unless the type's `permissions.personal` allows it. Personal IDs are `ST-P17`, counted by each person from 1 (Alice's `ST-P1` and Bob's are different records; what is unique is the ID with its owner, see D45). An item moves from a personal collection to the lab one (new lab ID, the old ID kept as an alias), never back, after a warning on the page with the button (also for bulk; a first page only when a linked item needs a decision); the personal items it links to move with it, or are relinked to a matching lab item. Edit rights as today (owner or administrator). Each person has a per-type checkbox "Visible to others" (checked by default; unchecked: only the owner and administrators see the items in the app; never called "private", because the repository and its backups still hold everything). A lab record cannot link to a personal item, and a personal item cannot link to another person's. Interface: "My collection" as a second part of the sidebar, a collection choice on new records, one search table per collection. See section 17 | User decision |
| D43 | **Lab records into a personal collection: an administrator command (built 2026-11-24).** The app still never moves a lab item to a personal collection (D42). For the cases where lab records really are one person's own (a person's boxes imported with the lab stock), `shelfblazer move-to-personal <username> <IDs or ranges> [--yes]` moves that person's own records, in one commit by the maintenance account: new personal IDs, the lab ID kept in `former_ids` (it still opens the record), files and mentions follow, the lab numbers stay unused. Refused for someone else's records, types without personal collections, and records that something staying links to. Preview without `--yes` | User request |
| D44 | **Table import for everyone who may add records (built 2026-11-24).** The CSV/TSV import (upload, columns, check, one undoable commit) moves from the type admin page to `/t/<type>/import`, linked as *Import…* beside *+ New* on list pages and from the new-record form. It follows the rules of the record form: a person imports into the collections they may add to (lab collection: the type's `create`; own collection: `permissions.personal`), a row with an ID updates that record only if they may edit it, a record they may not see answers "does not exist" and is never named as a duplicate. The page of a finished import is for the person who ran it and administrators. No separate import permission | User decision |
| D45 | **Personal numbers are per person (built 2026-11-25).** Each person counts their own personal items from 1 (`ST-P1` for Alice and for Bob); the key of an item is the ID with its owner (`ST-P1~U7F3K2QA`), used in file names, links, addresses and history; people see `ST-P1`, or `ST-P1[Alice]` for someone else's. A short personal ID is read for the person it is typed by or written for. Replaces the shared counter of D42: people did not like to see other people's numbers in theirs. Section 17.2 | User request |

---

## 3. Architecture

```
browser --HTTPS (Caddy)--> FastAPI + Jinja2  (single process, single worker)
                              |
              +---------------+----------------+
              | git repo  stocks-data/         |   source of truth
              |   config/types/*.yaml          |
              |   config/storage_places.yaml   |
              |   config/type_order.yaml       |
              |   (users: instance/users.json, NOT in repo, see 10)
              |   data/<type>/<ID>.md          |
              |   data/files/<ID>/<name>       |   (stored in Git LFS)
              +---------------+----------------+
                              | rebuilt on start, updated per commit
                         in-memory search index (built: no SQLite, see D19)
```

- **Two repositories**: the app code (project root; a local git repo since 2026-11-10) and the data (`var/data`: records and schemas). `var/` is not in the code repo.
- **Writes** go through one code path: validate -> take file lock -> write file(s) -> `git add` ->
  `git commit` (author = logged-in user) -> update index row -> release lock.
- **Concurrency**: global file lock (flock) around each write; optimistic check per record (form carries the
  file hash; a stale save is rejected with "modified by X, reload"). Concurrency is not a concern at this scale.
- **ID allocation** inside the lock; per-type counter kept in `config/counters.yaml` and committed.
- **Crash recovery** at startup: remove stale `.git/index.lock`, commit or discard a dirty tree, rebuild index.

---

## 4. Data model

### 4.1 Repository layout
```
config/
  types/<slug>.yaml            one schema per record type (built-in and custom)
  storage_places.yaml          reagents only: [{id, name}]; the dropdown lists the places in use (D33)
  counters.yaml                next number per type
  type_order.yaml              order of the types in the menu (D36); optional
data/
  strain/ST-1261.md  plasmid/PL-142.md  primer/PR-12.md
  chemical/CH-5.md   plate/MP-3.md      master/MM-17.md   document/DC-9.md
  files/<ID>/<file>            attachments (.gb, .dna, .xlsx, images)
```

### 4.2 Record file
```markdown
---
id: PL-142
type: plasmid
name: pUA66
created_by: welker
created_at: 2013-02-27T15:23:57
updated_at: 2013-07-10T14:03:21
archived: false
legacy_id: 1
fields:
  resistance: [Kanamycin]
  storage: "Room 6035 / Freezer ... / box X / A1"
  attachments: [PL-142/pUA66_crpRep.gb]
---
Free-text notes in Markdown (converted from the LabCollector HTML).
```
Front matter is emitted as JSON-compatible YAML so the importer needs no YAML dependency.

### 4.3 Type schema (`config/types/<slug>.yaml`)
```yaml
name: Plasmid
prefix: PL
list_columns: [id, name, resistance, created_by, created_at]
fields:
  - {key: name,       label: Name,       type: text,        required: true}
  - {key: resistance, label: Resistance, type: multiselect, options: [Kanamycin, Ampicillin]}
  - {key: sequence,   label: Sequence,   type: longtext}
  - {key: attachments,label: Files,      type: file, multiple: true}
```

Field types: `text`, `longtext` (Markdown), `number`, `date`, `select`, `multiselect`, `bool`, `url`,
`user`, `file` (optionally multiple), `ref` (to a type; optionally multiple), `computed` (a formula, D29).
Each field has a stable `key` and an editable `label`. Optional field settings built since: `hidden` (removing a field only hides it),
`fold` (collapsed on forms), `display` (storage text mode, D25), `source` (select options from the storage places), `expr` (computed).
Optional type settings: `color` (D23), `list_columns`, `permissions` (D26).

### 4.4 References
`ref` fields store record IDs. Every record page shows an automatic **Referenced by** panel
(computed from the index), so a plasmid lists every strain that contains it. Text fields also auto-link
anything matching `[A-Z]{2}-\d+` to the record.

### 4.5 Built-in types (initial)
| Type | Prefix | Source table | Notes |
|---|---|---|---|
| Strain | ST | `strains` | `plasmid` is a multi-`ref` to plasmid; free-text `storage` from box chain |
| Plasmid | PL | `plasmids` | + `sequence` (from `plasmid_sequences`), file attachments, resistance multiselect |
| Primer | PR | `primers` | storage only where recorded (9 of 233) |
| Chemical (now **Reagent**, D32) | CH | `chemicals` | `storage` is a select from `storage_places.yaml`; `out_of_stock` bool; seller, CAS, etc. |
| Plate (now **Microplate**, D32) | MP | `Microplates` | one item per plate; free-text `storage` |
| Master | MM | `Microfluidics_Masters` | design, batch, manufacturer, status (free text), comments |
| Document | DC | `docs` | title, description, file reference; the files were recovered separately and attached through its "Files" field (category dropped, D24) |

---

## 5. Permissions

| Action | user | admin |
|---|---|---|
| Read any record, search (CSV export: not built) | yes (unless the type's `read` is `admin`) | yes |
| Create record | yes (unless the type's `create` is `admin`) | yes |
| Edit / archive / restore own record (there is no delete, see D40) | yes (the type's `edit` can make this admins-only, or open changes to all readers; see D26) | yes |
| Edit / archive others' records | no | yes |
| Bulk edit | only own records | all (plus change owner, delete) |
| Manage users, types, storage places, sign-up passphrase | no | yes |

Enforced **server-side** on every write. Only the app process has write access to the data repo.

---

## 6. UI spec

| Route | Who | Content |
|---|---|---|
| `/login`, `/signup`, `/me` | all | Sign in; sign up with shared passphrase; change own password |
| `/` | all | Global search (autofocus; an exact ID jumps to the record on Enter; results filter while typing; a fuzzy switch); results as one table per type, top 10 each with "see all N"; tiles for the types with counts; recent activity. Built as planned, except "+ new" on the tiles (dropped) |
| `/t/<type>` | all | Fuzzy search (typos and partial IDs; live), sortable columns (asc/desc), pagination, "owner" and "archived" filters, table or cards, bulk-edit toolbar. **Not built:** per-field filters generated from the schema, a column picker for users (admins choose the columns on the type page), CSV export |
| `/r/<ID>` (planned as `/t/<type>/<ID>`) | all; edit for owner/admin | Fields, notes, files (per File field), **Referenced by**, history (git log; each entry but the newest, which is the current record, opens that version at `/r/<ID>/at/<commit>`), edit / archive |
| `/t/<type>/new` | all | Schema-generated form; ID allocated on save and shown for copying onto a label |
| `/admin/users` | admin | List, create/invite, role change, deactivate/reactivate (never delete), reset-password link, rotate sign-up passphrase, reassign records |
| `/admin/types` | admin | List with each type's permission rules and order, create, edit schema (fields, options, colour, columns, permissions), remove an empty type. **Not built:** archiving types; a storage places editor (D33) |

Bench-friendly: mobile-first CSS, large tap targets, lists collapse to cards on narrow screens,
no hover-only controls. State (query, sort, filters) lives in the URL.

### Filters by field type
text/longtext: contains; select/multiselect: choose values; number: min/max; date: from/to; bool: any/yes/no; user: dropdown; ref: ID.

---

## 7. Search
- Candidate set per type is small (<= ~1,300 records), so fuzzy matching runs in Python (`rapidfuzz`) over an in-memory view
  of the index; exact and prefix ID matches rank first.
- Global search runs the same matcher over all types and groups results by type.
- SQLite holds columns for filtering/sorting and an FTS table for plain keyword search.

## 8. Bulk edit (minimal)
- List view: checkbox per row, hidden until "Select" is pressed; header box; selection kept across filter/sort/page changes (sessionStorage); "show all N" for long lists; bar with Edit selected, Clear, Done.
- Actions: set a field to a value; add/remove an option (multi-select); change owner (admin); archive/restore.
- One POST: validates against the schema, applies the ownership check per record, skips records the user cannot edit and reports them,
  shows "will change N, skip M" before confirming, then makes **one git commit** for the batch. Undo = `git revert` of that commit (the person who made it, or an admin). Built as planned (D28).

## 9. Type management rules
- Adding a field: always allowed. Renaming a label: free. Removing a field: hides it (data stays in files).
- Changing a field type: built as "never, except text <-> longtext" (even before records exist).
- Prefix: immutable once the type exists (built stricter than planned). Type deletion: only if empty (types have no archive state).
- Built since (D30, D36): colour, list columns, order of types, per-type permissions, file and computed fields, all editable on the type page.
- Schema files are versioned in git like records.

## 10. Authentication and users
- Passwords hashed with scrypt (stdlib; built instead of argon2); stored in `instance/users.json` (built instead of `config/users.yaml`), kept **outside** the data repo and to be included in the encrypted nightly archive.
- Roles: `user`, `admin`. Status: `active` / `inactive`. Deactivate instead of delete so `created_by` always resolves.
- **Bootstrap**: CLI `shelfblazer set-password <username>` run on the VM; no password is stored in config or git.
- Import creates **inactive stubs** for every LabCollector account (matching on username); an admin sends a set-password link per person.
  LabCollector passwords are never imported.
- `common` stub is not created (see D12).
- Built since the audit of 2026-11-10 (documented in README.md, section Security): CSRF tokens, a strict content-security policy, sign-in throttling, signed sessions that end on password change or deactivation, upload limits and blocked file types, safe redirects.

## 11. Backup and restore
- App pushes to a private GitHub repo after each commit (background; failures never block saves) and a nightly cron pushes anything missed.
  Deploy key, write access limited to the one repo; force-push disabled.
- Nightly encrypted archive of repo + `users.yaml` + config to a location outside the VM; keep 30 daily and 12 monthly.
- Admin banner and email if pushes fail for more than a day.
- `RESTORE.md`: new VM -> clone data repo -> restore `users.yaml` -> start app (index rebuilds). Test once before go-live.
- Check institute policy on hosting inventory data on GitHub; the remote URL can be swapped for an internal GitLab.
- Personal data and erasure constrain this design (section 16.5): GitHub cannot delete single Git LFS objects, and a push-only mirror keeps everything forever.
  **So the GitHub push above is on hold until layer 1 of section 16 is done;** prefer the encrypted, time-limited backups of 16.5 over a permanent mirror.

## 12. Static read-only export
After each commit (or on demand) render all records to a static HTML site served by the VM behind the same login. It works as a fallback viewer if the app is down.
Status: not built. A read-only archive of the old LabCollector database was built once with one-off tools kept outside the release; `deploy/build-export.sh` is a first step toward a deployable export.

---

## 13. Migration from another system

Moving an existing database into ShelfBlazer is a one-off job and is documented outside the release. The app only consumes the import format:
a folder with `config/` (type definitions), `data/` (record files) and optionally `users_stubs.yaml`, read by `shelfblazer init-data` and `shelfblazer import-users`.


---

## 14. Phases and acceptance

| Phase | Deliverable | Done when | Status |
|---|---|---|---|
| 0 | Import dry run + REPORT.md | Record counts match the dump (minus documented drops); every strain->plasmid link resolves; report reviewed | Done |
| 1 | Core: schema loader, record I/O, git commit, index | Round-trip import -> index -> record page matches source | Done (in-memory index) |
| 2 | Auth (own accounts), roles, ownership rule, **user admin UI** (list, role, deactivate, reset link, sign-up passphrase) | Non-owner cannot edit; admin can; bootstrap CLI works; permission test suite passes | Done |
| 3 | Generated list/record/form pages, global search + ID jump, filters, sorting | Search for a typo'd strain name finds it; ID jump works | Done, except per-field filters and CSV export |
| 4 | Referenced-by, attachments, archive, history view | Plasmid shows its strains; `.gb` files download | Done (attachments as File fields, in Git LFS) |
| 5a | Bulk edit (checkbox select, set field / add option / change owner / archive; one commit per batch) | Change owner of 100 records in one commit; revert works | Done |
| 5b | **Simple type admin**: create a type, add fields and options, rename labels; no field removal or type change in the UI (hide only) | Create a type from the UI, add a record, find it in search | Done, with more than planned |
| 6 | Backup, restore test, static export, deployment on the VM | Restore on a clean VM succeeds from the GitHub copy | Not started (Docker draft in `deploy/`; see 11 and 16.5 first) |
| 7 | (added) Personal-data layers of section 16 | See 16.7 | Proposed |

## 15. Risks and open items
- Docs files and plasmid map images are not in the dump: they were recovered separately and 39 of them are attached; the report lists the ones still missing.
- Free-text `status` of masters mixes status and holder initials; copied as is (D11).
- Most imported strains are owned by one former member's account; admins can edit them; reassign later via bulk edit.
- Confirm institute policy on GitHub hosting (and see the note in section 11); confirm VM reachability and HTTPS.
- Personal data in the git history cannot be erased today (section 16). Do layer 1 before the data repo gets a remote; layer 2 before any type holds personal fields (orders, clubs).
- Primers have little storage data (9 of 233); chemicals rely on the 8 storage places.

---

## 16. Personal data and erasure (proposed, D39)

### 16.1 The problem
Git is append-only by design: every version of every record, every commit author and every commit message stays in the history
and in every clone and backup. That is what makes history, undo and backups cheap, and it is also why nothing can be erased.
The GDPR gives people a right to erasure, so a template meant for clubs, private use or order management must not depend on deleting things later.

**Principle: keep personal data out of what git keeps forever.** Only data that may stay forever goes into the history unprotected.

What the lab data repo holds today (checked 2026-11-10):

| Where | What | Erasable today? |
|---|---|---|
| `created_by` in every record | username (e.g. `jdoe`) | No (in every past version) |
| commit author | display name and `<username>@shelfblazer.invalid` | No |
| commit messages | `ST-1365: create by jdoe`, `bulk: owner to jdoe (…)` | No |
| free-text fields and notes | whatever people type (names of collaborators, phone numbers, …) | No |
| attachments (`data/files/`, Git LFS) | scans, quotes, gel images; could be invoices with names | No, and GitHub cannot delete single LFS objects |
| `users.json` (outside git) | names, roles, password hashes, sessions | Yes |

`legacy_owner` holds only `keeper none`, `keeper 0` or `common` (no names), and no email addresses were found in the records.
The old LabCollector tables with contacts are no longer copied into the data repo (they stay in the LabCollector backup).

Erasure has exceptions: orders are accounting records that the law usually requires keeping for a number of years (check the Dutch and
university rules). So the goal is not "delete everything on request" but: **collect little, keep it apart, and be able to erase what need not be kept.**

### 16.2 Layer 1: opaque user IDs in git (built 2026-11-10)
Built as described below, with these details: IDs look like `U7F3K2QA` (upper case, so never a username); the users file upgrades itself on first load;
`init-data` imports the stubs and converts owners before the first commit; formulas' `owner` is the display name; commit authors other than people
(the importer, `maintenance`) stay as they are. `legacy_owner`: the importer writes only `common` or `unknown` (contact names are never kept).

- Every account gets a stable random ID (e.g. `u7f3k2`, generated once, never reused) stored in `users.json`. The username becomes a sign-in name only and can change.
- Records store the ID: `created_by: "u7f3k2"`; `user` fields too. Commit authors become `u7f3k2 <u7f3k2@shelfblazer.invalid>` and
  messages `ST-1365: create by u7f3k2`. The committer stays `ShelfBlazer`.
- Pages resolve the ID to the display name at render time (lists, record pages, History, Recent activity, owner filter, bulk owner).
  An ID without an account shows as "former member".
- **Erasing a person** = blank their `users.json` entry (keep the ID as a tombstone so it is never reissued). The history still says
  `u7f3k2 created ST-1365`, but nothing links that ID to a person any more. Deactivation stays the normal way to remove someone; erasure is the explicit step.
- Bulk undo identifies "the person who made this bulk edit" by the ID in the author email (today: the username).
- **Migration:** one rewrite of the data repo (records + history), cheap while it has 2 commits and no remote. The importer maps LabCollector keepers
  straight to IDs. `users_stubs.yaml` keeps usernames; IDs are created on import.
- Code touched: `auth.py` (ID creation, lookup, tombstone), `store.py` (commit author, message, `created_by`, activity and history parsing, undo),
  `web.py` and templates (display), importer, CLI (`erase-user`). Tests: no username appears in any commit or record after a full UI pass.

### 16.3 Layer 2: personal fields, encrypted per person (with the orders extension)
Some types genuinely need personal data: requester, delivery address, phone, member list.
- A field definition may say `"personal": true` (with `"about": "requester"` naming the person field it belongs to, default the record owner).
- The store encrypts these values (e.g. AES-GCM via `cryptography`, or libsodium) with a **key per data subject**, kept in
  `instance/keys/` (outside git, backed up separately and encrypted). The record file holds `"enc:v1:<key id>:<ciphertext>"`.
- **Erasure** = delete that person's key: the value becomes unreadable in the working tree, every past commit and every backup at once,
  with no history rewrite (crypto-shredding). The app shows "erased".
- Search, sort and formulas work on decrypted values held in memory only (the index is already in memory and rebuilt at start).
  Decrypted values are never written to disk, logs or commit messages; bulk edit and the static export skip or redact them.
- Per-type **retention**, e.g. `"retention": {"field": "closed_at", "years": 7, "then": "erase-personal"}`: a nightly job erases the
  personal fields of records past their retention, by deleting per-record keys (so a person's other records are not affected).
  Per-record keys (one key per record and subject) make that possible; per-person keys make "erase everything about X" one step. Use both:
  a person key wraps the record keys.
- Free-text notes cannot be classified automatically: types that hold personal data should prefer structured personal fields, and the form
  says so under the notes box.
- Attachments of such types (quotes, invoices): encrypt with the record key before they go to Git LFS, or keep them outside git under
  the same retention. Decide with the orders design; it also needs per-record read rules and an "assistant" role between user and admin (section 5 today has neither).

### 16.4 Layer 3: purge procedure for accidents
Someone will paste a phone number into the notes or upload the wrong scan. For those rare cases:
- `shelfblazer purge --record ST-12 | --file data/files/ST-12/scan.pdf | --text "<string>"` rewrites the history with `git filter-repo`
  (and `git lfs prune` for LFS objects), records what was purged (not the content) in an admin log outside git, and prints the steps for the backups.
- After a purge: force-push to the backup remote (the only time it is allowed, by an admin, by hand), and every clone must be replaced.
  Commit IDs change, so bulk undo links on old result pages stop working; that is acceptable (so do links to old versions of a record, D41).
- Purge is also the only way to remove a record for good (the app only archives, D40), and the only way to remove a type that still has records.
- Test the procedure once on a copy, as part of the restore test.

### 16.5 Layer 4: backups that follow the same rules
- A push-only GitHub mirror (section 11) keeps everything forever, and GitHub cannot delete single Git LFS objects (only the whole repository).
  That is incompatible with layer 3.
- Prefer **encrypted backups with a fixed retention window** (e.g. restic, or encrypted `git bundle` files) on a server the institute controls:
  after a purge, old copies age out on their own (e.g. 30 daily + 12 monthly means a purge is complete after a year at most; state this in the privacy notice).
- With layers 1 and 2, the backed-up history holds nothing identifying except what layer 2 encrypts; `users.json` and `instance/keys/`
  are backed up separately with their own (shorter) retention, so a deleted key does not survive in an old key backup for longer than the stated window.
- If a GitHub mirror is kept for convenience, push only after layer 1 is in place and never put encrypted-field keys there.

### 16.6 Documentation for the template
Each template (lab, club, orders) states: which fields are personal and why, how long they are kept, who can see them, how a person asks
for erasure and what is erased (and what is kept for legal reasons), and how long backups keep copies. A short `PRIVACY.md` in the data repo
generated from the type definitions (`personal`, `retention`) keeps this in sync.

### 16.7 Order of work
| Step | When | Done when |
|---|---|---|
| Layer 1 (opaque IDs) | Before the data repo gets a remote | No username or display name in any record, commit author or message; `erase-user` leaves only a tombstone; existing tests pass |
| Layer 4 (backups) | With the backup job (phase 6) | Restore test from the encrypted backup, LFS contents included; retention documented |
| Layer 3 (purge) | With the backup job | Purge of a record and of a file tested on a copy, backups updated |
| Layer 2 (personal fields, retention) | With the orders extension | Erasing a person makes their personal fields unreadable in an old clone; retention job tested |


---

## 17. Personal collections (built, D42)

Status: proposed 2026-11-22, decided 2026-11-24, built 2026-11-25 on the `collections` branch for 0.3 (steps 0 to 4 of 17.8; step 3 tried on a copy of real data on 2026-11-25, desktop and phone). Where this text and the
code differ, the code and the decisions D42, D43 and D45 win; the text below keeps the reasoning.

### 17.1 Model
- For each record type there is the **lab collection** (what exists today) and one **personal collection** per person. A new person's
  personal collections are empty; they fill them by creating items, or by importing a table into them.
- A personal item uses the **same type definition** as the lab collection: same fields, options, validation, formulas. People do not
  administer types (types stay admin-only, D30).
- An administrator enables personal collections per type with `permissions.personal`: `off` (the default), `all`, or `admin` (only
  administrators keep personal items). A hidden type (`read: admin`) has no personal collections.
- **Edit rights are those of today:** the owner or an administrator changes, archives and restores a personal item, whatever the type's
  `edit` setting says (`edit: all` does not open someone's personal items to others).

### 17.2 IDs
- A personal item has the ID `<PREFIX>-P<n>`, for example `ST-P17`, **counted by each person from 1** (a counter per type and person,
  `"strain.P.<user ID>"` in `config/counters.yaml`), separate from the lab counter, so personal items never use up lab numbers.
  `ST-P1` of Alice and `ST-P1` of Bob are different records: what is unique is the ID together with its owner.
- **The key.** Files, links, addresses and history need something unique, so the key of a personal item carries its owner's opaque user ID:
  `ST-P17~U7F3K2QA` (`data/strain/ST-P17~U7F3K2QA.md`, `data/files/ST-P17~U7F3K2QA/`, `/r/ST-P17~U7F3K2QA`, and in `fields` and `former_ids`).
  The user ID is already in every record (`created_by`), so no new personal data enters the repository. Lab IDs have no owner part.
- **What people see and type: the short form.** `ST-P17` for your own, `ST-P17[Alice]` for someone else's. A short personal ID is read for
  the person it is written by or for: typed in a link field or a note of a personal item, it means that item's owner's; typed in the search
  or the address bar, it means yours (and when you have none, the search lists everybody's `ST-P17`). Pages are written with keys, and one
  module (`idlabels.py`) turns the keys in the visible text of a finished page into labels; a test asserts no key is readable on any page.
- Whether an item is personal is derived from the ID, not stored in a second field. One module parses and formats IDs for the whole code
  base, the browser picker included: `([A-Z]{2})-(P)?(\d+)(~owner)?` with the owner only after a `P`. The existing pattern does not match
  `ST-P17`, so it never links to `ST-17` by mistake.
- Sorting by ID uses (collection, number, owner): `ST-17` and `ST-P17` are different records with the same number.
- Exports write your own personal IDs short (an import reads them as yours) and other people's with their owner.

### 17.3 Visibility
- Each person has, **per type** (not per item), a checkbox **"Visible to others"** for their personal collection. Checked (the default):
  everybody who can read the type sees, searches and opens the items, read-only. Unchecked: only the owner and administrators do. The choice
  is stored with the account in `users.json` (outside git) and can be changed at any time.
- **The word "private" is not used** in the interface or the documentation. Unchecked only hides the items in the app: administrators, the
  data repository and its backups still hold everything. The checkbox's help text says so.
- **Unchecking is a convenience, not access control.** Anyone with access to the repository or its backups can read every personal item,
  whatever the checkbox says. Nothing that must stay confidential belongs in a personal collection.
- Every read path applies visibility through one rule, `perms.can_see_record()` (today the rule is written in several places): global and
  per-type search, ID jump, lists, record pages, Referenced by, formulas, file downloads, Recent activity, history links, exports, scan.
  An item that is not visible answers 404 like a missing record. A test requests every read path for a hidden item.
- Archived personal items follow D40 (owner and administrators only), whatever the collection's visibility.

### 17.4 Links
- **A lab record cannot link to a personal item** (ref fields reject it; the picker does not offer it). The collection's visibility can
  change at any time, so such a link could break for everybody else.
- **A personal item links to lab items and to its owner's own personal items only**, never to another person's personal items, visible or
  not, for the same reason.
- Text in notes that mentions a personal ID the viewer cannot see is shown as plain text, not as a link.

### 17.5 Moving an item to the lab collection
- Who: the owner or an administrator; only an administrator when the type's `create` is `admin`.
- **Linked personal items move too.** A lab record cannot link to a personal one, so moving an item also moves the personal items it links
  to, and the ones those link to in turn. When a lab item looks the same (same name, or the same sequence for types with a sequence
  search field), the preview offers to link to that lab item instead of moving the personal one; the person chooses per item.
- **One page with the warning and the button, for one item and for the bulk action alike:** the new lab ID or IDs, the linked items that move
  along or are relinked, validation problems against the current type definition, lab items with a similar name, and the consequences (the
  move cannot be undone by the user, the tube needs a new label, the item and its history become part of the lab collection and visible to
  everybody who can read the type), and the button that names what moves ("Move 12 strains and 3 plasmids to the lab collection"). A first
  page comes before it only when a linked personal item has a look-alike in the lab, to ask what to do about it (decided 2026-11-25: the
  two pages were redundant when there was nothing to decide).
- What happens, in one commit (one commit for a bulk move too): the next lab number (`ST-1262`), `git mv` of the record file, move of
  `data/files/ST-P17~U7F3K2QA/`, every mention of it in all records rewritten (a short mention in the notes of its owner's items too),
  `former_ids: [ST-P17~U7F3K2QA]` kept in the record. `/r/ST-P17` (for its owner) and the key redirect to the new ID, and the history reads across the rename (`_rel_paths` includes `former_ids`). The owner (`created_by`) is unchanged.
- **No way back:** nothing in the app moves a lab item to a personal collection (an administrator can, from the command line: D43). Only an administrator can undo a move (`git revert` of its commit);
  the lab number then stays unused (allowed by D5). The bulk undo of D28 does not apply to moves for other people.

### 17.6 Interface
Two places rather than a switch on one list, because most people are not expert users.
- **Words:** "Lab collection" and "My collection" (singular: each holds several types). The lists are "Strains", "My strains" and
  "<Name>'s strains". Not "group" (reserved for permission groups) and never "private".
- **Sidebar and phone menu:** the types as today (the lab collection), then a heading **My collection** with only the types that allow
  personal items.
- **A personal list is recognisable at a glance:** title "My strains", the person's avatar, and a line under the title ("Only in your
  collection. Visible to others: yes · change"). Behind it, the lab list and the personal list are the same page and the same record query.
- **New record:** the form starts with "Add to: Lab collection / My collection". Lab collection is the default; New from a personal list
  picks My collection.
- **Record page of a personal item:** a band at the top ("In <Name>'s collection, not in the lab collection"); for the owner and
  administrators, a "Move to the lab collection" button.
- **Global search:** one table per collection and type with hits: the lab ("Strains"), yours ("My strains"), then other people's visible
  collections by name ("<Name>'s strains").
- **Search in a type's list:** the lab list shows lab results only (sorting, pages and bulk selection work on one table), then one line when
  personal collections have hits ("Also in collections: yours (2), <Name>'s (3)") linking to the global search. A personal list searches
  that collection only.
- **Other people's collections:** not in the sidebar; a line at the end of a personal list ("Collections of others: <Name> (12), <Name> (4)").
- **Home:** the lab tiles as today, then a "My collection" row with its counts.
- **Me page:** the "Visible to others" checkbox for each type that allows personal items.

### 17.7 Other effects to handle
- Table import: a choice of target collection, for everyone who may add records (D44).
- Bulk edit works within one collection; change owner stays admin-only.
- Type admin: options in use and "the type still has records" count personal items too.
- Scan finds the personal items the viewer may see.
- People leaving: `erase-user` refuses while the person has personal items (archived ones included): an administrator moves them to the lab collection or archives them first. A reassignment does not exist, because the owner is part of the ID. `--leave-personal` erases anyway; the items then stay, for administrators only (the owner's choice of visibility goes with the account).
- `rename_prefix` renames `ST-P17` to `BS-P17` as well (keys and short mentions).
- **No downgrade** once a personal item exists: earlier versions do not understand `ST-P17` (an Upgrading note in the CHANGELOG).

### 17.8 Order of work (on a branch)
| Step | Done when |
|---|---|
| 0. Preparation, no visible change: one ID module, one visibility rule (the search index included), one record query for lists, bulk, export, the picker and Home, "rewrite mentions of IDs" shared with `rename_prefix` | Existing tests pass, plus a test that requests every read path for a record the viewer may not see |
| 1. Core: `ST-P<n>` IDs and counter, `permissions.personal`, per-person visibility, link rules, creating into a collection | That test passes for a hidden personal item; the lab counter does not move |
| 2. Move to the lab collection (single and bulk, one page with the warning, linked items moved or relinked) | Round trip with files; old ID and old links open the record; history across the move; admin undo |
| 3. Interface (section 17.6) and table import into a personal collection | Tried on a copy of real data, on a phone and a desktop. Done |
| 4. Docs: README, CHANGELOG (Upgrading), this section marked built | Done |
