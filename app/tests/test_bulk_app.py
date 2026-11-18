"""Bulk edit through the web pages."""
import re, unittest

from test_app import Base, read


class BulkBase(Base):
    def setUp(self):
        super().setUp()
        self.a = [self.create("alice", name=f"a{i}", genotype="g") for i in range(3)]
        self.b = [self.create("bob", name=f"b{i}", genotype="g") for i in range(2)]
        self.alice, self.bob, self.root = self.client("alice"), self.client("bob"), self.client("root")

    def prepare(self, c, ids, **extra):
        data = {"ids": ids, **extra}
        return self.post(c, "/t/strain/bulk", data)

    def apply(self, c, ids, action, **extra):
        return self.post(c, "/t/strain/bulk/apply", {"ids": ids, "action": action, **extra})


class TestSelectionUi(BulkBase):
    def test_table_has_checkboxes_and_a_toolbar_but_cards_do_not(self):
        page = self.alice.get("/t/strain").text
        self.assertIn('id="check-all"', page)
        self.assertEqual(page.count('class="row-check"'), 5)
        self.assertIn('form="bulk"', page)
        self.assertIn('<form id="bulk" method="post" action="/t/strain/bulk"', page)
        self.assertIn('data-total="5"', page)
        self.assertIn('<script src="/static/bulk.js" defer>', page)
        self.assertNotIn("row-check", self.alice.get("/t/strain?view=cards").text)
        self.assertRegex(page, r'<td class="check"><label><input type="checkbox"')       # a label around the box: clicking it must not open the record

    def test_toolbar_carries_the_current_filters(self):
        page = self.alice.get(f"/t/strain?q=a&owner={self.uid('alice')}&archived=1").text
        self.assertRegex(page, r'name="q" value="a"')
        self.assertIn(f'name="owner" value="{self.uid("alice")}"', page)
        self.assertRegex(page, r'name="archived" value="1"')

    def test_script_supports_select_all_and_range(self):
        js = read("bulk.js")
        for needle in ("check-all", "shiftKey", "indeterminate", "disabled", "data-for"):
            self.assertIn(needle, js)
        self.assertNotIn("innerHTML", js)

    def test_selection_survives_a_change_of_filter(self):
        js = read("bulk.js")
        for needle in ("sessionStorage", "sel-extra", "not in this view", "results-updated", "pageshow"):
            self.assertIn(needle, js)                                  # kept per type, re-applied to swapped rows, sent as hidden fields
        page = self.alice.get("/t/strain").text
        self.assertIn('id="bulk-clear"', page)
        self.assertIn('id="bulk-done"', page)

    def test_bulk_edit_is_hidden_until_select_is_pressed(self):
        page = self.alice.get("/t/strain").text
        self.assertIn('id="bulk-toggle"', page)                         # the Select button (table view only)
        self.assertNotIn('id="bulk-toggle"', self.alice.get("/t/strain?view=cards").text)
        css, js = read("style.css"), read("bulk.js")
        self.assertIn("html.js main:not(.selecting) .bulkbar", css)      # without scripting the boxes simply show
        self.assertIn("html.js .applybtn{display:none}", css)           # live search makes the Apply button useless with scripting
        for needle in ("selecting", "bulk-done", "setMode(false)"):
            self.assertIn(needle, js)


