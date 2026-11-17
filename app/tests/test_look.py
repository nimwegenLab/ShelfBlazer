"""The one look ("quiet notebook"): readable palette (WCAG AA), flat and dense, Auto/Day/Night control, sidebar menu, mono IDs."""
import os, re, unittest

from test_app import Base  # shared fixture: app + users + git repo in a temp dir

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shelfblazer_app", "static")
EMOJI = re.compile("[☀-➿️\U0001f300-\U0001faff]")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


def tokens(block):
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", block))


def blocks():
    css = read("style.css")
    day = re.search(r":root\{([^}]*)\}", css).group(1)
    auto_dark = re.search(r'@media \(prefers-color-scheme:dark\)\{:root:not\(\[data-appearance="day"\]\)\{([^}]*)\}', css).group(1)
    night = re.search(r':root\[data-appearance="night"\]\{([^}]*)\}', css).group(1)
    return tokens(day), tokens(auto_dark), tokens(night)


def lum(h):
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b):
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


SURFACES = ("bg", "card", "side", "hover")
PAIRS = [("fg", b) for b in SURFACES] + [("muted", b) for b in SURFACES] + [("link", b) for b in SURFACES]
PAIRS += [("warnfg", "warn"), ("danger", "card"), ("danger", "bg"), ("danger", "warn")]
PAIRS += [("warnfg", b) for b in SURFACES]                                                            # the [outdated] marker
PAIRS += [("bg", "fg")]                                                                               # primary button on pages without a type
PAIRS += [("accent-ink", "accent-soft"), ("on-ink", "accent-ink"), ("fg", "accent-soft")] + [("accent-ink", b) for b in SURFACES]    # the Classic look: one accent instead of the type colours
for n in range(1, 9):                                                                                # per-type: ink is for text and button fill
    PAIRS += [(f"h{n}-ink", f"h{n}-soft"), ("on-ink", f"h{n}-ink"), ("fg", f"h{n}-soft")] + [(f"h{n}-ink", b) for b in SURFACES]
LINE_PAIRS = [("line-strong", "bg"), ("line-strong", "card")] + [(f"h{n}", b) for n in range(1, 9) for b in ("bg", "card", "hover")] + [("accent", b) for b in ("bg", "card", "hover")]   # marks and input borders: 3:1


class TestPalette(unittest.TestCase):
    def test_every_text_pair_meets_wcag_aa_in_day_and_night(self):
        day, _, night = blocks()
        failures = []
        for mode, t in (("day", day), ("night", night)):
            for fg, bg in PAIRS:
                self.assertIn(fg, t, f"{mode}: missing --{fg}")
                self.assertIn(bg, t, f"{mode}: missing --{bg}")
                r = ratio(t[fg], t[bg])
                if r < 4.5:
                    failures.append(f"{mode}: {fg} {t[fg]} on {bg} {t[bg]} = {r:.2f}")
        self.assertEqual(failures, [], "\n" + "\n".join(failures))

    def test_marks_and_input_borders_have_enough_contrast(self):
        day, _, night = blocks()
        failures = [f"{mode}: {a} on {b} = {ratio(t[a], t[b]):.2f}" for mode, t in (("day", day), ("night", night))
                    for a, b in LINE_PAIRS if ratio(t[a], t[b]) < 3.0]       # WCAG asks 3:1 for meaningful non-text marks
        self.assertEqual(failures, [], "\n" + "\n".join(failures))

    def test_system_dark_and_forced_night_are_identical(self):
        _, auto_dark, night = blocks()
        self.assertEqual(auto_dark, night)
        self.assertGreater(len(night), 30)

    def test_night_overrides_every_hue_and_surface(self):
        day, _, night = blocks()
        for key in ["bg", "card", "side", "hover", "fg", "muted", "line", "line-strong", "link", "on-ink", "accent", "accent-ink", "accent-soft"] + \
                   [f"h{n}-{p}" for n in range(1, 9) for p in ("soft", "ink")]:
            self.assertNotEqual(day[key], night[key], f"--{key} is not overridden for night")


