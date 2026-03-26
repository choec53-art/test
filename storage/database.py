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
from datetime import datetime, timedelta

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

                CREATE INDEX IF NOT EXISTS idx_detections_post_link
                    ON detections(post_link);
                CREATE INDEX IF NOT EXISTS idx_detections_detected_at
                    ON detections(detected_at);
                CREATE INDEX IF NOT EXISTS idx_detections_severity
                    ON detections(severity);
                CREATE INDEX IF NOT EXISTS idx_posts_collected_at
                    ON posts(collected_at);
                CREATE INDEX IF NOT EXISTS idx_notifications_sent_at
                    ON notifications(sent_at);
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

    def get_known_links(self, days: int = 30) -> set[str]:
        """이미 수집된 게시글 링크 목록 반환 (최근 N일 이내)"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT link FROM posts WHERE collected_at > ?", (cutoff,)
            ).fetchall()
        return {row[0] for row in rows}

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

    # ─── 관리 페이지용 조회 메서드 ────────────────────────────────

    def get_severity_counts(self) -> dict:
        """심각도별 부적절 탐지 카운트 반환 — {severity: count}"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT severity, COUNT(*) as cnt
                   FROM detections WHERE is_inappropriate=1
                   GROUP BY severity"""
            ).fetchall()
        return {r["severity"]: r["cnt"] for r in rows}

    def get_daily_counts(self, days: int = 7) -> list[dict]:
        """최근 N일 일별 탐지 수 반환 — [{day, cnt}]"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT DATE(detected_at) as day, COUNT(*) as cnt
                   FROM detections WHERE is_inappropriate=1
                   GROUP BY day ORDER BY day DESC LIMIT ?""",
                (days,),
            ).fetchall()
        return [{"day": r["day"], "cnt": r["cnt"]} for r in rows]

    def get_source_counts(self) -> dict:
        """출처별 부적절 탐지 카운트 반환 — {source: count}"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT p.source, COUNT(*) as cnt
                   FROM detections d JOIN posts p ON d.post_link = p.link
                   WHERE d.is_inappropriate=1
                   GROUP BY p.source"""
            ).fetchall()
        return {r["source"]: r["cnt"] for r in rows}

    def get_detections_page(
        self,
        page: int = 1,
        per_page: int = 20,
        severity: str = "",
        source: str = "",
        keyword: str = "",
    ) -> tuple[int, list[dict]]:
        """
        필터/페이지네이션이 적용된 탐지 목록 반환 — (total, items).

        NOTE: WHERE 절은 하드코딩된 문자열로만 구성되며,
        사용자 입력은 모두 파라미터 바인딩(?)을 통해 전달되므로
        SQL injection 위험이 없습니다.
        """
        offset = (page - 1) * per_page

        # where_clauses는 하드코딩된 SQL 조각만 포함 — SQL injection 안전
        where_clauses = ["d.is_inappropriate = 1"]
        params: list = []

        if severity:
            where_clauses.append("d.severity = ?")
            params.append(severity)
        if source:
            where_clauses.append("p.source = ?")
            params.append(source)
        if keyword:
            where_clauses.append("(p.title LIKE ? OR p.description LIKE ?)")
            like = f"%{keyword}%"
            params.extend([like, like])

        where = " AND ".join(where_clauses)

        with self._conn() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM detections d"
                f" JOIN posts p ON d.post_link=p.link WHERE {where}",
                params,
            ).fetchone()[0]

            rows = conn.execute(
                f"""SELECT
                      d.id, d.post_link, d.confidence, d.categories,
                      d.matched_keywords, d.ai_reason, d.severity, d.detected_at,
                      p.title, p.source, p.blogger_name, p.cafe_name, p.post_date
                    FROM detections d
                    JOIN posts p ON d.post_link = p.link
                    WHERE {where}
                    ORDER BY d.detected_at DESC
                    LIMIT ? OFFSET ?""",
                [*params, per_page, offset],
            ).fetchall()

        items = [dict(r) for r in rows]
        # JSON 문자열 필드를 파싱
        for item in items:
            for key in ("categories", "matched_keywords"):
                if key in item and isinstance(item[key], str):
                    try:
                        item[key] = json.loads(item[key])
                    except (json.JSONDecodeError, TypeError):
                        item[key] = []
        return total, items

    def get_detection_detail(self, detection_id: int) -> dict | None:
        """탐지 상세 정보 반환 — 없으면 None"""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT d.*, p.title, p.source, p.blogger_name, p.cafe_name,
                          p.post_date, p.description, p.keyword, p.collected_at
                   FROM detections d JOIN posts p ON d.post_link = p.link
                   WHERE d.id = ?""",
                (detection_id,),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        for key in ("categories", "matched_keywords"):
            if key in item and isinstance(item[key], str):
                try:
                    item[key] = json.loads(item[key])
                except (json.JSONDecodeError, TypeError):
                    item[key] = []
        return item

    def get_daily_summary(self, date: str = "") -> dict:
        """일일 요약 통계 반환 — 전체 누적 / 금일 / 이번주 분류"""
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        # 이번주 월요일 계산
        from datetime import date as date_type
        target = date_type.fromisoformat(date)
        week_start = (target - timedelta(days=target.weekday())).isoformat()

        with self._conn() as conn:
            # 전체 누적
            total_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
            total_detections = conn.execute(
                "SELECT COUNT(*) FROM detections WHERE is_inappropriate=1"
            ).fetchone()[0]

            # 금일
            today_posts = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE DATE(collected_at) = ?", (date,)
            ).fetchone()[0]
            today_detections = conn.execute(
                """SELECT COUNT(*) FROM detections
                   WHERE is_inappropriate=1 AND DATE(detected_at) = ?""", (date,)
            ).fetchone()[0]

            # 이번주
            week_posts = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE DATE(collected_at) >= ?", (week_start,)
            ).fetchone()[0]
            week_detections = conn.execute(
                """SELECT COUNT(*) FROM detections
                   WHERE is_inappropriate=1 AND DATE(detected_at) >= ?""", (week_start,)
            ).fetchone()[0]

            # 금일 탐지 상세
            detections = conn.execute(
                """SELECT d.severity, d.confidence, d.categories, d.matched_keywords,
                          d.ai_reason, d.detected_at,
                          p.title, p.source, p.link, p.blogger_name, p.cafe_name,
                          p.post_date
                   FROM detections d JOIN posts p ON d.post_link = p.link
                   WHERE d.is_inappropriate=1 AND DATE(d.detected_at) = ?
                   ORDER BY d.confidence DESC""",
                (date,),
            ).fetchall()
            severity_counts = {}
            for row in detections:
                sev = row["severity"]
                severity_counts[sev] = severity_counts.get(sev, 0) + 1

            # 금일 출처별
            by_source = conn.execute(
                """SELECT p.source, COUNT(*) as cnt FROM posts p
                   WHERE DATE(p.collected_at) = ? GROUP BY p.source""",
                (date,),
            ).fetchall()

        return {
            "date": date,
            "total_posts": total_posts,
            "total_detections": total_detections,
            "today_posts": today_posts,
            "today_detections": today_detections,
            "week_posts": week_posts,
            "week_detections": week_detections,
            "severity_counts": severity_counts,
            "by_source": {r["source"]: r["cnt"] for r in by_source},
            "detections": [dict(r) for r in detections],
        }

    def get_notification_history(self, limit: int = 50) -> list[dict]:
        """알림 발송 이력 반환"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM notifications ORDER BY sent_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
