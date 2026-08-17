# 8단계. 전체를 관통하는 주제 — 흩어진 코드를 하나로 묶기

> 1~7단계는 **파일별**로 내려갔다. 이 문서는 **주제별로 가로지른다.**
> 같은 개념이 여러 파일에 어떻게 나타나는지 보면, 코드가 하나의 시스템으로 보이기 시작한다.

---

## 1. 시간 — 이 시스템은 시간을 어떻게 다루는가

### 시간이 등장하는 모든 곳

| 위치 | 값 | 시계 종류 | 누가 만드나 |
|---|---|---|---|
| `Submission.submitted_at` | 제출 시각 | UTC (aware) | **DB** (`server_default=func.now()`) |
| `Submission.started_at` | 평가 시작 | UTC | **DB** (`now()` in raw SQL) |
| `Submission.finished_at` | 평가 종료 | UTC | **파이썬(워커)** (`now_utc()`) |
| `WorkerHeartbeat.last_seen_at` | 워커 생존 | UTC | **파이썬(워커)** |
| `EvaluationResult.completed_at` | 결과 저장 | UTC | **DB** |
| `Team.daily_count_adjustment_date` | 보정 유효일 | **KST 날짜** | 파이썬 (`today_kst()`) |
| 업로드 파일명 접두사 | `20260726T120000` | UTC | 파이썬(웹) |
| 하루 한도 경계 | KST 자정 | **KST** | 파이썬 (`quota.py`) |
| 화면에 찍는 제출 시각 | `2026-08-18 08:59` | **KST** | 파이썬 (`render.py`의 `kst` 필터) |
| S3 `LastModified` | 오브젝트 갱신 | UTC | **MinIO** |
| `admin_lockout.locked_until` | 잠금 해제 시각 | **monotonic** | 파이썬(웹) |
| `upload.js`의 속도 계산 | 경과 시간 | 브라우저 `Date.now()` | 클라이언트 |

### 규칙 4개

**1. 저장·공유는 UTC(aware), 표시와 비즈니스 규칙은 KST.**
```python
KST = ZoneInfo("Asia/Seoul")                                # config.py
def now_utc(): return dt.datetime.now(tz=dt.timezone.utc)   # worker/run.py
def today_kst(): return dt.datetime.now(tz=KST).date()      # quota.py
def kst(value, fmt="%Y-%m-%d %H:%M"):                       # render.py (표시)
    return value.astimezone(KST).strftime(fmt)
```

> **"표시는 KST"를 한동안 지키지 못했다.** 템플릿이 `submitted_at.strftime(...)`을
> 직접 호출해, `TIMESTAMPTZ`가 psycopg2를 거쳐 **DB 세션 시간대(UTC)** 로 돌아온 값을
> 그대로 찍었다. 값은 맞는데 화면만 9시간 일렀다 (2026-08-18). 저장이 aware라고 해서
> 표시가 저절로 맞지는 않는다 — **변환은 표시하는 쪽이 직접 해야 한다.**
> 자세한 내용과 `TZ` 환경변수를 쓰지 않은 이유는 [1단계 §5](01-skeleton.md)에 있다.

**2. 모든 datetime은 aware.**
`DateTime(timezone=True)` → PostgreSQL `TIMESTAMPTZ`.
naive와 aware를 섞으면 파이썬은 `TypeError`, SQL은 **조용한 오답**.

**3. 하루 경계는 반열린 구간 `[start, end)`.**

**4. 경과 시간(duration)은 monotonic 시계로.**
```python
# app/admin_lockout.py
# 0이면 잠금 없음. time.monotonic() 기준이라 시스템 시계를 바꿔도 영향받지 않는다.
locked_until: float = 0.0
```

### 왜 4번이 따로 필요한가

| | 벽시계 (`time.time`, `datetime.now`) | monotonic (`time.monotonic`) |
|---|---|---|
| 기준 | 1970-01-01 | 임의 시점 (프로세스/부팅) |
| NTP 동기화 | **값이 점프한다** | 영향 없음 |
| 저장·공유 | ✅ 다른 프로세스가 읽을 수 있다 | ❌ 기준점이 프로세스마다 다름 |
| 경과 측정 | ⚠️ 시계가 뒤로 가면 음수 | ✅ 절대 감소하지 않음 |

**이 프로젝트가 정확히 구분한다:**
- `admin_lockout` (한 프로세스 안에서 경과만 잼) → **monotonic**
- `worker_heartbeats` (워커가 쓰고 웹이 읽음) → **벽시계 UTC**

> **철칙: 저장·공유는 벽시계, 경과 측정은 monotonic.**

### 이 시스템에는 시계가 5개 있다

1. PostgreSQL 서버 (Lightsail)
2. 웹 컨테이너의 파이썬
3. **평가 서버의 파이썬 (다른 기기!)**
4. MinIO 서버 (평가 서버 안)
5. 참가자 브라우저

**`server_default=func.now()`를 쓴 이유가 여기 있다** — 1번 시계로 통일하기 위해.
하지만 `finished_at`과 `last_seen_at`은 **3번 시계**가 만든다.

**평가 서버 시계가 5분 빠르면?**
```python
# app/worker_status.py
elapsed = now - last_seen        # now는 웹(1·2번), last_seen은 워커(3번)
```
`elapsed`가 **-5분**이 되어 `max(..., 0)`이 0으로 만든다 → "0분 전"으로 표시.
`online` 판정은 여전히 True (음수 ≤ 3분). **다행히 안전한 방향으로 틀린다.**

**반대로 5분 느리면?** 항상 5분 전으로 보여 **3분 임계값을 넘어 "중지"로 오판**한다.

> **[전공] NTP 동기화가 인프라 요구사항이 되는 순간이다.**
> EC2/Lightsail은 기본적으로 시간 동기화가 켜져 있지만,
> **노트북을 워커로 쓰면(절전 후 복귀 등) 어긋날 수 있다.**
> `S3 LastModified` 비교(`start_marker`)도 같은 위험을 갖는다.

> **일반 원칙: 여러 시계를 비교해야 한다면, 가능한 한 하나의 시계만 쓰도록 설계하라.**
> 불가능하면 **논리적 순서(시퀀스 번호, 버전)** 를 쓴다.

---

## 2. 실패 — 이 시스템의 실패 처리 철학

### 실패를 대하는 5가지 태도

