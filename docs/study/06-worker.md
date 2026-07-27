# 6단계. 평가 워커 — `worker/run.py`, `worker/drfc.py`, 셸 스크립트들

> 이 단계의 목표: **웹 요청 주기 밖에서 도는 프로세스**를 설계하는 법을 이해하는 것.
> 여기서 다루는 것: 작업 큐, 동시성 제어, 상태 기계, 장애 복구, 외부 프로세스 호출, 오브젝트 스토리지.
>
> **이 프로젝트에서 가장 어려운 부분이고, 가장 배울 게 많은 부분이다.**

---

## 0. 왜 별도 프로세스인가 — 요청-응답 주기의 한계

### 문제

평가 한 건에 **10분**이 걸린다. 만약 `POST /submit` 안에서 그냥 실행한다면?

```python
@router.post("/submit")
async def submit_upload(...):
    save_file()
    run_evaluation()      # ← 10분 블로킹
    return RedirectResponse("/submit")
```

**무슨 일이 일어나는가:**

1. **브라우저 타임아웃.** 기본 60~120초. 10분을 기다려주지 않는다
2. **프록시 타임아웃.** Cloudflare Tunnel은 기본 100초에 524 에러
3. **`async def` 안의 블로킹 → 이벤트 루프 정지.** 다른 모든 사용자의 요청이 10분간 멈춘다
4. **사용자가 새로고침하면?** 평가가 하나 더 시작된다
5. **서버 재시작하면?** 진행 중이던 평가가 흔적 없이 사라진다
6. **DRFC는 한 번에 하나만 돌 수 있다.** 동시 요청을 큐잉할 방법이 없다

**[쉬움]**
식당에서 손님이 주문하면, 요리가 다 될 때까지 카운터 앞에 세워두는 것과 같다.
뒤에 줄이 밀린다. 정상적인 식당은 **번호표를 주고 자리로 보낸다.**

### 해결: 작업 큐 + 워커

```
POST /submit  →  파일 저장 + DB에 '주문표' INSERT  →  즉시 응답 (0.5초)
                                 ↓
                    (별도 프로세스가 주문표를 집어감)
                                 ↓
                        10분간 요리 → 결과 저장
```

**웹은 "받아 적기"만, 워커는 "실행"만.** 관심사가 완전히 분리된다.

---

## 1. 작업 큐를 DB로 만들기

### 무엇을(What)

`submissions` 테이블이 곧 큐다. 별도 큐 시스템이 없다.

```
status='queued'   → 대기열에 있음
status='running'  → 누군가 처리 중
status='done'     → 완료
status='error'    → 실패
```

### 왜(Why) — Celery/Redis/RabbitMQ를 안 쓰는 이유

**[쉬움]**
전문 대기표 기계를 사는 대신, **이미 있는 장부에 "대기중"이라고 적는다.**
장부는 어차� 있어야 하니까 기계를 하나 덜 산다.

**[전공] 비교**

| | DB 큐 (현재) | Celery + Redis |
|---|---|---|
| 운영 프로세스 | DB 하나 (이미 있음) | + Redis + Celery worker + (Flower) |
| **진실의 원천** | **DB 하나** | DB와 브로커 **둘** → 불일치 가능 |
| 처리량 | 초당 수백 건 | 초당 수만 건 |
| 지연 | 폴링 간격(5초) | 즉시 (푸시) |
| 재시도/스케줄링 | 직접 구현 | 내장 |
| 관측성 | SQL로 바로 조회 | 별도 도구 |
| 트랜잭션 | **작업 상태와 데이터가 한 트랜잭션** | 분리됨 |

**"진실의 원천이 하나"가 결정적이다.**

Celery를 쓰면:
```python
db.add(submission); db.commit()      # DB에 저장
evaluate_task.delay(submission.id)   # Redis에 작업 발행
```
이 둘 사이에서 프로세스가 죽으면 **DB에는 있는데 큐에는 없는** 제출이 생긴다.
영원히 처리되지 않는다. (해결하려면 outbox 패턴 등 복잡도가 늘어난다)

DB 큐는 `INSERT` 하나가 곧 발행이므로 **원자적**이다.

**언제 전용 큐가 필요한가:**
- 초당 수백~수천 작업
- 다양한 작업 타입, 우선순위, 지연 실행, 주기 실행
- 여러 서비스가 이벤트를 구독

**우리는 하루 50건, 작업 타입 1개다.** DB 큐가 명백히 옳다.

> **[전공] 이 패턴에는 이름이 있다.** "Database as a Queue", 또는
> PostgreSQL 문맥에서 `SKIP LOCKED` 기반 큐. Sidekiq/Solid Queue(Rails),
> `pg-boss`(Node), `procrastinate`(Python) 등이 이 방식을 채택한다.
> **"안티패턴"이라는 옛 평판이 있었지만, `SKIP LOCKED`(PostgreSQL 9.5+) 이후로는 정석 중 하나다.**

---

## 2. 폴링 — 5초마다 물어보기

```python
POLL_INTERVAL_SECONDS = 5

while True:
    db = SessionLocal()
    try:
        submission_id = claim_next_submission(db)
    finally:
        db.close()

    if submission_id is None:
        time.sleep(POLL_INTERVAL_SECONDS)
        continue
    process_submission(submission_id)
```

### 폴링 vs 푸시

**[쉬움]**
- **폴링**: 5초마다 우체통을 확인하러 나간다
- **푸시**: 우편배달부가 초인종을 누른다

**[전공]**

| | 폴링 (현재) | LISTEN/NOTIFY | 메시지 브로커 |
|---|---|---|---|
| 지연 | 평균 2.5초, 최대 5초 | 즉시 | 즉시 |
| 구현 | `while True` + `sleep` | 커넥션 유지 + 이벤트 루프 | 라이브러리 |
| 커넥션 | 5초마다 열고 닫음 | 항상 1개 점유 | 항상 |
| 연결 끊김 대응 | **자동 복구** (다음 폴링) | 재연결 로직 필요 | 라이브러리가 처리 |

**평가에 10분이 걸리는데 시작이 5초 늦는 게 문제인가?** 아니다. **0.8% 오차다.**

**폴링의 진짜 장점은 단순함과 견고함이다.**
DB가 잠깐 죽었다 살아나도, 네트워크가 끊겼다 붙어도, **다음 5초에 아무 일 없었다는 듯 계속된다.**
`pool_pre_ping=True`(1단계)가 이걸 뒷받침한다.

**비용 계산**: 5초마다 쿼리 1회 = 하루 17,280회. PostgreSQL에겐 **아무것도 아니다.**

> **PostgreSQL의 `LISTEN`/`NOTIFY`**로 개선할 수 있다:
> ```sql
> -- 웹에서 INSERT 후
> NOTIFY new_submission;
> -- 워커에서
> LISTEN new_submission;
> ```
> 하지만 **NOTIFY는 트랜잭션 커밋 시 전달되고, 리스너가 없으면 사라진다.**
> 워커가 재시작 중이면 알림을 놓친다 → **결국 폴링을 백업으로 둬야 한다.**
> 복잡도가 배로 늘고 이득은 5초. **현재 설계가 맞다.**

---

## 3. **`FOR UPDATE SKIP LOCKED` — 이 프로젝트의 기술적 하이라이트**

```python
CLAIM_NEXT_SQL = text(
    """
    UPDATE submissions
    SET status = 'running', worker_id = :worker_id, started_at = now()
    WHERE id = (
        SELECT id FROM submissions
        WHERE status = 'queued'
        ORDER BY submitted_at ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING id
    """
)
```

**이 한 문장에 동시성 제어의 정수가 들어있다.** 한 조각씩 분해한다.

### 3-1. 해결하려는 문제

워커가 2대라면(또는 실수로 두 번 실행하면):

```
시각   워커1                          워커2
──────────────────────────────────────────────────────────
t=0    SELECT ... WHERE status='queued' LIMIT 1  → id=5
t=1                                   SELECT ... → id=5   ← 같은 걸 봤다!
t=2    UPDATE SET status='running' WHERE id=5
t=3                                   UPDATE SET status='running' WHERE id=5
t=4    평가 시작 (10분)                평가 시작 (10분)   ← 같은 모델을 두 번!
```

**결과**: DRFC를 두 프로세스가 동시에 실행 → **S3의 model/ 폴더를 서로 덮어씀** →
둘 다 엉뚱한 결과를 얻는다. 그리고 영상 파일(`0-video.mp4`)도 덮어써진다.

### 3-2. `FOR UPDATE` — 행 단위 배타 락

**[쉬움]**
도서관에서 책을 집으면서 **"이거 내가 볼 거예요"라고 딱지를 붙인다.**
다른 사람은 그 책을 못 가져간다.

**[전공]**
`SELECT ... FOR UPDATE`는 조회된 행에 **배타적 행 락(row-level exclusive lock)** 을 건다.
트랜잭션이 커밋/롤백될 때까지 유지된다.

PostgreSQL은 MVCC(다중 버전 동시성 제어)라 **읽기는 락을 걸지 않는다.**
`FOR UPDATE`는 "이 행을 곧 수정할 것"이라는 명시적 의도 표현이다.

### 3-3. `SKIP LOCKED` — 기다리지 말고 건너뛰기

**`FOR UPDATE`만 쓰면 어떻게 되나?**

