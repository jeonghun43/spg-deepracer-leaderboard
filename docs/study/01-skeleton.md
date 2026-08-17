# 1단계. 앱의 골격 — `main.py`, `config.py`, `db.py`, `render.py`

> 이 단계의 목표: **브라우저 주소창에 `https://.../leaderboard`를 치고 엔터를 누른 순간부터
> 화면이 뜰 때까지, 내 코드의 어느 줄이 어떤 순서로 실행되는지**를 끊김 없이 설명할 수 있게 되는 것.
> 그리고 **"코드에 안 적힌 URL이 어떻게 라우트로 등록되는가"** 를 이해하는 것.

---

## 0. 그 전에 — 웹 서버란 대체 무엇인가

### 무엇을(What)

**[쉬움]**
웹 서버는 **자판기**다. 손님(브라우저)이 버튼(URL)을 누르면, 자판기가 그에 맞는 물건(HTML)을 내놓는다.
자판기는 늘 켜져 있고, 누가 버튼을 누를 때까지 가만히 기다린다.

**[전공]**
웹 서버는 TCP 소켓을 특정 포트(여기선 8000)에 **bind → listen** 해두고,
클라이언트가 connect 하면 **HTTP 프로토콜**에 따라 텍스트를 주고받는 프로그램이다.
HTTP 요청은 결국 이렇게 생긴 평문이다:

```http
GET /leaderboard HTTP/1.1
Host: leaderboard.example.com
Cookie: session=eyJ0ZWFtX2lkIjo2fQ...
```

그리고 응답도 평문이다:

```http
HTTP/1.1 200 OK
Content-Type: text/html; charset=utf-8

<!doctype html><html lang="ko">...
```

**우리가 쓴 모든 프레임워크 코드는 결국 "이 텍스트를 파싱해서 파이썬 객체로 만들고,
파이썬 객체를 다시 이 텍스트로 만드는 일"의 자동화다.** 이걸 잊으면 프레임워크가 마법처럼 보인다.

### 왜(Why) — 왜 소켓을 직접 안 짜고 FastAPI를 쓰는가

소켓부터 직접 짜면 다음을 전부 스스로 해야 한다:
HTTP 파싱, keep-alive, chunked encoding, multipart 파싱, 쿠키 파싱, 라우팅,
동시 접속 처리, 타임아웃, 에러 응답 포맷…
이건 이미 수천 명이 수년간 다듬어 놓은 영역이라, 직접 짜면 반드시 보안 구멍이 생긴다.

### 어떻게(How) — 계층 구조

```
[브라우저]
   │ HTTPS
   ▼
[Caddy]            ← 운영에서만. TLS 종료 + 리버스 프록시 (7단계)
   │ HTTP + X-Forwarded-For
   ▼
[uvicorn]          ← 실제로 소켓을 열고 HTTP를 파싱하는 "서버"
   │ ASGI 인터페이스 (scope, receive, send)
   ▼
[Starlette 미들웨어 체인]   ← SessionMiddleware
   │
   ▼
[FastAPI 라우터]   ← URL을 보고 어느 파이썬 함수를 부를지 결정
   │
   ▼
[내가 쓴 함수]  예: leaderboard.season_leaderboard()
```

**핵심**: `uvicorn`이 "서버"고, `FastAPI`는 "서버가 부르는 애플리케이션"이다. 둘은 다른 물건이다.
`Dockerfile` 마지막 줄이 이걸 그대로 보여준다:

```dockerfile
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```

`app.main:app` = "`app/main.py` 파일 안의 `app` 이라는 변수를 가져다 실행해라."

> **중요**: `--workers` 옵션이 없다 → **uvicorn 워커 프로세스가 1개**다.
> 이 사실이 3단계에서 `admin_lockout.py`가 "프로세스 메모리에 카운터를 둬도 되는" 근거가 된다.
> **배포 설정 한 줄이 애플리케이션 설계의 전제가 되는 예다.**

---

## 1. WSGI vs ASGI — FastAPI가 `async def`를 쓸 수 있는 이유

### 무엇을(What)

**[쉬움]**
식당에 비유하자.

- **WSGI(옛날 방식, Flask/Django)**: 웨이터 한 명이 손님 한 명을 끝까지 담당한다.
  손님이 "10분 걸리는 요리"를 시키면 그 웨이터는 10분 동안 아무것도 못 한다.
- **ASGI(요즘 방식, FastAPI)**: 웨이터가 주문을 넣고 **주방이 요리하는 동안 다른 테이블을 받는다.**

**[전공]**

| | WSGI | ASGI |
|---|---|---|
| 호출 형태 | `app(environ, start_response)` — 동기 함수 | `await app(scope, receive, send)` — 코루틴 |
| 동시성 모델 | 프로세스/스레드 풀 | 단일 스레드 이벤트 루프 + 코루틴 |
| WebSocket | 불가 | 가능 |
| 스트리밍 요청 본문 | 제한적 | `receive()` 로 청크 단위 수신 |

ASGI의 `receive`가 **청크 단위**라는 점이 4단계(파일 업로드)에서 결정적으로 중요해진다.
`await model_file.read(1024*1024)` 가 가능한 근본 이유가 여기 있다.

### 왜(Why) — 우리 서비스에 ASGI가 필요한가?

동시 접속 10명 규모에선 WSGI로도 충분하다. 그럼에도 이득은 있다:
1. 500MB 파일 업로드를 메모리에 다 올리지 않고 스트리밍으로 받는다.
2. 워커가 올리는 영상(`/internal/.../video`)도 같은 방식으로 받는다.

**중요한 함정**: FastAPI에서 `def`(동기)로 선언한 라우트는 **스레드풀에서** 실행되고,
`async def`로 선언한 라우트는 **이벤트 루프에서** 실행된다.
`async def` 안에서 블로킹 I/O(동기 SQLAlchemy 쿼리, `time.sleep`)를 하면
**이벤트 루프 전체가 멈춘다** = 모든 사용자의 요청이 멈춘다.

**우리 코드에서 `async def`는 딱 3곳이다** — 전부 파일을 청크로 읽어야 하는 곳:

```python
# app/routers/submissions.py:92
async def submit_upload(...):
    while chunk := await model_file.read(1024 * 1024):

# app/routers/internal.py:58
async def upload_video(...):
    while chunk := await video.read(1024 * 1024):

# app/routers/internal.py:96
async def upload_metrics(...):
    content = await metrics.read()
```

나머지는 전부 `def`다. **동기 SQLAlchemy를 쓰므로 의도적으로 옳은 선택이다.**

> **면접 단골 질문**: "FastAPI에서 `def`와 `async def` 중 뭘 써야 하나요?"
> 정답: "안에서 `await`할 게 없으면 `def`. 동기 DB 드라이버를 쓴다면 `def`가 더 안전하다."

---

## 2. `app/main.py` — 한 줄씩 완전 분해

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.routers import admin, auth, internal, leaderboard, submissions

