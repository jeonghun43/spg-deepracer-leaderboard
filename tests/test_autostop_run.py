"""평가 서버 autostop 실행 검증 — 가짜 명령·가짜 DB로 끄기 경로를 끝까지 따라간다 (tasks.md T10).

지켜야 하는 것:

- 끄기 순서: idle_stop 기록 → 워커 중지 → 잡힌 제출 되돌리기 → poweroff. poweroff는 **마지막에 한 번만**
- 중간 단계가 실패하면 poweroff하지 않는다 — 평가가 '평가중'에 갇히거나 알림 없이 꺼지면 안 된다
- 확신이 없으면(docker 조회 실패, DB 불통) 끄지 않는다
- --dry-run은 아무것도 바꾸지 않는다
"""

import datetime as dt
import subprocess
import types

import pytest

from app.autopilot_logic import IDLE_STOP, SSH_BLOCKING
from worker import autostop

NOW = dt.datetime(2026, 10, 10, 3, 0, tzinfo=dt.timezone.utc)


class FakeSession:
    def __init__(self):
        self.rollbacks = 0
        self.closed = False

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def ok(stdout=""):
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


def fail(stderr="boom"):
    return subprocess.CompletedProcess([], 1, stdout="", stderr=stderr)


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = types.SimpleNamespace(
        calls=[],        # 순서대로 기록: 명령과 DB 작업
        enabled=True,
        active=0,
        stacks="",
        who="",
        ss="",
        docker_ok=True,
        worker_stop_ok=True,
        requeue_error=None,
        record_error=None,
    )

    def run_cmd(args):
        w.calls.append(" ".join(args[:3]))
        if args[:2] == ["docker", "stack"]:
            return ok(w.stacks) if w.docker_ok else fail()
        if args[0] == "who":
            return ok(w.who)
        if args[0] == "ss":
            return ok(w.ss)
        if args[:2] == ["systemctl", "stop"]:
            return ok() if w.worker_stop_ok else fail()
        return ok()

    def read_db_state(db):
        return w.enabled, w.active

    def record_event(db, kind, message):
        if w.record_error:
            raise w.record_error
        w.calls.append(f"event {kind}")

    def requeue_my_running(db):
        if w.requeue_error:
            raise w.requeue_error
        w.calls.append("requeue")
        return 1

    monkeypatch.setattr(autostop, "run_cmd", run_cmd)
    monkeypatch.setattr(autostop, "read_db_state", read_db_state)
    monkeypatch.setattr(autostop, "record_event", record_event)
    monkeypatch.setattr(autostop, "requeue_my_running", requeue_my_running)
    monkeypatch.setattr(autostop, "SessionLocal", FakeSession)
    monkeypatch.setattr(autostop, "STATE_DIR", tmp_path / "drfc-autostop")
    monkeypatch.setattr(autostop, "now_utc", lambda: NOW)
    w.state_dir = tmp_path / "drfc-autostop"
    return w


def set_idle_since(w, value):
    w.state_dir.mkdir(parents=True, exist_ok=True)
    (w.state_dir / "idle_since").write_text(value.isoformat())


def poweroffs(w):
    return [c for c in w.calls if c == "systemctl poweroff"]


def test_30분_유휴면_순서대로_끄고_poweroff는_마지막에_한_번(world):
    set_idle_since(world, NOW - dt.timedelta(minutes=30))
    assert autostop.main([]) == 0

    tail = world.calls[world.calls.index(f"event {IDLE_STOP}"):]
    assert tail == [f"event {IDLE_STOP}", "systemctl stop drfc-worker", "requeue", "systemctl poweroff"]
    assert len(poweroffs(world)) == 1
    assert world.calls[-1] == "systemctl poweroff"


def test_처음_유휴면_시각만_기록하고_끄지_않는다(world):
    assert autostop.main([]) == 0
    assert poweroffs(world) == []
    saved = dt.datetime.fromisoformat((world.state_dir / "idle_since").read_text())
    assert saved == NOW