```
워커1: SELECT ... FOR UPDATE  →  id=5 획득, 락 보유
워커2: SELECT ... FOR UPDATE  →  id=5가 잠겨있음 → **대기(blocking)**
       ...워커1이 커밋할 때까지 멈춤...
       워커1 커밋 후 → 다시 읽음 → status가 'running'이라 조건 불일치 → 0건
```

**결과**: 워커2는 **아무 이유 없이 기다렸다가 빈손으로 돌아간다.**
큐에 다른 작업이 10개 있어도 못 집는다. **처리량이 사실상 1대 수준으로 떨어진다.**

**`SKIP LOCKED`를 붙이면:**
```
워커1: id=5 획득 (락)
워커2: id=5는 잠겨있네 → 건너뛰고 → id=6 획득   ← 대기 없음!
```

**[쉬움]** 도서관에서 누가 딱지 붙인 책은 **그냥 지나치고 다음 책**을 집는다.

**[전공]**
`SKIP LOCKED`(PostgreSQL 9.5+)는 **잠긴 행을 결과 집합에서 조용히 제외**한다.
이것이 "DB를 큐로 쓰는 것"을 실용적으로 만든 결정적 기능이다.

| 옵션 | 잠긴 행을 만나면 |
|---|---|
| (기본) | 락이 풀릴 때까지 **대기** |
| `NOWAIT` | **즉시 에러** |
| `SKIP LOCKED` | **건너뛴다** ← 큐에 적합 |

### 3-4. `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED)`

**왜 이렇게 중첩했나?**

`UPDATE ... LIMIT 1`은 **PostgreSQL에서 지원하지 않는다.** (MySQL은 됨)
그래서 서브쿼리로 "하나만 고르고" 그 id를 UPDATE 대상으로 쓴다.

**그리고 UPDATE와 SELECT가 하나의 문장이라 원자적이다.**
파이썬에서 SELECT 후 UPDATE를 따로 하면 그 사이에 틈이 생긴다.

```python
# 나쁜 방법 — TOCTOU
row = db.execute(select(Submission).where(...).limit(1)).scalar_one_or_none()
row.status = 'running'   # ← 그 사이에 다른 워커가 집어갔을 수 있다
db.commit()
```

### 3-5. `ORDER BY submitted_at ASC` — 공정성(FIFO)

먼저 제출한 사람이 먼저 처리된다. **선착순**.

`_queue_position`(4단계)이 같은 기준으로 순번을 계산하므로 **안내와 실제가 일치**한다.

> 우선순위를 도입하려면 `ORDER BY priority DESC, submitted_at ASC` 로 확장 가능하다.
> **DB 큐의 장점 중 하나가 이런 정렬 규칙을 SQL로 자유롭게 표현할 수 있다는 것이다.**

### 3-6. `RETURNING id` — 왕복 한 번 아끼기

PostgreSQL 전용 기능(표준 SQL 아님).
`UPDATE`가 실제로 수정한 행의 값을 **같은 문장에서 돌려받는다.**

없다면:
```sql
UPDATE ... ;                                    -- 1회
SELECT id FROM submissions WHERE worker_id=... AND status='running';  -- 2회 (게다가 부정확)
```

`RETURNING`은 **"내가 방금 집은 그것"** 을 정확히 알려준다. 다른 워커의 것과 섞이지 않는다.

```python
row = db.execute(CLAIM_NEXT_SQL, {"worker_id": WORKER_ID}).first()
db.commit()          # ← 커밋해야 락이 풀리고 status='running'이 확정된다
return row[0] if row else None
```

**`db.commit()`이 반드시 필요하다.** 커밋 전에는 락이 유지되고,
다른 워커가 이 행을 `SKIP LOCKED`로 건너뛴다. 커밋하면 `status='running'`이 되어
`WHERE status='queued'` 조건에 애초에 안 걸린다.

### 3-7. 파라미터 바인딩 — SQL 인젝션 방어

```python
db.execute(CLAIM_NEXT_SQL, {"worker_id": WORKER_ID})
```

`:worker_id`는 **바인드 파라미터**다. 문자열 포매팅이 아니다.

```python
# 절대 하면 안 되는 것
text(f"UPDATE submissions SET worker_id = '{WORKER_ID}' ...")
```
`WORKER_ID`는 `socket.gethostname()`이라 실제 위험은 낮지만,
**raw SQL을 쓸 때는 항상 바인드 파라미터**가 원칙이다.

### 3-8. 워커가 1대인데 왜 이걸 썼나

```python
"""워커는 지금 1대만 운영하기로 했지만(2026-07-24 논의), FOR UPDATE SKIP LOCKED로
작성해두는 비용이 거의 없어 처음부터 여러 워커가 동시에 폴링해도 안전하게 작성한다
(multi-laptop-worker-pool.md에서 실제로 여러 대를 쓰기로 하면 그대로 재사용 가능)."""
```

**[전공] 좋은 판단의 예다.**
"지금 필요 없으니 나중에"라고 미루면, 나중에 워커를 늘릴 때
**증상이 비결정적인 버그**(가끔 두 번 평가됨)를 디버깅하게 된다.
**비용이 거의 0인 방어는 처음부터 넣는 게 맞다.**

**반대로**: 지금 필요 없는 기능을 미리 만드는 건(YAGNI 위반) 낭비다.
**"공짜에 가까운 정확성"과 "미리 만든 기능"은 다르다.**

---

## 4. 상태 기계 — 4개 상태와 전이

```
          제출 업로드
              ↓
        ┌──────────┐
        │  queued  │ ←──────────┐
        └────┬─────┘            │ recover_stale_running
             │ claim_next       │ (워커 재시작 시)
             ↓                  │
        ┌──────────┐            │
        │ running  │────────────┘
        └────┬─────┘
             │
     ┌───────┴────────┐
     ↓                ↓
┌────────┐      ┌─────────┐
│  done  │      │  error  │
└────────┘      └─────────┘
 하루한도 O      하루한도 X
```

### 각 전이가 일어나는 코드

| 전이 | 위치 | 조건 |
|---|---|---|
| (없음) → `queued` | `submissions.py:124` | 업로드 성공 |
| `queued` → `running` | `run.py:CLAIM_NEXT_SQL` | 워커가 집음 |
| `running` → `done` | `run.py:178` | 평가 성공 + 결과 저장 |
| `running` → `error` | `run.py:188, 196` | `EvaluationError` 또는 예상 못한 예외 |
| `running` → `queued` | `run.py:86` | **워커 재시작 + 타임아웃 초과** |

### 왜 상태를 이렇게 관리하는가

**상태 하나가 다섯 가지를 결정한다:**
1. 하루 한도 카운트 (`done`만)
2. 동시 제출 제한 (`queued`/`running`이면 새 제출 불가)
3. 대기 순번 계산
4. 리더보드 표시 (`done` + `finished`만)
5. 파일 보존 정책 (`queued`/`running`이면 삭제 금지)

**그래서 상태가 잘못 남으면 시스템이 조용히 망가진다.**
가장 위험한 것: `running`인 채로 워커가 죽는 경우.
→ 그 팀은 **영원히 새 제출을 못 한다.** (동시 제출 제한에 걸려서)

---

## 5. `recover_stale_running` — 죽은 작업 되살리기

```python
def recover_stale_running(db: Session) -> None:
    """워커가 중간에 죽어 '평가중'에 멈춰있는 제출을 재시작 시 다시 대기열로 되돌린다."""
    threshold = now_utc() - dt.timedelta(seconds=MAX_WAIT_SECONDS + 300)
    stale = db.query(Submission).filter(
        Submission.status == SubmissionStatus.RUNNING,
        Submission.started_at < threshold,
    ).all()
    for submission in stale:
        logger.warning("오래된 '평가중' 제출을 대기열로 되돌립니다: submission=%s", submission.id)
        submission.status = SubmissionStatus.QUEUED
        submission.worker_id = None
        submission.started_at = None
    if stale:
        db.commit()
```

### 무엇을 해결하는가

**워커가 `running` 상태에서 죽는 경우:**
- Ctrl+C
- OOM Kill
- WSL 재시작 / 노트북 절전
- 예외를 못 잡고 프로세스 종료

이때 DB에는 `status='running'`이 남는다. **아무도 처리하지 않는 유령 작업.**

### 임계값 `MAX_WAIT_SECONDS + 300`

```python
MAX_WAIT_SECONDS = int(os.environ.get("EVAL_MAX_WAIT_SECONDS", "1800"))   # 30분
threshold = now_utc() - dt.timedelta(seconds=1800 + 300)                   # 35분
```

**왜 여유 300초를 두는가?**
정상적인 평가도 최대 30분까지 걸릴 수 있다. 30분 정확히로 자르면
**정상 실행 중인 작업을 되돌릴 위험**이 있다.
5분 여유를 두면 "35분 넘게 running = 확실히 죽었다"고 판단할 수 있다.

### **이 함수의 한계 — 반드시 알 것**

```python
def main() -> None:
    db = SessionLocal()
    try:
        recover_stale_running(db)     # ← 시작할 때 딱 한 번만
    finally:
        db.close()

    while True:
        ...   # 루프 안에서는 안 부른다
```

**호출 시점이 "워커 시작 시" 뿐이다.**

**문제 시나리오:**
```
10:00  워커가 제출 5를 집음 (running)
10:03  워커 프로세스가 죽음
       → 아무도 워커를 재시작하지 않음
       → 제출 5는 영원히 running
       → 그 팀은 영원히 제출 불가
```

