# 평가 서버 자동 켜기·끄기 + 디스코드 알림 — STEP3 작업 분해 (2026-10-04)

> 진행 단계: STEP1 [명세서](worker-auto-start-stop.md) → STEP2 [기술 계획](worker-auto-start-stop-plan.md)
> → **STEP3 작업 분해(이 문서)** → STEP4 구현(**다른 세션에서 진행**).
>
> 구현 세션은 이 문서만 보고 시작할 수 있게 썼다. 작업마다 **무엇을 / 어디에 / 끝났다는 기준**을 적었다.
> "왜"는 계획서에 있으니, 판단이 애매하면 계획서의 해당 절을 본다.

---

## 0. 구현 세션을 시작하기 전에 읽을 것

1. **CLAUDE.md 전체.** 이 작업에서 특히 걸리는 규칙은 다음과 같다.
   - §1, §2: 웹 서버는 **tar 배포 + `up -d --build`**, 평가 서버는 **`git pull`**. 웹 서버 `.env`는
     tar 전송에서 빠지므로 **새 키를 서버 `.env`에 먼저** 넣는다
   - §3: `git commit`/`push`, 서버 배포·재기동은 **명령만 제시**하고 사용자가 실행한다
   - §4: 평가 서버에 AWS 자격증명을 두지 않는다. `.env` 값을 채팅으로 받지 않는다
   - §4-1: 커밋 명령을 주기 전에 **비밀값 검사**(이번에는 디스코드 웹훅과 AWS 키가 새로 생긴다)
   - §5: 스크래치 venv로 테스트한다(`MSYS_NO_PATHCONV=1`, 환경변수를 비운 셸).
     **줄바꿈(LF/CRLF)을 바꾸지 않는다.** 문서와 코드를 함께 고친다
2. [명세서](worker-auto-start-stop.md) §3(동작 요구사항), §5(성공 기준 S1~S10)
3. [계획서](worker-auto-start-stop-plan.md) 전체. 특히 §1.2의 판단 표, §2.2의 끄기 순서, §3의 표 구조

### 절대 하지 말 것

- **시험한다고 인스턴스의 "종료 시 동작"을 `terminate`로 바꾸지 않는다.** 서버 안에서 끄면 디스크째
  사라진다(계획서 §2.3). `misconfigured` 경고는 **단위 테스트로만** 확인한다.
- AWS 키와 디스코드 웹훅을 `web` 서비스 환경변수에 넣지 않는다. **`autopilot` 서비스에만** 넣는다(계획서 §1.1).
- 스위치 기본값을 `true`로 두지 않는다. 배포 직후 저절로 움직이면 안 된다(계획서 §3.1).

---

## 1. 현재 상태 (2026-10-04)

| 항목 | 상태 |
|---|---|
| 대상 서버 | 온디맨드 m7i.xlarge, 호스트 이름 `ip-172-31-61-59`, Tailscale `100.93.165.104` |
| 서버 안 `poweroff` → 중지 → 재시작 → 워커 자동 기동 | **수동 시험 완료**(계획서 §2.3 실측) |
| "종료 시 동작" | 위 시험에서 중지됨을 확인했다(= `stop`) |
| IAM 사용자 `drleader-autopilot` + 인라인 정책 | **생성 완료**, 정책 시뮬레이터 확인 완료. **액세스 키는 아직 발급하지 않음**(배포 때 발급) |
| 디스코드 웹훅 | 아직 없음 |
| 이 서버의 AMI | **확인 필요.** 없으면 배포 전에 §8.5로 먼저 뜬다 |
| 코드 | **STEP4 구현 완료(2026-10-04)** — T1~T24, 테스트 273개 통과. 구현 중 결정은 계획서 §10 |

---

## 2. 작업 목록

표기: **[코드]** 구현 세션이 작성 · **[문서]** 구현 세션이 작성 · **[운영]** 사용자가 콘솔·서버에서 실행

### Phase A — 데이터

- [x] **T1 [코드] 모델 2개** — `app/models.py`
  - `AutopilotState`(`__tablename__ = "autopilot_state"`): 계획서 §3.1의 열 그대로.
    `id`는 정수 PK(항상 1), `enabled`는 `server_default=text("false")`
  - `AutopilotEvent`(`"autopilot_events"`): 계획서 §3.2의 열 그대로. `kind`는 `String(30)`.
    `notified_at`은 nullable. `created_at`에 인덱스를 둔다(최근 기록 조회, 미발송 조회)
  - **완료 기준**: 두 모델이 기존 모델과 같은 스타일(`Mapped`, `mapped_column`, `DateTime(timezone=True)`)이다

