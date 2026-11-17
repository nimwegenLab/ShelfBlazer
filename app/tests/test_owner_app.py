"""An administrator can hand a record to someone else from its edit form; nobody else can."""
import unittest

from test_app import Base


class ChangeOwner(Base):
    def setUp(self):
        super().setUp()
        self.rid = self.create("alice", name="mine")
        self.admin, self.alice, self.bob = self.client("root"), self.client("alice"), self.client("bob")

    def edit(self, c, **extra):
        return self.post(c, f"/r/{self.rid}/edit", {"name": "mine", "version": self.version(self.rid), **extra})

    def test_admin_form_offers_the_owner_box_with_the_current_owner_selected(self):
        page = self.admin.get(f"/r/{self.rid}/edit").text
        self.assertIn('<select name="owner">', page)
        self.assertRegex(page, r'<option value="%s" selected>' % self.uid("alice"))

    def test_other_people_see_no_owner_box(self):
        self.assertNotIn('name="owner"', self.alice.get(f"/r/{self.rid}/edit").text)

    def test_admin_changes_the_owner_in_one_commit(self):
        r = self.edit(self.admin, owner=self.uid("bob"))
        self.assertEqual(r.status_code, 303, r.text[:300])
        self.assertEqual(self.store.records[self.rid]["created_by"], self.uid("bob"))
        self.assertIn("owner", self.store.history(self.rid)[0]["subject"])
        self.assertEqual(self.store.can_edit(self.users.get("bob"), self.store.records[self.rid]), True)

    def test_username_is_accepted_and_the_same_owner_is_a_no_change(self):
        self.edit(self.admin, owner="bob")
        self.assertEqual(self.store.records[self.rid]["created_by"], self.uid("bob"))
        n = len(self.store.history(self.rid))
        self.edit(self.admin, owner=self.uid("bob"))
        self.assertEqual(self.store.records[self.rid]["created_by"], self.uid("bob"))
        self.assertLessEqual(len(self.store.history(self.rid)), n + 1)

    def test_empty_owner_keeps_the_current_one(self):
        self.edit(self.admin, owner="")
        self.assertEqual(self.store.records[self.rid]["created_by"], self.uid("alice"))

    def test_unknown_owner_is_refused_and_nothing_is_saved(self):
        r = self.edit(self.admin, owner="nobody-here", f_dummy="")
        self.assertEqual(r.status_code, 422)
        self.assertIn("unknown user", r.text)
        self.assertEqual(self.store.records[self.rid]["created_by"], self.uid("alice"))

    def test_a_non_admin_owner_cannot_give_the_record_away(self):
        self.edit(self.alice, owner=self.uid("bob"))                  # the posted box is ignored: saved, owner unchanged
        self.assertEqual(self.store.records[self.rid]["created_by"], self.uid("alice"))
        from shelfblazer.store import Forbidden
        with self.assertRaises(Forbidden):                            # and the store itself refuses
            self.store.update(self.rid, self.users.get("alice"), "mine", {}, "", self.version(self.rid), owner=self.uid("bob"))
        self.assertEqual(self.store.records[self.rid]["created_by"], self.uid("alice"))


if __name__ == "__main__":
    unittest.main()
