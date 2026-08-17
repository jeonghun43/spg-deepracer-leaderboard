# 3단계. 인증과 은닉 — `deps.py`, `security.py`, `auth.py`, `admin_lockout.py`

> 이 단계의 목표: **"서버는 요청을 보낸 사람이 누구인지 어떻게 아는가?"** 에 밑바닥부터 답하는 것.
> **"비밀번호는 왜 되돌릴 수 없게 저장하는가?"**
> 그리고 이 프로젝트에만 있는 주제: **"관리자 페이지를 어떻게 숨기고, 왜 숨기는가?"**

---

## 0. 근본 문제 — HTTP는 기억을 못 한다

### 무엇을(What)

**[쉬움]**
HTTP는 **기억상실증 점원**이다.
손님이 "저 아까 왔던 사람이에요"라고 해도 기억을 못 한다. 요청 하나하나가 완전히 처음이다.

그래서 **번호표**를 준다. 손님이 올 때마다 번호표를 보여주면
"아, 3번 손님이시군요" 하고 알아본다. 그 번호표가 **쿠키**다.

**[전공]**
HTTP는 **stateless** 프로토콜이다. 이건 결함이 아니라 **의도된 설계**다 —
서버를 여러 대로 늘리기 쉽고, 중간에 캐시를 둘 수 있다.

하지만 "로그인"은 본질적으로 상태다. 그래서 상태를 **어딘가에 실어 보내야** 한다.

| 방법 | 어디에 | 문제 |
|---|---|---|
| URL 파라미터 | `?user=6` | 주소창에 노출, 링크 공유하면 계정 유출, 로그에 남음 |
| 요청 본문 | POST body | GET에는 못 씀 |
| **쿠키** | `Cookie:` 헤더 | 브라우저가 자동 관리. **표준 해법** |
| Authorization 헤더 | `Bearer <token>` | JS로 직접 붙여야 함. SPA/API에 적합 |

**이 프로젝트는 두 방식을 다 쓴다:**
- **사람**(팀·관리자) → 쿠키 세션. 서버 렌더링이라 자연스럽다
- **기계**(워커) → 커스텀 헤더 `X-Worker-Token`. 브라우저가 아니므로 쿠키가 무의미하다

### 어떻게(How) — 쿠키의 실제 동작

```
1) 로그인 성공 시 서버 응답:
   HTTP/1.1 303 See Other
   set-cookie: session=eyJhZG1pbl9pZCI6MX0.aBcD.EfGh; path=/; httponly; samesite=lax; secure
   location: /admin

2) 이후 브라우저는 같은 도메인의 모든 요청에 자동으로 붙인다:
   GET /admin HTTP/1.1
   Cookie: session=eyJhZG1pbl9pZCI6MX0.aBcD.EfGh
```

**브라우저가 자동으로 붙인다** — 이 점이 편리하면서 동시에 **CSRF 취약점의 근원**이다(§9).

| 속성 | 의미 | 우리 설정 |
|---|---|---|
| `httponly` | JS(`document.cookie`)로 못 읽음 → XSS로 세션 탈취 방지 | Starlette 기본 ON |
| `samesite=lax` | 다른 사이트에서 온 요청엔 쿠키를 안 보냄(GET 최상위 이동 제외) | Starlette 기본 |
| `secure` | HTTPS에서만 전송 | `session_https_only` (prod 기본 `true`) |
| `max-age` | 만료 시각 | Starlette 기본 14일 |

---

## 1. 세션을 어디에 저장할 것인가 — 3가지 방식

**방식 A. 서버 저장 세션 (전통적)**
```
쿠키:  session_id=abc123        (의미 없는 랜덤 문자열)
서버:  Redis/DB에 {abc123: {"team_id": 6}} 저장
```
- 장점: 서버가 세션을 **즉시 무효화**할 수 있다 (강제 로그아웃)
- 단점: **저장소가 하나 더 필요**하다

**방식 B. 서명된 쿠키 (이 프로젝트)**
```
쿠키:  session=<base64(JSON)>.<timestamp>.<HMAC 서명>
서버:  아무것도 저장 안 함
```
- 장점: **서버가 무상태**. 저장소 불필요
- 단점: 개별 세션을 무효화할 수 없다 (전체 무효화만 가능), 쿠키 크기 4KB 제한

**방식 C. JWT** — 방식 B와 원리는 같다(서명된 토큰). SPA/모바일/마이크로서비스에 적합.

### 왜 이 프로젝트는 B인가

**[쉬움]** Redis를 하나 더 띄우면 관리할 프로그램이 하나 더 늘어난다. 사용자가 10팀뿐인데.

**[전공]**
- 운영 부담 최소화 (`docker-compose.prod.yml`에 db·web·caddy 셋뿐인 이유)
- 세션에 담는 것이 정수 하나 — 크기 문제 없음
- **강제 로그아웃 요구사항이 없다.** 시즌이 끝나면 계정 자체를 삭제한다
- 서버 메모리가 900MB로 제한되어 있다(`mem_limit: 900m`) — Redis를 얹을 여유가 크지 않다

**적절한 판단이다.** 다만 한계를 알고 있어야 한다:
> "특정 관리자를 즉시 로그아웃시켜라"는 요구가 오면 이 구조로는 못 한다.
> 우회책: `SESSION_SECRET`을 바꾸고 재시작(**전원 로그아웃**), 또는 `admin_accounts` 행 삭제.
> 후자가 가능한 이유는 §2 마지막을 보라.

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
HMAC은 **키 없이는 올바른 서명을 만들 수 없다.**

**왜 상수 시간 비교인가?**
일반 `==`는 첫 글자가 다르면 즉시 False를 반환한다.
공격자가 응답 시간을 재면서 한 글자씩 맞춰가면 서명을 알아낼 수 있다(**타이밍 공격**).
`compare_digest`는 항상 전체를 비교해 시간이 일정하다.

**이 함수를 우리 코드에서도 직접 쓴다:**
```python
# app/routers/internal.py:36
if not secrets.compare_digest(x_worker_token, expected):
    raise NOT_FOUND
```
**워커 토큰 검증도 같은 이유로 상수 시간 비교다.** §8에서 자세히.

### 중요: 서명 ≠ 암호화

```python
import base64, json
raw = "eyJhZG1pbl9pZCI6MX0"        # 쿠키의 첫 조각
print(json.loads(base64.urlsafe_b64decode(raw + "==")))
# → {'admin_id': 1}
```

**누구나 읽을 수 있다.** 그래서:

> **세션에는 절대 넣으면 안 되는 것**: 비밀번호, 비밀번호 해시, 주민번호, 내부 시스템 비밀.

**이 프로젝트가 잘한 점**: 세션에 정수 하나만 넣는다.
```python
request.session["team_id"] = account.team_id      # auth.py:34
request.session["admin_id"] = admin.id            # admin.py:111
```
권한 정보를 세션에 넣지 않고, **매 요청마다 DB에서 다시 조회**한다:
```python
# app/deps.py:35-42
admin_id = request.session.get("admin_id")
admin = db.get(AdminAccount, admin_id)     # ← 진짜 관리자인지 DB로 확인
```

**만약 `session["is_admin"] = True` 로 했다면?**
값 자체는 위조 못 하지만, **관리자 세션 쿠키를 훔치면** 그대로 관리자가 된다.
지금 구조에서도 쿠키 탈취는 위험하지만, 최소한 **`admin_accounts`에서 행을 지우면 즉시 차단**된다.
→ **이것이 §1에서 말한 "부분적 강제 로그아웃 수단"이다.**

### 단, 세션 값 하나가 화면에 직접 쓰인다

```jinja
{# app/templates/base.html:17 #}
{% if request.session.get('admin_id') %}<a href="/admin">관리자</a>{% endif %}
```

**여기는 DB 확인을 안 한다.** 세션에 `admin_id`가 있기만 하면 링크가 보인다.

**괜찮은가?** 괜찮다:
- 링크가 보이는 것뿐이고, 실제 `/admin` 접근은 `get_current_admin`이 DB로 검증한다
- 매 페이지마다 DB를 한 번 더 치는 비용을 아낀다

