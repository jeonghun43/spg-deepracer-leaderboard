# 4단계. 제출 처리 — `routers/submissions.py`, `quota.py`, `storage_paths.py`

> 이 단계의 목표: **HTTP 요청 하나가 "파일 저장 + DB 레코드 생성"이라는 부작용을 만드는 과정**을
> 완전히 이해하는 것. 그리고 그 과정에서 나오는 세 가지 큰 주제 —
> **스트리밍 업로드**, **POST-Redirect-GET**, **타임존** — 을 밑바닥까지 파는 것.

---

## 0. 이 화면이 하는 일

```
GET  /submit   → 현재 상태를 보여준다 (남은 횟수, 대기 상태, 최근 결과)
POST /submit   → 파일을 받아 저장하고 큐에 넣는다
```

**같은 URL, 다른 메서드.** 이건 우연이 아니라 REST의 기본 관례다.

---

## 1. HTTP 메서드의 의미 — 왜 GET으로 파일을 못 올리는가

### 무엇을(What)

**[쉬움]**

| 메서드 | 뜻 | 비유 |
|---|---|---|
| GET | "보여줘" | 진열장 구경. 몇 번을 봐도 가게는 안 변한다 |
| POST | "이거 해줘" | 주문. 두 번 누르면 두 번 주문된다 |

**[전공] 두 가지 성질**

| 성질 | 정의 | GET | POST | PUT | DELETE |
|---|---|---|---|---|---|
| **safe** (안전) | 서버 상태를 안 바꿈 | ✅ | ❌ | ❌ | ❌ |
| **idempotent** (멱등) | 여러 번 해도 결과 동일 | ✅ | ❌ | ✅ | ✅ |

**왜 이게 중요한가 — safe가 깨지면 생기는 일:**
- 브라우저/프록시가 GET 응답을 **캐시**한다 → 오래된 화면이 보인다
- 브라우저가 링크를 **미리 가져온다**(prefetch) → 클릭도 안 했는데 실행된다
- 크롤러가 링크를 따라간다 → **관리자가 모르는 사이 데이터 변경**

3단계에서 `/logout`이 GET인 게 문제라고 한 이유가 이것이다.

**왜 POST는 멱등하지 않은가:**
제출을 두 번 보내면 제출이 두 건 생긴다. **그래서 새로고침 문제가 생긴다** (§4에서 다룸).

### 우리 코드에서 확인

```python
@router.get("/submit")     # 조회 — 안전
def submit_form(...): ...

@router.post("/submit")    # 생성 — 안전하지 않음
async def submit_upload(...): ...
```

**정확하다.** 그런데 관리자 라우터에는 애매한 게 있다:
```python
@router.post("/teams/{team_id}/disqualify")   # 토글이라 멱등하지 않다
def toggle_disqualify(...):
    team.disqualified = not team.disqualified
```
두 번 누르면 원상복귀된다. **의도적인 토글이지만, 네트워크 재시도가 있으면 위험하다.**
엄밀히는 `PUT /teams/{id}/disqualified` 에 `true/false`를 명시하는 게 멱등하고 안전하다.
**소규모 관리자 UI에서는 토글이 실용적** — 알고 쓰면 된다.

---

## 2. `GET /submit` — 상태 조회 화면

```python
@router.get("/submit")
def submit_form(request: Request, team: Team = Depends(get_current_team), db: Session = Depends(get_db)):
    season = team.season
    active_submission = has_active_submission(db, team)
    latest_submission = db.execute(
        select(Submission).where(Submission.team_id == team.id)
        .order_by(Submission.submitted_at.desc()).limit(1)
    ).scalar_one_or_none()
    queue_position = _queue_position(db, active_submission) if active_submission else None
    estimated_wait_minutes = (
        (queue_position + 1) * settings.eval_minutes_estimate if queue_position is not None else None
    )
    remaining = get_remaining_submissions(db, team)
    can_submit = (
        season.status == SeasonStatus.ACTIVE
        and active_submission is None
        and remaining > 0
        and not team.disqualified
    )
    return templates.TemplateResponse(request, "submit.html", {...})
```

### 2-1. `can_submit` — 4개 조건의 AND

```python
can_submit = (
    season.status == SeasonStatus.ACTIVE   # 대회가 열려 있고
    and active_submission is None          # 진행 중인 제출이 없고
    and remaining > 0                      # 오늘 횟수가 남았고
    and not team.disqualified              # 실격이 아니고
)
```

**이 4개가 POST 핸들러의 검증과 일치한다.** 화면에서 버튼을 숨기고, 서버에서도 다시 검사한다.

> **원칙: 클라이언트 검증은 UX, 서버 검증은 보안.**
> 버튼을 숨겨도 `curl`로 POST를 직접 날릴 수 있다.
> **화면 검증만 있고 서버 검증이 없으면 그건 검증이 아니다.**

`submit.html`이 이 값을 그대로 쓴다:
```jinja
{% elif can_submit %}
  <form method="post" action="/submit" enctype="multipart/form-data">
```

### 2-2. `_queue_position` — 대기 순번 계산

```python
def _queue_position(db: Session, submission: Submission) -> int:
    """이 제출 앞에 몇 건이 대기/평가 중인지."""
    stmt = select(func.count()).where(
        Submission.status.in_([SubmissionStatus.QUEUED, SubmissionStatus.RUNNING]),
        Submission.submitted_at < submission.submitted_at,
    )
    return db.execute(stmt).scalar_one()
```

