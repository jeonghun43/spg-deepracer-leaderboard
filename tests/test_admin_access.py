"""관리자 진입점 은닉 검증 (admin-access-hardening.md).

여기서 지키려는 경계는 두 가지다.

1. **은닉**: `/admin/login`은 존재하지 않고, 미인증 `/admin/*`는 리다이렉트가 아니라
   404여야 한다. 리다이렉트로 돌려주면 "여기 관리자 페이지가 있다"는 사실이 확인되고,
   숨긴 의미가 사라진다.
2. **잠금**: 비밀 경로가 유출됐을 때의 2차 방어선. IP 기준과 아이디 기준을 **둘 다**
   세야 한다 — IP는 `X-Forwarded-For` 위조로 우회되기 때문이다.
"""

import pytest
from fastapi.testclient import TestClient

from app import admin_lockout
from app.config import Settings, settings
from app.main import app


@pytest.fixture(autouse=True)
def _잠금_초기화():
    """잠금 카운터는 프로세스 전역이라 테스트끼리 새지 않게 매번 비운다."""
    admin_lockout.clear_all()
    yield
    admin_lockout.clear_all()


# ── 경로 정규화 (T124) ──────────────────────────────────────────────────
#
# .env에 사람이 손으로 넣는 값이라 흔한 실수를 흡수해야 한다.
# 특히 빈 문자열이 그대로 라우트로 등록되면 앱 자체가 깨진다.


@pytest.mark.parametrize(
    "입력, 기대",
    [
        ("/_ops/abc", "/_ops/abc"),
        ("_ops/abc", "/_ops/abc"),  # 앞 슬래시를 빠뜨림
        ("/_ops/abc/", "/_ops/abc"),  # 뒤 슬래시가 붙음
        ("  /_ops/abc  ", "/_ops/abc"),  # 복사하다 공백이 딸려옴
        ("_ops/abc/", "/_ops/abc"),  # 둘 다 틀림
        ("", "/admin/login"),  # 빈 값이면 기본값으로 되돌린다
        ("   ", "/admin/login"),
        ("/", "/admin/login"),  # 슬래시만 있으면 빈 값과 같다
    ],
)
def test_비밀_경로를_정규화한다(입력, 기대):
    assert Settings(admin_login_path=입력).admin_login_path == 기대


# ── 잠금 로직 (T129) ────────────────────────────────────────────────────
#
# 시간을 인자로 주입해 검증한다. 실제 시간을 기다리지 않기 위해서이기도 하고,
# time.monotonic() 기준이라 시스템 시계 변경에 흔들리지 않는다는 것도 함께 고정한다.


def _키():
    return admin_lockout.build_keys("203.0.113.9", "admin")


def test_상한_직전까지는_잠기지_않는다():
    키 = _키()
    for _ in range(settings.admin_login_max_attempts - 1):
        assert admin_lockout.record_failure(키, now=0.0) == 0
    assert admin_lockout.seconds_remaining(키, now=0.0) == 0


def test_상한에_닿으면_잠긴다():
    키 = _키()
    for _ in range(settings.admin_login_max_attempts):
        남은 = admin_lockout.record_failure(키, now=0.0)
    assert 남은 > 0
    assert admin_lockout.seconds_remaining(키, now=0.0) > 0


def test_잠금_시간이_지나면_풀린다():
    키 = _키()
    for _ in range(settings.admin_login_max_attempts):
        admin_lockout.record_failure(키, now=0.0)
    잠금초 = settings.admin_login_lockout_minutes * 60
    assert admin_lockout.seconds_remaining(키, now=잠금초 - 1) > 0
    assert admin_lockout.seconds_remaining(키, now=잠금초 + 1) == 0


def test_로그인에_성공하면_카운터가_지워진다():
    키 = _키()
    for _ in range(settings.admin_login_max_attempts - 1):
        admin_lockout.record_failure(키, now=0.0)
    admin_lockout.reset(키)
    # 초기화됐으므로 다시 상한 직전까지 시도할 수 있어야 한다.
    for _ in range(settings.admin_login_max_attempts - 1):
        assert admin_lockout.record_failure(키, now=0.0) == 0


def test_IP를_바꿔도_아이디_기준으로_잠긴다():
    """이 테스트가 이 모듈의 핵심이다.

    우리 앱은 Caddy 뒤에 있어 IP를 X-Forwarded-For에서 읽는데 그 헤더는 위조할 수 있다.
    매 시도마다 IP를 바꾸는 공격자도 아이디 기준 카운터에는 걸려야 한다.
    """
    for i in range(settings.admin_login_max_attempts):
        키 = admin_lockout.build_keys(f"198.51.100.{i}", "admin")
        admin_lockout.record_failure(키, now=0.0)

    새_IP = admin_lockout.build_keys("198.51.100.250", "admin")
    assert admin_lockout.seconds_remaining(새_IP, now=0.0) > 0


