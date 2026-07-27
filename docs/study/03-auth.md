# 3단계. 인증 — `deps.py`, `security.py`, `routers/auth.py`

> 이 단계의 목표: **"서버는 요청을 보낸 사람이 누구인지 어떻게 아는가?"** 에 밑바닥부터 답하는 것.
> 그리고 **"비밀번호는 왜 되돌릴 수 없게 저장하는가?"**.
> 마지막으로 **이 코드에 지금 빠져 있는 보안 장치**가 무엇인지 알고 넘어간다.

---

## 0. 근본 문제 — HTTP는 기억을 못 한다

### 무엇을(What)

**[쉬움]**
HTTP는 **기억상실증 점원**이다.
손님이 "저 아까 왔던 사람이에요"라고 해도 기억을 못 한다. 요청 하나하나가 완전히 처음이다.

그래서 **번호표**를 준다. 손님이 올 때마다 번호표를 보여주면
"아, 3번 손님이시군요" 하고 알아본다. 그 번호표가 **쿠키**다.

**[전공]**
HTTP는 **stateless** 프로토콜이다. 요청 간에 서버가 유지하는 상태가 없다.
이건 결함이 아니라 **의도된 설계**다 — 서버를 여러 대로 늘리기 쉽고, 중간에 캐시를 둘 수 있다.

하지만 "로그인"은 본질적으로 상태다. 그래서 상태를 **어딘가에 실어 보내야** 한다.

| 방법 | 어디에 | 문제 |
|---|---|---|
| URL 파라미터 | `?user=6` | 주소창에 노출, 링크 공유하면 계정 유출, 로그에 남음 |
| 요청 본문 | POST body | GET에는 못 씀 |
| **쿠키** | `Cookie:` 헤더 | 브라우저가 자동 관리. **표준 해법** |
| Authorization 헤더 | `Bearer <token>` | JS로 직접 붙여야 함. SPA/API에 적합 |

이 프로젝트는 서버 렌더링(SPA 아님)이므로 **쿠키가 자연스러운 선택**이다.

### 어떻게(How) — 쿠키의 실제 동작

```
1) 로그인 성공 시 서버 응답:
   HTTP/1.1 303 See Other
   set-cookie: session=eyJ0ZWFtX2lkIjo2fQ.aBcD.EfGh; path=/; httponly; samesite=lax
   location: /submit

2) 이후 브라우저는 같은 도메인의 모든 요청에 자동으로 붙인다:
   GET /submit HTTP/1.1
   Cookie: session=eyJ0ZWFtX2lkIjo2fQ.aBcD.EfGh
```

**브라우저가 자동으로 붙인다** — 이 점이 편리하면서 동시에 **CSRF 취약점의 근원**이다(뒤에서 다룸).

쿠키 속성들:
| 속성 | 의미 | 우리 설정 |
|---|---|---|
| `httponly` | JS(`document.cookie`)로 못 읽음 → XSS로 세션 탈취 방지 | Starlette 기본 ON |
| `samesite=lax` | 다른 사이트에서 온 요청엔 쿠키를 안 보냄(GET 최상위 이동 제외) | Starlette 기본 |
| `secure` | HTTPS에서만 전송 | `session_https_only` 설정으로 제어 |
| `max-age` | 만료 시각 | Starlette 기본 14일 (`max_age=14*24*60*60`) |

---

## 1. 세션을 어디에 저장할 것인가 — 3가지 방식

### 세 가지 선택지

**방식 A. 서버 저장 세션 (전통적)**
```
쿠키:  session_id=abc123        (의미 없는 랜덤 문자열)
서버:  Redis/DB에 {abc123: {"team_id": 6}} 저장
```
- 장점: 서버가 세션을 **즉시 무효화**할 수 있다 (강제 로그아웃)
- 장점: 세션에 큰 데이터를 넣어도 됨
- 단점: **저장소가 하나 더 필요**하다. Redis 운영, 백업, 장애 대응

**방식 B. 서명된 쿠키 (이 프로젝트)**
```
쿠키:  session=<base64(JSON)>.<timestamp>.<HMAC 서명>
서버:  아무것도 저장 안 함
```
- 장점: **서버가 무상태(stateless)**. 저장소 불필요
- 단점: 개별 세션을 무효화할 수 없다 (전체 무효화만 가능)
- 단점: 쿠키 크기 제한(4KB)

**방식 C. JWT**
- 방식 B와 원리는 같다(서명된 토큰). 표준 포맷, 만료·발급자 등 클레임 규격이 있음
- 보통 `Authorization` 헤더로 보냄. SPA/모바일/마이크로서비스에 적합

### 왜 이 프로젝트는 B인가

