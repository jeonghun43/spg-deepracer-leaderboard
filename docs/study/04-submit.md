# 4단계. 제출 처리 — `routers/submissions.py`, `quota.py`, `storage_paths.py`, `static/upload.js`

> 이 단계의 목표: **HTTP 요청 하나가 "파일 저장 + DB 레코드 생성"이라는 부작용을 만드는 과정**을
> 완전히 이해하는 것. 그 과정에서 나오는 큰 주제 4개 —
> **스트리밍 업로드**, **POST-Redirect-GET**, **타임존**, **점진적 향상(progressive enhancement)** — 을 밑바닥까지 판다.

---

## 0. 이 화면이 하는 일

```
GET  /submit   → 현재 상태를 보여준다 (남은 횟수, 대기 상태, 평가서버 생존, 최근 결과)
POST /submit   → 파일을 받아 저장하고 큐에 넣는다
                 · 일반 폼 전송  → 303 리다이렉트
                 · upload.js 전송 → JSON 응답
```

**같은 URL, 다른 메서드. 그리고 같은 메서드, 다른 응답 형식.**
후자가 이 단계의 새로운 주제다.

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
엄밀히는 `PUT /teams/{id}/disqualified` 에 `true/false`를 명시하는 게 멱등하다.
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
    return templates.TemplateResponse(request, "submit.html", {
        "team": team, "season": season,
        "active_submission": active_submission,
        "latest_submission": latest_submission,
        "queue_position": queue_position,
        "estimated_wait_minutes": estimated_wait_minutes,
        "remaining": remaining,
        "daily_limit": settings.daily_submission_limit,
        "can_submit": can_submit,
        "eval_laps": settings.online_eval_laps,
        "worker_status": get_worker_status(db),
        # 업로드 스크립트가 전송 전에 파일을 검사할 때 쓰는 값. 규칙의 출처는
        # config.py 하나로 유지하려고 화면에 내려보낸다 (upload-progress-ux.md §2-5).
        "upload_max_bytes": settings.model_upload_max_bytes,
        "allowed_extensions": settings.model_upload_allowed_extensions,
        "error": request.query_params.get("error"),
    })
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

`can_submit`은 **세 곳**에서 쓰인다:
```jinja
{% elif can_submit %}                              {# 폼 표시 #}
{% if can_submit %}<script src="/static/upload.js" defer></script>{% endif %}   {# 스크립트 로드 #}
```
폼이 없으면 스크립트도 로드하지 않는다 — **필요 없는 JS를 안 내려보낸다.**

### 2-2. **설정값을 화면에 내려보내는 이유 — 단일 진실 공급원**

```python
"upload_max_bytes": settings.model_upload_max_bytes,
"allowed_extensions": settings.model_upload_allowed_extensions,
```

```jinja
<form ... id="submit-form"
      data-max-bytes="{{ upload_max_bytes }}"
      data-allowed-ext="{{ allowed_extensions|join(',') }}">
```

```javascript
var maxBytes = parseInt(form.getAttribute("data-max-bytes"), 10) || 0;
var allowedExt = (form.getAttribute("data-allowed-ext") || "").split(",")...
```

**[쉬움]**
"최대 500MB"라는 규칙이 **서버에 한 번, JS에 한 번** 적혀 있으면
나중에 하나만 고쳤을 때 **둘이 다른 말을 한다.**
그래서 서버가 정한 값을 화면에 실어 보내고, JS는 그걸 읽어 쓴다.

**[전공]**
주석이 의도를 명시한다:
> 규칙의 출처는 config.py 하나로 유지하려고 화면에 내려보낸다

**HTML `data-*` 속성**은 이런 "서버 → 클라이언트 설정 전달"의 표준 통로다.
`<script>var MAX = 500;</script>` 로 인라인 JS를 만드는 것보다 낫다:
- CSP(Content Security Policy)로 인라인 스크립트를 막아도 동작한다
- 값이 자동 이스케이프된다 (5단계 XSS 참고)
- 마크업과 로직이 분리된다

**만약 JS에 하드코딩했다면 생길 일:**
```
config.py:  model_upload_max_bytes = 300MB 로 변경
upload.js:  여전히 500MB 라고 판단
→ 사용자가 400MB 파일을 고른다 → JS는 통과시킨다
→ 300MB 지점에서 서버가 거절 → "왜 미리 안 알려줬지?"
```

### 2-3. `_queue_position` — 대기 순번 계산

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

### 2-4. 예상 대기시간 — 그리고 그것이 거짓말이 되는 순간

```python
estimated_wait_minutes = (queue_position + 1) * settings.eval_minutes_estimate
```

`+1`은 **자기 자신**의 평가 시간이다. 앞에 3건 있으면 (3+1)×10 = 40분.

**이 계산이 성립하는 전제**: 워커가 **정확히 1대**이고 **지금 돌고 있다.**

**두 번째 전제가 깨질 수 있다** — 워커는 다른 기기에서 돈다(노트북이 꺼질 수 있다).
그래서 템플릿이 조건을 나눈다:

```jinja
{% if worker_status and not worker_status.online %}
{# 예상 시간 계산은 워커가 돌고 있다는 가정이라, 멈춰 있으면 숫자를 보여주면 거짓말이 된다. #}
<p class="muted">평가 서버가 재개된 뒤 순서대로 처리됩니다. 이 페이지를 새로고침해 확인하세요.</p>
{% elif estimated_wait_minutes %}
<p class="muted">결과가 나오기까지 약 {{ estimated_wait_minutes }}분 정도 걸립니다. ...</p>
{% endif %}
```

**[전공] 이것이 좋은 UX 판단이다.**
"40분"이라고 했는데 8시간이 걸리면, 그 숫자는 **없느니만 못하다.**
모를 때는 **모른다고 말하는 것**이 정직하고, 신뢰를 지킨다.

`eval_minutes_estimate = 10`은 실측값이고 `config.py`에 출처가 주석으로 있다:
```python
# GPU 없는 노트북 기준 실측값이며, 서버를 바꾸면 재측정해서 갱신해야 한다.
```
**마법의 숫자(magic number)에 출처를 남기는 좋은 습관이다.**
(현재는 EC2 평가 서버로 옮겼으니 재측정 대상이다.)

### 2-5. `_worker_status.html` — 부분 템플릿(partial)

```jinja
{# submit.html:9, leaderboard.html:7 #}
{% include "_worker_status.html" %}
```

```jinja
{# app/templates/_worker_status.html #}
{% if worker_status and not worker_status.online %}
<div class="card" style="border-color:#c98a00;">
  <strong>평가 서버가 잠시 중지되어 있습니다.</strong>
  <p class="muted" style="margin-bottom:0;">
    제출은 정상적으로 접수되며, 서버가 재개되면 접수된 순서대로 처리됩니다.
    {% if worker_status.minutes_ago is not none %}(마지막 응답: 약 {{ worker_status.minutes_ago }}분 전){% endif %}
  </p>
</div>
{% endif %}
```

**`_` 접두사**는 "이건 단독으로 렌더하는 화면이 아니라 조각"이라는 관례적 표시다.
(Rails의 partial, Django의 include와 같은 개념)

**`{% include %}` vs `{% extends %}`**
| | 방향 | 용도 |
|---|---|---|
| `extends` | 자식이 부모의 블록을 채운다 | 레이아웃 (base.html) |
| `include` | 현재 위치에 다른 파일을 끼워넣는다 | 재사용 조각 |

**include된 템플릿은 부모의 컨텍스트를 그대로 본다** — `worker_status`를 따로 안 넘겨도 된다.
그래서 두 라우터가 컨텍스트에 `worker_status`만 넣어주면 된다:
```python
"worker_status": get_worker_status(db),   # submissions.py:81, leaderboard.py:129
```

