"""
저장소 패키지

STORAGE_BACKEND 환경변수로 백엔드 선택:
  - "sqlite" (기본): 로컬 SQLite
  - "azure": Azure Table Storage
"""

from storage.base import StorageBackend


def create_storage() -> StorageBackend:
    """환경변수 기반 저장소 백엔드 팩토리"""
    from config import STORAGE_BACKEND

    if STORAGE_BACKEND == "azure":
        from config import AZURE_STORAGE_CONNECTION_STRING
        from storage.azure_storage import AzureTableStorage
        return AzureTableStorage(AZURE_STORAGE_CONNECTION_STRING)

    # 기본: SQLite
    from storage.database import SqliteStorage
    return SqliteStorage()
