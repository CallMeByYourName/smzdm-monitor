# -*- coding: utf-8 -*-
"""SMZDM 好价监控推送脚本 - GitHub Actions 单次执行模式"""

import os
import sys
import io
import sqlite3
import requests
import time
import random
import logging
import re
import html
import json
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ===== 配置 =====
APP_TOKEN = os.environ.get("WXPUSHER_APP_TOKEN", "")
UID = os.environ.get("WXPUSHER_UID", "")
DB_PATH = os.environ.get("SMZDM_DB_PATH", "smzdm.db")

CONFIG = {
    # 扫描参数
    "max_pages": 200,                   # 每次运行最多扫描页数
    "max_history_hours": 6,             # 最多扫描过去多少小时内的数据
    "whitelist_channel_types": ["faxian", "youhui"],  # 只保留好价相关频道
    "items_per_page": 20,

    # 第一阶段：综合评分筛选
    "score_weights": {
        "comments": 3,                  # 评论权重最高（最难刷）
        "collection": 2,                # 收藏权重中等
        "worthy": 1,                    # 值权重最低（最易刷）
    },
    "min_total_engagement": 15,         # 基础门槛：评论+收藏+值 >= 15
    "min_composite_score": 45,          # 综合评分阈值 (略微调高防止低质推送)
    "min_score_rate": 70,               # 均衡路径好评率
    "min_score_rate_relaxed": 70,        # 高讨论路径也要求较高好评率，避免推送争议商品
    "min_signal_worthy": 2,              # 至少有值票或评论信号，避免纯收藏/纯券活动
    "min_signal_comments": 2,
    "discussion_min_comments": 8,        # 高讨论路径：评论多、互动强，但仍要求较高好评率
    "discussion_min_total_engagement": 20,
    "discussion_min_composite_score": 70,
    "emerging_min_worthy": 4,            # 早期好价路径：低评论但值票/收藏快速增长
    "emerging_min_comments": 3,
    "emerging_min_total_engagement": 12,
    "emerging_min_composite_score": 25,
    "emerging_min_score_rate": 90,
    "excluded_status_keywords": [
        "售罄",
        "过期",
        "已结束",
    ],

    # 第二阶段：京东自营校验
    # GitHub Actions 访问京东商品页经常只拿到无商品字段的云端空壳页；
    # 默认关闭自营强过滤，京东商品与其他平台一样走评论等级/集中度/互动异常水军判断。
    "jd_self_filter_enabled": False,
    "jd_link_lookup_pages": 12,          # 当前列表接口不带 article_link，按频道接口最多回查页数
    "jd_link_lookup_page_size": 50,
    "jd_reject_when_unverified": True,   # 京东商品无法确认自营时拒绝，避免放过非自营
    "jd_self_check_min_per_run": 5,       # 京东候选少时的基础校验量
    "jd_self_check_candidate_ratio": 0.3, # 京东候选多时按比例增加校验量
    "max_jd_self_checks_per_run": 12,     # 硬上限，控制 go.smzdm/jd 外部链路请求量
    "jd_self_title_keywords": [           # 京东页面在云端不可用时，仅对确定的京东自营体系做兜底
        "京东自营",
        "自营旗舰店",
        "1号会员店",
        "京东京造",
        "京东超市",
    ],

    # 第三阶段：评论用户等级水军检测（haojia.m.smzdm.com 真实 JSON 的 vip_level）
    "comment_level_check_enabled": True,
    "comment_level_low_max": 5,          # Lv5 及以下视为低等级/新号
    "comment_level_high_min": 6,         # Lv6 及以上视为真实用户倾向
    "comment_level_min_comments": 3,     # 可取到的评论数少于此值时不做等级判断
    "comment_level_max_low_ratio": 0.35, # 低等级评论用户占比超过 35% 则过滤
    "comment_concentration_min_comments": 4,  # 评论样本达到此数量才检查集中度
    "comment_concentration_min_users": 3,     # 独立评论用户过少则可疑
    "comment_concentration_max_user_ratio": 0.5, # 单个用户评论占比过高则可疑
    "comment_level_check_min_per_run": 8,
    "comment_level_check_candidate_ratio": 0.9,
    "max_comment_level_checks_per_run": 40,
    "defer_emerging_when_comment_unavailable": True,
    "pending_review_fallback_runs": 2,
    "pending_review_keep_days": 2,
    "fallback_allowed_reasons": ["sample", "external"],
    "budget_strong_pass_min_score": 120,
    "budget_strong_pass_min_comments": 20,

    # 第四阶段：水军检测兜底（基于异常分析，需同时满足多项）
    "shill_detection_enabled": True,
    "shill_min_votes_for_check": 30,        # 总投票数少于此值时跳过水军检测
    "shill_max_worthy_unworthy_ratio": 20,  # 值/不值比超过此值则可疑指标+1
    "shill_min_comment_worthy_ratio": 0.2,  # 评论数/值票数低于此值则可疑指标+1
    "shill_min_flags": 2,                   # 至少N个可疑指标才标记为水军

    # 去重参数
    "fingerprint_dedupe_days": 3,       # 同商品标题指纹在 N 天内只推一次
    "fingerprint_min_len": 8,
    "price_drop_min_percent": 5,        # 同商品降价超过 5% 允许再次推送
    "price_drop_min_amount": 5,         # 或至少便宜 5 元允许再次推送

    # 请求参数
    "request_delay": (0.5, 1.5),        # 随机延迟范围（秒）
    "external_request_delay": (1.5, 3.5), # 详情/跳转类请求更慢，降低反爬风险
    "timeout": 30,                      # 请求超时（秒），GitHub Actions 到国内 API 延迟较高
    "waf_status_codes": [202, 403, 429],
    "waf_markers": ["probe.js", "tcaptcha", "验证码", "captcha", "访问过于频繁"],
}

# ===== 日志 =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(stream=sys.stdout)]
)