**워커를 재시작하면** 35분 뒤 조건을 만족해 복구된다.
하지만 **워커가 계속 떠 있는데 개별 작업만 유실되는 경우**(예: `process_submission`에서
프로세스가 안 죽고 무한 대기)는 복구되지 않는다.

**개선 방법:**
```python
last_recovery = 0.0
while True:
    if time.monotonic() - last_recovery > 300:      # 5분마다
        db = SessionLocal()
        try:
            recover_stale_running(db)
        finally:
            db.close()
        last_recovery = time.monotonic()
    ...
```

**또는** 관리자 화면에 "이 제출을 대기열로 되돌리기" 버튼을 두는 것.
`docs/operational-error-handling.md`에 수동 절차가 있을 가능성이 높다 — 확인해볼 것.

### **더 위험한 시나리오 — 워커가 살아있는데 복구가 도는 경우**

만약 `recover_stale_running`을 루프 안에서 자주 부른다면,
**정상 실행 중인 35분짜리 평가를 `queued`로 되돌릴 수 있다.**
그러면 같은 모델이 두 번 평가되고, DRFC의 S3 model/ 폴더가 충돌한다.

**제대로 하려면 하트비트가 필요하다:**
```python
# Submission에 heartbeat_at 컬럼 추가
# 워커가 평가 중 30초마다 UPDATE submissions SET heartbeat_at = now()
# 복구 조건: heartbeat_at < now() - interval '2 minutes'
```

**이게 분산 시스템에서 "죽음을 감지하는" 표준 방법이다.**
시간 기반 추정은 항상 오탐/미탐 사이의 트레이드오프다.

---

## 6. `process_submission` — 중첩 예외 처리 구조

```python
def process_submission(submission_id: int) -> None:
    db = SessionLocal()
    try:                                          # ── (A) 세션 수명
        submission = db.get(Submission, submission_id)
        if submission is None:
            return
        work_dir = settings.storage_dir / "work" / str(submission.id)
        work_dir.mkdir(parents=True, exist_ok=True)

        try:                                      # ── (B) 평가 파이프라인
            s3 = drfc.get_s3_client()
            ...
            submission.status = SubmissionStatus.DONE
            db.commit()
        except drfc.EvaluationError as exc:       # ── (C) 예상된 실패
            db.rollback()
            submission = db.get(Submission, submission_id)
            submission.status = SubmissionStatus.ERROR
            submission.error_message = str(exc)[:2000]
            submission.finished_at = now_utc()
            db.commit()
        except Exception as exc:                  # ── (D) 예상 못한 실패
            db.rollback()
            ... 동일 ...
            logger.exception(...)
        finally:                                  # ── (E) 항상 정리
            shutil.rmtree(work_dir, ignore_errors=True)
            prune_finished_team_files(db, submission_id)
    finally:                                      # ── (F) 항상 세션 닫기
        db.close()
```

### 6-1. 왜 예외를 두 종류로 나누는가

```python
except drfc.EvaluationError as exc:
    submission.error_message = str(exc)[:2000]
except Exception as exc:
    submission.error_message = f"예상치 못한 오류: {exc}"[:2000]
    logger.exception(...)      # ← 스택트레이스까지 로그
```

| | `EvaluationError` | 그 외 |
|---|---|---|
| 의미 | **예상된 실패**. 참가자 파일이 잘못됐거나 DRFC가 실패 | **버그 또는 인프라 문제** |
| 메시지 | 참가자가 읽고 조치할 수 있는 안내 | "예상치 못한 오류" 접두어 |
| 로그 | `logger.error` (한 줄) | `logger.exception` (스택트레이스) |

**[전공] 이 구분이 왜 중요한가**

`EvaluationError`는 `drfc.py`에서 **의도적으로** 던지는 것들이다:
```python
raise EvaluationError(MINIO_RAW_FORMAT_HELP)                    # 참가자 실수
raise EvaluationError("압축 파일에서 model_metadata.json을 찾을 수 없습니다...")
raise EvaluationError(f"dr-start-evaluation 실행 실패 (exit={...})...")
```

이건 **도메인 오류**다. 정상적인 운영 중에 발생하며, 사용자에게 그대로 보여줄 수 있다.

`Exception`은 **우리가 예상 못 한 것**이다. `KeyError`, `AttributeError`, 네트워크 예외 등.
스택트레이스를 남겨야 **디버깅이 가능하다.**

> **원칙: 예외 계층을 만들면 "누구의 잘못인가"를 코드로 표현할 수 있다.**
> `EvaluationError` = 사용자/외부, `Exception` = 우리.

### 6-2. **`except Exception`이 여기서는 옳은 이유**

일반적으로 `except Exception`(광범위 포획)은 나쁜 습관이다. 진짜 버그를 숨긴다.

**하지만 워커 루프에서는 다르다.** 주석이 정확히 설명한다:
```python
except Exception as exc:  # noqa: BLE001 - 예상 못한 오류도 '오류' 상태로 남겨 재제출 가능하게 함
```

**만약 안 잡는다면:**
```
제출 5 처리 중 KeyError 발생
 → process_submission이 예외를 던짐
 → main()의 while 루프까지 전파
 → 워커 프로세스 종료
 → 제출 5는 'running'에 멈춤
 → 큐의 나머지 제출도 전부 처리 안 됨
```

**한 건의 실패가 서비스 전체를 멈춘다.**

**잡으면:**
```
제출 5 → error 상태 + 스택트레이스 로그
제출 6 → 정상 처리 계속
```

> **핵심: 배치/워커 루프에서는 "한 건의 실패를 격리"하는 것이 최우선이다.**
> 단, **반드시 `logger.exception`으로 스택트레이스를 남겨야 한다.**
> 안 남기면 그건 그냥 오류를 삼키는 것이다. 이 코드는 남긴다. ✅

### 6-3. `db.rollback()` 먼저 하는 이유

```python
except drfc.EvaluationError as exc:
    db.rollback()                                 # ← 먼저
    submission = db.get(Submission, submission_id)  # ← 다시 조회
    submission.status = SubmissionStatus.ERROR
```

**왜 롤백하는가?**
예외가 난 시점에 세션에 **커밋되지 않은 변경**이 있을 수 있다.
예를 들어 `db.add(result)` 후 `db.commit()` 직전에 예외가 났다면.
그 상태로 다시 `commit()`하면 **의도치 않은 데이터**가 들어간다.

**왜 `submission`을 다시 조회하는가?**
`rollback()`은 세션의 모든 객체를 **만료(expire)** 시킨다.
기존 `submission` 객체는 상태가 무효하다. `db.get()`으로 다시 가져오면
Identity Map에서 찾되 만료됐으므로 **DB에서 새로 읽는다.**

**[전공] 이건 SQLAlchemy를 실전에서 써봐야 아는 지식이다.**
롤백 후 stale 객체를 만지면 `DetachedInstanceError`나 조용한 오동작이 난다.

### 6-4. `error_message[:2000]` — 길이 제한

```python
submission.error_message = str(exc)[:2000]
```

`error_message`는 `Text` 타입이라 길이 제한이 없다. 그런데 왜 자르나?

`drfc.py`의 에러 메시지를 보면:
```python
raise EvaluationError(
    f"dr-start-evaluation 실행 실패 (exit={result.returncode})\nSTDOUT: {tail_out}\nSTDERR: {tail_err}"
)
```
`tail_out`/`tail_err`가 각각 2000자다. 합치면 4000자 이상.

**이걸 그대로 저장하면:**
- 참가자 화면(`submit.html`)에 4000자 로그가 통째로 뜬다
- DB가 커진다
- 로그에 도배된다

**2000자면 원인 파악에 충분하고 화면도 견딜 만하다.** 실용적 절충.

> **더 나은 방법**: 참가자에게는 짧은 요약만 보여주고,
> 전체 로그는 `storage/eval_logs/{id}.log`에 있으니 관리자만 보게 한다.
> **실제로 그 로그 파일이 이미 존재한다.** 화면 표시만 다듬으면 된다.

### 6-5. `finally` — 반드시 실행되는 정리

```python
finally:
    shutil.rmtree(work_dir, ignore_errors=True)
    prune_finished_team_files(db, submission_id)
```

**`work_dir` 삭제:**
```python
work_dir = settings.storage_dir / "work" / str(submission.id)
```
여기에 모델 압축을 푼다. **건당 수백 MB.**
성공하든 실패하든 지워야 한다. → `finally`

`ignore_errors=True`: 삭제 실패해도 예외를 안 던진다.
**정리 실패가 평가 결과를 되돌리면 안 되기 때문.**

**`prune_finished_team_files`:**
```python
def prune_finished_team_files(db: Session, submission_id: int) -> None:
    try:
        submission = db.get(Submission, submission_id)
        if submission is None:
            return
        if prune_team_files(submission.team):
            db.commit()
    except Exception:  # noqa: BLE001 - 정리 실패가 평가 결과를 되돌리면 안 된다
        db.rollback()
        logger.exception("보존 정책 적용 실패: submission=%s", submission_id)
```

**같은 철학: 정리는 best-effort.** 실패해도 로그만 남기고 넘어간다.

**왜 시즌 종료까지 안 기다리나?**
`retention.py` docstring:
> 모델 아카이브가 건당 250MB 안팎이라 전부 남기면 시즌 하나로 디스크가 찬다
> (10팀 × 5회/일 × 2주 ≈ 175GB).

**175GB.** 노트북 디스크가 먼저 찬다. 그래서 **평가가 끝나는 즉시** 최고기록 외 파일을 지운다.