**[쉬움]**
Redis를 하나 더 띄우면 관리할 프로그램이 하나 더 늘어난다.
사용자가 10팀뿐인데 그럴 필요가 없다.

**[전공]**
- 운영 부담 최소화 (`docker-compose.yml`에 db와 web 둘뿐인 이유)
- 세션에 담는 것이 `{"team_id": 6}` 뿐 — 크기 문제 없음
- **강제 로그아웃 요구사항이 없다.** 시즌이 끝나면 계정 자체를 삭제한다
- 대회 기간이 짧아 세션 수명 관리가 중요하지 않다

**적절한 판단이다.** 다만 한계를 알고 있어야 한다:
> "특정 팀을 즉시 로그아웃시켜라"는 요구가 오면 이 구조로는 못 한다.
> 우회책: `Account`에 `session_epoch` 컬럼을 두고 세션에도 넣어 비교하거나,
> 아예 서버 세션으로 전환.

---

## 2. 서명(Signature)의 원리 — 왜 위조할 수 없는가

### 무엇을(What)

**[쉬움]**
편지 끝에 도장을 찍는다. 도장은 나만 갖고 있다.
누군가 편지 내용을 고치면 도장이 안 맞게 된다 → 위조 발각.

**단, 편지 내용 자체는 누구나 읽을 수 있다.** 도장은 "안 바뀌었음"만 증명하지 "비밀"을 만들지 않는다.

**[전공] — HMAC**

```
서명 = HMAC-SHA1( key = SESSION_SECRET, message = base64(JSON) + timestamp )
쿠키 = base64(JSON) . timestamp . 서명
```

검증:
1. 쿠키를 `.`으로 분해
2. 앞 두 조각으로 서명을 **다시 계산**
3. 쿠키에 들어있던 서명과 **상수 시간 비교**(`hmac.compare_digest`)
4. 다르면 → 세션을 빈 dict로 취급 (에러를 내지 않는다)

**왜 위조가 불가능한가?**
HMAC은 **키 없이는 올바른 서명을 만들 수 없다.** 공격자가 `{"team_id": 1}`을
`{"team_id": 999}`로 바꾸면 서명을 새로 만들어야 하는데, `SESSION_SECRET`이 없어 못 만든다.

**왜 상수 시간 비교인가?**
일반 `==`는 첫 글자가 다르면 즉시 False를 반환한다.
공격자가 응답 시간을 재면서 한 글자씩 맞춰가면 서명을 알아낼 수 있다(**타이밍 공격**).
`compare_digest`는 항상 전체를 비교해 시간이 일정하다.

### 중요: 서명 ≠ 암호화

```python
# 실험: 실제 쿠키를 디코드해보라
import base64, json
raw = "eyJ0ZWFtX2lkIjo2fQ"        # 쿠키의 첫 조각
print(json.loads(base64.urlsafe_b64decode(raw + "==")))
# → {'team_id': 6}
```

**누구나 읽을 수 있다.** 그래서:

> **세션에는 절대 넣으면 안 되는 것**: 비밀번호, 비밀번호 해시, 주민번호,
> 내부 시스템 비밀, "관리자 여부" 같은 걸 클라이언트가 조작 가능하다고 착각할 여지가 있는 값.

**이 프로젝트가 잘한 점**: 세션에 `team_id` / `admin_id` **정수 하나만** 넣는다.
```python
request.session["team_id"] = account.team_id      # auth.py:34
request.session["admin_id"] = admin.id            # admin.py:68
```
권한 정보를 세션에 넣지 않고, **매 요청마다 DB에서 다시 조회**한다:
```python
# app/deps.py:26-34
admin_id = request.session.get("admin_id")
admin = db.get(AdminAccount, admin_id)     # ← 진짜 관리자인지 DB로 확인
```

**만약 `session["is_admin"] = True` 로 했다면?**
값 자체는 위조 못 하지만, **관리자 세션 쿠키를 통째로 훔치면** 그대로 관리자가 된다.
지금 구조에서도 쿠키 탈취는 위험하지만, 최소한 **`admin_accounts` 테이블에서 행을 지우면 즉시 차단**된다.

---

## 3. `SESSION_SECRET` — 왜 바뀌면 전원 로그아웃되는가

### 답

서명은 `SESSION_SECRET`으로 만든다. 비밀이 바뀌면 **기존 쿠키의 서명을 재계산한 값이 달라진다**
→ 검증 실패 → 세션 = 빈 dict → `team_id` 없음 → 로그인 화면.

**[쉬움]** 도장을 새 걸로 바꾸면, 옛날 도장이 찍힌 편지는 전부 "가짜"가 된다.

### 운영상의 함의

memory에 기록된 주의사항:
> **세션 무효화**: SESSION_SECRET 변경 시 기존 세션 전부 무효화 → 대회 시작 직전에 설정

