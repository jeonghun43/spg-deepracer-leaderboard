"""평가 서버 자동 끄기 — 1분마다 systemd 타이머가 한 번씩 실행한다 (worker-auto-start-stop-plan.md §2).

실행: (평가 서버, 저장소 루트에서, root로) `python -m worker.autostop [--dry-run]`
      systemd 유닛은 docs/worker-server-setup.md "자동 켜기·끄기" 절에 있다.

한 번 판단하고 끝난다(판단은 worker/autostop_logic.py). 30분 동안 할 일이 없으면:
  ① DB에 idle_stop 기록 → ② 워커 내리기 → ③ 그 사이 집은 제출을 대기열로 되돌리기 → ④ poweroff

**AWS 키 없이 끈다.** 서버 안에서 poweroff하면 인스턴스의 "종료 시 동작"(stop)에 따라 중지된다.
그래서 이 서버에는 지금처럼 AWS 자격증명을 두지 않는다(CLAUDE.md §4). 다시 켜는 것은 웹 서버의
autopilot이 한다.

**확신이 없으면 끄지 않는다.** DB에 닿지 않거나 docker 조회가 실패하면 바쁜 것으로 본다.
켜져 있어서 생기는 손해는 요금이지만, 잘못 꺼서 생기는 손해는 평가 중단이다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.autopilot_logic import IDLE_STOP, SSH_BLOCKING
from app.config import settings
from app.db import SessionLocal
from app.models import AutopilotEvent, AutopilotState, Submission, SubmissionStatus
from worker.autostop_logic import AutostopInput, AutostopDecision, decide

logger = logging.getLogger("autostop")

# worker/run.py의 WORKER_ID와 같은 값이어야 한다 — 이 서버가 잡은 '평가중' 제출을 찾는 기준이다.
WORKER_ID = socket.gethostname()
# /run은 재부팅하면 비워진다. 그래서 켜질 때마다 유휴 시간을 0부터 다시 센다. 의도한 동작이다.
STATE_DIR = Path(os.environ.get("AUTOSTOP_STATE_DIR", "/run/drfc-autostop"))
EVAL_STACK_PREFIX = "deepracer-eval-"  # worker/run_evaluation.sh의 STACK_NAME
WORKER_SERVICE = "drfc-worker"


def now_utc() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


# ── 외부 명령 — 테스트에서 통째로 바꾼다 ────────────────────────────────────────


def run_cmd(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=60)


def eval_stack_running() -> bool:
    result = run_cmd(["docker", "stack", "ls", "--format", "{{.Name}}"])
    if result.returncode != 0:
        # docker가 대답하지 않으면 평가 중인지 알 수 없다. 끄지 않는 쪽으로 본다.
        logger.warning("docker stack ls 실패 — 바쁜 것으로 봅니다: %s", result.stderr.strip()[:200])
        return True
    return any(line.strip().startswith(EVAL_STACK_PREFIX) for line in result.stdout.splitlines())


def ssh_active() -> bool:
    """누군가 접속해 있나. 둘 중 하나라도 있으면 접속 중으로 본다.

    - `who`: 터미널(tty)을 연 로그인. 보통의 `ssh` 접속이 여기에 보인다
    - `ss`: 22번 포트의 연결. VS Code 원격 접속·scp처럼 터미널을 열지 않는 접속은 `who`에 안 보인다
    """
    who = run_cmd(["who"])
    if who.returncode == 0 and who.stdout.strip():
        return True
    ss = run_cmd(["ss", "-Htn", "state", "established", "( sport = :22 )"])
    return ss.returncode == 0 and bool(ss.stdout.strip())


# ── DB ───────────────────────────────────────────────────────────────────────


def read_db_state(db: Session) -> tuple[bool, int]:
    """(자동화 스위치, 대기+평가중 제출 수)."""
    state = db.get(AutopilotState, 1)
    enabled = bool(state.enabled) if state is not None else False
    active = db.execute(
        select(func.count())
        .select_from(Submission)
        .where(Submission.status.in_([SubmissionStatus.QUEUED, SubmissionStatus.RUNNING]))
    ).scalar_one()
    return enabled, active


def record_event(db: Session, kind: str, message: str) -> None:
    db.add(AutopilotEvent(kind=kind, source=WORKER_ID, message=message))
    db.commit()


def requeue_my_running(db: Session) -> int:
    """이 서버가 잡은 '평가중' 제출을 대기열로 되돌린다(worker/run.py recover_stale_running과 같은 처리).

    끄기로 판단한 뒤 워커를 내리기 전 몇 초 사이에 워커가 새 제출을 집었을 수 있다. 그대로 끄면
    그 제출이 '평가중'에 갇힌다(명세서 S4). 웹 서버의 autopilot이 대기열을 보고 다시 켠다.
    """
    result = db.execute(
        update(Submission)
        .where(Submission.status == SubmissionStatus.RUNNING, Submission.worker_id == WORKER_ID)
        .values(status=SubmissionStatus.QUEUED, worker_id=None, started_at=None)
    )
    db.commit()
    return result.rowcount or 0


# ── 상태 파일 ─────────────────────────────────────────────────────────────────


def _read_time(name: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat((STATE_DIR / name).read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _write_time(name: str, value: dt.datetime | None) -> None:
    path = STATE_DIR / name
    if value is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(value.isoformat())


def load_state() -> tuple[dt.datetime | None, dt.datetime | None, bool]:
    return _read_time("idle_since"), _read_time("ssh_block_since"), (STATE_DIR / "ssh_alerted").exists()


def save_state(decision: AutostopDecision) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _write_time("idle_since", decision.idle_since)
    _write_time("ssh_block_since", decision.ssh_block_since)
    flag = STATE_DIR / "ssh_alerted"
    if decision.ssh_alerted:
        flag.touch()
    else:
        flag.unlink(missing_ok=True)


# ── 끄기 ─────────────────────────────────────────────────────────────────────


def shut_down(db: Session, reason: str) -> None:
    """plan.md §2.2-4의 순서. 순서가 중요하다.

    1. idle_stop 기록을 **먼저** 남긴다. 끄고 나면 알릴 수 없다. 기록에 실패하면(DB 불통) 끄지 않는다
    2. 워커를 내린다. 실패하면 끄지 않는다 — 워커가 계속 제출을 집는 중일 수 있다
    3. 그 사이 집은 제출을 되돌린다. 실패하면 워커를 다시 올리고 끄지 않는다 — 되돌리지 못한 채 끄면
       '평가중'에 갇히고, 대기열이 비어 보여 아무도 서버를 다시 켜지 않는다
    4. poweroff
    """
    record_event(db, IDLE_STOP, f"{reason}. 평가 서버를 중지합니다. 다음 제출이 오면 자동으로 켜집니다.")

    stopped = run_cmd(["systemctl", "stop", WORKER_SERVICE])
    if stopped.returncode != 0:
        raise RuntimeError(f"워커 중지 실패 — 끄지 않습니다: {stopped.stderr.strip()[:200]}")

    try:
        requeued = requeue_my_running(db)
    except Exception:
        db.rollback()
        run_cmd(["systemctl", "start", WORKER_SERVICE])
        raise
    if requeued:
        logger.warning("끄기 직전에 잡힌 제출 %s건을 대기열로 되돌렸습니다", requeued)

    logger.info("poweroff")
    run_cmd(["systemctl", "poweroff"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="평가 서버 자동 끄기 (1회 판단)")
    parser.add_argument("--dry-run", action="store_true", help="판단 결과만 출력하고 아무것도 바꾸지 않는다")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    db = SessionLocal()
    try:
        enabled, active = read_db_state(db)
        idle_since, ssh_block_since, ssh_alerted = load_state()
        decision = decide(
            AutostopInput(
                now=now_utc(),
                enabled=enabled,
                active_count=active,
                eval_stack_running=eval_stack_running(),
                ssh_active=ssh_active(),
                idle_since=idle_since,
                ssh_block_since=ssh_block_since,
                ssh_alerted=ssh_alerted,
                idle_minutes=settings.autostop_idle_minutes,
                ssh_alert_minutes=settings.autostop_ssh_alert_minutes,
            )
        )

        if args.dry_run:
            print(f"[dry-run] worker_id={WORKER_ID} 판단: {decision.reason}")
            if decision.ssh_event:
                print(f"[dry-run] 남길 알림: {SSH_BLOCKING} — {decision.ssh_event}")
            print(f"[dry-run] {'끕니다(실제로는 끄지 않음)' if decision.stop else '끄지 않습니다'}")
            return 0

        logger.info("판단: %s", decision.reason)
        if decision.ssh_event:
            # 알림을 DB에 남긴 다음에 "보냈음"을 기억한다. 기록에 실패하면 다음 주기에 다시 시도한다.
            record_event(db, SSH_BLOCKING, decision.ssh_event)
        save_state(decision)
        if decision.stop:
            shut_down(db, decision.reason)
        return 0
    except Exception:  # noqa: BLE001 - systemd 로그(journalctl)에 남기고 다음 주기를 기다린다
        db.rollback()
        logger.exception("autostop 실패 — 이번 주기는 끄지 않습니다")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
