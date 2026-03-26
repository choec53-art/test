"""콘텐츠 분석기 테스트"""

from unittest.mock import MagicMock, patch

import pytest

from crawler.naver_crawler import NaverPost


def _make_post(title="테스트", description="내용", source="blog"):
    return NaverPost(
        source=source, title=title, description=description,
        link="https://test.com/1", blogger_name="테스터", cafe_name="",
        post_date="20260320", keyword="테스트", collected_at="2026-03-20T00:00:00",
    )


# ─── 키워드 필터링 ────────────────────────────────────────────────

class TestKeywordFilter:
    @patch("analyzer.content_analyzer.ContentAnalyzer._init_clients")
    def setup_method(self, method, mock_init=None):
        from analyzer.content_analyzer import ContentAnalyzer
        self.analyzer = ContentAnalyzer()

    def test_no_match(self):
        cats, kws = self.analyzer.keyword_filter("좋은 병원입니다 추천합니다")
        assert cats == []
        assert kws == []

    def test_single_category_match(self):
        cats, kws = self.analyzer.keyword_filter("이 병원 정말 불친절하다")
        assert "불친절/서비스 불만" in cats
        assert "불친절" in kws

    def test_multiple_category_match(self):
        cats, kws = self.analyzer.keyword_filter("돌팔이 의사 수술 실패 고소당할듯")
        assert "욕설/비하" in cats
        assert "허위 의료 정보" in cats
        assert "명예훼손" in cats

    def test_profanity_detection(self):
        cats, kws = self.analyzer.keyword_filter("쓰레기 같은 병원")
        assert "욕설/비하" in cats
        assert "쓰레기" in kws

    def test_fake_review_detection(self):
        cats, kws = self.analyzer.keyword_filter("이거 광고글 아니야? 돈 받고 쓴 글")
        assert "허위 리뷰 조작 의심" in cats

    def test_hygiene_detection(self):
        cats, kws = self.analyzer.keyword_filter("병원이 너무 더러워서 비위생적")
        assert "위생/시설 문제" in cats

    def test_financial_detection(self):
        cats, kws = self.analyzer.keyword_filter("진료비 바가지 과다청구")
        assert "금전/보험 문제" in cats

    def test_case_insensitive(self):
        """lower_text도 체크하므로 대소문자 혼합 처리"""
        cats, kws = self.analyzer.keyword_filter("불친절")
        assert len(kws) > 0


# ─── 키워드 스코어 ────────────────────────────────────────────────

class TestKeywordScore:
    @patch("analyzer.content_analyzer.ContentAnalyzer._init_clients")
    def setup_method(self, method, mock_init=None):
        from analyzer.content_analyzer import ContentAnalyzer
        self.analyzer = ContentAnalyzer()

    def test_no_keywords_zero_score(self):
        score = self.analyzer._calc_keyword_score([], [])
        assert score == 0.0

    def test_one_category_one_keyword(self):
        score = self.analyzer._calc_keyword_score(["욕설/비하"], ["쓰레기"])
        assert 0.0 < score < 1.0

    def test_max_score(self):
        cats = ["욕설/비하", "허위 의료 정보", "명예훼손"]
        kws = ["쓰레기", "돌팔이", "수술 실패", "고소당", "불친절"]
        score = self.analyzer._calc_keyword_score(cats, kws)
        assert score == 1.0

    def test_score_increases_with_more_keywords(self):
        s1 = self.analyzer._calc_keyword_score(["cat1"], ["kw1"])
        s2 = self.analyzer._calc_keyword_score(["cat1", "cat2"], ["kw1", "kw2", "kw3"])
        assert s2 > s1


# ─── AI 스코어 ────────────────────────────────────────────────────

