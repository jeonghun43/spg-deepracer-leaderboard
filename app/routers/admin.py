import datetime as dt

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import get_current_admin
from app.models import (
    Account,
    AdminAccount,
    Season,
    SeasonStatus,
    Submission,
    SubmissionStatus,
    Team,
)
from app.quota import get_daily_done_count, today_kst
from app.render import templates
from app.season_archive import archive_season
from app.security import generate_password, hash_password, verify_password

router = APIRouter(prefix="/admin", tags=["admin"])

NEXT_STATUS: dict[SeasonStatus, SeasonStatus] = {
    SeasonStatus.PREPARING: SeasonStatus.ACTIVE,
    SeasonStatus.ACTIVE: SeasonStatus.CLOSED,
    SeasonStatus.CLOSED: SeasonStatus.ARCHIVED,
}
# 한 번에 등록할 수 있는 팀 수 상한. 실수로 큰 목록을 붙여넣는 것을 막기 위한 값이며,
# 비밀번호 해시(bcrypt)가 팀당 수백 ms라 이 정도가 응답 시간 측면에서도 상한이다.
MAX_BULK_TEAMS = 50
STATUS_LABELS = {
    SeasonStatus.PREPARING: "준비중",
    SeasonStatus.ACTIVE: "진행중 (제출 가능)",
    SeasonStatus.CLOSED: "마감 (제출 종료, 순위 확정)",
    SeasonStatus.ARCHIVED: "아카이브됨",
}


# ── 로그인 ──────────────────────────────────────────────────────────────


@router.get("/login")
def admin_login_form(request: Request):
    if request.session.get("admin_id"):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(request, "admin/login.html", {"error": None})


@router.post("/login")
def admin_login_submit(
    request: Request,
    login_id: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    admin = db.execute(
        select(AdminAccount).where(AdminAccount.login_id == login_id)
    ).scalar_one_or_none()
    if admin is None or not verify_password(password, admin.password_hash):
        return templates.TemplateResponse(
            request, "admin/login.html", {"error": "아이디 또는 비밀번호가 올바르지 않습니다."}, status_code=401
        )
    request.session.clear()
    request.session["admin_id"] = admin.id
    return RedirectResponse("/admin", status_code=303)


@router.get("/logout")
def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=303)


# ── 대시보드 / 시즌 ──────────────────────────────────────────────────────


@router.get("")
def dashboard(request: Request, admin: AdminAccount = Depends(get_current_admin), db: Session = Depends(get_db)):
    seasons = db.execute(select(Season).order_by(Season.start_date.desc())).scalars().all()
    return templates.TemplateResponse(request, "admin/dashboard.html", {"seasons": seasons})


@router.get("/seasons/new")
def new_season_form(request: Request, admin: AdminAccount = Depends(get_current_admin)):
    return templates.TemplateResponse(request, "admin/season_new.html", {"error": None})


