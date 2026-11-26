# ShelfBlazer roadmap

What is planned, possible and parked. What has been released is in [CHANGELOG.md](CHANGELOG.md), the design decisions are in [docs/design.md](docs/design.md),
and how to run it is in [README.md](README.md). Nothing here is a promise or a date.

## Next

### UI polish
The look is settled: one flat style (warm paper, hairlines, dense, one accent colour per type; Day/Night/Auto, sidebar and folding menu, mono IDs, chips, avatars, cards view, contrast and flatness tests).
Still open: forms with inline help and grouped sections, a quick-view side panel, a column picker for each person (the type page sets the columns for everybody), an icon choice per type.

### Possible features (none started)
- **Administrator MCP** (design 18.3): administrator tools on a separate `/mcp/admin` for an administrator's own agent (types, users, bulk work), with the same two-step writes. The member endpoint is built so this is an addition.
- **Checks, next steps:** tune each group's rules on its curated data; a similarity score on names if the rules prove too rigid (design 19.5); a rules editor on the Types page instead of editing the file.
- **Groups** for permissions (for example "chem-team") if roles are not enough; today a type is open to all, or to administrators.
- **`shelfblazer purge`** (design 16.4): remove a record, a file or a text from the history for good. Archive is the only removal in the app, and a type with any records (archived included) cannot be removed until this exists.
- **History extras:** difference against the previous version, a "restore this version" button, more than the last 30 entries.
- **Computed fields that can be filtered on the server** (today they are shown and sorted, not searched).
- **Per-person column choice and saved filters** on list pages.
- **Label and QR printing**, e-mail based password reset.

### Housekeeping
- A full lock file for the server image.

## Parked
- **Automated backup** of the data repository (a nightly push or archive, and a restore test): design sections 11 and 16.5.
- **Static read-only export** (rebuild a static site after each save) as a fallback viewer: design section 12.
- **An independent review of the authentication code** before an instance is exposed beyond a trusted network.
- **A SQLite index** if the record count grows a lot (the in-memory index is fine for a few thousand records).