class TestPreparePage(BulkBase):
    def test_login_csrf_and_unknown_type(self):
        r = self.client().post("/t/strain/bulk", data={"ids": self.a})                       # not signed in: sent to the login page
        self.assertEqual((r.status_code, r.headers["location"].split("?")[0]), (303, "/login"))
        self.assertEqual(self.alice.post("/t/strain/bulk", data={"ids": self.a}).status_code, 403)   # signed in but no form token
        self.assertEqual(self.post(self.alice, "/t/ghost/bulk", {"ids": self.a}).status_code, 404)

    def test_nothing_selected_goes_back_with_a_message(self):
        r = self.prepare(self.alice, [])
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/t/strain"))
        self.assertIn("Select at least one", self.alice.get("/t/strain").text)

    def test_counts_for_a_user_and_for_an_admin(self):
        page = self.prepare(self.alice, self.a + self.b).text
        self.assertIn("<strong>5</strong> selected", page)
        self.assertIn("you can edit <strong>3</strong>", page)
        self.assertIn("<strong>2</strong> will be skipped", page)
        self.assertIn("Apply to 3 records", page)
        for i in self.a + self.b:
            self.assertIn(f'<input type="hidden" name="ids" value="{i}">', page)
        admin = self.prepare(self.root, self.a + self.b).text
        self.assertIn("you can edit <strong>5</strong>", admin)
        self.assertNotIn("will be skipped", admin)

    def test_owner_is_offered_to_admins_only_and_delete_to_nobody(self):
        self.assertNotIn('value="owner"', self.prepare(self.alice, self.a).text)
        admin = self.prepare(self.root, self.a).text
        self.assertIn('value="owner"', admin)
        self.assertNotIn('value="delete"', admin)

    def test_fields_offered_are_the_bulk_editable_ones(self):
        page = self.prepare(self.alice, self.a).text
        self.assertIn('name="value_genotype"', page)
        self.assertIn('name="value_status"', page)
        self.assertNotIn('name="value_name"', page)                                        # the title is not bulk-editable
        self.assertIn('name="opt_plasmid"', page)                                          # reference fields can add/remove
        self.assertIn('data-ref-to="plasmid" data-multiple="0"', page)                    # with the picker

    def test_a_list_can_show_every_record_so_the_header_box_selects_them_all(self):
        from shelfblazer_app import lists
        old, lists.PAGE_SIZE = lists.PAGE_SIZE, 2
        try:
            page = self.alice.get("/t/strain").text
            self.assertEqual(page.count('class="row-check"'), 2)
            self.assertIn("page 1 of 3", page)
            self.assertIn("show all 5</a>", page)
            self.assertIn("&show=all", page)
            full = self.alice.get("/t/strain?show=all").text
            self.assertEqual(full.count('class="row-check"'), 5)
            self.assertIn("all 5 shown", full)
            self.assertIn("show 100 per page", full)
            self.assertNotIn("next ›", full)
            self.assertRegex(full, r'class="sort" href="[^"]*&show=all')            # sorting keeps everything on screen
            self.assertEqual(self.alice.get(f"/t/strain?show=all&owner={self.uid('bob')}").text.count('class="row-check"'), 2)
        finally:
            lists.PAGE_SIZE = old

    def test_show_all_is_not_offered_beyond_the_bulk_limit(self):
        from shelfblazer_app import lists
        old = (lists.SHOW_ALL_MAX, lists.PAGE_SIZE)
        lists.SHOW_ALL_MAX, lists.PAGE_SIZE = 4, 2
        try:
            page = self.alice.get("/t/strain").text
            self.assertNotIn("show all", page)
            self.assertEqual(self.alice.get("/t/strain?show=all").text.count('class="row-check"'), 2)   # the request is ignored
        finally:
            lists.SHOW_ALL_MAX, lists.PAGE_SIZE = old

    def test_unknown_ids_are_ignored(self):
        page = self.prepare(self.alice, self.a[:1] + ["ST-999", "../x"]).text
        self.assertIn("<strong>1</strong> selected", page)


class TestApply(BulkBase):
    def commits(self):
        return int(self.store.git("rev-list", "--count", "HEAD"))

    def test_set_a_field_changes_only_own_records_in_one_commit_and_shows_a_summary(self):
        before = self.commits()
        page = self.apply(self.alice, self.a + self.b, "set", field="status", value_status="bad").text
        self.assertEqual(self.commits(), before + 1)
        self.assertIn("<strong>3</strong> changed", page)
        self.assertIn("2 skipped", page)
        self.assertIn("not yours", page)
        self.assertIn("Undo this bulk edit", page)
        for i in self.a:
            self.assertEqual(self.store.records[i]["fields"]["status"], "bad")
        self.assertNotIn("status", self.store.records[self.b[0]]["fields"])
        self.assertEqual(self.store.git("log", "-1", "--format=%an").strip(), self.uid("alice"))

    def test_invalid_value_shows_the_form_again_and_changes_nothing(self):
        before = self.commits()
        r = self.apply(self.alice, self.a, "set", field="status", value_status="nonsense")
        self.assertEqual(r.status_code, 200)                                                # reported per record, not as a failure of the page
        self.assertIn("could not be changed", r.text)
        self.assertEqual(self.commits(), before)
        r = self.apply(self.alice, self.a, "set", field="name", value_name="x")             # not a bulk field at all
        self.assertEqual(r.status_code, 422)
        self.assertIn("Apply to 3 records", r.text)
        self.assertEqual(self.commits(), before)

    def test_add_and_remove_an_option_on_a_multiselect(self):
        p = [self.create("alice", "plasmid", name=f"p{i}", resistance="Kan") for i in range(2)]
        self.post(self.alice, "/t/plasmid/bulk/apply", {"ids": p, "action": "add", "field": "resistance", "opt_resistance": "Amp"})
        self.assertEqual(self.store.records[p[0]]["fields"]["resistance"], ["Kan", "Amp"])
        self.post(self.alice, "/t/plasmid/bulk/apply", {"ids": p, "action": "remove", "field": "resistance", "opt_resistance": "Kan"})
        self.assertEqual(self.store.records[p[1]]["fields"]["resistance"], ["Amp"])

    def test_add_a_linked_record_to_many(self):
        pl = self.create("alice", "plasmid", name="pX")
        self.apply(self.alice, self.a, "add", field="plasmid", opt_plasmid=pl)
        for i in self.a:
            self.assertEqual(self.store.records[i]["fields"]["plasmid"], [pl])
        self.assertIn(self.a[0], self.alice.get(f"/r/{pl}").text)                           # back-links follow

    def test_archive_and_restore(self):
        self.apply(self.alice, self.a, "archive")
        self.assertTrue(all(self.store.records[i]["archived"] for i in self.a))
        self.apply(self.alice, self.a, "restore")
        self.assertFalse(any(self.store.records[i]["archived"] for i in self.a))

    def test_admin_only_actions_are_refused_for_users_and_change_nothing(self):
        before = self.commits()
        self.assertEqual(self.apply(self.alice, self.a, "owner", owner="bob").status_code, 403)
        self.assertEqual(self.commits(), before)
        self.assertEqual(self.store.records[self.a[0]]["created_by"], self.uid("alice"))

    def test_admin_changes_owner(self):
        self.apply(self.root, self.a, "owner", owner="bob")
        self.assertEqual({self.store.records[i]["created_by"] for i in self.a}, {self.uid("bob")})
        self.assertIn("Edit", self.bob.get(f"/r/{self.a[0]}").text)

    def test_csrf_and_login_are_required_to_apply(self):
        self.assertEqual(self.alice.post("/t/strain/bulk/apply", data={"ids": self.a, "action": "archive"}).status_code, 403)
        r = self.client().post("/t/strain/bulk/apply", data={"ids": self.a, "action": "archive"})
        self.assertEqual((r.status_code, r.headers["location"].split("?")[0]), (303, "/login"))
        self.assertFalse(any(self.store.records[i]["archived"] for i in self.a))