| 전략 | 언제 | 코드 예시 |
|---|---|---|
| **즉시 크게 실패** (fail fast) | 설정 오류, 불변식 위반 | `run_worker.sh` 환경변수 검증, `${VAR:?}`, `scalar_one_or_none` |
| **격리하고 계속** | 한 건의 실패가 전체를 멈추면 안 될 때 | 워커 루프의 `except Exception` |
| **되돌리고 재시도** | 일시적 인프라 장애 | `TransferError` → `queued` |
| **조용히 넘어감** (best-effort) | 부가 기능 | `download_video`, `deliver_video`, `request_prune`, `rmtree(ignore_errors=True)` |
| **방어적 기본값** | 데이터가 이상해도 화면은 떠야 할 때 | `get_open_season`의 `limit(1)`, `max(count, 0)`, `verify_password`의 `except ValueError` |

### 판단 기준 — 4가지 질문

**Q1. 누가 이 실패를 보는가?**
- 운영자 → 시끄럽게 실패해도 된다. 오히려 그래야 안다
- 참가자 → 이해할 수 있는 메시지로. 다른 사람에게 영향 없게

**Q2. 계속 진행하면 데이터가 오염되는가?**
- 오염 → **멈춰야 한다** (예: `start_marker` 없이 이전 결과를 저장하면 대회가 망가진다)
- 오염 없음 → 계속 (예: 영상 없어도 랩타임은 맞다)

**Q3. 이 실패가 예상된 것인가?**
- 예상됨 → 도메인 예외 (`EvaluationError`), 사용자용 메시지
- 예상 못함 → 스택트레이스 로그 + 일반 메시지

**Q4. 다시 시도하면 될 일인가?** ← 클라우드 이관으로 새로 생긴 질문
- 일시적(네트워크) → **`queued`로 되돌려 자동 재시도**
- 영구적(파일이 깨짐) → `error`. 사람이 고쳐야 한다

### 실제 적용 예시 대조

**같은 "데이터가 2건이다" 상황, 정반대 처리:**
```python
# app/quota.py — 팀의 활성 제출이 2건이면?
return db.execute(stmt).scalar_one_or_none()      # → 예외! 불변식이 깨졌다

# app/routers/leaderboard.py — 진행중 시즌이 2개면?
select(Season).where(...).order_by(...).limit(1)  # → 하나 고른다. 화면은 떠야 한다
```

**같은 "미인증" 상황, 정반대 처리:**
```python
# get_current_team — 303으로 /login 리다이렉트 (공개 로그인 화면)
raise HTTPException(status_code=303, headers={"Location": "/login"})

# get_current_admin — 404 (은닉)
raise HTTPException(status_code=404)
```

**같은 "설정값이 없다" 상황, 층마다 다른 처리:**
```python
# app/config.py — 개발 편의: 안전한 기본값
if not value:
    return "/admin/login"
```
```yaml
# docker-compose.prod.yml — 운영 안전: 기동 실패
ADMIN_LOGIN_PATH: "${ADMIN_LOGIN_PATH:?...}"
```

> **[전공] "상황이 같아도 맥락이 다르면 답이 다르다."**
> 이걸 판단할 수 있는 것이 설계 능력이다.

### 오류 메시지 품질 — 3단계

**최상 — 무엇/왜/어떻게가 다 있다:**
```python
MINIO_RAW_FORMAT_HELP = (
    "MinIO의 내부 저장 폴더를 그대로 압축한 것으로 보입니다 "
    "(xl.meta / part.N 파일이 들어 있음). 이 형식은 평가에 사용할 수 없습니다.\n"
    "DRFC 환경에서 아래처럼 모델을 정상적으로 내보낸 뒤 그 폴더를 압축해 주세요:\n"
    "  aws s3 sync ...\n"
)
CHECKPOINT_MISSING_HELP = (...)   # 같은 3요소
```

**좋음 — 무엇과 어떻게가 있다:**
```javascript
"파일 용량이 너무 큽니다 (최대 500.0MB). 선택한 파일은 612.3MB입니다."
"제출에 실패했습니다. 로그인이 만료되었을 수 있으니 페이지를 새로고침한 뒤 다시 시도해주세요."
```

**보통 — 무엇만 있다:**
```python
"평가가 끝났지만 새 metrics 파일을 찾지 못했습니다."
```
참가자가 이걸 보고 할 수 있는 게 없다. **"운영자에게 문의하세요"라도 붙이면 낫다.**

> **좋은 에러 메시지 = 무엇이 / 왜 / 어떻게 고치는지.**
> 이건 코드 품질이 아니라 **제품 품질**의 영역이다.

---

## 3. 동시성 — 두 개 이상이 동시에 움직일 때

### 이 시스템의 동시성 지점 전체 목록

| # | 상황 | 위험 | 방어 |
|---|---|---|---|
| 1 | 같은 팀이 제출 버튼 더블클릭 | 큐에 2건 | **부분 유니크 인덱스** (+ 앱 체크 + JS 버튼 비활성) |
| 2 | 여러 워커가 같은 작업을 집음 | 중복 평가 | **`FOR UPDATE SKIP LOCKED`** |
| 3 | 여러 요청이 같은 세션을 씀 | 데이터 꼬임 | **요청당 세션 1개** (`get_db`) |
| 4 | **하트비트 스레드와 메인 루프** | 세션 공유 충돌 | **스레드마다 자기 세션** |
| 5 | 여러 워커가 S3 `model/`을 씀 | 모델 섞임 | **없음** (워커 1대 전제) |
| 6 | 여러 워커가 같은 영상 키를 씀 | 영상 덮어씀 | **없음** (워커 1대 전제) |
| 7 | 관리자가 팀 등록 중 다른 관리자도 등록 | 팀명 중복 | **유니크 제약** (+ 앱 사전 필터) |
| 8 | 웹 컨테이너 여러 개가 마이그레이션 | 스키마 충돌 | **없음** (인스턴스 1개 전제) |
| 9 | 워커가 평가 중인 파일을 retention이 삭제 | 파일 유실 | **`ACTIVE_SUBMISSION_STATUSES` 체크** |
| 10 | **uvicorn 워커 여러 개 + 잠금 카운터** | 잠금 무력화 | **없음** (`--workers` 미사용 전제) |
| 11 | 두 워커가 같은 `worker_id`로 첫 하트비트 | PK 충돌 | **없음** (예외를 삼키고 재시도) |

### 5, 6, 8, 10이 "없음"인 것의 의미

**이 시스템은 "웹 1개, 워커 1개"라는 전제 위에 서 있다.**

