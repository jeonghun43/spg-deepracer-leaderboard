코드 전체(파이썬 47개 파일 ~3,000줄 + 인프라/템플릿/스크립트)를 읽고 정리했습니다.

## 표 읽는 법 — 특히 "출처" 열에 대해

먼저 한계를 밝힙니다. **커밋이 9개로 뭉쳐 있고**(`first commit`, `ver1`, `ver2`, `ver3`…) 모두 `Jeong Hun` 단독 저자이며 `Co-authored-by` 트레일러가 하나도 없어서, **라인별 작성 주체를 추적할 방법이 없습니다.** 따라서 "출처" 열은:

- **일반 원칙** — 프레임워크 공식 문서나 교과서가 그 형태를 그대로 제시하는 경우에만 부여
- **구분 불가** — 그 외 전부 (대부분)

"Claude 습관"으로 단정한 항목은 **하나도 없습니다.** 근거가 없기 때문입니다. 다만 표 뒤에 *"LLM 생성 코드에서 흔한 특징과 일치하지만 확증 불가"* 인 것들을 따로 모아뒀습니다.

---

## A. 설정 · 부트스트랩

| 파일:라인 | 무엇을 하는가 | 개념 | 카테고리 | 확신도 | 출처 |
|---|---|---|---|---|---|
| [app/config.py:12-96](app/config.py:12) | `.env` → 타입 있는 설정 객체 | Externalized Configuration (12-factor Config) | ARCH | **확실** — `env_file=".env"`(:13), 전 항목 타입+기본값, 단일 인스턴스 `settings = Settings()`(:96) | 일반 원칙 (pydantic-settings 표준) |
| [app/config.py:96](app/config.py:96) | 모듈 전역 설정 인스턴스 | Singleton | PATTERN | **유사** — 모듈 캐시에 의존하는 관용적 싱글턴. GoF 정석의 *인스턴스화 통제*(private ctor / getInstance)가 없어 `Settings()`를 누구든 또 만들 수 있다 | 일반 원칙 |
| [app/config.py:62-74](app/config.py:62) | `admin_login_path` 정규화 (빈 값→기본값, 슬래시 보정) | Validation at the boundary | PATTERN | **확실** — `@field_validator` + 정규화 후 반환 | 일반 원칙 |
| [app/config.py:76-93](app/config.py:76) | `models_dir` 등 파생 경로 | Computed property | LANG | **확실** — `@property` 4개, 저장 상태 없음 | 일반 원칙 |
| [app/main.py:10-35](app/main.py:10) | 앱·미들웨어·라우터 조립 | Composition Root | ARCH | **유사** — 조립 지점이 한 곳인 건 맞지만 **팩토리 함수가 아니라 import 시점 모듈 전역**(:10)이다. 설정을 바꿔 재조립하는 테스트가 불가능 | 구분 불가 |
| [app/main.py:12-14](app/main.py:12) | `docs_url`/`redoc_url`/`openapi_url` 모두 None | Attack surface reduction | ARCH | **확실** — 세 개를 빠짐없이 끈 것이 근거 | 구분 불가 |
| [app/main.py:38-40](app/main.py:38) | `/healthz` | Health check endpoint | INFRA | **확실** | 일반 원칙 |

## B. 데이터 모델 · 영속성

