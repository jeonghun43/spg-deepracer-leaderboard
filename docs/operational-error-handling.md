# 대회 운영 중 에러 처리 가이드

대회 운영 중 발생하는 에러들을 감지하고, 자동 복구 또는 운영자 개입을 위한 가이드.

## 0. 필요할만한 것들

```bash
# s3 버킷에 올라가 있는 목록 보기
aws $DR_LOCAL_PROFILE_ENDPOINT_URL s3 ls s3://$DR_LOCAL_S3_BUCKET 
```

## 1. 환경 설정 에러

### 1.1 DRFC 환경변수 미로드 (`DR_LOCAL_S3_BUCKET` 등)

**증상**
```
예상치 못한 오류: 'DR_LOCAL_S3_BUCKET'
```

**발생 원인**
- 워커 시작 시 DRFC의 `activate.sh`를 source하지 않음
- 또는 DRFC 디렉터리 경로가 잘못됨
- run.env 파일이 없거나 로드되지 않음
- **워커 프로세스는 시작 시점의 쉘 환경에 고정된다.** `worker/run.py`는 한 번 뜨면 계속 살아있는
  장기 실행 프로세스이고, `run_worker.sh`가 시작 시점에 `source bin/activate.sh`로 로드한
  환경변수를 그대로 물려받아 프로세스 생명주기 동안 유지한다. **다른 터미널에서 나중에
  `source bin/activate.sh`를 실행해도 이미 떠 있는 워커 프로세스에는 전혀 영향을 주지 않는다**
  (2026-07-25 실제 발생: 현재 터미널에서 `echo $DR_LOCAL_S3_BUCKET`가 정상 출력돼도, 워커가
  그보다 먼저 잘못된 환경으로 떠 있었다면 계속 이 에러가 난다).

**감지 방법**

워커 로그 확인:
```bash
tail -f /tmp/worker.log
```

**현재 터미널**의 환경변수 상태 확인 (워커 프로세스 자체의 상태는 아님, 아래 참고):
```bash
echo $DR_LOCAL_S3_BUCKET
# 공백이면 미로드 상태
```

**실제로 떠 있는 워커 프로세스**가 어떤 환경변수를 가지고 있는지 직접 확인 (가장 확실한 방법):
```bash
ps aux | grep 'worker.run'
# PID 확인 후
cat /proc/<PID>/environ | tr '\0' '\n' | grep DR_LOCAL_S3_BUCKET
# 출력이 없으면 그 프로세스는 여전히 환경변수 없이 떠 있는 상태
```

**처리 로직 (워커 코드에 반영됨)**

`worker/run_worker.sh`에 시작 시점 필수 환경변수 검증을 추가했다 (2026-07-25).
환경변수가 없으면 참가자 제출 시점까지 기다리지 않고 워커 기동 즉시 명확한 에러로 실패한다:

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
echo "[run_worker] DR_LOCAL_S3_BUCKET=$DR_LOCAL_S3_BUCKET (환경변수 확인됨)"
```

이제 워커를 잘못된 환경으로 띄우면 `/tmp/worker.log`에 즉시
`[run_worker] ERROR: 필수 환경변수 ...` 가 찍히고 프로세스가 종료된다 — 참가자가 제출할 때까지
기다렸다가 깊은 스택트레이스로 알게 되는 일이 없다.

**운영자 조치**

워커를 다시 시작하되, DRFC 환경을 먼저 로드:

```bash
cd ~/deepracer-for-cloud
source bin/activate.sh run.env
cd /mnt/c/Users/jjh03/spg_deepracer_leaderboard
pgrep -f -- '-m [w]orker\.run' | xargs -r kill   # pkill 금지 — 자기 셸까지 죽는다
setsid nohup bash worker/run_worker.sh > /tmp/worker.log 2>&1 < /dev/null &
```

또는 한 줄로:

```bash
(cd ~/deepracer-for-cloud && source bin/activate.sh run.env) && (cd /mnt/c/Users/jjh03/spg_deepracer_leaderboard && pgrep -f -- '-m [w]orker\.run' | xargs -r kill; setsid nohup bash worker/run_worker.sh > /tmp/worker.log 2>&1 < /dev/null &)
```

---

## 2. 모델 업로드 및 검증 에러

### 2.1 MinIO 데이터 폴더 직접 압축

**증상**
```
MinIO의 내부 저장 폴더를 그대로 압축한 것으로 보입니다 (xl.meta / part.N 파일이 들어 있음).
```

**발생 원인**
- 참가자가 `~/.minio/data/` 폴더를 직접 압축해서 제출
- DRFC에서 `aws s3 sync`로 정상 내보내지 않음

**감지 및 처리**
- 이미 worker/drfc.py의 `_looks_like_minio_raw_dump()`에서 자동 감지
- EvaluationError 발생 → Submission.status = 'error'로 전이
- 사용자에게 정확한 해결 방법 안내 (operations.md의 "참가자 제출 형식 안내" 참고)

**참가자에게 안내**

제출 전 [docs/operations.md의 "참가자 제출 형식 안내"](operations.md#참가자-제출-형식-안내) 섹션을 참조하도록 사전 공지.

### 2.2 압축 파일 손상 또는 형식 불일치

**증상**
```
모델 압축 파일을 열 수 없습니다: Bad CRC-32 for file ...
```
또는
```
압축 파일에서 model_metadata.json을 찾을 수 없습니다.
```

**감지 및 처리**
- worker/drfc.py의 `_extract_archive()` 및 `_find_model_root()`에서 자동 감지
- EvaluationError 발생 → 명확한 오류 메시지를 Submission.error_message에 저장
- 참가자가 웹 UI에서 오류 메시지 확인 후 재제출

**참가자 안내**
- 모델 폴더 안에 `model_metadata.json`과 체크포인트 파일들이 있는지 확인
- 빈 폴더는 포함하지 않기
- 형식은 `.zip` 또는 `.tar.gz`만 지원

---

## 3. 평가 실행 에러

### 3.1 DRFC 컨테이너 시작 실패

**증상**
```
[run_evaluation] 평가 컨테이너가 300초 안에 시작되지 않았습니다
```

**발생 원인**
- Docker Swarm 스택이 이미 실행 중
- Docker 이미지 다운로드 중단
- 디스크 공간 부족

**감지 및 처리 (worker/run_evaluation.sh에서)**

```bash
# 이미 실행 중인 부분:
if [[ "$started" -eq 0 ]]; then
  echo "[run_evaluation] 평가 컨테이너가 ${START_TIMEOUT_SECONDS}초 안에 시작되지 않았습니다" >&2
  capture_logs
  dr-stop-evaluation || true
  exit 3
