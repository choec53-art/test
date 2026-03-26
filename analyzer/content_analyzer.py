"""
콘텐츠 분석기
1단계: 키워드 기반 1차 필터링 (빠른 처리)
2단계: LLM 기반 문맥 분석 (AOAI 우선, Claude 폴백)
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

from config import INAPPROPRIATE_CATEGORIES, TARGET_HOSPITAL
from crawler.naver_crawler import NaverPost

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """분석 결과"""
    post: NaverPost
    is_inappropriate: bool
    confidence: float              # 0.0 ~ 1.0
    categories: list[str]          # 해당되는 부적절 카테고리들
    matched_keywords: list[str]    # 매칭된 키워드 목록
    ai_reason: str                 # AI 판단 근거
    severity: str                  # "low" | "medium" | "high"
    raw_content: str               # 분석에 사용된 전체 텍스트
    hybrid_score: float = 0.0     # 하이브리드 스코어 (0.0 ~ 1.0)
    keyword_score: float = 0.0    # 키워드 점수 (0.0 ~ 1.0)
    ai_score: float = 0.0         # AI 점수 (0.0 ~ 1.0)


@dataclass
class AnalysisSummary:
    """배치 분석 요약"""
    total_checked: int = 0
    inappropriate_count: int = 0
    results: list[AnalysisResult] = field(default_factory=list)


class ContentAnalyzer:
    """게시글 부적절 표현 분석기 (AOAI 우선, Claude 폴백)"""

    def __init__(self):
        self.provider = os.getenv("LLM_PROVIDER", "aoai")

        # AOAI 설정
        self.aoai_endpoint = os.getenv("AOAI_ENDPOINT", "")
        self.aoai_api_key = os.getenv("AOAI_API_KEY", "")
        self.aoai_deployment = os.getenv("AOAI_DEPLOYMENT", "gpt-4.1")
        self.aoai_api_version = os.getenv("AOAI_API_VERSION", "2024-12-01-preview")
        self._aoai_client = None

        # Claude 설정 (폴백)
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self._anthropic_client = None

        # 토큰 사용량 추적
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._total_requests = 0

        self._init_clients()

    def _init_clients(self):
        """LLM 클라이언트 초기화"""
        if self.aoai_endpoint and self.aoai_api_key:
            try:
                from openai import AzureOpenAI
                self._aoai_client = AzureOpenAI(
                    azure_endpoint=self.aoai_endpoint,
                    api_key=self.aoai_api_key,
                    api_version=self.aoai_api_version,
                )
                logger.info("AOAI 클라이언트 초기화 완료 (deployment: %s)", self.aoai_deployment)
            except Exception as e:
                logger.warning("AOAI 클라이언트 초기화 실패: %s", e)

        if self.anthropic_api_key:
            try:
                import anthropic
                self._anthropic_client = anthropic.Anthropic(api_key=self.anthropic_api_key)
                logger.info("Claude 클라이언트 초기화 완료 (폴백)")
            except Exception as e:
                logger.warning("Claude 클라이언트 초기화 실패: %s", e)

        if not self._aoai_client and not self._anthropic_client:
            logger.warning("LLM 클라이언트 없음 — 키워드 분석만 사용됩니다.")

    # ─── 1단계: 키워드 필터링 ───────────────────────────────────────

    def keyword_filter(self, text: str) -> tuple[list[str], list[str]]:
        matched_categories = []
        matched_keywords = []
        lower_text = text.lower()

        for category, keywords in INAPPROPRIATE_CATEGORIES.items():
            for kw in keywords:
                if kw in lower_text or kw in text:
                    if category not in matched_categories:
                        matched_categories.append(category)
                    if kw not in matched_keywords:
                        matched_keywords.append(kw)

        return matched_categories, matched_keywords

    # ─── 2단계: LLM 문맥 분석 ────────────────────────────────────────

    def _build_prompt(self, post: NaverPost) -> str:
        content = f"제목: {post.title}\n내용: {post.description}"
        return f"""당신은 병원 평판 모니터링 전문가입니다.
아래 게시글이 '{TARGET_HOSPITAL}'에 대해 부적절한 표현을 포함하고 있는지 분석하세요.

**부적절 표현 기준:**
- 욕설, 비하 표현
- 허위 의료 정보 유포
- 명예훼손성 주장
- 허위 리뷰/조작 의심 표현

**게시글:**
{content}

