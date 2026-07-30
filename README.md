# SPG DeepRacer Leaderboard

로컬 인프라만으로 운영하는 자체 DeepRacer 대회 플랫폼. AWS DeepRacer 공식 배포 방식은 사용하지 않는다 — 결제 계정이 필요하고 비용을 예측하기 어렵기 때문이다. 대신 DRFC(DeepRacer-for-Cloud)를 로컬 서버에서 활용해 참가자가 제출한 모델을 평가하고, 결과를 리더보드에 자동 반영한다.

## 프로젝트 구성 (두 개의 큰 덩어리)

| # | 서비스 | 한 줄 설명 | STEP1 명세 | STEP2 계획 | STEP3 작업 분해 |
|---|---|---|---|---|---|
| 1 | 가상 트랙 기반 자동 평가 플랫폼 | 참가팀이 모델 파일을 제출하면 서버가 `dr-start-evaluation`으로 자동 채점하고 리더보드에 반영 | [spec.md](specs/001-online-virtual-evaluation/spec.md) | [plan.md](specs/001-online-virtual-evaluation/plan.md) | [tasks.md](specs/001-online-virtual-evaluation/tasks.md) |
| 2 | 오프라인 대회용 리더보드 & 비전 타이머 | 현장 카메라가 출발~2바퀴 완주를 자동 계측해 리더보드에 실시간 반영 | [spec.md](specs/002-offline-vision-timer/spec.md) | [plan.md](specs/002-offline-vision-timer/plan.md) | (온라인 구현 이후 진행) |

두 서비스는 **예선(1번, 온라인) → 본선(2번, 오프라인)** 구조로 연결된다. 단, 보유 물리 차량이 10대로 제한되어 있어 예선 통과팀 수가 이를 초과하면 상위팀은 전담 차량을, 하위팀은 차량을 공유하는 방식으로 운영한다. (자세한 내용은 각 spec 참고)

## 진행 방식 (Spec Kit 스타일 4단계)

각 서비스마다 아래 4단계를 순서대로 진행하며, 단계별로 `.md` 문서를 남겨 이후 구현 시 참고한다.

1. **STEP1 — 명세서 (Spec)**: 무엇을, 왜 만드는지. 사용자 여정·경험·성공 기준에 집중. 기술적 세부사항 제외. ✅ 완료
2. **STEP2 — 기술 계획 (Plan)**: STEP1을 만족시키기 위한 아키텍처·데이터 모델·기술 스택 결정. ✅ 완료
3. **STEP3 — 작업 분해 (Tasks)**: STEP2를 실행 가능한 작업 단위로 쪼갬
4. **STEP4 — 구현 (Implement)**: 실제 코드 작성

현재까지 001(온라인)은 **STEP1~STEP4 완료** — 실환경 DRFC 평가 1건과 [Phase 8 검증 36항목](specs/001-online-virtual-evaluation/tasks.md)을 모두 통과했다. 002(오프라인)는 STEP1~STEP2까지 진행했고, 다음은 **002의 STEP3(작업 분해)** 다.

## 구현 순서

**001(온라인 가상 트랙 평가)을 먼저 전체 구현하고, 002(오프라인 비전 타이머)는 그 다음에 진행한다.** 오프라인 서비스는 카메라 등 물리 장비 조달이 필요해 시간이 걸리는 반면, 온라인 서비스는 지금 있는 자원(노트북 서버 + DRFC)만으로 바로 시작할 수 있기 때문. 002의 STEP3(작업 분해)는 001 구현이 끝난 뒤 다시 착수한다.

## 지금까지 확정된 공통 결정 사항

