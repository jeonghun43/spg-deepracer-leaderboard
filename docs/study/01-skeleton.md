# 1단계. 앱의 골격 — `main.py`, `config.py`, `db.py`

> 이 단계의 목표: **브라우저 주소창에 `http://localhost:8000/leaderboard`를 치고 엔터를 누른 순간부터
> 화면이 뜰 때까지, 내 코드의 어느 줄이 어떤 순서로 실행되는지**를 끊김 없이 설명할 수 있게 되는 것.

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
Host: localhost:8000
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
   │ HTTP over TCP
   ▼
[uvicorn]          ← 실제로 소켓을 열고 HTTP를 파싱하는 "서버"
   │ ASGI 인터페이스 (scope, receive, send)
   ▼
[Starlette 미들웨어 체인]   ← SessionMiddleware 등
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

---

## 1. WSGI vs ASGI — FastAPI가 `async def`를 쓸 수 있는 이유

### 무엇을(What)

**[쉬움]**
식당에 비유하자.

- **WSGI(옛날 방식, Flask/Django)**: 웨이터 한 명이 손님 한 명을 끝까지 담당한다.
  손님이 "10분 걸리는 요리"를 시키면 그 웨이터는 10분 동안 아무것도 못 한다.
  손님이 많으면 웨이터를 늘리는 수밖에 없다.
- **ASGI(요즘 방식, FastAPI)**: 웨이터가 주문을 넣고 **주방이 요리하는 동안 다른 테이블을 받는다.**
  요리가 나오면 그때 다시 그 테이블로 간다. 웨이터 한 명이 여러 손님을 본다.

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

솔직히 말하면 **동시 접속 10명 규모에선 WSGI로도 충분하다.**
그럼에도 이득은 있다:
1. 500MB 파일 업로드를 메모리에 다 올리지 않고 스트리밍으로 받는다.
2. 나중에 "평가 진행률 실시간 표시" 같은 기능을 붙일 때 WebSocket/SSE가 열려 있다.

**중요한 함정**: FastAPI에서 `def`(동기)로 선언한 라우트는 **스레드풀에서** 실행되고,
`async def`로 선언한 라우트는 **이벤트 루프에서** 실행된다.
`async def` 안에서 블로킹 I/O(예: 동기 SQLAlchemy 쿼리, `time.sleep`)를 하면
**이벤트 루프 전체가 멈춘다** = 모든 사용자의 요청이 멈춘다.

우리 코드를 보면:
```python
# app/routers/leaderboard.py:90
def season_leaderboard(season_id: int, request: Request, db: Session = Depends(get_db)):
```
`def`다. 동기 SQLAlchemy를 쓰므로 **의도적으로 옳은 선택**이다.

```python
# app/routers/submissions.py:70
async def submit_upload(...):
    while chunk := await model_file.read(1024 * 1024):
```
여기만 `async def`다. `await`로 파일 청크를 받아야 하기 때문. 그런데 이 함수 안에서
`db.commit()`(동기 블로킹)을 호출한다 — 엄밀히는 이벤트 루프를 잠깐 막는다.
규모가 작아 문제가 되지 않지만, **알고 넘어가야 할 트레이드오프**다.

> **면접 단골 질문**: "FastAPI에서 `def`와 `async def` 중 뭘 써야 하나요?"
> 정답: "안에서 `await`할 게 없으면 `def`. 동기 DB 드라이버를 쓴다면 `def`가 더 안전하다."

---

## 2. `app/main.py` — 한 줄씩 완전 분해

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.routers import admin, auth, leaderboard, submissions

app = FastAPI(title="SPG DeepRacer Leaderboard")
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


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
```

**27줄뿐이다.** 이게 이 프로젝트의 "조립 설명서"다. 하나씩 보자.

---

### 2-1. `app = FastAPI(...)`

**무엇을**: ASGI 애플리케이션 객체를 하나 만든다.

**[쉬움]** 자판기 본체를 하나 사 온 것. 아직 안에 물건은 안 넣었다.

**[전공]** `FastAPI`는 `Starlette`을 상속한 클래스다. 이 객체가 하는 일:
- `routes` 리스트를 갖고 있다가 요청이 오면 URL 패턴 매칭
- `user_middleware` 스택을 갖고 있다가 요청/응답을 감싼다
- 타입 힌트를 읽어 **자동 검증 + OpenAPI 스키마 생성**

`title="SPG DeepRacer Leaderboard"` 는 자동 생성되는 API 문서 제목이다.
서버를 띄우고 `http://localhost:8000/docs` 에 가보면 실제로 보인다.
**이건 개발 중에만 유용하고, 공개 운영 시에는 끄는 것을 고려할 만하다**
(관리자 API 경로가 그대로 노출되므로). 현재 코드는 켜져 있다.