class TestTypeColourCss(unittest.TestCase):
    def test_all_eight_colours_defined_in_every_palette_block(self):
        for block in blocks():
            for n in range(1, 9):
                self.assertIn(f"h{n}", block, f"--h{n} missing")
        css = read("style.css")
        for n in range(1, 9):
            self.assertIn(f'[data-hue="{n}"]{{--c:var(--h{n});--c-soft:var(--h{n}-soft);--c-ink:var(--h{n}-ink);--on:var(--on-ink)}}', css)

    def test_type_colour_is_used_for_header_cards_nav_and_borders(self):
        css = read("style.css")
        for needle in (".hero{", "var(--c-soft", "border-bottom:2px solid var(--c", "nav a[aria-current]{background:var(--c-soft",
                       "background:var(--c-ink,var(--fg))", "nav a[data-hue]::before",
                       ".card{--t-bg:var(--c-soft)"):
            self.assertIn(needle, css)
        self.assertNotRegex(css, r"--h\d:#[0-9a-f]{6};[^}]*--h\d:#[0-9a-f]{6};[^}]*\bcolor:var\(--h\d\)")  # strong tones are never used as text colour


class TestFlatAndSelfContained(unittest.TestCase):
    def test_no_gradients_lifts_or_shadows(self):
        css = read("style.css")
        for banned in ("gradient(", "translate", "rotate(", "text-shadow"):
            self.assertNotIn(banned, css)
        for value in re.findall(r"box-shadow:\s*([^;}]*)", css):
            self.assertEqual(value.strip(), "none")

    def test_one_stylesheet_system_fonts_nothing_external(self):
        css = read("style.css")
        self.assertFalse(os.path.exists(os.path.join(STATIC, "themes")), "the extra looks were removed")
        self.assertIn("system-ui", css)
        self.assertNotRegex(css, r"@import|url\(|https?://")

    def test_the_quiet_notebook_look_has_no_leftovers_of_the_sunny_one(self):
        css = read("style.css")
        for gone in ('content:">_"', 'content:"# "', 'content:"## "', "nth-child(even)", "--zebra", "--idbg", "--hl:", "--navon", "--brandbg", ".logo"):
            self.assertNotIn(gone, css)
        self.assertIn("--line-strong", css)
        self.assertRegex(css, r"body\{margin:0;[^}]*font:14px/20px")
        self.assertRegex(css, r"grid-template-columns:200px minmax\(0,1fr\)")
        self.assertRegex(css, r"h1\{font-size:18px;line-height:24px;font-weight:500")
        self.assertRegex(css, r":focus-visible\{outline:2px solid var\(--link\);outline-offset:2px")
        self.assertNotIn('class="logo"', self.client_page())

    def client_page(self):
        with open(os.path.join(STATIC, "..", "templates", "base.html"), encoding="utf-8") as fh:
            return fh.read()

    def test_icon_sprite_and_licence(self):
        sprite = read("icons.svg")
        for icon in ("bacterium", "dna", "bullseye", "flask", "table-cells", "layer-group", "file-lines", "box",
                     "bars", "users", "right-from-bracket", "sun", "moon", "circle-half-stroke"):
            self.assertIn(f'<symbol id="{icon}"', sprite)
        self.assertIn("CC BY 4.0", read("LICENSE-fontawesome.txt"))
        self.assertFalse(EMOJI.search(sprite))
        self.assertRegex(read("style.css"), r"\.ico\{[^}]*fill:currentColor")


