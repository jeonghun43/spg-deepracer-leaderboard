# 클라우드 서버 접속과 점검 가이드

> 📌 **주소·경로는 [handover.md](handover.md) §0 '내 환경 값' 표가 출처다.** 명령에 `<사용자>`가
> 보이면 본인 Windows 사용자명으로 바꿔서 실행한다. 서버를 새로 만들어 IP가 바뀌었다면 §0을
> 먼저 고치고 이 문서도 함께 갱신한다.

> 웹 서버(AWS Lightsail)에 직접 들어가 상태를 확인하고 문제를 다루는 방법. 리눅스를 잘 몰라도
> 따라 할 수 있게 썼다. 대회 운영 절차는 [handover.md](handover.md), 노트북 쪽 작업은
> [operations.md](operations.md)를 본다.
>
> 최종 갱신: 2026-07-30

---

## 0. 서버 기본 정보

| 항목 | 값 |
|---|---|
| 제공자 | AWS Lightsail (서울 리전) |
| 공인 IP | `15.164.198.36` (고정 IP) |
| 사설망(Tailscale) IP | `100.110.139.82` |
| 접속 계정 | `ubuntu` |
| 서비스 주소 | https://spg-deepracer.doublejeong.com |
| 프로젝트 경로 | `~/drleader` |
| 사양 | 2GB RAM / 2코어 / 58GB SSD (+스왑 2GB) |

여기서 도는 것은 **웹·DB·리버스 프록시 세 개뿐**이다. 평가(DRFC)는 **별도의 AWS EC2 스팟
인스턴스**에서 돈다 — 그쪽 접속·운영은 [worker-server-setup.md](worker-server-setup.md)를 본다.
(2026-08-01 이전에는 운영자 노트북에서 돌았고, 지금 노트북은 예비 워커로만 쓴다.)

---

## 1. 서버에 들어가는 세 가지 방법

### 방법 A. Lightsail 브라우저 콘솔 — **가장 쉽다. 이걸 먼저 써라**

키 파일도, 프로그램 설치도 필요 없다. AWS 계정만 있으면 된다.

1. AWS 콘솔 → **Lightsail** 검색
2. 인스턴스 `drleader` 클릭
3. 오른쪽 위 **"SSH를 사용하여 연결"** 버튼 클릭
4. 브라우저에 검은 터미널 창이 열린다 → 여기서 §2의 명령을 입력

> 인수인계받은 다음 회장은 이 방법만 알아도 충분하다. 키 관리가 필요 없기 때문이다.

### 방법 B. 운영자 노트북(WSL)에서 SSH

노트북에는 이미 접속용 키가 들어 있다(`~/.ssh/id_ed25519`).

```bash
ssh ubuntu@15.164.198.36
```

Windows PowerShell에서 바로 열고 싶다면:

```bash
wsl -d Ubuntu-22.04 ssh ubuntu@15.164.198.36
```

명령 하나만 실행하고 빠져나오려면 뒤에 붙이면 된다.

```bash
ssh ubuntu@15.164.198.36 "cd ~/drleader && docker compose -f docker-compose.prod.yml ps"
```

### 방법 C. Tailscale 사설망으로

같은 Tailscale 계정에 로그인된 기기에서는 사설망 주소로도 붙는다. 공인 IP가 바뀌어도 이 주소는 유지된다.

```bash
ssh ubuntu@100.110.139.82
```

> **새 노트북에서 방법 B·C를 쓰려면** 그 PC의 SSH 공개키를 서버에 등록해야 한다. §6 참고.

---

## 2. 상태 점검 명령어

접속하면 먼저 프로젝트 폴더로 이동한다. **아래 명령 대부분이 이 폴더 안에서 실행된다.**

```bash
cd ~/drleader
```

### 컨테이너가 살아있나

```bash
docker compose -f docker-compose.prod.yml ps
```

정상이면 네 개가 모두 `Up`이다.

