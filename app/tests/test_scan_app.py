"""Barcode scanning (app/shelfblazer_app/scan.py): the code logic, then the pages. A type with "scan_fields" is found by the codes it carries (the browser sends the decoded text to /scan,
a USB reader types it into the home search), and an unknown code leads to a new record with the code filled in."""
import unittest

from shelfblazer_app import scan
from test_app import Base


class Keys(unittest.TestCase):
    def test_gs1_with_brackets_keeps_the_product_not_the_lot(self):
        self.assertEqual(scan.keys("(01)04038917133913(17)250430(10)220590103"), ["04038917133913"])    # Sarstedt
        self.assertEqual(scan.keys("(01)04053252315893(10)R0EB68125(240)MSGVS2210(422)372"), ["04053252315893", "MSGVS2210"])  # Millipore
        self.assertEqual(scan.keys("(91)R1057(10)211479(17)231124"), ["R1057"])                          # Zymo: the catalog number is element 91

    def test_gs1_without_brackets_gives_the_gtin(self):
        self.assertEqual(scan.keys("0104038917133913" + "17250430" + "10220590103"), ["04038917133913"])
        self.assertEqual(scan.keys("0104022495251879\x1d10X"), ["04022495251879"])

    def test_plain_codes_are_themselves(self):
        for code in ("S1111-6701", "0036000055115", "MSGVS2210", "BT1000.96"):
            self.assertEqual(scan.keys(code), [code])
        self.assertEqual(scan.keys("  120637 \n"), ["120637"])
        self.assertEqual(scan.keys(""), [])

    def test_a_plain_code_a_decoder_shows_in_gs1_brackets_is_read_back(self):
        self.assertEqual(scan.keys("(37)17"), ["3717"])                    # Integra part number 3717
        self.assertEqual(scan.keys("(99)003897"), ["99003897"])            # Integra lot-like code, as printed

    def test_a_long_digit_string_starting_01_is_not_taken_for_a_gtin_without_a_next_element(self):
        self.assertEqual(scan.keys("0123456789012345678901"), ["0123456789012345678901"])


class Matching(unittest.TestCase):
    def test_norm_ignores_case_separators_and_leading_zeros_of_long_numbers(self):
        self.assertEqual(scan.norm("0030 073.495"), scan.norm("0030073495"))
        self.assertEqual(scan.norm("S1111-6701"), scan.norm("s11116701"))
        self.assertEqual(scan.norm("036000055115"), scan.norm("0036000055115"))      # UPC-A = EAN-13 with a 0
        self.assertEqual(scan.norm("00036000055115"), scan.norm("036000055115"))     # = GTIN-14
        self.assertNotEqual(scan.norm("123"), scan.norm("0123"))                     # short numbers keep their zeros

    def test_wanted_drops_short_codes(self):
        self.assertEqual(scan.wanted(["1", "120637"]), {"120637"})

    def test_prefill_lists_the_product_keys_once(self):
        self.assertEqual(scan.prefill(["(01)04038917133913(17)250430(10)1", "4038917133913", "S1111-6701", "1"]), "04038917133913, S1111-6701")


class FakeStore:
    schemas = {"item": {"scan_fields": ["barcodes", "ref"]}, "plain": {}, "bad": {"scan_fields": "barcodes"}}

    def __init__(self, recs):
        self.records = {r["id"]: r for r in recs}

    def visible_records(self, viewer):
        return self.records


def rec(rid, type_="item", n=1, archived=False, **fields):
    return dict(id=rid, type=type_, legacy_id=n, archived=archived, fields=fields)


class Find(unittest.TestCase):
    def test_finds_by_any_code_in_any_scan_field_and_by_any_form_of_the_gtin(self):
        s = FakeStore([rec("CO-1", barcodes="4038917133913, S1111-6701"), rec("CO-2", n=2, ref="MSGVS2210"), rec("CO-3", n=3, barcodes="999999999")])
        ids = lambda codes: [r["id"] for r in scan.find(s, None, codes)]
        self.assertEqual(ids(["(01)04038917133913(17)250430(10)220590103"]), ["CO-1"])
        self.assertEqual(ids(["s1111 6701"]), ["CO-1"])
        self.assertEqual(ids(["(01)04053252315893(10)R0EB68125(240)MSGVS2210(422)372"]), ["CO-2"])
        self.assertEqual(ids(["nothing here"]), [])
        self.assertEqual(ids(["1", ""]), [])
        self.assertEqual(sorted(ids(["4038917133913", "999999999"])), ["CO-1", "CO-3"])

    def test_other_fields_types_without_the_setting_and_bad_settings_are_ignored(self):
        s = FakeStore([rec("CO-1", barcodes="S1111-6701"), rec("PL-1", "plain", barcodes="S1111-6701"), rec("BD-1", "bad", barcodes="S1111-6701"),
                       rec("CO-2", n=2, name="S1111-6701")])
        self.assertEqual([r["id"] for r in scan.find(s, None, ["S1111-6701"])], ["CO-1"])

    def test_active_records_come_before_archived_ones(self):
        s = FakeStore([rec("CO-1", archived=True, ref="ABCD1234"), rec("CO-2", n=2, ref="ABCD1234")])
        self.assertEqual([r["id"] for r in scan.find(s, None, ["ABCD1234"])], ["CO-2", "CO-1"])

    def test_a_value_that_is_not_text_is_skipped(self):
        s = FakeStore([rec("CO-1", barcodes=["a", "b"], ref=None)])
        self.assertEqual(scan.find(s, None, ["abcd"]), [])



