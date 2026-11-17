"""Per-type permissions through the web app: hidden types, administrators-only editing or adding, open editing."""
import json, re, unittest

from test_app import Base


def vault(perm):
    return {"name": "Vault", "prefix": "VA", "list_columns": ["id", "name", "created_by"], "permissions": perm, "fields": [
        {"key": "name", "label": "Name", "type": "text", "required": True}, {"key": "code", "label": "Code", "type": "text"},
        {"key": "files", "label": "Files", "type": "file", "multiple": True}]}


class Perm(Base):
    def setUp(self):
        super().setUp()
        self.root = self.users.get("root")
        self.root_c = self.client("root")
        self.a, self.b = self.client("alice"), self.client("bob")

    def add_type(self, slug, perm, prefix=None):
        s = vault(perm)
        s["name"] = slug.title()
        s["prefix"] = prefix or {"vault": "VA", "locked": "LK", "staff": "SF", "wiki": "WK"}[slug]
        self.store.save_schema(slug, s, self.root, create=True)

    def make(self, who, slug, name="rec", **f):
        r = self.post(self.client(who), f"/t/{slug}/new", {"name": name, **{f"f_{k}": v for k, v in f.items()}})
        self.assertEqual(r.status_code, 303, r.text[:200])
        return r.headers["location"].split("/")[-1]

    def api(self, c, **params):
        return [d["id"] for d in c.get("/api/records", params=params).json()["results"]]