> **[전공] 권한 표시(display)와 권한 강제(enforcement)를 분리한 예다.**
> 표시는 틀려도 UI가 조금 이상할 뿐이지만, 강제는 틀리면 보안 사고다.
> **강제는 항상 서버에서, 진짜 데이터로.**

---

## 3. `SESSION_SECRET` — 왜 바뀌면 전원 로그아웃되는가

### 답

서명은 `SESSION_SECRET`으로 만든다. 비밀이 바뀌면 **기존 쿠키의 서명 재계산 값이 달라진다**
→ 검증 실패 → 세션 = 빈 dict → 로그인 화면.

**[쉬움]** 도장을 새 걸로 바꾸면, 옛날 도장이 찍힌 편지는 전부 "가짜"가 된다.

### 기본값이 위험한 이유

```python
session_secret: str = "change-me-in-production"
```

이 기본값 그대로 배포하면 **공격자가 같은 키로 `{"admin_id": 1}` 쿠키를 위조할 수 있다.**
→ 관리자 계정 완전 탈취. **은닉이고 잠금이고 전부 무의미해진다.**

### 어떻게 막고 있는가 — 3중 방어

**(1) `.env.example`이 생성 명령을 알려준다**
```bash
# 세션 쿠키 서명 키. 반드시 아래 명령으로 무작위 값을 생성해서 넣는다.
#   python -c "import secrets; print(secrets.token_hex(32))"
# 이 값이 바뀌면 접속 중인 관리자·참가자가 전부 로그아웃된다.
SESSION_SECRET=
```

**(2) `docker-compose.prod.yml`이 필수로 강제한다**
```yaml
SESSION_SECRET: ${SESSION_SECRET:?SESSION_SECRET을 .env에 설정하세요}
```
`${VAR:?메시지}` — **값이 없으면 compose가 에러를 내고 컨테이너가 아예 안 뜬다.**

**(3) 개발용 `docker-compose.yml`은 기본값을 허용한다**
```yaml
SESSION_SECRET: ${SESSION_SECRET:-change-me-in-production}
```
`:-`(기본값)과 `:?`(필수)의 차이가 곧 **개발과 운영의 차이**다.

> **[전공] 1단계 §3-5에서 본 `ADMIN_LOGIN_PATH`와 정확히 같은 패턴이다.**
> "운영에서 절대 일어나면 안 되는 것"은 **배포 설정 층**이 막는다.
> 애플리케이션 코드에 `if secret == "change-me": raise` 를 넣는 방법도 있지만,
> 그러면 로컬 개발이 불편해진다. **층을 나누는 것이 답이다.**

### 운영상의 함의

**대회 중에 `SESSION_SECRET`을 바꾸면 참가자 전원이 갑자기 로그아웃된다.**
→ 대회 시작 **전에** 확정해야 한다.

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

`secrets`는 운영체제의 진짜 무작위를 쓴다.

**[전공]**
- `random`: Mersenne Twister. **624개의 출력만 관찰하면 내부 상태를 완전히 복원**할 수 있다.
  **암호학적으로 완전히 무용하다.**
- `secrets`: `os.urandom()` → `/dev/urandom`(Linux) / `BCryptGenRandom`(Windows) → CSPRNG

**공격 시나리오**: 관리자가 팀 10개를 연속 등록한다.
`random`이었다면, 공격자가 자기 팀 비밀번호 하나를 알 때
**다른 9팀의 비밀번호를 계산해낼 수 있다.**

→ **비밀번호, 토큰, 세션 ID, 리셋 링크, 그리고 `WORKER_TOKEN`에는 무조건 `secrets`.**

**엔트로피 계산**:
```
알파벳 62자, 길이 10 → 62^10 ≈ 8.4 × 10^17 → log2 ≈ 59.5비트
```
59비트는 온라인 무차별 대입에 충분히 안전하다(초당 1000회여도 수천만 년).

**특수문자를 뺀 이유**: 관리자가 화면에서 읽어 참가자에게 전달하는 방식이라,
특수문자보다 전달 오류가 적다.

### 4-2. bcrypt — 왜 "암호화"가 아니라 "해시"인가

**[쉬움]**
- **암호화**: 자물쇠. 열쇠가 있으면 되돌릴 수 있다 → 열쇠가 유출되면 끝
- **해시**: 믹서기. 갈아버리면 되돌릴 수 없다 → 유출돼도 원본을 모른다

로그인할 때는 입력한 비밀번호를 **똑같이 갈아서** 저장된 결과와 비교한다.

**[전공]**
비밀번호 저장에 요구되는 성질:
1. **단방향성**: 해시에서 원문 복원 불가
2. **솔트(salt)**: 같은 비밀번호라도 매번 다른 해시 → **레인보우 테이블 무력화**
3. **느림(work factor)**: 의도적으로 느리게 → 무차별 대입 억제

**SHA-256으로는 왜 안 되나?**
SHA-256은 **빠르게 설계됐다.** GPU로 초당 수십억 번 계산한다.
bcrypt/scrypt/argon2는 **일부러 느리게** 설계됐다.

**bcrypt 해시의 구조:**
```
$2b$12$LongSaltAndHashStringHere.....
 │  │  │
 │  │  └─ 솔트(22자) + 해시(31자)  ← 솔트가 해시 안에 같이 들어있다
 │  └──── cost factor 12 → 2^12 = 4096번 반복
 └─────── 알고리즘 버전 (2b)
```

**솔트가 해시에 포함되어 있다** — 그래서 `Account` 테이블에 `salt` 컬럼이 없다.

**cost factor 12 → 대략 200~300ms.**

이게 `admin.py`에 언급된 그 비용이다:
```python
# 한 번에 등록할 수 있는 팀 수 상한. 실수로 큰 목록을 붙여넣는 것을 막기 위한 값이며,
# 비밀번호 해시(bcrypt)가 팀당 수백 ms라 이 정도가 응답 시간 측면에서도 상한이다.
MAX_BULK_TEAMS = 50
```
50팀 × 250ms = **12.5초**. 정확한 계산이다.

> **그리고 이 비용이 §7의 잠금 설계에도 직결된다.**
> bcrypt가 느리다는 것은 **로그인 시도 자체가 서버 CPU를 잡아먹는다**는 뜻이다.
> 그래서 잠긴 상태에서는 **비밀번호를 아예 검사하지 않는다.**

> **[전공] 왜 느린 게 좋은가**: 로그인 1회에 250ms는 사용자가 못 느낀다.
> 하지만 해시 DB가 유출됐을 때 1억 개 후보를 시도하려면 250ms × 1억 = **약 800년**.
> 이 비대칭이 bcrypt의 전부다.

> **현대의 권장**: Argon2id가 더 낫다(메모리 하드 — GPU/ASIC 저항성).
> 하지만 bcrypt는 여전히 충분히 안전하고 라이브러리가 성숙하다.

### 4-3. `verify_password`의 `try/except ValueError`

DB의 `password_hash`가 bcrypt 형식이 아니면(빈 문자열, 손상) `checkpw`가 `ValueError`를 던진다.
안 잡으면 **로그인 시도가 500 에러**가 된다. 잡으면 "비밀번호가 틀렸습니다"로 정상 처리.

**[전공] 방어적 프로그래밍의 좋은 예다.**
"데이터가 깨졌을 때 서비스 전체가 아니라 그 요청만 실패하게 한다."
단, `except ValueError`만 잡는 것도 의도적이다. `TypeError`(코딩 실수)는 그대로 터진다.

---

## 5. `routers/auth.py` — 팀 로그인 흐름 정독

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

`...`(Ellipsis)는 pydantic에서 **"필수"** 를 뜻하는 관례다.
`Form`을 쓰려면 `python-multipart` 패키지가 필요하다 — `requirements.txt`에 있다.

### 5-2. `scalar_one_or_none()` — 2.0 스타일 결과 추출

| 메서드 | 0건 | 1건 | 2건 이상 |
|---|---|---|---|
| `scalar_one()` | **예외** | 값 | **예외** |
| `scalar_one_or_none()` | `None` | 값 | **예외** |
| `scalars().first()` | `None` | 값 | 첫 번째 |

로그인 ID는 unique이므로 2건이 나올 수 없다. **만약 2건이 나오면 예외가 터지는데, 그게 옳다.**