- [x] **T2 [코드] 마이그레이션** *(2026-10-04: WSL Docker의 일회용 postgres:16 컨테이너에서 upgrade → downgrade -1 → upgrade 확인. 같은 DB로 autopilot·autostop의 실제 쿼리도 확인)* — `migrations/versions/<새 리비전>_autopilot.py`
  - `down_revision = "f3a9d6e21c58"`(현재 head인 `season_hidden`)
  - 표 2개를 만들고, `autopilot_state`에 `id=1, enabled=false` **한 줄을 넣는다**(코드가 행이 있다고 가정할 수 있게)
  - `downgrade`는 표 2개를 지운다
  - **완료 기준**: 빈 DB에서 `alembic upgrade head` → `downgrade -1` → `upgrade head`가 오류 없이 돈다
    (로컬 docker-compose의 Postgres로 확인)

### Phase B — 설정

- [x] **T3 [코드] 설정 키** — `app/config.py`의 `Settings`

  | 필드 | 기본값 | 비고 |
  |---|---|---|
  | `autopilot_instance_id` | `""` | 비어 있으면 기능 전체가 아무것도 하지 않는다 |
  | `autopilot_region` | `"ap-northeast-2"` | |
  | `discord_webhook_url` | `""` | 비어 있으면 알림을 보내지 않고 로그만 남긴다(`notified_at`도 채우지 않는다) |
  | `autopilot_poll_seconds` | `60` | |
  | `autopilot_start_timeout_minutes` | `10` | 명세서 Q1 |
  | `autopilot_manual_attention_minutes` | `10` | 계획서 P2 |
  | `autopilot_start_failed_repeat_minutes` | `30` | 계획서 §1.2 |
  | `autopilot_shutdown_check_hours` | `24` | 계획서 §1.4 |
  | `autostop_idle_minutes` | `30` | 명세서 확정값 |
  | `autostop_ssh_alert_minutes` | `60` | 명세서 Q3 |

  - AWS 키는 `Settings`에 넣지 않는다. boto3가 환경변수 `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`를 직접 읽는다
  - 기존 주석 스타일대로 **각 값이 왜 그 값인지** 한 줄씩 남긴다
  - **완료 기준**: `.env` 없이 `from app.config import settings`가 기존처럼 동작한다

### Phase C — 판단 로직(순수 함수)과 테스트

DB·AWS·디스코드·시계를 모두 인자로 받는 순수 함수로 만든다. 계획서 §7.

- [x] **T4 [코드] 웹 쪽 판단** — `app/autopilot_logic.py`
  - 입력: 현재 시각, 스위치, 대기 수, 대상 워커 마지막 생존 시각, 인스턴스 상태, `last_start_requested_at`,
    판단에 필요한 최근 이벤트(종류·시각)
  - 출력: 할 행동 목록(예: `START`, `EVENT(kind, message)`)
  - 계획서 §1.2의 **"스위치 켜짐" 표 6줄 + "스위치 꺼짐" 규칙**을 그대로 구현한다
  - 중복 방지(S5):
    - `start_timeout`: 마지막 `start_requested` 이후 이미 있으면 만들지 않는다
    - `start_failed`: 같은 종류가 30분 안에 있으면 만들지 않는다
    - `manual_attention`: 마지막 "워커 살아남 또는 대기열 비어 있음" 이후 이미 있으면 만들지 않는다.
      이 판단에 필요한 "이번 사건의 시작 시각"을 어떻게 기억할지(상태 표 열 추가 또는 이벤트로 판단)는 구현 때 정하고,
      정한 내용을 계획서 §3에 반영한다
  - "살아 있다" 기준은 `settings.worker_heartbeat_stale_minutes`(3분)

- [x] **T5 [코드] 평가 서버 쪽 판단** — `worker/autostop_logic.py`
  - 입력: 현재 시각, 스위치, 대기+평가중 수, 평가 스택 존재 여부, SSH 접속 여부, 유휴 시작 시각,
    SSH 차단 시작 시각, SSH 알림을 이미 보냈는지
  - 출력: `RESET_IDLE` / `MARK_IDLE(now)` / `MARK_SSH_BLOCK(now)` / `EVENT(ssh_blocking)` / `STOP` 중 해당하는 것들
  - 계획서 §2.2의 1~4단계 그대로

