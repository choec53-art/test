"""모니터 잡 테스트"""

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from crawler.naver_crawler import NaverPost
from analyzer.content_analyzer import AnalysisResult, AnalysisSummary


def _make_post(link="https://test.com/1"):
    return NaverPost(
        source="blog", title="테스트", description="내용",
        link=link, blogger_name="블로거", cafe_name="",
        post_date="20260320", keyword="테스트", collected_at="2026-03-20T00:00:00",
    )


def _make_result(post, is_inappropriate=True):
    return AnalysisResult(
        post=post, is_inappropriate=is_inappropriate, confidence=0.85,
        categories=["욕설/비하"], matched_keywords=["쓰레기"],
        ai_reason="테스트", severity="high", raw_content="테스트",
    )


class TestMonitorJob:
    @patch("scheduler.monitor_job.EmailNotifier")
    @patch("scheduler.monitor_job.Database")
    @patch("scheduler.monitor_job.ContentAnalyzer")
    @patch("scheduler.monitor_job.NaverCrawler")
    def test_run_no_new_posts(self, MockCrawler, MockAnalyzer, MockDB, MockNotifier):
        from scheduler.monitor_job import MonitorJob
        job = MonitorJob()
        job.db.get_known_links.return_value = set()
        job.crawler.collect_all.return_value = []

        job.run()
        job.analyzer.analyze_batch.assert_not_called()
        job.notifier.send.assert_not_called()

    @patch("scheduler.monitor_job.EmailNotifier")
    @patch("scheduler.monitor_job.Database")
    @patch("scheduler.monitor_job.ContentAnalyzer")
    @patch("scheduler.monitor_job.NaverCrawler")
    def test_run_with_new_posts_no_detection(self, MockCrawler, MockAnalyzer, MockDB, MockNotifier):
        from scheduler.monitor_job import MonitorJob
        job = MonitorJob()
        post = _make_post()
        job.db.get_known_links.return_value = set()
        job.crawler.collect_all.return_value = [post]
        job.db.save_post.return_value = True
        job.analyzer.analyze_batch.return_value = AnalysisSummary(
            total_checked=1, inappropriate_count=0, results=[],
        )
        job.db.get_stats.return_value = {"total_posts": 1, "total_detections": 0}

        job.run()
        job.analyzer.analyze_batch.assert_called_once()
        job.notifier.send.assert_not_called()

    @patch("scheduler.monitor_job.EmailNotifier")
    @patch("scheduler.monitor_job.Database")
    @patch("scheduler.monitor_job.ContentAnalyzer")
    @patch("scheduler.monitor_job.NaverCrawler")
    def test_run_with_detection_sends_email(self, MockCrawler, MockAnalyzer, MockDB, MockNotifier):
        from scheduler.monitor_job import MonitorJob
        job = MonitorJob()
        post = _make_post()
        result = _make_result(post)
        job.db.get_known_links.return_value = set()
        job.crawler.collect_all.return_value = [post]
        job.db.save_post.return_value = True
        job.analyzer.analyze_batch.return_value = AnalysisSummary(
            total_checked=1, inappropriate_count=1, results=[result],
        )
        job.notifier.send.return_value = True
        job.notifier.recipients = ["test@test.com"]
        job.db.get_stats.return_value = {"total_posts": 1, "total_detections": 1}

        job.run()
        job.notifier.send.assert_called_once_with([result])
        job.db.save_detection.assert_called_once_with(result)

    @patch("scheduler.monitor_job.EmailNotifier")
    @patch("scheduler.monitor_job.Database")
    @patch("scheduler.monitor_job.ContentAnalyzer")
    @patch("scheduler.monitor_job.NaverCrawler")
    def test_run_duplicate_posts_filtered(self, MockCrawler, MockAnalyzer, MockDB, MockNotifier):
        from scheduler.monitor_job import MonitorJob
        job = MonitorJob()
        post = _make_post()
        job.db.get_known_links.return_value = set()
        job.crawler.collect_all.return_value = [post]
        job.db.save_post.return_value = False  # 이미 존재
        job.db.get_stats.return_value = {"total_posts": 0, "total_detections": 0}

        job.run()
        job.analyzer.analyze_batch.assert_not_called()


class TestConfig:
    def test_config_values(self):
        from config import SEARCH_KEYWORDS, SEARCH_DISPLAY, SEARCH_DAYS
        assert isinstance(SEARCH_KEYWORDS, list)
        assert len(SEARCH_KEYWORDS) > 0
        assert SEARCH_DISPLAY <= 100
        assert SEARCH_DAYS > 0