### 5-3. **`or`의 단락 평가가 만드는 미묘한 취약점**

```python
if account is None or not verify_password(password, account.password_hash):
```

`account is None`이 참이면 **`verify_password`를 호출하지 않는다**(단락 평가).
→ 존재하지 않는 ID로 로그인하면 bcrypt 계산(250ms)이 생략되어 **응답이 훨씬 빠르다.**

**[전공] 사용자 열거(user enumeration) 타이밍 공격**

우리 팀 로그인 ID는 `f"{season.id}-{team.id}"` (`admin.py:297`)라
`1-1`, `1-2`, … 로 **어차피 추측 가능**하다. 심각도는 낮다.

**하지만 관리자 로그인은 다르다.** 관리자 아이디는 추측하기 어렵고,
알아내면 무차별 대입의 출발점이 된다.

```python
# app/routers/admin.py:100
if admin is None or not verify_password(password, admin.password_hash):
    admin_lockout.record_failure(keys)
```
**여기도 같은 단락 평가가 있다.** 다만 §7의 잠금이 시도 횟수를 5회로 제한해
타이밍 표본을 충분히 모으기 어렵게 만든다 — **완전한 방어는 아니지만 실질적 완화다.**

**제대로 막으려면** 더미 해시로 항상 같은 시간을 쓴다:
```python
_DUMMY_HASH = bcrypt.hashpw(b"dummy", bcrypt.gensalt()).decode()
password_ok = verify_password(password, admin.password_hash if admin else _DUMMY_HASH)
```

**에러 메시지는 잘 되어 있다**: "아이디 또는 비밀번호가 올바르지 않습니다."
어느 쪽이 틀렸는지 알려주지 않는다.

### 5-4. **`request.session.clear()` — 세션 혼재 방어**

```python
request.session.clear()          # ← 이 줄이 왜 있는가
request.session["team_id"] = account.team_id
```

**[전공]**
서명 쿠키 방식에서는 전통적 "세션 고정(session fixation)" 공격이 직접 성립하지 않는다.
그렇다면 `clear()`는 왜 필요한가?

**관리자 세션과 팀 세션의 혼재 방지.**
관리자로 로그인한 상태에서 팀 로그인을 하면 `{"admin_id":1, "team_id":6}`이 된다.
→ 팀 계정으로 로그인했는데 관리자 화면도 열리고, **네비게이션에 "관리자" 링크가 뜬다.**

`base.html`이 `request.session.get('admin_id')` 만 보고 링크를 표시하므로
**혼재가 화면에 그대로 드러난다.** `clear()`가 이걸 막는다.

`admin.py`의 로그인도 똑같이 `clear()`를 부른다(`admin.py:110`). **양쪽 다 있어야 한다.**

### 5-5. 로그아웃의 목적지가 서로 다르다 — 의도적인 차이

```python
# app/routers/auth.py:38-41  (팀)
@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)

# app/routers/admin.py:115-120  (관리자)
@router.get("/logout")
def admin_logout(request: Request):
    # 로그아웃 후 비밀 경로로 되돌리지 않는다. 관리자가 자리를 뜬 화면에 진입점이
    # 남지 않도록, 아무나 봐도 되는 공개 화면으로 보낸다.
    request.session.clear()
    return RedirectResponse("/leaderboard", status_code=303)
```

**[쉬움]**
팀은 로그아웃하면 로그인 화면으로 돌아간다. 다시 들어오기 편하니까.
관리자는 **리더보드로** 보낸다. 왜냐하면 로그인 화면 주소가 **비밀**이기 때문이다.
로그아웃 후 그 화면이 떠 있으면, **자리를 비운 사이 지나가는 사람이 비밀 주소를 본다.**

**[전공]** 은닉을 유지하려면 **비밀 정보가 화면·히스토리·주소창에 남지 않아야** 한다.
`/leaderboard`로 보내면 브라우저 주소창에도 비밀 경로가 안 남는다.

> **다만 브라우저 히스토리에는 남는다.** 로그인할 때 방문했으므로.
> 완전한 해결은 아니고, **"자리를 뜬 직후"라는 가장 흔한 상황을 막는 것**이다.

### 5-6. 로그아웃이 `GET`인 문제

**[전공] 엄밀히 말해 HTTP 명세 위반이다.**
GET은 **안전(safe)** 해야 한다 — 서버 상태를 바꾸면 안 된다. 로그아웃은 세션을 삭제한다.

**실제 문제**:
- 브라우저 프리페치나 링크 프리뷰가 `/logout`을 긁으면 **의도치 않게 로그아웃**된다
- `<img src="https://site/admin/logout">` 을 심으면 강제 로그아웃 (경미한 CSRF)

**그럼에도 GET인 이유**: 템플릿에서 `<a href="/logout">` 한 줄로 끝나서 편하다.
피해가 "로그아웃됨"뿐이라 실무에서도 흔히 타협하는 지점이다.
**알고 타협하는 것과 모르는 것은 다르다.**

---

## 6. **관리자 진입점 은닉 — 이 프로젝트의 특징적 설계**

여기서부터가 이 프로젝트만의 주제다. 배경은 `specs/001-online-virtual-evaluation/admin-access-hardening.md`.

### 왜(Why) — 무엇을 막으려는 것인가

**[쉬움]**
가게 뒷문에 "직원 전용"이라고 써 붙이면, **도둑이 어디를 노려야 할지 알려주는 셈**이다.
아예 문이 있는지 모르게 하면 두드릴 수조차 없다.

**[전공]**
공개 인터넷에 노출된 서비스의 `/admin`, `/admin/login`, `/wp-admin`, `/phpmyadmin` 은
**자동 스캐너가 하루 수백~수천 번 두드린다.** 봇넷이 IP 대역을 통째로 훑는다.

이건 우리를 노린 공격이 아니라 **배경 소음**이다. 그런데 그 소음이:
- 로그를 오염시켜 진짜 공격을 못 보게 한다
- bcrypt를 돌리게 만들어 CPU를 먹는다 (`mem_limit: 900m`인 작은 서버다)
- 비밀번호가 약하면 언젠가 뚫린다

**은닉은 이 대량 자동화를 통째로 없앤다.**

### **은닉은 방어가 아니다 — 이걸 반드시 이해할 것**

> "Security through obscurity"(숨김으로 지키기)는 **단독 방어책으로는 실패한다.**
> 주소는 언젠가 새어 나간다: 링크 공유, 브라우저 히스토리, 프록시 로그, 어깨너머, 실수한 스크린샷.

**그래서 이 프로젝트는 은닉을 "1차 필터"로만 쓰고, 2차 방어선을 따로 둔다:**

```
[1차] 은닉        — 자동화된 대량 시도를 없앤다      ← 이 절 (§6)
[2차] 잠금        — 주소를 알아낸 표적 공격을 막는다  ← 다음 절 (§7)
[3차] 강한 비밀번호 — 잠금을 우회해도 못 뚫는다
```

**[전공] 이것이 심층 방어(defense in depth)다.**
어느 한 층도 완전하지 않다는 것을 인정하고 **여러 층을 쌓는다.**
은닉을 비판하는 말은 "은닉만 쓰지 마라"이지 "은닉을 쓰지 마라"가 아니다.

### 어떻게(How) — 4가지 장치

#### (1) 로그인 폼이 `.env`가 정하는 경로에만 있다

```python
# app/routers/admin.py:26-30
router = APIRouter(prefix="/admin", tags=["admin"])
# 로그인 폼은 /admin 아래가 아니라 .env로 지정한 비밀 경로에 붙는다 (파일 끝에서 등록).
# /admin/*를 통째로 감출 때 예외를 파지 않아도 되고, 나중에 프록시에서 /admin/*를
# 차단하는 선택지도 열어두기 위해서다.
login_router = APIRouter(tags=["admin"])
```

파일 끝에서 `add_api_route`로 등록한다(1단계 §2-5).
**`/admin/login`은 더 이상 존재하지 않는다.**

```python
def test_옛_로그인_경로는_사라졌다():
    assert client.get("/admin/login").status_code == 404
    assert client.post("/admin/login", data={...}).status_code == 404
```

#### (2) **미인증 `/admin/*`가 리다이렉트가 아니라 404다**

