# 6단계. 평가 워커 — `worker/*`, `worker_status.py`, `routers/internal.py`

> 이 단계의 목표: **웹 요청 주기 밖에서 도는 프로세스**를 설계하는 법을 이해하는 것.
> 작업 큐, 동시성 제어, 상태 기계, 장애 복구, 외부 프로세스 호출, 오브젝트 스토리지,
> **그리고 "웹과 워커가 다른 기기에 있을 때" 생기는 모든 문제.**
>
> **이 프로젝트에서 가장 어려운 부분이고, 가장 배울 게 많은 부분이다.**

---

## 0. 왜 별도 프로세스인가 — 요청-응답 주기의 한계

### 문제

평가 한 건에 **10분**이 걸린다. 만약 `POST /submit` 안에서 그냥 실행한다면?

1. **브라우저 타임아웃.** 기본 60~120초
2. **프록시 타임아웃.** Caddy도 기본 응답 타임아웃이 있다
3. **`async def` 안의 블로킹 → 이벤트 루프 정지.** 다른 모든 사용자의 요청이 10분간 멈춘다
4. **사용자가 새로고침하면?** 평가가 하나 더 시작된다
5. **서버 재시작하면?** 진행 중이던 평가가 흔적 없이 사라진다
6. **DRFC는 한 번에 하나만 돌 수 있다.** 동시 요청을 큐잉할 방법이 없다
7. **웹은 클라우드에, DRFC는 다른 기기에 있다.** 애초에 같은 프로세스일 수 없다

**[쉬움]**
식당에서 손님이 주문하면, 요리가 다 될 때까지 카운터 앞에 세워두는 것과 같다.
정상적인 식당은 **번호표를 주고 자리로 보낸다.**

### 해결: 작업 큐 + 워커

```
POST /submit  →  파일 저장 + DB에 '주문표' INSERT  →  즉시 응답 (0.5초)
                                 ↓
                    (별도 프로세스, 어쩌면 다른 기기가 주문표를 집어감)
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

| | DB 큐 (현재) | Celery + Redis |
|---|---|---|
| 운영 프로세스 | DB 하나 (이미 있음) | + Redis + Celery worker |
| **진실의 원천** | **DB 하나** | DB와 브로커 **둘** → 불일치 가능 |
| 처리량 | 초당 수백 건 | 초당 수만 건 |
| 지연 | 폴링 간격(5초) | 즉시 (푸시) |
| 재시도/스케줄링 | 직접 구현 | 내장 |
| 관측성 | SQL로 바로 조회 | 별도 도구 |
| **원격 워커** | **DB 접속만 되면 됨** | 브로커도 노출해야 함 |
| 메모리 | 0 (기존 DB 활용) | Redis 최소 수십 MB |

**"진실의 원천이 하나"가 결정적이다.**

Celery를 쓰면:
```python
db.add(submission); db.commit()      # DB에 저장
evaluate_task.delay(submission.id)   # Redis에 작업 발행
```
이 둘 사이에서 프로세스가 죽으면 **DB에는 있는데 큐에는 없는** 제출이 생긴다.
DB 큐는 `INSERT` 하나가 곧 발행이므로 **원자적**이다.

**그리고 클라우드 이관 후 새로운 이점이 생겼다:**
워커는 이미 **Tailscale로 DB에 접속**한다. 큐를 위해 **포트를 하나 더 열 필요가 없다.**
**게다가 `mem_limit: 900m` 서버에 Redis를 얹을 여유가 크지 않다.**

> **[전공] 이 패턴에는 이름이 있다.** "Database as a Queue", PostgreSQL 문맥에서는
> `SKIP LOCKED` 기반 큐. Sidekiq/Solid Queue(Rails), `pg-boss`(Node),
> `procrastinate`(Python) 등이 이 방식을 채택한다.
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

| | 폴링 (현재) | LISTEN/NOTIFY | 메시지 브로커 |
|---|---|---|---|
| 지연 | 평균 2.5초, 최대 5초 | 즉시 | 즉시 |
| 구현 | `while True` + `sleep` | 커넥션 유지 + 이벤트 루프 | 라이브러리 |
| 연결 끊김 대응 | **자동 복구** (다음 폴링) | 재연결 로직 필요 | 라이브러리가 처리 |

**평가에 10분이 걸리는데 시작이 5초 늦는 게 문제인가?** 아니다. **0.8% 오차다.**

**폴링의 진짜 장점은 단순함과 견고함이다.**
DB가 잠깐 죽었다 살아나도, **Tailscale이 끊겼다 붙어도**,
**노트북이 절전에서 깨어나도** — **다음 5초에 아무 일 없었다는 듯 계속된다.**
`pool_pre_ping=True`(1단계)가 이걸 뒷받침한다.

**원격 워커 구조에서 이 견고함이 훨씬 중요해졌다.** 네트워크를 건너므로 끊김이 잦다.

**비용**: 5초마다 쿼리 1회 = 하루 17,280회. PostgreSQL에겐 **아무것도 아니다.**

> **`LISTEN`/`NOTIFY`**는 **리스너가 없으면 알림이 사라진다.** 워커가 재시작 중이면 놓친다
> → **결국 폴링을 백업으로 둬야 한다.** 복잡도가 배로 늘고 이득은 5초.

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

### 3-1. 해결하려는 문제

워커가 2대라면(또는 실수로 두 번 실행하면):

```
시각   워커1                          워커2
──────────────────────────────────────────────────────────
t=0    SELECT ... WHERE status='queued' LIMIT 1  → id=5
t=1                                   SELECT ... → id=5   ← 같은 걸 봤다!
t=2    UPDATE SET status='running' WHERE id=5
t=3                                   UPDATE ... WHERE id=5
t=4    평가 시작 (10분)                평가 시작 (10분)   ← 같은 모델을 두 번!
```

**결과**: DRFC를 두 프로세스가 동시에 실행 → **S3의 model/ 폴더를 서로 덮어씀.**

### 3-2. `FOR UPDATE` — 행 단위 배타 락

**[쉬움]** 도서관에서 책을 집으면서 **"이거 내가 볼 거예요"라고 딱지를 붙인다.**

**[전공]**
`SELECT ... FOR UPDATE`는 조회된 행에 **배타적 행 락**을 건다.
트랜잭션이 커밋/롤백될 때까지 유지된다.
PostgreSQL은 MVCC라 **읽기는 락을 걸지 않는다.**

### 3-3. `SKIP LOCKED` — 기다리지 말고 건너뛰기

**`FOR UPDATE`만 쓰면:**
```
워커1: id=5 획득, 락 보유
워커2: id=5가 잠겨있음 → **대기(blocking)**
       워커1 커밋 후 → 다시 읽음 → status가 'running'이라 조건 불일치 → 0건
```
**워커2는 아무 이유 없이 기다렸다가 빈손으로 돌아간다.**
큐에 다른 작업이 10개 있어도 못 집는다 → **처리량이 사실상 1대 수준.**

**`SKIP LOCKED`를 붙이면:**
```
워커1: id=5 획득 (락)
워커2: id=5는 잠겨있네 → 건너뛰고 → id=6 획득   ← 대기 없음!
```

| 옵션 | 잠긴 행을 만나면 |
|---|---|
| (기본) | 락이 풀릴 때까지 **대기** |
| `NOWAIT` | **즉시 에러** |
| `SKIP LOCKED` | **건너뛴다** ← 큐에 적합 |

### 3-4. 중첩 구조와 `RETURNING`

`UPDATE ... LIMIT 1`은 **PostgreSQL에서 지원하지 않는다.** 그래서 서브쿼리로 하나만 고른다.
**UPDATE와 SELECT가 하나의 문장이라 원자적이다** — 파이썬에서 나누면 TOCTOU 틈이 생긴다.

`RETURNING id`는 PostgreSQL 전용. **"내가 방금 집은 그것"** 을 정확히 알려준다.

```python
row = db.execute(CLAIM_NEXT_SQL, {"worker_id": WORKER_ID}).first()
db.commit()          # ← 커밋해야 락이 풀리고 status='running'이 확정된다
return row[0] if row else None
```

**`ORDER BY submitted_at ASC`** — 선착순(FIFO).
`_queue_position`(4단계)이 같은 기준을 쓰므로 **안내와 실제가 일치**한다.

**`:worker_id`는 바인드 파라미터다** — raw SQL에서는 항상 이렇게.

### 3-5. 워커가 1대인데 왜 이걸 썼나

```python
"""워커는 지금 1대만 운영하기로 했지만(2026-07-24 논의), FOR UPDATE SKIP LOCKED로
작성해두는 비용이 거의 없어 처음부터 여러 워커가 동시에 폴링해도 안전하게 작성한다."""
```

**[전공] 좋은 판단의 예다.**
나중에 워커를 늘릴 때 **증상이 비결정적인 버그**(가끔 두 번 평가됨)를 디버깅하게 된다.
**"공짜에 가까운 정확성"과 "미리 만든 기능(YAGNI 위반)"은 다르다.**

---

## 4. **상태 기계 — 6개 전이**

```
          제출 업로드
              ↓
        ┌──────────┐
        │  queued  │ ←─────────────┬──────────────┐
        └────┬─────┘               │              │
             │ claim_next          │ recover      │ TransferError
             ↓                     │ _stale       │ (웹 서버 연결 실패)
        ┌──────────┐               │ _running     │
        │ running  │───────────────┴──────────────┘
        └────┬─────┘
             │
     ┌───────┴────────┐
     ↓                ↓
┌────────┐      ┌─────────┐
│  done  │      │  error  │
└────────┘      └─────────┘
 하루한도 O      하루한도 X
