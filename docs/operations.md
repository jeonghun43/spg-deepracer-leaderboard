# 운영 가이드 (평가 워커 · 노트북 쪽)

**현재 구성 (2026-07-30 이후)**: 웹과 DB는 **클라우드 서버**(AWS Lightsail 서울)에서 24시간 돌고,
이 노트북에는 **평가 워커와 DRFC만** 남아 있다. 워커가 호스트의 DRFC Docker Swarm을 제어해야 해서
컨테이너에 넣지 않는다.

| 무엇을 | 어디서 | 문서 |
|---|---|---|
| 웹·DB·HTTPS | 클라우드 서버 | [server-access.md](server-access.md) |
| 평가 워커·DRFC | 이 노트북 (WSL2) | **이 문서** |
| 백업 실행 | 이 노트북 → 서버에서 끌어옴 | 이 문서 "자동 백업" |

> 이전 완료 기록: 2026-07-30에 웹·DB를 클라우드로 옮겼고, **노트북의 웹·DB 컨테이너는 정지했다**
> (`docker compose stop`). 데이터와 볼륨은 이전 검증용으로 그대로 남겨두었다. MinIO는 DRFC 평가에
> 필요하므로 계속 떠 있어야 한다.

## 사전 준비 (최초 1회)

```bash
cd /mnt/c/Users/jjh03/spg_deepracer_leaderboard
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

DRFC(`~/deepracer-for-cloud`)는 이미 설정되어 있다고 가정한다.

`.env`에는 **클라우드 서버를 가리키는 값**이 들어 있어야 한다(워커 전용).

```
DATABASE_URL=postgresql+psycopg2://drleader:<비밀번호>@100.110.139.82:5432/drleader
WEB_BASE_URL=https://spg-deepracer.doublejeong.com
WORKER_TOKEN=<서버 .env의 값과 동일>
```

`WORKER_TOKEN`이 설정되면 워커가 자동으로 **http 전송 모드**로 동작한다 — 모델을 서버에서
내려받고 영상·metrics를 서버로 올린다. 비어 있으면 같은 디스크를 쓰는 옛 방식으로 동작한다.

## 기동 절차

### 1. 평가 워커 기동

```bash
setsid nohup bash worker/run_worker.sh > /tmp/worker.log 2>&1 < /dev/null &
```

로그 확인:

```bash
tail -f /tmp/worker.log
```

**워커는 동시에 1개만 실행한다.** 지금 구조는 순차 처리를 전제로 하며, 특히 평가 영상이
S3의 고정 키(`.../mp4/camera-pip/0-video.mp4` 등 카메라 앵글별 고정 경로)에 덮어써지기 때문에
여러 워커가 동시에 돌면 영상이 섞인다. 여러 대로 확장하려면
[multi-laptop-worker-pool.md](../specs/001-online-virtual-evaluation/multi-laptop-worker-pool.md)의
설계를 먼저 반영해야 한다.

### 1-1. 평가 워커 멈추고 재개하기

노트북에서 다른 무거운 작업(특히 **DRFC 학습**)을 해야 하거나 노트북을 끄기 전에 쓴다.

**① 지금 평가 중인지 먼저 확인한다** — 이 확인을 건너뛰면 안 된다.

```bash
tail -5 /tmp/worker.log
```

`평가 시작` 이후 `평가 완료`가 안 보이면 평가가 진행 중이다. **끝날 때까지 기다린다**(1건에 10~30분).
서버에서 큐를 봐도 된다 — [server-access.md](server-access.md)의 "평가 대기열 조회" ①번 쿼리에서
`running`이 없으면 안전하다.

**② 멈추기**

```bash
pgrep -f -- '-m [w]orker\.run' | xargs -r kill
```

`kill $(pgrep ...)` 형태는 **워커가 이미 죽어 있으면 `kill: usage:` 라는 엉뚱한 에러**를 뱉는다
(빈 인자로 `kill`이 실행돼서다). 위 형태는 프로세스가 없으면 조용히 아무 일도 하지 않는다.

멈췄는지 확인 — 아무것도 안 나오면 정지된 것이다.

```bash
pgrep -af -- '-m [w]orker\.run'
```

**③ 다시 시작하기**

```bash
cd /mnt/c/Users/jjh03/spg_deepracer_leaderboard && setsid nohup bash worker/run_worker.sh > /tmp/worker.log 2>&1 < /dev/null &
```

띄운 뒤 **반드시 확인한다.** PID가 나오고 로그에 `워커 시작`이 찍혀야 성공이다.

```bash
sleep 3; pgrep -af -- '-m [w]orker\.run'; tail -3 /tmp/worker.log
```

⚠️ **`setsid`를 빼먹지 말 것.** 그냥 `&`로 띄우면 터미널 창을 닫는 순간 SIGHUP을 받고 죽는다.
로그에 아무 에러 없이 조용히 끊겨 있다면 십중팔구 이 경우다 (2026-07-30 실제 발생 — 평가를
정상적으로 마친 직후 죽어 있었고, 그 뒤 2시간 반 동안 아무도 몰랐다).

멈춰 있는 동안에도 **웹은 클라우드에서 계속 돌고 참가자 제출도 정상 접수된다.** 평가만 큐에 쌓이고
참가자 화면에는 "평가 서버가 재개된 뒤 순서대로 처리됩니다" 안내가 뜬다(하트비트가 3분간 끊기면
자동으로 표시된다). 워커를 다시 띄우면 밀린 것부터 제출 순서대로 처리한다.

⚠️ **평가 도중에 죽이면 그 제출이 `running`에 갇힌다.** 워커는 시작할 때 오래된 `running`을
대기열로 되돌리지만, 그 기준이 **시작한 지 35분(`EVAL_MAX_WAIT_SECONDS` 1800초 + 5분)이 지난 건**
이라 방금 죽인 건은 회수되지 않는다. 그 팀은 "이전 제출의 결과가 아직 나오지 않았습니다"에 막혀
새 모델을 못 올린다. 35분 뒤 워커를 재시작하면 자동으로 풀리고, 급하면 서버 DB에서 직접 되돌린다:

```sql
UPDATE submissions SET status='queued', worker_id=NULL, started_at=NULL WHERE id=<제출ID>;
```

### 1-2. ⚠️ 노트북에서 DRFC 학습을 돌릴 때

**학습(`dr-start-training`)과 평가는 같은 MinIO 버킷의 같은 경로**
(`s3://$DR_LOCAL_S3_BUCKET/$DR_LOCAL_S3_MODEL_PREFIX/model/`)를 쓴다. 워커는 평가 직전에
**이 경로의 오브젝트를 전부 지우고** 참가자 모델을 넣는다(`worker/drfc.py`의 `inject_model`).

