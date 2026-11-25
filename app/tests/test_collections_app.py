"""The interface of personal collections (design section 17.6): menu, lists, new records, search, Home, the Me page, links."""
import html, re

from test_app import PW, Base, PLASMID, STRAIN, leaked_keys


class Collections(Base):
    def setUp(self):
        super().setUp()
        self.root_user = self.users.get("root")
        self.alice, self.bob = self.users.get("alice"), self.users.get("bob")
        self.store.save_schema("strain", dict(STRAIN, permissions={"personal": "all"}), self.root_user)
        self.lab = self.create("bob", name="zebra lab strain")
        self.lab_pl = self.create("bob", "plasmid", name="pLab")
        self.mine = self.store.create("strain", self.alice, "zebra mine", {}, personal=True)
        self.bobs = self.store.create("strain", self.bob, "zebra bobs", {}, personal=True)
        self.a, self.b = self.client("alice"), self.client("bob")

    @staticmethod
    def text(r):
        return html.unescape(r.text)

    def rows(self, page):
        return re.findall(r'<tr data-href="/r/([A-Z]{2}-P?\d+(?:~\w+)?)"', page)

    # ---- menu
    def test_the_menu_has_a_my_collection_part_only_for_types_that_allow_it(self):
        nav = re.search(r"<nav.*?</nav>", self.a.get("/").text, re.S).group(0)
        self.assertRegex(nav, r'<span class="navhead">My collection</span><a href="/t/strain\?c=mine" data-hue="1">Strains</a>')
        self.assertRegex(nav, r'<nav[^>]*>\s*<span class="navhead">Lab collection</span><a href="/t/strain"')
        self.assertNotIn("/t/plasmid?c=mine", nav)                                        # plasmids do not allow it
        self.assertIn('<a href="/t/plasmid" data-hue="2">Plasmids</a>', nav)              # the lab's types are as before
        self.assertIn('aria-current="page">Strains</a>', re.search(r"<nav.*?</nav>", self.a.get("/t/strain?c=mine").text, re.S).group(0).split("My collection")[1])
        self.assertIn('aria-current="page"', re.search(r"<nav.*?</nav>", self.a.get(f"/r/{self.mine}").text, re.S).group(0).split("My collection")[1])
        lab_nav = re.search(r"<nav.*?</nav>", self.a.get("/t/strain").text, re.S).group(0)
        self.assertNotIn('aria-current="page"', lab_nav.split("My collection")[1])           # the lab list is not My collection

    def test_no_menu_part_when_no_type_allows_personal_collections(self):
        self.store.save_schema("strain", dict(STRAIN), self.root_user)
        self.assertNotIn("navhead", self.client("root").get("/").text)

    # ---- lists
    def test_the_lab_list_has_no_personal_records_and_the_personal_list_only_yours(self):
        self.assertEqual(self.rows(self.a.get("/t/strain").text), [self.lab])
        self.assertEqual(self.rows(self.a.get("/t/strain?c=mine").text), [self.mine])
        self.assertEqual(self.rows(self.b.get("/t/strain?c=mine").text), [self.bobs])

    def test_a_personal_list_says_so_and_is_titled_for_its_owner(self):
        page = self.text(self.a.get("/t/strain?c=mine"))
        self.assertIn("<title>My strains · ShelfBlazer</title>", page)
        self.assertIn("Only in your collection, not in the lab's records. Visible to others: <strong>yes</strong>", page)
        self.assertIn('href="/t/strain/new?c=mine"', page)
        self.assertNotIn("Owner<select", page)
        self.assertIn("Collections of others: <a", page)
        self.assertIn(f'href="/t/strain?c={self.users.get("bob")["uid"]}">Bob B (1)</a>', page)

    def test_someone_elses_visible_collection_is_read_only_and_a_hidden_one_is_empty(self):
        bob_uid = self.bob["uid"]
        page = self.text(self.a.get(f"/t/strain?c={bob_uid}"))
        self.assertIn("<title>Bob's strains · ShelfBlazer</title>", page)
        self.assertEqual(self.rows(page), [self.bobs])
        self.assertIn("In Bob's personal collection", page)
        self.assertNotIn("newbtn", page)
        self.users.set_collection_visible("bob", "strain", False)
        self.assertEqual(self.rows(self.a.get(f"/t/strain?c={bob_uid}").text), [])
        self.assertNotIn("Bob B (", self.a.get("/t/strain?c=mine").text)
        self.assertEqual(self.a.get("/t/strain?c=nobody").status_code, 404)

    def test_the_lab_list_search_points_to_hits_in_personal_collections_with_a_line(self):
        page = self.text(self.a.get("/t/strain?q=zebra"))
        self.assertEqual(self.rows(page), [self.lab])
        self.assertIn("Also in collections: ", page)
        self.assertIn('href="/t/strain?q=zebra&c=mine">yours (1)</a>', page)
        self.assertIn(f'href="/t/strain?q=zebra&c={self.bob["uid"]}">Bob\'s (1)</a>', page)
        self.assertIn('href="/?q=zebra">search everywhere</a>', page)
        self.assertNotIn("Also in collections", self.a.get("/t/strain?q=nothingmatches").text)
        self.assertNotIn("Also in collections", self.a.get("/t/strain?q=zebra&c=mine").text)           # not while searching a personal list
        self.assertEqual(self.rows(self.a.get("/t/strain?q=zebra&c=mine").text), [self.mine])

    def test_an_empty_personal_list_offers_to_add_to_it_not_to_the_lab(self):
        self.users.create("carol", "Carol C", "user", PW)
        self.store.save_schema("strain", dict(STRAIN, permissions={"personal": "all", "create": "admin"}), self.root_user)
        page = self.text(self.client("carol").get("/t/strain?c=mine"))
        self.assertIn("No strains yet.", page)
        empty = page.split('<div class="empty">')[1].split("</div>")[0]
        self.assertIn('href="/t/strain/new?c=mine"', empty)                 # her own collection, though only administrators add to the lab
        bob_list = self.text(self.client("carol").get(f"/t/strain?c={self.bob['uid']}"))
        self.assertNotIn("/t/strain/new", bob_list)                          # someone else's collection: no button

    def test_the_collection_lines_are_inside_the_results_so_live_search_updates_them(self):
        page = self.text(self.a.get("/t/strain?q=zebra", headers={"x-live": "1"}))
        results = page.split('<div id="results">')[1].split("<script")[0]
        self.assertIn("Also in collections: ", results)
        page = self.text(self.a.get("/t/strain?c=mine"))
        self.assertIn("Collections of others: ", page.split('<div id="results">')[1].split("<script")[0])

    # ---- new records
    def test_new_record_asks_for_the_collection_with_the_lab_as_the_default(self):
        page = self.a.get("/t/strain/new").text
        self.assertIn('<legend>Add to</legend>', page)
        self.assertRegex(page, r'name="collection" value="lab" checked> Lab collection')
        self.assertRegex(page, r'name="collection" value="mine"> My collection')
        self.assertRegex(self.a.get("/t/strain/new?c=mine").text, r'name="collection" value="mine" checked> My collection')
        self.assertNotIn("<legend>Add to</legend>", self.a.get("/t/plasmid/new").text)          # nothing to choose

    def test_saving_into_my_collection_gives_a_personal_id_and_the_lab_counter_stays(self):
        r = self.post(self.a, "/t/strain/new", {"name": "in mine", "collection": "mine"})
        key = f"ST-P2~{self.alice['uid']}"                                                 # her second: each person counts their own
        self.assertEqual((r.status_code, r.headers["location"]), (303, f"/r/{key}"))
        page = self.a.get(f"/r/{key}").text
        self.assertIn('Created <code class="rid">ST-P2</code> in your collection.', page)    # people see ST-P2, not the key
        self.assertEqual(leaked_keys(page), [])
        r = self.post(self.a, "/t/strain/new", {"name": "in the lab"})
        self.assertEqual(r.headers["location"], f"/r/ST-{self.store.records[self.lab]['legacy_id'] + 1}")
        r = self.post(self.a, "/t/strain/new", {"name": "another", "collection": "mine", "action": "another"})
        self.assertEqual(r.headers["location"], "/t/strain/new?c=mine")

    def test_a_type_that_does_not_allow_it_refuses_my_collection(self):
        r = self.post(self.a, "/t/plasmid/new", {"name": "x", "collection": "mine"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual([p for p in self.store.records if p.startswith("PL-P")], [])

    def test_when_only_administrators_add_to_the_lab_people_still_add_to_theirs(self):
        self.store.save_schema("strain", dict(STRAIN, permissions={"personal": "all", "create": "admin"}), self.root_user)
        page = self.a.get("/t/strain/new").text
        self.assertIn('<input type="hidden" name="collection" value="mine">', page)
        self.assertNotIn("<legend>Add to</legend>", page)
        r = self.post(self.a, "/t/strain/new", {"name": "x", "collection": "mine"})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.post(self.a, "/t/strain/new", {"name": "y", "collection": "lab"}).status_code, 403)

    # ---- the record page
    def test_a_personal_record_says_whose_collection_it_is_in(self):
        page = self.text(self.a.get(f"/r/{self.mine}"))
        self.assertIn("In your personal collection, not in the lab collection.", page)
        self.assertIn(f'<a class="btn" href="/move?ids={self.mine}">Move to the lab collection</a>', page)
        self.assertIn('href="/t/strain?c=mine">‹ My strains</a>', page)
        other = self.text(self.a.get(f"/r/{self.bobs}"))
        self.assertIn("In Bob's personal collection, not in the lab collection.", other)
        self.assertNotIn("/move?ids=", other)                                                  # only for its owner and administrators
        self.assertIn(f'href="/t/strain?c={self.bob["uid"]}">‹ Bob\'s strains</a>', other)
        self.assertNotIn("personal collection", self.a.get(f"/r/{self.lab}").text)

    # ---- search and Home
    def test_global_search_shows_one_table_per_collection(self):
        page = self.text(self.a.get("/?q=zebra"))
        titles = re.findall(r'<span class="typechip">.*?</svg>([^<]+)</span> <span class="muted">(\d+)</span>', page)
        self.assertEqual([t[0] for t in titles], ["Strains", "My strains", "Bob's strains"])
        self.assertIn('href="/t/strain?q=zebra&c=mine">open list</a>', page)
        self.assertIn(f"/t/strain?q=zebra&c={self.bob['uid']}", page)
        self.users.set_collection_visible("bob", "strain", False)
        self.assertNotIn("Bob's strains", self.text(self.a.get("/?q=zebra")))
        self.assertIn("My strains", self.b.get("/?q=zebra").text)                                  # he still sees his own

    def test_home_counts_the_lab_and_my_collection_apart_and_activity_is_the_labs(self):
        page = self.a.get("/").text
        lab_cards = page.split("<span>My collection</span>")[0]
        self.assertIn('<h2 class="sec"><span>Lab collection</span></h2>', page)
        self.assertNotIn("only yours, not part", page)
        self.assertRegex(lab_cards, r'<a class="l" href="/t/strain">Strains</a><div class="tilebottom"><span class="n">1</span>')
        self.assertRegex(page, r'<a class="l" href="/t/strain\?c=mine">My strains</a><div class="tilebottom"><span class="n">1</span>')
        self.assertIn('href="/t/strain/new?c=mine"', page)
        self.assertNotIn(self.mine, page.split("Recent activity")[1] if "Recent activity" in page else "")

    # ---- Me page
    def test_the_me_page_sets_visible_to_others_per_type(self):
        page = self.a.get("/me").text
        self.assertRegex(page, r'name="visible_strain" checked> My strains: visible to others')
        self.assertIn("keep nothing confidential here", page)
        r = self.post(self.a, "/me/collections", {})                                          # box unchecked
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.users.get("alice")["hidden_collections"], ["strain"])
        self.assertNotIn("visible_strain\" checked", self.a.get("/me").text)
        self.assertEqual(self.rows(self.b.get(f"/t/strain?c={self.alice['uid']}").text), [])
        self.post(self.a, "/me/collections", {"visible_strain": "on"})
        self.assertNotIn("hidden_collections", self.users.get("alice"))

    # ---- links and the picker
    def test_the_picker_offers_the_lab_and_only_your_own_when_the_form_allows_it(self):
        ids = lambda q: [d["id"] for d in self.a.get("/api/records?" + q).json()["results"]]
        self.assertEqual(ids("q=zebra&type=strain"), [self.lab])                                   # a lab record links to lab records
        self.assertEqual(sorted(ids("q=zebra&type=strain&collection=lab%2Bmine")), sorted([self.lab, self.mine]))
        self.assertEqual(ids(f"ids={self.mine}&type=strain"), [])
        self.assertEqual(ids(f"ids={self.mine}&type=strain&collection=any"), [self.mine])         # names of links already there
        self.assertIn('<input type="hidden" id="link-scope" value="lab+mine">', self.a.get(f"/r/{self.mine}/edit").text)
        self.assertIn('id="link-scope" value="lab">', self.b.get(f"/r/{self.lab}/edit").text)

    # ---- bulk
    def test_bulk_move_button_only_in_my_list_and_a_selection_opens_the_move_preview(self):
        self.assertIn('id="bulk-move"', self.a.get("/t/strain?c=mine").text)
        self.assertNotIn('id="bulk-move"', self.a.get("/t/strain").text)
        page = self.post(self.a, "/move/preview", {"ids": [self.mine]}).text
        self.assertIn("Move 1 strain to the lab collection</button>", page)
        self.assertIn(self.mine, page)
        self.assertEqual(self.post(self.a, "/move/preview", {"ids": []}).status_code, 404)

    def test_bulk_edit_of_personal_records_lets_the_picker_offer_your_own(self):
        page = self.post(self.a, "/t/strain/bulk", {"ids": [self.mine], "c": "mine"}).text
        self.assertIn('<input type="hidden" id="link-scope" value="lab+mine">', page)
        self.assertIn('id="link-scope" value="lab">', self.post(self.b, "/t/strain/bulk", {"ids": [self.lab]}).text)

    def test_a_move_of_too_many_records_stops_instead_of_moving_some(self):
        many = [f"ST-P{n}" for n in range(1, 260)]
        r = self.post(self.a, "/move/preview", {"ids": many})
        self.assertEqual(r.status_code, 400)
        self.assertIn("259 records are selected; at most 200", self.text(r))

    def test_trying_a_formula_uses_the_newest_lab_record_not_a_personal_one(self):
        r = self.client("root").get("/admin/types/strain/preview", params={"expr": "name"}).json()
        self.assertEqual(r["record"], self.lab)

    # ---- the type settings
    def test_the_type_settings_page_has_the_personal_collections_choice(self):
        root = self.client("root")
        page = root.get("/admin/types/strain").text
        self.assertRegex(page, r'<select name="perm_personal">.*?<option value="all" selected>')
        r = self.post(root, "/admin/types/strain/settings", {"name": "Strain", "color": "", "icon": "", "perm_personal": "admin", "columns": ["id", "name"]})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.store.schemas["strain"]["permissions"], {"personal": "admin"})
        self.post(root, "/admin/types/strain/settings", {"name": "Strain", "color": "", "icon": "", "perm_personal": "", "columns": ["id", "name"]})
        self.assertNotIn("permissions", self.store.schemas["strain"])