**[쉬움]** 은행 대기표. "내 앞에 몇 명?"

**[전공] 주목할 점**
- **팀 필터가 없다.** 전체 큐에서 앞선 건수를 센다 — 맞다. 워커가 전역 큐를 순차 처리하므로
- `submitted_at`으로 비교하는데, 워커의 정렬 기준(`ORDER BY submitted_at ASC`)과 **일치한다.**
  이게 어긋나면 안내한 순번과 실제 처리 순서가 달라진다
- `.in_()`에 enum 멤버를 넘긴다. `models.py`의 `values_callable` 덕에 올바른 소문자 값으로 번역된다

**미묘한 문제**: `submitted_at`이 정확히 같은 두 건이 있으면 순번이 흔들린다.
`server_default=func.now()`는 마이크로초 단위라 실제 충돌 가능성은 거의 없다.
그리고 팀당 활성 제출이 1건이라 **10팀 규모에서는 최대 10건**이다.

### 2-3. 예상 대기시간 — 곱셈 하나의 의미

```python
estimated_wait_minutes = (queue_position + 1) * settings.eval_minutes_estimate
```

`+1`은 **자기 자신**의 평가 시간이다. 앞에 3건 있으면 (3+1)×10 = 40분.

**이 계산이 성립하는 전제**: 워커가 **정확히 1대**이고 **순차 처리**한다.
워커를 2대로 늘리면 이 공식은 틀린다(`multi-laptop-worker-pool.md` 참고).

`eval_minutes_estimate = 10`은 실측값이고, `config.py`에 그 사실이 주석으로 적혀 있다:
```python
# GPU 없는 노트북 기준 실측값이며, 서버를 바꾸면 재측정해서 갱신해야 한다.
```
**마법의 숫자(magic number)에 출처를 남기는 좋은 습관이다.**

### 2-4. 왜 실시간 알림이 아니라 새로고침인가

`submit.html`:
```jinja
<p class="muted">결과가 나오기까지 약 {{ estimated_wait_minutes }}분 정도 걸립니다.
이 페이지를 새로고침해 확인하세요.</p>
```

**[전공] 대안과 트레이드오프**

| 방식 | 구현 | 비용 |
|---|---|---|
| 수동 새로고침 (현재) | 0줄 | 사용자가 직접 눌러야 함 |
| meta refresh / JS 폴링 | 몇 줄 | 서버에 주기적 요청 |
| SSE (Server-Sent Events) | 엔드포인트 + JS | 연결 유지, 워커→웹 통지 경로 필요 |
| WebSocket | 상당함 | 위와 같음 + 양방향 |

**결과가 10분 뒤에 나오는데 실시간 푸시를 만드는 것은 과잉이다.**
spec에서 "평가 완료 알림 없음"으로 확정한 것도 같은 판단이다.

가장 싼 개선은 HTML 한 줄이다:
```html
{% if active_submission %}<meta http-equiv="refresh" content="30">{% endif %}
```

---

## 3. `POST /submit` — 파일 업로드의 모든 것

```python
@router.post("/submit")
async def submit_upload(
    request: Request,
    team: Team = Depends(get_current_team),
    db: Session = Depends(get_db),
    model_file: UploadFile | None = File(None),
):
```

### 3-1. `multipart/form-data` — 파일이 실제로 전송되는 형식

**[쉬움]**
텍스트만 보낼 때는 `이름=값&나이=20` 처럼 간단히 붙인다.
그런데 파일은 그렇게 못 붙인다. 그래서 **구분선**을 그어 칸을 나눈다.

**[전공] 실제 전송되는 바이트**

```http
POST /submit HTTP/1.1
Content-Type: multipart/form-data; boundary=----WebKitFormBoundaryABC123
Content-Length: 262144000

------WebKitFormBoundaryABC123
Content-Disposition: form-data; name="model_file"; filename="my-model.tar.gz"
Content-Type: application/gzip

<...250MB의 바이너리...>
------WebKitFormBoundaryABC123--
```

- `boundary`는 본문에 나타나지 않을 랜덤 문자열
- 각 파트마다 헤더(`Content-Disposition`)와 본문
- **`filename`은 클라이언트가 보내는 값이다** — 신뢰하면 안 된다 (§3-6에서 다룸)

`enctype="multipart/form-data"`를 HTML 폼에 안 적으면?
브라우저가 `application/x-www-form-urlencoded`로 보내고, **파일 이름만 전송**된다. 내용은 안 간다.
```jinja
<form method="post" action="/submit" enctype="multipart/form-data">
```
`submit.html`에 정확히 있다.

### 3-2. `UploadFile` vs `bytes` — 메모리를 지키는 결정

FastAPI에는 두 가지 방법이 있다:
```python
model_file: bytes = File(...)         # 전체를 메모리에 로드
model_file: UploadFile = File(...)    # SpooledTemporaryFile 핸들
```

**`bytes`를 쓰면 250MB 파일 = 파이썬 메모리 250MB.**
동시에 3명이 올리면 750MB. 컨테이너 메모리 제한에 걸려 **OOM Kill**.

`UploadFile`은 Starlette의 `SpooledTemporaryFile`을 감싼다:
- 기본 임계값(약 1MB) 이하 → 메모리
- 넘으면 → **디스크의 임시 파일로 자동 전환(spool)**

**즉 UploadFile을 쓰는 순간 이미 메모리 문제는 상당 부분 해결된다.**

### 3-3. **청크 단위로 읽는 이유 — 이 파일의 핵심**

