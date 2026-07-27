from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Season, SeasonStatus, SubmissionStatus, Team
from app.records import get_team_best
from app.render import templates

router = APIRouter(tags=["leaderboard"])


def build_leaderboard(db: Session, season: Season):
    """완주 기록이 있는 팀은 랩타임 오름차순(가장 빠른 팀이 1위)으로, 없는 팀은 별도 목록으로 반환한다."""
    teams = db.execute(
        select(Team).where(Team.season_id == season.id, Team.disqualified.is_(False))
    ).scalars().all()

    ranked = []
    unranked = []

    for team in teams:
        best_submission, best_result = get_team_best(team)

        # 평가가 실제로 끝난 제출만 센다 — 업로드 오류나 시스템 오류(ERROR)는
        # 팀의 시도로 보기 어렵고, 하루 제출 한도 카운트 기준과도 일치한다.
        total_submissions = sum(1 for s in team.submissions if s.status == SubmissionStatus.DONE)

        if best_result is not None:
            video_url = f"/media/videos/{best_result.video_path}" if best_result.video_path else None
            ranked.append(
                {
                    "team": team,
                    "lap_time": best_result.lap_time_seconds,
                    "achieved_at": best_submission.submitted_at,
                    "total_submissions": total_submissions,
                    "video_url": video_url,
                }
            )
        else:
            unranked.append({"team": team, "total_submissions": total_submissions})

    # 랩타임 오름차순(가장 빠른 팀이 1위). 완전히 같은 랩타임이면 그 기록을 먼저 세운
    # 팀이 상위 — plan.md §5.2.
    ranked.sort(key=lambda row: (row["lap_time"], row["achieved_at"]))
    return ranked, unranked


def get_open_season(db: Session) -> Season | None:
    """지금 열려있는(진행중) 시즌. 운영상 한 번에 하나지만, 실수로 둘이 되어도
    화면이 흔들리지 않도록 가장 최근 시작한 시즌으로 결정한다."""
    return db.execute(
        select(Season)
        .where(Season.status == SeasonStatus.ACTIVE)
        .order_by(Season.start_date.desc())
        .limit(1)
    ).scalar_one_or_none()


def render_season_list(request: Request, db: Session):
    seasons = db.execute(select(Season).order_by(Season.start_date.desc())).scalars().all()
    return templates.TemplateResponse(request, "leaderboard_seasons.html", {"seasons": seasons})


@router.get("/")
def index():
    return RedirectResponse("/leaderboard", status_code=303)


@router.get("/leaderboard")
def leaderboard_entry(request: Request, db: Session = Depends(get_db)):
    """대회 기간에는 매번 시즌을 고르지 않아도 되게, 진행중 시즌으로 바로 들어간다.
    진행중 시즌이 없으면(전부 준비중/마감/아카이브) 지금까지처럼 시즌 목록을 보여준다."""
    open_season = get_open_season(db)
    if open_season is not None:
        return RedirectResponse(f"/leaderboard/{open_season.id}", status_code=303)
    return render_season_list(request, db)


# 주의: `/leaderboard/{season_id}`보다 먼저 선언해야 한다. FastAPI는 선언 순서로
# 매칭하므로 뒤에 두면 "seasons"를 int로 파싱하려다 422가 난다.
@router.get("/leaderboard/seasons")
def season_list(request: Request, db: Session = Depends(get_db)):
    """자동 진입을 우회해 과거 시즌까지 고르는 화면."""
    return render_season_list(request, db)


@router.get("/leaderboard/{season_id}")
def season_leaderboard(season_id: int, request: Request, db: Session = Depends(get_db)):
    season = db.get(Season, season_id)
    if season is None:
        return RedirectResponse("/leaderboard", status_code=303)
    ranked, unranked = build_leaderboard(db, season)
    return templates.TemplateResponse(
        request,
        "leaderboard.html",
        {"season": season, "ranked": ranked, "unranked": unranked},
    )
