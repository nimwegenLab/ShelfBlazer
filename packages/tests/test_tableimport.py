"""Import from CSV/TSV: reading the file, mapping columns, checking rows, one undoable commit."""
import unittest

from shelfblazer import tableimport as ti
from shelfblazer.errors import Forbidden
from helpers import ALICE, ROOT, Base


def parsed(text, **kw):
    return ti.parse(text, **kw)


class TestReading(unittest.TestCase):
    def test_utf8_with_bom_and_windows_1252(self):
        self.assertEqual(ti.decode("﻿name\tgenotype\n".encode("utf-8")), "name\tgenotype\n")
        self.assertEqual(ti.decode("Müller".encode("cp1252")), "Müller")

    def test_binary_and_oversized_files_are_refused(self):
        with self.assertRaises(ti.FileProblem):
            ti.decode(b"PK\x03\x04\x00\x00")
        with self.assertRaises(ti.FileProblem):
            ti.decode(b"x" * (ti.MAX_BYTES + 1))

    def test_the_delimiter_is_detected(self):
        self.assertEqual(ti.detect_delimiter("a\tb\tc\n"), "\t")
        self.assertEqual(ti.detect_delimiter("a,b,c\n"), ",")
        self.assertEqual(ti.detect_delimiter("a;b;c\n"), ";")
        self.assertEqual(ti.detect_delimiter("a\tb,c\n"), "\t")                         # tab wins a tie
        self.assertEqual(ti.detect_delimiter("name\n"), ",")

    def test_quoted_cells_may_hold_delimiters_and_line_breaks_and_lines_are_counted(self):
        header, rows, d = parsed('name,notes\nA,"x,y"\n\nB,"two\nlines"\nC,z\n')
        self.assertEqual((header, d), (["name", "notes"], ","))
        self.assertEqual(rows, [(2, ["A", "x,y"]), (4, ["B", "two\nlines"]), (6, ["C", "z"])])   # the blank line is dropped, B starts on line 4 and ends on 5

    def test_empty_header_only_and_huge_files(self):
        for text in ("", "\n\n", "name\tx\n"):
            with self.assertRaises(ti.FileProblem):
                parsed(text)
        with self.assertRaises(ti.FileProblem):
            parsed("name\n" + "\n".join(f"r{i}" for i in range(ti.MAX_ROWS + 1)))
        self.assertEqual(len(parsed("name\n" + "\n".join(f"r{i}" for i in range(ti.MAX_ROWS)))[1]), ti.MAX_ROWS)


class TestMapping(Base):
    def test_targets_are_id_name_fillable_fields_and_notes(self):
        keys = [k for k, _ in ti.targets(self.store, "strain")]
        self.assertEqual(keys, ["id", "name", "genotype", "tags", "ok", "plasmid", "notes"])      # no hidden, no calculated field

    def test_headers_are_matched_by_label_or_key_ignoring_case_and_punctuation(self):
        t = ti.targets(self.store, "strain")
        self.assertEqual(ti.auto_map(["ID", "Name", "GENOTYPE", "tags", "OK", "Plasmid", "Owner", "Created", "Archived"], t),
                         ["id", "name", "genotype", "tags", "ok", "plasmid", None, None, None])
        self.assertEqual(ti.auto_map(["name", "Name"], t), ["name", None])                       # a key goes to the first column naming it


