# 코드 학습 가이드 (다른 AI와 함께 공부하기 위한 온보딩 문서)

> 이 프로젝트 코드를 top-down으로 파헤치기 위한 자료다. 리포지토리를 읽을 수 없는 AI
> (ChatGPT·Gemini 웹 등)와 공부할 때, 아래 §1을 그대로 붙여넣어 사전 지식을 주고 시작한다.
>
> 작성일: 2026-07-26
>
> **코드를 직접 보며 혼자 공부할 때는 [`docs/study/`](study/00-index.md) 를 쓴다.**
> 실제 코드를 근거로 1~8단계를 쉬운 버전/전공자 버전으로 나눠 정리한 심화 문서다.
> 이 파일(study-guide.md)은 "코드를 못 보는 외부 AI에게 붙여넣기용" 브리핑에 특화되어 있다.

---

## 0. 쓰는 방법 (요약)

1. 새 대화를 열고 **§1 브리핑 블록**을 통째로 붙여넣는다.
2. 이어서 **`specs/001-online-virtual-evaluation/spec.md`와 `plan.md` 전문**을 붙여넣는다 (무엇을·왜 + 기술 설계).
3. 공부할 단계(§4)를 고르고, 해당 **파일의 실제 코드를 붙여넣은 뒤** §5의 질문 템플릿을 쓴다.
4. AI 답이 코드와 안 맞으면 그 자리에서 지적한다 — 붙여넣은 코드가 유일한 근거다.

**한 번에 한 파일씩.** 프로젝트 전체를 한꺼번에 넣으면 답이 얕아진다.

---

## 1. 붙여넣기용 브리핑 블록

```
나는 사내 DeepRacer 대회용 웹 서비스를 직접 만들었고, 그 코드를 top-down으로 이해하려 한다.
너는 이 코드베이스를 볼 수 없으므로, 다음 규칙을 지켜라.

[규칙]
1. 내가 붙여넣은 코드와 문서에만 근거해 설명하라. 붙여넣지 않은 파일의 내용은 추측하지 말고
   "그 파일을 붙여넣어 달라"고 요청하라.
2. 일반론(프레임워크 관례)과 이 코드의 실제 동작을 구분해서 말하라. 일반론을 말할 때는
   "일반적으로는" 이라고 명시하라.
3. 설명할 때 내가 붙여넣은 코드의 함수명·변수명을 그대로 인용하라.
4. 내가 틀리게 이해하고 있으면 정정하라. 맞다고 맞장구치지 마라.
5. 개념을 설명한 뒤에는 항상 "이 코드에서는 그게 어디에 해당하는가"를 짚어라.

[서비스 개요]
- 목적: 사내 DeepRacer(자율주행 RC카) 대회의 온라인 예선 플랫폼. 참가팀이 학습시킨 강화학습
  모델 파일을 업로드하면, 서버가 시뮬레이터로 자동 평가해 랩타임을 재고 리더보드에 순위를 매긴다.
- 규모: 사내 자체 운영. 시즌당 10팀 안팎, 동시 접속 소수.
- 핵심 규칙: 팀당 하루 5회 제출 제한, 한 팀은 "대기/평가중" 제출을 동시에 1건만 가질 수 있음,
  리더보드는 팀별 최고기록만 표시, 랩타임 오름차순(빠른 팀이 1위).

[기술 구성]
- 웹: Python 3.12 + FastAPI + Jinja2 서버사이드 템플릿 (SPA 아님, 페이지 새로고침 방식)
- DB: PostgreSQL + SQLAlchemy 2.0 ORM + Alembic 마이그레이션
- 인증: 세션 쿠키 기반 (Starlette SessionMiddleware, 서명된 쿠키). 팀 계정과 관리자 계정이 분리됨
- 비밀번호: bcrypt 해시
- 평가 워커: 웹과 별개의 파이썬 프로세스. DB 큐를 폴링해서 한 건씩 순차 처리하고,
  DeepRacer-for-Cloud(DRFC)라는 외부 도구를 셸로 호출해 시뮬레이션을 돌린다.
  결과(metrics JSON, 영상)는 MinIO(S3 호환 저장소)에서 boto3로 가져온다.
- 배포: **두 대로 나뉘어 있다.** 웹·DB·리버스 프록시(Caddy)는 클라우드 서버(AWS Lightsail)에서
  Docker Compose로 돌고 도메인+HTTPS로 공개된다. 평가 워커와 DRFC는 **AWS EC2 스팟 인스턴스**
  (m7i.xlarge, Ubuntu 22.04)에서 systemd 서비스로 돈다(시뮬레이터가 2GB 웹 서버로는 못 돌아감).
  둘은 Tailscale 사설망으로 DB를 주고받고, 모델·영상은 토큰 인증 HTTPS
  엔드포인트(`/internal/...`)로 전송한다. 운영자 노트북에도 같은 워커 구성이 남아 있어 예비
  워커로 켤 수 있다 — 워커가 여러 대여도 되도록 큐 확보가 `FOR UPDATE SKIP LOCKED`로 되어 있다.

[디렉터리 구조]
app/            웹 애플리케이션
  main.py         FastAPI 앱 생성, 미들웨어·라우터·정적파일 등록
  config.py       환경설정 (pydantic-settings)
  db.py           DB 엔진/세션 팩토리, ORM Base
  models.py       테이블 정의 (Season, Team, Account, AdminAccount, Submission, EvaluationResult)
  deps.py         인증 의존성 (로그인 여부 검사)
  security.py     비밀번호 해시/검증, 임의 비밀번호 생성
  quota.py        하루 제출 한도 계산
  records.py      팀별 최고기록 산출
  storage_paths.py 업로드 파일 경로 해석
  season_archive.py 시즌 종료 시 정리 작업
  routers/        URL별 처리 (auth / submissions / leaderboard / admin)
  templates/      Jinja2 HTML 템플릿
worker/         평가 워커 (run.py, drfc.py, run_worker.sh)
migrations/     Alembic 마이그레이션
tests/          pytest 테스트
specs/          기획·설계 문서 (spec.md, plan.md, tasks.md)

앞으로 내가 파일을 하나씩 붙여넣겠다. 준비됐으면 "준비됨"이라고만 답하라.
```

