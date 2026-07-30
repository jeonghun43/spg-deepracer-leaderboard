"""평가 워커가 살아있는지 판단한다 (cloud-migration.md §5).

워커는 운영자 노트북에서 돌기 때문에 노트북이 꺼지면 평가만 멈춘다. 이때 제출은 계속 접수되지만
결과가 나오지 않으므로, 참가자에게 "고장"이 아니라 "대기 중"임을 알려줘야 한다.
"""

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import WorkerHeartbeat


def get_worker_status(db: Session) -> dict:
    """가장 최근 하트비트를 기준으로 평가 서버 상태를 요약한다.

    반환: {"online": bool, "last_seen_at": datetime | None, "minutes_ago": int | None}
    하트비트가 한 번도 없으면(기능 도입 직후·워커 미기동) online=False로 본다.
    """
    last_seen = db.execute(select(func.max(WorkerHeartbeat.last_seen_at))).scalar_one_or_none()
    if last_seen is None:
        return {"online": False, "last_seen_at": None, "minutes_ago": None}

    now = dt.datetime.now(tz=dt.timezone.utc)
    elapsed = now - last_seen
    minutes_ago = max(int(elapsed.total_seconds() // 60), 0)
    online = elapsed <= dt.timedelta(minutes=settings.worker_heartbeat_stale_minutes)
    return {"online": online, "last_seen_at": last_seen, "minutes_ago": minutes_ago}


def touch_heartbeat(db: Session, worker_id: str) -> None:
    """워커가 자기 생존 신호를 남긴다. 폴링 루프마다 호출된다."""
    heartbeat = db.get(WorkerHeartbeat, worker_id)
    now = dt.datetime.now(tz=dt.timezone.utc)
    if heartbeat is None:
        db.add(WorkerHeartbeat(worker_id=worker_id, last_seen_at=now))
    else:
        heartbeat.last_seen_at = now
    db.commit()
