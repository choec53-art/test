"""
이메일 알림 발송
탐지된 부적절 게시글을 이메일로 보고합니다.
OAuth2 인증을 통해 Gmail SMTP로 발송합니다.
"""

import base64
import html
import json
import logging
import os
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from analyzer.content_analyzer import AnalysisResult
from config import EMAIL_CONFIG, TARGET_HOSPITAL

logger = logging.getLogger(__name__)

SEVERITY_KR = {"low": "낮음", "medium": "보통", "high": "높음"}
SEVERITY_COLOR = {"low": "#f0ad4e", "medium": "#d9534f", "high": "#a02020"}

TOKEN_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "token.json")

# Azure Blob Storage 설정 (서버리스 환경용 OAuth2 토큰 저장)
_BLOB_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
_BLOB_CONTAINER = "oauth-tokens"
_BLOB_NAME = "gmail-token.json"


class EmailNotifier:
    """이메일 알림 발송기 (OAuth2)"""

    def __init__(self):
        cfg = EMAIL_CONFIG
        self.smtp_host = cfg["smtp_host"]
        self.smtp_port = cfg["smtp_port"]
        self.sender = os.getenv("EMAIL_SENDER", cfg.get("sender_email", ""))
        raw_recipients = os.getenv("EMAIL_RECIPIENTS", "")
        self.recipients: list[str] = (
            [r.strip() for r in raw_recipients.split(",") if r.strip()]
            or cfg.get("recipient_emails", [])
        )
        self.subject_prefix = cfg.get("subject_prefix", "[병원 모니터링]")

    def _load_token_data(self) -> dict | None:
        """토큰 데이터 로드 — Blob Storage 우선, 로컬 파일 폴백"""
        # 1. Azure Blob Storage (서버리스 환경)
        if _BLOB_CONNECTION_STRING:
            try:
                from azure.storage.blob import BlobServiceClient
                blob_service = BlobServiceClient.from_connection_string(_BLOB_CONNECTION_STRING)
                container = blob_service.get_container_client(_BLOB_CONTAINER)
                blob = container.get_blob_client(_BLOB_NAME)
                data = blob.download_blob().readall()
                return json.loads(data)
            except Exception as e:
                logger.warning("Blob에서 토큰 로드 실패, 로컬 파일 시도: %s", e)

        # 2. 환경변수 (GMAIL_TOKEN_JSON)
        token_json_env = os.getenv("GMAIL_TOKEN_JSON", "")
        if token_json_env:
            try:
                return json.loads(token_json_env)
            except json.JSONDecodeError:
                logger.warning("GMAIL_TOKEN_JSON 환경변수 JSON 파싱 실패")

        # 3. 로컬 파일 (기존 방식)
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE) as f:
                return json.load(f)

        logger.error("OAuth2 토큰을 찾을 수 없음 — Blob/환경변수/token.json 모두 없음")
        return None

    def _save_token_data(self, token_data: dict):
        """토큰 데이터 저장 — Blob Storage 우선, 로컬 파일 폴백"""
        # 1. Azure Blob Storage
        if _BLOB_CONNECTION_STRING:
            try:
                from azure.storage.blob import BlobServiceClient
                blob_service = BlobServiceClient.from_connection_string(_BLOB_CONNECTION_STRING)
                container = blob_service.get_container_client(_BLOB_CONTAINER)
                try:
                    container.create_container()
                except Exception:
                    pass  # 이미 존재
                blob = container.get_blob_client(_BLOB_NAME)
                blob.upload_blob(
                    json.dumps(token_data, indent=2).encode(),
                    overwrite=True,
                )
                logger.info("OAuth2 토큰 Blob Storage에 저장 완료")
                return
            except Exception as e:
                logger.warning("Blob 저장 실패, 로컬 파일에 저장: %s", e)

        # 2. 로컬 파일
        try:
            with open(TOKEN_FILE, "w") as f:
                json.dump(token_data, f, indent=2)
        except OSError:
            logger.warning("로컬 token.json 저장 실패 (읽기 전용 파일시스템)")

    def _get_oauth2_token(self, force_refresh: bool = False) -> str | None:
        """OAuth2 액세스 토큰을 읽고, 만료 시 자동 갱신

        토큰 소스: Azure Blob Storage → GMAIL_TOKEN_JSON 환경변수 → 로컬 token.json
        force_refresh=True이면 만료 여부와 무관하게 강제 갱신합니다.
        """
        token_data = self._load_token_data()
        if not token_data:
            return None

        creds = Credentials(
            token=token_data["token"],
            refresh_token=token_data["refresh_token"],
            token_uri=token_data["token_uri"],
            client_id=token_data["client_id"],
            client_secret=token_data["client_secret"],
            scopes=token_data.get("scopes"),
        )

        if (force_refresh or creds.expired) and creds.refresh_token:
            creds.refresh(Request())
            token_data["token"] = creds.token
            if creds.expiry:
                token_data["expiry"] = creds.expiry.isoformat()
            self._save_token_data(token_data)
            logger.info("OAuth2 토큰 갱신 완료 (force=%s)", force_refresh)

        return creds.token

    @staticmethod
    def _calc_collect_period() -> str:
        """스케줄 기반 수집 구간 계산 (월~토 09:00~18:00)"""
        now = datetime.now()
        current_hour = now.replace(minute=0, second=0, microsecond=0)
        if current_hour.hour == 9:
            # 09:00 사이클: 전일 마지막 사이클(18:00) 이후부터
            prev = (current_hour - timedelta(days=1)).replace(hour=18)
            return f"{prev.strftime('%m/%d %H:%M')}~{current_hour.strftime('%m/%d %H:%M')}"
        prev_hour = current_hour - timedelta(hours=1)
        return f"{prev_hour.strftime('%H:%M')}~{current_hour.strftime('%H:%M')}"

    def _build_html(self, results: list[AnalysisResult]) -> str:
        now = datetime.now()
        collect_period = self._calc_collect_period()
        rows = ""
        for r in results:
            color = SEVERITY_COLOR.get(r.severity, "#999")
            sev_kr = SEVERITY_KR.get(r.severity, r.severity)
            safe_title = html.escape(r.post.title or '(제목 없음)')
            safe_link = html.escape(r.post.link)
            safe_blogger = html.escape(r.post.blogger_name)
            safe_reason = html.escape(r.ai_reason)
            categories = html.escape(", ".join(r.categories)) if r.categories else "-"
            keywords = html.escape(", ".join(r.matched_keywords)) if r.matched_keywords else "-"
            post_date = r.post.post_date[:8] if r.post.post_date else "-"
            if post_date != "-" and len(post_date) == 8:
                post_date = f"{post_date[:4]}-{post_date[4:6]}-{post_date[6:8]}"
            rows += f"""
            <tr>
              <td style="padding:8px;border:1px solid #ddd;">
                <a href="{safe_link}" style="color:#1a73e8;">{safe_title}</a>
              </td>
              <td style="padding:8px;border:1px solid #ddd;text-align:center;">
                {'블로그' if r.post.source=='blog' else '카페'}
              </td>
              <td style="padding:8px;border:1px solid #ddd;">{safe_blogger}</td>
              <td style="padding:8px;border:1px solid #ddd;text-align:center;">{post_date}</td>
              <td style="padding:8px;border:1px solid #ddd;color:{color};font-weight:bold;">{sev_kr}</td>
              <td style="padding:8px;border:1px solid #ddd;">{categories}</td>
              <td style="padding:8px;border:1px solid #ddd;">{keywords}</td>
              <td style="padding:8px;border:1px solid #ddd;">{safe_reason}</td>
            </tr>"""

        return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"></head>