| 파일:라인 | 무엇을 하는가 | 개념 | 카테고리 | 확신도 | 출처 |
|---|---|---|---|---|---|
| [app/models.py:54-194](app/models.py:54) | 6개 테이블 선언적 매핑 | Data Mapper (ORM) | ARCH | **확실** — `Mapped[]`/`mapped_column`, 도메인 객체가 SQL을 모름 | 일반 원칙 |
| [app/db.py:8-21](app/db.py:8) | 엔진·세션팩토리·요청 스코프 세션 | Unit of Work + Resource-scoped DI | ARCH | **확실** — `autoflush=False, autocommit=False`(:9), `try/finally: db.close()`(:16-21) | 일반 원칙 (FastAPI 공식 예제 형태) |
| [app/models.py:126-134](app/models.py:126) | `status IN ('queued','running')` 부분 유니크 인덱스 | Database-enforced invariant | DOMAIN | **확실** — `postgresql_where=text(...)`(:132) | 일반 원칙 |
| [app/models.py:23-26](app/models.py:23) | Enum을 `.name`이 아닌 `.value`로 저장 | 영속화 매핑 오버라이드 | LANG | **유사** — 이름 붙일 만한 "패턴"이라기보다 프레임워크 설정 관용구. `partial(SAEnum, values_callable=…)`로 전역 적용한 것이 특이점 | 구분 불가 |
| [migrations/versions/](migrations/versions) 4개 | 선형 리비전 체인 | Versioned schema migration | INFRA | **확실** — `685df→a1c4f→c3e7a→d4f1` `down_revision` 연결 | 일반 원칙 |
| [migrations/versions/a1c4f2b8d907_daily_count_adjustment.py:20-32](migrations/versions/a1c4f2b8d907_daily_count_adjustment.py:20) | 컬럼 rename + 기존 값 무효화 | Data migration / backfill | INFRA | **확실** — `op.execute("UPDATE teams SET … NULL")`(:26). `downgrade`도 대칭 | 일반 원칙 |
| [migrations/env.py:31-40](migrations/env.py:31) | online/offline 마이그레이션 러너 | Migration runner | INFRA | **확실** — Alembic 기본 템플릿 그대로 | 일반 원칙 |

## C. 인증 · 인가 · 방어

| 파일:라인 | 무엇을 하는가 | 개념 | 카테고리 | 확신도 | 출처 |
|---|---|---|---|---|---|
| [app/security.py:9-21](app/security.py:9) | bcrypt 해시 + `secrets` 난수 비밀번호 | Adaptive password hashing / CSPRNG | DOMAIN | **확실** — `bcrypt.gensalt()`(:14), `secrets.choice`(:10) | 일반 원칙 |
| [app/deps.py:8-42](app/deps.py:8) | 세션에서 주체 복원 후 주입 | Dependency Injection + Auth guard | ARCH | **확실** — `Depends(get_current_admin)` 사용처 [admin.py:119](app/routers/admin.py:119) 외 다수 | 일반 원칙 |
| [app/deps.py:26-42](app/deps.py:26) | 미인증 관리자에게 **404** | Uniform error response (존재 오라클 제거) | ARCH | **확실** — 리다이렉트 대신 404를 주는 이유가 docstring(:26-33)에 명시 | 구분 불가 |
| [app/admin_lockout.py:98-123](app/admin_lockout.py:98) | 실패 누적 → 잠금 | Account lockout / Throttling | ARCH | **유사** — 고정 윈도우·토큰 버킷 같은 정석 rate limiter가 아니다. ① 카운터가 **성공 없이 시간만으로 감소하지 않고** `_prune`(:64-74)의 전량 삭제로만 사라진다 ② 저장소가 프로세스 메모리라 **다중 워커에서 성립하지 않음**(전제는 :17-20에 문서화) | 구분 불가 |
| [app/admin_lockout.py:77-84](app/admin_lockout.py:77) | `{scope}:ip:` + `{scope}:id:` 두 키 | Composite throttling key | ARCH | **확실** — 어느 하나라도 잠기면 잠김(:87-95) | 구분 불가 |
| [app/routers/auth.py:35-43](app/routers/auth.py:35), [admin.py:79-87](app/routers/admin.py:79) | 잠긴 동안 bcrypt를 **아예 실행 안 함** | Fail-fast before expensive work (DoS 완화) | ARCH | **확실** — 잠금 검사가 `verify_password` 호출(:46)보다 위 | 구분 불가 |
| [app/routers/internal.py:31-37](app/routers/internal.py:31) | 워커 토큰 검증 | Shared-secret auth + Constant-time compare | ARCH | **확실** — `secrets.compare_digest`(:36), 실패 시 403이 아닌 404(:26) | 일반 원칙 |
| [app/routers/admin.py:385-390](app/routers/admin.py:385) | 설정값 경로에 로그인 라우트 등록 | Dynamic route registration | LANG | **확실** — 데코레이터로 불가능해 `add_api_route`, `include_in_schema=False` | 구분 불가 |

## D. 도메인 로직

