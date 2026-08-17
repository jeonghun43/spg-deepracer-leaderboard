import datetime as dt

from fastapi.templating import Jinja2Templates

from app.config import KST

templates = Jinja2Templates(directory="app/templates")


def kst(value: dt.datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """저장된 UTC 시각을 화면에 한국시간으로 찍는다.

    DB 컬럼은 `TIMESTAMPTZ`라 값 자체는 늘 정확하지만, psycopg2는 **DB 세션의
    시간대**로 aware datetime을 돌려준다. 컨테이너에 시간대 설정이 없어 그 값이
    UTC이고, 템플릿이 그대로 `strftime` 하면 참가자와 관리자에게 9시간 이른 시각이
    보인다 (2026-08-18 발견 — 대회 마감 시각을 확인하다 드러났다).

    컨테이너 `TZ`/`PGTZ` 환경변수로 맞추는 방법도 있지만 일부러 쓰지 않는다.
    그 방식은 **화면에 찍히는 시간대가 코드 어디에도 안 보이고** 배포 환경 설정에만
    의존해서, 서버를 옮기면 조용히 다시 UTC로 돌아간다. 표시 직전에 명시적으로
    변환하는 편이 8단계 문서의 규칙("저장·공유는 UTC, 표시는 KST")과도 맞는다.
    """
    if value is None:
        return ""
    return value.astimezone(KST).strftime(fmt)

# DRFC가 남기는 종료 사유를 참가자가 이해할 수 있는 말로 옮긴다.
# 이 표에 없는 값은 원문을 그대로 보여준다 — 모르는 사유를 감추면 원인 추적이 어려워진다.
FAILURE_REASON_LABELS = {
    "immobilized": "차량이 멈춤",
    "off_track": "트랙 이탈",
    "crashed": "충돌",
    "reversed": "역주행",
    "time_up": "시간 초과",
    "timeout": "시간 초과",
    "lap_complete": "완주",
}


def failure_summary(result) -> str:
    """완주하지 못한 결과를 '완주 실패 (67.8%) · 차량이 멈춤' 형태로 표현한다.

    예전에는 모든 실패를 "미완주 (시간 초과)"로 표시해, 실제로는 차가 트랙 중간에 멈춘
    경우에도 참가자가 시간 초과로 오해했다 (2026-07-30 submission 18).
    진행률이 기록되지 않은 예전 결과는 사유 없이 "완주 실패"로만 표시한다.
    """
    if result is None:
        return "완주 실패"

    parts = ["완주 실패"]
    progress = getattr(result, "best_progress_percent", None)
    if progress is not None:
        parts[0] = f"완주 실패 ({progress:.1f}%)"

    reason = getattr(result, "failure_reason", None)
    if reason:
        parts.append(FAILURE_REASON_LABELS.get(reason, reason))
    return " · ".join(parts)


templates.env.filters["failure_summary"] = failure_summary
templates.env.filters["kst"] = kst