**`{% if worker_status and ... %}`** — `worker_status`가 없는 화면에서 include돼도 안 깨진다.
방어적이면서 비용이 0이다.

### 2-6. 왜 실시간 알림이 아니라 새로고침인가

**[전공] 대안과 트레이드오프**

| 방식 | 구현 | 비용 |
|---|---|---|
| 수동 새로고침 (현재) | 0줄 | 사용자가 직접 눌러야 함 |
| meta refresh / JS 폴링 | 몇 줄 | 서버에 주기적 요청 |
| SSE (Server-Sent Events) | 엔드포인트 + JS | 연결 유지, **워커→웹 통지 경로 필요** |
| WebSocket | 상당함 | 위와 같음 + 양방향 |

**결과가 10분 뒤에 나오는데 실시간 푸시를 만드는 것은 과잉이다.**
게다가 워커가 **다른 기기**에 있어서 SSE를 하려면 워커→웹 통지 채널을 또 만들어야 한다.
(하트비트가 그 축소판이긴 하다 — 30초 주기 DB 갱신.)

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
    season: Season = team.season
    as_json = wants_json_response(request.headers.get("accept"))

    def redirect_with_error(message: str) -> JSONResponse | RedirectResponse:
        # 스크립트로 올린 경우엔 화면을 갈아끼우지 않고 그 자리에서 안내한다.
        # 리다이렉트로 답하면 고른 파일이 사라져 참가자가 처음부터 다시 해야 한다.
        if as_json:
            return JSONResponse({"ok": False, "error": message}, status_code=400)
        return RedirectResponse(f"/submit?error={message}", status_code=303)
    ...
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
Accept: application/json                      ← upload.js가 붙인다 (§4)

------WebKitFormBoundaryABC123
Content-Disposition: form-data; name="model_file"; filename="my-model.tar.gz"
Content-Type: application/gzip

<...250MB의 바이너리...>
------WebKitFormBoundaryABC123--
```

- `boundary`는 본문에 나타나지 않을 랜덤 문자열
- **`filename`은 클라이언트가 보내는 값이다** — 신뢰하면 안 된다 (§3-7)

`enctype="multipart/form-data"`를 HTML 폼에 안 적으면
브라우저가 `application/x-www-form-urlencoded`로 보내고 **파일 이름만 전송**된다.

### 3-2. `UploadFile` vs `bytes` — 메모리를 지키는 결정

```python
model_file: bytes = File(...)         # 전체를 메모리에 로드
model_file: UploadFile = File(None)   # SpooledTemporaryFile 핸들
```

**`bytes`를 쓰면 250MB 파일 = 파이썬 메모리 250MB.**
동시에 3명이 올리면 750MB. **`mem_limit: 900m`인 서버에서는 즉시 OOM Kill이다.**

`UploadFile`은 Starlette의 `SpooledTemporaryFile`을 감싼다:
- 기본 임계값(약 1MB) 이하 → 메모리
- 넘으면 → **디스크의 임시 파일로 자동 전환(spool)**

> **[전공] 이게 클라우드 이관으로 훨씬 중요해졌다.**
> 노트북(16GB)에서는 실수해도 티가 안 났지만, `mem_limit: 900m` 컨테이너에서는
> **바로 죽는다.** 배포 환경의 제약이 코드 선택을 검증해 준 셈이다.

### 3-3. **청크 단위로 읽는 이유 — 이 파일의 핵심**

```python
size = 0
try:
    with open(dest_path, "wb") as out:
        while chunk := await model_file.read(1024 * 1024):
            size += len(chunk)
            if size > settings.model_upload_max_bytes:
                out.close()
                dest_path.unlink(missing_ok=True)
                max_mb = settings.model_upload_max_bytes // (1024 * 1024)
                return redirect_with_error(f"파일 용량이 너무 큽니다 (최대 {max_mb}MB).")
            out.write(chunk)
except ClientDisconnect:
    # 참가자가 업로드 도중 취소하거나 창을 닫으면 여기로 온다. 절반짜리 파일을
    # 남겨두면 아무도 쓰지 않으면서 디스크만 차지한다 (250MB짜리다).
    dest_path.unlink(missing_ok=True)
    raise
```

**[쉬움]**
물통을 옮길 때 **한 번에 다 들지 않고 바가지로 퍼서** 옮긴다.
그리고 퍼면서 "어? 너무 많은데" 싶으면 **중간에 멈춘다.**

**[전공] 세 가지 이득**

1. **메모리 상한이 1MB로 고정된다.** 파일이 10GB여도 파이썬이 동시에 들고 있는 건 1MB
2. **조기 종료(early abort).** 500MB 제한인데 2GB를 올리면 **500MB 지점에서 즉시 중단**
3. **디스크에 쓰레기를 안 남긴다.** `dest_path.unlink(missing_ok=True)`

**`:=` (walrus operator, 파이썬 3.8+)**
= "읽어서 `chunk`에 넣고, 그 값이 참이면(빈 바이트열이 아니면) 루프 계속"

**`await`가 필요한 이유**: `UploadFile.read()`는 코루틴이다.
네트워크에서 아직 안 온 데이터를 기다려야 하므로, 그 동안 이벤트 루프가 다른 요청을 처리한다.
→ **이래서 이 핸들러가 `async def`인 것이다.**

### 3-4. **`ClientDisconnect` — 업로드 취소를 다루는 코드**

```python
except ClientDisconnect:
    dest_path.unlink(missing_ok=True)
    raise
```

### 무엇을(What)

**[쉬움]**
참가자가 250MB를 절반쯤 올리다가 **"취소" 버튼을 누르거나 창을 닫으면**,
서버에는 **반쪽짜리 파일 125MB**가 남는다. 아무도 안 쓰는데 디스크만 먹는다.

**[전공]**
`starlette.requests.ClientDisconnect`는 요청 본문을 읽는 도중
**클라이언트가 연결을 끊었을 때** Starlette이 던지는 예외다.

`await model_file.read(...)` 가 내부적으로 `receive()`를 호출하는데,
그때 ASGI 서버가 `http.disconnect` 이벤트를 주면 이 예외가 발생한다.

### 왜(Why) — 이 코드가 왜 생겼나

**`upload.js`가 취소 버튼을 만들었기 때문이다:**
```javascript
if (cancelButton) {
  cancelButton.addEventListener("click", function () {
    if (xhr && uploading) xhr.abort();     // ← 여기서 연결이 끊긴다
  });
}
```

**기능을 하나 추가하면 그 뒤처리도 따라온다.**
취소 버튼이 없었다면 이 예외는 거의 안 났을 것이다(창을 닫는 경우 정도).

### 어떻게(How) — `raise`를 다시 하는 이유

```python
except ClientDisconnect:
    dest_path.unlink(missing_ok=True)   # 정리하고
    raise                                # 다시 던진다
