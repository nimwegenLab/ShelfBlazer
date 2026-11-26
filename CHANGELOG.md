# Changelog

ShelfBlazer follows [Semantic Versioning](https://semver.org): `MAJOR.MINOR.PATCH`.
Until 1.0, a MINOR release may add features or change behaviour, and PATCH releases are fixes and small additions that need nothing from the person upgrading.
A change to the record or type-definition format in `data/` always gets an **Upgrading** note.

To release: set the same version in `app/pyproject.toml` and `packages/pyproject.toml`, add a section below, commit, then tag `vMAJOR.MINOR.PATCH`.
(Reinstall with `pip install -e packages -e app` afterwards so the version shown in the app follows.)

## [Unreleased]

## [0.4.1] - 2026-11-26

### Added
- **License:** ShelfBlazer is released under the GNU Affero General Public License, version 3 or later (`LICENSE`, and `license` in both `pyproject.toml` files).
- `deploy/backup-archive.sh` can send the encrypted archive to a remote `user@host:path` with rsync over ssh (retention applied there too, nothing but rsync needed on the remote side), so the server needs no space for the archives.
- Types page: *Record picture* in the type settings chooses the File field whose first image is the record's picture (no more hand-editing `thumbnail` in the type file). Hiding that field clears the setting.
- File fields can be limited to pictures (*pictures only* on the Types page, `pictures_only: true` in the type file): other files are refused on upload, and the file chooser offers images.
- MCP `get_picture` returns a record's picture (the type's `thumbnail` field) as an image the assistant can look at; `get_record` names it. Read only, same visibility as the record.

### Changed
- README: a new opening (what ShelfBlazer is, six things to know, the quick start first), the AI assistant, checks and Help page at the top of *What it does*, and a License section.

## [0.4.0] - 2026-11-26

### Added
- **The AI assistant is easy to find and to set up** (when the MCP endpoint is on): *Connect AI assistant* in the menu, next to Help (*AI assistant* once the person has a token), and a line on the new-record form open the account page's *AI assistant* tab. It says in plain words what the assistant does and walks through the setup in four numbered steps (install Claude Code, create a token, connect with two copyable commands, try it), lists the person's tokens, and opens on the connect step right after a token is made. The connect command uses `--scope user`, so the assistant works in every folder. The Help page keeps a short section with a button to that tab.
- **The account page has tabs**: General (display, own collection), AI assistant and Password.
- **MCP `search_records` answers questions about field values**: conditions on fields (`where`: empty, not empty, contains, equal, before/after for numbers and dates, including when a record was created or changed), chosen field values with each result (`fields`), sequence search with mismatches, sorting (`sort`), and counts per value (`group_by`), so an agent can answer "how many strains have no plasmid" in one call. Hidden fields and records stay out of conditions, values and counts.
- Account page: *Copy* buttons for a new access token and the two connect commands, and a clearly visible text selection.
- **Help page** (*Help*, above your name in the sidebar): what to know first (search before you add, who can change what, IDs, archiving), then how to find, add, link, import and export records, with its own section for personal collections, scanning and the MCP endpoint where those are switched on. Examples come from the instance's own types. The lab's own section, shown right after the summary under the title of its first heading, is `config/help.md` in the data repository; administrators edit it on *Admin → Help text* (one commit per save, with a preview). Small *Help* links on the new-record form, the *only the creator can edit* note, *My collection* lists, the move, import and scan pages jump to the matching section.
- **MCP endpoint (off unless `SHELFBLAZER_MCP=1`)**: `/mcp` lets a person's own Claude Code search, create, update, archive and import records as that person, with member rights even for administrators. Personal access tokens on the account page (name, expiry, created with the password, shown once and pasted at a hidden prompt so it never enters the shell history, stored hashed, revoked with the account and on any password change; wrong tokens throttled on their own budget, never locking a valid token or a sign-in). Tools: `list_types`, `search_records`, `get_record`, `check_record`, `create_record`, `update_record`, `archive_record`, `restore_record`, `import_table`, `undo_import`; resources `conventions://<type>` and `schema://<type>`. Commits made this way say "(via MCP)". Needs the `mcp` Python package. Not meant to be switched on for a group before the authentication review.
- Check before saving: when a new or edited record looks like a duplicate of one you can see (same name by default; what counts as "the same" can be set per type in `config/checks/<type>.yaml` in the data repository), the form shows the records and lets you save anyway. The same file can give suggested fixes (a button fills the field) and warnings about conventions, such as a linked record named in a name but not linked.

