"""
SQLite 저장소
- 수집된 게시글 저장 및 중복 방지
- 부적절 탐지 결과 저장
- 알림 전송 이력 관리
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from config import DB_PATH
from analyzer.content_analyzer import AnalysisResult

logger = logging.getLogger(__name__)


class Database:
    """모니터링 데이터베이스"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_tables()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_tables(self):
        """테이블 초기화"""
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS posts (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    link        TEXT UNIQUE NOT NULL,
                    source      TEXT,
                    title       TEXT,
                    description TEXT,
                    blogger_name TEXT,
                    cafe_name   TEXT,
                    post_date   TEXT,
                    keyword     TEXT,
                    collected_at TEXT
                );

                CREATE TABLE IF NOT EXISTS detections (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_link       TEXT NOT NULL,
                    is_inappropriate INTEGER,
                    confidence      REAL,
                    categories      TEXT,   -- JSON 배열
                    matched_keywords TEXT,  -- JSON 배열
                    ai_reason       TEXT,
                    severity        TEXT,
                    detected_at     TEXT,
                    FOREIGN KEY (post_link) REFERENCES posts(link)
                );

                CREATE TABLE IF NOT EXISTS notifications (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    sent_at     TEXT,
                    recipient   TEXT,
                    subject     TEXT,
                    post_count  INTEGER,
                    status      TEXT    -- "success" | "failed"
                );
            """)
        logger.debug("DB 테이블 초기화 완료: %s", self.db_path)

    # ─── 게시글 저장 ─────────────────────────────────────────────

    def is_post_known(self, link: str) -> bool:
        """이미 수집된 게시글인지 확인"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM posts WHERE link = ?", (link,)
            ).fetchone()
        return row is not None

    def save_post(self, post) -> bool:
        """
        게시글 저장. 이미 존재하면 False 반환.
        """
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT INTO posts
                       (link, source, title, description, blogger_name,
                        cafe_name, post_date, keyword, collected_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        post.link, post.source, post.title, post.description,
                        post.blogger_name, post.cafe_name, post.post_date,
                        post.keyword, post.collected_at,
                    ),
                )
                return True
            except sqlite3.IntegrityError:
                return False  # 중복

    # ─── 탐지 결과 저장 ──────────────────────────────────────────

    def save_detection(self, result: AnalysisResult):
        """분석 결과 저장"""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO detections
                   (post_link, is_inappropriate, confidence, categories,
                    matched_keywords, ai_reason, severity, detected_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    result.post.link,
                    int(result.is_inappropriate),
                    result.confidence,
                    json.dumps(result.categories, ensure_ascii=False),
                    json.dumps(result.matched_keywords, ensure_ascii=False),
                    result.ai_reason,
                    result.severity,
                    datetime.now().isoformat(),
                ),
            )

    # ─── 알림 이력 ───────────────────────────────────────────────

    def save_notification(self, recipient: str, subject: str, post_count: int, status: str):
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO notifications (sent_at, recipient, subject, post_count, status)
                   VALUES (?,?,?,?,?)""",
                (datetime.now().isoformat(), recipient, subject, post_count, status),
            )

    # ─── 조회 ────────────────────────────────────────────────────

    def get_recent_detections(self, limit: int = 50) -> list[sqlite3.Row]:
        """최근 탐지 결과 조회"""
        with self._conn() as conn:
            return conn.execute(
                """SELECT d.*, p.title, p.source, p.link, p.blogger_name, p.cafe_name
                   FROM detections d
                   JOIN posts p ON d.post_link = p.link
                   WHERE d.is_inappropriate = 1
                   ORDER BY d.detected_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()

    def get_stats(self) -> dict:
        """통계 조회"""
        with self._conn() as conn:
            total_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
            total_detections = conn.execute(
                "SELECT COUNT(*) FROM detections WHERE is_inappropriate=1"
            ).fetchone()[0]
            total_notifications = conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
        return {
            "total_posts": total_posts,
            "total_detections": total_detections,
            "total_notifications": total_notifications,
        }
