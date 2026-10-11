import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from smzdm import SmzdmScraper


class CommentAnomalyTests(unittest.TestCase):
    def setUp(self):
        with patch('smzdm.DB_PATH', ':memory:'):
            self.scraper = SmzdmScraper()
        self.addCleanup(self.scraper._cleanup)
        self.data = {'id': '183743621', 'title': 'NAS 测试商品', 'comments': 6,
                     'worthy': 14, 'unworthy': 0, 'collection': 5,
                     'quality_path': '早期好价'}
        self.long_text = '这是一段用于回归测试的完整商品宣传正文，包含型号123和价格199元。'

    def samples(self, total=6, duplicate=4, creation_date='2分钟前'):
        return [
            {'comment_id': str(100 + index), 'user_id': f'private-user-{index}',
             'identity_known': True, 'level': 8, 'display_author': False,
             'content': self.long_text if index < duplicate else f'普通不同评论 {index}',
             'creation_date': creation_date}
            for index in range(total)
        ]

    def observe(self, samples, representative=True):
        self.scraper._observe_comment_anomalies(
            self.data, samples, {'representative': representative, 'ratio': 1 if representative else 0.2}
        )
        return self.data['comment_anomaly_stats']

    def test_long_text_across_four_users_and_bounded_time_produces_two_diagnostic_signals(self):
        with self.assertLogs(level='INFO') as logs:
            evidence = self.observe(self.samples())
        self.assertEqual(evidence['flags'], ['repeated_long_text', 'duplicate_text_burst'])
        self.assertEqual(evidence['duplicate_users'], 4)
        self.assertEqual(evidence['burst_span_seconds'], 60)
        self.assertEqual(evidence['mode'], 'observe')
        self.assertEqual(self.scraper.stats['total_comment_anomaly_combined'], 1)
        message = '\n'.join(logs.output)
        self.assertIn('id:183743621', message)
        self.assertIn('未确认水军', message)
        self.assertNotIn(self.long_text, message)
        self.assertNotIn('private-user-', message)

    def test_common_short_phrases_and_small_samples_do_not_raise_flags(self):
        for total, text in ((6, '已买谢谢'), (5, self.long_text)):
            with self.subTest(total=total, text=text):
                rows = self.samples(total=total, duplicate=total)
                for row in rows:
                    row['content'] = text
                self.assertEqual(self.observe(rows)['flags'], [])

    def test_same_account_or_unknown_identity_is_not_multiple_independent_accounts(self):
        rows = self.samples(duplicate=6)
        for row in rows:
            row['user_id'] = 'one-user'
        self.assertEqual(self.observe(rows)['flags'], [])
        for row in rows:
            row['user_id'] = 'comment:' + row['comment_id']
            row['identity_known'] = False
        self.assertEqual(self.observe(rows)['flags'], [])

    def test_duplicate_comment_ids_and_author_comments_do_not_seed_evidence(self):
        rows = self.samples(duplicate=6)
        for row in rows:
            row['comment_id'] = 'same-id'
        self.assertEqual(self.observe(rows)['sample_count'], 1)
        self.assertEqual(self.data['comment_anomaly_stats']['flags'], [])
        rows = self.samples(duplicate=6)
        for row in rows:
            row['display_author'] = True
        self.assertEqual(self.observe(rows)['sample_count'], 0)

    def test_hour_precision_and_unknown_dates_never_produce_second_precision_bursts(self):
        for value in ('12小时前', '昨天', '', '1' * 100 + '秒前', '1' * 57 + '秒前'):
            with self.subTest(value=value):
                evidence = self.observe(self.samples(creation_date=value))
                self.assertEqual(evidence['flags'], ['repeated_long_text'])

    def test_mixed_minute_intervals_use_the_widest_bound(self):
        rows = self.samples()
        rows[0]['creation_date'] = '5分钟前'
        evidence = self.observe(rows)
        self.assertEqual(evidence['burst_span_seconds'], 240)
        self.assertEqual(evidence['flags'], ['repeated_long_text'])

    def test_full_date_is_interpreted_as_beijing_and_future_dates_are_unknown(self):
        now = datetime(2026, 10, 11, 2, 0, tzinfo=timezone.utc).timestamp()
        expected = datetime(2026, 10, 11, 1, 59, tzinfo=timezone.utc).timestamp()
        self.assertEqual(self.scraper._comment_time_bounds('2026-10-11 09:59:00', now), (expected, expected))
        self.assertIsNone(self.scraper._comment_time_bounds('2026-10-11 10:10:00', now))

    def test_html_quotes_are_excluded_without_erasing_price_or_model(self):
        value = '<blockquote>' + self.long_text + '</blockquote>本人只是在询问'
        self.assertEqual(self.scraper._comment_normalized_text(value), '本人只是在询问')
        self.assertNotEqual(self.scraper._comment_normalized_text(self.long_text),
                            self.scraper._comment_normalized_text(self.long_text.replace('199', '299')))

    def test_undercovered_module_can_raise_diagnostics_but_never_a_new_hard_rejection(self):
        rows = self.samples()
        self.data['comments'] = 30
        meta = {'module_total': 30, 'all_count': 6, 'author_count': 0}
        with patch.object(self.scraper, '_fetch_comment_samples', return_value=(rows, meta)):
            self.assertTrue(self.scraper._check_comment_user_levels(self.data))
        self.assertEqual(self.data['comment_level_status'], 'skipped')
        self.assertFalse(self.data['comment_anomaly_stats']['representative'])
        self.assertEqual(len(self.data['comment_anomaly_stats']['flags']), 2)

    def test_vote_imbalance_alone_is_diagnostic_and_stalled_growth_still_rejects(self):
        self.data.update(worthy=52, unworthy=1, comments=9)
        self.assertTrue(self.scraper._check_shill(self.data))
        self.assertEqual(self.scraper.stats['total_engagement_anomaly_observed'], 1)
        self.assertEqual(len(self.data['engagement_anomaly_flags']), 2)
        self.data.update(comments=3, trend_metrics={
            'snapshot_count': 2, 'elapsed_minutes': 90,
            'growth_score': 0, 'recent_growth_score': 0,
        })
        self.assertFalse(self.scraper._check_shill(self.data))
        self.assertEqual(self.scraper.stats['total_filtered_trend_stale'], 1)

    def test_missing_grade_is_preserved_and_nicknames_never_merge_unknown_users(self):
        nodes = [{'comment_id': index, 'content': '独立正文', 'display_name': '相同昵称',
                  'display_author': '0', 'creation_date': '12小时前'} for index in range(1, 7)]
        samples = []
        self.scraper._collect_comment_samples(nodes, samples)
        self.assertEqual(len(samples), 6)
        self.assertTrue(all(sample['level'] is None for sample in samples))
        self.assertTrue(all(not sample['identity_known'] for sample in samples))
        self.assertEqual(self.scraper._calculate_comment_user_stats(samples)['unique_users'], 6)
        nodes[0].update(user_smzdm_id='123', vip_level=-1)
        nodes[1].update(user_smzdm_id='456', vip_level=8)
        samples = []
        self.scraper._collect_comment_samples(nodes, samples)
        self.assertEqual(samples[0]['user_id'], '123')
        self.assertIsNone(samples[0]['level'])
        self.assertEqual(samples[1]['user_id'], '456')

    def test_invalid_comment_ids_cannot_merge_into_a_synthetic_account(self):
        samples = []
        rows = [{'comment_id': value, 'content': '未知账号', 'vip_level': 8}
                for value in (None, '', 0, False, [])]
        self.scraper._collect_comment_samples(rows, samples)
        self.assertEqual(samples, [])

    def test_fetch_uses_only_article_rows_and_child_list_excluding_mirrors_and_references(self):
        # Shapes mirror the observed module; values are synthetic and no network call occurs.
        author = {'comment_id': 1, 'content': '作者评论', 'user_smzdm_id': 'author',
                  'vip_level': 8, 'display_author': 0, 'child_list': [
                      {'comment_id': 2, 'content': '子评论等级缺失', 'user_smzdm_id': 'user',
                       'comment_parent_id': {'comment_id': 1, 'vip_level': 8, 'content': '引用作者'}}
                  ]}
        payload = {'data': {'comments': {'hot_comments_b': {
            'total': 2, 'author_smzdm_id': 'author', 'rows': [author],
            'aigc_comment_rows': [author, {'comment_id': 3, 'content': '仅镜像', 'vip_level': 8}],
        }}, 'other_module': {'comment_id': 4, 'content': '别处评论', 'vip_level': 8}}}
        response = Mock(status_code=200, headers={'content-type': 'application/json'}, text='{}')
        response.json.return_value = payload
        with patch.object(self.scraper, '_throttle_external_request'), patch.object(
                self.scraper.session, 'get', return_value=response) as get:
            samples, meta = self.scraper._fetch_comment_samples('183743621')
        get.assert_called_once()
        self.assertEqual([sample['comment_id'] for sample in samples], ['2'])
        self.assertIsNone(samples[0]['level'])
        self.assertEqual(meta['all_count'], 2)
        self.assertEqual(meta['author_count'], 1)

    def test_empty_or_changed_module_shape_degrades_without_aborting_scan(self):
        for payload in (None, [], {}, {'data': None}, {'data': []},
                        {'data': {'comments': None}}, {'data': {'comments': []}},
                        {'data': {'comments': {'hot_comments_b': None}}}):
            with self.subTest(payload=payload):
                response = Mock(status_code=200, headers={'content-type': 'application/json'}, text='{}')
                response.json.return_value = payload
                with patch.object(self.scraper, '_throttle_external_request'), patch.object(
                        self.scraper.session, 'get', return_value=response):
                    samples, meta = self.scraper._fetch_comment_samples('183743621')
                self.assertEqual(samples, [])
                self.assertEqual(meta['all_count'], 0)
                self.assertEqual(meta['module_total'], 0)


if __name__ == '__main__':
    unittest.main()
