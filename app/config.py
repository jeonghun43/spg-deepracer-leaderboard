from pathlib import Path
from zoneinfo import ZoneInfo

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