# 자동 생성 문서는 공개하지 않는다. API를 쓰는 외부 소비자가 없고(워커는 고정된 /internal
# 경로만 호출한다), 열어두면 관리자·워커 엔드포인트의 존재와 요청 형식이 그대로 드러난다.
app = FastAPI(
    title="SPG DeepRacer Leaderboard",
    docs_url=None,     # /docs (Swagger UI)
    redoc_url=None,    # /redoc (ReDoc) — 빠뜨리기 쉽다. 문서 UI는 두 개다
    openapi_url=None,  # /openapi.json (스키마 원본)
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    https_only=settings.session_https_only,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
settings.videos_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media/videos", StaticFiles(directory=str(settings.videos_dir)), name="videos")

app.include_router(auth.router)
app.include_router(submissions.router)
app.include_router(leaderboard.router)
app.include_router(admin.router)
# 관리자 로그인 폼 — .env의 ADMIN_LOGIN_PATH가 정하는 비밀 경로에 붙는다.
app.include_router(admin.login_router)
app.include_router(internal.router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
```

**37줄뿐이다.** 이게 이 프로젝트의 "조립 설명서"다. 하나씩 보자.

---

### 2-1. `app = FastAPI(...)`

**무엇을**: ASGI 애플리케이션 객체를 하나 만든다.

**[쉬움]** 자판기 본체를 하나 사 온 것. 아직 안에 물건은 안 넣었다.

**[전공]** `FastAPI`는 `Starlette`을 상속한 클래스다. 이 객체가 하는 일:
- `routes` 리스트를 갖고 있다가 요청이 오면 URL 패턴 매칭
- `user_middleware` 스택을 갖고 있다가 요청/응답을 감싼다
- 타입 힌트를 읽어 **자동 검증 + OpenAPI 스키마 생성**

### 자동 생성 문서를 끄는 이유 (2026-08-06 적용)

FastAPI는 **문서 엔드포인트 세 개를 기본으로 연다.**

| 경로 | 정체 |
|---|---|
| `/docs` | Swagger UI — 브라우저에서 API를 눌러볼 수 있는 화면 |
| `/redoc` | ReDoc — 같은 내용의 다른 UI. **이게 있는 걸 자주 잊는다** |
| `/openapi.json` | 위 둘이 읽어가는 **스키마 원본**. 모든 경로·파라미터·응답 형식이 JSON 한 덩어리 |

**[쉬움]** 가게 문을 잠갔는데 **설계도면을 창문에 붙여둔 것**과 같다. 문은 안 열리지만
"안에 금고가 어디 있고 뒷문은 몇 개인지"가 다 보인다.

**[전공]** 이 프로젝트에서 이걸 여는 이득이 사실상 없다. API를 소비하는 외부 클라이언트가
없기 때문이다 — 참가자는 HTML 화면을 쓰고, 워커는 `/internal/*`의 **고정된 몇 개 경로만**
호출한다. 반면 열어두면 `/internal/*`과 `/admin/*`의 존재·요청 형식·필수 필드가 목록으로
드러난다. **이득 0, 비용 > 0이면 끈다.**

```python
app = FastAPI(
    title="SPG DeepRacer Leaderboard",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
```

> **[전공] 셋 중 `openapi_url` 하나만 꺼도 나머지 둘이 함께 닫힌다.** FastAPI의
> `setup()`이 UI 라우트를 이렇게 등록하기 때문이다.
>
> ```python
> # fastapi/applications.py
> if self.openapi_url:                        # 스키마 라우트
> if self.openapi_url and self.docs_url:      # Swagger
> if self.openapi_url and self.redoc_url:     # ReDoc
> ```
>
> UI는 스키마를 읽어야 렌더되니 당연한 종속이다. 그래도 **셋 다 명시했다** — 이 종속을
> 모르는 사람이 코드를 읽으면 "왜 `docs_url`은 안 껐지?"에서 멈춘다.

### 그래도 `include_in_schema=False`를 지운 게 아니다 — 두 겹을 따로 지킨다

비밀 경로를 숨겼는데 `/openapi.json`에 그 경로가 실려 나가면 **은닉이 통째로 무의미해진다.**
그래서 `admin.py`가 `include_in_schema=False`를 준다(§2-5).

문서를 껐으니 이 검사도 필요 없어 보이지만, **아니다.** 엔드포인트를 닫아도
**스키마 생성 자체는 여전히 일어난다** — FastAPI는 라우트 목록에서 그때그때 만들어내고,
`app.openapi()`를 부르면 `openapi_url` 설정과 무관하게 21개 경로가 그대로 나온다. 지금
그것을 HTTP로 내보내지 않을 뿐이다.

> **[전공] 이것이 "심층 방어(defense in depth)"다.** 바깥 겹(문서 엔드포인트)이 언젠가 다시
> 열릴 수 있다고 가정하고, 안쪽 겹(`include_in_schema=False`)을 그대로 둔다. 한 겹이 뚫려도
> 비밀 경로는 새지 않는다.

그래서 테스트도 **두 개**다 (`tests/test_admin_access.py`).

| 테스트 | 지키는 것 | 방법 |
|---|---|---|
| `test_문서_엔드포인트가_닫혀_있다` | 바깥 겹 | 세 경로가 **404**인지 |
| `test_비밀_경로가_openapi에_노출되지_않는다` | 안쪽 겹 | `app.openapi()`를 **직접 호출**해 `paths`에 없는지 |

두 번째가 HTTP 요청 대신 `app.openapi()`를 부르는 이유가 여기 있다. `client.get("/openapi.json")`
으로 짜면 이제 404가 돌아와 `스키마["paths"]`에서 **KeyError로 터진다.** 그때 "문서를 껐으니
필요 없는 테스트"라며 지우면 안쪽 겹을 감시하는 눈이 사라진다.

---

### 2-2. `app.add_middleware(SessionMiddleware, ...)`

**무엇을**: 모든 요청/응답을 감싸는 "겹"을 하나 추가한다.

**[쉬움]**
미들웨어는 **양파 껍질**이다. 요청이 안으로 들어갈 때 껍질을 하나씩 통과하고,
응답이 나올 때 다시 반대로 통과한다.

```
요청 →  [세션 껍질]  →  [내 함수]  →  [세션 껍질]  → 응답
        쿠키 읽어서                    바뀐 세션을
        request.session에              쿠키로 구워서
        넣어줌                         Set-Cookie 헤더에 붙임
```

**[전공]**
Starlette 미들웨어는 ASGI 앱 래퍼다:

```python
class SomeMiddleware:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        # 요청 전처리
        await self.app(scope, receive, wrapped_send)   # 안쪽 호출
        # 응답 후처리는 wrapped_send 안에서
```

`SessionMiddleware`가 하는 일 (내부적으로 `itsdangerous` 사용 — `requirements.txt`에 있다):
1. 요청 헤더에서 `Cookie: session=<값>` 을 읽는다
2. 값을 `secret_key`로 **서명 검증**한다. 위조면 무시하고 빈 세션으로 시작
3. base64 디코드 → JSON 파싱 → `request.session` 이라는 **dict**로 만들어 준다
4. 핸들러 실행
5. `request.session`이 바뀌었으면 다시 JSON→base64→서명 → `Set-Cookie` 헤더로 응답에 붙인다

**여기가 핵심 오해 포인트다.**

> **세션 데이터는 서버가 아니라 브라우저 쿠키 안에 들어있다.**
> 서버는 아무것도 저장하지 않는다. 서명만 검증한다.

**base64는 암호화가 아니다.** 누구나 디코드해서 내용을 볼 수 있고, 바꿀 수만 없다(서명 때문).
→ 그래서 이 프로젝트가 세션에 `team_id`/`admin_id`(정수)만 넣는다. 3단계에서 더 깊게 다룬다.

**`https_only=settings.session_https_only` 의 의미**

쿠키에 `Secure` 속성을 붙일지 여부다. `Secure` 쿠키는 **HTTPS 연결에서만 전송**된다.
- 도메인 + Caddy로 공개 운영할 땐 `true` (`docker-compose.prod.yml`의 기본값이 `true`다)
- 로컬에서 `http://localhost:8000`으로 테스트할 땐 `false`
  → `true`인데 http로 접속하면 브라우저가 쿠키를 아예 저장 안 해서 **로그인이 무한 반복**된다

`config.py`에 이 함정이 주석으로 정확히 적혀 있다.

**미들웨어 순서 주의**: `add_middleware`는 **스택에 쌓는다(LIFO)**.
나중에 추가한 것이 **바깥쪽**이 된다. 지금은 하나뿐이라 문제없다.

---

### 2-3. `app.mount("/static", StaticFiles(...))`

**무엇을**: `/static/*` 으로 들어오는 요청은 라우터를 거치지 않고 **파일을 그대로** 내보낸다.

**[쉬움]**
CSS 파일이나 동영상은 계산할 게 없다. 그냥 파일을 통째로 주면 된다.

**[전공]**
`mount`는 라우팅이 아니라 **서브 애플리케이션 마운트**다. `/static`으로 시작하는 모든 경로는
`StaticFiles`라는 별도 ASGI 앱에 통째로 위임된다. `include_router`와는 근본적으로 다르다.

`StaticFiles`가 해주는 것:
- MIME 타입 추론 (`.css` → `text/css`, `.js` → `text/javascript`, `.mp4` → `video/mp4`)
- `Last-Modified` / `ETag` 헤더 → 브라우저 캐싱
- **Range 요청 지원** ← 동영상 재생에 필수
- 디렉터리 탈출(`../../etc/passwd`) 방지

두 개를 mount한 이유가 다르다:

| 마운트 | 디렉터리 | 왜 |
|---|---|---|
| `/static` | `app/static` | 코드와 함께 배포되는 CSS·**upload.js**. 이미지에 포함됨 |
| `/media/videos` | `settings.videos_dir` = `storage/videos` | **런타임에 워커가 만들어내는 파일**. 볼륨으로 마운트됨 |

`settings.videos_dir.mkdir(parents=True, exist_ok=True)` 가 **mount보다 먼저** 호출되는 이유:
`StaticFiles(directory=...)`는 생성 시점에 디렉터리 존재를 확인하고, 없으면 예외를 던진다.

**보안 관점**: `/media/videos`는 인증 없이 누구나 접근 가능하다.
이건 **의도된 것**이다(spec: 리더보드/영상은 완전 공개).

---

### 2-4. `app.include_router(...)` — 라우터가 5개인 이유

```python
app.include_router(auth.router)          # 팀 로그인 (/login, /logout)
app.include_router(submissions.router)   # /submit
app.include_router(leaderboard.router)   # /, /leaderboard/*
app.include_router(admin.router)         # /admin/*  ← prefix
app.include_router(admin.login_router)   # .env의 비밀 경로  ← prefix 없음
app.include_router(internal.router)      # /internal/*  ← prefix, 워커 전용
```

**[전공]**
`APIRouter`는 라우트를 담는 **컨테이너**일 뿐이고, 실제 매칭은 `app.routes` 리스트에서 일어난다.
`include_router`는 라우터의 라우트들을 `prefix`/`tags`를 붙여 앱 라우트 리스트에 복사한다.

**왜 `admin`에서 라우터를 두 개 꺼내는가?**

`app/routers/admin.py` 상단 주석이 답한다:
```python
router = APIRouter(prefix="/admin", tags=["admin"])
# 로그인 폼은 /admin 아래가 아니라 .env로 지정한 비밀 경로에 붙는다 (파일 끝에서 등록).
# /admin/*를 통째로 감출 때 예외를 파지 않아도 되고, 나중에 프록시에서 /admin/*를
# 차단하는 선택지도 열어두기 위해서다.
login_router = APIRouter(tags=["admin"])
```

**핵심 아이디어**: `/admin/*`는 **전부** 인증이 필요하다(미인증이면 404).
로그인 폼이 그 안에 있으면 "로그인 폼만 예외" 라는 구멍을 파야 한다.
아예 **다른 prefix 밖으로 빼면** 규칙에 예외가 없어진다.

> **[전공] 이것이 좋은 설계의 신호다.** "예외를 만드는 대신 구조를 바꾼다."
> 예외는 시간이 지나면 반드시 잊히고, 잊힌 예외가 보안 구멍이 된다.

**등록 순서가 중요한 이유**: Starlette은 `app.routes`를 **위에서부터 순서대로 스캔**해
첫 번째로 매칭되는 것을 쓴다. 그래서 이런 주석이 있다:

```python
# app/routers/leaderboard.py:108
# 주의: `/leaderboard/{season_id}`보다 먼저 선언해야 한다. FastAPI는 선언 순서로
# 매칭하므로 뒤에 두면 "seasons"를 int로 파싱하려다 422가 난다.
@router.get("/leaderboard/seasons")
```

**반드시 이해하라.** `/leaderboard/{season_id}`가 먼저 등록되면,
`/leaderboard/seasons` 요청이 `season_id="seasons"`로 매칭되고,
`season_id: int` 타입 힌트 때문에 파싱 실패 → **422 Unprocessable Entity**.

> 다른 프레임워크(Django 등)는 정적 세그먼트를 동적 세그먼트보다 우선하기도 한다.
> **Starlette은 순수하게 선언 순서다.** 프레임워크마다 다르므로 외우지 말고 확인하라.

---

### 2-5. **`add_api_route` — 실행 시점에 URL을 정하는 법**

`app/routers/admin.py` 맨 끝:

```python
# ── 비밀 경로 등록 ────────────────────────────────────────────────────────
#
# 데코레이터에는 상수만 쓸 수 있어 `add_api_route`로 붙인다.
# `include_in_schema=False`가 중요하다 — 이걸 빼면 FastAPI가 /docs와 /openapi.json에
# 경로를 그대로 실어 보내, 숨긴 주소가 공개 문서에서 새어 나간다.
login_router.add_api_route(
    settings.admin_login_path, admin_login_form, methods=["GET"], include_in_schema=False
)
login_router.add_api_route(
    settings.admin_login_path, admin_login_submit, methods=["POST"], include_in_schema=False
)
```

### 무엇을(What)

**[쉬움]**
보통 URL은 코드에 **고정된 글자**로 적는다: `@router.get("/login")`.
그런데 이 주소는 **설정 파일에서 읽어와야** 한다. 배포마다 다른 비밀 주소니까.
글자를 고정으로 적을 수 없으니, **"나중에 붙이는" 방법**을 쓴다.

**[전공]**
데코레이터는 **정의 시점에 평가되는 표현식**이다. 상수든 변수든 넣을 수는 있지만,
`@router.get(settings.admin_login_path)` 라고 쓰면 동작은 해도
"이 함수의 URL이 무엇인지 코드만 봐서는 모른다"는 점이 데코레이터의 장점(가독성)을 없앤다.

`add_api_route(path, endpoint, methods=..., ...)` 는 데코레이터가 내부적으로 하는 일을
**직접 호출**하는 것이다. 실제로 `@router.get(path)` 는 대략 이렇게 구현되어 있다:

```python
def get(self, path, **kwargs):
    def decorator(func):
        self.add_api_route(path, func, methods=["GET"], **kwargs)
        return func
    return decorator
```

**즉 데코레이터는 `add_api_route`의 문법 설탕(syntactic sugar)일 뿐이다.**

### 어떻게(How) — 실행 순서

```
uvicorn이 app.main 을 import
  ↓
from app.config import settings          → .env 읽고 admin_login_path 확정
  ↓
from app.routers import admin            → admin.py 실행
     └ 파일 맨 끝의 add_api_route 두 줄 실행
       → login_router.routes 에 "/_ops/k7f3q9x2p1" 등록
  ↓
app.include_router(admin.login_router)   → app.routes 로 복사
  ↓
서버 기동 완료. 이제 그 URL이 살아있다.
```

**설정이 라우팅을 결정한다.** 이건 강력하지만 위험하기도 하다:
`.env`를 바꾸면 **URL 자체가 바뀐다.** 그래서 재시작이 필요하다.

**그리고 이 때문에 `config.py`에 검증기가 필요해진다:**
```python
@field_validator("admin_login_path")
@classmethod
def _normalize_admin_login_path(cls, value: str) -> str:
    """사람이 .env에 손으로 넣는 값이라 흔한 실수를 흡수한다.
    특히 빈 문자열을 그대로 라우트로 등록하면 앱이 깨지므로 기본값으로 되돌린다."""
```
**빈 문자열을 `add_api_route("")` 에 넘기면 앱이 깨진다.** §3-5에서 자세히.

### `include_in_schema=False` — 은닉의 완성

이걸 빼면 스키마에 이렇게 실린다.

```python
>>> app.openapi()["paths"]["/_ops/k7f3q9x2p1"]
{"get": {...}, "post": {...}}
```

**비밀 경로가 API 문서에 그대로 실려 나간다.** 은닉이 완전히 무의미해진다.

> 지금은 문서 엔드포인트를 꺼서(§2-1) 이게 HTTP로 나가지는 않는다. 하지만 **스키마는
> 여전히 만들어지고 있고**, 문서를 다시 켜는 순간 그대로 노출된다. 그래서 이 줄은
> 남아 있어야 한다.

`tests/test_admin_access.py`가 이걸 감시한다:
```python
def test_비밀_경로가_openapi에_노출되지_않는다():
    """include_in_schema=False를 빼면 숨긴 주소가 스키마로 새어 나간다."""
    스키마 = app.openapi()
    assert settings.admin_login_path not in 스키마["paths"]
```

> **[전공] 이런 테스트를 "회귀 방지 테스트(regression test)"라고 한다.**
> 기능을 검증하는 게 아니라 **"이 실수를 다시 하지 않게" 못을 박는 것**이다.
> 주석만 남기면 반드시 잊힌다. 테스트는 CI에서 소리를 낸다.

---

### 2-6. `/healthz`

```python
@app.get("/healthz")
def healthz():
    return {"status": "ok"}
```

**무엇을**: "나 살아있어?"에 답하는 최소 엔드포인트.

**[쉬움]** 병원에서 "손가락 움직여 보세요" 하는 것. 살아있는지만 확인.

**[전공]**
컨테이너 오케스트레이터, 로드밸런서, 모니터링 도구가 주기적으로 때리는 경로다.
관례적으로 `/healthz` 또는 `/health`. (`z` 접미사는 구글 내부 관례에서 유래)

**지금 이 구현의 한계**: DB가 죽어도 `{"status":"ok"}`를 반환한다.
"프로세스가 살아있다"만 알려주지 "서비스가 정상이다"는 알려주지 않는다.

**하지만 이건 의도적일 수 있다.** liveness probe에 DB 체크를 넣으면
DB가 잠깐 흔들릴 때 웹 컨테이너가 재시작되는 **연쇄 장애**가 난다.
정석은 **liveness는 가볍게(현재), readiness는 무겁게(DB 체크)** 나누는 것.

> **참고**: `docker-compose.prod.yml`은 **db 서비스에만** healthcheck가 있다(`pg_isready`).
> web에는 없다. `/healthz`는 현재 어디에도 연결되어 있지 않다 — 개선 여지.

---

### 2-7. `import` 만으로 앱이 조립되는 구조 — 부작용 기반 초기화

**[전공] 놓치기 쉬운 중요한 포인트**

`app/main.py`는 **모듈 최상단에서 실행되는 코드**로 앱을 구성한다.
`uvicorn app.main:app` 이 `app/main.py`를 import 하는 순간:

1. `from app.config import settings` → `config.py` 실행 → `settings = Settings()`
   → **이때 `.env`를 읽고, 타입을 변환하고, `field_validator`가 돈다.** 실패하면 여기서 죽는다.
2. `from app.routers import ...` → 각 라우터 모듈 실행
   → 그 모듈들이 `from app.db import get_db` → **`db.py` 실행 → `create_engine()` 호출**
   → `admin.py`의 마지막 두 줄이 실행되어 **비밀 경로가 등록**된다
3. `add_middleware`, `mount`, `include_router` 전부 import 시점에 실행

**결과**: 설정 오류나 DB URL 오타는 **첫 요청이 아니라 서버 부팅 시점에** 드러난다.
이건 **좋은 성질**이다(fail fast).

**부작용도 있다:**
- `pytest`로 테스트를 돌릴 때도 `app`을 import하면 `create_engine`이 실행된다
- `tests/test_admin_access.py`가 이 사실을 주석으로 남기고 있다:
  > 실제 화면(/leaderboard 등)은 DB가 필요한데, 이 저장소의 .env는 운영 DB를 가리키므로
  > 테스트에서 붙이지 않는다.

그래서 테스트들은 **DB를 안 타는 경계만** 검증한다:
- 라우팅 (`/admin/login`이 404인가)
- 순수 함수 (`parse_evaluation_result`, `resolve_storage_path`, `admin_lockout`)
- 템플릿 렌더 (가짜 request 객체로 `base.html`만 렌더)

> **이건 "테스트하기 쉬운 코드를 위해 순수 함수를 분리"한 결과다.**
> `parse_evaluation_result`, `resolve_storage_path`, `parse_team_names`, `get_team_best`,
> `admin_lockout.*`, `summarize_progress`, `wants_json_response` —
> 전부 DB나 네트워크 없이 호출 가능한 순수 함수라 테스트가 쉽다.
> **이게 좋은 설계의 신호다.**

---

## 3. `app/config.py` — 설정을 코드 밖으로 빼는 이유

```python
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

KST = ZoneInfo("Asia/Seoul")
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", protected_namespaces=())

    database_url: str = "postgresql+psycopg2://drleader:drleader@localhost:5432/drleader"
    session_secret: str = "change-me-in-production"
    session_https_only: bool = False
    storage_dir: Path = BASE_DIR / "storage"

    # spec.md에서 확정한 규칙
    daily_submission_limit: int = 5
    online_eval_laps: int = 3
    eval_minutes_estimate: int = 10
    model_upload_max_bytes: int = 500 * 1024 * 1024
    model_upload_allowed_extensions: tuple[str, ...] = (".tar.gz", ".zip")

    # ── 워커가 웹과 다른 기기에서 돌 때 쓰는 설정 (cloud-migration.md §4) ──
    worker_token: str = ""
    web_base_url: str = "http://localhost:8000"
    video_upload_max_bytes: int = 200 * 1024 * 1024
    worker_heartbeat_stale_minutes: int = 3

    # ── 관리자 진입점 은닉 (admin-access-hardening.md) ──
    admin_login_path: str = "/admin/login"
    admin_login_max_attempts: int = 5
    admin_login_lockout_minutes: int = 15
```

### 왜(Why) — 왜 하드코딩하지 않는가

**[쉬움]**
같은 프로그램을 내 노트북에서도 돌리고, 클라우드 서버에서도 돌린다.
그런데 DB 주소나 비밀번호는 서로 다르다. 코드 안에 적어두면 장소를 옮길 때마다 코드를 고쳐야 하고,
**비밀번호가 코드에 남아 남들에게 보인다.**

**[전공] — 12-Factor App의 III. Config**

> "설정은 코드가 아니라 **환경**에 저장하라. 설정은 배포(deploy)마다 달라지는 모든 것이다."

이 원칙이 주는 구체적 이득:
1. **같은 도커 이미지**를 dev/prod에 그대로 쓸 수 있다. 빌드는 한 번, 배포는 여러 번
2. **비밀값이 git에 안 들어간다** (`.env.example`만 커밋하고 `.env`는 안 한다)
3. 값 하나 바꾸는 데 **재빌드가 필요 없다** (재시작만)

**이 프로젝트에서 그게 극단적으로 드러나는 예:**

| 설정 | local 모드 | 클라우드 운영 | 바뀌는 것 |
|---|---|---|---|
| `WORKER_TOKEN` | 빈 값 | 무작위 토큰 | **워커의 동작 방식 전체** |
| `ADMIN_LOGIN_PATH` | `/admin/login` | `/_ops/무작위` | **URL 라우팅** |
| `SESSION_HTTPS_ONLY` | `false` | `true` | 쿠키 정책 |
| `DATABASE_URL` | `localhost` | `db` (서비스명) | 접속 대상 |

**코드는 한 줄도 안 바뀌는데 시스템이 다르게 동작한다.** 이게 12-factor의 실질적 위력이다.

### 어떻게(How) — pydantic-settings의 동작 원리

**우선순위 (높은 것이 이김)**:
```
1. 코드에서 직접 넘긴 인자     Settings(admin_login_path="/x")   ← 테스트가 쓴다
2. 환경변수                    export ADMIN_LOGIN_PATH=...
3. .env 파일                   env_file=".env"
4. 클래스에 적힌 기본값
```

**이름 매핑**: 필드 `admin_login_path` ↔ 환경변수 `ADMIN_LOGIN_PATH` (대소문자 무시).

**타입 변환이 자동이다** — 이게 pydantic의 핵심 가치:
```
SESSION_HTTPS_ONLY=false   (문자열)  →  session_https_only: bool  →  False (진짜 bool)
```

**만약 이걸 직접 `os.environ.get()`으로 했다면?**
```python
session_https_only = os.environ.get("SESSION_HTTPS_ONLY")  # "false" — 문자열!
if session_https_only:  # ← 비어있지 않은 문자열이라 True!! 버그!
```
이 버그는 매우 흔하다. **pydantic-settings를 쓰는 가장 실질적인 이유가 이것이다.**

### 세부 옵션 세 개

```python
model_config = SettingsConfigDict(env_file=".env", extra="ignore", protected_namespaces=())
```

| 옵션 | 의미 | 왜 필요한가 |
|---|---|---|
| `env_file=".env"` | `.env` 파일도 읽는다 | 로컬 개발 편의 |
| `extra="ignore"` | 모르는 환경변수는 무시 | **필수**. 없으면 `PATH`, `HOME` 등 때문에 에러 |
| `protected_namespaces=()` | `model_` 접두사 보호 해제 | **필수**. pydantic이 `model_`을 예약어로 취급하는데 우리는 `model_upload_max_bytes`를 쓴다 |

---

### 3-5. **`@field_validator` — 사람의 실수를 흡수하는 층**

```python
@field_validator("admin_login_path")
@classmethod
def _normalize_admin_login_path(cls, value: str) -> str:
    """사람이 .env에 손으로 넣는 값이라 흔한 실수를 흡수한다.

    특히 빈 문자열을 그대로 라우트로 등록하면 앱이 깨지므로 기본값으로 되돌린다.
    """
    value = value.strip().rstrip("/")
    if not value:
        return "/admin/login"
    if not value.startswith("/"):
        value = "/" + value
    return value
```

### 무엇을(What)

**[쉬움]**
설정 파일에 주소를 손으로 적다 보면 실수한다.
- 앞에 `/`를 빠뜨림 → `_ops/abc`
- 뒤에 `/`가 붙음 → `/_ops/abc/`
- 복사하다 공백이 딸려옴 → `  /_ops/abc  `

이 함수가 **전부 같은 값으로 정리해 준다.**

**[전공]**
pydantic v2의 필드 검증기. `Settings()` 생성 시점에 그 필드 값을 받아 변환한다.
`@classmethod`가 붙어야 한다(v2 요구사항).

`tests/test_admin_access.py`가 8가지 케이스를 고정한다:
```python
@pytest.mark.parametrize("입력, 기대", [
    ("/_ops/abc", "/_ops/abc"),
    ("_ops/abc", "/_ops/abc"),      # 앞 슬래시를 빠뜨림
    ("/_ops/abc/", "/_ops/abc"),    # 뒤 슬래시가 붙음
    ("  /_ops/abc  ", "/_ops/abc"), # 복사하다 공백이 딸려옴
    ("_ops/abc/", "/_ops/abc"),     # 둘 다 틀림
    ("", "/admin/login"),           # 빈 값이면 기본값으로 되돌린다
    ("   ", "/admin/login"),
    ("/", "/admin/login"),          # 슬래시만 있으면 빈 값과 같다
])
def test_비밀_경로를_정규화한다(입력, 기대):
    assert Settings(admin_login_path=입력).admin_login_path == 기대
```

### 왜(Why) — 빈 문자열이 왜 위험한가

`add_api_route("")` 를 호출하면 Starlette이 경로 패턴을 만들지 못해 앱이 깨진다.
그런데 **이 실수는 `.env`에 `ADMIN_LOGIN_PATH=` 라고만 적으면 일어난다.** 아주 쉽게.

세 가지 대응이 가능했다:
1. **에러를 던진다** — `.env`가 잘못됐음을 즉시 알린다. 하지만 앱이 아예 안 뜬다
2. **기본값으로 되돌린다** (현재) — 앱은 뜬다. 다만 관리자 로그인이 공개 경로가 된다
3. 그대로 둔다 — **앱이 깨진다.** 최악

**왜 2번인가?** `docker-compose.prod.yml`이 이미 **1번 역할을 하고 있기 때문**이다:
```yaml
ADMIN_LOGIN_PATH: "${ADMIN_LOGIN_PATH:?ADMIN_LOGIN_PATH를 .env에 설정하세요. 예) /_ops/무작위문자열}"
```
`${VAR:?메시지}` 는 **변수가 없거나 비면 compose가 에러를 내고 멈춘다.**
운영에서는 compose가 막고, 개발에서는 검증기가 안전한 기본값으로 떨어뜨린다.

> **[전공] 방어를 어느 층에 둘 것인가 — 좋은 사례다.**
> "운영에서 절대 일어나면 안 되는 것"은 **배포 설정**이 막고,
> "개발에서 흔한 실수"는 **애플리케이션**이 흡수한다.
> 두 층의 역할이 다르므로 중복이 아니다.

---

### 3-6. `@property`로 파생 경로 만들기

```python
@property
def models_dir(self) -> Path:
    return self.storage_dir / "models"
```

**왜 필드로 안 하고 property인가?**

만약 `models_dir: Path = BASE_DIR / "storage" / "models"` 라고 필드로 뒀다면,
`STORAGE_DIR` 환경변수를 바꿔도 `models_dir`은 안 따라 바뀐다. **불일치가 생긴다.**
property로 두면 항상 `storage_dir`에서 파생되므로 **단일 진실 공급원**이 유지된다.

`Path`의 `/` 연산자는 `__truediv__` 오버로딩이다. OS별 구분자를 알아서 처리한다.

### 3-7. `KST = ZoneInfo("Asia/Seoul")`

파일 최상단에 있다. 4단계(하루 제출 한도)에서 결정적으로 쓰인다.

**[쉬움]** 서버는 세계 표준시(UTC)로 시간을 재는데, 우리 대회는 한국 시간 밤 12시에 하루가 바뀐다.

**[전공]** 파이썬 3.9+ 표준 라이브러리 `zoneinfo`. IANA 타임존 DB를 읽어 **DST까지** 반영한다.
`timedelta(hours=9)`로 직접 계산하면 안 되는 이유: 한국은 지금 DST가 없지만,
이런 코드를 습관화하면 DST가 있는 지역에서 반드시 버그가 난다.
(그리고 1987~1988년 한국에도 서머타임이 있었다.)

---

## 4. `app/db.py` — DB 연결의 3층 구조

```python
engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

**단 10줄인데 SQLAlchemy의 핵심 개념 4개가 다 들어있다.**

### 4-1. Engine — 커넥션 풀

**[쉬움]**
DB에 접속하는 건 전화를 거는 것과 같아서 시간이 걸린다(수십 ms).
매번 새로 거는 대신 **전화선 몇 개를 미리 연결해두고 돌려쓴다.**

**[전공]**
`create_engine`은 **연결하지 않는다.** 설정만 갖고 있다가 첫 쿼리 때 lazy하게 연결한다.
기본 풀은 `QueuePool`, `pool_size=5`, `max_overflow=10` → 최대 15개 동시 연결.

`postgresql+psycopg2://` 의 구조:
```
postgresql   +  psycopg2  ://  drleader : ***  @  db  : 5432 / drleader
[방언dialect]  [드라이버]      [유저]   [비번]   [호스트][포트][DB명]
```

**`pool_pre_ping=True` — 이 프로젝트에서 왜 중요한가**

**[쉬움]** 오래 안 쓴 전화선은 저쪽에서 끊겼을 수 있다. 쓰기 전에 "여보세요?" 하고 확인.

**[전공]**
풀에 있는 커넥션은 **서버·방화벽·NAT에 의해 조용히 끊길 수 있다.**
pre_ping 없이 죽은 커넥션을 꺼내 쓰면 첫 쿼리가 `OperationalError`로 실패한다.

**우리 상황에서 특히 필요한 이유** — 이제 더 강해졌다:
- 워커는 **몇 시간 동안 아무 제출이 없어도 계속 떠 있다**
- 워커가 **Tailscale 사설망을 거쳐 원격 DB에 붙는다** → 네트워크가 훨씬 불안정하다
- 하트비트 스레드가 **30초마다 별도 커넥션**을 쓴다
- EC2 스팟 인스턴스가 중지·복귀할 수 있다

비용은 쿼리마다 왕복 1회. 이 규모에서는 무시할 수준이다.

### 4-2. Session — 작업 단위(Unit of Work)

**[쉬움]**
장바구니다. 물건을 담는다고(`db.add`) 바로 결제되는 게 아니라,
계산대에 가서 "결제"(`db.commit()`)를 눌러야 실제로 산다.

**[전공]**
Session은 세 가지를 동시에 한다:
1. **Identity Map**: 같은 PK의 객체는 세션 안에서 항상 같은 파이썬 객체
2. **Unit of Work**: 변경된 객체를 추적했다가 flush 시점에 SQL을 순서대로 발행
3. **트랜잭션 경계**

**`autoflush=False` — 이 선택의 의미**

`autoflush=True`(기본값)면 쿼리 직전에 미커밋 변경이 자동 flush된다.
편해 보이지만 **예상 못 한 시점에 제약조건 위반이 터진다.**

`autoflush=False`면 **flush 시점을 내가 통제**한다. 실제로 `admin.py`가 명시적으로 쓴다:
```python
team = Team(season_id=season.id, name=name)
db.add(team)
db.flush()  # team.id 확보
```
`Account`를 만들려면 `team_id`가 필요한데 DB 시퀀스가 만든다. flush하면 INSERT가 나가고
`RETURNING id`로 값이 채워진다. **커밋은 아직 안 됐다** — 뒤에서 실패하면 롤백된다.

### 4-3. `DeclarativeBase`

```python
class Base(DeclarativeBase):
    pass
```

`Base`를 상속한 클래스가 정의되는 순간 메타클래스가 개입해
`Mapped[...]` 선언을 읽어 `Table` 객체를 만들고 `Base.metadata`에 등록한다.

`Base.metadata`는 **모든 테이블 정의의 레지스트리**다. alembic이 이걸 쓴다:
```python
# migrations/env.py
target_metadata = Base.metadata
```

`DeclarativeBase`는 **2.0 스타일**이다. 1.x는 `declarative_base()` 함수를 썼다.

### 4-4. `get_db()` — 제너레이터 기반 의존성

```python
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

**이 5줄이 FastAPI 의존성 주입의 정수다.**

**실행 순서:**
```
요청 도착 → get_db() 호출 → 제너레이터 생성
  → next() → SessionLocal() 실행 → yield db 에서 멈춤
  → db 객체를 핸들러의 db 파라미터로 주입
  → 핸들러 실행 (예외가 나도 상관없음)
  → 응답 생성 완료
  → 제너레이터를 다시 next() → finally → db.close()
```

**핵심 질문 1: 왜 `return db`가 아니라 `yield db`인가?**
`return`이면 "정리(cleanup) 코드를 실행할 기회"가 없다.
`yield`는 실행을 중간에 멈췄다 재개할 수 있으므로 **"요청 처리 후"에 코드를 끼워 넣을 수 있다.**

**핵심 질문 2: 왜 `db.commit()`을 여기서 안 하는가?**
핸들러가 명시적으로 커밋한다. 조금 번거롭지만 **트랜잭션 경계가 코드에 드러난다.**
커밋하지 않은 세션은 `close()` 시점에 **암묵적으로 롤백**된다.
즉 **커밋을 잊으면 저장이 안 된다** — 안전한 방향의 실수다.

**핵심 질문 3: 워커는 `get_db`를 안 쓴다. 왜?**
FastAPI 요청 주기가 없다. 직접 열고 직접 닫는다. 같은 패턴을 손으로 쓴 것뿐이다.

그리고 워커는 **루프마다 세션을 새로 연다**:
```python
while True:
    db = SessionLocal()
    try:
        submission_id = claim_next_submission(db)
    finally:
        db.close()
```
세션을 오래 유지하면 (a) 메모리가 늘고, (b) 트랜잭션이 길게 열려 낡은 스냅샷을 보게 되며,
(c) 커넥션이 끊겼을 때 복구가 어렵다. → **짧게 열고 짧게 닫는다.**

**하트비트 스레드도 같은 패턴이다:**
```python
# worker/run.py:150-158
while True:
    db = SessionLocal()
    try:
        touch_heartbeat(db, WORKER_ID)
    except Exception:
        db.rollback()
        logger.warning("하트비트 갱신 실패", exc_info=True)
    finally:
        db.close()
    time.sleep(HEARTBEAT_INTERVAL_SECONDS)
```

> **[전공] 스레드 안전성 주의**: SQLAlchemy `Session`은 **스레드 안전하지 않다.**
> 그래서 하트비트 스레드가 **자기 세션을 따로 연다.** 메인 루프의 세션을 공유하면 깨진다.
> `Engine`(커넥션 풀)은 스레드 안전하므로 공유해도 된다. **이 구분이 중요하다.**

---

## 5. `app/render.py` — 템플릿 엔진 + 커스텀 필터

```python
import datetime as dt

from fastapi.templating import Jinja2Templates

from app.config import KST

templates = Jinja2Templates(directory="app/templates")


def kst(value: dt.datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """저장된 UTC 시각을 화면에 한국시간으로 찍는다."""
    if value is None:
        return ""
    return value.astimezone(KST).strftime(fmt)


# DRFC가 남기는 종료 사유를 참가자가 이해할 수 있는 말로 옮긴다.
# 이 표에 없는 값은 원문을 그대로 보여준다 — 모르는 사유를 감추면 원인 추적이 어려워진다.
FAILURE_REASON_LABELS = {
    "immobilized": "차량이 멈춤",
    "off_track": "트랙 이탈",
    "crashed": "충돌",
    "reversed": "역주행",
    "time_up": "시간 초과",
    "timeout": "시간 초과",
    "lap_complete": "완주",
}


def failure_summary(result) -> str:
    """완주하지 못한 결과를 '완주 실패 (67.8%) · 차량이 멈춤' 형태로 표현한다."""
    if result is None:
        return "완주 실패"

    parts = ["완주 실패"]
    progress = getattr(result, "best_progress_percent", None)
    if progress is not None:
        parts[0] = f"완주 실패 ({progress:.1f}%)"

    reason = getattr(result, "failure_reason", None)
    if reason:
        parts.append(FAILURE_REASON_LABELS.get(reason, reason))
    return " · ".join(parts)


templates.env.filters["failure_summary"] = failure_summary
templates.env.filters["kst"] = kst
```

### 왜 시간대를 필터로 바꾸는가 — 환경변수로 하면 안 되나

**[쉬움]** DB에 담긴 시각은 틀리지 않았다. **보여줄 때** 한국시간으로 바꿔주는 일이 빠져 있었다.

**[전공]**
시각 컬럼은 전부 `DateTime(timezone=True)` → PostgreSQL `TIMESTAMPTZ`라, 저장된
값(시점)은 언제나 정확하다. 문제는 psycopg2가 그 값을 **DB 세션의 시간대**로 aware
datetime을 만들어 돌려준다는 것이다. 컨테이너에 시간대 설정이 없어 그게 UTC였고,
템플릿이 `submitted_at.strftime(...)`으로 그대로 찍어 참가자와 관리자 화면에 9시간
이른 시각이 보였다 (2026-08-18 발견 — 대회 마감 시각을 확인하다 드러났다).

컨테이너에 `TZ=Asia/Seoul`을 주는 방법도 있다. 쓰지 않은 이유는 두 가지다.

1. **웹 컨테이너의 `TZ`만으로는 안 고쳐진다.** 돌아오는 datetime의 오프셋을 정하는 건
   파이썬의 로컬 시간대가 아니라 **Postgres 세션 시간대**다. `PGTZ`까지 맞춰야 한다.
2. **화면에 찍히는 시간대가 코드 어디에도 안 보이게 된다.** 배포 환경 설정에만 의존해서,
   서버를 옮기거나 compose 파일을 새로 쓰면 조용히 다시 UTC로 돌아간다.

표시 직전에 명시적으로 변환하면 8단계의 규칙 — **저장·공유는 UTC, 표시와 비즈니스
규칙은 KST** — 이 코드에 그대로 드러난다. `quota.py`의 `today_kst()`가 하루 한도
경계에 대해 하는 일을, `kst` 필터가 화면에 대해 하는 셈이다.

### 왜 `render.py`가 별도 모듈인가

**[쉬움]** 여러 파일에서 같은 도구를 쓸 때, 도구를 한 곳에 두고 빌려 쓴다.

**[전공]**
4개 라우터가 각자 `Jinja2Templates(...)`를 만들면
- 템플릿 캐시가 4벌 생기고
- **커스텀 필터를 4곳에 등록해야 한다**

지금은 `templates.env.filters["failure_summary"] = failure_summary` 한 줄로 끝난다.
그리고 템플릿에서:
```jinja
{{ latest_submission.result | failure_summary }}
```

### 커스텀 필터란 무엇인가

**[쉬움]** Jinja의 `|` 는 "이 값을 이 함수에 통과시켜라"는 뜻이다.
`{{ 이름|upper }}` → `upper(이름)`. 우리가 만든 함수도 그렇게 쓸 수 있다.

**[전공]**
`templates.env`는 `jinja2.Environment` 객체다. `filters`는 이름→함수 dict.
`{{ x | foo(a) }}` 는 `foo(x, a)` 로 호출된다.

**왜 필터인가? 라우터에서 문자열을 만들어 넘기면 안 되나?**

가능하다. 하지만:
- **여러 화면이 같은 표현을 쓴다**: `submit.html`(최근 결과)과 `leaderboard.html`(미완주 표)
- 라우터에서 만들면 각 라우터가 표현 로직을 갖게 된다 → **중복**
- 필터로 두면 **"데이터는 라우터, 표현은 템플릿"** 경계가 유지된다

### `getattr(result, "...", None)` — 왜 직접 접근하지 않나

```python
progress = getattr(result, "best_progress_percent", None)
```

`result.best_progress_percent` 라고 써도 된다. 왜 `getattr`인가?

**이 필터가 `EvaluationResult`가 아닌 객체를 받을 수도 있기 때문이다.**
테스트에서 가짜 객체를 넣거나, 나중에 다른 타입을 넘길 수 있다.
`getattr`은 **속성이 없어도 죽지 않는다.**

> **[전공] 방어적이지만 과할 수도 있다.** ORM 모델은 항상 그 속성을 갖는다.
> 다만 이 함수는 **표현 계층**이고, 표현이 예외로 페이지 전체를 죽이면 안 된다는
> 판단으로 보면 타당하다.

### 마이그레이션과의 관계 — "값이 없으면 옛 문구"

```python
if progress is not None:
    parts[0] = f"완주 실패 ({progress:.1f}%)"
```

`best_progress_percent`는 나중에 추가된 컬럼이라 **옛 레코드는 NULL**이다.
(마이그레이션 `d4f1a2c86b73` 참고 — 2단계에서 다룬다)

**`None`이면 진행률 없이 "완주 실패"만 표시한다.**
옛 데이터가 있다고 화면이 깨지지 않는다. → **하위 호환 처리가 표현 계층까지 이어진 예다.**

---

## 6. 요청 하나의 전체 여정 — 통합 정리

`GET /leaderboard` 를 브라우저에 치면:

```
 1. 브라우저 → DNS → Caddy(443) → TLS 종료
      Caddy가 X-Forwarded-For / X-Forwarded-Proto 헤더를 붙여 web:8000 으로 전달

 2. uvicorn: HTTP 파싱 → ASGI scope dict 생성
      {"type":"http", "method":"GET", "path":"/leaderboard", "headers":[...]}

 3. SessionMiddleware.__call__
      - Cookie 헤더에서 session 값 추출
      - itsdangerous로 서명 검증 (secret_key 사용)
      - 성공하면 base64 디코드 → JSON → scope["session"] = {"team_id": 6}
      - 실패/없음이면 scope["session"] = {}

 4. FastAPI 라우터: app.routes를 순서대로 스캔
      "/login"?        → 매칭 안 됨
      "/submit"?       → 매칭 안 됨
      "/"?             → 매칭 안 됨
      "/leaderboard"?  → 매칭!  leaderboard_entry 함수

 5. 의존성 해결:
      - Request 객체 생성
      - Depends(get_db) → SessionLocal() → Session 객체

 6. leaderboard_entry(request, db) 실행
      → get_open_season(db) → SELECT * FROM seasons WHERE status='active' ...
      → 있으면 RedirectResponse("/leaderboard/1", 303)

 7. Response → ASGI send 이벤트

 8. SessionMiddleware: 세션이 안 바뀌었으므로 Set-Cookie 안 붙임

 9. uvicorn → Caddy → 브라우저
      HTTP/1.1 303 See Other
      location: /leaderboard/1

10. get_db()의 finally → db.close() → 커넥션 풀에 반납

11. 브라우저: 303을 보고 자동으로 GET /leaderboard/1 재요청 → 2번부터 반복
      이번엔 season_leaderboard() 실행 → build_leaderboard() + get_worker_status()
      → templates.TemplateResponse(...) → Jinja 렌더 → HTML 200
```

**이 11단계를 막힘없이 말할 수 있으면 1단계는 끝난 것이다.**

---

## 7. 자가 점검 질문

1. `uvicorn`과 `FastAPI`는 각각 무슨 일을 하는가? `--workers`가 없다는 사실이 어떤 설계 전제가 되는가?
2. `def`와 `async def` 라우트의 실행 방식 차이는? 우리 코드에서 `async def`가 3곳뿐인 이유는?
3. `SessionMiddleware`는 세션 데이터를 **어디에** 저장하는가? 사용자가 읽을 수 있는가? 바꿀 수 있는가?
4. `app.mount`와 `app.include_router`의 차이는?
5. `admin.router`와 `admin.login_router`를 나눈 이유는? "예외를 파지 않는다"가 무슨 뜻인가?
6. `@router.get("/x")` 와 `add_api_route("/x", fn, methods=["GET"])` 의 관계는? 왜 후자를 썼는가?
7. `include_in_schema=False`를 빼면 무슨 일이 일어나는가? 무엇이 그걸 감시하는가?
8. `/leaderboard/seasons`를 `/leaderboard/{season_id}`보다 먼저 선언해야 하는 이유는? 안 하면 어떤 에러가?
9. 설정을 `.env`로 빼는 것이 주는 이득 3가지는? 이 프로젝트에서 설정 하나가 **동작 방식 자체**를 바꾸는 예 2개는?
10. `extra="ignore"`가 없으면 무슨 일이 일어나는가? `protected_namespaces=()`는?
11. `admin_login_path` 검증기가 빈 문자열을 기본값으로 되돌리는 이유는? 왜 에러를 던지지 않는가?
12. `models_dir`를 필드가 아니라 `@property`로 만든 이유는?
13. `pool_pre_ping=True`가 원격 워커에게 특히 중요해진 이유 4가지는?
14. `autoflush=False`인데 `admin.py`는 왜 `db.flush()`를 명시적으로 부르는가?
15. `get_db`가 `return`이 아니라 `yield`를 쓰는 이유는?
16. 하트비트 스레드가 자기 세션을 따로 여는 이유는? `Engine`은 왜 공유해도 되는가?
17. `failure_summary`를 라우터가 아니라 Jinja 필터로 만든 이유는?
18. `getattr(result, "best_progress_percent", None)` 가 `None`을 다루는 것이 마이그레이션과 어떻게 연결되는가?

---

## 8. 실험 과제

> 모두 로컬에서, 운영 DB를 건드리지 않고 할 수 있다.

**실험 A — 등록된 라우트 전부 출력**
```bash
PYTHONPATH=. .venv/bin/python -c "
from app.main import app
for r in app.routes:
    print(getattr(r,'methods',''), r.path)
"
```
비밀 경로가 목록에 있는가? `/admin/login`은 없는가?
`.env`의 `ADMIN_LOGIN_PATH`를 바꾸고 다시 실행하면 목록이 바뀌는가?

**실험 B — 미들웨어를 눈으로 보기**
```python
import time
from starlette.middleware.base import BaseHTTPMiddleware

class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        print(f"→ 들어옴 {request.url.path}")
        t = time.perf_counter()
        response = await call_next(request)
        print(f"← 나감 {request.url.path} {(time.perf_counter()-t)*1000:.1f}ms")
        return response

app.add_middleware(TimingMiddleware)
```
`add_middleware` 순서를 바꾸면 로그 순서가 어떻게 바뀌는가?

**실험 C — 세션 쿠키 까보기**
로그인한 뒤 개발자도구 → Application → Cookies → `session` 값을 복사해 디코드:
```python
import base64, json
v = "여기에_붙여넣기".split(".")[0]
print(json.loads(base64.urlsafe_b64decode(v + "==")))
```
**"서명은 있지만 암호화는 아니다"를 눈으로 확인하는 실험이다.**

**실험 D — 설정 검증 확인**
```bash
PYTHONPATH=. .venv/bin/python -c "
from app.config import Settings
for v in ['_ops/abc', '/_ops/abc/', '  /x  ', '', '/']:
    print(repr(v), '→', Settings(admin_login_path=v).admin_login_path)
"
```
그다음 `.env`에 `DAILY_SUBMISSION_LIMIT=다섯` 을 넣고 서버를 띄워보라.
어떤 예외가 **어느 시점에** 나는가? 이것이 fail-fast다.

**실험 E — OpenAPI 유출 확인**

먼저 바깥 겹부터. 문서 엔드포인트가 정말 닫혔는지 본다.
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/openapi.json
```
**404**여야 한다. `/docs`, `/redoc`도 같이 해보라.

이제 안쪽 겹. 엔드포인트는 닫혔지만 **스키마는 여전히 만들어진다**는 것을 직접 확인한다.
```bash
PYTHONPATH=. .venv/bin/python -c "
from app.main import app
스키마 = app.openapi()
print('경로 수:', len(스키마['paths']))
print([p for p in 스키마['paths'] if 'ops' in p])
"
```
경로 수는 21인데 두 번째 줄은 **빈 리스트**여야 한다. 그다음 `admin.py`에서
`include_in_schema=False`를 지우고 다시 실행해보라 — 비밀 경로가 튀어나온다.
**확인했으면 반드시 되돌린다.** 그리고:
```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_admin_access.py -k "openapi or 문서" -v
```
테스트가 이걸 잡아내는지 확인.

**실험 F — 라우트 순서 버그 재현**
`leaderboard.py`에서 `/leaderboard/seasons` 라우트를 `/leaderboard/{season_id}` **아래로** 옮기고
`/leaderboard/seasons`에 접속해보라. 422 응답 본문을 확인하고 되돌린다.

---

## 9. 다음 단계로 넘어가기 전에

`main.py`에서 남은 미해결 질문은 전부 `models.py`에 있다:
- `Depends(get_db)`가 주는 `Session`으로 **어떤 테이블**을 어떻게 조회하는가?
- `Team`, `Submission`, `WorkerHeartbeat` 같은 클래스는 어디서 왔는가?

→ [02-data-model.md](02-data-model.md)