class PersonalNumbers(Base):
    """Each person counts their own personal records (design 17.2): Alice's ST-P1 and Bob's ST-P1 are different records. People see
    ST-P1 (yours) or ST-P1[Bob B] (someone else's); the key with the owner is what addresses and links carry."""

    @staticmethod
    def text(r):
        return html.unescape(r.text)

    def setUp(self):
        super().setUp()
        root = self.users.get("root")
        self.store.save_schema("strain", dict(STRAIN, permissions={"personal": "all"}), root)
        self.store.save_schema("plasmid", dict(PLASMID, permissions={"personal": "all"}), root)
        self.alice, self.bob = self.users.get("alice"), self.users.get("bob")
        self.ka = self.store.create("strain", self.alice, "zebra", {}, personal=True, notes="made like ST-P1 and see PL-P1")
        self.kb = self.store.create("strain", self.bob, "okapi", {}, personal=True, notes="the twin of ST-P1")
        self.pa = self.store.create("plasmid", self.alice, "pa", {}, personal=True)
        self.a, self.b = self.client("alice"), self.client("bob")

    def test_both_start_at_one_and_the_keys_differ(self):
        self.assertEqual((self.ka, self.kb), (f"ST-P1~{self.alice['uid']}", f"ST-P1~{self.bob['uid']}"))
        self.assertEqual(self.store.create("strain", self.alice, "second", {}, personal=True), f"ST-P2~{self.alice['uid']}")

    def test_lists_show_short_ids_and_label_other_peoples(self):
        mine = self.a.get("/t/strain?c=mine").text
        self.assertIn(">ST-P1<", mine)
        self.assertIn(f'data-href="/r/{self.ka}"', mine)                                    # the row opens the key
        self.assertEqual(leaked_keys(mine), [])
        bobs = self.a.get(f"/t/strain?c={self.bob['uid']}").text
        self.assertIn(">ST-P1[Bob B]<", bobs)
        self.assertEqual(leaked_keys(bobs), [])
        self.assertIn(">ST-P1[Bob B]<", self.a.get("/?q=okapi").text)
        self.assertIn(">ST-P1[Alice A]<", self.b.get("/?q=zebra").text)

    def test_no_page_shows_a_key(self):
        for c, path in [(self.a, "/"), (self.a, f"/r/{self.ka}"), (self.a, f"/r/{self.ka}/edit"), (self.a, f"/r/{self.kb}"), (self.b, f"/r/{self.ka}"),
                        (self.a, "/t/strain?c=mine"), (self.a, f"/t/strain?c={self.bob['uid']}"), (self.client("root"), "/?q=ST-P1"), (self.a, f"/move?ids={self.ka}"),
                        (self.client("root"), "/admin/users"), (self.client("root"), "/admin/users/alice/entries")]:
            r = c.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertEqual(leaked_keys(r.text), [], path)
        for path, data in (("/t/strain/bulk", {"ids": [self.ka], "c": "mine"}), ("/move/preview", {"ids": [self.ka]})):
            r = self.post(self.a, path, data)
            self.assertEqual(r.status_code, 200, path)
            self.assertEqual(leaked_keys(r.text), [], path)
        r = self.post(self.a, "/t/strain/bulk/apply", {"ids": [self.ka], "action": "archive"})            # the result page names the records
        self.assertEqual(leaked_keys(r.text), [])

    def test_a_short_id_in_the_address_is_yours_or_a_search(self):
        r = self.a.get("/r/ST-P1")
        self.assertEqual((r.status_code, r.headers["location"]), (302, f"/r/{self.ka}"))
        self.assertEqual(self.b.get("/r/ST-P1").headers["location"], f"/r/{self.kb}")
        r = self.client("root").get("/r/ST-P1")                                              # nobody's: whose is it? the search lists both
        self.assertEqual(r.headers["location"], "/?q=ST-P1")
        page = self.text(self.client("root").get("/?q=ST-P1"))
        self.assertIn(">ST-P1[Alice A]<", page)
        self.assertIn(">ST-P1[Bob B]<", page)

    def test_the_id_jump_goes_to_your_own_record(self):
        r = self.a.get("/?q=ST-P1")
        self.assertEqual((r.status_code, r.headers["location"]), (303, f"/r/{self.ka}"))
        self.assertEqual(self.b.get("/?q=st-p1").headers["location"], f"/r/{self.kb}")

    def test_a_short_id_in_notes_links_to_the_owners_record_and_is_labelled_for_the_reader(self):
        own = self.text(self.a.get(f"/r/{self.ka}"))
        self.assertIn(f'<a class="rid" href="/r/{self.ka}">ST-P1</a>', own)                  # Alice's note: her own
        self.assertIn(f'<a class="rid" href="/r/{self.pa}">PL-P1</a>', own)
        theirs = self.text(self.b.get(f"/r/{self.ka}"))
        self.assertIn(f'<a class="rid" href="/r/{self.ka}">ST-P1[Alice A]</a>', theirs)      # Bob reads Alice's note: hers, so labelled
        bobs_note = self.text(self.b.get(f"/r/{self.kb}"))
        self.assertIn(f'<a class="rid" href="/r/{self.kb}">ST-P1</a>', bobs_note)

    def test_a_short_id_typed_in_a_link_field_is_stored_as_the_key_and_the_picker_labels_it(self):
        st = self.store.create("strain", self.alice, "linker", {}, personal=True)
        r = self.post(self.a, f"/r/{st}/edit", {"name": "linker", "f_plasmid": "PL-P1", "version": self.store.records[st]["_version"]})
        self.assertEqual(r.status_code, 303, r.text[:300])
        self.assertEqual(self.store.records[st]["fields"]["plasmid"], [self.pa])
        page = self.a.get(f"/r/{st}").text
        self.assertIn(f'href="/r/{self.pa}"', page)
        self.assertEqual(leaked_keys(page), [])
        form = self.a.get(f"/r/{st}/edit").text
        self.assertIn(f'data-owner="{self.alice["uid"]}"', form)
        api = self.a.get("/api/records", params={"ids": "PL-P1", "collection": "any"}).json()["results"]
        self.assertEqual([(d["id"], d["label"]) for d in api], [(self.pa, "PL-P1")])
        bobs = self.b.get("/api/records", params={"ids": self.ka, "collection": "any"}).json()["results"]
        self.assertEqual([(d["id"], d["label"]) for d in bobs], [(self.ka, "ST-P1[Alice A]")])

    def test_the_history_names_the_record_as_people_read_it(self):
        page = self.text(self.b.get(f"/r/{self.ka}"))
        self.assertRegex(re.sub(r"<[^>]+>", " ", page), r"ST-P1\[Alice A\]\s*: create by Alice A")
        self.assertNotIn(self.alice["uid"], re.sub(r'(href|data-href|value|action|data-owner)="[^"]*"', "", page))        # no user ID shows either

    def test_an_export_writes_your_own_ids_short_and_others_with_their_owner(self):
        r = self.post(self.a, "/t/strain/export/download", {"ids": [self.ka, self.kb], "col": ["id", "name"]})
        self.assertEqual(r.text.splitlines()[1:], ["ST-P1\tzebra", f"{self.kb}\tokapi"])
        self.assertEqual(self.store.resolve("ST-P1", self.alice["uid"]), self.ka)                # an import reads the short ID as yours