**중요한 안전장치:**
```python
# app/retention.py:64-66
if submission.status.value in ACTIVE_SUBMISSION_STATUSES:
    continue      # 대기/평가중 제출의 파일은 절대 안 지운다
```
이게 없으면 **워커가 평가하려는 모델 파일을 스스로 지우는** 사태가 난다.

---

## 7. 평가 파이프라인 — 8단계

```python
s3 = drfc.get_s3_client()

# 1) 시작 시점 마커 기록
start_marker = None
existing_key = drfc.find_latest_metrics_key(s3)
if existing_key:
    head = s3.head_object(Bucket=drfc.get_bucket(), Key=existing_key)
    start_marker = head["LastModified"]

# 2) 모델 파일 경로 해석
model_file = resolve_storage_path(submission.model_path)
if not model_file.is_file():
    raise drfc.EvaluationError("업로드된 모델 파일을 찾을 수 없습니다 ...")

# 3) 압축 해제 → S3 업로드
drfc.inject_model(s3, model_file, work_dir)

# 4) 평가 실행 (블로킹)
drfc.run_evaluation_blocking(RUN_EVAL_SCRIPT, DRFC_DIR, DR_ENV_FILE, MAX_WAIT_SECONDS,
                             log_path=log_path_for(submission.id))

# 5) 새 metrics 찾기
metrics_key = drfc.find_latest_metrics_key(s3, after=start_marker)
if metrics_key is None:
    raise drfc.EvaluationError("평가가 끝났지만 새 metrics 파일을 찾지 못했습니다.")
metrics = drfc.download_json(s3, metrics_key)

# 6) 원본 백업
metrics_file.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

# 7) 파싱
finish_status, lap_time, off_track = drfc.parse_evaluation_result(metrics, settings.online_eval_laps)

# 8) 영상 다운로드 + 결과 저장
video_key = drfc.download_video(s3, settings.videos_dir / video_rel_path)
db.add(EvaluationResult(...))
submission.status = SubmissionStatus.DONE
db.commit()
```

### 7-1. **`start_marker` — 이 코드에서 가장 영리한 트릭**

**문제**: DRFC는 평가 결과를 `{prefix}/metrics/evaluation/evaluation-*.json` 에 남긴다.
파일명에 타임스탬프가 들어가지만 **우리가 그 규칙을 신뢰할 수 없다.**
그리고 **이전 평가의 파일들이 그대로 남아 있다.**

만약 그냥 "가장 최근 파일"을 집으면?
→ 평가가 실패해서 새 파일이 안 생겼을 때 **이전 팀의 결과를 이 팀 것으로 저장**한다.
**대회 결과가 완전히 오염된다.**

**해결:**
```python
# 평가 시작 전
start_marker = 현재 가장 최근 metrics의 LastModified

# 평가 후
metrics_key = find_latest_metrics_key(s3, after=start_marker)
if metrics_key is None:
    raise EvaluationError("평가가 끝났지만 새 metrics 파일을 찾지 못했습니다.")
```

```python
# worker/drfc.py:189-190
if after is not None and obj["LastModified"] <= after:
    continue
```

**"시작 시각 이후에 생긴 것만" 인정한다.** 없으면 명확한 에러.

**[쉬움]**
시험 보기 전에 답안지 상자에 뭐가 들어있는지 시각을 적어둔다.
시험 후 **그 시각 이후에 들어온 답안지만** 내 것으로 인정한다.

**[전공] 이 패턴의 이름**: watermark / high-water mark.
스트림 처리, 증분 백업, CDC 등에서 널리 쓰인다.

**남은 취약점**: 시계가 다르면(S3 서버 vs 우리) 오차가 생긴다.
MinIO가 같은 머신이라 문제없지만, **분산 환경에서는 시계 기반 비교가 위험하다.**

### 7-2. `inject_model` — 참가자 모델을 DRFC가 읽는 위치로

```python
def inject_model(s3_client, archive_path: Path, work_dir: Path) -> None:
    extract_dir = work_dir / "extracted"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        _extract_archive(archive_path, extract_dir)
    except (zipfile.BadZipFile, tarfile.TarError) as exc:
        raise EvaluationError(f"모델 압축 파일을 열 수 없습니다: {exc}") from exc

    extract_dir = _find_model_root(extract_dir)

    model_key_prefix = f"{prefix}/model/"
    existing = s3_client.list_objects_v2(Bucket=bucket, Prefix=model_key_prefix)
    for obj in existing.get("Contents", []):
        s3_client.delete_object(Bucket=bucket, Key=obj["Key"])

    uploaded = 0
    for file_path in extract_dir.rglob("*"):
        if file_path.is_file():
            rel_key = model_key_prefix + str(file_path.relative_to(extract_dir)).replace("\\", "/")
            s3_client.upload_file(str(file_path), bucket, rel_key)
            uploaded += 1
    if uploaded == 0:
        raise EvaluationError("압축 파일 안에 업로드할 모델 파일이 없습니다.")
```

**왜 S3에 올려야 하나?**
memory에 기록된 사실:
> `dr-start-evaluation`은 **모델 경로 인자를 받지 않고 항상 `{DR_LOCAL_S3_MODEL_PREFIX}/model/`을 평가**한다
> DRFC 모델 로딩 메커니즘: robomaker는 S3 API로만 모델 접근 (filesystem 마운트 없음)

**즉 "이 모델을 평가해줘"라고 말할 방법이 없다. 정해진 자리에 갖다 놓는 수밖에 없다.**

**기존 내용을 지우는 이유:**
이전 팀의 파일이 남아 있으면 섞인다. 파일 개수가 다를 수 있으므로 **덮어쓰기만으로는 부족**하다.

> **[전공] 위험**: `delete` → `upload` 사이에 프로세스가 죽으면 `model/`이 **빈 상태**로 남는다.
> 다음 평가가 "모델 없음"으로 실패한다. 순차 처리라 복구는 되지만,
> **원자적 교체가 불가능한 구조**임을 알고 있어야 한다.
> (더 안전한 방법: 제출별 prefix에 올리고 `DR_LOCAL_S3_MODEL_PREFIX`를 바꿔 실행)

**`.replace("\\", "/")`**: S3 키는 항상 `/`. Windows에서 만든 경로가 섞이면 깨진다.

### 7-3. `_find_model_root` — 참가자의 압축 방식이 제각각인 문제

```python
def _find_model_root(extract_dir: Path) -> Path:
    if (extract_dir / "model_metadata.json").is_file():
        return extract_dir

    candidates = [p for p in extract_dir.rglob("model_metadata.json") if p.is_file()]
    if not candidates:
        if _looks_like_minio_raw_dump(extract_dir):
            raise EvaluationError(MINIO_RAW_FORMAT_HELP)
        raise EvaluationError("압축 파일에서 model_metadata.json을 찾을 수 없습니다. ...")

    return min(candidates, key=lambda p: len(p.relative_to(extract_dir).parts)).parent
```

**참가자가 압축하는 방식이 다양하다:**
```
방식1:  model.tar.gz  →  model_metadata.json, *.pb, ...        (내용물만)
방식2:  model.tar.gz  →  my-model/model_metadata.json, ...     (폴더 포함)
방식3:  model.tar.gz  →  a/b/c/model/model_metadata.json       (경로가 깊음)
```

**`model_metadata.json`을 랜드마크로 삼아 재귀 탐색**한다.
여러 개면 **가장 얕은 것**(`min(..., key=경로 깊이)`)을 쓴다 — 최상위 모델 폴더일 가능성이 높다.

**[전공] 좋은 사용자 경험 설계다.**
"정확히 이렇게 압축하세요"라고 요구하고 어기면 실패시키는 대신,
**흔한 변형을 자동으로 흡수**한다. 사용자 문의가 크게 줄어든다.

### 7-4. **MinIO 원본 덤프 감지 — 실제 참가자 실수 대응**

```python
def _looks_like_minio_raw_dump(extract_dir: Path) -> bool:
    """MinIO는 오브젝트를 '폴더 + xl.meta(+ part.N)' 형태로 디스크에 저장하기 때문에,
    참가자가 S3에서 정상적으로 내보내지 않고 MinIO 데이터 폴더를 그대로 압축하면
    파일 이름은 그럴듯해 보여도 실제 모델 파일이 하나도 없다."""
    return next(extract_dir.rglob("xl.meta"), None) is not None
```

**[쉬움]**
냉장고 안의 음식을 가져오랬더니 **냉장고 부품**을 가져온 것.
겉보기엔 이름이 맞는데 안에 음식이 없다.

**[전공]**
MinIO의 디스크 레이아웃:
```
bucket/prefix/model/model_metadata.json/     ← 폴더다!
    xl.meta                                   ← 메타데이터
    part.1                                    ← 실제 데이터
```
참가자가 `~/deepracer-for-cloud/data/minio/bucket/...` 를 그대로 `tar`로 묶으면
**파일명은 `model_metadata.json`인데 그게 디렉터리**다.

일반적인 에러 메시지("model_metadata.json을 못 찾음")로는
참가자가 **무엇을 잘못했는지 절대 알 수 없다.**

그래서 구체적 해결법을 담은 에러를 준다:
```python
MINIO_RAW_FORMAT_HELP = (
    "MinIO의 내부 저장 폴더를 그대로 압축한 것으로 보입니다 (xl.meta / part.N 파일이 들어 있음). ...\n"
    "  aws s3 sync s3://$DR_LOCAL_S3_BUCKET/$DR_LOCAL_S3_MODEL_PREFIX/model/ ./my-model/ ...\n"
    "  tar -zcvf rl-deepracer-sagamer.tar.gz ./my-model"
)
```

