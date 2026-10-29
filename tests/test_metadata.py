import os
import unittest

from PIL import Image

from app import metadata

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp")
ITEM = {
    "title": "Glass lightbulb containing miniature forest, Earth Hour concept",
    "keywords": "earth hour, energy conservation, glass lightbulb, forest inside bulb, climate action",
    "concept": "Ide 66: Earth Hour 2027, Hemat Energi",
}


class MetadataTests(unittest.TestCase):
    def setUp(self):
        os.makedirs(TMP, exist_ok=True)
        self.path = os.path.join(TMP, "test.jpg")
        Image.new("RGB", (64, 64), (30, 40, 50)).save(self.path, format="JPEG")

    def tearDown(self):
        for f in ("test.jpg",):
            p = os.path.join(TMP, f)
            if os.path.exists(p):
                os.remove(p)

    def test_inject_then_verify(self):
        self.assertTrue(metadata.inject(self.path, ITEM))
        result = metadata.verify(self.path, ITEM)
        self.assertTrue(result["xmp"], "XMP tidak tertanam")
        self.assertTrue(result["iptc"], "IPTC tidak tertanam")
        self.assertTrue(result["title"], "Judul tidak tertanam")
        self.assertGreaterEqual(result["keywords_found"], 3)
        self.assertTrue(result["ok"])

    def test_file_stays_valid_jpeg(self):
        metadata.inject(self.path, ITEM)
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(2), b"\xff\xd8")
        with Image.open(self.path) as im:
            self.assertEqual(im.size, (64, 64))

    def test_verify_flags_missing_file(self):
        self.assertFalse(metadata.verify(os.path.join(TMP, "tidak-ada.jpg"), ITEM)["ok"])


if __name__ == "__main__":
    unittest.main()
