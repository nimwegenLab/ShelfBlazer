"""Tab-separated export: columns, values, privacy, spreadsheet-formula safety."""
import csv, io, unittest

from shelfblazer import export
from test_bulk import ALICE, BOB, ROOT, Base


def rows(text):
    return list(csv.reader(io.StringIO(text), delimiter="\t"))


class TestExport(Base):
    def test_columns_skip_hidden_fields_and_add_owner_created_archived(self):
        keys = [k for k, _ in export.columns(self.store, "strain")]
        self.assertEqual(keys, ["id", "name", "genotype", "tags", "ok", "plasmid", "calc", "created_by", "created_at", "archived"])

    def test_header_and_one_line_per_record_in_the_order_given(self):
        text, n = export.tsv(self.store, ALICE, "strain", [self.a[1], self.a[0]], ["id", "name", "genotype"])
        self.assertEqual(n, 2)
        self.assertEqual(rows(text), [["ID", "Name", "Genotype"], [self.a[1], "a1", "g"], [self.a[0], "a0", "g"]])

    def test_values_are_readable_text(self):
        self.store.update(self.a[0], ALICE, "a0", {"tags": ["a", "c"], "ok": True, "plasmid": [self.p]}, "", self.version(self.a[0]))
        text, _ = export.tsv(self.store, ALICE, "strain", [self.a[0]], ["tags", "ok", "plasmid", "calc", "created_by", "archived"])
        self.assertEqual(rows(text)[1], ["a, c", "yes", self.p, "1", "Alice A", ""])             # the owner by name, never by user ID

    def test_unknown_and_hidden_columns_are_ignored(self):
        text, _ = export.tsv(self.store, ALICE, "strain", [self.a[0]], ["name", "hiddenf", "nope"])
        self.assertEqual(rows(text), [["Name"], ["a0"]])

    def test_only_records_the_viewer_may_read_of_this_type(self):
        self.store.set_archived(self.b[0], BOB, True)                                       # an archived record is for its owner and admins only
        text, n = export.tsv(self.store, ALICE, "strain", [self.a[0], self.b[0], self.b[1], self.p, "ST-999"], ["id"])
        self.assertEqual((n, [r[0] for r in rows(text)[1:]]), (2, [self.a[0], self.b[1]]))
        self.assertEqual(export.tsv(self.store, ROOT, "strain", [self.b[0]], ["id"])[1], 1)

    def test_tabs_and_line_breaks_stay_inside_their_cell(self):
        self.store.update(self.a[0], ALICE, "a0", {"genotype": "x\ty\nz \"q\""}, "", self.version(self.a[0]))
        text, _ = export.tsv(self.store, ALICE, "strain", [self.a[0]], ["id", "genotype"])
        self.assertEqual(rows(text)[1], [self.a[0], 'x\ty\nz "q"'])

    def test_text_that_a_spreadsheet_would_run_gets_an_apostrophe(self):
        self.assertEqual([export.safe(v) for v in ("=1+1", "@SUM(A1)", "+cmd", "-cmd", "-80", "+1.5", "1e-3", "a=b", "", "plain")],
                         ["'=1+1", "'@SUM(A1)", "'+cmd", "'-cmd", "-80", "+1.5", "1e-3", "a=b", "", "plain"])
        self.store.update(self.a[0], ALICE, "=HYPERLINK(1)", {}, "", self.version(self.a[0]))
        self.assertEqual(rows(export.tsv(self.store, ALICE, "strain", [self.a[0]], ["name"])[0])[1], ["'=HYPERLINK(1)"])

    def version(self, rid):
        return self.store.records[rid]["_version"]


if __name__ == "__main__":
    unittest.main()
