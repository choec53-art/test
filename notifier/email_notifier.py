"""
이메일 알림 발송
탐지된 부적절 게시글을 이메일로 보고합니다.
"""

import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from dotenv import load_dotenv

from analyzer.content_analyzer import AnalysisResult
from config import EMAIL_CONFIG, TARGET_HOSPITAL

load_dotenv()

logger = logging.getLogger(__name__)

SEVERITY_KR = {"low": "낮음", "medium": "보통", "high": "높음"}
SEVERITY_COLOR = {"low": "#f0ad4e", "medium": "#d9534f", "high": "#a02020"}


class EmailNotifier:
    """이메일 알림 발송기"""

    def __init__(self):
        cfg = EMAIL_CONFIG
        self.smtp_host = cfg["smtp_host"]
        self.smtp_port = cfg["smtp_port"]
        self.sender = os.getenv("EMAIL_SENDER", cfg.get("sender_email", ""))
        self.password = os.getenv("EMAIL_PASSWORD", cfg.get("sender_password", ""))
        raw_recipients = os.getenv("EMAIL_RECIPIENTS", "")
        self.recipients: list[str] = (
            [r.strip() for r in raw_recipients.split(",") if r.strip()]
            or cfg.get("recipient_emails", [])
        )
        self.subject_prefix = cfg.get("subject_prefix", "[병원 모니터링]")

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
        """탐지 결과 이메일 발송"""
        if not results:
            logger.info("탐지된 게시글 없음 — 이메일 발송 생략")
            return True

        if not self.sender or not self.password or not self.recipients:
            logger.warning("이메일 설정 미완료 (.env 확인 필요) — 발송 생략")
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
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.sender, self.password)
                server.sendmail(self.sender, self.recipients, msg.as_string())
            logger.info("이메일 발송 성공 → %s (%d건)", self.recipients, len(results))
            return True
        except smtplib.SMTPException as e:
            logger.error("이메일 발송 실패: %s", e)
            return False