| 파일:라인 | 무엇을 하는가 | 개념 | 카테고리 | 확신도 | 출처 |
|---|---|---|---|---|---|
| [app/records.py:9-31](app/records.py:9) | 팀 최고기록 선정 (랩타임→제출시각 타이브레이크) | Domain service (순수 함수) / Single source of truth | DOMAIN | **확실** — [leaderboard.py:43](app/routers/leaderboard.py:43)·[retention.py:59](app/retention.py:59) 두 곳이 공유 | 구분 불가 |
| [app/quota.py:27-49](app/quota.py:27) | KST 자정 기준 하루 한도 (제출 시각 기준) | Quota policy | DOMAIN | **확실** — 하루 경계 계산(:30-31) + DONE만 카운트(:34) + 제출 시각으로 날짜 결정(:35-36) | 구분 불가 |
| [app/quota.py:42-43](app/quota.py:42) + [models.py:84-88](app/models.py:84) | 카운트 조정을 절대값이 아닌 **델타**로 | Delta over absolute override | DOMAIN | **확실** — 마이그레이션 [a1c4f2b8d907](migrations/versions/a1c4f2b8d907_daily_count_adjustment.py:1)이 이 전환을 기록 | 구분 불가 |
| [app/retention.py:32-71](app/retention.py:32) | 최고기록 외 파일 삭제 (DB 레코드는 보존) | Retention policy / rule-based GC | DOMAIN | **확실** — 활성 제출 제외(:65), DB 유지 명시(:7) | 구분 불가 |
| [app/models.py:32-36](app/models.py:32) + [admin.py:32-36](app/routers/admin.py:32) | `preparing→active→closed→archived` | State machine (선형 전이표) | DOMAIN | **유사** — 전이표는 있으나 **전이 가드가 없다**. `advance_status`(:202-217)는 요청만 오면 전진시키고, 역전이 금지는 표에 없어서 성립할 뿐 검증되지 않는다 | 구분 불가 |
| [app/worker_status.py:16-41](app/worker_status.py:16) | 마지막 하트비트 나이로 생사 판정 | Heartbeat / Liveness with stale threshold | ARCH | **확실** — `elapsed <= timedelta(minutes=…)`(:29) | 일반 원칙 |
| [app/storage_paths.py:60-74](app/storage_paths.py:60) | 컨테이너/호스트 경로 차이 흡수 | Path canonicalization (환경 간 변환 계층) | ARCH | **유사** — "Anti-corruption layer"라 부르기엔 모델 변환이 아니라 경로 문자열 재루팅 한 가지만 한다. 3단계 폴백(:63-65)은 명확 | 구분 불가 |
| [app/storage_paths.py:23-25](app/storage_paths.py:23) | `..` 세그먼트 거부 | Path traversal 방어 | ARCH | **확실** — `if ".." in parts: raise` | 일반 원칙 |

## E. HTTP 표현 계층

| 파일:라인 | 무엇을 하는가 | 개념 | 카테고리 | 확신도 | 출처 |
|---|---|---|---|---|---|
| [app/routers/](app/routers) 5개 | 기능별 `APIRouter` 분할 | Modular router / vertical slice | ARCH | **확실** — `main.py:29-35`에서 조립 | 일반 원칙 |
| [app/render.py:10-25](app/render.py:10) | UTC 저장 → 표시 직전 KST 변환 | Store UTC, render local | PATTERN | **확실** — `value.astimezone(KST)`(:25). 컨테이너 `TZ`를 쓰지 않는 이유까지 :18-21에 기록 | 일반 원칙 |
| [app/render.py:29-58](app/render.py:29) | 실패 사유 코드 → 한국어 | Lookup table + 원문 폴백 | PATTERN | **확실** — `FAILURE_REASON_LABELS.get(reason, reason)`(:57) | 구분 불가 |
| [app/routers/submissions.py:22-34](app/routers/submissions.py:22) | Accept 헤더로 JSON/리다이렉트 분기 | Content negotiation | PATTERN | **유사** — 정석은 q-value 협상 + 406 가능이어야 하는데, 여기선 `application/json` **정확 일치만** 보고 나머지는 전부 HTML로 떨어뜨린다(:31-34). 의도적 단순화이며 이유가 :26-27에 있음 | 구분 불가 |
| [app/routers/submissions.py:132-151](app/routers/submissions.py:132) | 1MB 청크 스트리밍 + 상한 초과 시 중단·삭제 | Streaming upload with bounded size | ARCH | **확실** — `while chunk := await …read(1MB)`(:135), 초과 시 unlink(:139) | 일반 원칙 |
| [app/routers/submissions.py:161-174](app/routers/submissions.py:161) | 선검사 통과해도 DB 제약이 최종 판정 | Optimistic check + DB constraint (TOCTOU 방어) | ARCH | **확실** — `except IntegrityError`(:163), 주석이 TOCTOU를 명시(:164-165), 파일까지 정리(:171) | 구분 불가 |
| [app/static/upload.js:12-16](app/static/upload.js:12), [131-140](app/static/upload.js:131) | JS 없으면 평범한 form POST | Progressive enhancement | ARCH | **확실** — `if (!form \|\| !window.XMLHttpRequest \|\| !window.FormData) return;`(:16) + 의도 명시(:8-10) | 일반 원칙 |
| [app/templates/base.html](app/templates/base.html) + `{% extends %}` | 레이아웃 상속 | Template inheritance | LANG | **확실** | 일반 원칙 |
| [app/templates/_worker_status.html](app/templates/_worker_status.html) | 두 화면이 공유하는 배너 | Partial / include | PATTERN | **확실** — `_` 접두사 관례 | 일반 원칙 |