> **[전공] 이것이 좋은 에러 메시지의 정석이다.**
> 1. **무엇이 잘못됐는지** (MinIO 내부 폴더를 압축함)
> 2. **왜 그렇게 판단했는지** (xl.meta 파일이 있음)
> 3. **어떻게 고치는지** (구체적 명령어)
>
> 이런 코드는 **실제로 사용자가 그 실수를 하는 걸 겪어봐야** 나온다.
> memory에 "참가자 제출 형식 함정(중요)"으로 기록된 그 사건의 결과물이다.

`next(iterator, None)`: 제너레이터에서 첫 원소만 꺼내고 없으면 `None`.
`rglob`이 전체를 순회하지 않고 **첫 발견에서 멈춘다** — 큰 디렉터리에서 효율적.

### 7-5. `parse_evaluation_result` — 도메인 규칙의 핵심

```python
def parse_evaluation_result(metrics: dict, required_laps: int) -> tuple[str, float | None, int]:
    trials = sorted(metrics.get("metrics", []), key=lambda t: t.get("trial", 0))
    off_track_total = sum(t.get("off_track_count", 0) for t in trials)
    completed = [t for t in trials if t.get("completion_percentage") == 100]

    if len(completed) >= required_laps:
        total_ms = sum(t["elapsed_time_in_milliseconds"] for t in completed[:required_laps])
        return "finished", total_ms / 1000.0, off_track_total
    return "timeout", None, off_track_total
```

**규칙**: "3바퀴 모두 100% 완주해야 완주. 랩타임은 그 3바퀴 합계."

**방어적 코딩이 곳곳에 있다:**
| 코드 | 방어하는 것 |
|---|---|
| `metrics.get("metrics", [])` | 키가 없어도 `KeyError` 안 남 |
| `t.get("trial", 0)` | trial 필드가 없어도 정렬 가능 |
| `t.get("off_track_count", 0)` | 없으면 0 |
| `t.get("completion_percentage") == 100` | 없으면 `None == 100` → `False` |

**하지만 `t["elapsed_time_in_milliseconds"]`는 `[]` 접근이다.**
`completed` 필터를 통과한 trial에는 이 필드가 반드시 있다는 가정.
**없으면 `KeyError`가 나고 `except Exception`에 잡혀 "예상치 못한 오류"가 된다.**
→ 스택트레이스가 남아 진단은 가능하다. 의도적 판단으로 볼 수 있다.

**`completed[:required_laps]`의 의미:**
4바퀴를 성공했어도 **앞 3개만** 센다. `DR_EVAL_NUMBER_OF_TRIALS`가 3보다 크게 설정된 경우 대비.

**이 함수는 순수 함수다** — DB도 네트워크도 안 쓴다.
그래서 `tests/test_evaluation_parsing.py`로 쉽게 테스트된다. **좋은 설계의 증거.**

### 7-6. `download_video` — 폴백 전략

```python
VIDEO_KEY_SUFFIXES = (
    "mp4/camera-pip/0-video.mp4",
    "mp4/camera-45degree/0-video.mp4",
    "mp4/camera-topview/0-video.mp4",
)
MIN_VALID_VIDEO_BYTES = 10 * 1024

def download_video(s3_client, dest_path: Path) -> str | None:
    for suffix in VIDEO_KEY_SUFFIXES:
        key = f"{prefix}/{suffix}"
        try:
            head = s3_client.head_object(Bucket=bucket, Key=key)
        except ClientError:
            continue                                  # 없는 앵글 → 다음
        if head.get("ContentLength", 0) < MIN_VALID_VIDEO_BYTES:
            continue                                  # 깨진 파일 → 다음
        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            s3_client.download_file(bucket, key, str(dest_path))
        except ClientError:
            continue
        return key
    return None
```

**주석의 사건 기록:**
> 2026-07-26: camera-topview가 261바이트로만 생성되고 있었고, 그걸 고정으로 받는 바람에
> 리더보드 영상이 계속 비어 있었다.

**[전공] 여기서 배울 것 3가지**

1. **`head_object`로 먼저 확인.** 다운로드 전에 크기를 본다. 261바이트 파일을 받을 필요가 없다.
2. **크기 임계값으로 "깨진 파일" 판별.**
   ```python
   # 정상 영상은 10MB를 넘고 깨진 것은 수백 바이트라, 그 사이 어디를 잘라도 판별된다.
   MIN_VALID_VIDEO_BYTES = 10 * 1024
   ```
   **10KB라는 숫자에 근거가 있다.** 마법의 숫자에 이유를 남기는 좋은 예.
3. **실패해도 예외를 안 던진다.**
   ```python
   """영상이 없어도 순위·기록에는 영향이 없으므로(리더보드가 "—"로 표시) 예외를 던지지 않는다."""
   ```
   **핵심 기능(랩타임)과 부가 기능(영상)을 구분한 것.**
   영상이 없다고 평가 결과를 통째로 버리면 안 된다.

호출부:
```python
video_key = drfc.download_video(s3, settings.videos_dir / video_rel_path)
if video_key is None:
    logger.warning("쓸 만한 평가 영상을 찾지 못했습니다: submission=%s", submission.id)
...
video_path=video_rel_path if video_key else None,
```
**영상이 없으면 `video_path=None`** → 5단계에서 본 "—" 표시로 이어진다.

### 7-7. **덮어쓰기 위험 — 이 설계의 근본 제약**

```python
# worker/drfc.py:10-12
- 평가 영상: s3://{bucket}/{DR_LOCAL_S3_MODEL_PREFIX}/mp4/camera-topview/0-video.mp4
  주의: 이 경로는 평가마다 같은 키로 덮어써진다. 평가 직후 즉시 내려받아야 한다
  (지금 설계상 워커가 한 번에 한 건씩만 순차 처리하므로 안전하다).
```

**이 시스템은 "워커 1대, 순차 처리"에 강하게 의존한다:**
- S3 `model/` 폴더 — 공유 자원
- `mp4/camera-*/0-video.mp4` — 고정 키, 덮어써짐
- DRFC Swarm 스택 이름 `deepracer-eval-${DR_RUN_ID}` — 공유

**워커를 2대로 늘리면 `SKIP LOCKED`가 DB 레벨 경쟁은 막지만,
DRFC/S3 레벨에서 서로를 덮어쓴다.**

→ `multi-laptop-worker-pool.md`에서 다루는 주제일 것이다.
**해결하려면 워커마다 다른 `DR_RUN_ID`와 다른 S3 prefix를 써야 한다.**

> **교훈: 동시성 안전은 "가장 약한 고리"가 결정한다.**
> DB만 안전하게 만들어도 파일시스템/외부 서비스가 공유 자원이면 소용없다.

---

## 8. 외부 프로세스 호출 — `subprocess`

```python
def run_evaluation_blocking(script_path, drfc_dir, env_file, max_wait_seconds, log_path=None):
    env = os.environ.copy()
    env["DRFC_DIR"] = drfc_dir
    env["DR_ENV_FILE"] = env_file
    env["MAX_WAIT_SECONDS"] = str(max_wait_seconds)
    if log_path is not None:
        env["EVAL_LOG_PATH"] = str(log_path)
    try:
        result = subprocess.run(
            ["bash", str(script_path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=max_wait_seconds + 120,
        )
    except subprocess.TimeoutExpired as exc:
        raise EvaluationError(f"run_evaluation.sh가 응답 없이 멈췄습니다: {exc}") from exc

    if result.returncode != 0:
        tail_out = result.stdout[-2000:]
        tail_err = result.stderr[-2000:]
        raise EvaluationError(f"dr-start-evaluation 실행 실패 (exit={result.returncode})\n...")
```

### 8-1. 리스트로 인자를 넘기는 것 = 셸 인젝션 방어

```python
subprocess.run(["bash", str(script_path)], ...)      # ✅ 안전
subprocess.run(f"bash {script_path}", shell=True)    # ❌ 위험
```

`shell=True`면 문자열이 셸에 해석된다. 경로에 `;`나 `$()`가 있으면 임의 명령 실행.
**리스트 형태는 셸을 거치지 않고 `execve`로 직접 실행**한다.

여기선 `script_path`가 코드에 고정되어 있어 실제 위험은 없지만, **원칙이 중요하다.**

### 8-2. `env=env` — 환경변수 전달

```python
env = os.environ.copy()      # 현재 환경을 복사하고
env["DRFC_DIR"] = drfc_dir   # 필요한 것만 추가/덮어씀
```

**`os.environ.copy()`가 왜 필요한가?**
`env=`를 주면 **그 dict가 전부**가 된다. `PATH`도 없어진다 → `bash`조차 못 찾는다.
복사해서 추가하는 것이 정석.

**여기서 전달되는 `DR_*` 변수들이 어디서 왔는지가 중요하다.**
`run_worker.sh`가 DRFC의 `bin/activate.sh`를 source한 뒤 파이썬을 실행하므로,
파이썬 프로세스의 `os.environ`에 이미 들어있다. 그게 자식 프로세스로 전파된다.

```
run_worker.sh
  └─ source bin/activate.sh   → DR_LOCAL_S3_BUCKET 등이 셸 환경에 로드
  └─ exec python -m worker.run  → 그 환경을 물려받음
       └─ subprocess.run(env=os.environ.copy() + 추가)  → 다시 물려줌
            └─ run_evaluation.sh
```

