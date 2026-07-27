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
    admin_id = request.session.get("admin_id")
    if not admin_id:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    admin = db.get(AdminAccount, admin_id)
    if admin is None:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    return admin