## F. 워커 (큐 / 배치)

| 파일:라인 | 무엇을 하는가 | 개념 | 카테고리 | 확신도 | 출처 |
|---|---|---|---|---|---|
| [worker/run.py:43-56](worker/run.py:43), [69-78](worker/run.py:69) | `FOR UPDATE SKIP LOCKED`로 원자적 작업 선점 | DB-backed work queue / Competing consumers | ARCH | **확실** — 한 문장 `UPDATE … WHERE id = (SELECT … SKIP LOCKED) RETURNING id`(:45-54) | 일반 원칙 (Postgres 큐 정석) |
| [worker/run.py:302-312](worker/run.py:302) | 5초 폴링 루프 | Polling consumer | ARCH | **확실** | 일반 원칙 |
| [worker/run.py:81-114](worker/run.py:81) | 죽은 워커가 남긴 `running` 회수 | Crash recovery / lease expiry | ARCH | **유사** — 정석 리스는 처리 중 **주기적 갱신**되는데 여기선 `started_at`을 한 번만 찍고 `MAX_WAIT+300` 고정 임계(:95)를 쓴다. 하트비트(:140-163)는 워커 단위라 개별 작업 리스와 연결되지 않음. 자기 ID는 시간 무관 즉시 회수(:99)로 보완 | 구분 불가 |
| [worker/run.py:254-281](worker/run.py:254) | 전송 실패=재큐 / 평가 실패=error / 미상=error | Error classification (retry vs terminal) | ARCH | **확실** — 예외 타입별 3분기, 각각의 이유가 주석에 있음(:255-256) | 구분 불가 |
| [worker/run.py:140-163](worker/run.py:140) | 데몬 스레드에서 30초마다 하트비트 | Background heartbeat thread | ARCH | **확실** — `daemon=True`(:161)로 워커 사망 시 함께 죽는 것까지 설계(:146-147) | 구분 불가 |
| [worker/transfer.py:33-34](worker/transfer.py:33), [41-74](worker/transfer.py:41) | 토큰 유무로 local/http 모드 전환 | Strategy | PATTERN | **유사** — 전략 **객체**나 다형성이 아니라 각 함수 내부의 `if not uses_http():` 분기(:47, :86, :124)다. 세 번째 모드를 넣으려면 네 함수를 전부 고쳐야 해서 OCP를 만족하지 않는다 | 구분 불가 |
| [worker/drfc.py:1-17](worker/drfc.py:1), [74-89](worker/drfc.py:74) | DRFC/MinIO 호출을 한 모듈에 격리 | Adapter / Gateway | ARCH | **확실** — S3 레이아웃 가정을 docstring에 전부 명시(:3-16) | 일반 원칙 |
| [worker/drfc.py:37-43](worker/drfc.py:37), [254-268](worker/drfc.py:254) | 카메라 앵글 3개를 우선순위대로, 크기로 검증 | Fallback chain + sanity check | PATTERN | **확실** — 실패 사례(261바이트 껍데기)까지 주석에 기록 | 구분 불가 |
| [worker/drfc.py:316-337](worker/drfc.py:316) | metrics 우선, 비면 로그 파싱 | Graceful degradation / secondary source | PATTERN | **확실** — `if percentages: … / if log_path is not None:` | 구분 불가 |
| [worker/drfc.py:160-183](worker/drfc.py:160), [204-205](worker/drfc.py:204) | 기존 모델 삭제 **전에** 체크포인트 검증 | Precondition validation before destructive op | ARCH | **확실** — 순서의 이유가 :165·:204에 명시 | 구분 불가 |
| [worker/run_evaluation.sh:51-64](worker/run_evaluation.sh:51), [112-123](worker/run_evaluation.sh:112) | `desired-state=running` 태스크 수로 완료 판정 | Polling for completion with timeout | INFRA | **확실** — 이력 태스크 때문에 생겼던 버그가 :46-50에 기록 | 구분 불가 |
| [worker/run_worker.sh:161-173](worker/run_worker.sh:161) | 필수 `DR_*` 없으면 즉시 종료 | Fail-fast config validation | INFRA | **확실** — 늦게 터졌던 사고(2026-07-25)가 :157-160에 기록 | 구분 불가 |