**즉 대회 중에 `SESSION_SECRET`을 바꾸면 참가자 전원이 갑자기 로그아웃된다.**

### 기본값이 위험한 이유

```python
session_secret: str = "change-me-in-production"
```

이 기본값 그대로 배포하면?
**공격자가 소스코드(또는 이 문서)를 보고 같은 키로 서명을 만들어
`{"admin_id": 1}` 쿠키를 위조할 수 있다.** → 관리자 계정 완전 탈취.

Cloudflare Tunnel로 인터넷에 노출하는 서비스이므로 **실제 위험**이다.

```bash
openssl rand -hex 32
```
로 만들어 `.env`에 넣어야 한다.

> **[전공] 개선 제안**: 프로덕션에서 기본값이면 부팅을 거부하게 만들 수 있다.
> ```python
> from pydantic import field_validator
>
> @field_validator("session_secret")
> @classmethod
> def _reject_default(cls, v, info):
>     if v == "change-me-in-production" and info.data.get("session_https_only"):
>         raise ValueError("공개 배포 시 SESSION_SECRET을 반드시 설정하세요")
>     return v
> ```
> `worker/run_worker.sh`에는 이미 환경변수 검증 로직이 있다. 웹에도 같은 방어를 둘 만하다.

---

## 4. `security.py` — 비밀번호를 다루는 3개 함수

```python
import secrets
import string
import bcrypt

_PASSWORD_ALPHABET = string.ascii_uppercase + string.ascii_lowercase + string.digits


def generate_password(length: int = 10) -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def hash_password(raw_password: str) -> str:
    return bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(raw_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(raw_password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False
```

### 4-1. `secrets` vs `random` — 반드시 구분할 것

**[쉬움]**
`random`은 "적당히 무작위처럼 보이는" 숫자를 만든다. 게임의 주사위용이다.
씨앗(seed) 값을 알면 **다음에 나올 숫자를 전부 예측할 수 있다.**

`secrets`는 운영체제의 진짜 무작위(마우스 움직임, 하드웨어 노이즈 등에서 수집)를 쓴다.

**[전공]**
- `random`: Mersenne Twister. **624개의 출력만 관찰하면 내부 상태를 완전히 복원**할 수 있다.
  즉 앞뒤 모든 값을 계산 가능. **암호학적으로 완전히 무용하다.**
- `secrets`: `os.urandom()` → `/dev/urandom`(Linux) / `BCryptGenRandom`(Windows) → CSPRNG

**공격 시나리오**: 관리자가 팀 10개를 연속 등록한다.
`random`이었다면, 공격자가 자기 팀 비밀번호 하나를 알 때
**다른 9팀의 비밀번호를 계산해낼 수 있다.**

→ **비밀번호, 토큰, 세션 ID, 리셋 링크에는 무조건 `secrets`.**

**엔트로피 계산**:
```
알파벳 62자 (대문자26 + 소문자26 + 숫자10), 길이 10
경우의 수 = 62^10 ≈ 8.4 × 10^17
log2(8.4e17) ≈ 59.5비트
```
59비트는 **온라인 무차별 대입에는 충분히 안전**하다(초당 1000번 시도해도 수천만 년).
오프라인(해시 유출 후)에서는 bcrypt cost가 방어한다.

**특수문자를 뺀 이유**: 관리자가 화면에서 읽어 참가자에게 전달하는 방식이라,
`l/1/I`, `O/0` 혼동은 남아 있지만 특수문자보다는 전달 오류가 적다.
(더 개선하려면 헷갈리는 문자를 알파벳에서 빼는 방법도 있다)

### 4-2. bcrypt — 왜 "암호화"가 아니라 "해시"인가

**[쉬움]**
- **암호화**: 자물쇠. 열쇠가 있으면 원래대로 되돌릴 수 있다. → 열쇠가 유출되면 끝
- **해시**: 믹서기. 갈아버리면 되돌릴 수 없다. → 유출돼도 원본을 모른다

로그인할 때는 어떻게 확인하나?
입력한 비밀번호를 **똑같이 갈아서** 저장된 결과와 같은지 본다.

**[전공]**
비밀번호 저장에 요구되는 성질:
1. **단방향성**: 해시에서 원문 복원 불가
2. **솔트(salt)**: 같은 비밀번호라도 매번 다른 해시 → **레인보우 테이블 무력화**
3. **느림(work factor)**: 의도적으로 느리게 → 무차별 대입 억제

**SHA-256으로는 왜 안 되나?**
SHA-256은 **빠르게 설계됐다.** GPU로 초당 수십억 번 계산한다.
8자리 비밀번호는 몇 시간이면 전부 뚫린다.
bcrypt/scrypt/argon2는 **일부러 느리게** 설계됐다.

