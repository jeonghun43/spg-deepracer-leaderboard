"""제출 파일 보존 정책 — 팀의 최고기록이 아닌 제출의 모델·영상 파일을 지운다.

모델 아카이브가 건당 250MB 안팎이라 전부 남기면 시즌 하나로 디스크가 찬다
(10팀 × 5회/일 × 2주 ≈ 175GB). 리더보드가 실제로 쓰는 것은 팀별 최고기록 하나뿐이므로,
그 외 파일은 평가가 끝나는 즉시 지운다 (ux-improvements.md §2-5-2).

**지우는 것은 파일뿐이고 DB 레코드는 남긴다** — 리더보드의 "제출 횟수"와 이력이 그대로여야 한다.
"""

import logging
from pathlib import Path

from app.config import settings
from app.models import ACTIVE_SUBMISSION_STATUSES, Submission, Team
from app.records import get_team_best
from app.storage_paths import resolve_storage_path

logger = logging.getLogger(__name__)


def _remove(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        path.unlink()
        return True
    except OSError:
        logger.warning("파일 삭제 실패: %s", path)
        return False


def remove_submission_files(submission: Submission, videos_dir: Path | None = None) -> int:
    """제출 1건의 모델·영상 파일을 지우고 지운 개수를 돌려준다.

    영상은 지운 뒤 `video_path`를 비운다 — 파일이 없는데 경로만 남으면 나중에 깨진 링크가 된다.
    모델 경로(`model_path`)는 이력 확인용으로 그대로 둔다(참조하는 화면이 없다).
    """
    videos_dir = videos_dir or settings.videos_dir
    removed = 0

    if submission.model_path:
        removed += int(_remove(resolve_storage_path(submission.model_path)))

    result = submission.result
    if result is not None and result.video_path:
        if _remove(videos_dir / result.video_path):
            removed += 1
        result.video_path = None

    return removed


def prune_team_files(team: Team, videos_dir: Path | None = None) -> int:
    """팀의 최고기록 제출만 남기고 나머지 제출의 파일을 지운다.

    아직 평가에 쓰이는 중인 대기/평가중 제출은 건드리지 않는다 — 그 모델 파일을 지우면
    워커가 평가할 대상을 잃는다.
    """
    best_submission, _ = get_team_best(team)
    removed = 0

    for submission in team.submissions:
        if best_submission is not None and submission.id == best_submission.id:
            continue
        if submission.status.value in ACTIVE_SUBMISSION_STATUSES:
            continue
        removed += remove_submission_files(submission, videos_dir)

    if removed:
        logger.info("보존 정책 적용: team=%s 파일 %d개 삭제", team.id, removed)
    return removed