## G. 인프라 · 운영

| 파일:라인 | 무엇을 하는가 | 개념 | 카테고리 | 확신도 | 출처 |
|---|---|---|---|---|---|
| [Dockerfile:5-8](Dockerfile:5) | requirements 먼저 COPY 후 install, 코드는 나중 | Layer cache 최적화 | INFRA | **확실** | 일반 원칙 |
| [Dockerfile:16](Dockerfile:16) | `alembic upgrade head && uvicorn` | Migrate-on-boot | INFRA | **유사** — 12-factor의 release는 **별도 단계**인데 여기선 매 기동마다 실행된다. 인스턴스가 1개라 성립할 뿐, 스케일아웃하면 동시 마이그레이션 경합 | 구분 불가 |
| [docker-compose.prod.yml:15](docker-compose.prod.yml:15), [36](docker-compose.prod.yml:36), [44](docker-compose.prod.yml:44) | `${VAR:?메시지}`로 필수 값 강제 | Fail-fast on missing config | INFRA | **확실** — "조용히 기본값으로 넘어가느니 기동 실패가 낫다"는 근거가 :39-41 | 구분 불가 |
| [docker-compose.prod.yml:24](docker-compose.prod.yml:24) | `${DB_BIND_ADDRESS:-127.0.0.1}:5432:5432` | Secure default (fail-closed) | INFRA | **확실** — 값이 없으면 루프백으로 떨어짐 | 구분 불가 |
| [docker-compose.prod.yml:25-29](docker-compose.prod.yml:25), [50-52](docker-compose.prod.yml:50) | healthcheck + `condition: service_healthy` | Readiness gate | INFRA | **확실** | 일반 원칙 |
| [Caddyfile:4-20](Caddyfile:4) | 리버스 프록시 + 자동 TLS | TLS termination at edge | INFRA | **확실** — 업로드용 30분 타임아웃(:16-19)까지 조정 | 일반 원칙 |
| [scripts/backup.sh:98-124](scripts/backup.sh:98) | `.tmp`에 쓰고 → 4단계 검증 → `mv` | Verified backup + atomic publish | INFRA | **확실** — `PIPESTATUS`로 pg_dump 실패 포착(:99), 헤더·꼬리·필수 테이블 검사(:112-122), 성공해야 `mv`(:124) | 구분 불가 |
| [scripts/backup.sh:148-160](scripts/backup.sh:148) | 최신 N벌만 보관 | Rotation / retention | INFRA | **확실** | 일반 원칙 |
| [scripts/systemd/drleader-backup.timer:31](scripts/systemd/drleader-backup.timer:31), [:34](scripts/systemd/drleader-backup.timer:34) | `Persistent=true` + `RandomizedDelaySec` | Catch-up scheduling + jitter | INFRA | **확실** — 노트북이 꺼져 있던 날을 따라잡는 이유가 :29-30 | 일반 원칙 |
| [tests/test_leaderboard_ranking.py:16-27](tests/test_leaderboard_ranking.py:16) | `SimpleNamespace`로 DB 없이 순수 로직 검증 | Test double (Stub) | PATTERN | **확실** — 15개 테스트가 같은 방식 | 구분 불가 |

