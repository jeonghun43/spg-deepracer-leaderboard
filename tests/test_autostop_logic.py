"""평가 서버 자동 끄기 판단 검증 (worker-auto-start-stop-plan.md §2.2).

판단이 틀리면 평가 도중에 서버가 꺼지거나(명세서 S4) 밤새 켜져 요금이 나간다. 지켜야 하는 것:

- 스위치 꺼짐이면 아무것도 끄지 않는다(S7)
- 대기·평가중 제출이 있거나 평가 스택이 돌면 끄지 않는다(S4)
- SSH로 접속해 있으면 끄지 않고, 60분이 넘으면 한 번 알린다(S9)
- 30분 유휴면 끈다(S3)
"""

import datetime as dt

from worker.autostop_logic import AutostopInput, decide

NOW = dt.datetime(2026, 10, 10, 3, 0, tzinfo=dt.timezone.utc)


def ago(**kwargs) -> dt.datetime:
    return NOW - dt.timedelta(**kwargs)


def inp(**overrides) -> AutostopInput:
    """기본값: 스위치 켜짐, 할 일 없음, SSH 없음, 유휴 기록 없음."""
    values = dict(
        now=NOW,
        enabled=True,
        active_count=0,
        eval_stack_running=False,
        ssh_active=False,
        idle_since=None,
        ssh_block_since=None,
        ssh_alerted=False,
        idle_minutes=30,
        ssh_alert_minutes=60,
    )
    values.update(overrides)
    return AutostopInput(**values)


# ── 1. 스위치 ───────────────────────────────────────────────────────────


def test_스위치가_꺼져_있으면_기록을_지우고_끄지_않는다():
    d = decide(inp(enabled=False, idle_since=ago(hours=5), ssh_block_since=ago(hours=2), ssh_alerted=True))
    assert d.stop is False
    assert (d.idle_since, d.ssh_block_since, d.ssh_alerted) == (None, None, False)


# ── 2. 바쁨 ─────────────────────────────────────────────────────────────


def test_대기_또는_평가중_제출이_있으면_끄지_않는다():
    d = decide(inp(active_count=1, idle_since=ago(hours=1)))
    assert d.stop is False
    assert d.idle_since is None, "바쁘면 유휴 시간을 처음부터 다시 센다"


def test_평가_스택이_돌고_있으면_끄지_않는다():
    """DB에는 끝난 것으로 보여도 스택이 남아 있으면 정리 중일 수 있다."""
    d = decide(inp(eval_stack_running=True, idle_since=ago(hours=1)))
    assert d.stop is False
    assert d.idle_since is None


def test_바쁘면_SSH_기록도_지운다():
    d = decide(inp(active_count=1, ssh_active=True, ssh_block_since=ago(minutes=90), ssh_alerted=True))
    assert (d.ssh_block_since, d.ssh_alerted, d.ssh_event) == (None, False, None)


# ── 3. SSH ──────────────────────────────────────────────────────────────


def test_SSH_접속_중이면_30분이_넘어도_끄지_않는다():
    d = decide(inp(ssh_active=True, idle_since=ago(hours=2)))
    assert d.stop is False
    assert d.ssh_block_since == NOW
    assert d.idle_since is None, "접속을 끊은 뒤 30분을 새로 센다"


def test_SSH_60분이면_한_번_알린다():
    d = decide(inp(ssh_active=True, ssh_block_since=ago(minutes=60)))
    assert d.ssh_event is not None
    assert d.ssh_alerted is True
    assert d.stop is False


def test_SSH_59분이면_아직_알리지_않는다():
    d = decide(inp(ssh_active=True, ssh_block_since=ago(minutes=59, seconds=59)))
    assert d.ssh_event is None
    assert d.ssh_alerted is False


def test_SSH_알림은_한_번만():
    d = decide(inp(ssh_active=True, ssh_block_since=ago(minutes=120), ssh_alerted=True))
    assert d.ssh_event is None
    assert d.ssh_alerted is True
    assert d.ssh_block_since == ago(minutes=120), "시작 시각을 유지해야 한다"


def test_SSH를_끊으면_SSH_기록을_지우고_유휴를_새로_센다():
    d = decide(inp(ssh_active=False, ssh_block_since=ago(minutes=90), ssh_alerted=True))
    assert (d.ssh_block_since, d.ssh_alerted) == (None, False)
    assert d.idle_since == NOW
    assert d.stop is False


# ── 4. 유휴 ─────────────────────────────────────────────────────────────


def test_처음_유휴면_시작_시각만_기록한다():
    d = decide(inp())
    assert d.idle_since == NOW
    assert d.stop is False


def test_유휴_29분이면_끄지_않는다():
    d = decide(inp(idle_since=ago(minutes=29, seconds=59)))
    assert d.stop is False
    assert d.idle_since == ago(minutes=29, seconds=59)


def test_유휴_정확히_30분이면_끈다():
    d = decide(inp(idle_since=ago(minutes=30)))
    assert d.stop is True


def test_유휴_시간은_설정값을_따른다():
    assert decide(inp(idle_since=ago(minutes=10), idle_minutes=10)).stop is True