<body style="font-family:Apple SD Gothic Neo,sans-serif;color:#333;margin:20px;">
  <h2 style="color:#c0392b;">🏥 {TARGET_HOSPITAL} 부적절 콘텐츠 모니터링 리포트</h2>
  <p style="color:#666;">수집 구간: {now.strftime('%Y-%m-%d')} {collect_period} | 탐지 건수: <strong>{len(results)}건</strong></p>
  <table style="border-collapse:collapse;width:100%;font-size:13px;">
    <thead>
      <tr style="background:#f5f5f5;">
        <th style="padding:8px;border:1px solid #ddd;">제목</th>
        <th style="padding:8px;border:1px solid #ddd;">출처</th>
        <th style="padding:8px;border:1px solid #ddd;">작성자</th>
        <th style="padding:8px;border:1px solid #ddd;">작성일</th>
        <th style="padding:8px;border:1px solid #ddd;">심각도</th>
        <th style="padding:8px;border:1px solid #ddd;">카테고리</th>
        <th style="padding:8px;border:1px solid #ddd;">탐지 키워드</th>
        <th style="padding:8px;border:1px solid #ddd;">AI 판단</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  <p style="color:#999;font-size:11px;margin-top:20px;">
    본 메일은 자동 모니터링 시스템에서 발송된 메일입니다.
  </p>
