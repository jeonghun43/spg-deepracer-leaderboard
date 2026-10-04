"""평가 서버 자동 켜기 + 디스코드 알림 — 웹 서버의 autopilot 컨테이너 (worker-auto-start-stop-plan.md §1).

실행: `python -m app.autopilot` (docker-compose.prod.yml의 autopilot 서비스)

1분마다:
  상태 표 읽기 → EC2 상태 조회 → 상태 저장 → 판단(app/autopilot_logic.py) → 켜기 요청 → 쌓인 알림 발송

역할 분담 — **켜기는 밖에서, 끄기는 안에서.** 꺼져 있는 서버는 스스로 켤 수 없으니 늘 켜져 있는 웹
서버가 켠다. 끄기는 평가 서버 안의 worker/autostop.py가 한다. 그래서 이 프로세스에는 끄기 권한이 없다.

AWS 키와 디스코드 웹훅은 이 컨테이너에만 넘긴다. web 컨테이너가 털려도 환경변수에 키가 없다.
"""

from __future__ import annotations

import datetime as dt
import logging
import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import autopilot_aws, autopilot_notify
from app.autopilot_logic import (
    MISCONFIGURED,
    START_FAILED,
    START_REQUESTED,
    Limits,
    Observation,
    decide,
    shutdown_behavior_problem,
    start_failed_due,
    worker_id_from_private_dns,
)
from app.config import settings
from app.db import SessionLocal
from app.models import AutopilotEvent, AutopilotState, Submission, SubmissionStatus
from app.worker_status import get_worker_last_seen

logger = logging.getLogger("autopilot")

SOURCE = "web"
# 한 주기에 보내는 최대 알림 수. 디스코드 웹훅의 속도 제한(초당 수 건)에 걸리지 않게 한다.
MAX_NOTIFICATIONS_PER_CYCLE = 10
# 이보다 오래된 미발송 알림은 보내지 않는다. 웹훅을 한참 뒤에 설정했을 때 지난 일이 한꺼번에
# 쏟아져 지금 중요한 알림이 묻히는 것을 막는다. 지난 기록은 관리자 화면에서 볼 수 있다.
NOTIFY_MAX_AGE = dt.timedelta(hours=24)


def now_utc() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


class DbStore:
    """run_cycle이 쓰는 DB 조회·기록. 테스트는 같은 메서드를 가진 가짜로 바꾼다(tests/test_autopilot_loop.py)."""

    def __init__(self, db: Session):
        self.db = db

    def get_state(self) -> AutopilotState:
        state = self.db.get(AutopilotState, 1)
        if state is None:
            # 마이그레이션이 행을 넣어 두지만, 누가 지웠어도 꺼짐 상태로 다시 만든다.
            state = AutopilotState(id=1, enabled=False)
            self.db.add(state)
            self.db.flush()
        return state

    def queued_count(self) -> int:
        return self.db.execute(
            select(func.count()).select_from(Submission).where(Submission.status == SubmissionStatus.QUEUED)
        ).scalar_one()

    def worker_last_seen(self, worker_id: str) -> dt.datetime | None:
        return get_worker_last_seen(self.db, worker_id)

    def last_event_times(self) -> dict[str, dt.datetime]:
        rows = self.db.execute(
            select(AutopilotEvent.kind, func.max(AutopilotEvent.created_at)).group_by(AutopilotEvent.kind)
        ).all()
        return {kind: at for kind, at in rows}

    def add_event(self, kind: str, message: str, now: dt.datetime) -> None:
        logger.info("이벤트 %s: %s", kind, message)
        self.db.add(AutopilotEvent(kind=kind, source=SOURCE, message=message, created_at=now))

    def unsent_events(self, since: dt.datetime, limit: int) -> list[AutopilotEvent]:
        return list(
            self.db.execute(
                select(AutopilotEvent)
                .where(AutopilotEvent.notified_at.is_(None), AutopilotEvent.created_at >= since)
                .order_by(AutopilotEvent.created_at.asc(), AutopilotEvent.id.asc())
                .limit(limit)
            ).scalars()
        )

    def commit(self) -> None:
        self.db.commit()


def limits_from_settings() -> Limits:
    return Limits(
        heartbeat_stale_minutes=settings.worker_heartbeat_stale_minutes,
        start_timeout_minutes=settings.autopilot_start_timeout_minutes,
        manual_attention_minutes=settings.autopilot_manual_attention_minutes,
        start_failed_repeat_minutes=settings.autopilot_start_failed_repeat_minutes,
    )