class TestAppearance(Base):
    def test_default_is_auto_and_choice_is_remembered(self):
        c = self.client("alice")
        self.assertNotIn("data-appearance", c.get("/").text)             # follows the system
        r = c.get("/appearance?name=night&next=/t/strain")
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/t/strain"))
        self.assertIn("appearance=night", r.headers["set-cookie"])
        self.assertIn('<html lang="en" data-appearance="night">', c.get("/t/strain").text)
        c.get("/appearance?name=day&next=/")
        self.assertIn('data-appearance="day"', c.get("/").text)
        c.get("/appearance?name=auto&next=/")
        self.assertNotIn("data-appearance", c.get("/").text)

    def test_control_marks_the_current_choice(self):
        c = self.client("alice")
        page = c.get("/me").text
        for label in ("Auto", "Day", "Night"):
            self.assertIn(f'aria-label="{label}"', page)
        self.assertRegex(page, r'aria-label="Auto" aria-current="true"')
        c.get("/appearance?name=night&next=/")
        self.assertRegex(c.get("/me").text, r'aria-label="Night" aria-current="true"')
        self.assertNotRegex(c.get("/me").text, r'aria-label="Auto" aria-current')

    def test_display_settings_live_on_the_account_page_not_in_the_sidebar(self):
        c = self.client("alice")
        side = re.search(r"<header.*?</header>", c.get("/t/strain").text, re.S).group(0)
        self.assertNotIn('class="modes"', side)
        self.assertNotIn('class="looks"', side)
        me = c.get("/me").text
        self.assertIn("<h2>Display</h2>", me)
        self.assertIn('class="modes"', me)
        self.assertIn('class="looks"', me)
        self.assertIn("next=/me", me)                                               # choosing returns to the account page
        r = c.get("/appearance?name=night&next=/me")
        self.assertEqual(r.headers["location"], "/me")

    def test_logged_out_pages_have_the_control_too(self):
        c = self.client()
        c.get("/appearance?name=night&next=/login")
        page = c.get("/login").text
        self.assertIn('data-appearance="night"', page)
        self.assertIn('class="modes"', page)

    def test_invalid_values_redirects_and_cookies_are_safe(self):
        c = self.client("alice")
        for bad in ("soft", "../../x", "<script>"):
            self.assertNotIn("set-cookie", c.get("/appearance", params={"name": bad, "next": "/"}).headers)
        for bad in ("//evil.example", "https://evil.example/x", "javascript:alert(1)"):
            self.assertEqual(c.get("/appearance", params={"name": "day", "next": bad}).headers["location"], "/", bad)
        c.cookies.set("appearance", "<script>")
        self.assertNotIn("data-appearance", c.get("/").text)             # a tampered cookie falls back to auto

    def test_the_old_look_switcher_is_gone(self):
        c = self.client("alice")
        self.assertEqual(c.get("/theme?name=bright").status_code, 404)
        page = c.get("/").text
        self.assertNotIn("Look:", page)
        self.assertNotIn("themes/", page)
        self.assertEqual(c.get("/static/themes/common.css").status_code, 404)

    def test_form_pages_still_work(self):
        # the form template has its own `mode` variable (new/edit); the colour setting must not collide with it
        c = self.client("alice")
        c.get("/appearance?name=night&next=/")
        page = c.get("/t/strain/new").text
        self.assertIn('data-appearance="night"', page)
        self.assertIn("New strain", page)


class TestLook(Base):
    def test_spectrum_is_the_default_and_classic_is_remembered(self):
        c = self.client("alice")
        self.assertNotIn("data-look", c.get("/").text)
        r = c.get("/look?name=classic&next=/t/strain")
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/t/strain"))
        self.assertIn("look=classic", r.headers["set-cookie"])
        self.assertIn('<html lang="en" data-look="classic">', c.get("/t/strain").text)
        c.get("/appearance?name=night&next=/")
        self.assertIn('data-appearance="night" data-look="classic"', c.get("/").text)      # independent of the colour mode
        c.get("/look?name=spectrum&next=/")
        self.assertNotIn("data-look", c.get("/").text)

    def test_control_marks_the_current_look_and_exists_when_signed_out(self):
        c = self.client("alice")
        self.assertRegex(c.get("/me").text, r'class="looks".*?<a href="/look\?name=spectrum[^"]*" aria-current="true">Spectrum</a><a href="/look\?name=classic[^"]*">Classic</a>')
        c.get("/look?name=classic&next=/")
        self.assertRegex(c.get("/me").text, r'<a href="/look\?name=classic[^"]*" aria-current="true">Classic</a>')
        self.assertIn('class="looks"', self.client().get("/login").text)

    def test_bad_values_and_redirects_are_safe(self):
        c = self.client("alice")
        for bad in ("bright", "../x", "<script>"):
            self.assertNotIn("set-cookie", c.get("/look", params={"name": bad, "next": "/"}).headers)
        for bad in ("//evil.example", "https://evil.example/x"):
            self.assertEqual(c.get("/look", params={"name": "classic", "next": bad}).headers["location"], "/")
        c.cookies.set("look", "<script>")
        self.assertNotIn("data-look", c.get("/").text)

    def test_classic_remaps_the_three_local_colours_to_the_accent_and_nothing_else_mentions_the_look(self):
        css = read("style.css")
        self.assertIn(':root[data-look="classic"],[data-look="classic"] [data-hue]{', css)
        self.assertIn("--c:var(--accent);--c-soft:var(--accent-soft);--c-ink:var(--accent-ink)", css)
        mentions = [m for m in re.findall(r"[^{}]*data-look[^{}]*\{", css)]
        self.assertLessEqual(len(mentions), 9, mentions)                                  # the remap, grey nav dots, tiles, avatars: a short list


