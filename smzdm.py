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
    "max_pages": 150,                   # 每次运行最多扫描页数 (放宽以扫描历史数据)
    "max_history_hours": 3,             # 最多扫描过去多少小时内的数据
    "blacklist_channels": ["文章", "资讯", "社区", "晒物"], # 黑名单频道，直接跳过
    "items_per_page": 20,

    # 第一阶段：互动数据筛选
    "min_comments": 5,
    "min_collection": 5,
    "min_worthy": 5,
    "min_score_rate": 70,               # 好评率 %

    # 第二阶段：水军检测（基于互动数据异常分析，需同时满足多项）
    "shill_detection_enabled": True,
    "shill_min_votes_for_check": 30,        # 总投票数少于此值时跳过水军检测
    "shill_max_worthy_unworthy_ratio": 30,  # 值/不值比超过此值则可疑指标+1
    "shill_min_comment_worthy_ratio": 0.15, # 评论数/值票数低于此值则可疑指标+1
    "shill_min_flags": 2,                   # 至少N个可疑指标才标记为水军

    # 请求参数
    "request_delay": (1, 3),            # 随机延迟范围（秒）
    "timeout": 15,
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
        self._load_existing_ids()

        self.stats = {
            'total_fetched': 0,
            'total_sent': 0,
            'total_duplicates': 0,
            'total_filtered_stage1': 0,
            'total_filtered_shill': 0,
        }

    def _init_session(self):
        retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
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
                last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.conn.commit()

    def _load_existing_ids(self):
        cursor = self.conn.execute("SELECT id FROM history")
        self.seen_ids = {row[0] for row in cursor.fetchall()}
        logging.info(f"已加载 {len(self.seen_ids)} 条历史记录")

    # ==================== 扫描 ====================

    def run(self):
        """单次执行入口：扫描 -> 筛选 -> 推送 -> 退出"""
        if not APP_TOKEN or not UID:
            logging.error("缺少 WXPUSHER_APP_TOKEN 或 WXPUSHER_UID 环境变量")
            sys.exit(1)

        logging.info("开始扫描...")
        self._clean_old_records()

        stop_scanning = False

        for page in range(1, CONFIG['max_pages'] + 1):
            if stop_scanning:
                break

            items = self._fetch_page(page)
            if not items:
                logging.info(f"第{page}页无数据，停止扫描")
                break

            self.stats['total_fetched'] += len(items)

            for item in items:
                parsed = self._parse_item(item)
                if not parsed:
                    continue

                # 时间窗口检查 (超过指定小时数则停止扫描后续)
                if parsed.get('age_hours', 0) > CONFIG['max_history_hours']:
                    logging.info(f"扫到 {parsed['age_hours']:.1f} 小时前的数据，达到时间上限 {CONFIG['max_history_hours']} 小时，停止扫描。")
                    stop_scanning = True
                    break

                if self._is_duplicate(parsed['id']):
                    self.stats['total_duplicates'] += 1
                    continue

                # 第一阶段：互动数据筛选
                if not self._filter_stage1(parsed):
                    self.stats['total_filtered_stage1'] += 1
                    continue

                # 第二阶段：水军检测
                if CONFIG['shill_detection_enabled'] and not self._check_shill(parsed):
                    self.stats['total_filtered_shill'] += 1
                    continue

                # 推送
                if self._send_notification(parsed):
                    self._save_history(parsed)
                    self.stats['total_sent'] += 1

            time.sleep(random.uniform(*CONFIG['request_delay']))

        self._print_statistics()
        self._cleanup()

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
                return []
            data = response.json().get('data', {})
            return data.get('rows', [])
        except Exception as e:
            logging.error(f"第{page}页请求异常: {e}")
            return []

    def _parse_item(self, item):
        """解析单个商品数据"""
        if not item or 'article_id' not in item:
            return None

        # 频道黑名单过滤
        channel = str(item.get('article_channel_name', '')).strip()
        if channel in CONFIG.get('blacklist_channels', []):
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

        return {
            'id': article_id,
            'title': title,
            'price': str(item.get('article_price', '未知')).strip(),
            'link': str(item.get('article_url', '')).strip(),
            'mall': str(item.get('article_mall', '未知')).strip(),
            'pub_time': str(item.get('article_format_date', '')).strip(),
            'comments': tongji['comments'] or comments,
            'collection': tongji['collection'],
            'worthy': tongji['worthy'] or worthy,
            'unworthy': tongji['unworthy'] or unworthy,
            'age_hours': age_hours,
        }

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

    # ==================== 筛选 ====================

    def _filter_stage1(self, parsed):
        """第一阶段：互动数据筛选"""
        comments = parsed['comments']
        collection = parsed['collection']
        worthy = parsed['worthy']
        unworthy = parsed['unworthy']

        # 所有互动指标都需达标
        if comments < CONFIG['min_comments']:
            return False
        if collection < CONFIG['min_collection']:
            return False
        if worthy < CONFIG['min_worthy']:
            return False

        # 好评率检查
        total_votes = worthy + unworthy
        if total_votes > 0:
            score_rate = worthy / total_votes * 100
            if score_rate < CONFIG['min_score_rate']:
                return False

        logging.info(
            f"[粗筛通过] {parsed['title'][:40]}... | "
            f"评论:{comments} 收藏:{collection} 值:{worthy} 不值:{unworthy}"
        )
        return True

    def _check_shill(self, parsed):
        """第二阶段：水军检测 - 基于互动数据异常分析

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

        # 检查1：值/不值比例异常（水军只刷值不刷不值）
        if unworthy == 0:
            wu_ratio = float('inf')
        else:
            wu_ratio = worthy / unworthy

        if wu_ratio > CONFIG['shill_max_worthy_unworthy_ratio']:
            reasons.append(f"值/不值比异常: {worthy}/{unworthy}")

        # 检查2：有票无评论（水军刷值票但不写评论）
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
        content = f"""
        <b>【{data['mall']}】{data['title']}</b>
        <br><br>
        💰 价格：<span style='color:#e62828;font-weight:bold;'>{data['price']}</span><br>
        🕒 发布：{data['pub_time']}<br>
        💬 评论：{data['comments']} | ⭐ 收藏：{data['collection']}<br>
        👍 值：{data['worthy']} | 👎 不值：{data['unworthy']}<br>
        <br>
        🔗 <a href='{data['link']}'>点击查看详情</a>
        """

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

    # ==================== 数据库 ====================

    def _is_duplicate(self, article_id):
        return article_id in self.seen_ids

    def _save_history(self, data):
        try:
            self.conn.execute(
                'INSERT OR REPLACE INTO history (id, title) VALUES (?, ?)',
                (data['id'], data['title'][:100])
            )
            self.conn.commit()
            self.seen_ids.add(data['id'])
        except Exception as e:
            logging.error(f"保存历史失败: {e}")

    def _clean_old_records(self):
        cutoff = datetime.now() - timedelta(days=30)
        cursor = self.conn.execute("DELETE FROM history WHERE last_seen < ?", (cutoff,))
        deleted = cursor.rowcount
        self.conn.commit()
        if deleted > 0:
            logging.info(f"清理 {deleted} 条过期记录")

    # ==================== 辅助 ====================

    def _print_statistics(self):
        logging.info("=" * 50)
        logging.info(f"扫描完成:")
        logging.info(f"  获取商品: {self.stats['total_fetched']}")
        logging.info(f"  重复跳过: {self.stats['total_duplicates']}")
        logging.info(f"  粗筛过滤: {self.stats['total_filtered_stage1']}")
        logging.info(f"  水军过滤: {self.stats['total_filtered_shill']}")
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