```python
# app/deps.py:26-42
def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminAccount:
    """미인증이면 404를 돌려준다 — 리다이렉트가 아니다 (admin-access-hardening.md §3.2).

    로그인 페이지로 보내주면 "이 경로에 관리자 페이지가 있다"는 사실이 확인된다.
    404를 주면 존재하지 않는 아무 경로와 응답이 구별되지 않아 은닉이 성립한다.

    **부작용은 의도된 것이다**: 세션이 만료된 관리자도 /admin에서 404를 보게 된다.
    관리자는 .env에 설정한 비밀 경로로 다시 들어와야 한다.
    """
    admin_id = request.session.get("admin_id")
    if not admin_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    admin = db.get(AdminAccount, admin_id)
    if admin is None:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return admin
```

**[쉬움]**
누가 "직원실 어디예요?"라고 물으면 **"그런 거 없는데요"** 라고 답하는 것.
"저기 있는데 출입증 있어야 해요"라고 하면 **직원실이 있다는 걸 알려준 것**이다.

**[전공] 이것이 핵심 아이디어다.**

| 응답 | 공격자가 알게 되는 것 |
|---|---|
| `303 → /admin/login` | **"여기 관리자 페이지가 있다"** + 로그인 주소까지 |
| `401 Unauthorized` | "여기 뭔가 있는데 인증이 필요하다" |
| `403 Forbidden` | "여기 뭔가 있는데 권한이 없다" |
| **`404 Not Found`** | **아무것도.** 없는 경로와 구별 불가 |

테스트가 이 등가성을 못 박는다:
```python
def test_없는_경로와_응답이_구별되지_않는다():
    관리자 = client.get("/admin", follow_redirects=False)
    아무거나 = client.get("/존재하지-않는-경로", follow_redirects=False)
    assert 관리자.status_code == 아무거나.status_code == 404
    assert 관리자.json() == 아무거나.json()      # ← 본문까지 같아야 한다
```

**`.json()`까지 비교하는 것이 중요하다.** 상태 코드만 같고 본문이 다르면
(`{"detail":"Not Found"}` vs `{"detail":"Unauthorized"}`) 여전히 구별된다.

**팀 로그인과 정반대 선택이라는 점을 주목하라:**
```python
# get_current_team — 303으로 /login 리다이렉트
raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})

# get_current_admin — 404
raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
```

**왜 다른가?**
- 팀 로그인 화면(`/login`)은 **공개다.** 숨길 이유가 없고, 참가자가 편해야 한다
- 관리자 로그인은 **비밀이다.** 편의보다 은닉이 우선

**같은 문제(미인증)에 다른 답을 준 것이 설계다.**

#### (3) 네비게이션에서 링크를 감춘다

```jinja
{# app/templates/base.html:15-17 #}
{# 관리자로 로그인한 세션에서만 보인다. 참가자·관전자에게는 진입점 자체를 노출하지 않는다
   (admin-access-hardening.md §3.5). 관리자는 로그인 후 비밀 경로를 다시 칠 필요가 없다. #}
{% if request.session.get('admin_id') %}<a href="/admin">관리자</a>{% endif %}
```

**두 가지를 동시에 한다:**
1. 참가자·관전자에게는 `/admin`이라는 경로의 존재조차 안 보인다
2. **관리자에게는 편의를 준다** — 한 번 로그인하면 비밀 주소를 다시 칠 필요 없이 메뉴로 이동

테스트 3개가 이 조건을 고정한다:
```python
def test_비로그인_방문자에게는_관리자_링크가_안_보인다()
def test_참가팀으로_로그인해도_관리자_링크는_안_보인다()
def test_관리자로_로그인하면_관리자_링크가_보인다()
```

**테스트 방법이 영리하다** — DB 없이 템플릿만 렌더한다:
```python
class _가짜요청:
    """base.html이 쓰는 request.session만 흉내 낸다."""
    def __init__(self, session: dict):
        self.session = session

def _네비게이션(session: dict) -> str:
    from app.render import templates
    return templates.get_template("base.html").render(request=_가짜요청(session))
```
**템플릿이 `request`에서 쓰는 것이 `session` 하나뿐이므로, 그것만 있는 가짜 객체면 충분하다.**
이게 **덕 타이핑(duck typing)** 의 실용적 활용이다.

#### (4) OpenAPI 문서에서 제외

```python
login_router.add_api_route(
    settings.admin_login_path, admin_login_form, methods=["GET"], include_in_schema=False
)
```
안 하면 OpenAPI 스키마에 비밀 주소가 실려 나간다. 1단계 §2-5 참고.

> 2026-08-06부터 `/docs`·`/redoc`·`/openapi.json` 엔드포인트 자체를 껐다(1단계 §2-1).
> 그래도 이 줄은 지운 게 아니다 — **스키마 생성은 계속 일어나고**(`app.openapi()`),
> 문서를 다시 켜는 순간 그대로 노출되기 때문이다. 두 겹을 따로 지킨다.

### 은닉의 대가 — 알고 있어야 할 불편

| 대가 | 설명 |
|---|---|
| 세션 만료 시 404 | 관리자가 `.env`의 주소를 **다시 찾아 쳐야** 한다 |
| 주소 분실 | `.env`를 잃으면 관리자 로그인 자체가 불가능 (서버 접속해서 확인해야) |
| 주소 변경 = 재시작 | 라우트가 import 시점에 등록되므로 |
| 북마크 위험 | 관리자가 비밀 주소를 북마크하면 그 브라우저가 곧 열쇠 |

**deps.py의 docstring이 이 대가를 명시적으로 인정한다:**
> **부작용은 의도된 것이다**: 세션이 만료된 관리자도 /admin에서 404를 보게 된다.

> **[전공] 좋은 설계 문서의 조건이다.** 장점만 적으면 나중에 그 대가를 만났을 때
> "버그인가?" 하고 혼란스럽다. **"이건 의도한 불편이다"라고 적어두면 판단이 빨라진다.**

---

## 7. **`admin_lockout.py` — 2차 방어선, 로그인 잠금**

```python
"""관리자 로그인 무차별 대입 방어 (admin-access-hardening.md §3.4).

**왜 IP만 세지 않는가.** 우리 웹은 Caddy 뒤에 있어 클라이언트 IP를 `X-Forwarded-For`
헤더에서 읽는데, 이 헤더는 요청자가 임의로 넣을 수 있다. 헤더를 바꿔가며 IP 잠금을
우회하더라도 로그인 아이디 기준 카운터에 걸리게 해, 계정 쪽이 실질적인 방어선이 된다.

**왜 프로세스 메모리인가.** 운영 웹은 컨테이너 1개·uvicorn 워커 1개로 돌아
(`Dockerfile`의 CMD에 `--workers`가 없다) 공유 저장소가 필요 없다. 재시작하면
카운터가 초기화되지만 공격자가 우리 컨테이너를 재시작시킬 수단이 없으므로 감수할 수 있고,
오히려 관리자가 자기 계정을 잠갔을 때 `docker compose restart web`이 탈출구가 된다.
"""
```

**이 docstring 하나에 설계 결정 3개의 근거가 다 들어있다.** 하나씩 뜯어보자.

### 7-1. 무엇을(What) — 자료구조

```python
@dataclass
class _Entry:
    failures: int = 0
    last_failure_at: float = 0.0
    # 0이면 잠금 없음. time.monotonic() 기준이라 시스템 시계를 바꿔도 영향받지 않는다.
    locked_until: float = 0.0


_entries: dict[str, _Entry] = {}
```

**[쉬움]**
칠판에 "이 사람 몇 번 틀렸는지" 를 적어둔다. 5번 틀리면 15분간 문을 잠근다.

**[전공]**
모듈 전역 dict. 키는 문자열, 값은 카운터+잠금시각.

### 7-2. **`time.monotonic()` — 왜 `time.time()`이 아닌가**

```python
locked_until: float = 0.0
# 0이면 잠금 없음. time.monotonic() 기준이라 시스템 시계를 바꿔도 영향받지 않는다.
```