</body>
</html>"""

    def _build_daily_report_html(self, summary: dict) -> str:
        """일일 리포트 HTML 생성"""
        date = summary["date"]
        total_posts = summary["total_posts"]
        total_detections = summary["total_detections"]
        today_posts = summary["today_posts"]
        today_detections = summary["today_detections"]
        week_posts = summary["week_posts"]
        week_detections = summary["week_detections"]
        severity_counts = summary["severity_counts"]
        by_source = summary["by_source"]
        detections = summary["detections"]

        sev_rows = ""
        for sev in ("high", "medium", "low"):
            cnt = severity_counts.get(sev, 0)
            if cnt:
                color = SEVERITY_COLOR.get(sev, "#999")
                sev_kr = SEVERITY_KR.get(sev, sev)
                sev_rows += f'<span style="color:{color};font-weight:bold;">{sev_kr} {cnt}건</span> &nbsp; '

        source_text = ", ".join(f"{k} {v}건" for k, v in by_source.items()) or "없음"

        det_rows = ""
        for d in detections:
            color = SEVERITY_COLOR.get(d["severity"], "#999")
            sev_kr = SEVERITY_KR.get(d["severity"], d["severity"])
            safe_title = html.escape(d.get("title") or "(제목 없음)")
            safe_link = html.escape(d.get("link", ""))
            safe_blogger = html.escape(d.get("blogger_name", ""))
            safe_reason = html.escape(d.get("ai_reason", ""))
            cats = d.get("categories", "")
            if isinstance(cats, str):
                import json as _json
                try:
                    cats = _json.loads(cats)
                except Exception:
                    cats = []
            cats_text = html.escape(", ".join(cats)) if cats else "-"
            raw_date = d.get("post_date", "") or ""
            post_date = raw_date[:8] if raw_date else "-"
            if post_date != "-" and len(post_date) == 8:
                post_date = f"{post_date[:4]}-{post_date[4:6]}-{post_date[6:8]}"
            det_rows += f"""
            <tr>
              <td style="padding:8px;border:1px solid #ddd;">
                <a href="{safe_link}" style="color:#1a73e8;">{safe_title}</a>
              </td>
              <td style="padding:8px;border:1px solid #ddd;">{safe_blogger}</td>
              <td style="padding:8px;border:1px solid #ddd;text-align:center;">{post_date}</td>
              <td style="padding:8px;border:1px solid #ddd;color:{color};font-weight:bold;">{sev_kr}</td>
              <td style="padding:8px;border:1px solid #ddd;">{cats_text}</td>
              <td style="padding:8px;border:1px solid #ddd;">{safe_reason}</td>
            </tr>"""

        detection_table = ""
        if det_rows:
            detection_table = f"""
  <h3 style="margin-top:24px;">탐지 상세</h3>
  <table style="border-collapse:collapse;width:100%;font-size:13px;">
    <thead>
      <tr style="background:#f5f5f5;">
        <th style="padding:8px;border:1px solid #ddd;">제목</th>
        <th style="padding:8px;border:1px solid #ddd;">작성자</th>
        <th style="padding:8px;border:1px solid #ddd;">작성일</th>
        <th style="padding:8px;border:1px solid #ddd;">심각도</th>
        <th style="padding:8px;border:1px solid #ddd;">카테고리</th>
        <th style="padding:8px;border:1px solid #ddd;">AI 판단</th>
      </tr>
    </thead>
    <tbody>{det_rows}</tbody>
  </table>"""

        return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"></head>
<body style="font-family:Apple SD Gothic Neo,sans-serif;color:#333;margin:20px;">
  <h2 style="color:#2c3e50;">📊 {TARGET_HOSPITAL} 일일 모니터링 리포트</h2>
  <p style="color:#666;">날짜: <strong>{date}</strong></p>
  <table style="border-collapse:collapse;font-size:14px;margin:16px 0;width:100%;max-width:500px;">
    <thead>
      <tr style="background:#f5f5f5;">
        <th style="padding:8px 12px;border:1px solid #ddd;text-align:left;"></th>
        <th style="padding:8px 12px;border:1px solid #ddd;text-align:center;">수집 게시글</th>
        <th style="padding:8px 12px;border:1px solid #ddd;text-align:center;">부적절 탐지</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td style="padding:8px 12px;border:1px solid #ddd;font-weight:bold;">금일</td>
        <td style="padding:8px 12px;border:1px solid #ddd;text-align:center;">{today_posts}건</td>
        <td style="padding:8px 12px;border:1px solid #ddd;text-align:center;">{today_detections}건</td>
      </tr>
      <tr>
        <td style="padding:8px 12px;border:1px solid #ddd;font-weight:bold;">이번주</td>
        <td style="padding:8px 12px;border:1px solid #ddd;text-align:center;">{week_posts}건</td>
        <td style="padding:8px 12px;border:1px solid #ddd;text-align:center;">{week_detections}건</td>
      </tr>
      <tr style="background:#fafafa;">
        <td style="padding:8px 12px;border:1px solid #ddd;font-weight:bold;">전체 누적</td>
        <td style="padding:8px 12px;border:1px solid #ddd;text-align:center;">{total_posts}건</td>
        <td style="padding:8px 12px;border:1px solid #ddd;text-align:center;">{total_detections}건</td>
      </tr>
    </tbody>
  </table>
  <table style="border-collapse:collapse;font-size:14px;margin:8px 0;">
    <tr><td style="padding:4px 12px 4px 0;font-weight:bold;">금일 심각도별</td><td>{sev_rows or '없음'}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;font-weight:bold;">금일 출처별</td><td>{source_text}</td></tr>
  </table>
  {detection_table}
  <p style="color:#999;font-size:11px;margin-top:20px;">
    본 메일은 자동 모니터링 시스템에서 발송된 일일 리포트입니다.
  </p>
</body>
</html>"""

    def send_daily_report(self, summary: dict) -> bool:
        """일일 리포트 이메일 발송"""
        if not self.sender or not self.recipients:
            logger.warning("이메일 설정 미완료 — 일일 리포트 발송 생략")
            return False

        access_token = self._get_oauth2_token()
        if not access_token:
            return False

        date = summary["date"]
        subject = (
            f"{self.subject_prefix} {TARGET_HOSPITAL} 일일 리포트 "
            f"[{date}] — 금일 수집 {summary['today_posts']}건, 탐지 {summary['today_detections']}건"
        )
        html_body = self._build_daily_report_html(summary)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        for attempt in range(2):
            try:
                auth_string = f"user={self.sender}\x01auth=Bearer {access_token}\x01\x01"
                with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                    server.starttls()
                    server.docmd("AUTH", "XOAUTH2 " + base64.b64encode(auth_string.encode()).decode())
                    server.sendmail(self.sender, self.recipients, msg.as_string())
                logger.info("일일 리포트 발송 성공 → %s [%s]", self.recipients, date)
                return True
            except smtplib.SMTPException as e:
                is_auth_error = (
                    isinstance(e, smtplib.SMTPAuthenticationError)
                    or (isinstance(e, smtplib.SMTPResponseException) and e.smtp_code in (530, 535))
                )
                if is_auth_error and attempt == 0:
                    logger.warning("SMTP 인증 실패 (%s) — 토큰 강제 갱신 후 재시도", e)
                    access_token = self._get_oauth2_token(force_refresh=True)
                    if not access_token:
                        return False
                elif is_auth_error:
                    logger.error("토큰 갱신 후에도 SMTP 인증 실패: %s", e)
                    return False
                else:
                    logger.error("일일 리포트 발송 실패: %s", e)
                    return False
        return False

    # TODO: 빈번한 발송 시 SMTP connection pooling 고려
    def send(self, results: list[AnalysisResult]) -> bool:
        """탐지 결과 이메일 발송 (OAuth2 XOAUTH2)"""
        if not results:
            logger.info("탐지된 게시글 없음 — 이메일 발송 생략")
            return True

        if not self.sender or not self.recipients:
            logger.warning("이메일 설정 미완료 (.env 확인 필요) — 발송 생략")
            return False

        access_token = self._get_oauth2_token()
        if not access_token:
            return False

        subject = (
            f"{self.subject_prefix} {TARGET_HOSPITAL} 부적절 게시글 {len(results)}건 탐지 "
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}]"
        )
        html_body = self._build_html(results)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        for attempt in range(2):
            try:
                auth_string = f"user={self.sender}\x01auth=Bearer {access_token}\x01\x01"
                with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                    server.starttls()
                    server.docmd("AUTH", "XOAUTH2 " + base64.b64encode(auth_string.encode()).decode())
                    server.sendmail(self.sender, self.recipients, msg.as_string())
                logger.info("이메일 발송 성공 → %s (%d건)", self.recipients, len(results))
                return True
            except smtplib.SMTPException as e:
                is_auth_error = (
                    isinstance(e, smtplib.SMTPAuthenticationError)
                    or (isinstance(e, smtplib.SMTPResponseException) and e.smtp_code in (530, 535))
                )
                if is_auth_error and attempt == 0:
                    logger.warning("SMTP 인증 실패 (%s) — 토큰 강제 갱신 후 재시도", e)
                    access_token = self._get_oauth2_token(force_refresh=True)
                    if not access_token:
                        return False
                elif is_auth_error:
                    logger.error("토큰 갱신 후에도 SMTP 인증 실패: %s", e)
                    return False
                else:
                    logger.error("이메일 발송 실패: %s", e)
                    return False
        return False

    def send_scrape_alert(self, failures: list[dict]) -> bool:
        """스크래핑 실패 경고 메일 발송

        failures: [{"url": str, "source": str, "title": str, "error": str}, ...]
        """
        if not failures:
            return True
        if not self.sender or not self.recipients:
            logger.warning("이메일 설정 미완료 — 스크래핑 경고 발송 생략")
            return False

        access_token = self._get_oauth2_token()
        if not access_token:
            return False

        now = datetime.now()
        subject = (
            f"{self.subject_prefix} 스크래핑 실패 경고 — {len(failures)}건 "
            f"[{now.strftime('%Y-%m-%d %H:%M')}]"
        )

        rows = ""
        for f in failures:
            safe_title = html.escape(f.get("title", "(제목 없음)"))
            safe_url = html.escape(f.get("url", ""))
            source = "블로그" if f.get("source") == "blog" else "카페"
            safe_error = html.escape(f.get("error", "알 수 없음"))
            rows += f"""
            <tr>
              <td style="padding:8px;border:1px solid #ddd;">
                <a href="{safe_url}" style="color:#1a73e8;">{safe_title}</a>
              </td>
              <td style="padding:8px;border:1px solid #ddd;text-align:center;">{source}</td>
              <td style="padding:8px;border:1px solid #ddd;color:#c0392b;">{safe_error}</td>
            </tr>"""

        html_body = f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"></head>
