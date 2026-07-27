# 8단계. 전체를 관통하는 주제 — 흩어진 코드를 하나로 묶기

> 1~7단계는 **파일별**로 내려갔다. 이 문서는 **주제별로 가로지른다.**
> 같은 개념이 여러 파일에 어떻게 나타나는지 보면, 코드가 하나의 시스템으로 보이기 시작한다.

---

## 1. 시간 — 이 시스템은 시간을 어떻게 다루는가

### 시간이 등장하는 모든 곳

| 위치 | 값 | 타임존 | 누가 만드나 |
|---|---|---|---|
| `Submission.submitted_at` | 제출 시각 | UTC (aware) | **DB** (`server_default=func.now()`) |
| `Submission.started_at` | 평가 시작 | UTC | **DB** (`now()` in raw SQL) |
| `Submission.finished_at` | 평가 종료 | UTC | **파이썬** (`now_utc()`) |
| `EvaluationResult.completed_at` | 결과 저장 | UTC | **DB** |
| `Team.daily_count_adjustment_date` | 보정 유효일 | **KST 날짜** | 파이썬 (`today_kst()`) |
| 업로드 파일명 접두사 | `20260726T120000` | UTC | 파이썬 |
| 하루 한도 경계 | KST 자정 | **KST** | 파이썬 (`quota.py`) |
| S3 `LastModified` | 오브젝트 갱신 | UTC | **MinIO** |

### 규칙 3개

**1. 저장은 UTC, 표시와 비즈니스 규칙은 KST.**
```python
KST = ZoneInfo("Asia/Seoul")                          # config.py
def now_utc(): return dt.datetime.now(tz=dt.timezone.utc)   # worker/run.py
def today_kst(): return dt.datetime.now(tz=KST).date()      # quota.py
```

**2. 모든 datetime은 aware.**
`DateTime(timezone=True)` → PostgreSQL `TIMESTAMPTZ`.
naive와 aware를 섞으면 파이썬은 `TypeError`, SQL은 **조용한 오답**.

**3. 하루 경계는 반열린 구간.**
```python
day_start <= finished_at < day_end
```

### 여기서 배울 것

**[전공] 시간은 "값"이 아니라 "값 + 문맥"이다.**
`2026-07-26 00:00:00`은 정보가 아니다. **어느 시간대의** 00시인지 알아야 정보다.

**이 시스템에는 시계가 4개 있다:**
1. PostgreSQL 서버 시계
2. 웹 컨테이너의 파이썬
3. WSL 호스트의 파이썬 (워커)
4. MinIO 서버 시계

**`server_default=func.now()`를 쓴 이유가 여기 있다** — 1번 시계로 통일하기 위해.
하지만 `finished_at`은 파이썬이 만든다(3번 시계). **완전히 일관되지는 않다.**
같은 머신이라 실제 차이는 없지만, **분산 환경이라면 이게 버그가 된다.**

**S3 `LastModified` 비교(`start_marker`)는 4번 시계에 의존한다.**
MinIO가 다른 머신에 있고 시계가 몇 초 어긋나면 새 metrics를 못 찾을 수 있다.

> **일반 원칙: 여러 시계를 비교해야 한다면, 가능한 한 하나의 시계만 쓰도록 설계하라.**
> 그게 불가능하면 **논리적 순서(시퀀스 번호, 버전)** 를 쓴다.

---

## 2. 실패 — 이 시스템의 실패 처리 철학

### 실패를 대하는 4가지 태도

이 코드는 상황마다 **다른 전략**을 쓴다. 그 판단 기준을 정리하면:

| 전략 | 언제 | 코드 예시 |
|---|---|---|
| **즉시 크게 실패** (fail fast) | 설정 오류, 불변식 위반 | `run_worker.sh`의 환경변수 검증, `has_active_submission`의 `scalar_one_or_none` |
| **격리하고 계속** | 한 건의 실패가 전체를 멈추면 안 될 때 | 워커 루프의 `except Exception` |
| **조용히 넘어감** (best-effort) | 부가 기능 | `download_video`, `prune_finished_team_files`, `shutil.rmtree(ignore_errors=True)` |
| **방어적 기본값** | 데이터가 이상해도 화면은 떠야 할 때 | `get_open_season`의 `limit(1)`, `max(done_count, 0)`, `verify_password`의 `except ValueError` |

