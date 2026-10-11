import sqlite3
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import requests

from smzdm import CONFIG, SmzdmScraper


class PushDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch('smzdm.DB_PATH', ':memory:'))
        self.patches.enter_context(patch('smzdm.APP_TOKEN', 'AT_private_test_token'))
        self.patches.enter_context(patch('smzdm.UID', 'UID_private_test_recipient'))
        self.scraper = SmzdmScraper()
        self.addCleanup(self.scraper._cleanup)
        self.scraper._build_notification_content = Mock(return_value='<p>测试商品</p>')
        self.scraper._build_notification_summary = Mock(return_value='¥19.9 测试商品｜互动升温')
        self.data = {
            'id': '178999999',
            'title': '测试商品',
            'fingerprint': 'test-product-fingerprint',
            'product_key': 'jd:123456',
            'mall': '京东',
            'quality_path': '升温好价',
            'price': '19.9元',
            'price_value': 19.9,
            'comments': 6,
            'collection': 8,
            'worthy': 10,
            'unworthy': 1,
        }

    @staticmethod
    def accepted_result():
        return {
            'success': True,
            'code': 1000,
            'data': [{
                'uid': 'UID_private_test_recipient',
                'code': 1000,
                'status': '创建发送任务成功',
                'sendRecordId': 12313,
            }],
        }

    def run_one_candidate(self):
        """保留真实发送和历史落库，用一个已经通过筛选的候选验证边界。"""
        with ExitStack() as mocks:
            mocks.enter_context(patch.dict(CONFIG, {
                'jd_self_filter_enabled': False,
                'comment_level_check_enabled': False,
                'shill_detection_enabled': False,
            }))
            mocks.enter_context(patch.object(
                self.scraper, '_scan_and_filter_stage1', return_value=[self.data]))
            mocks.enter_context(patch.object(self.scraper, '_enrich_candidates_from_channel_apis'))
            mocks.enter_context(patch.object(self.scraper, '_cleanup'))
            mocks.enter_context(patch.object(self.scraper, '_print_statistics'))
            mocks.enter_context(patch.object(
                self.scraper, '_prioritize_candidates', side_effect=lambda candidates: candidates))
            for method in (
                '_should_defer_for_comment_unavailable', '_is_slow_growth_candidate',
                '_should_wait_for_trend_confirmation', '_is_low_growth_candidate',
            ):
                mocks.enter_context(patch.object(self.scraper, method, return_value=False))
            mocks.enter_context(patch('smzdm.time.sleep'))
            self.scraper.run()

    def assert_no_history(self):
        self.assertEqual(self.scraper.conn.execute('SELECT COUNT(*) FROM history').fetchone()[0], 0)
        self.assertNotIn(self.data['id'], self.scraper.seen_ids)
        self.assertEqual(self.scraper.stats['total_sent'], 0)
        self.assertEqual(self.scraper.stats['total_push_failed'], 1)
        self.assertNotIn('push_send_record_id', self.data)

    @patch('smzdm.requests.post')
    def test_matching_recipient_acceptance_saves_send_record_and_summary(self, post):
        post.return_value.status_code = 200
        post.return_value.json.return_value = self.accepted_result()
        with self.assertLogs(level='INFO') as logs:
            self.run_one_candidate()
        self.assertEqual(self.scraper.stats['total_sent'], 1)
        self.assertEqual(self.scraper.stats['total_push_failed'], 0)
        self.assertEqual(self.data['push_send_record_id'], '12313')
        row = self.scraper.conn.execute(
            'SELECT id, send_record_id, price_value FROM history').fetchone()
        self.assertEqual(row, ('178999999', '12313', 19.9))
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['summary'], '¥19.9 测试商品｜互动升温')
        self.assertEqual(payload['uids'], ['UID_private_test_recipient'])
        message = '\n'.join(logs.output)
        self.assertIn('sendRecordId:12313', message)
        self.assertIn('接收人code:1000', message)
        self.assertIn('异步到达未确认', message)
        self.assertNotIn('AT_private_test_token', message)
        self.assertNotIn('UID_private_test_recipient', message)

    @patch('smzdm.requests.post')
    def test_recipient_and_envelope_failures_never_enter_history(self, post):
        cases = [
            {'success': True},
            {'success': False, 'code': 1000, 'data': self.accepted_result()['data']},
            {'success': 'true', 'code': 1000, 'data': self.accepted_result()['data']},
            {'success': True, 'code': 1001, 'data': self.accepted_result()['data']},
            {'success': True, 'code': 1000},
            {'success': True, 'code': 1000, 'data': None},
            {'success': True, 'code': 1000, 'data': {}},
            {'success': True, 'code': 1000, 'data': []},
            {'success': True, 'code': 1000, 'data': [None, 1, 'bad-row']},
            {'success': True, 'code': 1000, 'data': [{
                'uid': 'UID_other_recipient', 'code': 1000, 'sendRecordId': 12313}]},
            {'success': True, 'code': 1000, 'data': [{
                'uid': 'UID_private_test_recipient', 'code': 1001, 'status': '接收人失败'}]},
            {'success': True, 'code': 1000, 'data': self.accepted_result()['data'] * 2},
            ['unexpected-json-array'],
            None,
        ]
        post.return_value.status_code = 200
        for result in cases:
            with self.subTest(result=result):
                self.scraper.stats['total_push_failed'] = 0
                post.return_value.json.return_value = result
                self.run_one_candidate()
                self.assert_no_history()

    @patch('smzdm.requests.post')
    def test_http_error_and_invalid_json_never_enter_history(self, post):
        post.return_value.status_code = 503
        self.run_one_candidate()
        self.assert_no_history()
        post.return_value.json.assert_not_called()
        self.scraper.stats['total_push_failed'] = 0
        post.return_value.status_code = 200
        post.return_value.json.side_effect = ValueError('body contains AT_private_test_token')
        with self.assertLogs(level='ERROR') as logs:
            self.run_one_candidate()
        self.assert_no_history()
        self.assertNotIn('AT_private_test_token', '\n'.join(logs.output))

    @patch('smzdm.requests.get')
    @patch('smzdm.requests.post')
    def test_original_link_is_sent_as_raw_json_and_no_status_query_is_added(self, post, get):
        post.return_value.status_code = 200
        post.return_value.json.return_value = self.accepted_result()
        link = 'https://www.smzdm.com/p/178999999/?a=1&b=2'
        self.data['link'] = link
        self.assertTrue(self.scraper._send_notification(self.data))
        self.assertEqual(post.call_args.kwargs['json']['url'], link)
        self.assertEqual(post.call_args.kwargs['json']['contentType'], 2)
        self.assertNotIn('&amp;', post.call_args.kwargs['json']['url'])
        get.assert_not_called()
        for invalid in ('', 'javascript:alert(1)', 'jd://123', 'https://www.smzdm.com/' + 'x' * 1000):
            with self.subTest(link=invalid):
                self.data['link'] = invalid
                self.assertTrue(self.scraper._send_notification(self.data))
                self.assertNotIn('url', post.call_args.kwargs['json'])
        get.assert_not_called()

    @patch('smzdm.requests.post')
    def test_post_timeout_is_unknown_and_is_not_retried(self, post):
        post.side_effect = requests.ReadTimeout('request included AT_private_test_token')
        with self.assertLogs(level='ERROR') as logs:
            self.run_one_candidate()
        self.assert_no_history()
        self.assertEqual(self.scraper.stats['total_push_unknown'], 1)
        post.assert_called_once()
        self.assertNotIn('AT_private_test_token', '\n'.join(logs.output))

    @patch('smzdm.requests.post')
    def test_response_status_redacts_credentials_and_control_characters(self, post):
        result = self.accepted_result()
        result['data'][0]['code'] = 1001
        result['data'][0]['status'] = 'AT_private_test_token\nUID_private_test_recipient\t失败'
        post.return_value.status_code = 200
        post.return_value.json.return_value = result
        with self.assertLogs(level='ERROR') as logs:
            self.assertFalse(self.scraper._send_notification(self.data))
        message = '\n'.join(logs.output)
        self.assertNotIn('AT_private_test_token', message)
        self.assertNotIn('UID_private_test_recipient', message)
        self.assertNotIn('\t', message)
        self.assertIn('[已隐藏]', message)

    @patch('smzdm.requests.post')
    def test_invalid_recipient_configuration_does_not_submit_a_post(self, post):
        for uid in ('', ' UID_recipient', 'UID_recipient\n', 'UID_a,UID_b', 'UID_a\x00', None):
            with self.subTest(uid=uid), patch('smzdm.UID', uid):
                self.assertFalse(self.scraper._send_notification(self.data))
        post.assert_not_called()

    def test_get_retries_never_sleep_for_a_rate_limit_retry_after(self):
        retry = self.scraper.session.get_adapter('https://').max_retries
        self.assertFalse(retry.respect_retry_after_header)
        self.assertFalse(retry.is_retry('GET', 429, has_retry_after=True))
        self.assertFalse(retry.is_retry('GET', 403, has_retry_after=True))
        self.assertFalse(retry.is_retry('GET', 202, has_retry_after=True))
        for status in (500, 502, 503, 504):
            self.assertTrue(retry.is_retry('GET', status, has_retry_after=True))
        self.assertFalse(retry.is_retry('POST', 503, has_retry_after=True))
        response = Mock(headers={'Retry-After': '3600'})
        with patch('urllib3.util.retry.time.sleep') as sleep:
            retry.sleep(response)
        sleep.assert_not_called()

    def test_history_migration_preserves_old_rows_and_adds_record_column(self):
        legacy = SmzdmScraper.__new__(SmzdmScraper)
        legacy.conn = sqlite3.connect(':memory:')
        self.addCleanup(legacy.conn.close)
        legacy.conn.execute('''CREATE TABLE history (
            id TEXT PRIMARY KEY, title TEXT, last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
        legacy.conn.execute('INSERT INTO history (id, title) VALUES (?, ?)', ('old-id', '旧商品'))
        legacy._ensure_history_columns()
        legacy._ensure_history_columns()
        columns = {row[1] for row in legacy.conn.execute('PRAGMA table_info(history)')}
        self.assertIn('send_record_id', columns)
        self.assertEqual(
            legacy.conn.execute('SELECT id, title, send_record_id FROM history').fetchone(),
            ('old-id', '旧商品', None),
        )


if __name__ == '__main__':
    unittest.main()
