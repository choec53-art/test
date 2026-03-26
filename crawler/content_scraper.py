"""
게시글 전문 스크래퍼
- 블로그: requests + BeautifulSoup (모바일 페이지, 로그인 불필요)
- 카페: Playwright + 네이버 로그인 세션 (persistent context)

DOM 변경 대응 전략:
  - depth selector(div > div > ul) 사용 금지 — class 기반만 사용
  - fallback selector 체인 (신형/구형/SPA/generic 순서)
  - iframe name 의존 안 함 — URL 패턴으로 cafe frame 탐색
  - text 기반 selector 보조 (get_by_text, get_by_role)
  - 모든 selector 실패 시 page 전체 텍스트 추출 (graceful degradation)

사용 흐름:
  1) 최초 1회: python -m crawler.content_scraper --login  (브라우저에서 네이버 로그인)
  2) 이후 자동: ContentScraper.scrape(post) 호출 시 세션 재사용
"""

import logging
import os
import re
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

NAVER_SESSION_DIR = os.path.abspath(os.getenv("NAVER_SESSION_DIR", ".naver_session"))
MAX_CONTENT_LENGTH = 5000  # 전문 최대 길이 (LLM 토큰 절약)
MIN_CONTENT_LENGTH = 50    # 이보다 짧으면 추출 실패로 간주


# ─── 블로그 스크래퍼 ────────────────────────────────────────────


class BlogScraper:
    """네이버 블로그 전문 스크래퍼 (requests + BeautifulSoup, 모바일 버전)"""

    _MOBILE_BASE = "https://m.blog.naver.com"
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/17.0 Mobile/15E148 Safari/604.1"
        ),
    }

    # 본문 selector 우선순위 (신형 → 구형 → 범용)
    _SELECTORS = [
        "div.se-main-container",    # SmartEditor 3/ONE (가장 안정적)
        "div.__viewer_container",   # 모바일 뷰어 컨테이너
        "div#postViewArea",         # 구형 에디터
        "div.post-view",            # 구형 모바일
    ]

    def scrape(self, url: str) -> str:
        """블로그 게시글 전문 추출. 실패 시 빈 문자열 반환."""
        try:
            blog_id, log_no = self._parse_blog_url(url)
            if not blog_id or not log_no:
                logger.warning("블로그 URL 파싱 실패: %s", url)
                return ""

            mobile_url = f"{self._MOBILE_BASE}/{blog_id}/{log_no}"
            resp = requests.get(mobile_url, headers=self._HEADERS, timeout=10)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")

            for selector in self._SELECTORS:
                content = soup.select_one(selector)
                if content:
                    text = content.get_text(separator="\n", strip=True)
                    if len(text) >= MIN_CONTENT_LENGTH:
                        return text[:MAX_CONTENT_LENGTH]

            logger.warning("블로그 본문 셀렉터 매칭 실패: %s", url)
            return ""
        except Exception as e:
            logger.error("블로그 스크래핑 실패 [%s]: %s", url, e)
            return ""

    @staticmethod
    def _parse_blog_url(url: str) -> tuple[str, str]:
        """블로그 URL → (blogId, logNo) 추출"""
        parsed = urlparse(url)

        # /PostView.nhn?blogId=xxx&logNo=yyy
        if "PostView" in parsed.path:
            qs = parse_qs(parsed.query)
            return qs.get("blogId", [""])[0], qs.get("logNo", [""])[0]

        # /blogId/logNo
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        if len(parts) >= 2:
            return parts[0], parts[1]

        return "", ""


# ─── 카페 스크래퍼 ─────────────────────────────────────────────