그 전제를 깨면:
- 워커 2대 → DRFC/S3 레벨에서 충돌 (6단계 §3-1)
- 웹 2개 → 마이그레이션 충돌 (7단계 §1-5)
- `--workers 4` → **잠금 카운터가 4벌로 쪼개져 실질 한도가 20회** (3단계 §7-6)

**[전공] 이건 결함이 아니라 명시된 제약이다.**
문제는 **그 제약이 코드 어디에도 강제되어 있지 않다**는 것.

**다만 세 곳이 주석으로 경고한다:**
```python
# app/admin_lockout.py
# 운영 웹은 컨테이너 1개·uvicorn 워커 1개로 돌아 (`Dockerfile`의 CMD에 `--workers`가 없다)
```
```python
# worker/run.py
# 워커는 지금 1대만 운영하기로 했지만(2026-07-24 논의) ...
```
```yaml
# docker-compose.yml
# 평가 워커(worker/)는 이 compose에 포함하지 않는다.
```

> **개선 아이디어**: PostgreSQL 어드바이저리 락(`pg_advisory_lock`)으로 워커를 1대로 강제하거나,
> 부팅 시 다른 활성 워커가 있으면 경고를 남긴다. **암묵적 전제는 언젠가 깨진다.**

### 배운 패턴 4개 — 다른 프로젝트에서도 쓸 것

**패턴 1. 다층 검사 — 각 층의 목적이 다르다**
```
JS 검사   = 최고의 UX (0바이트 전송)
앱 검사   = 친절한 안내 + 정책 강제
DB 제약   = 진짜 보장 (경쟁 조건에서도 안 깨짐)
인프라 제한 = 극단 방어 (DoS)
```
**전부 있어야 하고, 각각의 목적이 다르다.**

**패턴 2. TOCTOU를 원자적 연산으로 바꾸기**
```sql
-- 나쁨: 확인 후 사용 (사이에 틈이 있다)
SELECT ...; UPDATE ...;
-- 좋음: 하나의 문장
UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING id;
```

**패턴 3. 공짜에 가까운 미래 대비는 지금 하기**
`SKIP LOCKED`를 워커 1대일 때부터 쓴 것.
**"기능"을 미리 만드는 건 낭비지만, "정확성"은 나중에 넣기가 훨씬 어렵다.**

**패턴 4. 생명주기를 묶기**
```python
thread = threading.Thread(target=loop, daemon=True, name="heartbeat")
```
**신호를 보내는 주체와 일하는 주체가 같이 죽어야 신호가 거짓말을 안 한다.**

---

## 4. **은닉과 심층 방어 — 이 프로젝트의 보안 철학**

### 방어 층 전체

```
[1층] 은닉      — 자동화된 대량 시도를 없앤다
    · 비밀 경로 (ADMIN_LOGIN_PATH)
    · 미인증 /admin → 404 (403/401이 아니라)
    · 네비게이션 링크 조건부 표시
    · include_in_schema=False
    · /internal/* 인증 실패도 404

[2층] 잠금      — 표적 공격을 막는다
    · admin_lockout (IP + 아이디 이중 카운터)
    · 잠긴 동안 bcrypt를 아예 안 돌린다

[3층] 인증      — 뚫려도 못 들어온다
    · bcrypt cost 12 (250ms)
    · secrets 기반 10자리 비밀번호 (59비트)
    · HMAC 서명 쿠키
    · secrets.compare_digest (상수 시간)

[4층] 네트워크  — 애초에 도달을 막는다
    · Caddy만 인터넷에 노출 (web은 expose)
    · DB는 Tailscale IP에만 바인드
    · WORKER_TOKEN 없으면 /internal/* 자체가 닫힘

[5층] 배포 강제 — 설정 실수를 기동 실패로
    · ${VAR:?} 필수 환경변수
    · DB_BIND_ADDRESS 안전한 기본값
```

### **은닉의 원칙 — 반드시 이해할 것**

> "Security through obscurity"(숨김으로 지키기)는 **단독 방어책으로는 실패한다.**
> 주소는 언젠가 새어 나간다: 링크 공유, 브라우저 히스토리, 프록시 로그, 어깨너머.

**그럼에도 하는 이유:**
- 공개 인터넷의 `/admin/login`은 **자동 스캐너가 하루 수백 번 두드린다**
- 그 소음이 로그를 오염시키고, bcrypt로 CPU를 먹는다
- **은닉은 이 대량 자동화를 통째로 없앤다**

**은닉을 비판하는 말은 "은닉만 쓰지 마라"이지 "은닉을 쓰지 마라"가 아니다.**

### 404를 일관되게 쓰는 것

같은 논리가 **두 곳**에서 반복된다:

```python
# app/deps.py — 관리자
"""로그인 페이지로 보내주면 "이 경로에 관리자 페이지가 있다"는 사실이 확인된다.
404를 주면 존재하지 않는 아무 경로와 응답이 구별되지 않아 은닉이 성립한다."""
```
```python
# app/routers/internal.py — 워커 API
# 인증 실패와 "없는 제출"을 같은 응답으로 돌려준다 (정보 노출 방지).
NOT_FOUND = HTTPException(status_code=404, detail="Not Found")
```

| 응답 | 공격자가 알게 되는 것 |
|---|---|
| `303 → /login` | **"여기 있다"** + 로그인 주소까지 |
| `401` / `403` | "여기 뭔가 있는데 권한이 없다" |
| **`404`** | **아무것도.** 없는 경로와 구별 불가 |

**테스트가 이 등가성을 못 박는다:**
```python
assert 관리자.status_code == 아무거나.status_code == 404
assert 관리자.json() == 아무거나.json()      # ← 본문까지
```

### 은닉의 대가를 인정한 것

```python
"""**부작용은 의도된 것이다**: 세션이 만료된 관리자도 /admin에서 404를 보게 된다.
관리자는 .env에 설정한 비밀 경로로 다시 들어와야 한다."""
```

> **[전공] 좋은 설계 문서의 조건이다.**
> 장점만 적으면 나중에 그 대가를 만났을 때 "버그인가?" 하고 혼란스럽다.
> **"이건 의도한 불편이다"라고 적어두면 판단이 빨라진다.**

---

## 5. 단일 진실 공급원 — 같은 규칙이 두 곳에 있으면 반드시 어긋난다

### 이 프로젝트가 잘한 것