class ImportIntoMyCollection(Base):
    """A table imported into one's own personal collection, by an administrator or anyone else the type allows."""
    def setUp(self):
        super().setUp()
        root = self.users.get("root")
        self.store.save_schema("strain", dict(STRAIN, permissions={"personal": "all"}), root)
        self.lab = self.create("alice", name="in the lab", genotype="g")
        self.root = self.client("root")
        self.text = "name\tgenotype\nfirst\tg1\nsecond\tg2\n"

    def step(self, path, **extra):
        return self.post(self.root, f"/t/strain/import/{path}", {"text": self.text, "map_0": "name", "map_1": "genotype", **extra})

    def test_the_upload_page_offers_the_collection_only_where_it_applies(self):
        self.assertIn('name="collection" value="mine"', self.root.get("/t/strain/import").text)
        self.assertNotIn('name="collection"', self.root.get("/t/plasmid/import").text)

    def test_importing_into_my_collection_creates_personal_records_only(self):
        preview = self.step("preview", collection="mine").text
        self.assertIn('<input type="hidden" name="collection" value="mine">', preview)
        r = self.step("apply", collection="mine")
        self.assertEqual(r.status_code, 303)
        root = self.users.get("root")["uid"]
        self.assertEqual(sorted(i for i in self.store.records if i.startswith("ST-P")), [f"ST-P1~{root}", f"ST-P2~{root}"])
        self.assertEqual(self.store.records[f"ST-P1~{root}"]["created_by"], root)
        self.assertEqual([i for i in self.store.records if i.startswith("ST-") and "P" not in i], [self.lab])    # the lab collection is untouched

    def test_the_two_collections_are_checked_separately(self):
        self.step("apply", collection="mine")
        page = self.step("preview", collection="mine").text
        self.assertIn("first", page)                                                          # an existing personal name is a duplicate warning there
        text = f"id\tname\nST-P1~{self.uid('root')}\tchanged\n"
        r = self.post(self.root, "/t/strain/import/preview", {"text": text, "map_0": "id", "map_1": "name", "collection": "lab"})
        self.assertIn("is in the personal collection, not the one you import into", r.text)
        text = f"id\tname\n{self.lab}\tchanged\n"
        r = self.post(self.root, "/t/strain/import/preview", {"text": text, "map_0": "id", "map_1": "name", "collection": "mine"})
        self.assertIn("is in the lab collection, not the one you import into", r.text)
        self.assertEqual(self.store.records[self.lab]["name"], "in the lab")

    def test_a_type_that_does_not_allow_it_refuses(self):
        r = self.post(self.root, "/t/plasmid/import/apply", {"text": "name\nx\n", "map_0": "name", "collection": "mine"})
        self.assertEqual(r.status_code, 403)

    def test_anyone_may_import_into_their_own_collection(self):
        alice = self.client("alice")
        self.assertIn('name="collection" value="mine"', alice.get("/t/strain/import?c=mine").text)
        self.assertRegex(alice.get("/t/strain/import?c=mine").text, r'value="mine" checked')
        self.assertIn('href="/t/strain/import?c=mine"', alice.get("/t/strain?c=mine").text)
        r = self.post(alice, "/t/strain/import/apply", {"text": self.text, "map_0": "name", "map_1": "genotype", "collection": "mine"})
        self.assertEqual(r.status_code, 303)
        mine = sorted(i for i, rec in self.store.records.items() if i.startswith("ST-P"))
        self.assertEqual(mine, [f"ST-P1~{self.uid('alice')}", f"ST-P2~{self.uid('alice')}"])
        self.assertEqual({self.store.records[i]["created_by"] for i in mine}, {self.uid("alice")})

    def test_someone_who_may_only_add_to_their_own_collection_imports_there(self):
        self.store.save_schema("strain", dict(STRAIN, permissions={"personal": "all", "create": "admin"}), self.users.get("root"))
        alice = self.client("alice")
        page = alice.get("/t/strain/import").text
        self.assertIn('<input type="hidden" name="collection" value="mine">', page)                 # no choice to make
        self.assertNotIn('value="lab"', page)
        r = self.post(alice, "/t/strain/import/apply", {"text": self.text, "map_0": "name", "map_1": "genotype"})
        self.assertEqual(r.status_code, 303)
        self.assertTrue(all(i.startswith("ST-P") for i, rec in self.store.records.items() if rec["name"] in ("first", "second")))
        r = self.post(alice, "/t/strain/import/apply", {"text": self.text, "map_0": "name", "map_1": "genotype", "collection": "lab"})
        self.assertEqual(r.status_code, 403)