> **실험 1**: `FastAPI(title=..., docs_url=None)` 로 바꾸고 `/docs`가 404가 되는지 확인해보라.

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
Starlette 미들웨어는 다음 시그니처를 만족하는 ASGI 앱 래퍼다:

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

즉 `request.session["team_id"] = 6` 을 하면, 브라우저 쿠키에
`{"team_id": 6}` 을 base64로 인코딩한 문자열 + 서명이 들어간다.
**base64는 암호화가 아니다. 누구나 디코드해서 내용을 볼 수 있다.**
바꿀 수만 없을 뿐이다(서명 때문에).

→ **그래서 이 프로젝트가 세션에 `team_id`(숫자)만 넣고 비밀번호나 개인정보를 안 넣는 것이다.**
3단계에서 더 깊게 다룬다.

**`https_only=settings.session_https_only` 의 의미**

쿠키에 `Secure` 속성을 붙일지 여부다. `Secure` 쿠키는 **HTTPS 연결에서만 전송**된다.
- Cloudflare Tunnel로 공개할 땐 `true`여야 안전하다 (중간에서 쿠키 탈취 방지)
- 로컬에서 `http://localhost:8000`으로 테스트할 땐 `false`여야 한다
  → `true`인데 http로 접속하면 브라우저가 쿠키를 아예 저장 안 해서 **로그인이 무한 반복**된다

`config.py`에 이 함정이 주석으로 정확히 적혀 있다:
```python
# true인 상태에서 http://localhost:8000으로 직접 접속하면(터널 없이) 브라우저가
# Secure 쿠키를 저장하지 않아 로그인이 깨지므로, 로컬 전용 운영/테스트 중에는 false로 둔다.
```

**미들웨어 순서 주의**: `add_middleware`는 **스택에 쌓는다(LIFO)**.
나중에 추가한 것이 **바깥쪽**이 된다. 지금은 하나뿐이라 문제없지만,
CORS나 로깅 미들웨어를 추가할 때 순서가 동작을 바꾼다.

---

### 2-3. `app.mount("/static", StaticFiles(...))`

**무엇을**: `/static/*` 으로 들어오는 요청은 라우터를 거치지 않고 **파일을 그대로** 내보낸다.

**[쉬움]**
CSS 파일이나 동영상은 계산할 게 없다. 그냥 파일을 통째로 주면 된다.
그래서 "이 주소로 오면 그냥 이 폴더에서 찾아 줘"라고 지름길을 만들어 둔 것.

**[전공]**
`mount`는 라우팅이 아니라 **서브 애플리케이션 마운트**다. `/static`으로 시작하는 모든 경로는
`StaticFiles`라는 별도 ASGI 앱에 통째로 위임된다. `include_router`와는 근본적으로 다르다.

`StaticFiles`가 해주는 것:
- MIME 타입 추론 (`.css` → `text/css`, `.mp4` → `video/mp4`)
- `Last-Modified` / `ETag` 헤더 → 브라우저 캐싱
- **Range 요청 지원** ← 동영상 재생에 필수. 브라우저가 "10초 지점부터 주세요"를 할 수 있다
- 디렉터리 탈출(`../../etc/passwd`) 방지

두 개를 mount한 이유가 다르다:

| 마운트 | 디렉터리 | 왜 |
|---|---|---|
| `/static` | `app/static` | 코드와 함께 배포되는 CSS. 이미지에 포함됨 |
| `/media/videos` | `settings.videos_dir` = `storage/videos` | **런타임에 워커가 만들어내는 파일**. 볼륨으로 마운트됨 |

`settings.videos_dir.mkdir(parents=True, exist_ok=True)` 가 **mount보다 먼저** 호출되는 이유:
`StaticFiles(directory=...)`는 생성 시점에 디렉터리 존재를 확인하고, 없으면 예외를 던진다.
컨테이너를 처음 띄울 때 `storage/videos`가 없을 수 있으므로 미리 만든다.

> **[전공] 운영 관점 지적**: 대용량 mp4를 uvicorn이 직접 서빙하는 것은 이상적이지 않다.
> 파이썬 프로세스가 파일 I/O에 묶인다. 규모가 커지면 nginx를 앞에 두고
> `X-Accel-Redirect`로 넘기거나, 정적 파일 전용 서버로 분리한다.
> **10팀 규모에서는 과잉 설계이므로 지금이 맞다.**

**보안 관점**: `/media/videos`는 인증 없이 누구나 접근 가능하다.
이건 **의도된 것**이다(spec: 리더보드/영상은 완전 공개). 하지만 만약 비공개 요구가 생기면
mount를 걷어내고 라우터에서 `FileResponse`로 권한 검사 후 내보내야 한다.