**환경변수가 3단계를 타고 흐른다.** 어느 한 단계라도 끊기면 `KeyError`.

### 8-3. `capture_output=True`의 함정

**stdout/stderr를 파이프로 받는다.** 그런데:

> **[전공] 데드락 위험**: `subprocess.run`은 내부적으로 `communicate()`를 써서
> stdout과 stderr를 **동시에** 읽으므로 안전하다.
> 하지만 `Popen` + `wait()`를 직접 쓰면서 파이프를 안 읽으면,
> 자식이 파이프 버퍼(보통 64KB)를 채우고 **영원히 블록**된다.
>
> `run_evaluation.sh`는 로그를 별로 안 뿜지만, DRFC 로그를 stdout으로 흘렸다면 위험했을 것이다.
> 실제로 로그는 **파일로 따로** 저장한다(`EVAL_LOG_PATH`). 좋은 분리다.

**메모리**: `capture_output`은 전체 출력을 메모리에 담는다.
출력이 GB 단위면 OOM. 여기선 짧아서 안전하다.

### 8-4. **타임아웃 이중 방어**

```python
timeout=max_wait_seconds + 120        # 파이썬 쪽 (1920초)
```
그리고 셸 스크립트 안:
```bash
MAX_WAIT_SECONDS="${MAX_WAIT_SECONDS:-1800}"   # 1800초
while [[ "$(running_task_count)" -gt 0 ]]; do
  if [[ "$elapsed" -ge "$MAX_WAIT_SECONDS" ]]; then
    echo "[run_evaluation] TIMEOUT: ${MAX_WAIT_SECONDS}초 초과" >&2
    capture_logs
    dr-stop-evaluation || true
    exit 2
  fi
```

**왜 두 겹인가?**
- **스크립트 타임아웃(1800초)**: 정상 경로. 로그를 저장하고 스택을 정리한 뒤 종료
- **파이썬 타임아웃(1920초)**: 스크립트 자체가 멈췄을 때의 최후 방어

**120초 여유**: 스크립트가 타임아웃 처리(로그 저장 + `dr-stop-evaluation`)를 할 시간.

**[전공] 계층적 타임아웃은 분산 시스템의 기본 패턴이다.**
바깥 계층이 안쪽보다 항상 길어야 한다. 반대면 안쪽이 정리할 기회를 못 얻는다.

> **주의**: `subprocess.run`의 `timeout`은 **자식 프로세스만** 죽인다.
> `bash`가 띄운 `docker` 자식들은 남을 수 있다(고아 프로세스).
> `dr-stop-evaluation`이 실행되지 않으면 **Swarm 스택이 남아** 다음 평가를 방해한다.
> → 그래서 스크립트 시작 부분에 이게 있다:
> ```bash
> echo "[run_evaluation] 이전 평가가 남아있지 않은지 확인 중..."
> dr-stop-evaluation || true
> wait_for_stack_removed
> ```
> **"시작할 때 청소한다"** 는 방어. 좋은 패턴이다.

### 8-5. `raise ... from exc` — 예외 체이닝

```python
raise EvaluationError(f"...") from exc
```

`from exc`가 있으면 스택트레이스에 **"직접적인 원인은 위 예외"** 로 원본이 함께 표시된다.
없으면 원본 예외가 사라져 디버깅이 어려워진다.

```
Traceback ...
  subprocess.TimeoutExpired: ...
The above exception was the direct cause of the following exception:
Traceback ...
  EvaluationError: run_evaluation.sh가 응답 없이 멈췄습니다
```

---

## 9. `run_evaluation.sh` — 셸 스크립트의 함정들

### 9-1. **`set -u`를 쓰지 않는 이유**

```bash
# set -u는 쓰지 않는다: DRFC의 dr-* 함수들이 내부적으로 activate.sh를 다시 source하는데
# (dr-update-env), 그 안에서 미설정 변수를 참조해 unbound variable로 죽는다.
set -eo pipefail
```

**`set -euo pipefail`은 셸 스크립트의 관용적 안전 설정이다:**
- `-e`: 명령이 실패하면 즉시 종료
- `-u`: 정의되지 않은 변수 참조 시 에러
- `-o pipefail`: 파이프라인 중 하나라도 실패하면 전체 실패

**하지만 남의 코드를 source하면 그 코드가 우리 규칙을 안 지킨다.**
DRFC의 `activate.sh`가 미설정 변수를 참조하므로 `-u`가 켜져 있으면 죽는다.

**[전공] 교훈**: 엄격 모드는 **내 코드에만** 적용할 수 있다.
외부 코드를 부를 때는 그 구간만 끄는 것이 현실적이다:

```bash
set +e +o pipefail
source bin/activate.sh "$DR_ENV_FILE"
set -e -o pipefail
```

**`run_worker.sh`에도 똑같은 패턴이 있다.** 일관성 있게 처리했다.

### 9-2. **`docker stack ps`의 함정 — 실제로 30분을 날린 버그**

```bash
running_task_count() {
  docker stack ps "$STACK_NAME" --filter desired-state=running -q 2>/dev/null | wc -l || true
}
```

주석:
```bash
# 주의: 그냥 `docker stack ps`를 쓰면 이미 끝난 태스크도 이력으로 계속 남아 있어
# 카운트가 절대 0이 되지 않는다(실제로 이 때문에 평가가 7분 만에 끝났는데도
# 30분 타임아웃까지 기다리는 버그가 있었다). 평가 컨테이너는 restart_policy가
# none이라 완료되면 DESIRED STATE가 shutdown으로 바뀌므로,
# desired-state=running인 것만 세야 "아직 돌고 있는지"를 정확히 알 수 있다.
```

**[쉬움]**
학생이 몇 명 남았는지 세려고 출석부를 봤는데,
**이미 집에 간 학생도 출석부에 그대로 적혀 있어서** 계속 "아직 다 있네"라고 판단한 것.

**[전공]**
Docker Swarm의 태스크는 **불변(immutable)** 이다.
컨테이너가 종료되면 태스크가 사라지는 게 아니라 **상태만 바뀌고 이력으로 남는다.**

```
NAME              DESIRED STATE   CURRENT STATE
eval_robomaker.1  Shutdown        Complete 5 minutes ago     ← 끝났지만 목록에 있음
```

`--filter desired-state=running`으로 **"아직 돌아야 하는" 태스크만** 센다.

**증상이 왜 진단하기 어려웠나:**
- 평가는 **정상적으로 성공**한다 (7분 만에)
- 결과 파일도 정상적으로 생긴다
- 단지 워커가 **23분을 더 기다린다**
- 에러도 안 나고 로그도 정상
- → "왜 이렇게 느리지?" 라고만 생각하게 된다

**[전공] 교훈: 외부 도구의 출력을 파싱할 때는 "무엇을 세고 있는지" 정확히 확인해야 한다.**
`wc -l`은 줄 수를 셀 뿐, **그 줄이 무슨 의미인지 모른다.**

### 9-3. **로그를 스택 삭제 전에 저장하기**

```bash
capture_logs() {
  [[ -n "${EVAL_LOG_PATH:-}" ]] || return 0
  mkdir -p "$(dirname "$EVAL_LOG_PATH")"
  {
    echo "=== robomaker ==="
    docker service logs "${STACK_NAME}_robomaker" --no-task-ids 2>&1 || echo "(로그 없음)"
    echo
    echo "=== rl_coach ==="
    docker service logs "${STACK_NAME}_rl_coach" --no-task-ids 2>&1 || echo "(로그 없음)"
  } > "$EVAL_LOG_PATH" 2>&1 || true
}
```

주석:
```bash
# DRFC는 DR_ROBOMAKER_MOUNT_LOGS=False이면 시뮬레이션 로그를 디스크에 남기지 않는다.
# 로그는 Swarm 서비스가 살아있는 동안만 `docker service logs`로 볼 수 있고,
# dr-stop-evaluation으로 서비스를 지우는 순간 영구히 사라진다.
# 평가가 실패했을 때 원인을 추적하려면 스택을 내리기 전에 반드시 받아둬야 한다.
```

**호출되는 3곳:**
```bash
# 1) 시작 실패
if [[ "$started" -eq 0 ]]; then
  capture_logs
  dr-stop-evaluation || true
  exit 3

# 2) 타임아웃
  capture_logs
  dr-stop-evaluation || true
  exit 2

# 3) 정상 완료
capture_logs
dr-stop-evaluation || true
```

**모든 종료 경로에서 로그를 먼저 저장하고 스택을 내린다.**

**[전공] "관측 가능성(observability)은 파괴 전에 확보해야 한다."**
장애 조사를 위한 정보는 **장애가 난 직후에만** 존재하는 경우가 많다.
"나중에 필요하면 다시 보지 뭐"가 통하지 않는다.

`|| true`가 곳곳에 있는 이유: **로그 저장 실패가 평가 결과를 망치면 안 되므로.**
`{ ... } > "$EVAL_LOG_PATH" 2>&1 || true` — 전체를 실패해도 넘어간다.

### 9-4. 2단계 대기 — 기동 확인 + 완료 대기

