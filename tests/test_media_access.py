"""평가 영상 접근 권한 검증 (app/routers/media.py).

직전 제출 영상을 남기게 되면서 영상 폴더를 StaticFiles로 통째로 공개할 수 없게 됐다.
지켜야 하는 경계:

- 최고기록 영상은 리더보드에서 **누구나** 본다 (비로그인 포함).
- 최고기록이 아닌 영상(직전 제출)은 **그 팀만** 본다. 다른 팀·비로그인은 404 —
  403이면 "그 제출에 영상이 있다"는 사실이 드러난다.
"""

import datetime as dt
import types

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import get_db
from app.deps import get_current_team_optional
from app.main import app
from app.models import FinishStatus, SubmissionStatus

BASE_TIME = dt.datetime(2026, 10, 1, 10, 0, tzinfo=dt.timezone.utc)
VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"0123456789" * 100


class FakeDB:
    def __init__(self, submissions):
        self._by_id = {s.id: s for s in submissions}

    def get(self, _model, key):
        return self._by_id.get(key)


def make_team(team_id):
    return types.SimpleNamespace(id=team_id, submissions=[])


def add_submission(storage, team, sub_id, lap_time, minutes=0, with_file=True):
    video_rel = f"1/{team.id}/{sub_id}.mp4"
    if with_file:
        path = storage / "videos" / video_rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(VIDEO_BYTES)
    result = types.SimpleNamespace(
        finish_status=FinishStatus.FINISHED if lap_time is not None else FinishStatus.TIMEOUT,
        lap_time_seconds=lap_time,
        video_path=video_rel,
    )
    submission = types.SimpleNamespace(
        id=sub_id,
        team_id=team.id,
        team=team,
        status=SubmissionStatus.DONE,
        submitted_at=BASE_TIME + dt.timedelta(minutes=minutes),
        result=result,
    )
    team.submissions.append(submission)
    return submission


@pytest.fixture
def world(tmp_path, monkeypatch):
    """팀 A: 최고기록(1번) + 완주 못 한 직전 제출(2번). 팀 B: 다른 팀."""
    storage = tmp_path / "storage"
    (storage / "videos").mkdir(parents=True)
    monkeypatch.setattr(settings, "storage_dir", storage)

    team_a, team_b = make_team(1), make_team(2)
    best = add_submission(storage, team_a, 1, lap_time=90.0)
    latest = add_submission(storage, team_a, 2, lap_time=None, minutes=10)
    db = FakeDB([best, latest])

    viewer = {"team": None}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_team_optional] = lambda: viewer["team"]
    yield types.SimpleNamespace(
        client=TestClient(app), viewer=viewer, team_a=team_a, team_b=team_b,
        best=best, latest=latest, storage=storage, db=db,
    )
    app.dependency_overrides.clear()


def test_best_video_is_public(world):
    res = world.client.get(f"/media/videos/{world.best.id}")
    assert res.status_code == 200
    assert res.headers["content-type"] == "video/mp4"
    assert res.content == VIDEO_BYTES


def test_owner_can_view_latest_video(world):
    world.viewer["team"] = world.team_a
    assert world.client.get(f"/media/videos/{world.latest.id}").status_code == 200


def test_other_team_cannot_view_latest_video(world):
    world.viewer["team"] = world.team_b
    assert world.client.get(f"/media/videos/{world.latest.id}").status_code == 404


def test_anonymous_cannot_view_latest_video(world):
    assert world.client.get(f"/media/videos/{world.latest.id}").status_code == 404


def test_unknown_submission_is_404(world):
    assert world.client.get("/media/videos/999").status_code == 404


def test_missing_file_is_404(world):
    (world.storage / "videos" / world.best.result.video_path).unlink()
    assert world.client.get(f"/media/videos/{world.best.id}").status_code == 404


def test_path_outside_videos_dir_is_refused(world):
    """video_path가 어떤 이유로든 storage 밖을 가리키면 내주지 않는다."""
    secret = world.storage.parent / "secret.txt"
    secret.write_text("비밀")
    world.best.result.video_path = "../../secret.txt"
    assert world.client.get(f"/media/videos/{world.best.id}").status_code == 404


def test_range_request_is_supported(world):
    """<video>에서 원하는 지점으로 건너뛰려면 Range 요청에 206으로 답해야 한다."""
    res = world.client.get(f"/media/videos/{world.best.id}", headers={"Range": "bytes=0-9"})
    assert res.status_code == 206
    assert res.content == VIDEO_BYTES[:10]


def test_old_static_file_path_is_gone(world):
    """예전 StaticFiles 경로(파일 경로 그대로)로는 더 이상 받을 수 없다."""
    assert world.client.get(f"/media/videos/{world.latest.result.video_path}").status_code == 404