### 판단 기준 — 3가지 질문

**Q1. 누가 이 실패를 보는가?**
- 운영자 → 시끄럽게 실패해도 된다. 오히려 그래야 안다
- 참가자 → 이해할 수 있는 메시지로. 다른 사람에게 영향 없게

**Q2. 계속 진행하면 데이터가 오염되는가?**
- 오염 → **멈춰야 한다** (예: `start_marker` 없이 이전 결과를 저장하면 대회가 망가진다)
- 오염 없음 → 계속 (예: 영상 없어도 랩타임은 맞다)

**Q3. 이 실패가 예상된 것인가?**
- 예상됨 → 도메인 예외 (`EvaluationError`), 사용자용 메시지
- 예상 못함 → 스택트레이스 로그 + 일반 메시지

### 실제 적용 예시 대조

**같은 "데이터가 2건이다" 상황, 정반대 처리:**
```python
# app/quota.py — 팀의 활성 제출이 2건이면?
return db.execute(stmt).scalar_one_or_none()      # → 예외! 불변식이 깨졌다

# app/routers/leaderboard.py — 진행중 시즌이 2개면?
select(Season).where(...).order_by(...).limit(1)  # → 하나 고른다. 화면은 떠야 한다
```

**같은 "정리 작업 실패", 같은 처리:**
```python
shutil.rmtree(work_dir, ignore_errors=True)       # 무시
except Exception: logger.exception(...)            # 로그만
```
**공통점: 정리 실패가 이미 확정된 결과를 되돌리면 안 된다.**

### 오류 메시지 품질 — 3단계

이 프로젝트의 에러 메시지를 품질순으로 나열하면:

**최상 — 무엇/왜/어떻게가 다 있다:**
```python
MINIO_RAW_FORMAT_HELP = (
    "MinIO의 내부 저장 폴더를 그대로 압축한 것으로 보입니다 "
    "(xl.meta / part.N 파일이 들어 있음). 이 형식은 평가에 사용할 수 없습니다.\n"
    "DRFC 환경에서 아래처럼 모델을 정상적으로 내보낸 뒤 그 폴더를 압축해 주세요:\n"
    "  aws s3 sync ...\n"
)
```

**좋음 — 무엇과 어떻게가 있다:**
```python
"압축 파일에서 model_metadata.json을 찾을 수 없습니다. "
"DRFC 학습 결과의 model/ 폴더 내용을 압축했는지 확인해 주세요."
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
| 1 | 같은 팀이 제출 버튼 더블클릭 | 큐에 2건 | **부분 유니크 인덱스** (+ 앱 체크) |
| 2 | 여러 워커가 같은 작업을 집음 | 중복 평가 | **`FOR UPDATE SKIP LOCKED`** |
| 3 | 여러 요청이 같은 세션을 씀 | 데이터 꼬임 | **요청당 세션 1개** (`get_db`) |
| 4 | 여러 워커가 S3 `model/`을 씀 | 모델 섞임 | **없음** (워커 1대 전제) |
| 5 | 여러 워커가 같은 영상 키를 씀 | 영상 덮어씀 | **없음** (워커 1대 전제) |
| 6 | 관리자가 팀 등록 중 다른 관리자도 등록 | 팀명 중복 | **유니크 제약** (+ 앱 사전 필터) |
| 7 | 웹 컨테이너 여러 개가 마이그레이션 | 스키마 충돌 | **없음** (인스턴스 1개 전제) |
| 8 | 워커가 평가 중인 파일을 retention이 삭제 | 파일 유실 | **`ACTIVE_SUBMISSION_STATUSES` 체크** |

### 4, 5, 7이 "없음"인 것의 의미

**이 시스템은 "웹 1개, 워커 1개"라는 전제 위에 서 있다.**

그 전제를 깨면:
- 워커 2대 → DRFC/S3 레벨에서 충돌 (6단계 §7-7)
- 웹 2개 → 마이그레이션 충돌 (7단계 §1-5)

**[전공] 이건 결함이 아니라 명시된 제약이다.**
문제는 **그 제약이 코드 어디에도 강제되어 있지 않다**는 것.
누군가 `docker compose up -d --scale web=3` 을 실행하면 조용히 깨진다.

> **개선 아이디어**: 워커가 시작할 때 "다른 워커가 이미 running 상태 작업을 갖고 있는지" 확인하고
> 경고하거나, PostgreSQL 어드바이저리 락(`pg_advisory_lock`)으로 워커를 1대로 강제.

### 배운 패턴 3개 — 다른 프로젝트에서도 쓸 것

**패턴 1. 앱 검사 + DB 제약 이중화**
```
앱 검사  = 친절한 안내 (사용자에게 이해되는 메시지)
DB 제약  = 진짜 보장 (경쟁 조건에서도 깨지지 않음)
```
**둘 중 하나만 있으면 안 된다.**

**패턴 2. TOCTOU를 원자적 연산으로 바꾸기**
```sql
-- 나쁨: 확인 후 사용 (사이에 틈이 있다)
SELECT ...; UPDATE ...;

