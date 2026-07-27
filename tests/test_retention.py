"""보존 정책 검증 (ux-improvements.md §2-5-2).

모델 1건이 약 250MB라 전부 남기면 시즌 하나로 디스크가 찬다. 팀별 최고기록만 남기되,
평가에 쓰이는 중인 파일과 DB 이력은 건드리지 않아야 한다.
"""

import datetime as dt
import types

import pytest

from app.config import settings
from app.models import FinishStatus, SubmissionStatus
from app.retention import prune_team_files

BASE_TIME = dt.datetime(2026, 7, 26, 10, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def storage(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    (root / "models").mkdir(parents=True)
    (root / "videos").mkdir(parents=True)
    monkeypatch.setattr(settings, "storage_dir", root)
    return root


def make_submission(storage, sub_id, status, lap_time=None, minutes=0, with_video=True):
    """실제 파일까지 만들어 두고 그 경로를 가리키는 제출 객체를 흉내 낸다."""
    model_rel = f"models/1/1/{sub_id}.tar.gz"
    model_file = storage / model_rel
    model_file.parent.mkdir(parents=True, exist_ok=True)
    model_file.write_text("model")

    result = None
    if lap_time is not None:
        video_rel = f"1/1/{sub_id}.mp4"
        if with_video:
            video_file = storage / "videos" / video_rel
            video_file.parent.mkdir(parents=True, exist_ok=True)
            video_file.write_text("video")
        result = types.SimpleNamespace(
            finish_status=FinishStatus.FINISHED,
            lap_time_seconds=lap_time,
            video_path=video_rel,
        )

    return types.SimpleNamespace(
        id=sub_id,
        status=status,
        submitted_at=BASE_TIME + dt.timedelta(minutes=minutes),
        model_path=model_rel,
        result=result,
    )


def make_team(submissions):
    return types.SimpleNamespace(id=1, submissions=submissions)


def model_file(storage, sub_id):
    return storage / "models" / "1" / "1" / f"{sub_id}.tar.gz"


def video_file(storage, sub_id):
    return storage / "videos" / "1" / "1" / f"{sub_id}.mp4"


def test_only_best_record_files_are_kept(storage):
    slow = make_submission(storage, 1, SubmissionStatus.DONE, lap_time=120.0)
    best = make_submission(storage, 2, SubmissionStatus.DONE, lap_time=90.0, minutes=10)
    team = make_team([slow, best])

    prune_team_files(team, storage / "videos")

    assert model_file(storage, 2).is_file(), "최고기록 모델은 남아야 한다"
    assert video_file(storage, 2).is_file(), "최고기록 영상은 남아야 한다"
    assert not model_file(storage, 1).exists()
    assert not video_file(storage, 1).exists()


def test_db_records_are_kept(storage):
    """파일만 지우고 제출 이력은 남긴다 — 리더보드의 '제출 횟수'가 유지돼야 한다."""
    slow = make_submission(storage, 1, SubmissionStatus.DONE, lap_time=120.0)
    best = make_submission(storage, 2, SubmissionStatus.DONE, lap_time=90.0, minutes=10)
    team = make_team([slow, best])

    prune_team_files(team, storage / "videos")

    assert len(team.submissions) == 2
    assert slow.result is not None and slow.result.lap_time_seconds == 120.0
    assert slow.result.video_path is None, "파일을 지웠으면 경로도 비워 깨진 링크를 남기지 않는다"


def test_active_submission_files_are_never_removed(storage):
    """대기/평가중 제출의 모델을 지우면 워커가 평가할 대상을 잃는다."""
    best = make_submission(storage, 1, SubmissionStatus.DONE, lap_time=90.0)
    queued = make_submission(storage, 2, SubmissionStatus.QUEUED, minutes=10)
    running = make_submission(storage, 3, SubmissionStatus.RUNNING, minutes=20)
    team = make_team([best, queued, running])

    prune_team_files(team, storage / "videos")

    assert model_file(storage, 2).is_file()
    assert model_file(storage, 3).is_file()


def test_error_submission_files_are_removed(storage):
    """오류로 끝난 제출은 다시 쓰이지 않으므로 정리 대상이다."""
    best = make_submission(storage, 1, SubmissionStatus.DONE, lap_time=90.0)
    failed = make_submission(storage, 2, SubmissionStatus.ERROR, minutes=10)
    team = make_team([best, failed])

    prune_team_files(team, storage / "videos")

    assert not model_file(storage, 2).exists()


def test_team_without_finished_record_keeps_nothing_but_active(storage):
    """완주 기록이 없으면 남길 최고기록도 없다 — 끝난 제출 파일은 모두 정리된다."""
    timeout = make_submission(storage, 1, SubmissionStatus.DONE)
    failed = make_submission(storage, 2, SubmissionStatus.ERROR, minutes=5)
    queued = make_submission(storage, 3, SubmissionStatus.QUEUED, minutes=10)
    team = make_team([timeout, failed, queued])

    prune_team_files(team, storage / "videos")

    assert not model_file(storage, 1).exists()
    assert not model_file(storage, 2).exists()
    assert model_file(storage, 3).is_file()


def test_absolute_legacy_path_is_resolved(storage):
    """구환경에서 컨테이너 절대 경로로 저장된 레코드도 실제로 지워져야 한다
    (Phase 9-0 이전 레코드 + 상대 경로 전환 시 조용히 실패하던 버그)."""
    best = make_submission(storage, 1, SubmissionStatus.DONE, lap_time=90.0)
    stale = make_submission(storage, 2, SubmissionStatus.DONE, lap_time=120.0, minutes=10)
    stale.model_path = "/app/storage/models/1/1/2.tar.gz"
    team = make_team([best, stale])

    prune_team_files(team, storage / "videos")

    assert not model_file(storage, 2).exists()