- [x] **T6 [코드] 판단 로직 테스트** — `tests/test_autopilot_logic.py`, `tests/test_autostop_logic.py`
  - 계획서 §1.2 표의 **각 줄마다 테스트 1개 이상**, §2.2의 각 분기마다 1개 이상
  - 경계값: 정확히 30분/10분/60분, 하트비트 정확히 3분
  - 중복 방지: 같은 상황을 두 번 넣으면 이벤트가 한 번만 나온다
  - `misconfigured`: 종료 시 동작이 `terminate`일 때 이벤트가 나온다(**실제 인스턴스로 시험하지 않는다**)
  - 기존 테스트처럼 FakeDB/SimpleNamespace 스타일을 쓴다(`tests/test_worker_status.py` 참고)

### Phase D — 웹 서버 `autopilot` 프로세스

- [x] **T7 [코드] AWS 래퍼** — `app/autopilot_aws.py`
  - `describe(instance_id) -> (state, private_dns_name)`, `start(instance_id)`, `shutdown_behavior(instance_id) -> str`
  - boto3 클라이언트를 이 파일 안에서만 만든다. 테스트에서 통째로 가짜로 바꿀 수 있게
  - 사설 DNS 이름 → 워커 ID: 첫 `.` 앞부분(계획서 §1.3). `worker/run.py`의 `socket.gethostname()` 값과 같은지 배포 때 확인한다

- [x] **T8 [코드] 디스코드 발송** — `app/autopilot_notify.py`
  - `httpx.post(url, json={"content": ..., "allowed_mentions": {"parse": []}})`. 멘션이 터지지 않게
  - 메시지는 한국어 한 줄 + 이벤트 종류. **참가자 팀명·계정·비밀값을 넣지 않는다**(대기 건수·시각만)
  - 실패하면 `notified_at`을 비워 둔 채 다음 주기에 다시 보낸다. 한 주기에 보내는 최대 건수를 정한다(예: 10)

- [x] **T9 [코드] 메인 루프** — `app/autopilot.py`(`python -m app.autopilot`)
  - `autopilot_poll_seconds`마다: 상태 표 읽기 → AWS describe → 상태 저장 → T4 판단 → 행동 실행 → 미발송 이벤트 발송
  - `autopilot_instance_id`가 비어 있으면 "설정되지 않음" 로그만 남기고 쉰다
  - 한 주기에서 예외가 나도 **루프는 죽지 않는다**(로그만 남김). 다만 DB에 접속할 수 없는 상태가 계속되면 로그로 알 수 있게 한다
  - 기동할 때와 `autopilot_shutdown_check_hours`마다 종료 시 동작을 확인한다(계획서 §1.4)
  - 대상 워커의 하트비트를 읽는 함수는 `app/worker_status.py`에 **새 함수로** 추가한다. 기존 `get_worker_status`는 참가자 화면이 쓰므로 바꾸지 않는다
  - **완료 기준**: 가짜 AWS·가짜 디스코드로 한 주기를 돌리는 테스트(`tests/test_autopilot_loop.py`)가 통과한다

### Phase E — 평가 서버 `autostop`

- [x] **T10 [코드] 실행 스크립트** — `worker/autostop.py`(`python -m worker.autostop`, 한 번 판단하고 끝난다)
  - 저장소 루트에서 실행해 `.env`(`DATABASE_URL`)를 읽는다. 워커 ID는 `socket.gethostname()`(`worker/run.py`와 같은 방식)
  - 바쁨 확인: DB의 `queued`+`running` 수, `docker stack ls --format '{{.Name}}'`에 `deepracer-eval-` 접두사, `who` 출력이 비어 있지 않음
  - 상태 파일: `/run/drfc-autostop/idle_since`, `ssh_block_since`, `ssh_alerted`(재부팅하면 비워져야 한다)
  - 끄기 순서(계획서 §2.2-4): `idle_stop` 이벤트 기록·커밋 → `systemctl stop drfc-worker` → 내 워커 ID로 `running`인 제출을 `queued`로 되돌림 → `systemctl poweroff`
  - 외부 명령(`docker`, `who`, `systemctl`)은 작은 함수로 감싸 테스트에서 바꿀 수 있게 한다
  - `--dry-run` 옵션: 판단 결과만 출력하고 아무것도 끄지 않는다(배포 시험용)
  - **완료 기준**: `tests/test_autostop_run.py`에서 가짜 명령으로 "끄기" 경로를 끝까지 따라가며 `poweroff`가 **마지막에 한 번만** 호출되는지 확인한다