class TestHiddenType(Perm):
    def setUp(self):
        super().setUp()
        self.add_type("vault", {"read": "admin"})
        self.v = self.make("root", "vault", "top secret thing", code="xyzzy-42")
        self.o = self.make("alice", "plasmid", "open one")

    def test_other_users_do_not_see_the_type_anywhere(self):
        for c in (self.a, self.b):
            self.assertNotIn("/t/vault", c.get("/").text)
            self.assertNotIn("Vault", c.get("/").text)
            self.assertEqual(c.get("/t/vault").status_code, 404)
            self.assertEqual(c.get("/t/vault/new").status_code, 404)
            self.assertEqual(c.get(f"/r/{self.v}").status_code, 404)
            self.assertEqual(c.get(f"/r/{self.v}/edit").status_code, 404)
        self.assertIn("/t/vault", self.root_c.get("/").text)
        self.assertEqual(self.root_c.get(f"/r/{self.v}").status_code, 200)
        self.assertIn("xyzzy-42", self.root_c.get(f"/r/{self.v}").text)

    def test_the_record_looks_exactly_like_a_missing_one(self):
        gone = self.a.get("/r/VA-999")
        hidden = self.a.get(f"/r/{self.v}")
        self.assertEqual(gone.status_code, hidden.status_code)
        self.assertNotIn("top secret", hidden.text)

    def test_search_and_exact_id_do_not_find_it(self):
        for q in ("xyzzy", "top secret", self.v, self.v.lower()):
            r = self.a.get("/", params={"q": q})
            self.assertEqual(r.status_code, 200, q)
            self.assertNotIn("top secret thing", r.text)
        self.assertIn("top secret thing", self.root_c.get("/", params={"q": "xyzzy"}).text)
        self.assertEqual(self.root_c.get("/", params={"q": self.v}).status_code, 303)

    def test_picker_api_does_not_list_it(self):
        self.assertNotIn(self.v, self.api(self.a, q="secret"))
        self.assertNotIn(self.v, self.api(self.a))
        self.assertNotIn(self.v, self.api(self.a, ids=self.v))
        self.assertEqual(self.api(self.a, type="vault"), [])
        self.assertIn(self.v, self.api(self.root_c, q="secret"))
        self.assertIn(self.v, self.api(self.root_c, ids=self.v))
        self.assertIn(self.o, self.api(self.a, ids=f"{self.v},{self.o}"))

    def test_writes_are_refused_as_if_missing(self):
        v = self.store.records[self.v]["_version"]
        for path, data in ((f"/r/{self.v}/edit", {"name": "x", "version": v}), (f"/r/{self.v}/archive", {}),
                           ("/t/vault/new", {"name": "forged"}), (f"/r/{self.v}/files/delete", {"name": "a.txt"})):
            self.assertEqual(self.post(self.a, path, data).status_code, 404, path)
        self.assertEqual(self.store.records[self.v]["name"], "top secret thing")
        self.assertEqual(len([r for r in self.store.records.values() if r["type"] == "vault"]), 1)

    def test_files_of_hidden_records_cannot_be_downloaded_or_uploaded(self):
        self.store.add_attachment(self.v, self.root, "plan.txt", b"classified")
        self.assertEqual(self.root_c.get(f"/files/{self.v}/plan.txt").status_code, 200)
        self.assertEqual(self.a.get(f"/files/{self.v}/plan.txt").status_code, 404)
        r = self.a.post(f"/r/{self.v}/files", files={"files": ("x.txt", b"hi")}, data={"csrf_token": self.token(self.a)})
        self.assertEqual(r.status_code, 404)

    def test_files_left_behind_by_a_purged_hidden_record_stay_hidden(self):
        self.store.add_attachment(self.v, self.root, "plan.txt", b"classified")
        v2 = self.make("root", "vault", "second")
        self.purge(v2)
        import os
        os.makedirs(os.path.join(self.repo, "data", "files", v2), exist_ok=True)
        with open(os.path.join(self.repo, "data", "files", v2, "old.txt"), "w") as fh:
            fh.write("old")
        self.assertEqual(self.a.get(f"/files/{v2}/old.txt").status_code, 404)
        self.assertEqual(self.root_c.get(f"/files/{v2}/old.txt").status_code, 200)
        os.makedirs(os.path.join(self.repo, "data", "files", "ZZ-1"))
        with open(os.path.join(self.repo, "data", "files", "ZZ-1", "x.txt"), "w") as fh:
            fh.write("x")
        self.assertEqual(self.a.get("/files/ZZ-1/x.txt").status_code, 404)           # unknown prefix: administrators only

    def test_bulk_edit_of_the_hidden_type_is_refused(self):
        self.assertEqual(self.post(self.a, "/t/vault/bulk", {"ids": [self.v]}).status_code, 404)
        self.assertEqual(self.post(self.a, "/t/vault/bulk/apply", {"ids": [self.v], "action": "archive"}).status_code, 404)
        self.assertFalse(self.store.records[self.v]["archived"])
        r = self.post(self.a, "/t/plasmid/bulk", {"ids": [self.o, self.v]})
        self.assertNotIn(self.v, r.text)

    def test_links_and_backlinks_do_not_leak_names(self):
        s = self.make("alice", "strain", "my strain", plasmid=self.o)
        self.store.save_schema("plasmid", {**self.store.schemas["plasmid"], "permissions": {"read": "admin"}}, self.root)
        self.assertEqual(self.a.get(f"/r/{self.o}").status_code, 404)
        page = self.a.get(f"/r/{s}").text
        self.assertNotIn("open one", page)                                  # the name of the plasmid it links to
        self.assertNotIn(f'href="/r/{self.o}"', page)
        self.assertIn("open one", self.root_c.get(f"/r/{s}").text)
        back = self.root_c.get(f"/r/{self.o}").text
        self.assertIn("my strain", back)

    def test_backlinks_to_a_visible_record_from_a_hidden_type_are_not_listed(self):
        self.store.save_schema("strain", {**self.store.schemas["strain"], "permissions": {"read": "admin"}}, self.root)
        s = self.make("root", "strain", "classified strain", plasmid=self.o)
        page = self.a.get(f"/r/{self.o}").text
        self.assertNotIn(s, page)
        self.assertNotIn("classified strain", page)
        self.assertIn(s, self.root_c.get(f"/r/{self.o}").text)

    def test_markdown_links_in_notes_do_not_expose_hidden_names(self):
        rid = self.make("alice", "plasmid", "with note")
        v = self.store.records[rid]["_version"]
        self.post(self.a, f"/r/{rid}/edit", {"name": "with note", "notes": f"see {self.v} please", "version": v})
        page = self.a.get(f"/r/{rid}").text
        self.assertNotIn("top secret", page)

    def test_home_counts_skip_the_hidden_type(self):
        page = self.a.get("/").text
        self.assertNotIn("Vault", page)
        self.assertIn("Plasmid", page)

    def test_a_formula_does_not_count_hidden_records(self):
        pl = self.store.schemas["plasmid"]
        self.store.save_schema("plasmid", {**pl, "fields": pl["fields"] + [{"key": "n", "label": "N", "type": "computed", "expr": "count(referenced_by('strain'))"}]}, self.root)
        self.store.save_schema("strain", {**self.store.schemas["strain"], "permissions": {"read": "admin"}}, self.root)
        self.make("root", "strain", "hidden strain", plasmid=self.o)
        self.assertRegex(self.root_c.get(f"/r/{self.o}").text, r"N <span[^>]*>calculated</span></th><td>1")
        self.assertRegex(self.a.get(f"/r/{self.o}").text, r"N <span[^>]*>calculated</span></th><td>0")