---

### 2-4. `app.include_router(...)`

**무엇을**: 각 파일에 흩어져 정의된 URL 처리 함수들을 앱에 등록한다.

**[쉬움]**
자판기 버튼을 종류별로 다른 사람이 만들었다. 이제 그 버튼판들을 본체에 끼워 넣는 것.

**[전공]**
`APIRouter`는 라우트를 담는 **컨테이너**일 뿐이고, 실제 매칭은 `app.routes` 리스트에서 일어난다.
`include_router`는 라우터의 라우트들을 `prefix`/`tags`/`dependencies`를 붙여 앱 라우트 리스트에 복사한다.

```python
# app/routers/admin.py:25
router = APIRouter(prefix="/admin", tags=["admin"])
```
→ admin 라우터의 `@router.get("/login")` 은 실제로 `/admin/login`이 된다.

**등록 순서가 중요한 이유**: Starlette은 `app.routes`를 **위에서부터 순서대로 스캔**해
첫 번째로 매칭되는 것을 쓴다. 그래서 이런 주석이 있다:

```python
# app/routers/leaderboard.py:81
# 주의: `/leaderboard/{season_id}`보다 먼저 선언해야 한다. FastAPI는 선언 순서로
# 매칭하므로 뒤에 두면 "seasons"를 int로 파싱하려다 422가 난다.
@router.get("/leaderboard/seasons")
```

**이걸 반드시 이해하라.** `/leaderboard/{season_id}`가 먼저 등록되면,
`/leaderboard/seasons` 요청이 들어왔을 때 `season_id="seasons"`로 매칭되고,
`season_id: int` 타입 힌트 때문에 파싱 실패 → **422 Unprocessable Entity**.

> 다른 프레임워크(예: Django)는 정적 세그먼트를 동적 세그먼트보다 우선하는 규칙을 갖기도 한다.
> **Starlette은 그런 우선순위가 없고 순수하게 선언 순서다.** 프레임워크마다 다르므로 외우지 말고 확인하라.

**라우터를 왜 4개로 나눴나?**
`auth` / `submissions` / `leaderboard` / `admin`.
기준은 **URL이 아니라 "관심사(concern)"** 다.
- 파일 하나가 1000줄 되는 걸 막는다
- 각 라우터가 필요로 하는 의존성이 다르다 (admin은 `get_current_admin`, submissions는 `get_current_team`)
- 나중에 admin만 별도 인증 정책을 붙이려면 `include_router(admin.router, dependencies=[...])` 한 줄이면 된다

---

### 2-5. `/healthz`

```python
@app.get("/healthz")
def healthz():
    return {"status": "ok"}
```

**무엇을**: "나 살아있어?"에 답하는 최소 엔드포인트.

**[쉬움]** 병원에서 "손가락 움직여 보세요" 하는 것. 살아있는지만 확인.

**[전공]**
컨테이너 오케스트레이터(Docker healthcheck, k8s liveness/readiness probe), 로드밸런서,
모니터링 도구가 주기적으로 때리는 경로다. 관례적으로 `/healthz` 또는 `/health`.
(`z` 접미사는 구글 내부 관례에서 유래 — 일반 URL과 충돌 안 나게)

**지금 이 구현의 한계**: DB가 죽어도 `{"status":"ok"}`를 반환한다.
"프로세스가 살아있다"만 알려주지 "서비스가 정상이다"는 알려주지 않는다.

제대로 하려면:
```python
@app.get("/healthz")
def healthz(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}
```

**하지만 이건 트레이드오프다.** liveness probe에 DB 체크를 넣으면
DB가 잠깐 흔들릴 때 웹 컨테이너가 재시작되는 **연쇄 장애**가 난다.
정석은 **liveness는 가볍게(현재 구현), readiness는 무겁게(DB 체크)** 나누는 것.

> **[전공] 지금 이 코드는 어디에도 안 쓰이고 있다.** `docker-compose.yml`에 `healthcheck:` 항목이 없다.
> 붙이려면:
> ```yaml
> healthcheck:
>   test: ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://localhost:8000/healthz')"]
>   interval: 30s
> ```

---

### 2-6. `import` 만으로 앱이 조립되는 구조 — 부작용(side effect) 기반 초기화

**[전공] 놓치기 쉬운 중요한 포인트**

`app/main.py`는 **모듈 최상단에서 실행되는 코드**로 앱을 구성한다.
`uvicorn app.main:app` 이 `app/main.py`를 import 하는 순간:

1. `from app.config import settings` → `config.py` 실행 → `settings = Settings()`
   → **이때 `.env` 파일을 읽고 환경변수를 파싱한다.** 실패하면 여기서 죽는다.
