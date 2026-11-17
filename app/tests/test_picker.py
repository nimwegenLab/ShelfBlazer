"""Reference picker: the JSON lookup and the markup it enhances."""
import os, unittest

from test_app import Base

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shelfblazer_app", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


class TestRecordsApi(Base):
    def setUp(self):
        super().setUp()
        self.p1 = self.create("alice", "plasmid", name="pUA66")
        self.p2 = self.create("alice", "plasmid", name="pZA21 kanamycin")
        self.p3 = self.create("bob", "plasmid", name="pUA139")
        self.s1 = self.create("alice", name="MG1655 ybaL wt")
        self.c = self.client("alice")

    def ids(self, url):
        r = self.c.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertIn("application/json", r.headers["content-type"])
        return [x["id"] for x in r.json()["results"]]

    def test_login_is_required_and_answers_json_not_a_redirect(self):
        r = self.client().get("/api/records?q=pua")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json(), {"error": "login required"})

    def test_search_by_text_filtered_by_type(self):
        self.assertEqual(set(self.ids("/api/records?q=pUA&type=plasmid")), {self.p1, self.p3})
        self.assertEqual(self.ids("/api/records?q=pUA&type=strain"), [])
        self.assertEqual(set(self.ids("/api/records?q=ybaL")), {self.s1})                  # no type: everything
        self.assertEqual(set(self.ids("/api/records?q=a&type=plasmid,strain")) >= {self.p1}, True)   # several types

    def test_typo_tolerant_and_id_search(self):
        self.assertIn(self.p2, self.ids("/api/records?q=kanamycn&type=plasmid"))
        self.assertEqual(self.ids(f"/api/records?q={self.p3}&type=plasmid")[0], self.p3)

    def test_lookup_by_ids_keeps_order_and_skips_unknown(self):
        self.assertEqual(self.ids(f"/api/records?ids={self.p3},PL-999,{self.p1}"), [self.p3, self.p1])
        self.assertEqual(self.ids(f"/api/records?ids={self.s1}&type=plasmid"), [])         # wrong type is skipped

    def test_item_shape(self):
        item = self.c.get(f"/api/records?ids={self.p1}").json()["results"][0]
        self.assertEqual(set(item), {"id", "name", "type", "type_name", "hue", "archived"})
        self.assertEqual((item["type"], item["type_name"], item["name"], item["archived"]), ("plasmid", "Plasmid", "pUA66", False))
        self.assertEqual(item["hue"], 2)

    def test_archived_hidden_unless_asked(self):
        self.post(self.c, f"/r/{self.p1}/archive")
        self.assertNotIn(self.p1, self.ids("/api/records?q=pUA66&type=plasmid"))
        self.assertIn(self.p1, self.ids("/api/records?q=pUA66&type=plasmid&archived=1"))

    def test_empty_query_lists_the_newest_first_and_limit_is_capped(self):
        self.assertEqual(self.ids("/api/records?type=plasmid&limit=2"), [self.p3, self.p2])
        for i in range(25):
            self.store.create("plasmid", self.users.get("alice"), f"x{i}", {})
        self.assertEqual(len(self.ids("/api/records?type=plasmid&limit=1000")), 20)
        self.assertEqual(len(self.ids("/api/records?type=plasmid&limit=0")), 1)

    def test_unknown_type_and_hostile_input(self):
        self.assertEqual(self.ids("/api/records?q=pUA&type=ghost"), [])
        self.assertEqual(self.ids("/api/records?q=%3Cscript%3E&type=plasmid"), [])
        self.assertEqual(self.c.get("/api/records?limit=abc").status_code, 422)

    def test_names_are_returned_as_data_not_markup(self):
        rid = self.create("alice", "plasmid", name="<img src=x onerror=alert(1)>")
        item = self.c.get(f"/api/records?ids={rid}").json()["results"][0]
        self.assertEqual(item["name"], "<img src=x onerror=alert(1)>")                     # JSON string; the script inserts it as text


class TestPickerMarkup(Base):
    def test_reference_fields_carry_the_picker_hooks(self):
        page = self.client("alice").get("/t/strain/new").text
        self.assertIn('data-ref-to="plasmid"', page)
        self.assertIn('data-multiple="1"', page)
        self.assertIn('<script src="/static/picker.js" defer>', page)
        self.assertIn('name="f_plasmid"', page)                                           # the plain input is still there (no JavaScript needed)

    def test_the_form_still_accepts_what_the_picker_writes(self):
        a = self.create("alice", "plasmid", name="a")
        b = self.create("alice", "plasmid", name="b")
        c = self.client("alice")
        r = self.post(c, "/t/strain/new", {"name": "s", "f_plasmid": f"{a}, {b}"})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.store.records["ST-1"]["fields"]["plasmid"], [a, b])
        bad = self.post(c, "/t/strain/new", {"name": "s", "f_plasmid": "PL-999"})
        self.assertEqual(bad.status_code, 422)                                            # the server still validates

    def test_edit_form_prefills_ids_for_the_picker(self):
        a = self.create("alice", "plasmid", name="a")
        s = self.create("alice", name="s", plasmid=a)
        self.assertIn(f'value="{a}"', self.client("alice").get(f"/r/{s}/edit").text)

    def test_script_is_safe_and_complete(self):
        js = read("picker.js")
        for needle in ('input[data-ref-to]', "/api/records", "role", "combobox", "ArrowDown", "ArrowUp", "Escape", "Backspace",
                       "textContent", "aria-label", "mousedown", "listbox"):
            self.assertIn(needle, js)
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "document.write", "new Function"):
            self.assertNotIn(banned, js)

    def test_picker_styles_exist(self):
        css = read("style.css")
        for sel in (".pick{", ".pick-chip{", ".pick-list{", '.pick-item[aria-selected="true"]'):
            self.assertIn(sel, css)


if __name__ == "__main__":
    unittest.main()
