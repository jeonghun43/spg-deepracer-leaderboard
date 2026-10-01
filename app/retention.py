"""제출 파일 보존 정책 — 팀의 최고기록이 아닌 제출의 모델·영상 파일을 지운다.

모델 아카이브가 건당 250MB 안팎이라 전부 남기면 시즌 하나로 디스크가 찬다
(10팀 × 3회/일 × 2주 ≈ 105GB). 리더보드가 실제로 쓰는 것은 팀별 최고기록 하나뿐이므로,
그 외 파일은 평가가 끝나는 즉시 지운다 (ux-improvements.md §2-5-2).

**예외: 팀의 직전 완료 제출(latest DONE)의 영상은 남긴다.** 최고기록 영상만 남기던 시절에는
완주 못 한 제출의 영상이 업로드 몇 초 뒤 지워져서, 참가자가 받는 정보가 "시간·실패 사유"뿐이었다.
어디서 탈선하는지 볼 수 없으니 보상 함수를 고칠 근거가 없었다. 영상은 모델보다 훨씬 작고
팀당 하나만 더 남기는 것이라 디스크 정책의 취지(250MB 모델 정리)는 그대로다.
그 제출의 **모델 파일은 여전히 지운다.**

**지우는 것은 파일뿐이고 DB 레코드는 남긴다** — 리더보드의 "제출 횟수"와 이력이 그대로여야 한다.
"""

import logging
from pathlib import Path

from app.config import settings
from app.models import ACTIVE_SUBMISSION_STATUSES, Submission, Team
from app.records import get_latest_done_submission, get_team_best
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


def remove_submission_files(
    submission: Submission, videos_dir: Path | None = None, keep_video: bool = False
) -> int:
    """제출 1건의 모델·영상 파일을 지우고 지운 개수를 돌려준다.

    영상은 지운 뒤 `video_path`를 비운다 — 파일이 없는데 경로만 남으면 나중에 깨진 링크가 된다.
    모델 경로(`model_path`)는 이력 확인용으로 그대로 둔다(참조하는 화면이 없다).
    `keep_video=True`면 모델만 지운다 (직전 제출 영상 보존용).
    """
    videos_dir = videos_dir or settings.videos_dir
    removed = 0

    if submission.model_path:
        removed += int(_remove(resolve_storage_path(submission.model_path)))

    result = submission.result
    if not keep_video and result is not None and result.video_path:
        if _remove(videos_dir / result.video_path):
            removed += 1
        result.video_path = None

    return removed


def prune_team_files(
    team: Team, videos_dir: Path | None = None, keep_latest_video: bool = True
) -> int:
    """팀의 최고기록 제출만 남기고 나머지 제출의 파일을 지운다.

    아직 평가에 쓰이는 중인 대기/평가중 제출은 건드리지 않는다 — 그 모델 파일을 지우면
    워커가 평가할 대상을 잃는다.

    `keep_latest_video=True`(평가 직후 정리)면 직전 완료 제출의 영상은 남긴다. 시즌 아카이브는
    False로 불러 최고기록만 남긴다 — 대회가 끝나면 더 볼 사람이 없다.
    """
    best_submission, _ = get_team_best(team)
    latest_done = get_latest_done_submission(team) if keep_latest_video else None
    removed = 0

    for submission in team.submissions:
        if best_submission is not None and submission.id == best_submission.id:
            continue
        if submission.status.value in ACTIVE_SUBMISSION_STATUSES:
            continue
        keep_video = latest_done is not None and submission.id == latest_done.id
        removed += remove_submission_files(submission, videos_dir, keep_video=keep_video)

    if removed:
        logger.info("보존 정책 적용: team=%s 파일 %d개 삭제", team.id, removed)
    return removed
