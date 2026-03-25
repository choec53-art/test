"""
네이버 블로그 / 카페 게시글 크롤러
네이버 검색 API를 사용하여 특정 키워드가 포함된 게시글을 수집합니다.
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class NaverPost:
    """수집된 게시글/댓글 단위"""
    source: str          # "blog" | "cafe"
    title: str
    description: str     # 미리보기 텍스트
    link: str
    blogger_name: str    # 블로그명 또는 카페명
    cafe_name: str       # 카페 전용 (blog이면 빈 문자열)
    post_date: str       # 원문 날짜 문자열 (yyyyMMddTHHmmss+0900)
    keyword: str         # 검색에 사용된 키워드
    collected_at: str    # 수집 시각 (ISO 8601)


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

    def _search(self, url: str, query: str, start: int = 1) -> list[dict]:
        """네이버 검색 API 호출 (단일 페이지)"""
        params = {
            "query": query,
            "display": self.display,
            "start": start,
            "sort": "date",  # 최신순
        }
        try:
            resp = requests.get(url, headers=self._headers, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json().get("items", [])
        except requests.RequestException as e:
            logger.error("네이버 API 요청 실패: %s", e)
            return []

    def _search_all(self, url: str, query: str, max_pages: int = 3) -> list[dict]:
        """최대 max_pages 페이지까지 수집 (API 한도: start 최대 1000)"""
        results = []
        for page in range(max_pages):
            start = page * self.display + 1
            items = self._search(url, query, start=start)
            if not items:
                break
            results.extend(items)
        return results

    def search_blogs(self, keyword: str) -> list[NaverPost]:
        """블로그 게시글 검색"""
        items = self._search_all(self.BLOG_URL, keyword)
        collected_at = datetime.now().isoformat()
        posts = []
        for item in items:
            posts.append(NaverPost(
                source="blog",
                title=self._clean(item.get("title", "")),
                description=self._clean(item.get("description", "")),
                link=item.get("link", ""),
                blogger_name=self._clean(item.get("bloggername", "")),
                cafe_name="",
                post_date=item.get("postdate", ""),
                keyword=keyword,
                collected_at=collected_at,
            ))
        logger.info("[블로그] '%s' 검색 결과: %d건", keyword, len(posts))
        return posts

    def search_cafes(self, keyword: str) -> list[NaverPost]:
        """카페 게시글 검색"""
        items = self._search_all(self.CAFE_URL, keyword)
        collected_at = datetime.now().isoformat()
        posts = []
        for item in items:
            posts.append(NaverPost(
                source="cafe",
                title=self._clean(item.get("title", "")),
                description=self._clean(item.get("description", "")),
                link=item.get("link", ""),
                blogger_name=self._clean(item.get("writername", "")),
                cafe_name=self._clean(item.get("cafename", "")),
                post_date=item.get("postdate", ""),
                keyword=keyword,
                collected_at=collected_at,
            ))
        logger.info("[카페] '%s' 검색 결과: %d건", keyword, len(posts))
        return posts

    def collect_all(self, keywords: list[str]) -> list[NaverPost]:
        """모든 키워드에 대해 블로그 + 카페 통합 수집"""
        all_posts: list[NaverPost] = []
        seen_links: set[str] = set()

        for keyword in keywords:
            for post in self.search_blogs(keyword) + self.search_cafes(keyword):
                if post.link not in seen_links:
                    seen_links.add(post.link)
                    all_posts.append(post)

        logger.info("총 수집 게시글: %d건 (중복 제거)", len(all_posts))
        return all_posts

    @staticmethod
    def _clean(text: str) -> str:
        """HTML 태그 제거"""
        import re
        return re.sub(r"<[^>]+>", "", text).strip()
