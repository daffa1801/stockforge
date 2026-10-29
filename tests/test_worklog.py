import os
import shutil
import tempfile
import unittest

from app import worklog


_REAL_LOG_DIR = worklog.LOG_DIR


class WorklogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="stockforge-log-")
        worklog.LOG_DIR = cls.tmp

    @classmethod
    def tearDownClass(cls):
        worklog.LOG_DIR = _REAL_LOG_DIR
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        worklog.log("test_start", "mulai uji", filename="a.jpg", attempt=1)
        worklog.log("test_event", "kejadian kedua")

    def test_read_returns_newest_first(self):
        entries = worklog.read(limit=50)
        self.assertGreaterEqual(len(entries), 2)
        self.assertEqual(entries[0]["event"], "test_event")
        self.assertEqual(entries[1]["event"], "test_start")

    def test_fields_are_stored(self):
        entries = [e for e in worklog.read(limit=50) if e["event"] == "test_start"]
        self.assertTrue(entries)
        self.assertEqual(entries[0]["filename"], "a.jpg")
        self.assertEqual(entries[0]["attempt"], 1)

    def test_entry_has_time_and_date(self):
        entry = worklog.read(limit=1)[0]
        for key in ("ts", "date", "time", "event", "message"):
            self.assertIn(key, entry)

    def test_empty_fields_are_dropped(self):
        worklog.log("test_drop", "x", kosong="", nol=None)
        entry = worklog.read(limit=1)[0]
        self.assertNotIn("kosong", entry)
        self.assertNotIn("nol", entry)

    def test_summary_counts_events(self):
        summary = worklog.summarize()
        self.assertGreaterEqual(summary["total"], 2)
        self.assertIn("test_start", summary["events"])
        self.assertTrue(os.path.isdir(worklog.LOG_DIR))


if __name__ == "__main__":
    unittest.main()