class TestUndo(BulkBase):
    def sha_of(self, page):
        return re.search(r'name="sha" value="([0-9a-f]{40})"', page).group(1)

    def test_the_person_who_did_it_can_undo_and_lands_back_on_the_list(self):
        sha = self.sha_of(self.apply(self.alice, self.a, "set", field="status", value_status="bad").text)
        r = self.post(self.alice, "/bulk/undo", {"sha": sha, "back": "/t/strain"})
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/t/strain"))
        self.assertIn("was undone", self.alice.get("/t/strain").text)
        self.assertNotIn("status", self.store.records[self.a[0]]["fields"])
        self.assertTrue(self.store.git("log", "-1", "--format=%s").startswith("Revert"))

    def test_someone_else_cannot_but_an_admin_can(self):
        sha = self.sha_of(self.apply(self.alice, self.a, "set", field="status", value_status="bad").text)
        self.assertEqual(self.post(self.bob, "/bulk/undo", {"sha": sha, "back": "/t/strain"}).status_code, 403)
        self.assertEqual(self.store.records[self.a[0]]["fields"]["status"], "bad")
        self.post(self.root, "/bulk/undo", {"sha": sha, "back": "/t/strain"})
        self.assertNotIn("status", self.store.records[self.a[0]]["fields"])

    def test_twice_garbage_and_open_redirects(self):
        sha = self.sha_of(self.apply(self.alice, self.a, "archive").text)
        self.post(self.alice, "/bulk/undo", {"sha": sha, "back": "/t/strain"})
        r = self.post(self.alice, "/bulk/undo", {"sha": sha, "back": "//evil.example"})
        self.assertEqual(r.headers["location"], "/")                                       # unsafe "back" is ignored
        self.assertIn("already undone", self.alice.get("/").text)
        r = self.post(self.alice, "/bulk/undo", {"sha": "not-a-sha", "back": "/t/strain"})
        self.assertEqual(r.status_code, 303)
        r = self.client().post("/bulk/undo", data={"sha": sha})
        self.assertEqual((r.status_code, r.headers["location"].split("?")[0]), (303, "/login"))

    def test_undo_after_a_later_edit_is_refused_politely(self):
        sha = self.sha_of(self.apply(self.alice, self.a, "set", field="genotype", value_genotype="bulk").text)
        v = self.store.records[self.a[0]]["_version"]
        self.post(self.alice, f"/r/{self.a[0]}/edit", {"name": "a0", "f_genotype": "later", "version": v})
        self.post(self.alice, "/bulk/undo", {"sha": sha, "back": "/t/strain"})
        self.assertIn("can no longer be undone", self.alice.get("/t/strain").text)
        self.assertEqual(self.store.records[self.a[0]]["fields"]["genotype"], "later")

    def test_the_author_cannot_undo_once_the_records_are_no_longer_theirs(self):
        sha = self.sha_of(self.apply(self.alice, self.a, "set", field="genotype", value_genotype="bulk").text)
        self.post(self.root, "/t/strain/bulk/apply", {"ids": self.a[0], "action": "owner", "owner": self.uid("bob")})
        r = self.post(self.alice, "/bulk/undo", {"sha": sha, "back": "/t/strain"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("ask an administrator", r.text)
        self.assertEqual(self.store.records[self.a[1]]["fields"]["genotype"], "bulk")


if __name__ == "__main__":
    unittest.main()