```

**왜 삼키지 않는가?**

클라이언트가 이미 끊었으므로 **응답을 보낼 곳이 없다.**
여기서 `return redirect_with_error(...)` 를 해봐야 아무도 안 받는다.

`raise`로 다시 올리면:
- Starlette/uvicorn이 "아, 끊긴 요청이구나" 하고 조용히 정리한다
- 액세스 로그에 비정상 종료로 남아 **실제로 무슨 일이 있었는지 알 수 있다**

> **[전공] 패턴 이름: "정리하고 다시 던지기(cleanup and re-raise)".**
> ```python
> try:
>     ...
> except SomeError:
>     cleanup()
>     raise
> ```
> **내 책임(내가 만든 임시 파일)만 정리하고, 예외 처리 자체는 상위에 맡긴다.**
> 예외를 삼키면 상위 계층이 "성공했다"고 오해한다.

**`with open(...)` 블록 안에서 예외가 나면 파일은 어떻게 되나?**
`with`가 `__exit__`을 호출해 **파일 핸들은 닫힌다.** 하지만 **파일 자체는 디스크에 남는다.**
그래서 `unlink`가 따로 필요하다. **닫는 것과 지우는 것은 다르다.**

### 3-5. **검증 순서 — 왜 이 순서인가**

```python
if team.disqualified:                          # 1. 메모리 (팀 객체는 이미 로드됨)
if season.status != SeasonStatus.ACTIVE:       # 2. 메모리
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

**[전공]** 순서가 반대라면(파일 먼저 받고 검증):
- 하루 한도를 다 쓴 팀이 250MB를 올린다 → 다 받고 나서 거절
- **250MB의 대역폭과 디스크 I/O가 완전히 낭비**된다

**한 가지 미묘한 문제**: 확장자 검사(6번)가 파일을 읽기 전이지만,
**그 시점에 이미 클라이언트는 본문을 전송하기 시작했다.**
HTTP는 헤더를 먼저 보내고 본문을 이어 보내므로, 서버가 조기 응답해도
클라이언트가 이미 보낸 데이터는 버퍼에 있다. **완벽한 조기 차단은 불가능하다.**

> **그래서 `upload.js`가 클라이언트 쪽에서 먼저 막는다** (§4-3).
> 전송 자체를 시작하지 않으면 낭비가 0이다. **가장 좋은 최적화다.**

### 3-6. `endswith`에 튜플을 넘기는 트릭

```python
model_upload_allowed_extensions: tuple[str, ...] = (".tar.gz", ".zip")
...
if not filename_lower.endswith(settings.model_upload_allowed_extensions):
```

`str.endswith()`는 **튜플을 받으면 OR로 동작**한다. 파이썬 내장 기능이다.
`config.py`에서 `list`가 아니라 `tuple`로 선언한 이유가 이것이다 — `list`를 넘기면 `TypeError`.

> **주의: 확장자 검사는 보안 검사가 아니다.**
> `.zip`으로 이름만 바꾼 아무 파일이나 통과한다.
> 실제 안전성은 워커의 `_extract_archive`가 실패하면서 확보된다(`EvaluationError`).
> **여기서의 확장자 검사는 "사용자 실수를 빨리 알려주는 UX"다.**

### 3-7. **파일명 처리 — 이 코드에서 가장 미묘한 지점**

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
| 파일명 길이 300자 | 경로가 파일명 제한(255바이트)을 넘어 실패 → 500 에러 |
| 파일명에 `/` 포함 | 존재하지 않는 하위 경로 → 실패 → 500 |
| 파일명에 널바이트 `\0` | `ValueError: embedded null byte` → 500 |
| `model_path` 컬럼 500자 초과 | DB 에러 → 500 |

**전부 "데이터는 안전하지만 500 에러"** 다.

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
> **"사용자 입력이 파일시스템 경로에 절대 닿지 않게" 하는 것이 가장 확실하다.**

### 3-8. 디렉터리 구조 `models/{season_id}/{team_id}/`

**왜 이렇게 나누나?**
1. 한 디렉터리에 파일이 수천 개면 느려진다
2. **시즌 단위 삭제가 쉽다**: `rm -rf storage/models/1/`
3. **소유자가 경로에 드러난다** — 디버깅할 때 결정적

`storage/`의 실제 구조를 보면 이 규칙이 일관되게 적용되어 있다:
```
storage/models/{season}/{team}/{timestamp}_{filename}
storage/videos/{season}/{team}/{submission_id}.mp4
storage/metrics/{season}/{team}/{submission_id}.json
storage/eval_logs/{submission_id}.log        ← 여기만 다르다 (임시 진단용)
storage/work/{submission_id}/                ← 워커의 작업 디렉터리 (평가 후 삭제)
```

**중요**: `internal.py`가 영상/metrics를 저장할 때도 **같은 규칙**을 쓴다:
```python
# app/routers/internal.py:72
rel_path = f"{submission.team.season_id}/{submission.team_id}/{submission.id}.mp4"
```
**워커가 HTTP로 올려도 경로 규칙은 서버가 정한다.** 워커가 보낸 경로를 믿지 않는다.
→ **경로 주입(path injection)이 원천 차단된다.** 좋은 설계다.

### 3-9. **저장 순서 — 파일 먼저, DB 나중**

```python
# 1. 파일을 디스크에 쓴다
with open(dest_path, "wb") as out: ...

# 2. DB에 레코드를 만든다
submission = Submission(team_id=team.id, model_path=..., status=SubmissionStatus.QUEUED)
db.add(submission)
db.commit()
```

**두 가지 저장소(파일시스템 + DB)에 걸친 작업이라 원자적일 수 없다.**

| 순서 | 중간에 죽으면 | 심각도 |
|---|---|---|
| **파일 → DB (현재)** | 파일은 있는데 DB 레코드 없음 = **고아 파일** | 낮음. 디스크만 좀 먹음 |
| DB → 파일 | DB에 `queued`인데 파일 없음 | **높음.** 워커가 집어서 실패 |

**현재 순서가 옳다.** "실패 시 덜 나쁜 쪽"을 고른 것이다.

**남은 문제**: `db.commit()`에서 `IntegrityError`(부분 유니크 인덱스 위반, 2단계)가 나면
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

> **`ClientDisconnect`는 정리하는데 `IntegrityError`는 안 한다** — 일관성이 아쉬운 지점이다.
> 같은 함수 안에서 한쪽은 고아 파일을 지우고 한쪽은 안 지운다.

---

## 4. **`upload.js` — 점진적 향상(Progressive Enhancement)**

이 절이 이 단계의 새로운 큰 주제다.

### 왜(Why) — 무엇을 해결하려는 것인가

파일 상단 주석:
```javascript
/*
 * 250MB 파일이 회선을 타고 가는 20초~수 분 동안 화면이 침묵하면 참가자는 서비스가
 * 고장 났다고 판단한다. 전송 시간 자체는 줄일 수 없으므로 "지금 무엇이 얼마나
 * 진행됐는지"를 계속 보여준다.
 */
```

**[쉬움]**
택배를 보냈는데 **아무 소식이 없으면** 불안하다. "지금 어디쯤 갔어요"를 보여주면 기다릴 수 있다.
걸리는 시간은 똑같은데 **체감이 완전히 달라진다.**

**[전공]**
이건 성능 최적화가 아니라 **인지된 성능(perceived performance)** 개선이다.
사용자가 못 견디는 것은 "느림"이 아니라 **"무슨 일이 일어나는지 모름"** 이다.

기본 HTML 폼 업로드는 진행률을 보여줄 방법이 없다.
브라우저 하단에 아주 작은 표시가 뜨거나 아무것도 안 뜬다.

### 4-1. **점진적 향상 — 이 스크립트의 설계 철학**

```javascript
/*
 * 이 스크립트는 기존 form의 submit을 가로채는 방식으로만 동작한다. 파일이 로딩되지
 * 않거나 JS가 꺼져 있으면 form이 그대로 POST되어 지금까지와 똑같이 제출된다.
 * 대회 중에 참가자의 제출 경로가 이 파일 하나에 걸리게 두지 않기 위함이다.
 */
```

**[쉬움]**
계단이 있고, 그 옆에 에스컬레이터를 놓았다.
**에스컬레이터가 고장 나도 계단으로 올라갈 수 있다.**
에스컬레이터만 있는 건물은 고장 나면 아무도 못 올라간다.