**bcrypt 해시의 구조:**
```
$2b$12$LongSaltAndHashStringHere.....
 │  │  │
 │  │  └─ 솔트(22자) + 해시(31자)  ← 솔트가 해시 안에 같이 들어있다
 │  └──── cost factor 12 → 2^12 = 4096번 반복
 └─────── 알고리즘 버전 (2b)
```

**솔트가 해시에 포함되어 있다** — 이게 중요하다.
그래서 `checkpw`는 저장된 해시에서 솔트를 꺼내 같은 조건으로 재계산한다.
**솔트를 따로 저장할 컬럼이 필요 없다.** `Account` 테이블에 `salt` 컬럼이 없는 이유가 이것이다.

**cost factor**: `bcrypt.gensalt()`의 기본값은 12 (bcrypt 4.x).
2^12 = 4096회 반복. 대략 **200~300ms**.

이게 `admin.py`에 언급된 그 비용이다:
```python
# 한 번에 등록할 수 있는 팀 수 상한. 실수로 큰 목록을 붙여넣는 것을 막기 위한 값이며,
# 비밀번호 해시(bcrypt)가 팀당 수백 ms라 이 정도가 응답 시간 측면에서도 상한이다.
MAX_BULK_TEAMS = 50
```
50팀 × 250ms = **12.5초**. HTTP 요청 하나로는 이미 긴 시간이다. 정확한 계산이다.

> **[전공] 왜 느린 게 좋은가**: 로그인 1회에 250ms는 사용자가 못 느낀다.
> 하지만 해시 DB가 유출됐을 때 공격자가 1억 개 후보를 시도하려면
> 250ms × 1억 = **약 800년**. 이 비대칭이 bcrypt의 전부다.

> **현대의 권장**: Argon2id가 더 낫다(메모리 하드 — GPU/ASIC 저항성).
> bcrypt는 72바이트 입력 제한도 있다. 하지만 **bcrypt는 여전히 충분히 안전**하고
> 라이브러리가 성숙해서 이 규모에서는 완벽히 타당한 선택이다.

### 4-3. `verify_password`의 `try/except ValueError`

```python
try:
    return bcrypt.checkpw(raw_password.encode("utf-8"), password_hash.encode("utf-8"))
except ValueError:
    return False
```

**왜 필요한가?**
DB의 `password_hash`가 bcrypt 형식이 아니면(빈 문자열, 손상, 다른 알고리즘으로 만든 값)
`checkpw`가 `ValueError: invalid salt`를 던진다.

이걸 안 잡으면 **로그인 시도가 500 에러**가 된다.
잡아서 `False`를 반환하면 → "비밀번호가 틀렸습니다"로 정상 처리된다.

**[전공] 방어적 프로그래밍의 좋은 예다.**
"데이터가 깨졌을 때 서비스 전체가 아니라 그 요청만 실패하게 한다."

단, `except ValueError`만 잡는 것도 의도적이다. `TypeError`(코딩 실수)는 그대로 터진다.
**너무 넓게 잡으면(`except Exception`) 진짜 버그가 숨는다.**

---

## 5. `routers/auth.py` — 로그인 흐름 정독

```python
@router.post("/login")
def login_submit(request: Request, login_id: str = Form(...), password: str = Form(...),
                 db: Session = Depends(get_db)):
    account = db.execute(select(Account).where(Account.login_id == login_id)).scalar_one_or_none()
    if account is None or not verify_password(password, account.password_hash):
        return templates.TemplateResponse(
            request, "login.html", {"error": "아이디 또는 비밀번호가 올바르지 않습니다."}, status_code=401
        )
    request.session.clear()
    request.session["team_id"] = account.team_id
    return RedirectResponse("/submit", status_code=303)
```

### 5-1. `Form(...)` — FastAPI가 폼을 파싱하는 방식

`...`(Ellipsis)는 pydantic에서 **"필수"** 를 뜻하는 관례다. 기본값이 없다.
값이 없으면 FastAPI가 자동으로 422를 반환한다.

`Form`을 쓰려면 `python-multipart` 패키지가 필요하다 — `requirements.txt`에 있다.
HTML 폼은 `application/x-www-form-urlencoded` 또는 `multipart/form-data`로 전송되는데,
이 파싱을 그 라이브러리가 한다.

### 5-2. `scalar_one_or_none()` — 2.0 스타일 결과 추출

| 메서드 | 0건 | 1건 | 2건 이상 |
|---|---|---|---|
| `scalar_one()` | **예외** | 값 | **예외** |
| `scalar_one_or_none()` | `None` | 값 | **예외** |
| `scalars().first()` | `None` | 값 | 첫 번째 |
| `scalars().all()` | `[]` | `[값]` | 리스트 |