class TestPlan(Base):
    def plan(self, text, mapping=None):
        header, rows, _ = ti.parse(text)
        mapping = mapping or ti.auto_map(header, ti.targets(self.store, "strain"))
        return ti.plan(self.store, ROOT, "strain", header, rows, mapping)

    def test_rows_without_an_id_are_created(self):
        p = self.plan("Name\tGenotype\tTags\tOK\nnew1\tg1\ta, b\tyes\nnew2\t\t\tno\n")
        self.assertEqual((p["created"], p["updated"], p["errors"]), (2, 0, 0))
        e = p["entries"][0]
        self.assertEqual((e["action"], e["name"], e["fields"]), ("create", "new1", {"genotype": "g1", "tags": ["a", "b"], "ok": True}))

    def test_rows_with_an_id_update_and_blank_cells_leave_fields_alone(self):
        p = self.plan(f"ID\tName\tGenotype\n{self.a[0]}\t\tchanged\n{self.a[1]}\t\t\n")
        e1, e2 = p["entries"]
        self.assertEqual((e1["action"], e1["name"], e1["fields"]["genotype"]), ("update", "a0", "changed"))   # blank name: unchanged
        self.assertEqual(e1["changes"], [("Genotype", "g", "changed")])
        self.assertEqual((e2["action"], e2["fields"]["genotype"]), ("unchanged", "g"))                        # a blank cell does not wipe it

    def test_a_changed_name_is_listed_first(self):
        e = self.plan(f"ID\tName\n{self.a[0]}\trenamed\n")["entries"][0]
        self.assertEqual(e["changes"], [("Name", "a0", "renamed")])

    def test_id_problems(self):
        p = self.plan(f"ID\tName\nST-999\tx\n{self.p}\ty\n{self.a[0]}\tz\n{self.a[0]}\tz2\n")
        msgs = [e["errors"].get("id") for e in p["entries"]]
        self.assertEqual(msgs[0], "ST-999 does not exist")
        self.assertEqual(msgs[1], f"{self.p} is not a strain")
        self.assertIsNone(msgs[2])
        self.assertEqual(msgs[3], f"{self.a[0]} already appears on line 4")
        self.assertEqual(p["errors"], 3)

    def test_a_new_record_needs_a_name_and_values_are_checked_like_the_form(self):
        p = self.plan("Name\tTags\tOK\tPlasmid\n\ta\tyes\t\nx\tzzz\tmaybe\tPL-999\n")
        self.assertEqual(p["entries"][0]["errors"], {"name": "required"})
        errs = p["entries"][1]["errors"]
        self.assertEqual(errs["ok"], "use yes or no")
        self.assertEqual(errs["plasmid"], "PL-999 does not exist")
        self.assertEqual(errs["tags"], "not one of the allowed options (a, b, c)")                    # says what is allowed

    def test_options_match_case_insensitively_and_a_whole_cell_option_may_hold_a_comma(self):
        self.store.schemas["strain"]["fields"][2]["options"] = ["a, b", "c"]
        p = self.plan("Name\tTags\nx\tA, B\ny\tC\n")
        self.assertEqual([e["fields"].get("tags") for e in p["entries"]], [["a, b"], ["c"]])

    def test_references_by_id(self):
        e = self.plan(f"Name\tPlasmid\nx\t{self.p}\n")["entries"][0]
        self.assertEqual(e["fields"]["plasmid"], [self.p])

    def test_required_fields_are_checked_on_new_records(self):
        self.store.schemas["strain"]["fields"][1]["required"] = True
        p = self.plan(f"Name\tGenotype\nx\t\nID\tName\n")
        self.assertEqual(p["entries"][0]["errors"], {"genotype": "required"})

    def test_same_names_are_warned_about_not_refused(self):
        p = self.plan("Name\nA0\nnew\nNEW\n")
        self.assertEqual([e["warnings"] for e in p["entries"]], [[f"same name as {self.a[0]}"], [], ["same name as line 3"]])
        self.assertEqual(p["errors"], 0)

    def test_the_column_choice_is_checked(self):
        header, rows, _ = ti.parse("a\tb\nx\ty\n")
        for mapping in ([None, None], ["genotype", "genotype"], ["name"], ["name", "nope"]):
            with self.assertRaises(ti.FileProblem):
                ti.plan(self.store, ROOT, "strain", header, rows, mapping)