-- 좋음: 하나의 문장
UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING id;
```

**패턴 3. 공짜에 가까운 미래 대비는 지금 하기**
```python
"""워커는 지금 1대만 운영하기로 했지만, FOR UPDATE SKIP LOCKED로
작성해두는 비용이 거의 없어 처음부터 여러 워커가 동시에 폴링해도 안전하게 작성한다"""
```
**"기능"을 미리 만드는 건 낭비지만, "정확성"은 나중에 넣기가 훨씬 어렵다.**

---

## 4. 단일 진실 공급원 — 같은 규칙이 두 곳에 있으면 반드시 어긋난다

### 이 프로젝트가 잘한 것

**`get_team_best`** — 최고기록 계산이 리더보드와 파일 보존에서 공유된다.
```python
# leaderboard.py:24
best_submission, best_result = get_team_best(team)
# retention.py:59
best_submission, _ = get_team_best(team)
```
**어긋났다면**: 리더보드에 보이는 영상이 삭제되는 버그.

**`ACTIVE_SUBMISSION_STATUSES`** — 상수 하나가 SQL과 파이썬 양쪽에서 쓰인다.
```python
Submission.status.in_(ACTIVE_SUBMISSION_STATUSES)          # quota.py — SQL
submission.status.value in ACTIVE_SUBMISSION_STATUSES      # retention.py — 파이썬
```

**`settings.daily_submission_limit`** — 검증, 화면 표시, 에러 메시지가 같은 값을 본다.
```python
get_remaining_submissions()  # 계산
"daily_limit": settings.daily_submission_limit   # 화면
f"오늘 제출 한도({settings.daily_submission_limit}회)를 모두 사용했습니다."  # 메시지
```

**`storage_paths`** — 경로 해석 규칙이 한 모듈에만 있다.

### 아직 어긋날 수 있는 곳

**1. 상태 문자열이 4곳에 하드코딩되어 있다**
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
# drfc.py
subprocess.run(["ip", "-4", "route", "get", "1.1.1.1"], ...)
```
```bash
# run_evaluation.sh
DRFC_WSL_IP=$(ip -4 route get 1.1.1.1 | awk ...)
```

**3. `counts_toward_daily_limit` property가 실제로는 안 쓰인다**
```python
@property
def counts_toward_daily_limit(self) -> bool:
    return self.status == SubmissionStatus.DONE
```
`quota.py`는 조건을 직접 쓴다. **규칙이 두 곳에 존재한다.**

**4. `can_submit`의 4개 조건이 GET과 POST에 각각 있다**
```python
# GET /submit
can_submit = (season.status == ACTIVE and active is None and remaining > 0 and not disqualified)
# POST /submit — 같은 4개를 순서대로 다시 검사
```
**의도적 중복**(GET은 화면용, POST는 보안용)이지만, 규칙이 바뀌면 두 곳을 고쳐야 한다.

> **[전공] "중복을 없애라"가 항상 옳은 건 아니다.**
> 4번은 목적이 다르므로(안내 vs 강제) 중복이 정당하다.
> 하지만 **"왜 중복인가"를 설명할 수 없다면 그건 그냥 버그의 씨앗**이다.

---

## 5. 테스트 — 무엇을 테스트했고 무엇을 안 했나

