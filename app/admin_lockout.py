"""로그인 무차별 대입·계산 자원 소모 방어 (admin-access-hardening.md §3.4).

**관리자와 참가자 로그인 둘 다** 이 모듈을 쓴다. 모듈 이름이 `admin_lockout`인 것은
관리자 화면을 지키려고 먼저 만들었기 때문이고(2026-08-03), 2026-08-10에 참가자 로그인에도
같은 장치를 붙였다. 이름을 바꾸지 않은 것은 `docs/study/03-auth.md`가 이 모듈을 이름째로
인용하고 있어서다 — 이름보다 인용 정합성을 택했다.

**왜 참가자 로그인에도 필요한가.** 비밀번호는 62자 알파벳 10자리라 추측이 불가능하다.
막으려는 것은 추측이 아니라 **비용**이다. `verify_password`(bcrypt)가 요청당 CPU를 수백 ms
물고, 운영 웹은 2코어·메모리 900MB 상한이다. 인증 없이 `/login`에 초당 수십 건만 던지면
웹이 통째로 응답을 멈춘다. 그래서 **잠긴 동안에는 bcrypt를 아예 돌리지 않는다.**

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

from starlette.requests import Request

from app.config import settings


@dataclass
class _Entry:
    failures: int = 0
    last_failure_at: float = 0.0
    # 0이면 잠금 없음. time.monotonic() 기준이라 시스템 시계를 바꿔도 영향받지 않는다.
    locked_until: float = 0.0
    # 이 항목에 적용된 잠금 길이(초). 관리자와 참가자의 정책이 달라서 전역값을 쓸 수 없고,
    # 항목마다 들고 있어야 _prune이 올바른 기준으로 정리한다.
    lockout_seconds: float = 0.0


_entries: dict[str, _Entry] = {}


def _default_lockout_seconds() -> float:
    return settings.admin_login_lockout_minutes * 60


def _now(now: float | None) -> float:
    return time.monotonic() if now is None else now


def client_ip(request: Request) -> str:
    """Caddy 뒤에 있으므로 X-Forwarded-For의 첫 값이 실제 접속자다.

    이 헤더는 위조할 수 있다 — 그래서 IP 잠금만 믿지 않고 아이디 기준 잠금을 함께 쓴다.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _prune(now: float) -> None:
    """만료된 항목을 지운다.

    항목 수가 많아야 수십 개라 별도 청소 스레드 없이 기록할 때마다 함께 정리한다.
    잠금이 풀렸거나, 마지막 실패 후 잠금 시간만큼 조용했으면 카운터를 버린다 —
    몇 달 전의 오타 한 번이 계속 남아 있을 이유가 없다.
    """
    for key, entry in list(_entries.items()):
        window = entry.lockout_seconds or _default_lockout_seconds()
        if max(entry.locked_until, entry.last_failure_at + window) <= now:
            del _entries[key]


def build_keys(ip: str, login_id: str, scope: str = "admin") -> list[str]:
    """IP 기준과 아이디 기준 두 개의 카운터 키를 만든다.

    `scope`로 관리자와 참가자의 카운터를 분리한다. **분리하지 않으면 같은 공유 네트워크
    (동아리방·학교 와이파이)에서 참가자가 오타를 반복했을 때 IP 키가 겹쳐 관리자까지
    잠긴다.** 대회 중에 관리자가 못 들어가는 것이 가장 곤란한 상황이다.
    """
    return [f"{scope}:ip:{ip}", f"{scope}:id:{login_id.strip().lower()}"]


def seconds_remaining(keys: list[str], now: float | None = None) -> int:
    """잠겨 있으면 남은 초, 아니면 0. 어느 한 키라도 잠겨 있으면 잠긴 것으로 본다."""
    current = _now(now)
    remaining = 0.0
    for key in keys:
        entry = _entries.get(key)
        if entry is not None:
            remaining = max(remaining, entry.locked_until - current)
    return int(remaining) + 1 if remaining > 0 else 0


def record_failure(
    keys: list[str],
    max_attempts: int | None = None,
    lockout_minutes: int | None = None,
    now: float | None = None,
) -> int:
    """실패를 1회 기록하고, 그 결과 잠겼다면 남은 초를 돌려준다.

    `max_attempts`·`lockout_minutes`를 주지 않으면 관리자 정책(`.env`의
    `ADMIN_LOGIN_*`)을 쓴다. 참가자 로그인은 더 느슨한 값을 넘긴다 — 참가자는
    비밀번호를 발급받아 붙여넣기 때문에 오타가 잦고, 대회 중에 잠기면 제출을 못 한다.
    """
    current = _now(now)
    _prune(current)
    limit = settings.admin_login_max_attempts if max_attempts is None else max_attempts
    window = (
        _default_lockout_seconds() if lockout_minutes is None else lockout_minutes * 60
    )
    for key in keys:
        entry = _entries.setdefault(key, _Entry())
        entry.failures += 1
        entry.last_failure_at = current
        entry.lockout_seconds = window
        if entry.failures >= limit:
            entry.locked_until = current + window
    return seconds_remaining(keys, current)


def reset(keys: list[str]) -> None:
    """로그인에 성공하면 양쪽 카운터를 모두 지운다."""
    for key in keys:
        _entries.pop(key, None)


def clear_all() -> None:
    """테스트 전용 — 전역 상태를 비운다."""
    _entries.clear()
