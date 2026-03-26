"""
병원 콘텐츠 모니터링 시스템 - 진입점
대상: 조정훈유바외과

사용법:
  python main.py              # 스케줄러 모드 (주기적 실행)
  python main.py --once       # 1회만 실행
  python main.py --stats      # 통계 출력
"""

import argparse
import logging
import sys

from dotenv import load_dotenv

from config import LOG_FILE, LOG_LEVEL

load_dotenv()

# ─── 로깅 설정 ──────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="병원 콘텐츠 모니터링 시스템")
    parser.add_argument("--once", action="store_true", help="1회만 실행하고 종료")
    parser.add_argument("--stats", action="store_true", help="누적 통계 출력")
    args = parser.parse_args()

    if args.stats:
        from storage import create_storage
        stats = create_storage().get_stats()
        print("\n=== 모니터링 통계 ===")
        print(f"  총 수집 게시글  : {stats['total_posts']:,}건")
        print(f"  부적절 탐지     : {stats['total_detections']:,}건")
        print(f"  알림 발송 횟수  : {stats['total_notifications']:,}회")
        return

    if args.once:
        from scheduler.monitor_job import MonitorJob
        MonitorJob().run()
    else:
        from scheduler.monitor_job import run_scheduler
        run_scheduler()


if __name__ == "__main__":
    main()