| 겹치면 | 결과 |
|---|---|
| 학습 중에 평가가 시작됨 | 워커가 **학습 중인 체크포인트를 삭제**한다 |
| 평가 중에 학습을 시작함 | 학습이 참가자 모델을 덮어써 **엉뚱한 평가 결과**가 나온다 |

**그래서 노트북에서 학습하려면 §1-1로 워커를 먼저 멈춘다.** 학습이 끝나고 다시 띄우면 된다.
자기 모델을 리더보드에 올리고 싶다면 다른 팀과 똑같이 **웹사이트에 업로드해야 한다** — 노트북에서
학습했다고 큐에 자동으로 들어가지 않는다. 큐에 행을 넣는 경로는 웹 업로드(`POST /submit`) 하나뿐이다.

### 2. 클라우드 서버 확인

웹은 노트북과 무관하게 이미 돌고 있다. 상태만 확인하면 된다.

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://spg-deepracer.doublejeong.com/healthz
```

서버에 직접 들어가는 방법과 점검 명령은 [server-access.md](server-access.md)에 있다.

---

## [과거 방식 · 현재 미사용] 외부 공개 (Cloudflare Tunnel)

> ⚠️ **평상시에는 쓰지 않는다.** 지금은 클라우드 서버 + 도메인(`spg-deepracer.doublejeong.com`)으로
> 공개한다. 아래는 **클라우드 서버를 못 쓰게 됐을 때 노트북만으로 급히 서비스를 되살리는 비상
> 수단**으로 남겨둔다. 이 방식은 주소가 매번 바뀌고 노트북이 절전에 들어가면 끊긴다(2026-07-27 실제 발생).
>
> 비상 복구 시에는 노트북의 웹·DB를 다시 띄우고(`docker compose up -d`) 백업을 복원한 뒤 아래 터널을 실행한다.

참가자가 다른 네트워크에서도 접속할 수 있게 하려면 터널을 띄운다. 최초 1회만 설치한다.

```bash
mkdir -p ~/bin
curl -L --output ~/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x ~/bin/cloudflared
```

터널 실행:

```bash
setsid nohup ~/bin/cloudflared tunnel --url http://localhost:8000 > /tmp/cloudflared.log 2>&1 < /dev/null &
```

발급된 공개 주소 확인 (10초 정도 기다린 뒤):

```bash
grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' /tmp/cloudflared.log | head -1
```

**이 주소를 참가자에게 공지한다.**

⚠️ **터널을 껐다 켜면 주소가 바뀐다.** 대회 기간 내내 `cloudflared` 프로세스를 죽이지 말 것.
주소가 바뀌면 참가자에게 안내한 링크가 전부 무효가 된다.

⚠️ 외부 공개로 운영할 때는 `.env`에 다음을 설정한다 (설정 후 `docker compose up -d web`으로 반영):

```
SESSION_SECRET=<무작위 문자열로 반드시 교체>
SESSION_HTTPS_ONLY=true
```

`SESSION_HTTPS_ONLY=true`인 상태에서는 터널을 거치지 않고 `http://localhost:8000`으로 직접
접속하면 로그인이 되지 않는다(브라우저가 Secure 쿠키를 저장하지 않음). 로컬에서 관리 작업을
할 때는 터널 주소로 접속하거나, 잠시 `false`로 되돌린다.