class TestAIScore:
    @patch("analyzer.content_analyzer.ContentAnalyzer._init_clients")
    def setup_method(self, method, mock_init=None):
        from analyzer.content_analyzer import ContentAnalyzer
        self.analyzer = ContentAnalyzer()

    def test_inappropriate_returns_confidence(self):
        assert self.analyzer._calc_ai_score(True, 0.9) == 0.9

    def test_appropriate_returns_zero(self):
        assert self.analyzer._calc_ai_score(False, 0.9) == 0.0

    def test_inappropriate_low_confidence(self):
        assert self.analyzer._calc_ai_score(True, 0.3) == 0.3


# ─── 심각도 판정 ──────────────────────────────────────────────────

class TestSeverity:
    @patch("analyzer.content_analyzer.ContentAnalyzer._init_clients")
    def setup_method(self, method, mock_init=None):
        from analyzer.content_analyzer import ContentAnalyzer
        self.analyzer = ContentAnalyzer()

    def test_high_severity(self):
        assert self.analyzer._determine_severity(0.9) == "high"
        assert self.analyzer._determine_severity(0.8) == "high"

    def test_medium_severity(self):
        assert self.analyzer._determine_severity(0.7) == "medium"
        assert self.analyzer._determine_severity(0.6) == "medium"

    def test_low_severity(self):
        assert self.analyzer._determine_severity(0.5) == "low"
        assert self.analyzer._determine_severity(0.1) == "low"


# ─── 하이브리드 분석 ──────────────────────────────────────────────

class TestHybridAnalysis:
    @patch("analyzer.content_analyzer.ContentAnalyzer._init_clients")
    def setup_method(self, method, mock_init=None):
        from analyzer.content_analyzer import ContentAnalyzer
        self.analyzer = ContentAnalyzer()

    @patch.object(
        __import__("analyzer.content_analyzer", fromlist=["ContentAnalyzer"]).ContentAnalyzer,
        "ai_analyze",
    )
    def test_analyze_inappropriate(self, mock_ai):
        mock_ai.return_value = (True, 0.9, "부적절한 표현 포함", "high")
        post = _make_post(title="돌팔이 의사 수술 실패", description="쓰레기 같은 병원")
        result = self.analyzer.analyze(post)
        assert result.is_inappropriate is True
        assert result.hybrid_score >= 0.5
        assert result.severity in ("medium", "high")

    @patch.object(
        __import__("analyzer.content_analyzer", fromlist=["ContentAnalyzer"]).ContentAnalyzer,
        "ai_analyze",
    )
    def test_analyze_appropriate(self, mock_ai):
        mock_ai.return_value = (False, 0.1, "정상적인 후기", "low")
        post = _make_post(title="좋은 병원 추천", description="친절하고 깔끔합니다")
        result = self.analyzer.analyze(post)
        assert result.is_inappropriate is False
        assert result.hybrid_score < 0.5

    @patch.object(
        __import__("analyzer.content_analyzer", fromlist=["ContentAnalyzer"]).ContentAnalyzer,
        "ai_analyze",
    )
    def test_analyze_keyword_only_inappropriate(self, mock_ai):
        """AI는 정상이라 했지만 키워드가 많으면"""
        mock_ai.return_value = (False, 0.1, "정상", "low")
        post = _make_post(
            title="돌팔이 쓰레기 병신 사기꾼",
            description="수술 실패 의료사고 고소당할 불친절 바가지",
        )
        result = self.analyzer.analyze(post)
        # 키워드 점수만으로도 0.3 weight → 최대 0.3, threshold 0.5 미만
        assert result.keyword_score > 0

    @patch.object(
        __import__("analyzer.content_analyzer", fromlist=["ContentAnalyzer"]).ContentAnalyzer,
        "ai_analyze",
    )
    def test_analyze_no_llm_fallback(self, mock_ai):
        """LLM 분석 불가 시"""
        mock_ai.return_value = (False, 0.0, "LLM 분석 불가", "low")
        post = _make_post(title="일반 글", description="내용")
        result = self.analyzer.analyze(post)
        assert result.ai_score == 0.0


# ─── 배치 분석 ────────────────────────────────────────────────────