| | `time.time()` | `time.monotonic()` |
|---|---|---|
| 기준 | 1970-01-01 (벽시계) | 임의 시점 (프로세스/부팅 기준) |
| NTP 동기화 | **값이 갑자기 점프한다** | 영향 없음 |
| 시계 수동 변경 | **뒤로 갈 수 있다** | 절대 감소하지 않음 |
| 용도 | "언제인가" | **"얼마나 지났는가"** |

**공격 시나리오**: `time.time()`을 쓰면, 서버 시계가 NTP로 15분 앞당겨지는 순간
**모든 잠금이 한꺼번에 풀린다.**

**[전공] 철칙: 경과 시간(duration)을 재는 데는 항상 monotonic 시계를 쓴다.**
타임아웃, 재시도 백오프, 레이트 리밋, 성능 측정 전부.

> **참고**: `worker/run.py`도 하트비트 주기에 `time.sleep()`을 쓰지만,
> DB에 저장되는 `last_seen_at`은 **벽시계(UTC)** 다.
> 이건 맞다 — 저장하고 다른 프로세스가 읽어야 하므로 monotonic은 쓸 수 없다
> (프로세스마다 기준점이 다르다). **"저장·공유는 벽시계, 경과 측정은 monotonic."**

### 7-3. **이중 카운터 — 이 모듈의 핵심**

```python
def build_keys(ip: str, login_id: str) -> list[str]:
    """IP 기준과 아이디 기준 두 개의 카운터 키를 만든다."""
    return [f"ip:{ip}", f"id:{login_id.strip().lower()}"]
```

**[쉬움]**
"이 컴퓨터에서 5번 틀림"과 "이 아이디로 5번 틀림"을 **둘 다** 센다.
컴퓨터를 바꿔가며 시도해도 아이디 쪽 카운터에 걸린다.

**[전공] 왜 IP만으로는 부족한가**

우리 앱은 **Caddy 뒤에** 있다. uvicorn이 보는 클라이언트 IP는 항상 Caddy의 IP다.
진짜 IP는 `X-Forwarded-For` 헤더에 있다:

```python
# app/admin_lockout.py
def client_ip(request: Request) -> str:
    """Caddy 뒤에 있으므로 X-Forwarded-For의 첫 값이 실제 접속자다.

    이 헤더는 위조할 수 있다 — 그래서 IP 잠금만 믿지 않고 아이디 기준 잠금을 함께 쓴다.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
```

> 2026-08-10에 `admin.py`의 `_client_ip`에서 `admin_lockout.client_ip`로 옮겼다.
> 참가자 로그인(§9-2)도 같은 함수가 필요해졌기 때문이다 — 라우터에 있던 헬퍼를
> 두 라우터가 쓰게 되면 공용 모듈이 있어야 할 자리다.

**문제**: `X-Forwarded-For`는 **클라이언트가 직접 넣을 수 있는 헤더다.**

```bash
curl -H "X-Forwarded-For: 1.2.3.4" https://site/_ops/secret -d "login_id=admin&password=x"
curl -H "X-Forwarded-For: 1.2.3.5" ...   # 매번 다른 IP인 척
```

Caddy가 이 헤더를 **덮어쓰는지 이어붙이는지**에 따라 다르다.
일반적으로 리버스 프록시는 기존 값 뒤에 자기가 본 IP를 **append**한다.
그러면 `split(",")[0]` 은 **클라이언트가 넣은 가짜 값**을 집는다.

> **[전공] 정석 해법**: 신뢰할 수 있는 프록시 개수를 알고 **뒤에서부터** 세거나,
> Caddy가 `X-Forwarded-For`를 **덮어쓰도록** 설정한다.
> `Caddyfile`을 확인해볼 가치가 있다. 그리고 uvicorn에 `--proxy-headers`를 주면
> `request.client.host`가 알아서 처리되기도 한다(현재 `Dockerfile`에는 없다).

**이 프로젝트의 답: "IP를 완전히 신뢰할 수 없으니, 신뢰할 수 있는 축을 하나 더 둔다."**

`login_id`는 **요청 본문**에 들어있고, 로그인하려면 **반드시 실제 아이디여야** 한다.
공격자가 아이디를 바꿔가며 시도할 수는 있지만, 그러면 **표적 계정을 못 뚫는다.**

**테스트가 이 핵심을 정확히 짚는다:**
```python
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
```

**반대 방향도 테스트한다:**
```python
def test_다른_아이디는_같은_IP라도_따로_센다():
    """한 아이디가 잠겼다고 다른 아이디까지 잠기면 안 된다 (IP 기준은 별개로 동작)."""
```
→ 관리자가 여럿일 때 한 명의 실수가 다른 사람을 막으면 안 된다.

**정규화도 잊지 않았다:**
```python
return [f"ip:{ip}", f"id:{login_id.strip().lower()}"]
                                ^^^^^^^^^^^^^^^^^^
```
```python
def test_아이디_대소문자와_공백은_같은_카운터로_본다():
    """'Admin '과 'admin'을 다르게 세면 카운터를 우회할 수 있다."""
```

**[전공] 이게 없으면 공격자가 `admin`, `Admin`, `ADMIN`, `admin `(공백) …
로 카운터를 무한히 나눠 우회할 수 있다.** 아주 흔한 실수다.

### 7-4. 잠금 판정 — "어느 한 키라도 잠겼으면"

```python
def seconds_remaining(keys: list[str], now: float | None = None) -> int:
    """잠겨 있으면 남은 초, 아니면 0. 어느 한 키라도 잠겨 있으면 잠긴 것으로 본다."""
    current = _now(now)
    remaining = 0.0
    for key in keys:
        entry = _entries.get(key)
        if entry is not None:
            remaining = max(remaining, entry.locked_until - current)
    return int(remaining) + 1 if remaining > 0 else 0
```

**OR 판정 + `max()`** — 가장 오래 남은 잠금을 따른다. **보수적인 쪽.**

`int(remaining) + 1` — 남은 시간을 **올림**한다.
`0.4초 남음`이 `0초`(=잠금 해제)로 보이면 안 되기 때문. **경계에서 안전한 쪽으로 반올림.**

### 7-5. 자동 정리 — 별도 스레드 없이

```python
def _prune(now: float) -> None:
    """만료된 항목을 지운다.

    항목 수가 많아야 수십 개라 별도 청소 스레드 없이 기록할 때마다 함께 정리한다.
    잠금이 풀렸거나, 마지막 실패 후 잠금 시간만큼 조용했으면 카운터를 버린다 —
    몇 달 전의 오타 한 번이 계속 남아 있을 이유가 없다.
    """
    for key, entry in list(_entries.items()):
        if max(entry.locked_until, entry.last_failure_at + _lockout_seconds()) <= now:
            del _entries[key]
```

**[쉬움]** 청소 담당을 따로 두는 대신, **누가 올 때마다 조금씩 치운다.**

**[전공] amortized cleanup 패턴.**
- 별도 스레드/스케줄러 불필요 → 복잡도 0
- `record_failure`에서만 호출된다(§7-6) → 실패가 없으면 정리도 안 되지만, 그럼 쌓일 것도 없다
- `list(_entries.items())` 로 복사하는 이유: **순회 중 dict를 수정하면 `RuntimeError`**

**"마지막 실패 후 잠금 시간만큼 조용했으면"** 이라는 조건이 중요하다.
5분 전에 1번 틀린 사람의 카운터를 계속 들고 있을 이유가 없다.
→ **오래된 실패는 잊는다.** 이게 없으면 정상 사용자가 며칠에 걸쳐 5번 오타를 내도 잠긴다.

### 7-6. **메모리 저장의 정당화 — 배포 구조가 근거다**

> **왜 프로세스 메모리인가.** 운영 웹은 컨테이너 1개·uvicorn 워커 1개로 돌아
> (`Dockerfile`의 CMD에 `--workers`가 없다) 공유 저장소가 필요 없다.

**[전공] 이 논증을 정확히 이해하라.**

만약 uvicorn 워커가 4개라면?
```
요청1 → 워커A → _entries에 실패 1회 기록
요청2 → 워커B → _entries는 별개! 실패 1회
요청3 → 워커C → 또 별개
...
```
→ **실질 시도 한도가 5회가 아니라 20회가 된다.** 잠금이 사실상 무력해진다.

**해결책은 두 가지:**
1. 워커를 1개로 유지 (현재)
2. Redis 같은 공유 저장소 사용

