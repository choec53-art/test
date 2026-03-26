"""
관리 페이지 Flask 앱
- 탐지 알람 리스트
- 통계 대시보드
- 상세 보기
"""

import os
from functools import wraps

from flask import Flask, jsonify, render_template, request

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import TARGET_HOSPITAL, SCHEDULE_INTERVAL_MINUTES
from storage import create_storage

app = Flask(__name__)
db = create_storage()

_ADMIN_TOKEN = os.getenv("ADMIN_API_TOKEN")


# ─── 인증 ────────────────────────────────────────────────────

def require_auth(f):
    """
    Bearer 토큰 인증 데코레이터.
    - ADMIN_API_TOKEN 환경변수가 설정되지 않으면 인증을 건너뛴다 (로컬 개발용).
    - 설정되어 있으면 Authorization: Bearer <token> 헤더를 검증한다.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if _ADMIN_TOKEN is None:
            # 토큰 미설정 — 로컬 개발 모드, 인증 생략
            return f(*args, **kwargs)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header"}), 401
        token = auth_header[len("Bearer "):]
        if token != _ADMIN_TOKEN:
            return jsonify({"error": "Invalid token"}), 403

        return f(*args, **kwargs)
    return decorated


# ─── 페이지 라우터 ───────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html",
                           hospital=TARGET_HOSPITAL,
                           interval=SCHEDULE_INTERVAL_MINUTES)


# ─── API ────────────────────────────────────────────────────────

@app.route("/api/stats")
@require_auth
def api_stats():
    """통계 요약"""
    stats = db.get_stats()
    severity = db.get_severity_counts()
    daily = db.get_daily_counts(days=7)
    by_source = db.get_source_counts()

    return jsonify({
        **stats,
        "severity": severity,
        "daily": daily,
        "by_source": by_source,
        "interval_minutes": SCHEDULE_INTERVAL_MINUTES,
    })


@app.route("/api/detections")
@require_auth
def api_detections():
    """알람 리스트 (페이지네이션)"""
    page = max(int(request.args.get("page", 1)), 1)
    per_page = int(request.args.get("per_page", 20))
    severity_filter = request.args.get("severity", "")   # low|medium|high
    source_filter = request.args.get("source", "")       # blog|cafe
    keyword_filter = request.args.get("keyword", "")

    total, items = db.get_detections_page(
        page=page,
        per_page=per_page,
        severity=severity_filter,
        source=source_filter,
        keyword=keyword_filter,
    )

    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "items": items,
    })


@app.route("/api/detections/<int:detection_id>")
@require_auth
def api_detection_detail(detection_id: int):
    """알람 상세"""
    item = db.get_detection_detail(detection_id)
    if not item:
        return jsonify({"error": "Not found"}), 404
    return jsonify(item)


@app.route("/api/notifications")
@require_auth
def api_notifications():
    """알림 발송 이력"""
    items = db.get_notification_history(limit=50)
    return jsonify(items)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
