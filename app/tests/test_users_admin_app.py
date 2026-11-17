"""User admin page: order, filter, entry links and the entries page, copy button for one-time links."""
import json, os, re, unittest

from test_app import Base

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shelfblazer_app", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


class TestUsersPage(Base):
    def setUp(self):
        super().setUp()
        self.root = self.client("root")
        self.s1 = self.create("alice", name="alice strain")
        self.s2 = self.create("alice", name="alice strain 2")
        self.p1 = self.create("alice", "plasmid", name="pAlice")
        self.post(self.client("alice"), f"/r/{self.s2}/archive")

    def set_created(self, stamps):
        path = os.path.join(self.inst, "users.json")
        with open(path) as fh:
            d = json.load(fh)
        for name, stamp in stamps.items():
            d["users"][name]["created"] = stamp
        with open(path, "w") as fh:
            json.dump(d, fh)

    def order(self):
        page = self.root.get("/admin/users").text
        return re.findall(r"<tr data-text=\"[^\"]*\"><td><code>([a-z]+)</code>", page)

    def test_newest_accounts_first_ties_by_name(self):
        self.set_created({"root": "2026-01-01T10:00:00", "alice": "2026-03-01T10:00:00", "bob": "2026-03-01T10:00:00"})
        self.assertEqual(self.order(), ["alice", "bob", "root"])
        self.set_created({"bob": "2026-05-01T10:00:00"})
        self.assertEqual(self.order(), ["bob", "alice", "root"])

    def test_the_created_date_is_shown(self):
        self.set_created({"alice": "2026-03-01T10:00:00"})
        self.assertRegex(self.root.get("/admin/users").text, r"<td>Alice A</td><td class=\"muted\" data-v=\"2026-03-01T10:00:00\">2026-03-01</td>")

    def test_the_entry_count_links_to_the_entries_page_only_when_not_zero(self):
        page = self.root.get("/admin/users").text
        self.assertRegex(page, r'<a href="/admin/users/alice/entries"[^>]*>3</a>')          # archived entries count too
        self.assertNotIn("/admin/users/bob/entries", page)
        self.assertRegex(page, r'<td data-v="0">0</td>')

    def test_columns_can_be_sorted_by_clicking_their_title(self):
        page = self.root.get("/admin/users").text
        for title in ("Username", "Name", "Created", "Role", "Status", "Entries"):
            self.assertRegex(page, r'<th data-sort="(text|num)" aria-sort="(none|descending)"><button type="button" class="sortbtn">%s</button></th>' % title)
        self.assertIn('aria-sort="descending"><button type="button" class="sortbtn">Created', page)      # the order the server sends
        self.assertNotIn("sortbtn\">Actions", page)
        self.assertRegex(page, r'<td data-v="3"><a href="/admin/users/alice/entries"')                  # numbers sort as numbers
        js = read("app.js")
        for needle in ("th[data-sort] .sortbtn", "aria-sort", "localeCompare", "data-v"):
            self.assertIn(needle, js)

    def test_two_states_inactive_means_deactivated_or_no_password_and_is_listed_by_default(self):
        self.post(self.root, "/admin/users/bob/update", {"active": "0"})               # deactivated by an administrator
        self.users.create("carol", "Carol C")                                           # imported person: no password yet
        page = self.root.get("/admin/users").text
        self.assertIn('id="show-inactive" checked data-toggle="#users" data-class="show-inactive"', page)       # shown by default
        self.assertIn('<table id="users" class="sortable show-inactive">', page)
        self.assertIn("show inactive (2)", page)
        self.assertRegex(page, r'<tr data-text="bob b bob" data-inactive="1">')
        self.assertRegex(page, r'<tr data-text="carol c carol" data-inactive="1">')
        self.assertNotRegex(page, r'<tr data-text="alice a alice" data-inactive')
        self.assertNotIn("no password yet</span>", page)                                  # no third state on the page
        self.assertEqual(page.count('<td data-v="inactive">'), 2)
        self.assertEqual(page.count('<td data-v="active">'), 2)
        row = lambda name: re.search(r'<tr data-text="%s.*?</tr>' % name, page, re.S).group(0)
        self.assertIn("Reactivate", row("bob b bob"))                                   # a deactivated account can be brought back
        self.assertNotIn("Deactivate", row("carol c carol"))                            # nothing to toggle without a password
        self.assertNotIn("Reactivate", row("carol c carol"))
        self.assertIn("Set-password link", row("carol c carol"))
        self.assertIn(">Deactivate<", row("alice a alice"))
        css, js = read("style.css"), read("app.js")
        self.assertIn("html.js #users:not(.show-inactive) tr[data-inactive]{display:none}", css)
        for needle in ("data-inactive", "show-inactive", "data-toggle", "refilter"):
            self.assertIn(needle, js)

    def test_a_deactivated_or_passwordless_account_cannot_sign_in(self):
        self.post(self.root, "/admin/users/bob/update", {"active": "0"})
        self.users.create("carol", "Carol C")
        anon = self.client()
        self.assertEqual(self.post(anon, "/login", {"username": "bob", "password": "correct horse battery"}, anon=True).status_code, 401)
        self.assertEqual(self.post(anon, "/login", {"username": "carol", "password": ""}, anon=True).status_code, 401)

    def test_the_filter_box_and_script(self):
        page = self.root.get("/admin/users").text
        self.assertIn('id="user-filter" data-filter="#users tbody tr"', page)
        self.assertIn('data-text="alice a alice"', page)                                     # name and username, lower case
        for needle in ("data-filter", "data-text", "r.hidden"):
            self.assertIn(needle, read("app.js"))

    def test_entries_page_has_one_table_per_type(self):
        page = self.root.get("/admin/users/alice/entries").text
        self.assertIn("Entries of Alice A", page)
        for rid in (self.s1, self.s2, self.p1):
            self.assertIn(f'href="/r/{rid}"', page)
        self.assertEqual(page.count('class="userentries"'), 2)
        self.assertRegex(page, r'<tr data-href="/r/%s" class="archived">.*archived</span>' % self.s2)
        self.assertIn(f'href="/t/strain?owner={self.uid("alice")}&archived=1"', page)    # the heading opens the filterable list

    def test_entries_page_stops_at_the_cap_and_points_to_the_list(self):
        from shelfblazer_app import users_admin as web
        old, web.ENTRIES_PER_TYPE = web.ENTRIES_PER_TYPE, 1
        try:
            page = self.root.get("/admin/users/alice/entries").text
        finally:
            web.ENTRIES_PER_TYPE = old
        self.assertIn("The first 1 of 2.", page)
        self.assertIn("Show all 2 in the list", page)

    def test_entries_page_of_someone_without_entries_and_of_nobody(self):
        self.assertIn("has not created any entries", self.root.get("/admin/users/bob/entries").text)
        self.assertEqual(self.root.get("/admin/users/nobody/entries").status_code, 404)

    def test_only_admins_see_it(self):
        self.assertEqual(self.client("alice").get("/admin/users/alice/entries").status_code, 403)
        self.assertIn(self.client().get("/admin/users/alice/entries").status_code, (303, 401, 403))


class TestResetLink(Base):
    def test_the_one_time_link_has_a_copy_button(self):
        root = self.client("root")
        r = self.post(root, "/admin/users/bob/reset-link")
        self.assertEqual(r.status_code, 200)
        self.assertIn('<pre class="seq" id="reset-link">http', r.text)
        self.assertIn('<button type="button" class="btn" data-copy="#reset-link">Copy link</button>', r.text)
        js = read("app.js")
        for needle in ("data-copy", "navigator.clipboard", "execCommand"):
            self.assertIn(needle, js)


class TestSidebar(Base):
    def test_sign_out_is_an_icon_next_to_the_name(self):
        page = self.client("alice").get("/").text
        self.assertRegex(page, r'<div class="whorow"><a href="/me" class="who".*?</a>\s*<form method="post" action="/logout".*?title="Sign out" aria-label="Sign out">'
                         r'<svg class="ico".*?#right-from-bracket')
        self.assertNotRegex(page, r">\s*Sign out\s*<")                                         # no text link any more


if __name__ == "__main__":
    unittest.main()
