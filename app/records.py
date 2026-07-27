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