**1번을 택하고 그 사실을 코드 주석에 명시했다.** 이게 중요하다 —
누군가 나중에 `--workers 4`를 추가하면 **이 방어가 조용히 약해진다.**
주석이 그 위험을 경고한다.

> **개선 아이디어**: 워커 수를 코드가 확인하도록 만들 수도 있다.
> 또는 `docker-compose.prod.yml`에 주석으로 "web을 scale하지 말 것"을 남긴다.
> **암묵적 전제는 언젠가 깨진다.**

**재시작 시 초기화되는 것도 정당화한다:**
> 재시작하면 카운터가 초기화되지만 공격자가 우리 컨테이너를 재시작시킬 수단이 없으므로
> 감수할 수 있고, 오히려 관리자가 자기 계정을 잠갔을 때
> `docker compose restart web`이 탈출구가 된다.

**[전공] 단점을 기능으로 재해석한 것이다.** 그리고 실제로 맞다 —
잠금 시스템에는 **관리자용 탈출구(escape hatch)** 가 반드시 필요하다.
없으면 오타 5번으로 15분간 대회 운영이 멈춘다.

`.env.example`에도 이 탈출구가 적혀 있다:
```
# 잠긴 계정은 `docker compose restart web`으로 즉시 풀 수 있다.
```

### 7-7. 로그인 핸들러에서의 사용 — 순서가 중요하다

```python
# app/routers/admin.py:77-112
def admin_login_submit(request, login_id=Form(...), password=Form(...), db=Depends(get_db)):
    keys = admin_lockout.build_keys(admin_lockout.client_ip(request), login_id, scope="admin")

    # 잠긴 동안에는 비밀번호를 아예 검사하지 않는다 — bcrypt를 돌리지 않아야
    # 잠금이 계산 자원 소모 공격의 통로가 되지 않는다.
    remaining = admin_lockout.seconds_remaining(keys)
    if remaining:
        minutes = max(1, (remaining + 59) // 60)
        return templates.TemplateResponse(
            request, "admin/login.html",
            _login_context(request, f"로그인 시도가 너무 많습니다. 약 {minutes}분 뒤에 다시 시도하세요."),
            status_code=429,
        )

    admin = db.execute(select(AdminAccount).where(AdminAccount.login_id == login_id)).scalar_one_or_none()
    if admin is None or not verify_password(password, admin.password_hash):
        admin_lockout.record_failure(keys)
        return templates.TemplateResponse(..., status_code=401)

    admin_lockout.reset(keys)
    request.session.clear()
    request.session["admin_id"] = admin.id
    return RedirectResponse("/admin", status_code=303)
```

**순서: 잠금 확인 → DB 조회 → bcrypt → (실패 시) 카운터 증가 → (성공 시) 카운터 초기화**

**왜 잠금 확인이 맨 앞인가?**
주석이 답한다:
> 잠긴 동안에는 비밀번호를 아예 검사하지 않는다 — bcrypt를 돌리지 않아야
> 잠금이 계산 자원 소모 공격의 통로가 되지 않는다.

**[쉬움]**
문을 잠갔는데도 매번 열쇠를 맞춰보면, **문을 잠근 의미가 없다.**
잠긴 동안에는 아예 쳐다보지도 않는다.

**[전공]**
bcrypt는 요청당 **250ms의 CPU**를 쓴다. 잠긴 뒤에도 계속 검사하면:
```
공격자: 초당 100개 요청 → 100 × 250ms = 25초치 CPU/초
→ 서버 CPU 포화 → 정상 사용자도 못 들어옴 (DoS)
```
**잠금이 오히려 공격 도구가 된다.**

잠금 확인은 **dict 조회 한 번**이다. 거의 공짜.
→ 잠긴 뒤에는 공격자가 아무리 두드려도 서버가 안 힘들다.

**`status_code=429` (Too Many Requests)** — 의미에 맞는 코드다.
401(인증 실패)과 구별되므로 로그를 보면 "잠금이 실제로 동작했다"를 알 수 있다.

**`max(1, (remaining + 59) // 60)`** — 초를 분으로 올림하되 최소 1분.
"0분 뒤에 다시 시도하세요"라는 이상한 문구를 막는다.

**성공 시 `reset(keys)`** — 정상 로그인하면 카운터를 지운다.
안 지우면 **오타 4번 → 성공 → 다음날 오타 1번 → 잠김** 이 된다.

### 7-8. 테스트가 시간을 주입하는 이유

```python
def test_잠금_시간이_지나면_풀린다():
    키 = _키()
    for _ in range(settings.admin_login_max_attempts):
        admin_lockout.record_failure(키, now=0.0)
    잠금초 = settings.admin_login_lockout_minutes * 60
    assert admin_lockout.seconds_remaining(키, now=잠금초 - 1) > 0
    assert admin_lockout.seconds_remaining(키, now=잠금초 + 1) == 0
```

모든 함수가 `now: float | None = None` 파라미터를 받는다:
```python
def _now(now: float | None) -> float:
    return time.monotonic() if now is None else now
```

**[전공] 이것이 "시간 의존성 주입"이다.**
- 테스트가 **15분을 실제로 기다리지 않는다**
- 경계값(`잠금초 - 1`, `잠금초 + 1`)을 정확히 찌를 수 있다
- 운영 코드는 인자를 안 주면 실제 시계를 쓴다 → **호출부가 지저분해지지 않는다**

대안은 `unittest.mock.patch("time.monotonic")` 인데,
**인자 주입이 훨씬 명시적이고 깨지기 어렵다.**

> **일반화: 테스트하기 어려운 것(시간, 난수, 네트워크, 파일시스템)은 인자로 빼라.**
> 그러면 순수 함수가 되고, 순수 함수는 테스트가 쉽다.

**그리고 전역 상태 격리:**
```python
@pytest.fixture(autouse=True)
def _잠금_초기화():
    """잠금 카운터는 프로세스 전역이라 테스트끼리 새지 않게 매번 비운다."""
    admin_lockout.clear_all()
    yield
    admin_lockout.clear_all()
```
`autouse=True`라 모든 테스트에 자동 적용된다.
**전역 상태를 쓰는 모듈은 반드시 이런 장치가 필요하다.**

---

## 8. **워커 인증 — 사람이 아닌 클라이언트**

```python
# app/routers/internal.py:31-37
def require_worker(x_worker_token: str | None = Header(default=None)) -> None:
    expected = settings.worker_token
    # 토큰이 설정되지 않은 배포(웹·워커가 같은 기기)에서는 이 경로 자체를 열지 않는다.
    if not expected or not x_worker_token:
        raise NOT_FOUND
    if not secrets.compare_digest(x_worker_token, expected):
        raise NOT_FOUND
```

### 왜 쿠키가 아니라 헤더인가

**[쉬움]** 워커는 브라우저가 아니다. 쿠키를 저장하고 자동으로 붙이는 기능이 없다.

**[전공]**
- 쿠키는 브라우저의 메커니즘(도메인·경로·SameSite 규칙)이다. 서버 대 서버에는 부적합
- **CSRF가 원천적으로 불가능하다** — 쿠키가 아니면 브라우저가 자동으로 안 붙인다
- 표준적으로는 `Authorization: Bearer <token>` 을 쓰지만,
  커스텀 헤더(`X-Worker-Token`)도 흔하고 의도가 더 명확하다

FastAPI가 `Header(default=None)` 로 선언하면 `X-Worker-Token` 헤더를 자동으로 매핑한다
(파이썬 변수명 `x_worker_token`의 `_`를 `-`로 바꿔 찾는다).

### `secrets.compare_digest` — 여기서도 타이밍 공격 방어

```python
if not secrets.compare_digest(x_worker_token, expected):
```
`==` 로 비교하면 첫 글자부터 순차 비교라 **응답 시간으로 토큰을 한 글자씩 알아낼 수 있다.**
실제로 네트워크 지터 때문에 실용적 공격은 어렵지만, **비용이 0인 방어는 항상 한다.**

### **인증 실패에 403이 아니라 404를 주는 이유**

```python
# 인증 실패와 "없는 제출"을 같은 응답으로 돌려준다 (정보 노출 방지).
NOT_FOUND = HTTPException(status_code=404, detail="Not Found")
```