fi
```

워커에서 exit code 3을 감지하면 자동으로:
- 로그 확인: `cat storage/eval_logs/{제출ID}.log`
- Submission.status = 'error'
- 운영자에게 알림

**운영자 조치**

```bash
# 스택 상태 확인
docker stack ps deepracer-eval-{RUN_ID}

# 혹시 이전 스택 잔재가 남아있으면 정리
docker stack rm deepracer-eval-* || true

# 워커 재시작
pgrep -f -- '-m [w]orker\.run' | xargs -r kill   # pkill 금지 — 자기 셸까지 죽는다
(cd ~/deepracer-for-cloud && source bin/activate.sh run.env) && \
cd /mnt/c/Users/jjh03/spg_deepracer_leaderboard && \
setsid nohup bash worker/run_worker.sh > /tmp/worker.log 2>&1 < /dev/null &
```

### 3.2 평가 타임아웃 (30분 초과)

**증상**
```
[run_evaluation] TIMEOUT: 1800초 초과
```

**발생 원인**
- 모델이 무한 루프에 빠짐
- DRFC 자체 성능 저하
- 시스템 리소스 부족 (메모리, CPU)

**감지 및 처리**

worker/run_evaluation.sh에서 exit code 2 반환:

```bash
if [[ "$elapsed" -ge "$MAX_WAIT_SECONDS" ]]; then
  echo "[run_evaluation] TIMEOUT: ${MAX_WAIT_SECONDS}초 초과" >&2
  capture_logs
  dr-stop-evaluation || true
  exit 2
fi
```

워커에서 처리:
- Submission.status = 'error'
- 시뮬레이션 로그를 `storage/eval_logs/{제출ID}.log`에 저장
- error_message = "평가 시간 초과 (30분 이상)"

**운영자 조치**

로그 확인:
```bash
cat storage/eval_logs/{제출ID}.log
```

혹시 이전 스택이 남아있으면 정리:
```bash
docker stack ps deepracer-eval-* 2>/dev/null || echo "스택 없음"
docker stack rm deepracer-eval-* 2>/dev/null || true
```

시스템 리소스 확인:
```bash
free -h
df -h
```

---

## 3.5 웹 앱 컨테이너가 뜨지 않음 (포트 8000 충돌)

**증상**

`docker compose up` 시:
```
failed to bind host port 0.0.0.0:8000/tcp: address already in use
```

이후 컨테이너가 재시작 루프에 빠지며 로그에는 엉뚱하게도 DB 관련 오류가 찍힌다:
```
could not translate host name "db" to address: Temporary failure in name resolution
```

**발생 원인 (2026-07-25 실제 발생)**

컨테이너가 아니라 **WSL 호스트에서 `uvicorn`을 직접 띄워둔 프로세스**가 8000번 포트를 점유하고
있었다. 디버깅 중에 `docker compose` 대신 `uvicorn`을 수동 실행해두고 잊은 경우다.

포트 바인딩에 실패한 컨테이너는 생성만 되고 네트워크에 붙지 못한 채 남는다. 그 상태로
`restart: unless-stopped` 정책 때문에 계속 재시작되면서, **원래 원인(포트 충돌)이 아니라
2차 증상(`db` 이름 해석 실패)만 로그에 보이게 된다** — 이 로그만 보고 DB 문제로 오해하기 쉽다.

**감지 방법**

```bash
# 8000번을 누가 잡고 있는지 (sudo 없이)
ss -tlnp | grep 8000
ps aux | grep '[u]vicorn'