@router.post("/seasons/new")
def new_season_submit(
    request: Request,
    name: str = Form(...),
    track_name: str = Form(...),
    start_date: dt.date = Form(...),
    end_date: dt.date = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    season = Season(
        name=name,
        track_name=track_name,
        start_date=start_date,
        end_date=end_date,
        status=SeasonStatus.PREPARING,
    )
    db.add(season)
    db.commit()
    return RedirectResponse(f"/admin/seasons/{season.id}", status_code=303)


def render_season_detail(
    request: Request,
    season: Season,
    db: Session,
    issued: list[dict] | None = None,
    skipped: list[dict] | None = None,
    bulk_error: str | None = None,
    issued_kind: str = "new",
):
    """시즌 관리 화면 렌더. 팀 일괄 등록/재발급 결과(issued)는 POST 응답에서 바로 넘긴다 —
    비밀번호를 URL이나 쿠키에 실어 보내지 않기 위해서다 (ux-improvements.md §2-2)."""
    submissions = db.execute(
        select(Submission)
        .join(Team)
        .where(Team.season_id == season.id)
        .order_by(Submission.submitted_at.desc())
        .limit(100)
    ).scalars().all()
    return templates.TemplateResponse(
        request,
        "admin/season_detail.html",
        {
            "season": season,
            "teams": season.teams,
            "daily_counts": {team.id: get_daily_done_count(db, team) for team in season.teams},
            "daily_limit": settings.daily_submission_limit,
            "submissions": submissions,
            "next_status": NEXT_STATUS.get(season.status),
            "status_labels": STATUS_LABELS,
            "issued": issued or [],
            "issued_kind": issued_kind,
            "skipped": skipped or [],
            "bulk_error": bulk_error,
            "max_bulk_teams": MAX_BULK_TEAMS,
        },
    )


@router.get("/seasons/{season_id}")
def season_detail(
    season_id: int,
    request: Request,
    admin: AdminAccount = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    season = db.get(Season, season_id)
    if season is None:
        return RedirectResponse("/admin", status_code=303)
    return render_season_detail(request, season, db)


@router.post("/seasons/{season_id}/advance-status")
def advance_status(
    season_id: int,
    admin: AdminAccount = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    season = db.get(Season, season_id)
    if season is None:
        return RedirectResponse("/admin", status_code=303)
    next_status = NEXT_STATUS.get(season.status)
    if next_status == SeasonStatus.ARCHIVED:
        archive_season(db, season, settings.videos_dir)
    elif next_status is not None:
        season.status = next_status
        db.commit()
    return RedirectResponse(f"/admin/seasons/{season_id}", status_code=303)


# ── 팀 등록 / 관리 ──────────────────────────────────────────────────────


def parse_team_names(raw: str) -> list[str]:
    """여러 줄 입력을 팀 이름 목록으로 바꾼다.

    줄바꿈이 기본 구분자이고, 쉼표·탭도 구분자로 인정한다 (엑셀/시트에서 한 행을
    그대로 붙여넣는 경우 대응). 앞뒤 공백과 빈 줄은 버리고, 입력 안에서 중복된
    이름은 처음 것만 남긴다.
    """
    names: list[str] = []
    seen: set[str] = set()
    for chunk in raw.replace(",", "\n").replace("\t", "\n").splitlines():
        name = chunk.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


@router.post("/seasons/{season_id}/teams/new")
def register_teams(
    season_id: int,
    request: Request,
    team_names: str = Form(""),
    admin: AdminAccount = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """팀 여러 개를 한 번에 등록하고, 발급된 계정을 이 응답에서 바로 표로 보여준다.

    비밀번호는 해시로만 저장돼 나중에 다시 볼 수 없으므로, 계정을 만든 바로 그 응답에
    실어 보낸다 (리다이렉트로 넘기면 중간에 유실될 여지가 있다 — ux-improvements.md §2-2).
    """
    season = db.get(Season, season_id)
    if season is None:
        return RedirectResponse("/admin", status_code=303)

    names = parse_team_names(team_names)
    if not names:
        return render_season_detail(
            request, season, db, bulk_error="등록할 팀 이름을 한 줄에 하나씩 입력해주세요."
        )
    if len(names) > MAX_BULK_TEAMS:
        return render_season_detail(
            request,
            season,
            db,
            bulk_error=(
                f"한 번에 최대 {MAX_BULK_TEAMS}팀까지 등록할 수 있습니다 "
                f"(입력한 팀: {len(names)}개). 나눠서 등록해주세요."
            ),
        )

    # 팀명은 시즌 안에서 유일해야 한다(uq_team_season_name). 중복 한 건 때문에
    # 트랜잭션 전체가 깨지지 않도록 DB 예외에 기대지 않고 미리 걸러낸다.
    existing_names = {team.name for team in season.teams}
    issued: list[dict] = []
    skipped: list[dict] = []

    for name in names:
        if name in existing_names:
            skipped.append({"name": name, "reason": "이미 등록된 팀 이름"})
            continue

        team = Team(season_id=season.id, name=name)
        db.add(team)
        db.flush()  # team.id 확보

        login_id = f"{season.id}-{team.id}"
        raw_password = generate_password()
        db.add(
            Account(team_id=team.id, login_id=login_id, password_hash=hash_password(raw_password))
        )
        existing_names.add(name)
        issued.append({"name": name, "login_id": login_id, "password": raw_password})

    db.commit()
    return render_season_detail(request, season, db, issued=issued, skipped=skipped)


@router.post("/teams/{team_id}/reissue-password")
def reissue_password(
    team_id: int,
    request: Request,
    admin: AdminAccount = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """팀 비밀번호를 새로 발급한다 (ux-improvements.md §2-4).

    비밀번호는 bcrypt 해시로만 저장돼 원문을 다시 볼 수 없으므로, 분실 시 유일한
    해결책이 재발급이다. 기존 비밀번호는 이 시점부터 사용할 수 없다.
    """
    team = db.get(Team, team_id)
    if team is None:
        return RedirectResponse("/admin", status_code=303)

    account = team.account
    if account is None:
        # 아카이브된 시즌은 계정을 삭제한다 (plan.md §5.4).
        return render_season_detail(
            request,
            team.season,
            db,
            bulk_error=f"'{team.name}' 팀은 로그인 계정이 없어 비밀번호를 재발급할 수 없습니다.",
        )

    raw_password = generate_password()
    account.password_hash = hash_password(raw_password)
    db.commit()

    return render_season_detail(
        request,
        team.season,
        db,
        issued=[{"name": team.name, "login_id": account.login_id, "password": raw_password}],
        issued_kind="reissued",
    )


@router.post("/teams/{team_id}/disqualify")
def toggle_disqualify(
    team_id: int,
    admin: AdminAccount = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    team = db.get(Team, team_id)
    if team is None:
        return RedirectResponse("/admin", status_code=303)
    team.disqualified = not team.disqualified
    db.commit()
    return RedirectResponse(f"/admin/seasons/{team.season_id}", status_code=303)


@router.post("/teams/{team_id}/daily-count")
def set_daily_count(
    team_id: int,
    count: int = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """테스트/운영 편의: 팀의 '오늘 완료 카운트'를 관리자가 직접 지정한다 (plan.md §5.3).

    지정한 값은 보정 델타로 환산해 저장한다. 그래야 지정 이후에 완료되는 평가가
    그대로 카운트에 누적되어 하루 한도가 계속 정상 동작한다.
    """
    team = db.get(Team, team_id)
    if team is None:
        return RedirectResponse("/admin", status_code=303)
    today = today_kst()
    # 보정값을 잠시 비우고 실제 완료 건수만 구한 뒤, 목표치와의 차이를 보정값으로 저장한다.
    team.daily_count_adjustment = None
    team.daily_count_adjustment_date = None
    actual_done = get_daily_done_count(db, team, today)
    team.daily_count_adjustment = max(count, 0) - actual_done
    team.daily_count_adjustment_date = today
    db.commit()
    return RedirectResponse(f"/admin/seasons/{team.season_id}", status_code=303)