def test_다른_아이디는_같은_IP라도_따로_센다():
    """한 아이디가 잠겼다고 다른 아이디까지 잠기면 안 된다 (IP 기준은 별개로 동작)."""
    for _ in range(settings.admin_login_max_attempts - 1):
        admin_lockout.record_failure(admin_lockout.build_keys("203.0.113.9", "admin"), now=0.0)
    다른_아이디 = admin_lockout.build_keys("203.0.113.9", "operator")
    assert admin_lockout.seconds_remaining(다른_아이디, now=0.0) == 0


def test_아이디_대소문자와_공백은_같은_카운터로_본다():
    """'Admin '과 'admin'을 다르게 세면 카운터를 우회할 수 있다."""
    assert admin_lockout.build_keys("1.2.3.4", " Admin ") == admin_lockout.build_keys("1.2.3.4", "admin")


# ── 라우팅 (T125·T126) ──────────────────────────────────────────────────
#
# DB가 필요 없는 경계만 확인한다. 로그인 성공 이후의 여정은
# tests/verify_phase8.py가 실제 DB를 붙여 검증한다.

client = TestClient(app, raise_server_exceptions=False)


def test_옛_로그인_경로는_사라졌다():
    assert client.get("/admin/login").status_code == 404
    assert client.post("/admin/login", data={"login_id": "a", "password": "b"}).status_code == 404


@pytest.mark.parametrize("경로", ["/admin", "/admin/seasons/new", "/admin/seasons/1"])
def test_미인증_관리자_페이지는_404다(경로):
    """리다이렉트(303)면 존재가 드러난다. 없는 경로와 똑같이 보여야 한다."""
    응답 = client.get(경로, follow_redirects=False)
    assert 응답.status_code == 404


def test_없는_경로와_응답이_구별되지_않는다():
    관리자 = client.get("/admin", follow_redirects=False)
    아무거나 = client.get("/존재하지-않는-경로", follow_redirects=False)
    assert 관리자.status_code == 아무거나.status_code == 404
    assert 관리자.json() == 아무거나.json()


def test_비밀_경로에서는_로그인_폼이_열린다():
    응답 = client.get(settings.admin_login_path)
    assert 응답.status_code == 200
    # 폼이 자기 경로로 전송돼야 한다. 하드코딩된 /admin/login이 남아 있으면 여기서 걸린다.
    assert f'action="{settings.admin_login_path}"' in 응답.text


def test_문서_엔드포인트가_닫혀_있다():
    """/docs·/redoc·/openapi.json이 공개되면 관리자·워커 엔드포인트가 목록으로 드러난다."""
    for 경로 in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(경로).status_code == 404, f"{경로}가 열려 있다"


def test_비밀_경로가_openapi에_노출되지_않는다():
    """include_in_schema=False를 빼면 숨긴 주소가 스키마로 새어 나간다.

    HTTP로 /openapi.json을 받지 않고 `app.openapi()`를 직접 부른다. 위 테스트대로
    엔드포인트는 닫혀 있지만, **스키마 생성 자체는 여전히 일어난다**(FastAPI는 라우트
    목록에서 만들어낸다). 엔드포인트를 닫았다고 이 검사를 지우면, 나중에 문서를 다시
    열었을 때 비밀 경로가 새는 것을 아무도 못 잡는다. 두 겹을 따로 지킨다.
    """
    스키마 = app.openapi()
    assert settings.admin_login_path not in 스키마["paths"]


# ── 메뉴 노출 (T127) ────────────────────────────────────────────────────
#
# base.html만 따로 렌더해 조건을 검증한다. 실제 화면(/leaderboard 등)은 DB가 필요한데,
# 이 저장소의 .env는 운영 DB를 가리키므로 테스트에서 붙이지 않는다.


class _가짜요청:
    """base.html이 쓰는 request.session만 흉내 낸다."""

    def __init__(self, session: dict):
        self.session = session


def _네비게이션(session: dict) -> str:
    from app.render import templates

    return templates.get_template("base.html").render(request=_가짜요청(session))


def test_비로그인_방문자에게는_관리자_링크가_안_보인다():
    화면 = _네비게이션({})
    assert 'href="/admin"' not in 화면
    assert 'href="/leaderboard"' in 화면  # 다른 메뉴는 그대로 있어야 한다


def test_참가팀으로_로그인해도_관리자_링크는_안_보인다():
    assert 'href="/admin"' not in _네비게이션({"team_id": 7})


def test_관리자로_로그인하면_관리자_링크가_보인다():
    assert 'href="/admin"' in _네비게이션({"admin_id": 1})
