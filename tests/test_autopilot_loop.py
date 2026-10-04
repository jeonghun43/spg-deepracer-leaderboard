"""autopilot 한 주기 검증 — 가짜 AWS·가짜 디스코드·가짜 DB로 (plan.md §1.2, tasks.md T9).

판단 자체는 test_autopilot_logic.py가 고정한다. 여기서는 판단을 **실행**하는 부분을 본다:

- 켜기 요청이 성공하면 start_requested를 남기고 요청 시각을 저장한다
- 켜기 요청이 실패해도 루프가 죽지 않고, 알림은 30분에 한 번만 남긴다
- 알림은 보낸 것만 "보냄"으로 표시한다. 디스코드가 안 되면 다음 주기에 다시 보낸다
- 웹훅이 없으면 보내지 않고, 보낸 것으로 표시하지도 않는다
"""

import datetime as dt
import types

import pytest

from app import autopilot
from app.autopilot_logic import MISCONFIGURED, START_FAILED, START_REQUESTED
from app.config import settings

NOW = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.timezone.utc)
WEBHOOK = "https://discord.example/webhook"


class FakeStore:
    def __init__(self, queued=1, last_seen=None, enabled=True):
        self.state = types.SimpleNamespace(
            enabled=enabled,
            instance_state=None,
            instance_state_at=None,
            target_worker_id=None,
            last_start_requested_at=None,
            attention_since=None,
        )
        self.queued = queued
        self.last_seen = last_seen
        self.events = []
        self.commits = 0

    def get_state(self):
        return self.state

    def queued_count(self):
        return self.queued

    def worker_last_seen(self, worker_id):
        assert worker_id == "ip-172-31-61-59"
        return self.last_seen

    def last_event_times(self):
        times = {}
        for e in self.events:
            times[e.kind] = max(times.get(e.kind, e.created_at), e.created_at)
        return times

    def add_event(self, kind, message, now):
        self.events.append(
            types.SimpleNamespace(kind=kind, message=message, source="web", created_at=now, notified_at=None)
        )

    def unsent_events(self, since, limit):
        return [e for e in self.events if e.notified_at is None and e.created_at >= since][:limit]

    def commit(self):
        self.commits += 1


class FakeAws:
    def __init__(self, state="stopped", start_error=None, behavior="stop"):
        self.state = state
        self.start_error = start_error
        self.behavior = behavior
        self.started = []

    def describe(self, instance_id):
        if isinstance(self.state, Exception):
            raise self.state
        return self.state, "ip-172-31-61-59.ap-northeast-2.compute.internal"

    def start(self, instance_id):
        if self.start_error:
            raise self.start_error
        self.started.append(instance_id)
        self.state = "pending"

    def shutdown_behavior(self, instance_id):
        if isinstance(self.behavior, Exception):
            raise self.behavior
        return self.behavior


class FakeSend:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    def __call__(self, url, content):
        self.sent.append((url, content))
        return self.ok


class AwsError(Exception):
    """botocore ClientError처럼 response["Error"]["Code"]를 가진 오류."""

    def __init__(self, code):
        super().__init__(f"An error occurred ({code}) ... arn:aws:iam::123456789012:user/drleader-autopilot")
        self.response = {"Error": {"Code": code}}


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "autopilot_instance_id", "i-0abc")
    monkeypatch.setattr(settings, "discord_webhook_url", WEBHOOK)


def test_중지된_서버에_대기가_있으면_켜고_알린다():
    store, aws, send = FakeStore(), FakeAws(), FakeSend()
    autopilot.run_cycle(store, aws, send, NOW)

    assert aws.started == ["i-0abc"]
    assert store.state.last_start_requested_at == NOW
    assert store.state.instance_state == "stopped"
    assert store.state.instance_state_at == NOW
    assert store.state.target_worker_id == "ip-172-31-61-59"
    assert [e.kind for e in store.events] == [START_REQUESTED]
    assert len(send.sent) == 1 and START_REQUESTED in send.sent[0][1]
    assert store.events[0].notified_at == NOW


