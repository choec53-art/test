"""
Azure Functions 진입점 — 병원 콘텐츠 모니터링 시스템

Timer Triggers:
  - monitor_trigger: 월~토 09:00~18:50 매 10분 (모니터링 사이클)
  - daily_report_trigger: 월~토 19:00 (일일 리포트)

HTTP Triggers:
  - api_stats: GET /api/stats
  - api_detections: GET /api/detections
  - api_detection_detail: GET /api/detections/{detection_id}
  - api_notifications: GET /api/notifications
"""

import json
import logging
import os

import azure.functions as func
from dotenv import load_dotenv

load_dotenv()

from config import TARGET_HOSPITAL, SCHEDULE_INTERVAL_MINUTES
from scheduler.monitor_job import MonitorJob
from storage import create_storage

logger = logging.getLogger(__name__)

app = func.FunctionApp()

# ─── 인증 헬퍼 ────────────────────────────────────────────────────

_ADMIN_TOKEN = os.getenv("ADMIN_API_TOKEN")


def _check_auth(req: func.HttpRequest) -> func.HttpResponse | None:
    """Bearer 토큰 인증. 토큰 미설정 시 인증 생략 (로컬 개발용)."""
    if _ADMIN_TOKEN is None:
        return None

    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return func.HttpResponse(
            json.dumps({"error": "Missing or invalid Authorization header"}),
            status_code=401,
            mimetype="application/json",
        )
    token = auth_header[len("Bearer "):]
    if token != _ADMIN_TOKEN:
        return func.HttpResponse(
            json.dumps({"error": "Invalid token"}),
            status_code=403,
            mimetype="application/json",
        )
    return None


def _json_response(data, status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(data, ensure_ascii=False, default=str),
        status_code=status_code,
        mimetype="application/json",
    )


# ─── Timer Triggers ──────────────────────────────────────────────

@app.timer_trigger(
    schedule="0 */10 9-18 * * 1-6",
    arg_name="timer",
    run_on_startup=False,
)
def monitor_trigger(timer: func.TimerRequest) -> None:
    """모니터링 사이클: 월~토 09:00~18:50 매 10분"""
    logger.info("monitor_trigger 실행 (past_due=%s)", timer.past_due)
    MonitorJob().run()


@app.timer_trigger(
    schedule="0 0 19 * * 1-6",
    arg_name="timer",
    run_on_startup=False,
)
def daily_report_trigger(timer: func.TimerRequest) -> None:
    """일일 리포트: 월~토 19:00"""
    logger.info("daily_report_trigger 실행 (past_due=%s)", timer.past_due)
    MonitorJob().run_daily_report()


# ─── HTTP Triggers (관리 API) ────────────────────────────────────

@app.route(route="api/stats", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def api_stats(req: func.HttpRequest) -> func.HttpResponse:
    """통계 요약"""
    auth_err = _check_auth(req)
    if auth_err:
        return auth_err

    db = create_storage()
    stats = db.get_stats()
    severity = db.get_severity_counts()
    daily = db.get_daily_counts(days=7)
    by_source = db.get_source_counts()

    return _json_response({
        **stats,
        "severity": severity,
        "daily": daily,
        "by_source": by_source,
        "interval_minutes": SCHEDULE_INTERVAL_MINUTES,
    })


@app.route(route="api/detections", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def api_detections(req: func.HttpRequest) -> func.HttpResponse:
    """알람 리스트 (페이지네이션)"""
    auth_err = _check_auth(req)
    if auth_err:
        return auth_err

    page = max(int(req.params.get("page", "1")), 1)
    per_page = int(req.params.get("per_page", "20"))
    severity_filter = req.params.get("severity", "")
    source_filter = req.params.get("source", "")
    keyword_filter = req.params.get("keyword", "")

    db = create_storage()
    total, items = db.get_detections_page(
        page=page,
        per_page=per_page,
        severity=severity_filter,
        source=source_filter,
        keyword=keyword_filter,
    )

    return _json_response({
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "items": items,
    })


@app.route(
    route="api/detections/{detection_id}",
    methods=["GET"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def api_detection_detail(req: func.HttpRequest) -> func.HttpResponse:
    """알람 상세"""
    auth_err = _check_auth(req)
    if auth_err:
        return auth_err

    detection_id = req.route_params.get("detection_id", "")
    db = create_storage()
    item = db.get_detection_detail(detection_id)
    if not item:
        return _json_response({"error": "Not found"}, status_code=404)
    return _json_response(item)


@app.route(route="api/notifications", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def api_notifications(req: func.HttpRequest) -> func.HttpResponse:
    """알림 발송 이력"""
    auth_err = _check_auth(req)
    if auth_err:
        return auth_err

    db = create_storage()
    items = db.get_notification_history(limit=50)
    return _json_response(items)
