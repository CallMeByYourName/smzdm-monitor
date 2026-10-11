import unittest
from unittest.mock import patch

from smzdm import SmzdmScraper


class CommentEvidenceTests(unittest.TestCase):
    def setUp(self):
        with patch("smzdm.DB_PATH", ":memory:"):
            self.scraper = SmzdmScraper()
        self.addCleanup(self.scraper._cleanup)

    def check_samples(self, levels, user_ids=None, expected=None, author_count=0,
                      quality_path="早期好价"):
        user_ids = user_ids or [f"u{index}" for index in range(len(levels))]
        samples = [
            {"user_id": user_id, "level": level}
            for user_id, level in zip(user_ids, levels)
        ]
        returned = len(samples) + author_count
        total = returned if expected is None else expected
        data = {
            "id": "183732488", "title": "得宝 抽纸", "comments": total,
            "worthy": 14, "unworthy": 0, "collection": 11,
            "quality_path": quality_path, "score_rate": 100,
            "composite_score": total * 3 + 11 * 2 + 14,
        }
        meta = {
            "module_total": total, "all_count": returned,
            "author_count": author_count,
        }
        with patch.object(self.scraper, "_fetch_comment_samples", return_value=(samples, meta)):
            accepted = self.scraper._check_comment_user_levels(data)
        return accepted, data

    def assert_diagnostic(self, data):
        self.assertEqual(data["comment_level_status"], "skipped")
        self.assertIn("等级证据不足", data["comment_level_note"])
        self.assertFalse(data["comment_level_stats"]["evidence_sufficient"])
        self.assertEqual(self.scraper.stats["total_comment_level_insufficient_evidence"], 1)

    def test_three_known_low_levels_do_not_veto_a_representative_sample(self):
        accepted, data = self.check_samples([3, 4, 5])
        self.assertTrue(accepted)
        self.assert_diagnostic(data)
        self.assertEqual(data["comment_level_stats"]["low_ratio"], 1)

    def test_five_known_low_levels_do_not_veto_a_representative_sample(self):
        accepted, data = self.check_samples([3] * 5)
        self.assertTrue(accepted)
        self.assert_diagnostic(data)

    def test_unknown_levels_do_not_turn_one_low_level_into_a_hard_rejection(self):
        accepted, data = self.check_samples([3, None, None, None, None, None])
        self.assertTrue(accepted)
        self.assert_diagnostic(data)
        self.assertEqual(data["comment_level_stats"]["level_count"], 1)
        self.assertAlmostEqual(data["comment_level_stats"]["known_ratio"], 1 / 6)

    def test_six_known_levels_still_need_eighty_percent_level_coverage(self):
        accepted, data = self.check_samples([3] * 6 + [None] * 2)
        self.assertTrue(accepted)
        self.assert_diagnostic(data)
        self.assertEqual(data["comment_level_stats"]["known_ratio"], 0.75)

    def test_six_known_levels_and_sufficient_coverage_can_reject(self):
        accepted, data = self.check_samples([3] * 3 + [8] * 3 + [None])
        self.assertFalse(accepted)
        self.assertEqual(data["comment_level_status"], "rejected")
        self.assertTrue(data["comment_level_stats"]["evidence_sufficient"])

    def test_exact_eighty_percent_level_coverage_can_reject(self):
        accepted, data = self.check_samples([3] * 3 + [8] * 5 + [None] * 2)
        self.assertFalse(accepted)
        self.assertEqual(data["comment_level_stats"]["known_ratio"], 0.8)
        self.assertTrue(data["comment_level_stats"]["evidence_sufficient"])

    def test_six_representative_high_levels_can_pass(self):
        accepted, data = self.check_samples([8] * 6)
        self.assertTrue(accepted)
        self.assertEqual(data["comment_level_status"], "passed")
        self.assertNotIn("comment_level_note", data)
        self.assertTrue(data["comment_level_stats"]["evidence_sufficient"])

    def test_four_samples_with_two_users_still_fail_concentration(self):
        accepted, data = self.check_samples([3] * 4, ["u1", "u1", "u2", "u2"])
        self.assertFalse(accepted)
        self.assertEqual(data["comment_level_status"], "rejected")
        self.assertIn("独立评论用户不足", data["comment_level_note"])

    def test_five_samples_with_a_sixty_percent_user_still_fail_concentration(self):
        accepted, data = self.check_samples([None] * 5, ["u1", "u1", "u1", "u2", "u3"])
        self.assertFalse(accepted)
        self.assertIn("单用户评论过于集中", data["comment_level_note"])

    def test_one_user_at_exact_fifty_percent_is_not_over_concentration_limit(self):
        accepted, data = self.check_samples([3] * 4, ["u1", "u1", "u2", "u3"])
        self.assertTrue(accepted)
        self.assert_diagnostic(data)

    def test_known_levels_do_not_override_insufficient_module_coverage(self):
        accepted, data = self.check_samples([3] * 8, expected=16)
        self.assertTrue(accepted)
        self.assertEqual(data["comment_level_status"], "skipped")
        self.assertIn("评论模块仅返回8/16", data["comment_level_note"])
        self.assertNotIn("comment_level_stats", data)
        self.assertEqual(self.scraper.stats["total_comment_level_undercovered_skipped"], 1)

    def test_excluded_author_comments_do_not_fill_known_level_minimum(self):
        accepted, data = self.check_samples([3] * 5, author_count=2)
        self.assertTrue(accepted)
        self.assert_diagnostic(data)
        self.assertEqual(data["comment_level_stats"]["level_count"], 5)
        self.assertEqual(data["comment_module_coverage"]["returned"], 7)

    def test_author_heavy_representative_module_cannot_bypass_concentration(self):
        accepted, data = self.check_samples(
            [8] * 4, ["u1", "u1", "u2", "u2"], author_count=46,
            quality_path="均衡热度",
        )
        self.assertFalse(accepted)
        self.assertEqual(data["comment_level_status"], "rejected")
        self.assertIn("独立评论用户不足", data["comment_level_note"])
        self.assertTrue(data["comment_module_coverage"]["representative"])
        self.assertEqual(data["comment_module_coverage"]["expected"], 50)
        self.assertEqual(self.scraper.stats["total_comment_level_large_thread_allowed"], 0)

    def test_author_heavy_module_preserves_two_user_mature_sample_escape(self):
        accepted, data = self.check_samples(
            [8, 8], ["u1", "u2"], author_count=48, quality_path="均衡热度",
        )
        self.assertTrue(accepted)
        self.assertEqual(data["comment_level_status"], "skipped")
        self.assertIn("大评论区少样本放行", data["comment_level_note"])
        self.assertEqual(self.scraper.stats["total_comment_level_large_thread_allowed"], 1)


if __name__ == "__main__":
    unittest.main()
