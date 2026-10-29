import unittest

from app import sheets

CSV = """Filename,Title,Keywords,Category,Releases,Ide Konsep,AI Prompt (Siap Pakai)
adobestock_20261029_001.jpg,Glass bulb forest,"earth hour, energy, forest",5,,Ide 66: Earth Hour 2027 (27 Maret 2027),Conceptual 3D render
adobestock_20261029_002.jpg,Second bulb,"earth hour, bulb",5,,Ide 66: Earth Hour 2027 (27 Maret 2027),Another render
adobestock_20261029_003.jpg,Audiologist,"hearing, audiology",16,,Ide 67: Hari Pendengaran Sedunia (3 Maret 2027),Close up
,,,5,,Ide 67: kosong,Tanpa filename harus dibuang
"""


class SheetTests(unittest.TestCase):
    def setUp(self):
        self.items = sheets.parse_csv(CSV)

    def test_skips_rows_without_filename(self):
        self.assertEqual(len(self.items), 3)

    def test_parses_core_columns(self):
        first = self.items[0]
        self.assertEqual(first["filename"], "adobestock_20261029_001.jpg")
        self.assertEqual(first["category"], "5")
        self.assertIn("Earth Hour", first["concept"])
        self.assertTrue(first["prompt"])

    def test_groups_by_concept_number(self):
        groups = sheets.group_by_concept(self.items)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["key"], "Ide 66")
        self.assertEqual(len(groups[0]["items"]), 2)

    def test_event_label_strips_prefix(self):
        groups = sheets.group_by_concept(self.items)
        self.assertTrue(groups[0]["label"].startswith("Earth Hour 2027"))

    def test_extract_sheet_id(self):
        url = "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit?usp=sharing"
        self.assertEqual(sheets.extract_sheet_id(url), "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789")

    def test_extract_sheet_id_rejects_garbage(self):
        with self.assertRaises(ValueError):
            sheets.extract_sheet_id("bukan link")


if __name__ == "__main__":
    unittest.main()