| 서비스 | 하는 일 | 죽으면 |
|---|---|---|
| `caddy` | HTTPS 접수 → 웹으로 전달 | 사이트 접속 불가 |
| `web` | 리더보드·로그인·제출 처리 | 사이트가 502 오류 |
| `db` | 모든 데이터 저장 | 웹도 함께 동작 불가 |
| `autopilot` | 평가 서버(EC2) 자동 켜기·디스코드 알림 (2026-10-04 추가, §5-1) | 사이트는 정상이다. 제출이 쌓여도 평가 서버가 **스스로 켜지지 않고**, 알림이 끊긴다 |

`db`는 `(healthy)` 표시까지 나와야 정상이다.

전체 컨테이너를 보려면 (compose 밖의 것도 포함):

```bash
docker ps
```

### 로그 보기

```bash
docker compose -f docker-compose.prod.yml logs --tail 50 web
```

실시간으로 흘려보며 보려면 `-f`를 붙인다. **`Ctrl+C`로 빠져나온다** (서비스가 멈추지 않는다).

```bash
docker compose -f docker-compose.prod.yml logs -f web
```

평가 서버 자동 켜기·디스코드 알림(`autopilot`)의 로그는 이렇게 본다. 1분에 한 번 판단하므로
평소에는 조용하고, 켜기 요청·실패·알림 발송 실패가 있을 때만 줄이 늘어난다.

```bash
docker compose -f docker-compose.prod.yml logs -f autopilot
```

서비스 이름을 빼면 전부 섞어서 보여준다.

```bash
docker compose -f docker-compose.prod.yml logs --tail 100
```

**어떤 로그를 봐야 하나**

| 증상 | 볼 로그 | 찾을 것 |
|---|---|---|
| 사이트가 안 열림 | `caddy` | `certificate obtained`(인증서 정상), `error` |
| 접속은 되는데 오류 화면 | `web` | `Traceback`, `500` |
| 데이터가 이상함 | `db` | `FATAL`, `could not` |
| 제출이 쌓였는데 평가 서버가 안 켜짐 · 디스코드 알림이 안 옴 | `autopilot` | `켜기 요청 실패`, `EC2 상태 조회 실패`, `디스코드 발송 실패`, `AUTOPILOT_INSTANCE_ID가 설정되지 않았습니다` |

### 리소스 확인

```bash
free -h
```

```bash
df -h /
```

메모리는 `available`이 200MB 아래로 떨어지면 위험하고, 디스크는 90%를 넘기면 정리가 필요하다.