### 현재 테스트 목록

```
tests/test_evaluation_parsing.py   — parse_evaluation_result
tests/test_leaderboard_build.py    — build_leaderboard
tests/test_leaderboard_ranking.py  — 순위 정렬
tests/test_model_archive.py        — 압축 해제 / 모델 루트 탐색
tests/test_quota_adjustment.py     — 하루 한도 보정
tests/test_retention.py            — 파일 보존 정책
tests/test_storage_paths.py        — 경로 해석
tests/test_team_name_parsing.py    — parse_team_names
tests/test_video_selection.py      — 영상 앵글 선택
tests/verify_phase8.py             — 통합 검증 스크립트
```

### 패턴 — 전부 "순수 함수"에 가까운 것들이다

| 함수 | 왜 테스트하기 쉬운가 |
|---|---|
| `parse_evaluation_result(metrics, laps)` | dict 넣고 tuple 받음. **외부 의존 0** |
| `resolve_storage_path(stored)` | 문자열 넣고 Path 받음 |
| `parse_team_names(raw)` | 문자열 → 리스트 |
| `get_team_best(team)` | 객체 하나만 있으면 됨 (DB 쿼리 안 함) |
| `_find_model_root(dir)` | 임시 디렉터리만 만들면 됨 |

**[전공] 이것이 "테스트 가능한 설계"의 정의다.**

`get_team_best`가 좋은 예다. 이렇게 짤 수도 있었다:
```python
def get_team_best(db: Session, team_id: int):     # DB에 직접 쿼리
    ...
```
그러면 테스트에 **DB가 필요**하다. 지금처럼 `team` 객체를 받으면
가짜 객체를 만들어 넣을 수 있다.

> **원칙: I/O(DB, 네트워크, 파일)와 로직을 분리하라.**
> 로직은 순수 함수로, I/O는 얇게. 그러면 테스트가 저절로 쉬워진다.

### 테스트가 없는 곳

| 영역 | 왜 없나 | 위험도 |
|---|---|---|
| 라우트 (HTTP 레벨) | `TestClient` 설정 + DB 필요 | **중** — 라우트 순서 버그를 못 잡는다 |
| 인증 흐름 | 세션 모킹 필요 | 중 |
| 워커 파이프라인 | DRFC/S3 전체가 필요 | 낮음 (모킹 비용이 큼) |
| 동시성 (SKIP LOCKED) | 실제 DB + 병렬 필요 | 낮음 (DB가 보장) |
| 마이그레이션 | — | 중 |

**가장 가치 있는 추가는 라우트 테스트다:**
```python
from fastapi.testclient import TestClient
from app.main import app

def test_seasons_route_not_shadowed():
    client = TestClient(app)
    r = client.get("/leaderboard/seasons", follow_redirects=False)
    assert r.status_code != 422        # 라우트 순서가 깨지면 여기서 잡힌다
```

**5단계의 라우트 순서 함정을 자동으로 감시하는 테스트다.**
지금은 사람이 주석으로만 지키고 있다.