```python
size = 0
with open(dest_path, "wb") as out:
    while chunk := await model_file.read(1024 * 1024):
        size += len(chunk)
        if size > settings.model_upload_max_bytes:
            out.close()
            dest_path.unlink(missing_ok=True)
            max_mb = settings.model_upload_max_bytes // (1024 * 1024)
            return redirect_with_error(f"파일 용량이 너무 큽니다 (최대 {max_mb}MB).")
        out.write(chunk)
```

**[쉬움]**
물통을 옮길 때 **한 번에 다 들지 않고 바가지로 퍼서** 옮긴다.
그리고 퍼면서 "어? 너무 많은데" 싶으면 **중간에 멈춘다.**

만약 다 옮긴 뒤에 재려면, 이미 다 옮긴 상태다. 의미가 없다.

**[전공] 세 가지 이득**

1. **메모리 상한이 1MB로 고정된다.**
   파일이 10GB여도 파이썬이 동시에 들고 있는 건 1MB.

2. **조기 종료(early abort).**
   500MB 제한인데 2GB를 올리면, **500MB 지점에서 즉시 중단**한다.
   나머지 1.5GB를 받지 않는다 → 대역폭·디스크·시간 절약.

3. **디스크에 쓰레기를 안 남긴다.**
   `dest_path.unlink(missing_ok=True)`로 부분 파일을 지운다.

**`:=` (walrus operator, 파이썬 3.8+)**
```python
while chunk := await model_file.read(1024 * 1024):
```
= "읽어서 `chunk`에 넣고, 그 값이 참이면(빈 바이트열이 아니면) 루프 계속"

이게 없던 시절엔:
```python
while True:
    chunk = await model_file.read(1024*1024)
    if not chunk:
        break
```

**`await`가 필요한 이유**: `UploadFile.read()`는 코루틴이다.
네트워크에서 아직 안 온 데이터를 기다려야 하므로, 그 동안 이벤트 루프가 다른 요청을 처리한다.
→ **이래서 이 핸들러만 `async def`인 것이다.**

**청크 크기 1MB의 선택**
- 너무 작으면(4KB): 시스템 콜 횟수가 많아 오버헤드
- 너무 크면(100MB): 메모리 이득이 사라짐
- **1MB는 관례적으로 좋은 균형**이다

### 3-4. **검증 순서 — 왜 이 순서인가**

```python
if team.disqualified:                          # 1. 메모리 (팀 객체는 이미 로드됨)
if season.status != SeasonStatus.ACTIVE:       # 2. 메모리 (season도 이미 로드됨)
if has_active_submission(db, team) is not None:# 3. DB 쿼리 1회
if get_remaining_submissions(db, team) <= 0:   # 4. DB 쿼리 1회 (COUNT)
if model_file is None or not model_file.filename:  # 5. 메모리
if not filename_lower.endswith(...):           # 6. 문자열 비교
# ─────── 여기서부터 실제 파일 쓰기 ───────
while chunk := await model_file.read(...)      # 7. 디스크 I/O (가장 비쌈)
    if size > max_bytes: ...
if size == 0:                                  # 8. 다 받은 뒤에만 알 수 있음
```

**원칙: 싼 검사 먼저, 비싼 검사 나중.**

**[쉬움]**
놀이기구 앞에서 키를 잰다. 태우고 나서 재면 안 된다.
"오늘 이미 5번 탔어요"를 **줄 서기 전에** 확인해야지, 다 타고 나서 하면 무의미하다.

**[전공] 구체적 이득**

만약 순서가 반대라면(파일 먼저 받고 검증):
- 하루 한도를 다 쓴 팀이 250MB를 올린다 → 다 받고 나서 거절
- **250MB의 대역폭과 디스크 I/O가 완전히 낭비**된다
- 악의적으로 반복하면 그 자체가 DoS

**한 가지 미묘한 문제**: 확장자 검사(6번)가 파일을 읽기 전이라 좋은데,
**그 시점에 이미 클라이언트는 본문을 전송하기 시작했다.**
HTTP는 헤더를 먼저 보내고 본문을 이어 보내므로, 서버가 조기 응답해도
클라이언트가 이미 보낸 데이터는 버퍼에 있다. **완벽한 조기 차단은 불가능하다.**
그래도 서버가 디스크에 안 쓰는 것만으로 충분한 이득이다.

**`size == 0` 검사가 마지막인 이유**: 다 읽어봐야 안다. 구조적으로 어쩔 수 없다.

### 3-5. `endswith`에 튜플을 넘기는 트릭

```python
model_upload_allowed_extensions: tuple[str, ...] = (".tar.gz", ".zip")
...
if not filename_lower.endswith(settings.model_upload_allowed_extensions):
```

`str.endswith()`는 **튜플을 받으면 OR로 동작**한다. 파이썬 내장 기능이다.
```python
"a.zip".endswith((".tar.gz", ".zip"))   # True
```

`config.py`에서 `list`가 아니라 `tuple`로 선언한 이유가 이것이다.
`list`를 넘기면 `TypeError`. **타입 선택에 이유가 있다.**

`.lower()`를 먼저 한 이유: `MY-MODEL.ZIP`도 허용하기 위해.

> **주의: 확장자 검사는 보안 검사가 아니다.**
> `.zip`으로 이름만 바꾼 아무 파일이나 통과한다.
> 실제 안전성은 워커의 `_extract_archive`가 실패하면서 확보된다(`EvaluationError`).
> **여기서의 확장자 검사는 "사용자 실수를 빨리 알려주는 UX"이지 방어가 아니다.**

