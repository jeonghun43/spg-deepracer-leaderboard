# 이 프로젝트 완전 분해 — 학습 인덱스

> 대상: 이 코드를 직접 만들었지만 "왜 이렇게 되어 있는지"를 밑바닥까지 이해하고 싶은 사람.
> 방식: top-down. 큰 그림 → 파일 → 함수 → 한 줄 → 그 밑에 깔린 CS 개념.
>
> 각 절은 **[쉬움]** 과 **[전공]** 두 버전으로 나뉜다.
> - **[쉬움]**: 비유 위주. 중학생이 읽어도 그림이 그려지는 수준.
> - **[전공]**: 정확한 용어, 대안 설계, 트레이드오프, 실패 시나리오.
>
> 각 주제는 항상 **무엇을(What) → 왜(Why) → 어떻게(How)** 순으로 내려간다.
>
> **기준 코드**: `develop` 브랜치 작업 트리 (관리자 은닉 · 워커 원격 모드 · 업로드 진행률 반영)

---

## 전체 지도 — 이 서비스는 무엇인가

**[쉬움]**
학교에서 종이비행기 대회를 연다고 하자. 참가자가 비행기를 만들어 오면, 선생님이 하나씩 날려보고
날아간 거리를 재서 칠판에 순위를 적는다. 이 프로젝트는 그걸 컴퓨터가 대신 하는 것이다.

- **비행기** = 참가팀이 학습시킨 AI 모델 파일
- **날려보기** = 컴퓨터 시뮬레이터(DeepRacer)에서 3바퀴 주행
- **거리 재기** = 랩타임 측정
- **칠판** = 웹 리더보드
- **선생님** = 서버 프로그램

**[전공]**
파일 업로드를 트리거로 하는 **비동기 배치 처리 파이프라인** + 그 결과를 조회하는 **읽기 전용 공개 뷰**.
전형적인 *producer-consumer* 구조이며, 큐를 별도 미들웨어(Redis/RabbitMQ/Celery) 없이
**RDBMS 테이블 하나로 구현**한 것이 이 프로젝트의 가장 특징적인 설계 결정이다.

### 배포 형태가 두 가지다 — 이걸 먼저 이해해야 코드가 읽힌다

같은 코드가 **환경변수 하나(`WORKER_TOKEN`)로 두 모양**을 갖는다.

**(A) local 모드 — 웹과 워커가 같은 기기 (개발/초기 운영)**
```
┌─ 노트북(WSL2) ────────────────────────────────────────────┐
│  [web 컨테이너] ──┐                                        │
│  [db 컨테이너]  ──┤ 같은 디스크 ./storage 를 bind mount 공유 │
│  [워커 프로세스] ─┘                                        │
│        └─ DRFC(Docker Swarm) + MinIO                       │
└────────────────────────────────────────────────────────────┘
```

**(B) http 모드 — 웹은 클라우드, 워커는 별도 기기 (현재 운영)**
```
[참가자] ──HTTPS──> ┌─ Lightsail 서버 ─────────────┐
                    │ [caddy] → [web] ← [db]       │
                    │        ./storage (원본 보관)  │
                    └──────────────┬───────────────┘
                                   │ Tailscale 사설망
                        DB 접속 ───┤ + HTTP(/internal/*, X-Worker-Token)
                                   ▼
                    ┌─ 평가 서버(EC2 / 노트북) ─────┐
                    │ [워커 프로세스]                │
                    │   ├ 모델 다운로드 (HTTP)       │
                    │   ├ DRFC 평가                  │
                    │   └ 영상·metrics 업로드 (HTTP) │
                    └────────────────────────────────┘
```

**핵심**: 워커는 어느 모드에서도 **같은 `worker/run.py`** 다.
차이는 `worker/transfer.py`가 흡수한다. → [06-worker.md](06-worker.md) §11

### 요청과 데이터의 흐름

```
[참가자 브라우저]        [관리자 브라우저]           [구경꾼 브라우저]
  │ POST /submit          │ 비밀 경로로 로그인         │ GET /leaderboard
  │ (upload.js 진행률)    │ → /admin/*                 │
  ▼                       ▼                            ▼
┌───────────────────────────────────────────────────────────────────┐
│ FastAPI (컨테이너, 8000)                                          │
│  main.py → SessionMiddleware                                      │
│    ├ auth.router          팀 로그인                               │
│    ├ submissions.router   제출 화면·업로드                        │
│    ├ leaderboard.router   공개 리더보드                           │
│    ├ admin.router         /admin/* (미인증이면 404)               │
│    ├ admin.login_router   .env의 비밀 경로에만 붙는 로그인 폼      │
│    └ internal.router      /internal/* (워커 전용, X-Worker-Token) │
└──────────┬──────────────────────────────────┬─────────────────────┘
           │ INSERT submissions('queued')      │ SELECT
           ▼                                   ▼
     ┌──────────────────────────────────────────────┐
     │ PostgreSQL   ← 이게 곧 "작업 큐"              │
     │   submissions / worker_heartbeats / ...       │
     └──────────┬───────────────────────────────────┘
                │ 5초 폴링 · FOR UPDATE SKIP LOCKED
                │ 30초마다 하트비트
                ▼
     ┌──────────────────────────────────────────────┐
     │ worker/run.py                                 │
     │  ├ transfer.fetch_model()   로컬 or HTTP      │
     │  ├ drfc.validate_checkpoint_selection()       │
     │  ├ drfc.inject_model()      → MinIO           │
     │  ├ run_evaluation.sh        → Docker Swarm    │
     │  ├ parse_evaluation_result / summarize_progress│
     │  └ transfer.deliver_video / deliver_metrics    │
     └──────────────────────────────────────────────┘
```

