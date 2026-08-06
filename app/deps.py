from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AdminAccount, Team


def get_current_team(request: Request, db: Session = Depends(get_db)) -> Team:
    team_id = request.session.get("team_id")
    if not team_id:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    team = db.get(Team, team_id)
    if team is None:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return team


def get_current_team_optional(request: Request, db: Session = Depends(get_db)) -> Team | None:
    team_id = request.session.get("team_id")
    if not team_id:
        return None
    return db.get(Team, team_id)


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminAccount:
    """미인증이면 404를 돌려준다 — 리다이렉트가 아니다 (admin-access-hardening.md §3.2).

    로그인 페이지로 보내주면 "이 경로에 관리자 페이지가 있다"는 사실이 확인된다.
    404를 주면 존재하지 않는 아무 경로와 응답이 구별되지 않아 은닉이 성립한다.

    **부작용은 의도된 것이다**: 세션이 만료된 관리자도 /admin에서 404를 보게 된다.
    관리자는 .env에 설정한 비밀 경로로 다시 들어와야 한다.
    """
    admin_id = request.session.get("admin_id")
    if not admin_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    admin = db.get(AdminAccount, admin_id)
    if admin is None:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return admin