```bash
# 1) 태스크가 실제로 뜰 때까지 대기 (이미지 준비 등으로 몇십 초 걸릴 수 있다).
started=0
for _ in $(seq 1 "$START_TIMEOUT_SECONDS"); do
  if [[ "$(running_task_count)" -gt 0 ]]; then
    started=1; break
  fi
  sleep 1
done
if [[ "$started" -eq 0 ]]; then ... exit 3; fi

# 2) 살아있는 태스크가 0이 되면 평가가 끝난 것이다.
while [[ "$(running_task_count)" -gt 0 ]]; do
  if [[ "$elapsed" -ge "$MAX_WAIT_SECONDS" ]]; then ... exit 2; fi
  sleep 10
  elapsed=$((elapsed + 10))
done
```

**왜 2단계인가?**

만약 기동 확인 없이 바로 "0이 되면 끝"이라고 하면:
```
dr-start-evaluation -q  →  즉시 반환 (스택 배포만)
running_task_count      →  아직 0 (컨테이너가 아직 안 뜸)
→ "평가가 끝났다!"  ← 시작도 안 했는데
```
**즉시 성공으로 오판한다.**

`dr-start-evaluation -q`가 **비동기**라는 사실(memory에 기록됨)이 이 설계의 이유다.

**폴링 간격이 다른 것도 의도적이다:**
- 기동 확인: `sleep 1` (최대 300초) — **빨리 감지해야 시간 낭비가 없다**
- 완료 대기: `sleep 10` (최대 1800초) — **10분짜리 작업에 1초 폴링은 낭비**

### 9-5. 종료 코드로 실패 유형 구분

| exit | 의미 |
|---|---|
| 0 | 정상 완료 |
| 1 | `dr-start-evaluation` 호출 자체 실패 |
| 2 | 타임아웃 |
| 3 | 컨테이너가 기동되지 않음 |

파이썬 쪽은 `returncode != 0`으로만 판단하고 stdout/stderr를 메시지에 담는다.
**세분화된 종료 코드를 활용하면 더 나은 안내가 가능하다:**
```python
if result.returncode == 2:
    raise EvaluationError("평가가 제한 시간(30분)을 초과했습니다. 모델이 트랙을 못 도는 것 같습니다.")
```
**개선 여지로 기록할 만하다.**

---

## 10. `run_worker.sh` — 조용한 실패를 막는 방어

```bash
REQUIRED_DR_VARS=(DR_LOCAL_S3_BUCKET DR_LOCAL_S3_MODEL_PREFIX DR_LOCAL_S3_PROFILE)
missing=0
for var in "${REQUIRED_DR_VARS[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    echo "[run_worker] ERROR: 필수 환경변수 $var 가 로드되지 않았습니다." >&2
    missing=1
  fi
done
if [[ "$missing" -eq 1 ]]; then
  echo "[run_worker] DRFC_DIR=$DRFC_DIR DR_ENV_FILE=$DR_ENV_FILE 를 확인하세요." >&2
  exit 1
fi
```

주석이 이 코드가 태어난 이유를 정확히 기록한다:
```bash
# activate.sh가 조용히 실패하거나(예: run.env 경로 오류) 워커가 이 스크립트를
# 거치지 않고 직접 실행되면, DR_* 변수가 없는 채로 몇 시간을 떠 있다가
# 참가자 제출 시점에야 깊은 스택트레이스의 KeyError로 드러난다
# (2026-07-25 실제 발생). 여기서 즉시, 명확하게 실패시킨다.
```

### **[전공] 이것이 "fail fast"의 교과서적 사례다**

**나쁜 실패:**
```
09:00  워커 시작 (DR_LOCAL_S3_BUCKET 없음)  → 정상처럼 보임
09:00~14:00  아무 일 없음 (큐가 비어 있어서)
14:32  참가자가 제출
14:32  KeyError: 'DR_LOCAL_S3_BUCKET'  ← 5시간 30분 뒤에야 발견
       참가자는 "오류" 상태를 봄. 대회 진행이 멈춤
```

**좋은 실패:**
```
09:00  워커 시작 시도
09:00  [run_worker] ERROR: 필수 환경변수 DR_LOCAL_S3_BUCKET 가 로드되지 않았습니다.
       exit 1                                          ← 즉시
```

**차이는 5시간 반이 아니라 "누가 피해를 보는가"다.**
전자는 참가자가 피해를 보고, 후자는 운영자가 즉시 안다.

**`${!var:-}` 문법**: bash의 **간접 참조(indirect expansion)**.
`var="DR_LOCAL_S3_BUCKET"` 일 때 `${!var}` 는 `$DR_LOCAL_S3_BUCKET` 의 값.
`:-`는 미설정 시 빈 문자열(`set -u` 없이도 안전).

### `/tmp/sagemaker` 미리 만들기

```bash
mkdir -p /tmp/sagemaker 2>/dev/null || true
```

```bash
# DRFC는 평가 시작 시 _dr_ensure_sagemaker_dir로 /tmp/sagemaker가 있는지 확인하고,
# 없으면 sudo로 만들려 한다 — 비대화형 워커는 여기서 비밀번호를 못 넣어 실패한다.
# ... /tmp가 world-writable이라 sudo 없이도 만들 수 있다. 있기만 하면 DRFC가 sudo를 호출하지 않으므로 미리 만들어 둔다.
# /tmp는 WSL 재시작 시 비워지므로 워커를 띄울 때마다 확인한다.
```

**[전공] 외부 도구의 동작을 소스를 읽어 파악하고 우회한 것이다.**
"sudo가 필요하다"는 표면적 결론에서 멈추지 않고
"왜 필요한가 → 실제로는 안 필요하다 → 조건만 만족시키면 된다"까지 파고들었다.

`exec` 사용:
```bash
exec "$PROJECT_DIR/.venv/bin/python" -m worker.run
```
`exec`는 **현재 셸 프로세스를 파이썬으로 교체**한다. 새 프로세스를 만들지 않는다.
→ 프로세스가 하나 줄고, **시그널(Ctrl+C, SIGTERM)이 파이썬에 직접 전달**된다.
`exec` 없이 그냥 실행하면 bash가 부모로 남아 시그널이 제대로 전파 안 될 수 있다.

---

## 11. S3/MinIO 연동

### 11-1. WSL2 localhost 문제

```python
def resolve_s3_endpoint() -> str:
    endpoint = os.environ.get("DR_LOCAL_S3_ENDPOINT_URL", "")
    if endpoint and "localhost" not in endpoint and "127.0.0.1" not in endpoint:
        return endpoint
    return f"http://{_get_wsl_ip()}:9000"


def _get_wsl_ip() -> str:
    result = subprocess.run(["ip", "-4", "route", "get", "1.1.1.1"], capture_output=True, text=True, check=True)
    tokens = result.stdout.split()
    for i, tok in enumerate(tokens):
        if tok == "src" and i + 1 < len(tokens):
            return tokens[i + 1]
    raise EvaluationError("WSL IP를 확인할 수 없습니다 ...")
```

**[쉬움]**
"우리 집"이라고 말했는데 컴퓨터가 다른 집을 찾아갔다.
그래서 **정확한 주소(IP)를 직접 알아내서** 쓴다.

**[전공]**
WSL2는 경량 VM이다. Docker Swarm 컨테이너에서 `localhost`는 **컨테이너 자신**을 가리키지,
WSL 호스트의 MinIO가 아니다. 네트워크 네임스페이스가 다르다.

`ip route get 1.1.1.1`은 "1.1.1.1로 가려면 어느 인터페이스/소스 IP를 쓰나"를 묻는 것.
출력:
```
1.1.1.1 via 172.20.0.1 dev eth0 src 172.20.5.3 uid 1000
                                     ^^^^^^^^^^ 이게 우리 IP
```
`src` 다음 토큰이 로컬 IP다.

**같은 로직이 `run_evaluation.sh`에도 있다:**
```bash
DRFC_WSL_IP=$(ip -4 route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')
```

> **[전공] 중복이다.** 파이썬과 bash에 같은 로직이 두 벌 있다.
> 한쪽만 고치면 어긋난다. 하지만 **각자 독립적으로 실행 가능해야 하므로**
> 공유하기 어려운 구조이기도 하다. 알고 있어야 할 기술 부채.

### 11-2. boto3 설정

```python
def get_s3_client():
    profile = os.environ.get("DR_LOCAL_S3_PROFILE", "minio")
    session = boto3.Session(profile_name=profile)
    return session.client(
        "s3",
        endpoint_url=resolve_s3_endpoint(),
        config=BotoConfig(connect_timeout=5, read_timeout=15, retries={"max_attempts": 3}),
    )
```

- **`profile_name`**: `~/.aws/credentials`의 `[minio]` 프로필에서 액세스 키를 읽는다.
  **코드에 키가 없다.** 좋다
- **`endpoint_url`**: AWS가 아니라 로컬 MinIO를 가리킨다.
  **S3 API 호환이라 boto3를 그대로 쓸 수 있다** — 오브젝트 스토리지의 사실상 표준
- **타임아웃**: 기본값은 60초씩이라 MinIO가 죽었을 때 **오래 매달린다**.
  5초/15초로 줄여 빨리 실패하게 했다
- **재시도 3회**: 일시적 네트워크 오류는 넘긴다

**[전공] 타임아웃과 재시도는 모든 외부 호출의 필수 항목이다.**
기본값을 그대로 쓰면 장애 시 시스템 전체가 멈춘다.

### 11-3. `get_paginator` — 1000개 제한 우회

```python
paginator = s3_client.get_paginator("list_objects_v2")
for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/metrics/"):
    for obj in page.get("Contents", []):
```

