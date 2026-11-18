"""Page markup shared by every page: the sidebar menu and its script, icons (no emoji), mono record IDs, chips and avatars,
list links that keep their filters, empty states."""
import re, unittest

from test_app import EMOJI, Base, read, tags


class TestMenuAndMarkup(Base):
    def test_sidebar_menu_markup(self):
        self.create("alice", name="s1")
        c = self.client("alice")
        page = c.get("/t/strain").text
        self.assertIn('<header class="side">', page)
        self.assertIn('class="menu-toggle" aria-expanded="false" aria-controls="menu"', page)
        self.assertGreater(page.index("menu-toggle"), page.index('class="brand"'))      # hamburger sits to the right of the name
        self.assertIn('id="menu"', page)
        self.assertRegex(page, r'<a href="/t/strain"[^>]*aria-current="page"')
        self.assertNotRegex(page, r'<a href="/t/plasmid"[^>]*aria-current')
        self.assertRegex(c.get("/r/ST-1").text, r'<a href="/t/strain"[^>]*aria-current="page"')
        self.assertNotIn("Search</a>", page)
        self.assertIn('<a class="brand" href="/">', page)
        self.assertIn("Sign out", page)
        self.assertIn('<script src="/static/app.js">', page)
        self.assertNotIn("Users</a>", page)
        admin = self.client("root").get("/admin/users").text
        self.assertRegex(admin, r'<a class="admin" href="/admin/users" aria-current="page">Admin</a>')

    def test_logged_out_pages_have_no_menu(self):
        page = self.client().get("/login").text
        self.assertIn('<body class="anon">', page)
        self.assertNotIn("menu-toggle", page)
        self.assertNotIn('id="menu"', page)

    def test_menu_script_toggles_and_is_not_inline(self):
        js = read("app.js")
        for needle in ('classList.add("js")', ".menu-toggle", "aria-expanded", "Escape"):
            self.assertIn(needle, js)
        self.assertNotIn("<script>", self.client("alice").get("/").text)

    def test_nav_puts_strain_before_plasmid_not_alphabetical(self):
        page = self.client("alice").get("/").text
        self.assertLess(page.index('href="/t/strain"'), page.index('href="/t/plasmid"'))

    def test_no_emoji_anywhere_only_svg_icons(self):
        self.create("alice", name="s1")
        c = self.client("alice")
        for path in ("/", "/t/strain", "/t/strain?view=cards", "/t/plasmid", "/r/ST-1", "/t/strain/new", "/me", "/login"):
            self.assertFalse(EMOJI.search(c.get(path).text), path)
        page = c.get("/t/strain").text
        self.assertIn('href="/static/icons.svg#bacterium"', page)
        self.assertIn('href="/static/icons.svg#dna"', c.get("/").text)               # home tiles carry the type icons
        sprite = c.get("/static/icons.svg")
        self.assertEqual(sprite.status_code, 200)
        self.assertIn("image/svg", sprite.headers["content-type"])

    def test_custom_types_get_the_box_icon(self):
        from shelfblazer_app.web import svg_icon
        self.assertEqual(self.store.type_icon("custom-type"), "box")
        self.assertNotIn("<script", svg_icon("x", '"><script>'))

    def test_a_type_chooses_its_icon_and_bad_icons_are_refused(self):
        root = self.client("root")
        self.post(root, "/admin/types/strain/settings", {"name": "Strain", "icon": "flask", "columns": ["plasmid"]})
        self.assertEqual(self.store.schemas["strain"]["icon"], "flask")
        self.assertIn("icons.svg#flask", self.client("alice").get("/t/strain").text)
        self.post(root, "/admin/types/strain/settings", {"name": "Strain", "icon": "skull", "columns": ["plasmid"]})
        self.assertEqual(self.store.schemas["strain"]["icon"], "flask")

    def test_chips_and_avatars_render_and_escape(self):
        self.create("alice", "plasmid", name="<i>pX</i>", resistance="Kan")
        c = self.client("alice")
        self.assertIn('class="chip"', c.get("/r/PL-1").text)
        for path in ("/t/plasmid", "/t/plasmid?view=cards", "/r/PL-1"):
            page = c.get(path).text
            self.assertIn('class="avatar"', page, path)
            self.assertNotIn("<i>pX</i>", page, path)
        self.assertIn("rcards", c.get("/t/plasmid?view=cards").text)
        self.assertIn("<table", self.client("alice").get("/t/plasmid?view=bogus").text)    # unknown view: the default (nothing remembered)

    def test_list_links_keep_the_current_filters_and_view(self):
        self.create("alice", name="s1")
        c = self.client("alice")
        me = self.uid("alice")
        page = c.get(f"/t/strain?q=s&view=cards&sort=name&dir=desc&owner={me}").text
        self.assertIn(f'href="/t/strain?q=s&sort=name&dir=desc&owner={me}&fuzzy=1&view=table&page=1"', page)
        page = c.get(f"/t/strain?q=s&view=table&sort=name&dir=desc&owner={me}").text
        self.assertIn(f'href="/t/strain?q=s&sort=id&dir=asc&owner={me}&fuzzy=1&view=table&page=1"', page)
        self.assertIn(f'href="/t/strain?q=s&sort=name&dir=asc&owner={me}&fuzzy=1&view=table&page=1"', page)
        self.assertNotIn("sort=&", page)
        self.assertNotIn("view=&", page)

    def test_every_record_id_is_a_code_snippet(self):
        pl = self.create("alice", "plasmid", name="pX")
        st = self.create("alice", name="s1", plasmid=pl)
        c = self.client("alice")
        self.post(c, f"/r/{st}/edit", {"name": "s1", "f_plasmid": pl, "version": self.version(st), "notes": f"derived from {pl}, not ST-99"})
        listing = c.get("/t/strain").text
        self.assertTrue(tags(listing, "a", class_="rid", href=f"/r/{st}", _text=st))
        self.assertTrue(tags(listing, "a", class_="rid", href=f"/r/{pl}", data_hue="2", _text=pl))         # a plasmid ID is green on a strain list
        self.assertTrue(tags(c.get("/t/strain?view=cards").text, "span", class_="idtag", _text=st))
        self.assertTrue(tags(c.get("/?q=s1").text, "a", class_="rid", href=f"/r/{st}", _text=st))
        page = c.get(f"/r/{st}").text
        self.assertTrue(tags(page, "a", class_="rid", href=f"/r/{pl}", data_hue="2", _text=pl))
        self.assertNotIn('class="rid" href="/r/ST-99"', page)
        self.assertTrue(tags(c.get(f"/r/{pl}").text, "a", class_="rid", href=f"/r/{st}", data_hue="1", _text=st))   # back-links carry the other type's colour
        self.assertTrue(tags(page, "code", class_="rid", _text=st))
        self.assertTrue(tags(c.get(f"/r/{st}/edit").text, "input", class_="mono", name="f_plasmid"))
        r = self.post(c, "/t/strain/new", {"name": "x"})
        self.assertRegex(c.get(r.headers["location"]).text, r'Created <code class="rid">ST-\d+</code>')
        self.assertIn('<code class="rid">ST-9999</code> not found', c.get("/r/ST-9999").text)
        self.assertRegex(read("style.css"), r"\.rid[^{]*\{[^}]*font-family:var\(--mono\)|\.rid,\.idtag\{font-family:var\(--mono\)")

    def test_menu_names_are_plural_like_the_page_titles(self):
        page = self.client("alice").get("/t/strain").text
        self.assertRegex(page, r'<a href="/t/strain"[^>]*>(<svg.*?</svg>)?Strains</a>')
        self.assertRegex(page, r'<a href="/t/plasmid"[^>]*>(<svg.*?</svg>)?Plasmids</a>')
        self.assertRegex(page, r"<h1>(<svg.*?</svg>)?Strains ")

    def test_new_records_do_not_offer_the_old_free_text_storage(self):
        c = self.client("alice")
        self.assertNotIn('name="f_storage"', c.get("/t/strain/new").text)
        self.assertIn('name="f_genotype"', c.get("/t/strain/new").text)
        rid = self.create("alice", name="s1", storage="Box 5 Well A1")                 # old data and scripted input are still stored
        self.assertIn('name="f_storage"', c.get(f"/r/{rid}/edit").text)                 # and editable on an existing record
        bad = self.post(c, "/t/strain/new", {"name": ""})                               # the error page keeps the same fields
        self.assertEqual(bad.status_code, 422)
        self.assertNotIn('name="f_storage"', bad.text)

    def test_icons_only_in_home_tiles_the_type_page_title_and_sign_out(self):
        self.create("alice", name="s1")
        c = self.client("alice")
        nav = re.search(r"<nav.*?</nav>", c.get("/t/strain").text, re.S).group(0)
        self.assertNotIn("<svg", nav)
        self.assertIn('class="card"', c.get("/").text)
        self.assertEqual(c.get("/").text.count("icons.svg#bacterium"), 1)                 # the Strain tile
        self.assertRegex(c.get("/t/strain").text, r"<h1><svg[^>]*><use href=\"/static/icons.svg#bacterium\"")
        home = c.get("/").text
        self.assertEqual(home.count("icons.svg#right-from-bracket"), 1)                  # the sign-out icon next to your name, nowhere else
        self.assertIn("right-from-bracket", re.search(r'<div class="side-foot">.*?</header>', home, re.S).group(0))
        self.assertNotIn("+ New", c.get("/").text)                                        # home tiles are plain links to the list

    def test_admin_entry_is_last_in_the_menu_and_only_for_admins(self):
        admin = re.search(r"<nav.*?</nav>", self.client("root").get("/").text, re.S).group(0)
        self.assertTrue(admin.rstrip().endswith('<a class="admin" href="/admin/users">Admin</a></nav>'), admin[-120:])
        self.assertNotIn("Users</a>", admin)
        self.assertNotIn(">Types</a>", admin)
        self.assertNotIn('class="admin"', self.client("alice").get("/").text)
        # Users and Types are reached from the Admin pages
        self.assertIn('href="/admin/types"', self.client("root").get("/admin/users").text)
        self.assertIn('href="/admin/users"', self.client("root").get("/admin/types").text)
        self.assertTrue(tags(self.client("root").get("/admin/types").text, "a", class_="admin", href="/admin/users", aria_current="page"))

    def test_empty_list_shows_a_friendly_state(self):
        page = self.client("alice").get("/t/strain").text
        self.assertIn('class="empty"', page)
        self.assertIn("No strains yet", page)


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