---

## 학습 순서와 각 단계에서 얻는 것

| # | 문서 | 다루는 파일 | 이 단계가 끝나면 답할 수 있어야 하는 질문 |
|---|---|---|---|
| 1 | [01-skeleton.md](01-skeleton.md) | `main.py` `config.py` `db.py` `render.py` | URL을 치면 왜 그 함수가 실행되나? 비밀 경로는 어떻게 등록되나? |
| 2 | [02-data-model.md](02-data-model.md) | `models.py` `migrations/` | 동시 제출 1건을 왜 파이썬 `if`로는 못 막나? 컬럼 추가는 왜 무해한가? |
| 3 | [03-auth.md](03-auth.md) | `deps.py` `security.py` `auth.py` `admin_lockout.py` | 미인증 `/admin`이 왜 리다이렉트가 아니라 404인가? IP 잠금만으로 왜 부족한가? |
| 4 | [04-submit.md](04-submit.md) | `submissions.py` `quota.py` `storage_paths.py` `upload.js` | 500MB 업로드가 왜 메모리를 안 터뜨리나? JS가 꺼져 있어도 왜 제출되나? |
| 5 | [05-leaderboard.md](05-leaderboard.md) | `leaderboard.py` `records.py` `render.py` `templates/` | 팀명에 `<script>`를 넣어도 왜 안전한가? 미완주 팀에게 무엇을 보여주나? |
| 6 | [06-worker.md](06-worker.md) | `worker/*` `worker_status.py` `internal.py` | 워커가 죽으면 그 제출은? 웹이 죽으면? 두 실패가 왜 다르게 처리되나? |
| 7 | [07-ops.md](07-ops.md) | `Dockerfile` `docker-compose*.yml` `Caddyfile` | 개발용과 운영용 compose가 왜 다른가? 필수 환경변수를 왜 기동 실패로 강제하나? |
| 8 | [08-crosscutting.md](08-crosscutting.md) | 전체 | 시간·실패·동시성·은닉을 한 번에 정리 + 졸업 시험 |

---

## 이 프로젝트를 관통하는 7개의 큰 질문

공부하는 내내 이 질문들을 머릿속에 두면 흩어진 코드가 하나로 묶인다.

### Q1. 왜 웹 서버와 워커를 분리했는가?
평가 한 건에 **10분**이 걸린다. HTTP 요청 하나를 10분 붙잡고 있으면
브라우저는 타임아웃 나고, 그 동안 다른 요청을 못 받는다.
→ **"오래 걸리는 일은 요청-응답 주기 밖으로 뺀다"** 는 웹 백엔드의 제1원칙.

### Q2. 왜 큐를 DB 테이블로 만들었는가?
Redis/Celery를 붙이면 운영할 프로세스가 하나 더 늘고, "DB에는 done인데 큐에는 남아있는"
**이중 진실(dual source of truth)** 문제가 생긴다. 하루 50건 규모에서는 DB 폴링이 압도적으로 단순하다.

### Q3. 왜 상태(status)를 이렇게 집요하게 관리하는가?
`queued → running → done/error`, 그리고 되돌아오는 `running → queued`.
이 상태가 곧 **하루 제출 한도**, **동시 제출 제한**, **대기열 순서**, **재시도 가능 여부**,
**디스크 정리 대상**을 전부 결정한다. 상태 하나가 잘못 남으면 팀은 영원히 제출을 못 한다.

### Q4. 왜 "실패"에 대한 코드가 이렇게 많은가?
외부 도구(DRFC), 외부 저장소(MinIO), 외부 프로세스(bash), 사용자 입력(압축 파일),
그리고 **네트워크 건너편의 웹 서버** — **내 통제 밖에 있는 것이 5개**다.
이런 시스템에서 코드의 절반이 에러 처리인 건 정상이다.

### Q5. 왜 "경로"에 별도 모듈(`storage_paths.py`)이 있는가?
같은 파일이 웹에서는 `/app/storage/models/1/6/x.tar.gz`,
워커에서는 `/mnt/c/.../storage/models/1/6/x.tar.gz`로 보인다.
**절대 경로를 DB에 저장한 순간 시스템이 깨진다.** 실제로 장애가 났다.