### 접속이 실제로 되는지

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://spg-deepracer.doublejeong.com/healthz
```

`200`이 나오면 정상이다.

---

## 3. 문제가 생겼을 때

### 특정 서비스만 재시작

```bash
docker compose -f docker-compose.prod.yml restart web
```

### 전체 재기동

```bash
docker compose -f docker-compose.prod.yml up -d
```

이미 떠 있는 것은 그대로 두고, 죽었거나 설정이 바뀐 것만 다시 만든다. **가장 안전한 복구 명령**이라
어지간한 문제는 이걸로 해결된다.

### 서버 자체를 재부팅해야 할 때

```bash
sudo reboot
```

재부팅 후 **컨테이너는 자동으로 다시 뜬다**(`restart: unless-stopped` 설정). 1~2분 뒤
`docker compose ... ps`로 확인하면 된다.

### 데이터베이스를 직접 들여다보기

```bash
docker compose -f docker-compose.prod.yml exec db psql -U drleader -d drleader
```

`psql` 안에서 쓰는 명령:

```sql
SELECT id, name, status FROM seasons;
SELECT id, name FROM teams ORDER BY id;
SELECT id, team_id, status, submitted_at FROM submissions ORDER BY id DESC LIMIT 10;
```

빠져나올 때는 `\q` 를 입력한다.

### 평가 대기열(큐) 조회

**큐의 정체는 `submissions` 테이블이다.** 별도의 큐 서버나 메시지 브로커는 없고, 상태가
`queued`/`running`인 행이 곧 대기열이다. 관리자 화면에는 큐 목록 페이지가 없으므로 여기서 조회한다.

**① 지금 밀려 있는 것만 보기** — 가장 자주 쓰는 조회다.

```sql
SELECT s.id, t.name AS team, s.status, to_char(s.submitted_at AT TIME ZONE 'Asia/Seoul','MM-DD HH24:MI') AS 제출, to_char(s.started_at AT TIME ZONE 'Asia/Seoul','HH24:MI') AS 시작, s.worker_id FROM submissions s JOIN teams t ON t.id = s.team_id WHERE s.status IN ('queued','running') ORDER BY s.submitted_at;
```

아무것도 안 나오면 큐가 비어 있다는 뜻이다. **배포하기 좋은 시점**이기도 하다(§5 참고).

**② 최근 제출 이력 보기**

```sql
SELECT s.id, t.name AS team, s.status, to_char(s.submitted_at AT TIME ZONE 'Asia/Seoul','MM-DD HH24:MI') AS 제출, s.worker_id, left(coalesce(s.error_message,''), 40) AS 오류 FROM submissions s JOIN teams t ON t.id = s.team_id ORDER BY s.id DESC LIMIT 15;
```

**③ 평가 결과까지 같이 보기**

```sql
SELECT s.id, t.name AS team, r.finish_status, round(r.lap_time_seconds::numeric,3) AS 랩타임, round(r.best_progress_percent::numeric,1) AS 진행률, r.failure_reason FROM submissions s JOIN teams t ON t.id = s.team_id LEFT JOIN evaluation_results r ON r.submission_id = s.id WHERE s.status = 'done' ORDER BY s.id DESC LIMIT 15;
```

**상태값 읽는 법**

| status | 의미 | 이때 볼 것 |
|---|---|---|
| `queued` | 대기 중. 워커가 아직 가져가지 않았다 | 오래 머물면 워커가 죽은 것 |
| `running` | 평가 중 | `worker_id`(누가 잡았는지), `started_at`(언제부터) |
| `done` | 평가가 끝까지 실행됨. **완주 실패도 포함** | `evaluation_results` 테이블 |
| `error` | 파일 문제나 DRFC 실행 실패. 하루 한도에서 제외 | `error_message` |

**`queued`가 쌓인 채 안 줄어들면** 이 서버가 아니라 **평가 서버(EC2)** 쪽 문제다. 이 서버는 제출을
접수만 하고 평가는 EC2 워커가 한다. [worker-server-setup.md](worker-server-setup.md) §8.6의
`journalctl -u drfc-worker`로 워커 로그를 먼저 본다. 스팟이 회수돼 인스턴스가 내려가 있을 수도
있으니 AWS 콘솔에서 인스턴스 상태도 확인한다. 중지돼 있다면 원인 확인은 같은 문서 §8.11을 따른다.

**시각은 UTC로 저장된다.** 위 쿼리의 `AT TIME ZONE 'Asia/Seoul'`이 한국 시간으로 바꿔주는
부분이다. 이걸 빼고 조회하면 9시간 이른 시각이 나오니 놀라지 말 것.

---

## 4. 절대 하면 안 되는 명령

| 명령 | 결과 |
|---|---|
| `docker compose ... down -v` | **`-v`가 DB 볼륨을 지운다. 대회 데이터 전체 소실.** 절대 붙이지 말 것 |
| `docker system prune -a --volumes` | 위와 같은 이유로 데이터가 사라진다 |
| `rm -rf ~/drleader/storage` | 평가 영상·모델 전부 삭제 |
| `rm ~/drleader/.env` | 비밀값이 사라져 서비스가 뜨지 않는다. 백업에도 안 들어 있다 |
| 컨테이너 안에서 파일 수정 | 재시작하면 사라진다. 호스트의 `~/drleader`에서 고치고 재배포해야 한다 |

`down`은 `-v` 없이 쓰면 컨테이너만 내리고 데이터는 남지만, 굳이 쓸 일이 없다.
멈추려면 `stop`, 다시 띄우려면 `up -d`를 쓴다.

---

## 5. 코드를 고친 뒤 서버에 반영하기

노트북에서 코드를 수정했다면, 서버로 보내고 다시 빌드해야 한다.

> ⚠️ **이 서버는 git 저장소가 아니다.** `git pull`은 동작하지 않는다. 평가 서버(EC2)는
> git으로 관리하지만 웹 서버는 아래의 tar 방식이다. 두 서버의 방식이 다르다는 점을 기억할 것.
> 대회 종료 후 git 방식으로 전환하는 절차는 [git-deploy-migration.md](git-deploy-migration.md)에 있다.

**① 먼저 대기열이 비었는지 확인** (대회 기간에만 해당)

```bash
ssh ubuntu@15.164.198.36 "cd ~/drleader && docker compose -f docker-compose.prod.yml exec -T db psql -U drleader -d drleader -c \"SELECT count(*) FROM submissions WHERE status IN ('queued','running');\""
```

`0`이면 바로 진행한다. 웹 컨테이너가 교체되는 몇 초 사이에 **업로드 중이던 제출 1건이 실패할 수 있어서**다.
평가 자체는 노트북 워커가 하므로 웹을 갈아끼워도 진행 중인 평가는 영향받지 않는다.

**② 노트북에서 파일 전송**

```bash
cd /mnt/c/Users/<사용자>/spg_deepracer_leaderboard && tar czf - --exclude=.venv --exclude=storage --exclude=.env --exclude=__pycache__ --exclude=.git . | ssh ubuntu@15.164.198.36 "tar xzf - -C ~/drleader"
```

** 2-2 노트북에서 클라우드 서버에 접속 **
```bash
ssh ubuntu@15.164.198.36
```


**③ 서버에서 다시 빌드·기동**

```bash
cd ~/drleader && docker compose -f docker-compose.prod.yml up -d --build
```

빌드가 도는 동안 기존 컨테이너는 계속 서비스하고, 교체는 몇 초면 끝난다.
DB 스키마가 바뀌는 변경이면 컨테이너가 뜰 때 마이그레이션이 자동으로 적용된다.

**④ 반영됐는지 확인**

```bash
cd ~/drleader && docker compose -f docker-compose.prod.yml ps && docker compose -f docker-compose.prod.yml logs --tail 20 web
```

`web`이 `Up`이고 로그에 `Application startup complete`가 보이면 정상이다.
마지막으로 브라우저에서 `https://spg-deepracer.doublejeong.com`을 **강력 새로고침**(`Ctrl`+`F5`)해서
바뀐 화면이 나오는지 본다 — CSS·JS는 브라우저가 캐시하므로 그냥 새로고침하면 옛 파일이 보일 수 있다.

