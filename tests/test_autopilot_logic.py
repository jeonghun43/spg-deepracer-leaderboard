"""평가 서버 자동 켜기 판단 검증 (worker-auto-start-stop-plan.md §1.2).

판단이 틀리면 서버가 엉뚱하게 켜지거나(요금) 켜지지 않는다(평가 지연). 표의 각 줄을 실제
AWS·DB 없이 고정한다. 지켜야 하는 것:

- 스위치 켜짐: 대기 있음 + 대상 워커 죽음 + stopped일 때만 켠다
- 켜는 중·꺼지는 중에는 다시 켜라고 하지 않는다
- 같은 사건에 같은 알림이 두 번 나가지 않는다(명세서 S5)
- 스위치 꺼짐: 켜지 않는다. 10분 넘게 방치되면 한 번 알린다(plan.md P2)
"""

import datetime as dt

import pytest

from app.autopilot_logic import (
    MANUAL_ATTENTION,
    START_FAILED,
    START_TIMEOUT,
    Limits,
    Observation,
    decide,
    shutdown_behavior_problem,
    start_failed_due,
    status_label,
    worker_alive,
    worker_id_from_private_dns,
)

NOW = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.timezone.utc)
LIMITS = Limits(heartbeat_stale_minutes=3, start_timeout_minutes=10, manual_attention_minutes=10,
                start_failed_repeat_minutes=30)


def ago(**kwargs) -> dt.datetime:
    return NOW - dt.timedelta(**kwargs)


def obs(**overrides) -> Observation:
    """기본값: 스위치 켜짐, 대기 1건, 워커는 1시간 전에 마지막으로 살아 있었음, 인스턴스 중지됨."""
    values = dict(
        now=NOW,
        enabled=True,
        queued_count=1,
        worker_last_seen=ago(hours=1),
        instance_state="stopped",
        last_start_requested_at=None,
        attention_since=None,
        last_event_at={},
    )
    values.update(overrides)
    return Observation(**values)


def kinds(decision) -> list[str]:
    return [kind for kind, _ in decision.events]


# ── 스위치 켜짐 — 표의 각 줄 ─────────────────────────────────────────────


def test_대기_있음_워커_죽음_중지됨이면_켠다():
    d = decide(obs(), LIMITS)
    assert d.start is True
    assert d.attention_since == NOW, "이번 사건의 시작 시각을 기억해야 한다"


@pytest.mark.parametrize("state", ["pending", "running"])
def test_켜지는_중이면_다시_켜지_않는다(state):
    d = decide(obs(instance_state=state, last_start_requested_at=ago(minutes=2), attention_since=ago(minutes=3)), LIMITS)
    assert d.start is False
    assert d.events == []


def test_켜기_요청_10분_뒤에도_워커가_없으면_start_timeout():
    d = decide(
        obs(instance_state="running", last_start_requested_at=ago(minutes=10), attention_since=ago(minutes=11)),
        LIMITS,
    )
    assert kinds(d) == [START_TIMEOUT]
    assert d.start is False


def test_켜기_요청_9분59초면_아직_기다린다():
    d = decide(
        obs(instance_state="running", last_start_requested_at=ago(minutes=9, seconds=59), attention_since=ago(minutes=11)),
        LIMITS,
    )
    assert d.events == []


def test_start_timeout은_켜기_요청_1건당_한_번만():
    base = dict(instance_state="running", last_start_requested_at=ago(minutes=15), attention_since=ago(minutes=16))
    first = decide(obs(**base), LIMITS)
    assert kinds(first) == [START_TIMEOUT]
    again = decide(obs(**base, last_event_at={START_TIMEOUT: ago(minutes=4)}), LIMITS)
    assert again.events == []


def test_새_켜기_요청_뒤에는_start_timeout이_다시_나올_수_있다():
    """지난 사건의 start_timeout이 이번 켜기 요청보다 앞이면 이번 사건은 아직 알리지 않은 것이다."""
    d = decide(
        obs(
            instance_state="running",
            last_start_requested_at=ago(minutes=12),
            attention_since=ago(minutes=13),
            last_event_at={START_TIMEOUT: ago(hours=5)},
        ),
        LIMITS,
    )
    assert kinds(d) == [START_TIMEOUT]


