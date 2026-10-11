import sqlite3
import unittest
from html.parser import HTMLParser

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
        for condition in ('8.88元', '需9件', '88VIP', '券'):
            self.assertIn(condition, summary)
        self.assertLess(summary.index('需9件'), summary.index('湾仔码头'))
        content = self.scraper._build_notification_content(data)
        self.assertIn('需买9件', content)
        self.assertIn('需用券', content)
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

    def test_compact_conditions_bring_product_name_forward_without_dropping_membership(self):
        data = dict(self.data, title='PLUS会员：冷酸灵 焕色美白专业修复牙膏',
                    price='17.04元（需买2件，需用券）')
        summary = self.scraper._build_notification_summary(data)
        self.assertTrue(summary.startswith('17.04元｜需2件/PLUS/券｜冷酸灵'))
        self.assertLess(summary.index('冷酸灵'), 20)
        self.assertIn('价差未核实', summary)
        self.assertIn('PLUS会员', self.scraper._build_notification_content(data))

    def test_extreme_price_and_conditions_keep_uncertainty_and_product_in_bounded_summary(self):
        data = dict(self.data,
                    title='PLUS会员：88VIP、国家补贴、淘金币、限地区、凑单品、移动端、随机免单、'
                          '晒单返奖励 品牌 商品型号 ABC123',
                    price='12345678901234567890123456789012345678901234567890元起'
                          '（需买99件，需用券，返100元E卡，返240积分）')
        summary = self.scraper._build_notification_summary(data)
        self.assertLessEqual(len(summary), 100)
        self.assertTrue(summary.startswith('复杂价格见详情｜'))
        self.assertIn('价差未核实', summary)
        for condition in ('需99件', 'PLUS', '88VIP', '返E卡', '返积分', '国补', '券'):
            self.assertIn(condition, summary)
        self.assertIn('元起', self.scraper._build_notification_content(data))

    def test_unusually_long_quantity_cannot_clip_product_and_price_uncertainty(self):
        data = dict(self.data, title='PLUS会员：品牌 商品 ABC123',
                    price='99元（需买' + '9' * 1000 + '件，需用券）')
        summary = self.scraper._build_notification_summary(data)
        self.assertLessEqual(len(summary), 100)
        self.assertIn('数量见原文', summary)
        self.assertIn('品牌 商品 ABC123', summary)
        self.assertIn('价差未核实', summary)

    def test_copy_attributes_retain_full_product_and_raw_link_without_injection(self):
        class CopyParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.buttons = []

            def handle_starttag(self, tag, attrs):
                if tag == 'copy':
                    self.buttons.append(dict(attrs))

        data = dict(self.data, title='88VIP：品牌 ABC Plus 98R69A "特别款" & 规格' + ' 原厂配件' * 15,
                    link='https://www.smzdm.com/p/183730000/?a=1&b=%22quoted%22')
        content = self.scraper._build_notification_content(data)
        parser = CopyParser()
        parser.feed(content)
        self.assertEqual(len(parser.buttons), 2)
        self.assertEqual(parser.buttons[0]['data-clipboard-text'],
                         self.scraper._notification_short_title(data))
        self.assertEqual(parser.buttons[1]['data-clipboard-text'], data['link'])
        self.assertTrue(all(set(button) == {'style', 'data-clipboard-text'} for button in parser.buttons))
        self.assertLess(content.index('复制商品名'), content.index('筛选记录'))
        self.assertIn('&amp;b=', content)
        bad_link = self.scraper._build_notification_content(dict(data, link='javascript:alert(1)'))
        self.assertNotIn('复制原文链接', bad_link)
        self.assertIn('复制商品名', bad_link)

    def test_original_url_length_protocol_and_structure_are_validated_without_html_escaping(self):
        url = 'https://www.smzdm.com/p/123/?a=1&b=2'
        self.assertEqual(self.scraper._notification_original_url(url), url)
        self.assertEqual(self.scraper._notification_safe_url(url), url.replace('&', '&amp;'))
        valid_max = 'https://www.smzdm.com/' + 'x' * (1000 - len('https://www.smzdm.com/'))
        self.assertEqual(self.scraper._notification_original_url(valid_max), valid_max)
        for invalid in (None, [], '', 'javascript:alert(1)', 'jd://123', '//www.smzdm.com/',
                        'http:///no-host', 'https://user:password@www.smzdm.com/',
                        'https://www.smzdm.com:bad/', 'https://www.smzdm.com/\x00',
                        'https://www.smzdm.com/\npath', 'https://www.smzdm.com/\\path',
                        'https://www.smzdm.com/"quoted"', valid_max + 'x'):
            with self.subTest(url=invalid):
                self.assertEqual(self.scraper._notification_original_url(invalid), '')


if __name__ == '__main__':
    unittest.main()
