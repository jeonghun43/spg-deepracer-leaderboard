"""metrics json 파싱 검증.

샘플 데이터는 실제 DRFC 평가가 남긴 evaluation-*.json 구조를 그대로 따른다.
"""

from worker.drfc import parse_evaluation_result

# 실제 DRFC 출력 (3바퀴 모두 완주한 케이스)
REAL_FINISHED = {
    "metrics": [
        {"completion_percentage": 100, "elapsed_time_in_milliseconds": 36798, "off_track_count": 0, "trial": 1},
        {"completion_percentage": 100, "elapsed_time_in_milliseconds": 36876, "off_track_count": 0, "trial": 2},
        {"completion_percentage": 100, "elapsed_time_in_milliseconds": 36895, "off_track_count": 0, "trial": 3},
    ]
}


def test_finished_sums_three_laps():
    status, lap_time, off_track = parse_evaluation_result(REAL_FINISHED, required_laps=3)
    assert status == "finished"
    assert lap_time == (36798 + 36876 + 36895) / 1000.0
    assert off_track == 0


def test_incomplete_lap_is_timeout():
    metrics = {
        "metrics": [
            {"completion_percentage": 100, "elapsed_time_in_milliseconds": 30000, "off_track_count": 1, "trial": 1},
            {"completion_percentage": 42, "elapsed_time_in_milliseconds": 60000, "off_track_count": 3, "trial": 2},
            {"completion_percentage": 100, "elapsed_time_in_milliseconds": 31000, "off_track_count": 0, "trial": 3},
        ]
    }
    status, lap_time, off_track = parse_evaluation_result(metrics, required_laps=3)
    assert status == "timeout"
    assert lap_time is None
    assert off_track == 4


def test_off_track_counts_are_totalled_for_finished_runs():
    metrics = {
        "metrics": [
            {"completion_percentage": 100, "elapsed_time_in_milliseconds": 40000, "off_track_count": 2, "trial": 1},
            {"completion_percentage": 100, "elapsed_time_in_milliseconds": 41000, "off_track_count": 1, "trial": 2},
            {"completion_percentage": 100, "elapsed_time_in_milliseconds": 39000, "off_track_count": 0, "trial": 3},
        ]
    }
    status, lap_time, off_track = parse_evaluation_result(metrics, required_laps=3)
    assert status == "finished"
    assert off_track == 3


def test_empty_metrics_is_timeout():
    status, lap_time, off_track = parse_evaluation_result({"metrics": []}, required_laps=3)
    assert status == "timeout"
    assert lap_time is None
    assert off_track == 0