def test_켜기_요청_없이_켜져_있는데_워커만_죽어도_10분이면_알린다():
    """사람이 켰거나 켜진 뒤 워커만 멈춘 경우. 기준은 이번 사건이 시작된 시각이다."""
    d = decide(obs(instance_state="running", last_start_requested_at=None, attention_since=ago(minutes=10)), LIMITS)
    assert kinds(d) == [START_TIMEOUT]
    early = decide(obs(instance_state="running", last_start_requested_at=None, attention_since=ago(minutes=5)), LIMITS)
    assert early.events == []


def test_지난_사건의_오래된_켜기_요청은_기준이_아니다():
    """며칠 전 켜기 요청 시각을 기준으로 삼으면 사건이 시작되자마자 오경보가 난다."""
    d = decide(
        obs(instance_state="running", last_start_requested_at=ago(days=2), attention_since=ago(minutes=1)),
        LIMITS,
    )
    assert d.events == []


def test_꺼지는_중이면_기다린다():
    """다 꺼진 다음 주기에 켠다(plan.md §2.4의 경합)."""
    d = decide(obs(instance_state="stopping"), LIMITS)
    assert d.start is False
    assert d.events == []


def test_워커가_살아_있으면_할_일이_없다():
    d = decide(obs(worker_last_seen=ago(seconds=30), instance_state="running", attention_since=ago(minutes=20)), LIMITS)
    assert d.start is False
    assert d.events == []
    assert d.attention_since is None, "사건이 끝났으니 다음 사건을 위해 비운다"


def test_대기가_없으면_할_일이_없다():
    d = decide(obs(queued_count=0), LIMITS)
    assert d.start is False
    assert d.events == []
    assert d.attention_since is None


def test_AWS_조회_실패면_켜지_않고_start_failed():
    d = decide(obs(instance_state=None), LIMITS)
    assert d.start is False
    assert kinds(d) == [START_FAILED]


def test_AWS_조회_실패_알림도_30분에_한_번():
    assert decide(obs(instance_state=None, last_event_at={START_FAILED: ago(minutes=29)}), LIMITS).events == []
    assert kinds(decide(obs(instance_state=None, last_event_at={START_FAILED: ago(minutes=30)}), LIMITS)) == [START_FAILED]


@pytest.mark.parametrize("state", ["terminated", "shutting-down"])
def test_인스턴스가_사라졌으면_켜지_않고_알린다(state):
    d = decide(obs(instance_state=state), LIMITS)
    assert d.start is False
    assert kinds(d) == [START_FAILED]


def test_start_failed_반복_간격():
    assert start_failed_due(NOW, None, 30) is True
    assert start_failed_due(NOW, ago(minutes=29, seconds=59), 30) is False
    assert start_failed_due(NOW, ago(minutes=30), 30) is True


# ── 하트비트 경계 ───────────────────────────────────────────────────────


def test_하트비트_정확히_3분이면_살아_있다():
    """참가자 화면(get_worker_status)과 같은 기준: 3분 이하면 살아 있음."""
    assert worker_alive(NOW, ago(minutes=3), 3) is True
    assert worker_alive(NOW, ago(minutes=3, seconds=1), 3) is False
    assert worker_alive(NOW, None, 3) is False


def test_하트비트_3분이면_켜지_않는다():
    assert decide(obs(worker_last_seen=ago(minutes=3)), LIMITS).start is False
    assert decide(obs(worker_last_seen=ago(minutes=3, seconds=1)), LIMITS).start is True


def test_워커가_한_번도_뜬_적_없으면_죽은_것으로_본다():
    assert decide(obs(worker_last_seen=None), LIMITS).start is True


# ── 스위치 꺼짐 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("state", ["stopped", "running", "pending", "stopping", None])
def test_스위치가_꺼져_있으면_절대_켜지_않는다(state):
    assert decide(obs(enabled=False, instance_state=state), LIMITS).start is False


