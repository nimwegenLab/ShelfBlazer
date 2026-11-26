"""The one look ("quiet notebook"): readable palette (WCAG AA), type colours, flat and self-contained CSS, the Auto/Day/Night
and Spectrum/Classic controls. Page markup is tested in test_menu_markup_app.py and test_list_display_app.py."""
import os, re, unittest

from test_app import EMOJI, STATIC, Base, read, tags  # shared fixture: app + users + git repo in a temp dir


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
        self.assertRegex(css, r"h1\{font-size:16px;line-height:22px;font-weight:500")       # never larger than the site name (16px/600)
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
        c.cookies.delete("appearance")                                   # newer httpx keeps both cookies and sends the first
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
        def current(page):
            return [a["_text"].strip() for a in tags(page, "a", href=re.compile(r"^/look\?name=")) if a.get("aria-current") == "true"]
        me = c.get("/me").text
        self.assertTrue(tags(me, None, class_="looks"))
        self.assertEqual(current(me), ["Spectrum"])
        self.assertTrue(tags(me, "a", href=re.compile(r"^/look\?name=classic"), _text="Classic"))
        c.get("/look?name=classic&next=/")
        self.assertEqual(current(c.get("/me").text), ["Classic"])
        self.assertIn('class="looks"', self.client().get("/login").text)

    def test_bad_values_and_redirects_are_safe(self):
        c = self.client("alice")
        for bad in ("bright", "../x", "<script>"):
            self.assertNotIn("set-cookie", c.get("/look", params={"name": bad, "next": "/"}).headers)
        for bad in ("//evil.example", "https://evil.example/x"):
            self.assertEqual(c.get("/look", params={"name": "classic", "next": bad}).headers["location"], "/")
        c.cookies.delete("look")
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

    @staticmethod
    def tile_hue(page, slug):
        """The colour slot of a type's Home tile (the tile is a div; its main link opens the list)."""
        return re.search(r'<div class="card" data-hue="(\d+)">(?:(?!<div class="card").)*?<a class="l" href="/t/%s">' % slug, page, re.S).group(1)

    def test_default_colours_come_from_the_palette_and_differ_per_type(self):
        page = self.client("alice").get("/").text
        self.assertEqual(self.hue_on(page, "/t/strain"), 1)    # blue
        self.assertEqual(self.hue_on(page, "/t/plasmid"), 2)   # green
        self.assertEqual((self.tile_hue(page, "strain"), self.tile_hue(page, "plasmid")), ("1", "2"))

    def test_type_pages_carry_their_colour(self):
        self.create("alice", name="s1")
        c = self.client("alice")
        for path in ("/t/strain", "/t/strain?view=cards", "/r/ST-1", "/t/strain/new"):
            self.assertIn('<main data-hue="1">', c.get(path).text, path)
        self.assertIn('<main data-hue="2">', c.get("/t/plasmid").text)
        page = c.get("/r/ST-1").text
        self.assertTrue(tags(page, "a", class_="crumb", href="/t/strain", _text="‹ Strains"))      # a quiet way back to the list, not a pill
        self.assertNotIn('class="typechip"', page[page.index("<main"):page.index("</h1>")])

    def test_version_sits_right_of_the_name_in_the_sidebar_header(self):
        head = re.search(r'<div class="side-head">.*?</div>', self.client("alice").get("/").text, re.S).group(0)
        self.assertTrue(tags(head, "span", class_="ver", _text=re.compile(r"^v\S+$")))
        self.assertLess(head.index('class="brand"'), head.index('class="ver"'))                    # right after the name, before the menu button
        self.assertLess(head.index('class="ver"'), head.index("menu-toggle"))
        self.assertNotIn("muted small ver", self.client("alice").get("/").text)               # no longer at the foot of the bar
        self.assertNotIn('class="ver"', self.client().get("/login").text)                      # not shown to people who are not signed in

    def test_colour_in_the_type_definition_overrides_the_default(self):
        self.create("alice", name="s1")
        self.store.schemas["strain"]["color"] = "teal"
        c = self.client("alice")
        page = c.get("/").text
        self.assertEqual(self.hue_on(page, "/t/strain"), 6)
        self.assertEqual(self.hue_on(page, "/t/plasmid"), 2)   # other types unchanged
        self.assertEqual(self.tile_hue(page, "strain"), "6")
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
        self.store.schemas["strain"].pop("color")
        derived = self.hue_on(c.get("/").text, "/t/strain")                              # no colour: a slot derived from the name
        for bad in ("#ff0000", "magenta", "", 3, None, "BLUE"):
            self.store.schemas["strain"]["color"] = bad
            self.assertEqual(self.hue_on(c.get("/").text, "/t/strain"), derived, repr(bad))   # falls back to the derived slot
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


class TestFormLabels(unittest.TestCase):
    def test_checkbox_and_radio_labels_stay_in_a_row(self):
        self.assertRegex(read("style.css"), r"form\.edit label\.chk[^{]*\{[^}]*flex-direction:row")