로그인 ID는 unique이므로 2건이 나올 수 없다. `scalar_one_or_none()`이 정확하다.
**만약 2건이 나오면 예외가 터지는데, 그게 옳다** — 데이터가 깨졌다는 신호를 삼키면 안 된다.

`quota.py`에서 `scalar_one()`을 쓰는 이유도 같다:
```python
done_count = db.execute(stmt).scalar_one()   # COUNT는 반드시 1행
```

### 5-3. **`or`의 단락 평가가 만드는 미묘한 취약점**

```python
if account is None or not verify_password(password, account.password_hash):
```

`account is None`이 참이면 **`verify_password`를 호출하지 않는다**(단락 평가).

**결과**: 존재하지 않는 ID로 로그인하면 bcrypt 계산(250ms)이 생략되어 **응답이 훨씬 빠르다.**

**[전공] 사용자 열거(user enumeration) 타이밍 공격**
공격자가 응답 시간만 재도 "이 ID는 존재한다 / 안 한다"를 구분할 수 있다.

우리 시스템의 로그인 ID는 `f"{season.id}-{team.id}"` 형식(`admin.py:252`)이라
`1-1`, `1-2`, … 로 **어차피 추측 가능**하다. 심각도는 낮다.

**제대로 막으려면** 더미 해시로 항상 같은 시간을 쓴다:
```python
_DUMMY_HASH = bcrypt.hashpw(b"dummy", bcrypt.gensalt()).decode()

account = ...
password_ok = verify_password(password, account.password_hash if account else _DUMMY_HASH)
if account is None or not password_ok:
    ...
```

**에러 메시지는 잘 되어 있다**: "아이디 또는 비밀번호가 올바르지 않습니다."
"아이디가 없습니다"라고 하면 **어떤 ID가 존재하는지 알려주는 꼴**이다.

### 5-4. **`request.session.clear()` — 세션 고정 공격 방어**

```python
request.session.clear()          # ← 이 줄이 왜 있는가
request.session["team_id"] = account.team_id
```

**[쉬움]**
로그인하기 전에 갖고 있던 번호표를 **버리고 새 번호표를 받는다.**

**[전공] 세션 고정(Session Fixation) 공격**

일반적인 서버 세션 방식에서:
1. 공격자가 사이트에 접속해 세션 ID `X`를 받는다
2. 피해자에게 `https://site/?sessionid=X` 같은 링크로 세션 ID를 심는다
3. 피해자가 그 세션으로 로그인한다
4. 서버가 세션 ID를 바꾸지 않으면 → **공격자도 `X`로 로그인 상태에 접근**

**서명 쿠키 방식에서는 세션 ID 개념이 없어서 이 공격이 직접적으로는 성립하지 않는다.**
그렇다면 `clear()`는 왜 필요한가?

1. **관리자 세션과 팀 세션의 혼재 방지.**
   관리자로 로그인한 상태에서 팀 로그인을 하면 `{"admin_id":1, "team_id":6}`이 된다.
   → 팀 계정으로 로그인했는데 관리자 화면도 열린다. **권한 누수다.**
   `clear()`가 이걸 막는다. `admin.py`의 로그인도 똑같이 `clear()`를 부른다.
2. 이전 세션에 남은 잔여 데이터(플래시 메시지 등) 정리
3. **좋은 습관**: 인증 경계를 넘을 때는 세션을 새로 시작한다

> **이 한 줄이 실제로 막는 시나리오를 직접 재현해보라.**
> `clear()`를 주석 처리 → 관리자 로그인 → 팀 로그인 → `/admin` 접속.
> 열리는가? (실험 과제 B)

### 5-5. `RedirectResponse(..., status_code=303)` — POST-Redirect-GET

**303을 쓰는 이유는 4단계에서 자세히 다룬다.** 여기선 요점만:

| 코드 | 의미 | 리다이렉트 시 메서드 |
|---|---|---|
| 301 Moved Permanently | 영구 이동 | GET으로 바뀜 (브라우저가 캐시함 — 위험) |
| 302 Found | 임시 | **명세는 유지, 실제론 GET으로 바뀜** (역사적 혼란) |
| **303 See Other** | 다른 자원 참조 | **반드시 GET** (명확) |
| 307/308 | 임시/영구 | **메서드 유지** (POST → POST) |

**POST 처리 후에는 303이 정답이다.** 302는 브라우저마다 다르게 동작한 역사가 있고,
301은 브라우저가 캐시해서 나중에 URL이 바뀌어도 옛 주소로 간다.

### 5-6. 로그아웃이 `GET`인 문제

```python
@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
```

**[전공] 이건 엄밀히 말해 HTTP 명세 위반이다.**
GET은 **안전(safe)** 해야 한다 — 서버 상태를 바꾸면 안 된다.
로그아웃은 상태를 바꾼다(세션 삭제).

