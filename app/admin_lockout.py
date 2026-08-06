"""관리자 로그인 무차별 대입 방어 (admin-access-hardening.md §3.4).

**왜 IP만 세지 않는가.** 우리 웹은 Caddy 뒤에 있어 클라이언트 IP를 `X-Forwarded-For`
헤더에서 읽는데, 이 헤더는 요청자가 임의로 넣을 수 있다. 헤더를 바꿔가며 IP 잠금을
우회하더라도 로그인 아이디 기준 카운터에 걸리게 해, 계정 쪽이 실질적인 방어선이 된다.

**왜 프로세스 메모리인가.** 운영 웹은 컨테이너 1개·uvicorn 워커 1개로 돌아
(`Dockerfile`의 CMD에 `--workers`가 없다) 공유 저장소가 필요 없다. 재시작하면
카운터가 초기화되지만 공격자가 우리 컨테이너를 재시작시킬 수단이 없으므로 감수할 수 있고,
오히려 관리자가 자기 계정을 잠갔을 때 `docker compose restart web`이 탈출구가 된다.
"""

import time
from dataclasses import dataclass

from app.config import settings


@dataclass
class _Entry:
    failures: int = 0
    last_failure_at: float = 0.0
    # 0이면 잠금 없음. time.monotonic() 기준이라 시스템 시계를 바꿔도 영향받지 않는다.
    locked_until: float = 0.0


_entries: dict[str, _Entry] = {}


def _lockout_seconds() -> float:
    return settings.admin_login_lockout_minutes * 60


def _now(now: float | None) -> float:
    return time.monotonic() if now is None else now


def _prune(now: float) -> None:
    """만료된 항목을 지운다.

    항목 수가 많아야 수십 개라 별도 청소 스레드 없이 기록할 때마다 함께 정리한다.
    잠금이 풀렸거나, 마지막 실패 후 잠금 시간만큼 조용했으면 카운터를 버린다 —
    몇 달 전의 오타 한 번이 계속 남아 있을 이유가 없다.
    """
    for key, entry in list(_entries.items()):
        if max(entry.locked_until, entry.last_failure_at + _lockout_seconds()) <= now:
            del _entries[key]


def build_keys(ip: str, login_id: str) -> list[str]:
    """IP 기준과 아이디 기준 두 개의 카운터 키를 만든다."""
    return [f"ip:{ip}", f"id:{login_id.strip().lower()}"]


def seconds_remaining(keys: list[str], now: float | None = None) -> int:
    """잠겨 있으면 남은 초, 아니면 0. 어느 한 키라도 잠겨 있으면 잠긴 것으로 본다."""
    current = _now(now)
    remaining = 0.0
    for key in keys:
        entry = _entries.get(key)
        if entry is not None:
            remaining = max(remaining, entry.locked_until - current)
    return int(remaining) + 1 if remaining > 0 else 0


def record_failure(keys: list[str], now: float | None = None) -> int:
    """실패를 1회 기록하고, 그 결과 잠겼다면 남은 초를 돌려준다."""
    current = _now(now)
    _prune(current)
    for key in keys:
        entry = _entries.setdefault(key, _Entry())
        entry.failures += 1
        entry.last_failure_at = current
        if entry.failures >= settings.admin_login_max_attempts:
            entry.locked_until = current + _lockout_seconds()
    return seconds_remaining(keys, current)


def reset(keys: list[str]) -> None:
    """로그인에 성공하면 양쪽 카운터를 모두 지운다."""
    for key in keys:
        _entries.pop(key, None)


def clear_all() -> None:
    """테스트 전용 — 전역 상태를 비운다."""
    _entries.clear()
