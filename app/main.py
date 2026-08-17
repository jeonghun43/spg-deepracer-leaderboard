from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.routers import admin, auth, internal, leaderboard, submissions

# 자동 생성 문서는 공개하지 않는다. API를 쓰는 외부 소비자가 없고(워커는 고정된 /internal
# 경로만 호출한다), 열어두면 관리자·워커 엔드포인트의 존재와 요청 형식이 그대로 드러난다.
app = FastAPI(
    title="SPG DeepRacer Leaderboard",
    docs_url=None,     # /docs (Swagger UI)
    redoc_url=None,    # /redoc (ReDoc) — 빠뜨리기 쉽다. 문서 UI는 두 개다
    openapi_url=None,  # /openapi.json (스키마 원본)
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    https_only=settings.session_https_only,
    # 지정하지 않으면 Starlette 기본값이 14일이다. 공용 PC에서 로그인한 관리자 세션이
    # 2주를 사는 것을 막는다 — 비밀 경로를 몰라도 /admin에 그대로 들어가진다.
    max_age=settings.session_max_age_seconds,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
settings.videos_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media/videos", StaticFiles(directory=str(settings.videos_dir)), name="videos")

app.include_router(auth.router)
app.include_router(submissions.router)
app.include_router(leaderboard.router)
app.include_router(admin.router)
# 관리자 로그인 폼 — .env의 ADMIN_LOGIN_PATH가 정하는 비밀 경로에 붙는다.
app.include_router(admin.login_router)
app.include_router(internal.router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
