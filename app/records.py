"""팀의 '최고기록'을 계산하는 공용 로직 — 리더보드 조회와 시즌 아카이브에서 함께 쓴다.

규모(시즌당 약 10팀)가 작아 별도 캐시 테이블 없이 즉시 계산한다 (plan.md §4).
"""

from app.models import FinishStatus, Submission, SubmissionStatus, Team


def get_team_best(team: Team) -> tuple[Submission | None, "EvaluationResult | None"]:  # noqa: F821
    best_submission: Submission | None = None
    best_result = None

    for submission in team.submissions:
        if submission.status != SubmissionStatus.DONE or submission.result is None:
            continue
        result = submission.result
        if result.finish_status != FinishStatus.FINISHED:
            continue

        is_better = best_result is None or (
            result.lap_time_seconds < best_result.lap_time_seconds
            or (
                result.lap_time_seconds == best_result.lap_time_seconds
                and submission.submitted_at < best_submission.submitted_at
            )
        )
        if is_better:
            best_submission = submission
            best_result = result

    return best_submission, best_result


def get_latest_done_submission(team: Team) -> Submission | None:
    """평가가 끝난(DONE) 가장 최근 제출. 완주 여부는 따지지 않는다.

    참가자 제출 탭의 "직전 주행 영상"이 가리키는 제출이다. 대기/평가 중이거나 오류로
    끝난 제출은 영상이 없으므로 건너뛰고, 그 이전 완료분을 계속 보여준다.
    """
    done = [
        s for s in team.submissions if s.status == SubmissionStatus.DONE and s.result is not None
    ]
    if not done:
        return None
    return max(done, key=lambda s: s.submitted_at)