### 3-6. **파일명 처리 — 이 코드에서 가장 미묘한 지점**

```python
dest_dir = settings.models_dir / str(season.id) / str(team.id)
dest_dir.mkdir(parents=True, exist_ok=True)
timestamp = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
dest_path = dest_dir / f"{timestamp}_{model_file.filename}"
```

**`model_file.filename`은 클라이언트가 보낸 값을 그대로 쓴 것이다.**

**[쉬움]**
택배 상자에 붙은 주소 스티커를 그대로 믿고 그 자리에 놓는 것.
스티커에 "옆집 금고 안"이라고 적혀 있으면?

**[전공] 경로 순회(Path Traversal) 공격 시도**

공격자가 `curl`로 직접 요청을 보낸다:
```
Content-Disposition: form-data; name="model_file"; filename="../../../../tmp/evil.zip"
```

`dest_path = dest_dir / "20260726T120000_../../../../tmp/evil.zip"`

경로 조각으로 쪼개면:
```
["20260726T120000_..", "..", "..", "..", "tmp", "evil.zip"]
```

**첫 조각이 `..`가 아니라 `20260726T120000_..` 라는 이상한 디렉터리명이다.**
그런 디렉터리는 존재하지 않고, `open(..., "wb")`는 중간 디렉터리를 만들지 않으므로
`FileNotFoundError`가 나며 실패한다.

> **즉 타임스탬프 접두사가 우연히 방어 역할을 하고 있다.**
> 이건 **설계된 방어가 아니라 부작용**이다. 접두사를 빼거나 형식을 바꾸면 즉시 취약해진다.

**여전히 남아 있는 문제들:**

| 입력 | 결과 |
|---|---|
| 파일명 길이 300자 | 경로가 255바이트(ext4 파일명 제한)를 넘어 실패 → 500 에러 |
| 파일명에 `/` 포함 | 존재하지 않는 하위 경로 → 실패 → 500 에러 |
| 파일명에 널바이트 `\0` | `ValueError: embedded null byte` → 500 에러 |
| 한글/이모지 파일명 | 대체로 동작하지만 파일시스템 인코딩 의존 |
| `model_path` 컬럼 500자 초과 | DB 에러 → 500 |

**전부 "데이터는 안전하지만 500 에러"** 다. 심각하진 않으나 깔끔하지 않다.

**정석 처리:**
```python
import re
from pathlib import PurePosixPath

def safe_filename(raw: str) -> str:
    base = PurePosixPath(raw.replace("\\", "/")).name      # 디렉터리 성분 제거
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)           # 안전한 문자만
    return base[:100] or "model"                            # 길이 제한 + 빈값 대비

dest_path = dest_dir / f"{timestamp}_{safe_filename(model_file.filename)}"
```

> **더 나은 설계**: 원본 파일명을 저장 경로에 쓰지 않는 것.
> 파일은 `{submission_id}.tar.gz`로 저장하고, 원본 파일명은 DB 컬럼에 따로 보관한다.
> **"사용자 입력이 파일시스템 경로에 절대 닿지 않게" 하는 것이 가장 확실하다.**
> (단 submission_id는 커밋 후에야 나오므로 순서를 바꿔야 한다 — §3-8 참고)

### 3-7. 디렉터리 구조 `models/{season_id}/{team_id}/`

**왜 이렇게 나누나?**

1. **한 디렉터리에 파일이 수천 개면 느려진다.** (ext4는 많이 개선됐지만 `ls`가 느려진다)
2. **시즌 단위 삭제가 쉽다**: `rm -rf storage/models/1/`
3. **소유자가 경로에 드러난다** — 디버깅할 때 결정적
4. 백업/용량 계산을 팀 단위로 할 수 있다

`storage/`의 실제 구조를 보면 이 규칙이 4곳에 일관되게 적용되어 있다:
```
storage/models/{season}/{team}/{timestamp}_{filename}
storage/videos/{season}/{team}/{submission_id}.mp4
storage/metrics/{season}/{team}/{submission_id}.json
storage/eval_logs/{submission_id}.log        ← 여기만 다르다
```
`eval_logs`만 평평한 구조다. 워커가 `log_path_for(submission_id)`로 만드는데
시즌/팀 정보를 안 쓴다. **일관성 관점에서는 아쉽지만, 로그는 임시 진단용이라 타당하다.**

### 3-8. **저장 순서 — 파일 먼저, DB 나중**

```python
# 1. 파일을 디스크에 쓴다
with open(dest_path, "wb") as out: ...

# 2. DB에 레코드를 만든다
submission = Submission(team_id=team.id, model_path=..., status=SubmissionStatus.QUEUED)
db.add(submission)
db.commit()
```

**두 가지 저장소(파일시스템 + DB)에 걸친 작업이라 원자적일 수 없다.**
어느 순서든 중간에 죽으면 불일치가 생긴다.

| 순서 | 중간에 죽으면 | 심각도 |
|---|---|---|
| **파일 → DB (현재)** | 파일은 있는데 DB 레코드 없음 = **고아 파일** | 낮음. 디스크만 좀 먹음 |
| DB → 파일 | DB에 `queued`인데 파일 없음 | **높음.** 워커가 집어서 실패 → 사용자는 오류를 봄 |

**현재 순서가 옳다.** "실패 시 덜 나쁜 쪽"을 고른 것이다.

