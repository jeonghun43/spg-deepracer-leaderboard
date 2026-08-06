from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

KST = ZoneInfo("Asia/Seoul")

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", protected_namespaces=())

    database_url: str = "postgresql+psycopg2://drleader:drleader@localhost:5432/drleader"
    session_secret: str = "change-me-in-production"
    # Cloudflare Tunnel 등으로 공인 인터넷에 노출할 때만 true로 설정한다.
    # true인 상태에서 http://localhost:8000으로 직접 접속하면(터널 없이) 브라우저가
    # Secure 쿠키를 저장하지 않아 로그인이 깨지므로, 로컬 전용 운영/테스트 중에는 false로 둔다.
    session_https_only: bool = False
    storage_dir: Path = BASE_DIR / "storage"

    # spec.md에서 확정한 규칙
    daily_submission_limit: int = 5
    online_eval_laps: int = 3
    # 참가자에게 보여줄 예상 대기 시간 계산에 쓰는 평가 1건당 소요 시간(분).
    # GPU 없는 노트북 기준 실측값이며, 서버를 바꾸면 재측정해서 갱신해야 한다.
    eval_minutes_estimate: int = 10
    model_upload_max_bytes: int = 500 * 1024 * 1024  # 500MB
    model_upload_allowed_extensions: tuple[str, ...] = (".tar.gz", ".zip")

    # ── 워커가 웹과 다른 기기에서 돌 때 쓰는 설정 (cloud-migration.md §4) ──────────
    # worker_token이 비어 있으면 웹과 워커가 같은 디스크를 공유하는 지금 방식(local 모드)으로
    # 동작하고, 값이 설정되면 워커가 HTTP로 모델을 받고 영상을 올리는 방식으로 전환된다.
    # 하나의 스위치로 두 배포 형태를 모두 지원해, 이관 전후로 코드를 바꾸지 않아도 되게 한다.
    worker_token: str = ""
    web_base_url: str = "http://localhost:8000"
    video_upload_max_bytes: int = 200 * 1024 * 1024  # 200MB (실측 14MB 내외)
    # 이 시간 안에 하트비트가 없으면 평가 서버가 멈춘 것으로 보고 화면에 안내한다.
    worker_heartbeat_stale_minutes: int = 3

    # ── 관리자 진입점 은닉 (admin-access-hardening.md) ──────────────────────
    # 관리자 로그인 폼이 열리는 경로. 기본값을 두는 것은 로컬 개발 편의를 위해서이고,
    # 운영 배포에서는 docker-compose.prod.yml이 이 값을 필수로 요구한다 — 설정하지 않으면
    # 웹 컨테이너가 아예 뜨지 않아, 관리자 로그인이 조용히 공개된 채로 배포되는 일을 막는다.
    admin_login_path: str = "/admin/login"
    # 로그인 실패가 이만큼 쌓이면 잠근다. 비밀 경로가 유출됐을 때의 2차 방어선이다.
    admin_login_max_attempts: int = 5
    admin_login_lockout_minutes: int = 15

    @field_validator("admin_login_path")
    @classmethod
    def _normalize_admin_login_path(cls, value: str) -> str:
        """사람이 .env에 손으로 넣는 값이라 흔한 실수를 흡수한다.

        특히 빈 문자열을 그대로 라우트로 등록하면 앱이 깨지므로 기본값으로 되돌린다.
        """
        value = value.strip().rstrip("/")
        if not value:
            return "/admin/login"
        if not value.startswith("/"):
            value = "/" + value
        return value

    @property
    def models_dir(self) -> Path:
        return self.storage_dir / "models"

    @property
    def videos_dir(self) -> Path:
        return self.storage_dir / "videos"

    @property
    def metrics_dir(self) -> Path:
        """DRFC가 만든 원본 metrics json 사본 — 나중에 결과를 재확인/재파싱할 때 쓴다."""
        return self.storage_dir / "metrics"

    @property
    def eval_logs_dir(self) -> Path:
        """제출별 시뮬레이션 로그. DRFC는 로그를 디스크에 안 남기고 스택을 지우면
        사라지므로, 평가 실패 원인을 추적하려면 여기에 받아둬야 한다."""
        return self.storage_dir / "eval_logs"


settings = Settings()