def test_스위치_꺼짐_10분_방치면_manual_attention():
    d = decide(obs(enabled=False, attention_since=ago(minutes=10)), LIMITS)
    assert kinds(d) == [MANUAL_ATTENTION]


def test_스위치_꺼짐_9분이면_아직_알리지_않는다():
    assert decide(obs(enabled=False, attention_since=ago(minutes=9, seconds=59)), LIMITS).events == []


def test_스위치_꺼짐_처음_보면_시작_시각만_기억한다():
    d = decide(obs(enabled=False), LIMITS)
    assert d.events == []
    assert d.attention_since == NOW


def test_manual_attention은_사건당_한_번만():
    since = ago(minutes=30)
    d = decide(obs(enabled=False, attention_since=since, last_event_at={MANUAL_ATTENTION: ago(minutes=20)}), LIMITS)
    assert d.events == []


def test_워커가_살아났다가_다시_멈추면_다시_알릴_수_있다():
    # 이번 사건은 15분 전에 시작됐고, 지난번 알림은 그 전(지난 사건)이다.
    d = decide(
        obs(enabled=False, attention_since=ago(minutes=15), last_event_at={MANUAL_ATTENTION: ago(hours=2)}),
        LIMITS,
    )
    assert kinds(d) == [MANUAL_ATTENTION]


def test_스위치_꺼짐이면_start_timeout을_내지_않는다():
    d = decide(
        obs(enabled=False, instance_state="running", last_start_requested_at=ago(minutes=15), attention_since=ago(minutes=5)),
        LIMITS,
    )
    assert START_TIMEOUT not in kinds(d)


# ── 같은 상황을 두 번 넣으면 이벤트가 한 번만 (S5) ─────────────────────────


def test_한_사건을_여러_주기_돌려도_알림은_한_번씩():
    """중지된 서버 → 켜기 → 켜졌지만 워커가 안 뜸 → 20분 동안 1분마다 판단."""
    attention = None
    requested = None
    events: dict[str, dt.datetime] = {}
    emitted = []
    state = "stopped"
    for minute in range(0, 21):
        now = NOW + dt.timedelta(minutes=minute)
        d = decide(
            Observation(
                now=now, enabled=True, queued_count=2, worker_last_seen=None, instance_state=state,
                last_start_requested_at=requested, attention_since=attention, last_event_at=dict(events),
            ),
            LIMITS,
        )
        attention = d.attention_since
        if d.start:
            requested = now
            emitted.append("start")
            state = "pending"
        for kind, _ in d.events:
            events[kind] = now
            emitted.append(kind)
        if state == "pending":
            state = "running"
    assert emitted == ["start", START_TIMEOUT]


# ── 종료 시 동작 점검 (plan.md §1.4) ─────────────────────────────────────


def test_종료_시_동작이_stop이면_문제없다():
    assert shutdown_behavior_problem("stop") is None


def test_종료_시_동작이_terminate면_misconfigured_경고():
    """실제 인스턴스로 시험하지 않는다 — terminate로 바꾸고 끄면 디스크째 사라진다."""
    message = shutdown_behavior_problem("terminate")
    assert message is not None and "terminate" in message


def test_종료_시_동작을_확인하지_못해도_경고():
    assert shutdown_behavior_problem(None) is not None


# ── 기타 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "dns, expected",
    [
        ("ip-172-31-61-59.ap-northeast-2.compute.internal", "ip-172-31-61-59"),
        ("ip-172-31-61-59", "ip-172-31-61-59"),
        ("", None),
        (None, None),
    ],
)
def test_사설_DNS에서_워커_ID를_얻는다(dns, expected):
    assert worker_id_from_private_dns(dns) == expected


@pytest.mark.parametrize(
    "state, alive, expected",
    [
        ("stopped", False, "꺼짐(중지됨)"),
        ("pending", False, "켜지는 중"),
        ("running", True, "켜짐 · 워커 정상"),
        ("running", False, "켜짐 · 워커 응답 없음"),
    ],
)
def test_관리자_화면_상태_문구(state, alive, expected):
    assert status_label(state, alive) == expected
