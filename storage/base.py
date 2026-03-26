"""
저장소 추상 인터페이스

로컬(SQLite)과 Azure(Table Storage) 백엔드를 동일한 인터페이스로 사용하기 위한 ABC.
"""

from abc import ABC, abstractmethod

from analyzer.content_analyzer import AnalysisResult


class StorageBackend(ABC):
    """모니터링 저장소 인터페이스"""

    # ─── 게시글 ─────────────────────────────────────────────────

    @abstractmethod
    def is_post_known(self, link: str) -> bool:
        """이미 수집된 게시글인지 확인"""

    @abstractmethod
    def save_post(self, post) -> bool:
        """게시글 저장. 이미 존재하면 False 반환."""

    # ─── 탐지 결과 ──────────────────────────────────────────────

    @abstractmethod
    def save_detection(self, result: AnalysisResult):
        """분석 결과 저장"""

    # ─── 알림 이력 ──────────────────────────────────────────────

    @abstractmethod
    def save_notification(self, recipient: str, subject: str, post_count: int, status: str):
        """알림 발송 이력 저장"""

    # ─── 조회 ───────────────────────────────────────────────────

    @abstractmethod
    def get_recent_detections(self, limit: int = 50) -> list:
        """최근 부적절 탐지 결과 조회"""

    @abstractmethod
    def get_known_links(self, days: int = 30) -> set[str]:
        """최근 N일 이내 수집된 게시글 링크 목록"""

    @abstractmethod
    def get_stats(self) -> dict:
        """누적 통계 — {total_posts, total_detections, total_notifications}"""

    # ─── 관리 페이지용 ──────────────────────────────────────────

    @abstractmethod
    def get_severity_counts(self) -> dict:
        """심각도별 탐지 카운트 — {severity: count}"""

    @abstractmethod
    def get_daily_counts(self, days: int = 7) -> list[dict]:
        """최근 N일 일별 탐지 수 — [{day, cnt}]"""

    @abstractmethod
    def get_source_counts(self) -> dict:
        """출처별 탐지 카운트 — {source: count}"""

    @abstractmethod
    def get_detections_page(
        self,
        page: int = 1,
        per_page: int = 20,
        severity: str = "",
        source: str = "",
        keyword: str = "",
    ) -> tuple[int, list[dict]]:
        """필터/페이지네이션 탐지 목록 — (total, items)"""

    @abstractmethod
    def get_detection_detail(self, detection_id) -> dict | None:
        """탐지 상세 정보 — 없으면 None"""

    @abstractmethod
    def get_daily_summary(self, date: str = "") -> dict:
        """일일 요약 통계"""

    @abstractmethod
    def get_notification_history(self, limit: int = 50) -> list[dict]:
        """알림 발송 이력"""