class CafeScraper:
    """네이버 카페 전문 스크래퍼 (Playwright persistent context)

    DOM 변경 대응:
    - iframe: name 의존 X → URL에 "cafe" 포함된 frame 탐색
    - selector: depth selector X → class 기반 fallback 체인
    - 최종 fallback: page 전체 텍스트에서 노이즈 제거
    """

    # 본문 selector 우선순위 (class 기반, depth 사용 금지)
    _CONTENT_SELECTORS = [
        ".se-main-container",       # SmartEditor 3/ONE (가장 안정적, 오래 살아남음)
        ".ContentRenderer",         # 모바일 렌더러
        ".article_viewer",          # 구형 에디터
        ".ArticleContentBox",       # 모바일 카페 앱 뷰
        ".post_article",            # 구형 모바일
    ]

    # 노이즈 제거 대상 selector (광고, 추천글 등)
    _NOISE_SELECTORS = [
        ".ad_area", ".revenue_unit", ".u_cbox",       # 광고 / 댓글
        ".RelatedArticles", ".CafeRelatedBox",         # 추천글
        ".profile_area", ".ArticleBottomBtns",         # 프로필 / 버튼
    ]

    def __init__(self, session_dir: str = NAVER_SESSION_DIR):
        self._session_dir = session_dir
        self._pw = None
        self._context = None
        self._page = None

    def _ensure_browser(self):
        """Playwright 브라우저 lazy 초기화"""
        if self._page:
            return

        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._context = self._pw.chromium.launch_persistent_context(
            user_data_dir=self._session_dir,
            headless=True,
            locale="ko-KR",
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/17.0 Mobile/15E148 Safari/604.1"
            ),
        )
        self._page = self._context.new_page()
        logger.info("Playwright 브라우저 초기화 완료 (session: %s)", self._session_dir)

    def is_logged_in(self) -> bool:
        """네이버 로그인 상태 확인"""
        self._ensure_browser()
        self._page.goto("https://m.cafe.naver.com", wait_until="domcontentloaded")
        logged_in = "nid.naver.com" not in self._page.url
        logger.info("네이버 로그인 상태: %s", "OK" if logged_in else "미로그인")
        return logged_in

    def _find_cafe_frame(self):
        """iframe name에 의존하지 않고 URL 패턴으로 cafe frame 탐색

        네이버 카페 PC 버전은 iframe 기반이므로, frame.url에 "cafe" 키워드가
        포함된 frame을 찾는다. 모바일 버전이면 iframe 없이 메인 page 반환.
        """
        for frame in self._page.frames:
            if "cafe" in frame.url and frame != self._page.main_frame:
                return frame
        # 모바일 URL이면 iframe 없이 메인 page
        return self._page

    def _remove_noise(self, frame_or_page):
        """광고/추천글/댓글 등 노이즈 요소 DOM에서 제거"""
        for selector in self._NOISE_SELECTORS:
            try:
                frame_or_page.evaluate(
                    f'document.querySelectorAll("{selector}").forEach(el => el.remove())'
                )
            except Exception:
                pass

    def _extract_by_selectors(self, frame_or_page) -> str:
        """class 기반 selector 체인으로 본문 추출"""
        for selector in self._CONTENT_SELECTORS:
            el = frame_or_page.query_selector(selector)
            if el:
                text = el.inner_text()
                if text and len(text.strip()) >= MIN_CONTENT_LENGTH:
                    return text.strip()[:MAX_CONTENT_LENGTH]
        return ""

    def _extract_by_text_heuristic(self, frame_or_page) -> str:
        """selector 전부 실패 시 — 텍스트 길이 기반 휴리스틱 추출

        페이지 내 가장 긴 텍스트 블록을 본문으로 간주한다.
        """
        try:
            blocks = frame_or_page.query_selector_all("div, article, section")
            best = ""
            for block in blocks:
                try:
                    text = block.inner_text()
                    if text and len(text) > len(best):
                        best = text
                except Exception:
                    continue
            if len(best.strip()) >= MIN_CONTENT_LENGTH:
                return best.strip()[:MAX_CONTENT_LENGTH]
        except Exception as e:
            logger.debug("텍스트 휴리스틱 추출 실패: %s", e)
        return ""

    def scrape(self, url: str) -> str:
        """카페 게시글 전문 추출. 실패 시 빈 문자열 반환."""
        try:
            self._ensure_browser()

            # 모바일 URL로 변환 (로그인 세션 유지 + 간결한 DOM)
            mobile_url = url.replace("://cafe.naver.com", "://m.cafe.naver.com")
            self._page.goto(mobile_url, wait_until="domcontentloaded", timeout=15000)
            self._page.wait_for_timeout(2000)

            # iframe 탐색 (PC 버전 대비, 모바일이면 메인 page)
            target = self._find_cafe_frame()

            # 노이즈 제거
            self._remove_noise(target)

            # 1차: class 기반 selector 체인
            text = self._extract_by_selectors(target)
            if text:
                return text

            # 2차: 텍스트 길이 기반 휴리스틱
            text = self._extract_by_text_heuristic(target)
            if text:
                logger.info("카페 본문: selector 실패, 휴리스틱 추출 성공 (%d자)", len(text))
                return text

            logger.warning("카페 본문 추출 실패 (selector + 휴리스틱 모두 실패): %s", url)
            return ""
        except Exception as e:
            logger.error("카페 스크래핑 실패 [%s]: %s", url, e)
            return ""

    def close(self):
        """브라우저 리소스 해제"""
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
            self._page = None
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None


