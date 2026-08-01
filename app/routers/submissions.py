import datetime as dt

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.requests import ClientDisconnect
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import get_current_team
from app.models import Season, SeasonStatus, Submission, SubmissionStatus, Team
from app.quota import get_remaining_submissions, has_active_submission, today_kst
from app.render import templates
from app.storage_paths import to_storage_relative
from app.worker_status import get_worker_status

router = APIRouter(tags=["submissions"])


def wants_json_response(accept_header: str | None) -> bool:
    """이 요청에 JSON으로 답해야 하는가 (upload-progress-ux.md §2-4).

    진행률을 표시하는 업로드 스크립트는 `Accept: application/json`을 명시해서 보낸다.
    그 외(일반 폼 전송, `*/*`만 보내는 클라이언트)는 지금까지와 똑같이 303 리다이렉트를
    받는다 — 스크립트가 없어도 제출이 되어야 하기 때문이다.
    """
    if not accept_header:
        return False
    return any(
        part.split(";", 1)[0].strip().lower() == "application/json"
        for part in accept_header.split(",")
    )


def _queue_position(db: Session, submission: Submission) -> int:
    """이 제출 앞에 몇 건이 대기/평가 중인지."""
    stmt = select(func.count()).where(
        Submission.status.in_([SubmissionStatus.QUEUED, SubmissionStatus.RUNNING]),
        Submission.submitted_at < submission.submitted_at,
    )
    return db.execute(stmt).scalar_one()


@router.get("/submit")
def submit_form(request: Request, team: Team = Depends(get_current_team), db: Session = Depends(get_db)):
    season = team.season
    active_submission = has_active_submission(db, team)
    latest_submission = db.execute(
        select(Submission)
        .where(Submission.team_id == team.id)
        .order_by(Submission.submitted_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    queue_position = _queue_position(db, active_submission) if active_submission else None
    # 평가는 한 번에 한 건씩 순차 처리되므로, 앞선 건들 + 자기 건을 곱해 대략적인 대기 시간을 낸다.
    estimated_wait_minutes = (
        (queue_position + 1) * settings.eval_minutes_estimate if queue_position is not None else None
    )
    remaining = get_remaining_submissions(db, team)
    can_submit = (
        season.status == SeasonStatus.ACTIVE
        and active_submission is None
        and remaining > 0
        and not team.disqualified
    )
    return templates.TemplateResponse(
        request,
        "submit.html",
        {
            "team": team,
            "season": season,
            "active_submission": active_submission,
            "latest_submission": latest_submission,
            "queue_position": queue_position,
            "estimated_wait_minutes": estimated_wait_minutes,
            "remaining": remaining,
            "daily_limit": settings.daily_submission_limit,
            "can_submit": can_submit,
            "eval_laps": settings.online_eval_laps,
            "worker_status": get_worker_status(db),
            # 업로드 스크립트가 전송 전에 파일을 검사할 때 쓰는 값. 규칙의 출처는
            # config.py 하나로 유지하려고 화면에 내려보낸다 (upload-progress-ux.md §2-5).
            "upload_max_bytes": settings.model_upload_max_bytes,
            "allowed_extensions": settings.model_upload_allowed_extensions,
            "error": request.query_params.get("error"),
        },
    )


@router.post("/submit")
async def submit_upload(
    request: Request,
    team: Team = Depends(get_current_team),
    db: Session = Depends(get_db),
    model_file: UploadFile | None = File(None),
):
    season: Season = team.season
    as_json = wants_json_response(request.headers.get("accept"))

    def redirect_with_error(message: str) -> JSONResponse | RedirectResponse:
        # 스크립트로 올린 경우엔 화면을 갈아끼우지 않고 그 자리에서 안내한다.
        # 리다이렉트로 답하면 고른 파일이 사라져 참가자가 처음부터 다시 해야 한다.
        if as_json:
            return JSONResponse({"ok": False, "error": message}, status_code=400)
        return RedirectResponse(f"/submit?error={message}", status_code=303)

    if team.disqualified:
        return redirect_with_error("실격 처리된 팀은 제출할 수 없습니다.")
    if season.status != SeasonStatus.ACTIVE:
        return redirect_with_error("현재 제출을 받지 않는 대회입니다.")
    if has_active_submission(db, team) is not None:
        return redirect_with_error(
            "이전 제출의 결과가 아직 나오지 않았습니다. 결과가 확정된 후 다시 업로드해주세요."
        )
    if get_remaining_submissions(db, team) <= 0:
        return redirect_with_error(f"오늘 제출 한도({settings.daily_submission_limit}회)를 모두 사용했습니다.")
    if model_file is None or not model_file.filename:
        return redirect_with_error("업로드할 모델 파일을 선택해주세요.")

    filename_lower = model_file.filename.lower()
    if not filename_lower.endswith(settings.model_upload_allowed_extensions):
        allowed = ", ".join(settings.model_upload_allowed_extensions)
        return redirect_with_error(f"허용되지 않는 파일 형식입니다 (허용: {allowed}).")

    dest_dir = settings.models_dir / str(season.id) / str(team.id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
    dest_path = dest_dir / f"{timestamp}_{model_file.filename}"

    size = 0
    try:
        with open(dest_path, "wb") as out:
            while chunk := await model_file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.model_upload_max_bytes:
                    out.close()
                    dest_path.unlink(missing_ok=True)
                    max_mb = settings.model_upload_max_bytes // (1024 * 1024)
                    return redirect_with_error(f"파일 용량이 너무 큽니다 (최대 {max_mb}MB).")
                out.write(chunk)
    except ClientDisconnect:
        # 참가자가 업로드 도중 취소하거나 창을 닫으면 여기로 온다. 절반짜리 파일을
        # 남겨두면 아무도 쓰지 않으면서 디스크만 차지한다 (250MB짜리다).
        dest_path.unlink(missing_ok=True)
        raise

    if size == 0:
        dest_path.unlink(missing_ok=True)
        return redirect_with_error("빈 파일은 업로드할 수 없습니다.")

    submission = Submission(
        team_id=team.id,
        # 절대 경로가 아니라 storage_dir 기준 상대 경로로 적는다 — 웹은 컨테이너 안,
        # 워커는 호스트에서 도는데 같은 파일의 절대 경로가 서로 달라서다 (storage_paths 참고).
        model_path=to_storage_relative(dest_path),
        status=SubmissionStatus.QUEUED,
    )
    db.add(submission)
    db.commit()

    if as_json:
        return JSONResponse({"ok": True, "redirect": "/submit"})
    return RedirectResponse("/submit", status_code=303)