### Q6. 왜 관리자 로그인 주소를 숨기는가?
공개 인터넷에 노출된 서비스의 `/admin/login`은 **자동 스캐너가 하루 수백 번 두드린다.**
은닉은 그 자체로 방어가 아니지만(security through obscurity),
**"자동화된 대량 시도를 통째로 없애는" 효과가 크다.** 그 뒤에 잠금이 2차 방어선으로 선다.
→ [03-auth.md](03-auth.md) §6

### Q7. 왜 같은 코드가 두 배포 형태를 지원하는가?
이관(노트북 → 클라우드) 도중에 코드를 두 벌로 나누면 **둘 다 검증이 안 된다.**
스위치 하나(`WORKER_TOKEN`)로 두 모드를 지원하면, 이관 전에 한 대에서 http 모드를 미리 시험해볼 수 있다.
→ [06-worker.md](06-worker.md) §11

---

## 파일별 역할 한 줄 요약 (현재 코드 기준)

### `app/` — 웹 애플리케이션
| 파일 | 역할 |
|---|---|
| `main.py` | 앱 조립. 미들웨어·5개 라우터·정적파일 마운트 |
| `config.py` | 환경변수 → 설정 객체. `admin_login_path` 정규화 검증기 포함 |
| `db.py` | 엔진·세션 팩토리·ORM Base |
| `models.py` | 테이블 7개 (Season/Team/Account/AdminAccount/Submission/EvaluationResult/**WorkerHeartbeat**) |
| `deps.py` | 인증 의존성. 관리자는 미인증 시 **404** |
| `security.py` | bcrypt 해시, 안전한 임의 비밀번호 |
| `admin_lockout.py` | **관리자 로그인 무차별 대입 잠금** (IP + 아이디 이중 카운터) |
| `quota.py` | 하루 제출 한도 (KST 자정 기준) |
| `records.py` | 팀별 최고기록 산출 (리더보드·보존정책 공용) |
| `retention.py` | 최고기록 외 파일 삭제 |
| `season_archive.py` | 시즌 종료 처리 |
| `storage_paths.py` | 컨테이너↔호스트 경로 차이 흡수 |
| `worker_status.py` | **하트비트로 평가 서버 생존 판정** |
| `render.py` | Jinja 템플릿 + **`failure_summary` 커스텀 필터** |
| `seed.py` | 초기 관리자 계정 생성 |
| `routers/auth.py` | 팀 로그인/로그아웃 |
| `routers/submissions.py` | 제출 화면·업로드 (**JSON/리다이렉트 이중 응답**) |
| `routers/leaderboard.py` | 리더보드·시즌 목록 |
| `routers/admin.py` | 시즌·팀 관리 + **비밀 경로 로그인 폼** |
| `routers/internal.py` | **워커 전용 파일 송수신 API** |
| `static/upload.js` | **업로드 진행률 표시 (점진적 향상)** |

### `worker/` — 평가 워커 (호스트에서 실행)
| 파일 | 역할 |
|---|---|
| `run.py` | 큐 폴링·상태 전이·결과 저장·**하트비트 스레드** |
| `drfc.py` | DRFC 연동, S3 조회, metrics 파싱, **체크포인트 검증**, **로그에서 진행률 추출** |
| `transfer.py` | **local/http 두 모드의 파일 송수신** |
| `run_evaluation.sh` | DRFC 실행 + 완료 폴링 + 로그 수집 |
| `run_worker.sh` | 환경변수 검증 후 워커 기동 |

### 배포
| 파일 | 역할 |
|---|---|
| `Dockerfile` | 웹 이미지 |
| `docker-compose.yml` | **개발/노트북용** (DB 포트 노출, 웹 직접 노출) |
| `docker-compose.prod.yml` | **클라우드용** (Caddy HTTPS, 필수 환경변수, 메모리 상한, 헬스체크) |
| `Caddyfile` | 리버스 프록시 + 자동 HTTPS |

---

## 공부하는 방법 (권장)

1. 문서를 읽고 → **코드 파일을 열어 대조**한다. 문서만 읽으면 남지 않는다.
2. 각 문서 끝의 **자가 점검 질문**에 소리내어 답해본다. 막히면 그 절만 다시 읽는다.
3. **실험 과제**를 실제로 해본다. 코드를 고쳐 깨뜨려 보는 것이 가장 확실하다.
   ```bash
   PYTHONPATH=. .venv/bin/python -m pytest tests -q
   ```
4. 순서를 지켜라. 6번(워커)부터 보면 `models.py`를 모르는 상태라 반드시 막힌다.

> **주의**: 운영 중인 서비스/DB에는 실험하지 말 것. `.env`가 운영 DB를 가리킬 수 있다
> (`tests/test_admin_access.py`가 그 사실을 주석으로 남기고 있다).