| 무엇 | 어디서 공유되나 |
|---|---|
| `get_team_best` | 리더보드 표시 + 파일 보존 정책 |
| `prune_team_files` | 워커(local) + 서버(`internal.py`) + 시즌 아카이브 |
| `ACTIVE_SUBMISSION_STATUSES` | `quota.py`(SQL) + `retention.py`(파이썬) |
| `settings.daily_submission_limit` | 검증 + 화면 표시 + 에러 메시지 |
| `settings.model_upload_max_bytes` | 서버 검증 + **HTML `data-*` → JS 검증** |
| `settings.admin_login_path` | 라우트 등록 + 폼 `action` |
| `storage_paths` | 웹 + 워커(local) + `internal.py` |
| `failure_summary` 필터 | `submit.html` + `leaderboard.html` |
| `_worker_status.html` | `submit.html` + `leaderboard.html` |
| 상태 정규화 (`.strip().lower().replace(" ","_")`) | metrics 경로 + 로그 파싱 경로 |

**어긋났다면 생겼을 버그:**
- `get_team_best` → **리더보드에 표시되는 영상 파일이 삭제된다**
- `model_upload_max_bytes` → JS는 통과시키고 서버가 거절 → 250MB 낭비
- `admin_login_path` → `.env`를 바꿔도 폼이 옛 주소로 전송
- 상태 정규화 → 로그 경로의 사유가 한국어로 번역 안 됨

### 아직 어긋날 수 있는 곳

**1. 상태 문자열이 여러 곳에 하드코딩되어 있다**
```python
# models.py
QUEUED = "queued"
postgresql_where=text("status IN ('queued', 'running')")
# worker/run.py
WHERE status = 'queued'
SET status = 'running'
```
`SubmissionStatus.QUEUED = "pending"` 으로 바꾸면 **워커가 조용히 멈춘다.**
(2단계에서 실제로 일어난 사건의 원인)

**2. WSL IP 탐지 로직이 파이썬과 bash에 두 벌**
```python
subprocess.run(["ip", "-4", "route", "get", "1.1.1.1"], ...)   # drfc.py
```
```bash
DRFC_WSL_IP=$(ip -4 route get 1.1.1.1 | awk ...)               # run_evaluation.sh
```

**3. `counts_toward_daily_limit` property가 실제로는 안 쓰인다**
```python
@property
def counts_toward_daily_limit(self) -> bool:
    return self.status == SubmissionStatus.DONE
```
`quota.py`는 조건을 직접 쓴다. **규칙이 두 곳에 존재한다.**
(`hybrid_property`로 만들면 SQL과 파이썬 양쪽에서 쓸 수 있다)

**4. 타임아웃 값이 여러 층에 흩어져 있다**
```
Caddy 30m  /  subprocess 1920s  /  MAX_WAIT_SECONDS 1800s  /  transfer read 600s
```
관계가 코드에 표현되어 있지 않다. **한 곳을 늘리면 다른 곳도 봐야 한다.**

**5. `can_submit`의 4개 조건이 GET과 POST에 각각 있다**
**의도적 중복**(GET은 화면용, POST는 보안용)이지만, 규칙이 바뀌면 두 곳을 고쳐야 한다.

> **[전공] "중복을 없애라"가 항상 옳은 건 아니다.**
> 5번은 목적이 다르므로 중복이 정당하다.
> 하지만 **"왜 중복인가"를 설명할 수 없다면 그건 그냥 버그의 씨앗**이다.

---

## 6. 점진적 향상과 계층적 폴백 — "없어도 되게" 설계하기

이 프로젝트에는 **같은 패턴이 4번** 나온다.

### (1) `upload.js` — JS가 없어도 제출된다

```javascript
if (!form || !window.XMLHttpRequest || !window.FormData) return;
```
스크립트가 아무것도 안 하면 **브라우저의 기본 폼 전송**이 그대로 일어난다.
```jinja
<form method="post" action="/submit" enctype="multipart/form-data" ...>
```

### (2) `wants_json_response` — 명시적 옵트인

```python
if not accept_header:
    return False
```
**새 동작은 명시적으로 요청한 클라이언트에게만.** 기본 동작은 절대 안 바뀐다.

### (3) `summarize_progress` — metrics가 없으면 로그에서

```python
if percentages:
    return float(best), ...
if log_path is not None:
    return extract_progress_from_log(log_path)
return None, None
```

### (4) `failure_summary` — 값이 없으면 옛 문구

```python
if progress is not None:
    parts[0] = f"완주 실패 ({progress:.1f}%)"
```
옛 레코드(NULL)는 진행률 없이 "완주 실패"만 표시된다.

### (5) `download_video` — 앵글 3개를 순서대로

```python
for suffix in VIDEO_KEY_SUFFIXES:
    ...
    if head.get("ContentLength", 0) < MIN_VALID_VIDEO_BYTES:
        continue  # 생성에 실패한 앵글 — 다음 후보로
return None
```

### 공통 원리

**[쉬움]**
**계단 옆에 에스컬레이터**를 놓는다. 에스컬레이터가 고장 나도 계단으로 올라간다.

**[전공]**
1. **기본 경로를 먼저 완성한다** (HTML 폼, 303 리다이렉트, metrics json, 옛 문구)
2. **개선을 그 위에 얹는다** (JS 진행률, JSON 응답, 로그 파싱, 진행률 표시)
3. **개선이 실패해도 기본은 살아있다**

**반대 접근(Graceful Degradation)** — "JS 우선, 실패 시 대체 제공" — 은
대체 경로가 실제로 테스트되지 않아 잘 안 동작한다.

> **이 프로젝트에서는 특히 중요하다.** 대회 중에 참가자의 제출 경로가
> `upload.js` 하나에 걸리면 안 되기 때문이다. 주석이 그렇게 말한다:
> ```javascript
> // 대회 중에 참가자의 제출 경로가 이 파일 하나에 걸리게 두지 않기 위함이다.
> ```

---

## 7. 기능 플래그와 이관 전략

### `WORKER_TOKEN` — 하나의 변수가 결정하는 것

```
WORKER_TOKEN = ""           WORKER_TOKEN = "abc..."
─────────────────────       ───────────────────────
워커: 로컬 디스크 사용        워커: HTTP 다운로드/업로드
서버: /internal/* → 404      서버: /internal/* 동작
정리: 워커가 직접             정리: 서버에 요청
```

**한 변수가 워커의 동작, 서버의 엔드포인트 개방, 정리 방식을 동시에 결정한다.**