### 테스트 실행

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests -q
```

`PYTHONPATH=.`이 필요한 이유: `app`, `worker` 패키지를 import하려면 프로젝트 루트가
모듈 검색 경로에 있어야 한다. (`pyproject.toml`이나 `setup.cfg`로 설정하면 생략 가능)

---

## 6. 이 코드를 읽으며 발견한 개선 여지 — 정리

> 공부의 결과물로 **"내가 발견한 것"** 을 목록으로 갖는 것이 중요하다.
> 심각도순이 아니라 **단계순**으로 정리했다.

### 보안

| # | 내용 | 위치 | 심각도 |
|---|---|---|---|
| S1 | `SESSION_SECRET` 기본값으로 배포되면 관리자 세션 위조 가능 | `config.py` | **높음** (공개 배포 시) |
| S2 | CSRF 토큰 없음 (SameSite=Lax가 대부분 막지만) | 전체 POST | 중 |
| S3 | `/logout`이 GET | `auth.py` | 낮음 |
| S4 | 로그인 rate limit 없음 → bcrypt CPU 소모 DoS | `auth.py` | 낮음 |
| S5 | 사용자 열거 타이밍 차이 | `auth.py:29` | 낮음 |
| S6 | 파일명 살균 없음 (타임스탬프 접두사가 우연히 막고 있음) | `submissions.py:102` | 중 |
| S7 | `error` 쿼리 파라미터로 임의 문구 표시 가능 (피싱) | `submissions.py:79` | 낮음 |
| S8 | DB 포트가 `0.0.0.0:5432`에 노출 + 기본 비밀번호 | `docker-compose.yml` | 중 |

### 정확성 / 견고성

| # | 내용 | 위치 | 심각도 |
|---|---|---|---|
| C1 | 유니크 인덱스 위반 시 `IntegrityError` 미처리 → 500 + 고아 파일 | `submissions.py:127` | 중 |
| C2 | `recover_stale_running`이 워커 시작 시에만 실행 | `worker/run.py:214` | **중** |
| C3 | 동점 순위가 1, 2로 표시됨 (1, 1, 3이 관례) | `leaderboard.html:12` | 중 |
| C4 | 하루 한도가 `finished_at` 기준이라 자정 근처에 틈 | `quota.py` | 낮음 |
| C5 | `inject_model`의 delete→upload 사이 중단 시 `model/`이 빈 상태 | `drfc.py:165` | 낮음 |
| C6 | `depends_on`이 DB 준비를 보장 안 함 (재시작으로 우연히 해결) | `docker-compose.yml` | 낮음 |
| C7 | 워커에 재시작 정책 없음 (죽으면 아무도 모름) | 운영 | **중** |
| C8 | 상태 문자열이 raw SQL에 하드코딩 | `worker/run.py:38` | 낮음 |

### 성능 / 운영

| # | 내용 | 위치 |
|---|---|---|
| P1 | 리더보드 N+1 (`selectinload`로 해결 가능) | `leaderboard.py:16` |
| P2 | `CMD`에 `exec` 없음 → 종료 시 SIGTERM 전파 지연 | `Dockerfile:16` |
| P3 | 워커 로그가 `/tmp`(재부팅 시 소실) | 운영 |
| P4 | `eval_logs`를 볼 수 있는 화면 없음 | 관리자 UI |
| P5 | DB 백업 절차 미확인 | 운영 |
| P6 | `--proxy-headers` 없음 (터널 뒤 실제 IP 미확보) | `Dockerfile` |
| P7 | 세션 `max_age` 미명시 (기본 14일) | `main.py` |
| P8 | `/healthz`가 아무데도 연결 안 됨 | `docker-compose.yml` |

### 정리 / 일관성

| # | 내용 |
|---|---|
| T1 | `get_current_team_optional`이 미사용 |
| T2 | `counts_toward_daily_limit` property가 미사용 (규칙 중복) |
| T3 | `worker/run.py:79`만 1.x 스타일 `db.query()` |
| T4 | WSL IP 탐지가 파이썬/bash 두 벌 |
| T5 | `inject_model`의 `list_objects_v2`가 paginator 미사용 |
| T6 | `future=True`가 SQLAlchemy 2.0에서 무의미 |

> **이 목록을 만든 것 자체가 공부의 성과다.**
> "코드를 읽었다"와 "코드를 평가할 수 있다"는 다르다.
> 다만 **전부 고칠 필요는 없다.** 각 항목마다 "이 규모에서 이게 실제로 문제인가?"를 물어야 한다.
> 예: S4(rate limit)는 사내 10팀 대회에서 사실상 무의미하다.
> 반면 S1(SESSION_SECRET)과 C7(워커 감시)은 **대회 전에 반드시** 처리해야 한다.

---

## 7. 이 프로젝트에서 배운 것을 일반화하기

### 웹 백엔드의 공통 구조

이 프로젝트에서 배운 것은 **거의 모든 웹 서비스에 그대로 적용된다.**

```
[진입]     서버 프로세스 + 미들웨어 + 라우팅          → 1단계
[데이터]   스키마 설계 + 제약 + 마이그레이션          → 2단계
[신원]     세션/토큰 + 해시 + 인가                    → 3단계
[쓰기]     검증 + 저장 + 부작용 + PRG                 → 4단계
[읽기]     조회 + 가공 + 렌더 + 이스케이프            → 5단계
[비동기]   큐 + 워커 + 상태 기계 + 복구               → 6단계
[운영]     컨테이너 + 볼륨 + 네트워크 + 공개          → 7단계
```

**프레임워크가 Django든 Rails든 Spring이든 이 뼈대는 같다.**
바뀌는 것은 문법이지 구조가 아니다.

### 다른 스택에서의 대응표

| 개념 | FastAPI (이 프로젝트) | Django | Spring Boot | Express |
|---|---|---|---|---|
| 앱 객체 | `FastAPI()` | `wsgi.py` | `@SpringBootApplication` | `express()` |
| 라우팅 | `@router.get` | `urls.py` | `@GetMapping` | `app.get()` |
| ORM | SQLAlchemy | Django ORM | JPA/Hibernate | Prisma/TypeORM |
| 마이그레이션 | Alembic | `makemigrations` | Flyway/Liquibase | Prisma Migrate |
| 의존성 주입 | `Depends` | (미들웨어/데코레이터) | `@Autowired` | 미들웨어 |
| 템플릿 | Jinja2 | Django Template | Thymeleaf | EJS/Pug |
| 세션 | SessionMiddleware | `django.contrib.sessions` | Spring Session | express-session |
| 작업 큐 | DB 폴링 | Celery | `@Async`/Quartz | BullMQ |

**이 표를 보고 "아, 그건 여기서 뭐였지?"를 매칭할 수 있으면 이식 가능한 지식이다.**

### 다음에 공부하면 좋을 것

**바로 이어지는 것:**
1. **비동기 처리 심화** — asyncio 이벤트 루프, async DB 드라이버(asyncpg)
2. **SQL 심화** — 실행 계획(`EXPLAIN ANALYZE`), 인덱스 설계, window function
3. **트랜잭션 격리 수준** — READ COMMITTED / REPEATABLE READ, 팬텀 리드
4. **테스트 전략** — `TestClient`, 픽스처, 테스트 DB, 모킹

**시야를 넓히는 것:**
5. **HTTP 심화** — 캐시 헤더, 조건부 요청, 청크 인코딩
6. **관측성** — 구조화 로그, 메트릭, 분산 추적
7. **CI/CD** — GitHub Actions로 테스트 자동화, 이미지 빌드
8. **002 프로젝트(오프라인 비전 타이머)** — 완전히 다른 도메인(컴퓨터 비전, 실시간)

---

## 8. 졸업 시험 — 이 20문제에 답할 수 있으면 끝이다

> 파일을 안 보고 답해보라. 막히는 문제의 번호가 곧 돌아가야 할 곳이다.

**아키텍처**
1. 참가자가 모델을 올리고 리더보드에 순위가 뜰 때까지, 관여하는 프로세스와 저장소를 전부 나열하고 순서대로 설명하라.
2. 평가를 웹 요청 안에서 처리하지 않는 이유를 6가지 이상 들라.
3. 이 시스템이 "웹 1개 + 워커 1개"에 의존하는 지점을 4개 이상 찾아라.

**데이터**
4. `uq_team_active_submission` 인덱스가 없다면 어떤 시나리오에서 무슨 일이 생기는가? 앱의 `if`문으로는 왜 부족한가?
5. `values_callable`이 없어서 생긴 버그를 설명하라. 왜 진단이 어려웠는가?
6. `daily_count_override`(절대값)에서 `daily_count_adjustment`(델타)로 바꾼 이유는?

**인증**
7. 세션 데이터는 어디에 있는가? 사용자가 읽을 수 있는가? 바꿀 수 있는가? 각각의 근거는?
8. `SESSION_SECRET`이 기본값이면 공격자가 구체적으로 무엇을 할 수 있는가?
9. bcrypt가 SHA-256보다 나은 이유와, `MAX_BULK_TEAMS=50`이 그와 무슨 관계인지 설명하라.

**요청 처리**
10. 500MB 업로드가 서버 메모리를 안 터뜨리는 이유를 코드로 설명하라.
11. 검증 순서가 "싼 것부터"인 이유를 낭비량으로 설명하라.
12. PRG 패턴이 없으면 F5를 눌렀을 때 무슨 일이 생기는가? 왜 303이어야 하는가?

**조회/표현**
13. 팀명에 `<script>`를 넣으면 무슨 일이 생기는가? 무엇이 막는가? 그것이 막지 못하는 문맥 4가지는?
14. 순위 계산을 SQL이 아니라 파이썬에서 하는 것의 장단점은? 언제 바꿔야 하는가?

**워커**
15. `FOR UPDATE SKIP LOCKED`가 없으면(a) 아무것도 없을 때, (b) `FOR UPDATE`만 있을 때 각각 무슨 일이 생기는가?
16. `start_marker` 기법이 막는 재앙은 무엇인가?
17. 워커가 각 단계에서 죽으면 어떻게 되는지 표로 그려라. 완전히 해결 못 하는 지점은 어디이며 왜인가?
18. `docker stack ps` 버그가 왜 진단하기 어려웠는가?

**운영**
19. `/app/storage`와 `/mnt/c/.../storage` 문제를 설명하고, 해결책의 일반 원칙을 말하라.
20. Cloudflare Tunnel이 포트포워딩 없이 동작하는 원리와, `SESSION_HTTPS_ONLY`를 잘못 설정했을 때의 증상은?

---

## 9. 학습 일정 제안

**하루 1단계, 8일 코스:**

| 일 | 문서 | 할 일 |
|---|---|---|
| 1 | 01-skeleton | 읽기 + 실험 A, B, E |
| 2 | 02-data-model | 읽기 + 실험 A(psql), C(N+1 관찰) |
| 3 | 03-auth | 읽기 + 실험 A(쿠키 위조), C(bcrypt 측정) |
| 4 | 04-submit | 읽기 + 실험 A(청크), B(PRG 제거) |
| 5 | 05-leaderboard | 읽기 + 실험 A(XSS), D(N+1 해결) |
| 6 | 06-worker | 읽기 + 실험 A(SKIP LOCKED) ← **가장 중요** |
| 7 | 07-ops | 읽기 + 실험 A(캐시), B(경로 비교) |
| 8 | 08-crosscutting | 졸업 시험 20문제 |

**빠른 코스 (3일):**
- 1일차: 01 + 02 (뼈대와 데이터)
- 2일차: 04 + 06 (쓰기와 비동기 — **핵심**)
- 3일차: 08 (졸업 시험으로 구멍 찾기 → 해당 문서만 보충)

**실전 코스 (읽으면서 고치기):**
§6의 개선 목록에서 **S1, C2, C7** 세 개를 실제로 구현한다.
읽기만 하는 것보다 훨씬 오래 남는다.

---

## 10. 마지막 조언

**1. 코드를 고쳐 깨뜨려 보라.**
설명 100줄보다 "지우니까 이렇게 망가지는구나"를 한 번 보는 게 낫다.
각 문서의 실험 과제는 그걸 위해 만들었다.

**2. 주석에 적힌 날짜를 주목하라.**
```python
# (2026-07-26 운영 장애: 업로드한 모델을 워커가 못 찾아 평가 실패)
# 2026-07-26: camera-topview가 261바이트로만 생성되고 있었고...
# (2026-07-25 실제 발생). 여기서 즉시, 명확하게 실패시킨다.
```
**이 주석들이 이 프로젝트에서 가장 값진 문서다.**
"이론상 이럴 수 있다"가 아니라 **"실제로 이렇게 됐다"** 는 기록이다.
새 코드를 쓸 때도 이런 주석을 남겨라.

**3. "왜 이게 여기 있지?"를 계속 물어라.**
`storage_paths.py`가 왜 있는지, `values_callable`이 왜 있는지,
`--filter desired-state=running`이 왜 있는지 —
**이유 없는 코드는 없고, 이유를 알면 지워도 될 코드도 보인다.**

**4. 다른 AI와 공부할 때는 [../study-guide.md](../study-guide.md)를 쓰라.**
코드를 못 보는 AI에게 붙여넣을 브리핑 블록과 환각 검증법이 정리되어 있다.

**5. 이 문서들도 틀릴 수 있다.**
코드가 바뀌면 문서는 낡는다. **항상 코드가 진실이다.**
문서와 코드가 다르면 코드를 믿고, 문서를 고쳐라.
