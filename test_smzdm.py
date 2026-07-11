import unittest

from smzdm import SmzdmScraper


class SmzdmFilterTests(unittest.TestCase):
    def setUp(self):
        self.scraper = SmzdmScraper.__new__(SmzdmScraper)
        self.scraper.quality_path_pass_counts = {}

    def test_task_title_variants_are_blocked(self):
        blocked_titles = [
            "店铺领5️⃣",
            "京东母婴直播5豆",
            "京东欧乐B直播间抽25豆",
            "美的京东自营旗舰店 每日签到抽奖 逗 小家电如图",
            "黛米珠宝 签到每天 十逗 7天100逗",
            "小玉米的小店关注二+豆",
            "加州宝宝海外京东自营关注领五；豆",
            "S图伦JD旗舰店关注45D",
            "金融汇添抽奖88，大概率",
        ]
        for title in blocked_titles:
            with self.subTest(title=title):
                self.assertTrue(self.scraper._is_title_blocked({"title": title, "mall": "京东"}))

    def test_normal_product_titles_are_not_blocked(self):
        titles = [
            "小米 智能插座3",
            "红豆 男士纯棉短袖T恤",
            "巴布豆 儿童运动鞋",
            "九阳 豆浆机 1.2L",
            "雅漾 祛痘舒缓小黑膜面膜5片*3盒",
            "老板 天空之境系列 W76-F80D 独嵌两用洗碗机 15套",
            "长虹 75D66H 144Hz高刷 4K平板液晶电视机",
            "容声 BCD-515D30FNLBD 一级能效变频风冷冰箱",
            "厨邦 特级鲜生抽1.06kg*2 黄豆酿造酱油",
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertFalse(self.scraper._is_title_blocked({"title": title, "mall": "京东"}))

    def test_no_votes_do_not_receive_perfect_score_rate(self):
        metrics = self.scraper._current_interaction_metrics(
            {"comments": 12, "collection": 3, "worthy": 0, "unworthy": 0}
        )
        self.assertEqual(metrics["score_rate"], 0)

    def test_comment_only_growth_cannot_confirm_warming(self):
        trend = self.scraper._empty_trend_metrics()
        trend.update({
            "snapshot_count": 2,
            "recent_minutes": 30,
            "elapsed_minutes": 60,
            "recent_growth_score": 24,
            "growth_score": 40,
            "recent_signal_growth_score": 0,
            "signal_growth_score": 0,
            "recent_growth_per_hour": 48,
            "growth_per_hour": 40,
        })
        self.assertFalse(self.scraper._has_warming_trend(trend))
        self.assertFalse(self.scraper._has_confirmed_early_trend(trend))

    def test_worthy_and_collection_growth_confirms_trend(self):
        trend = self.scraper._empty_trend_metrics()
        trend.update({
            "snapshot_count": 2,
            "recent_minutes": 30,
            "elapsed_minutes": 60,
            "recent_growth_score": 13,
            "growth_score": 13,
            "recent_signal_growth_score": 5,
            "signal_growth_score": 5,
            "recent_growth_per_hour": 26,
            "growth_per_hour": 13,
        })
        self.assertTrue(self.scraper._has_warming_trend(trend))
        self.assertTrue(self.scraper._has_confirmed_early_trend(trend))

    def test_early_quality_waits_until_signal_growth(self):
        parsed = {
            "title": "雅漾 舒缓面膜",
            "comments": 9,
            "collection": 4,
            "worthy": 4,
            "unworthy": 0,
            "is_sold_out": False,
            "is_timeout": False,
            "status_text": "",
            "trend_metrics": self.scraper._empty_trend_metrics(),
        }
        self.assertTrue(self.scraper._filter_stage1(parsed))
        self.assertEqual(parsed["quality_path"], "早期好价")
        self.assertTrue(self.scraper._should_wait_for_trend_confirmation(parsed))

        parsed["trend_metrics"].update({
            "snapshot_count": 2,
            "recent_minutes": 30,
            "elapsed_minutes": 60,
            "recent_growth_score": 15,
            "growth_score": 20,
            "recent_signal_growth_score": 7,
            "signal_growth_score": 9,
            "recent_growth_per_hour": 30,
            "growth_per_hour": 20,
        })
        self.assertFalse(self.scraper._should_wait_for_trend_confirmation(parsed))


if __name__ == "__main__":
    unittest.main()