`list_objects_v2`는 **한 번에 최대 1000개**만 반환한다.
더 있으면 `NextContinuationToken`으로 이어 받아야 한다.
`paginator`가 그 반복을 자동화한다.

**직접 `list_objects_v2`를 부르면 1001번째부터 조용히 누락된다.**
평가를 1000번 넘게 돌리면 최신 metrics를 못 찾게 된다. **잠재적 버그를 미리 막은 것.**

> 참고: `inject_model`의 기존 파일 삭제는 paginator를 안 쓴다:
> ```python
> existing = s3_client.list_objects_v2(Bucket=bucket, Prefix=model_key_prefix)
> ```
> 모델 파일이 1000개를 넘을 일은 없지만 **일관성 관점에서는 아쉽다.**

---

## 12. 워커가 죽으면 어떻게 되는가 — 시점별 정리

**이 표를 설명할 수 있으면 6단계는 끝이다.**

| 죽는 시점 | DB 상태 | 파일 상태 | 복구 |
|---|---|---|---|
| claim 직전 | `queued` | 모델 있음 | 다음 폴링에 정상 처리 |
| claim 직후 (커밋 전) | `queued` (롤백) | 모델 있음 | 락 해제 → 다음에 처리 |
| claim 커밋 후 | **`running`** | 모델 있음 | 워커 재시작 + 35분 후 `queued`로 복구 |
| 모델 S3 업로드 중 | `running` | S3 `model/`이 **불완전** | 재시도 시 `inject_model`이 지우고 다시 올림 ✅ |
| 평가 실행 중 | `running` | Swarm 스택 **남음** | 재시도 시 스크립트 시작 부분에서 `dr-stop-evaluation` ✅ |
| metrics 파싱 중 | `running` | 결과는 S3에 있음 | 재시도 시 `start_marker`가 새로 잡혀 **이전 결과를 못 찾음** ⚠️ |
| `db.commit()` 직전 | `running` | 영상 파일 저장됨 | 재평가되어 **다른 결과**가 나올 수 있음 ⚠️ |
| `db.commit()` 직후 | **`done`** | 전부 저장됨 | 완료 ✅ |
| `finally` 정리 중 | `done` | `work_dir` 남음 | 디스크만 좀 먹음. 수동 정리 |

### ⚠️ 표시 항목의 의미

**"metrics 파싱 중 죽으면 이전 결과를 못 찾는다"**
재시도 시 `start_marker`를 새로 잡으므로, 이미 생성된 metrics는 "이전 것"으로 분류된다.
평가를 처음부터 다시 돌리므로 **결과적으로는 맞다.** 시간만 10분 더 든다.

**"재평가되어 다른 결과가 나올 수 있다"**
시뮬레이션은 완전히 결정론적이지 않을 수 있다. 같은 모델이 두 번째엔 다른 랩타임을 낼 수 있다.
**"정확히 한 번(exactly-once)"이 아니라 "최소 한 번(at-least-once)"** 처리다.

**[전공] 분산 시스템에서 exactly-once는 매우 어렵다.**
일반적 해법은 **멱등성(idempotency)**: 같은 작업을 여러 번 해도 결과가 같게 만드는 것.
여기서는 시뮬레이션 자체가 멱등하지 않아 근본적으로 불가능하다.

**하지만 실질적 피해는 없다:**
- 결과는 하나만 저장된다 (`EvaluationResult.submission_id` unique)
- 랩타임이 조금 달라도 대회 공정성에 큰 영향이 없다
- 발생 확률이 극히 낮다 (10분 중 몇 초 구간)

> **알고 있는 것과 모르는 것의 차이가 여기 있다.**
> "완벽하지 않지만 이 규모에서는 문제없다"고 **판단**한 것과,
> 그냥 모르는 것은 다르다.

---

## 13. 자가 점검 질문

1. 평가를 `POST /submit` 안에서 실행하면 생기는 문제 6가지를 나열하라.
2. Celery+Redis 대신 DB 큐를 쓸 때 "진실의 원천이 하나"라는 게 왜 결정적인가?
3. 폴링의 지연 비용을 계산하고, 왜 이 시스템에서 무시할 만한지 설명하라.
4. `FOR UPDATE`만 쓰고 `SKIP LOCKED`를 빼면 워커 2대의 처리량이 왜 1대 수준이 되는가?
5. `UPDATE ... WHERE id = (SELECT ... LIMIT 1 FOR UPDATE SKIP LOCKED)` 구조가 필요한 이유는?
6. `RETURNING id`가 없으면 어떻게 해야 하고, 왜 그 방법이 부정확한가?
7. `claim_next_submission`에서 `db.commit()`을 빼면 무슨 일이 생기는가?
8. `recover_stale_running`이 시작 시 한 번만 불리는 것의 한계는? 루프 안에서 자주 부르면 왜 위험한가?
9. `EvaluationError`와 일반 `Exception`을 나누는 이유는? 각각 어떻게 다르게 처리되는가?
10. 워커 루프에서 `except Exception`이 오히려 옳은 이유는? 반드시 함께 해야 하는 것은?
11. `db.rollback()` 후 `submission`을 다시 조회하는 이유는?
12. `start_marker` 기법이 없으면 어떤 재앙이 일어나는가?
13. `inject_model`이 기존 S3 파일을 지우는 이유는? 그 사이 죽으면?
14. `xl.meta` 감지가 왜 필요했는가? 이런 에러 메시지의 3가지 요소는?
15. `download_video`가 예외를 안 던지는 이유는? 핵심 기능과 부가 기능의 구분이란?
16. 워커를 2대로 늘리면 `SKIP LOCKED`가 있어도 왜 여전히 위험한가?
17. `subprocess.run`에 리스트로 인자를 넘기는 것과 `shell=True`의 차이는?
18. 타임아웃이 파이썬(1920초)과 셸(1800초) 두 겹인 이유는? 순서가 반대면?
19. `docker stack ps`에 `--filter desired-state=running`이 없으면 무슨 일이 있었는가? 왜 진단이 어려웠는가?
20. 로그를 `dr-stop-evaluation` **전에** 저장해야 하는 이유는?
21. `run_worker.sh`의 환경변수 검증이 없으면 실패가 언제, 누구에게 드러나는가?
22. `exec python -m worker.run`에서 `exec`의 역할은?
23. `get_paginator`를 안 쓰면 언제부터 문제가 되는가?
24. §12 표에서 ⚠️ 두 항목이 왜 완벽히 해결 불가능한가?

---

## 14. 실험 과제

**실험 A — SKIP LOCKED 직접 확인**
psql 창을 2개 열고:
```sql
-- 창1
BEGIN;
SELECT id FROM submissions WHERE status='queued' ORDER BY submitted_at LIMIT 1 FOR UPDATE SKIP LOCKED;
-- 커밋하지 말고 그대로 둔다

-- 창2
SELECT id FROM submissions WHERE status='queued' ORDER BY submitted_at LIMIT 1 FOR UPDATE SKIP LOCKED;
-- 다른 id가 나오는가? (queued가 2건 이상 있어야 함)

-- 창2에서 SKIP LOCKED를 빼고 실행하면? → 멈춘다(대기)
-- 창1에서 ROLLBACK; 하면 창2가 풀린다
```
**이 실험 하나가 §3 전체를 몸으로 이해시킨다.**

**실험 B — 워커를 두 번 실행**
터미널 2개에서 `worker/run_worker.sh`를 각각 실행하고 제출을 2건 넣어보라.
DB의 `worker_id` 컬럼을 보면 서로 다른 건을 집었는가?
(단, DRFC 충돌이 나므로 **실제 평가까지는 가지 말 것** — claim만 확인)

**실험 C — 상태 기계 수동 조작**
```sql
UPDATE submissions SET status='running', started_at = now() - interval '40 minutes' WHERE id=5;
```
워커를 재시작하면 `recover_stale_running`이 이걸 `queued`로 되돌리는가? 로그를 확인하라.

**실험 D — start_marker 없이 만들어보기**
`find_latest_metrics_key(s3, after=start_marker)` 에서 `after=None`으로 바꾸고
평가를 실패시킨 뒤(예: 빈 zip 업로드) 결과를 보라.
**이전 평가 결과가 이 팀 것으로 저장되는가?** 확인 후 반드시 되돌린다.

**실험 E — docker stack ps 관찰**
평가가 도는 동안:
```bash
watch -n 2 'docker stack ps deepracer-eval-$DR_RUN_ID'
```
평가가 끝난 뒤에도 목록에 남는지, `DESIRED STATE`가 어떻게 바뀌는지 눈으로 보라.
그다음:
```bash
docker stack ps deepracer-eval-$DR_RUN_ID --filter desired-state=running -q | wc -l
```
차이를 확인.

**실험 F — 순수 함수 테스트 읽기**
```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_evaluation_parsing.py tests/test_video_selection.py -v
```
테스트를 읽고, `parse_evaluation_result`에 대한 케이스를 하나 추가하라.
(예: trial이 4개인데 3개만 완주한 경우)

**실험 G — 환경변수 검증 확인**
```bash
env -u DR_LOCAL_S3_BUCKET .venv/bin/python -m worker.run
```
(run_worker.sh를 거치지 않고 직접 실행) 언제 어떤 에러가 나는가?
그다음 `run_worker.sh`로 같은 상황을 만들면 언제 실패하는가?

---

→ 다음: [07-ops.md](07-ops.md) — 컨테이너, 볼륨, 그리고 인터넷 공개
