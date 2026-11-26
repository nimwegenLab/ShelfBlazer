"""The lab's own section of the Help page: config/help.md, its title, and who may change it (shelfblazer/helptext.py)."""
import os

from shelfblazer import helptext
from shelfblazer.store import Forbidden, ValidationError

from helpers import ALICE, ROOT, Base


class HelpText(Base):
    def test_no_file_is_an_empty_text(self):
        self.assertEqual(helptext.read(self.store), "")

    def test_the_first_heading_is_the_title(self):
        self.assertEqual(helptext.split("# How we work\n\nFirst.\n\nSecond."), ("How we work", "First.\n\nSecond."))
        self.assertEqual(helptext.split("\n\n# Title after blank lines\nBody"), ("Title after blank lines", "Body"))

    def test_no_heading_no_title(self):
        self.assertEqual(helptext.split("Just text.\n\n# Later heading"), (None, "Just text.\n\n# Later heading"))
        self.assertEqual(helptext.split("## A second-level heading first"), (None, "## A second-level heading first"))
        self.assertEqual(helptext.split(""), (None, ""))

    def test_saving_is_one_commit_and_reads_back(self):
        before = self.commits()
        helptext.save(self.store, "# Rules\r\n\r\nOne.\r\n", ROOT)
        self.assertEqual(self.commits(), before + 1)
        self.assertEqual(self.head_message(), "help text: change")
        self.assertEqual(helptext.read(self.store), "# Rules\n\nOne.\n")
        self.assertTrue(os.path.exists(os.path.join(self.repo, "config", "help.md")))
        self.assertEqual(self.store.git("status", "--porcelain").strip(), "")

    def test_saving_the_same_text_again_commits_nothing(self):
        helptext.save(self.store, "# Rules\n\nOne.", ROOT)
        before = self.commits()
        helptext.save(self.store, "# Rules\n\nOne.", ROOT)
        self.assertEqual(self.commits(), before)

    def test_an_empty_text_removes_the_section(self):
        helptext.save(self.store, "# Rules\n\nOne.", ROOT)
        helptext.save(self.store, "  \n", ROOT)
        self.assertEqual(self.head_message(), "help text: remove")
        self.assertEqual(helptext.split(helptext.read(self.store)), (None, ""))

    def test_only_administrators_save(self):
        with self.assertRaises(Forbidden):
            helptext.save(self.store, "# Mine", ALICE)
        self.assertEqual(helptext.read(self.store), "")

    def test_a_text_that_is_too_long_is_refused_and_nothing_is_written(self):
        before = self.commits()
        with self.assertRaises(ValidationError):
            helptext.save(self.store, "x" * (helptext.MAX_CHARS + 1), ROOT)
        self.assertEqual((self.commits(), helptext.read(self.store)), (before, ""))

    def test_a_nul_character_is_dropped(self):
        helptext.save(self.store, "# T\n\nA\x00B", ROOT)
        self.assertNotIn("\x00", helptext.read(self.store))
