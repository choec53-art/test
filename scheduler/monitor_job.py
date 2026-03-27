"""
모니터링 스케줄러
APScheduler를 사용하여 주기적으로 크롤링 → 분석 → 알림을 실행합니다.
"""

import logging

from config import SEARCH_KEYWORDS, SCHEDULE_INTERVAL_MINUTES, SEARCH_DISPLAY, SEARCH_DAYS
from crawler.naver_crawler import NaverCrawler
from crawler.content_scraper import ContentScraper
from analyzer.content_analyzer import ContentAnalyzer
from storage import create_storage
from notifier.email_notifier import EmailNotifier

logger = logging.getLogger(__name__)


class MonitorJob:
    """단일 모니터링 사이클"""

    def __init__(self):
        self.crawler = NaverCrawler(display=SEARCH_DISPLAY)
        self.scraper = ContentScraper()
        self.analyzer = ContentAnalyzer()
        self.db = create_storage()
        self.notifier = EmailNotifier()

    def run(self):
        """크롤링 → 분석 → 저장 → 알림 파이프라인 실행"""
        logger.info("=== 모니터링 사이클 시작 ===")
        try:
            # 1. 기존 수집 링크 로드
            known_links = self.db.get_known_links()
            logger.info("[1/5] known_links 로드: %d건", len(known_links))

            # 2. 크롤링
            posts = self.crawler.collect_all(
                SEARCH_KEYWORDS, days=SEARCH_DAYS, known_links=known_links,
            )
            logger.info("[2/5] 크롤링 완료: %d건 수집 (키워드 %d개, 기간 %d일)",
                        len(posts), len(SEARCH_KEYWORDS), SEARCH_DAYS)

            # 3. 신규 게시글 저장
            new_posts = []
            dup_count = 0
            for post in posts:
                if self.db.save_post(post):
                    new_posts.append(post)
                else:
                    dup_count += 1

            logger.info("[3/6] DB 저장: 신규 %d건, 중복 스킵 %d건", len(new_posts), dup_count)

            if not new_posts:
                stats = self.db.get_stats()
                logger.info("=== 사이클 종료 (신규 없음) | 누적 수집: %d건, 누적 탐지: %d건 ===",
                            stats["total_posts"], stats["total_detections"])
                return

            # 4. 키워드 매칭된 게시글만 전문 스크래핑
            scrape_count = 0
            scrape_failures: list[dict] = []
            for post in new_posts:
                raw = f"{post.title} {post.description}"
                categories, matched_kws = self.analyzer.keyword_filter(raw)
                if matched_kws:
                    full_text = self.scraper.scrape(post)
                    if full_text:
                        post.full_content = full_text
                        self.db.update_post_full_content(post.link, full_text)
                        scrape_count += 1
                        logger.debug("  전문 스크래핑 성공: %s (%d자)", post.title[:30], len(full_text))
                    else:
                        scrape_failures.append({
                            "url": post.link,
                            "source": post.source,
                            "title": post.title,
                            "error": "selector 매칭 실패 (DOM 구조 변경 가능성)",
                        })
                        logger.debug("  전문 스크래핑 실패 (description 사용): %s", post.title[:30])
            logger.info("[4/6] 전문 스크래핑: 대상 %d건, 성공 %d건, 실패 %d건",
                        scrape_count + len(scrape_failures), scrape_count, len(scrape_failures))

            # 스크래핑 실패 경고 메일
            if scrape_failures:
                self.notifier.send_scrape_alert(scrape_failures)

            # 5. 분석
            summary = self.analyzer.analyze_batch(new_posts)
            logger.info("[5/6] 분석 완료: 전체 %d건, 키워드 매칭 → LLM 호출 %d건, 부적절 판정 %d건",
                        summary.total_checked,
                        self.analyzer._total_requests,
                        summary.inappropriate_count)

            # 6. 탐지 결과 저장
            for result in summary.results:
                self.db.save_detection(result)
                logger.info("  [탐지] %s | %s | 심각도=%s | 점수=%.2f | 사유=%s",
                            result.post.source, result.post.title[:40],
                            result.severity, result.hybrid_score, result.ai_reason[:50])

            # 7. 이메일 알림
            if summary.results:
                success = self.notifier.send(summary.results)
                status = "success" if success else "failed"
                logger.info("[6/6] 이메일 발송: %s (%d건 → %s)",
                            status, len(summary.results), self.notifier.recipients)
                for recipient in self.notifier.recipients:
                    self.db.save_notification(
                        recipient=recipient,
                        subject=f"부적절 게시글 {len(summary.results)}건 탐지",
                        post_count=len(summary.results),
                        status=status,
                    )
            else:
                logger.info("[6/6] 부적절 게시글 없음 — 이메일 발송 생략")

            stats = self.db.get_stats()
            logger.info(
                "=== 사이클 완료 | 신규 %d건, 탐지 %d건 | 누적 수집: %d건, 누적 탐지: %d건 ===",
                len(new_posts), summary.inappropriate_count,
                stats["total_posts"], stats["total_detections"],
            )
        except Exception as e:
            logger.error("모니터링 사이클 실패: %s", e, exc_info=True)


    def run_daily_report(self):
        """일일 리포트 생성 및 발송 (19:00 실행)"""
        logger.info("=== 일일 리포트 생성 시작 ===")
        try:
            summary = self.db.get_daily_summary()
            success = self.notifier.send_daily_report(summary)
            status = "success" if success else "failed"
            for recipient in self.notifier.recipients:
                self.db.save_notification(
                    recipient=recipient,
                    subject=f"일일 리포트 [{summary['date']}]",
                    post_count=summary["today_posts"],
                    status=status,
                )
            logger.info(
                "=== 일일 리포트 완료 | 금일 수집: %d건, 금일 탐지: %d건, 누적: %d건 ===",
                summary["today_posts"],
                summary["today_detections"],
                summary["total_posts"],
            )
        except Exception as e:
            logger.error("일일 리포트 실패: %s", e, exc_info=True)


def run_scheduler():
    """APScheduler로 cron 기반 실행

    - 모니터링: 월~토 09:00~18:00 매시 정각
    - 일일 리포트: 월~토 19:00
    """
    from apscheduler.schedulers.blocking import BlockingScheduler

    job = MonitorJob()
    scheduler = BlockingScheduler(timezone="Asia/Seoul")

    # 모니터링 사이클: 월~토, 09:00~18:59 매 10분
    scheduler.add_job(
        job.run,
        trigger="cron",
        day_of_week="mon-sat",
        hour="9-18",
        minute="*/10",
        id="monitor_job",
    )

    # 일일 리포트: 월~토, 19:00
    scheduler.add_job(
        job.run_daily_report,
        trigger="cron",
        day_of_week="mon-sat",
        hour=19,
        minute=0,
        id="daily_report",
    )

    logger.info(
        "스케줄러 시작 — 모니터링: 월~토 09:00~18:50 매 10분, 일일 리포트: 19:00"
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")
