"""최초 관리자 계정 생성 스크립트.

사용법: python -m app.seed <login_id> <password>
"""

import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AdminAccount
from app.security import hash_password


def create_admin(login_id: str, password: str) -> None:
    db = SessionLocal()
    try:
        existing = db.execute(
            select(AdminAccount).where(AdminAccount.login_id == login_id)
        ).scalar_one_or_none()
        if existing is not None:
            print(f"이미 존재하는 관리자 아이디입니다: {login_id}")
            return
        admin = AdminAccount(login_id=login_id, password_hash=hash_password(password))
        db.add(admin)
        db.commit()
        print(f"관리자 계정 생성 완료: {login_id}")
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("사용법: python -m app.seed <login_id> <password>")
        raise SystemExit(1)
    create_admin(sys.argv[1], sys.argv[2])