### 왜 이렇게 했나

**[전공] 이관(migration)의 고전적 함정**

코드를 두 벌로 나눴다면 **이관 당일에 새 코드를 처음 돌린다.**
그리고 반드시 뭔가 안 된다.

**스위치 하나로 두면:**
1. 이관 **전에** 한 대에서 http 모드를 시험할 수 있다
2. 문제가 생기면 **환경변수 하나 지우고** 즉시 되돌린다
3. 버그 수정이 한 곳에서 끝난다

> **이것이 "기능 플래그(feature flag)" 패턴이다.**
> **롤백이 재배포가 아니라 설정 변경**이 된다 — 훨씬 빠르고 안전하다.

### 설정 항목을 줄이는 것도 설계다

`WORKER_MODE=http` 같은 변수를 따로 만들지 않았다.
```python
def uses_http() -> bool:
    return bool(settings.worker_token)
```

**변수가 두 개면 잘못된 조합이 생긴다** (`WORKER_MODE=http` 인데 토큰이 없으면?).

> **원칙: 파생 가능한 값은 파생시켜라. 설정 항목을 줄이면 잘못된 조합도 줄어든다.**

---

## 8. 테스트 — 무엇을 테스트했고 무엇을 안 했나

### 현재 테스트 목록 (15개 + 검증 스크립트)

```
tests/test_admin_access.py           — 은닉·잠금·네비게이션 (16개 케이스)
tests/test_checkpoint_validation.py  — 체크포인트 사전 검증
tests/test_evaluation_parsing.py     — parse_evaluation_result
tests/test_leaderboard_build.py      — build_leaderboard
tests/test_leaderboard_ranking.py    — 순위 정렬
tests/test_model_archive.py          — 압축 해제 / 모델 루트 탐색
tests/test_progress_summary.py       — summarize_progress / 로그 파싱
tests/test_quota_adjustment.py       — 하루 한도 보정
tests/test_retention.py              — 파일 보존 정책
tests/test_storage_paths.py          — 경로 해석
tests/test_team_name_parsing.py      — parse_team_names
tests/test_upload_response_mode.py   — Accept 헤더 협상
tests/test_video_selection.py        — 영상 앵글 선택
tests/test_worker_status.py          — 하트비트 판정
tests/test_worker_transfer.py        — local/http 모드
tests/verify_phase8.py               — 실제 DB를 붙인 통합 검증
```

### 패턴 — 전부 "순수 함수" 또는 "얇은 경계"

| 함수 | 왜 테스트하기 쉬운가 |
|---|---|
| `parse_evaluation_result(metrics, laps)` | dict → tuple. **외부 의존 0** |
| `summarize_progress(metrics, log_path)` | dict + 파일 경로 → tuple |
| `resolve_storage_path(stored)` | 문자열 → Path |
| `parse_team_names(raw)` | 문자열 → 리스트 |
| `wants_json_response(header)` | 문자열 → bool |
| `get_team_best(team)` | 객체 하나만 있으면 됨 (DB 쿼리 안 함) |
| `admin_lockout.*` | **시간을 인자로 주입** |
| `Settings(admin_login_path=...)` | 생성자 인자로 검증 |

**[전공] 이것이 "테스트 가능한 설계"의 정의다.**

> **원칙: I/O(DB, 네트워크, 파일, 시간)와 로직을 분리하라.**
> 로직은 순수 함수로, I/O는 얇게. 그러면 테스트가 저절로 쉬워진다.

### 세 가지 테스트 기법 — 이 프로젝트에서 배울 것

**(1) 시간 주입**
```python
def _now(now: float | None) -> float:
    return time.monotonic() if now is None else now
```
```python
assert admin_lockout.seconds_remaining(키, now=잠금초 - 1) > 0
assert admin_lockout.seconds_remaining(키, now=잠금초 + 1) == 0
```
**15분을 실제로 기다리지 않고 경계값을 정확히 찌른다.**

**(2) 덕 타이핑으로 가짜 객체**
```python
class _가짜요청:
    """base.html이 쓰는 request.session만 흉내 낸다."""
    def __init__(self, session: dict):
        self.session = session
```
**템플릿이 실제로 쓰는 것만 갖춘 최소 객체.** 진짜 `Request`를 만들 필요가 없다.

**(3) 전역 상태 격리**
```python
@pytest.fixture(autouse=True)
def _잠금_초기화():
    admin_lockout.clear_all()
    yield
    admin_lockout.clear_all()
```
**전역 상태를 쓰는 모듈은 반드시 이런 장치가 필요하다.**

**(4) 회귀 방지 테스트**
```python
def test_비밀_경로가_openapi에_노출되지_않는다():
    """include_in_schema=False를 빼면 숨긴 주소가 스키마로 새어 나간다."""
```
**기능이 아니라 "이 실수를 다시 하지 않게" 못을 박는 테스트.**

같은 파일의 `test_문서_엔드포인트가_닫혀_있다`와 **짝**이다. 하나는 `/docs`·`/redoc`·
`/openapi.json`이 404인지(바깥 겹), 다른 하나는 `app.openapi()`의 `paths`에 비밀 경로가
없는지(안쪽 겹)를 본다. **바깥 겹이 다시 열려도 안쪽이 버티게** 두 겹을 따로 지킨다
([01-skeleton.md](01-skeleton.md) §2-1).

### 테스트가 없는 곳

| 영역 | 왜 없나 | 위험도 |
|---|---|---|
| DB를 타는 라우트 | `.env`가 운영 DB를 가리킴 | **중** |
| 인증 성공 후 흐름 | 세션 + DB 필요 | 중 (`verify_phase8.py`가 일부 커버) |
| 워커 파이프라인 전체 | DRFC/S3 전체가 필요 | 낮음 (모킹 비용이 큼) |
| 동시성 (SKIP LOCKED) | 실제 DB + 병렬 필요 | 낮음 (DB가 보장) |
| 마이그레이션 | — | 중 |
| `upload.js` | JS 테스트 환경 없음 | 중 |

**`tests/test_admin_access.py`가 이 한계를 주석으로 남긴다:**
```python
# DB가 필요 없는 경계만 확인한다. 로그인 성공 이후의 여정은
# tests/verify_phase8.py가 실제 DB를 붙여 검증한다.
```

