"""디스코드 웹훅으로 autopilot 이벤트를 보낸다 (plan.md §3.2).

웹훅 주소는 비밀값이다. 알면 누구나 그 채널에 글을 쓸 수 있다. 그래서 웹 서버의 autopilot
컨테이너에만 넘기고, 로그에도 찍지 않는다.
"""

from __future__ import annotations

import datetime as dt
import logging

import httpx

from app.autopilot_logic import EVENT_LABELS
from app.config import KST

logger = logging.getLogger("autopilot")


def format_message(kind: str, message: str, source: str, created_at: dt.datetime) -> str:
    """한국어 한 줄 + 이벤트 종류. 참가자 팀명·계정·비밀값은 넣지 않는다(호출하는 쪽이 문구에 넣지 않는다)."""
    label = EVENT_LABELS.get(kind, kind)
    when = created_at.astimezone(KST).strftime("%m-%d %H:%M")
    return f"**{label}** · {when} KST · `{kind}` · {source}\n{message}"


def send(webhook_url: str, content: str) -> bool:
    """보냈으면 True. 실패하면 False — 호출하는 쪽이 notified_at을 비워 두어 다음 주기에 다시 보낸다."""
    try:
        response = httpx.post(
            webhook_url,
            # allowed_mentions를 비워 @everyone 같은 문구가 들어가도 멘션이 터지지 않게 한다.
            json={"content": content[:1900], "allowed_mentions": {"parse": []}},
            timeout=10,
        )
    except httpx.HTTPError as exc:
        # 예외 문구에 웹훅 주소(비밀값)가 들어갈 수 있어 종류만 남긴다.
        logger.warning("디스코드 발송 실패: %s", type(exc).__name__)
        return False
    if response.status_code >= 300:
        logger.warning("디스코드 발송 실패: HTTP %s", response.status_code)
        return False
    return True
