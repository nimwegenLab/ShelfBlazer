# Changelog

ShelfBlazer follows [Semantic Versioning](https://semver.org): `MAJOR.MINOR.PATCH`.
Until 1.0, a MINOR release may add features or change behaviour, and PATCH releases are fixes only.
A change to the record or type-definition format in `data/` always gets an **Upgrading** note.

To release: set the same version in `app/pyproject.toml` and `packages/pyproject.toml`, add a section below, commit, then tag `vMAJOR.MINOR.PATCH`.
(Reinstall with `pip install -e packages -e app` afterwards so the version shown in the app follows.)

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