2. `from app.routers import ...` → 각 라우터 모듈 실행
   → 그 모듈들이 `from app.db import get_db` → **`db.py` 실행 → `create_engine()` 호출**
3. `app.add_middleware(...)`, `app.mount(...)`, `include_router(...)` 전부 import 시점에 실행

**결과**: 설정 오류나 DB URL 오타는 **첫 요청이 아니라 서버 부팅 시점에** 드러난다.
이건 **좋은 성질**이다(fail fast). 하지만 부작용도 있다:

- `pytest`로 테스트를 돌릴 때도 `app`을 import하면 `create_engine`이 실행된다
  → 테스트가 실제 DB URL을 요구하게 될 수 있다
- 그래서 `tests/`를 보면 대부분 `app`을 import하지 않고
  **순수 함수만 따로 테스트**한다 (`test_evaluation_parsing.py`, `test_storage_paths.py` 등)

> 이건 **테스트하기 쉬운 코드를 위해 순수 함수를 분리**한 결과다.
> `parse_evaluation_result`, `resolve_storage_path`, `parse_team_names`, `get_team_best` —
> 전부 DB나 네트워크 없이 호출 가능한 순수 함수라 테스트가 쉽다.
> **이게 좋은 설계의 신호다.**

---

## 3. `app/config.py` — 설정을 코드 밖으로 빼는 이유

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", protected_namespaces=())

    database_url: str = "postgresql+psycopg2://drleader:drleader@localhost:5432/drleader"
    session_secret: str = "change-me-in-production"
    session_https_only: bool = False
    storage_dir: Path = BASE_DIR / "storage"

    daily_submission_limit: int = 5
    online_eval_laps: int = 3
    eval_minutes_estimate: int = 10
    model_upload_max_bytes: int = 500 * 1024 * 1024
    model_upload_allowed_extensions: tuple[str, ...] = (".tar.gz", ".zip")
```

### 왜(Why) — 왜 하드코딩하지 않는가

**[쉬움]**
같은 프로그램을 내 노트북에서도 돌리고, 대회장 서버에서도 돌린다.
그런데 DB 주소나 비밀번호는 서로 다르다. 코드 안에 적어두면 장소를 옮길 때마다 코드를 고쳐야 하고,
**비밀번호가 코드에 남아 남들에게 보인다.**

**[전공] — 12-Factor App의 III. Config**

> "설정은 코드가 아니라 **환경**에 저장하라. 설정은 배포(deploy)마다 달라지는 모든 것이다."

이 원칙이 주는 구체적 이득:
1. **같은 도커 이미지**를 dev/staging/prod에 그대로 쓸 수 있다.
   빌드는 한 번, 배포는 여러 번. 이미지가 환경별로 다르면 "테스트한 것과 배포한 것이 다른" 사태가 난다.
2. **비밀값이 git에 안 들어간다.** `.gitignore`에 `.env`가 있는지 확인해봐야 한다.
3. 값 하나 바꾸는 데 **재빌드가 필요 없다** (재시작만).

**이 프로젝트에서 실제로 그렇게 되고 있는지 확인:**
```yaml
# docker-compose.yml
environment:
  DATABASE_URL: postgresql+psycopg2://drleader:drleader@db:5432/drleader
  SESSION_SECRET: ${SESSION_SECRET:-change-me-in-production}