**[전공] Progressive Enhancement의 정의**

1. **기본 기능을 HTML만으로 완성한다** (여기서는 `<form method="post">`)
2. JS는 그 위에 **얹기만** 한다
3. JS가 실패해도 기본 기능은 살아있다

반대 접근은 **Graceful Degradation**(JS 우선, 실패 시 대체 제공)인데,
대체 경로가 실제로 테스트되지 않아 잘 안 동작하는 경우가 많다.

**코드가 이 철학을 어떻게 지키는가:**

```javascript
var form = document.getElementById("submit-form");
if (!form || !window.XMLHttpRequest || !window.FormData) return;
...
if (!fileInput || !submitButton || !progressBar || !progressText) return;
```

**필요한 것이 하나라도 없으면 조용히 아무것도 안 한다.**
그러면 `form.addEventListener("submit", ...)` 이 등록되지 않고,
브라우저의 기본 폼 전송이 그대로 일어난다.

**HTML 쪽도 이 전제를 지킨다:**
```jinja
<form method="post" action="/submit" enctype="multipart/form-data" id="submit-form" ...>
```
`method`, `action`, `enctype`이 다 있다. **JS 없이도 완전한 폼이다.**

`<script src="/static/upload.js" defer></script>` — `defer`는 HTML 파싱 후 실행.
스크립트 로딩이 화면 렌더를 막지 않는다.

### 4-2. IIFE와 `"use strict"`

```javascript
(function () {
  "use strict";
  ...
})();
```

**[전공]**
- **IIFE**(즉시 실행 함수 표현식): 변수를 전역에 안 남긴다.
  `var form`이 `window.form`이 되는 것을 막는다
- `"use strict"`: 선언 안 한 변수 사용, 중복 매개변수 등을 에러로 만든다

`var`를 쓰고 `const/let`을 안 쓴 것, `XMLHttpRequest`를 쓰고 `fetch`를 안 쓴 것은
**구형 브라우저 호환**을 위한 선택으로 보인다.
(그리고 실제로 `fetch`로는 **업로드 진행률을 못 잰다** — §4-4)

### 4-3. **전송 전 검사 — 가장 좋은 최적화**

```javascript
function validationError(file) {
  if (!file) return "업로드할 모델 파일을 선택해주세요.";
  var name = (file.name || "").toLowerCase();
  var extOk = allowedExt.length === 0 || allowedExt.some(function (ext) {
    return name.slice(-ext.length) === ext;
  });
  if (!extOk) return "허용되지 않는 파일 형식입니다 (허용: " + allowedExt.join(", ") + ").";
  if (file.size === 0) return "빈 파일은 업로드할 수 없습니다.";
  if (maxBytes && file.size > maxBytes) {
    return "파일 용량이 너무 큽니다 (최대 " + formatBytes(maxBytes) + "). 선택한 파일은 " +
      formatBytes(file.size) + "입니다.";
  }
  return null;
}
```

**[쉬움]**
250MB를 다 보내고 나서 "형식이 틀렸어요"라고 하면 시간이 통째로 낭비된다.
**보내기 전에** 파일 이름과 크기를 보고 미리 알려준다.

**[전공]**
`File` 객체는 `name`, `size`, `type`을 **읽는 것만으로** 제공한다. 파일을 읽지 않아도 안다.
→ **0바이트 전송으로 검사가 끝난다.**

**두 시점에 검사한다:**
```javascript
fileInput.addEventListener("change", function () {   // 파일을 고르는 순간
  ...
  var problem = validationError(file);
  if (problem) showError(problem);
});

form.addEventListener("submit", function (event) {   // 제출 버튼을 누를 때
  var problem = validationError(file);
  if (problem) { event.preventDefault(); showError(problem); return; }
```

**고르는 순간 알려주는 것이 UX의 핵심이다.** 제출 버튼을 누를 때까지 기다리지 않는다.

**서버 검증과 완전히 중복이다. 그래도 필요하다:**

| | 클라이언트 검사 | 서버 검사 |
|---|---|---|
| 목적 | **빠른 피드백** (0초) | **보안·정합성** |
| 우회 가능? | ✅ (JS 끄면 그만) | ❌ |
| 없으면? | 250MB 보내고 실패 | **시스템이 뚫린다** |

**둘 다 있어야 하고, 둘의 목적이 다르다.**
2단계의 "앱 검사 = 안내, DB 제약 = 보장"과 정확히 같은 구조다.

**규칙 값이 서버에서 온다는 점을 다시 강조한다:**
```javascript
var maxBytes = parseInt(form.getAttribute("data-max-bytes"), 10) || 0;
```
→ 두 검사가 **같은 규칙**을 쓴다. 어긋날 수 없다.

### 4-4. `XMLHttpRequest`와 업로드 진행률

```javascript
xhr = new XMLHttpRequest();
xhr.open("POST", form.getAttribute("action") || "/submit", true);
xhr.setRequestHeader("Accept", "application/json");

xhr.upload.onprogress = function (e) { ... };
```

**[전공] 왜 `fetch`가 아니라 `XMLHttpRequest`인가?**

`fetch()` API는 **업로드 진행률을 제공하지 않는다.**
(다운로드는 `response.body` 스트림으로 가능하지만 업로드는 표준화되지 않았다.
`ReadableStream` 요청 본문이 일부 브라우저에 있지만 널리 쓰기 어렵다.)

`XMLHttpRequest`의 `xhr.upload`는 `XMLHttpRequestUpload` 객체이고,
`onprogress`, `onload`, `onabort` 이벤트를 준다.

**`xhr.upload.onprogress` vs `xhr.onprogress`**
- `xhr.upload.onprogress` — **보내는** 진행률 ← 우리가 원하는 것
- `xhr.onprogress` — **받는** 진행률

`form.getAttribute("action")` 으로 URL을 읽는다 — **HTML을 단일 진실 공급원으로 유지**한다.
`"/submit"`을 JS에 하드코딩하지 않는다.

### 4-5. **진행률 표시의 정직함 — 99%에서 멈추는 이유**

```javascript
xhr.upload.onprogress = function (e) {
  if (!e.lengthComputable) {
    progressText.textContent = "업로드 중… (진행률을 알 수 없습니다)";
    return;
  }
  // 브라우저가 보고하는 loaded는 소켓 버퍼에 넘긴 양이라 실제 수신보다 앞선다.
  // 그래서 여기서는 100%를 쓰지 않고, 서버 응답을 기다리는 구간에서 따로 알린다.
  var percent = Math.min(99, (e.loaded / e.total) * 100);
  setProgress(percent);
```

**[쉬움]**
브라우저가 "다 보냈어요"라고 해도, 그건 **우체통에 넣었다**는 뜻이지
**상대가 받았다**는 뜻이 아니다. 그래서 99%까지만 보여주고,
진짜 다 끝나면 그때 100%로 만든다.

**[전공]**
`e.loaded`는 **OS 소켓 버퍼에 write한 양**이다. 네트워크로 실제 전송되고
서버가 수신한 양보다 **항상 앞선다.**

100%를 보여주고 나서 몇 초~몇십 초를 더 기다리면
사용자는 **"멈췄다"** 고 느낀다. 이게 진행률 UI의 고전적 실패 유형이다.

