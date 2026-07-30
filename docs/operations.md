# 운영 가이드 (온라인 평가 플랫폼)

현재 구성: **Windows 노트북 + WSL2 Ubuntu**. DB와 웹 앱은 Docker로, 평가 워커는 WSL 호스트에서 직접 실행한다
(워커가 호스트의 DRFC Docker Swarm을 제어해야 하므로 컨테이너 안에 두지 않는다).

## 사전 준비 (최초 1회)

```bash
cd /mnt/c/Users/jjh03/spg_deepracer_leaderboard
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

DRFC(`~/deepracer-for-cloud`)는 이미 설정되어 있다고 가정한다.

## 기동 절차

### 1. DB + 웹 앱 기동

```bash
docker compose up -d
```

최초 1회는 관리자 계정도 만든다.

```bash
docker compose exec web python -m app.seed admin <원하는_비밀번호>
```

### 2. 평가 워커 기동

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

### 3. 외부 공개 (Cloudflare Tunnel)

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
kill $(pgrep -f '[w]orker.run')
```

```bash
kill $(pgrep -f '[c]loudflared tunnel')
```

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
- 저장 위치: `/mnt/c/Users/jjh03/drleader-backup` (Windows: `C:\Users\jjh03\drleader-backup`)
- 담는 것: DB 덤프(gzip) + `storage/`(단 `work/`와 `models/` 제외)
- 보관: 최근 14벌, 오래된 것부터 자동 삭제
- 결과 요약은 `STATUS` 파일에, 실행 이력은 `backup.log`에 남는다

**모델 파일을 기본 제외하는 이유**: 건당 약 250MB라 매일 담으면 디스크가 금방 찬다. 참가자가
원본을 갖고 있어 재업로드가 가능하고, 보존 정책상 최고기록 것만 남는다. 포함하려면
`BACKUP_INCLUDE_MODELS=true bash scripts/backup.sh`.

바꿀 수 있는 값: `BACKUP_DIR`, `BACKUP_KEEP`, `BACKUP_INCLUDE_MODELS`.

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
