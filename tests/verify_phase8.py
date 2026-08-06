"""Phase 8 검증 스크립트 (tasks.md T043~T047).

실제로 기동 중인 웹앱(localhost:8000)에 HTTP로 요청을 보내 참가자/관리자 여정을
그대로 재현한다. DRFC 평가 자체는 이미 제출 4번으로 실환경 검증이 끝났으므로,
여기서는 평가 결과만 워커가 쓰는 것과 동일한 형태로 DB에 채워 넣고 그 뒤 로직
(카운트·동시제출·리더보드·아카이브)을 검증한다.

실행 전제: 웹앱은 떠 있고 **워커는 꺼져 있어야 한다** (테스트 제출이 실제 평가로
넘어가지 않도록).

    .venv/bin/python -m tests.verify_phase8
"""

import datetime as dt
import http.client
import io
import urllib.parse
import uuid

from app.config import KST, settings
from app.db import SessionLocal
from app.models import (
    Account,
    EvaluationResult,
    FinishStatus,
    Season,
    Submission,
    SubmissionStatus,
    Team,
)
from worker.run import CLAIM_NEXT_SQL

HOST, PORT = "localhost", 8000

passed: list[str] = []
failed: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        passed.append(label)
        print(f"  [OK]   {label}")
    else:
        failed.append(f"{label} — {detail}")
        print(f"  [FAIL] {label} — {detail}")


class Client:
    """세션 쿠키를 유지하는 최소 HTTP 클라이언트."""

    def __init__(self) -> None:
        self.cookie: str | None = None

    def _headers(self, extra: dict | None = None) -> dict:
        headers = dict(extra or {})
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    def _store_cookie(self, resp) -> None:
        raw = resp.getheader("Set-Cookie")
        if raw:
            self.cookie = raw.split(";")[0]

    def get(self, path: str):
        conn = http.client.HTTPConnection(HOST, PORT)
        conn.request("GET", path, headers=self._headers())
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", "replace")
        self._store_cookie(resp)
        location = resp.getheader("Location")
        conn.close()
        return resp.status, location, body

    def post(self, path: str, data: dict):
        conn = http.client.HTTPConnection(HOST, PORT)
        body = urllib.parse.urlencode(data, encoding="utf-8")
        conn.request(
            "POST", path, body=body,
            headers=self._headers({"Content-Type": "application/x-www-form-urlencoded"}),
        )
        resp = conn.getresponse()
        text = resp.read().decode("utf-8", "replace")
        self._store_cookie(resp)
        location = resp.getheader("Location")
        conn.close()
        return resp.status, location, text

    def post_file(self, path: str, field: str, filename: str, content: bytes):
        boundary = f"----verify{uuid.uuid4().hex}"
        buf = io.BytesIO()
        buf.write(f"--{boundary}\r\n".encode())
        buf.write(
            f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode()
        )
        buf.write(b"Content-Type: application/octet-stream\r\n\r\n")
        buf.write(content)
        buf.write(f"\r\n--{boundary}--\r\n".encode())
        payload = buf.getvalue()

        conn = http.client.HTTPConnection(HOST, PORT)
        conn.request(
            "POST", path, body=payload,
            headers=self._headers({
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(payload)),
            }),
        )
        resp = conn.getresponse()
        resp.read()
        self._store_cookie(resp)
        location = resp.getheader("Location")
        conn.close()
        return resp.status, location


def error_of(location: str | None) -> str:
    """리다이렉트 Location의 error 파라미터를 디코드한다."""
    if not location or "?" not in location:
        return ""
    query = urllib.parse.parse_qs(location.split("?", 1)[1])
    return query.get("error", [""])[0]


DUMMY_ARCHIVE = b"dummy model archive for verification\n" * 64


def submit_model(client: Client, filename: str = "model.tar.gz") -> tuple[int, str]:
    status, location = client.post_file("/submit", "model_file", filename, DUMMY_ARCHIVE)
    return status, error_of(location)