배경과 GitHub Pages 검토 결과는
[deployment-public-access.md](../specs/001-online-virtual-evaluation/deployment-public-access.md) 참고.

## 종료

```bash
pgrep -f -- '-m [w]orker\.run' | xargs -r kill
```

```bash
pgrep -f '[c]loudflared tunnel' | xargs -r kill
```

> 평가 중일 때 끄면 그 제출이 `running`에 갇힌다 — §1-1의 경고 참고. 먼저 확인하고 끈다.

```bash
docker compose down
```

⚠️ `pkill -f 'worker.run'`을 쓰지 말 것. 그 명령을 실행한 셸 자신의 명령줄에도 `worker.run`이라는
문자열이 들어 있어 **자기 자신까지 함께 죽는다** (2026-07-26 실제 발생). 위처럼 `pgrep`의 대괄호
표기(`[w]orker`)로 PID를 먼저 찾아 종료하면 안전하다.

## DB 결과 조회 및 로그 확인

모든 명령어는 **WSL Ubuntu 터미널**에서 실행한다 (VSCode WSL 통합이 권장됨).

### DB 결과 조회

**PostgreSQL 직접 접속:**

```bash
docker compose exec db psql -U drleader drleader
```

그 다음 SQL 쿼리 실행:

```sql
-- 최근 제출 10건 (상태 포함)
SELECT id, team_id, status, submitted_at FROM submissions ORDER BY submitted_at DESC LIMIT 10;

-- 평가 결과 조회
SELECT * FROM evaluation_results ORDER BY submission_id DESC LIMIT 10;

-- 제출과 평가 결과 함께 보기
SELECT s.id, s.team_id, s.status, e.finish_status, e.lap_time_seconds, e.off_track_count
FROM submissions s
LEFT JOIN evaluation_results e ON s.id = e.submission_id
ORDER BY s.submitted_at DESC LIMIT 10;
```

**Python으로 조회 (권장):**

```bash
cd /mnt/c/Users/jjh03/spg_deepracer_leaderboard
source .venv/bin/activate
python << 'EOF'
from app.db import SessionLocal
from app.models import Submission, EvaluationResult

db = SessionLocal()
results = (
    db.query(Submission, EvaluationResult)
    .join(EvaluationResult, Submission.id == EvaluationResult.submission_id, isouter=True)
    .order_by(Submission.submitted_at.desc())
    .limit(10)
    .all()
)

print("=== 최근 제출 10건 ===\n")
for submission, eval_result in results:
    print(f"제출ID: {submission.id}, 팀ID: {submission.team_id}, 상태: {submission.status.value}")
    if eval_result:
        print(f"  완주: {eval_result.finish_status.value}, 랩타임: {eval_result.lap_time_seconds}초, 이탈: {eval_result.off_track_count}회")
    print()
db.close()
EOF
```

