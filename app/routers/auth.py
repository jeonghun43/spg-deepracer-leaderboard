from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import admin_lockout
from app.config import settings
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
    keys = admin_lockout.build_keys(admin_lockout.client_ip(request), login_id, scope="team")

    # 잠긴 동안에는 비밀번호를 아예 검사하지 않는다 — bcrypt를 돌리지 않아야
    # 이 엔드포인트가 계산 자원 소모 공격의 통로가 되지 않는다. 비밀번호를 추측당할
    # 위험(62자 10자리)보다 이쪽이 훨씬 현실적인 위협이다.
    remaining = admin_lockout.seconds_remaining(keys)
    if remaining:
        minutes = max(1, (remaining + 59) // 60)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": f"로그인 시도가 너무 많습니다. 약 {minutes}분 뒤에 다시 시도해주세요."},
            status_code=429,
        )

    account = db.execute(select(Account).where(Account.login_id == login_id)).scalar_one_or_none()
    if account is None or not verify_password(password, account.password_hash):
        admin_lockout.record_failure(
            keys,
            max_attempts=settings.team_login_max_attempts,
            lockout_minutes=settings.team_login_lockout_minutes,
        )
        return templates.TemplateResponse(
            request, "login.html", {"error": "아이디 또는 비밀번호가 올바르지 않습니다."}, status_code=401
        )

    admin_lockout.reset(keys)
    request.session.clear()
    request.session["team_id"] = account.team_id
    return RedirectResponse("/submit", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