⚠️ **`.env`는 전송 대상에서 제외돼 있다.** 서버의 비밀값을 실수로 노트북 값으로 덮어쓰지 않기
위해서다. 설정을 바꿔야 하면 서버에서 직접 편집한다: `nano ~/drleader/.env` → 저장 후 `up -d`.

**⚠️ 새 설정 키가 생긴 배포는 `.env`를 먼저 고쳐야 한다.** `.env`가 전송되지 않기 때문에,
코드가 새 키를 요구하면 **서버에서 컨테이너가 뜨지 않는다.** 현재 필수 키는 다음과 같다.

| 키 | 없으면 |
|---|---|
| `POSTGRES_PASSWORD` · `SESSION_SECRET` · `SITE_DOMAIN` | 기동 실패 |
| `ADMIN_LOGIN_PATH` (2026-08-03 추가) | 기동 실패 — 아래 참고 |

평가 서버 자동 켜기·끄기용 키(2026-10-04 추가)는 **없어도 기동은 된다** — 그 기능만 쉰다. 넣는 법은 §5-1.

`ADMIN_LOGIN_PATH`는 **관리자 로그인 폼이 열리는 비밀 경로**다. 값이 없을 때 기본값으로
조용히 넘어가면 관리자 로그인이 다시 공개된 채 배포되므로, 일부러 기동을 막아 즉시 드러나게 했다
([admin-access-hardening.md](../specs/001-online-virtual-evaluation/admin-access-hardening.md)).
서버에서 이렇게 만들어 넣는다.

