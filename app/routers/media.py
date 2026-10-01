"""평가 영상 제공 — 최고기록 영상은 누구나, 그 외 영상은 그 팀만 본다.

예전에는 `storage/videos`를 StaticFiles로 통째로 공개했다. 남는 영상이 팀별 최고기록뿐이라
문제가 없었지만, 직전 제출 영상도 남기게 되면서(app/retention.py) 그대로 두면 다른 팀의
실패 주행을 볼 수 있게 된다. 경로가 `{시즌}/{팀}/{제출}.mp4`라 번호만 바꿔 넣으면 추측도 된다.
그래서 제출 id로 받아 권한을 확인한 뒤 파일을 내준다.

URL 접두어 `/media/videos`는 예전 마운트와 같게 유지했다 — Caddy와 문서가 그대로 맞는다.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import get_current_team_optional
from app.models import Submission, Team
from app.records import get_team_best

router = APIRouter(tags=["media"])


def can_view_video(submission: Submission, viewer: Team | None) -> bool:
    """이 제출의 영상을 볼 수 있는가. 최고기록이면 공개, 아니면 소유 팀만."""
    if viewer is not None and viewer.id == submission.team_id:
        return True
    best_submission, _ = get_team_best(submission.team)
    return best_submission is not None and best_submission.id == submission.id


@router.get("/media/videos/{submission_id}")
def get_video(
    submission_id: int,
    viewer: Team | None = Depends(get_current_team_optional),
    db: Session = Depends(get_db),
):
    # 권한이 없을 때도 "없음"과 똑같이 404를 준다 — 403이면 그 제출에 영상이 있다는 사실이 드러난다.
    not_found = HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    submission = db.get(Submission, submission_id)
    if submission is None or submission.result is None or not submission.result.video_path:
        raise not_found
    if not can_view_video(submission, viewer):
        raise not_found

    videos_dir = settings.videos_dir.resolve()
    path = (videos_dir / submission.result.video_path).resolve()
    # video_path는 서버가 만든 값이지만, DB 값 하나로 storage 밖 파일을 내주게 두지 않는다.
    if not path.is_relative_to(videos_dir) or not path.is_file():
        raise not_found

    # FileResponse는 Range 요청을 처리한다 — <video>에서 원하는 지점으로 건너뛰려면 필요하다.
    return FileResponse(path, media_type="video/mp4")
