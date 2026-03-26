"""데이터베이스 테스트"""

import os
import tempfile

import pytest

from crawler.naver_crawler import NaverPost
from storage.database import Database
from analyzer.content_analyzer import AnalysisResult


def _make_post(link="https://test.com/1", title="테스트 제목"):
    return NaverPost(
        source="blog", title=title, description="테스트 내용",
        link=link, blogger_name="테스터", cafe_name="",
        post_date="20260320", keyword="테스트", collected_at="2026-03-20T00:00:00",
    )


def _make_result(post=None, is_inappropriate=True):
    post = post or _make_post()
    return AnalysisResult(
        post=post, is_inappropriate=is_inappropriate, confidence=0.85,
        categories=["욕설/비하"], matched_keywords=["쓰레기"],
        ai_reason="테스트 사유", severity="high", raw_content="테스트",
        hybrid_score=0.85, keyword_score=0.3, ai_score=0.9,
    )


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    database = Database(db_path=path)
    yield database
    os.unlink(path)


class TestDatabaseInit:
    def test_tables_created(self, db):
        import sqlite3
        conn = sqlite3.connect(db.db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
        table_names = {t[0] for t in tables}
        assert "posts" in table_names
        assert "detections" in table_names
        assert "notifications" in table_names


class TestPostOperations:
    def test_save_post(self, db):
        post = _make_post()
        assert db.save_post(post) is True

    def test_save_duplicate_post(self, db):
        post = _make_post()
        db.save_post(post)
        assert db.save_post(post) is False  # 중복

    def test_is_post_known(self, db):
        post = _make_post()
        assert db.is_post_known(post.link) is False
        db.save_post(post)
        assert db.is_post_known(post.link) is True

    def test_get_known_links(self, db):
        db.save_post(_make_post(link="https://a.com"))
        db.save_post(_make_post(link="https://b.com"))
        links = db.get_known_links()
        assert links == {"https://a.com", "https://b.com"}

    def test_get_known_links_empty(self, db):
        assert db.get_known_links() == set()


class TestDetectionOperations:
    def test_save_detection(self, db):
        post = _make_post()
        db.save_post(post)
        result = _make_result(post)
        db.save_detection(result)  # 에러 없이 저장

    def test_get_recent_detections(self, db):
        post = _make_post()
        db.save_post(post)
        result = _make_result(post)
        db.save_detection(result)
        detections = db.get_recent_detections(limit=10)
        assert len(detections) == 1

    def test_get_recent_detections_only_inappropriate(self, db):
        post1 = _make_post(link="https://bad.com")
        post2 = _make_post(link="https://good.com")
        db.save_post(post1)
        db.save_post(post2)
        db.save_detection(_make_result(post1, is_inappropriate=True))
        db.save_detection(_make_result(post2, is_inappropriate=False))
        detections = db.get_recent_detections()
        assert len(detections) == 1


class TestNotificationOperations:
    def test_save_notification(self, db):
        db.save_notification("test@test.com", "테스트 제목", 3, "success")

    def test_save_multiple_notifications(self, db):
        db.save_notification("a@test.com", "제목1", 1, "success")
        db.save_notification("b@test.com", "제목2", 2, "failed")


class TestStats:
    def test_empty_stats(self, db):
        stats = db.get_stats()
        assert stats["total_posts"] == 0
        assert stats["total_detections"] == 0
        assert stats["total_notifications"] == 0

    def test_stats_with_data(self, db):
        post = _make_post()
        db.save_post(post)
        db.save_detection(_make_result(post))
        db.save_notification("test@test.com", "제목", 1, "success")
        stats = db.get_stats()
        assert stats["total_posts"] == 1
        assert stats["total_detections"] == 1
        assert stats["total_notifications"] == 1
