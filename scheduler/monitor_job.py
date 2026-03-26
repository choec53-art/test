"""
모니터링 스케줄러
APScheduler를 사용하여 주기적으로 크롤링 → 분석 → 알림을 실행합니다.
"""

import logging
import os

from dotenv import load_dotenv

from config import SEARCH_KEYWORDS, SCHEDULE_INTERVAL_MINUTES, SEARCH_DISPLAY, SEARCH_DAYS
from crawler.naver_crawler import NaverCrawler
from analyzer.content_analyzer import ContentAnalyzer
from storage.database import Database
from notifier.email_notifier import EmailNotifier

load_dotenv()

logger = logging.getLogger(__name__)


class MonitorJob:
    """단일 모니터링 사이클"""

    def __init__(self):
        self.crawler = NaverCrawler(display=SEARCH_DISPLAY)
        self.analyzer = ContentAnalyzer()
        self.db = Database()
        self.notifier = EmailNotifier()

    def run(self):
        """크롤링 → 분석 → 저장 → 알림 파이프라인 실행"""
        logger.info("=== 모니터링 사이클 시작 ===")

        # 1. 기존 수집 링크 로드 → 크롤러에 전달하여 카페 게시글도 필터링
        known_links = self.db.get_known_links()
        posts = self.crawler.collect_all(
            SEARCH_KEYWORDS, days=SEARCH_DAYS, known_links=known_links,
        )

        # 2. 신규 게시글 저장
        new_posts = []
        for post in posts:
            if self.db.save_post(post):
                new_posts.append(post)

        logger.info("신규 게시글: %d건 (전체 수집: %d건)", len(new_posts), len(posts))

        if not new_posts:
            logger.info("신규 게시글 없음 — 사이클 종료")
            return

        # 3. 분석
        summary = self.analyzer.analyze_batch(new_posts)

        # 4. 탐지 결과 저장
        for result in summary.results:
            self.db.save_detection(result)

        # 5. 이메일 알림
        if summary.results:
            success = self.notifier.send(summary.results)
            for r in summary.results:
                status = "success" if success else "failed"
                for recipient in self.notifier.recipients:
                    self.db.save_notification(
                        recipient=recipient,
                        subject=f"부적절 게시글 {len(summary.results)}건 탐지",
                        post_count=len(summary.results),
                        status=status,
                    )

        stats = self.db.get_stats()
        logger.info(
            "=== 사이클 완료 | 누적 수집: %d건, 누적 탐지: %d건 ===",
            stats["total_posts"],
            stats["total_detections"],
        )


def run_scheduler():
    """APScheduler로 주기 실행"""
    from apscheduler.schedulers.blocking import BlockingScheduler

    job = MonitorJob()
    scheduler = BlockingScheduler(timezone="Asia/Seoul")
    scheduler.add_job(
        job.run,
        trigger="interval",
        minutes=SCHEDULE_INTERVAL_MINUTES,
        id="monitor_job",
    )

    logger.info(
        "스케줄러 시작 — 실행 주기: %d분마다", SCHEDULE_INTERVAL_MINUTES
    )

    # 시작 즉시 1회 실행
    job.run()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")