class TestTypeColours(Base):
    def hue_on(self, page, href):
        m = re.search(rf'<a href="{re.escape(href)}" data-hue="(\d)"', page)
        self.assertTrue(m, f"no coloured nav link for {href}")
        return int(m.group(1))

    def test_default_colours_come_from_the_palette_and_differ_per_type(self):
        page = self.client("alice").get("/").text
        self.assertEqual(self.hue_on(page, "/t/strain"), 1)    # blue
        self.assertEqual(self.hue_on(page, "/t/plasmid"), 2)   # green
        self.assertIn('class="card" href="/t/strain" data-hue="1"', page)
        self.assertIn('class="card" href="/t/plasmid" data-hue="2"', page)

    def test_type_pages_carry_their_colour(self):
        self.create("alice", name="s1")
        c = self.client("alice")
        for path in ("/t/strain", "/t/strain?view=cards", "/r/ST-1", "/t/strain/new"):
            self.assertIn('<main data-hue="1">', c.get(path).text, path)
        self.assertIn('<main data-hue="2">', c.get("/t/plasmid").text)
        self.assertIn('class="typechip"', c.get("/r/ST-1").text)

    def test_colour_in_the_type_definition_overrides_the_default(self):
        self.create("alice", name="s1")
        self.store.schemas["strain"]["color"] = "teal"
        c = self.client("alice")
        page = c.get("/").text
        self.assertEqual(self.hue_on(page, "/t/strain"), 6)
        self.assertEqual(self.hue_on(page, "/t/plasmid"), 2)   # other types unchanged
        self.assertIn('class="card" href="/t/strain" data-hue="6"', page)
        for path in ("/t/strain", "/r/ST-1", "/t/strain/new"):
            self.assertIn('<main data-hue="6">', c.get(path).text, path)

    def test_every_palette_name_maps_to_its_slot(self):
        from shelfblazer.store import TYPE_COLORS
        c = self.client("alice")
        self.assertEqual(TYPE_COLORS, ("blue", "green", "orange", "pink", "purple", "teal", "red", "gold"))
        for slot, name in enumerate(TYPE_COLORS, start=1):
            self.store.schemas["strain"]["color"] = name
            self.assertEqual(self.hue_on(c.get("/").text, "/t/strain"), slot, name)

    def test_unknown_or_hex_colours_are_ignored(self):
        c = self.client("alice")
        for bad in ("#ff0000", "magenta", "", 3, None, "BLUE"):
            self.store.schemas["strain"]["color"] = bad
            self.assertEqual(self.hue_on(c.get("/").text, "/t/strain"), 1, repr(bad))   # falls back to the default
        self.assertNotIn("#ff0000", c.get("/t/strain").text)

    def test_custom_types_get_a_stable_palette_slot(self):
        import copy
        self.store.schemas["lipid"] = dict(copy.deepcopy(self.store.schemas["plasmid"]), name="Lipid", prefix="LP")
        self.store.prefix_type["LP"] = "lipid"
        c = self.client("alice")
        a = self.hue_on(c.get("/").text, "/t/lipid")
        self.assertIn(a, range(1, 9))
        self.assertEqual(a, self.hue_on(c.get("/t/strain").text, "/t/lipid"))
        self.store.schemas["lipid"]["color"] = "gold"
        self.assertEqual(self.hue_on(c.get("/").text, "/t/lipid"), 8)


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
        self.assertIn(f'<a class="rid" href="/r/{st}">{st}</a>', c.get("/t/strain").text)
        self.assertIn(f'<a class="rid" href="/r/{pl}" data-hue="2">{pl}</a>', c.get("/t/strain").text)     # a plasmid ID is green on a strain list
        self.assertIn(f'<span class="idtag">{st}</span>', c.get("/t/strain?view=cards").text)
        self.assertIn(f'<a class="rid" href="/r/{st}">{st}</a>', c.get("/?q=s1").text)
        page = c.get(f"/r/{st}").text
        self.assertIn(f'<a class="rid" href="/r/{pl}" data-hue="2">{pl}</a>', page)
        self.assertNotIn('class="rid" href="/r/ST-99"', page)
        self.assertIn(f'<a class="rid" href="/r/{st}" data-hue="1">{st}</a>', c.get(f"/r/{pl}").text)    # back-links carry the other type's colour
        self.assertIn(f'<code class="rid">{st}</code>', page)
        self.assertIn('<input class="mono" name="f_plasmid"', c.get(f"/r/{st}/edit").text)
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
        self.assertRegex(self.client("root").get("/admin/types").text, r'class="admin" href="/admin/users" aria-current="page"')

    def test_empty_list_shows_a_friendly_state(self):
        page = self.client("alice").get("/t/strain").text
        self.assertIn('class="empty"', page)
        self.assertIn("No strains yet", page)