```

| 전이 | 위치 | 조건 |
|---|---|---|
| (없음) → `queued` | `submissions.py:159` | 업로드 성공 |
| `queued` → `running` | `run.py:CLAIM_NEXT_SQL` | 워커가 집음 |
| `running` → `done` | `run.py:240` | 평가 성공 + 결과 저장 |
| `running` → `error` | `run.py:269, 277` | `EvaluationError` 또는 예상 못한 예외 |
| `running` → `queued` | `run.py:86-114` | 워커 재시작 시 stale 복구 |
| **`running` → `queued`** | **`run.py:254-265`** | **`TransferError` — 웹 서버 연결 실패** |

### **왜 `TransferError`는 `error`가 아니라 `queued`로 되돌리나**

```python
except transfer.TransferError as exc:
    # 웹 서버에 연결하지 못한 것은 참가자 잘못이 아니다. '오류'로 끝내면 결과 없이
    # 제출이 소진되므로, 대기열로 되돌려 서버가 돌아왔을 때 다시 처리되게 한다.
    db.rollback()
    submission = db.get(Submission, submission_id)
    submission.status = SubmissionStatus.QUEUED
    submission.worker_id = None
    submission.started_at = None
    db.commit()
    logger.warning("파일 전송 실패 — 대기열로 되돌립니다: submission=%s %s", submission_id, exc)
    # 곧바로 같은 건을 다시 집어 로그만 쌓는 것을 막는다.
    time.sleep(TRANSFER_RETRY_SLEEP_SECONDS)
```

**[쉬움]**
택배 기사가 물건을 가지러 갔는데 **창고 문이 잠겨 있었다.**
이건 주문한 사람 잘못이 아니다. **주문을 취소하면 안 되고, 나중에 다시 가야 한다.**

**[전공] 실패의 "책임 소재"에 따라 처리가 달라진다.**

| 실패 유형 | 누구 잘못 | 전이 | 하루 한도 | 재시도 |
|---|---|---|---|---|
| 압축 파일이 깨짐 | **참가자** | `error` | 제외 | 참가자가 다시 올려야 함 |
| DRFC 실행 실패 | **인프라** | `error` | 제외 | 참가자가 다시 올려야 함 |
| 웹 서버 연결 실패 | **우리 시스템(일시적)** | **`queued`** | 해당 없음 | **자동** |

**세 번째가 결정적으로 다르다.** 웹 서버가 잠깐 재시작 중이거나 Tailscale이 끊긴 것은
**곧 복구될 일시적 장애**다. `error`로 끝내면:
- 참가자는 이유 모를 오류를 본다
- 다시 올리려면 250MB를 또 업로드해야 한다
- 그런데 원본 파일은 **서버에 멀쩡히 있다**

`queued`로 되돌리면 **아무도 모르는 사이 자동으로 처리된다.**

> **[전공] "재시도 가능한 실패(transient)" vs "영구적 실패(permanent)".**
> HTTP 상태 코드로 치면 5xx(재시도할 만함) vs 4xx(재시도해도 소용없음).
> **모든 분산 시스템이 이 구분을 갖는다.**

### `TRANSFER_RETRY_SLEEP_SECONDS = 30` — 폭주 방지

```python
# 웹 서버가 내려가 있을 때 같은 제출을 초당 몇 번씩 다시 집지 않도록 잠시 쉰다.
TRANSFER_RETRY_SLEEP_SECONDS = 30
```

없으면 `queued → claim → 실패 → queued → claim → 실패` 를 5초마다 반복.
**로그가 폭발하고 DB에 부하가 간다.** 30초를 쉬면 분당 2회로 줄어든다.

> **정석은 지수 백오프(1→2→4→8초).** 여기서는 고정 30초 — **단순하고 이 규모에서 충분하다.**

### 상태가 결정하는 것 — 6가지

1. 하루 한도 카운트 (`done`만)
2. 동시 제출 제한 (`queued`/`running`이면 새 제출 불가)
3. 대기 순번 계산
4. 리더보드 표시 (`done` + `finished`만)
5. 파일 보존 정책 (`queued`/`running`이면 삭제 금지)
6. **자동 재시도 여부**

**그래서 상태가 잘못 남으면 시스템이 조용히 망가진다.**
가장 위험한 것: `running`인 채로 워커가 죽는 경우 → 그 팀은 **영원히 새 제출을 못 한다.**

---

## 5. `recover_stale_running` — 죽은 작업 되살리기

```python
def recover_stale_running(db: Session) -> None:
    """워커가 중간에 죽어 '평가중'에 멈춰있는 제출을 재시작 시 다시 대기열로 되돌린다.

    되돌리는 대상은 두 가지다.

    1. **내 worker_id로 잡혀 있는 것** — 시간과 무관하게 즉시 되돌린다.
       이 함수는 main() 진입 시에만 불리므로, 내 이름표가 붙은 '평가중'이 남아 있다는 것은
       이전 생애의 내가 그 평가를 끝내지 못하고 죽었다는 뜻이다. 지금 이 프로세스는 그 작업을
       이어받을 수 없으니 시간을 볼 이유가 없다.
       (EC2 스팟에서 필요하다: 회수로 인스턴스가 중지됐다가 35분 안에 복귀하면 아래 2번
       기준에 걸리지 않아 그 제출이 영구히 '평가중'에 갇혔다. worker-server-setup.md §8.7)
    2. **다른 워커가 잡은 채 오래된 것** — 평가 최대 시간 + 5분이 지난 것만 되돌린다.
       그 워커가 지금도 정상 처리 중일 수 있으므로 시간 기준이 반드시 필요하다.
    """
    threshold = now_utc() - dt.timedelta(seconds=MAX_WAIT_SECONDS + 300)
    stale = db.query(Submission).filter(
        Submission.status == SubmissionStatus.RUNNING,
        or_(
            Submission.worker_id == WORKER_ID,
            Submission.started_at < threshold,
        ),
    ).all()
    for submission in stale:
        logger.warning(
            "'평가중'에 멈춰있던 제출을 대기열로 되돌립니다: submission=%s (worker_id=%s, started_at=%s)",
            submission.id, submission.worker_id, submission.started_at,
        )
        submission.status = SubmissionStatus.QUEUED
        submission.worker_id = None
        submission.started_at = None
    if stale:
        db.commit()
```

### **`or_` 조건이 추가된 이유 — EC2 스팟이 가르쳐준 것**

**[쉬움]**
평가 서버를 **싸게 빌린 컴퓨터(스팟 인스턴스)** 로 옮겼다.
싼 대신 **주인이 갑자기 회수해 갈 수 있다.**

```
10:00  워커가 제출 5를 집음 (running, worker_id="eval-server")
10:03  AWS가 인스턴스를 회수 → 워커가 즉시 죽음
10:20  인스턴스가 다시 뜸 → 워커 재시작
       → "started_at이 17분 전이네? 35분 안 지났으니 아직 처리 중인가 보다"
       → 그냥 둔다
       → 제출 5는 영원히 'running'  ← 갇혔다!
```

**시간 기준만으로는 이 상황을 못 잡는다.**

**[전공] 핵심 통찰**

`recover_stale_running`은 **`main()` 진입 시에만** 호출된다:
```python
def main() -> None:
    logger.info("워커 시작 ...")
    db = SessionLocal()
    try:
        recover_stale_running(db)     # ← 여기 딱 한 번
    finally:
        db.close()
```

**따라서 이 함수가 도는 시점에 "내 worker_id로 running인 것"이 존재한다면
그건 100% 이전 생애의 나다.** 지금 막 시작했으니 내가 집은 게 있을 리 없다.

→ **시간을 볼 이유가 전혀 없다.** 즉시 되돌린다.

**다른 워커의 것은 여전히 시간 기준이 필요하다** — 그 워커가 지금 정상 처리 중일 수 있다.

> **[전공] 이것이 "소유권(ownership) 기반 복구"다.**
> "내 것"은 확실히 알 수 있으므로 즉시 판단하고,
> "남의 것"은 추측할 수밖에 없으므로 시간에 의존한다.
> **확실한 정보가 있으면 추측을 쓰지 마라.**

### 임계값 `MAX_WAIT_SECONDS + 300`

정상적인 평가도 최대 30분까지 걸릴 수 있다. 30분 정확히로 자르면
**정상 실행 중인 작업을 되돌릴 위험**이 있다. 5분 여유를 둔다.

### 로그가 `worker_id`와 `started_at`을 함께 남기는 이유

**두 복구 경로 중 어느 것이 발동했는지 로그만 보고 알 수 있다:**
- `worker_id`가 내 호스트명 → 1번 (내 이전 생애)
- 다른 이름 + 오래된 `started_at` → 2번

**[전공] 로그에 "판단 근거"를 남기는 것이 좋은 로깅이다.**

### **여전히 남은 한계**

**호출 시점이 "워커 시작 시" 뿐이다.**
워커가 죽은 채 아무도 재시작하지 않으면 그 제출은 영원히 `running`이다.

**하지만 이제 하트비트가 이 상황을 "보이게" 만든다** (§6).
참가자 화면에 "평가 서버가 잠시 중지되어 있습니다"가 뜨고,
운영자가 그걸 보고 워커를 재시작하면 복구된다.

> **개선 여지**: 루프 안에서 주기적으로 부르는 것.
> 다만 **정상 실행 중인 평가를 되돌릴 위험**이 있어,
> 제출 단위 하트비트(`heartbeat_at` 컬럼)가 있어야 안전하다.
> 지금 `worker_heartbeats`는 워커 단위이지 제출 단위가 아니다.

---

## 6. **하트비트 스레드 — 살아있음을 알리기**

```python
HEARTBEAT_INTERVAL_SECONDS = 30
# 화면의 "중지" 판정 기준(기본 3분)보다 충분히 짧아야 평가 중에도 살아있다고 인식된다.


def start_heartbeat_thread() -> threading.Thread:
    """생존 신호를 별도 스레드에서 주기적으로 남긴다.

    폴링 루프에서만 갱신하면, 평가 한 건에 붙잡혀 있는 10~30분 동안 신호가 끊긴다.
    그러면 워커가 가장 열심히 일하는 중에 참가자 화면에 "평가 서버 중지"가 뜬다
    (2026-07-30 실제 발생). 데몬 스레드라 워커가 죽으면 함께 죽으므로, 진짜 중단은
    그대로 감지된다.
    """

    def loop() -> None:
        while True:
            db = SessionLocal()
            try:
                touch_heartbeat(db, WORKER_ID)
            except Exception:  # noqa: BLE001 - 하트비트 실패로 워커가 죽으면 안 된다
                db.rollback()
                logger.warning("하트비트 갱신 실패", exc_info=True)
            finally:
                db.close()
            time.sleep(HEARTBEAT_INTERVAL_SECONDS)

    thread = threading.Thread(target=loop, daemon=True, name="heartbeat")
    thread.start()
    return thread