**남은 문제**: 고아 파일이 정리되지 않는다.
특히 `db.commit()`에서 `IntegrityError`(부분 유니크 인덱스 위반, 2단계 참고)가 나면
**500 에러 + 250MB 고아 파일**이 남는다.

```python
try:
    db.add(submission)
    db.commit()
except IntegrityError:
    db.rollback()
    dest_path.unlink(missing_ok=True)      # ← 파일도 정리
    return redirect_with_error("이전 제출의 결과가 아직 나오지 않았습니다.")
```

> **[전공] 이것이 분산 트랜잭션 문제의 축소판이다.**
> 정석 해법들: (a) outbox 패턴, (b) 주기적 고아 정리 배치, (c) 콘텐츠 주소 저장(해시 이름).
> **10팀 규모에서는 (b) 수준의 간단한 정리 스크립트면 충분하다.**

---

## 4. **POST-Redirect-GET — 새로고침 문제**

### 문제

```python
return RedirectResponse("/submit", status_code=303)
```

**만약 이렇게 했다면?**
```python
return templates.TemplateResponse(request, "submit.html", {...})   # 직접 렌더
```

**[쉬움]**
주문하고 나서 화면에 "주문 완료"가 떴다. 여기서 **F5를 누르면?**
브라우저가 "아까 그 주문을 다시 보낼까요?" 하고 묻거나, 그냥 다시 보낸다.
→ **똑같은 주문이 두 번 들어간다.**

**[전공]**
POST 응답을 직접 렌더링하면, 브라우저의 히스토리에서 그 페이지의 상태가 "POST"다.
- F5 → 브라우저가 폼 재전송 경고 (`ERR_CACHE_MISS` 또는 재전송 확인 대화상자)
- 뒤로가기 → 같은 문제
- 북마크 → 동작하지 않음

### 해결: PRG 패턴

```
POST /submit  →  303 See Other + Location: /submit
                        ↓ 브라우저가 자동으로
                 GET /submit  →  200 OK + HTML
```

브라우저 히스토리에는 **마지막 GET만** 남는다. F5를 눌러도 GET이 반복될 뿐이다.

**우리 코드는 성공/실패 양쪽 모두 리다이렉트한다:**
```python
def redirect_with_error(message: str) -> RedirectResponse:
    return RedirectResponse(f"/submit?error={message}", status_code=303)
```

**그리고 GET 쪽에서 받는다:**
```python
"error": request.query_params.get("error"),
```

### 이 방식(쿼리 파라미터로 에러 전달)의 장단점

**장점**: 상태를 서버에 저장할 필요가 없다. 무상태 유지.

**단점 3가지:**

1. **URL이 지저분해진다.**
   `/submit?error=%EC%98%A4%EB%8A%98%20%EC%A0%9C%EC%B6%9C%20%ED%95%9C%EB%8F%84...`
   한글이 퍼센트 인코딩된다. (Starlette `RedirectResponse`가 `quote()`로 자동 처리하므로 **깨지지는 않는다**)

2. **누구나 임의 메시지를 띄울 수 있다.**
   `/submit?error=계정이 정지되었습니다. 010-xxxx로 연락주세요` 링크를 만들어 배포하면
   **우리 사이트가 그 메시지를 그대로 표시**한다. 피싱에 악용 가능하다.

   Jinja2 자동 이스케이프 덕에 `<script>`는 실행되지 않는다(5단계에서 다룸).
   **하지만 텍스트 자체가 신뢰를 주는 것이 문제다.**

   → 정석: 에러를 **코드**로 넘기고 서버가 문구를 결정한다.
   ```python
   return RedirectResponse("/submit?error=quota_exceeded", status_code=303)
   # 템플릿에서
   {% if error == 'quota_exceeded' %}오늘 제출 한도를 모두 사용했습니다.{% endif %}
   ```

3. **새로고침해도 에러가 계속 보인다.** URL에 남아 있으므로.
   → 정석: **플래시 메시지**(세션에 한 번 넣고 읽으면 삭제)
   ```python
   request.session["flash"] = "..."      # POST에서
   msg = request.session.pop("flash", None)  # GET에서 (읽으면 사라짐)
   ```

> **판단**: 지금 방식은 가장 단순하고 동작한다. 소규모 사내 서비스에서 흔한 타협이다.
> **다만 (2)는 공개 URL을 가진 서비스에서 실제 위험이니 알고 있어야 한다.**

### 왜 303인가 (302가 아니라)

| | POST 후 리다이렉트 시 |
|---|---|
| 302 | 명세상 메서드 유지, **실제 브라우저는 GET으로 변환**(역사적 관행) — 모호함 |
| **303** | **반드시 GET으로 변환** — 명확 |
| 307 | **POST를 유지** → `/submit`에 다시 POST → **무한 루프** |

**307을 쓰면 실제로 망가진다.** 303이 유일하게 정확한 선택이다.

---

## 5. `quota.py` — 타임존과 하루의 경계

```python
def today_kst() -> dt.date:
    return dt.datetime.now(tz=KST).date()


def get_daily_done_count(db: Session, team: Team, on_date: dt.date | None = None) -> int:
    on_date = on_date or today_kst()

    day_start = dt.datetime.combine(on_date, dt.time.min, tzinfo=KST)
    day_end = day_start + dt.timedelta(days=1)
    stmt = select(func.count()).where(
        Submission.team_id == team.id,
        Submission.status == SubmissionStatus.DONE,
        Submission.finished_at >= day_start,
        Submission.finished_at < day_end,
    )
    done_count = db.execute(stmt).scalar_one()

    if team.daily_count_adjustment is not None and team.daily_count_adjustment_date == on_date:
        done_count += team.daily_count_adjustment
    return max(done_count, 0)
```