def test_대기가_있으면_유휴_기록을_지운다(world):
    set_idle_since(world, NOW - dt.timedelta(hours=1))
    world.active = 1
    autostop.main([])
    assert poweroffs(world) == []
    assert not (world.state_dir / "idle_since").exists()


def test_평가_스택이_있으면_끄지_않는다(world):
    set_idle_since(world, NOW - dt.timedelta(hours=1))
    world.stacks = "something-else\ndeepracer-eval-0\n"
    autostop.main([])
    assert poweroffs(world) == []


def test_docker_조회가_실패하면_바쁜_것으로_본다(world):
    set_idle_since(world, NOW - dt.timedelta(hours=1))
    world.docker_ok = False
    autostop.main([])
    assert poweroffs(world) == []


@pytest.mark.parametrize("who, ss", [("ubuntu pts/0 2026-10-10 11:58 (100.64.0.1)", ""), ("", "ESTAB 0 0 10.0.0.5:22 10.0.0.9:51234")])
def test_SSH_접속이_있으면_끄지_않는다(world, who, ss):
    """who에 안 보이는 접속(VS Code 원격·scp)도 ss로 잡는다."""
    set_idle_since(world, NOW - dt.timedelta(hours=1))
    world.who, world.ss = who, ss
    autostop.main([])
    assert poweroffs(world) == []
    assert (world.state_dir / "ssh_block_since").exists()


def test_SSH_60분이면_알림을_남기고_한_번만(world):
    world.who = "ubuntu pts/0"
    world.state_dir.mkdir(parents=True)
    (world.state_dir / "ssh_block_since").write_text((NOW - dt.timedelta(minutes=61)).isoformat())
    autostop.main([])
    autostop.main([])
    assert world.calls.count(f"event {SSH_BLOCKING}") == 1
    assert (world.state_dir / "ssh_alerted").exists()


def test_스위치가_꺼져_있으면_끄지_않는다(world):
    set_idle_since(world, NOW - dt.timedelta(hours=5))
    world.enabled = False
    autostop.main([])
    assert poweroffs(world) == []


def test_idle_stop_기록에_실패하면_끄지_않는다(world):
    """알림 없이 꺼지면 운영자가 서버가 왜 꺼졌는지 모른다. DB 불통이면 대기열도 확인할 수 없다."""
    set_idle_since(world, NOW - dt.timedelta(minutes=31))
    world.record_error = RuntimeError("db down")
    assert autostop.main([]) == 1
    assert "systemctl stop drfc-worker" not in world.calls
    assert poweroffs(world) == []


def test_워커_중지에_실패하면_끄지_않는다(world):
    set_idle_since(world, NOW - dt.timedelta(minutes=31))
    world.worker_stop_ok = False
    assert autostop.main([]) == 1
    assert poweroffs(world) == []


def test_되돌리기에_실패하면_워커를_다시_올리고_끄지_않는다(world):
    """되돌리지 못한 채 끄면 그 제출이 '평가중'에 갇히고, 대기열이 비어 보여 아무도 다시 켜지 않는다."""
    set_idle_since(world, NOW - dt.timedelta(minutes=31))
    world.requeue_error = RuntimeError("db down")
    assert autostop.main([]) == 1
    assert poweroffs(world) == []
    assert world.calls[-1] == "systemctl start drfc-worker"


def test_dry_run은_아무것도_바꾸지_않는다(world, capsys):
    set_idle_since(world, NOW - dt.timedelta(minutes=31))
    assert autostop.main(["--dry-run"]) == 0
    assert poweroffs(world) == []
    assert not any(c.startswith("event") or c.startswith("systemctl") for c in world.calls)
    assert "끕니다" in capsys.readouterr().out
    # 유휴 기록도 그대로다
    assert dt.datetime.fromisoformat((world.state_dir / "idle_since").read_text()) == NOW - dt.timedelta(minutes=31)
