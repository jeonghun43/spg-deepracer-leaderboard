"""로컬 확인용 데모 데이터 — 직전 제출 영상·트랙 이탈 횟수·업로드 일시 중지 화면을 눈으로 보기 위한 것.

로컬에는 평가 워커(EC2)가 없어서 제출을 올려도 평가가 끝나지 않는다. 그래서 "평가가 끝난 상태"를
DB에 직접 만들어 넣는다. 영상은 storage/videos에 이미 있는 mp4를 복사해 쓴다.

사용법 (WSL, 저장소 루트에서 — 로컬 compose가 떠 있어야 한다):
    docker compose exec -T web python - < scripts/dev_seed_video_demo.py

**운영 서버에서는 돌지 않는다.** prod compose는 ADMIN_LOGIN_PATH를 필수로 넘기므로 그걸 보고 거절한다.
여러 번 돌려도 된다 — 같은 이름의 데모 시즌을 지우고 새로 만든다.
"""

import datetime as dt
import secrets
import shutil

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import (
    Account,
    AdminAccount,
    EvaluationResult,
    FinishStatus,
    Season,
    SeasonStatus,
    Submission,
    SubmissionStatus,
    Team,
)
from app.security import hash_password

SEASON_NAME = "[로컬 데모] 영상 확인용"

# 운영 판별: prod compose는 ADMIN_LOGIN_PATH를 필수로 넘기고(비밀 경로), 로컬 compose는 넘기지 않아
# 기본값 /admin/login이 된다. SESSION_HTTPS_ONLY로는 못 가른다 — 로컬 .env에도 true가 들어 있을 수 있다.
if settings.admin_login_path != "/admin/login":
    raise SystemExit("ADMIN_LOGIN_PATH가 설정돼 있다 — 운영 환경으로 보여 중단한다. 로컬 compose에서만 쓴다.")

now = dt.datetime.now(tz=dt.timezone.utc)
db = SessionLocal()

# 이전 실행분의 시즌과 영상 폴더를 먼저 치운다. 영상을 남겨 두면 아래에서 샘플로 다시 집혀,
# 같은 경로에 자기 자신을 복사하려다 실패한다(2회차 실행에서 실제로 났다).
old = db.execute(select(Season).where(Season.name == SEASON_NAME)).scalars().all()
for season in old:
    shutil.rmtree(settings.videos_dir / str(season.id), ignore_errors=True)
    db.delete(season)
db.commit()

sample_paths = sorted(settings.videos_dir.rglob("*.mp4"))[:3]
if not sample_paths:
    raise SystemExit(f"{settings.videos_dir}에 복사해 쓸 mp4가 없다. 아무 mp4나 하나 넣고 다시 실행.")
# 미리 읽어 둔다 — 새 시즌 id가 예전 시즌 id와 겹쳐도 원본을 덮어쓰며 읽는 일이 없다.
samples = [path.read_bytes() for path in sample_paths]

season = Season(
    name=SEASON_NAME,
    track_name="Vegas_track",
    start_date=now.date() - dt.timedelta(days=1),
    end_date=now.date() + dt.timedelta(days=7),
    status=SeasonStatus.ACTIVE,
)
db.add(season)
db.flush()

passwords = {}


def make_team(name: str, login_id: str) -> Team:
    team = Team(season_id=season.id, name=name)
    db.add(team)
    db.flush()
    password = secrets.token_urlsafe(9)
    # 로그인 아이디는 전역 유니크라 이전 실행분이 남아 있으면 지운다(시즌 삭제 때 같이 지워지지만 방어).
    stale = db.execute(select(Account).where(Account.login_id == login_id)).scalar_one_or_none()
    if stale is not None:
        db.delete(stale)
        db.flush()
    db.add(Account(team_id=team.id, login_id=login_id, password_hash=hash_password(password)))
    passwords[login_id] = password
    return team


def add_done(team: Team, minutes_ago: int, sample_index: int, **result_fields) -> Submission:
    submission = Submission(
        team_id=team.id,
        submitted_at=now - dt.timedelta(minutes=minutes_ago),
        model_path=f"models/demo/{team.id}/{minutes_ago}.tar.gz",  # 파일은 없다 — 화면 확인용
        status=SubmissionStatus.DONE,
        finished_at=now - dt.timedelta(minutes=minutes_ago - 8),
    )
    db.add(submission)
    db.flush()
    rel = f"{season.id}/{team.id}/{submission.id}.mp4"
    dest = settings.videos_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(samples[sample_index % len(samples)])
    db.add(EvaluationResult(submission_id=submission.id, video_path=rel, **result_fields))
    return submission


team_a = make_team("데모A", "demo_a")
team_b = make_team("데모B", "demo_b")

# 팀 A: 최고기록(완주) 1건 + 그 뒤에 완주 못 한 직전 제출 1건 → 제출 탭에 "직전" 영상이 따로 보여야 한다.
best_a = add_done(team_a, 120, 0, finish_status=FinishStatus.FINISHED, lap_time_seconds=92.31,
                  off_track_count=1, best_progress_percent=100.0, failure_reason="lap_complete")
latest_a = add_done(team_a, 30, 1, finish_status=FinishStatus.TIMEOUT, lap_time_seconds=None,
                    off_track_count=4, best_progress_percent=47.3, failure_reason="off_track")
# 팀 B: 완주 1건 — A로 로그인했을 때 B의 영상 권한을 확인하는 상대.
best_b = add_done(team_b, 60, 2, finish_status=FinishStatus.FINISHED, lap_time_seconds=101.05,
                  off_track_count=0, best_progress_percent=100.0, failure_reason="lap_complete")

admin_password = None
if db.execute(select(AdminAccount).where(AdminAccount.login_id == "demo_admin")).scalar_one_or_none() is None:
    admin_password = secrets.token_urlsafe(9)
    db.add(AdminAccount(login_id="demo_admin", password_hash=hash_password(admin_password)))

db.commit()

print(f"데모 시즌 생성: id={season.id}  ({SEASON_NAME})")
print(f"  팀 A 최고기록 영상(공개):   /media/videos/{best_a.id}")
print(f"  팀 A 직전 제출 영상(A만):   /media/videos/{latest_a.id}   ← 탈선 4회, 47.3%")
print(f"  팀 B 최고기록 영상(공개):   /media/videos/{best_b.id}")
print("로그인 정보 (이 터미널에만 출력된다)")
for login_id, password in passwords.items():
    print(f"  팀 {login_id} / {password}")
if admin_password:
    print(f"  관리자 demo_admin / {admin_password}   (경로: {settings.admin_login_path})")
else:
    print(f"  관리자 demo_admin은 이전 실행 때 만든 비밀번호 그대로 (경로: {settings.admin_login_path})")
db.close()