### 백엔드 서버 로그

| 로그 타입 | 확인 방법 |
|---|---|
| **웹앱 컨테이너** | `docker compose logs -f web --tail 50` |
| **PostgreSQL** | `docker compose logs -f db --tail 50` |
| **워커 실행 로그** | `tail -f /tmp/worker.log` |
| **DRFC 시뮬 로그** (평가 실패 원인) | `cat storage/eval_logs/{제출ID}.log` |

## 평가 결과와 로그는 어디에 저장되나

평가 1건이 돌면 생기는 데이터의 위치는 다음과 같다.

| 데이터 | 저장 위치 | 어디서 확인 |
|---|---|---|
| **평가 결과**(완주 여부, 랩타임, 이탈 횟수) | PostgreSQL `evaluation_results` 테이블 | 웹: 리더보드, 참가자 제출 화면, 관리자 시즌 상세 |
| **평가 영상** | `storage/videos/{시즌}/{팀}/{제출ID}.mp4` | 웹: 리더보드의 "영상 보기" (`/media/videos/...`) |
| **원본 metrics json** (DRFC가 만든 것) | `storage/metrics/{시즌}/{팀}/{제출ID}.json` | 파일 직접 열람 (재파싱·검증용) |
| **시뮬레이션 로그** (robomaker/rl_coach) | `storage/eval_logs/{제출ID}.log` | 파일 직접 열람 (실패 원인 추적용) |
| **워커 진행 로그** | `/tmp/worker.log` (`run_worker.sh` 리다이렉트 대상) | `tail -f /tmp/worker.log` |
| **오류 사유** (참가자에게 보여줄 요약) | `submissions.error_message` 컬럼 | 웹: 참가자 제출 화면, 관리자 시즌 상세 |

몇 가지 알아둘 점:

- **DRFC 자체는 시뮬레이션 로그를 디스크에 남기지 않는다** (`DR_ROBOMAKER_MOUNT_LOGS=False`).
  로그는 Swarm 서비스가 살아있는 동안만 `docker service logs`로 볼 수 있고, 평가가 끝나
  스택을 내리면 사라진다. 그래서 워커가 스택을 내리기 **전에** `storage/eval_logs/`로 받아둔다.
  DRFC 쪽 로그를 디스크에도 남기고 싶으면 `system.env`의 `DR_ROBOMAKER_MOUNT_LOGS=True`로 바꾸면
  `~/deepracer-for-cloud/data/logs/`에 쌓인다(용량 주의).
- MinIO(DRFC의 S3)에도 원본이 남지만(`s3://bucket/{prefix}/metrics/evaluation/*.json`),
  모델 폴더는 다음 평가 때 덮어써지고 영상은 **고정 키 하나를 계속 덮어쓴다**. 그래서 우리 쪽
  `storage/`로 복사해두는 것이 실제 보관본이다.
- `/tmp/worker.log`는 WSL을 재시작하면 사라진다. 오래 보관하려면 `run_worker.sh` 실행 시
  리다이렉트 경로를 `storage/` 아래로 바꾼다.

## 백업 대상

| 대상 | 내용 |
|---|---|
| PostgreSQL | 시즌·팀·계정·제출·평가결과 메타데이터. `docker compose exec db pg_dump -U drleader drleader > backup.sql` |
| `storage/models/` | 참가자가 제출한 모델 원본 |
| `storage/videos/` | 평가 영상 (리더보드에서 재생) |
| `storage/metrics/` | 원본 metrics json (결과 검증·재파싱용) |
| `storage/eval_logs/` | 시뮬레이션 로그 (실패 원인 추적용, 용량 크면 주기적으로 정리 가능) |

DB와 `storage/`는 **반드시 함께** 백업/복원해야 한다. 한쪽만 복원하면 리더보드 기록과 실제 영상 파일이 어긋난다.

### 자동 백업 (scripts/backup.sh)

수동으로 기억해서 돌리는 백업은 결국 안 돌아간다. 아래 스크립트가 DB 덤프와 `storage/` 압축을
한 번에 하고, 무결성 검사와 오래된 백업 정리까지 수행한다.