class TestAnalyzeBatch:
    @patch("analyzer.content_analyzer.ContentAnalyzer._init_clients")
    def setup_method(self, method, mock_init=None):
        from analyzer.content_analyzer import ContentAnalyzer
        self.analyzer = ContentAnalyzer()

    @patch.object(
        __import__("analyzer.content_analyzer", fromlist=["ContentAnalyzer"]).ContentAnalyzer,
        "ai_analyze",
    )
    def test_batch_counts(self, mock_ai):
        mock_ai.return_value = (True, 0.9, "부적절", "high")
        posts = [
            _make_post(title="쓰레기 병원 돌팔이", description="수술 실패 의료사고"),
            _make_post(title="좋은 병원", description="추천합니다"),
        ]
        summary = self.analyzer.analyze_batch(posts)
        assert summary.total_checked == 2


# ─── LLM 응답 파싱 ────────────────────────────────────────────────

class TestParseResponse:
    @patch("analyzer.content_analyzer.ContentAnalyzer._init_clients")
    def setup_method(self, method, mock_init=None):
        from analyzer.content_analyzer import ContentAnalyzer
        self.analyzer = ContentAnalyzer()

    def test_parse_json(self):
        raw = '{"is_inappropriate": true, "confidence": 0.85, "reason": "욕설 포함", "severity": "high"}'
        is_inapp, conf, reason, sev = self.analyzer._parse_response(raw)
        assert is_inapp is True
        assert conf == 0.85
        assert reason == "욕설 포함"
        assert sev == "high"

    def test_parse_json_with_code_block(self):
        raw = '```json\n{"is_inappropriate": false, "confidence": 0.1, "reason": "정상", "severity": "low"}\n```'
        is_inapp, conf, reason, sev = self.analyzer._parse_response(raw)
        assert is_inapp is False
        assert sev == "low"

    def test_parse_invalid_json(self):
        with pytest.raises(Exception):
            self.analyzer._parse_response("이건 JSON이 아닙니다")

    def test_parse_missing_fields(self):
        raw = '{"is_inappropriate": true}'
        is_inapp, conf, reason, sev = self.analyzer._parse_response(raw)
        assert is_inapp is True
        assert conf == 0.0
        assert reason == ""
        assert sev == "low"


# ─── AI 분석 폴백 ─────────────────────────────────────────────────

class TestAIAnalyzeFallback:
    @patch("analyzer.content_analyzer.ContentAnalyzer._init_clients")
    def setup_method(self, method, mock_init=None):
        from analyzer.content_analyzer import ContentAnalyzer
        self.analyzer = ContentAnalyzer()

    def test_no_client_returns_default(self):
        self.analyzer._aoai_client = None
        self.analyzer._anthropic_client = None
        post = _make_post()
        result = self.analyzer.ai_analyze(post)
        assert result == (False, 0.0, "LLM 분석 불가", "low")

    def test_aoai_success(self):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = '{"is_inappropriate": true, "confidence": 0.8, "reason": "테스트", "severity": "medium"}'
        mock_resp.usage.prompt_tokens = 100
        mock_resp.usage.completion_tokens = 50
        mock_client.chat.completions.create.return_value = mock_resp
        self.analyzer._aoai_client = mock_client

        post = _make_post()
        is_inapp, conf, reason, sev = self.analyzer.ai_analyze(post)
        assert is_inapp is True
        assert conf == 0.8

    def test_aoai_fail_claude_fallback(self):
        self.analyzer._aoai_client = MagicMock()
        self.analyzer._aoai_client.chat.completions.create.side_effect = Exception("AOAI 오류")

        mock_claude = MagicMock()
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock()]
        mock_msg.content[0].text = '{"is_inappropriate": false, "confidence": 0.2, "reason": "정상", "severity": "low"}'
        mock_claude.messages.create.return_value = mock_msg
        self.analyzer._anthropic_client = mock_claude

        post = _make_post()
        is_inapp, conf, reason, sev = self.analyzer.ai_analyze(post)
        assert is_inapp is False
        assert conf == 0.2
