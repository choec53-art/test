"""
이메일 알림 발송
탐지된 부적절 게시글을 이메일로 보고합니다.
OAuth2 인증을 통해 Gmail SMTP로 발송합니다.
"""

import base64
import json
import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from analyzer.content_analyzer import AnalysisResult
from config import EMAIL_CONFIG, TARGET_HOSPITAL

load_dotenv()

logger = logging.getLogger(__name__)

SEVERITY_KR = {"low": "낮음", "medium": "보통", "high": "높음"}
SEVERITY_COLOR = {"low": "#f0ad4e", "medium": "#d9534f", "high": "#a02020"}

TOKEN_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "token.json")


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

    def _get_oauth2_token(self) -> str | None:
        """token.json에서 OAuth2 액세스 토큰을 읽고, 만료 시 자동 갱신"""
        if not os.path.exists(TOKEN_FILE):
            logger.error("token.json 없음 — python oauth2_setup.py 를 먼저 실행하세요")
            return None

        with open(TOKEN_FILE) as f:
            token_data = json.load(f)

        creds = Credentials(
            token=token_data["token"],
            refresh_token=token_data["refresh_token"],
            token_uri=token_data["token_uri"],
            client_id=token_data["client_id"],
            client_secret=token_data["client_secret"],
            scopes=token_data.get("scopes"),
        )

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_data["token"] = creds.token
            with open(TOKEN_FILE, "w") as f:
                json.dump(token_data, f, indent=2)
            logger.info("OAuth2 토큰 자동 갱신 완료")

        return creds.token

    def _build_html(self, results: list[AnalysisResult]) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        rows = ""
        for r in results:
            color = SEVERITY_COLOR.get(r.severity, "#999")
            sev_kr = SEVERITY_KR.get(r.severity, r.severity)
            categories = ", ".join(r.categories) if r.categories else "-"
            keywords = ", ".join(r.matched_keywords) if r.matched_keywords else "-"
            rows += f"""
            <tr>
              <td style="padding:8px;border:1px solid #ddd;">
                <a href="{r.post.link}" style="color:#1a73e8;">{r.post.title or '(제목 없음)'}</a>
              </td>
              <td style="padding:8px;border:1px solid #ddd;text-align:center;">
                {'블로그' if r.post.source=='blog' else '카페'}
              </td>
              <td style="padding:8px;border:1px solid #ddd;">{r.post.blogger_name}</td>
              <td style="padding:8px;border:1px solid #ddd;color:{color};font-weight:bold;">{sev_kr}</td>
              <td style="padding:8px;border:1px solid #ddd;">{categories}</td>
              <td style="padding:8px;border:1px solid #ddd;">{keywords}</td>
              <td style="padding:8px;border:1px solid #ddd;">{r.ai_reason}</td>
            </tr>"""

        return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"></head>
<body style="font-family:Apple SD Gothic Neo,sans-serif;color:#333;margin:20px;">
  <h2 style="color:#c0392b;">🏥 {TARGET_HOSPITAL} 부적절 콘텐츠 모니터링 리포트</h2>
  <p style="color:#666;">수집 시각: {now} | 탐지 건수: <strong>{len(results)}건</strong></p>
  <table style="border-collapse:collapse;width:100%;font-size:13px;">
    <thead>
      <tr style="background:#f5f5f5;">
        <th style="padding:8px;border:1px solid #ddd;">제목</th>
        <th style="padding:8px;border:1px solid #ddd;">출처</th>
        <th style="padding:8px;border:1px solid #ddd;">작성자</th>
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

        try:
            auth_string = f"user={self.sender}\x01auth=Bearer {access_token}\x01\x01"
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.docmd("AUTH", "XOAUTH2 " + base64.b64encode(auth_string.encode()).decode())
                server.sendmail(self.sender, self.recipients, msg.as_string())
            logger.info("이메일 발송 성공 → %s (%d건)", self.recipients, len(results))
            return True
        except smtplib.SMTPException as e:
            logger.error("이메일 발송 실패: %s", e)
            return False