---

### "일반 원칙"과 "생성 습관"을 가르지 못한 것들에 대해

솔직히 적자면, 이 저장소에서 가장 특징적인 것은 **주석·docstring이 "결론"이 아니라 "실제로 겪은 사고와 날짜"를 적는다**는 점입니다 (`2026-07-26: camera-topview가 261바이트로만 생성`, `2026-07-30 submission 18`, `2026-08-18 발견`). 이건 [CLAUDE.md §5](CLAUDE.md)가 명시적으로 요구하는 프로젝트 규약이므로 **작성 주체와 무관한 하우스 스타일**이고, 생성 습관의 근거가 될 수 없습니다.

LLM 생성 코드에서 흔한 특징과 *형태가 일치하지만* 확증할 수 없는 것 세 가지만 적어둡니다 — 어느 쪽인지 저는 모릅니다:

- **항상 존재하는 속성에 `getattr(obj, "x", None)`** — [render.py:51,55](app/render.py:51), [leaderboard.py:26](app/routers/leaderboard.py:26). 컬럼은 모델에 정의돼 있어([models.py:188-189](app/models.py:188)) NULL일 뿐 속성은 항상 있습니다. 과잉 방어이거나, 마이그레이션 전 코드의 잔재이거나 둘 중 하나인데 구분 불가.
- **`# noqa`로 무마한 타입 힌트** — [records.py:9](app/records.py:9)의 `-> tuple[Submission | None, "EvaluationResult | None"]  # noqa: F821`. import하면 되는데 문자열+noqa로 처리.
- **거의 모든 함수에 docstring** — 규약 때문일 수도, 습관일 수도. 구분 불가.

---

# 품질 문제

패턴 이름과 별개로, 구현이 문제인 지점들입니다. 심각도 순.

### 1. 참가자 압축 파일을 검증 없이 전개 — 경로 탈출 가능 · [worker/drfc.py:92-101](worker/drfc.py:92)
```python
zf.extractall(dest_dir)        # :96
tf.extractall(dest_dir)        # :99
```
둘 다 `filter=`도 멤버 경로 검사도 없습니다. 아카이브 안에 `../../..` 또는 절대 경로 멤버가 있으면 `work_dir` 밖에 파일을 씁니다(CVE-2007-4559). 입력은 **외부인(참가자)이 업로드한 파일**이고, 워커는 컨테이너가 아니라 **호스트 사용자 권한**으로 돕니다([run_worker.sh:185](worker/run_worker.sh:185)). 이 저장소에서 유일하게 "즉시 고쳐야 한다"고 말할 항목입니다 — Python 3.12+라면 `tf.extractall(dest_dir, filter="data")`, zip은 멤버별 `os.path.realpath` 검사.

### 2. nullable 랩타임을 None 검사 없이 비교 → 리더보드 전체 500 · [records.py:20-26](app/records.py:20), [leaderboard.py:73](app/routers/leaderboard.py:73)
`lap_time_seconds`는 nullable인데([models.py:184](app/models.py:184)) `finish_status == FINISHED`만 확인하고 `<`로 비교하고 `sort` 합니다. "FINISHED면 랩타임이 있다"를 보장하는 것은 [drfc.py:350-352](worker/drfc.py:350)의 코드 경로뿐이고 **DB 제약이 아닙니다.** 수기 수정·과거 데이터·재파싱으로 그런 행이 하나만 생기면 `TypeError`로 리더보드 페이지 전체가 죽습니다. 대회 중이면 가장 눈에 띄는 실패 방식입니다.

### 3. MinIO 기존 모델 삭제에 페이지네이션 없음 · [worker/drfc.py:208-210](worker/drfc.py:208)
```python
existing = s3_client.list_objects_v2(Bucket=bucket, Prefix=model_key_prefix)
```
최대 1000개만 반환됩니다. 같은 파일 [:225-227](worker/drfc.py:225)에서는 `get_paginator`를 제대로 쓰고 있어 **일관성도 없습니다.** 1000개를 넘으면 이전 참가자 모델 파편이 남은 채 다음 평가가 돌아, 원인 찾기 어려운 오염이 됩니다.

