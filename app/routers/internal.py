"""워커 전용 엔드포인트 — 웹과 워커가 다른 기기에서 돌 때 파일을 주고받는 통로.

웹은 클라우드, 워커는 운영자 노트북에서 도는 구성(cloud-migration.md §4)에서는 둘이 디스크를
공유하지 않는다. 워커는 여기서 모델을 내려받아 평가하고, 결과 영상을 다시 올린다.

**인증**: `X-Worker-Token` 헤더를 서버의 `WORKER_TOKEN`과 상수 시간 비교한다. 실패하면 403이
아니라 404를 준다 — 어떤 제출이 존재하는지조차 알려주지 않기 위해서다. 사설망(Tailscale) 안에서만
접근하는 것이 정상이지만, 사설망을 유일한 방어선으로 삼지 않는다.
"""

import secrets

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Submission
from app.retention import prune_team_files
from app.storage_paths import resolve_storage_path

router = APIRouter(prefix="/internal", tags=["internal"])

# 인증 실패와 "없는 제출"을 같은 응답으로 돌려준다 (정보 노출 방지).
NOT_FOUND = HTTPException(status_code=404, detail="Not Found")
# metrics json은 정상이면 1KB 안팎이다. 넉넉히 잡되 무제한은 두지 않는다.
MAX_METRICS_BYTES = 5 * 1024 * 1024


def require_worker(x_worker_token: str | None = Header(default=None)) -> None:
    expected = settings.worker_token
    # 토큰이 설정되지 않은 배포(웹·워커가 같은 기기)에서는 이 경로 자체를 열지 않는다.
    if not expected or not x_worker_token:
        raise NOT_FOUND
    if not secrets.compare_digest(x_worker_token, expected):
        raise NOT_FOUND


@router.get("/submissions/{submission_id}/model")
def download_model(
    submission_id: int,
    _: None = Depends(require_worker),
    db: Session = Depends(get_db),
):
    """워커가 평가할 모델 아카이브를 내려받는다 (수백 MB이므로 스트리밍 응답)."""
    submission = db.get(Submission, submission_id)
    if submission is None or not submission.model_path:
        raise NOT_FOUND

    path = resolve_storage_path(submission.model_path)
    if not path.is_file():
        raise NOT_FOUND
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


@router.post("/submissions/{submission_id}/video")
async def upload_video(
    submission_id: int,
    video: UploadFile = File(...),
    _: None = Depends(require_worker),
    db: Session = Depends(get_db),
):
    """평가 결과 영상을 받아 저장하고 `EvaluationResult.video_path`를 채운다.

    영상은 순위에 영향을 주지 않는 부가 정보라, 평가 결과가 먼저 저장된 뒤에 올라온다.
    """
    submission = db.get(Submission, submission_id)
    if submission is None or submission.result is None:
        raise NOT_FOUND

    rel_path = f"{submission.team.season_id}/{submission.team_id}/{submission.id}.mp4"
    dest_path = settings.videos_dir / rel_path
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    size = 0
    with open(dest_path, "wb") as out:
        while chunk := await video.read(1024 * 1024):
            size += len(chunk)
            if size > settings.video_upload_max_bytes:
                out.close()
                dest_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="video too large")
            out.write(chunk)

    if size == 0:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="empty video")

    submission.result.video_path = rel_path
    db.commit()
    return {"video_path": rel_path, "bytes": size}


@router.post("/submissions/{submission_id}/metrics")
async def upload_metrics(
    submission_id: int,
    metrics: UploadFile = File(...),
    _: None = Depends(require_worker),
    db: Session = Depends(get_db),
):
    """DRFC가 만든 원본 metrics json을 서버에 보관한다.

    MinIO의 원본은 다음 평가 때 같은 키로 덮어써지므로 이 사본이 유일한 기록이다.
    워커(노트북)에만 두면 백업 대상에서 빠지므로 서버로 올려 함께 백업되게 한다.
    """
    submission = db.get(Submission, submission_id)
    if submission is None:
        raise NOT_FOUND

    rel_path = f"{submission.team.season_id}/{submission.team_id}/{submission.id}.json"
    dest_path = settings.metrics_dir / rel_path
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    content = await metrics.read()
    if not content or len(content) > MAX_METRICS_BYTES:
        raise HTTPException(status_code=400, detail="invalid metrics size")
    dest_path.write_bytes(content)
    return {"metrics_path": rel_path, "bytes": len(content)}


@router.post("/submissions/{submission_id}/prune")
def prune_submission_team(
    submission_id: int,
    _: None = Depends(require_worker),
    db: Session = Depends(get_db),
):
    """이 제출이 속한 팀의 파일을 보존 정책대로 정리한다 (최고기록만 남긴다).

    파일은 서버에 있으므로 정리도 서버에서 해야 한다. 워커가 자기 디스크를 지워봐야
    서버의 250MB짜리 모델은 그대로 쌓인다 (2026-07-30 실제 발생).
    """
    submission = db.get(Submission, submission_id)
    if submission is None:
        raise NOT_FOUND

    removed = prune_team_files(submission.team)
    if removed:
        db.commit()
    return {"removed": removed}