### Changed
- The shared text no longer names one group's types or history: example IDs and formula examples come from the instance's own types, the assistant's instructions and tool descriptions are generic (a group's own rules stay in `config/checks/<type>.yaml`), and the keeper imported from a previous system is shown under the label of its field in the type definition ("Previous owner" when the type has none). A test renders every main page for a made-up instance to keep it so.

### Upgrading
- **No change to existing records or type definitions.**
- Every type now gets the same-name check, so a form can show the *Check before saving* panel for names that already exist (archived records included). It only asks once; nothing is blocked. To refine or silence it, add `config/checks/<type>.yaml` to the data repository (README, "Checks before saving"); a file with a mistake is reported on the Types page and ignored.
- The import preview also shows these findings per row (it did only exact-name warnings before).
- Nothing to do for the Help page. To give it a lab section, write `config/help.md` (or use *Admin → Help text*); the record "how to use" document it replaces can be archived.
- The MCP endpoint is off unless `SHELFBLAZER_MCP=1`, and needs the `mcp` Python package: run `pip install -e packages -e app` again (or rebuild the image).
- Access tokens are kept with the accounts in `users.json` (a `tokens` list per person, hashes only). Going back to 0.3 is safe: 0.3 ignores the list and keeps it when it saves the file.
- No data-repository tag or backup is needed beyond the usual ones: records and type definitions do not change.
- **Types without an `icon` or `color` no longer borrow defaults from the code** (the table for the first instance's type names is gone): such a type gets a colour derived from its name and a box icon, and types not placed on the Types page follow alphabetically. An instance that relied on those defaults sets `icon` and `color` in its type definitions and saves its menu order (Types page) before upgrading, or its types change colour and order.

## [0.3.0] - 2026-11-25

### Added
- **Personal collections.** A type can let each person keep their own items beside the lab collection (`permissions.personal`: `off`, `all`, `admin`; off by default). Personal items use the type's fields and rules. Each person counts their own from 1 (`ST-P3`); someone else's shows as `ST-P3[Alice A]`, and the file name, links and addresses carry the owner (`ST-P3~U7F3K2QA`). The sidebar, lists, new-record form, search, Home, scan, export and import know the two collections. *Visible to others* (Me page, per type, on by default) hides a person's items from other people's lists, search and links; administrators still see them. A lab record cannot link to a personal item, and a personal item cannot link to another person's.
- **Move to the lab collection:** one page with what will happen and the button (also for several items). The personal items a moved item links to come along, or are linked to a matching lab item (a first page asks only when there is a look-alike). The old ID keeps opening the record, and *Undo* is available to administrators.
- `shelfblazer move-to-personal <username> <IDs> [--from FILE] [--yes]`: moves a person's own lab records into their personal collection (preview without `--yes`).
- `shelfblazer erase-user` refuses while the person still has personal items (archived ones count); `--leave-personal` erases anyway.
- Import from CSV/TSV is open to everyone who may add records of a type, not only administrators: *Import…* beside *+ New* on list pages (and a link from the new-record form), into the lab collection or one's own. A row with an ID updates that record only if the person could edit it by hand. The import page moves to `/t/<type>/import` (the old administrator address redirects).
- Scan: when no record carries a code, it can be added to an existing entry (the same product in a new package). Choose the entry with the usual search, tick the codes to keep (lot numbers and unsure codes start unticked when a product code is present), save: one commit, and the next scan opens that entry. Only for entries the person may edit.
- The import page offers an empty template file with the type's column titles.
- Strain lists wrap long storage text onto several lines; the sidebar puts Scan under the product name and aligns its entries on the left.

### Upgrading
- **No change to existing records or type definitions.** Personal collections are off until an administrator sets `permissions.personal` on a type, and nothing else changes until someone creates a personal item.
- **No way back to 0.2 once a personal item exists:** 0.2 does not know IDs such as `ST-P3` or the owner in a file name (`ST-P3~U7F3K2QA.md`). Before deploying 0.3, tag the data repository (`git tag before-0.3`) and keep a backup of the instance folder, so 0.2 can be restored.
- `config/counters.yaml` gets one counter per person and type (`strain.P.<user ID>`) when they create their first personal item.
- To give a person their own stock after the upgrade: enable personal collections for the type, preview with `shelfblazer move-to-personal <username> <IDs>`, then repeat with `--yes`.

## [0.2.1] - 2026-11-18

### Added
- Edit form: a Scan button beside each of a type's scan fields adds the codes the camera reads to that field. GS1 codes are cut to their product part (no lot or expiry); when a label carries several codes, they are listed with what each one is and the product codes ticked.

### Changed
- Phones: Scan is a button next to the menu button on every page (no longer in the menu or under the Home search); the phone menu signs out with the same icon as the sidebar.
- Home tiles: in Day the hover edge and the New (+) border take the type colour; Night keeps the grey edge.
- Cards: select fields show as chips beside the ID without their label, other fields on one line each, and the picture fills the right third of the card. On the record page the picture sits beside the fields (above them on phones).
- Files are added on the edit page only; the record page lists attached files (with "remove") and hides empty file fields.
- Sign-in page: the account help is on two lines.

### Upgrading
- No change to the record or type-definition format.

## [0.2.0] - 2026-11-18

### Added
- Record picture (optional, per type with `thumbnail`): the first image of a File field shows on the record page and as a cover on the cards; photos are shrunk in the browser before upload.
- Barcode scanning (optional, per type with `scan_fields`): the camera or a USB reader finds a product by the codes it carries, decoded in the browser; unknown codes offer to add the product with the code filled in. The security policy allows `'wasm-unsafe-eval'` for the bundled decoder.
- `run_tests.py`: runs both test suites with one process per test file, several at a time (about four times faster on 8 cores).

### Changed
- New Home page: a large live search (no Search button), the types as tiles capped at four per row with a New button and the archived count, and Recent activity as a timeline of the last 7 events grouped by day, with record names.
- Phone menu: a full-screen layer with its own top bar and close button, larger rows, a "Sign out" text button and the version at the bottom. Scan appears in the desktop sidebar, under the Home search on phones and at the top of the phone menu.
- Site name and page titles: one consistent size (16px), the title never larger than the name.
- Internal: the web app and the record store are split into smaller modules (no change in behaviour); the tests share their fixtures.

### Upgrading
- No change to the record or type-definition format. `scan_fields` and `thumbnail` are new, optional type settings.
- The security policy now allows `'wasm-unsafe-eval'` (the barcode decoder runs as WebAssembly). If a reverse proxy sets its own Content-Security-Policy, add it there too.

## [0.1.1] - 2026-11-18

### Added
- Import records from a CSV, TSV or semicolon-separated file (administrators, from the type page): choose what each column is, check every row, then import as one commit with Undo. Rows with an ID update existing records, rows without one create new ones; blank cells leave fields as they are; one bad row stops the whole import.
- `deploy/backup-archive.sh` and `deploy/restore-archive.sh`: a daily public-key-encrypted archive of the accounts, the git history and every attached file, with 30-day plus monthly retention, and a tested restore.
- `deploy/backup-push.sh`: pushes the data repository to a backup remote within a minute of any change, and keeps Git LFS uploads within a size budget (commits still go up, attachments wait).

### Changed
- The version number sits at the right of the name in the sidebar header (it was at the foot of the bar).
- Record pages (and old versions of a record) show a quiet "‹ Plasmids" link back to the list above the title, instead of the type pill.

## [0.1.0] - 2026-11-17

First release.

### Features
- Git-backed record store: one Markdown file per record, one commit per change, history and undo.
- Accounts with roles, creator-or-admin editing rule, one-time set-password links, login throttling, user administration.
- Global search (typo tolerant, jump by ID), generated list, record and form pages, filters, sorting, back-links between records.
- Reference picker, bulk edit with preview and undo, file upload (25 MB per file, stored in Git LFS), calculated fields.
- Type administration (types, fields, options, colours, list columns) and per-type read, create and edit permissions.
- `shelfblazer init-data --empty` starts a new instance with one starter type and an `admin` account; `init-data --from DIR` starts from an import folder.
- Day, Night and Auto appearance, per-type colours, sidebar menu, phone layout.
- Records changed outside the app are picked up without a restart; option changes are saved as one commit.

### Upgrading
Nothing to do (first release).