def _error_code(exc: Exception) -> str:
    """AWS 오류에서 코드만 꺼낸다. 오류 문구에는 계정 ID·사용자 ARN이 들어 있어 디스코드에 그대로 싣지 않는다."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if code:
            return str(code)
    return type(exc).__name__


def run_cycle(store, aws, send, now: dt.datetime) -> None:
    """한 주기. 예외는 호출하는 쪽(main)이 잡는다."""
    instance_id = settings.autopilot_instance_id
    state = store.get_state()

    instance_state: str | None
    try:
        instance_state, private_dns = aws.describe(instance_id)
        state.instance_state = instance_state
        state.instance_state_at = now
        worker_id = worker_id_from_private_dns(private_dns)
        if worker_id:
            state.target_worker_id = worker_id
    except Exception as exc:  # noqa: BLE001 - AWS가 잠깐 안 돼도 루프는 계속 돈다
        logger.warning("EC2 상태 조회 실패: %s", _error_code(exc))
        instance_state = None

    queued = store.queued_count()
    last_seen = store.worker_last_seen(state.target_worker_id) if state.target_worker_id else None
    last_events = store.last_event_times()

    decision = decide(
        Observation(
            now=now,
            enabled=bool(state.enabled),
            queued_count=queued,
            worker_last_seen=last_seen,
            instance_state=instance_state,
            last_start_requested_at=state.last_start_requested_at,
            attention_since=state.attention_since,
            last_event_at=last_events,
        ),
        limits_from_settings(),
    )
    state.attention_since = decision.attention_since

    if decision.start:
        try:
            aws.start(instance_id)
        except Exception as exc:  # noqa: BLE001 - 권한·용량 부족 등. 다음 주기에 다시 시도한다
            code = _error_code(exc)
            logger.warning("켜기 요청 실패: %s", code)
            if start_failed_due(now, last_events.get(START_FAILED), settings.autopilot_start_failed_repeat_minutes):
                store.add_event(
                    START_FAILED,
                    f"켜기 요청이 실패했습니다({code}). 대기 {queued}건. 콘솔에서 직접 켜고 원인을 확인하세요.",
                    now,
                )
        else:
            state.last_start_requested_at = now
            store.add_event(START_REQUESTED, f"대기 제출 {queued}건 — 평가 서버를 켭니다.", now)

    for kind, message in decision.events:
        store.add_event(kind, message, now)
    store.commit()

    deliver_pending(store, send, settings.discord_webhook_url, now)


def deliver_pending(store, send, webhook_url: str, now: dt.datetime) -> int:
    """쌓인 알림을 보낸다. 보낸 건수를 돌려준다.

    웹훅이 비어 있으면 보내지 않고 notified_at도 채우지 않는다(나중에 설정하면 24시간 안의 것은 보낸다).
    한 건이라도 실패하면 이번 주기는 멈춘다 — 디스코드가 안 되는 중이면 나머지도 실패하고, 순서도 지킨다.
    """
    if not webhook_url:
        return 0
    sent = 0
    for event in store.unsent_events(now - NOTIFY_MAX_AGE, MAX_NOTIFICATIONS_PER_CYCLE):
        content = autopilot_notify.format_message(event.kind, event.message, event.source, event.created_at)
        if not send(webhook_url, content):
            break
        event.notified_at = now
        store.commit()
        sent += 1
    return sent


def check_shutdown_behavior(store, aws, now: dt.datetime) -> None:
    """"종료 시 동작"이 stop이 아니면 경고를 남긴다(plan.md §1.4). 기동할 때와 하루 한 번 부른다.

    terminate로 바뀌어 있으면 평가 서버가 스스로 끌 때 **디스크째 사라진다**(2026-10-01 사고와 같은 결과).
    """
    try:
        value = aws.shutdown_behavior(settings.autopilot_instance_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("종료 시 동작 확인 실패: %s", _error_code(exc))
        value = None
    problem = shutdown_behavior_problem(value)
    if problem:
        store.add_event(MISCONFIGURED, problem, now)
        store.commit()
    else:
        logger.info("종료 시 동작 확인: stop (정상)")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if not settings.autopilot_instance_id:
        # 종료하면 restart: unless-stopped 때문에 계속 다시 뜨며 로그만 쌓인다. 조용히 쉰다.
        logger.warning("AUTOPILOT_INSTANCE_ID가 설정되지 않았습니다. 자동 켜기·알림을 하지 않습니다.")
        while True:
            time.sleep(3600)

    logger.info(
        "autopilot 시작 (instance=%s, region=%s, 주기=%ss, 디스코드=%s)",
        settings.autopilot_instance_id,
        settings.autopilot_region,
        settings.autopilot_poll_seconds,
        "설정됨" if settings.discord_webhook_url else "없음 — 알림은 로그로만",
    )
    next_shutdown_check = 0.0
    failures = 0
    while True:
        db = SessionLocal()
        try:
            store = DbStore(db)
            if time.monotonic() >= next_shutdown_check:
                check_shutdown_behavior(store, autopilot_aws, now_utc())
                next_shutdown_check = time.monotonic() + settings.autopilot_shutdown_check_hours * 3600
            run_cycle(store, autopilot_aws, autopilot_notify.send, now_utc())
            if failures:
                logger.info("주기 정상화 (%s회 연속 실패 뒤)", failures)
            failures = 0
        except Exception:  # noqa: BLE001 - 한 주기가 실패해도 루프는 죽지 않는다
            db.rollback()
            failures += 1
            # DB 접속이 계속 안 되면 같은 오류가 매분 쌓인다. 연속 횟수를 붙여 상황을 알 수 있게 한다.
            logger.exception("주기 실패 (%s회 연속)", failures)
        finally:
            db.close()
        time.sleep(settings.autopilot_poll_seconds)


if __name__ == "__main__":
    main()