- [x] **T11 [문서] systemd 유닛 2개** — worker-server-setup.md 새 절(T15)에 싣는다
  - `drfc-autostop.service`: `Type=oneshot`, `User=root`, `WorkingDirectory=/home/ubuntu/spg-deepracer-leaderboard`,
    `ExecStart=/home/ubuntu/spg-deepracer-leaderboard/.venv/bin/python -m worker.autostop`
  - `drfc-autostop.timer`: `OnBootSec=5min`, `OnUnitActiveSec=1min`, `WantedBy=timers.target`
  - 각 항목의 "왜"를 §8.6 표 형식으로 적는다

### Phase F — 관리자 페이지

- [x] **T12 [코드] 라우트** — `app/routers/admin.py`
  - `GET /admin/autopilot`: 스위치, `instance_state`와 시각, 대상 워커 마지막 생존 시각, 최근 이벤트 20건
  - `POST /admin/autopilot`: `action=enable|disable` 폼 값, 기존 `set_uploads_paused`와 같은 패턴
    (`Form`, `Depends(get_current_admin)`, 처리 뒤 303 리다이렉트). `switch_changed` 이벤트에 관리자 `login_id`를 남긴다
  - `autopilot_instance_id`가 비어 있으면 "설정되지 않음"을 보여 주고, POST는 바꾸지 않고 돌아간다

- [x] **T13 [코드] 템플릿** — `app/templates/admin/autopilot.html` + `admin/dashboard.html`에 링크
  - 기존 관리자 화면 스타일(`season_detail.html`)을 따른다
  - 상태 문구 예: "꺼짐(중지됨)", "켜지는 중", "켜짐 · 워커 정상", "켜짐 · 워커 응답 없음"

- [x] **T14 [코드] 테스트** — `tests/test_admin_autopilot.py`
  - 미인증 GET/POST → **404**(`get_current_admin` 규칙, `tests/test_upload_pause.py` 참고)
  - enable/disable이 상태와 이벤트를 바꾼다
  - 인스턴스 ID 미설정이면 POST가 아무것도 바꾸지 않는다

### Phase G — 배포 구성