**반드시 아래 JSON 형식으로만 응답하세요:**
{{
  "is_inappropriate": true 또는 false,
  "confidence": 0.0~1.0 사이 숫자,
  "severity": "low" 또는 "medium" 또는 "high",
  "reason": "판단 근거를 한국어로 2~3문장으로 설명"
}}"""

    def _parse_response(self, raw: str) -> tuple[bool, float, str, str]:
        """LLM 응답 JSON 파싱"""
        # JSON 블록 추출 (```json ... ``` 처리)
        if "```" in raw:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            raw = raw[start:end]
        data = json.loads(raw)
        return (
            bool(data.get("is_inappropriate", False)),
            float(data.get("confidence", 0.0)),
            str(data.get("reason", "")),
            str(data.get("severity", "low")),
        )

    def _analyze_aoai(self, post: NaverPost) -> tuple[bool, float, str, str]:
        """Azure OpenAI로 분석"""
        prompt = self._build_prompt(post)
        response = self._aoai_client.chat.completions.create(
            model=self.aoai_deployment,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=512,
            temperature=0,
        )
        usage = response.usage
        if usage:
            self._total_prompt_tokens += usage.prompt_tokens
            self._total_completion_tokens += usage.completion_tokens
            self._total_requests += 1
        raw = response.choices[0].message.content.strip()
        return self._parse_response(raw)

    def _analyze_claude(self, post: NaverPost) -> tuple[bool, float, str, str]:
        """Claude API로 분석"""
        import anthropic
        prompt = self._build_prompt(post)
        message = self._anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        return self._parse_response(raw)

    def ai_analyze(self, post: NaverPost) -> tuple[bool, float, str, str]:
        """AOAI 우선, 실패 시 Claude 폴백"""
        # 1차: AOAI
        if self._aoai_client:
            try:
                result = self._analyze_aoai(post)
                return result
            except Exception as e:
                logger.warning("AOAI 분석 실패, Claude 폴백 시도: %s", e)

        # 2차: Claude 폴백
        if self._anthropic_client:
            try:
                result = self._analyze_claude(post)
                return result
            except Exception as e:
                logger.error("Claude 폴백도 실패: %s", e)

        return False, 0.0, "LLM 분석 불가", "low"

    # ─── 하이브리드 스코어링 ─────────────────────────────────────────
    # LLM 70% + 키워드 30%, 임계값 0.5 이상이면 부적절 판정

    WEIGHT_AI = 0.7
    WEIGHT_KEYWORD = 0.3
    HYBRID_THRESHOLD = 0.5

    def _calc_keyword_score(self, categories: list[str], matched_kws: list[str]) -> float:
        """키워드 매칭 기반 점수 산출 (0.0 ~ 1.0)"""
        if not matched_kws:
            return 0.0
        # 카테고리 다양성 + 키워드 수로 점수 산출
        cat_score = min(len(categories) / 3.0, 1.0)   # 3개 카테고리 이상이면 만점
        kw_score = min(len(matched_kws) / 5.0, 1.0)   # 5개 키워드 이상이면 만점
        return (cat_score * 0.4) + (kw_score * 0.6)

    def _calc_ai_score(self, ai_inappropriate: bool, confidence: float) -> float:
        """AI 판단 기반 부적절 점수 산출 (0.0 ~ 1.0)
        - 부적절 판단 시: 신뢰도 그대로 (높을수록 위험)
        - 정상 판단 시: 0.0 (AI가 정상이라고 판단했으므로 부적절 점수 없음)
        """
        if ai_inappropriate:
            return confidence
        return 0.0

    def _determine_severity(self, hybrid_score: float) -> str:
        """하이브리드 스코어 기반 심각도 결정"""
        if hybrid_score >= 0.8:
            return "high"
        if hybrid_score >= 0.6:
            return "medium"
        return "low"

    def analyze(self, post: NaverPost) -> AnalysisResult:
        """게시글 단건 하이브리드 분석 (LLM 70% + 키워드 30%)"""
        raw_content = f"{post.title} {post.description}"

        # 1단계: 키워드 필터
        categories, matched_kws = self.keyword_filter(raw_content)

        # 2단계: AI 분석
        ai_inappropriate, ai_confidence, ai_reason, _ = self.ai_analyze(post)

        # 3단계: 하이브리드 스코어 산출
        keyword_score = self._calc_keyword_score(categories, matched_kws)
        ai_score = self._calc_ai_score(ai_inappropriate, ai_confidence)
        hybrid_score = (self.WEIGHT_AI * ai_score) + (self.WEIGHT_KEYWORD * keyword_score)

        # 최종 판정
        is_inappropriate = hybrid_score >= self.HYBRID_THRESHOLD
        severity = self._determine_severity(hybrid_score)

        return AnalysisResult(
            post=post,
            is_inappropriate=is_inappropriate,
            confidence=hybrid_score,
            categories=categories,
            matched_keywords=matched_kws,
            ai_reason=ai_reason,
            severity=severity,
            raw_content=raw_content,
            hybrid_score=hybrid_score,
            keyword_score=keyword_score,
            ai_score=ai_score,
        )

    def analyze_batch(self, posts: list[NaverPost]) -> AnalysisSummary:
        """게시글 목록 일괄 분석"""
        summary = AnalysisSummary(total_checked=len(posts))

        for i, post in enumerate(posts, 1):
            logger.debug("[%d/%d] 분석 중: %s", i, len(posts), post.title[:40])
            result = self.analyze(post)
            if result.is_inappropriate:
                summary.inappropriate_count += 1
                summary.results.append(result)

        total_tokens = self._total_prompt_tokens + self._total_completion_tokens
        logger.info(
            "분석 완료 — 전체: %d건, 부적절: %d건 | "
            "토큰 사용량: prompt=%d, completion=%d, total=%d (요청 %d회)",
            summary.total_checked,
            summary.inappropriate_count,
            self._total_prompt_tokens,
            self._total_completion_tokens,
            total_tokens,
            self._total_requests,
        )
        return summary