```bash
bash scripts/backup.sh
```

기본 동작
- **원본: 클라우드 서버**(`ubuntu@15.164.198.36`의 `~/drleader`). 이 스크립트는 노트북에서 돌면서
  SSH로 서버의 DB와 `storage/`를 끌어온다.
- 저장 위치: `/mnt/c/Users/jjh03/drleader-backup` (Windows: `C:\Users\jjh03\drleader-backup`)
- 담는 것: DB 덤프(gzip) + `storage/`(단 `work/`와 `models/` 제외)
- 보관: 최근 14벌, 오래된 것부터 자동 삭제
- 결과 요약은 `STATUS` 파일에, 실행 이력은 `backup.log`에 남는다

**왜 서버에서 직접 돌리지 않고 노트북으로 끌어오나**: 서버가 통째로 사라지는 상황(계정 정지,
인스턴스 삭제, 결제 실패)이 백업이 가장 필요한 순간인데, 백업본이 그 서버 안에 있으면 함께
사라진다. 노트북으로 끌어와 Google Drive에 올려두면 **서버·노트북·Drive 세 곳 중 둘이 죽어도**
데이터가 남는다. 단, 노트북이 오래 꺼져 있으면 백업도 멈추므로 `STATUS` 파일을 가끔 확인한다.

**노트북 자체를 백업하려면** `BACKUP_SOURCE=local bash scripts/backup.sh`. 클라우드 이전 전의
옛 데이터를 받아둘 때 쓴다.

**모델 파일을 기본 제외하는 이유**: 건당 약 250MB라 매일 담으면 디스크가 금방 찬다. 참가자가
원본을 갖고 있어 재업로드가 가능하고, 보존 정책상 최고기록 것만 남는다. 포함하려면
`BACKUP_INCLUDE_MODELS=true bash scripts/backup.sh`.

바꿀 수 있는 값: `BACKUP_DIR`, `BACKUP_KEEP`, `BACKUP_INCLUDE_MODELS`, `BACKUP_SOURCE`,
`BACKUP_REMOTE_HOST`, `BACKUP_REMOTE_DIR`.

⚠️ **서버 IP나 접속 계정이 바뀌면 `BACKUP_REMOTE_HOST`를 반드시 함께 고쳐야 한다.** 안 고치면
백업이 조용히 실패하고, 그 사실은 `STATUS` 파일을 열어봐야만 드러난다.

**백업본을 노트북 밖으로 보내기**: Google Drive 데스크톱 → 설정 → **내 컴퓨터** → 폴더 추가 →
`C:\Users\jjh03\drleader-backup`를 **"Google Drive에 백업"**으로 지정한다. Drive의 가상
드라이브(G:)에는 WSL에서 직접 쓸 수 없어서, 로컬 폴더를 Drive가 올려가게 하는 방향으로 구성한다.
⚠️ "내 드라이브 미러링"을 켜면 Drive 전체가 로컬로 내려와 C 드라이브가 꽉 찬다. 고르지 말 것.

### 자동 실행 등록 (systemd 타이머)

```bash
sudo cp scripts/systemd/drleader-backup.* /etc/systemd/system/ && sudo chmod 644 /etc/systemd/system/drleader-backup.*
```

