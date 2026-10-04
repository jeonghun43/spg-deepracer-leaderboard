"""관리자 페이지 — 평가 서버 자동화 스위치 검증 (worker-auto-start-stop-plan.md §4).

지켜야 하는 것:

- 미인증 GET/POST는 404 (관리자 경로 은닉 규칙, tests/test_upload_pause.py와 같다)
- 켜고 끄면 상태와 switch_changed 기록이 바뀐다. 같은 값으로 다시 보내면(더블클릭) 아무것도 안 바뀐다
- 인스턴스 ID가 설정되지 않았으면 바꾸지 않는다 — 켜 둬도 아무 일도 안 일어나는데 "켜짐"으로 보이면 안 된다
"""

import datetime as dt
import types

import pytest
from fastapi.testclient import TestClient

from app.autopilot_logic import SWITCH_CHANGED
from app.config import settings
from app.db import get_db
from app.deps import get_current_admin
from app.main import app
from app.models import AutopilotEvent, AutopilotState, WorkerHeartbeat
from app.routers import admin as admin_router


class FakeDB:
    def __init__(self, state, heartbeat=None):
        self.state = state
        self.heartbeat = heartbeat
        self.added = []
        self.commits = 0

    def get(self, model, key):
        if model is AutopilotState:
            return self.state if key == 1 else None
        if model is WorkerHeartbeat:
            return self.heartbeat
        return None

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, AutopilotState):
            self.state = obj

    def commit(self):
        self.commits += 1


def make_state(**overrides):
    values = dict(
        enabled=False,
        enabled_changed_at=None,
        enabled_changed_by=None,
        instance_state="stopped",
        instance_state_at=dt.datetime(2026, 10, 10, 3, 0, tzinfo=dt.timezone.utc),
        target_worker_id="ip-172-31-61-59",
        last_start_requested_at=None,
        attention_since=None,
    )
    values.update(overrides)
    return types.SimpleNamespace(**values)


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(settings, "autopilot_instance_id", "i-0abc")
    events = [
        types.SimpleNamespace(
            created_at=dt.datetime(2026, 10, 10, 3, 0, tzinfo=dt.timezone.utc),
            kind="idle_stop", source="ip-172-31-61-59", message="유휴 30분 — 끈다", notified_at=None,
        )
    ]
    monkeypatch.setattr(admin_router, "_recent_autopilot_events", lambda db: events)
    db = FakeDB(make_state())
    app.dependency_overrides[get_db] = lambda: db
    yield types.SimpleNamespace(client=TestClient(app), db=db)
    app.dependency_overrides.clear()


def login(login_id="ops"):
    app.dependency_overrides[get_current_admin] = lambda: types.SimpleNamespace(id=1, login_id=login_id)


def switch_events(db):
    return [o for o in db.added if isinstance(o, AutopilotEvent) and o.kind == SWITCH_CHANGED]


# ── 접근 제어 ────────────────────────────────────────────────────────────


def test_미인증_GET은_404(world):
    assert world.client.get("/admin/autopilot", follow_redirects=False).status_code == 404


def test_미인증_POST는_404이고_바뀌지_않는다(world):
    res = world.client.post("/admin/autopilot", data={"action": "enable"}, follow_redirects=False)
    assert res.status_code == 404
    assert world.db.state.enabled is False


# ── 화면 ────────────────────────────────────────────────────────────────


def test_화면에_상태와_기록이_보인다(world):
    login()
    world.db.heartbeat = types.SimpleNamespace(last_seen_at=dt.datetime.now(tz=dt.timezone.utc))
    world.db.state.instance_state = "running"
    res = world.client.get("/admin/autopilot")
    assert res.status_code == 200
    assert "켜짐 · 워커 정상" in res.text
    assert "자동화 켜기" in res.text
    assert "유휴 30분" in res.text


def test_설정되지_않았으면_화면에_알리고_버튼을_감춘다(world, monkeypatch):
    login()
    monkeypatch.setattr(settings, "autopilot_instance_id", "")
    res = world.client.get("/admin/autopilot")
    assert "설정되지 않음" in res.text
    assert "자동화 켜기" not in res.text


def test_대시보드에_링크가_있다(world, monkeypatch):
    login()

    class _Rows:
        def scalars(self):
            return types.SimpleNamespace(all=lambda: [])

    world.db.execute = lambda stmt: _Rows()
    assert 'href="/admin/autopilot"' in world.client.get("/admin").text


# ── 스위치 ──────────────────────────────────────────────────────────────


def test_켜고_끄면_상태와_기록이_바뀐다(world):
    login("ops")
    res = world.client.post("/admin/autopilot", data={"action": "enable"}, follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/admin/autopilot"
    assert world.db.state.enabled is True
    assert world.db.state.enabled_changed_by == "ops"
    assert world.db.state.enabled_changed_at is not None
    assert len(switch_events(world.db)) == 1
    assert "ops" in switch_events(world.db)[0].message

    world.client.post("/admin/autopilot", data={"action": "disable"}, follow_redirects=False)
    assert world.db.state.enabled is False
    assert len(switch_events(world.db)) == 2


def test_같은_값으로_다시_보내면_아무것도_안_바뀐다(world):
    """더블클릭·새로고침 재전송으로 디스코드에 같은 알림이 두 번 가면 안 된다(S5)."""
    login()
    world.client.post("/admin/autopilot", data={"action": "enable"}, follow_redirects=False)
    world.client.post("/admin/autopilot", data={"action": "enable"}, follow_redirects=False)
    assert len(switch_events(world.db)) == 1
    assert world.db.commits == 1


def test_인스턴스_ID가_없으면_POST가_아무것도_바꾸지_않는다(world, monkeypatch):
    login()
    monkeypatch.setattr(settings, "autopilot_instance_id", "")
    res = world.client.post("/admin/autopilot", data={"action": "enable"}, follow_redirects=False)
    assert res.status_code == 303
    assert world.db.state.enabled is False
    assert world.db.added == []
    assert world.db.commits == 0


def test_모르는_action은_무시한다(world):
    login()
    world.client.post("/admin/autopilot", data={"action": "toggle"}, follow_redirects=False)
    assert world.db.state.enabled is False
    assert world.db.commits == 0