---

## 2. 이 서비스에서 요청이 흐르는 길 (top-down 뼈대)

세 갈래만 이해하면 전체가 잡힌다.

**(A) 리더보드 보기 — 로그인 불필요**
```
브라우저 GET /leaderboard
  → main.py 가 등록한 라우터 중 leaderboard.py 의 leaderboard_entry()
  → get_open_season(): 진행중 시즌이 있으면 303 리다이렉트 → /leaderboard/{id}
  → season_leaderboard(): build_leaderboard()로 팀별 최고기록 계산·정렬
  → templates/leaderboard.html 렌더 → HTML 응답
```

**(B) 모델 제출 — 로그인 필요**
```
브라우저 POST /submit (multipart 파일 업로드)
  → deps.get_current_team(): 세션 쿠키에서 team_id 확인, 없으면 로그인 화면으로
  → submissions.submit_upload(): 실격 여부 → 시즌 상태 → 동시 제출 → 하루 한도 →
     확장자 → 용량 순으로 검증
  → storage/models/{시즌}/{팀}/ 아래에 파일 저장
  → Submission 레코드를 status='queued'로 INSERT (이게 곧 작업 큐다)
```

**(C) 평가 — 웹과 완전히 분리된 프로세스**
```
worker/run.py 가 5초마다 폴링
  → claim_next_submission(): UPDATE ... FOR UPDATE SKIP LOCKED 로 한 건을 원자적으로 선점
  → status='running'
  → 모델 압축 해제 → MinIO에 업로드 → run_evaluation.sh 로 DRFC 평가 실행 (블로킹)
  → 결과 metrics JSON 파싱 → EvaluationResult INSERT, status='done'
  → 실패하면 status='error' + 사유 기록 (하루 한도에서 제외됨)
```

> 참가자는 "완료 알림"을 받지 않는다. 새로고침으로 확인하는 설계다(spec.md 확정 사항).

---

## 3. 파일별 역할 한 줄 요약