**그래서 3단계로 나눈다:**
```javascript
// 1) 전송 중: 0~99%
var percent = Math.min(99, (e.loaded / e.total) * 100);

// 2) 전송 완료, 서버 처리 대기
xhr.upload.onload = function () {
  // 바이트는 다 넘겼지만 서버는 아직 파일을 받아 검사하는 중이다. 이 구간을
  // 100%로 놔두면 "막대가 멈췄는데 화면이 안 넘어간다"는 새로운 불안이 생긴다.
  setProgress(100);
  progressText.textContent = "서버에서 파일을 확인하는 중…";
};

// 3) 응답 도착
xhr.onload = function () { ... window.location.href = payload.redirect; };
```

**막대는 100%가 되지만 문구가 바뀐다** — "아직 뭔가 하고 있다"를 알린다.

**`e.lengthComputable`** — 전체 크기를 모를 때(chunked 전송 등)를 대비.
**진행률을 못 재면 못 잰다고 말한다.** 여기서도 정직함이 원칙이다.

### 4-6. **남은 시간 추정 — 지수이동평균**

```javascript
var startedAt = Date.now();
var lastAt = startedAt;
var lastLoaded = 0;
var rate = 0; // bytes/sec, 지수이동평균

// onprogress 안에서:
var now = Date.now();
var elapsed = (now - lastAt) / 1000;
if (elapsed >= 0.3) {
  var sample = (e.loaded - lastLoaded) / elapsed;
  rate = rate ? rate * 0.7 + sample * 0.3 : sample;
  lastAt = now;
  lastLoaded = e.loaded;
}

var text = Math.floor(percent) + "%  ·  " + formatBytes(e.loaded) + " / " + formatBytes(e.total);
// 시작 직후의 표본은 심하게 튄다("약 4시간 남음"). 1초는 지나고 나서 보여준다.
if (rate > 0 && now - startedAt > 1000) {
  text += "  ·  " + formatBytes(rate) + "/s  ·  " + remainingLabel((e.total - e.loaded) / rate);
}
```

**[쉬움]**
방금 1초 동안의 속도만 보면 숫자가 계속 요동친다.
**"지금까지의 평균"과 "방금 속도"를 섞어서** 부드럽게 만든다.

**[전공] 지수이동평균(EMA, Exponential Moving Average)**

```
rate_new = rate_old × 0.7 + sample × 0.3
```

- `α = 0.3` (새 표본의 가중치). 클수록 민감, 작을수록 부드러움
- 과거 값이 **지수적으로 감쇠**한다: 직전 표본 30%, 그 전 21%, 그 전 14.7%…
- **전체 이력을 저장할 필요가 없다** — 값 하나만 들고 있으면 된다 (O(1) 메모리)

**같은 기법이 쓰이는 곳**: TCP RTT 추정(RFC 6298), 시스템 로드 애버리지,
주가 이동평균, 그래디언트 최적화(Adam의 모멘텀).

**세 가지 안정화 장치:**

1. **표본 최소 간격 `elapsed >= 0.3`**
   `onprogress`는 초당 수십 번 불릴 수 있다. 간격이 짧으면 분모가 작아 속도가 튄다

2. **초기 1초 무시 `now - startedAt > 1000`**
   ```javascript
   // 시작 직후의 표본은 심하게 튄다("약 4시간 남음"). 1초는 지나고 나서 보여준다.
   ```
   TCP slow start 때문에 초반 속도가 실제보다 훨씬 낮다.
   **"약 4시간 남음"을 본 사용자는 그냥 창을 닫는다.**

3. **표시 단위 뭉개기**
   ```javascript
   function remainingLabel(seconds) {
     // 초 단위를 그대로 흘리면 숫자가 계속 튀어 오히려 불안해 보인다. 뭉뚱그린다.
     if (seconds < 5) return "거의 다 됐습니다";
     if (seconds < 60) return "약 " + (Math.ceil(seconds / 5) * 5) + "초 남음";
     if (seconds < 3600) return "약 " + Math.ceil(seconds / 60) + "분 남음";
     return "1시간 이상 남음";
   }
   ```
   `Math.ceil(seconds / 5) * 5` — **5초 단위로 반올림**.
   `47초 → 45초 → 43초` 처럼 흔들리는 대신 `45초 → 45초 → 40초`.

> **[전공] "정확한 숫자"보다 "안정된 숫자"가 신뢰를 준다.**
> 이건 UI 설계의 일반 원칙이다. 계속 바뀌는 숫자는 읽을 수 없고, 읽을 수 없으면 불안하다.

### 4-7. `beforeunload` — 실수로 나가는 것을 막기

```javascript
// 업로드 중 새로고침·뒤로가기 한 번에 수 분치 전송이 날아가는 것을 막는다.
window.addEventListener("beforeunload", function (event) {
  if (!uploading) return;
  event.preventDefault();
  event.returnValue = "";
  return "";
});
```

**[전공]**
브라우저가 "이 페이지를 나가시겠습니까?" 대화상자를 띄운다.
**메시지 내용은 커스터마이즈할 수 없다** — 피싱 방지를 위해 브라우저가 고정 문구를 쓴다.
(`event.returnValue = ""` 는 구형 브라우저 호환용 관례)

**`if (!uploading) return;`** 이 중요하다.
업로드 중이 아닐 때도 물어보면 **매번 짜증나는 사이트**가 된다.

**성공 시 플래그를 먼저 끈다:**
```javascript
if (xhr.status >= 200 && xhr.status < 300 && payload && payload.ok) {
  uploading = false; // 이탈 경고를 끄고 이동한다
  progressText.textContent = "제출이 접수되었습니다. 화면을 이동합니다…";
  window.location.href = payload.redirect || "/submit";
```
**안 끄면 성공했는데도 "나가시겠습니까?"가 뜬다.** 세심한 처리다.

### 4-8. 취소 시 파일을 남겨두는 배려

```javascript
function leaveUploadingState() {
  uploading = false;
  submitButton.disabled = false;
  submitButton.textContent = "제출하기";
  if (cancelButton) cancelButton.hidden = true;
  if (progressBox) progressBox.hidden = true;
  // 파일 입력은 건드리지 않는다 — 고른 파일이 남아 있어야 버튼 한 번으로 재시도된다.
  fileInput.disabled = false;
  setProgress(0);
}
```

```javascript
xhr.onabort = function () {
  leaveUploadingState();
  showError("업로드를 취소했습니다. 파일은 그대로 선택되어 있으니 다시 제출할 수 있습니다.");
};
```

**[쉬움]** 취소했다고 고른 파일까지 지워버리면, 다시 파일 탐색기를 열어 찾아야 한다.

**[전공]**
`<input type="file">`의 값은 **보안상 JS로 설정할 수 없다**(파일 경로 조작 방지).
한 번 초기화하면 **되돌릴 방법이 없다.** 그래서 건드리지 않는다.

`fileInput.disabled = true` 는 업로드 중 변경만 막고, 끝나면 `false`로 되돌린다.
**`disabled`는 값을 지우지 않는다.**

---

## 5. **응답 형식 협상 — `wants_json_response`**

