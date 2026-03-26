"""네이버 크롤러 테스트"""

import re
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from crawler.naver_crawler import NaverCrawler, NaverPost


# ─── NaverPost 데이터클래스 ────────────────────────────────────────

class TestNaverPost:
    def test_create_blog_post(self):
        post = NaverPost(
            source="blog", title="테스트", description="내용",
            link="https://blog.naver.com/test", blogger_name="블로거",
            cafe_name="", post_date="20260320", keyword="테스트",
            collected_at="2026-03-20T10:00:00",
        )
        assert post.source == "blog"
        assert post.cafe_name == ""

    def test_create_cafe_post(self):
        post = NaverPost(
            source="cafe", title="카페글", description="내용",
            link="https://cafe.naver.com/test", blogger_name="작성자",
            cafe_name="테스트카페", post_date="", keyword="키워드",
            collected_at="2026-03-20T10:00:00",
        )
        assert post.source == "cafe"
        assert post.cafe_name == "테스트카페"


# ─── NaverCrawler 초기화 ──────────────────────────────────────────

class TestNaverCrawlerInit:
    def test_init_with_keys(self):
        crawler = NaverCrawler(client_id="test_id", client_secret="test_secret")
        assert crawler.client_id == "test_id"
        assert crawler.client_secret == "test_secret"

    def test_init_display_cap(self):
        crawler = NaverCrawler(client_id="id", client_secret="sec", display=200)
        assert crawler.display == 100  # 최대 100

    def test_init_display_normal(self):
        crawler = NaverCrawler(client_id="id", client_secret="sec", display=50)
        assert crawler.display == 50

    def test_headers(self):
        crawler = NaverCrawler(client_id="my_id", client_secret="my_sec")
        headers = crawler._headers
        assert headers["X-Naver-Client-Id"] == "my_id"
        assert headers["X-Naver-Client-Secret"] == "my_sec"


# ─── HTML 클리너 ──────────────────────────────────────────────────

class TestClean:
    def test_clean_html_tags(self):
        assert NaverCrawler._clean("<b>볼드</b> 텍스트") == "볼드 텍스트"

    def test_clean_nested_tags(self):
        assert NaverCrawler._clean("<div><span>안녕</span></div>") == "안녕"

    def test_clean_no_tags(self):
        assert NaverCrawler._clean("일반 텍스트") == "일반 텍스트"

    def test_clean_empty(self):
        assert NaverCrawler._clean("") == ""

    def test_clean_whitespace(self):
        assert NaverCrawler._clean("  공백  ") == "공백"


# ─── API 호출 (_search) ──────────────────────────────────────────