```

### 왜(Why) — 실제로 일어난 문제

**[쉬움]**
경비원이 30분마다 순찰 도장을 찍는다. 그런데 **화재를 끄느라 40분이 걸렸다.**
본부는 "도장이 안 찍혔네? 경비원이 없나 보다"라고 판단한다.
**가장 열심히 일하는 순간에 "근무 이탈"로 찍힌다.**

**[전공]**
처음 구현은 **폴링 루프 안에서** 하트비트를 찍었을 것이다:
```python
while True:
    touch_heartbeat(db, WORKER_ID)      # ← 여기
    submission_id = claim_next_submission(db)
    if submission_id is None:
        time.sleep(5); continue
    process_submission(submission_id)   # ← 여기서 10~30분 블로킹!
```

`process_submission`이 30분 돌아오지 않으면 **하트비트도 30분 끊긴다.**
`worker_heartbeat_stale_minutes = 3` 이므로 **3분 뒤부터 "중지" 배너**가 뜬다.

**참가자 입장**: 제출했더니 "평가 서버가 중지되어 있습니다"가 뜬다.
그런데 실제로는 **내 모델을 평가하고 있는 중**이다. 최악의 오해다.

### 어떻게(How) — `daemon=True` 가 핵심

| | `daemon=False` (기본) | `daemon=True` |
|---|---|---|
| 메인 스레드 종료 시 | **이 스레드가 끝날 때까지 기다린다** | **함께 죽는다** |
| 여기서는 | `while True`라 **영원히 안 끝남 → 프로세스 종료 불가** | 정상 종료 |

**docstring이 이 성질을 정확히 활용한다:**
> 데몬 스레드라 워커가 죽으면 함께 죽으므로, 진짜 중단은 그대로 감지된다.

**즉 "하트비트만 살아있고 워커는 죽은" 상태가 원천적으로 불가능하다.**
이게 하트비트 설계에서 가장 중요한 성질이다 —
**신호를 보내는 주체와 일하는 주체가 같은 생명주기를 가져야 한다.**

> **[전공] 만약 하트비트를 별도 프로세스(cron 등)로 만들었다면?**
> 워커가 죽어도 cron은 계속 도장을 찍는다 → **거짓 생존 신호**.
> 이게 하트비트 구현의 고전적 실수다.

### 스레드 안전성 — 세션을 따로 여는 이유

**SQLAlchemy `Session`은 스레드 안전하지 않다.**
메인 루프의 세션을 공유하면 두 스레드가 같은 커넥션에 SQL을 보내 깨진다.

**`Engine`(커넥션 풀)은 스레드 안전하다.** 그래서 `SessionLocal()` 호출은 안전하다.

> **[전공] 이 구분을 정확히 알아야 한다.**
> - `Engine` / `sessionmaker` → **공유 가능**
> - `Session` / `Connection` → **스레드마다 하나**

**커넥션 풀 크기**: 기본 `pool_size=5`. 메인 1 + 하트비트 1 = 2개. 여유 있다.

### 예외를 삼키는 이유

**하트비트는 부가 기능이다.** DB가 잠깐 끊겨서 실패했다고 평가 작업이 멈추면 본말전도.

**스레드에서 예외가 새어 나가면 그 스레드가 죽는다.**
`while True` 밖으로 나가므로 **다시는 하트비트를 안 찍는다** → 영원히 "중지"로 표시.
→ **루프 안에서 잡아야 한다.**

`exc_info=True` — 스택트레이스를 남겨 원인 추적이 가능하다.

### 주기 30초 vs 판정 3분 — 왜 6배 차이인가

```python
worker_heartbeat_stale_minutes: int = 3    # config.py
```

**여유(margin)를 두는 이유:**
- 한 번 실패해도(네트워크 순간 끊김) 다음 시도가 성공하면 무사
- **6번 연속 실패해야** "중지"로 판정된다
- **오탐(false positive)을 막는다**

**반대 방향의 비용**: 워커가 진짜 죽으면 **최대 3분간 모르고 있다.**
10분짜리 평가를 기다리는 상황에서 3분은 짧다. **적절한 균형이다.**

> **일반 공식**: `판정 임계값 ≥ 주기 × 3` 정도가 관례. 여기는 6배로 더 여유 있다.

### 읽는 쪽 — `worker_status.py`

```python
def get_worker_status(db: Session) -> dict:
    last_seen = db.execute(select(func.max(WorkerHeartbeat.last_seen_at))).scalar_one_or_none()
    if last_seen is None:
        return {"online": False, "last_seen_at": None, "minutes_ago": None}

    now = dt.datetime.now(tz=dt.timezone.utc)
    elapsed = now - last_seen
    minutes_ago = max(int(elapsed.total_seconds() // 60), 0)
    online = elapsed <= dt.timedelta(minutes=settings.worker_heartbeat_stale_minutes)
    return {"online": online, "last_seen_at": last_seen, "minutes_ago": minutes_ago}
```

- **`func.max()`** — 워커가 여러 대여도 "하나라도 살아있으면 온라인"
- **`max(..., 0)`** — 시계 오차로 음수가 나와도 0으로
- **`last_seen is None`** — 하트비트가 한 번도 없으면 오프라인 (보수적)

**호출되는 곳**: `GET /submit`, 시즌 리더보드 — 두 곳 다 `_worker_status.html`로 표시.

---

## 7. `process_submission` — 4단계 예외 처리 구조

```python
def process_submission(submission_id: int) -> None:
    db = SessionLocal()
    try:                                          # ── (A) 세션 수명
        submission = db.get(Submission, submission_id)
        if submission is None:
            return
        work_dir = settings.storage_dir / "work" / str(submission.id)
        work_dir.mkdir(parents=True, exist_ok=True)
        ...
        try:                                      # ── (B) 평가 파이프라인
            ...
            db.commit()
        except transfer.TransferError as exc:     # ── (C) 일시적 실패 → 재시도
            ... status = QUEUED ...
        except drfc.EvaluationError as exc:       # ── (D) 예상된 실패 → error
            ... status = ERROR ...
        except Exception as exc:                  # ── (E) 예상 못한 실패 → error
            ... status = ERROR ...
            logger.exception(...)
        finally:                                  # ── (F) 항상 정리
            shutil.rmtree(work_dir, ignore_errors=True)
            prune_finished_team_files(db, submission_id)
    finally:                                      # ── (G) 항상 세션 닫기
        db.close()
```

### 7-1. **예외를 세 종류로 나누는 이유**

| | `TransferError` | `EvaluationError` | 그 외 `Exception` |
|---|---|---|---|
| 의미 | **일시적 인프라 문제** | **예상된 실패** (참가자 파일/DRFC) | **버그 또는 미지의 문제** |
| 전이 | `queued` (재시도) | `error` | `error` |
| 로그 | `logger.warning` | `logger.error` (한 줄) | `logger.exception` (스택트레이스) |
| 참가자 메시지 | (없음 — 조용히 재시도) | 그대로 노출 | "예상치 못한 오류" 접두어 |

**순서가 중요하다** — 구체적인 예외가 `Exception`보다 **먼저** 와야 한다.
파이썬은 `except`를 위에서부터 검사하므로, `Exception`이 먼저 오면 나머지는 영원히 안 걸린다.

### 7-2. **`except Exception`이 여기서는 옳은 이유**

```python
except Exception as exc:  # noqa: BLE001 - 예상 못한 오류도 '오류' 상태로 남겨 재제출 가능하게 함
```

**만약 안 잡는다면:**
```
제출 5 처리 중 KeyError 발생 → 예외가 main()까지 전파 → 워커 프로세스 종료
 → 제출 5는 'running'에 멈춤
 → 큐의 나머지 제출도 전부 처리 안 됨
 → 하트비트도 끊김 → 참가자 화면에 "중지" 배너
```

**한 건의 실패가 서비스 전체를 멈춘다.**

> **핵심: 배치/워커 루프에서는 "한 건의 실패를 격리"하는 것이 최우선이다.**
> 단, **반드시 `logger.exception`으로 스택트레이스를 남겨야 한다.**
> 안 남기면 그건 그냥 오류를 삼키는 것이다. 이 코드는 남긴다. ✅

### 7-3. `db.rollback()` 먼저, 그리고 다시 조회

```python
db.rollback()                                   # ← 먼저
submission = db.get(Submission, submission_id)  # ← 다시 조회
```

**왜 롤백하는가?** 예외 시점에 세션에 **커밋되지 않은 변경**이 있을 수 있다.
**왜 다시 조회하는가?** `rollback()`은 세션의 모든 객체를 **만료(expire)** 시킨다.

**[전공] SQLAlchemy를 실전에서 써봐야 아는 지식이다.**
롤백 후 stale 객체를 만지면 `DetachedInstanceError`나 조용한 오동작이 난다.

### 7-4. `finally` — 반드시 실행되는 정리

`work_dir`에는 **압축 해제된 모델 + (http 모드에선) 다운로드한 아카이브 + 영상**이 들어있다.
**500MB 이상**이 될 수 있다. `ignore_errors=True`로 삭제 실패해도 넘어간다.

---

## 8. **`prune_finished_team_files` — 파일이 있는 쪽에서 지워야 한다**

```python
def prune_finished_team_files(db: Session, submission_id: int) -> None:
    """평가가 끝난 팀의 파일을 보존 정책대로 정리한다 (최고기록만 남긴다).

    시즌 종료까지 기다리면 디스크가 먼저 찬다 (모델 1건 약 250MB).

    **파일이 있는 쪽에서 지워야 한다.** 웹이 다른 기기에 있으면(http 모드) 이 워커의 디스크에는
    지울 파일이 없다 — 서버에 정리를 요청해야 한다. 로컬 모드에서만 직접 지운다.
    """
    try:
        if transfer.uses_http():
            transfer.request_prune(submission_id)
            return
        submission = db.get(Submission, submission_id)
        if submission is None:
            return
        if prune_team_files(submission.team):
            db.commit()
    except Exception:  # noqa: BLE001 - 정리 실패가 평가 결과를 되돌리면 안 된다
        db.rollback()
        logger.exception("보존 정책 적용 실패: submission=%s", submission_id)
```

### 왜(Why) — 실제로 일어난 문제

`internal.py`의 대응 엔드포인트 docstring:
> 파일은 서버에 있으므로 정리도 서버에서 해야 한다. 워커가 자기 디스크를 지워봐야
> 서버의 250MB짜리 모델은 그대로 쌓인다 (2026-07-30 실제 발생).

**[쉬움]**
청소를 하랬더니 **자기 방을 치웠다.** 정작 쓰레기는 **창고**에 쌓여 있다.

**[전공]**
클라우드 이관 전에는 웹과 워커가 같은 디스크를 봤다. 이관 후에는:
```
서버:  storage/models/1/6/*.tar.gz   ← 진짜 파일 (250MB × N)
워커:  storage/work/17/               ← 임시 작업 디렉터리뿐
```

워커가 `resolve_storage_path(...)` 로 계산해 지우려 하면
**자기 디스크에 그런 파일이 없으므로 아무 일도 안 일어난다.**
`retention.py`의 `_remove`가 `if not path.is_file(): return False` 로 **조용히** 넘어간다.

→ **서버 디스크가 조용히 차오른다. 로그도 안 남는다.**

### 어떻게(How) — 동작을 데이터가 있는 곳으로

서버 쪽:
```python
@router.post("/submissions/{submission_id}/prune")
def prune_submission_team(submission_id: int, _: None = Depends(require_worker), db=Depends(get_db)):
    submission = db.get(Submission, submission_id)
    if submission is None:
        raise NOT_FOUND
    removed = prune_team_files(submission.team)
    if removed:
        db.commit()
    return {"removed": removed}
```

**같은 `prune_team_files` 함수를 서버가 실행한다.** 로직은 하나, 실행 위치만 다르다.

> **[전공] "동작을 데이터가 있는 곳으로 보낸다"는 원칙이다.**
> 데이터를 옮기는 대신 **명령을 옮긴다.** 분산 시스템의 기본 전략 중 하나다.

### `retention.py` 안전장치

```python
for submission in team.submissions:
    if best_submission is not None and submission.id == best_submission.id:
        continue                    # ① 최고기록은 안 지운다
    if submission.status.value in ACTIVE_SUBMISSION_STATUSES:
        continue                    # ② 평가 대기/진행 중인 것은 안 지운다
    removed += remove_submission_files(submission, videos_dir)
```

**②가 없으면 워커가 평가하려는 파일을 스스로 지운다.**
그리고 삭제 후 `result.video_path = None` — 깨진 링크 방지(5단계).

### 175GB 문제

> 모델 아카이브가 건당 250MB 안팎이라 전부 남기면 시즌 하나로 디스크가 찬다
> (10팀 × 5회/일 × 2주 ≈ 175GB).

**Lightsail 서버 디스크는 보통 40~80GB다.** 대회 중간에 찬다.
→ 평가 직후 즉시 정리가 **선택이 아니라 필수**다.

**DB 레코드는 남긴다** — 리더보드의 "제출 횟수"와 이력이 그대로여야 한다.
**"큰 것만 지운다"** 는 정확한 판단이다.

---

## 9. 평가 파이프라인 — 순서가 바뀐 이유까지

```python
# 1) 시작 시점 마커 기록
start_marker = None
existing_key = drfc.find_latest_metrics_key(s3)
if existing_key:
    head = s3.head_object(Bucket=drfc.get_bucket(), Key=existing_key)
    start_marker = head["LastModified"]

# 2) 모델 확보 (로컬 경로 or HTTP 다운로드)
model_file = transfer.fetch_model(submission.id, submission.model_path, work_dir)

# 3) 검증 → S3 업로드
drfc.inject_model(s3, model_file, work_dir)

# 4) 평가 실행 (블로킹)
drfc.run_evaluation_blocking(RUN_EVAL_SCRIPT, DRFC_DIR, DR_ENV_FILE, MAX_WAIT_SECONDS,
                             log_path=log_path_for(submission.id))

# 5) 새 metrics 찾기
metrics_key = drfc.find_latest_metrics_key(s3, after=start_marker)
if metrics_key is None:
    raise drfc.EvaluationError("평가가 끝났지만 새 metrics 파일을 찾지 못했습니다.")
metrics = drfc.download_json(s3, metrics_key)

# 6) 원본 백업 (로컬 + 서버)
metrics_file.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
transfer.deliver_metrics(submission.id, metrics_file)

# 7) 파싱 + 진행률 요약
finish_status, lap_time, off_track = drfc.parse_evaluation_result(metrics, settings.online_eval_laps)
best_progress, failure_reason = drfc.summarize_progress(metrics, log_path_for(submission.id))

# 8) 영상 먼저 내려받는다 (아직 저장은 안 함)
local_video = work_dir / "evaluation.mp4"
video_key = drfc.download_video(s3, local_video)

# 9) 결과 확정 (영상 경로는 None으로)
result = EvaluationResult(..., video_path=None, ...)
db.add(result); submission.status = SubmissionStatus.DONE; db.commit()

# 10) 그 다음에 영상을 전달하고 경로를 채운다
if video_key is not None:
    stored_video = transfer.deliver_video(submission.id, local_video, video_rel_path)
    if stored_video:
        result.video_path = stored_video
        db.commit()
```

### 9-1. **`start_marker` — 가장 영리한 트릭**

**문제**: DRFC는 평가 결과를 `.../metrics/evaluation/evaluation-*.json` 에 남기는데
**이전 평가의 파일들이 그대로 남아 있다.**

그냥 "가장 최근 파일"을 집으면 → 평가가 실패해 새 파일이 안 생겼을 때
**이전 팀의 결과를 이 팀 것으로 저장**한다. **대회 결과가 완전히 오염된다.**

**해결**: 평가 전에 시각을 기록하고, 그 이후 것만 인정한다.
```python
if after is not None and obj["LastModified"] <= after:
    continue
```

**[쉬움]**
시험 보기 전에 답안지 상자에 뭐가 들어있는지 시각을 적어둔다.
시험 후 **그 시각 이후에 들어온 답안지만** 내 것으로 인정한다.

**[전공] 이 패턴의 이름**: watermark / high-water mark.

### 9-2. **영상 순서가 3단계로 나뉜 이유**

```python
# 영상 키는 다음 평가 때 덮어써지므로 결과 저장 전에 먼저 내려받아 둔다.
...
# 순위를 좌우하는 결과를 먼저 확정한다. 영상은 부가 정보라 그 다음이다
# (전송 대상 제출이 DB에 있어야 웹이 영상을 받아줄 수 있기도 하다).
```

**(a) S3 → 로컬 `work_dir`** — 다음 평가가 시작되면 **같은 키로 덮어써진다.** 빨리 확보해야 한다.

**(b) DB 결과를 먼저 커밋 (`video_path=None`)** — 두 가지 이유:

1. **순위가 부가 정보에 인질로 잡히면 안 된다.**
   영상 전송이 실패해 예외가 나면 `except` 블록으로 가서 **`error` 상태가 되고 기록이 사라진다.**
2. **서버가 영상을 받으려면 `EvaluationResult`가 먼저 있어야 한다:**
```python
# app/routers/internal.py:68-70
if submission is None or submission.result is None:
    raise NOT_FOUND
...
submission.result.video_path = rel_path
```

→ **순서가 논리적으로 강제된다.**

**(c) 영상 전달 후 경로 채우기** — 실패하면 `None`이 오고 리더보드는 "—"를 표시한다.

> **[전공] "핵심 경로와 부가 경로의 분리"다.**
> 커밋을 두 번 하는 것은 원자성 관점에서 이상적이지 않지만,
> **"랩타임은 확정, 영상은 best-effort"** 라는 요구를 정확히 표현한다.
> 한 트랜잭션으로 묶으면 영상 실패가 랩타임을 날린다.

### 9-3. `validate_checkpoint_selection` — 파괴 전에 검증

```python
def validate_checkpoint_selection(model_root: Path) -> None:
    """평가에 쓸 체크포인트가 아카이브 안에 실제로 있는지 미리 확인한다.

    `DR_EVAL_CHECKPOINT=best`로 운영하는데 아카이브에 best 정보가 없으면, 시뮬레이터가
    한참 뒤에 알 수 없는 오류로 죽어 참가자는 원인을 모른다. 여기서 먼저 걸러 한국어로 알린다.
    **모델을 S3에 주입하기 전에 호출해야 한다** — 그래야 잘못된 제출이 이전 모델을 지우지 않는다.
    """
    kind = os.environ.get("DR_EVAL_CHECKPOINT", "last").strip().lower()
    key = _CHECKPOINT_KINDS.get(kind)
    if key is None:
        return  # 특정 체크포인트를 직접 지정한 운영 — 검사 대상이 아니다

    index_path = model_root / CHECKPOINT_INDEX_FILE
    if not index_path.is_file():
        raise EvaluationError(CHECKPOINT_MISSING_HELP)
    ...
    entry = index.get(key) or {}
    if not entry.get("name"):
        raise EvaluationError(CHECKPOINT_MISSING_HELP)
```

호출 위치:
```python
extract_dir = _find_model_root(extract_dir)
# 아래에서 기존 모델을 지우기 전에 검사한다 — 잘못된 제출이 이전 모델을 날리면 안 된다.
validate_checkpoint_selection(extract_dir)

model_key_prefix = f"{prefix}/model/"
existing = s3_client.list_objects_v2(Bucket=bucket, Prefix=model_key_prefix)
for obj in existing.get("Contents", []):
    s3_client.delete_object(Bucket=bucket, Key=obj["Key"])   # ← 파괴
```

### **"파괴 전에 검증한다" — 이 절의 핵심 교훈**

**[쉬움]**
이사할 때 **새 가구가 제대로 왔는지 확인하고 나서** 헌 가구를 버린다.

**[전공] 검증이 삭제 뒤에 있었다면:**
```
1. 참가자 A의 잘못된 모델 도착
2. S3의 model/ 폴더를 전부 삭제
3. 검증 실패 → EvaluationError
4. → S3 model/ 폴더가 빈 상태로 남는다
5. → 다음 참가자 B의 평가도 이상하게 동작할 수 있다
```
**한 사람의 잘못된 제출이 시스템 전체를 오염시킨다.**

**`inject_model`의 실제 순서:**
```
1. 압축 해제                       (실패 가능)
2. _find_model_root                (실패 가능 — model_metadata.json 없음, MinIO 덤프)
3. validate_checkpoint_selection   (실패 가능 — 체크포인트 정보 없음)
────────── 여기까지 실패해도 S3는 무사 ──────────
4. S3 기존 파일 삭제  ← 되돌릴 수 없는 지점
5. 새 파일 업로드
```

> **일반 원칙: 되돌릴 수 없는 작업(삭제, 전송, 결제) 전에 모든 검증을 끝내라.**
> 트랜잭션이 없는 외부 시스템(S3, 파일시스템, 외부 API)에서 특히 중요하다.

### 왜 체크포인트 검증이 필요했나

**[쉬움]**
DeepRacer 학습은 여러 저장 지점(체크포인트)을 남긴다.
"제일 잘한 순간(best)"과 "마지막 순간(last)"이 있다. 우리 대회는 **best**로 평가한다.
참가자가 모델을 잘못 내보내면 그 색인 정보가 빠진다.

**[전공]**
`deepracer_checkpoints.json` 이 그 색인이다. 없으면 시뮬레이터가
**한참 뒤에(수 분) 알 수 없는 오류로 죽는다.**
- 참가자는 10분을 기다린 뒤 영어 스택트레이스를 본다
- 원인을 짐작할 수 없다

**미리 검사하면 몇 초 만에 한국어로 알려준다** (`CHECKPOINT_MISSING_HELP`).

**`kind`가 `best`/`last`가 아니면 검사를 건너뛴다:**
```python
if key is None:
    return  # 특정 체크포인트를 직접 지정한 운영 — 검사 대상이 아니다
```
운영자가 특정 이름을 지정했다면 **우리가 무엇이 맞는지 판단할 수 없다.**

> **[전공] "모르면 막지 마라"** — 검증기가 자기 판단 범위를 아는 것도 설계다.
> 과잉 검증은 정상 케이스를 막는다.

### 9-4. **`summarize_progress` — metrics가 비었을 때의 대안 경로**

```python
def summarize_progress(metrics: dict, log_path: Path | None = None) -> tuple[float | None, str | None]:
    """참가자에게 보여줄 '최고 진행률'과 '종료 사유'를 정한다.

    metrics의 trial 기록을 우선 쓰고, 비어 있으면 평가 로그로 넘어간다.
    """
    trials = metrics.get("metrics", [])
    percentages = [t["completion_percentage"] for t in trials if t.get("completion_percentage") is not None]
    if percentages:
        best = max(percentages)
        statuses = [str(t.get("episode_status", "")).strip().lower().replace(" ", "_") for t in trials]
        terminal = [s for s in statuses if s and s not in _NON_TERMINAL_STATUSES]
        return float(best), (terminal[-1] if terminal else None)

    if log_path is not None:
        return extract_progress_from_log(log_path)
    return None, None
```

**[쉬움]** 성적표에 점수가 안 적혀 있으면, **수업 기록장**을 뒤져서라도 알아낸다.

**[전공] 2단계 폴백(fallback) 구조**
```
1순위: metrics json의 trial 기록      ← 정확하고 구조화됨
2순위: 평가 로그의 SIM_TRACE_LOG      ← 지저분하지만 항상 있음
없으면: (None, None)                  ← 화면은 "완주 실패"만 표시
```

**왜 폴백이 필요한가?**
> metrics json이 비어 있는 경우(차가 한 바퀴도 못 끝냈을 때)에도 참가자에게 "어디까지
> 갔고 왜 멈췄는지"를 알려주기 위한 보조 경로다 (2026-07-30 submission 18에서 필요성 확인).

**DRFC는 trial이 끝나야 metrics에 기록한다.**
차가 5초 만에 멈추면 **metrics가 빈 배열**이다.

### 로그 파싱 — `extract_progress_from_log`

```python
# SIM_TRACE_LOG의 필드 위치(0-based). DRFC 시뮬레이터가 매 스텝 찍는 로그로,
# metrics json이 비어 있어도 여기에는 진행률과 종료 사유가 남는다.
#   SIM_TRACE_LOG:episode,step,x,y,yaw,steer,throttle,action,reward,done,
#                 all_wheels_on_track,progress,closest_waypoint,track_len,tstamp,episode_status,...
SIM_TRACE_PROGRESS_INDEX = 11
SIM_TRACE_STATUS_INDEX = 15
_NON_TERMINAL_STATUSES = {"in_progress", "prepare", "pause"}
```

**[전공] 남의 로그를 파싱하는 코드의 교과서적 방어**

| 코드 | 방어하는 것 |
|---|---|
| `if not log_path.is_file(): return None, None` | 로그 파일 자체가 없음 |
| `errors="replace"` | **인코딩 깨진 바이트** |
| `for line in f` | **스트리밍 읽기** — 수백 MB여도 메모리 안전 |
| `line.find("SIM_TRACE_LOG:")` | 앞에 타임스탬프·컨테이너명이 붙어도 찾는다 |
| `if len(fields) <= INDEX: continue` | 필드 수가 모자란 잘린 줄 |
| `try: float(...) except ValueError: continue` | 숫자가 아닌 값 |
| `except OSError: return None, None` | 파일 읽기 자체가 실패 |

**한 줄이라도 이상하면 그 줄만 건너뛴다.** 전체가 실패하지 않는다.

> **[전공] 이것이 "관대한 파서(lenient parser)"다.**
> 내가 만든 형식이면 엄격하게 검증해야 한다(잘못된 데이터를 조기에 잡기 위해).
> **남이 만든 형식은 관대하게 읽어야 한다** — 내가 형식을 바꿀 수 없기 때문이다.
> (포스텔의 법칙: "보내는 것은 엄격하게, 받는 것은 관대하게")

**필드 인덱스를 상수로 뽑고 주석에 전체 스키마를 적어둔 것**도 중요하다.
`fields[11]` 이라고만 써 있으면 나중에 아무도 왜 11인지 모른다.

**`best_progress`는 `max`, `last_status`는 마지막 값** — **다른 집계 방식이다.**
로그의 **마지막 종료 상태**가 실제 종료 사유이기 때문.

**정규화가 두 경로에서 동일하다:**
```python
.strip().lower().replace(" ", "_")
```
`"Off Track"` → `"off_track"` → `render.py`의 `FAILURE_REASON_LABELS["off_track"]` → "트랙 이탈"
**두 경로가 같은 형식으로 정규화해야** 표현 필터가 양쪽을 다 처리할 수 있다.

### 9-5. `parse_evaluation_result` — 도메인 규칙

```python
trials = sorted(metrics.get("metrics", []), key=lambda t: t.get("trial", 0))
off_track_total = sum(t.get("off_track_count", 0) for t in trials)
completed = [t for t in trials if t.get("completion_percentage") == 100]

if len(completed) >= required_laps:
    total_ms = sum(t["elapsed_time_in_milliseconds"] for t in completed[:required_laps])
    return "finished", total_ms / 1000.0, off_track_total
return "timeout", None, off_track_total
```

**규칙**: "3바퀴 모두 100% 완주해야 완주. 랩타임은 그 3바퀴 합계."
`completed[:required_laps]` — 4바퀴를 성공했어도 **앞 3개만** 센다.

**이 함수는 순수 함수다** — `tests/test_evaluation_parsing.py`로 쉽게 테스트된다.
`summarize_progress`도 마찬가지 (`tests/test_progress_summary.py`).

---

## 10. `inject_model` — 참가자 모델을 DRFC가 읽는 위치로

**왜 S3에 올려야 하나?**
> `dr-start-evaluation`은 **모델 경로 인자를 받지 않고 항상 `{DR_LOCAL_S3_MODEL_PREFIX}/model/`을 평가**한다

**"이 모델을 평가해줘"라고 말할 방법이 없다. 정해진 자리에 갖다 놓는 수밖에 없다.**

> **[전공] 위험**: `delete` → `upload` 사이에 프로세스가 죽으면 `model/`이 **빈 상태**로 남는다.
> 순차 처리라 복구는 되지만, **원자적 교체가 불가능한 구조**임을 알고 있어야 한다.

### `_find_model_root` — 참가자의 압축 방식이 제각각인 문제

```
방식1:  model_metadata.json, *.pb, ...        (내용물만)
방식2:  my-model/model_metadata.json, ...     (폴더 포함)
방식3:  a/b/c/model/model_metadata.json       (경로가 깊음)
```

`model_metadata.json`을 랜드마크로 재귀 탐색하고, 여러 개면 **가장 얕은 것**을 쓴다.

**[전공] 좋은 사용자 경험 설계다.**
"정확히 이렇게 압축하세요"라고 요구하는 대신 **흔한 변형을 자동으로 흡수**한다.

### **MinIO 원본 덤프 감지**

```python
def _looks_like_minio_raw_dump(extract_dir: Path) -> bool:
    """MinIO는 오브젝트를 '폴더 + xl.meta(+ part.N)' 형태로 디스크에 저장하기 때문에,
    참가자가 S3에서 정상적으로 내보내지 않고 MinIO 데이터 폴더를 그대로 압축하면
    파일 이름은 그럴듯해 보여도 실제 모델 파일이 하나도 없다."""
    return next(extract_dir.rglob("xl.meta"), None) is not None
```

**[쉬움]** 냉장고 안의 음식을 가져오랬더니 **냉장고 부품**을 가져온 것.

> **[전공] 좋은 에러 메시지의 정석이다.**
> 1. **무엇이 잘못됐는지** (MinIO 내부 폴더를 압축함)
> 2. **왜 그렇게 판단했는지** (xl.meta 파일이 있음)
> 3. **어떻게 고치는지** (구체적 명령어)
>
> **`CHECKPOINT_MISSING_HELP`도 정확히 같은 3요소를 갖는다.** 일관된 스타일이다.

`next(iterator, None)`: **`rglob`이 전체를 순회하지 않고 첫 발견에서 멈춘다.**

### `download_video` — 폴백 전략

```python
VIDEO_KEY_SUFFIXES = ("mp4/camera-pip/0-video.mp4", "mp4/camera-45degree/...", "mp4/camera-topview/...")
MIN_VALID_VIDEO_BYTES = 10 * 1024
```

주석의 사건 기록:
> 2026-07-26: camera-topview가 261바이트로만 생성되고 있었고, 그걸 고정으로 받는 바람에
> 리더보드 영상이 계속 비어 있었다.

**배울 것 3가지:**
1. **`head_object`로 먼저 확인** — 다운로드 전에 크기를 본다
2. **크기 임계값으로 "깨진 파일" 판별** — 10KB라는 숫자에 근거가 주석에 있다
3. **실패해도 예외를 안 던진다** — 핵심 기능(랩타임)과 부가 기능(영상)의 구분

---

## 11. **`transfer.py` — 하나의 코드, 두 배포 형태**

```python
"""웹 서버와 모델·영상을 주고받는다 (cloud-migration.md §4).

두 배포 형태를 하나의 스위치로 지원한다.

- `WORKER_TOKEN`이 비어 있음 → **local 모드**: 웹과 워커가 같은 디스크를 공유하는 지금 구성.
- `WORKER_TOKEN`이 설정됨 → **http 모드**: 웹이 클라우드에 있는 구성.

이렇게 두면 이관 전후로 워커 코드를 바꾸지 않아도 되고, 이관 전에 한 대에서 http 모드를
그대로 시험해볼 수 있다.
"""
```

### 왜(Why) — 왜 코드를 두 벌로 안 나눴나

**[쉬움]**
이사할 때 **둘 다 되는 물건**을 사면 이사 전에 미리 써볼 수 있다.

**[전공] 이관(migration)의 고전적 함정**

코드를 두 벌로 나눴다면:
```
worker/run_local.py    ← 지금 돌고 있는 것 (검증됨)
worker/run_remote.py   ← 새로 만든 것 (한 번도 안 돌려봄)
```
**이관 당일에 처음 돌린다.** 그리고 반드시 뭔가 안 된다.
게다가 버그를 고치면 **두 파일에 다 고쳐야 한다.**

**스위치 하나로 두면:**
1. 이관 **전에** 한 대에서 http 모드를 시험할 수 있다
2. 문제가 생기면 **환경변수 하나 지우고** 즉시 되돌린다
3. 버그 수정이 한 곳에서 끝난다

> **[전공] 이것이 "기능 플래그(feature flag)" 패턴이다.**
> 새 동작을 코드에 넣되 **꺼진 상태로 배포**하고, 준비되면 켠다.
> 롤백이 재배포가 아니라 **설정 변경**이 된다 — 훨씬 빠르고 안전하다.

### 어떻게(How) — 스위치의 구현

```python
def uses_http() -> bool:
    return bool(settings.worker_token)
```

**단 한 줄.**

**왜 별도의 `WORKER_MODE=http` 같은 변수를 안 만들었나?**
- 변수가 두 개면 **불일치**가 생긴다 (`WORKER_MODE=http` 인데 토큰이 없으면?)
- 토큰은 http 모드에 **반드시 필요하다.** 있으면 http, 없으면 local — 논리적으로 일관

> **[전공] "설정 항목을 줄이면 잘못된 조합도 줄어든다."** 파생 가능한 값은 파생시켜라.

서버 쪽도 같은 논리를 쓴다:
```python
# 토큰이 설정되지 않은 배포(웹·워커가 같은 기기)에서는 이 경로 자체를 열지 않는다.
if not expected or not x_worker_token:
    raise NOT_FOUND
```
**한 변수가 워커의 동작과 서버의 엔드포인트 개방을 동시에 결정한다.**

### 세 함수의 대칭 구조

```python
def fetch_model(...):
    if not uses_http():
        path = resolve_storage_path(stored_path)      # 로컬 경로 그대로
        if not path.is_file():
            raise TransferError(...)
        return path
    # ... HTTP 다운로드 ...

def deliver_video(...):
    if not uses_http():
        dest = settings.videos_dir / video_rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if local_video.resolve() != dest.resolve():
            shutil.copy2(local_video, dest)
        return video_rel_path
    # ... HTTP 업로드 ...

def deliver_metrics(...):
    if not uses_http():
        return True  # 이미 서버와 같은 디스크에 쓰여 있다
    # ... HTTP 업로드 ...
```

**모든 함수가 같은 구조다.** 호출부는 모드를 전혀 모른다:
```python
model_file = transfer.fetch_model(submission.id, submission.model_path, work_dir)
```
`run.py`는 이 한 줄만 안다. **추상화가 잘 됐다는 증거다.**

### 세부 결정들

**(1) local 모드에서 모델을 복사하지 않는다**
> local 모드에서는 원본 경로를 그대로 쓰고(복사하지 않는다)

250MB 복사는 시간과 디스크 낭비. **읽기만 하므로 원본을 그대로 쓴다.**

**(2) `local_video.resolve() != dest.resolve()` 비교**
**같은 파일을 자기 자신에게 복사하면** `SameFileError`가 나거나 **파일이 0바이트가 될 수 있다.**
`resolve()`로 심볼릭 링크까지 풀어 비교한다.

**(3) `deliver_metrics`가 local에서 `True`를 반환하는 이유**
`run.py`가 이미 저장했고 같은 디스크를 공유하므로 **할 일이 없다.**

**(4) 실패 처리가 함수마다 다르다**

| 함수 | 실패 시 | 이유 |
|---|---|---|
| `fetch_model` | **`TransferError` 발생** | 모델 없이는 평가 자체가 불가능 |
| `deliver_video` | `None` 반환 (로그만) | 영상은 부가 정보 |
| `deliver_metrics` | `False` 반환 (로그만) | 사본이라 없어도 결과는 무사 |
| `request_prune` | `0` 반환 (로그만) | 다음 평가에서 다시 정리됨 |

**"이게 없으면 작업이 무의미해지는가?"** 로 판단했다.

```python
"""파일이 서버에 있으므로 워커가 직접 지울 수 없다. 실패해도 평가 결과에는 영향이 없으므로
예외를 올리지 않는다 — 다음 평가에서 다시 정리된다."""
```
**"다음 평가에서 다시 정리된다"** 가 핵심이다.
`prune_team_files`는 **멱등**이므로 한 번 실패해도 다음에 만회된다.
→ **실패를 무시해도 되는 근거가 있다.**

### 타임아웃 설정

```python
# 모델이 수백 MB라 넉넉히 잡는다. 연결 자체가 죽은 경우는 connect 타임아웃이 먼저 걸린다.
TRANSFER_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=600.0, pool=10.0)
```

| 종류 | 의미 | 값 |
|---|---|---|
| `connect` | TCP 연결 수립 | **10초** — 서버가 죽었으면 빨리 알아야 |
| `read` | 응답 데이터 대기 | **600초** — 250MB 다운로드 |
| `write` | 요청 데이터 전송 | **600초** — 영상 업로드 |
| `pool` | 커넥션 풀 대기 | 10초 |

**하나의 값으로 뭉뚱그리면 안 되는 이유:**
- 전체를 10초로 하면 → 큰 파일 전송이 실패
- 전체를 600초로 하면 → **서버가 죽었을 때 10분을 기다린다**

**연결은 빨리 포기하고, 데이터는 오래 기다린다.**

### 스트리밍 다운로드

```python
with httpx.stream("GET", url, headers=_headers(), timeout=TRANSFER_TIMEOUT) as response:
    if response.status_code != 200:
        raise TransferError(f"모델 다운로드 실패 (HTTP {response.status_code}) — 토큰 설정과 제출 상태를 확인하세요")
    with open(dest, "wb") as out:
        for chunk in response.iter_bytes(1024 * 1024):
            out.write(chunk)
```

`httpx.get(url).content` 라고 하면 **250MB가 메모리에 통째로** 올라간다.

**4단계에서 본 서버 쪽 청크 읽기와 정확히 대칭이다.**
```
업로드: 브라우저 → [서버가 1MB씩 읽어 디스크에 씀]
다운로드: [워커가 1MB씩 읽어 디스크에 씀] ← 서버(FileResponse도 내부적으로 스트리밍)
```

**0바이트 검증:**
```python
if dest.stat().st_size == 0:
    raise TransferError("모델 파일을 0바이트로 받았습니다.")
```
**"성공 응답 = 올바른 데이터"가 아니다.** 받은 것을 검증한다.

**에러 메시지에 조치 방법 포함:**
"토큰 설정과 제출 상태를 확인하세요" — 404가 나는 두 원인을 짚어준다.
(3단계에서 본 대로 인증 실패도 404이므로 구분이 안 된다 → **운영자에게 힌트가 필요하다.**)

---

## 12. `internal.py` — 서버 쪽 대응

| 워커 (`transfer.py`) | 서버 (`internal.py`) |
|---|---|
| `fetch_model` | `GET /internal/submissions/{id}/model` |
| `deliver_video` | `POST /internal/submissions/{id}/video` |
| `deliver_metrics` | `POST /internal/submissions/{id}/metrics` |
| `request_prune` | `POST /internal/submissions/{id}/prune` |

### 경로를 서버가 정한다

```python
rel_path = f"{submission.team.season_id}/{submission.team_id}/{submission.id}.mp4"
dest_path = settings.videos_dir / rel_path
```

**워커가 보낸 `video_rel_path`를 쓰지 않는다.** DB의 정수 ID로 직접 조립한다.
→ **경로 주입이 원천 차단된다.**

**응답으로 그 경로를 돌려주고, 워커가 그걸 DB에 기록한다:**
```python
return {"video_path": rel_path, "bytes": size}
```
```python
return response.json().get("video_path", video_rel_path)
```
**서버가 정한 경로를 워커가 따른다.** 일관성이 보장된다.

### 크기 제한

```python
# metrics json은 정상이면 1KB 안팎이다. 넉넉히 잡되 무제한은 두지 않는다.
MAX_METRICS_BYTES = 5 * 1024 * 1024
video_upload_max_bytes: int = 200 * 1024 * 1024  # 200MB (실측 14MB 내외)
```
**두 값 모두 "실측값 + 여유"** 로 정해졌고 근거가 주석에 있다.

**크기에 따라 다른 전략:**
```python
# 영상 — 큰 파일이라 청크 + 413
while chunk := await video.read(1024 * 1024): ...
# metrics — 작은 파일이라 한 번에 + 400
content = await metrics.read()
if not content or len(content) > MAX_METRICS_BYTES: ...
```
5MB를 청크로 나누는 건 과잉이다.

---

## 13. 외부 프로세스 호출과 셸 스크립트

### 13-1. `subprocess.run` — 리스트로 인자 넘기기

```python
subprocess.run(["bash", str(script_path)], env=env, capture_output=True, text=True,
               timeout=max_wait_seconds + 120)
```

`shell=True` 면 문자열이 셸에 해석된다 → 셸 인젝션.
**리스트 형태는 셸을 거치지 않고 `execve`로 직접 실행**한다.

**`env = os.environ.copy()`** — `env=`를 주면 그 dict가 전부가 된다.
복사하지 않으면 `PATH`도 없어져 `bash`조차 못 찾는다.

**환경변수가 3단계를 타고 흐른다:**
```
run_worker.sh → source bin/activate.sh (DR_* 로드)
  └─ exec python -m worker.run
       └─ subprocess.run(env=os.environ.copy() + 추가)
            └─ run_evaluation.sh
```

### 13-2. **타임아웃 삼중 방어**

```
transfer read=600초  <  MAX_WAIT_SECONDS=1800초(셸)  <  파이썬 subprocess=1920초
```

- **셸 타임아웃(1800초)**: 정상 경로. 로그를 저장하고 스택을 정리한 뒤 종료
- **파이썬 타임아웃(1920초)**: 스크립트 자체가 멈췄을 때의 최후 방어
- **120초 여유**: 스크립트가 정리할 시간

**[전공] 계층적 타임아웃은 분산 시스템의 기본 패턴이다.**
바깥 계층이 안쪽보다 항상 길어야 한다. 반대면 안쪽이 정리할 기회를 못 얻는다.

### 13-3. **`docker stack ps`의 함정 — 실제로 23분을 날린 버그**

```bash
running_task_count() {
  docker stack ps "$STACK_NAME" --filter desired-state=running -q 2>/dev/null | wc -l || true
}
```

```bash
# 주의: 그냥 `docker stack ps`를 쓰면 이미 끝난 태스크도 이력으로 계속 남아 있어
# 카운트가 절대 0이 되지 않는다(실제로 이 때문에 평가가 7분 만에 끝났는데도
# 30분 타임아웃까지 기다리는 버그가 있었다).
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

**증상이 왜 진단하기 어려웠나:**
- 평가는 **정상적으로 성공**한다 (7분 만에)
- 결과 파일도 정상적으로 생긴다
- 단지 워커가 **23분을 더 기다린다**
- 에러도 안 나고 로그도 정상

> **[전공] 교훈: 외부 도구의 출력을 파싱할 때는 "무엇을 세고 있는지" 정확히 확인해야 한다.**
> `wc -l`은 줄 수를 셀 뿐, **그 줄이 무슨 의미인지 모른다.**

### 13-4. **로그를 스택 삭제 전에 저장하기**

```bash
# DRFC는 DR_ROBOMAKER_MOUNT_LOGS=False이면 시뮬레이션 로그를 디스크에 남기지 않는다.
# 로그는 Swarm 서비스가 살아있는 동안만 `docker service logs`로 볼 수 있고,
# dr-stop-evaluation으로 서비스를 지우는 순간 영구히 사라진다.
```

**모든 종료 경로(시작 실패, 타임아웃, 정상 완료)에서 로그를 먼저 저장하고 스택을 내린다.**

**[전공] "관측 가능성은 파괴 전에 확보해야 한다."**

**그리고 이 로그가 이제 두 번째 용도를 갖는다:**
```python
best_progress, failure_reason = drfc.summarize_progress(metrics, log_path_for(submission.id))
```
**§9-4의 폴백 경로가 바로 이 로그를 읽는다.**
장애 조사용으로 만든 것이 **참가자에게 보여줄 정보의 출처**가 됐다.

> **[전공] 좋은 관측 데이터는 나중에 다른 용도로도 쓰인다.**
> "일단 남겨두자"가 옳았던 사례다.

### 13-5. `set -u`를 쓰지 않는 이유

```bash
# set -u는 쓰지 않는다: DRFC의 dr-* 함수들이 내부적으로 activate.sh를 다시 source하는데
# (dr-update-env), 그 안에서 미설정 변수를 참조해 unbound variable로 죽는다.
set -eo pipefail
```

**엄격 모드는 내 코드에만 적용할 수 있다.** 외부 코드를 부를 때는 그 구간만 끈다:
```bash
set +e +o pipefail
source bin/activate.sh "$DR_ENV_FILE"
set -e -o pipefail
```

### 13-6. `run_worker.sh` — 조용한 실패를 막는 방어

```bash
REQUIRED_DR_VARS=(DR_LOCAL_S3_BUCKET DR_LOCAL_S3_MODEL_PREFIX DR_LOCAL_S3_PROFILE)
for var in "${REQUIRED_DR_VARS[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    echo "[run_worker] ERROR: 필수 환경변수 $var 가 로드되지 않았습니다." >&2
    missing=1
  fi
done
```

```bash
# activate.sh가 조용히 실패하거나 워커가 이 스크립트를 거치지 않고 직접 실행되면,
# DR_* 변수가 없는 채로 몇 시간을 떠 있다가 참가자 제출 시점에야 깊은 스택트레이스의
# KeyError로 드러난다 (2026-07-25 실제 발생). 여기서 즉시, 명확하게 실패시킨다.
```

**[전공] fail fast의 교과서적 사례.**

| | 나쁜 실패 | 좋은 실패 |
|---|---|---|
| 발견 시점 | 09:00 기동 → **14:32 참가자 제출 시** | **09:00 기동 즉시** |
| 피해자 | **참가자** (오류 화면, 대회 중단) | 운영자 (즉시 인지) |

**`${!var:-}`**: bash의 **간접 참조(indirect expansion)**.

**`exec` 사용:**
```bash
exec "$PROJECT_DIR/.venv/bin/python" -m worker.run
```
현재 셸 프로세스를 파이썬으로 **교체**한다.
→ **시그널(Ctrl+C, SIGTERM)이 파이썬에 직접 전달**된다.
**하트비트 데몬 스레드가 제대로 죽으려면 이게 중요하다.**

---

## 14. S3/MinIO 연동

### WSL2 localhost 문제

```python
def resolve_s3_endpoint() -> str:
    endpoint = os.environ.get("DR_LOCAL_S3_ENDPOINT_URL", "")
    if endpoint and "localhost" not in endpoint and "127.0.0.1" not in endpoint:
        return endpoint
    return f"http://{_get_wsl_ip()}:9000"
```

WSL2는 경량 VM이다. Docker Swarm 컨테이너에서 `localhost`는 **컨테이너 자신**을 가리킨다.
`ip route get 1.1.1.1` 출력에서 `src` 다음 토큰이 로컬 IP다.

> **같은 로직이 `run_evaluation.sh`에도 awk로 구현되어 있다. 중복이다.**
> 한쪽만 고치면 어긋난다. 알고 있어야 할 기술 부채.

### boto3 설정

```python
session.client("s3", endpoint_url=resolve_s3_endpoint(),
    config=BotoConfig(connect_timeout=5, read_timeout=15, retries={"max_attempts": 3}))
```
- **`profile_name`**: `~/.aws/credentials`에서 키를 읽는다. **코드에 키가 없다**
- **`endpoint_url`**: AWS가 아니라 로컬 MinIO. **S3 API 호환**
- **타임아웃**: 기본 60초를 5초/15초로 줄여 빨리 실패하게 했다

### `get_paginator` — 1000개 제한 우회

`list_objects_v2`는 **한 번에 최대 1000개**만 반환한다.
직접 부르면 1001번째부터 조용히 누락된다.

> 참고: `inject_model`의 삭제는 paginator를 안 쓴다. **일관성 관점에서 아쉽다.**

---

## 15. 워커가 죽으면 어떻게 되는가 — 시점별 정리

**이 표를 설명할 수 있으면 6단계는 끝이다.**

| 죽는 시점 | DB 상태 | 파일 상태 | 복구 |
|---|---|---|---|
| claim 직전 | `queued` | 모델 있음 | 다음 폴링에 정상 처리 |
| claim 직후 (커밋 전) | `queued` (롤백) | 모델 있음 | 락 해제 → 다음에 처리 |
| claim 커밋 후 | **`running`** | 모델 있음 | **워커 재시작 시 즉시 복구** (내 worker_id) ✅ |
| 모델 다운로드 중 (http) | `running` | work_dir에 부분 파일 | 재시작 시 복구 → work_dir 새로 만듦 ✅ |
| 모델 S3 업로드 중 | `running` | S3 `model/`이 **불완전** | 재시도 시 `inject_model`이 지우고 다시 올림 ✅ |
| 평가 실행 중 | `running` | Swarm 스택 **남음** | 재시도 시 스크립트가 `dr-stop-evaluation` ✅ |
| metrics 파싱 중 | `running` | 결과는 S3에 있음 | 재평가 → `start_marker`가 새로 잡혀 **이전 결과를 못 찾음** ⚠️ |
| `db.commit()` 직전 | `running` | 영상은 work_dir에만 | **재평가되어 다른 결과**가 나올 수 있음 ⚠️ |
| **`db.commit()` 직후** | **`done`** | 영상 아직 미전달 | **영상 없이 확정** ⚠️ 리더보드에 "—" |
| 영상 전달 중 | `done` | 부분 업로드 | 서버가 0바이트/초과 검사로 거부 |
| `finally` 정리 중 | `done` | `work_dir` 남음 | 디스크만 좀 먹음 |

### ⚠️ 표시 항목의 의미

**"영상 없이 확정"** — 결과는 정확하지만 영상이 영영 안 붙는다.
**부가 정보라 감수하는 설계다.**

**"재평가되어 다른 결과가 나올 수 있다"**
시뮬레이션은 완전히 결정론적이지 않을 수 있다.
**"정확히 한 번(exactly-once)"이 아니라 "최소 한 번(at-least-once)"** 처리다.

**[전공] 분산 시스템에서 exactly-once는 매우 어렵다.**
일반적 해법은 **멱등성(idempotency)** 인데, 시뮬레이션 자체가 멱등하지 않아 불가능하다.

**하지만 실질적 피해는 없다:**
- 결과는 하나만 저장된다 (`EvaluationResult.submission_id` unique — 2단계)
- 랩타임이 조금 달라도 대회 공정성에 큰 영향이 없다
- 발생 확률이 극히 낮다

> **알고 있는 것과 모르는 것의 차이가 여기 있다.**
> "완벽하지 않지만 이 규모에서는 문제없다"고 **판단**한 것과, 그냥 모르는 것은 다르다.

### 웹 서버가 죽으면? (http 모드 전용)

| 시점 | 결과 |
|---|---|
| `fetch_model` 중 | `TransferError` → **`queued`로 복귀** → 30초 후 자동 재시도 ✅ |
| 평가 중 | 웹과 무관. 정상 진행 |
| `deliver_metrics` 중 | 로그만 남기고 진행 (사본이므로) |
| `db.commit()` 시 | **DB는 별도 컨테이너** — 웹이 죽어도 DB가 살아있으면 정상 |
| `deliver_video` 중 | 로그만 남기고 진행 → 영상 없음 |
| `request_prune` 중 | 로그만 남기고 진행 → 다음 평가에서 정리 |

**웹이 죽어도 평가 자체는 계속된다.** 결과가 DB에 들어가므로 웹이 살아나면 화면에 뜬다.
→ **좋은 분리다.**

---

## 16. 자가 점검 질문

**구조**
1. 평가를 `POST /submit` 안에서 처리하면 생기는 문제 7가지를 나열하라.
2. Celery+Redis 대신 DB 큐를 쓸 때 "진실의 원천이 하나"라는 게 왜 결정적인가?
3. 클라우드 이관 후 DB 큐가 더 유리해진 이유 2가지는?
4. 폴링의 지연 비용을 계산하고, 원격 워커 구조에서 왜 더 유리한지 설명하라.

**동시성**
5. `FOR UPDATE`만 쓰고 `SKIP LOCKED`를 빼면 워커 2대의 처리량이 왜 1대 수준이 되는가?
6. `UPDATE ... WHERE id = (SELECT ... LIMIT 1 FOR UPDATE SKIP LOCKED)` 구조가 필요한 이유는?
7. `claim_next_submission`에서 `db.commit()`을 빼면 무슨 일이 생기는가?

**상태 기계**
8. `TransferError`가 `error`가 아니라 `queued`로 가는 이유는? 세 예외의 처리 차이를 표로 그려라.
9. `TRANSFER_RETRY_SLEEP_SECONDS = 30` 이 없으면 무슨 일이 생기는가?
10. "재시도 가능한 실패"와 "영구적 실패"의 구분을 HTTP 상태 코드에 비유하라.
11. 상태 하나가 결정하는 6가지는?

**복구**
12. `recover_stale_running`의 `or_` 두 갈래는 각각 무엇을 잡는가? 왜 시간 기준이 한쪽에만 필요한가?
13. EC2 스팟 회수 시나리오에서 시간 기준만으로는 왜 부족한가?
14. 이 함수가 시작 시에만 불리는 것의 한계는? 하트비트가 그 한계를 어떻게 완화하는가?

**하트비트**
15. 하트비트를 폴링 루프에서 찍으면 무슨 일이 일어나는가? (2026-07-30 사건)
16. `daemon=True` 가 하트비트 설계에서 왜 결정적인가? 별도 프로세스로 만들면 뭐가 문제인가?
17. 하트비트 스레드가 자기 세션을 여는 이유는? `Engine`은 왜 공유해도 되는가?
18. 하트비트 예외를 삼키는 이유는? 안 삼키면 어떤 최악의 상태가 되는가?
19. 주기 30초 vs 판정 3분의 6배 여유가 막는 것은? 반대 방향의 비용은?

**파이프라인**
20. `start_marker` 기법이 막는 재앙은 무엇인가?
21. 영상을 "S3에서 먼저 받고 → 결과 커밋 → 그다음 전달" 하는 3단계 이유 3가지는?
22. `validate_checkpoint_selection`이 S3 삭제 **전에** 호출되어야 하는 이유는?
23. `kind`가 `best`/`last`가 아니면 검사를 건너뛰는 이유는?
24. `summarize_progress`의 2단계 폴백은? metrics가 비는 상황은 언제인가?
25. `extract_progress_from_log`의 방어 장치 7개를 나열하라. "관대한 파서"란?
26. `best_progress`는 `max`인데 `last_status`는 마지막 값인 이유는?
27. `xl.meta` 감지가 왜 필요했는가? 좋은 에러 메시지의 3요소는?

**두 배포 모드**
28. 코드를 두 벌로 나누지 않고 스위치 하나로 만든 이유 3가지는?
29. `WORKER_MODE` 같은 별도 변수를 안 만든 이유는?
30. `transfer.py` 네 함수의 실패 처리가 다른 기준은?
31. `request_prune` 실패를 무시해도 되는 근거는? (멱등성과 어떻게 연결되나)
32. 타임아웃을 connect/read/write로 나누는 이유는? 하나로 하면?
33. `httpx.stream`을 쓰는 이유는? 4단계의 서버 쪽 코드와 어떻게 대칭인가?
34. `local_video.resolve() != dest.resolve()` 비교가 없으면?
35. `prune_finished_team_files`가 모드를 확인하는 이유는? (2026-07-30 사건)

**외부 도구**
36. 타임아웃이 파이썬(1920) / 셸(1800) / transfer(600) 세 겹인 이유는?
37. `docker stack ps`에 `--filter desired-state=running`이 없으면? 왜 진단이 어려웠는가?
38. 로그를 `dr-stop-evaluation` 전에 저장해야 하는 이유는? 그 로그가 지금 두 번째로 쓰이는 곳은?
39. `run_worker.sh`의 환경변수 검증이 없으면 실패가 언제, 누구에게 드러나는가?
40. `exec python -m worker.run`에서 `exec`가 하트비트 스레드와 무슨 관계인가?

**실패 시나리오**
41. §15 표에서 ⚠️ 세 항목이 왜 완벽히 해결 불가능한가?
42. 웹 서버가 죽었을 때와 워커가 죽었을 때의 차이를 설명하라.

---

## 17. 실험 과제

**실험 A — SKIP LOCKED 직접 확인**
psql 창을 2개 열고 (queued가 2건 이상 있어야 함):
```sql
-- 창1
BEGIN;
SELECT id FROM submissions WHERE status='queued' ORDER BY submitted_at LIMIT 1 FOR UPDATE SKIP LOCKED;
-- 커밋하지 말고 그대로 둔다

-- 창2
SELECT id FROM submissions WHERE status='queued' ORDER BY submitted_at LIMIT 1 FOR UPDATE SKIP LOCKED;
-- 다른 id가 나오는가?  SKIP LOCKED를 빼면? → 멈춘다(대기)
-- 창1에서 ROLLBACK; 하면 창2가 풀린다
```
**이 실험 하나가 §3 전체를 몸으로 이해시킨다.**

**실험 B — 하트비트 관찰**
```sql
SELECT worker_id, last_seen_at, now() - last_seen_at AS 경과 FROM worker_heartbeats;
```
- 30초 간격으로 두 번 실행 → 갱신되는가?
- **평가가 도는 동안**에도 갱신되는가? (이게 §6의 핵심이다)
- 워커를 끄고 4분 뒤 리더보드를 열어보라. 배너가 뜨는가?
```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_worker_status.py -v
```

**실험 C — 하트비트를 루프로 되돌려 문제 재현**
`start_heartbeat_thread()` 호출을 주석 처리하고,
폴링 루프 안에 `touch_heartbeat(db, WORKER_ID)` 를 넣어보라.
평가를 하나 돌리고 **3분 뒤** `/submit`을 열면 배너가 뜨는가?
**확인 후 반드시 되돌린다.**

**실험 D — 상태 기계 수동 조작**
```sql
UPDATE submissions SET status='running', worker_id='<내hostname>', started_at=now() WHERE id=5;
```
워커를 재시작하면 **즉시** `queued`로 돌아가는가? 로그에 두 값이 찍히는가?
그다음 `worker_id='다른이름'`, `started_at=now()` 로 바꾸고 재시작하면? (안 돌아가야 한다)

**실험 E — TransferError 재현**
http 모드로 설정하고(`WORKER_TOKEN` 지정) **웹 컨테이너를 내린 뒤** 제출을 넣어보라.
```bash
docker compose stop web
# 워커 로그를 본다
```
- `queued`로 되돌아가는가?
- 30초마다 재시도하는가?
- `docker compose start web` 하면 자동으로 처리되는가?

**실험 F — 두 모드 비교**
```bash
PYTHONPATH=. .venv/bin/python -c "from worker import transfer; print('uses_http:', transfer.uses_http())"
WORKER_TOKEN=testtoken PYTHONPATH=. .venv/bin/python -c "from worker import transfer; print('uses_http:', transfer.uses_http())"
```
```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_worker_transfer.py -v
```
테스트가 두 모드를 어떻게 구분해 검증하는지 읽어보라.

**실험 G — 순수 함수 테스트**
```bash
PYTHONPATH=. .venv/bin/python -m pytest \
  tests/test_evaluation_parsing.py tests/test_progress_summary.py \
  tests/test_video_selection.py tests/test_checkpoint_validation.py -v
```
`summarize_progress`에 케이스를 추가하라 (예: metrics가 비고 로그도 없는 경우).

**실험 H — 로그 파서 직접 돌려보기**
```bash
PYTHONPATH=. .venv/bin/python -c "
from pathlib import Path
from worker.drfc import extract_progress_from_log
print(extract_progress_from_log(Path('storage/eval_logs/17.log')))
"
```
실제 로그에서 무엇이 나오는가? 로그 파일을 열어 `SIM_TRACE_LOG:` 줄을 직접 세어보라.

**실험 I — docker stack ps 관찰**
평가가 도는 동안:
```bash
watch -n 2 'docker stack ps deepracer-eval-$DR_RUN_ID'
```
끝난 뒤에도 목록에 남는지, `DESIRED STATE`가 어떻게 바뀌는지 보라. 그다음:
```bash
docker stack ps deepracer-eval-$DR_RUN_ID --filter desired-state=running -q | wc -l
```

**실험 J — 환경변수 검증 확인**
```bash
env -u DR_LOCAL_S3_BUCKET PYTHONPATH=. .venv/bin/python -m worker.run
```
(run_worker.sh를 거치지 않고 직접) 언제 어떤 에러가 나는가?
`run_worker.sh`로 같은 상황을 만들면 언제 실패하는가?

---

→ 다음: [07-ops.md](07-ops.md) — 컨테이너, 볼륨, Caddy, 그리고 두 개의 compose 파일