| 파일 | 역할 | 붙여넣어 공부할 때 함께 볼 것 |
|---|---|---|
| `app/main.py` | 앱 조립. 미들웨어·라우터·정적파일 마운트 | 없음 (가장 먼저) |
| `app/config.py` | 환경변수 → 설정 객체 | `.env` (비밀값은 가리고) |
| `app/db.py` | 엔진·세션 팩토리·ORM Base | `main.py` |
| `app/models.py` | 테이블 6개 정의, 관계, 제약 | `migrations/versions/*.py` |
| `app/deps.py` | 로그인 검사 의존성 | `routers/auth.py` |
| `app/routers/auth.py` | 팀 로그인/로그아웃 | `deps.py`, `security.py` |
| `app/routers/submissions.py` | 제출 화면·업로드 처리 | `quota.py`, `storage_paths.py` |
| `app/routers/leaderboard.py` | 리더보드·시즌 목록 | `records.py`, `models.py` |
| `app/routers/admin.py` | 시즌·팀·계정 관리 | `security.py`, `season_archive.py` |
| `app/quota.py` | 하루 한도 계산 (KST 기준, 관리자 보정값) | `models.py`의 Team |
| `app/storage_paths.py` | 컨테이너/호스트 경로 차이 흡수 | `worker/run.py` |
| `worker/run.py` | 큐 폴링·상태 전이·결과 저장 | `models.py`의 Submission |
| `worker/drfc.py` | 외부 도구 연동, S3 조회, metrics 파싱 | `worker/run.py` |
| `app/templates/*.html` | 화면 | 대응하는 라우터 |

---

## 4. 학습 순서 (top-down) 와 단계별 필수 개념

각 단계는 "코드를 붙여넣고 → 개념을 묻고 → 직접 바꿔보며 확인"의 순서로 한다.

### 1단계. 앱의 골격 — `main.py`, `config.py`, `db.py`
- **개념**: WSGI/ASGI가 뭔지, FastAPI 앱 객체가 어떻게 요청을 라우터로 넘기는지, 미들웨어가
  요청/응답을 감싸는 구조, 환경변수로 설정을 주입하는 이유(12-factor), 커넥션 풀과 세션 팩토리
- **핵심 질문**: 왜 설정을 코드에 하드코딩하지 않고 `.env`로 뺐는가?

### 2단계. 데이터 모델 — `models.py`, `migrations/`
- **개념**: ORM이 객체와 테이블을 잇는 방식, 외래키와 `relationship`, `cascade="all, delete-orphan"`,
  지연 로딩(lazy loading)과 N+1 문제, 유니크 제약, **부분 유니크 인덱스**(`uq_team_active_submission`
  — "대기/평가중은 팀당 1건"을 DB가 강제하는 장치), Enum 저장 방식, 마이그레이션이 필요한 이유
- **핵심 질문**: 동시 제출 제한을 애플리케이션 코드가 아니라 DB 인덱스로도 막는 이유는?

### 3단계. 인증 — `deps.py`, `routers/auth.py`, `security.py`
- **개념**: 쿠키와 세션의 차이, **서명된 쿠키**(변조는 막지만 내용은 숨기지 않음), FastAPI `Depends`
  의존성 주입, 비밀번호를 왜 암호화가 아니라 **단방향 해시**로 저장하는지, bcrypt의 salt와 cost,
  로그인 실패 시 "아이디/비밀번호 중 무엇이 틀렸는지" 알려주지 않는 이유
- **핵심 질문**: `SESSION_SECRET`이 바뀌면 왜 모두 로그아웃되는가?

### 4단계. 요청 처리 — `routers/submissions.py`, `quota.py`
- **개념**: HTTP 메서드 의미(GET은 안전, POST는 상태 변경), **POST-Redirect-GET 패턴**과 새로고침
  재전송 문제, multipart 파일 업로드를 청크로 나눠 읽는 이유(메모리), 검증 순서를 설계하는 법
  (싼 검사 먼저), 타임존 처리(KST 자정 기준 리셋), 트랜잭션과 커밋 시점
- **핵심 질문**: 업로드 용량 검사를 파일을 다 읽은 뒤가 아니라 읽는 도중에 하는 이유는?

### 5단계. 조회·표현 — `routers/leaderboard.py`, `records.py`, `templates/`
- **개념**: 라우트 매칭 순서(`/leaderboard/seasons`가 `/leaderboard/{id}`보다 먼저 선언돼야 하는 이유),
  리다이렉트 상태코드(301/302/303 차이), 서버사이드 렌더링과 템플릿 상속, 정렬 키를 튜플로 주는
  동점 처리, 자동 이스케이프와 XSS
- **핵심 질문**: 순위 계산을 SQL이 아니라 파이썬에서 하는 지금 방식의 장단점은?

