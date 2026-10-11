import hashlib
import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import requests

from smzdm import CONFIG, SmzdmScraper


class PushKeywordCooldownTests(unittest.TestCase):
    def setUp(self):
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch('smzdm.DB_PATH', ':memory:'))
        self.patches.enter_context(patch('smzdm.APP_TOKEN', 'AT_private_token'))
        self.patches.enter_context(patch('smzdm.UID', 'UID_private_recipient'))
        self.post = self.patches.enter_context(patch('smzdm.requests.post'))
        self.post.return_value.status_code = 200
        self.scraper = SmzdmScraper()
        self.addCleanup(self.scraper._cleanup)
        self.scraper._build_notification_content = Mock(return_value='<p>测试商品</p>')
        self.scraper._build_notification_summary = Mock(return_value='¥19.9 测试商品')
        self.data = {
            'id': '183743621', 'title': '极空间 NAS 测试商品',
            'fingerprint': 'nas-model', 'mall': '京东',
            'price': '19.9元', 'price_value': 19.9,
            'worthy': 14, 'unworthy': 0, 'collection': 5, 'comments': 6,
            'age_hours': 2, 'quality_path': '早期好价',
        }

    @staticmethod
    def rejected_result():
        return {
            'success': True, 'code': 1000,
            'data': [{
                'uid': 'UID_private_recipient', 'code': 1001,
                'status': '微信用户[UID=UID_private_recipient]因为用户设置的关键字过滤，不发送。'
                          '请引导用户在【我的订阅】-【设置过滤关键字】修改。',
            }],
        }

    def test_explicit_target_keyword_rejection_is_separate_from_history_and_post_failures(self):
        self.post.return_value.json.return_value = self.rejected_result()
        with self.assertLogs(level='INFO') as logs:
            self.assertFalse(self.scraper._send_notification(self.data))
            self.assertFalse(self.scraper._send_notification(self.data))
        self.post.assert_called_once()
        self.assertEqual(self.scraper.stats['total_push_failed'], 0)
        self.assertEqual(self.scraper.stats['total_push_keyword_filtered'], 1)
        self.assertEqual(self.scraper.stats['total_push_keyword_cooldown_skipped'], 1)
        self.assertEqual(self.scraper.conn.execute('SELECT COUNT(*) FROM history').fetchone()[0], 0)
        self.assertNotIn(self.data['id'], self.scraper.seen_ids)
        row = self.scraper.conn.execute(
            'SELECT recipient_key, article_id, expires_at FROM push_keyword_cooldowns'
        ).fetchone()
        self.assertEqual(row[0], hashlib.sha256(b'UID_private_recipient').hexdigest())
        self.assertEqual(row[1], self.data['id'])
        expires_at = datetime.fromisoformat(row[2])
        self.assertGreater(expires_at, datetime.utcnow() + timedelta(minutes=44))
        self.assertLessEqual(expires_at, datetime.utcnow() + timedelta(minutes=45))
        message = '\n'.join(logs.output)
        self.assertNotIn('UID_private_recipient', message)
        self.assertNotIn('AT_private_token', message)
        self.assertIn('关键词冷却跳过', message)
        self.assertIn('接收人code:1001', message)
        self.assertIn('sendRecordId:-', message)
        self.assertFalse(any(':ERROR:' in entry for entry in logs.output))

    def test_unverified_results_and_other_failures_never_create_cooldown(self):
        matching = self.rejected_result()['data'][0]
        cases = [
            {'success': False, 'code': 1000, 'data': [matching]},
            {'success': True, 'code': 1001, 'data': [matching]},
            {'success': True, 'code': 1000, 'data': [matching, matching]},
            {'success': True, 'code': 1000, 'data': [{**matching, 'uid': 'UID_someone_else'}]},
            {'success': True, 'code': 1000, 'data': [{**matching, 'code': 1002}]},
            {'success': True, 'code': 1000, 'data': [{**matching, 'status': '接收人失败'}]},
            {'success': True, 'code': 1000, 'data': [{**matching, 'status': '请检查关键字设置'}]},
            {'success': True, 'code': 1000, 'data': [{**matching, 'status': None}]},
            {'success': True, 'code': 1000, 'msg': matching['status'], 'data': []},
        ]
        for result in cases:
            with self.subTest(result=result):
                self.post.return_value.json.return_value = result
                self.assertFalse(self.scraper._send_notification(self.data))
                self.assertFalse(self.scraper._is_push_keyword_cooling_down(self.data['id']))
        self.post.side_effect = requests.ReadTimeout('unknown POST outcome')
        self.assertFalse(self.scraper._send_notification(self.data))
        self.assertEqual(self.scraper.stats['total_push_unknown'], 1)
        self.assertEqual(self.scraper.stats['total_push_keyword_filtered'], 0)
        self.assertEqual(self.scraper.conn.execute(
            'SELECT COUNT(*) FROM push_keyword_cooldowns').fetchone()[0], 0)

    def test_cooldown_is_scoped_to_recipient_and_article_and_expiry_allows_retry(self):
        self.scraper._save_push_keyword_cooldown(self.data['id'])
        self.assertTrue(self.scraper._is_push_keyword_cooling_down(self.data['id']))
        self.assertFalse(self.scraper._is_push_keyword_cooling_down('different-article'))
        with patch('smzdm.UID', 'UID_another_recipient'):
            self.assertFalse(self.scraper._is_push_keyword_cooling_down(self.data['id']))
        self.scraper.conn.execute(
            'UPDATE push_keyword_cooldowns SET expires_at = ?',
            (datetime.utcnow().isoformat(timespec='seconds'),),
        )
        self.assertFalse(self.scraper._is_push_keyword_cooling_down(self.data['id']))
        self.post.return_value.json.return_value = {
            'success': True, 'code': 1000,
            'data': [{'uid': 'UID_private_recipient', 'code': 1000, 'sendRecordId': 12313}],
        }
        self.assertTrue(self.scraper._send_notification(self.data))
        self.post.assert_called_once()
        self.assertEqual(self.scraper.stats['total_push_keyword_cooldown_skipped'], 0)

    def test_cooling_list_candidate_keeps_snapshot_without_enrichment_or_comment_requests(self):
        self.scraper._save_push_keyword_cooldown(self.data['id'])
        self.scraper._filter_stage1 = Mock(side_effect=AssertionError('cooling item should stop early'))
        candidates = []
        self.scraper._consider_stage1_candidate(self.data, candidates)
        self.scraper._commit_candidate_snapshots()
        self.assertEqual(candidates, [])
        rows = self.scraper.conn.execute(
            'SELECT article_id, worthy, comments FROM candidate_snapshots'
        ).fetchall()
        self.assertEqual(rows, [(self.data['id'], 14, 6)])
        self.scraper._filter_stage1.assert_not_called()
        self.post.assert_not_called()
        self.assertEqual(self.scraper.stats['total_candidate_snapshots_saved'], 1)
        self.assertEqual(self.scraper.stats['total_push_keyword_cooldown_skipped'], 1)

    def test_run_skips_external_checks_for_a_cooling_candidate(self):
        self.scraper._save_push_keyword_cooldown(self.data['id'])
        with ExitStack() as mocks:
            mocks.enter_context(patch.object(
                self.scraper, '_scan_and_filter_stage1', return_value=[self.data]))
            mocks.enter_context(patch.object(self.scraper, '_enrich_candidates_from_channel_apis'))
            mocks.enter_context(patch.object(self.scraper, '_cleanup'))
            mocks.enter_context(patch.object(self.scraper, '_print_statistics'))
            comment_check = mocks.enter_context(patch.object(self.scraper, '_check_comment_user_levels'))
            self.scraper.run()
        comment_check.assert_not_called()
        self.post.assert_not_called()
        self.assertEqual(self.scraper.stats['total_push_keyword_cooldown_skipped'], 1)
        self.assertEqual(self.scraper.stats['total_push_failed'], 0)

    def test_late_recheck_excludes_cooldown_before_limit_and_backfills_request_budget(self):
        captured_at = (datetime.utcnow() - timedelta(hours=2)).isoformat(timespec='seconds')
        for index in range(20):
            article_id = str(183700000 + index)
            self.scraper.conn.execute(
                '''INSERT INTO candidate_snapshots
                   (item_key, article_id, title, worthy, collection, comments,
                    composite_score, age_hours, captured_at)
                   VALUES (?, ?, ?, 10, 8, 8, 50, 2, ?)''',
                (f'id:{article_id}', article_id, f'正常商品 {index}', captured_at),
            )
            if index < 18:
                self.scraper._save_push_keyword_cooldown(article_id)
        rows = self.scraper._select_late_recheck_rows(set(), 2)
        self.assertEqual([row['article_id'] for row in rows], ['183700018', '183700019'])
        self.assertEqual(self.scraper.late_recheck_eligible_count, 2)
        with patch.object(self.scraper, '_fetch_late_recheck_detail', return_value=None) as fetch:
            self.scraper._append_late_recheck_candidates(set(), [])
        self.assertEqual([call.args[0] for call in fetch.call_args_list], ['183700018', '183700019'])
        self.assertEqual(self.scraper.stats['total_late_recheck_selected'], 2)
        self.assertEqual(self.scraper.conn.execute('SELECT COUNT(*) FROM history').fetchone()[0], 0)

    def test_expired_state_is_cleaned_without_deleting_active_recipient_state(self):
        self.scraper._save_push_keyword_cooldown('active')
        self.scraper._save_push_keyword_cooldown('expired')
        self.scraper.conn.execute(
            'UPDATE push_keyword_cooldowns SET expires_at = ? WHERE article_id = ?',
            ((datetime.utcnow() - timedelta(seconds=1)).isoformat(timespec='seconds'), 'expired'),
        )
        self.scraper._clean_old_records()
        self.assertEqual(self.scraper.conn.execute(
            'SELECT article_id FROM push_keyword_cooldowns').fetchall(), [('active',)])

    def test_new_state_survives_reopening_the_database_and_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory, patch(
                'smzdm.DB_PATH', os.path.join(directory, 'monitor.db')):
            first = SmzdmScraper()
            try:
                first._save_push_keyword_cooldown(self.data['id'])
            finally:
                first._cleanup()
            second = SmzdmScraper()
            try:
                self.assertTrue(second._is_push_keyword_cooling_down(self.data['id']))
                self.assertEqual(second.conn.execute('SELECT COUNT(*) FROM history').fetchone()[0], 0)
            finally:
                second._cleanup()


if __name__ == '__main__':
    unittest.main()