```bash
python3 -c "import secrets,string;a=string.ascii_lowercase+string.digits;print('ADMIN_LOGIN_PATH=/_ops/'+''.join(secrets.choice(a) for _ in range(14)))" >> ~/drleader/.env
```

넣은 값은 `tail -1 ~/drleader/.env`로 확인해 **북마크해 둔다.** 이 주소를 잊으면 관리자 화면에
들어갈 수 없다(그때는 서버의 `.env`를 다시 열어보면 된다).

🔒 **Caddy 접근 로그(`/data/access.log`)에 이 경로가 평문으로 남는다.** 서버에 SSH로 들어올 수
있는 사람은 어차피 DB도 볼 수 있어 새로 생기는 위험은 아니지만, **로그를 캡처해 공유할 때는
경로를 가려야 한다.**

**워커도 다시 띄워야 하나?** `worker/` 아래 코드를 고쳤을 때만 그렇다. 웹 화면(`app/`)만 고쳤다면
노트북 워커는 건드리지 않아도 된다 — 서버와 워커는 별개 프로세스이고 위 명령은 서버만 바꾼다.

---

## 5-1. 평가 서버 자동 켜기·끄기 설정 (2026-10-04 추가)

웹 서버의 `autopilot` 컨테이너가 1분마다 대기열을 보고, 제출이 있는데 평가 서버(EC2)가 꺼져 있으면
**AWS API로 켠다.** 같은 컨테이너가 사건을 **디스코드 운영 채널로 알린다.** 끄기는 평가 서버가
스스로 한다(평가 서버 쪽 설정은 worker-server-setup.md의 '자동 켜기·끄기' 절). 왜 이렇게 나눴는지는
[worker-auto-start-stop-plan.md](../specs/001-online-virtual-evaluation/worker-auto-start-stop-plan.md) §0에 있다.
평소 운영(스위치, 알림 대응)은 [operations.md](operations.md)의 "평가 서버 자동 켜기·끄기" 절을 본다.

### 웹 서버 `.env`에 넣는 키

| 키 | 비밀값? | 넘겨받는 서비스 | 내용 |
|---|---|---|---|
| `AUTOPILOT_INSTANCE_ID` | 아니다 | `autopilot`, `web` | 대상 평가 서버의 인스턴스 ID(`i-…`). 인스턴스를 새로 만들면 이 값만 바꾼다 |
| `AUTOPILOT_REGION` | 아니다 | `autopilot` | 생략하면 `ap-northeast-2` |
| `AWS_ACCESS_KEY_ID` · `AWS_SECRET_ACCESS_KEY` | **예** | **`autopilot`만** | 켜기 전용 IAM 사용자 `drleader-autopilot`의 액세스 키. 발급 절차는 worker-server-setup.md의 '자동 켜기·끄기' 절 |
| `DISCORD_WEBHOOK_URL` | **예** | **`autopilot`만** | 운영 채널의 웹후크 주소. 아래 "디스코드 웹후크" |

- **왜 AWS 키와 웹후크는 `autopilot`에만 넘기나**: `web`은 인터넷에 열려 있는 유일한 컨테이너다.
  웹이 털려도 그 환경변수에 AWS 키가 없게 하려고, `docker-compose.prod.yml`이 이 키들을 `autopilot`에만
  넘긴다. `web`은 관리자 화면에 "설정됨/설정되지 않음"을 보여 주려고 인스턴스 ID만 받는다.
  그래서 `docker compose exec web env`에 AWS 키가 안 보이는 것이 **정상이다.**
- **왜 키 권한이 "그 서버 하나를 켜는 것"뿐인가**: 키가 새더라도 최악의 결과가 "평가 서버가 켜져서
  요금이 나간다"에 그치게 하려고다. 끄기·만들기·지우기 권한은 없다. **root 계정의 키는 절대 쓰지 않는다.**
