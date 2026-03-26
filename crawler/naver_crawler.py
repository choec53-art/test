"""
네이버 블로그 / 카페 게시글 크롤러
네이버 검색 API를 사용하여 특정 키워드가 포함된 게시글을 수집합니다.
"""

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)


@dataclass
class NaverPost:
    """수집된 게시글/댓글 단위"""
    source: str          # "blog" | "cafe"
    title: str
    description: str     # 미리보기 텍스트 (API 제공, ~200자)
    link: str
    blogger_name: str    # 블로그명 또는 카페명
    cafe_name: str       # 카페 전용 (blog이면 빈 문자열)
    post_date: str       # 원문 날짜 문자열 (yyyyMMddTHHmmss+0900)
    keyword: str         # 검색에 사용된 키워드
    collected_at: str    # 수집 시각 (ISO 8601)
    full_content: str = ""  # 전문 스크래핑 결과 (없으면 빈 문자열)


class NaverCrawler:
    """네이버 블로그 및 카페 검색 API 크롤러"""

    BLOG_URL = "https://openapi.naver.com/v1/search/blog.json"
    CAFE_URL = "https://openapi.naver.com/v1/search/cafearticle.json"

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        display: int = 100,
    ):
        self.client_id = client_id or os.getenv("NAVER_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("NAVER_CLIENT_SECRET", "")
        self.display = min(display, 100)

        if not self.client_id or not self.client_secret:
            logger.warning("네이버 API 키가 설정되지 않았습니다. .env 파일을 확인하세요.")

    @property
    def _headers(self) -> dict:
        return {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
        }

    @retry(
        retry=retry_if_exception_type(requests.RequestException),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
    )
    def _search_with_retry(self, url: str, params: dict) -> list[dict]:
        """네이버 검색 API 호출 (재시도 포함, 단일 페이지)"""
        resp = requests.get(url, headers=self._headers, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json().get("items", [])

    def _search(self, url: str, query: str, start: int = 1) -> list[dict]:
        """네이버 검색 API 호출 (단일 페이지)"""
        # 쌍따옴표로 감싸 정확한 구문 매칭 강제 (형태소 분리 방지)
        exact_query = f'"{query}"' if not query.startswith('"') else query
        params = {
            "query": exact_query,
            "display": self.display,
            "start": start,
            "sort": "date",  # 최신순
        }
        # Rate limiting: Naver API 호출 간 간격을 두어 rate limit 방지
        time.sleep(0.2)
        try:
            return self._search_with_retry(url, params)
        except requests.RequestException as e:
            logger.error("네이버 API 요청 실패 (재시도 소진): %s", e)
            return []

    def _search_all(
        self, url: str, query: str, max_pages: int = 3, cutoff: str = "",
    ) -> list[dict]:
        """최대 max_pages 페이지까지 수집 (API 한도: start 최대 1000)

        cutoff이 지정되면 postdate가 cutoff보다 오래된 항목을 만나는 즉시
        해당 항목을 제외하고 페이지네이션을 중단합니다. (sort=date 최신순 전제)
        """
        results = []
        for page in range(max_pages):
            start = page * self.display + 1
            items = self._search(url, query, start=start)
            if not items:
                break

            if cutoff:
                filtered = []
                hit_old = False
                for item in items:
                    if item.get("postdate", "99999999")[:8] >= cutoff:
                        filtered.append(item)
                    else:
                        hit_old = True
                results.extend(filtered)
                if hit_old:
                    break
            else:
                results.extend(items)
        return results

    def search_blogs(self, keyword: str, cutoff: str = "") -> list[NaverPost]:
        """블로그 게시글 검색"""
        items = self._search_all(self.BLOG_URL, keyword, cutoff=cutoff)
        collected_at = datetime.now().isoformat()
        posts = []
        for item in items:
            post = NaverPost(
                source="blog",
                title=self._clean(item.get("title", "")),
                description=self._clean(item.get("description", "")),
                link=item.get("link", ""),
                blogger_name=self._clean(item.get("bloggername", "")),
                cafe_name="",
                post_date=item.get("postdate", ""),
                keyword=keyword,
                collected_at=collected_at,
            )
            if self._contains_keyword(post, keyword):
                posts.append(post)
        logger.info("[블로그] '%s' 검색 결과: %d건 (API %d건 중 키워드 검증 통과)", keyword, len(posts), len(items))
        return posts

    def search_cafes(self, keyword: str) -> list[NaverPost]:
        """카페 게시글 검색"""
        items = self._search_all(self.CAFE_URL, keyword)
        collected_at = datetime.now().isoformat()
        posts = []
        for item in items:
            post = NaverPost(
                source="cafe",
                title=self._clean(item.get("title", "")),
                description=self._clean(item.get("description", "")),
                link=item.get("link", ""),
                blogger_name=self._clean(item.get("writername", "")),
                cafe_name=self._clean(item.get("cafename", "")),
                post_date=item.get("postdate", ""),
                keyword=keyword,
                collected_at=collected_at,
            )
            if self._contains_keyword(post, keyword):
                posts.append(post)
        logger.info("[카페] '%s' 검색 결과: %d건 (API %d건 중 키워드 검증 통과)", keyword, len(posts), len(items))
        return posts

    def collect_all(
        self,
        keywords: list[str],
        days: int = 30,
        known_links: set[str] | None = None,
    ) -> list[NaverPost]:
        """모든 키워드에 대해 블로그 + 카페 통합 수집

        - 블로그: postdate 기반 날짜 필터링 (최근 N일)
        - 카페: postdate 없으므로 known_links 기반 필터링
          (이미 수집된 링크를 제외하고 신규 게시글만 반환)
        """
        all_posts: list[NaverPost] = []
        seen_links: set[str] = set(known_links or set())
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

        for keyword in keywords:
            # 블로그: API 페이지네이션 단계에서 날짜 필터링 (cutoff 이전이면 즉시 중단)
            for post in self.search_blogs(keyword, cutoff=cutoff):
                if post.link not in seen_links:
                    seen_links.add(post.link)
                    all_posts.append(post)

            # 카페: known_links 기반 필터링 (날짜 없으므로 신규 링크만 수집)
            # TODO: 카페 게시글은 검색 API 응답에 정확한 날짜가 없어 날짜 필터링 불가.
            #       날짜 기반 필터링을 하려면 카페 상세 페이지 크롤링이 필요하며,
            #       이는 현재 범위 밖이므로 known_links 기반 중복 제거만 적용.
            for post in self.search_cafes(keyword):
                if post.link not in seen_links:
                    seen_links.add(post.link)
                    all_posts.append(post)

        logger.info("총 수집 게시글: %d건 (중복 제거, 최근 %d일)", len(all_posts), days)
        return all_posts

    @staticmethod
    def _clean(text: str) -> str:
        """HTML 태그 제거"""
        import re
        return re.sub(r"<[^>]+>", "", text).strip()

    @staticmethod
    def _contains_keyword(post: "NaverPost", keyword: str) -> bool:
        """게시글 제목/본문에 키워드가 실제로 포함되어 있는지 검증"""
        text = f"{post.title} {post.description}".lower()
        # 띄어쓰기 제거 후 비교 (키워드 변형 대응)
        normalized_text = text.replace(" ", "")
        normalized_kw = keyword.lower().replace(" ", "")
        return normalized_kw in normalized_text
