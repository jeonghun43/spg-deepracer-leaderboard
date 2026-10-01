"""긴급 패치용 업로드 일시 중지 검증.

지난 대회 온라인 주행 기간에 평가 서버를 급히 고칠 때 참가자 업로드를 막을 수단이 없었다.
지켜야 하는 것:

- 중지 중에는 **서버가** 업로드를 거절한다 — 중지 전에 페이지를 열어 둔 참가자는 폼이
  그대로 보이므로 화면에서 숨기는 것만으로는 못 막는다.
- 관리자가 켜고 끌 수 있고, 미인증 요청은 404 (관리자 경로 은닉 규칙).
"""

import types
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.deps import get_current_admin, get_current_team
from app.main import app
from app.models import SeasonStatus
from app.routers.submissions import DEFAULT_UPLOADS_PAUSED_MESSAGE


class FakeDB:
    def __init__(self, season):
        self.season = season
        self.added = []
        self.commits = 0

    def get(self, _model, key):
        return self.season if key == self.season.id else None

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1


@pytest.fixture
def world():
    season = types.SimpleNamespace(
        id=1,
        status=SeasonStatus.ACTIVE,
        uploads_paused=True,
        uploads_paused_message="평가 서버 점검 중 (15시 재개 예정) & 공지 확인",
        uploads_paused_at=None,
    )
    team = types.SimpleNamespace(id=1, season=season, season_id=1, disqualified=False)
    db = FakeDB(season)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_team] = lambda: team
    yield types.SimpleNamespace(client=TestClient(app), season=season, db=db)
    app.dependency_overrides.clear()


def upload(client, **headers):
    return client.post(
        "/submit",
        files={"model_file": ("model.tar.gz", b"x" * 10, "application/gzip")},
        headers=headers,
        follow_redirects=False,
    )


def test_paused_upload_is_rejected_for_form_post(world):
    res = upload(world.client)
    assert res.status_code == 303
    assert world.db.added == [], "중지 중에는 제출 레코드가 생기면 안 된다"


def test_paused_message_survives_the_redirect_query(world):
    """공지 문구에 &가 있어도 쿼리가 잘리지 않고 그대로 전달돼야 한다."""
    res = upload(world.client)
    query = parse_qs(urlparse(res.headers["location"]).query)
    assert query["error"] == [world.season.uploads_paused_message]


def test_paused_upload_is_rejected_for_script_upload(world):
    res = upload(world.client, Accept="application/json")
    assert res.status_code == 400
    assert res.json()["error"] == world.season.uploads_paused_message


def test_default_message_when_admin_left_it_blank(world):
    world.season.uploads_paused_message = None
    res = upload(world.client, Accept="application/json")
    assert res.json()["error"] == DEFAULT_UPLOADS_PAUSED_MESSAGE


# ── 관리자 토글 ──────────────────────────────────────────────────────────


def test_admin_toggle_requires_login(world):
    res = world.client.post(
        "/admin/seasons/1/uploads-pause", data={"action": "resume"}, follow_redirects=False
    )
    assert res.status_code == 404
    assert world.season.uploads_paused is True


def test_admin_can_resume_and_pause(world):
    app.dependency_overrides[get_current_admin] = lambda: types.SimpleNamespace(id=1)

    res = world.client.post(
        "/admin/seasons/1/uploads-pause", data={"action": "resume"}, follow_redirects=False
    )
    assert res.status_code == 303
    assert world.season.uploads_paused is False
    assert world.season.uploads_paused_message is None

    world.client.post(
        "/admin/seasons/1/uploads-pause",
        data={"action": "pause", "message": "  워커 교체 중  "},
        follow_redirects=False,
    )
    assert world.season.uploads_paused is True
    assert world.season.uploads_paused_message == "워커 교체 중"
    assert world.season.uploads_paused_at is not None


def test_blank_pause_message_falls_back_to_default(world):
    app.dependency_overrides[get_current_admin] = lambda: types.SimpleNamespace(id=1)
    world.client.post(
        "/admin/seasons/1/uploads-pause",
        data={"action": "pause", "message": "   "},
        follow_redirects=False,
    )
    assert world.season.uploads_paused_message is None