**실제 문제**:
- 브라우저 프리페치나 링크 프리뷰가 `/logout`을 긁으면 **의도치 않게 로그아웃**된다
- 공격자가 `<img src="https://site/logout">` 을 어딘가에 심으면 강제 로그아웃 (경미한 CSRF)

**제대로 하려면** `<form method="post" action="/logout">` + `@router.post("/logout")`.

**그럼에도 지금 GET인 이유**: 템플릿에서 `<a href="/logout">로그아웃</a>` 한 줄로 끝나서 편하다.
피해가 "로그아웃됨"뿐이라 실무에서도 흔히 타협하는 지점이다.
**하지만 알고 타협하는 것과 모르는 것은 다르다.**

---

## 6. `deps.py` — FastAPI 의존성 주입의 핵심

```python
def get_current_team(request: Request, db: Session = Depends(get_db)) -> Team:
    team_id = request.session.get("team_id")
    if not team_id:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    team = db.get(Team, team_id)
    if team is None:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return team
```

### 6-1. 의존성 주입(DI)이란 무엇인가

**[쉬움]**
요리사가 재료를 직접 사러 가지 않는다. 주방 보조가 미리 손질해서 갖다 놓는다.
요리사는 "양파 필요해"라고 **선언만** 하면 된다.

```python
def submit_form(request: Request, team: Team = Depends(get_current_team), ...):
    #                              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    #                              "로그인된 팀 객체를 주세요"라고 선언
```
이 함수 안에는 세션을 읽거나 DB를 조회하는 코드가 **한 줄도 없다.**
이미 `team`이 준비되어 들어온다.

**[전공]**
FastAPI는 함수 시그니처를 **런타임에 introspection**해서 의존성 그래프를 만든다.

```
submit_form
 ├── request: Request        (특수 타입 — 프레임워크가 직접 제공)
 ├── team: Depends(get_current_team)
 │      ├── request: Request
 │      └── db: Depends(get_db)      ← 제너레이터 의존성
 └── db: Depends(get_db)             ← 같은 요청 안에서는 캐시되어 재사용!
```

**중요**: `get_db`가 두 번 나오지만 **한 요청 안에서는 한 번만 실행**된다(기본 `use_cache=True`).
→ 같은 `Session` 객체가 주입된다. **같은 트랜잭션 안에서 동작한다는 뜻이다.**
이게 아니었다면 `get_current_team`이 읽은 팀과 핸들러가 쓰는 세션이 달라 문제가 생긴다.

### 6-2. DI가 주는 실질적 이득

1. **횡단 관심사 분리**: 인증 로직이 모든 핸들러에 복붙되지 않는다
2. **테스트 용이성**: `app.dependency_overrides[get_current_team] = lambda: fake_team`
   → 로그인 없이 테스트 가능
3. **일관성**: 인증 규칙을 바꿀 때 `deps.py` 한 곳만 고치면 된다
4. **선언적**: 함수 시그니처만 봐도 "이 엔드포인트는 로그인이 필요하구나"를 안다

### 6-3. **`HTTPException`으로 리다이렉트하는 특이한 패턴**

```python
raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
```

**이건 관용적이지 않다.** 보통 `HTTPException`은 에러(4xx/5xx)에 쓴다.
303은 에러가 아니라 리다이렉트다.

**왜 이렇게 했나?**
의존성 함수는 **응답 객체를 반환할 수 없다.** 반환값은 핸들러의 파라미터로 들어가기 때문이다.
그래서 흐름을 끊으려면 **예외를 던지는 수밖에 없다.**

**동작은 하는가?** 한다. FastAPI의 `http_exception_handler`가
`status_code`와 `headers`를 그대로 응답에 반영한다. 브라우저는 `Location` 헤더를 보고 이동한다.

**대안**: 
```python
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import RedirectResponse

class NotAuthenticated(Exception): pass

@app.exception_handler(NotAuthenticated)
async def _redirect_to_login(request, exc):
    return RedirectResponse("/login", status_code=303)
```
더 명시적이지만 코드가 늘어난다. **현재 방식은 실용적 타협이다.**

**[전공] 부작용 하나**: `/docs`(OpenAPI 문서)에서 이 엔드포인트가
"303 에러를 낼 수 있음"으로 표시된다. API 클라이언트 입장에서는 혼란스럽다.
브라우저 전용 서비스라 문제되지 않는다.

### 6-4. 세 가지 의존성의 차이

```python
get_current_team          # 로그인 필수. 없으면 /login 으로
get_current_team_optional # 로그인 안 해도 됨. 없으면 None
get_current_admin         # 관리자 필수. 없으면 /admin/login 으로
```

