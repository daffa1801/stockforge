import unittest

from app.ranking import rank_concept


def v(name, votes, score, passed):
    return {"filename": name, "judgement": {"votes": votes, "avg_score": score, "passed": passed}}


class RankingTests(unittest.TestCase):
    def test_picks_top_three_passing_only(self):
        variations = [
            v("a.jpg", 4, 92, True),
            v("b.jpg", 3, 88, True),
            v("c.jpg", 3, 85, True),
            v("d.jpg", 4, 90, True),
            v("e.jpg", 1, 40, False),
            v("f.jpg", 0, 30, False),
        ]
        result = rank_concept(variations, max_per_concept=3, threshold=3)
        chosen = [s["filename"] for s in result["shortlist"]]
        self.assertEqual(chosen, ["a.jpg", "d.jpg", "b.jpg"])
        self.assertEqual(len(result["shortlist"]), 3)
        self.assertEqual(result["rejected"], ["c.jpg", "e.jpg", "f.jpg"])
        self.assertFalse(result["low_confidence"])

    def test_nothing_passes_falls_back_to_best_one(self):
        variations = [v("a.jpg", 1, 50, False), v("b.jpg", 2, 61, False)]
        result = rank_concept(variations, max_per_concept=3, threshold=3)
        self.assertEqual(len(result["shortlist"]), 1)
        self.assertEqual(result["shortlist"][0]["filename"], "b.jpg")
        self.assertTrue(result["low_confidence"])

    def test_single_variation_passing_is_kept(self):
        result = rank_concept([v("only.jpg", 3, 70, True)], max_per_concept=3, threshold=3)
        self.assertEqual(result["shortlist"][0]["filename"], "only.jpg")
        self.assertFalse(result["low_confidence"])

    def test_rejected_list_excludes_chosen(self):
        result = rank_concept([v("a.jpg", 4, 90, True), v("b.jpg", 0, 10, False)], 3, 3)
        self.assertEqual(result["shortlist"][0]["filename"], "a.jpg")
        self.assertEqual(result["rejected"], ["b.jpg"])


if __name__ == "__main__":
    unittest.main()