```
컨테이너 안에서는 호스트 `db`, 로컬에서는 `localhost`. **같은 코드, 다른 값.** 정확히 12-factor다.

`${SESSION_SECRET:-change-me-in-production}` 은 셸 문법으로 "환경변수가 없으면 기본값" 이라는 뜻.
`docker compose`가 이걸 해석한다.

### 어떻게(How) — pydantic-settings의 동작 원리

**우선순위 (높은 것이 이김)**:
```
1. 코드에서 직접 넘긴 인자     Settings(database_url="...")
2. 환경변수                    export DATABASE_URL=...
3. .env 파일                   env_file=".env"
4. 클래스에 적힌 기본값         database_url: str = "postgresql+..."
```

**이름 매핑**: 필드 `database_url` ↔ 환경변수 `DATABASE_URL` (대소문자 무시).

**타입 변환이 자동이다** — 이게 pydantic의 핵심 가치:
```
SESSION_HTTPS_ONLY=false   (문자열)  →  session_https_only: bool  →  False (진짜 bool)
```
`"false"`라는 문자열을 파이썬 `bool`로 바꿔주고, `"yes"`, `"1"`, `"true"` 등도 인식한다.
**만약 이걸 직접 `os.environ.get()`으로 했다면?**
```python
session_https_only = os.environ.get("SESSION_HTTPS_ONLY")  # "false" — 문자열!
if session_https_only:  # ← 비어있지 않은 문자열이라 True!! 버그!
```
이 버그는 실제로 매우 흔하다. **pydantic-settings를 쓰는 가장 실질적인 이유가 이것이다.**

**타입이 틀리면 부팅이 실패한다**:
`DAILY_SUBMISSION_LIMIT=다섯` 이라고 넣으면 `Settings()` 생성 시점에 `ValidationError`.
서버가 아예 안 뜬다. → **잘못된 설정으로 조용히 돌아가는 것보다 낫다.**

### 세부 옵션 세 개

```python
model_config = SettingsConfigDict(env_file=".env", extra="ignore", protected_namespaces=())
```

| 옵션 | 의미 | 왜 필요한가 |
|---|---|---|
| `env_file=".env"` | `.env` 파일도 읽는다 | 로컬 개발 편의. `export`를 매번 안 해도 됨 |
| `extra="ignore"` | 모르는 환경변수는 무시 | **필수**. 이게 없으면 `PATH`, `HOME` 등 시스템 환경변수 때문에 에러 |
| `protected_namespaces=()` | `model_` 접두사 보호 해제 | **필수**. pydantic이 `model_`로 시작하는 필드명을 예약어로 취급하는데, 우리는 `model_upload_max_bytes`, `model_upload_allowed_extensions`를 쓴다 |

> **`protected_namespaces=()` 를 지우면 어떻게 되나?**
> pydantic이 "`model_upload_max_bytes` 필드가 `model_config` 네임스페이스와 충돌한다"는 경고 또는 에러를 낸다.
> **실험해 볼 가치가 있다.** 이런 프레임워크 내부 규칙은 겪어봐야 기억에 남는다.

### `@property`로 파생 경로 만들기

```python
@property
def models_dir(self) -> Path:
    return self.storage_dir / "models"
```

**왜 필드로 안 하고 property인가?**

만약 `models_dir: Path = BASE_DIR / "storage" / "models"` 라고 필드로 뒀다면,
`STORAGE_DIR` 환경변수를 바꿔도 `models_dir`은 안 따라 바뀐다. **불일치가 생긴다.**
property로 두면 항상 `storage_dir`에서 파생되므로 **단일 진실 공급원(single source of truth)**이 유지된다.

`Path` 의 `/` 연산자는 `__truediv__` 오버로딩이다. OS별 구분자(`/` vs `\`)를 알아서 처리한다.
문자열 `+`로 경로를 붙이면 Windows에서 깨진다.

### `KST = ZoneInfo("Asia/Seoul")`

파일 최상단에 있다. 4단계(하루 제출 한도)에서 결정적으로 쓰인다.

**[쉬움]** 서버는 세계 표준시(UTC)로 시간을 재는데, 우리 대회는 한국 시간 밤 12시에 하루가 바뀐다.
그 차이를 맞춰주는 도구.

**[전공]** 파이썬 3.9+ 표준 라이브러리 `zoneinfo`. IANA 타임존 DB를 읽어 **DST(서머타임)까지** 반영한다.
`timedelta(hours=9)`로 직접 계산하면 안 되는 이유: 한국은 DST가 없지만, 이런 코드를 습관화하면
DST가 있는 지역에서 반드시 버그가 난다. 그리고 **1987~1988년 한국에도 서머타임이 있었다.**

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

**무엇을**: DB로 가는 **연결 통로들의 관리자**.

**[쉬움]**
DB에 접속하는 건 전화를 거는 것과 같아서 시간이 걸린다(수십 ms).
매번 새로 거는 대신 **전화선 몇 개를 미리 연결해두고 돌려쓴다.** 그게 커넥션 풀이다.

**[전공]**
`create_engine`은 **연결하지 않는다.** 설정만 갖고 있다가 첫 쿼리 때 lazy하게 연결한다.
기본 풀은 `QueuePool`, 크기는 `pool_size=5`, `max_overflow=10` → 최대 15개 동시 연결.

`postgresql+psycopg2://` 의 구조:
```
postgresql   +  psycopg2  ://  drleader : drleader @  db  : 5432 / drleader
[방언dialect]  [드라이버]      [유저]   [비번]     [호스트][포트][DB명]
```
- **dialect**: SQL 방언. PostgreSQL은 `ILIKE`, `RETURNING`을 쓸 수 있고 MySQL은 못 쓴다.
- **driver**: 실제로 TCP로 통신하는 파이썬 라이브러리 (`psycopg2-binary`가 requirements에 있다)

**`pool_pre_ping=True` — 이게 이 프로젝트에서 왜 중요한가**

