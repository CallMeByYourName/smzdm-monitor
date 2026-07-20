import unittest
from unittest.mock import patch

from smzdm import CONFIG, SmzdmScraper


class SmzdmFilterTests(unittest.TestCase):
    def setUp(self):
        self.scraper = SmzdmScraper.__new__(SmzdmScraper)
        self.scraper.quality_path_pass_counts = {}

    def test_main_feed_uses_api_page_limit_without_reducing_scan_budget(self):
        self.assertEqual(CONFIG["items_per_page"], 100)
        self.assertEqual(CONFIG["items_per_page"] * CONFIG["max_pages"], 4000)

    def test_rank_windows_alternate_without_increasing_requests(self):
        self.assertEqual(self.scraper._select_rank_source_hour(now_ts=0), 3)
        self.assertEqual(self.scraper._select_rank_source_hour(now_ts=1800), 12)
        self.assertEqual(self.scraper._select_rank_source_hour(now_ts=3600), 3)

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
            "兰芝会员积分兑换实物",
            "评论有奖：发布评论赢现金奖励",
            "京棟 河南首豫白酒专营店有2 0",
            "京棟 礼来蓉城旗舰店 共计二十个豆",
            "美士京东自营旗舰店 加购 如图",
            "来来来这是一百的",
            "慢来慢来 都有份 积分兑10豆",
            "维达（Vinda）纸品清洁京东自营旗舰店 入 会1 0",
            "容声京自加",
            "小天才京自领京豆",
            "京东 悦诗风吟 店铺活动（加购 10）",
            "京东 海尔智家 店铺活动（首页关注店铺抽好礼）",
            "京东 蔓迪 店铺会员入会50",
            "悠格码京东自营旗舰店1元红包",
            "保乐力加官旗 加 go lO",
            "橙奥京东自营旗舰店有 1 0豆",
            "稀物集加购10豆",
            "京东一三共加购10豆",
            "凯利来京东自营旗舰店有五十豆",
            "英友ENYOU办公官方旗舰店 共1 00豆",
            "罗技京东自营旗舰店 10元e卡 抽奖",
            "保联京东自营旗舰店 一共5 0豆",
            "天猫超市 猫超X省钱购 进入弹窗可领随机超市卡/优惠券",
            "墨觉京东自营领京豆",
            "必看促销：京东服饰真5折来袭！25个运动品牌参与",
            "七度空间京东自营积分兑换实测二十五豆",
            "花王女性护理京东自营专区有二十豆",
            "京东 每日进入弹窗可领至高1888元 实测0.64元",
            "聚优购五金机电工具经营部等级礼包五十豆",
            "保联京东自营旗舰店 有10豆",
            "得10京豆",
            "京东 E卡一分购 0.01元得1元e卡+100京豆",
            "京东 市民服务分享小红薯得36京豆",
            "倍轻松抽2京豆",
            "真五折！千仞岗羽绒服专区",
            "京东超市 黑五签到有奖 每日签到可领满200-20元黑五券×3张/超市卡",
            "京东金融 全网订单天天报销 10万奖池等你瓜分 每日选择订单可抽随机现金/京豆",
            "ROG 加购 20（需入会）",
            "京东 超级18会场 弹窗可领随机红包 实测0.62元（每日可领）",
            "光明牛奶京东自营旗舰店 积分兑换京豆",
            "雅博士海外旗舰店 积分兑换京豆",
            "沱牌官旗签⑤",
            "京东 屈臣氏（Watsons）饮料专卖店 加购1个商品5个京豆",
            "京东 好想你 入会20",
            "环球好物礼品店关二豆，侧边浮窗刮二十豆，会员一百豆",
            "马爹利官方旗舰店每日一豆",
            "vivo X300福袋 购买后参加三个活动 非必中",
            "京东关注有礼（1）个豆子",
            "值友专享：深夜游乐场，满18续摊～",
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
            "小米京东自营旗舰店 加购价199元 智能空气炸锅 6L",
            "PLUS会员：洁柔 抽纸缤纷系列原生木浆 3层 100抽*20包",
            "1号会员店 尊享年卡送（2斤冷冻金枕榴莲+240枚鸡蛋）",
            "COSTA 咖世家 入会专享醇小杯系列任选",
            "Hape 多向轨道大转盘 E3723 儿童益智玩具",
            "仙居酒店1晚+杨梅节抽奖一张+欢迎水果+餐厅消费券",
            "芒果TV 全屏会员年卡 支持电视端",
            "随机免单：凯米 U6系列超薄防蓝光镜片+镜框",
            "今日必买：桃李 早餐面包大礼包整箱6袋",
            "国家补贴：米家 桌面移动风扇",
            "京东服饰真5折：海澜之家男士POLO衫",
            "宝得聪PS磷脂酰丝氨酸儿童青少年藻油DHA聚力豆",
            "北面 男士圆领速干短袖T恤 8GXD JK3",
            "得力 12色加赠3袋超轻粘土儿童手工玩具",
            "湿美 工业除湿机 适用80-100㎡车间仓库抽湿器",
            "优易点 加厚抽屉式收纳柜 3格 奶咖土豆35cm",
            "南珠宫 18K金海水珍珠吊坠 送银链11-14mm C-D00",
            "海尔 Leader懒人抽油烟机 92D小黑翼Pro",
            "杰克琼斯 美式翻领夹克 五折专区任选 黑色 L",
            "移动端：润科 宝得聪PS磷脂酰丝氨酸儿童青少年藻油DHA 3岁以上聚力豆",
            "ROG 加购价20元 游戏鼠标",
            "COSTA 入会专享醇小杯系列任选",
            "沃隆 每日坚果 750g",
            "vivo X300 手机 赠品牌福袋",
            "沱牌 旗舰店签名纪念酒 500ml",
            "屈臣氏 苏打水 加购价19.9元",
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertFalse(self.scraper._is_title_blocked({"title": title, "mall": "京东"}))

    def test_no_votes_do_not_receive_perfect_score_rate(self):
        metrics = self.scraper._current_interaction_metrics(
            {"comments": 12, "collection": 3, "worthy": 0, "unworthy": 0}
        )
        self.assertEqual(metrics["score_rate"], 0)

    @patch("smzdm.requests.post")
    def test_successful_push_logs_review_fields(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"success": True}
        data = {
            "id": "178999999",
            "title": "测试商品",
            "quality_path": "升温好价",
            "price": "19.9元",
            "comments": 6,
            "collection": 8,
            "worthy": 10,
            "unworthy": 1,
        }

        with self.assertLogs(level="INFO") as logs:
            self.assertTrue(self.scraper._send_notification(data))

        message = "\n".join(logs.output)
        self.assertIn("id:178999999", message)
        self.assertIn("路径:升温好价", message)
        self.assertIn("价格:19.9元", message)
        self.assertIn("评论:6 收藏:8 值:10 不值:1", message)

    def test_rank_item_maps_all_interaction_metrics(self):
        row = {
            "article_id": "178128662",
            "article_channel_type": "youhui",
            "article_title": "京鲜生 新疆吊干杏 净重2斤",
            "article_subtitle": "29.5元（需用券）",
            "article_url": "https://m.smzdm.com/p/178128662/",
            "article_date": "09:27",
            "article_timesort": "1783906029",
            "article_mall": "京东",
            "article_worthy": "284",
            "article_unworthy": "31",
            "article_collection": "268",
            "article_comment": "1345",
            "article_stock_status": 0,
        }
        parsed = self.scraper._parse_rank_item(row, 4)
        self.assertEqual(parsed["worthy"], 284)
        self.assertEqual(parsed["unworthy"], 31)
        self.assertEqual(parsed["collection"], 268)
        self.assertEqual(parsed["comments"], 1345)
        self.assertEqual(parsed["rank_position"], 4)

    def test_rank_metrics_upgrade_stale_list_counts(self):
        parsed = {"comments": 20, "collection": 5, "worthy": 4, "unworthy": 1, "link": ""}
        row = {
            "article_comment": "338",
            "article_collection": "8",
            "article_worthy": "14",
            "article_unworthy": "1",
            "article_url": "https://m.smzdm.com/p/178457908/",
        }
        self.scraper._apply_rank_metrics(parsed, row, 16)
        self.assertEqual(parsed["comments"], 338)
        self.assertEqual(parsed["collection"], 8)
        self.assertEqual(parsed["worthy"], 14)
        self.assertEqual(parsed["rank_position"], 16)

    def test_rank_source_does_not_bypass_low_score_rate(self):
        parsed = {
            "title": "富德 FG87 三模机械键盘",
            "comments": 291,
            "collection": 596,
            "worthy": 106,
            "unworthy": 32,
            "is_sold_out": False,
            "is_timeout": False,
            "status_text": "",
            "trend_metrics": self.scraper._empty_trend_metrics(),
            "rank_source": True,
        }
        self.assertFalse(self.scraper._filter_stage1(parsed))

    def test_rank_waf_response_is_optional(self):
        class Response:
            status_code = 202

        class Session:
            @staticmethod
            def get(*args, **kwargs):
                return Response()

        self.scraper.session = Session()
        self.scraper.stats = {"total_rank_unavailable": 0, "total_rank_fetched": 0}
        self.assertEqual(self.scraper._fetch_rank_rows(), [])
        self.assertEqual(self.scraper.stats["total_rank_unavailable"], 1)

    def test_rank_fetch_uses_one_selected_window_request(self):
        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"error_code": 0, "data": {"rows": [{"article_id": "1"}]}}

        class Session:
            calls = []

            @classmethod
            def get(cls, *args, **kwargs):
                cls.calls.append((args, kwargs))
                return Response()

        self.scraper.session = Session()
        self.scraper.stats = {"total_rank_unavailable": 0, "total_rank_fetched": 0}
        self.scraper._select_rank_source_hour = lambda: 3
        rows = self.scraper._fetch_rank_rows()
        self.assertEqual(len(Session.calls), 1)
        self.assertEqual(Session.calls[0][1]["params"]["hour"], 3)
        self.assertEqual(rows[0]["_rank_window_hours"], 3)

    def test_main_scan_stops_after_consecutive_page_failures(self):
        calls = []
        self.scraper._fetch_rank_rows = lambda: []
        self.scraper._fetch_page = lambda page: calls.append(page)
        self.scraper._commit_candidate_snapshots = lambda: None
        self.scraper.stats = {
            "total_fetched": 0,
            "total_page_request_failures": 0,
            "total_main_scan_aborted": 0,
        }

        self.assertEqual(self.scraper._scan_and_filter_stage1(), [])
        self.assertEqual(calls, [1, 2, 3])
        self.assertEqual(self.scraper.stats["total_page_request_failures"], 3)
        self.assertEqual(self.scraper.stats["total_main_scan_aborted"], 1)

    def test_comment_request_failures_trip_external_circuit_breaker(self):
        class Session:
            @staticmethod
            def get(*args, **kwargs):
                raise TimeoutError("comment endpoint timed out")

        self.scraper.session = Session()
        self.scraper._throttle_external_request = lambda: None
        self.scraper.external_checks_suspended = False
        self.scraper.consecutive_comment_request_failures = 0
        self.scraper.stats = {"total_external_checks_suspended": 0}

        self.scraper._fetch_comment_samples("1")
        self.assertFalse(self.scraper.external_checks_suspended)
        self.scraper._fetch_comment_samples("2")
        self.assertTrue(self.scraper.external_checks_suspended)
        self.assertEqual(self.scraper.stats["total_external_checks_suspended"], 1)

    def test_color_choice_suffixes_share_a_fingerprint(self):
        first = self.scraper._build_title_fingerprint("蕉下 透气 男士短袖T恤（5色可选）")
        second = self.scraper._build_title_fingerprint("蕉下 透气 男士短袖T恤（多色可选）")
        self.assertEqual(first, second)

    def test_price_in_title_keeps_product_fingerprint(self):
        fingerprint = self.scraper._build_title_fingerprint(
            "中国电信 19元205G全国流量不限速100分钟"
        )
        self.assertTrue(fingerprint)
        self.assertIn("205g全国流量", fingerprint)

    def test_changed_leading_price_shares_a_fingerprint(self):
        first = self.scraper._build_title_fingerprint(
            "22.5元/斤：元牧希 新西兰进口羔羊排肉卷2斤"
        )
        second = self.scraper._build_title_fingerprint(
            "18.5元/斤：元牧希 新西兰进口羔羊排肉卷2斤"
        )
        self.assertEqual(first, second)

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

    def test_collection_only_growth_cannot_confirm_warming(self):
        trend = self.scraper._empty_trend_metrics()
        trend.update({
            "snapshot_count": 2,
            "recent_minutes": 30,
            "elapsed_minutes": 60,
            "recent_growth_score": 16,
            "growth_score": 20,
            "recent_signal_growth_score": 16,
            "signal_growth_score": 20,
            "recent_delta_collection": 8,
            "delta_collection": 10,
            "recent_delta_worthy": 0,
            "delta_worthy": 0,
            "recent_growth_per_hour": 32,
            "growth_per_hour": 20,
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
            "recent_delta_worthy": 1,
            "delta_worthy": 1,
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
            "recent_delta_worthy": 1,
            "delta_worthy": 2,
            "recent_growth_per_hour": 30,
            "growth_per_hour": 20,
        })
        self.assertFalse(self.scraper._should_wait_for_trend_confirmation(parsed))

    def test_mature_balanced_early_deal_does_not_wait_for_recent_growth(self):
        parsed = {
            "title": "京东京造 男士复合维生素180片",
            "comments": 10,
            "collection": 20,
            "worthy": 6,
            "unworthy": 0,
            "is_sold_out": False,
            "is_timeout": False,
            "status_text": "",
            "trend_metrics": self.scraper._empty_trend_metrics(),
        }
        self.assertTrue(self.scraper._filter_stage1(parsed))
        self.assertEqual(parsed["quality_path"], "早期好价")
        self.assertFalse(self.scraper._should_wait_for_trend_confirmation(parsed))

    def test_mature_discussion_deal_does_not_require_recent_worthy_growth(self):
        parsed = {
            "title": "崇鲜 挪威冰鲜三文鱼生鱼片350g",
            "comments": 47,
            "collection": 11,
            "worthy": 4,
            "unworthy": 0,
            "is_sold_out": False,
            "is_timeout": False,
            "status_text": "",
            "trend_metrics": self.scraper._empty_trend_metrics(),
        }
        self.assertTrue(self.scraper._filter_stage1(parsed))
        self.assertEqual(parsed["quality_path"], "早期好价")
        self.assertFalse(self.scraper._should_wait_for_trend_confirmation(parsed))

    def test_high_comments_without_enough_collection_still_waits(self):
        parsed = {
            "title": "德路普 手机远程开关水阀",
            "comments": 28,
            "collection": 6,
            "worthy": 4,
            "unworthy": 0,
            "is_sold_out": False,
            "is_timeout": False,
            "status_text": "",
            "trend_metrics": self.scraper._empty_trend_metrics(),
        }
        self.assertTrue(self.scraper._filter_stage1(parsed))
        self.assertTrue(self.scraper._should_wait_for_trend_confirmation(parsed))


if __name__ == "__main__":
    unittest.main()
