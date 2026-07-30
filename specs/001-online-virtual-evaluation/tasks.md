# STEP 3 — 작업 분해: 가상 트랙 기반 자동 평가 플랫폼

> [plan.md](plan.md)(STEP2)의 아키텍처·데이터 모델을 실행 가능한 작업 단위로 쪼갠다. STEP4(구현)에서 이 목록을 순서대로 처리한다. `[P]` 표시는 서로 다른 파일을 건드리고 의존성이 없어 병렬로 진행해도 되는 작업.

## 진행 순서 (Phase)

```
Phase 0 프로젝트 셋업
   └─▶ Phase 1 데이터 모델 & DB
         └─▶ Phase 2 인증 · 계정
               └─▶ Phase 3 관리자 기능 ──┐
               └─▶ Phase 4 제출 & 큐 ────┼─▶ Phase 5 평가 워커 ──▶ Phase 6 리더보드 & 참가자 화면 ──▶ Phase 7 배포/운영 ──▶ Phase 8 검증
```
Phase 3과 4는 Phase 2 완료 후 병렬 진행 가능. Phase 5는 Phase 4(큐/데이터 모델)가 끝나야 시작 가능.

---

## Phase 0 — 프로젝트 셋업

> 로컬 WSL2 Ubuntu 환경에서 `dr-start-evaluation`이 정상 동작하는 것은 이미 확인됐고, DRFC 관련 환경변수(`DR_LOCAL_S3_*` 등)는 DRFC 자체의 `run.env`/`system.env`에 이미 있으므로 우리 앱이 별도로 `.env.example`에 중복 정의하지 않는다.

- [x] **T001** 리포지토리 기본 구조 생성 (`app/`, `worker/`, `storage/`, `migrations/`, `tests/`)
- [x] **T002** Python 의존성 정의 (FastAPI, SQLAlchemy, Alembic, boto3, bcrypt, Jinja2, pytest 등) — `pyproject.toml` 또는 `requirements.txt`
- [x] **T003** `docker-compose.yml` 골격 작성 — web / worker / postgres 3개 서비스. 우리 앱 자체 설정(DATABASE_URL, 세션 시크릿 등)만 `environment`에 정의하고, DRFC 관련 값은 DRFC의 `run.env`/`system.env`를 그대로 참조(중복 정의 금지)

## Phase 1 — 데이터 모델 & DB

- [x] **T004** `Season` 테이블/모델 정의 (id, 이름, 시작일, 마감일, 트랙 식별자, 상태) — plan.md §4
- [x] **T005** `Team` 테이블/모델 정의 (id, season_id FK, 팀명, 생성 시각, 실격 여부)
- [x] **T006** `Account` 테이블/모델 정의 (id, team_id FK, 로그인 아이디, 비밀번호 해시)
- [x] **T007** `Submission` 테이블/모델 정의 (id, team_id FK, 제출 시각, 모델 파일 경로, 상태 enum: 대기/평가중/완료/오류). **제약**: 팀별로 상태가 "대기" 또는 "평가중"인 레코드는 동시에 최대 1건만 존재해야 한다 — DB 유니크 인덱스(부분 인덱스) 또는 애플리케이션 레벨 검증으로 강제
- [x] **T008** `EvaluationResult` 테이블/모델 정의 (id, submission_id FK(1:1), 완주 여부 enum(완주/미완주-타임아웃), 랩타임 nullable, 트랙 이탈 이벤트 수, 영상 경로, metrics json 경로, 완료 시각)
- [x] **T009** Alembic 초기 마이그레이션 작성 및 로컬 DB에 적용 확인
- [x] **T010 [P]** 관리자용 시드 스크립트 작성 (최초 관리자 계정 생성용)

## Phase 2 — 인증 · 계정

- [x] **T011** 비밀번호 해시(bcrypt) 유틸 작성
- [x] **T012** 세션 기반 로그인/로그아웃 엔드포인트 (팀 계정용)
- [x] **T013 [P]** 관리자 로그인 엔드포인트 (팀 계정과 분리된 권한 체계, plan.md §6)
- [x] **T014** 로그인 필요 페이지에 대한 인증 미들웨어/의존성(FastAPI `Depends`) 작성
- [x] **T015 [P]** 팀 계정 로그인 화면 (Jinja2 템플릿)

## Phase 3 — 관리자 기능