SARSTEDT = "(01)04038917133913(17)250430(10)220590103"           # GS1: GTIN, expiry, lot


class Helpers(unittest.TestCase):
    def test_prefilled_puts_the_code_in_the_first_scan_field_only_where_the_type_has_them(self):
        self.assertEqual(scan.prefilled({"scan_fields": ["barcodes", "ref"]}, "123456"), {"barcodes": "123456"})
        self.assertEqual(scan.prefilled({"scan_fields": ["barcodes"]}, ""), {})
        self.assertEqual(scan.prefilled({}, "123456"), {})
        self.assertEqual(scan.prefilled({"scan_fields": "barcodes"}, "123456"), {})
        self.assertEqual(len(scan.prefilled({"scan_fields": ["b"]}, "x" * 900)["b"]), 500)

    def test_active_leaves_out_archived_records(self):
        s = FakeStore([rec("CO-1", archived=True, ref="ABCD1234"), rec("CO-2", n=2, ref="ABCD1234")])
        self.assertEqual([r["id"] for r in scan.active(s, None, ["ABCD1234"])], ["CO-2"])


def schema(**extra):
    return {"name": "Consumable", "prefix": "CS", "list_columns": ["id", "name", "supplier"], "scan_fields": ["barcodes", "catalog_no"], "fields": [
        {"key": "name", "label": "Name", "type": "text", "required": True},
        {"key": "supplier", "label": "Supplier", "type": "text"},
        {"key": "catalog_no", "label": "Catalog no.", "type": "text"},
        {"key": "barcodes", "label": "Barcodes", "type": "text"}], **extra}