`get_current_team_optional`은 **"로그인했으면 다르게 보여주고 싶은" 공개 페이지**용이다.
현재 코드에서 **실제로 쓰이는 곳이 없다.** 리더보드가 로그인 여부와 무관하게 동일하기 때문.

> 사용 예시가 있다면: 리더보드에서 "내 팀"을 하이라이트하는 기능.
> 지금은 미사용 코드이므로, **쓸 계획이 없으면 지우는 것도 방법**이다.

### 6-5. `team is None`일 때 `session.clear()`를 하는 이유

```python
team = db.get(Team, team_id)
if team is None:
    request.session.clear()      # ← 여기
    raise HTTPException(...)
```

**언제 이런 일이 생기나?**
- 시즌이 아카이브되며 팀이 삭제됐는데 참가자 브라우저에 쿠키가 남아 있다
- 관리자가 팀을 지웠다
- DB를 새로 만들었다(개발 중 흔함)

`clear()`가 없으면 **매 요청마다 "쿠키 있음 → DB 조회 → 없음 → 리다이렉트"** 를 반복한다.
쿠키를 지워주면 다음부터는 DB 조회 없이 바로 로그인 화면이다. **작지만 옳은 처리다.**

---

## 7. **이 코드에 빠져 있는 것 — 반드시 알고 넘어갈 것**

top-down 공부의 핵심은 "있는 것"뿐 아니라 **"없는 것"을 아는 것**이다.

### 7-1. CSRF 보호가 없다

**[쉬움]**
악당이 만든 사이트에 접속했는데, 그 사이트가 몰래
"우리 리더보드 사이트에 요청 보내기" 코드를 심어놨다.
브라우저는 쿠키를 **자동으로** 붙이므로, 내 로그인 상태로 요청이 나간다.

**[전공] 공격 시나리오**

관리자가 로그인한 상태로 악성 페이지를 연다:
```html
<form action="https://our-tunnel.trycloudflare.com/admin/teams/3/disqualify" method="POST">
</form>
<script>document.forms[0].submit()</script>
```
→ **관리자 권한으로 팀이 실격 처리된다.**

**왜 지금은 그나마 괜찮은가?**
1. `SameSite=Lax`가 Starlette 기본값이다. **크로스 사이트 POST에는 쿠키가 안 붙는다.**
   현대 브라우저에서는 이것만으로 대부분의 CSRF가 막힌다.
2. 관리자가 대회 중에 낯선 사이트를 열 가능성이 낮다.

**하지만 `SameSite=Lax`는 방어의 전부가 아니다:**
- 같은 사이트(same-site) 안에 XSS나 사용자 생성 콘텐츠가 있으면 무력
- 구형 브라우저는 SameSite를 무시

**정석 방어**: CSRF 토큰
```python
# 폼 렌더 시 세션에 토큰 저장 + hidden input으로 심기
token = secrets.token_urlsafe(32)
request.session["csrf"] = token
# POST 처리 시 검증
if form_token != request.session.get("csrf"):
    raise HTTPException(403)
```

> **판단**: 사내 소규모 대회 + SameSite=Lax 기본값이면 **현실적 위험은 낮다.**
> 하지만 "왜 없어도 되는가"를 설명할 수 있어야지, 그냥 없는 건 다르다.
> 관리자 상태 변경 라우트(`advance-status`, `disqualify`, `daily-count`)는 붙일 가치가 있다.

### 7-2. 로그인 시도 제한(rate limiting)이 없다

무제한으로 비밀번호를 시도할 수 있다.
- 10자리 랜덤 비밀번호라 온라인 무차별 대입은 현실적으로 불가능
- bcrypt가 250ms 걸려 초당 4회가 상한
- **하지만 동시 요청을 100개 날리면 서버 CPU가 bcrypt로 포화된다** → 사실상 DoS

간단한 완화: 로그인 실패를 IP별로 세어 일정 횟수 넘으면 지연/차단.
`slowapi` 같은 라이브러리 또는 Cloudflare 측 rate limit 규칙.

### 7-3. 세션 만료 설정이 명시되지 않았다

Starlette `SessionMiddleware`의 `max_age` 기본값은 **14일**이다.
대회가 2주라면 우연히 맞지만, **의도한 값이 아니라 기본값이다.**
```python
app.add_middleware(SessionMiddleware, secret_key=..., https_only=..., max_age=60*60*12)
```
명시하면 의도가 드러난다.

### 7-4. 비밀번호 변경 기능이 없다

참가자는 관리자가 발급한 비밀번호를 계속 쓴다. 재발급만 가능(`admin.py`).
**소규모 단기 대회에서는 합리적 생략**이다. 계정 수명이 시즌 하나뿐이므로.

---

## 8. 자가 점검 질문