모듈 docstring:
> 실패하면 403이 아니라 404를 준다 — 어떤 제출이 존재하는지조차 알려주지 않기 위해서다.
> 사설망(Tailscale) 안에서만 접근하는 것이 정상이지만, 사설망을 유일한 방어선으로 삼지 않는다.

**§6의 관리자 은닉과 정확히 같은 논리다.**
- 403 → "이 제출은 존재하는데 권한이 없다"
- 404 → "그런 제출 없다" (또는 "그런 경로 없다")

**토큰이 유출된 상태에서도** 404는 유용하다: 존재하는 submission_id를 스캔으로 알아낼 수 없다.

**"사설망을 유일한 방어선으로 삼지 않는다"** 가 핵심 문장이다.
Tailscale이 뚫리거나 설정이 잘못돼도 토큰이 남는다. **심층 방어.**

### local 모드에서는 경로가 아예 닫힌다

```python
if not expected or not x_worker_token:
    raise NOT_FOUND
```

`WORKER_TOKEN`이 빈 문자열이면 **무조건 404**다.
→ 웹과 워커가 같은 기기인 배포에서는 `/internal/*` 가 존재하지 않는 것과 같다.

**[전공] "기능을 안 쓰면 공격면도 없다"** — 설정 하나로 엔드포인트 4개가 통째로 닫힌다.

### ⚠️ 알아둘 점 — 본문이 인증보다 먼저 읽힌다

```python
@router.post("/submissions/{submission_id}/video")
async def upload_video(
    submission_id: int,
    video: UploadFile = File(...),        # ← 본문
    _: None = Depends(require_worker),    # ← 인증
    db: Session = Depends(get_db),
):
```

**FastAPI는 핸들러를 부르기 전에 요청 본문을 파싱한다.**
즉 **토큰이 틀려도 서버는 영상 파일을 먼저 다 받는다.**

`UploadFile`이라 메모리가 아니라 디스크 임시 파일로 스풀되므로 OOM은 아니지만,
**인증 없는 요청이 디스크와 대역폭을 쓸 수 있다.**

**얼마나 위험한가?** 낮다:
- `/internal/*` 경로를 알아야 한다
- Tailscale 사설망 안에서만 접근 가능한 것이 정상 구성
- Caddy 앞단에서 `/internal/*`를 차단하면 완전히 막힌다

**제대로 막으려면** 프록시 층에서 경로를 차단하거나,
`Content-Length` 기반 사전 검사를 미들웨어로 둔다.

> **직접 확인해 볼 가치가 있다** (실험 F). "프레임워크가 언제 무엇을 하는지"를
> 몸으로 아는 계기가 된다.

---

## 9. **이 코드에 아직 빠져 있는 것**

top-down 공부의 핵심은 "있는 것"뿐 아니라 **"없는 것"을 아는 것**이다.

### 9-1. CSRF 보호가 없다 ⚠️ 남아 있음

**[쉬움]**
악당이 만든 사이트에 접속했는데, 그 사이트가 몰래
"우리 리더보드 사이트에 요청 보내기" 코드를 심어놨다.
브라우저는 쿠키를 **자동으로** 붙이므로, 내 로그인 상태로 요청이 나간다.

**[전공] 공격 시나리오**

관리자가 로그인한 상태로 악성 페이지를 연다:
```html
<form action="https://our-site/admin/teams/3/disqualify" method="POST"></form>
<script>document.forms[0].submit()</script>
```
→ **관리자 권한으로 팀이 실격 처리된다.**

**왜 지금은 그나마 괜찮은가?**
1. `SameSite=Lax`가 Starlette 기본값이다. **크로스 사이트 POST에는 쿠키가 안 붙는다.**
   현대 브라우저에서는 이것만으로 대부분의 CSRF가 막힌다
2. `/admin/*` 경로를 알아야 한다(경로 자체는 추측 가능하지만)
3. 관리자가 대회 중에 낯선 사이트를 열 가능성이 낮다

**하지만 `SameSite=Lax`는 방어의 전부가 아니다:**
- 같은 사이트(same-site) 안에 XSS나 사용자 생성 콘텐츠가 있으면 무력
- 구형 브라우저는 SameSite를 무시

**정석 방어**: CSRF 토큰
```python
token = secrets.token_urlsafe(32)
request.session["csrf"] = token
# POST 처리 시
if form_token != request.session.get("csrf"):
    raise HTTPException(403)
```

> **판단**: 상태 변경 라우트(`advance-status`, `disqualify`, `daily-count`,
> `reissue-password`, `teams/new`)는 붙일 가치가 있다.
> **은닉과 잠금까지 갖춘 시스템에서 CSRF만 비어 있는 것은 균형이 안 맞는다.**

### 9-2. ~~팀 로그인에는 잠금이 없다~~ → 해결 (2026-08-10)

**있었던 문제**: `admin_lockout`이 관리자 로그인에만 적용돼, `auth.py`의 팀 로그인은
무제한 시도가 가능했다.

**심각도 판단이 이랬다:**
- 팀 비밀번호는 10자리 랜덤 → 온라인 무차별 대입은 비현실적
- **하지만 bcrypt CPU 소모 DoS는 여전히 가능하다** (§7-7에서 본 그 문제)
- 팀 계정이 뚫려도 피해는 "그 팀 이름으로 제출" 정도

즉 **막으려는 것은 추측이 아니라 비용이다.** 운영 웹은 2코어·메모리 900MB 상한인데
bcrypt가 요청당 수백 ms를 물어, 인증 없이 초당 수십 건만 던지면 웹이 응답을 멈춘다.

**같은 모듈을 재사용해 해결했다:**
```python
# app/routers/auth.py
keys = admin_lockout.build_keys(admin_lockout.client_ip(request), login_id, scope="team")
```

두 가지를 함께 도입했다.

**① `scope`로 카운터를 분리했다.** 이게 없으면 같은 공유 네트워크(동아리방 와이파이)에서
참가자가 오타를 반복할 때 **IP 키가 겹쳐 관리자까지 잠긴다.** 대회 중에 운영자가 못
들어가는 것이 가장 곤란한 상황이라, 나누는 것이 필수였다.
(`test_참가자와_관리자_카운터는_분리된다`가 이걸 지킨다.)

**② 정책을 따로 뒀다.** 관리자 5회/15분, 참가자 **10회/5분**. 참가자는 발급받은
비밀번호를 붙여넣다 실수하기 쉽고, 잠기면 대회 중에 제출을 못 한다. 반면 막으려는 것이
추측이 아니라 CPU 소모이므로 문턱을 낮게 둘 이유가 없다.

### 9-3. ~~세션 만료 설정이 명시되지 않았다~~ → 해결 (2026-08-10)

Starlette `SessionMiddleware`의 `max_age` 기본값은 **14일**이다.
대회가 2주라면 우연히 맞지만, **의도한 값이 아니라 기본값이었다.**

**관리자 세션이 14일 유지되는 것이 특히 아쉬웠다.** 은닉으로 진입점을 막아놨는데
쿠키가 2주를 살아있으면, 그 쿠키 하나가 2주짜리 열쇠다. 공용 PC에서 로그인하면
비밀 경로를 몰라도 `/admin`에 그대로 들어가진다.

```python
# app/main.py
app.add_middleware(SessionMiddleware, ..., max_age=settings.session_max_age_seconds)
# app/config.py — session_max_age_seconds: int = 8 * 60 * 60
```

**8시간**으로 잡았다. 참가자와 관리자가 같은 미들웨어를 공유하므로 한 값을 써야 하는데,
8시간이면 참가자에게 부담이 없으면서 관리자 세션이 밤을 넘기지 않는다.
`test_세션_유효기간이_지정되어_있다`가 **다시 기본값으로 돌아가는 것**을 막는다.

### 9-4. 비밀번호 변경 기능이 없다

참가자는 관리자가 발급한 비밀번호를 계속 쓴다. 재발급만 가능.
**소규모 단기 대회에서는 합리적 생략**이다. 계정 수명이 시즌 하나뿐이므로.

### 9-5. ✅ 해결된 것 (이전 문서에서 지적했던 것)

