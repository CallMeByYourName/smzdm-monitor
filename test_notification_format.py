import sqlite3
import unittest

from smzdm import SmzdmScraper


class NotificationFormatTests(unittest.TestCase):
    def setUp(self):
        self.scraper = SmzdmScraper.__new__(SmzdmScraper)
        self.data = {
            'id': '183730000', 'title': '惠威 D300 2.0声道音箱 棕色',
            'price': '1431.74元', 'mall': '京东', 'pub_time': '14:43',
            'link': 'https://www.smzdm.com/p/183730000/',
            'worthy': 4, 'unworthy': 0, 'comments': 9, 'collection': 4,
            'quality_path': '早期好价',
            'trend_metrics': {
                'snapshot_count': 3, 'elapsed_minutes': 106,
                'growth_score': 50, 'delta_worthy': 4, 'delta_collection': 3,
                'recent_minutes': 15, 'recent_growth_score': 0,
            },
        }

    def test_list_shows_price_and_factual_signal_without_claiming_price_quality(self):
        summary = self.scraper._build_notification_summary(self.data)
        self.assertTrue(summary.startswith('1431.74元｜惠威 D300'))
        self.assertIn('106分钟增4值', summary)
        self.assertIn('价差未核实', summary)
        self.assertNotIn('好评率', summary)
        self.assertNotIn('史低', summary)
        content = self.scraper._build_notification_content(self.data)
        self.assertIn('值票支持率 100%，共 4 票', content)
        self.assertIn('价格优势待核实', content)
        self.assertLess(content.index('这次为什么推送'), content.index('筛选记录'))
        self.assertLess(content.index('查看好价与购买方法'), content.index('筛选记录'))

    def test_multi_buy_condition_survives_long_title_in_summary(self):
        data = dict(self.data, title='88VIP、今日必买：湾仔码头 水饺' + '规格描述' * 60,
                    price='8.88元（需买9件，需用券）')
        summary = self.scraper._build_notification_summary(data)
        self.assertLessEqual(len(summary), 100)
        for condition in ('8.88元', '需买9件', '88VIP', '需用券'):
            self.assertIn('用券' if condition == '需用券' else condition, summary)
        self.assertLess(summary.index('需买9件'), summary.index('湾仔码头'))
        self.assertNotIn('需买4件', self.scraper._build_notification_summary(
            dict(self.data, title='品牌 4件套 厨具', price='49元')))

    def test_rebates_not_presented_as_cash_payment(self):
        data = dict(self.data, title='AHC 洗面奶',
                    price='25.72元返30元e卡，返45积分后')
        summary = self.scraper._build_notification_summary(data)
        self.assertTrue(summary.startswith('折算25.72元'))
        self.assertIn('返E卡', summary)
        self.assertIn('返积分', summary)
        content = self.scraper._build_notification_content(data)
        self.assertIn('25.72元返30元e卡，返45积分后', content)
        self.assertIn('实际支付和返还请分别核对', content)
        self.assertNotIn('实际支付25.72', content)

    def test_promo_prefix_cleaning_keeps_model_and_coins_condition(self):
        data = dict(self.data, title='88VIP、今日必买：雷鸟 鹤6 Plus 98R69A Plus',
                    price='1056.32元淘金币到手999.22元')
        self.assertEqual(self.scraper._notification_short_title(data), '雷鸟 鹤6 Plus 98R69A Plus')
        summary = self.scraper._build_notification_summary(data)
        self.assertTrue(summary.startswith('1056.32元'))
        self.assertIn('淘金币', summary)
        self.assertIn('999.22元', self.scraper._build_notification_content(data))

    def test_previous_push_price_not_called_market_low(self):
        data = dict(self.data, price_drop_note='较历史相似商品推送低 100.00 元（1.9%）')
        summary = self.scraper._build_notification_summary(data)
        content = self.scraper._build_notification_content(data)
        self.assertIn('较此前推送低', summary)
        self.assertIn('此前推送', content)
        self.assertIn('购买条件可能不同', content)
        for text in (summary, content):
            self.assertNotIn('历史最低', text)
            self.assertNotIn('史低', text)

    def test_ambiguous_rebate_and_price_range_are_not_rewritten_as_cash_price(self):
        cases = {
            '119.9元（返240值得买积分）': '119.9元',
            '294.96元（多重优惠、晒单返100元E卡）': '294.96元',
            '199元起': '199元起',
            '99-199元': '99-199元',
            '返100元E卡后194元': '返100元E卡后194元',
            '9.9元/斤': '9.9元/斤',
        }
        for raw, display in cases.items():
            with self.subTest(price=raw):
                data = dict(self.data, price=raw)
                self.assertEqual(self.scraper._notification_display_price(data), display)
                self.assertTrue(self.scraper._build_notification_summary(data).startswith(display))
        for title, condition in [('移动端：品牌 商品', '移动端'), ('凑单品：品牌 商品', '凑单品')]:
            data = dict(self.data, title=title)
            self.assertIn(condition, self.scraper._build_notification_summary(data))
            self.assertIn(condition, self.scraper._build_notification_content(data))

    def test_rebate_and_coupon_amounts_are_not_dedup_sale_prices(self):
        cases = {
            '25.72元返30元e卡，返45积分后': 25.72,
            '294.96元（多重优惠、晒单返100元E卡）': 294.96,
            '119.9元（返240值得买积分）': 119.9,
            '返100元E卡后194元': 194,
            '119.9元（满100元减20元）': 119.9,
            '原价199元，券后99元': 99,
            '30元（26.29元淘金币到手）': 26.29,
            '满100元减20元': None,
        }
        for raw, expected in cases.items():
            with self.subTest(price=raw):
                self.assertEqual(self.scraper._parse_price_value(raw), expected)

    def test_existing_history_wrong_rebate_price_is_corrected(self):
        self.scraper.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.scraper.conn.close)
        self.scraper.conn.execute('CREATE TABLE history(id TEXT, price TEXT, price_value REAL)')
        self.scraper.conn.executemany('INSERT INTO history VALUES (?, ?, ?)', [
            ('1', '25.72元返30元e卡，返45积分后', 30),
            ('2', '294.96元（晒单返100元E卡）', 100),
            ('3', '满100元减20元', 20),
            ('4', '19.9元', 19.9),
        ])
        self.scraper._backfill_history_price_values()
        self.assertEqual(self.scraper.conn.execute(
            'SELECT id, price_value FROM history ORDER BY id').fetchall(),
            [('1', 25.72), ('2', 294.96), ('3', None), ('4', 19.9)])

    def test_external_markup_and_unsafe_link_cannot_become_active_html(self):
        data = dict(self.data, title='商品 <img src=x onerror=alert(1)> & 特价',
                    price='<script>alert(1)</script>19.9元',
                    price_drop_note='<img src=x onerror=alert(1)>',
                    link='javascript:alert(1)')
        content = self.scraper._build_notification_content(data)
        self.assertNotIn('<script', content)
        self.assertNotIn('<img', content)
        self.assertNotIn('href="javascript:', content)
        self.assertIn('购买链接暂不可用', content)


if __name__ == '__main__':
    unittest.main()