def finish_submission(
    submission_id: int, *, finished: bool, lap_time: float | None, video: str | None = None
) -> None:
    """워커가 평가를 마쳤을 때와 같은 상태로 DB를 채운다."""
    db = SessionLocal()
    try:
        submission = db.get(Submission, submission_id)
        submission.status = SubmissionStatus.DONE
        submission.finished_at = dt.datetime.now(tz=dt.timezone.utc)
        db.add(
            EvaluationResult(
                submission_id=submission.id,
                finish_status=FinishStatus.FINISHED if finished else FinishStatus.TIMEOUT,
                lap_time_seconds=lap_time,
                off_track_count=0 if finished else 3,
                video_path=video,
                metrics_raw_path="verify/metrics.json",
            )
        )
        db.commit()
    finally:
        db.close()


def latest_submission_id(team_id: int) -> int | None:
    db = SessionLocal()
    try:
        row = (
            db.query(Submission)
            .filter(Submission.team_id == team_id)
            .order_by(Submission.submitted_at.desc())
            .first()
        )
        return row.id if row else None
    finally:
        db.close()


def submission_count(team_id: int) -> int:
    db = SessionLocal()
    try:
        return db.query(Submission).filter(Submission.team_id == team_id).count()
    finally:
        db.close()


def team_id_by_name(season_id: int, name: str) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(Team).filter(Team.season_id == season_id, Team.name == name).one().id
        )
    finally:
        db.close()


# ── T044 관리자 여정 ────────────────────────────────────────────────────

print("\n=== T044 관리자 전체 여정 (시즌 생성 → 팀 등록 → 실격 → 아카이브) ===")

admin = Client()
# 로그인 폼은 .env의 ADMIN_LOGIN_PATH가 정하는 비밀 경로에 있다 (admin-access-hardening.md).
status, _, _ = admin.post(settings.admin_login_path, {"login_id": "admin", "password": "admin1234"})
check("관리자 로그인", status == 303, f"status={status}")

suffix = dt.datetime.now(tz=KST).strftime("%H%M%S")
season_name = f"검증 시즌 {suffix}"
status, location, _ = admin.post(
    "/admin/seasons/new",
    {
        "name": season_name,
        "track_name": "Vegas_track",
        "start_date": "2026-07-25",
        "end_date": "2026-08-25",
    },
)
season_id = int(location.rstrip("/").rsplit("/", 1)[-1]) if location else 0
check("시즌 생성", season_id > 0, f"location={location}")

credentials: dict[str, tuple[str, str]] = {}
for team_name in ("검증팀A", "검증팀B", "검증팀C"):
    status, location, _ = admin.post(
        f"/admin/seasons/{season_id}/teams/new", {"team_name": team_name}
    )
    query = urllib.parse.parse_qs(location.split("?", 1)[1]) if location and "?" in location else {}
    credentials[team_name] = (query.get("new_login", [""])[0], query.get("new_password", [""])[0])
check(
    "팀 3개 등록 + 계정 자동 발급",
    all(login and password for login, password in credentials.values()),
    str(credentials),
)

team_a_id = team_id_by_name(season_id, "검증팀A")
team_b_id = team_id_by_name(season_id, "검증팀B")
team_c_id = team_id_by_name(season_id, "검증팀C")

status, _, body = admin.get(f"/admin/seasons/{season_id}")
check("시즌 상세에 한글 팀명 정상 표시", "검증팀A" in body, "팀명이 화면에 없음")

admin.post(f"/admin/seasons/{season_id}/advance-status", {})
db = SessionLocal()
season_status = db.get(Season, season_id).status
db.close()
check("시즌 상태 준비중 → 진행중", season_status.value == "active", f"status={season_status}")

# ── T043 참가자 여정 ────────────────────────────────────────────────────

print("\n=== T043 참가자 전체 여정 (로그인 → 제출 → 대기열 → 결과 → 리더보드) ===")

team_a = Client()
login_a, password_a = credentials["검증팀A"]
status, _, _ = team_a.post("/login", {"login_id": login_a, "password": password_a})
check("팀 로그인", status == 303, f"status={status}")

status, _, body = team_a.get("/submit")
check("제출 화면 진입 (잔여 5/5)", status == 200 and "5 / 5" in body, f"status={status}")

status, err = submit_model(team_a)
check("모델 업로드 접수", status == 303 and not err, f"error={err}")

sub1 = latest_submission_id(team_a_id)
status, _, body = team_a.get("/submit")
check("대기열 상태 노출", "대기" in body or "평가" in body, "대기 상태 문구 없음")

