"""평가 서버 생존 판정 검증 (cloud-migration.md §5).

워커는 운영자 노트북에서 돌기 때문에 노트북이 꺼지면 평가만 멈춘다. 이때 참가자 화면에
"대기 중"을 정확히 띄우려면 하트비트 만료 판정이 정확해야 한다.
"""

import datetime as dt
import types

import pytest

from app.config import settings
from app.worker_status import get_worker_status


class FakeDB:
    """get_worker_status가 쓰는 db.execute(...).scalar_one_or_none()만 흉내 낸다."""

    def __init__(self, last_seen):
        self._last_seen = last_seen

    def execute(self, _stmt):
        return types.SimpleNamespace(scalar_one_or_none=lambda: self._last_seen)


def ago(**kwargs):
    return dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(**kwargs)


def test_recent_heartbeat_is_online():
    status = get_worker_status(FakeDB(ago(seconds=10)))
    assert status["online"] is True
    assert status["minutes_ago"] == 0


def test_stale_heartbeat_is_offline():
    status = get_worker_status(FakeDB(ago(minutes=30)))
    assert status["online"] is False
    assert status["minutes_ago"] == 30


def test_boundary_uses_configured_threshold(monkeypatch):
    monkeypatch.setattr(settings, "worker_heartbeat_stale_minutes", 3)
    assert get_worker_status(FakeDB(ago(minutes=2, seconds=50)))["online"] is True
    assert get_worker_status(FakeDB(ago(minutes=3, seconds=10)))["online"] is False


def test_no_heartbeat_at_all_is_offline():
    """워커가 한 번도 뜬 적 없으면(기능 도입 직후 포함) 대기 중으로 본다."""
    status = get_worker_status(FakeDB(None))
    assert status["online"] is False
    assert status["last_seen_at"] is None
    assert status["minutes_ago"] is None


@pytest.mark.parametrize("minutes", [0, 1, 5, 120])
def test_minutes_ago_is_never_negative(minutes):
    assert get_worker_status(FakeDB(ago(minutes=minutes)))["minutes_ago"] >= 0