**가장 가치 있는 추가는 라우트 순서 테스트다:**
```python
def test_seasons_route_not_shadowed():
    r = TestClient(app).get("/leaderboard/seasons", follow_redirects=False)
    assert r.status_code != 422        # 라우트 순서가 깨지면 여기서 잡힌다
```
지금은 사람이 주석으로만 지키고 있다.

### 실행

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests -q
```
`PYTHONPATH=.`이 필요한 이유: `app`, `worker` 패키지를 import하려면 루트가 검색 경로에 있어야 한다.

---

## 9. 이 코드를 읽으며 발견한 개선 여지 — 정리

> 공부의 결과물로 **"내가 발견한 것"** 을 목록으로 갖는 것이 중요하다.
> **전부 고칠 필요는 없다.** 각 항목마다 "이 규모에서 실제로 문제인가?"를 물어야 한다.

### 보안

| # | 내용 | 위치 | 심각도 |
|---|---|---|---|
| S1 | **CSRF 토큰 없음** (SameSite=Lax가 대부분 막지만) | 모든 POST | **중** |
| S2 | ~~팀 로그인에 잠금 없음 (bcrypt CPU 소모 DoS 가능)~~ → **해결** (2026-08-10, `scope="team"`으로 분리된 잠금) | `auth.py` | ~~중~~ |
| S3 | ~~세션 `max_age` 미명시 (기본 14일)~~ → **해결** (2026-08-10, 8시간) | `main.py` | ~~중~~ |
| S4 | ~~`/docs`, `/openapi.json`이 열려 있다~~ → **해결** (2026-08-06, `/redoc`까지 셋 다 차단) | `main.py` | ~~낮음~~ |
| S5 | `/logout`이 GET | `auth.py`, `admin.py` | 낮음 |
| S6 | 사용자 열거 타이밍 차이 | `auth.py`, `admin.py` | 낮음 |
| S7 | 파일명 살균 없음 (타임스탬프 접두사가 우연히 막고 있음) | `submissions.py:129` | 중 |
| S8 | `error` 쿼리 파라미터로 임의 문구 표시 가능 (피싱) | `submissions.py:106` | 낮음 |
| S9 | 개발용 compose의 DB가 `0.0.0.0:5432` | `docker-compose.yml` | 중 (개발 환경만) |
| S10 | `internal.py`에서 인증보다 본문이 먼저 읽힌다 | `internal.py:58` | 낮음 |
| S11 | `X-Forwarded-For` 위조 가능 (아이디 카운터가 완화) | `admin.py:59` | 낮음 |

### 정확성 / 견고성

| # | 내용 | 위치 | 심각도 |
|---|---|---|---|
| C1 | ~~`IntegrityError` 미처리 → 500 + 250MB 고아 파일~~ → **해결** (2026-08-10, rollback + 파일 삭제) | `submissions.py` | ~~**중**~~ |
| C2 | 동점 순위가 1, 2로 표시됨 (1, 1, 3이 관례) | `leaderboard.html:14` | 중 |
| C3 | `recover_stale_running`이 시작 시에만 실행 | `run.py:295` | 중 (하트비트가 완화) |
| C4 | 워커에 재시작 정책 없음 | 운영 | **중** |
| C5 | 하루 한도가 `finished_at` 기준 — 워커가 밤에 꺼져 있으면 아침에 몰린다 | `quota.py` | 중 |
| C6 | `inject_model`의 delete→upload 사이 중단 시 `model/`이 빈 상태 | `drfc.py:208` | 낮음 |
| C7 | 상태 문자열이 raw SQL에 하드코딩 | `run.py:43` | 낮음 |
| C8 | `worker_heartbeats` 첫 INSERT 경쟁 (upsert 아님) | `worker_status.py:35` | 낮음 |
| C9 | 여러 시계 간 오차 (NTP 의존) | 전반 | 낮음 |

### 성능 / 운영

| # | 내용 | 위치 |
|---|---|---|
| P1 | 리더보드 N+1 (`selectinload`로 해결 가능) | `leaderboard.py:35` |
| P2 | `CMD`에 `exec` 없음 → 종료 시 SIGTERM 전파 지연 | `Dockerfile:16` |
| P3 | Docker 로그 드라이버 크기 제한 없음 | 두 compose |
| P4 | `storage/eval_logs/` 정리 정책 없음 | `retention.py` |
| P5 | `web` 헬스체크 없음 (`/healthz`가 미사용) | `docker-compose.prod.yml` |
| P6 | `--proxy-headers` 없음 | `Dockerfile` |
| P7 | 관리자 화면에 워커 상태/큐 길이 표시 없음 | `admin/dashboard.html` |
| P8 | `eval_logs`를 볼 수 있는 화면 없음 | 관리자 UI |
| P9 | `.dockerignore` 확인 필요 | 루트 |

### 정리 / 일관성

| # | 내용 |
|---|---|
| T1 | `get_current_team_optional`이 미사용 |
| T2 | `counts_toward_daily_limit` property가 미사용 (규칙 중복) |
| T3 | `worker/run.py:96`만 1.x 스타일 `db.query()` |
| T4 | WSL IP 탐지가 파이썬/bash 두 벌 |
| T5 | `inject_model`의 `list_objects_v2`가 paginator 미사용 |
| T6 | `future=True`가 SQLAlchemy 2.0에서 무의미 |
| T7 | 타임아웃 4개 층의 관계가 코드에 표현되지 않음 |
| T8 | `season_archive.archive_season`이 `videos_dir` 인자를 받는데 `retention`은 기본값도 지원 — 호출 방식이 두 가지 |

### **대회 전에 반드시 처리할 것 — 3개**

1. **C4 (워커 재시작 정책)** — 죽으면 평가가 멈춘다. `systemd` 유닛이 답
2. **C1 (IntegrityError)** — 더블클릭 한 번에 500 + 250MB 고아 파일
3. **S1 (CSRF)** — 관리자 상태 변경 라우트만이라도. 은닉·잠금까지 갖췄는데 여기만 비어 있다

### ✅ 이전 검토에서 지적했다가 해결된 것

| 이전 지적 | 현재 상태 |
|---|---|
| 로그인 rate limit 없음 | ✅ `admin_lockout.py` (관리자) |
| `/admin/login`이 공개 | ✅ 비밀 경로 |
| 미인증 시 로그인 화면 노출 | ✅ 404 |
| 관리자 링크가 모두에게 보임 | ✅ 세션 조건부 |
| `SESSION_SECRET` 기본값 배포 위험 | ✅ prod compose에서 `:?` 강제 |
| `depends_on`이 DB 준비를 보장 안 함 | ✅ prod에서 `service_healthy` |
| DB 포트가 `0.0.0.0` | ✅ prod에서 `DB_BIND_ADDRESS` |
| 미완주 팀에게 정보 없음 | ✅ `best_progress_percent` + `failure_summary` |
| 업로드 중 화면이 침묵 | ✅ `upload.js` 진행률 |
| 워커가 죽었는지 모름 | ✅ 하트비트 + 배너 |
| EC2 스팟 회수 시 제출이 갇힘 | ✅ `recover_stale_running`의 `or_` |
| 서버 디스크가 조용히 참 | ✅ `request_prune` |
| 모든 실패가 "시간 초과"로 표시 | ✅ `failure_reason` |
| 자동 생성 API 문서가 공개 | ✅ `docs_url`·`redoc_url`·`openapi_url` = `None` (S4) |
| 팀 로그인 bcrypt CPU 소모로 서버 마비 | ✅ `scope="team"` 잠금 (S2) |
| 관리자 세션이 14일 산다 | ✅ `session_max_age_seconds` = 8시간 (S3) |
| 동시 제출 시 250MB 고아 파일이 영구히 남음 | ✅ `IntegrityError` → rollback + `unlink` (C1) |

**17개가 해결됐다.** 이게 이 프로젝트가 실제로 운영되며 배운 것들이다.

---

## 10. 이 프로젝트에서 배운 것을 일반화하기

### 웹 백엔드의 공통 구조

```
[진입]     서버 프로세스 + 미들웨어 + 라우팅          → 1단계
[데이터]   스키마 설계 + 제약 + 마이그레이션          → 2단계
[신원]     세션/토큰 + 해시 + 인가 + 은닉             → 3단계
[쓰기]     검증 + 저장 + 부작용 + PRG + 점진적 향상   → 4단계
[읽기]     조회 + 가공 + 렌더 + 이스케이프            → 5단계
[비동기]   큐 + 워커 + 상태 기계 + 복구 + 원격 전송   → 6단계
[운영]     컨테이너 + 볼륨 + 프록시 + 필수 설정 강제  → 7단계
```

**프레임워크가 Django든 Rails든 Spring이든 이 뼈대는 같다.**

### 다른 스택에서의 대응표

| 개념 | FastAPI (이 프로젝트) | Django | Spring Boot | Express |
|---|---|---|---|---|
| 앱 객체 | `FastAPI()` | `wsgi.py` | `@SpringBootApplication` | `express()` |
| 라우팅 | `@router.get` | `urls.py` | `@GetMapping` | `app.get()` |
| 동적 라우트 등록 | `add_api_route` | `path()` 리스트 조립 | `RouterFunction` | `app.get(var)` |
| ORM | SQLAlchemy | Django ORM | JPA/Hibernate | Prisma |
| 마이그레이션 | Alembic | `makemigrations` | Flyway/Liquibase | Prisma Migrate |
| 의존성 주입 | `Depends` | (미들웨어/데코레이터) | `@Autowired` | 미들웨어 |
| 템플릿 | Jinja2 | Django Template | Thymeleaf | EJS |
| 커스텀 필터 | `env.filters[...]` | `@register.filter` | Thymeleaf dialect | EJS helper |
| 세션 | SessionMiddleware | `django.contrib.sessions` | Spring Session | express-session |
| 작업 큐 | DB 폴링 + SKIP LOCKED | Celery | `@Async`/Quartz | BullMQ |
| 리버스 프록시 | Caddy | nginx/Caddy | nginx | nginx |

### 다음에 공부하면 좋을 것

**바로 이어지는 것:**
1. **비동기 처리 심화** — asyncio 이벤트 루프, async DB 드라이버(asyncpg)
2. **SQL 심화** — `EXPLAIN ANALYZE`, 인덱스 설계, window function (동점 순위!)
3. **트랜잭션 격리 수준** — READ COMMITTED / REPEATABLE READ, 팬텀 리드
4. **테스트 전략** — `TestClient` + 테스트 DB 픽스처, 모킹

**시야를 넓히는 것:**
5. **HTTP 심화** — 캐시 헤더, 조건부 요청, Range 요청(영상 재생!)
6. **관측성** — 구조화 로그, 메트릭(Prometheus), 분산 추적
7. **CI/CD** — GitHub Actions로 테스트 자동화, 이미지 빌드
8. **002 프로젝트(오프라인 비전 타이머)** — 완전히 다른 도메인(컴퓨터 비전, 실시간)

---

## 11. 졸업 시험 — 이 25문제에 답할 수 있으면 끝이다

> 파일을 안 보고 답해보라. 막히는 문제의 번호가 곧 돌아가야 할 곳이다.

**아키텍처**
1. 참가자가 모델을 올리고 리더보드에 순위가 뜰 때까지, 관여하는 프로세스·저장소·네트워크 경계를 전부 나열하고 순서대로 설명하라.
2. 평가를 웹 요청 안에서 처리하지 않는 이유를 7가지 이상 들라.
3. 이 시스템이 "웹 1개 + 워커 1개 + uvicorn 워커 1개"에 의존하는 지점을 4개 이상 찾아라.
4. `WORKER_TOKEN` 하나가 바꾸는 동작 3가지는? 왜 별도 모드 변수를 안 만들었나?

**데이터**
5. `uq_team_active_submission` 인덱스가 없다면 어떤 시나리오에서 무슨 일이 생기는가? 앱 검사와 JS 버튼 비활성화로는 왜 부족한가?
6. `values_callable`이 없어서 생긴 버그를 설명하라. 왜 진단이 어려웠는가?
7. `nullable=True` 컬럼 추가가 왜 무중단 배포의 기본기인가? "확장 후 수축"이란?
8. `WorkerHeartbeat`에 FK가 없는 이유는? 이력을 안 남기는 이유는?

**인증과 은닉**
9. 세션 데이터는 어디에 있는가? 읽을 수 있는가? 바꿀 수 있는가? 각각의 근거는?
10. 미인증 `/admin`이 404인 이유는? 401/403은 왜 안 되는가? 테스트가 `.json()`까지 비교하는 이유는?
11. 은닉이 "방어가 아니다"라는 말은 무슨 뜻인가? 그럼에도 왜 하는가? 대가 4가지는?
12. IP 카운터만으로 부족한 이유는? `X-Forwarded-For`가 왜 신뢰할 수 없는가?
13. 잠긴 상태에서 bcrypt를 돌리면 왜 잠금이 오히려 공격 도구가 되는가?
14. 잠금 카운터를 프로세스 메모리에 두는 것이 정당한 근거는? 언제 그 근거가 깨지는가?
15. `time.monotonic()`과 벽시계를 각각 어디에 쓰는가? 왜 다른가?

**요청 처리**
16. 500MB 업로드가 서버 메모리를 안 터뜨리는 이유를 코드로 설명하라. `mem_limit: 900m`과 어떻게 연결되는가?
17. 검증 순서가 "싼 것부터"인 이유를 낭비량으로 설명하라. `upload.js`가 그 앞에 한 층을 더한 이유는?
18. 점진적 향상이란? 이 프로젝트에서 그 패턴이 나타나는 곳 5개는?
19. `Accept: */*` 를 JSON으로 취급하면 무슨 일이 일어나는가?
20. `ClientDisconnect`를 잡아서 정리하고 **다시 던지는** 이유는?

**조회와 표현**
21. 팀명에 `<script>`를 넣으면 무슨 일이 생기는가? XSS 하나로 왜 은닉·잠금이 전부 무의미해지는가?
22. `failure_summary` 필터가 `Markup`이 아니라 `str`을 반환하는 것이 왜 중요한가?

**워커**
23. `FOR UPDATE SKIP LOCKED`가 없으면 (a) 아무것도 없을 때, (b) `FOR UPDATE`만 있을 때 각각 무슨 일이?
24. `TransferError`가 `error`가 아니라 `queued`로 가는 이유는? 세 예외의 처리 차이는?
25. 하트비트를 별도 데몬 스레드로 만든 이유 2가지는? `daemon=True`가 왜 결정적인가?
26. `start_marker` 기법이 막는 재앙은? `validate_checkpoint_selection`이 S3 삭제 전이어야 하는 이유는?
27. 워커가 각 단계에서 죽으면 어떻게 되는지 표로 그려라. 완전히 해결 못 하는 지점은 어디이며 왜인가?

**운영**
28. `${VAR:?}` 와 `${VAR:-}` 를 각각 언제 쓰는가? `ADMIN_LOGIN_PATH`와 `DB_BIND_ADDRESS`가 왜 다른가?
29. Caddy의 `request_body max_size`를 앱 상한보다 크게 잡은 이유는? 크기 제한 3층의 역할은?
30. `/app/storage`와 호스트 경로 문제를 설명하고, 해결책의 일반 원칙을 말하라. http 모드에서는 문제의 성격이 어떻게 바뀌는가?

---

## 12. 학습 일정 제안

**하루 1단계, 8일 코스:**

| 일 | 문서 | 핵심 실험 |
|---|---|---|
| 1 | 01-skeleton | A(라우트 목록), C(쿠키 디코드), E(OpenAPI 유출) |
| 2 | 02-data-model | A(psql 인덱스), C(하트비트 관찰), F(하위 호환) |
| 3 | 03-auth | A(쿠키 위조), B(404 등가성), C·D(잠금) |
| 4 | 04-submit | A(청크), B(ClientDisconnect), D(JS 끄기) |
| 5 | 05-leaderboard | A(XSS), B(필터 이스케이프), D(미완주 표시) |
| 6 | 06-worker | A(SKIP LOCKED), B(하트비트), E(TransferError) ← **가장 중요** |
| 7 | 07-ops | A(캐시), D(필수 환경변수), G(크기 제한 3층) |
| 8 | 08-crosscutting | 졸업 시험 30문제 |

**빠른 코스 (3일):**
- 1일차: 01 + 02 (뼈대와 데이터)
- 2일차: 04 + 06 (쓰기와 비동기 — **핵심**)
- 3일차: 08 (졸업 시험으로 구멍 찾기 → 해당 문서만 보충)

**실전 코스 (읽으면서 고치기):**
§9의 "대회 전에 반드시 처리할 것" 3개(C4, C1, S1)를 실제로 구현한다.
읽기만 하는 것보다 훨씬 오래 남는다.

---

## 13. 마지막 조언

**1. 코드를 고쳐 깨뜨려 보라.**
설명 100줄보다 "지우니까 이렇게 망가지는구나"를 한 번 보는 게 낫다.

**2. 주석에 적힌 날짜를 주목하라.**
```python
# (2026-07-26 운영 장애: 업로드한 모델을 워커가 못 찾아 평가 실패)
# 2026-07-26: camera-topview가 261바이트로만 생성되고 있었고...
# (2026-07-25 실제 발생). 여기서 즉시, 명확하게 실패시킨다.
# (2026-07-30 실제 발생). 데몬 스레드라 워커가 죽으면 함께 죽으므로...
# (2026-07-30 submission 18에서 필요성 확인)
# 워커가 자기 디스크를 지워봐야 서버의 250MB짜리 모델은 그대로 쌓인다 (2026-07-30 실제 발생).
# EC2 스팟에서 필요하다: ... 그 제출이 영구히 '평가중'에 갇혔다.
```
**이 주석들이 이 프로젝트에서 가장 값진 문서다.**
"이론상 이럴 수 있다"가 아니라 **"실제로 이렇게 됐다"** 는 기록이다.
새 코드를 쓸 때도 이런 주석을 남겨라.

**3. "왜 이게 여기 있지?"를 계속 물어라.**
`storage_paths.py`가 왜 있는지, `values_callable`이 왜 있는지,
`--filter desired-state=running`이 왜 있는지, `daemon=True`가 왜 있는지 —
**이유 없는 코드는 없고, 이유를 알면 지워도 될 코드도 보인다.**

**4. 같은 문제에 다른 답이 나오는 지점을 찾아라.**
- 미인증: 팀은 303, 관리자는 404
- 데이터 2건: quota는 예외, 시즌은 하나 선택
- 설정 누락: 앱은 기본값, compose는 기동 실패
- 실패: TransferError는 재시도, EvaluationError는 종료

**"왜 다른가"를 설명할 수 있으면 설계를 이해한 것이다.**

**5. 다른 AI와 공부할 때는 [../study-guide.md](../study-guide.md)를 쓰라.**
코드를 못 보는 AI에게 붙여넣을 브리핑 블록과 환각 검증법이 정리되어 있다.

**6. 이 문서들도 낡는다.**
코드가 바뀌면 문서는 낡는다. **항상 코드가 진실이다.**
문서와 코드가 다르면 코드를 믿고, 문서를 고쳐라.