finish_submission(sub1, finished=True, lap_time=95.5, video=f"{season_id}/{team_a_id}/{sub1}.mp4")

status, err = submit_model(team_a)
sub2 = latest_submission_id(team_a_id)
check("결과 확정 후 재제출 가능", status == 303 and not err and sub2 != sub1, f"error={err}")
finish_submission(sub2, finished=True, lap_time=88.25, video=f"{season_id}/{team_a_id}/{sub2}.mp4")

# 미완주(타임아웃) 케이스 — 검증팀B는 완주 기록 없이 미완주만 남긴다
team_b = Client()
login_b, password_b = credentials["검증팀B"]
team_b.post("/login", {"login_id": login_b, "password": password_b})
submit_model(team_b)
sub_b = latest_submission_id(team_b_id)
finish_submission(sub_b, finished=False, lap_time=None)

public = Client()
status, _, board = public.get(f"/leaderboard/{season_id}")
check("리더보드 로그인 없이 조회", status == 200, f"status={status}")
check("최고기록만 반영 (95.5 대신 88.25)", "88.25" in board and "95.5" not in board, "기록 표시 오류")
check("누적 제출 횟수 표시", "검증팀A" in board, "팀A 미표시")
check("미완주 팀 별도 표시", "검증팀B" in board and "미완주" in board, "미완주 구획 없음")

a_pos, b_pos = board.find("검증팀A"), board.find("검증팀B")
check("완주 팀이 미완주 팀보다 위에 표시", 0 <= a_pos < b_pos, f"A={a_pos} B={b_pos}")

# 실격 처리 → 리더보드에서 제외
admin.post(f"/admin/teams/{team_b_id}/disqualify", {})
status, _, board = public.get(f"/leaderboard/{season_id}")
check("실격 팀은 리더보드에서 제외", "검증팀B" not in board, "실격 팀이 여전히 노출됨")

status, err = submit_model(team_b)
check("실격 팀은 제출 불가", "실격" in err, f"error={err}")
admin.post(f"/admin/teams/{team_b_id}/disqualify", {})  # 원복

# ── T045 하루 제출 한도 경계 ────────────────────────────────────────────

print("\n=== T045 하루 제출 한도 경계 (한도 5회) ===")

admin.post(f"/admin/teams/{team_a_id}/daily-count", {"count": "4"})
status, _, body = team_a.get("/submit")
check("관리자 카운트 조정 = 4 반영 (잔여 1/5)", "1 / 5" in body, "잔여 표기 확인 실패")

status, err = submit_model(team_a)
check("5번째 제출 접수", status == 303 and not err, f"error={err}")
sub5 = latest_submission_id(team_a_id)
finish_submission(sub5, finished=False, lap_time=None)  # 미완주도 카운트 대상

db = SessionLocal()
count_after = None
try:
    from app.quota import get_daily_done_count

    count_after = get_daily_done_count(db, db.get(Team, team_a_id))
finally:
    db.close()
check("미완주 제출이 카운트에 포함 (4 → 5)", count_after == 5, f"count={count_after}")

status, err = submit_model(team_a)
check("6번째 제출 차단", "한도" in err, f"error={err}")

before = submission_count(team_a_id)
admin.post(f"/admin/teams/{team_a_id}/daily-count", {"count": "3"})
status, err = submit_model(team_a, filename="model.txt")
check("허용되지 않는 확장자 거부", "허용되지 않는" in err, f"error={err}")
check("업로드 오류는 제출로 기록되지 않음", submission_count(team_a_id) == before, "제출 레코드가 생김")

db = SessionLocal()
try:
    from app.quota import get_daily_done_count

    count_now = get_daily_done_count(db, db.get(Team, team_a_id))
finally:
    db.close()
check("업로드 오류는 카운트에 미포함", count_now == 3, f"count={count_now}")

# ── T046 동시 제출 제한 & 큐 순차 처리 ──────────────────────────────────

print("\n=== T046 동시 제출 제한 & 대기열 순차 처리 ===")

status, err = submit_model(team_a)
check("새 제출 접수 (대기 상태로 유지)", status == 303 and not err, f"error={err}")
status, err = submit_model(team_a)
check("대기 중 재업로드 차단", "이전 제출" in err, f"error={err}")