- [x] **T016** 시즌 생성/수정 화면 + API (이름, 기간, 트랙, 상태 전이: 준비중→진행중→마감→아카이브됨)
- [x] **T017** 참가팀 사전 등록 화면 + API (팀명 입력 시 `Team` + `Account` 동시 생성, 발급된 로그인 정보 표시/다운로드)
- [x] **T018** 제출 대기열 모니터링 화면 (대기/평가중/완료/오류 상태별 목록, spec.md 4.1의 "대기열 모니터링")
- [x] **T019** 실격 처리 기능 — 팀 또는 특정 제출을 실격 플래그 처리 (리더보드에서 조용히 제외되는지 확인 포함)
- [x] **T020** 시즌 아카이브 배치 작업 — plan.md §5.4: 최고기록 외 제출의 모델/영상 삭제, `Account` 삭제, `Season.상태 = 아카이브됨` 전환
- [x] **T021** 관리자용 "팀 오늘 제출 카운트 수동 조정" 기능 — 하루 제출 한도 테스트/운영 편의를 위해, 실제로 5회를 다 채우지 않고도 관리자가 특정 팀의 "오늘 완료 카운트"를 원하는 값으로 직접 설정할 수 있게 한다 (예: 4로 설정해두면 다음 제출 1건만으로 한도 초과 동작을 바로 검증 가능). 설계: 팀+날짜 단위 override 값을 저장해두고, 카운트 계산 시 override가 있으면 실제 카운트 대신 그 값을 사용

## Phase 4 — 제출 & 큐

- [x] **T022** 모델 파일 업로드 API — 다음 순서로 검증: (1) 이 팀이 현재 "대기" 또는 "평가중" 상태의 제출을 갖고 있는지 확인, 있으면 즉시 거부(새 모델 업로드 불가) → (2) 오늘 완료 카운트가 5회 미만인지 확인 → (3) 파일 확장자·용량 검증(실패 시 큐에 등록하지 않고 즉시 오류 반환, 카운트 제외)
- [x] **T023** 하루 제출 횟수 조회 로직 (KST 자정 기준 리셋, "완료" 상태만 카운트, 관리자 override 값이 있으면 우선 적용) — plan.md §5.3
- [x] **T024** 제출 페이지에 "오늘 남은 횟수" 및 "현재 진행 중인 제출이 있어 업로드 불가" 상태 표시
- [x] **T025** `Submission` "대기" 상태 생성 + 파일을 `storage/models/{season}/{team}/`에 저장
- [x] **T026** 참가자용 제출 상태 화면 — 대기열 내 순서/예상 대기 시간 표시 (§1의 "10분/건" 수치 기반 추정치)

## Phase 5 — 평가 워커

- [x] **T027** 워커 프로세스 골격 — 큐를 폴링해 가장 오래된 "대기" 건을 "평가중"으로 전이
- [x] **T028** `dr-start-evaluation` 실행 래퍼 작성 (모델 파일 경로 전달, 트랙 설정 반영)
- [x] **T029** MinIO(S3 호환) 결과 조회 함수 — boto3로 `valuation` 포함 + `.json` 종료 키 중 LastModified 최신 오브젝트 탐색 (plan.md §5.1)
- [x] **T030** metrics json 파싱 함수 — 완주 여부, 랩타임, 트랙 이탈 이벤트 수 추출, 완주/미완주(타임아웃) 판정 로직
- [x] **T031** 평가 결과 영상 파일 수집/저장 (`storage/videos/{season}/{team}/`)
- [x] **T032** `EvaluationResult` 생성 + `Submission` 상태를 "완료"로 전이 (완주·미완주 모두 포함, 이 시점에 팀이 다시 업로드 가능해짐)
- [x] **T033** DRFC 실행 자체가 비정상 종료된 경우 `Submission` 상태를 "오류"로 전이 + 사유 기록 (카운트 제외, 이 시점에도 팀이 다시 업로드 가능해짐)
- [x] **T034** 워커 예외 처리·재시작 복원력 (프로세스가 죽어도 "평가중"에 멈춰있는 건을 감지해 복구하는 방법 검토)

## Phase 6 — 리더보드 & 참가자 화면

- [x] **T035** 공개 리더보드 쿼리 — 팀별 최고 완주 기록만 조회, 실격 팀 제외, **랩타임 오름차순 정렬(가장 빠른 기록이 1위로 최상단)**. 표시 항목: 순위, 팀명, 전체 누적 제출 횟수, 최고 랩타임, 최고기록 영상 URL
- [x] **T036** 동점 처리 로직 — 먼저 기록을 세운(제출 시각이 빠른) 팀 우선
- [x] **T037** 미완주 팀 표시 — 완주 목록 아래 별도 구획, 순위 번호 없음
- [x] **T038** 리더보드 화면 (로그인 불필요, 시즌 선택 드롭다운 포함 — 과거 아카이브 시즌도 조회 가능)
- [x] **T039 [P]** 팀별 최고기록 영상 재생 UI (리더보드에서 바로 재생/열람)