class SmzdmScraper:
    def __init__(self):
        self.session = requests.Session()
        self._init_session()
        self._init_database()
        self.seen_ids = set()
        self.seen_fingerprints = set()
        self.fingerprint_min_prices = {}
        self._load_existing_ids()

        self.stats = {
            'total_fetched': 0,
            'total_sent': 0,
            'total_duplicates': 0,
            'total_fingerprint_duplicates': 0,
            'total_filtered_stage1': 0,
            'total_filtered_jd_self': 0,
            'total_filtered_comment_level': 0,
            'total_filtered_shill': 0,
            'total_comment_level_unavailable': 0,
            'total_comment_level_unavailable_budget': 0,
            'total_comment_level_unavailable_sample': 0,
            'total_comment_level_unavailable_low_comments': 0,
            'total_comment_level_unavailable_external': 0,
            'total_comment_level_deferred': 0,
            'total_comment_level_fallback_allowed': 0,
            'total_comment_level_budget_strong_allowed': 0,
            'total_external_checks_suspended': 0,
        }
        self.article_link_cache = {}
        self.channel_article_link_cache = {}
        self.channel_link_pages_loaded = {}
        self.channel_link_exhausted = set()
        self.jd_self_checks = 0
        self.jd_self_check_limit = CONFIG['jd_self_check_min_per_run']
        self.jd_fetch_debug = []
        self.jd_page_checks_unavailable = False
        self.comment_level_checks = 0
        self.comment_level_check_limit = CONFIG['comment_level_check_min_per_run']
        self.external_checks_suspended = False

    def _init_session(self):
        retry = Retry(total=5, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20)
        self.session.mount('https://', adapter)
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Referer': 'https://www.smzdm.com/',
        })

    def _init_database(self):
        self.conn = sqlite3.connect(DB_PATH)
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS history (
                id TEXT PRIMARY KEY,
                title TEXT,
                fingerprint TEXT,
                mall TEXT,
                price TEXT,
                price_value REAL,
                last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self._ensure_history_columns()
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_history_fingerprint ON history (fingerprint, last_seen)')
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS pending_reviews (
                review_key TEXT PRIMARY KEY,
                article_id TEXT,
                title TEXT,
                fingerprint TEXT,
                mall TEXT,
                unavailable_count INTEGER DEFAULT 0,
                first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_quality_path TEXT,
                last_reason TEXT
            )
        ''')
        self.conn.commit()

    def _ensure_history_columns(self):
        cursor = self.conn.execute("PRAGMA table_info(history)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        required_columns = {
            'fingerprint': 'TEXT',
            'mall': 'TEXT',
            'price': 'TEXT',
            'price_value': 'REAL',
        }
        for column, column_type in required_columns.items():
            if column not in existing_columns:
                self.conn.execute(f'ALTER TABLE history ADD COLUMN {column} {column_type}')
        self._backfill_history_price_values()

    def _backfill_history_price_values(self):
        cursor = self.conn.execute(
            "SELECT id, price FROM history WHERE price_value IS NULL AND price IS NOT NULL AND price != ''"
        )
        rows = cursor.fetchall()
        for article_id, price in rows:
            price_value = self._parse_price_value(price)
            if price_value is not None:
                self.conn.execute(
                    "UPDATE history SET price_value = ? WHERE id = ?",
                    (price_value, article_id)
                )

    def _load_existing_ids(self):
        cursor = self.conn.execute("SELECT id FROM history")
        self.seen_ids = {row[0] for row in cursor.fetchall()}

        cutoff = datetime.now() - timedelta(days=CONFIG['fingerprint_dedupe_days'])
        cursor = self.conn.execute(
            '''
            SELECT fingerprint, MIN(price_value)
            FROM history
            WHERE fingerprint IS NOT NULL AND fingerprint != '' AND last_seen >= ?
            GROUP BY fingerprint
            ''',
            (cutoff,)
        )
        for fingerprint, min_price in cursor.fetchall():
            self.seen_fingerprints.add(fingerprint)
            if min_price is not None:
                self.fingerprint_min_prices[fingerprint] = float(min_price)
        logging.info(f"已加载 {len(self.seen_ids)} 条历史记录，{len(self.seen_fingerprints)} 个近期商品指纹")

    # ==================== 扫描 ====================

    def run(self):
        """单次执行入口：扫描 -> 三层筛选 -> 推送 -> 退出"""
        if not APP_TOKEN or not UID:
            logging.error("缺少 WXPUSHER_APP_TOKEN 或 WXPUSHER_UID 环境变量")
            sys.exit(1)

        logging.info("开始扫描...")
        self._clean_old_records()

        # 第一轮：API 扫描 + 综合评分筛选，收集候选商品
        candidates = self._scan_and_filter_stage1()
        candidates = self._prioritize_candidates(candidates)
        self.jd_self_check_limit = self._calculate_jd_self_check_limit(candidates)
        self.comment_level_check_limit = self._calculate_comment_level_check_limit(candidates)

        logging.info(f"综合评分筛选后候选商品: {len(candidates)} 条")
        if CONFIG['jd_self_filter_enabled']:
            logging.info(f"京东自营动态校验预算: {self.jd_self_check_limit} 次")
        else:
            logging.info("京东自营过滤: 关闭，京东商品按通用水军策略判断")
        logging.info(f"评论等级动态校验预算: {self.comment_level_check_limit} 次")

        # 第二轮：可选京东自营、评论等级、互动异常检测
        for parsed in candidates:
            if CONFIG['jd_self_filter_enabled'] and not self._check_jd_self_operated(parsed):
                self.stats['total_filtered_jd_self'] += 1
                continue

            if CONFIG['comment_level_check_enabled'] and not self._check_comment_user_levels(parsed):
                self.stats['total_filtered_comment_level'] += 1
                self._clear_pending_review(parsed)
                continue

            if self._should_defer_for_comment_unavailable(parsed):
                if parsed.get('comment_level_unavailable_reason') != 'budget':
                    self._save_pending_review(parsed)
                self.stats['total_comment_level_deferred'] += 1
                continue

            if CONFIG['shill_detection_enabled'] and not self._check_shill(parsed):
                self.stats['total_filtered_shill'] += 1
                continue

            if self._is_fingerprint_duplicate(parsed):
                self.stats['total_duplicates'] += 1
                continue

            # 推送
            if self._send_notification(parsed):
                # 只有推送成功才写入历史；未达标或推送失败的商品下次运行会重新按最新互动数据评估。
                self._save_history(parsed)
                self._clear_pending_review(parsed)
                self.stats['total_sent'] += 1

        self._print_statistics()
        self._cleanup()

    def _scan_and_filter_stage1(self):
        """扫描 API 并通过综合评分筛选候选商品"""
        candidates = []
        stop_scanning = False

        for page in range(1, CONFIG['max_pages'] + 1):
            if stop_scanning:
                break

            items = self._fetch_page(page)
            if items is None:
                continue
            if not items:
                logging.info(f"第{page}页无数据，停止扫描")
                break

            self.stats['total_fetched'] += len(items)

            for item in items:
                parsed = self._parse_item(item)
                if not parsed:
                    continue

                if parsed.get('age_hours', 0) > CONFIG['max_history_hours']:
                    logging.info(f"扫到 {parsed['age_hours']:.1f} 小时前的数据，达到时间上限 {CONFIG['max_history_hours']} 小时，停止扫描。")
                    stop_scanning = True
                    break

                if self._is_duplicate(parsed):
                    self.stats['total_duplicates'] += 1
                    continue

                # 第一阶段：综合评分筛选
                if not self._filter_stage1(parsed):
                    self.stats['total_filtered_stage1'] += 1
                    continue

                candidates.append(parsed)
                self.seen_ids.add(parsed['id'])  # 立即标记，防止跨页重复

            time.sleep(random.uniform(*CONFIG['request_delay']))

        return candidates

    def _fetch_page(self, page):
        """获取列表页数据"""
        offset = (page - 1) * CONFIG['items_per_page']
        try:
            response = self.session.get(
                f'https://api.smzdm.com/v1/list?limit={CONFIG["items_per_page"]}&offset={offset}',
                timeout=CONFIG['timeout']
            )
            if response.status_code != 200:
                logging.warning(f"第{page}页 HTTP {response.status_code}")
                return None

            resp_json = response.json()
            if isinstance(resp_json, dict):
                data = resp_json.get('data', {})
                if isinstance(data, dict):
                    return data.get('rows', [])
                elif isinstance(data, list):
                    return data
            elif isinstance(resp_json, list):
                return resp_json
            return []
        except Exception as e:
            logging.error(f"第{page}页请求异常: {e}")
            return None

    def _parse_item(self, item):
        """解析单个商品数据"""
        if not item or 'article_id' not in item:
            return None

        # 频道白名单过滤
        channel_type = str(item.get('article_channel_type', '')).strip()
        whitelist = CONFIG.get('whitelist_channel_types')
        if whitelist and channel_type not in whitelist:
            return None

        article_id = str(item['article_id']).strip()
        title = str(item.get('article_title', '')).strip()[:200]
        if not article_id or not title:
            return None

        # 计算发布时间
        age_hours = 0
        time_sort = str(item.get('publish_date_lt', '0')).strip()
        if time_sort.isdigit() and int(time_sort) > 0:
            ts = int(time_sort)
            if ts > 10000000000:
                ts = ts / 1000
            item_time = datetime.fromtimestamp(ts)
            age_hours = (datetime.now() - item_time).total_seconds() / 3600

        worthy = max(0, int(item.get('article_worthy', 0)))
        unworthy = max(0, int(item.get('article_unworthy', 0)))
        comments = max(0, int(item.get('article_comment', 0)))

        # 解析 tongji_hudong 获取精确数据
        tongji = self._parse_tongji_hudong(item.get('tongji_hudong', ''))
        article_link = self._extract_article_link(item)

        return {
            'id': article_id,
            'title': title,
            'price': str(item.get('article_price', '未知')).strip(),
            'link': str(item.get('article_url', '')).strip(),
            'article_link': article_link,
            'channel_type': channel_type,
            'mall': str(item.get('article_mall', '未知')).strip(),
            'pub_time': str(item.get('article_format_date', '')).strip(),
            'comments': tongji['comments'] or comments,
            'collection': tongji['collection'],
            'worthy': tongji['worthy'] or worthy,
            'unworthy': tongji['unworthy'] or unworthy,
            'age_hours': age_hours,
            'fingerprint': self._build_title_fingerprint(title),
            'price_value': self._parse_price_value(item.get('article_price', '')),
            'is_sold_out': self._truthy_field(item.get('article_is_sold_out')),
            'is_timeout': self._truthy_field(item.get('article_is_timeout')),
            'status_text': ' '.join(
                str(item.get(key, '')).strip()
                for key in ('stock_status_name', 'article_status_name')
                if str(item.get(key, '')).strip()
            ),
        }

    @staticmethod
    def _truthy_field(value):
        text = str(value or '').strip().lower()
        return text not in ('', '0', 'false', 'none', 'null')

    def _extract_article_link(self, item):
        article_link = str(item.get('article_link') or '').strip()
        if article_link:
            return article_link

        redirect_data = item.get('redirect_data') or {}
        if isinstance(redirect_data, str):
            try:
                redirect_data = json.loads(redirect_data)
            except json.JSONDecodeError:
                redirect_data = {}
        if not isinstance(redirect_data, dict):
            return ''

        md5_url = str(redirect_data.get('md5_url') or '').strip()
        if not md5_url:
            return ''
        if md5_url.startswith(('http://', 'https://')):
            return md5_url
        # 列表接口常见的 md5_url 只有 32 位哈希，直接拼 go.smzdm.com 会 404；
        # 只有拿到完整路径片段时才作为 article_link 回退。
        if '/' not in md5_url:
            return ''
        return f"https://go.smzdm.com/{md5_url.lstrip('/')}"

    def _parse_tongji_hudong(self, tongji_str):
        """解析 tongji_hudong 字段：评论_5,收藏_3,值_10,不值_2"""
        result = {'comments': 0, 'collection': 0, 'worthy': 0, 'unworthy': 0}
        if not tongji_str:
            return result
        mapping = {'评论': 'comments', '收藏': 'collection', '值': 'worthy', '不值': 'unworthy'}
        for part in tongji_str.split(','):
            if '_' in part:
                key, value = part.split('_', 1)
                if key in mapping and value.isdigit():
                    result[mapping[key]] = int(value)
        return result

    def _build_title_fingerprint(self, title):
        """生成跨文章 ID 的商品指纹，降低重复爆料推送。"""
        text = title.lower()
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'（[^）]*(券|优惠|返|plus|会员|满减|到手|需买|凑单)[^）]*）', '', text)
        text = re.sub(r'\([^)]*(券|优惠|返|plus|会员|满减|到手|需买|凑单)[^)]*\)', '', text)

        promo_words = [
            'plus会员', '88vip', '值友专享', '今日必买', '百亿补贴', '淘金币可用',
            '历史低价', '历史新低', '新低价', '新低', '限地区', '限时', '临期品',
            '凑单品', '需用券', '券后', '下单立减', '满减', '可用券', '可用',
        ]
        for word in promo_words:
            text = text.replace(word, '')

        text = re.sub(r'\d+(\.\d+)?\s*元.*$', '', text)
        text = re.sub(r'[^\w\u4e00-\u9fff]+', '', text)
        if len(text) < CONFIG['fingerprint_min_len']:
            return ''
        return text[:80]

    def _parse_price_value(self, price_text):
        """从价格文本中提取可比较的人民币数值，解析失败返回 None。"""
        text = str(price_text or '')
        text = text.replace(',', '')
        matches = re.findall(r'(\d+(?:\.\d+)?)\s*元', text)
        if not matches:
            return None
        values = [float(value) for value in matches]
        if any(keyword in text for keyword in ('低至', '折合', '约', '券后', '到手')):
            return min(values)
        return values[-1]

    # ==================== 筛选 ====================

    def _filter_stage1(self, parsed):
        """第一阶段：综合评分筛选"""
        comments = parsed['comments']
        collection = parsed['collection']
        worthy = parsed['worthy']
        unworthy = parsed['unworthy']
        weights = CONFIG['score_weights']

        if self._is_inactive_deal(parsed):
            return False

        if worthy < CONFIG['min_signal_worthy'] and comments < CONFIG['min_signal_comments']:
            return False

        # 基础门槛：总互动量
        total_engagement = comments + collection + worthy

        # 好评率检查
        total_votes = worthy + unworthy
        score_rate = worthy / total_votes * 100 if total_votes > 0 else 100
        if total_votes >= 3 and score_rate < CONFIG['min_score_rate_relaxed']:
            return False

        # 综合评分
        composite_score = (comments * weights['comments']
                           + collection * weights['collection']
                           + worthy * weights['worthy'])

        quality_path = ''
        if (total_engagement >= CONFIG['min_total_engagement']
                and composite_score >= CONFIG['min_composite_score']
                and score_rate >= CONFIG['min_score_rate']):
            quality_path = '均衡热度'
        elif (comments >= CONFIG['discussion_min_comments']
              and total_engagement >= CONFIG['discussion_min_total_engagement']
              and composite_score >= CONFIG['discussion_min_composite_score']
              and score_rate >= CONFIG['min_score_rate_relaxed']):
            quality_path = '高讨论'
        elif (worthy >= CONFIG['emerging_min_worthy']
              and comments >= CONFIG['emerging_min_comments']
              and total_engagement >= CONFIG['emerging_min_total_engagement']
              and composite_score >= CONFIG['emerging_min_composite_score']
              and score_rate >= CONFIG['emerging_min_score_rate']):
            quality_path = '早期好价'

        if not quality_path:
            return False

        # 好评率和综合评分挂到 parsed 上，供推送使用
        parsed['score_rate'] = round(score_rate) if total_votes > 0 else 100
        parsed['composite_score'] = composite_score
        parsed['quality_path'] = quality_path

        logging.info(
            f"[综合评分通过:{quality_path}] {parsed['title'][:40]}... | "
            f"评分:{composite_score} 好评率:{parsed['score_rate']}% 评论:{comments} 收藏:{collection} 值:{worthy} 不值:{unworthy}"
        )
        return True

    def _prioritize_candidates(self, candidates):
        """把有限的评论校验预算优先用在早期/高风险候选上。"""
        path_rank = {
            '早期好价': 4,
            '高讨论': 3,
            '均衡热度': 2,
        }

        def priority(parsed):
            return (
                path_rank.get(parsed.get('quality_path'), 0),
                parsed.get('comments', 0) >= CONFIG['comment_level_min_comments'],
                parsed.get('composite_score', 0),
                parsed.get('comments', 0),
                parsed.get('score_rate', 0),
                parsed.get('worthy', 0),
                parsed.get('collection', 0),
            )

        return sorted(candidates, key=priority, reverse=True)

    def _calculate_jd_self_check_limit(self, candidates):
        if not CONFIG.get('jd_self_filter_enabled'):
            return 0

        jd_candidates = sum(1 for parsed in candidates if parsed.get('mall') == '京东')
        if jd_candidates <= 0:
            return 0

        min_budget = max(0, int(CONFIG.get('jd_self_check_min_per_run', 0)))
        max_budget = max(min_budget, int(CONFIG.get('max_jd_self_checks_per_run', min_budget)))
        ratio = float(CONFIG.get('jd_self_check_candidate_ratio', 0))
        ratio = min(max(ratio, 0), 1)

        scaled_budget = int(jd_candidates * ratio)
        if jd_candidates * ratio > scaled_budget:
            scaled_budget += 1

        return min(jd_candidates, max_budget, max(min_budget, scaled_budget))

    def _calculate_comment_level_check_limit(self, candidates):
        if not CONFIG.get('comment_level_check_enabled'):
            return 0

        min_comments = int(CONFIG.get('comment_level_min_comments', 0))
        checkable_candidates = sum(
            1 for parsed in candidates
            if parsed.get('comments', 0) >= min_comments
        )
        if checkable_candidates <= 0:
            return 0

        min_budget = max(0, int(CONFIG.get('comment_level_check_min_per_run', 0)))
        max_budget = max(min_budget, int(CONFIG.get('max_comment_level_checks_per_run', min_budget)))
        ratio = float(CONFIG.get('comment_level_check_candidate_ratio', 0))
        scaled_budget = int(checkable_candidates * ratio)
        if checkable_candidates * ratio > scaled_budget:
            scaled_budget += 1

        return min(checkable_candidates, max_budget, max(min_budget, scaled_budget))

    def _is_inactive_deal(self, parsed):
        if parsed.get('is_sold_out') or parsed.get('is_timeout'):
            return True
        status_text = parsed.get('status_text', '')
        return any(keyword in status_text for keyword in CONFIG.get('excluded_status_keywords', []))

    def _check_jd_self_operated(self, parsed):
        """京东渠道只放行京东自营商品。

        当前主列表接口只返回 article_mall=京东，不含店铺名；实测频道列表
        article_link 可解析到京东商品页，商品页 HTML 中有 isSelf:true/false。
        """
        if parsed['mall'] != '京东':
            return True

        if self.external_checks_suspended:
            return self._handle_jd_unverified(parsed, "外部校验已熔断")

        if self._is_known_jd_self_from_title(parsed):
            logging.info(f"[京东自营标题兜底通过] {parsed['title'][:40]}...")
            return True

        article_link = self._get_article_link(parsed)
        if not article_link:
            return self._handle_jd_unverified(parsed, "未找到 article_link")

        if self.jd_page_checks_unavailable:
            return self._handle_jd_unverified(parsed, "京东商品页云端不可用")

        if self.jd_self_checks >= self.jd_self_check_limit:
            return self._handle_jd_unverified(parsed, "达到本轮京东自营校验上限")
        self.jd_self_checks += 1
        self.jd_fetch_debug = []

        jd_url = self._resolve_smzdm_go_link(article_link, parsed.get('link'))
        if not jd_url:
            return self._handle_jd_unverified(parsed, "无法解析 SMZDM 跳转链接")

        parsed['jd_url'] = jd_url
        try:
            self._throttle_external_request()
            response = self.session.get(
                jd_url,
                headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Encoding': 'identity',
                },
                timeout=CONFIG['timeout'],
                allow_redirects=True
            )
            self._record_jd_fetch_debug('direct', response)
            if self._is_waf_response(response):
                self._suspend_external_checks("京东商品页触发反爬/验证码")
                return self._handle_jd_unverified(parsed, "京东商品页触发反爬/验证码")
            html_text = response.text
        except Exception as e:
            return self._handle_jd_unverified(parsed, f"京东商品页请求异常: {e}")

        is_self = self._extract_jd_is_self(html_text)
        if is_self is None:
            canonical_url = self._build_jd_canonical_url(jd_url)
            if canonical_url and canonical_url != jd_url:
                parsed['jd_url'] = canonical_url
                is_self = self._fetch_jd_is_self_from_url(canonical_url)
                jd_url = canonical_url
        if is_self is None:
            is_self = self._fetch_jd_is_self_from_mobile(jd_url)

        if is_self is True:
            logging.info(f"[京东自营通过] {parsed['title'][:40]}... | {jd_url}")
            return True
        if is_self is False:
            logging.warning(f"[京东非自营过滤] {parsed['title'][:40]}... | {jd_url}")
            return False
        reason = "京东商品页未找到 isSelf"
        if self.jd_fetch_debug:
            logging.warning(
                f"[京东判定诊断] #{parsed['id']} {parsed['title'][:30]}... | "
                + " ; ".join(self.jd_fetch_debug)
            )
            if not self._jd_fetch_has_product_markers():
                self.jd_page_checks_unavailable = True
                reason = "京东商品页返回云端空壳页"
        return self._handle_jd_unverified(parsed, reason)

    def _handle_jd_unverified(self, parsed, reason):
        action = "过滤" if CONFIG['jd_reject_when_unverified'] else "放行"
        log_fn = logging.info if "达到本轮京东自营校验上限" in reason else logging.warning
        log_fn(f"[京东自营无法确认，{action}] #{parsed['id']} {parsed['title'][:40]}... | {reason}")
        return not CONFIG['jd_reject_when_unverified']

    def _fetch_jd_is_self_from_url(self, jd_url):
        if self.external_checks_suspended:
            return None
        try:
            self._throttle_external_request()
            response = self.session.get(
                jd_url,
                headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Encoding': 'identity',
                },
                timeout=CONFIG['timeout'],
                allow_redirects=True
            )
            self._record_jd_fetch_debug('canonical', response)
            if self._is_waf_response(response):
                self._suspend_external_checks("京东 canonical 商品页触发反爬/验证码")
                return None
            return self._extract_jd_is_self(response.text)
        except Exception as e:
            logging.warning(f"京东 canonical 商品页请求异常: {e}")
            return None

    def _fetch_jd_is_self_from_mobile(self, jd_url):
        mobile_url = self._build_jd_mobile_url(jd_url)
        if not mobile_url or self.external_checks_suspended:
            return None
        try:
            self._throttle_external_request()
            response = self.session.get(
                mobile_url,
                headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Encoding': 'identity',
                    'Referer': jd_url,
                    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
                                  'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148',
                },
                timeout=CONFIG['timeout'],
                allow_redirects=True
            )
            self._record_jd_fetch_debug('mobile', response)
            if self._is_waf_response(response):
                self._suspend_external_checks("京东移动商品页触发反爬/验证码")
                return None
            return self._extract_jd_mobile_is_self(response.text)
        except Exception as e:
            logging.warning(f"京东移动商品页请求异常: {e}")
            return None

    def _record_jd_fetch_debug(self, label, response):
        text = response.text or ''
        markers = []
        for marker in ['isSelf', 'shopName', '自营', 'passport', '验证', '风险']:
            if marker in text:
                markers.append(marker)
        prefix = re.sub(r'\s+', ' ', text[:80]).strip()
        if len(prefix) > 40:
            prefix = prefix[:40]
        self.jd_fetch_debug.append(
            f"{label}:http={response.status_code},ce={response.headers.get('content-encoding') or '-'},"
            f"len={len(text)},markers={','.join(markers) or '-'},head={prefix or '-'}"
        )

    def _jd_fetch_has_product_markers(self):
        return any('markers=-' not in item for item in self.jd_fetch_debug)

    def _is_known_jd_self_from_title(self, parsed):
        title = parsed.get('title', '')
        return any(keyword in title for keyword in CONFIG.get('jd_self_title_keywords', []))

    @staticmethod
    def _build_jd_canonical_url(jd_url):
        sku_id = SmzdmScraper._extract_jd_sku_id(jd_url)
        if not sku_id:
            return ''
        return f"https://item.jd.com/{sku_id}.html"

    @staticmethod
    def _build_jd_mobile_url(jd_url):
        sku_id = SmzdmScraper._extract_jd_sku_id(jd_url)
        if not sku_id:
            return ''
        return f"https://item.m.jd.com/product/{sku_id}.html"

    @staticmethod
    def _extract_jd_sku_id(jd_url):
        match = re.search(r'item(?:\.m)?\.jd\.com/(?:product/)?(\d+)\.html', jd_url)
        return match.group(1) if match else ''

    def _get_article_link(self, parsed):
        article_link = parsed.get('article_link')
        if article_link:
            return article_link

        article_id = parsed['id']
        if article_id in self.article_link_cache:
            return self.article_link_cache[article_id]

        endpoints = []
        if parsed.get('channel_type') == 'faxian':
            endpoints.append('faxian/list')
        elif parsed.get('channel_type') == 'youhui':
            endpoints.append('youhui/list')
        endpoints.extend(['youhui/list', 'faxian/list'])

        for endpoint in dict.fromkeys(endpoints):
            found = self._lookup_article_link_from_channel_api(article_id, endpoint)
            if found:
                self.article_link_cache[article_id] = found
                return found

        self.article_link_cache[article_id] = ''
        return ''

    def _lookup_article_link_from_channel_api(self, article_id, endpoint):
        endpoint_cache = self.channel_article_link_cache.setdefault(endpoint, {})
        if article_id in endpoint_cache:
            return endpoint_cache[article_id]
        if endpoint in self.channel_link_exhausted:
            return ''

        page_size = CONFIG['jd_link_lookup_page_size']
        start_page = self.channel_link_pages_loaded.get(endpoint, 0)
        for page in range(start_page, CONFIG['jd_link_lookup_pages']):
            offset = page * page_size
            url = f'https://api.smzdm.com/v1/{endpoint}?limit={page_size}&offset={offset}&version=2'
            try:
                response = self.session.get(url, timeout=CONFIG['timeout'])
                if response.status_code != 200:
                    continue
                resp_json = response.json()
            except Exception as e:
                logging.warning(f"article_link 回查失败 {endpoint} offset={offset}: {e}")
                continue

            rows = (resp_json.get('data') or {}).get('rows') if isinstance(resp_json, dict) else []
            if not rows:
                self.channel_link_exhausted.add(endpoint)
                break

            for item in rows:
                item_id = str(item.get('article_id', '')).strip()
                if item_id:
                    endpoint_cache[item_id] = str(item.get('article_link', '')).strip()
            self.channel_link_pages_loaded[endpoint] = page + 1

            if article_id in endpoint_cache:
                return endpoint_cache[article_id]
            time.sleep(0.1)
        return ''

    def _resolve_smzdm_go_link(self, article_link, referer):
        """解析 SMZDM go 链接到最终京东商品链接。"""
        if self.external_checks_suspended:
            return ''
        try:
            self._throttle_external_request()
            response = self.session.get(
                article_link,
                headers={
                    'Accept': 'application/json, text/plain, */*',
                    'Referer': referer or 'https://www.smzdm.com/',
                },
                timeout=CONFIG['timeout'],
                allow_redirects=False
            )
            if self._is_waf_response(response):
                self._suspend_external_checks("SMZDM 跳转页触发反爬/验证码")
                return ''
        except Exception as e:
            logging.warning(f"SMZDM 跳转页请求失败: {e}")
            return ''

        target = response.headers.get('Location', '')
        if not target:
            target = self._extract_smzdmhref(response.text)
        if not target:
            return ''

        if 'union-click.jd.com/jdc' in target:
            return self._resolve_jd_union_link(target)
        if 'item.jd.com/' in target or 'item.m.jd.com/' in target:
            return target
        return ''

    def _extract_smzdmhref(self, page_text):
        match = re.search(r"smzdmhref\s*=\s*['\"]([^'\"]+)['\"]", page_text)
        if match:
            return html.unescape(match.group(1))

        unpacked = self._unpack_packer_js(page_text)
        unpacked = unpacked.replace("\\'", "'").replace('\\"', '"').replace('\\/', '/')
        match = re.search(r"smzdmhref\s*=\s*['\"]([^'\"]+)['\"]", unpacked)
        if match:
            return html.unescape(match.group(1))
        return ''

    def _unpack_packer_js(self, page_text):
        match = re.search(
            r"eval\((function\(p,a,c,k,e,d\).*?\}\('(?P<payload>.*?)',(?P<base>\d+),(?P<count>\d+),'(?P<words>.*?)'\.split\('\|'\),0,\{\}\))\)",
            page_text,
            re.DOTALL
        )
        if not match:
            return ''

        payload = match.group('payload')
        base = int(match.group('base'))
        count = int(match.group('count'))
        words = match.group('words').split('|')
        if count > len(words):
            words.extend([''] * (count - len(words)))

        for index in range(count - 1, -1, -1):
            word = words[index]
            if not word:
                continue
            payload = re.sub(rf'\b{self._base_n(index, base)}\b', lambda _match: word, payload)
        return payload

    @staticmethod
    def _base_n(num, base):
        chars = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
        if num == 0:
            return '0'
        result = ''
        while num:
            num, rem = divmod(num, base)
            result = chars[rem] + result
        return result

    def _resolve_jd_union_link(self, union_url):
        if self.external_checks_suspended:
            return ''
        try:
            self._throttle_external_request()
            response = self.session.get(
                union_url,
                headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Referer': 'https://www.smzdm.com/',
                },
                timeout=CONFIG['timeout'],
                allow_redirects=False
            )
            if self._is_waf_response(response):
                self._suspend_external_checks("京东联盟链接触发反爬/验证码")
                return ''
        except Exception as e:
            logging.warning(f"京东联盟链接请求失败: {e}")
            return ''

        location = response.headers.get('Location', '')
        if location:
            return requests.compat.urljoin(union_url, location)

        match = re.search(r"https://union-click\.jd\.com/jda\?[^'\"<>]+", response.text)
        if not match:
            return ''

        jump_url = html.unescape(match.group(0))
        if 'h5st=' not in jump_url:
            jump_url = f"{jump_url}&h5st={self._js_hash_code(self.session.headers.get('User-Agent', ''))}"

        try:
            self._throttle_external_request()
            response = self.session.get(
                jump_url,
                headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Referer': 'https://www.smzdm.com/',
                },
                timeout=CONFIG['timeout'],
                allow_redirects=False
            )
            if self._is_waf_response(response):
                self._suspend_external_checks("京东联盟二跳触发反爬/验证码")
                return ''
        except Exception as e:
            logging.warning(f"京东联盟二跳请求失败: {e}")
            return ''

        location = response.headers.get('Location', '')
        if location:
            return requests.compat.urljoin(jump_url, location)
        return ''

    @staticmethod
    def _js_hash_code(value):
        result = 0
        for char in value:
            result = ((result << 5) - result + ord(char)) & 0xffffffff
        if result >= 0x80000000:
            result -= 0x100000000
        return result

    @staticmethod
    def _extract_jd_is_self(html_text):
        if re.search(r'''["']?\bisSelf\b["']?\s*:\s*true\b''', html_text):
            return True
        if re.search(r'''["']?\bisSelf\b["']?\s*:\s*false\b''', html_text):
            return False
        if (re.search(r'''(?:title|alt)\s*=\s*["'][^"']*自营[^"']*["']''', html_text)
                or re.search(r'''["'](?:shopName|vender)["']\s*:\s*["'][^"']*自营[^"']*["']''', html_text)):
            return True
        return None

    @staticmethod
    def _extract_jd_mobile_is_self(html_text):
        if re.search(r'''["']?\bisSelf\b["']?\s*:\s*true\b''', html_text):
            return True
        if re.search(r'''["']?\bisSelf\b["']?\s*:\s*false\b''', html_text):
            return False

        shop_names = re.findall(r'''["'](?:shopName|vender)["']\s*:\s*["']([^"']+)["']''', html_text)
        if any('自营' in name for name in shop_names):
            return True
        if re.search(r'''alt\s*=\s*["']自营["']''', html_text):
            return True
        if re.search(r'''["']po["']\s*:\s*false\b''', html_text) and '自营订单' in html_text:
            return True
        if shop_names and re.search(r'''["']po["']\s*:\s*true\b''', html_text):
            return False
        return None

    def _check_comment_user_levels(self, parsed):
        """根据评论 JSON 中的 vip_level 识别低等级账号占比。"""
        if self.external_checks_suspended:
            self._mark_comment_level_unavailable(parsed, 'external')
            logging.info(f"[评论等级跳过] {parsed['title'][:40]}... | 外部校验已熔断")
            return True

        if parsed.get('comments', 0) < CONFIG['comment_level_min_comments']:
            self._mark_comment_level_unavailable(parsed, 'low_comments')
            logging.info(
                f"[评论等级跳过] {parsed['title'][:40]}... | "
                f"列表评论数 {parsed.get('comments', 0)} 不足 {CONFIG['comment_level_min_comments']}"
            )
            return True

        if self.comment_level_checks >= self.comment_level_check_limit:
            self._mark_comment_level_unavailable(parsed, 'budget')
            logging.info(f"[评论等级跳过] {parsed['title'][:40]}... | 达到本轮评论等级校验上限")
            return True
        self.comment_level_checks += 1

        samples = self._fetch_comment_samples(parsed['id'])
        levels = [sample['level'] for sample in samples if sample.get('level') is not None]
        if len(samples) < CONFIG['comment_level_min_comments']:
            if parsed.get('comments', 0) >= CONFIG['comment_level_min_comments']:
                self._mark_comment_level_unavailable(parsed, 'sample')
                logging.info(
                    f"[评论等级不足，跳过] {parsed['title'][:40]}... | "
                    f"API评论:{parsed['comments']} 可取评论:{len(samples)}"
                )
            return True

        low_count = sum(1 for level in levels if level <= CONFIG['comment_level_low_max'])
        high_count = sum(1 for level in levels if level >= CONFIG['comment_level_high_min'])
        low_ratio = low_count / len(levels) if levels else 0
        user_stats = self._calculate_comment_user_stats(samples)

        parsed['comment_level_stats'] = {
            'count': len(samples),
            'low': low_count,
            'high': high_count,
            'low_ratio': round(low_ratio, 2),
            'unique_users': user_stats['unique_users'],
            'max_user_comments': user_stats['max_user_comments'],
            'max_user_ratio': round(user_stats['max_user_ratio'], 2),
        }

        if low_ratio > CONFIG['comment_level_max_low_ratio']:
            logging.warning(
                f"[评论等级水军过滤] {parsed['title'][:40]}... | "
                f"Lv<={CONFIG['comment_level_low_max']} {low_count}/{len(levels)}={low_ratio:.0%}, "
                f"Lv>={CONFIG['comment_level_high_min']} {high_count}"
            )
            return False

        if (len(samples) >= CONFIG['comment_concentration_min_comments']
                and user_stats['unique_users'] < CONFIG['comment_concentration_min_users']):
            logging.warning(
                f"[评论集中水军过滤] {parsed['title'][:40]}... | "
                f"独立用户:{user_stats['unique_users']}/{len(samples)}"
            )
            return False

        if (len(samples) >= CONFIG['comment_concentration_min_comments']
                and user_stats['max_user_ratio'] > CONFIG['comment_concentration_max_user_ratio']):
            logging.warning(
                f"[评论集中水军过滤] {parsed['title'][:40]}... | "
                f"最高单用户:{user_stats['max_user_comments']}/{len(samples)}={user_stats['max_user_ratio']:.0%}"
            )
            return False

        parsed['comment_level_status'] = 'passed'
        parsed.pop('comment_level_unavailable_reason', None)
        logging.info(
            f"[评论等级通过] {parsed['title'][:40]}... | "
            f"Lv<={CONFIG['comment_level_low_max']} {low_count}/{len(levels)}={low_ratio:.0%}, "
            f"Lv>={CONFIG['comment_level_high_min']} {high_count} | "
            f"独立用户:{user_stats['unique_users']}/{len(samples)}"
        )
        return True

    def _mark_comment_level_unavailable(self, parsed, reason):
        parsed['comment_level_status'] = 'unavailable'
        parsed['comment_level_unavailable_reason'] = reason
        self.stats['total_comment_level_unavailable'] += 1

        stat_key = {
            'budget': 'total_comment_level_unavailable_budget',
            'sample': 'total_comment_level_unavailable_sample',
            'low_comments': 'total_comment_level_unavailable_low_comments',
            'external': 'total_comment_level_unavailable_external',
        }.get(reason)
        if stat_key:
            self.stats[stat_key] += 1

    def _should_defer_for_comment_unavailable(self, parsed):
        if parsed.get('comment_level_status') != 'unavailable':
            return False

        pending = self._get_pending_review(parsed)
        pending_count = pending['unavailable_count'] if pending else 0
        quality_path = parsed.get('quality_path')
        reason = parsed.get('comment_level_unavailable_reason', 'unknown')

        if (reason == 'budget'
                and quality_path in ('均衡热度', '高讨论')
                and self._is_strong_budget_pass(parsed)):
            self.stats['total_comment_level_budget_strong_allowed'] += 1
            logging.info(
                f"[评论预算不足强信号放行] {parsed['title'][:40]}... | "
                f"路径:{quality_path} 评分:{parsed.get('composite_score', 0)} 评论:{parsed.get('comments', 0)}"
            )
            return False

        if reason == 'budget':
            logging.info(
                f"[评论预算不足暂缓] {parsed['title'][:40]}... | "
                f"路径:{quality_path} 评分:{parsed.get('composite_score', 0)} 评论:{parsed.get('comments', 0)}"
            )
            return True

        if quality_path == '早期好价' and CONFIG.get('defer_emerging_when_comment_unavailable'):
            logging.info(
                f"[早期好价暂缓] {parsed['title'][:40]}... | "
                f"评论等级未确认:{reason}，已暂缓 {pending_count} 轮"
            )
            return True

        if (quality_path in ('均衡热度', '高讨论')
                and reason in CONFIG['fallback_allowed_reasons']
                and pending_count >= CONFIG['pending_review_fallback_runs']):
            self.stats['total_comment_level_fallback_allowed'] += 1
            logging.info(
                f"[评论等级兜底放行] {parsed['title'][:40]}... | "
                f"已暂缓 {pending_count} 轮仍不可用，当前路径:{quality_path} 原因:{reason}"
            )
            return False

        if quality_path in ('均衡热度', '高讨论'):
            logging.info(
                f"[评论等级继续暂缓] {parsed['title'][:40]}... | "
                f"已暂缓 {pending_count} 轮，当前路径:{quality_path}，原因:{reason}"
            )
            return True

        return False

    def _is_strong_budget_pass(self, parsed):
        return (
            parsed.get('composite_score', 0) >= CONFIG['budget_strong_pass_min_score']
            and parsed.get('comments', 0) >= CONFIG['budget_strong_pass_min_comments']
        )

    def _fetch_comment_samples(self, article_id):
        url = f'https://haojia.m.smzdm.com/detail_modul/user_related_modul?article_id={article_id}'
        try:
            self._throttle_external_request()
            response = self.session.get(
                url,
                headers={
                    'Accept': 'application/json, text/plain, */*',
                    'Referer': f'https://www.smzdm.com/p/{article_id}/',
                },
                timeout=CONFIG['timeout']
            )
            if self._is_waf_response(response):
                self._suspend_external_checks("评论等级接口触发反爬/验证码")
                return []
            if response.status_code != 200:
                logging.warning(f"评论等级接口 HTTP {response.status_code}: {article_id}")
                return []
            resp_json = response.json()
        except Exception as e:
            logging.warning(f"评论等级接口请求失败 {article_id}: {e}")
            return []

        samples = []
        self._collect_comment_samples(resp_json, samples)
        return self._dedupe_comment_samples(samples)

    def _collect_comment_samples(self, node, samples):
        if isinstance(node, dict):
            if 'comment_id' in node and 'vip_level' in node and not node.get('display_author'):
                user_id = str(node.get('user_smzdm_id') or node.get('display_name') or '').strip()
                level = None
                try:
                    level = int(node['vip_level'])
                except (TypeError, ValueError):
                    pass
                samples.append({
                    'level': level,
                    'user_id': user_id or f"comment:{node.get('comment_id')}",
                    'comment_id': str(node.get('comment_id', '')),
                })
            for value in node.values():
                self._collect_comment_samples(value, samples)
        elif isinstance(node, list):
            for value in node:
                self._collect_comment_samples(value, samples)

    @staticmethod
    def _calculate_comment_user_stats(samples):
        counts = {}
        for sample in samples:
            user_id = sample.get('user_id') or sample.get('comment_id') or 'unknown'
            counts[user_id] = counts.get(user_id, 0) + 1
        max_count = max(counts.values()) if counts else 0
        total = len(samples)
        return {
            'unique_users': len(counts),
            'max_user_comments': max_count,
            'max_user_ratio': max_count / total if total else 0,
        }

    @staticmethod
    def _dedupe_comment_samples(samples):
        deduped = []
        seen_ids = set()
        for sample in samples:
            comment_id = sample.get('comment_id')
            if comment_id:
                if comment_id in seen_ids:
                    continue
                seen_ids.add(comment_id)
            deduped.append(sample)
        return deduped

    def _throttle_external_request(self):
        time.sleep(random.uniform(*CONFIG['external_request_delay']))

    def _is_waf_response(self, response):
        if response.status_code in CONFIG['waf_status_codes']:
            return True
        content_type = response.headers.get('content-type', '')
        text = response.text[:5000].lower()
        looks_like_html = 'text/html' in content_type or text.lstrip().startswith(('<!doctype', '<html', '<script'))
        if not looks_like_html:
            return False
        return any(marker.lower() in text for marker in CONFIG['waf_markers'])

    def _suspend_external_checks(self, reason):
        if not self.external_checks_suspended:
            self.external_checks_suspended = True
            self.stats['total_external_checks_suspended'] += 1
            logging.warning(f"[外部校验熔断] {reason}，本轮停止详情/跳转类请求")

    def _check_shill(self, parsed):
        """第四阶段：互动数据水军检测兜底

        水军贴典型特征：
        1. 值票极高但几乎无不值票（正常商品会有一定反对声音）
        2. 值票多但评论极少（真实用户投票的同时通常也会评论）
        """
        worthy = parsed['worthy']
        unworthy = parsed['unworthy']
        comments = parsed['comments']
        total_votes = worthy + unworthy

        # 投票数太少，无法判断，放行
        if total_votes < CONFIG['shill_min_votes_for_check']:
            return True

        reasons = []

        # 检查1：值/不值比例异常
        if unworthy == 0:
            wu_ratio = float('inf')
        else:
            wu_ratio = worthy / unworthy

        if wu_ratio > CONFIG['shill_max_worthy_unworthy_ratio']:
            reasons.append(f"值/不值比异常: {worthy}/{unworthy}")

        # 检查2：有票无评论
        if worthy > 0:
            comment_ratio = comments / worthy
            if comment_ratio < CONFIG['shill_min_comment_worthy_ratio']:
                reasons.append(f"评论/值票比过低: {comments}/{worthy}={comment_ratio:.2f}")

        if len(reasons) >= CONFIG['shill_min_flags']:
            logging.warning(
                f"[水军嫌疑] {parsed['title'][:40]}... | "
                + " | ".join(reasons)
            )
            return False

        return True

    # ==================== 推送 ====================

    def _send_notification(self, data):
        """WXPusher 微信推送"""
        content = self._build_notification_content(data)

        try:
            response = requests.post(
                'https://wxpusher.zjiecode.com/api/send/message',
                json={
                    "appToken": APP_TOKEN,
                    "content": content,
                    "contentType": 2,
                    "uids": [UID]
                },
                timeout=10
            )
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    logging.info(f"[推送成功] {data['title'][:40]}...")
                    return True
                else:
                    logging.error(f"[推送失败] {result.get('msg', 'unknown')}")
                    return False
            else:
                logging.error(f"[推送失败] HTTP {response.status_code}")
                return False
        except Exception as e:
            logging.error(f"[推送异常] {e}")
            return False

    def _build_notification_content(self, data):
        """生成 WXPusher HTML 内容。"""
        score_rate = data.get('score_rate', 0)
        composite_score = data.get('composite_score', 0)
        quality_path = data.get('quality_path', '综合筛选')
        level_stats = data.get('comment_level_stats') or {}
        price_drop_note = data.get('price_drop_note')

        if composite_score >= 100:
            score_tag = '🔥爆'
            score_label = '高热度'
        elif composite_score >= 60:
            score_tag = '👍热'
            score_label = '值得看'
        else:
            score_tag = '🆕新'
            score_label = '早期信号'

        title = html.escape(str(data.get('title', '未知商品')), quote=True)
        mall = html.escape(str(data.get('mall', '未知')), quote=True)
        price = html.escape(str(data.get('price', '未知')), quote=True)
        pub_time = html.escape(str(data.get('pub_time', '')), quote=True)
        link = html.escape(str(data.get('link', '')), quote=True)
        quality_path = html.escape(str(quality_path), quote=True)

        level_line = ''
        if level_stats:
            low = level_stats.get('low', 0)
            count = level_stats.get('count', 0)
            high = level_stats.get('high', 0)
            low_ratio = int(level_stats.get('low_ratio', 0) * 100)
            unique_users = level_stats.get('unique_users', 0)
            max_user_comments = level_stats.get('max_user_comments', 0)
            level_line = (
                f"👤 评论等级：Lv5及以下 {low}/{count}（{low_ratio}%）"
                f" | Lv6及以上 {high}<br>"
                f"👥 评论用户：独立 {unique_users}/{count} | 单人最多 {max_user_comments}<br>"
            )
        price_drop_line = ''
        if price_drop_note:
            price_drop_line = f"📉 降价：{html.escape(str(price_drop_note), quote=True)}<br>"

        return f"""
        <div style="font-size:15px;line-height:1.65;">
          <div style="font-size:17px;font-weight:bold;margin-bottom:8px;">
            {score_tag}【{mall}】{title}
          </div>
          <div>
            💰 <span style="color:#e62828;font-size:18px;font-weight:bold;">{price}</span>
            <span style="color:#666;">（{score_label} / {quality_path}）</span>
          </div>
          <hr style="border:none;border-top:1px solid #eee;margin:10px 0;">
          🕒 发布：{pub_time}<br>
          📊 好评率：{score_rate}% | 综合评分：{composite_score}<br>
          {price_drop_line}
          👍 值：{data['worthy']} | 👎 不值：{data['unworthy']}<br>
          💬 评论：{data['comments']} | ⭐ 收藏：{data['collection']}<br>
          {level_line}
          <div style="margin-top:10px;">
            🔗 <a href="{link}">查看 SMZDM 详情</a>
          </div>
        </div>
        """

    # ==================== 数据库 ====================

    def _is_duplicate(self, article_id):
        if isinstance(article_id, dict):
            parsed = article_id
            if parsed['id'] in self.seen_ids:
                return True
            # 只拦截历史中已成功推送过的相似商品；未推送商品不会写入指纹，后续扫描仍可复评。
            return self._is_fingerprint_duplicate(parsed)
        return article_id in self.seen_ids

    def _is_fingerprint_duplicate(self, parsed):
        fingerprint = parsed.get('fingerprint')
        if fingerprint and fingerprint in self.seen_fingerprints:
            if self._is_meaningful_price_drop(parsed):
                return False
            self.stats['total_fingerprint_duplicates'] += 1
            logging.info(f"[相似商品重复跳过] {parsed['title'][:40]}... | 指纹:{fingerprint}")
            return True
        return False

    def _is_meaningful_price_drop(self, parsed):
        fingerprint = parsed.get('fingerprint')
        new_price = parsed.get('price_value')
        old_price = self.fingerprint_min_prices.get(fingerprint)
        if new_price is None or old_price is None or old_price <= 0:
            return False

        drop_amount = old_price - new_price
        drop_percent = drop_amount / old_price * 100
        if (drop_amount >= CONFIG['price_drop_min_amount']
                or drop_percent >= CONFIG['price_drop_min_percent']):
            parsed['price_drop_note'] = f"较历史推送低 {drop_amount:.2f} 元（{drop_percent:.1f}%）"
            logging.info(
                f"[相似商品降价放行] {parsed['title'][:40]}... | "
                f"历史:{old_price:.2f} 新价:{new_price:.2f} 降幅:{drop_percent:.1f}%"
            )
            return True
        return False

    def _pending_review_key(self, parsed):
        return parsed.get('fingerprint') or f"id:{parsed.get('id')}"

    def _get_pending_review(self, parsed):
        review_key = self._pending_review_key(parsed)
        if not review_key:
            return None

        cursor = self.conn.execute(
            '''
            SELECT unavailable_count, last_quality_path, last_reason
            FROM pending_reviews
            WHERE review_key = ?
            ''',
            (review_key,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            'unavailable_count': int(row[0] or 0),
            'last_quality_path': row[1],
            'last_reason': row[2],
        }

    def _save_pending_review(self, parsed):
        review_key = self._pending_review_key(parsed)
        if not review_key:
            return

        pending = self._get_pending_review(parsed)
        unavailable_count = (pending['unavailable_count'] if pending else 0) + 1
        reason = parsed.get('comment_level_unavailable_reason', 'unknown')
        self.conn.execute(
            '''
            INSERT INTO pending_reviews (
                review_key, article_id, title, fingerprint, mall,
                unavailable_count, first_seen, last_seen, last_quality_path, last_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?)
            ON CONFLICT(review_key) DO UPDATE SET
                article_id = excluded.article_id,
                title = excluded.title,
                fingerprint = excluded.fingerprint,
                mall = excluded.mall,
                unavailable_count = excluded.unavailable_count,
                last_seen = CURRENT_TIMESTAMP,
                last_quality_path = excluded.last_quality_path,
                last_reason = excluded.last_reason
            ''',
            (
                review_key,
                parsed.get('id'),
                parsed.get('title', '')[:100],
                parsed.get('fingerprint', ''),
                parsed.get('mall', ''),
                unavailable_count,
                parsed.get('quality_path', ''),
                reason,
            )
        )
        self.conn.commit()
        logging.info(
            f"[暂缓复评记录] {parsed['title'][:40]}... | "
            f"key:{review_key[:40]} 次数:{unavailable_count} 原因:{reason}"
        )

    def _clear_pending_review(self, parsed):
        review_key = self._pending_review_key(parsed)
        if not review_key:
            return
        self.conn.execute("DELETE FROM pending_reviews WHERE review_key = ?", (review_key,))
        self.conn.commit()

    def _save_history(self, data):
        try:
            self.conn.execute(
                '''
                INSERT OR REPLACE INTO history (id, title, fingerprint, mall, price, price_value, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''',
                (
                    data['id'],
                    data['title'][:100],
                    data.get('fingerprint', ''),
                    data.get('mall', ''),
                    data.get('price', ''),
                    data.get('price_value'),
                )
            )
            self.conn.commit()
            self.seen_ids.add(data['id'])
            if data.get('fingerprint'):
                self.seen_fingerprints.add(data['fingerprint'])
                price_value = data.get('price_value')
                if price_value is not None:
                    old_price = self.fingerprint_min_prices.get(data['fingerprint'])
                    if old_price is None or price_value < old_price:
                        self.fingerprint_min_prices[data['fingerprint']] = price_value
        except Exception as e:
            logging.error(f"保存历史失败: {e}")

    def _clean_old_records(self):
        cutoff = datetime.now() - timedelta(days=30)
        cursor = self.conn.execute("DELETE FROM history WHERE last_seen < ?", (cutoff,))
        deleted = cursor.rowcount

        pending_cutoff = datetime.now() - timedelta(days=CONFIG['pending_review_keep_days'])
        pending_cursor = self.conn.execute("DELETE FROM pending_reviews WHERE last_seen < ?", (pending_cutoff,))
        pending_deleted = pending_cursor.rowcount

        self.conn.commit()
        if deleted > 0:
            logging.info(f"清理 {deleted} 条过期记录")
        if pending_deleted > 0:
            logging.info(f"清理 {pending_deleted} 条过期待复评记录")

    # ==================== 辅助 ====================

    def _print_statistics(self):
        logging.info("=" * 50)
        logging.info(f"扫描完成:")
        logging.info(f"  获取商品: {self.stats['total_fetched']}")
        logging.info(f"  重复跳过: {self.stats['total_duplicates']}")
        logging.info(f"  相似商品重复: {self.stats['total_fingerprint_duplicates']}")
        logging.info(f"  综合评分过滤: {self.stats['total_filtered_stage1']}")
        logging.info(f"  京东非自营过滤: {self.stats['total_filtered_jd_self']}")
        if CONFIG['jd_self_filter_enabled']:
            logging.info(f"  京东自营校验: {self.jd_self_checks}/{self.jd_self_check_limit}")
        else:
            logging.info("  京东自营校验: 关闭")
        logging.info(f"  评论等级水军过滤: {self.stats['total_filtered_comment_level']}")
        logging.info(f"  评论等级不可用: {self.stats['total_comment_level_unavailable']}")
        logging.info(f"    预算不足: {self.stats['total_comment_level_unavailable_budget']}")
        logging.info(f"    样本不足: {self.stats['total_comment_level_unavailable_sample']}")
        logging.info(f"    列表评论不足: {self.stats['total_comment_level_unavailable_low_comments']}")
        logging.info(f"    外部熔断: {self.stats['total_comment_level_unavailable_external']}")
        logging.info(f"  评论等级暂缓复评: {self.stats['total_comment_level_deferred']}")
        logging.info(f"  评论等级兜底放行: {self.stats['total_comment_level_fallback_allowed']}")
        logging.info(f"  评论预算不足强信号放行: {self.stats['total_comment_level_budget_strong_allowed']}")
        logging.info(f"  互动数据水军过滤: {self.stats['total_filtered_shill']}")
        logging.info(f"  外部校验熔断: {self.stats['total_external_checks_suspended']}")
        logging.info(f"  成功推送: {self.stats['total_sent']}")
        logging.info("=" * 50)

    def _cleanup(self):
        if hasattr(self, 'conn'):
            self.conn.close()
        if hasattr(self, 'session'):
            self.session.close()


def main():
    scraper = SmzdmScraper()
    try:
        scraper.run()
    except KeyboardInterrupt:
        logging.warning("用户终止")
    except Exception as e:
        logging.error(f"运行异常: {e}")
        sys.exit(1)
    finally:
        scraper._cleanup()


if __name__ == "__main__":
    main()