class MoveToPersonalCommand(Base):
    """``shelfblazer move-to-personal``: preview without --yes, then one commit."""

    def setUp(self):
        super().setUp()
        self.store.save_schema("strain", dict(STRAIN, permissions={"personal": "all"}), self.users.get("root"))
        self.ids = [self.create("alice", name=f"s{n}") for n in range(4)]            # ST-1 .. ST-4
        self.bobs = self.create("bob", name="his")                                     # ST-5

    def run_cli(self, *args):
        import argparse, contextlib, io
        from shelfblazer_app import cli
        out = io.StringIO()
        a = argparse.Namespace(instance=self.inst, data=self.repo, username="alice", ids=list(args[:-1]) if args[-1] == "--yes" else list(args),
                               from_file=None, yes=args[-1] == "--yes")
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) if not a.yes else contextlib.nullcontext():
            cli.cmd_move_to_personal(a)
        return out.getvalue()

    def test_ranges_are_expanded(self):
        from shelfblazer_app import cli
        self.assertEqual(cli.expand_ids(["BS-15, bs-34..BS-36", "BS-38..39"]), ["BS-15", "BS-34", "BS-35", "BS-36", "BS-38", "BS-39"])

    def test_preview_then_move(self):
        out = self.run_cli("ST-1..ST-3", "ST-90")
        self.assertIn("Into the personal collection of Alice A (alice): 3 record(s) (3 strain); 1 ID(s) in the ranges do not exist", out)
        self.assertIn("ST-1 -> ST-P1", out)
        self.assertIn(self.ids[0], self.store.records)                               # a preview writes nothing
        self.run_cli("ST-1..ST-3", "--yes")
        self.store.refresh()
        a = self.users.get("alice")["uid"]
        self.assertEqual(sorted(r for r in self.store.records if r.startswith("ST-P")), [f"ST-P1~{a}", f"ST-P2~{a}", f"ST-P3~{a}"])
        r = self.client("alice").get("/r/ST-2")
        self.assertEqual((r.status_code, r.headers["location"]), (302, f"/r/ST-P2~{a}"))     # the lab ID on the tube still opens it
        short = self.client("alice").get("/r/ST-P2")                                         # and so does the short one, for its owner
        self.assertEqual((short.status_code, short.headers["location"]), (302, f"/r/ST-P2~{a}"))

    def test_someone_elses_record_stops_it(self):
        out = self.run_cli("ST-4..ST-5")
        self.assertIn(f"problem: {self.bobs} belongs to Bob B", out)
        self.assertIn(self.ids[3], self.store.records)