### 6단계. 비동기 작업 — `worker/run.py`, `worker/drfc.py`
- **개념**: 작업 큐를 왜 DB 테이블로 만들 수 있는지(전용 큐 시스템 없이), 폴링 vs 이벤트,
  **`FOR UPDATE SKIP LOCKED`**로 여러 워커가 같은 작업을 집지 않게 하는 원리, 상태 기계
  (queued→running→done/error), 죽은 작업 복구(stale running), 외부 프로세스를 `subprocess`로
  호출할 때의 타임아웃·표준출력 처리, S3 호환 저장소와 boto3
- **핵심 질문**: 워커가 중간에 죽으면 그 제출은 어떻게 되는가?

### 7단계. 운영 — `Dockerfile`, `docker-compose.yml`, `docs/operations.md`
- **개념**: 이미지와 컨테이너, 바인드 마운트와 볼륨, **컨테이너와 호스트의 파일 경로가 다르다는 것**
  (`app/storage_paths.py`가 존재하는 바로 그 이유 — 실제로 장애가 났던 지점), 포트 매핑,
  무중단에 가까운 배포 순서(build → up), 리버스 터널로 사설망 서비스를 공개하는 원리
- **핵심 질문**: 웹은 컨테이너에서, 워커는 호스트에서 도는 구성이 왜 필요했는가?

---

## 5. 질문 템플릿 (그대로 복사해 쓰기)

**(1) 파일 하나를 처음 볼 때**
```
아래는 이 프로젝트의 <파일경로> 전문이다.
1) 이 파일이 전체 구조에서 맡은 역할을 3줄로 요약하라.
2) 위에서 아래로 읽으며, 의미 단위로 끊어서 각 부분이 무엇을 하는지 설명하라.
3) 이 파일을 이해하려면 알아야 하는 개념을 목록으로 뽑아라. 각 개념은 한 줄 정의 +
   "이 코드에서 어디에 해당하는지"를 함께 적어라.
4) 내가 아직 안 붙여넣은 파일 중 이 파일을 이해하는 데 꼭 필요한 것이 있으면 알려달라.

[코드]
<여기에 붙여넣기>
```

**(2) 개념을 깊이 팔 때**
```
위 코드의 <개념/함수명>에 대해 묻는다.
1) 이게 없으면 어떤 문제가 생기는지 구체적인 시나리오로 설명하라.
2) 대안 설계는 무엇이 있고, 각각의 트레이드오프는 무엇인가?
3) 이 코드가 택한 방식이 이 서비스 규모(10팀 내외, 사내 운영)에서 적절한지 평가하라.
```

**(3) 이해했는지 확인할 때 (제일 중요)**
```
내가 이해한 내용을 적겠다. 틀린 곳을 지적하고, 맞으면 맞다고만 하라. 보충 설명은 틀린 부분에만 해라.
[내 이해]
...
```

**(4) 직접 실험 과제를 받을 때**
```
이 파일을 제대로 이해했는지 확인할 수 있는 실습 과제를 3개 내라.
조건: 실제로 코드를 고치거나 값을 바꿔보고 결과를 눈으로 확인할 수 있어야 하고,
운영 데이터를 망가뜨리지 않아야 한다.
```

---

## 6. 다른 AI와 공부할 때 주의점

- **환각 확인법**: "그 함수는 이 파일 몇 번째 줄에 있나?"라고 되물어라. 붙여넣지 않은 내용을
  지어냈다면 여기서 드러난다.
- **버전 차이**: 이 프로젝트는 FastAPI 0.115 / SQLAlchemy **2.0** 스타일이다. AI가 SQLAlchemy 1.x
  문법(`db.query(...)` 위주)이나 옛 FastAPI 관례로 설명하면 지적하라. 2.0은 `select()` + `db.execute()`가 기본이다.
- **"왜"는 문서에 있다**: 설계 의도가 궁금하면 AI에게 묻기 전에 `specs/001-online-virtual-evaluation/`
  의 spec.md(무엇을·왜) → plan.md(어떻게) → tasks.md(작업 단위와 검증 기록) 순서로 찾아보라.
  특히 tasks.md에는 실제로 겪은 장애와 그 원인이 기록돼 있다.
- **실행해서 확인하라**: 테스트가 있다. `PYTHONPATH=. .venv/bin/python -m pytest tests -q`.
  코드를 고쳐보고 테스트가 깨지는지 보는 것이 어떤 설명보다 확실하다.
- **운영 중 서비스를 건드리지 말 것**: 공부는 로컬 사본이나 별도 DB에서. 운영 DB·컨테이너에
  직접 실험하면 대회가 멈춘다. 안전한 실험 방법은 `docs/operations.md` 참고.