db = SessionLocal()
try:
    db.add(Submission(team_id=team_a_id, model_path="/tmp/dup.tar.gz", status=SubmissionStatus.QUEUED))
    db.commit()
    duplicate_blocked = False
except Exception:
    db.rollback()
    duplicate_blocked = True
finally:
    db.close()
check("DB 유니크 인덱스로도 중복 대기 차단", duplicate_blocked, "중복 대기 행이 삽입됨")

# 여러 팀이 동시에 대기 중일 때, 두 워커가 동시에 집어도 서로 다른 건을 가져가야 한다
team_c = Client()
login_c, password_c = credentials["검증팀C"]
team_c.post("/login", {"login_id": login_c, "password": password_c})
submit_model(team_c)

db1, db2 = SessionLocal(), SessionLocal()
try:
    row1 = db1.execute(CLAIM_NEXT_SQL, {"worker_id": "verify-1"}).first()
    row2 = db2.execute(CLAIM_NEXT_SQL, {"worker_id": "verify-2"}).first()
    db1.commit()
    db2.commit()
    claimed = [row1[0] if row1 else None, row2[0] if row2 else None]
finally:
    db1.close()
    db2.close()
check(
    "동시 claim 시 서로 다른 제출을 가져감 (SKIP LOCKED)",
    claimed[0] is not None and claimed[1] is not None and claimed[0] != claimed[1],
    f"claimed={claimed}",
)

db = SessionLocal()
try:
    stuck = db.query(Submission).filter(Submission.status == SubmissionStatus.RUNNING).all()
    for submission in stuck:
        submission.status = SubmissionStatus.DONE
        submission.finished_at = dt.datetime.now(tz=dt.timezone.utc)
        db.add(
            EvaluationResult(
                submission_id=submission.id,
                finish_status=FinishStatus.TIMEOUT,
                lap_time_seconds=None,
                off_track_count=1,
                video_path=None,
                metrics_raw_path="verify/metrics.json",
            )
        )
    db.commit()
finally:
    db.close()

# ── T047 아카이브 후 조회 / 계정 삭제 ───────────────────────────────────

print("\n=== T047 시즌 아카이브 (리더보드·영상 유지, 계정 삭제) ===")

# 최고기록 영상과 그렇지 않은 영상을 실제 파일로 만들어 둔다
best_video = settings.videos_dir / f"{season_id}/{team_a_id}/{sub2}.mp4"
old_video = settings.videos_dir / f"{season_id}/{team_a_id}/{sub1}.mp4"
for path in (best_video, old_video):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake mp4")

admin.post(f"/admin/seasons/{season_id}/advance-status", {})  # active → closed
admin.post(f"/admin/seasons/{season_id}/advance-status", {})  # closed → archived

db = SessionLocal()
try:
    archived_status = db.get(Season, season_id).status.value
    remaining_accounts = (
        db.query(Account).join(Team).filter(Team.season_id == season_id).count()
    )
finally:
    db.close()
check("시즌 상태 아카이브됨", archived_status == "archived", f"status={archived_status}")
check("팀 계정 삭제됨", remaining_accounts == 0, f"남은 계정 수={remaining_accounts}")

status, _, board = public.get(f"/leaderboard/{season_id}")
check("아카이브 후에도 리더보드 조회 가능", status == 200 and "88.25" in board, f"status={status}")
check("최고기록 영상 파일 보존", best_video.exists(), f"{best_video} 없음")
check("최고기록 아닌 영상은 정리됨", not old_video.exists(), f"{old_video}가 남아있음")

status, _, media = public.get(f"/media/videos/{season_id}/{team_a_id}/{sub2}.mp4")
check("아카이브 후 영상 URL 접근 가능", status == 200, f"status={status}")

relogin = Client()
status, _, body = relogin.post("/login", {"login_id": login_a, "password": password_a})
check("아카이브된 시즌 계정으로 로그인 불가", status == 401, f"status={status}")

status, _, body = public.get("/leaderboard")
check("시즌 목록에 아카이브 시즌 노출", season_name in body, "목록에 없음")

# ── 결과 ────────────────────────────────────────────────────────────────

print(f"\n{'=' * 60}")
print(f"통과 {len(passed)}건 / 실패 {len(failed)}건")
for item in failed:
    print(f"  실패: {item}")
print(f"검증용 시즌: {season_name} (id={season_id})")