> 참가자 전용 "내 제출 이력" 페이지는 만들지 않는다 — 리더보드의 제출 횟수·최고기록·영상 정보로 충분하다는 판단.

## Phase 7 — 배포/운영

- [x] **T040** `docker-compose.yml` 완성 — 웹/워커/DB 볼륨 마운트(모델·영상·로그가 컨테이너 재시작에도 유지되도록)
- [x] **T041** 운영 문서 작성 — 서버 기동/재시작 절차, 백업 대상(DB, storage 디렉터리)
- [x] **T042** GPU 서버 이전 대비 — [gpu-server-migration.md](gpu-server-migration.md) 절차대로 실제 이관 시 수행할 수 있도록 현재 구성이 그 문서의 전제(Docker Compose 기반, storage 분리, DRFC 환경변수 비중복)를 만족하는지 최종 점검

## Phase 8 — 검증

- [x] **T043** 참가자 전체 여정 수동 테스트 — 로그인 → 제출 → 대기열 확인 → 결과 확인(완주/미완주 각각) → 리더보드 반영 확인
- [x] **T044** 관리자 전체 여정 수동 테스트 — 시즌 생성 → 팀 등록 → 실격 처리 → 아카이브
- [x] **T045** 하루 제출 한도 경계 테스트 — 관리자 카운트 조정 기능(T021)으로 4회로 맞춘 뒤 5번째 제출 시 정상 카운트되고 6번째는 차단되는지 확인. 업로드 오류(카운트 제외)와 미완주(카운트 포함)도 함께 확인
- [x] **T046** 동시 제출 제한 테스트 — 한 팀이 제출을 대기/평가중 상태로 둔 채 새 모델을 업로드하려 하면 거부되는지 확인. 여러 팀이 동시에 제출했을 때 큐가 순차적으로 정상 처리되는지도 함께 확인
- [x] **T047** 시즌 아카이브 후 리더보드·영상이 그대로 조회되는지, 계정 삭제로 로그인이 막히는지 확인

### 검증 방법과 결과 (2026-07-25)

- **실환경 DRFC 평가**: 제출 4번으로 업로드 → 큐 → `dr-start-evaluation` → metrics 파싱 → 결과 저장까지 실제로 1회 완주 검증 (`finished`, 109.801초, 이탈 0회). metrics JSON·시뮬레이션 로그·영상이 모두 `storage/` 아래에 남는 것까지 확인.
- **T043~T047**: [tests/verify_phase8.py](../../tests/verify_phase8.py)로 기동 중인 웹앱에 실제 HTTP 요청을 보내 재현 — **36개 항목 전부 통과**. 평가 자체는 위에서 이미 실환경 검증이 끝났으므로, 이 스크립트는 워커를 끈 채로 평가 결과만 워커와 동일한 형태로 채워 넣고 그 뒤 로직(카운트·동시제출·리더보드·아카이브)을 검증한다.
  - 실행: 워커를 정지한 뒤 `PYTHONPATH=. .venv/bin/python -m tests.verify_phase8`
  - 검증용 시즌이 하나 생성되므로, 끝난 뒤 해당 시즌은 삭제하고 워커를 다시 켠다.