class Scan(Base):
    def setUp(self):
        super().setUp()
        self.root = self.users.get("root")
        self.store.save_schema("consumable", schema(), self.root, create=True)
        self.alice = self.users.get("alice")
        self.tubes = self.store.create("consumable", self.alice, "PCR tubes", {"supplier": "Sarstedt", "barcodes": "4038917133913"})
        self.tips = self.store.create("consumable", self.alice, "TipOne tips", {"supplier": "Starlab", "catalog_no": "S1111-6701"})
        self.c = self.client("alice")

    def scan(self, *codes):
        return self.c.get("/scan", params=[("c", x) for x in codes])

    def test_one_match_opens_the_record_whatever_the_form_of_the_code(self):
        for code in (SARSTEDT, "4038917133913", "04038917133913", "0104038917133913" + "17250430"):
            r = self.scan(code)
            self.assertEqual((r.status_code, r.headers["location"]), (303, f"/r/{self.tubes}"), code)
        self.assertEqual(self.scan("s1111 6701").headers["location"], f"/r/{self.tips}")

    def test_any_of_several_codes_from_one_label_is_enough(self):
        r = self.scan("(91)ignored", "S1111-6701", "1")
        self.assertEqual(r.headers["location"], f"/r/{self.tips}")

    def test_no_match_offers_to_add_the_product_with_the_code_filled_in(self):
        page = self.scan("(01)04022495251879(17)301201(10)25N03C8").text
        self.assertIn("No record carries this code yet", page)
        self.assertIn('href="/t/consumable/new?code=04022495251879"', page)
        self.assertIn("Add as consumable", page)
        self.assertIn('href="/?scan=1"', page)

    def test_the_new_form_has_the_code_in_the_first_scan_field_and_saving_makes_the_next_scan_find_it(self):
        form = self.c.get("/t/consumable/new", params={"code": "04022495251879, B-9"}).text
        self.assertIn('name="f_barcodes" value="04022495251879, B-9"', form)
        self.assertNotIn('name="f_catalog_no" value="04022495251879', form)
        r = self.post(self.c, "/t/consumable/new", {"name": "Tape", "f_barcodes": "04022495251879, B-9"})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.scan("(01)04022495251879(17)301201(10)25N03C8").headers["location"], r.headers["location"])

    def test_a_code_in_the_url_is_ignored_by_types_without_scan_fields(self):
        self.assertNotIn("04022495251879", self.c.get("/t/strain/new", params={"code": "04022495251879"}).text)

    def test_several_matches_are_listed_and_archived_ones_marked(self):
        other = self.store.create("consumable", self.alice, "Tubes, other pack", {"barcodes": "4038917133913"})
        page = self.scan("4038917133913").text
        self.assertIn("2 records carry this code", page)
        self.assertIn(f'href="/r/{self.tubes}"', page)
        self.assertIn(f'href="/r/{other}"', page)
        self.post(self.c, f"/r/{other}/archive")
        self.assertEqual(self.scan("4038917133913").headers["location"], f"/r/{self.tubes}")      # an archived duplicate does not hide the one in use
        self.post(self.c, f"/r/{self.tubes}/archive")
        page = self.scan("4038917133913").text                                                      # only archived ones left: listed, not opened
        self.assertIn("2 records carry this code", page)
        self.assertIn('class="badge">archived<', page)

    def test_nothing_read_and_too_short_codes_match_nothing(self):
        self.assertEqual(self.scan().status_code, 200)
        self.assertIn("Nothing was read", self.scan().text)
        self.assertIn("No record carries", self.scan("1").text)

    def test_codes_are_shown_escaped(self):
        page = self.scan("<script>alert(1)</script>").text
        self.assertNotIn("<script>alert(1)</script>", page)

    def test_sign_in_is_required(self):
        r = self.client().get("/scan", params={"c": "S1111-6701"})
        self.assertEqual(r.status_code, 303)
        self.assertIn("/login", r.headers["location"])

    def test_a_hidden_type_is_neither_matched_nor_offered(self):
        self.store.save_schema("consumable", schema(permissions={"read": "admin"}), self.root)
        page = self.scan("S1111-6701").text
        self.assertIn("No record carries", page)
        self.assertNotIn("Add as", page)
        self.assertIn("No record type is set up for scanning", page)
        self.assertEqual(self.client("root").get("/scan", params={"c": "S1111-6701"}).headers["location"], f"/r/{self.tips}")

    def test_only_people_who_may_create_are_offered_to_add(self):
        self.store.save_schema("consumable", schema(permissions={"create": "admin"}), self.root)
        page = self.scan("0000-NEW-CODE").text
        self.assertIn("No record carries", page)
        self.assertNotIn("Add as", page)

    def test_the_home_search_opens_a_record_on_enter_but_not_while_typing(self):
        r = self.c.get("/", params={"q": "04038917133913"})
        self.assertEqual((r.status_code, r.headers["location"]), (303, f"/r/{self.tubes}"))
        live = self.c.get("/", params={"q": "04038917133913"}, headers={"x-live": "1"})
        self.assertEqual(live.status_code, 200)
        self.assertEqual(self.c.get("/", params={"q": "PCR"}).status_code, 200)               # an ordinary word is an ordinary search

    def test_the_scan_button_is_on_the_home_page_only_when_a_type_is_set_up_for_it(self):
        self.assertIn('id="scan-open"', self.c.get("/").text)
        self.store.save_schema("consumable", schema(permissions={"read": "admin"}), self.root)
        self.assertNotIn('id="scan-open"', self.c.get("/").text)
        self.assertIn('id="scan-open"', self.client("root").get("/").text)

    def test_the_header_has_a_scan_button_on_every_page_when_a_type_is_set_up_for_it(self):
        self.assertIn('class="headscan" href="/?scan=1"', self.c.get("/t/strain").text)
        self.store.save_schema("consumable", schema(permissions={"read": "admin"}), self.root)
        self.assertNotIn('class="headscan"', self.c.get("/t/strain").text)

    def test_the_scan_fields_of_a_form_have_a_scan_button_that_fills_them(self):
        form = self.c.get("/t/consumable/new").text
        self.assertIn('data-scan-into="f_barcodes"', form)
        self.assertIn('data-scan-into="f_catalog_no"', form)
        self.assertNotIn('data-scan-into="f_supplier"', form)
        self.assertIn('id="scan"', form)
        self.assertNotIn("data-scan-into", self.c.get("/t/strain/new").text)

    def test_the_page_may_run_webassembly_for_the_decoder_and_nothing_else_new(self):
        csp = self.c.get("/").headers["content-security-policy"]
        self.assertIn("script-src 'self' 'wasm-unsafe-eval';", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("'unsafe-eval'", csp.replace("'wasm-unsafe-eval'", ""))

    def test_the_setting_must_list_text_fields_of_the_type(self):
        for bad in (["nope"], "barcodes", [], ["supplier", "nope"]):
            s = schema()
            s["scan_fields"] = bad
            with self.assertRaises(Exception, msg=repr(bad)):
                self.store.save_schema("consumable2", s, self.root, create=True)
        s = schema()
        s["fields"].append({"key": "weight", "label": "Weight", "type": "number"})
        s["scan_fields"] = ["weight"]
        with self.assertRaises(Exception):
            self.store.save_schema("consumable3", s, self.root, create=True)


if __name__ == "__main__":
    unittest.main()
