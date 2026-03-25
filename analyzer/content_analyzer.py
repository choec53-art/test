"""
콘텐츠 분석기
1단계: 키워드 기반 1차 필터링 (빠른 처리)
2단계: Claude API 기반 문맥 분석 (정밀 판단)
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import anthropic
from dotenv import load_dotenv

from config import INAPPROPRIATE_CATEGORIES, TARGET_HOSPITAL, CLAUDE_MODEL
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
    ai_reason: str                 # Claude의 판단 근거
    severity: str                  # "low" | "medium" | "high"
    raw_content: str               # 분석에 사용된 전체 텍스트


@dataclass
class AnalysisSummary:
    """배치 분석 요약"""
    total_checked: int = 0
    inappropriate_count: int = 0
    results: list[AnalysisResult] = field(default_factory=list)


class ContentAnalyzer:
    """게시글 부적절 표현 분석기"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._client: Optional[anthropic.Anthropic] = None

        if self.api_key:
            self._client = anthropic.Anthropic(api_key=self.api_key)
        else:
            logger.warning("ANTHROPIC_API_KEY 미설정 — 키워드 분석만 사용됩니다.")

    # ─── 1단계: 키워드 필터링 ───────────────────────────────────────

    def keyword_filter(self, text: str) -> tuple[list[str], list[str]]:
        """
        텍스트에서 부적절 키워드 탐지.
        반환: (매칭된_카테고리_목록, 매칭된_키워드_목록)
        """
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

    # ─── 2단계: Claude AI 문맥 분석 ────────────────────────────────

    def ai_analyze(self, post: NaverPost) -> tuple[bool, float, str, str]:
        """
        Claude API로 게시글 문맥 분석.
        반환: (부적절여부, 신뢰도, 판단근거, 심각도)
        """
        if not self._client:
            return False, 0.0, "API 키 없음 — AI 분석 생략", "low"

        content = f"제목: {post.title}\n내용: {post.description}"
        prompt = f"""당신은 병원 평판 모니터링 전문가입니다.
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

        try:
            message = self._client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
            # JSON 파싱
            data = json.loads(raw)
            return (
                bool(data.get("is_inappropriate", False)),
                float(data.get("confidence", 0.0)),
                str(data.get("reason", "")),
                str(data.get("severity", "low")),
            )
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.error("AI 분석 결과 파싱 실패: %s", e)
            return False, 0.0, "파싱 오류", "low"
        except anthropic.APIError as e:
            logger.error("Claude API 오류: %s", e)
            return False, 0.0, f"API 오류: {e}", "low"

    # ─── 통합 분석 ──────────────────────────────────────────────────

    def analyze(self, post: NaverPost) -> AnalysisResult:
        """게시글 단건 분석"""
        raw_content = f"{post.title} {post.description}"

        # 1단계: 키워드 필터
        categories, matched_kws = self.keyword_filter(raw_content)
        keyword_hit = len(categories) > 0

        # 2단계: AI 분석 (키워드 히트 여부와 무관하게 실행)
        ai_inappropriate, confidence, ai_reason, severity = self.ai_analyze(post)

        # 최종 판단: 키워드 히트 OR AI 판단
        is_inappropriate = keyword_hit or ai_inappropriate

        # 신뢰도 보정: 키워드 히트 시 최소 0.6 보장
        if keyword_hit and confidence < 0.6:
            confidence = 0.6

        return AnalysisResult(
            post=post,
            is_inappropriate=is_inappropriate,
            confidence=confidence,
            categories=categories,
            matched_keywords=matched_kws,
            ai_reason=ai_reason,
            severity=severity,
            raw_content=raw_content,
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

        logger.info(
            "분석 완료 — 전체: %d건, 부적절: %d건",
            summary.total_checked,
            summary.inappropriate_count,
        )
        return summary