| 이전 지적 | 현재 상태 |
|---|---|
| 로그인 rate limit 없음 | ✅ `admin_lockout.py` (관리자만) |
| `/admin/login`이 공개 | ✅ 비밀 경로로 이동 |
| 미인증 시 로그인 화면 노출 | ✅ 404 |
| 관리자 링크가 모두에게 보임 | ✅ 세션 조건부 표시 |
| `SESSION_SECRET` 기본값 배포 위험 | ✅ prod compose에서 `:?`로 필수 강제 |

---

## 10. 자가 점검 질문

**기초**
1. HTTP가 stateless인데 로그인이 유지되는 원리를 3문장으로 설명하라.
2. 세션 데이터는 서버와 브라우저 중 어디에 있는가? 읽을 수 있는가? 바꿀 수 있는가? 각각 왜?
3. HMAC 서명이 위조를 막는 원리는? 서명 비교를 상수 시간으로 하는 이유는?
4. `SESSION_SECRET`이 기본값 그대로 배포되면 무슨 일이 가능한가? 무엇이 그걸 막고 있는가?
5. `random` 대신 `secrets`를 써야 하는 이유를 공격 시나리오로 설명하라.
6. bcrypt가 SHA-256보다 나은 두 가지 이유는? 솔트는 어디에 저장되는가?
7. `MAX_BULK_TEAMS = 50`이라는 숫자가 bcrypt와 무슨 관계인가?

**세션과 권한**
8. `request.session.clear()`를 지우면 어떤 권한 누수가 화면에 드러나는가?
9. `base.html`이 세션만 보고 링크를 표시하는데 왜 안전한가? "표시"와 "강제"의 차이는?
10. 팀 로그아웃과 관리자 로그아웃의 목적지가 다른 이유는?

**은닉**
11. `/admin`이 미인증일 때 303이 아니라 404인 이유는? 401/403은 왜 안 되는가?
12. `get_current_team`은 303, `get_current_admin`은 404다. 같은 문제에 왜 다른 답인가?
13. `test_없는_경로와_응답이_구별되지_않는다`가 `.json()`까지 비교하는 이유는?
14. 은닉이 "방어가 아니다"라는 말은 무슨 뜻인가? 그럼에도 왜 하는가?
15. 은닉의 대가 4가지는? 코드가 그걸 어디서 인정하고 있는가?
16. `login_router`를 `router`와 분리한 이유를 "예외를 파지 않는다"로 설명하라.

**잠금**
17. `time.time()` 대신 `time.monotonic()`을 쓰는 이유는? 어떤 공격이 가능해지는가?
18. IP 카운터만으로 부족한 이유는? `X-Forwarded-For`가 왜 신뢰할 수 없는가?
19. `login_id.strip().lower()` 가 없으면 어떻게 우회되는가?
20. 잠긴 상태에서 bcrypt를 돌리면 왜 잠금이 오히려 공격 도구가 되는가?
21. 잠금 카운터를 프로세스 메모리에 두는 것이 정당한 근거는? 언제 그 근거가 깨지는가?
22. 로그인 성공 시 `reset(keys)`를 안 하면 어떤 사용자 불편이 생기는가?
23. 테스트가 `now` 인자를 주입하는 이유 3가지는?
24. `@pytest.fixture(autouse=True)` 로 카운터를 비우는 이유는?

**워커 인증**
25. 워커가 쿠키가 아니라 헤더를 쓰는 이유 3가지는?
26. 인증 실패에 403이 아니라 404를 주는 이유는? 관리자 은닉과 어떻게 같은 논리인가?
27. `WORKER_TOKEN`이 빈 값이면 `/internal/*`가 어떻게 되는가? 왜 그 설계가 좋은가?
28. `upload_video`에서 인증보다 본문이 먼저 읽히는 것이 왜 문제인가? 얼마나 위험한가?

**빠진 것**
29. CSRF 공격 시나리오를 우리 관리자 화면 기준으로 구체적으로 서술하라. 지금 무엇이 막고 있는가?
30. 팀 로그인에 잠금이 없는 것의 실질적 위험은?

---

## 11. 실험 과제

**실험 A — 쿠키 위조 시도**
1. 로그인 후 개발자도구에서 `session` 쿠키 값을 복사
2. 첫 조각을 디코드해 내용 확인
3. `{"admin_id": 1}`로 바꿔 base64 인코딩 후 쿠키 교체 → **404가 나온다** (서명 검증 실패)
4. 이번엔 `SESSION_SECRET`을 알고 직접 서명해보라:
```python
from itsdangerous import TimestampSigner
import base64, json
s = TimestampSigner("여기에_실제_SESSION_SECRET")
data = base64.urlsafe_b64encode(json.dumps({"admin_id":1}).encode()).rstrip(b"=")
print(s.sign(data).decode())
```
→ **이게 되는 것을 보면 `SESSION_SECRET`의 중요성이 몸으로 이해된다.**

**실험 B — 404 등가성 확인**
```bash
curl -si http://localhost:8000/admin | head -20
curl -si http://localhost:8000/아무거나없는경로 | head -20
```
상태 코드와 본문이 완전히 같은가? 헤더는? (`content-length`까지 비교해보라)

**실험 C — 잠금 직접 돌려보기**
```bash
PYTHONPATH=. .venv/bin/python -c "
from app import admin_lockout as L
from app.config import settings
k1 = L.build_keys('1.1.1.1', 'admin')
for i in range(settings.admin_login_max_attempts):
    print(i+1, '회 실패 → 남은 잠금', L.record_failure(k1, now=0.0))
k2 = L.build_keys('9.9.9.9', 'ADMIN ')   # IP도 다르고 대소문자·공백도 다름
print('다른 IP, 같은 아이디 →', L.seconds_remaining(k2, now=0.0))
k3 = L.build_keys('1.1.1.1', 'operator')
print('같은 IP, 다른 아이디 →', L.seconds_remaining(k3, now=0.0))
"
```
**세 결과가 왜 그렇게 나오는지 설명할 수 있어야 한다.**

**실험 D — 실제 잠금 걸어보기**
비밀 경로에 틀린 비밀번호로 6번 POST하고 응답 코드를 관찰하라.
```bash
for i in $(seq 1 6); do
  curl -s -o /dev/null -w "%{http_code} " -X POST http://localhost:8000$ADMIN_PATH \
    -d "login_id=admin&password=wrong"
done; echo
```
`401 401 401 401 401 429` 가 나오는가?
그다음 `docker compose restart web` 하고 다시 시도하면 풀리는가?

**실험 E — bcrypt 비용 측정 + 잠금의 가치**
```python
import time, bcrypt
t = time.perf_counter()
bcrypt.hashpw(b"password", bcrypt.gensalt())
print(f"{(time.perf_counter()-t)*1000:.0f}ms")
```
초당 100 요청이 들어오면 CPU가 몇 배로 필요한가?
잠금이 그걸 어떻게 막는지 §7-7을 다시 읽어라.

**실험 F — 인증보다 본문이 먼저 읽히는지 확인**
```bash
# 100MB 더미 파일
dd if=/dev/zero of=/tmp/big.mp4 bs=1M count=100
# 틀린 토큰으로 업로드 (시간을 측정한다)
time curl -s -o /dev/null -w "%{http_code}\n" \
  -X POST http://localhost:8000/internal/submissions/1/video \
  -H "X-Worker-Token: wrong" -F "video=@/tmp/big.mp4"
```
**즉시 404가 나오는가, 100MB를 다 보낸 뒤에 나오는가?**
(로컬이라 빠르므로 파일을 더 키우거나 `--limit-rate 1M`을 주면 명확해진다)

**실험 G — 세션 혼재 재현**
`auth.py`와 `admin.py`의 `request.session.clear()`를 둘 다 주석 처리 →
관리자 로그인 → (같은 브라우저에서) 팀 로그인 → 네비게이션을 보라.
"관리자" 링크가 보이는가? 확인 후 **반드시 복구.**

**실험 H — 테스트 전부 돌려보기**
```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_admin_access.py -v
```
16개 테스트가 각각 무엇을 지키는지 이름만 보고 말할 수 있는가?

---

→ 다음: [04-submit.md](04-submit.md) — 500MB 업로드, 진행률, 그리고 두 가지 응답 형식