### 5-1. 왜 타임존이 어려운가

**[쉬움]**
서버는 세계 표준시(UTC)로 시간을 잰다. 한국은 그보다 9시간 빠르다.
"7월 26일"이 서버에서는 7월 25일 15:00 ~ 7월 26일 15:00 이다.
**시각을 그냥 비교하면 하루가 9시간 어긋난다.**

**[전공] naive vs aware**

```python
dt.datetime(2026, 7, 26, 0, 0)                    # naive — "어디의" 0시인지 모름
dt.datetime(2026, 7, 26, 0, 0, tzinfo=KST)        # aware — 한국 시간 0시 = UTC 7/25 15:00
```

naive와 aware를 비교하면 파이썬은 `TypeError`를 낸다. **다행이다** — 조용히 틀리는 것보다 낫다.
하지만 SQL에서는 조용히 틀린다. 그래서 `DateTime(timezone=True)`가 2단계에서 중요했던 것이다.

### 5-2. 하루 경계 계산 — 정확한 이유

```python
day_start = dt.datetime.combine(on_date, dt.time.min, tzinfo=KST)   # KST 2026-07-26 00:00:00
day_end   = day_start + dt.timedelta(days=1)                         # KST 2026-07-27 00:00:00
```

그리고 **반열린 구간** `[day_start, day_end)`:
```python
Submission.finished_at >= day_start,
Submission.finished_at <  day_end,
```

**왜 `<=` day_end가 아니라 `<` day_end인가?**
`<=`면 자정 정각(`00:00:00.000000`)에 끝난 제출이 **어제와 오늘 양쪽에 카운트**된다.
반열린 구간은 이 중복을 원천 차단한다.

**왜 `BETWEEN`이나 `DATE(finished_at) = '2026-07-26'`을 안 쓰나?**
```sql
-- 나쁜 방법
WHERE DATE(finished_at AT TIME ZONE 'Asia/Seoul') = '2026-07-26'
```
컬럼에 함수를 씌우면 **인덱스를 못 쓴다**(sargable하지 않음).
범위 비교는 인덱스를 탄다. 지금 규모에선 차이가 없지만 **습관이 중요하다.**

**`dt.time.min`** = `time(0, 0, 0, 0)`. `time(0,0,0)`이라고 써도 되지만 의도가 더 명확하다.

### 5-3. **`finished_at` 기준인 것의 의미 — 미묘하지만 중요**

카운트 기준이 `submitted_at`(제출 시각)이 아니라 **`finished_at`(완료 시각)** 이다.

**시나리오:**
```
23:55  팀A 제출         → submitted_at = 7/26 23:55
00:05  평가 완료         → finished_at  = 7/27 00:05
```
→ 이 제출은 **7월 27일** 카운트에 들어간다.

**이게 옳은가?**

**찬성**: spec의 정의가 "평가가 끝까지 정상 실행됐는지"다. `status == DONE`은
`finished_at`이 채워지는 순간 확정되므로, **상태와 시각의 기준이 일치**한다.
`submitted_at` 기준이면 아직 `queued`인 제출을 어느 날로 셀지 모호해진다.

**허점**: 참가자가 23:59에 제출하면 그건 7/27 카운트에 들어가고,
7/27 00:01에도 또 제출할 수 있다. **자정 근처에 한도를 살짝 넘겨 쓸 수 있다.**

동시 제출 1건 제약 때문에 크게 악용되진 않는다(10분에 1건씩만 가능).
**하지만 이건 알고 있어야 할 규칙의 틈이다.**

> **대안**: `submitted_at` 기준으로 세되 `status != ERROR`인 것만 센다.
> 그러면 "제출한 날" 기준이 되어 직관적이다. 대신 `queued` 상태를 어떻게 셀지 정해야 한다.
> **정답은 없고 spec이 결정할 문제다.** 현재 코드는 일관되게 `finished_at`을 쓴다.

### 5-4. 보정 델타 로직

```python
if team.daily_count_adjustment is not None and team.daily_count_adjustment_date == on_date:
    done_count += team.daily_count_adjustment
return max(done_count, 0)
```

**날짜 검사가 왜 필요한가?**
보정값에 유효기간이 없으면 어제 넣은 보정이 오늘까지 따라온다.
`daily_count_adjustment_date == on_date` 로 **당일에만 유효**하게 만든다.

**`max(done_count, 0)`가 왜 필요한가?**
관리자가 카운트를 0으로 지정하면 `adjustment = 0 - 실제건수` = 음수가 된다.
그 뒤 다른 제출이 삭제되거나 하면 합계가 음수가 될 수 있다.
**음수 카운트 → `remaining = 5 - (-2) = 7` → 한도를 넘겨 제출 가능.** 이걸 막는다.

**방어적 프로그래밍의 정석**: "이론상 안 일어나야 하는데, 일어나면 안전한 쪽으로."

### 5-5. `has_active_submission` — 조회이면서 검증

```python
def has_active_submission(db: Session, team: Team) -> Submission | None:
    stmt = select(Submission).where(
        Submission.team_id == team.id,
        Submission.status.in_(ACTIVE_SUBMISSION_STATUSES),
    )
    return db.execute(stmt).scalar_one_or_none()
```