- **발견·수정한 버그**: 관리자 "오늘 완료 카운트" 조정이 절대값이라 조정 이후 완료된 평가가 카운트에 반영되지 않았다 (6번째 제출이 차단되지 않음). 실제 완료 건수에 더해지는 델타로 변경 (`daily_count_adjustment`, 마이그레이션 `a1c4f2b8d907`).
- ~~**미해결(운영에 지장 없음)**: 평가 영상 MP4가 261바이트로 비어 있다. DRFC의 `agents_video_editor`가 `/agent/mp4_video_metrics` ROS 서비스 호출에 실패해 프레임이 하나도 쌓이지 않는 문제로, 이 플랫폼을 붙이기 전부터 있던 환경 이슈다(관련 [DRFC 이슈 #67](https://github.com/aws-deepracer-community/deepracer-for-cloud/issues/67)).~~
  → **이 진단은 틀렸다 (2026-07-26 정정).** DRFC는 카메라 앵글 3종을 모두 만들고 있었고 `camera-pip`·`camera-45degree`는 13.8MB로 정상이었다. 우리 워커가 하필 깨진 `camera-topview`를 하드코딩해서 받고 있었던 것이 원인이다. Phase 9-5(T071)에서 수정했다.

## Phase 9 — 업로드 경로 긴급 수정 & 운영 편의 개선 (2026-07-26 추가)

> 실제 운영 중 나온 두 가지 불편(리더보드 매번 재선택 / 팀 계정 1개씩 발급)을 해소하고, 그 준비 중 발견한 **운영 장애 1건**(웹 컨테이너화 이후 업로드 모델을 워커가 못 찾음)을 먼저 고친다. 배경·설계 근거·운영 중 배포 제약은 [ux-improvements.md](ux-improvements.md)에 있다. 데이터 모델 변경 없음 → 마이그레이션 불필요.
>
> **운영 전제**: Cloudflare Tunnel 주소를 임원진에게 공유해 실사용 중이다. `cloudflared`는 재시작하지 않고(주소가 바뀜), 배포는 대기열이 빈 시점에 `build → up -d web` 순서로 한다.

### 9-0. [P0] 업로드 모델 경로가 워커에서 안 잡히는 문제
- [x] **T048** `app/storage_paths.py` 신규: `to_storage_relative()` / `resolve_storage_path()` — 상대 경로는 `settings.storage_dir` 기준으로, 절대 경로는 존재하면 그대로, 없으면 `storage/` 이후를 잘라 재루팅(컨테이너 `/app/storage/...` ↔ 호스트 경로 흡수)
- [x] **T049** `app/routers/submissions.py`: 업로드 시 `Submission.model_path`를 `storage_dir` 기준 상대 경로로 저장
- [x] **T050** `worker/run.py`: `submission.model_path`를 `resolve_storage_path()`로 해석해 `inject_model`에 전달. 파일이 없으면 원인이 드러나는 오류 메시지로 실패시킨다
- [x] **T051** `tests/test_storage_paths.py` 신규: 상대 경로 / 기존 절대 경로 / 컨테이너 절대 경로(`/app/storage/...`) 세 경우의 해석 규칙 고정

### 9-1. 리더보드 자동 진입
- [x] **T052** `app/routers/leaderboard.py`: `/leaderboard/seasons` 라우트 추가(시즌 목록 렌더) — `/leaderboard/{season_id}`보다 먼저 선언 (FastAPI는 선언 순서로 매칭하므로 뒤에 두면 422)
- [x] **T053** `app/routers/leaderboard.py`: `/leaderboard`를 "진행중 시즌 있으면 303 리다이렉트, 없으면 목록 렌더"로 변경 (진행중 = `status==ACTIVE`, `start_date` 내림차순 첫 건)
- [x] **T054 [P]** `app/templates/leaderboard.html`: "← 다른 시즌 보기" 링크를 `/leaderboard/seasons`로 변경
- [x] **T055 [P]** `app/templates/leaderboard_seasons.html`: 진행중 시즌 행 강조 + "현재 진행중" 배지 표시

### 9-2. 팀 계정 일괄 발급
- [x] **T056** `app/templates/admin/season_detail.html`: 팀 등록 폼을 여러 줄 `textarea`(줄바꿈/쉼표 구분, placeholder 안내)로 교체
- [x] **T057** `app/routers/admin.py`: 팀명 파싱 헬퍼 — 줄바꿈·쉼표 분리, 공백 제거, 빈 줄 제거, 입력 내 중복 제거, 최대 50개 검사
- [x] **T058** `app/routers/admin.py`: `register_team` → 일괄 처리로 변경. 시즌 내 기존 팀명을 미리 조회해 중복은 건너뛰고(DB 예외에 기대지 않음), 팀+계정 생성 후 commit 1회
- [x] **T059** `app/routers/admin.py`: 발급 결과를 리다이렉트 없이 POST 응답에서 바로 렌더(쿼리 파라미터에서 비밀번호 제거 — URL·히스토리에 남지 않게)
- [x] **T060** `app/templates/admin/season_detail.html`: 발급 결과를 표(팀명/아이디/비밀번호)로 표시 + 건너뛴 이름 사유 안내
- [x] **T061** `app/templates/admin/season_detail.html`: CSV 다운로드 버튼(클라이언트 Blob, UTF-8 BOM, 파일명 `{시즌명}_teams.csv`)

### 9-3. 검증 (운영 중이므로 DB를 건드리지 않는 방법으로)
- [x] **T062** `pytest tests/` 전체 통과 — 기존 테스트 회귀 없음 + 신규 경로 테스트 통과
- [x] **T063** 팀명 파싱 규칙 단위 테스트 — 빈 줄·쉼표 혼용·입력 내 중복·50개 초과
- [x] **T064** 배포 후 스모크 체크 — `/healthz`, `/leaderboard` 리다이렉트, `/leaderboard/seasons`, 기존 `/leaderboard/1` 링크, 로그인 화면
- [x] **T065** 실제 평가 1건 성공 확인 — 운영자 승인 후 submission 16을 재큐잉했고, 워커가 파일을 정상적으로 찾아 평가를 시작했다(`평가 시작: submission=16`, 이전의 FileNotFoundError 없음). **경로 수정은 검증 완료.** 평가 자체는 모델 구조 문제로 error로 끝났는데 이는 참가자 제출물 문제이고 플랫폼 이슈가 아니다 (2026-07-26 운영자 확인).
- [ ] ~~verify_phase8.py 실행~~ — 운영 DB에 검증 데이터를 만들고 워커 중지를 요구하므로 **이번 배포에서는 실행하지 않는다**. 대회 종료 후 재실행.

### 9-4. 팀 비밀번호 재발급 (2026-07-26 추가)

> 비밀번호는 bcrypt 해시로만 저장돼 원문 조회가 불가능하다. 분실 시 유일한 해결책인 재발급 경로를 만든다. 설계 근거는 [ux-improvements.md](ux-improvements.md) §2-4. 데이터 모델 변경 없음 → 마이그레이션 불필요.
>
> **제외**: 팀 목록에 아이디 컬럼 추가 — 재발급 결과 표가 아이디를 함께 보여주므로 불필요 (2026-07-26 운영자 판단).

- [x] **T066** `app/routers/admin.py`: `POST /admin/teams/{team_id}/reissue-password` 추가 — 새 비밀번호 생성 후 `Account.password_hash` 갱신, 결과를 기존 발급 표(`issued`)로 재사용해 렌더. 관리자 인증은 라우터 공통 `get_current_admin`으로 강제
- [x] **T067** `app/routers/admin.py`: 계정이 없는 팀(아카이브 시즌)에 대한 요청은 안내 메시지로 거절 — `bulk_error` 재사용
- [x] **T068** `app/templates/admin/season_detail.html`: 팀 행에 "비밀번호 재발급" 버튼 + 확인창(기존 비밀번호가 즉시 무효화됨을 명시). 계정 없는 팀은 버튼 숨김. 결과 카드 제목을 신규 발급 / 재발급으로 구분
- [x] **T069** 검증 — `pytest tests/` 39개 통과 + 격리 DB 스모크 44개 항목 통과: 재발급 후 (a) 새 비밀번호로 로그인 성공 (b) 기존 비밀번호로 로그인 실패 (c) 관리자 세션 없이 호출하면 `/admin/login`으로 차단 (d) 다른 팀 계정은 영향 없음

### 9-4 배포 기록 (2026-07-26)

- 대기열 0건 시점에 `docker compose build web` → `up -d web`. 워커 코드는 바뀌지 않아 워커는 재시작하지 않았고, `cloudflared`도 그대로 두어 공유 주소가 유지됐다.
- 배포 후 터널 주소로 확인: `/healthz` 200, `/leaderboard` → `/leaderboard/1` 303, `/admin/login` 200.
- 권한 확인: 로그인하지 않은 상태로 `POST /admin/teams/1/reissue-password`를 보내면 `/admin/login`으로 303 리다이렉트되고, 해당 팀의 비밀번호 해시가 변하지 않은 것을 DB에서 확인했다.
- 팀 목록 열 순서: 팀 / 제출 수 / 오늘 완료 카운트 / 비밀번호(재발급) / 실격.

### 배포 기록 (2026-07-26)

- 검증: `pytest tests/` 39개 통과. 별도 DB(`drleader_smoke`, 검증 후 삭제)에 앱을 띄워 스모크 32개 항목 통과 — 운영 DB는 읽기만 했다.
- 배포: 대기열 0건인 시점에 `docker compose build web`(무중단) → `docker compose up -d web`(수 초 교체) → 워커 재시작. `cloudflared`는 건드리지 않아 공유된 주소가 유지됐다.
- 배포 후 확인: 터널 주소로 `/healthz` 200, `/leaderboard` → `/leaderboard/1` 303, `/leaderboard/seasons` 200, 기존 `/leaderboard/1` 200.
- 경로 수정 실환경 확인: DB에 `/app/storage/models/1/6/...`로 적힌 submission 16의 실제 파일(246MB)이 호스트 경로로 재루팅되어 열리는 것을 확인했다.
- 주의(운영 메모): 워커를 재시작할 때 `pkill -f "worker.run"`을 쓰면 그 명령을 실행한 쉘 자신도 패턴에 걸려 함께 죽는다. `pgrep -af "[w]orker.run"`으로 PID를 확인해 종료하는 편이 안전하다.

### 9-5. 평가 영상 정상화 & 저장 용량 관리 (2026-07-26 추가)

> 영상이 261바이트였던 원인은 DRFC 환경 문제가 아니라 **워커가 깨진 카메라 앵글(`camera-topview`)을 하드코딩해서 받고 있었기 때문**이었다(`camera-pip`/`camera-45degree`는 13.8MB로 정상). Phase 8의 "미해결" 항목 진단을 정정한다. 함께 저장 용량 정책도 잡는다 — 설계 근거는 [ux-improvements.md](ux-improvements.md) §2-5.

- [x] **T070** `storage/work/` 잔존물 삭제 — 크래시로 남은 압축 해제 작업본 820MB 회수 (평가 미진행 시점에 수행, 2026-07-26 완료)
- [x] **T071** `worker/drfc.py`: 영상 키를 후보 목록(`camera-pip` → `camera-45degree` → `camera-topview`)으로 바꾸고, 오브젝트 크기가 유효한 첫 번째를 내려받도록 변경. 전부 실패하면 지금처럼 영상 없이 결과를 저장한다(순위에는 영향 없음)
- [x] **T072** `app/retention.py` 신규: "팀의 최고기록이 아닌 제출의 모델·영상 파일 삭제" 로직을 공용 함수로 분리. `queued`/`running` 제출은 제외, DB 레코드는 유지(제출 이력·횟수 보존)
- [x] **T073** `worker/run.py`: 평가가 `done`/`error`로 끝난 직후 해당 팀에 대해 T072를 호출 — 시즌 종료까지 기다리지 않고 즉시 정리(175GB → 약 2.6GB 추정)
- [x] **T074** `app/season_archive.py`: T072의 공용 함수를 쓰도록 정리 + **버그 수정** — Phase 9-0에서 `model_path`가 상대 경로가 된 뒤로 `Path(path_str)`가 CWD 기준으로 해석돼 아카이브 시 모델 파일이 조용히 안 지워지고 있었다. `resolve_storage_path()`로 해석
- [x] **T075** 검증 — `pytest tests/` 통과 + 신규 테스트: (a) 최고기록 파일은 남고 나머지는 지워지는지 (b) `queued`/`running` 제출 파일은 보존되는지 (c) DB 레코드는 유지되는지 (d) 영상 앵글 선택이 깨진 후보를 건너뛰는지
- [ ] **T076** (다음 평가 대기 중) 배포 후 실환경 확인 — 다음 평가 1건에서 13.8MB 영상이 `storage/videos/`에 저장되고 리더보드 "영상 보기"로 재생되는지, 그리고 그 팀의 이전 제출 파일이 정리되는지
- [ ] **T077 [보류]** `storage/`를 WSL ext4로 이전 — 대회 종료 후 재검토. 장단점은 [operations.md](../../docs/operations.md) "저장 위치" 절 참고

## Phase 10 — 웹 서버 상시 운영 (클라우드 이관, 2026-07-27 추가)

> 노트북이 꺼져도 리더보드·로그인·제출이 24시간 동작하게 한다. 평가는 GPU/Docker가 필요해 무료 티어로 못 돌리므로 노트북에 남기고 **웹 + DB만** 옮긴다. 설계 근거·호스팅 비교·이관 절차는 [cloud-migration.md](cloud-migration.md).
>
> **선행 조건(운영자 직접)**: 클라우드 계정 + 카드 인증, 도메인 구매, Tailscale 계정. 계정 생성은 대행할 수 없다.
>
> 10-1·10-2는 **계정 준비와 무관하게 지금 할 수 있는 코드 작업**이고, 10-3부터가 실제 이관이다.

### 10-1. 워커 ↔ 웹 파일 전송 경로
- [x] **T078** `app/config.py`: `worker_token`(웹 쪽 검증용), `web_base_url`(워커 쪽 접속용) 설정 추가
- [x] **T079** `app/routers/internal.py` 신규: `GET /internal/submissions/{id}/model` — 모델 아카이브 스트리밍 응답. `X-Worker-Token`을 `secrets.compare_digest`로 검증하고, 실패 시 404(존재 여부도 노출하지 않음)
- [x] **T080** `app/routers/internal.py`: `POST /internal/submissions/{id}/video` — 결과 영상 업로드 후 `storage/videos/`에 저장하고 `EvaluationResult.video_path` 갱신
- [x] **T081** `worker/run.py`: 모델을 로컬 디스크에서 읽는 대신 웹에서 내려받아 임시 디렉터리에서 평가하고, 끝나면 삭제. 영상은 업로드로 전송. **로컬(노트북)에 영구 저장하지 않는다**
- [x] **T082** 워커가 웹과 통신하지 못할 때의 처리 — 재시도 후에도 실패하면 제출을 `queued`로 되돌려 다음 기회에 다시 잡히게 한다(제출을 잃지 않는다)

### 10-2. 평가 서버 상태 표시
- [x] **T083** `worker_heartbeats` 테이블 + Alembic 마이그레이션 (`worker_id` PK, `last_seen_at`)
- [x] **T084** `worker/run.py`: 폴링 루프마다 하트비트 갱신
- [x] **T085** `app/routers/submissions.py`·`leaderboard.py`: 최근 3분 내 하트비트가 없으면 "평가 서버 대기 중(마지막 응답 N분 전), 제출은 접수되며 재개 후 순차 처리" 배너 표시. 예상 대기 시간 문구도 이 상태에 맞게 변경
- [x] **T086** 워커 중지 상태에서도 **제출은 계속 접수**되는지 확인 (참가자가 올려두고 퇴근할 수 있어야 한다)

### 10-1·10-2 배포 기록 (2026-07-27)

- 검증: `pytest tests/` 66개 통과 + 격리 DB 스모크 17개 통과 + 신규 마이그레이션 upgrade/downgrade 왕복 확인.
- 배포: 대기열 0건에 `build web` → `up -d web`(마이그레이션 `c3e7a91b45d2` 적용) → 워커 재시작. 하트비트가 즉시 기록되는 것 확인.
- **현행 배포는 `WORKER_TOKEN`이 비어 있어 local 모드로 동작한다** — 웹·워커가 같은 디스크를 쓰는 지금 구성이 그대로 유지되고, 이관 시점에 토큰을 설정하면 http 모드로 전환된다.
- **운영 사고(무관한 원인)**: 이 배포 중에 Cloudflare quick tunnel 호스트명이 DNS에서 사라져 외부 접속이 끊겼다(`cloudflared` 프로세스는 살아 있었고 연결 재시도만 반복). Cloudflare가 quick tunnel을 회수한 것으로, 코드 배포와는 무관하다. 터널을 재시작해 복구했고 **주소가 바뀌어 재공지가 필요했다.** → Phase 10의 도메인 이전이 필요한 이유가 실제로 증명된 사례.

### 10-3. 서버 준비 및 이관 (계정·도메인 준비 후)
- [ ] **T087** VM 프로비저닝 — Docker 설치, 방화벽 80/443만 개방, Tailscale 연결
- [ ] **T088** `docker-compose.yml`: db 포트를 tailnet 주소에만 바인딩(공개 인터넷 노출 금지), 리버스 프록시(Caddy/nginx) + Let's Encrypt HTTPS 추가
- [ ] **T089** 도메인 A 레코드 연결, `SESSION_HTTPS_ONLY=true`, 새 `SESSION_SECRET`·`WORKER_TOKEN` 발급
- [ ] **T090** 데이터 이전 — `pg_dump` 복원 + `storage/` 전송. **원본은 이관 검증이 끝날 때까지 보존**
- [ ] **T091** 노트북 워커 재구성 — `DATABASE_URL`(tailnet), `WEB_BASE_URL`, `WORKER_TOKEN` 적용 후 재시작

### 10-4. 검증
- [ ] **T092** `pytest tests/` 통과 + 신규 테스트: 워커 토큰 인증(정상/오탐/미제시), 하트비트 만료 판정, 전송 실패 시 `queued` 복구
- [ ] **T093** 이관 후 종단 검증 — 새 도메인으로 리더보드·로그인·제출, 노트북 워커가 모델을 내려받아 평가하고 영상이 리더보드에 재생되는지
- [ ] **T094** 노트북 전원 차단 시나리오 — 웹은 계속 응답하고 제출이 접수되며 "대기 중" 배너가 뜨는지, 노트북 복귀 후 밀린 제출이 순차 처리되는지
- [ ] **T095** 참가자 재공지 + 기존 `cloudflared` 종료. 롤백 절차(노트북 웹 재기동)를 문서대로 한 번 점검

## Phase 11 — 백업 자동화 (2026-07-27 추가)

> 지금 대회 데이터는 이 노트북 한 곳에만 있고 복사본이 없다. 특히 **DB는 Windows 폴더가 아니라 WSL 내부 Docker 볼륨에 있어 C 드라이브를 통째로 백업해도 들어가지 않는다.** 랩타임·순위는 한 번 잃으면 재현이 불가능하므로(모델·비밀번호는 재발급/재업로드가 가능) 최우선으로 보호한다.

### 설계 결정

- **저장 위치**: 기본값 `/mnt/c/Users/jjh03/drleader-backup` (Windows에서는 `C:\Users\jjh03\drleader-backup`). 환경변수 `BACKUP_DIR`로 바꿀 수 있다.
  - OneDrive는 용량이 차서 사용할 수 없다(2026-07-27 확인).
  - **Google Drive 가상 드라이브(G:)에는 직접 쓸 수 없다** — 실제 볼륨이 아니라 Drive 앱이 만든 가상 파일시스템이라 WSL에서 `/mnt/g`로 마운트되지 않는다(확인 완료). 그래서 **스크립트는 평범한 로컬 폴더에 쓰고, Google Drive 데스크톱의 "내 컴퓨터 폴더 백업" 기능으로 그 폴더를 Drive에 올린다.** 방향을 뒤집는 것이 가상 드라이브 문제를 피하는 가장 단순한 방법이다.
  - 대안으로 `rclone`을 써서 WSL에서 Drive API로 직접 올릴 수도 있지만, OAuth 인증과 토큰 관리가 추가되어 인수인계 난이도가 올라간다. 채택하지 않는다.
- **모델 파일은 기본 제외**. 건당 250MB(현재 2.5GB)라 매일 담으면 C 드라이브(여유 30GB)와 OneDrive 용량이 금방 찬다. 모델은 참가자 본인이 원본을 갖고 있어 재업로드가 가능하고, 보존 정책상 최고기록 것만 남는다. `BACKUP_INCLUDE_MODELS=true`로 켤 수 있게 한다.
- **담는 것**: DB 덤프(gzip) + `storage/videos`·`metrics`·`eval_logs`. 영상은 재생성이 불가능하고(평가 때만 만들어짐) 용량도 팀당 14MB 수준이라 포함한다.
- **주기·보관**: 하루 1회, 최근 14벌 유지 후 오래된 것부터 삭제. 최악의 경우 하루치 제출만 다시 받으면 된다.
- **스케줄러**: cron이 아니라 **systemd 타이머**(이 WSL은 systemd가 돌고 있다). `Persistent=true`로 두어 노트북이 꺼져 있어 걸렀던 실행을 다음 부팅 때 따라잡게 한다 — 매일 켜져 있지 않은 노트북이라 이 옵션이 핵심이다.
- **검증까지 포함**: 백업은 복원이 되어야 백업이다. 덤프가 비어 있지 않은지, gzip이 온전한지 매번 확인하고 결과를 로그에 남긴다.

### 작업

- [x] **T096** `scripts/backup.sh` 신규 — `pg_dump` → gzip, `storage/` 선택 압축(모델 제외 기본), 무결성 검사(`gunzip -t` + 크기 0 확인), 보관 기수 초과분 삭제, 실행 로그 기록
- [x] **T097** 설정값 정리 — `BACKUP_DIR`, `BACKUP_KEEP`, `BACKUP_INCLUDE_MODELS`를 환경변수로 받고 기본값을 스크립트 헤더와 [operations.md](../../docs/operations.md)에 문서화. **`.env.example`에는 넣지 않는다** — `.env`는 파이썬 앱(pydantic-settings)이 읽는 파일이고 셸 스크립트는 읽지 않으므로, 거기 적으면 '설정했는데 반영이 안 되는' 오해를 부른다. 값을 고정하려면 systemd 유닛의 `Environment=` 줄을 쓴다
- [x] **T098** systemd 유닛 2개(`drleader-backup.service` / `.timer`) 작성 + 문법 검증 + 설치 절차 문서화. `Persistent=true`, 매일 04:00. **설치 명령 실행은 sudo 비밀번호가 필요해 운영자가 직접 해야 한다**(미완료 상태)
- [x] **T099** 복원 절차 문서화 — [operations.md](../../docs/operations.md) "백업 대상" 절과 [handover.md](../../docs/handover.md) §6을 자동 백업 기준으로 갱신. **DB와 storage를 반드시 함께 복원**해야 한다는 점 명시
- [x] **T100** 검증 — 실제로 1회 백업 실행 → 무결성 검사 통과, **격리 DB(`drleader_restore_test`)에 실제로 복원해 시즌·팀·제출 건수가 일치**하는지 확인, 보관 기수 초과분이 지워지는지 확인, 타이머가 등록·예약되는지 확인

---

## 범위 밖 (이번 STEP3에 포함하지 않음)

- 002(오프라인 비전 타이머) 관련 작업 일체 — 온라인 서비스 구현 완료 후 별도로 STEP3 진행.
- plan.md §8의 미결 사항(예선 통과 N팀 산정 규칙)에 대한 구현 — 확정되면 태스크를 추가한다.