def test_다음_주기에는_다시_켜지_않는다():
    store, aws, send = FakeStore(), FakeAws(), FakeSend()
    autopilot.run_cycle(store, aws, send, NOW)
    autopilot.run_cycle(store, aws, send, NOW + dt.timedelta(minutes=1))
    assert aws.started == ["i-0abc"]
    assert len(send.sent) == 1, "같은 알림을 다시 보내면 안 된다"


def test_워커가_살아_있으면_아무것도_하지_않는다():
    store = FakeStore(last_seen=NOW - dt.timedelta(seconds=20))
    aws, send = FakeAws(state="running"), FakeSend()
    autopilot.run_cycle(store, aws, send, NOW)
    assert aws.started == []
    assert store.events == []


def test_켜기_실패는_알리되_30분에_한_번():
    store = FakeStore()
    aws = FakeAws(start_error=AwsError("InsufficientInstanceCapacity"))
    send = FakeSend()
    for minute in range(0, 31):
        autopilot.run_cycle(store, aws, send, NOW + dt.timedelta(minutes=minute))
    failed = [e for e in store.events if e.kind == START_FAILED]
    assert len(failed) == 2, "0분과 30분에만 알린다"
    assert "InsufficientInstanceCapacity" in failed[0].message
    assert store.state.last_start_requested_at is None


def test_실패_알림에_계정_ID_같은_AWS_원문을_싣지_않는다():
    store = FakeStore()
    aws = FakeAws(start_error=AwsError("UnauthorizedOperation"))
    autopilot.run_cycle(store, aws, FakeSend(), NOW)
    assert "123456789012" not in store.events[0].message


def test_AWS_조회_실패해도_예외를_올리지_않는다():
    store, send = FakeStore(), FakeSend()
    autopilot.run_cycle(store, FakeAws(state=AwsError("RequestExpired")), send, NOW)
    assert [e.kind for e in store.events] == [START_FAILED]
    assert store.state.instance_state is None


def test_디스코드가_안_되면_보냄으로_표시하지_않고_다음_주기에_다시_보낸다():
    store, aws = FakeStore(), FakeAws()
    autopilot.run_cycle(store, aws, FakeSend(ok=False), NOW)
    assert store.events[0].notified_at is None

    later = NOW + dt.timedelta(minutes=1)
    send = FakeSend(ok=True)
    autopilot.run_cycle(store, aws, send, later)
    assert len(send.sent) == 1
    assert store.events[0].notified_at == later


def test_웹훅이_없으면_보내지_않고_표시도_하지_않는다(monkeypatch):
    monkeypatch.setattr(settings, "discord_webhook_url", "")
    store, send = FakeStore(), FakeSend()
    autopilot.run_cycle(store, FakeAws(), send, NOW)
    assert send.sent == []
    assert store.events[0].notified_at is None


def test_한_주기에_보내는_알림_수에는_상한이_있다():
    store, send = FakeStore(queued=0), FakeSend()
    for i in range(15):
        store.add_event("idle_stop", f"#{i}", NOW - dt.timedelta(minutes=15 - i))
    sent = autopilot.deliver_pending(store, send, WEBHOOK, NOW)
    assert sent == autopilot.MAX_NOTIFICATIONS_PER_CYCLE
    assert "#0" in send.sent[0][1], "오래된 것부터 보낸다"


def test_24시간보다_오래된_미발송_알림은_보내지_않는다():
    store, send = FakeStore(queued=0), FakeSend()
    store.add_event("idle_stop", "old", NOW - dt.timedelta(hours=25))
    assert autopilot.deliver_pending(store, send, WEBHOOK, NOW) == 0


def test_종료_시_동작이_terminate면_misconfigured를_남긴다():
    store = FakeStore()
    autopilot.check_shutdown_behavior(store, FakeAws(behavior="terminate"), NOW)
    assert [e.kind for e in store.events] == [MISCONFIGURED]


def test_종료_시_동작이_stop이면_조용하다():
    store = FakeStore()
    autopilot.check_shutdown_behavior(store, FakeAws(behavior="stop"), NOW)
    assert store.events == []


def test_종료_시_동작_확인_권한이_없어도_알린다():
    store = FakeStore()
    autopilot.check_shutdown_behavior(store, FakeAws(behavior=AwsError("UnauthorizedOperation")), NOW)
    assert [e.kind for e in store.events] == [MISCONFIGURED]
