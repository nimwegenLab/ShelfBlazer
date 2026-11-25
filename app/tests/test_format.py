"""Storage display abbreviation (pure function)."""
import unittest

from shelfblazer_app.format import short_storage as f, storage_chunks

ROOM = "Room 6035 / Freezer 1 -80 / "


class TestShortStorage(unittest.TestCase):
    def test_the_requested_pattern(self):
        self.assertEqual(f(ROOM + "Freezer_1_rack12_Strain_Collec / box 'Strains collec (box 2)' / G3"), "Box 2 Well G3")

    def test_box_names_with_number_or_letter(self):
        for name, label in (("Plasmid collection Box1", "Box 1"), ("Plasmidcollection Box2", "Box 2"), ("plasmid collection box3", "Box 3"),
                            ("F1_rack11_BoxE", "Box E"), ("Strains collec (box 10)", "Box 10")):
            self.assertEqual(f(f"{ROOM}rack / box '{name}' / A1"), f"{label} Well A1", name)

    def test_other_box_names_are_used_as_they_are(self):
        self.assertEqual(f(ROOM + "rack / box 'Genestrands_primers_arantxa' / G7"), "Genestrands_primers_arantxa Well G7")
        self.assertEqual(f(ROOM + "rack / box 'box collection' / A1"), "box collection Well A1")     # "box c..." is not a box letter
        self.assertEqual(f(ROOM + "rack / box 'empty'"), "empty")                                      # plates: no well

    def test_no_position_and_several_wells(self):
        self.assertEqual(f(ROOM + "rack / box 'Strains collec (box 8)'"), "Box 8")
        self.assertEqual(f(ROOM + "rack / box 'FISH' / B2,B3"), "FISH Wells B2, B3")
        self.assertEqual(f(ROOM + "rack / box 'FISH' / B2,B3,B4,B5,C2,C3"), "FISH Wells B2 … C3 (6)")

    def test_several_locations_show_the_freezer_only_when_it_differs(self):
        same = "Room 6020 / -20C Freezer / d / box 'Plasmid collection Box1' / A4; also: Room 6020 / -20C Freezer / d / box 'Plasmidcollection Box2' / A4"
        self.assertEqual(f(same), "Box 1 Well A4; also Box 2 Well A4")
        diff = ROOM + "r / box 'Strains collec (box 2)' / D5; also: Room 6035 / Freezer 2 -80 / r / box 'Freezer2 (box 1)' / A3"
        self.assertEqual(f(diff), "Box 2 Well D5 (Freezer 1 -80); also Box 1 Well A3 (Freezer 2 -80)")
        self.assertEqual(f(ROOM + "r / box 'Strains collec (box 1)'; also: box ''"), "Box 1")        # empty second location is dropped

    def test_pieces_for_wrapping(self):
        self.assertEqual(storage_chunks("Box 2 Well C5 (Freezer 1 -80); also Box 2 Well A7 (Freezer 2 -80)"),
                         ["Box 2 Well C5", "(Freezer 1 -80);", "also Box 2 Well A7", "(Freezer 2 -80)"])
        self.assertEqual(storage_chunks("Box 1 Well A4; also Box 2 Well A4"), ["Box 1 Well A4;", "also Box 2 Well A4"])
        self.assertEqual(storage_chunks("Box 2 Well D3"), ["Box 2 Well D3"])
        self.assertEqual(storage_chunks(""), [])

    def test_anything_else_is_left_alone(self):
        for text in ("legacy box 111 (missing)", "Box 2 Well G3", "freezer 3, top shelf", "", None):
            self.assertEqual(f(text), text)

    def test_never_raises_on_odd_input(self):
        for text in ("/ / /", "box '", "box ''", " / box 'x' / ", "; also: ; also: ", "box 'a' / ", "x" * 10000):
            self.assertIsInstance(f(text), str)


if __name__ == "__main__":
    unittest.main()