1. HTTP가 stateless인데 로그인이 유지되는 원리를 3문장으로 설명하라.
2. 세션 데이터는 서버와 브라우저 중 어디에 있는가? 사용자가 읽을 수 있는가? 바꿀 수 있는가? 각각 왜?
3. HMAC 서명이 위조를 막는 원리는? 서명 비교를 상수 시간으로 하는 이유는?
4. `SESSION_SECRET`이 기본값 그대로 인터넷에 노출되면 공격자는 구체적으로 무엇을 할 수 있는가?
5. `random` 대신 `secrets`를 써야 하는 이유를 공격 시나리오로 설명하라.
6. bcrypt가 SHA-256보다 나은 두 가지 이유는? 솔트는 어디에 저장되는가?
7. `MAX_BULK_TEAMS = 50`이라는 숫자가 bcrypt와 무슨 관계인가?
8. `verify_password`가 `ValueError`를 잡지 않으면 어떤 상황에서 무슨 일이 생기는가?
9. `if account is None or not verify_password(...)` 에서 단락 평가가 만드는 정보 누출은?
10. `request.session.clear()`를 지우면 어떤 권한 누수가 가능한가?
11. 303과 302의 차이는? POST 처리 후 303을 쓰는 이유는?
12. `/logout`이 GET인 것의 문제는? 어떤 실제 피해가 가능한가?
13. `Depends(get_db)`가 한 요청에서 두 번 선언됐는데 세션이 하나인 이유는? 만약 두 개였다면?
14. 의존성 함수가 `RedirectResponse`를 return하지 못하고 `raise`해야 하는 이유는?
15. CSRF 공격 시나리오를 우리 관리자 화면 기준으로 구체적으로 서술하라. 지금 무엇이 막아주고 있는가?

---

## 9. 실험 과제

**실험 A — 쿠키 위조 시도**
1. 로그인 후 개발자도구에서 `session` 쿠키 값을 복사
2. 첫 조각(`.` 앞)을 디코드해 `{"team_id": 6}` 확인
3. `{"team_id": 1}`로 바꿔 base64 인코딩 후 쿠키를 교체
4. 페이지 새로고침 → **로그인 화면으로 튕긴다.** 서명 검증 실패 확인
5. 이번엔 `SESSION_SECRET`을 알고 있으니 직접 서명해서 위조해보라:
```python
from itsdangerous import TimestampSigner
import base64, json
s = TimestampSigner("여기에_실제_SESSION_SECRET")
data = base64.urlsafe_b64encode(json.dumps({"team_id":1}).encode()).rstrip(b"=")
print(s.sign(data).decode())
```
→ **이게 되는 것을 보면 `SESSION_SECRET`의 중요성이 몸으로 이해된다.**

**실험 B — 세션 혼재 재현**
`auth.py`와 `admin.py`의 `request.session.clear()`를 둘 다 주석 처리 →
관리자 로그인 → (같은 브라우저에서) 팀 로그인 → `/admin` 접속.
관리자 화면이 열리는가? 쿠키 내용은? 확인 후 복구한다.

**실험 C — bcrypt 비용 측정**
```python
import time, bcrypt
for cost in (10, 12, 14):
    t = time.perf_counter()
    bcrypt.hashpw(b"password", bcrypt.gensalt(cost))
    print(cost, f"{(time.perf_counter()-t)*1000:.0f}ms")
```
cost가 1 오를 때마다 시간이 2배가 되는 것을 확인하라.
`MAX_BULK_TEAMS=50`일 때 cost 14면 응답이 몇 초가 되는가?

**실험 D — 타이밍 차이 측정**
존재하는 ID와 없는 ID로 각각 로그인을 시도하며 응답 시간을 측정하라.
```bash
curl -o /dev/null -s -w "%{time_total}\n" -X POST http://localhost:8000/login -d "login_id=1-1&password=wrong"
curl -o /dev/null -s -w "%{time_total}\n" -X POST http://localhost:8000/login -d "login_id=없는아이디&password=wrong"
```
차이가 보이는가? 그것이 사용자 열거 취약점이다.

**실험 E — CSRF 재현**
로컬에 HTML 파일 하나를 만들고 브라우저로 연다(파일 프로토콜 = 크로스 사이트):
```html
<form action="http://localhost:8000/teams/1/disqualify" method="POST"></form>
<script>document.forms[0].submit()</script>
```
`SameSite=Lax` 때문에 쿠키가 안 붙어 실패할 것이다.
`SessionMiddleware(..., same_site="none")`로 바꾸면 성공한다.
**직접 성공시켜 본 뒤 반드시 되돌려라.**

---

→ 다음: [04-submit.md](04-submit.md) — 500MB 파일 업로드가 서버를 죽이지 않는 이유