- 참가 단위: **팀**. 팀은 참가자가 자율 가입하는 게 아니라 **관리자가 대회 전 사전 등록**한다.
- 공개 범위: 순위표·기록·평가 영상 모두 **완전 공개**(로그인 없이 링크로 누구나 열람).
- 운영 형태: 단발성 대회가 아니라 **상시 운영되는 서비스** 위에서 **시즌/대회 단위**로 반복 개최하고, 과거 시즌 기록도 조회 가능해야 한다.
- 1번(온라인 예선)과 2번(오프라인 본선)은 연결된 하나의 대회 흐름이며, **물리 차량 10대 제약**이 본선 참가 방식(전담 차량 vs 공유 차량)에 영향을 준다.
- 온라인 예선: 팀당 하루 제출 5회 제한, 리더보드는 최고기록만 표시, 트랙 1개 고정, 3바퀴 평가, 제출 취소 불가, 실격 시 사유 비공개.
- 오프라인 본선: 2바퀴 고정, 연습주행(미반영)·실제주행(반영) 구분, 실제주행은 10분 내 무제한 재도전, 트랙 이탈 시 5초 페널티, 차량·주행순서는 추첨.
- 기술 스택(STEP2 확정): **Python 기반**(백엔드 FastAPI + 서버 렌더링) + **PostgreSQL** + 로컬 디스크 저장. 현재 실행 환경은 Windows 노트북(GPU 없음) + WSL2 Ubuntu에서 DRFC 실행(평가 1건당 약 10분). 향후 확장 옵션: GPU 서버 1대로 이전([gpu-server-migration.md](specs/001-online-virtual-evaluation/gpu-server-migration.md)) 또는 GPU 없는 노트북 여러 대를 워커 풀로 묶어 병렬 처리([multi-laptop-worker-pool.md](specs/001-online-virtual-evaluation/multi-laptop-worker-pool.md)) — 아직 실제 채택 여부는 미정, 참고 문서로만 존재. 비전 타이머 하드웨어는 신규 구축 예정.
- 온라인 제출 규칙(STEP3 정리): 한 팀은 "대기/평가중" 제출을 동시에 1건만 가질 수 있음(이전 결과가 나와야 재업로드 가능). 하루 5회 한도는 완주 성공 여부가 아니라 "평가가 끝까지 정상 실행됐는지"로 카운트. 리더보드에는 순위·팀명·누적 제출 횟수·최고기록·영상 링크를 표시.

## 실행하기 (온라인 서비스)

처음 받았다면 환경 변수부터 채운다. `.env`는 커밋되지 않으므로 클론 후에는 항상 직접 만들어야 한다.

```bash
cp -n .env.example .env
```

> `-n`은 "이미 있으면 덮어쓰지 않는다"는 뜻. 운영 중인 서버에서 실수로 실행해도
> 기존 `SESSION_SECRET`이 날아가지 않는다. (시크릿이 바뀌면 접속 중인 관리자·참가자가 전부 로그아웃된다.)

`SESSION_SECRET`은 비어 있으면 안 된다. `python -c "import secrets; print(secrets.token_hex(32))"` 로 생성해서 넣는다.

### 운영 중인 서비스 (2026-07-30 이후)

웹과 DB는 **클라우드 서버**(AWS Lightsail 서울)에서 24시간 돌고 있다.

- 서비스: **https://spg-deepracer.doublejeong.com** · 관리자: `/admin`
- 서버 접속·점검: [docs/server-access.md](docs/server-access.md)

**노트북에서는 평가 워커만 띄운다.** DRFC 시뮬레이터가 노트북에 있기 때문이다.

```bash
setsid nohup bash worker/run_worker.sh > /tmp/worker.log 2>&1 < /dev/null &
```

워커는 `.env`의 `WORKER_TOKEN`이 설정돼 있으면 클라우드 서버에서 모델을 받아오는 방식으로 동작한다.
자세한 절차는 [docs/operations.md](docs/operations.md), 대회 운영 전반은
[docs/handover.md](docs/handover.md) 참고.

### 로컬에서 전체를 띄워보려면 (개발·비상 복구용)

```bash
docker compose up -d
```

웹: http://localhost:8000 · 관리자: http://localhost:8000/admin

> 이 방식은 개발용이거나 클라우드 서버를 못 쓰게 됐을 때의 비상 수단이다. 운영 데이터는
> 클라우드 서버에 있으므로, 로컬로 되살리려면 백업 복원이 함께 필요하다.

## 참고 자료

- 리더보드 UI 예시: [춘천 DeepRacer 리더보드](https://chuncheon-deepracer.ai-castle.com/2024/leaderboard/%EB%B3%B8%EC%84%A0%EC%88%9C%EC%9C%84.html)
- 오프라인 리더보드 참고: [nalbam/deepracer-board](https://github.com/nalbam/deepracer-board)
- 비전 타이머 참고: [ai-castle/deepracer-vision-timer](https://github.com/ai-castle/deepracer-vision-timer)