### 4. 전역 dict를 락 없이 read-modify-write · [admin_lockout.py:42](app/admin_lockout.py:42), [116-122](app/admin_lockout.py:116)
FastAPI는 `async def`가 아닌 핸들러를 **스레드풀에서** 실행합니다. `login_submit`([auth.py:23](app/routers/auth.py:23))·`admin_login_submit`([admin.py:65](app/routers/admin.py:65)) 모두 `def`이므로 동시 요청이 같은 `_Entry`를 함께 갱신할 수 있고, `entry.failures += 1`은 원자적이지 않습니다. 동시 실패가 과소 집계되어 잠금이 늦게 걸립니다. 모듈 docstring(:17-20)은 "프로세스 1개·uvicorn 워커 1개"만 논하고 **스레드 문제는 다루지 않습니다** — 전제가 한 겹 빠져 있습니다.

### 5. 예외 처리 안에서 None 검사 없이 재조회 · [worker/run.py:258](worker/run.py:258), [268](worker/run.py:268), [276](worker/run.py:276)
```python
db.rollback()
submission = db.get(Submission, submission_id)
submission.status = SubmissionStatus.ERROR   # submission이 None이면 AttributeError
```
세 곳 모두 같은 형태입니다. None이면 `AttributeError`가 **원래 예외를 덮어써서** 제출이 `running`에 갇히고 진짜 원인은 로그에 남지 않습니다. 정확히 이 상태를 청소하려고 만든 것이 `recover_stale_running`인데, 재시작 전까지는 안 돕습니다.

### 6. 재큐에 시도 횟수 상한이 없다 — head-of-line blocking · [worker/run.py:254-265](worker/run.py:254)
웹 서버가 오래 죽어 있으면 같은 제출을 30초마다 무한히 다시 집습니다. 워커가 순차 처리([:312](worker/run.py:312))라 그동안 **대기열 뒤쪽은 한 건도 진행되지 않습니다.** 실패 횟수를 세서 일정 횟수 후 뒤로 미루거나 error로 종결하는 출구가 필요합니다.

### 7. ~~에러 메시지를 URL 인코딩 없이 쿼리스트링에 삽입~~ → **해결** (2026-10-01) · [submissions.py:123](app/routers/submissions.py:123)
```python
return RedirectResponse(f"/submit?error={message}", status_code=303)
```
현재 메시지들에는 `&`·`#`가 없어 **지금은 터지지 않습니다**(잠재적 결함). 다만 [:118](app/routers/submissions.py:118)처럼 설정값을 끼워 넣는 문구가 있어, 문구를 한 번 고치면 조용히 깨집니다. `urllib.parse.quote` 한 줄이면 됩니다. XSS는 Jinja 자동 이스케이프([submit.html:7](app/templates/submit.html:7))로 막혀 있습니다.

**해결 경위**: 업로드 일시 중지 기능이 들어오면서 관리자가 직접 쓴 공지 문구가 이 경로로 가게 됐습니다. "잠재적"이던 결함이 실제로 터질 수 있게 된 것입니다. 그래서 `quote(message, safe='')`로 인코딩하도록 고쳤습니다. 회귀 테스트는 `tests/test_upload_pause.py::test_paused_message_survives_the_redirect_query`입니다.

### 8. N+1 쿼리 — 규모 전제에만 의존 · [admin.py:175](app/routers/admin.py:175), [leaderboard.py:42-47](app/routers/leaderboard.py:42)
`{team.id: get_daily_done_count(db, team) for team in season.teams}`는 팀당 COUNT 1회, `build_leaderboard`는 팀당 `team.submissions` 지연 로딩입니다. [records.py:3](app/records.py:3)이 "시즌당 약 10팀"을 근거로 캐시 없음을 정당화하는데, **그 상한은 코드 어디에도 강제돼 있지 않습니다**([MAX_BULK_TEAMS=50](app/routers/admin.py:39)은 1회 등록 상한일 뿐 누적 상한이 아님). 지금 고칠 필요는 없지만, 전제가 깨지는 지점이 문서에만 있고 코드에 없다는 것이 문제입니다.

