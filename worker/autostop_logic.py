"""평가 서버 자동 끄기 — 판단 로직 (worker-auto-start-stop-plan.md §2.2).

DB·docker·who·시계를 모두 인자로 받는 순수 함수다. 판단이 틀리면 평가 도중에 서버가 꺼지거나
(명세서 S4 위반) 밤새 켜져 요금이 나간다. 그래서 분기마다 테스트로 고정한다
(tests/test_autostop_logic.py). 실행은 worker/autostop.py가 맡는다.

출력은 "다음 주기에 기억할 상태"와 "이번에 할 일"이다. 상태는 /run/drfc-autostop/에 파일로 둔다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class AutostopInput:
    now: dt.datetime
    enabled: bool
    # 대기(queued) + 평가중(running) 제출 수. 다른 워커가 잡은 것도 센다 — 대기열이 비어야 할 일이 없는 것이다.
    active_count: int
    eval_stack_running: bool
    ssh_active: bool
    idle_since: dt.datetime | None
    ssh_block_since: dt.datetime | None
    ssh_alerted: bool
    idle_minutes: int = 30
    ssh_alert_minutes: int = 60


@dataclass(frozen=True)
class AutostopDecision:
    idle_since: dt.datetime | None
    ssh_block_since: dt.datetime | None
    ssh_alerted: bool
    # 이번에 남길 ssh_blocking 이벤트 문구 (없으면 None)
    ssh_event: str | None = None
    stop: bool = False
    # 사람이 읽는 판단 이유 — 로그와 --dry-run 출력용
    reason: str = ""


def decide(inp: AutostopInput) -> AutostopDecision:
    """plan.md §2.2의 1~4단계.

    1. 스위치가 꺼져 있으면 기록을 모두 지우고 끝낸다. 사람이 직접 관리하는 상태다.
    2. 대기·평가중 제출이 있거나 평가 스택이 돌고 있으면 바쁘다. 기록을 지우고 끝낸다.
    3. SSH 접속이 있으면 끄지 않는다. 그 상태가 60분을 넘으면 한 번 알린다.
       **유휴 기록도 지운다.** 접속을 끊고 나서 30분을 새로 센다. 작업하다 잠깐 끊었다 다시
       붙는 사이에 서버가 꺼지면 안 되기 때문이다(구현 때 정함, plan.md §2.2에 반영).
    4. 유휴 시작 시각을 기록하고, 30분이 지났으면 끈다.
    """
    now = inp.now

    if not inp.enabled:
        return AutostopDecision(None, None, False, reason="자동화 꺼짐 — 사람이 직접 관리")

    if inp.active_count > 0 or inp.eval_stack_running:
        why = []
        if inp.active_count > 0:
            why.append(f"대기·평가중 제출 {inp.active_count}건")
        if inp.eval_stack_running:
            why.append("평가 스택 실행 중")
        return AutostopDecision(None, None, False, reason="바쁨: " + ", ".join(why))

    if inp.ssh_active:
        block_since = inp.ssh_block_since or now
        blocked = now - block_since
        event = None
        alerted = inp.ssh_alerted
        if not alerted and blocked >= dt.timedelta(minutes=inp.ssh_alert_minutes):
            event = (
                f"할 일이 없는데 SSH 접속이 {int(blocked.total_seconds() // 60)}분째 이어져 "
                "평가 서버를 끄지 못하고 있습니다. 작업이 끝났으면 접속을 끊으세요."
            )
            alerted = True
        return AutostopDecision(
            None, block_since, alerted, ssh_event=event,
            reason=f"SSH 접속 중 — 끄지 않음 ({int(blocked.total_seconds() // 60)}분째)",
        )

    # SSH가 없으니 SSH 기록은 지운다. 다음에 접속하면 다시 0분부터 센다.
    idle_since = inp.idle_since or now
    idle = now - idle_since
    if idle >= dt.timedelta(minutes=inp.idle_minutes):
        return AutostopDecision(
            idle_since, None, False, stop=True,
            reason=f"유휴 {int(idle.total_seconds() // 60)}분 — 끈다",
        )
    return AutostopDecision(
        idle_since, None, False,
        reason=f"유휴 {int(idle.total_seconds() // 60)}분 / {inp.idle_minutes}분",
    )