`chmod`이 필요한 이유: 유닛 파일이 `/mnt/c`(Windows 파일시스템)에 있어 권한이 777로 보이는데,
systemd는 world-writable 유닛을 경고한다.

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now drleader-backup.timer
```

⚠️ 유닛 파일에는 사용자명(`jeonghun`)과 프로젝트 경로가 들어 있다. **다른 PC로 옮기면 그 환경에
맞게 고쳐야 한다.**

확인:

```bash
systemctl list-timers drleader-backup.timer
```

매일 04:00에 돈다. **노트북이 꺼져 있어 걸렀던 실행은 다음 부팅 직후 자동으로 따라잡는다**
(`Persistent=true`). 24시간 켜두는 서버가 아니라 이 설정이 핵심이다.

수동 실행과 로그 확인:

```bash
sudo systemctl start drleader-backup.service && journalctl -u drleader-backup.service -n 30
```

### 복원 절차

⚠️ **운영 DB에 그대로 덮어쓰기 전에, 반드시 별도 DB에 먼저 복원해 내용을 확인한다.**

1. 검증용 DB에 복원해 건수를 확인한다.

```bash
docker compose exec -T db psql -U drleader -d postgres -c "CREATE DATABASE drleader_restore_test;"
```

```bash
gunzip -c ~/drleader-backup/db_YYYY-MM-DD.sql.gz | docker compose exec -T db psql -U drleader -d drleader_restore_test
```

```bash
docker compose exec -T db psql -U drleader -d drleader_restore_test -c "SELECT (SELECT count(*) FROM teams) AS teams, (SELECT count(*) FROM submissions) AS submissions;"
```

2. 내용이 맞으면 운영 DB를 교체한다. **웹과 워커를 먼저 멈춘 뒤** 진행한다.

```bash
docker compose exec -T db psql -U drleader -d postgres -c "DROP DATABASE drleader;" -c "CREATE DATABASE drleader;"
```

```bash
gunzip -c ~/drleader-backup/db_YYYY-MM-DD.sql.gz | docker compose exec -T db psql -U drleader -d drleader
```

3. `storage/`도 같은 날짜 것으로 함께 복원한다.

```bash
tar -xzf ~/drleader-backup/storage_YYYY-MM-DD.tar.gz -C /mnt/c/Users/jjh03/spg_deepracer_leaderboard
```

4. 검증용 DB를 정리한다.

```bash
docker compose exec -T db psql -U drleader -d postgres -c "DROP DATABASE drleader_restore_test;"
```

## 저장 위치 — `/mnt/c` vs WSL ext4 (2026-07-26 검토, 현행 유지)

`storage/`(모델·영상·로그)는 리포지토리 아래, 즉 Windows 드라이브(`/mnt/c`)에 있다. WSL 내부 ext4로
옮기는 안을 검토했고, **대회 기간에는 현행을 유지**하기로 했다.

| | 현행: `/mnt/c/...` (Windows 파일시스템) | 대안: WSL ext4 (예: `/home/<user>/drleader-storage`) |
|---|---|---|
| 여유 공간 | 33 GB (C 드라이브 87% 사용) | 933 GB |
| 파일 I/O 속도 | 느림 — WSL이 9p/DrvFs로 우회 접근. 250MB 모델 압축 해제·복사에서 체감된다 | 네이티브 ext4, 훨씬 빠름 |
| Windows에서 접근 | 탐색기로 바로 열림 | `\\wsl$\Ubuntu-22.04\home\...` 경로로 접근 가능(되긴 하지만 한 단계 번거롭다) |
| WSL 배포판 삭제 시 | **데이터 남음** | **데이터 소실** (`wsl --unregister`는 되돌릴 수 없다) |
| 다른 노트북으로 이전 | 폴더 복사로 끝 | `tar -czf`로 묶어 옮기거나 `wsl --export` (한 단계 추가) |
| 디스크 반환 | 파일을 지우면 즉시 반환 | VHDX가 자동 축소되지 않아 파일을 지워도 가상 디스크 크기는 그대로(수동 compact 필요) |
| 파일 권한 | 전부 `777`로 보임(메타데이터 표현 제한) | 정상 동작 |

**판단**: 보존 정책(평가 직후 최고기록 외 파일 삭제 — [ux-improvements.md](../specs/001-online-virtual-evaluation/ux-improvements.md) §2-5-2)을
적용하면 시즌당 약 2.6GB로 수렴해 33GB로 충분하다. 경로를 바꾸려면 웹 컨테이너 볼륨 마운트, 워커의
`STORAGE_DIR`, 백업 절차를 동시에 맞춰야 하고 서비스 중단이 필요하므로 참가자가 쓰는 중에 할 이유가 없다.
**성능이 문제가 되거나 용량이 다시 빠듯해지면 시즌 종료 후 이전한다.**

**어느 쪽이든 다른 노트북 이전은 문제없다.** 옮겨야 하는 것은 저장 위치와 무관하게 세 가지로 동일하다 —
① `storage/` 디렉터리 ② PostgreSQL 데이터(`pg_dump`) ③ `.env`. 절차는
[gpu-server-migration.md](../specs/001-online-virtual-evaluation/gpu-server-migration.md) 참고.

## 참가자 제출 형식 안내

참가자에게 반드시 공지해야 할 내용:

> **DRFC에서 모델을 내보낸 뒤** 그 폴더를 압축해서 제출하세요.
>
> ```bash
> cd ~/deepracer-for-cloud
> aws s3 sync s3://$DR_LOCAL_S3_BUCKET/$DR_LOCAL_S3_MODEL_PREFIX/model/ ./my-model/ $DR_LOCAL_PROFILE_ENDPOINT_URL
> tar -zcvf rl-deepracer-sagamer.tar.gz ./my-model
> explorer.exe .
> 
> ```
>
> - 형식: `.zip` 또는 `.tar.gz` (최대 500MB)
> - 압축 안에 `model_metadata.json`과 체크포인트 파일들이 있어야 합니다.
> - **평가는 학습 중 성적이 가장 좋았던 체크포인트(best)로 진행됩니다.** 위 명령으로 `model/`
>   폴더를 통째로 내보내면 그 정보(`deepracer_checkpoints.json`)가 함께 들어갑니다. 체크포인트를
>   골라 담거나 이 파일을 빼면 평가할 수 없습니다.
> - 기록은 **3바퀴 합계 시간**입니다. 3바퀴를 모두 완주해야 순위에 오릅니다.
> - ⚠️ **MinIO 데이터 폴더를 직접 압축하면 안 됩니다.** MinIO는 오브젝트를
>   "폴더 + `xl.meta`" 형태로 디스크에 저장하기 때문에, 그 폴더를 그대로 압축하면
>   파일 이름은 그럴듯해 보여도 실제 모델 데이터가 들어가지 않습니다.
>   반드시 위처럼 `aws s3 sync`로 내보낸 뒤 압축하세요.
> - AWS DeepRacer 콘솔에서 내려받은 모델 파일도 형식이 달라 지원하지 않습니다.

실제 테스트에서 MinIO 데이터 폴더를 압축해 올리는 실수가 확인되어, 워커가 이 경우를
자동으로 감지해 위 해결 방법을 오류 메시지로 안내한다. 그래도 참가자 안내문에 미리
적어두는 편이 문의를 줄인다.

## 알려진 제약 / 주의사항

- **평가 1건당 약 10분** (GPU 없는 노트북 기준). 대기열이 밀리면 그만큼 순차적으로 늘어난다.
- 평가는 DRFC의 `run.env` 설정을 그대로 쓴다. 트랙(`DR_WORLD_NAME`)이나 바퀴 수
  (`DR_EVAL_NUMBER_OF_TRIALS`)를 바꾸려면 DRFC의 `run.env`를 직접 수정한다. 앱의 시즌 설정에
  적어둔 트랙 이름은 화면 표시용이며, 실제 평가 트랙을 바꾸지는 않는다 — 시즌을 새로 열 때
  둘이 일치하는지 운영자가 확인해야 한다.
- 워커가 평가 중 죽으면 해당 제출은 "평가중"에 멈춘다. 워커를 다시 시작하면 일정 시간이 지난
  건을 자동으로 대기열에 되돌린다.
- **평가 영상(MP4)이 261바이트로 비어 있다.** DRFC의 영상 편집 노드가 `/agent/mp4_video_metrics`
  ROS 서비스 호출에 실패해 프레임이 하나도 쌓이지 않는 환경 문제로, 이 플랫폼과 무관하게
  이전부터 있던 증상이다(관련 [DRFC 이슈 #67](https://github.com/aws-deepracer-community/deepracer-for-cloud/issues/67)).
  리더보드는 영상이 없으면 "—"로 표시하므로 순위·기록에는 영향이 없다. 원인을 해결하면
  그 이후 평가부터 코드 변경 없이 영상이 자동으로 붙는다.
- 관리자 화면의 "오늘 완료 카운트"는 지정한 값이 그 시점 기준으로 맞춰지는 **보정값**이다.
  지정한 뒤에 완료되는 평가는 그대로 누적되므로 하루 한도는 계속 정상 동작한다.
- GPU 서버로 옮기는 절차는 [gpu-server-migration.md](../specs/001-online-virtual-evaluation/gpu-server-migration.md) 참고.