```python
def wants_json_response(accept_header: str | None) -> bool:
    """이 요청에 JSON으로 답해야 하는가 (upload-progress-ux.md §2-4).

    진행률을 표시하는 업로드 스크립트는 `Accept: application/json`을 명시해서 보낸다.
    그 외(일반 폼 전송, `*/*`만 보내는 클라이언트)는 지금까지와 똑같이 303 리다이렉트를
    받는다 — 스크립트가 없어도 제출이 되어야 하기 때문이다.
    """
    if not accept_header:
        return False
    return any(
        part.split(";", 1)[0].strip().lower() == "application/json"
        for part in accept_header.split(",")
    )
```

### 무엇을(What)

**[쉬움]**
같은 창구에서 **한국어로 물으면 한국어로, 영어로 물으면 영어로** 답한다.
스크립트가 "JSON으로 주세요"라고 하면 JSON, 아무 말 없으면 원래대로 화면 이동.

**[전공] 콘텐츠 협상(Content Negotiation)**

HTTP의 `Accept` 헤더는 클라이언트가 원하는 응답 형식을 알린다:
```
Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8
                                                        ^^^^^ 품질(quality) 값
```

### 어떻게(How) — 파싱을 왜 이렇게 하나

```python
part.split(";", 1)[0].strip().lower() == "application/json"
```

한 조각이 `application/json;q=0.9` 처럼 올 수 있다.
`;` 앞부분만 잘라 미디어 타입을 얻는다.

**`;` 를 최대 1번만 자른다(`split(";", 1)`)** — 뒤에 파라미터가 여러 개여도 첫 조각만 필요하다.

**엄격한 정확 일치(`==`)를 쓴다.** `in` 이 아니다:
```python
"application/json" in "application/jsonp"     # True — 틀렸다!
"application/jsonp" == "application/json"     # False — 옳다
```

**`*/*` 는 왜 False인가?**

브라우저의 일반 폼 전송은 `Accept: text/html,...,*/*;q=0.8` 을 보낸다.
`*/*` 는 "아무거나"라는 뜻이지 "JSON을 원한다"가 아니다.

**만약 `*/*` 를 JSON으로 취급했다면?**
→ **모든 브라우저 폼 전송이 JSON을 받게 되어** 화면에 `{"ok": true, ...}` 가 뜬다.
**서비스가 통째로 망가진다.**

> **[전공] 이것이 "명시적 옵트인(explicit opt-in)" 설계다.**
> 새 동작은 **명시적으로 요청한 클라이언트에게만** 준다.
> 기본 동작은 절대 바뀌지 않는다 → **하위 호환이 보장된다.**

### 두 응답 경로

```python
def redirect_with_error(message: str) -> JSONResponse | RedirectResponse:
    # 스크립트로 올린 경우엔 화면을 갈아끼우지 않고 그 자리에서 안내한다.
    # 리다이렉트로 답하면 고른 파일이 사라져 참가자가 처음부터 다시 해야 한다.
    if as_json:
        return JSONResponse({"ok": False, "error": message}, status_code=400)
    return RedirectResponse(f"/submit?error={message}", status_code=303)
```

```python
if as_json:
    return JSONResponse({"ok": True, "redirect": "/submit"})
return RedirectResponse("/submit", status_code=303)
```

**왜 JSON 경로에서는 리다이렉트를 안 하나?**

`XMLHttpRequest`는 **3xx를 자동으로 따라간다.** JS가 개입할 수 없다.
그러면 `xhr.responseText`에 `/submit` 페이지의 HTML이 통째로 들어오고,
`JSON.parse`가 실패한다.

**그리고 더 중요한 이유** — 주석이 말한다:
> 리다이렉트로 답하면 고른 파일이 사라져 참가자가 처음부터 다시 해야 한다.

에러일 때 화면이 새로 그려지면 `<input type="file">`이 초기화된다.
JSON으로 답하면 **화면은 그대로 두고 에러 메시지만 표시**할 수 있다.

**성공 시에도 서버가 목적지를 알려준다:**
```javascript
window.location.href = payload.redirect || "/submit";
```
`|| "/submit"` 은 방어. **서버가 경로를 정하고 클라이언트가 따른다.**

### 클라이언트의 응답 처리 — 3갈래

```javascript
xhr.onload = function () {
  var payload = null;
  try { payload = JSON.parse(xhr.responseText); } catch (err) { payload = null; }

  if (xhr.status >= 200 && xhr.status < 300 && payload && payload.ok) {
    uploading = false;
    window.location.href = payload.redirect || "/submit";
    return;
  }
  if (payload && payload.error) {
    fail(payload.error);
    return;
  }
  // JSON이 아니면 대개 로그인 화면(세션 만료)으로 리다이렉트된 경우다.
  fail("제출에 실패했습니다. 로그인이 만료되었을 수 있으니 페이지를 새로고침한 뒤 다시 시도해주세요.");
};
```

**세 번째 갈래가 중요하다.**

세션이 만료되면 `get_current_team`이 **303 → `/login`** 을 던진다(3단계).
`XMLHttpRequest`가 그걸 따라가서 **로그인 HTML**을 받는다 → `JSON.parse` 실패.

**"JSON이 아니면 인증 문제일 가능성이 높다"** 는 추론으로 유용한 안내를 준다.
250MB를 올린 뒤 "알 수 없는 오류"만 보는 것보다 훨씬 낫다.

**`xhr.onerror`(네트워크 끊김), `xhr.ontimeout`(시간 초과)도 각각 다른 문구를 준다.**
> **실패 유형마다 다른 안내를 주는 것이 좋은 에러 처리다.**

---

## 6. **POST-Redirect-GET — 새로고침 문제**

### 문제

**[쉬움]**
주문하고 나서 화면에 "주문 완료"가 떴다. 여기서 **F5를 누르면?**
브라우저가 "아까 그 주문을 다시 보낼까요?" 하고 묻거나, 그냥 다시 보낸다.
→ **똑같은 주문이 두 번 들어간다.**

**[전공]**
POST 응답을 직접 렌더링하면, 브라우저 히스토리에서 그 페이지의 상태가 "POST"다.
- F5 → 폼 재전송 경고
- 뒤로가기 → 같은 문제
- 북마크 → 동작하지 않음

### 해결: PRG 패턴

```
POST /submit  →  303 See Other + Location: /submit
                        ↓ 브라우저가 자동으로
                 GET /submit  →  200 OK + HTML
```

브라우저 히스토리에는 **마지막 GET만** 남는다.

**JSON 경로도 결과적으로 같다:**
```javascript
window.location.href = payload.redirect;   // JS가 GET으로 이동
```
**메커니즘은 다르지만 효과는 동일하다** — 최종 상태가 GET이다.

### 왜 303인가 (302가 아니라)

| | POST 후 리다이렉트 시 |
|---|---|
| 302 | 명세상 메서드 유지, **실제 브라우저는 GET으로 변환**(역사적 관행) — 모호 |
| **303** | **반드시 GET으로 변환** — 명확 |
| 307 | **POST를 유지** → `/submit`에 다시 POST → **무한 루프** |

**307을 쓰면 실제로 망가진다.** 303이 유일하게 정확한 선택이다.

### 쿼리 파라미터로 에러를 넘기는 방식의 장단점

```python
return RedirectResponse(f"/submit?error={message}", status_code=303)
```
```python
"error": request.query_params.get("error"),
```

**장점**: 상태를 서버에 저장할 필요가 없다. 무상태 유지.

**단점 3가지:**

1. **URL이 지저분해진다.** 한글이 퍼센트 인코딩된다
   (Starlette `RedirectResponse`가 `quote()`로 자동 처리하므로 **깨지지는 않는다**)

2. **누구나 임의 메시지를 띄울 수 있다.**
   `/submit?error=계정이 정지되었습니다. 010-xxxx로 연락주세요` 링크를 배포하면
   **우리 사이트가 그 메시지를 표시**한다. 피싱에 악용 가능.

   Jinja2 자동 이스케이프 덕에 `<script>`는 실행되지 않는다(5단계).
   **하지만 텍스트 자체가 신뢰를 주는 것이 문제다.**

   → 정석: 에러를 **코드**로 넘기고 서버가 문구를 결정한다

3. **새로고침해도 에러가 계속 보인다.** URL에 남아 있으므로
   → 정석: **플래시 메시지**(세션에 한 번 넣고 읽으면 삭제)

> **JS 경로에서는 이 문제가 전부 사라진다** — URL을 안 건드리고 화면에 직접 표시한다.
> **JS를 쓰는 사용자가 다수라면 실질적 위험은 줄어들지만, 취약점 자체는 남아 있다.**

---

## 7. `quota.py` — 타임존과 하루의 경계

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

### 7-1. 왜 타임존이 어려운가

**[쉬움]**
서버는 세계 표준시(UTC)로 시간을 잰다. 한국은 그보다 9시간 빠르다.
"7월 26일"이 서버에서는 7월 25일 15:00 ~ 7월 26일 15:00 이다.

**[전공] naive vs aware**

```python
dt.datetime(2026, 7, 26, 0, 0)                    # naive — "어디의" 0시인지 모름
dt.datetime(2026, 7, 26, 0, 0, tzinfo=KST)        # aware — 한국 0시 = UTC 7/25 15:00
```

naive와 aware를 비교하면 파이썬은 `TypeError`를 낸다. **다행이다** — 조용히 틀리는 것보다 낫다.
하지만 SQL에서는 조용히 틀린다. 그래서 `DateTime(timezone=True)`가 2단계에서 중요했다.

**클라우드 서버는 UTC, 운영자 노트북은 KST일 가능성이 높다.**
→ **이 규칙이 원격 워커 구조에서 훨씬 중요해졌다.**

### 7-2. 하루 경계 계산 — 반열린 구간

```python
Submission.finished_at >= day_start,
Submission.finished_at <  day_end,
```

**왜 `<=` day_end가 아니라 `<` day_end인가?**
`<=`면 자정 정각에 끝난 제출이 **어제와 오늘 양쪽에 카운트**된다.
반열린 구간 `[start, end)` 은 이 중복을 원천 차단한다.

**왜 `DATE(finished_at) = '2026-07-26'`을 안 쓰나?**
컬럼에 함수를 씌우면 **인덱스를 못 쓴다**(sargable하지 않음).
범위 비교는 인덱스를 탄다. 지금 규모에선 차이가 없지만 **습관이 중요하다.**

### 7-3. **`finished_at` 기준인 것의 의미**

카운트 기준이 `submitted_at`이 아니라 **`finished_at`(완료 시각)** 이다.

**시나리오:**
```
23:55  팀A 제출         → submitted_at = 7/26 23:55
00:05  평가 완료         → finished_at  = 7/27 00:05
```
→ 이 제출은 **7월 27일** 카운트에 들어간다.

**찬성 논리**: spec의 정의가 "평가가 끝까지 정상 실행됐는지"다.
`status == DONE`은 `finished_at`이 채워지는 순간 확정되므로 **상태와 시각의 기준이 일치**한다.

**허점**: 자정 근처에 한도를 살짝 넘겨 쓸 수 있다.
동시 제출 1건 제약 때문에 크게 악용되진 않는다(10분에 1건).
**하지만 이건 알고 있어야 할 규칙의 틈이다.**

> **평가 서버가 멈춰 있으면 이 틈이 커진다.**
> 밤새 워커가 꺼져 있다가 아침에 켜지면, 밤에 제출된 것들이 **전부 아침 날짜로** 카운트된다.
> → 그날 오전에 이미 한도를 소진한 상태가 된다.
> **운영자가 알고 있어야 할 부작용이다.**

### 7-4. 보정 델타 로직

```python
if team.daily_count_adjustment is not None and team.daily_count_adjustment_date == on_date:
    done_count += team.daily_count_adjustment
return max(done_count, 0)
```

**날짜 검사가 왜 필요한가?** 보정값에 유효기간이 없으면 어제 넣은 보정이 오늘까지 따라온다.

**`max(done_count, 0)`가 왜 필요한가?**
관리자가 카운트를 0으로 지정하면 `adjustment`가 음수가 된다.
합계가 음수가 되면 `remaining = 5 - (-2) = 7` → **한도를 넘겨 제출 가능.** 이걸 막는다.

**방어적 프로그래밍의 정석**: "이론상 안 일어나야 하는데, 일어나면 안전한 쪽으로."

### 7-5. `has_active_submission` — 조회이면서 검증

```python
def has_active_submission(db: Session, team: Team) -> Submission | None:
    stmt = select(Submission).where(
        Submission.team_id == team.id,
        Submission.status.in_(ACTIVE_SUBMISSION_STATUSES),
    )
    return db.execute(stmt).scalar_one_or_none()
```

**`scalar_one_or_none()`을 쓴 것에 주목하라.** 2건 이상이면 **예외가 터진다.**
그런데 부분 유니크 인덱스가 2건을 막는다.

→ **인덱스가 깨졌다면 여기서 시끄럽게 실패한다.** 조용히 첫 번째를 쓰는(`first()`) 것보다 낫다.
**"불변식(invariant)이 깨지면 즉시 알려라"** 는 원칙의 좋은 예다.

`ACTIVE_SUBMISSION_STATUSES`는 `.value`(문자열) 튜플이라
SQL(`.in_()`)과 파이썬(`retention.py`의 `in` 비교) 양쪽에서 재사용된다.

---

## 8. `storage_paths.py` — 실제 장애에서 태어난 모듈

### 문제가 무엇이었나

```
웹 (컨테이너 안)                  워커 (호스트)
/app/storage/models/1/6/x.tar.gz  ↔  /mnt/c/.../storage/models/1/6/x.tar.gz
        ↑ 같은 파일인데 경로가 다르다
```

`docker-compose.yml`: `- ./storage:/app/storage` (**bind mount**)
같은 디스크 영역이지만 **경로 이름이 완전히 다르다.**

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
- `.as_posix()`: **항상 `/` 구분자**로 만든다
- `except ValueError`: storage_dir 밖의 경로면 변환하지 않고 그대로

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

`_reroot`는 **마지막 `storage` 세그먼트 뒤**를 잘라 현재 환경의 루트에 붙인다:
```
/app/storage/models/1/6/x.tar.gz
→ ["app","storage","models","1","6","x.tar.gz"]
→ 마지막 "storage" 이후: ["models","1","6","x.tar.gz"]
→ settings.storage_dir / "models/1/6/x.tar.gz" ✅
```

**왜 "마지막" storage인가?** `/home/user/storage/app/storage/models/...` 처럼
`storage`가 여러 번 나올 수 있다. `parts[::-1].index(...)`로 뒤에서부터 찾는다.

### `_split`의 방어

```python
def _split(raw: str) -> list[str]:
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
    if ".." in parts:
        raise ValueError(f"상위 디렉터리 참조가 포함된 경로는 허용하지 않습니다: {raw}")
    return parts
```

1. `\` → `/` 통일 (컨테이너가 만든 경로를 Windows에서 읽는 경우)
2. 빈 조각과 `.` 제거
3. **`..` 발견 시 예외** ← **경로 순회 방어**

**§3-7에서 본 파일명 문제가 여기서 부분적으로 막힌다.**
다만 `to_storage_relative`(쓸 때)는 이 검사를 안 한다 — **입구에서 막는 게 더 나은 설계다.**

### `_looks_absolute` — 크로스 플랫폼

```python
def _looks_absolute(raw: str) -> bool:
    return raw.startswith("/") or raw.startswith("\\") or (len(raw) > 1 and raw[1] == ":")
```

`Path(raw).is_absolute()`를 안 쓴 이유: **파이썬이 실행 중인 OS 기준으로만 판단**하기 때문.
Linux에서 `Path("C:/x").is_absolute()` 는 `False`다.

### **http 모드에서 이 모듈이 여전히 쓰이는 곳**

워커가 원격이면 파일을 HTTP로 받으므로 경로 문제가 없어 보인다. 그런데:

```python
# app/routers/internal.py:51 — 서버 쪽에서
path = resolve_storage_path(submission.model_path)
if not path.is_file():
    raise NOT_FOUND
return FileResponse(path, ...)

# worker/transfer.py:48 — local 모드에서
path = resolve_storage_path(stored_path)
```

**local 모드에서는 워커가 직접 읽고, http 모드에서는 웹이 읽어 내려준다.**
어느 쪽이든 **경로 해석 규칙은 같은 함수**를 쓴다.
→ 배포 형태가 바뀌어도 이 모듈은 그대로다. **좋은 추상화의 증거다.**

---

## 9. 자가 점검 질문

**HTTP·업로드**
1. GET이 "safe"해야 한다는 게 무슨 뜻인가? 깨지면 어떤 실제 사고가 나는가?
2. `bytes = File(...)` 대신 `UploadFile`을 쓰는 이유는? `mem_limit: 900m`과 어떻게 연결되는가?
3. 청크 단위로 읽으면서 크기를 검사하는 것이 주는 이득 3가지는?
4. `ClientDisconnect`는 언제 발생하는가? 왜 정리하고 **다시 던지는가**?
5. `with open(...)` 안에서 예외가 나면 파일 핸들과 파일 자체는 각각 어떻게 되는가?
6. 검증 순서를 "싼 것부터"로 하는 이유를 구체적 낭비량으로 설명하라.
7. 확장자 검사는 보안 검사인가? 아니라면 진짜 방어는 어디에 있는가?
8. `filename="../../evil.zip"` 을 보내면 실제로 무슨 일이 일어나는가? 왜 그렇게 되는가?
9. 파일 저장과 DB INSERT의 순서가 지금과 반대라면 어떤 문제가 더 심각해지는가?
10. `internal.py`가 워커가 보낸 경로를 안 쓰고 직접 조립하는 이유는?

**upload.js·점진적 향상**
11. 점진적 향상이란? 이 스크립트가 그 원칙을 지키는 코드 위치 3곳은?
12. `fetch` 대신 `XMLHttpRequest`를 쓴 결정적 이유는?
13. 진행률을 99%에서 멈추는 이유는? 100%로 두면 어떤 새로운 불안이 생기는가?
14. 지수이동평균 공식과 `α = 0.3`의 의미는? 표본을 안정화하는 장치 3가지는?
15. 시작 후 1초간 남은 시간을 안 보여주는 이유는?
16. `beforeunload`에서 `if (!uploading) return;` 이 없으면 무슨 일이 생기는가?
17. 취소 시 `fileInput`을 초기화하지 않는 이유는? JS로 되돌릴 수 있는가?
18. 클라이언트 검사와 서버 검사가 완전히 중복인데 둘 다 필요한 이유는?
19. `data-max-bytes` 로 값을 내려보내는 이유는? 하드코딩하면 어떤 어긋남이 생기는가?

**응답 협상**
20. `Accept: */*` 를 JSON으로 취급하면 무슨 일이 일어나는가?
21. `in` 대신 `==` 로 미디어 타입을 비교하는 이유는?
22. JSON 경로에서 리다이렉트를 안 하는 이유 2가지는?
23. `xhr.onload`의 세 번째 갈래(JSON 파싱 실패)는 어떤 상황을 다루는가?
24. "명시적 옵트인"이 하위 호환을 어떻게 보장하는가?

**PRG·타임존·경로**
25. PRG 패턴이 없으면 F5를 눌렀을 때 무슨 일이 생기는가? 307이면?
26. 에러를 쿼리 파라미터로 넘기는 방식의 문제 3가지는? JS 경로에서는 왜 사라지는가?
27. `day_end`를 `<=`가 아니라 `<`로 비교하는 이유는?
28. 하루 카운트가 `finished_at` 기준인 것의 장점과 허점은? 워커가 밤에 꺼져 있으면?
29. `max(done_count, 0)`이 막는 시나리오는?
30. `has_active_submission`이 `first()`가 아니라 `scalar_one_or_none()`인 것의 의미는?
31. 왜 DB에 절대 경로를 저장하면 안 되는가? `_reroot`가 "마지막" storage를 찾는 이유는?
32. http 모드에서도 `resolve_storage_path`가 필요한 이유는?

---

## 10. 실험 과제

**실험 A — 청크 읽기와 조기 종료**
`model_upload_max_bytes`를 10MB로 낮추고 50MB 파일을 올려보라.
```python
while chunk := await model_file.read(1024 * 1024):
    size += len(chunk)
    print(f"읽음 {size / 1024 / 1024:.1f}MB")
