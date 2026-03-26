"""
Azure Table Storage 저장소

Azure Functions (serverless) 환경에서 사용하는 영구 저장소.
- posts / detections / notifications 3개 테이블
- PartitionKey: YYYY-MM (월별 파티션)
- RowKey: link SHA256 해시(posts) / UUID(detections, notifications)
- detections에 post 정보를 비정규화하여 JOIN 없이 조회 가능
"""

import hashlib
import json
import logging
import uuid
from collections import Counter
from datetime import datetime, timedelta

from azure.data.tables import TableServiceClient

from analyzer.content_analyzer import AnalysisResult
from storage.base import StorageBackend

logger = logging.getLogger(__name__)


def _month_pk(iso_dt: str | None = None) -> str:
    """ISO datetime 문자열에서 YYYY-MM 파티션키 추출"""
    if iso_dt:
        return iso_dt[:7]
    return datetime.now().strftime("%Y-%m")


def _link_hash(link: str) -> str:
    """link → SHA256 해시 (RowKey용)"""
    return hashlib.sha256(link.encode()).hexdigest()


class AzureTableStorage(StorageBackend):
    """Azure Table Storage 기반 저장소 구현체"""

    def __init__(self, connection_string: str):
        self._service = TableServiceClient.from_connection_string(connection_string)
        self._posts = self._service.create_table_if_not_exists("posts")
        self._detections = self._service.create_table_if_not_exists("detections")
        self._notifications = self._service.create_table_if_not_exists("notifications")

    # ─── 게시글 ─────────────────────────────────────────────────

    def is_post_known(self, link: str) -> bool:
        try:
            # link hash로 모든 파티션을 검색하는 대신 필터 쿼리 사용
            entities = self._posts.query_entities(
                query_filter=f"RowKey eq '{_link_hash(link)}'",
                select=["RowKey"],
                results_per_page=1,
            )
            return next(iter(entities), None) is not None
        except Exception:
            return False

    def save_post(self, post) -> bool:
        row_key = _link_hash(post.link)
        pk = _month_pk(post.collected_at)

        # 중복 체크
        try:
            self._posts.get_entity(partition_key=pk, row_key=row_key)
            return False  # 이미 존재
        except Exception:
            pass

        # is_post_known 으로도 다른 파티션 중복 체크
        if self.is_post_known(post.link):
            return False

        entity = {
            "PartitionKey": pk,
            "RowKey": row_key,
            "link": post.link,
            "source": post.source or "",
            "title": post.title or "",
            "description": post.description or "",
            "blogger_name": post.blogger_name or "",
            "cafe_name": post.cafe_name or "",
            "post_date": post.post_date or "",
            "keyword": post.keyword or "",
            "collected_at": post.collected_at or datetime.now().isoformat(),
        }
        try:
            self._posts.create_entity(entity)
            return True
        except Exception:
            return False

    # ─── 탐지 결과 ──────────────────────────────────────────────

    def save_detection(self, result: AnalysisResult):
        now = datetime.now().isoformat()
        entity = {
            "PartitionKey": _month_pk(now),
            "RowKey": str(uuid.uuid4()),
            "post_link": result.post.link,
            "is_inappropriate": result.is_inappropriate,
            "confidence": result.confidence,
            "categories": json.dumps(result.categories, ensure_ascii=False),
            "matched_keywords": json.dumps(result.matched_keywords, ensure_ascii=False),
            "ai_reason": result.ai_reason or "",
            "severity": result.severity or "",
            "detected_at": now,
            # 비정규화: post 정보 포함 (JOIN 대체)
            "post_title": result.post.title or "",
            "post_source": result.post.source or "",
            "post_blogger_name": result.post.blogger_name or "",
            "post_cafe_name": result.post.cafe_name or "",
            "post_date": result.post.post_date or "",
            "post_description": result.post.description or "",
            "post_keyword": result.post.keyword or "",
            "post_collected_at": result.post.collected_at or "",
        }
        self._detections.create_entity(entity)

    # ─── 알림 이력 ──────────────────────────────────────────────

    def save_notification(self, recipient: str, subject: str, post_count: int, status: str):
        now = datetime.now().isoformat()
        entity = {
            "PartitionKey": _month_pk(now),
            "RowKey": str(uuid.uuid4()),
            "sent_at": now,
            "recipient": recipient,
            "subject": subject,
            "post_count": post_count,
            "status": status,
        }
        self._notifications.create_entity(entity)

    # ─── 조회 헬퍼 ──────────────────────────────────────────────

    def _query_all(self, table, query_filter: str = "", select=None) -> list[dict]:
        """테이블에서 조건에 맞는 모든 엔티티를 list[dict]로 반환"""
        kwargs = {}
        if query_filter:
            kwargs["query_filter"] = query_filter
        if select:
            kwargs["select"] = select
        return [dict(e) for e in table.query_entities(**kwargs)]

    def _recent_partition_keys(self, days: int) -> list[str]:
        """최근 N일을 포함하는 YYYY-MM 파티션키 목록"""
        now = datetime.now()
        keys = set()
        for d in range(days + 1):
            dt = now - timedelta(days=d)
            keys.add(dt.strftime("%Y-%m"))
        return sorted(keys)

    # ─── 조회 ───────────────────────────────────────────────────

    def get_recent_detections(self, limit: int = 50) -> list[dict]:
        pks = self._recent_partition_keys(90)
        results = []
        for pk in reversed(pks):  # 최신 파티션 먼저
            entities = self._query_all(
                self._detections,
                query_filter=f"PartitionKey eq '{pk}' and is_inappropriate eq true",
            )
            results.extend(entities)
            if len(results) >= limit:
                break

        # detected_at 기준 내림차순 정렬 후 limit
        results.sort(key=lambda x: x.get("detected_at", ""), reverse=True)
        items = results[:limit]

        # 필드명을 SQLite 출력과 맞춤
        for item in items:
            item.setdefault("title", item.get("post_title", ""))
            item.setdefault("source", item.get("post_source", ""))
            item.setdefault("link", item.get("post_link", ""))
            item.setdefault("blogger_name", item.get("post_blogger_name", ""))
            item.setdefault("cafe_name", item.get("post_cafe_name", ""))
        return items

    def get_known_links(self, days: int = 30) -> set[str]:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        pks = self._recent_partition_keys(days)
        links = set()
        for pk in pks:
            entities = self._query_all(
                self._posts,
                query_filter=f"PartitionKey eq '{pk}' and collected_at gt '{cutoff}'",
                select=["link"],
            )
            links.update(e["link"] for e in entities)
        return links

    def get_stats(self) -> dict:
        total_posts = len(self._query_all(self._posts, select=["RowKey"]))
        detections = self._query_all(
            self._detections,
            query_filter="is_inappropriate eq true",
            select=["RowKey"],
        )
        total_notifications = len(self._query_all(self._notifications, select=["RowKey"]))
        return {
            "total_posts": total_posts,
            "total_detections": len(detections),
            "total_notifications": total_notifications,
        }

    # ─── 관리 페이지용 ──────────────────────────────────────────

    def get_severity_counts(self) -> dict:
        entities = self._query_all(
            self._detections,
            query_filter="is_inappropriate eq true",
            select=["severity"],
        )
        return dict(Counter(e.get("severity", "") for e in entities))

    def get_daily_counts(self, days: int = 7) -> list[dict]:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        pks = self._recent_partition_keys(days)
        entities = []
        for pk in pks:
            entities.extend(self._query_all(
                self._detections,
                query_filter=(
                    f"PartitionKey eq '{pk}' and is_inappropriate eq true"
                    f" and detected_at gt '{cutoff}'"
                ),
                select=["detected_at"],
            ))
        counter: dict[str, int] = {}
        for e in entities:
            day = e.get("detected_at", "")[:10]
            counter[day] = counter.get(day, 0) + 1
        result = [{"day": d, "cnt": c} for d, c in counter.items()]
        result.sort(key=lambda x: x["day"], reverse=True)
        return result[:days]

    def get_source_counts(self) -> dict:
        entities = self._query_all(
            self._detections,
            query_filter="is_inappropriate eq true",
            select=["post_source"],
        )
        return dict(Counter(e.get("post_source", "") for e in entities))

    def get_detections_page(
        self,
        page: int = 1,
        per_page: int = 20,
        severity: str = "",
        source: str = "",
        keyword: str = "",
    ) -> tuple[int, list[dict]]:
        # 기본 필터
        filters = ["is_inappropriate eq true"]
        if severity:
            filters.append(f"severity eq '{severity}'")
        if source:
            filters.append(f"post_source eq '{source}'")
        query_filter = " and ".join(filters)

        entities = self._query_all(self._detections, query_filter=query_filter)

        # keyword 필터는 클라이언트 사이드 (Table Storage에 LIKE 없음)
        if keyword:
            kw = keyword.lower()
            entities = [
                e for e in entities
                if kw in (e.get("post_title", "") or "").lower()
                or kw in (e.get("post_description", "") or "").lower()
            ]

        # 정렬
        entities.sort(key=lambda x: x.get("detected_at", ""), reverse=True)
        total = len(entities)

        # 페이지네이션
        offset = (page - 1) * per_page
        page_items = entities[offset:offset + per_page]

        # 필드명 정규화 (SQLite 출력과 호환)
        items = []
        for e in page_items:
            item = {
                "id": e.get("RowKey", ""),
                "post_link": e.get("post_link", ""),
                "confidence": e.get("confidence", 0),
                "categories": e.get("categories", "[]"),
                "matched_keywords": e.get("matched_keywords", "[]"),
                "ai_reason": e.get("ai_reason", ""),
                "severity": e.get("severity", ""),
                "detected_at": e.get("detected_at", ""),
                "title": e.get("post_title", ""),
                "source": e.get("post_source", ""),
                "blogger_name": e.get("post_blogger_name", ""),
                "cafe_name": e.get("post_cafe_name", ""),
                "post_date": e.get("post_date", ""),
            }
            # JSON 문자열 → 리스트 파싱
            for key in ("categories", "matched_keywords"):
                if isinstance(item[key], str):
                    try:
                        item[key] = json.loads(item[key])
                    except (json.JSONDecodeError, TypeError):
                        item[key] = []
            items.append(item)

        return total, items

    def get_detection_detail(self, detection_id) -> dict | None:
        detection_id = str(detection_id)
        # RowKey(UUID)로 검색 — 파티션 모름이므로 필터 쿼리
        entities = self._query_all(
            self._detections,
            query_filter=f"RowKey eq '{detection_id}'",
        )
        if not entities:
            return None
        e = entities[0]
        item = {
            "id": e.get("RowKey", ""),
            "post_link": e.get("post_link", ""),
            "is_inappropriate": e.get("is_inappropriate", False),
            "confidence": e.get("confidence", 0),
            "categories": e.get("categories", "[]"),
            "matched_keywords": e.get("matched_keywords", "[]"),
            "ai_reason": e.get("ai_reason", ""),
            "severity": e.get("severity", ""),
            "detected_at": e.get("detected_at", ""),
            "title": e.get("post_title", ""),
            "source": e.get("post_source", ""),
            "blogger_name": e.get("post_blogger_name", ""),
            "cafe_name": e.get("post_cafe_name", ""),
            "post_date": e.get("post_date", ""),
            "description": e.get("post_description", ""),
            "keyword": e.get("post_keyword", ""),
            "collected_at": e.get("post_collected_at", ""),
        }
        for key in ("categories", "matched_keywords"):
            if isinstance(item[key], str):
                try:
                    item[key] = json.loads(item[key])
                except (json.JSONDecodeError, TypeError):
                    item[key] = []
        return item

    def get_daily_summary(self, date: str = "") -> dict:
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        from datetime import date as date_type
        target = date_type.fromisoformat(date)
        week_start = (target - timedelta(days=target.weekday())).isoformat()

        # 전체 누적
        total_posts = len(self._query_all(self._posts, select=["RowKey"]))
        total_detections = len(self._query_all(
            self._detections,
            query_filter="is_inappropriate eq true",
            select=["RowKey"],
        ))

        # 금일 posts
        pk = date[:7]
        today_posts_entities = self._query_all(
            self._posts,
            query_filter=(
                f"PartitionKey eq '{pk}'"
                f" and collected_at ge '{date}T00:00:00'"
                f" and collected_at lt '{date}T23:59:60'"
            ),
            select=["RowKey", "source"],
        )
        today_posts = len(today_posts_entities)

        # 금일 detections
        today_det_entities = self._query_all(
            self._detections,
            query_filter=(
                f"PartitionKey eq '{pk}' and is_inappropriate eq true"
                f" and detected_at ge '{date}T00:00:00'"
                f" and detected_at lt '{date}T23:59:60'"
            ),
        )
        today_detections = len(today_det_entities)

        # 이번주
        week_pks = set()
        for d in range(7):
            dt = target - timedelta(days=d)
            week_pks.add(dt.strftime("%Y-%m"))
        week_posts = 0
        week_detections = 0
        for wpk in week_pks:
            week_posts += len(self._query_all(
                self._posts,
                query_filter=f"PartitionKey eq '{wpk}' and collected_at ge '{week_start}'",
                select=["RowKey"],
            ))
            week_detections += len(self._query_all(
                self._detections,
                query_filter=(
                    f"PartitionKey eq '{wpk}' and is_inappropriate eq true"
                    f" and detected_at ge '{week_start}'"
                ),
                select=["RowKey"],
            ))

        # severity counts & by_source (금일)
        severity_counts: dict[str, int] = {}
        for e in today_det_entities:
            sev = e.get("severity", "")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        by_source: dict[str, int] = {}
        for e in today_posts_entities:
            src = e.get("source", "")
            by_source[src] = by_source.get(src, 0) + 1

        # 금일 탐지 상세
        detections = []
        for e in sorted(today_det_entities, key=lambda x: x.get("confidence", 0), reverse=True):
            det = {
                "severity": e.get("severity", ""),
                "confidence": e.get("confidence", 0),
                "categories": e.get("categories", "[]"),
                "matched_keywords": e.get("matched_keywords", "[]"),
                "ai_reason": e.get("ai_reason", ""),
                "detected_at": e.get("detected_at", ""),
                "title": e.get("post_title", ""),
                "source": e.get("post_source", ""),
                "link": e.get("post_link", ""),
                "blogger_name": e.get("post_blogger_name", ""),
                "cafe_name": e.get("post_cafe_name", ""),
                "post_date": e.get("post_date", ""),
            }
            detections.append(det)

        return {
            "date": date,
            "total_posts": total_posts,
            "total_detections": total_detections,
            "today_posts": today_posts,
            "today_detections": today_detections,
            "week_posts": week_posts,
            "week_detections": week_detections,
            "severity_counts": severity_counts,
            "by_source": by_source,
            "detections": detections,
        }

    def get_notification_history(self, limit: int = 50) -> list[dict]:
        entities = self._query_all(self._notifications)
        entities.sort(key=lambda x: x.get("sent_at", ""), reverse=True)
        items = []
        for e in entities[:limit]:
            items.append({
                "id": e.get("RowKey", ""),
                "sent_at": e.get("sent_at", ""),
                "recipient": e.get("recipient", ""),
                "subject": e.get("subject", ""),
                "post_count": e.get("post_count", 0),
                "status": e.get("status", ""),
            })
        return items
