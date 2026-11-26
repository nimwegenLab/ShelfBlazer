"""The Help page (/help) and the administrators' editor for the lab's own section (/admin/help)."""
import html, os, re

from test_app import PW, Base, PLASMID, STRAIN
from test_mcp_app import McpBase

TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shelfblazer_app", "templates")
BOOK = {"name": "Book", "prefix": "BK", "list_columns": ["id", "name"], "fields": [
    {"key": "name", "label": "Name", "type": "text", "required": True},
    {"key": "author", "label": "Author", "type": "text"}]}
TOOL = {"name": "Tool", "prefix": "TL", "list_columns": ["id", "name"], "fields": [
    {"key": "name", "label": "Name", "type": "text", "required": True},
    {"key": "book", "label": "Manual", "type": "ref", "to": "book"}]}


def anchors(page):
    return set(re.findall(r'\bid="([^"]+)"', page))


class Help(Base):
    def setUp(self):
        super().setUp()
        self.root_user = self.users.get("root")
        self.a, self.r = self.client("alice"), self.client("root")

    def text(self, c, path="/help"):
        r = c.get(path)
        self.assertEqual(r.status_code, 200, r.text[:200])
        return html.unescape(r.text)

    def test_the_page_needs_a_sign_in(self):
        r = self.client().get("/help")
        self.assertEqual(r.status_code, 303)
        self.assertIn("/login", r.headers["location"])

    def test_the_menu_has_a_help_entry_above_the_person(self):
        page = self.a.get("/").text
        self.assertRegex(page, r'<div class="side-foot"><a href="/help" class="helpnav">.*?Help</a><div class="whorow">')
        self.assertIn('class="helpnav" aria-current="page"', self.a.get("/help").text)

    def test_every_help_link_in_the_templates_has_its_section(self):
        wanted = set()
        for name in os.listdir(TEMPLATES):
            with open(os.path.join(TEMPLATES, name), encoding="utf-8") as fh:
                wanted |= set(re.findall(r'/help#([a-z]+)', fh.read()))
        self.assertTrue(wanted >= {"duplicates", "editing", "personal", "moving", "importing", "scan"})
        self.store.save_schema("strain", dict(STRAIN, permissions={"personal": "all"}, scan_fields=["genotype"]), self.root_user)
        page = self.text(self.a)
        from fastapi.testclient import TestClient
        from shelfblazer_app.web import create_app
        with_mcp = TestClient(create_app(self.repo, self.inst, mcp=True), follow_redirects=False)       # the assistant's section
        self.post(with_mcp, "/login", {"username": "alice", "password": PW}, anon=True)
        page += self.text(with_mcp)
        self.assertEqual(sorted(wanted - anchors(page)), [])

    def test_the_table_of_contents_is_not_a_nav_element(self):
        page = self.text(self.a)           # the sidebar's "nav" CSS (flex links, 28px rows) would restyle it
        self.assertIn('<div class="toc" role="navigation"', page)
        self.assertEqual(page.count("<nav"), 1)                        # only the sidebar's

    def test_the_table_of_contents_only_links_to_sections_that_exist(self):
        for page in (self.text(self.a), self.text(self.r)):
            toc = re.search(r'<div class="toc".*?</div>', page, re.S).group(0)
            self.assertEqual(sorted(set(re.findall(r'href="#([a-z-]+)"', toc)) - anchors(page)), [])

    def test_sections_follow_what_the_instance_has_on(self):
        page = self.text(self.a)
        for gone in ("Your own collection", "Scanning barcodes", "With your AI assistant", "id=\"personal\"", "id=\"scan\"", "id=\"assistant\""):
            self.assertNotIn(gone, page)
        self.assertIn("Four things to know", page)
        self.store.save_schema("strain", dict(STRAIN, permissions={"personal": "all"}, scan_fields=["genotype"]), self.root_user)
        page = self.text(self.a)
        self.assertIn("Your own collection", page)
        self.assertIn("Scanning barcodes", page)
        self.assertIn("Five things to know", page)
        self.assertNotIn("With your AI assistant", page)

    def test_the_sequence_search_is_described_only_where_a_type_has_a_sequence(self):
        self.assertNotIn("paste at least 6 bases", self.text(self.a))
        dna = [dict(f, validate="dna") if f["key"] == "sequence" else f for f in PLASMID["fields"]]
        self.store.save_schema("plasmid", dict(PLASMID, fields=dna), self.root_user)
        self.assertIn("paste at least 6 bases", self.text(self.a))
        for slug in ("strain", "plasmid"):
            self.store.delete_type(slug, self.root_user)
        self.store.save_schema("book", BOOK, self.root_user, create=True)
        self.assertNotIn("paste at least 6 bases", self.text(self.a))

    def test_examples_come_from_the_types_of_the_instance(self):
        for slug in ("strain", "plasmid"):
            self.store.delete_type(slug, self.root_user)
        self.store.save_schema("book", BOOK, self.root_user, create=True)
        self.store.save_schema("tool", TOOL, self.root_user, create=True)
        page = self.text(self.a)
        self.assertIn("<code>BK-12</code>", page)                      # the first type this person may add to
        self.assertIn("<em>Manual</em> in a tool, pointing to a book", page)
        for word in ("strain", "plasmid", "primer", "reagent", "genotype"):
            self.assertNotIn(word, page.lower(), f'"{word}" belongs to the lab\'s own section, not to the shared text')

    def test_the_lab_section_is_shown_after_the_summary_under_its_own_title(self):
        page = self.text(self.a)
        self.assertNotIn('id="lab"', page)
        pl = self.create("alice", "plasmid", name="pLab")
        from shelfblazer import helptext
        helptext.save(self.store, f"# How our records are organised\n\n**A or B?** See {pl}.\n\n- one\n- two", self.root_user)
        page = self.text(self.a)
        self.assertRegex(page, r'<h2 id="lab">How our records are organised</h2>')
        self.assertLess(page.index('id="summary"'), page.index('id="lab"'))
        self.assertLess(page.index('id="lab"'), page.index('id="duplicates"'))
        self.assertIn(f'<a class="rid" href="/r/{pl}">{pl}</a>', page)             # IDs in the lab's text link
        self.assertIn("<li>two</li>", page)
        self.assertRegex(page, r'(?s)<div class="toc".*?<a href="#lab">How our records are organised</a>')

    def test_a_lab_text_without_a_heading_gets_a_plain_title(self):
        from shelfblazer import helptext
        helptext.save(self.store, "Ask the front desk.", self.root_user)
        self.assertRegex(self.text(self.a), r'<h2 id="lab">In this lab</h2>')

    def test_the_lab_text_cannot_inject_markup_or_show_hidden_records(self):
        from shelfblazer import helptext
        hidden = self.create("bob", name="SECRETARCHIVED")
        self.post(self.client("bob"), f"/r/{hidden}/archive")
        helptext.save(self.store, f"# T\n\n<script>alert(1)</script> {hidden}", self.root_user)
        page = self.a.get("/help").text
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn(f'href="/r/{hidden}"', page)
        self.assertNotIn("SECRETARCHIVED", page)

    def test_the_page_has_a_spine_of_numbered_parts_and_every_section_sits_in_one(self):
        self.store.save_schema("strain", dict(STRAIN, permissions={"personal": "all"}, scan_fields=["genotype"]), self.root_user)
        page = self.text(self.a)
        parts = re.findall(r'<h2 class="part" id="([^"]+)">', page)
        self.assertEqual(parts, ["p-find", "p-add", "p-change", "p-many", "personal", "account"])
        first = page.index('id="p-find"')
        self.assertEqual([m for m in re.findall(r'<h3 id="([^"]+)">', page[:first])], [])      # nothing loose between the summary and part 1
        self.assertEqual(re.findall(r'<h3 id="([^"]+)">', page),
                         ["finding", "duplicates", "adding", "importing", "ids", "links", "editing", "history", "bulk", "scan"])

    def test_headings_are_larger_than_the_text_they_head(self):
        from test_app import read
        css = read("style.css")
        body = int(re.search(r"body\{[^}]*font:(\d+)px", css).group(1))
        for rule in (r"\.help h2\{font-size:(\d+)px", r"\.help h3\{font-size:(\d+)px"):
            self.assertGreater(int(re.search(rule, css).group(1)), body, rule)

    # ---- the editor
    def test_the_editor_is_for_administrators(self):
        self.assertEqual(self.a.get("/admin/help").status_code, 403)
        self.assertEqual(self.post(self.a, "/admin/help", {"text": "# Mine", "action": "save"}).status_code, 403)
        self.assertEqual(self.client().get("/admin/help").status_code, 303)
        self.assertEqual(self.r.get("/admin/help").status_code, 200)

    def test_the_editor_starts_from_a_starting_point_when_there_is_no_text(self):
        page = self.text(self.r, "/admin/help")
        self.assertIn("There is no help text yet", page)
        self.assertIn("# How our records are organised", page)
        self.assertNotIn("config/help.md", self.store.git("ls-files"))     # nothing is saved until Save

    def test_preview_shows_the_section_and_saves_nothing(self):
        before = self.store.git("rev-list", "--count", "HEAD")
        r = self.post(self.r, "/admin/help", {"text": "# Preview me\n\nHello **there**.", "action": "preview"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("<h3>Preview me</h3>", r.text)
        self.assertIn("<strong>there</strong>", r.text)
        self.assertEqual(self.store.git("rev-list", "--count", "HEAD"), before)

    def test_saving_commits_once_shows_on_the_help_page_and_can_be_removed(self):
        before = int(self.store.git("rev-list", "--count", "HEAD"))
        r = self.post(self.r, "/admin/help", {"text": "# Our rules\n\nBe kind.", "action": "save"})
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/admin/help"))
        self.assertEqual(int(self.store.git("rev-list", "--count", "HEAD")), before + 1)
        self.assertIn("Our rules", self.text(self.a))
        self.assertIn("Help text saved", self.r.get("/admin/help").text)
        self.post(self.r, "/admin/help", {"text": "  ", "action": "save"})
        self.assertNotIn('id="lab"', self.text(self.a))

    def test_a_text_that_is_too_long_comes_back_with_the_text_kept(self):
        r = self.post(self.r, "/admin/help", {"text": "# T\n" + "x" * 20001, "action": "save"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("at most 20000", r.text)
        self.assertIn("xxxxxxxx", r.text)

    def test_a_save_without_the_form_token_is_refused(self):
        self.assertEqual(self.r.post("/admin/help", data={"text": "# T", "action": "save"}).status_code, 403)

    def test_the_admin_pages_link_to_the_editor(self):
        for path in ("/admin/users", "/admin/types", "/admin/types/strain", "/admin/help"):
            self.assertIn('href="/admin/help"' if path != "/admin/help" else "<strong>Help text</strong>", self.r.get(path).text, path)


class HelpWithAssistant(McpBase):
    def test_the_assistant_is_the_first_point_of_the_summary_and_has_its_own_section(self):
        a = self.client("alice")
        page = html.unescape(a.get("/help").text)
        self.assertNotIn("helplead", page)                                            # no preamble above the summary
        first = re.search(r'<ol class="five">\s*<li>(.*?)</li>', page, re.S).group(1)
        self.assertIn("Search in the app, add with your AI assistant.", first)
        second = re.findall(r'<li>(.*?)</li>', re.search(r'<ol class="five">.*?</ol>', page, re.S).group(0), re.S)[1]
        self.assertIn("Search before you add.", second)
        self.assertIn("Five things to know", page)
        self.assertIn('<h3 id="assistant">With your AI assistant</h3>', page)
        self.assertLess(page.index('id="p-add"'), page.index('id="assistant"'))       # it belongs to "Add something"
        section = re.search(r'(?s)<h3 id="assistant">.*?(?=<h3)', page).group(0)
        self.assertRegex(section, r'<a class="btn" href="/me/assistant">.*?Set up your AI assistant</a>')     # the steps live there
        for detail in ("claude mcp add", "/mcp<", "days by default"):                   # short: no setup details here
            self.assertNotIn(detail, section)
        self.assertLess(len(section), 1500)
        toc = re.search(r'<div class="toc".*?</div>', page, re.S).group(0)
        self.assertEqual(sorted(set(re.findall(r'href="#([a-z-]+)"', toc)) - anchors(page)), [])

    def test_the_account_page_has_the_tokens_the_help_points_to(self):
        page = self.client("alice").get("/me/assistant").text
        self.assertIn('action="/me/tokens#connect"', page)                                         # where the help sends people: the steps
        self.assertIn('<h2 class="part">Create a token for this computer</h2>', page)
