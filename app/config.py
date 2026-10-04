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
    # 세션 쿠키 유효 시간. **지정하지 않으면 Starlette 기본값이 14일**이라, 공용 PC에서
    # 로그인한 관리자 세션이 2주 동안 살아 있게 된다. 참가자는 재로그인이 쉽고 관리자
    # 세션은 짧아야 하므로 8시간으로 줄인다 (둘이 같은 미들웨어를 공유한다).
    session_max_age_seconds: int = 8 * 60 * 60
    storage_dir: Path = BASE_DIR / "storage"

    # spec.md에서 확정한 규칙
    # 하루 제출 한도만 .env(DAILY_SUBMISSION_LIMIT)로 덮어쓸 수 있게 열어 뒀다.
    # 대회 중에 대기열이 밀리면 한도를 조여야 하는데, 그때마다 코드를 배포하는 것은
    # 위험하기 때문이다. 나머지 규칙은 바꿀 일이 없어 코드에만 둔다.
    # (2026-09-02: 5회 → 3회로 변경)
    daily_submission_limit: int = 3
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

    # ── 참가자 로그인 잠금 ──────────────────────────────────────────────────
    # 관리자보다 느슨하게 잡는다. 비밀번호를 추측당할 위험이 아니라 **bcrypt 연산으로
    # 서버를 마비시키는 것**을 막는 장치라, 문턱을 낮게 둘 이유가 없다. 반대로 너무
    # 빡빡하면 발급받은 비밀번호를 잘못 붙여넣은 참가자가 대회 중에 제출을 못 하게 된다.
    team_login_max_attempts: int = 10
    team_login_lockout_minutes: int = 5

    # ── 평가 서버 자동 켜기·끄기 (worker-auto-start-stop-plan.md) ────────────────
    # 켜기는 웹 서버의 autopilot 컨테이너가, 끄기는 평가 서버 안의 autostop 타이머가 한다.
    # 인스턴스 ID가 비어 있으면 기능 전체가 아무것도 하지 않는다. 그래서 키를 넣지 않은 기존
    # 배포(노트북 개발 환경 포함)는 그대로 동작한다.
    # AWS 키는 여기에 두지 않는다. boto3가 AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY를 직접 읽으므로,
    # 설정 객체를 화면이나 로그에 찍더라도 키가 딸려 나가지 않는다.
    autopilot_instance_id: str = ""
    autopilot_region: str = "ap-northeast-2"
    # 비어 있으면 알림을 보내지 않고 로그만 남긴다. 보낸 것으로 표시하지도 않는다(나중에 넣으면 보낸다).
    discord_webhook_url: str = ""
    # 제출 후 2분 안에 켜기 요청(명세서 S1)을 만족하는 가장 긴 간격이다.
    autopilot_poll_seconds: int = 60
    # 켜기 요청 후 이만큼 지나도 워커가 살아나지 않으면 알린다(명세서 Q1). 평소 부팅부터 워커 기동까지
    # 몇 분이 걸려서, 더 짧으면 오경보가 난다.
    autopilot_start_timeout_minutes: int = 10
    # 스위치가 꺼진 채 대기 제출이 있고 워커가 이만큼 살아 있지 않으면 알린다(plan.md P2).
    # 운영자가 스위치를 끄고 서버 켜는 것을 잊은 경우를 잡는다.
    autopilot_manual_attention_minutes: int = 10
    # 켜기 요청이 계속 실패하면 1분마다 다시 시도하지만, 알림은 이 간격에 한 번만 보낸다(plan.md §1.2).
    autopilot_start_failed_repeat_minutes: int = 30
    # "종료 시 동작"이 stop인지 확인하는 간격. 누가 바꾸면 첫 자동 끄기에서 디스크째 사라진다(plan.md §1.4).
    autopilot_shutdown_check_hours: int = 24
    # 대기·평가가 없는 상태가 이만큼 이어지면 평가 서버가 스스로 끈다(명세서 확정값).
    autostop_idle_minutes: int = 30
    # SSH 접속 때문에 이만큼 끄지 못하고 있으면 알린다. 접속 창을 열어 둔 채 잊은 경우다(명세서 Q3).
    autostop_ssh_alert_minutes: int = 60

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
