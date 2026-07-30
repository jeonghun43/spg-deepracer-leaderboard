"""미완주 사유·진행률 추출과 표시 검증 (tasks.md 12-1).

배경: 화면이 모든 실패를 "미완주 (시간 초과)"로 표시해, 실제로는 차가 트랙 67.8% 지점에서
멈춘 경우(submission 18)에도 참가자가 시간 초과로 오해했다. 게다가 그 케이스는 metrics json이
빈 배열이라 진행률을 metrics에서만 뽑으면 아무것도 못 보여준다 — 평가 로그를 함께 봐야 한다.
"""

import types

import pytest

from app.render import failure_summary
from worker import drfc

# 실제 로그에서 그대로 가져온 형식 (필드 위치가 바뀌면 이 테스트가 먼저 깨진다)
TRACE = "SIM_TRACE_LOG:0,{step},8.37,1.55,89.04,-15.00,0.60,1,0.5000,{done},True,{progress},101,22.55,184.06,{status},0.00,0,1785438187.56"


def write_log(tmp_path, lines):
    path = tmp_path / "eval.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ── 로그에서 뽑기 ─────────────────────────────────────────────────────────


def test_log_gives_best_progress_and_final_status(tmp_path):
    """submission 18 재현 — 67.8%까지 가서 멈춘 경우."""
    log = write_log(tmp_path, [
        TRACE.format(step=1, done="False", progress="10.5", status="in_progress"),
        TRACE.format(step=2, done="False", progress="67.8492", status="in_progress"),
        TRACE.format(step=3, done="True", progress="67.8492", status="immobilized"),
    ])
    progress, reason = drfc.extract_progress_from_log(log)
    assert progress == pytest.approx(67.8492)
    assert reason == "immobilized"


def test_progress_is_the_maximum_not_the_last(tmp_path):
    """차가 뒤로 밀리거나 리셋돼도 '가장 멀리 간 지점'을 보여준다."""
    log = write_log(tmp_path, [
        TRACE.format(step=1, done="False", progress="80.0", status="in_progress"),
        TRACE.format(step=2, done="True", progress="5.0", status="off_track"),
    ])
    progress, reason = drfc.extract_progress_from_log(log)
    assert progress == pytest.approx(80.0)
    assert reason == "off_track"


def test_in_progress_is_not_reported_as_a_reason(tmp_path):
    """'진행중'은 실패 사유가 아니다."""
    log = write_log(tmp_path, [TRACE.format(step=1, done="False", progress="12.0", status="in_progress")])
    progress, reason = drfc.extract_progress_from_log(log)
    assert progress == pytest.approx(12.0)
    assert reason is None


def test_missing_log_is_handled(tmp_path):
    assert drfc.extract_progress_from_log(tmp_path / "없는파일.log") == (None, None)


def test_malformed_lines_are_skipped(tmp_path):
    log = write_log(tmp_path, [
        "쓰레기 줄",
        "SIM_TRACE_LOG:0,1,2",
        TRACE.format(step=9, done="True", progress="42.0", status="crashed"),
    ])
    assert drfc.extract_progress_from_log(log) == (pytest.approx(42.0), "crashed")


# ── metrics 우선, 로그는 보조 ─────────────────────────────────────────────


def test_metrics_take_priority_over_log(tmp_path):
    metrics = {"metrics": [
        {"completion_percentage": 100, "episode_status": "Lap complete"},
        {"completion_percentage": 45, "episode_status": "off_track"},
    ]}
    log = write_log(tmp_path, [TRACE.format(step=1, done="True", progress="1.0", status="crashed")])
    progress, reason = drfc.summarize_progress(metrics, log)
    assert progress == pytest.approx(100)
    assert reason == "off_track"  # 마지막 종료 상태


def test_empty_metrics_falls_back_to_log(tmp_path):
    """submission 18의 실제 상황: metrics가 빈 배열."""
    log = write_log(tmp_path, [TRACE.format(step=1, done="True", progress="67.8", status="immobilized")])
    progress, reason = drfc.summarize_progress({"metrics": []}, log)
    assert progress == pytest.approx(67.8)
    assert reason == "immobilized"


def test_no_metrics_and_no_log_yields_nothing():
    assert drfc.summarize_progress({"metrics": []}, None) == (None, None)


# ── 화면 문구 ─────────────────────────────────────────────────────────────


def result(progress=None, reason=None):
    return types.SimpleNamespace(best_progress_percent=progress, failure_reason=reason)


def test_summary_shows_progress_and_korean_reason():
    assert failure_summary(result(67.8492, "immobilized")) == "완주 실패 (67.8%) · 차량이 멈춤"


def test_summary_without_progress_keeps_it_simple():
    """진행률이 없는 예전 결과는 문구만 바꾼다."""
    assert failure_summary(result()) == "완주 실패"


def test_unknown_reason_is_shown_as_is():
    """모르는 사유를 감추면 원인 추적이 어려워진다."""
    assert failure_summary(result(30.0, "some_new_status")) == "완주 실패 (30.0%) · some_new_status"


def test_summary_handles_missing_result():
    assert failure_summary(None) == "완주 실패"
