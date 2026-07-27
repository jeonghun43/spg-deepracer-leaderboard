"""하루 제출 카운트 보정값(관리자 조정) 계산 검증.

관리자 조정값은 절대값이 아니라 실제 완료 건수에 더해지는 델타다. 절대값으로 두면
조정 이후에 완료된 평가가 카운트에 반영되지 않아 하루 한도가 영영 걸리지 않는다.
"""

import datetime as dt
import types

from app.quota import get_daily_done_count

TODAY = dt.date(2026, 7, 25)


def make_db(done_count: int):
    """get_daily_done_count가 쓰는 db.execute(...).scalar_one()만 흉내 낸다."""
    return types.SimpleNamespace(
        execute=lambda stmt: types.SimpleNamespace(scalar_one=lambda: done_count)
    )


def make_team(adjustment=None, adjustment_date=None):
    return types.SimpleNamespace(
        id=1,
        daily_count_adjustment=adjustment,
        daily_count_adjustment_date=adjustment_date,
    )


def test_no_adjustment_uses_actual_count():
    assert get_daily_done_count(make_db(3), make_team(), TODAY) == 3


def test_adjustment_is_added_to_actual_count():
    # 관리자가 완료 2건인 팀의 카운트를 4로 지정 → 델타 +2가 저장된다
    team = make_team(adjustment=2, adjustment_date=TODAY)
    assert get_daily_done_count(make_db(2), team, TODAY) == 4
    # 그 뒤 평가 1건이 더 완료되면 카운트도 함께 올라간다 (절대값이면 4에 머물렀을 것)
    assert get_daily_done_count(make_db(3), team, TODAY) == 5


def test_adjustment_from_other_day_is_ignored():
    team = make_team(adjustment=4, adjustment_date=TODAY - dt.timedelta(days=1))
    assert get_daily_done_count(make_db(1), team, TODAY) == 1


def test_negative_adjustment_never_goes_below_zero():
    # 카운트를 0으로 되돌린 뒤 이전 완료 건이 정리되는 등으로 델타가 남아도 음수가 되지 않는다
    team = make_team(adjustment=-5, adjustment_date=TODAY)
    assert get_daily_done_count(make_db(2), team, TODAY) == 0