```
**50MB를 다 읽는가, 10MB에서 멈추는가?**

**실험 B — 업로드 취소로 `ClientDisconnect` 재현**
큰 파일 업로드를 시작하고 "업로드 취소"를 누른 뒤:
```bash
ls -la storage/models/*/*/    # 반쪽 파일이 남아 있는가?
docker compose logs web | tail -20
```
`except ClientDisconnect` 블록을 주석 처리하고 다시 해보라. 차이를 확인 후 복구.

**실험 C — 진행률 UI 관찰**
브라우저 개발자도구 Network 탭에서 **Throttling을 "Slow 3G"** 로 놓고 업로드하라.
- 남은 시간이 어떻게 변하는가?
- 초반 1초 동안 안 보이는가?
- 99%에서 멈추고 "서버에서 파일을 확인하는 중…"이 뜨는가?

**실험 D — 점진적 향상 확인**
개발자도구 → Settings → Debugger → **"Disable JavaScript"** 체크 후 제출해보라.
**제출이 되는가?** 진행률만 안 보이고 정상 동작해야 한다.
그다음 `upload.js`를 일부러 깨뜨리고(`throw new Error("x")`를 맨 앞에) 다시 해보라.

**실험 E — 응답 협상 직접 확인**
```bash
# 일반 폼 전송 흉내 — 303이 나와야 한다
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/submit \
  -H "Cookie: session=<로그인쿠키>" -F "model_file=@bad.txt"

