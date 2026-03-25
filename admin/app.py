"""
관리 페이지 Flask 앱
- 탐지 알람 리스트
- 통계 대시보드
- 상세 보기
"""

import json
import sqlite3
from datetime import datetime
from functools import wraps

from flask import Flask, jsonify, render_template, request

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import DB_PATH, TARGET_HOSPITAL, SCHEDULE_INTERVAL_MINUTES
from storage.database import Database

app = Flask(__name__)
db = Database(DB_PATH)


# ─── 유틸 ───────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def _parse_json_field(d: dict, *keys):
    for key in keys:
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                d[key] = []
    return d


# ─── 페이지 라우터 ───────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html",
                           hospital=TARGET_HOSPITAL,
                           interval=SCHEDULE_INTERVAL_MINUTES)


# ─── API ────────────────────────────────────────────────────────

@app.route("/api/stats")
def api_stats():
    """통계 요약"""
    stats = db.get_stats()

    # 심각도별 카운트
    with db._conn() as conn:
        severity_rows = conn.execute(
            """SELECT severity, COUNT(*) as cnt
               FROM detections WHERE is_inappropriate=1
               GROUP BY severity"""
        ).fetchall()
        severity = {r["severity"]: r["cnt"] for r in severity_rows}

        # 최근 7일 일별 탐지 수
        daily_rows = conn.execute(
            """SELECT DATE(detected_at) as day, COUNT(*) as cnt
               FROM detections WHERE is_inappropriate=1
               GROUP BY day ORDER BY day DESC LIMIT 7"""
        ).fetchall()
        daily = [{"day": r["day"], "cnt": r["cnt"]} for r in daily_rows]

        # 출처별 카운트
        source_rows = conn.execute(
            """SELECT p.source, COUNT(*) as cnt
               FROM detections d JOIN posts p ON d.post_link = p.link
               WHERE d.is_inappropriate=1
               GROUP BY p.source"""
        ).fetchall()
        by_source = {r["source"]: r["cnt"] for r in source_rows}

    return jsonify({
        **stats,
        "severity": severity,
        "daily": daily,
        "by_source": by_source,
        "interval_minutes": SCHEDULE_INTERVAL_MINUTES,
    })


@app.route("/api/detections")
def api_detections():
    """알람 리스트 (페이지네이션)"""
    page = max(int(request.args.get("page", 1)), 1)
    per_page = int(request.args.get("per_page", 20))
    severity_filter = request.args.get("severity", "")   # low|medium|high
    source_filter = request.args.get("source", "")       # blog|cafe
    keyword_filter = request.args.get("keyword", "")

    offset = (page - 1) * per_page

    where_clauses = ["d.is_inappropriate = 1"]
    params: list = []

    if severity_filter:
        where_clauses.append("d.severity = ?")
        params.append(severity_filter)
    if source_filter:
        where_clauses.append("p.source = ?")
        params.append(source_filter)
    if keyword_filter:
        where_clauses.append("(p.title LIKE ? OR p.description LIKE ?)")
        like = f"%{keyword_filter}%"
        params.extend([like, like])

    where = " AND ".join(where_clauses)

    with db._conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM detections d JOIN posts p ON d.post_link=p.link WHERE {where}",
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

    items = [_parse_json_field(_row_to_dict(r), "categories", "matched_keywords")
             for r in rows]

    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "items": items,
    })


@app.route("/api/detections/<int:detection_id>")
def api_detection_detail(detection_id: int):
    """알람 상세"""
    with db._conn() as conn:
        row = conn.execute(
            """SELECT d.*, p.title, p.source, p.blogger_name, p.cafe_name,
                      p.post_date, p.description, p.keyword, p.collected_at
               FROM detections d JOIN posts p ON d.post_link = p.link
               WHERE d.id = ?""",
            (detection_id,),
        ).fetchone()

    if not row:
        return jsonify({"error": "Not found"}), 404

    item = _parse_json_field(_row_to_dict(row), "categories", "matched_keywords")
    return jsonify(item)


@app.route("/api/notifications")
def api_notifications():
    """알림 발송 이력"""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT * FROM notifications ORDER BY sent_at DESC LIMIT 50"
        ).fetchall()
    return jsonify([_row_to_dict(r) for r in rows])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
