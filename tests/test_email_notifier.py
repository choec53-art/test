"""이메일 알림 발송기 테스트"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch, mock_open

import pytest

from crawler.naver_crawler import NaverPost
from analyzer.content_analyzer import AnalysisResult


def _make_result(severity="high"):
    post = NaverPost(
        source="blog", title="테스트 제목", description="테스트 내용",
        link="https://test.com/1", blogger_name="테스터", cafe_name="",
        post_date="20260320", keyword="테스트", collected_at="2026-03-20T00:00:00",
    )
    return AnalysisResult(
        post=post, is_inappropriate=True, confidence=0.85,
        categories=["욕설/비하"], matched_keywords=["쓰레기"],
        ai_reason="테스트 사유", severity=severity, raw_content="테스트",
        hybrid_score=0.85, keyword_score=0.3, ai_score=0.9,
    )


class TestEmailNotifierInit:
    @patch.dict(os.environ, {
        "EMAIL_SENDER": "sender@test.com",
        "EMAIL_RECIPIENTS": "a@test.com,b@test.com",
    })
    def test_init_from_env(self):
        from notifier.email_notifier import EmailNotifier
        notifier = EmailNotifier()
        assert notifier.sender == "sender@test.com"
        assert notifier.recipients == ["a@test.com", "b@test.com"]

    @patch.dict(os.environ, {"EMAIL_SENDER": "", "EMAIL_RECIPIENTS": ""}, clear=False)
    def test_init_empty_env(self):
        from notifier.email_notifier import EmailNotifier
        notifier = EmailNotifier()
        # config의 기본값 사용


class TestBuildHtml:
    @patch.dict(os.environ, {"EMAIL_SENDER": "test@test.com", "EMAIL_RECIPIENTS": "r@test.com"})
    def test_build_html_contains_title(self):
        from notifier.email_notifier import EmailNotifier
        notifier = EmailNotifier()
        result = _make_result()
        html = notifier._build_html([result])
        assert "테스트 제목" in html
        assert "조정훈유바외과" in html

    @patch.dict(os.environ, {"EMAIL_SENDER": "test@test.com", "EMAIL_RECIPIENTS": "r@test.com"})
    def test_build_html_severity_colors(self):
        from notifier.email_notifier import EmailNotifier
        notifier = EmailNotifier()
        for severity in ["low", "medium", "high"]:
            result = _make_result(severity=severity)
            html = notifier._build_html([result])
            assert "탐지 건수" in html

    @patch.dict(os.environ, {"EMAIL_SENDER": "test@test.com", "EMAIL_RECIPIENTS": "r@test.com"})
    def test_build_html_empty_results(self):
        from notifier.email_notifier import EmailNotifier
        notifier = EmailNotifier()
        html = notifier._build_html([])
        assert "0건" in html


class TestSendEmail:
    @patch.dict(os.environ, {"EMAIL_SENDER": "", "EMAIL_RECIPIENTS": ""}, clear=False)
    def test_send_no_config(self):
        from notifier.email_notifier import EmailNotifier
        notifier = EmailNotifier()
        notifier.sender = ""
        notifier.recipients = []
        result = notifier.send([_make_result()])
        assert result is False

    @patch.dict(os.environ, {"EMAIL_SENDER": "test@test.com", "EMAIL_RECIPIENTS": "r@test.com"})
    def test_send_empty_results(self):
        from notifier.email_notifier import EmailNotifier
        notifier = EmailNotifier()
        result = notifier.send([])
        assert result is True

    @patch.dict(os.environ, {"EMAIL_SENDER": "test@test.com", "EMAIL_RECIPIENTS": "r@test.com"})
    def test_send_no_token_file(self):
        from notifier.email_notifier import EmailNotifier
        notifier = EmailNotifier()
        with patch("notifier.email_notifier.os.path.exists", return_value=False):
            result = notifier.send([_make_result()])
            assert result is False

    @patch.dict(os.environ, {"EMAIL_SENDER": "test@test.com", "EMAIL_RECIPIENTS": "r@test.com"})
    @patch("notifier.email_notifier.smtplib.SMTP")
    @patch("notifier.email_notifier.os.path.exists", return_value=True)
    def test_send_success(self, mock_exists, mock_smtp):
        from notifier.email_notifier import EmailNotifier
        notifier = EmailNotifier()

        token_data = {
            "token": "access_token",
            "refresh_token": "refresh_token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client_id",
            "client_secret": "client_secret",
        }
        mock_creds = MagicMock()
        mock_creds.expired = False
        mock_creds.token = "access_token"

        with patch("builtins.open", mock_open(read_data=json.dumps(token_data))):
            with patch("notifier.email_notifier.Credentials", return_value=mock_creds):
                mock_server = MagicMock()
                mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
                mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
                result = notifier.send([_make_result()])
                assert result is True