# 컨테이너가 네트워크에 붙어 있는지 — 출력이 비어 있으면 붙지 못한 상태
docker inspect spg_deepracer_leaderboard-web-1 --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
```

**운영자 조치**

```bash
pkill -f 'uvicorn app.main:app'
docker compose up -d --force-recreate web
```

`--force-recreate`가 필요한 이유: 포트 충돌로 망가진 컨테이너는 단순 재시작만으로는 네트워크에
다시 붙지 않는다. 컨테이너를 새로 만들어야 compose 네트워크에 정상 연결된다.
(DB 데이터는 `db_data` 볼륨에 있으므로 web 컨테이너를 재생성해도 안전하다.)

확인:
```bash
curl -s http://localhost:8000/healthz   # {"status":"ok"} 나와야 정상
```

---

## 4. 데이터베이스 에러

### 4.1 PostgreSQL 연결 실패

**증상**
```
예상치 못한 오류: could not connect to database
```

**발생 원인**
- DB 컨테이너가 시작되지 않음
- DB 포트(5432) 충돌
- DB 디스크 공간 부족

**감지 방법**

DB 로그 확인:
```bash
docker compose logs -f db --tail 50
```

DB 상태 확인:
```bash
docker compose ps db
```

**처리 로직 (app 코드에서)**

worker/drfc.py 또는 app 서버에서 DB 연결 재시도 추가:

```python
from sqlalchemy.exc import OperationalError
import time

def save_submission_with_retry(db, submission, max_retries=3):
    for attempt in range(max_retries):
        try:
            db.add(submission)
            db.commit()
            return
        except OperationalError as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # 1, 2, 4초
                print(f"DB 연결 실패, {wait_time}초 후 재시도...")
                time.sleep(wait_time)
            else:
                raise
```

**운영자 조치**

DB 재시작:
```bash
docker compose restart db
```

또는 전체 재시작:
```bash
docker compose down
docker compose up -d
```

### 4.2 S3(MinIO) 저장 실패

**증상**
```
S3 저장 실패: Connection refused
```

**발생 원인**
- MinIO 컨테이너 미실행
- 바켓 권한 문제

**감지 방법**

워커 로그에서 확인:
```bash
grep -i "S3\|minio\|bucket" /tmp/worker.log
```

MinIO 연결 테스트:
```bash
cd ~/deepracer-for-cloud
source bin/activate.sh run.env
aws s3 ls $DR_LOCAL_S3_ENDPOINT_URL --profile $DR_LOCAL_S3_PROFILE 
```

**운영자 조치**

DRFC 스택 재시작:
```bash
cd ~/deepracer-for-cloud
source bin/activate.sh run.env
dr-stop-evaluation || true
dr-start-evaluation -q  # 스택 재배포
```

---

## 5. 런타임 에러 감시 체크리스트

### 매일 아침 운영자 확인 사항

```bash
# 1. 웹 앱 상태
docker compose logs -f web --tail 50 | grep -i error

# 2. DB 상태
docker compose ps db

# 3. DRFC 연결
cd ~/deepracer-for-cloud && source bin/activate.sh run.env
echo "DR_LOCAL_S3_BUCKET=$DR_LOCAL_S3_BUCKET"
aws s3 ls --profile $DR_LOCAL_S3_PROFILE

# 4. 워커 상태
ps aux | grep 'worker.run'
tail -n 30 /tmp/worker.log

# 5. 평가 대기열
python << 'EOF'
from app.db import SessionLocal
from app.models import Submission

db = SessionLocal()
pending = db.query(Submission).filter(Submission.status == 'pending').count()
evaluating = db.query(Submission).filter(Submission.status == 'evaluating').count()
error = db.query(Submission).filter(Submission.status == 'error').count()

print(f"대기 중: {pending}, 평가 중: {evaluating}, 오류: {error}")
db.close()
EOF
```

### 긴급 상황 대응

**워커가 죽었을 때**
```bash
pgrep -f -- '-m [w]orker\.run' | xargs -r kill   # pkill 금지 — 자기 셸까지 죽는다
# 위의 "DRFC 환경변수 미로드" 섹션의 "운영자 조치" 참고
```

**DB가 응답 없을 때**
```bash
docker compose restart db
```

**평가가 계속 타임아웃할 때**
```bash
# 1. 이전 스택 정리
docker stack rm deepracer-eval-* || true
docker ps -a | grep deepracer

# 2. 워커 재시작
pgrep -f -- '-m [w]orker\.run' | xargs -r kill   # pkill 금지 — 자기 셸까지 죽는다
# 다시 시작하기 전에 위의 "DRFC 환경변수 미로드" 섹션 참고
```

---

## 6. 향후 개선 사항

- [ ] 워커 프로세스 자동 헬스체크 (systemd, supervisord)
- [ ] 에러 발생 시 운영자에게 메일/Slack 알림
- [ ] 자동 복구 스크립트 (예: 타임아웃 후 자동 재시작)
- [ ] 에러 통계 대시보드 (일일/주간 에러율)
- [ ] 모델 업로드 전 자동 검증 (형식, 크기, 메타데이터)