class TestImportTable(Base):
    def run_import(self, text, actor=ROOT, **kw):
        header, rows, _ = ti.parse(text)
        return self.store.import_table(actor, "strain", header, rows, ti.auto_map(header, ti.targets(self.store, "strain")), **kw)

    def names(self, name):
        return [r for r in self.store.records.values() if r["name"] == name]

    def test_anyone_who_may_add_records_may_import_them(self):
        r = self.run_import("Name\nx\n", actor=ALICE)
        self.assertEqual((r["created"], len(self.names("x"))), (1, 1))
        self.assertEqual(self.names("x")[0]["created_by"], ALICE["uid"])

    def test_a_type_only_administrators_add_to_refuses_the_others(self):
        self.store.save_schema("strain", dict(self.store.schemas["strain"], permissions={"create": "admin"}), ROOT)
        with self.assertRaises(Forbidden):
            self.run_import("Name\nx\n", actor=ALICE)
        self.assertEqual(self.names("x"), [])
        self.assertEqual(self.run_import("Name\nx\n")["created"], 1)

    def test_a_personal_import_needs_personal_collections(self):
        with self.assertRaises(Forbidden):
            self.run_import("Name\nx\n", actor=ALICE, personal=True)

    def test_an_update_of_someone_elses_record_is_a_row_error(self):
        rid = self.store.create("strain", ROOT, "rootsbug", {})
        r = self.run_import(f"ID\tGenotype\n{rid}\tg\n", actor=ALICE)
        self.assertEqual((r["errors"], r["sha"]), (1, None))
        self.assertIn("you may not change", r["entries"][0]["errors"]["id"])
        self.assertEqual(self.run_import(f"ID\tGenotype\n{rid}\tg\n")["updated"], 1)

    def test_dry_run_writes_nothing(self):
        before = self.commits()
        r = self.run_import("Name\nx\n", dry_run=True)
        self.assertEqual((r["created"], r["sha"]), (1, None))
        self.assertEqual(self.commits(), before)

    def test_one_bad_row_stops_everything(self):
        before = self.commits()
        r = self.run_import("Name\tOK\nfine\tyes\nbad\tmaybe\n")
        self.assertEqual((r["errors"], r["sha"], r["written"]), (1, None, []))
        self.assertEqual(self.commits(), before)
        self.assertFalse([x for x in self.store.records.values() if x["name"] == "fine"])

    def test_creates_and_updates_in_one_commit_owned_by_the_importer(self):
        before = self.commits()
        r = self.run_import(f"ID\tName\tGenotype\n\tnew one\tg9\n{self.a[0]}\t\tupdated\n{self.a[1]}\t\t\n")
        self.assertEqual((r["created"], r["updated"], r["unchanged"], r["errors"]), (1, 1, 1, 0))
        self.assertEqual(self.commits(), before + 1)
        self.assertEqual(self.head_message().splitlines()[0], "bulk: import 1 new, 1 updated (2 strain records)")
        new = self.store.records[r["written"][0]]
        self.assertEqual((new["name"], new["created_by"], new["fields"]["genotype"]), ("new one", ROOT["uid"], "g9"))
        self.assertEqual(self.store.records[self.a[0]]["fields"]["genotype"], "updated")
        self.assertEqual(self.store.records[self.a[0]]["created_by"], ALICE["uid"])               # the owner of an updated record stays
        self.assertIn(self.a[0], self.store.git("log", "-1", "--format=%B"))                 # the body lists the records

    def test_new_records_get_consecutive_ids(self):
        r = self.run_import("Name\nn1\nn2\nn3\n")
        nums = [int(i.split("-")[1]) for i in r["written"]]
        self.assertEqual(nums, [nums[0], nums[0] + 1, nums[0] + 2])
        self.assertEqual(len(set(r["written"])), 3)

    def test_the_files_survive_a_reload_and_the_import_can_be_undone(self):
        r = self.run_import("Name\tGenotype\nn1\tg1\n")
        rid = r["written"][0]
        self.store.reload()
        self.assertEqual(self.store.records[rid]["fields"]["genotype"], "g1")
        self.store.undo(ROOT, r["sha"])
        self.assertNotIn(rid, self.store.records)

    def test_a_file_that_changes_nothing_writes_nothing(self):
        before = self.commits()
        r = self.run_import(f"ID\tGenotype\n{self.a[0]}\tg\n")
        self.assertEqual((r["unchanged"], r["sha"]), (1, None))
        self.assertEqual(self.commits(), before)

    def test_the_import_shows_in_recent_activity(self):
        self.run_import("Name\nn1\n")
        self.assertIn("import 1 new, 0 updated", [x["name"] for x in self.store.recent_activity(ROOT)][0])


if __name__ == "__main__":
    unittest.main()
