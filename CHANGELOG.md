# Changelog

ShelfBlazer follows [Semantic Versioning](https://semver.org): `MAJOR.MINOR.PATCH`.
Until 1.0, a MINOR release may add features or change behaviour, and PATCH releases are fixes only.
A change to the record or type-definition format in `data/` always gets an **Upgrading** note.

To release: set the same version in `app/pyproject.toml` and `packages/pyproject.toml`, add a section below, commit, then tag `vMAJOR.MINOR.PATCH`.
(Reinstall with `pip install -e packages -e app` afterwards so the version shown in the app follows.)

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