**[쉬움]** 오래 안 쓴 전화선은 저쪽에서 끊겼을 수 있다.
쓰기 전에 "여보세요?" 하고 확인하는 옵션.

**[전공]**
풀에 있는 커넥션은 **서버·방화벽·NAT에 의해 조용히 끊길 수 있다.**
pre_ping 없이 죽은 커넥션을 꺼내 쓰면 첫 쿼리가 `OperationalError: server closed the connection`으로 실패한다.
`pool_pre_ping=True`면 커넥션을 꺼낼 때마다 `SELECT 1`을 날려보고, 실패하면 조용히 버리고 새로 연결한다.

**우리 상황에서 특히 필요한 이유**:
- 워커는 **몇 시간 동안 아무 제출이 없어도 계속 떠 있다.** 커넥션이 유휴 상태로 오래 방치된다
- `docker compose restart db` 로 DB만 재시작하는 운영을 한다 → 기존 커넥션 전부 끊김
- WSL2 네트워크는 슬립/재개 시 불안정할 수 있다

비용은 쿼리마다 왕복 1회. **로컬 DB에서는 무시할 수준.**

`future=True`는 SQLAlchemy 1.4 시절 "2.0 스타일 API를 쓰겠다"는 플래그다.
**2.0에서는 기본값이라 사실상 무의미하다.** (남아 있어도 해롭진 않다)

### 4-2. Session — 작업 단위(Unit of Work)

**무엇을**: 한 번의 "일 묶음". 여러 객체를 바꾸고 한꺼번에 커밋한다.

**[쉬움]**
장바구니다. 물건을 담는다고(`db.add`) 바로 결제되는 게 아니라,
계산대에 가서 "결제"(`db.commit()`)를 눌러야 실제로 산다.
중간에 마음이 바뀌면 장바구니를 비운다(`db.rollback()`).

**[전공]**
Session은 세 가지를 동시에 한다:
1. **Identity Map**: 같은 PK의 객체는 세션 안에서 항상 같은 파이썬 객체.
   `db.get(Team, 6)`을 두 번 호출하면 **쿼리는 한 번**만 나가고 같은 객체가 온다.
2. **Unit of Work**: 변경된 객체를 추적했다가 flush 시점에 INSERT/UPDATE/DELETE를 **순서대로** 발행.
3. **트랜잭션 경계**: 세션 = 트랜잭션 하나 (대체로).

```python
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
```

**`autoflush=False` — 이 선택의 의미**

`autoflush=True`(기본값)면, 쿼리를 날리기 직전에 SQLAlchemy가
**아직 커밋 안 한 변경사항을 자동으로 DB에 flush**한다.

```python
# autoflush=True 였다면
team = Team(name="새팀")
db.add(team)
# ↓ 이 SELECT가 나가기 직전에 위 INSERT가 자동으로 flush됨
count = db.execute(select(func.count()).select_from(Team)).scalar_one()
```

편해 보이지만 **예상 못 한 시점에 제약조건 위반이 터진다.** 게다가 어디서 터졌는지 추적이 어렵다.
`autoflush=False`면 **flush 시점을 내가 통제**한다.

실제로 `admin.py`에서 명시적 flush를 쓰고 있다:
```python
# app/routers/admin.py:249-250
team = Team(season_id=season.id, name=name)
db.add(team)
db.flush()  # team.id 확보
```
**왜 flush가 필요한가?** `Account`를 만들려면 `team_id`가 필요한데,
`team.id`는 DB가 시퀀스로 생성한다. flush 전에는 `None`이다.
flush하면 INSERT가 나가고 `RETURNING id`로 값을 받아와 `team.id`가 채워진다.
**하지만 커밋은 아직 안 됐다** — 뒤에서 실패하면 롤백된다. 정확한 사용법이다.

**`autocommit=False`**: SQLAlchemy 2.0에서는 이게 유일한 동작이다.
명시해도 되고 안 해도 되지만, **의도를 문서화하는 효과**가 있다.

### 4-3. `DeclarativeBase` — ORM 매핑의 뿌리

```python
class Base(DeclarativeBase):
    pass
```

**[쉬움]** "지금부터 만들 클래스들은 DB 테이블이야"라고 선언하는 도장.

**[전공]**
`Base`를 상속한 클래스가 정의되는 순간, 메타클래스가 개입해서
클래스 속성(`Mapped[int]` 등)을 읽어 `Table` 객체를 만들고 `Base.metadata`에 등록한다.

`Base.metadata`는 **모든 테이블 정의의 레지스트리**다. 이게 alembic에서 쓰인다:
```python
# migrations/env.py 에서 target_metadata = Base.metadata
```
→ alembic이 "코드가 정의한 테이블"과 "실제 DB 테이블"을 비교해 마이그레이션을 자동 생성한다.