if __name__ == "__main__":
    unittest.main()


FULL = "Room 6035 / Freezer 1 -80 / Freezer_1_rack12_Strain_Collec / box 'Strains collec (box 2)' / G3"


class TestRowsPrefsStorageAndForm(Base):
    def test_whole_rows_are_clickable(self):
        self.create("alice", name="s1")
        c = self.client("alice")
        self.assertIn('<tr data-href="/r/ST-1">', c.get("/t/strain").text)
        self.assertIn('<tr data-href="/r/ST-1">', c.get("/?q=s1").text)
        self.assertIn('class="stretch" href="/r/ST-1"', c.get("/t/strain?view=cards").text)   # cards: the whole card is the link
        js = read("app.js")
        for needle in ('closest("tr[data-href]")', 'closest("a, button, input', "getSelection", "metaKey", "window.open"):
            self.assertIn(needle, js)
        self.assertIn("tr[data-href]{cursor:pointer}", read("style.css"))

    def test_sort_and_view_are_remembered_per_type(self):
        for n in ("beta", "alpha", "gamma"):
            self.create("alice", name=n)
        self.create("alice", "plasmid", name="pX")
        c = self.client("alice")
        r = c.get("/t/strain")
        self.assertNotIn("listprefs", r.headers.get("set-cookie", ""))              # nothing chosen, nothing stored
        r = c.get("/t/strain?sort=name&dir=desc")
        self.assertIn("listprefs=", r.headers["set-cookie"])
        page = c.get("/t/strain").text                                              # no parameters: last choice comes back
        self.assertIn('name="sort" value="name"', page)
        self.assertIn('name="dir" value="desc"', page)
        self.assertLess(page.index("gamma"), page.index("beta"))
        self.assertLess(page.index("beta"), page.index("alpha"))
        other = c.get("/t/plasmid").text                                            # other types are unaffected
        self.assertIn('name="sort" value="id"', other)
        c.get("/t/strain?view=cards")
        self.assertIn("rcards", c.get("/t/strain").text)                            # the view is remembered too
        self.assertIn("rcards", c.get("/t/strain?sort=name&dir=desc&q=").text)      # ...and survives explicit sort links
        self.assertIn("<table", c.get("/t/strain?view=table").text)
        self.assertIn("<table", c.get("/t/strain").text)
        c.get("/t/strain?sort=id&dir=asc")
        self.assertIn('name="sort" value="id"', c.get("/t/strain").text)            # a new choice replaces the old one
        self.assertIn("<table", self.client("alice").get("/t/strain").text)         # another browser starts with the defaults

    def test_remembered_values_are_validated_and_the_cookie_is_safe(self):
        self.create("alice", name="s1")
        c = self.client("alice")
        r = c.get("/t/strain?sort=name&dir=desc&view=cards")
        cookie = r.headers["set-cookie"]
        for flag in ("HttpOnly", "SameSite=lax", "Max-Age=31536000"):
            self.assertIn(flag, cookie)
        self.assertNotRegex(cookie.split(";")[0], r'[ ",]')                          # cookie-safe encoding
        c.get("/t/strain?sort=%3Cscript%3E&dir=sideways&view=3d")                    # invalid values change nothing
        self.assertNotIn("set-cookie", c.get("/t/strain?sort=nope").headers)
        page = c.get("/t/strain").text
        self.assertIn('name="sort" value="name"', page)
        self.assertIn("rcards", page)
        from urllib.parse import quote
        for junk in ("not json", quote('{"strain":{"sort":"<x>","dir":"up","view":"x"},"ghost":{"sort":"id"}}'), quote("[1,2]"), quote('{"strain":5}')):
            d = self.client("alice")
            d.cookies.set("listprefs", junk)
            r = d.get("/t/strain")
            self.assertEqual(r.status_code, 200, junk)
            self.assertIn('name="sort" value="id"', r.text)
            self.assertIn("<table", r.text)

    def test_storage_is_shown_short_with_the_full_path_available(self):
        rid = self.create("alice", name="s1", storage=FULL)
        c = self.client("alice")
        lst = c.get("/t/strain").text
        self.assertIn(">Box 2 Well G3<", lst)
        self.assertIn(f'title="{FULL}"'.replace("'", "&#39;"), lst)                  # full path as tooltip
        self.assertNotIn(f">{FULL.replace(chr(39), '&#39;')}<", lst)
        self.assertIn('class="clamp storage"', lst)                                      # kept on one line
        self.assertRegex(read("style.css"), r"\.clamp\.storage\{[^}]*white-space:nowrap")
        self.assertIn(">Box 2 Well G3<", c.get("/t/strain?view=cards").text)
        rec = c.get(f"/r/{rid}").text
        self.assertIn("Box 2 Well G3", rec)
        self.assertIn('class="muted small fullpath"', rec)
        self.assertIn("Freezer_1_rack12_Strain_Collec", rec)                         # full path stays on the record page
        self.assertIn("Freezer_1_rack12_Strain_Collec", c.get(f"/r/{rid}/edit").text)  # the form edits the stored text
        self.assertIn(rid, c.get("/?q=Freezer_1_rack12").text)                       # and it is still searchable
        self.assertEqual(self.store.records[rid]["fields"]["storage"], FULL)         # data untouched
        self.create("alice", name="s2", storage="Box 5 Well A1")                     # short text typed by a person is shown as is
        self.assertIn(">Box 5 Well A1<", c.get("/t/strain").text)

    def test_storage_sorts_naturally(self):
        for n, box in ((1, 10), (2, 2), (3, 1)):
            self.create("alice", name=f"s{n}", storage=f"Room 1 / F / r / box 'Strains collec (box {box})' / A1")
        page = self.client("alice").get("/t/strain?sort=storage&dir=asc").text
        self.assertLess(page.index("Box 1 Well"), page.index("Box 2 Well"))
        self.assertLess(page.index("Box 2 Well"), page.index("Box 10 Well"))

    def test_create_form_has_save_and_save_and_add_another(self):
        page = self.client("alice").get("/t/strain/new").text
        self.assertRegex(page, r'<button class="primary" name="action" value="save">Save</button>\s*<button name="action" value="another">Save and add another</button>')
        self.create("alice", name="s1")
        edit = self.client("alice").get("/r/ST-1/edit").text
        self.assertIn(">Save</button>", edit)
        self.assertNotIn("add another", edit)                                         # editing has a single Save

    def test_save_and_add_another_flow(self):
        c = self.client("alice")
        r = self.post(c, "/t/strain/new", {"name": "first", "action": "another"})
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/t/strain/new"))
        page = c.get("/t/strain/new").text
        self.assertRegex(page, r'Created <code class="rid">ST-1</code>')              # the new ID is confirmed on the fresh form
        self.assertEqual(self.store.records["ST-1"]["name"], "first")
        r = self.post(c, "/t/strain/new", {"name": "second", "action": "save"})
        self.assertEqual(r.headers["location"], "/r/ST-2")
        r = self.post(c, "/t/strain/new", {"name": "third"})                           # Enter key / no button value: plain save
        self.assertEqual(r.headers["location"], "/r/ST-3")
        bad = self.post(c, "/t/strain/new", {"name": "", "action": "another"})
        self.assertEqual(bad.status_code, 422)                                         # nothing is created; both buttons come back
        self.assertIn("Save and add another", bad.text)
        self.assertEqual(len(self.store.records), 3)