class TestAdminsOnlyEditing(Perm):
    def setUp(self):
        super().setUp()
        self.add_type("locked", {"edit": "admin"})
        self.r = self.make("alice", "locked", "alice made this")

    def test_nobody_but_admins_gets_edit_buttons_not_even_the_creator(self):
        for c in (self.a, self.b):
            page = c.get(f"/r/{self.r}").text
            self.assertNotIn(f'href="/r/{self.r}/edit"', page)
            self.assertNotIn("/archive", page)
            self.assertIn("Only administrators can edit this record", page)
        page = self.root_c.get(f"/r/{self.r}").text
        self.assertIn(f'href="/r/{self.r}/edit"', page)
        self.assertIn("/archive", page)

    def test_direct_requests_are_refused(self):
        v = self.store.records[self.r]["_version"]
        self.assertEqual(self.a.get(f"/r/{self.r}/edit").status_code, 403)
        for path, data in ((f"/r/{self.r}/edit", {"name": "x", "version": v}), (f"/r/{self.r}/archive", {})):
            self.assertEqual(self.post(self.a, path, data).status_code, 403, path)
        r = self.a.post(f"/r/{self.r}/files", files={"files": ("x.txt", b"hi")}, data={"csrf_token": self.token(self.a)})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.store.records[self.r]["name"], "alice made this")

    def test_admins_can_edit_and_everyone_can_still_create_and_read(self):
        self.assertEqual(self.a.get(f"/r/{self.r}").status_code, 200)
        self.assertEqual(self.b.get("/t/locked").status_code, 200)
        self.assertIn("+ New", self.b.get("/t/locked").text)
        v = self.store.records[self.r]["_version"]
        self.assertEqual(self.post(self.root_c, f"/r/{self.r}/edit", {"name": "renamed", "version": v}).status_code, 303)
        self.assertEqual(self.store.records[self.r]["name"], "renamed")

    def test_bulk_edit_skips_everything_for_ordinary_users(self):
        page = self.post(self.a, "/t/locked/bulk", {"ids": [self.r]}).text
        self.assertRegex(page, r"(?s)0 of 1|cannot|not allowed|skipped|0 record")
        r = self.post(self.a, "/t/locked/bulk/apply", {"ids": [self.r], "action": "archive"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.store.records[self.r]["archived"])


class TestAdminsOnlyAdding(Perm):
    def setUp(self):
        super().setUp()
        self.add_type("staff", {"create": "admin"})

    def test_no_new_button_and_no_form_for_ordinary_users(self):
        self.assertNotIn("/t/staff/new", self.a.get("/t/staff").text)
        self.assertEqual(self.a.get("/t/staff/new").status_code, 403)
        self.assertEqual(self.post(self.a, "/t/staff/new", {"name": "x"}).status_code, 403)
        self.assertEqual([r for r in self.store.records.values() if r["type"] == "staff"], [])
        self.assertIn("/t/staff/new", self.root_c.get("/t/staff").text)
        rid = self.make("root", "staff", "by admin")
        self.assertEqual(self.a.get(f"/r/{rid}").status_code, 200)

    def test_the_creator_can_still_edit_their_own_old_records_when_adding_is_closed(self):
        rid = self.make("root", "staff", "x")
        self.store.records[rid]["created_by"] = self.uid("alice")          # as if alice had made it before the rule was set
        self.assertEqual(self.a.get(f"/r/{rid}/edit").status_code, 200)


class TestOpenEditing(Perm):
    def setUp(self):
        super().setUp()
        self.add_type("wiki", {"edit": "all"})
        self.r = self.make("alice", "wiki", "shared page")

    def test_others_may_edit_and_archive(self):
        page = self.b.get(f"/r/{self.r}").text
        self.assertIn(f'href="/r/{self.r}/edit"', page)
        self.assertIn("/archive", page)
        v = self.store.records[self.r]["_version"]
        self.assertEqual(self.post(self.b, f"/r/{self.r}/edit", {"name": "bob changed it", "version": v}).status_code, 303)
        self.assertEqual(self.store.records[self.r]["name"], "bob changed it")
        self.assertEqual(self.store.records[self.r]["created_by"], self.uid("alice"))   # the creator does not change
        self.assertEqual(self.post(self.b, f"/r/{self.r}/archive").status_code, 303)
        self.assertEqual(self.b.get(f"/r/{self.r}").status_code, 404)                 # archived: only the creator and administrators see it now
        self.assertEqual(self.post(self.a, f"/r/{self.r}/archive").status_code, 303)   # the creator restores it
        self.assertFalse(self.store.records[self.r]["archived"])

    def test_the_commit_is_authored_by_the_person_who_edited(self):
        from test_app import git
        v = self.store.records[self.r]["_version"]
        self.post(self.b, f"/r/{self.r}/edit", {"name": "n", "version": v})
        self.assertIn(f"{self.uid('bob')}@shelfblazer.invalid", git(self.repo, "log", "-1", "--format=%ae"))


class TestOverview(Perm):
    def rules(self, slug):
        page = self.root_c.get("/admin/types").text
        row = re.search(rf'<tr data-href="/admin/types/{slug}".*?</tr>', page, re.S).group(0)
        return [(re.sub(r"<[^>]+>", "", c), "changed" in cls) for cls, c in re.findall(r'<td class="(rule[^"]*)">(.*?)</td>', row)]

    def test_every_type_shows_its_three_rules_and_marks_the_ones_that_differ_from_the_default(self):
        self.assertEqual(self.rules("strain"), [("everyone", False), ("everyone", False), ("creator, admins", False)])
        self.add_type("vault", {"read": "admin", "create": "admin", "edit": "admin"})
        self.add_type("wiki", {"edit": "all"})
        self.assertEqual(self.rules("vault"), [("admins", True), ("admins", True), ("admins", True)])
        self.assertEqual(self.rules("wiki"), [("everyone", False), ("everyone", False), ("everyone", True)])
        page = self.root_c.get("/admin/types").text
        self.assertIn("<th", page)
        self.assertRegex(page, r"<th[^>]*>Read</th><th[^>]*>Create</th><th[^>]*>Edit</th>")

    def test_the_overview_follows_a_change_and_is_admin_only(self):
        self.post(self.root_c, "/admin/types/plasmid/settings", {"name": "Plasmid", "color": "", "columns": ["created_by"], "perm_read": "admin"})
        self.assertEqual(self.rules("plasmid")[0], ("admins", True))
        self.assertIn(self.a.get("/admin/types").status_code, (303, 403))


class TestSettingsPage(Perm):
    def test_admin_sets_rules_and_the_page_shows_them(self):
        self.post(self.root_c, "/admin/types/plasmid/settings", {"name": "Plasmid", "color": "", "columns": ["created_by"], "perm_read": "", "perm_create": "admin", "perm_edit": "all"})
        self.assertEqual(self.store.schemas["plasmid"]["permissions"], {"create": "admin", "edit": "all"})
        page = self.root_c.get("/admin/types/plasmid").text
        self.assertRegex(page, r'name="perm_create">.*?<option value="admin" selected>')
        self.assertRegex(page, r'name="perm_edit">.*?<option value="all" selected>')
        self.assertRegex(self.root_c.get("/admin/types").text, r'(?s)Plasmid</span></td>.*?<td class="rule">everyone</td><td class="rule changed">admins</td><td class="rule changed">everyone</td>')
        self.assertEqual(json.loads(self.read(f"{self.repo}/config/types/plasmid.yaml"))["permissions"], {"create": "admin", "edit": "all"})
        self.post(self.root_c, "/admin/types/plasmid/settings", {"name": "Plasmid", "color": "", "columns": ["created_by"], "perm_read": "", "perm_create": "", "perm_edit": ""})
        self.assertNotIn("permissions", self.store.schemas["plasmid"])

    def test_invalid_values_are_refused_and_users_cannot_set_rules(self):
        r = self.post(self.root_c, "/admin/types/plasmid/settings", {"name": "Plasmid", "color": "", "columns": ["created_by"], "perm_read": "friends"})
        self.assertNotIn("permissions", self.store.schemas["plasmid"])
        self.assertIn(self.post(self.a, "/admin/types/plasmid/settings", {"name": "P", "perm_read": "admin"}).status_code, (303, 403))
        self.assertNotIn("permissions", self.store.schemas["plasmid"])

    def test_hiding_a_type_takes_effect_for_open_sessions_at_once(self):
        rid = self.make("alice", "plasmid", "p")
        self.assertEqual(self.b.get(f"/r/{rid}").status_code, 200)
        self.post(self.root_c, "/admin/types/plasmid/settings", {"name": "Plasmid", "color": "", "columns": ["created_by"], "perm_read": "admin"})
        self.assertEqual(self.b.get(f"/r/{rid}").status_code, 404)
        self.assertEqual(self.a.get(f"/r/{rid}").status_code, 404)                       # even the creator
        self.assertEqual(self.root_c.get(f"/r/{rid}").status_code, 200)


if __name__ == "__main__":
    unittest.main()