# 스크립트 흉내 — 400 + JSON이 나와야 한다
curl -s -X POST http://localhost:8000/submit \
  -H "Cookie: session=<로그인쿠키>" -H "Accept: application/json" -F "model_file=@bad.txt"
```
```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_upload_response_mode.py -v
```
테스트가 어떤 `Accept` 값들을 검증하는지 읽어보라.

**실험 F — 경로 순회 시도**
```bash
curl -X POST http://localhost:8000/submit \
  -H "Cookie: session=<로그인쿠키>" \
  -F "model_file=@some.zip;filename=../../../../tmp/evil.zip" -v
```
실제로 어디에 저장되는가? 에러가 나는가? **왜 그런지 설명할 수 있어야 한다.**

**실험 G — 타임존 경계 검증**
```python
import datetime as dt
from zoneinfo import ZoneInfo
KST = ZoneInfo("Asia/Seoul")

d = dt.date(2026, 7, 26)
start = dt.datetime.combine(d, dt.time.min, tzinfo=KST)
print(start)                              # 2026-07-26 00:00:00+09:00
print(start.astimezone(dt.timezone.utc))  # 2026-07-25 15:00:00+00:00  ← UTC로는 전날!
```
DB의 `finished_at`을 psql로 보고 어느 날짜로 카운트되는지 손으로 계산해보라.

**실험 H — 경로 유틸 테스트 읽고 추가하기**
```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_storage_paths.py -v
```
케이스를 하나 추가해보라 (예: `\\` 구분자, `..` 포함 경로).

---

→ 다음: [05-leaderboard.md](05-leaderboard.md) — 조회, 정렬, 템플릿, 그리고 XSS