**`scalar_one_or_none()`을 쓴 것에 주목하라.**
2건 이상이면 **예외가 터진다.** 그런데 부분 유니크 인덱스가 2건을 막는다.

→ **인덱스가 깨졌다면 여기서 시끄럽게 실패한다.** 조용히 첫 번째를 쓰는(`first()`) 것보다 낫다.
**"불변식(invariant)이 깨지면 즉시 알려라"** 는 원칙의 좋은 예다.

`ACTIVE_SUBMISSION_STATUSES`는 `models.py`의 상수다:
```python
ACTIVE_SUBMISSION_STATUSES = (SubmissionStatus.QUEUED.value, SubmissionStatus.RUNNING.value)
```
**`.value`(문자열)로 되어 있다.** `.in_()`에 문자열을 넘겨도 SQLAlchemy가 처리한다.
`retention.py`에서는 `submission.status.value in ACTIVE_SUBMISSION_STATUSES`로 파이썬 비교에 쓴다.
**한 상수를 SQL과 파이썬 양쪽에서 재사용** — 2단계에서 본 enum 불일치 문제를 줄이는 방법이다.

---

## 6. `storage_paths.py` — 실제 장애에서 태어난 모듈

### 문제가 무엇이었나

```
웹 (컨테이너 안)                워커 (WSL 호스트)
/app/storage/models/1/6/x.tar.gz  ↔  /mnt/c/Users/.../storage/models/1/6/x.tar.gz
        ↑ 같은 파일인데 경로가 다르다
```

`docker-compose.yml`:
```yaml
volumes:
  - ./storage:/app/storage
```
**bind mount**: 호스트의 `./storage`를 컨테이너의 `/app/storage`에 연결.
같은 디스크 영역이지만 **경로 이름이 완전히 다르다.**

웹이 절대 경로를 DB에 저장하면:
```
model_path = "/app/storage/models/1/6/x.tar.gz"
```
워커가 이 경로를 열려고 하면 → 호스트에 `/app/storage`는 없다 → **FileNotFoundError**.

`storage_paths.py` 최상단 주석이 이 사건을 기록하고 있다:
> (2026-07-26 운영 장애: 업로드한 모델을 워커가 못 찾아 평가 실패)

### 해결 원칙

> **DB에는 `storage_dir` 기준 상대 경로를 적고, 읽는 쪽이 자기 환경의 `settings.storage_dir`에 붙인다.**

```python
def to_storage_relative(path: Path) -> str:
    try:
        return path.relative_to(settings.storage_dir).as_posix()
    except ValueError:
        return str(path)
```

- `relative_to`: `/app/storage/models/1/6/x.tar.gz` → `models/1/6/x.tar.gz`
- `.as_posix()`: **항상 `/` 구분자**로 만든다. Windows에서 만들어도 `\`가 안 들어간다
- `except ValueError`: storage_dir 밖의 경로면 변환하지 않고 그대로.
  주석대로 "업로드 자체가 이 함수 때문에 실패하는 일은 없어야" 하기 때문

### 하위 호환 — 이미 쌓인 나쁜 데이터 다루기

```python
def resolve_storage_path(stored: str | Path) -> Path:
    raw = str(stored)
    if not _looks_absolute(raw):
        return settings.storage_dir.joinpath(*_split(raw))     # 1) 상대 경로 (현재 방식)

    path = Path(raw)
    if path.exists():
        return path                                             # 2) 절대 경로인데 존재 → 그대로

    return _reroot(raw) or path                                 # 3) 절대 경로인데 없음 → 재루팅
```

**3번이 핵심이다.** `_reroot`:
```python
def _reroot(raw: str) -> Path | None:
    parts = _split(raw)
    if STORAGE_DIR_NAME not in parts:
        return None
    idx = len(parts) - 1 - parts[::-1].index(STORAGE_DIR_NAME)   # 마지막 "storage" 위치
    tail = parts[idx + 1:]
    if not tail:
        return None
    return settings.storage_dir.joinpath(*tail)
```

`/app/storage/models/1/6/x.tar.gz`
→ 조각: `["app","storage","models","1","6","x.tar.gz"]`
→ 마지막 `"storage"` 이후: `["models","1","6","x.tar.gz"]`
→ `settings.storage_dir / "models/1/6/x.tar.gz"` ✅

**왜 "마지막" storage인가?**
`/home/user/storage/app/storage/models/...` 처럼 `storage`가 여러 번 나올 수 있다.
**마지막 것 뒤가 실제 상대 경로**다. `parts[::-1].index(...)`로 뒤에서부터 찾는다.

### `_split`의 방어

```python
def _split(raw: str) -> list[str]:
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
    if ".." in parts:
        raise ValueError(f"상위 디렉터리 참조가 포함된 경로는 허용하지 않습니다: {raw}")
    return parts
```

1. `\` → `/` 통일: 컨테이너(POSIX)가 만든 경로를 Windows에서 읽는 경우 대비
2. 빈 조각과 `.` 제거: `//`, `/./` 정규화
3. **`..` 발견 시 예외** ← **경로 순회 방어**

**§3-6에서 본 파일명 문제가 여기서 부분적으로 막힌다.**
DB에 `../` 가 들어간 경로가 저장되어 있어도 읽을 때 거부한다.

**하지만 `to_storage_relative`는 이 검사를 안 한다** — 쓸 때는 안 막고 읽을 때만 막는다.
**입구에서 막는 게 더 나은 설계다.**

