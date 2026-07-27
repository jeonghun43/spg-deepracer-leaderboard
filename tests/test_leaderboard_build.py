"""리더보드 전체 구성(순위 정렬·미완주 분리·제출 횟수) 검증.

DB 없이 build_leaderboard의 정렬/분류 규칙만 확인하기 위해, Session.execute가
팀 목록을 돌려주도록 최소한만 흉내 낸다.
"""

import datetime as dt
import types

from app.models import FinishStatus, SubmissionStatus
from app.routers.leaderboard import build_leaderboard

BASE_TIME = dt.datetime(2026, 7, 24, 10, 0, tzinfo=dt.timezone.utc)


def make_submission(submitted_at, status, finish_status=None, lap_time=None, video_path=None):
    result = None
    if finish_status is not None:
        result = types.SimpleNamespace(
            finish_status=finish_status,
            lap_time_seconds=lap_time,
            video_path=video_path,
        )
    return types.SimpleNamespace(
        id=id(result),
        submitted_at=submitted_at,
        status=status,
        result=result,
    )


def make_team(name, submissions):
    return types.SimpleNamespace(name=name, submissions=submissions, disqualified=False)


class FakeDB:
    """build_leaderboard가 쓰는 db.execute(...).scalars().all()만 흉내 낸다."""

    def __init__(self, teams):
        self._teams = teams

    def execute(self, _stmt):
        teams = self._teams
        return types.SimpleNamespace(scalars=lambda: types.SimpleNamespace(all=lambda: teams))


def build(teams):
    season = types.SimpleNamespace(id=1)
    return build_leaderboard(FakeDB(teams), season)


def test_fastest_team_ranks_first():
    teams = [
        make_team("느린팀", [make_submission(BASE_TIME, SubmissionStatus.DONE, FinishStatus.FINISHED, 150.0)]),
        make_team("빠른팀", [make_submission(BASE_TIME, SubmissionStatus.DONE, FinishStatus.FINISHED, 90.0)]),
        make_team("중간팀", [make_submission(BASE_TIME, SubmissionStatus.DONE, FinishStatus.FINISHED, 120.0)]),
    ]
    ranked, _ = build(teams)
    assert [row["team"].name for row in ranked] == ["빠른팀", "중간팀", "느린팀"]


def test_tie_across_teams_prefers_earlier_record():
    """랩타임이 같으면 그 기록을 먼저 세운 팀이 상위 (plan.md §5.2)."""
    late = make_team("늦게세운팀", [
        make_submission(BASE_TIME + dt.timedelta(hours=5), SubmissionStatus.DONE, FinishStatus.FINISHED, 100.0)
    ])
    early = make_team("먼저세운팀", [
        make_submission(BASE_TIME, SubmissionStatus.DONE, FinishStatus.FINISHED, 100.0)
    ])
    # DB가 늦게 세운 팀을 먼저 돌려줘도 순위는 뒤집혀야 한다.
    ranked, _ = build([late, early])
    assert [row["team"].name for row in ranked] == ["먼저세운팀", "늦게세운팀"]


def test_teams_without_finished_runs_are_unranked():
    teams = [
        make_team("완주팀", [make_submission(BASE_TIME, SubmissionStatus.DONE, FinishStatus.FINISHED, 110.0)]),
        make_team("미완주팀", [make_submission(BASE_TIME, SubmissionStatus.DONE, FinishStatus.TIMEOUT, None)]),
        make_team("제출없는팀", []),
    ]
    ranked, unranked = build(teams)
    assert [row["team"].name for row in ranked] == ["완주팀"]
    assert {row["team"].name for row in unranked} == {"미완주팀", "제출없는팀"}


def test_submission_count_excludes_errors():
    team = make_team("팀", [
        make_submission(BASE_TIME, SubmissionStatus.DONE, FinishStatus.FINISHED, 100.0),
        make_submission(BASE_TIME + dt.timedelta(hours=1), SubmissionStatus.DONE, FinishStatus.TIMEOUT, None),
        make_submission(BASE_TIME + dt.timedelta(hours=2), SubmissionStatus.ERROR),
        make_submission(BASE_TIME + dt.timedelta(hours=3), SubmissionStatus.QUEUED),
    ])
    ranked, _ = build([team])
    assert ranked[0]["total_submissions"] == 2


def test_video_url_uses_media_path():
    team = make_team("팀", [
        make_submission(BASE_TIME, SubmissionStatus.DONE, FinishStatus.FINISHED, 100.0, video_path="1/2/3.mp4")
    ])
    ranked, _ = build([team])
    assert ranked[0]["video_url"] == "/media/videos/1/2/3.mp4"


def test_missing_video_yields_no_url():
    team = make_team("팀", [
        make_submission(BASE_TIME, SubmissionStatus.DONE, FinishStatus.FINISHED, 100.0, video_path=None)
    ])
    ranked, _ = build([team])
    assert ranked[0]["video_url"] is None