- [x] **T15 [코드] compose** — `docker-compose.prod.yml`에 `autopilot` 서비스
  - `build: .`, `command: python -m app.autopilot`, `restart: unless-stopped`, `mem_limit: 150m`,
    `depends_on: db (service_healthy)`, `web`과 같은 `DATABASE_URL` 형식
  - 환경변수: `AUTOPILOT_INSTANCE_ID`, `AUTOPILOT_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
    `DISCORD_WEBHOOK_URL`(전부 `${...:-}`로 비어도 뜨게)
  - `web`에는 `AUTOPILOT_INSTANCE_ID` **하나만** 추가한다(관리자 화면의 "설정되지 않음" 판단용)
  - 각 값 옆에 기존 스타일대로 이유 주석
  - 개발용 `docker-compose.yml`에는 넣지 않는다(로컬에서 AWS를 부르면 안 된다)

- [x] **T16 [코드] `.env.example`** — 위 키들을 자리표시로 추가. 웹 서버 전용임과 이유(계획서 §1.1)를 주석으로

### Phase H — 문서

- [x] **T17 [문서] worker-server-setup.md**
  - 새 절 "자동 켜기·끄기": 개요, "종료 시 동작" 확인법, autostop 유닛 등록·확인(`systemctl list-timers`, `--dry-run`),
    로그 보기(`journalctl -u drfc-autostop`), SSH로 접속해 있으면 꺼지지 않는다는 안내
  - **IAM 절차**: 사용자 생성(콘솔 접근 없음) → 인라인 정책(JSON 전문, 계정 ID는 하이픈 없이) →
    **정책 시뮬레이터 확인 표** → 액세스 키 발급(용도 "AWS 외부 애플리케이션", 비밀 키는 한 번만 보임) → 키 유출 시 교체.
    2026-10-04의 `ap-nortease-2` 오타 사례를 "왜 시뮬레이터를 거치나"의 근거로 남긴다
  - §8.8 노트북 예비 워커: 2026-10-04 폐기 표시(명세서 Q2)
  - §9 비용: "온디맨드 + 자동 켜기·끄기" 행 추가, 실제 값은 대회 후 기록(S8)
- [x] **T18 [문서] server-access.md** — 새 `.env` 키, `docker compose -f docker-compose.prod.yml logs -f autopilot`
- [x] **T19 [문서] operations.md** — 서버가 켜지고 꺼지는 것이 정상이라는 안내, **디스코드 알림 종류별 의미와 대응 표**
- [x] **T20 [문서] handover.md** — 대회 시작·종료 체크리스트에 스위치, IAM 사용자·키 인수인계
- [x] **T21 [문서] CLAUDE.md §4-1** — 검사 패턴에 `discord.com/api/webhooks/`, `DISCORD_WEBHOOK_URL=` 뒤 실제 값 추가
- [x] **T22 [문서] docs/study** — 새 모듈을 인용해야 할 곳이 있는지 검색해 필요하면 갱신(CLAUDE.md §5)
- [x] **T23 [문서] 계획서·명세서 상태 갱신** — 구현 중 바뀐 결정(T4의 `manual_attention` 처리 등)을 계획서에 반영

### Phase I — 검증과 배포

- [x] **T24 [코드] 전체 테스트** — 스크래치 venv에서 `pytest` 전체 통과(CLAUDE.md §5의 두 함정 주의)
- [ ] **T25 커밋 준비** — CLAUDE.md §4-1 비밀값 검사 → 결과 한 줄 보고 → 커밋 명령 **제안만**

배포는 계획서 §6 순서를 따른다. **모든 [운영] 작업은 사용자가 실행한다.**

- [ ] **T26 [운영] 사전 준비**
  - 이 서버의 AMI가 최신인지 확인하고, 없으면 먼저 뜬다(§8.5)
  - 디스코드 운영 채널 → 채널 설정 → 연동 → 웹후크 만들기 → URL 복사(채팅에 붙이지 않는다)
  - IAM `drleader-autopilot` → 액세스 키 발급
- [ ] **T27 [운영] 웹 서버** — 서버 `.env`에 키 추가 → tar 배포 → `up -d --build` → `logs autopilot` 확인.
  스위치는 아직 **꺼짐**이다. 관리자 페이지에서 EC2 상태가 보이는지 확인한다
- [ ] **T28 [운영] 디스코드 시험 알림** — 스위치를 켰다 끄면 `switch_changed`가 디스코드에 오는지 확인
- [ ] **T29 [운영] 평가 서버** — `git pull` → 유닛 2개 등록 → `python -m worker.autostop --dry-run`으로 판단 확인 → 타이머 시작
- [ ] **T30 [운영] 인수 시험**(스위치 켬). 결과를 계획서 끝에 날짜와 함께 기록한다

  | 기준 | 시험 방법 |
  |---|---|
  | S3 | 대기열이 빈 채 SSH 접속을 끊고 30분 기다린다 → **중지됨**, 디스코드 `idle_stop` |
  | S1 | 서버가 중지된 상태에서 테스트 모델 제출 → 2분 안에 디스코드 `start_requested`, 콘솔 "시작 중" |
  | S2 | 같은 제출의 평가가 제출 후 10분 안에 시작된다(관리자 화면·워커 로그) |
  | S4 | 평가가 끝날 때까지 꺼지지 않는다. 평가가 끝난 뒤 30분이 지나서야 꺼진다 |
  | S5 | 위 과정에서 같은 알림이 두 번 오지 않는다 |
  | S9 | SSH로 접속한 채 30분 이상 둔다 → 꺼지지 않는다. 60분 → `ssh_blocking` 알림 |
  | S7 | 스위치를 끄고 제출 → 켜지지 않는다. 10분 뒤 `manual_attention` 1번 |
  | S10 | 스위치 변경이 다음 주기(1분)에 반영된다 |
  | S6 | (선택) 평가 서버에서 `sudo systemctl disable --now drfc-worker` 후 중지 → 제출 → 10분 뒤 `start_timeout`. **시험 뒤 반드시 `enable --now`로 되돌린다** |
  | S8 | 대회 종료 후 결제 콘솔에서 실제 인스턴스 요금을 기록한다 |

---

## 3. 순서와 의존 관계

```
T1 → T2 ─┐
T3 ──────┼─→ T4·T5 → T6 → T7·T8 → T9 ─┐
         │                  T10 → T11 ─┼─→ T15·T16 → T17~T23 → T24 → T25 → T26~T30
         └─→ T12 → T13 → T14 ──────────┘
```

- Phase C(판단 로직)를 먼저 끝내고 테스트로 고정한 뒤에 루프와 화면을 붙인다. 판단이 틀리면 서버가 엉뚱하게 켜지고 꺼지기 때문이다.
- 문서(Phase H)는 코드가 확정된 뒤에 쓴다. 인용할 함수 이름과 명령이 바뀌지 않게.
- 배포(T26~)는 테스트 통과(T24)와 커밋(T25) 이후에만 한다.

## 4. 열린 질문

없음 — STEP3 확정(2026-10-04). 구현 중 새로 생기는 결정은 계획서에 반영하고(T23), 사용자 확인이 필요한 것은 구현 세션에서 묻는다.