class TestSearch:
    def setup_method(self):
        self.crawler = NaverCrawler(client_id="id", client_secret="sec")

    @patch("crawler.naver_crawler.requests.get")
    def test_search_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": [{"title": "결과1"}, {"title": "결과2"}]}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = self.crawler._search(NaverCrawler.BLOG_URL, "테스트")
        assert len(result) == 2
        mock_get.assert_called_once()

    @patch("crawler.naver_crawler.requests.get")
    def test_search_empty(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": []}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = self.crawler._search(NaverCrawler.BLOG_URL, "없는키워드")
        assert result == []

    @patch("crawler.naver_crawler.requests.get")
    def test_search_api_error(self, mock_get):
        import requests
        mock_get.side_effect = requests.RequestException("API 오류")

        result = self.crawler._search(NaverCrawler.BLOG_URL, "테스트")
        assert result == []

    @patch("crawler.naver_crawler.requests.get")
    def test_search_params(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": []}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        self.crawler._search(NaverCrawler.BLOG_URL, "키워드", start=101)
        args, kwargs = mock_get.call_args
        assert kwargs["params"]["query"] == '"키워드"'  # 쌍따옴표 래핑
        assert kwargs["params"]["start"] == 101
        assert kwargs["params"]["sort"] == "date"


# ─── 페이지네이션 (_search_all) ───────────────────────────────────

class TestSearchAll:
    def setup_method(self):
        self.crawler = NaverCrawler(client_id="id", client_secret="sec", display=100)

    @patch.object(NaverCrawler, "_search")
    def test_search_all_multiple_pages(self, mock_search):
        mock_search.side_effect = [
            [{"title": f"item{i}"} for i in range(100)],
            [{"title": f"item{i}"} for i in range(100, 200)],
            [{"title": f"item{i}"} for i in range(200, 250)],
        ]
        result = self.crawler._search_all(NaverCrawler.BLOG_URL, "test", max_pages=3)
        assert len(result) == 250
        assert mock_search.call_count == 3

    @patch.object(NaverCrawler, "_search")
    def test_search_all_early_stop(self, mock_search):
        mock_search.side_effect = [
            [{"title": "item1"}],
            [],  # 빈 결과 → 중단
        ]
        result = self.crawler._search_all(NaverCrawler.BLOG_URL, "test", max_pages=3)
        assert len(result) == 1
        assert mock_search.call_count == 2


# ─── 블로그/카페 검색 ─────────────────────────────────────────────

class TestSearchBlogsAndCafes:
    def setup_method(self):
        self.crawler = NaverCrawler(client_id="id", client_secret="sec")

    @patch.object(NaverCrawler, "_search_all")
    def test_search_blogs_parsing(self, mock_search_all):
        mock_search_all.return_value = [
            {
                "title": "<b>테스트</b> 병원",
                "description": "<b>좋은</b> 후기",
                "link": "https://blog.naver.com/test1",
                "bloggername": "블로거A",
                "postdate": "20260320",
            }
        ]
        posts = self.crawler.search_blogs("테스트")
        assert len(posts) == 1
        assert posts[0].source == "blog"
        assert posts[0].title == "테스트 병원"  # HTML 제거됨
        assert posts[0].blogger_name == "블로거A"
        assert posts[0].cafe_name == ""

    @patch.object(NaverCrawler, "_search_all")
    def test_search_cafes_parsing(self, mock_search_all):
        mock_search_all.return_value = [
            {
                "title": "<b>테스트</b> 카페 글",
                "description": "테스트 카페 내용",
                "link": "https://cafe.naver.com/test1",
                "writername": "작성자B",
                "cafename": "맘카페",
                "postdate": "",
            }
        ]
        posts = self.crawler.search_cafes("테스트")
        assert len(posts) == 1
        assert posts[0].source == "cafe"
        assert posts[0].blogger_name == "작성자B"
        assert posts[0].cafe_name == "맘카페"


# ─── collect_all 통합 수집 ────────────────────────────────────────

class TestCollectAll:
    def setup_method(self):
        self.crawler = NaverCrawler(client_id="id", client_secret="sec")

    @patch.object(NaverCrawler, "search_cafes")
    @patch.object(NaverCrawler, "search_blogs")
    def test_collect_dedup_by_link(self, mock_blogs, mock_cafes):
        """같은 링크는 중복 제거"""
        mock_blogs.return_value = [
            NaverPost("blog", "제목1", "내용", "https://link1", "블로거", "",
                      "20260325", "kw", "2026-03-25T00:00:00"),
            NaverPost("blog", "제목2", "내용", "https://link1", "블로거", "",
                      "20260325", "kw", "2026-03-25T00:00:00"),
        ]
        mock_cafes.return_value = []
        result = self.crawler.collect_all(["kw"], days=7)
        assert len(result) == 1

    @patch.object(NaverCrawler, "search_cafes")
    @patch.object(NaverCrawler, "search_blogs")
    def test_collect_known_links_excluded(self, mock_blogs, mock_cafes):
        """known_links에 있는 링크 제외"""
        mock_blogs.return_value = [
            NaverPost("blog", "제목", "내용", "https://known", "블로거", "",
                      "20260325", "kw", "2026-03-25T00:00:00"),
        ]
        mock_cafes.return_value = []
        result = self.crawler.collect_all(["kw"], days=7, known_links={"https://known"})
        assert len(result) == 0

    @patch.object(NaverCrawler, "search_cafes")
    @patch.object(NaverCrawler, "_search_all")
    def test_collect_date_filter_blog(self, mock_search_all, mock_cafes):
        """블로그: 기간 외 게시글은 _search_all의 cutoff에서 필터링됨"""
        old_date = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
        recent_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        # _search_all에 cutoff이 전달되므로 오래된 항목은 이미 필터링됨
        mock_search_all.return_value = [
            {"title": "최근글 kw 포함", "description": "내용", "link": "https://new",
             "bloggername": "블로거", "postdate": recent_date},
        ]
        mock_cafes.return_value = []
        result = self.crawler.collect_all(["kw"], days=7)
        assert len(result) == 1
        assert "최근글" in result[0].title
        # cutoff이 _search_all에 전달되었는지 확인
        call_kwargs = mock_search_all.call_args
        assert call_kwargs[1].get("cutoff") or (len(call_kwargs[0]) > 3 and call_kwargs[0][3])

    @patch.object(NaverCrawler, "search_cafes")
    @patch.object(NaverCrawler, "search_blogs")
    def test_collect_cafe_no_date_filter(self, mock_blogs, mock_cafes):
        """카페: 날짜 필터 없이 known_links로만 필터링"""
        mock_blogs.return_value = []
        mock_cafes.return_value = [
            NaverPost("cafe", "카페글", "내용", "https://cafe1", "작성자", "카페",
                      "", "kw", "2026-03-25T00:00:00"),
        ]
        result = self.crawler.collect_all(["kw"], days=7)
        assert len(result) == 1

    @patch.object(NaverCrawler, "search_cafes")
    @patch.object(NaverCrawler, "search_blogs")
    def test_collect_multiple_keywords(self, mock_blogs, mock_cafes):
        """여러 키워드에서 중복 제거"""
        post = NaverPost("blog", "제목", "내용", "https://link1", "블로거", "",
                         datetime.now().strftime("%Y%m%d"), "kw1", "2026-03-25T00:00:00")
        mock_blogs.return_value = [post]
        mock_cafes.return_value = []
        result = self.crawler.collect_all(["kw1", "kw2"], days=7)
        # 같은 link → 두 번째 키워드에서 중복 제거
        assert len(result) == 1

    @patch.object(NaverCrawler, "search_cafes")
    @patch.object(NaverCrawler, "search_blogs")
    def test_collect_naver_datetime_format(self, mock_blogs, mock_cafes):
        """네이버 API 실제 날짜 형식(yyyyMMddTHHmmss+0900)도 필터링 통과"""
        recent = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        naver_format_date = recent + "T120000+0900"
        mock_blogs.return_value = [
            NaverPost("blog", "네이버형식", "내용", "https://naver1", "블로거", "",
                      naver_format_date, "kw", "2026-03-25T00:00:00"),
        ]
        mock_cafes.return_value = []
        result = self.crawler.collect_all(["kw"], days=7)
        assert len(result) == 1

    @patch.object(NaverCrawler, "_search")
    def test_search_all_cutoff_filters_old_posts(self, mock_search):
        """_search_all cutoff: 오래된 게시글이 나오면 필터링 + 페이지네이션 중단"""
        old_date = "20200101"
        recent_date = datetime.now().strftime("%Y%m%d")
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
        mock_search.return_value = [
            {"postdate": recent_date, "title": "최근글"},
            {"postdate": old_date, "title": "오래된글"},
        ]
        result = self.crawler._search_all(
            NaverCrawler.BLOG_URL, "test", max_pages=3, cutoff=cutoff,
        )
        assert len(result) == 1
        assert result[0]["title"] == "최근글"
        # 오래된 항목 발견 후 다음 페이지 호출 없이 1번만 호출
        assert mock_search.call_count == 1

    @patch.object(NaverCrawler, "_search")
    def test_search_all_cutoff_empty_postdate_excluded(self, mock_search):
        """_search_all cutoff: postdate가 빈 문자열이면 필터링됨"""
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
        mock_search.return_value = [
            {"postdate": "", "title": "날짜없음"},
        ]
        result = self.crawler._search_all(
            NaverCrawler.BLOG_URL, "test", max_pages=3, cutoff=cutoff,
        )
        assert len(result) == 0