# ─── 통합 스크래퍼 ─────────────────────────────────────────────


class ContentScraper:
    """블로그/카페 통합 전문 스크래퍼

    post.source 값에 따라 자동 분기:
      - "blog" → BlogScraper (requests, 로그인 불필요)
      - "cafe" → CafeScraper (Playwright, 네이버 로그인 필요)
    """

    def __init__(self):
        self._blog = BlogScraper()
        self._cafe: CafeScraper | None = None  # lazy init (Playwright 무거움)
        self._cafe_available: bool | None = None

    def _get_cafe_scraper(self) -> CafeScraper | None:
        """CafeScraper lazy 초기화. Playwright 미설치 시 None 반환."""
        if self._cafe_available is False:
            return None
        if self._cafe is not None:
            return self._cafe

        try:
            self._cafe = CafeScraper()
            self._cafe_available = True
            return self._cafe
        except Exception as e:
            logger.warning("CafeScraper 초기화 실패 (Playwright 미설치?): %s", e)
            self._cafe_available = False
            return None

    def scrape(self, post) -> str:
        """게시글 전문 추출. source에 따라 자동 분기."""
        if post.source == "blog":
            return self._blog.scrape(post.link)

        if post.source == "cafe":
            cafe = self._get_cafe_scraper()
            if cafe:
                return cafe.scrape(post.link)
            logger.debug("CafeScraper 사용 불가 — 카페 전문 스킵: %s", post.link)
            return ""

        return ""

    def close(self):
        """리소스 해제"""
        if self._cafe:
            self._cafe.close()
            self._cafe = None


# ─── CLI: 네이버 로그인 세션 설정 ────────────────────────────────


def setup_naver_login():
    """대화형 네이버 로그인 — 브라우저가 열리면 로그인 후 Enter"""
    from playwright.sync_api import sync_playwright

    print(f"세션 저장 경로: {NAVER_SESSION_DIR}")
    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir=NAVER_SESSION_DIR,
        headless=False,
        locale="ko-KR",
    )
    page = context.new_page()
    page.goto("https://nid.naver.com/nidlogin.login")

    print("=" * 50)
    print("  브라우저에서 네이버 로그인을 완료한 후")
    print("  이 터미널에서 Enter를 누르세요.")
    print("=" * 50)
    input()

    context.close()
    pw.stop()
    print("로그인 세션이 저장되었습니다.")


if __name__ == "__main__":
    import sys

    if "--login" in sys.argv:
        setup_naver_login()
    else:
        print("사용법: python -m crawler.content_scraper --login")
