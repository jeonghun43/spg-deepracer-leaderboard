"""시즌 숨김 검증 (plan.md §5.7).

숨긴 시즌은 방문자에게 시즌 목록·자동 진입·리더보드에서 모두 보이지 않아야 한다. 목록에서만 빼고
주소를 열어 두면 번호만 바꿔 넣어 들어간다. 관리자와 **그 시즌의 참가팀**에게는 계속 보인다.

`get_open_season`처럼 SQL에서 거르는 부분이 있어 흉내 객체 대신 메모리 SQLite를 쓴다.
"""

import datetime as dt
import types

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.deps import get_current_admin_optional, get_current_team_optional
from app.main import app
from app.models import Season, SeasonStatus
from app.routers.leaderboard import get_open_season

TODAY = dt.date(2026, 10, 1)


@pytest.fixture
def world():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    public = Season(name="공개 시즌", track_name="t", start_date=TODAY - dt.timedelta(days=30),
                    end_date=TODAY, status=SeasonStatus.ACTIVE)
    hidden = Season(name="숨긴 시즌", track_name="t", start_date=TODAY,
                    end_date=TODAY + dt.timedelta(days=7), status=SeasonStatus.ACTIVE, hidden=True)
    db.add_all([public, hidden])
    db.commit()

    viewer = {"team": None, "admin": None}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_team_optional] = lambda: viewer["team"]
    app.dependency_overrides[get_current_admin_optional] = lambda: viewer["admin"]
    yield types.SimpleNamespace(
        client=TestClient(app), db=db, public=public, hidden=hidden, viewer=viewer
    )
    app.dependency_overrides.clear()
    db.close()


def team_of(season):
    return types.SimpleNamespace(id=99, season_id=season.id)


def test_open_season_skips_hidden(world):
    """숨긴 시즌이 더 최근에 시작했어도 /leaderboard 자동 진입은 공개 시즌으로 간다."""
    assert get_open_season(world.db).id == world.public.id


def test_entry_shows_list_when_only_hidden_is_active(world):
    world.public.status = SeasonStatus.CLOSED
    world.db.commit()
    res = world.client.get("/leaderboard", follow_redirects=False)
    assert res.status_code == 200
    assert "숨긴 시즌" not in res.text


def test_season_list_hides_from_visitors(world):
    text = world.client.get("/leaderboard/seasons").text
    assert "공개 시즌" in text
    assert "숨긴 시즌" not in text


def test_season_list_shows_hidden_to_admin(world):
    world.viewer["admin"] = types.SimpleNamespace(id=1)
    text = world.client.get("/leaderboard/seasons").text
    assert "숨긴 시즌" in text and "숨김" in text


def test_season_list_shows_hidden_to_its_team(world):
    world.viewer["team"] = team_of(world.hidden)
    assert "숨긴 시즌" in world.client.get("/leaderboard/seasons").text


def test_hidden_leaderboard_redirects_visitor_like_missing_season(world):
    hidden = world.client.get(f"/leaderboard/{world.hidden.id}", follow_redirects=False)
    missing = world.client.get("/leaderboard/9999", follow_redirects=False)
    assert hidden.status_code == missing.status_code == 303
    assert hidden.headers["location"] == missing.headers["location"]


def test_hidden_leaderboard_blocks_team_of_other_season(world):
    world.viewer["team"] = team_of(world.public)
    assert world.client.get(f"/leaderboard/{world.hidden.id}", follow_redirects=False).status_code == 303


def test_hidden_leaderboard_open_to_its_team_with_notice(world):
    world.viewer["team"] = team_of(world.hidden)
    res = world.client.get(f"/leaderboard/{world.hidden.id}")
    assert res.status_code == 200
    assert "숨김 시즌" in res.text


def test_hidden_leaderboard_open_to_admin(world):
    world.viewer["admin"] = types.SimpleNamespace(id=1)
    assert world.client.get(f"/leaderboard/{world.hidden.id}").status_code == 200


def test_public_leaderboard_has_no_hidden_notice(world):
    res = world.client.get(f"/leaderboard/{world.public.id}")
    assert res.status_code == 200
    assert "숨김 시즌" not in res.text