class TestStorageDisplayModes(Base):
    SHORT = "Box 2 Well G3"

    def set_mode(self, mode):
        for f in self.store.schemas["strain"]["fields"]:
            if f["key"] == "storage":
                if mode is None:
                    f.pop("display", None)
                else:
                    f["display"] = mode

    def setUp(self):
        super().setUp()
        self.rid = self.create("alice", name="s1", storage=FULL)
        self.c = self.client("alice")

    def test_an_old_storage_field_without_a_setting_is_short(self):
        self.set_mode(None)
        self.store.reload()                                                   # definitions from before "display": storage was short
        self.assertIn(f">{self.SHORT}<", self.c.get("/t/strain").text)
        self.assertNotIn("[outdated]", self.c.get("/t/strain").text)

    def test_outdated_shows_the_original_text_with_a_marker(self):
        self.set_mode("outdated")
        full = FULL.replace("'", "&#39;")
        for path in ("/t/strain", "/t/strain?view=cards"):
            page = self.c.get(path).text
            self.assertIn(f">{full}</span>", page, path)                      # the stored text, unabbreviated
            self.assertIn('class="outdated"', page, path)
            self.assertIn("[outdated]", page, path)
            self.assertNotIn(f">{self.SHORT}<", page, path)
        rec = self.c.get(f"/r/{self.rid}").text
        self.assertIn(full, rec)
        self.assertIn("[outdated]", rec)
        self.assertNotIn(f"{self.SHORT}", rec)
        self.assertNotIn("fullpath", rec)                                      # the original is already what is shown
        self.assertEqual(self.store.records[self.rid]["fields"]["storage"], FULL)   # data untouched
        self.assertIn(self.rid, self.c.get("/?q=Freezer_1_rack12").text)       # still searchable

    def test_full_shows_the_original_without_a_marker(self):
        self.set_mode("full")
        page = self.c.get("/t/strain").text
        self.assertIn(FULL.replace("'", "&#39;"), page)
        self.assertNotIn("[outdated]", page)

    def test_unknown_display_shows_the_text_as_stored(self):
        for bad in ("outdated ", "Short", "", 3):
            self.set_mode(bad)
            page = self.c.get("/t/strain").text
            self.assertNotIn(f">{self.SHORT}<", page, repr(bad))
            self.assertNotIn("[outdated]", page, repr(bad))

    def test_any_text_field_can_be_a_location(self):
        f = next(f for f in self.store.schemas["strain"]["fields"] if f["key"] == "genotype")
        f["display"] = "short"
        rid = self.create("alice", name="s2", genotype=FULL)
        page = self.c.get(f"/r/{rid}").text
        self.assertIn(self.SHORT, page)
        self.assertIn('class="muted small fullpath"', page)                  # the full text underneath

    def test_marker_only_where_there_is_storage(self):
        self.set_mode("outdated")
        self.create("alice", name="no storage here")
        page = self.c.get("/t/strain").text
        self.assertEqual(page.count("[outdated]"), 1)                          # one record has storage, one does not

    def test_marker_and_css_exist(self):
        css = read("style.css")
        self.assertRegex(css, r"\.outdated\{[^}]*color:var\(--warnfg\)")
        with open(os.path.join(STATIC, "..", "templates", "list.html"), encoding="utf-8") as fh:
            self.assertIn('title="This location comes from the old database', fh.read())


class TestFormLabels(unittest.TestCase):
    def test_checkbox_and_radio_labels_stay_in_a_row(self):
        self.assertRegex(read("style.css"), r"form\.edit label\.chk[^{]*\{[^}]*flex-direction:row")
