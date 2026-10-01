"""시즌 상태 전환 검증 (plan.md §5.7).

배경: 전환 버튼이 시즌 상세 맨 위, 대시보드에서 시즌을 눌러 들어오는 자리에 있어 한 번 더 눌리면
넘어갔다. 서버는 "지금 상태에서 한 칸 전진"이라 같은 요청이 두 번 가면 두 칸 갔고, 되돌릴 수 없었다.
지켜야 하는 것:

- 폼이 보낸 `from_status`가 현재 상태와 다르면 **아무것도 하지 않는다** (이중 요청 방어).
- 한 칸 앞·뒤로만 갈 수 있다. 아카이브는 되돌릴 수 없다.
- 아카이브로 갈 때만 파일·계정 정리(`archive_season`)가 돈다.
"""

import types

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.deps import get_current_admin
from app.main import app
from app.models import SeasonStatus
from app.routers import admin as admin_router


class FakeDB:
    def __init__(self, season):
        self.season = season
        self.commits = 0

    def get(self, _model, key):
        return self.season if key == self.season.id else None

    def commit(self):
        self.commits += 1


@pytest.fixture
def world(monkeypatch):
    season = types.SimpleNamespace(id=1, status=SeasonStatus.ACTIVE, hidden=False)
    db = FakeDB(season)
    archived = []

    def fake_archive(_db, s, _videos_dir):
        archived.append(s.id)
        s.status = SeasonStatus.ARCHIVED

    monkeypatch.setattr(admin_router, "archive_season", fake_archive)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_admin] = lambda: types.SimpleNamespace(id=1)
    yield types.SimpleNamespace(client=TestClient(app), season=season, archived=archived)
    app.dependency_overrides.clear()


def change(client, from_status, to_status):
    return client.post(
        "/admin/seasons/1/status",
        data={"from_status": from_status, "to_status": to_status},
        follow_redirects=False,
    )


def test_advance_one_step(world):
    assert change(world.client, "active", "closed").status_code == 303
    assert world.season.status == SeasonStatus.CLOSED


def test_double_submit_moves_only_one_step(world):
    """더블클릭·새로고침 재전송 — 예전에는 진행중 → 마감 → 아카이브까지 갔다."""
    change(world.client, "active", "closed")
    change(world.client, "active", "closed")
    assert world.season.status == SeasonStatus.CLOSED
    assert world.archived == []


def test_revert_one_step(world):
    change(world.client, "active", "preparing")
    assert world.season.status == SeasonStatus.PREPARING


def test_revert_closed_to_active(world):
    world.season.status = SeasonStatus.CLOSED
    change(world.client, "closed", "active")
    assert world.season.status == SeasonStatus.ACTIVE


def test_cannot_skip_a_step(world):
    change(world.client, "active", "archived")
    assert world.season.status == SeasonStatus.ACTIVE
    assert world.archived == []


def test_archive_runs_cleanup(world):
    world.season.status = SeasonStatus.CLOSED
    change(world.client, "closed", "archived")
    assert world.archived == [1]


def test_archived_cannot_be_reverted(world):
    """아카이브 때 파일·계정이 이미 지워져 되돌릴 대상이 없다."""
    world.season.status = SeasonStatus.ARCHIVED
    change(world.client, "archived", "closed")
    assert world.season.status == SeasonStatus.ARCHIVED


def test_garbage_status_value_is_ignored(world):
    assert change(world.client, "active", "nonsense").status_code == 303
    assert world.season.status == SeasonStatus.ACTIVE


def test_old_route_is_gone(world):
    res = world.client.post("/admin/seasons/1/advance-status", follow_redirects=False)
    assert res.status_code in (404, 405)
    assert world.season.status == SeasonStatus.ACTIVE


def test_requires_admin(world):
    del app.dependency_overrides[get_current_admin]
    assert change(world.client, "active", "closed").status_code == 404
    assert world.season.status == SeasonStatus.ACTIVE


# ── 숨김 토글 ────────────────────────────────────────────────────────────


def test_hide_and_show(world):
    world.client.post("/admin/seasons/1/visibility", data={"action": "hide"}, follow_redirects=False)
    assert world.season.hidden is True
    world.client.post("/admin/seasons/1/visibility", data={"action": "show"}, follow_redirects=False)
    assert world.season.hidden is False


def test_visibility_requires_admin(world):
    del app.dependency_overrides[get_current_admin]
    res = world.client.post("/admin/seasons/1/visibility", data={"action": "hide"}, follow_redirects=False)
    assert res.status_code == 404
    assert world.season.hidden is False
