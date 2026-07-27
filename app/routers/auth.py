from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Account
from app.render import templates
from app.security import verify_password

router = APIRouter(tags=["auth"])


@router.get("/login")
def login_form(request: Request):
    if request.session.get("team_id"):
        return RedirectResponse("/submit", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(
    request: Request,
    login_id: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    account = db.execute(select(Account).where(Account.login_id == login_id)).scalar_one_or_none()
    if account is None or not verify_password(password, account.password_hash):
        return templates.TemplateResponse(
            request, "login.html", {"error": "아이디 또는 비밀번호가 올바르지 않습니다."}, status_code=401
        )
    request.session.clear()
    request.session["team_id"] = account.team_id
    return RedirectResponse("/submit", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
