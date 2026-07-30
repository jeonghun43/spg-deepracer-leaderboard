from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

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
