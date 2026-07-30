from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.routers import admin, auth, internal, leaderboard, submissions

app = FastAPI(title="SPG DeepRacer Leaderboard")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    https_only=settings.session_https_only,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
settings.videos_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media/videos", StaticFiles(directory=str(settings.videos_dir)), name="videos")

app.include_router(auth.router)
app.include_router(submissions.router)
app.include_router(leaderboard.router)
app.include_router(admin.router)
app.include_router(internal.router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
