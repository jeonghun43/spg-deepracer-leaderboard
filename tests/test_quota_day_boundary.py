"""하루 제출 한도를 어느 날로 세는지 검증 (spec.md §8, 2026-09-11 결정).

기준은 **제출 시각**이다. 23:59에 올린 제출이 대기열 때문에 자정 이후에 끝나도 올린 날의 횟수다.
실제 DB 없이 확인하기 위해, get_daily_done_count가 실행하려는 SQL을 가로채 조건 컬럼과 경계값을 본다.
"""

import datetime as dt
import types

from sqlalchemy.dialects import postgresql

from app.config import KST
from app.quota import get_daily_done_count

ON_DATE = dt.date(2026, 9, 10)


def compile_count_query():
    captured = {}

    def execute(stmt):
        captured["stmt"] = stmt
        return types.SimpleNamespace(scalar_one=lambda: 0)

    db = types.SimpleNamespace(execute=execute)
    team = types.SimpleNamespace(id=1, daily_count_adjustment=None, daily_count_adjustment_date=None)
    get_daily_done_count(db, team, ON_DATE)
    return captured["stmt"].compile(dialect=postgresql.dialect())


def day_bounds():
    compiled = compile_count_query()
    values = sorted(v for v in compiled.params.values() if isinstance(v, dt.datetime))
    assert len(values) == 2
    return values


def test_counts_by_submission_time_not_finish_time():
    sql = str(compile_count_query())
    assert "submissions.submitted_at >=" in sql
    assert "submissions.submitted_at <" in sql
    assert "finished_at" not in sql


def test_day_is_kst_midnight_to_next_midnight():
    start, end = day_bounds()
    assert start == dt.datetime(2026, 9, 10, 0, 0, tzinfo=KST)
    assert end == dt.datetime(2026, 9, 11, 0, 0, tzinfo=KST)


def test_submission_at_2359_counts_for_that_day():
    start, end = day_bounds()
    submitted = dt.datetime(2026, 9, 10, 23, 59, tzinfo=KST)
    finished = dt.datetime(2026, 9, 11, 0, 10, tzinfo=KST)
    assert start <= submitted < end
    # 완료 시각 기준이었다면 이 제출은 다음 날 횟수로 넘어갔다
    assert not (start <= finished < end)


def test_exact_midnight_belongs_to_next_day():
    start, end = day_bounds()
    assert not (start <= dt.datetime(2026, 9, 11, 0, 0, tzinfo=KST) < end)