### `_looks_absolute` — 크로스 플랫폼

```python
def _looks_absolute(raw: str) -> bool:
    return raw.startswith("/") or raw.startswith("\\") or (len(raw) > 1 and raw[1] == ":")
```
- `/app/...` — POSIX 절대
- `\\server\share` — UNC
- `C:\Users\...` — Windows 드라이브 (두 번째 글자가 `:`)

`Path(raw).is_absolute()`를 안 쓴 이유: **파이썬이 실행 중인 OS 기준으로만 판단**하기 때문.
Linux에서 `Path("C:/x").is_absolute()` 는 `False`다.
**컨테이너(Linux)가 만든 경로를 Windows에서, 또는 그 반대로 해석해야 하므로 직접 판단한다.**

이건 `tests/test_storage_paths.py`가 검증하고 있다.

---

## 7. 자가 점검 질문

1. GET이 "safe"해야 한다는 게 무슨 뜻인가? 깨지면 어떤 실제 사고가 나는가?
2. `enctype="multipart/form-data"`를 빼면 어떻게 되는가?
3. `bytes = File(...)` 대신 `UploadFile`을 쓰는 이유는? `UploadFile` 내부는 어떻게 동작하는가?
4. 청크 단위로 읽으면서 크기를 검사하는 것이 주는 이득 3가지는?
5. `while chunk := await ...` 에서 `await`가 필요한 이유는? 이 때문에 왜 `async def`여야 하는가?
6. 검증 순서를 "싼 것부터"로 하는 이유를 구체적 낭비량으로 설명하라.
7. `endswith`에 튜플을 넘기면 왜 동작하는가? `config.py`가 `list`가 아니라 `tuple`인 이유는?
8. 확장자 검사는 보안 검사인가? 아니라면 진짜 방어는 어디에 있는가?
9. `filename="../../evil.zip"` 을 보내면 실제로 무슨 일이 일어나는가? 왜 그렇게 되는가?
10. 파일 저장과 DB INSERT의 순서가 지금과 반대라면 어떤 문제가 더 심각해지는가?
11. PRG 패턴이 없으면 사용자가 F5를 눌렀을 때 무슨 일이 생기는가?
12. 302, 303, 307 중 POST 후 리다이렉트에 303을 써야 하는 이유는? 307이면 어떻게 되는가?
13. 에러를 쿼리 파라미터로 넘기는 방식의 문제 3가지는? 각각의 대안은?
14. `day_end`를 `<=`가 아니라 `<`로 비교하는 이유는?
15. 하루 카운트가 `submitted_at`이 아니라 `finished_at` 기준인 것의 장점과 허점은?
16. `max(done_count, 0)`이 막는 시나리오는?
17. `has_active_submission`이 `first()`가 아니라 `scalar_one_or_none()`인 것의 의미는?
18. 왜 DB에 절대 경로를 저장하면 안 되는가? 2026-07-26에 무슨 일이 있었는가?
19. `_reroot`가 "마지막" storage를 찾는 이유는?
20. `Path.is_absolute()` 대신 `_looks_absolute`를 직접 만든 이유는?

---

## 8. 실험 과제

**실험 A — 청크 읽기 관찰**
업로드 루프에 로그를 넣고 큰 파일을 올려보라.
```python
while chunk := await model_file.read(1024 * 1024):
    size += len(chunk)
    print(f"읽음 {size / 1024 / 1024:.1f}MB")
```
그리고 `model_upload_max_bytes`를 10MB로 낮춘 뒤 50MB 파일을 올려보라.
**50MB를 다 읽는가, 10MB에서 멈추는가?** 이것이 조기 종료다.

**실험 B — PRG 없애보기**
성공 응답을 `RedirectResponse` 대신 `templates.TemplateResponse(...)`로 바꾼 뒤
제출하고 F5를 눌러보라. 브라우저가 뭐라고 하는가? 제출이 두 번 되는가?
**반드시 되돌릴 것.**

**실험 C — 307로 무한 루프 만들기**
`status_code=303`을 `307`로 바꾸고 제출해보라. 개발자도구 Network 탭을 켜두고.
무슨 일이 일어나는지 보고 즉시 되돌린다.

**실험 D — 경로 순회 시도**
```bash
curl -X POST http://localhost:8000/submit \
  -H "Cookie: session=<로그인쿠키>" \
  -F "model_file=@some.zip;filename=../../../../tmp/evil.zip" -v
```
실제로 어디에 저장되는가? 에러가 나는가? **왜 그런지 설명할 수 있어야 한다.**

**실험 E — 타임존 경계 검증**
```python
import datetime as dt
from zoneinfo import ZoneInfo
KST = ZoneInfo("Asia/Seoul")

d = dt.date(2026, 7, 26)
start = dt.datetime.combine(d, dt.time.min, tzinfo=KST)
print(start)                              # 2026-07-26 00:00:00+09:00
print(start.astimezone(dt.timezone.utc))  # 2026-07-25 15:00:00+00:00  ← UTC로는 전날!
```
DB에 저장된 `finished_at`을 psql로 보고, 어느 날짜로 카운트되는지 손으로 계산해보라.

**실험 F — 경로 유틸 직접 테스트**
```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_storage_paths.py -v
```
테스트를 읽고, 케이스를 하나 추가해보라 (예: `\\` 구분자, `..` 포함 경로).

---

→ 다음: [05-leaderboard.md](05-leaderboard.md) — 조회, 정렬, 템플릿, 그리고 XSS
