"""팀별 하루 제출 한도 계산 (spec.md §8, plan.md §5.3).

카운트 기준: "완주 성공 여부"가 아니라 "평가가 끝까지 정상 실행됐는지"다.
DONE 상태(완주든 미완주-타임아웃이든)만 카운트하고, ERROR(업로드 실패·DRFC 실행
오류)는 카운트에서 제외한다. 하루는 한국 시간(KST) 자정 기준으로 리셋된다.
"""

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import KST, settings
from app.models import ACTIVE_SUBMISSION_STATUSES, Submission, SubmissionStatus, Team


def today_kst() -> dt.date:
    return dt.datetime.now(tz=KST).date()


def get_daily_done_count(db: Session, team: Team, on_date: dt.date | None = None) -> int:
    on_date = on_date or today_kst()

    day_start = dt.datetime.combine(on_date, dt.time.min, tzinfo=KST)
    day_end = day_start + dt.timedelta(days=1)
    stmt = select(func.count()).where(
        Submission.team_id == team.id,
        Submission.status == SubmissionStatus.DONE,
        Submission.finished_at >= day_start,
        Submission.finished_at < day_end,
    )
    done_count = db.execute(stmt).scalar_one()

    # 관리자가 오늘 카운트를 조정해둔 경우 그 보정값을 더한다. 보정 이후에 완료되는 평가도
    # 그대로 누적되므로 한도는 계속 정상 동작한다.
    if team.daily_count_adjustment is not None and team.daily_count_adjustment_date == on_date:
        done_count += team.daily_count_adjustment
    return max(done_count, 0)


def get_remaining_submissions(db: Session, team: Team) -> int:
    used = get_daily_done_count(db, team)
    return max(settings.daily_submission_limit - used, 0)


def has_active_submission(db: Session, team: Team) -> Submission | None:
    """이 팀이 현재 '대기' 또는 '평가중' 상태의 제출을 갖고 있는지 확인한다.

    새 모델은 이전 제출이 완료(DONE) 또는 오류(ERROR)로 끝나야만 업로드할 수 있다.
    """
    stmt = select(Submission).where(
        Submission.team_id == team.id,
        Submission.status.in_(ACTIVE_SUBMISSION_STATUSES),
    )
    return db.execute(stmt).scalar_one_or_none()