- **평가 서버(EC2)에는 이 키들을 넣지 않는다.** 평가 서버는 스스로 `poweroff`해서 꺼지므로 AWS 키가
  필요 없고(CLAUDE.md §4 — 평가 서버에 실제 AWS 자격증명을 두지 않는다), 알림도 웹 서버가 대신 보낸다.
- 키가 모두 비어 있어도 `autopilot` 컨테이너는 뜬다. 로그에 `AUTOPILOT_INSTANCE_ID가 설정되지 않았습니다`만
  남기고 쉰다 — 이 기능을 아직 쓰지 않는 배포가 깨지지 않게 하려고다. 웹후크만 비어 있으면 알림은
  로그로만 남고, 나중에 넣으면 **최근 24시간 안의** 못 보낸 알림만 몰아서 보낸다.

### 처음 켤 때의 순서 — `.env`가 먼저다

`.env`는 tar 전송에서 빠진다(§5). 그래서 **배포보다 서버 `.env` 수정이 먼저다.** 순서가 바뀌면 새
`autopilot`이 키 없이 떠서 아무 일도 하지 않는다(그러다 키를 넣고 `up -d`하면 그때부터 동작한다).

1. 서버에 접속해 `.env`를 연다: `nano ~/drleader/.env`
2. 위 다섯 키를 붙여 넣는다. 형식은 저장소의 `.env.example`에 있다.
   값은 **노트북에서 복사해 서버 편집기에 바로 붙여 넣는다** — 채팅·메모장·문서를 거치지 않는다.
3. 넣었는지는 **값이 아니라 키 이름으로만** 확인한다.

   ```bash
   grep -oE '^(AUTOPILOT_[A-Z_]+|AWS_[A-Z_]+|DISCORD_WEBHOOK_URL)=' ~/drleader/.env
   ```

   다섯 줄이 나오면 된다. 값을 화면에 띄우는 `cat .env`는 쓰지 않는다 — 화면 공유·캡처로 새기 쉽다.
4. §5의 ①~③대로 tar 전송 → `up -d --build`
5. 확인

   ```bash
   cd ~/drleader && docker compose -f docker-compose.prod.yml ps && docker compose -f docker-compose.prod.yml logs --tail 20 autopilot
   ```

   `autopilot`이 `Up`이고, 로그에 `autopilot 시작 (… 디스코드=설정됨)`과 `종료 시 동작 확인: stop (정상)`이
   보이면 된다. `종료 시 동작 확인 실패`가 보이면 IAM 정책의 `DescribeInstanceAttribute` 권한을 본다.
6. 관리자 화면의 **평가 서버 자동화**(`/admin/autopilot`)에서 스위치를 켠다. 1분 안에 디스코드에
   "자동화 스위치 변경"이 오면 웹후크까지 정상이다.

**코드는 그대로이고 `.env`만 바꿨다면** `up -d`면 된다(`--build` 불필요). 컨테이너를 새 환경변수로
다시 만든다. **`restart`로는 `.env`가 다시 읽히지 않는다.**

```bash
cd ~/drleader && docker compose -f docker-compose.prod.yml up -d
```

### 디스코드 웹후크 만들기

웹후크 주소는 **비밀값이다.** 주소를 아는 사람은 누구나 그 채널에 글을 쓸 수 있다(운영자를 사칭한
"서버를 지금 끄세요" 같은 글도 된다). 그래서 이 주소는 서버 `.env` 한 곳에만 둔다.

1. 디스코드에서 운영 채널 이름 옆 톱니바퀴(**채널 편집**) — 또는 **서버 설정** — 로 들어간다
2. **연동** → **웹후크** → **새 웹후크**
3. 이름을 알아보기 쉽게 바꾼다(예: `평가 서버 알림`). 채널이 운영 채널인지 확인한다
4. **웹후크 URL 복사** → 위 "처음 켤 때의 순서" 2번처럼 서버 `.env`의 `DISCORD_WEBHOOK_URL=` 뒤에 바로 붙여 넣는다