### 9. `.gitignore`의 `CLAUDE.md`가 아무 일도 하지 않는다 · [.gitignore:16](.gitignore:16)
`git ls-files`에 `CLAUDE.md`가 **이미 있습니다.** 추적 중인 파일은 gitignore가 무시하므로 이 줄은 무효인데, 게다가 "로컬 전용 설정 — 내 PC의 경로·권한 허용 목록이라 공유하지 않는다"라는 주석 **바로 아래**에 주석 없이 붙어 있어, 파일만 읽으면 CLAUDE.md가 공유되지 않는다고 오해합니다. 추적 해제가 의도면 `git rm --cached CLAUDE.md`가, 아니면 이 줄 삭제가 필요합니다.

### 10. 개발용 compose의 세션 시크릿 기본값 · [docker-compose.yml:25](docker-compose.yml:25)
`SESSION_SECRET: ${SESSION_SECRET:-change-me-in-production}`. prod compose는 `:?`로 강제하지만([prod:36](docker-compose.prod.yml:36)) 개발용에는 방어가 없습니다. CLAUDE.md가 "두 서버의 배포 방식이 다르다"고 경고하는 바로 그 지점이라, 잘못된 compose 파일을 서버에서 쓰면 세션 위조가 가능한 상태로 뜹니다.

### 11. import 부수효과로 전역 Jinja 환경 변경 · [render.py:61-62](app/render.py:61)
`templates`를 import하는 것만으로 필터가 등록됩니다. 동작은 하지만 등록 시점이 import 순서에 묶여 있어, 필터를 추가하다 순환 import가 생기면 원인 찾기 어려운 `TemplateAssertionError`로 나타납니다.

### 12. 상태 비교 방식 불일치 · [retention.py:65](app/retention.py:65) vs [records.py:14](app/records.py:14), [leaderboard.py:24](app/routers/leaderboard.py:24)
retention만 `submission.status.value in ACTIVE_SUBMISSION_STATUSES`(문자열)이고 나머지는 enum 직접 비교입니다. 지금은 둘 다 맞지만, 한쪽만 리팩터링하면 조용히 어긋납니다.

### 13. 인증 실패를 예외로 리다이렉트 흉내 · [deps.py:8-16](app/deps.py:8)
`HTTPException(303, headers={"Location": "/login"})`. 브라우저는 따라가지만 응답 본문은 JSON이고, 예외 핸들러를 커스터마이즈하는 순간 조용히 깨집니다. 같은 파일의 관리자 쪽(404)은 예외 사용이 자연스러운데 팀 쪽만 어색합니다.

### 14. `import`가 코드 사이에 있음 · [models.py:26-27](app/models.py:26)
`Enum = partial(...)` 다음 줄에 `from sqlalchemy.orm import Mapped, ...` (PEP8 E402). `Enum`이라는 이름이 상단의 `import enum`·`SAEnum`과 겹쳐 읽기도 혼란스럽습니다.

### 15. 수동 스모크 스크립트가 테스트 디렉터리에 섞여 있음 · [tests/verify_phase8.py](tests/verify_phase8.py)
파일명이 `test_`로 시작하지 않아 **pytest가 수집하지 않고**, 실행 중인 서버와 "워커가 꺼져 있을 것"을 요구합니다(:9-10). 자동 테스트 15개와 같은 디렉터리에 있어 "테스트 다 통과"가 무엇을 포함하는지 흐려집니다. `scripts/` 이동이나 `@pytest.mark.manual` 중 하나가 낫습니다.

---

**전체 인상**: 개념 태깅 관점에서 이 저장소는 이례적으로 깔끔합니다 — 특히 `SKIP LOCKED` 큐, TOCTOU 방어, 검증 후 원자적 발행 백업, progressive enhancement는 교과서적으로 정확하고 **왜 그렇게 했는지가 코드 옆에 남아 있습니다**. 문제는 개념 선택이 아니라 **경계 조건**에 몰려 있습니다: nullable, 페이지네이션 한계, 스레드, 재시도 상한, 그리고 외부 입력(압축 파일). 1번과 2번만 먼저 처리하시길 권합니다.