`DeclarativeBase`는 **2.0 스타일**이다. 1.x는 `declarative_base()` 함수를 썼다.
AI가 `Base = declarative_base()`로 설명하면 옛날 문법이다.

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

**[쉬움]**
요청 하나마다 새 장바구니를 하나 준다. 요청이 끝나면 장바구니를 반납한다.
`finally`가 붙어 있어서 **중간에 에러가 나도 반드시 반납**한다.

**[전공] 실행 순서**

```
요청 도착
  ↓
FastAPI가 get_db() 호출 → 제너레이터 생성
  ↓
next() → SessionLocal() 실행 → yield db 에서 멈춤
  ↓
db 객체를 핸들러 함수의 db 파라미터로 주입
  ↓
핸들러 실행 (여기서 예외가 나도 상관없음)
  ↓
응답 생성 완료
  ↓
FastAPI가 제너레이터를 다시 next() → finally 블록 → db.close()
```

**핵심 질문 1: 왜 `return db`가 아니라 `yield db`인가?**

`return`이면 "정리(cleanup) 코드를 실행할 기회"가 없다.
`yield`는 함수 실행을 중간에 멈췄다가 나중에 재개할 수 있으므로,
**"요청 처리 후"라는 시점에 코드를 끼워 넣을 수 있다.**
`contextlib.contextmanager`, `with`문과 정확히 같은 원리다.

**핵심 질문 2: 왜 `db.commit()`을 여기서 안 하는가?**

두 가지 철학이 있다:
- (A) `get_db`에서 자동 커밋: 핸들러가 편하다. 하지만 **어디서 커밋됐는지 안 보인다**
- (B) 핸들러가 명시적으로 커밋 (현재 코드): 조금 번거롭지만 **트랜잭션 경계가 코드에 드러난다**

이 프로젝트는 (B)다. 실제 코드를 보면:
```python
# app/routers/submissions.py:126-127
db.add(submission)
db.commit()
```
커밋하지 않은 세션은 `close()` 시점에 **암묵적으로 롤백**된다. 즉 **커밋을 잊으면 저장이 안 된다.**
이건 안전한 방향의 실수다(잘못 저장되는 것보다 안 저장되는 게 낫다).

**핵심 질문 3: 세션 하나가 요청 하나 — 왜?**

세션을 전역으로 하나 두면:
- 여러 요청이 같은 Identity Map을 공유 → **A 요청이 수정 중인 객체를 B 요청이 본다**
- 한 요청이 롤백하면 다른 요청의 변경도 날아간다
- 스레드 안전하지 않다

→ **요청당 세션 1개(session-per-request)** 는 웹 애플리케이션의 표준 패턴이다.

**핵심 질문 4: 워커는 `get_db`를 안 쓴다. 왜?**

```python
# worker/run.py:110
db = SessionLocal()
try:
    ...
finally:
    db.close()
```
워커는 FastAPI 요청 주기가 없다. **직접 열고 직접 닫는다.** 같은 패턴을 손으로 쓴 것뿐이다.

그리고 `worker/run.py:main()`을 보면 **폴링 루프마다 세션을 새로 연다**:
```python
while True:
    db = SessionLocal()
    try:
        submission_id = claim_next_submission(db)
    finally:
        db.close()
```
**왜 루프 밖에서 한 번만 열지 않는가?**
세션을 오래 유지하면 (a) Identity Map에 객체가 계속 쌓여 메모리가 늘고,
(b) 트랜잭션이 길게 열려 있어 **다른 트랜잭션이 이 세션의 옛 스냅샷을 보게** 되며,
(c) 커넥션이 끊겼을 때 복구가 어렵다.
→ **짧게 열고 짧게 닫는다.** 정석이다.

---

## 5. 요청 하나의 전체 여정 — 통합 정리

`GET /leaderboard` 를 브라우저에 치면:

