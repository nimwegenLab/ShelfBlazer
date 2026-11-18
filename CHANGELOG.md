# Changelog

ShelfBlazer follows [Semantic Versioning](https://semver.org): `MAJOR.MINOR.PATCH`.
Until 1.0, a MINOR release may add features or change behaviour, and PATCH releases are fixes only.
A change to the record or type-definition format in `data/` always gets an **Upgrading** note.

To release: set the same version in `app/pyproject.toml` and `packages/pyproject.toml`, add a section below, commit, then tag `vMAJOR.MINOR.PATCH`.
(Reinstall with `pip install -e packages -e app` afterwards so the version shown in the app follows.)

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
