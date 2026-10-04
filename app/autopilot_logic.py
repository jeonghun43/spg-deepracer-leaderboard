"""평가 서버 자동 켜기 — 판단 로직 (worker-auto-start-stop-plan.md §1.2).

DB·AWS·디스코드·시계를 모두 인자로 받는 순수 함수만 둔다. 판단이 틀리면 서버가 엉뚱하게
켜지거나(요금) 켜지지 않는다(평가 지연). 그래서 표의 각 줄을 실제 서비스 없이 테스트로 고정한다
(tests/test_autopilot_logic.py). 실행은 app/autopilot.py가 맡는다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

# 이벤트 종류. autostop(평가 서버)이 남기는 idle_stop·ssh_blocking도 같은 표를 쓴다.
START_REQUESTED = "start_requested"
START_FAILED = "start_failed"
START_TIMEOUT = "start_timeout"
IDLE_STOP = "idle_stop"
SSH_BLOCKING = "ssh_blocking"
MANUAL_ATTENTION = "manual_attention"
MISCONFIGURED = "misconfigured"
SWITCH_CHANGED = "switch_changed"

# 디스코드·관리자 화면에 보여 줄 이름
EVENT_LABELS = {
    START_REQUESTED: "평가 서버 켜기 요청",
    START_FAILED: "⚠️ 평가 서버 켜기 실패",
    START_TIMEOUT: "⚠️ 평가 서버가 켜지지 않음",
    IDLE_STOP: "평가 서버 자동 중지",
    SSH_BLOCKING: "SSH 접속 때문에 끄지 못하는 중",
    MANUAL_ATTENTION: "⚠️ 자동화 꺼짐 · 대기 제출 있음",
    MISCONFIGURED: "🚨 평가 서버 설정 위험",
    SWITCH_CHANGED: "자동화 스위치 변경",
}

# 켜기 요청을 해도 되는 상태. pending/running은 이미 켜지는 중이고, stopping은 다 꺼진 뒤 다음 주기에 켠다.
STARTABLE_STATES = ("stopped",)
BOOTING_STATES = ("pending", "running")
# 인스턴스가 사라졌거나 사라지는 중이다. 켤 수 없으니 사람이 봐야 한다.
GONE_STATES = ("shutting-down", "terminated")


@dataclass(frozen=True)
class Observation:
    """한 주기에 autopilot이 본 것. 모든 시각은 timezone이 있는 UTC다."""

    now: dt.datetime
    enabled: bool
    queued_count: int
    worker_last_seen: dt.datetime | None
    # None이면 AWS 조회에 실패했다는 뜻이다.
    instance_state: str | None
    last_start_requested_at: dt.datetime | None
    attention_since: dt.datetime | None
    # 종류별 가장 최근 이벤트 시각 (중복 방지용)
    last_event_at: dict[str, dt.datetime] = field(default_factory=dict)


@dataclass(frozen=True)
class Limits:
    heartbeat_stale_minutes: int = 3
    start_timeout_minutes: int = 10
    manual_attention_minutes: int = 10
    start_failed_repeat_minutes: int = 30


@dataclass
class Decision:
    start: bool = False
    events: list[tuple[str, str]] = field(default_factory=list)
    # 상태 표에 저장할 새 값
    attention_since: dt.datetime | None = None


def worker_alive(now: dt.datetime, last_seen: dt.datetime | None, stale_minutes: int) -> bool:
    """참가자 화면(app/worker_status.get_worker_status)과 같은 기준이다. 정확히 3분이면 아직 살아 있다."""
    if last_seen is None:
        return False
    return now - last_seen <= dt.timedelta(minutes=stale_minutes)


def _happened_since(last_event_at: dict[str, dt.datetime], kind: str, since: dt.datetime | None) -> bool:
    """kind 이벤트가 since 이후(같은 시각 포함)에 이미 있었나."""
    last = last_event_at.get(kind)
    if last is None:
        return False
    return since is None or last >= since


def start_failed_due(now: dt.datetime, last_failed_at: dt.datetime | None, repeat_minutes: int) -> bool:
    """같은 실패 알림은 repeat_minutes에 한 번만 보낸다. 켜기 시도 자체는 매 주기 계속한다."""
    return last_failed_at is None or now - last_failed_at >= dt.timedelta(minutes=repeat_minutes)


def decide(obs: Observation, limits: Limits = Limits()) -> Decision:
    """plan.md §1.2의 판단 표를 그대로 옮긴다.

    스위치 켜짐:
      대기 있음 · 워커 죽음 · stopped          → 켜기 요청 (이벤트는 실행 결과를 보고 app/autopilot.py가 남긴다)
      대기 있음 · 워커 죽음 · pending/running  → 기다림. 10분 지나면 start_timeout (사건당 1번)
      대기 있음 · 워커 죽음 · stopping         → 기다림. 다 꺼지면 다음 주기에 켠다
      워커 살아 있음 / 대기 없음               → 할 일 없음 (끄기는 평가 서버가 한다)
    스위치 꺼짐:
      켜지도 끄지도 않는다. 대기 있음 · 워커 죽음이 10분 넘으면 manual_attention (사건당 1번)
    """
    now = obs.now
    alive = worker_alive(now, obs.worker_last_seen, limits.heartbeat_stale_minutes)
    needs_worker = obs.queued_count > 0 and not alive

    if not needs_worker:
        # 사건이 끝났다. 다음에 같은 상황이 오면 다시 알릴 수 있는 상태로 돌아간다.
        return Decision(attention_since=None)

    attention_since = obs.attention_since or now
    decision = Decision(attention_since=attention_since)
    waited = now - attention_since

    if not obs.enabled:
        if waited >= dt.timedelta(minutes=limits.manual_attention_minutes) and not _happened_since(
            obs.last_event_at, MANUAL_ATTENTION, attention_since
        ):
            decision.events.append(
                (
                    MANUAL_ATTENTION,
                    f"자동화가 꺼져 있는데 대기 제출 {obs.queued_count}건이 {int(waited.total_seconds() // 60)}분째 "
                    "처리되지 않고 있습니다. 평가 서버를 직접 켜거나 자동화를 켜세요.",
                )
            )
        return decision

    state = obs.instance_state
    if state is None:
        # AWS 조회 실패 — 키·권한·네트워크 문제다. 켤 수 없으니 켜기 실패와 같이 다룬다.
        if start_failed_due(now, obs.last_event_at.get(START_FAILED), limits.start_failed_repeat_minutes):
            decision.events.append(
                (START_FAILED, f"EC2 상태를 조회하지 못했습니다(대기 {obs.queued_count}건). 로그를 확인하세요.")
            )
        return decision

    if state in STARTABLE_STATES:
        decision.start = True
        return decision

    if state in GONE_STATES:
        if start_failed_due(now, obs.last_event_at.get(START_FAILED), limits.start_failed_repeat_minutes):
            decision.events.append(
                (START_FAILED, f"대상 인스턴스가 {state} 상태라 켤 수 없습니다(대기 {obs.queued_count}건).")
            )
        return decision

    if state in BOOTING_STATES:
        # 이번 사건의 기준 시각: 이번 사건 안에서 켜기 요청을 했으면 그 시각, 아니면 사건이 시작된 시각.
        # 후자는 서버가 켜져 있는데 워커만 죽은 경우다(사람이 켰거나, 켜진 뒤 워커가 멈춤).
        # 표에는 "켜기 요청 후 10분"만 있지만, 이 경우도 사람이 봐야 하는 상황이라 같은 알림으로 잡는다.
        reference = attention_since
        if obs.last_start_requested_at is not None and obs.last_start_requested_at >= attention_since:
            reference = obs.last_start_requested_at
        if now - reference >= dt.timedelta(minutes=limits.start_timeout_minutes) and not _happened_since(
            obs.last_event_at, START_TIMEOUT, reference
        ):
            minutes = int((now - reference).total_seconds() // 60)
            decision.events.append(
                (
                    START_TIMEOUT,
                    f"인스턴스는 {state} 상태인데 워커가 {minutes}분째 응답하지 않습니다"
                    f"(대기 {obs.queued_count}건). 서버에 접속해 drfc-worker를 확인하세요.",
                )
            )
        return decision

    # stopping 등: 기다린다.
    return decision


def shutdown_behavior_problem(value: str | None) -> str | None:
    """"종료 시 동작"이 stop이 아니면 경고 문구를 돌려준다 (plan.md §1.4)."""
    if value == "stop":
        return None
    if value is None:
        return "평가 서버의 '종료 시 동작'을 확인하지 못했습니다. IAM 권한(DescribeInstanceAttribute)을 확인하세요."
    return (
        f"평가 서버의 '종료 시 동작'이 '{value}'입니다. 자동 끄기가 서버를 디스크째 지웁니다. "
        "콘솔에서 '중지'로 바꾸기 전까지 자동화를 끄세요."
    )


def worker_id_from_private_dns(private_dns_name: str | None) -> str | None:
    """사설 DNS 이름(ip-172-31-61-59.ap-northeast-2.compute.internal) → 워커 ID(ip-172-31-61-59).

    워커 ID는 평가 서버의 호스트 이름(worker/run.py의 socket.gethostname())이다(plan.md §1.3).
    """
    if not private_dns_name:
        return None
    return private_dns_name.split(".", 1)[0] or None


def status_label(instance_state: str | None, alive: bool) -> str:
    """관리자 화면 한 줄 요약."""
    if alive:
        return "켜짐 · 워커 정상"
    if instance_state == "stopped":
        return "꺼짐(중지됨)"
    if instance_state == "pending":
        return "켜지는 중"
    if instance_state == "running":
        return "켜짐 · 워커 응답 없음"
    if instance_state == "stopping":
        return "꺼지는 중"
    if instance_state in GONE_STATES:
        return f"인스턴스 없음({instance_state})"
    return "알 수 없음(AWS 상태를 아직 받지 못함)"