```
 1. 브라우저 → DNS/hosts → 127.0.0.1:8000 TCP 연결
 2. HTTP 요청 텍스트 전송
      GET /leaderboard HTTP/1.1
      Cookie: session=...

 3. uvicorn: HTTP 파싱 → ASGI scope dict 생성
      {"type":"http", "method":"GET", "path":"/leaderboard", "headers":[...]}

 4. SessionMiddleware.__call__
      - Cookie 헤더에서 session 값 추출
      - itsdangerous로 서명 검증 (secret_key 사용)
      - 성공하면 base64 디코드 → JSON → scope["session"] = {"team_id": 6}
      - 실패/없음이면 scope["session"] = {}

 5. FastAPI 라우터: app.routes를 순서대로 스캔
      "/" ?           → 매칭 안 됨
      "/leaderboard"? → 매칭!  leaderboard_entry 함수

 6. 의존성 해결:
      - Request 객체 생성 (scope 감싸기)
      - Depends(get_db) → get_db() 제너레이터 → SessionLocal() → Session 객체

 7. leaderboard_entry(request, db) 실행
      → get_open_season(db) → SELECT * FROM seasons WHERE status='active' ...
      → 있으면 RedirectResponse("/leaderboard/1", 303)

 8. Response 객체 → ASGI send 이벤트로 변환
      {"type":"http.response.start","status":303,"headers":[("location","/leaderboard/1")]}

 9. SessionMiddleware: 세션이 안 바뀌었으므로 Set-Cookie 안 붙임

10. uvicorn: HTTP 응답 텍스트 생성 → TCP 전송
      HTTP/1.1 303 See Other
      location: /leaderboard/1

11. get_db()의 finally → db.close() → 커넥션 풀에 반납

12. 브라우저: 303을 보고 자동으로 GET /leaderboard/1 재요청 → 3번부터 반복
```

**이 12단계를 막힘없이 말할 수 있으면 1단계는 끝난 것이다.**

---

## 6. 자가 점검 질문

각 질문에 소리내어 답해보라. 막히면 해당 절로 돌아간다.

1. `uvicorn`과 `FastAPI`는 각각 무슨 일을 하는가? 왜 분리되어 있는가?
2. `def`와 `async def` 라우트의 실행 방식 차이는? 우리 코드에서 `async def`는 어디에 왜 있는가?
3. `SessionMiddleware`는 세션 데이터를 **어디에** 저장하는가? 그 데이터를 사용자가 읽을 수 있는가? 바꿀 수 있는가?
4. `app.mount`와 `app.include_router`의 차이는?
5. `/leaderboard/seasons`를 `/leaderboard/{season_id}`보다 먼저 선언해야 하는 이유는? 안 하면 어떤 에러가?
6. 설정을 `.env`로 빼는 것이 주는 이득 3가지는?
7. `extra="ignore"`가 없으면 무슨 일이 일어나는가?
8. `models_dir`를 필드가 아니라 `@property`로 만든 이유는?
9. `pool_pre_ping=True`가 우리 워커에게 특히 중요한 이유는?
10. `autoflush=False`인데 `admin.py`는 왜 `db.flush()`를 명시적으로 부르는가?
11. `get_db`가 `return`이 아니라 `yield`를 쓰는 이유는?
12. 커밋을 안 하고 요청이 끝나면 데이터는 어떻게 되는가?
13. 워커가 폴링 루프마다 세션을 새로 여는 이유 3가지는?

---

## 7. 실험 과제

> 모두 로컬에서, 운영 DB를 건드리지 않고 할 수 있다.

**실험 A — 미들웨어를 눈으로 보기**
`main.py`에 커스텀 미들웨어를 추가하고 로그를 찍어 "양파 구조"를 확인한다.
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
확인할 것: `add_middleware` 순서를 바꾸면 로그 순서가 어떻게 바뀌는가?

**실험 B — 세션 쿠키 까보기**
로그인한 뒤 브라우저 개발자도구 → Application → Cookies → `session` 값을 복사해
파이썬으로 디코드해보라.
```python
import base64, json
v = "여기에_붙여넣기".split(".")[0]
print(json.loads(base64.urlsafe_b64decode(v + "==")))
```
**"서명은 있지만 암호화는 아니다"를 눈으로 확인하는 실험이다.**

**실험 C — 라우트 순서 버그 재현**
`leaderboard.py`에서 `/leaderboard/seasons` 라우트를 `/leaderboard/{season_id}` **아래로** 옮기고
`/leaderboard/seasons`에 접속해보라. 422 응답과 그 본문을 확인한다. 그리고 되돌린다.

**실험 D — 설정 검증 확인**
`.env`에 `DAILY_SUBMISSION_LIMIT=다섯` 을 넣고 서버를 띄워보라.
어떤 예외가 어느 시점에 나는가? 이것이 fail-fast다.

**실험 E — 등록된 라우트 전부 출력**
```python
python -c "
from app.main import app
for r in app.routes:
    print(getattr(r,'methods',''), r.path)
"
```
내가 만든 URL 전체 목록이 나온다. `include_router`가 실제로 무슨 일을 했는지 눈으로 확인.

---

## 8. 다음 단계로 넘어가기 전에

`main.py`에서 남은 미해결 질문은 전부 `models.py`에 있다:
- `Depends(get_db)`가 주는 `Session`으로 **어떤 테이블**을 어떻게 조회하는가?
- `Team`, `Submission` 같은 클래스는 어디서 왔는가?

→ [02-data-model.md](02-data-model.md)
