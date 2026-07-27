"""리더보드 순위 산정 규칙 검증 (plan.md §5.2).

- 완주 기록만 순위에 오르고, 랩타임 오름차순(가장 빠른 팀이 1위)
- 동점이면 먼저 그 기록을 세운(제출 시각이 빠른) 팀이 상위
- 미완주 결과는 최고기록 갱신에 영향을 주지 않음
"""

import datetime as dt
import types

from app.models import FinishStatus, SubmissionStatus
from app.records import get_team_best


def make_submission(submission_id, submitted_at, status, finish_status=None, lap_time=None):
    result = None
    if finish_status is not None:
        result = types.SimpleNamespace(
            finish_status=finish_status,
            lap_time_seconds=lap_time,
        )
    return types.SimpleNamespace(
        id=submission_id,
        submitted_at=submitted_at,
        status=status,
        result=result,
    )


BASE_TIME = dt.datetime(2026, 7, 24, 10, 0, tzinfo=dt.timezone.utc)


def make_team(submissions):
    return types.SimpleNamespace(submissions=submissions)


def test_picks_fastest_finished_lap():
    team = make_team([
        make_submission(1, BASE_TIME, SubmissionStatus.DONE, FinishStatus.FINISHED, 120.0),
        make_submission(2, BASE_TIME + dt.timedelta(hours=1), SubmissionStatus.DONE, FinishStatus.FINISHED, 95.5),
        make_submission(3, BASE_TIME + dt.timedelta(hours=2), SubmissionStatus.DONE, FinishStatus.FINISHED, 110.0),
    ])
    best_submission, best_result = get_team_best(team)
    assert best_submission.id == 2
    assert best_result.lap_time_seconds == 95.5


def test_timeout_results_never_become_best():
    team = make_team([
        make_submission(1, BASE_TIME, SubmissionStatus.DONE, FinishStatus.TIMEOUT, None),
        make_submission(2, BASE_TIME + dt.timedelta(hours=1), SubmissionStatus.DONE, FinishStatus.FINISHED, 130.0),
    ])
    best_submission, best_result = get_team_best(team)
    assert best_submission.id == 2
    assert best_result.finish_status == FinishStatus.FINISHED


def test_team_with_only_timeouts_has_no_best():
    team = make_team([
        make_submission(1, BASE_TIME, SubmissionStatus.DONE, FinishStatus.TIMEOUT, None),
    ])
    best_submission, best_result = get_team_best(team)
    assert best_submission is None
    assert best_result is None


def test_tie_prefers_earlier_submission():
    team = make_team([
        make_submission(1, BASE_TIME + dt.timedelta(hours=2), SubmissionStatus.DONE, FinishStatus.FINISHED, 100.0),
        make_submission(2, BASE_TIME, SubmissionStatus.DONE, FinishStatus.FINISHED, 100.0),
    ])
    best_submission, _ = get_team_best(team)
    assert best_submission.id == 2


def test_queued_and_error_submissions_are_ignored():
    team = make_team([
        make_submission(1, BASE_TIME, SubmissionStatus.QUEUED),
        make_submission(2, BASE_TIME + dt.timedelta(hours=1), SubmissionStatus.ERROR),
        make_submission(3, BASE_TIME + dt.timedelta(hours=2), SubmissionStatus.DONE, FinishStatus.FINISHED, 105.0),
    ])
    best_submission, best_result = get_team_best(team)
    assert best_submission.id == 3
    assert best_result.lap_time_seconds == 105.0