🔒 **웹후크 URL을 채팅(Claude 포함)·문서·커밋·이슈·스크린샷에 붙이지 않는다.** 문서에 예시가 필요하면
`<디스코드 채널 설정 → 연동 → 웹후크에서 복사>` 같은 자리표시를 쓴다. 커밋 전 검사(CLAUDE.md §4-1)도
`discord.com/api/webhooks/`를 찾는다.

### 새었을 때 — 폐기하고 다시 만든다

웹후크 주소가 어디에든 붙여졌다면 **지우는 것보다 무효화가 먼저다.** 옛 웹후크를 디스코드에서 삭제하면
그 주소는 즉시 쓸모가 없어진다. 문서·기록에 남은 옛 값은 그 뒤에 천천히 정리하면 된다.

1. 디스코드 **채널 편집 → 연동 → 웹후크**에서 그 웹후크를 **삭제**한다
2. **새 웹후크**를 만들어 URL을 복사한다(위 절차)
3. 서버 `.env`의 `DISCORD_WEBHOOK_URL=` 값을 교체한다: `nano ~/drleader/.env`
4. `cd ~/drleader && docker compose -f docker-compose.prod.yml up -d`
5. 관리자 화면에서 스위치를 한 번 껐다 켜서 새 웹후크로 알림이 오는지 본다

1~3 사이에 생긴 알림은 `디스코드 발송 실패: HTTP 404`로 로그에 남고 보내지 않은 상태로 남아 있다가,
교체 뒤 다음 주기에 나간다(24시간 안의 것만). 관리자 화면의 최근 기록에는 그대로 보인다.

**AWS 키가 새었을 때**도 같은 원리다: IAM 콘솔 → 사용자 `drleader-autopilot` → 보안 자격 증명에서
그 액세스 키를 **비활성화** → 새 키 발급 → 서버 `.env`의 두 값 교체 → `up -d` → 로그에 `켜기 요청 실패`나
`EC2 상태 조회 실패`가 없는지 확인 → 옛 키 삭제.

---

## 6. 새 PC에서 접속할 수 있게 하기 (인수인계용)

다음 회장의 PC에서 SSH로 붙으려면 그 PC의 공개키를 서버에 등록해야 한다.

**① 새 PC에서 키 만들기** (이미 있으면 건너뛴다)

```bash
ssh-keygen -t ed25519 -C "drleader-deploy"
```

**② 공개키 내용 확인**

```bash
cat ~/.ssh/id_ed25519.pub
```

**③ Lightsail 브라우저 콘솔(§1 방법 A)로 접속해 등록**

```bash
echo "여기에-위에서-복사한-공개키-한줄" >> ~/.ssh/authorized_keys
```

> 🔒 **개인키(`id_ed25519`, 확장자 없는 쪽)는 절대 남에게 주거나 어딘가에 올리지 않는다.**
> 공유하는 것은 `.pub`으로 끝나는 공개키뿐이다.

---

## 7. 자주 쓰는 명령 한눈에

```bash
cd ~/drleader                                                  # 프로젝트 폴더로
docker compose -f docker-compose.prod.yml ps                   # 상태 확인
docker compose -f docker-compose.prod.yml logs --tail 50 web   # 웹 로그
docker compose -f docker-compose.prod.yml logs -f autopilot    # 평가 서버 자동 켜기·알림 로그
docker compose -f docker-compose.prod.yml restart web          # 웹만 재시작
docker compose -f docker-compose.prod.yml up -d                # 전체 복구
free -h && df -h /                                             # 자원 확인
```

매번 긴 명령을 치기 번거로우면 서버에 별칭을 만들어두면 된다.

```bash
echo "alias dr='cd ~/drleader && docker compose -f docker-compose.prod.yml'" >> ~/.bashrc && source ~/.bashrc
```

그러면 `dr ps`, `dr logs -f web`, `dr restart web` 처럼 짧게 쓸 수 있다.