<body style="font-family:Apple SD Gothic Neo,sans-serif;color:#333;margin:20px;">
  <h2 style="color:#e67e22;">&#9888; 스크래핑 실패 경고</h2>
  <p style="color:#666;">
    {now.strftime('%Y-%m-%d %H:%M')} 모니터링 사이클에서
    <strong>{len(failures)}건</strong>의 게시글 전문 스크래핑에 실패했습니다.<br>
    DOM 구조 변경 또는 네트워크 오류가 원인일 수 있습니다.
  </p>
  <table style="border-collapse:collapse;width:100%;font-size:13px;">
    <thead>
      <tr style="background:#f5f5f5;">
        <th style="padding:8px;border:1px solid #ddd;">게시글</th>
        <th style="padding:8px;border:1px solid #ddd;">출처</th>
        <th style="padding:8px;border:1px solid #ddd;">실패 사유</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  <p style="color:#999;font-size:11px;margin-top:20px;">
    selector 매칭 실패가 반복되면 DOM 구조 변경을 의심하세요.<br>
    crawler/content_scraper.py 의 _CONTENT_SELECTORS 를 확인하세요.
  </p>
</body>
</html>"""

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        for attempt in range(2):
            try:
                auth_string = f"user={self.sender}\x01auth=Bearer {access_token}\x01\x01"
                with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                    server.starttls()
                    server.docmd("AUTH", "XOAUTH2 " + base64.b64encode(auth_string.encode()).decode())
                    server.sendmail(self.sender, self.recipients, msg.as_string())
                logger.info("스크래핑 경고 메일 발송 성공 → %s (%d건)", self.recipients, len(failures))
                return True
            except smtplib.SMTPException as e:
                is_auth_error = (
                    isinstance(e, smtplib.SMTPAuthenticationError)
                    or (isinstance(e, smtplib.SMTPResponseException) and e.smtp_code in (530, 535))
                )
                if is_auth_error and attempt == 0:
                    logger.warning("SMTP 인증 실패 (%s) — 토큰 강제 갱신 후 재시도", e)
                    access_token = self._get_oauth2_token(force_refresh=True)
                    if not access_token:
                        return False
                elif is_auth_error:
                    logger.error("토큰 갱신 후에도 SMTP 인증 실패: %s", e)
                    return False
                else:
                    logger.error("스크래핑 경고 메일 발송 실패: %s", e)
                    return False
        return False
