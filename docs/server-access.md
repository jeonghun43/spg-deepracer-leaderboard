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

정상이면 세 개가 모두 `Up`이다.

| 서비스 | 하는 일 | 죽으면 |
|---|---|---|
| `caddy` | HTTPS 접수 → 웹으로 전달 | 사이트 접속 불가 |
| `web` | 리더보드·로그인·제출 처리 | 사이트가 502 오류 |
| `db` | 모든 데이터 저장 | 웹도 함께 동작 불가 |

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

서비스 이름을 빼면 세 개 전부 섞어서 보여준다.

```bash
docker compose -f docker-compose.prod.yml logs --tail 100
```

**어떤 로그를 봐야 하나**

| 증상 | 볼 로그 | 찾을 것 |
|---|---|---|
| 사이트가 안 열림 | `caddy` | `certificate obtained`(인증서 정상), `error` |
| 접속은 되는데 오류 화면 | `web` | `Traceback`, `500` |
| 데이터가 이상함 | `db` | `FATAL`, `could not` |

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
있으니 AWS 콘솔에서 인스턴스 상태도 확인한다.

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
docker compose -f docker-compose.prod.yml restart web          # 웹만 재시작
docker compose -f docker-compose.prod.yml up -d                # 전체 복구
free -h && df -h /                                             # 자원 확인
```

매번 긴 명령을 치기 번거로우면 서버에 별칭을 만들어두면 된다.

```bash
echo "alias dr='cd ~/drleader && docker compose -f docker-compose.prod.yml'" >> ~/.bashrc && source ~/.bashrc
```

그러면 `dr ps`, `dr logs -f web`, `dr restart web` 처럼 짧게 쓸 수 있다.
