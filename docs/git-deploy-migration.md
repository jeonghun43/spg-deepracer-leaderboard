# 웹 서버 배포를 git 방식으로 바꾸기 (대회 종료 후 작업)

> **지금 하지 말 것.** 이 작업은 운영 중인 서버의 디렉터리를 통째로 바꾼다. 실수하면
> 제출 파일과 평가 영상이 날아가고, 인증서가 재발급되며, 최악의 경우 **DB가 빈 상태로 뜬다.**
> **대회가 완전히 끝나고, 백업을 복원해본 뒤에** 한다.

작성: 2026-08-06

---

## 0. 왜 바꾸나

지금 웹 서버는 git 저장소가 아니다. 노트북에서 **tar를 SSH로 밀어 넣는다**
([server-access.md](server-access.md) §5).

```bash
tar czf - --exclude=... . | ssh ubuntu@15.164.198.36 "tar xzf - -C ~/drleader"
```

이 방식의 문제는 세 가지다.

| 문제 | 설명 |
|---|---|
| **삭제가 반영되지 않는다** | `tar xzf`는 덮어쓰기만 한다. 로컬에서 지운 파일이 서버에 계속 남아 이미지에 구워진다. **가장 실질적인 결함이다** |
| **서버 버전을 알 수 없다** | 무슨 코드가 올라가 있는지 확인할 방법이 없어, [handover.md](handover.md)가 **파일 타임스탬프를 `ls -l`로 보라고** 안내한다. 그게 최선이었다 |
| **롤백이 어렵다** | 노트북에서 옛 상태를 복원해 다시 밀어야 한다 |

**평가 서버(EC2)는 이미 git으로 관리하고 있다**([worker-server-setup.md](worker-server-setup.md) §9.3).
전환하면 두 서버의 방식이 통일된다.

**전환 비용은 거의 없다.** 저장소가 공개(public)라 서버에 인증을 설정할 필요가 없다.

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://github.com/jeonghun43/spg-deepracer-leaderboard
```

`200`이면 공개다. `404`로 바뀌었다면 서버에 **읽기 전용 deploy key**를 등록해야 하고,
그 절차는 이 문서 §7에 있다.

---

## 1. ⚠️ 가장 큰 함정 — 폴더 이름이 곧 볼륨 이름이다

**이것 하나만 알면 이 작업의 위험은 대부분 사라진다.**

Docker Compose는 **compose 파일이 있는 디렉터리 이름**을 프로젝트 이름으로 쓰고, 볼륨 이름
앞에 그것을 붙인다. 지금 서버의 디렉터리가 `~/drleader`이므로 실제 볼륨은 이렇게 생겼다.

```
drleader_db_data       ← 대회 기록 전부 (팀·제출·랩타임)
drleader_caddy_data    ← 발급받은 HTTPS 인증서
drleader_caddy_config
```

**그래서 `~/drleader-new`에서 `docker compose up -d`를 하면** Compose는
`drleader-new_db_data`라는 **새 빈 볼륨**을 만들고, **DB가 텅 빈 채로 서비스가 뜬다.**
리더보드가 백지가 되고, Caddy는 인증서를 새로 받으려다 발급 한도에 걸릴 수 있다.

확인해두자.

```bash
ssh ubuntu@15.164.198.36 "docker volume ls | grep -E 'db_data|caddy'"
```

> **규칙: 폴더 이름이 `drleader`가 아닌 상태에서는 절대 `up -d`를 하지 않는다.**
> 아래 절차는 **이름을 바꾼 뒤에만** 기동하도록 짜여 있다.

한 겹 더 안전하게 하려면 서버 `.env`에 프로젝트 이름을 못박아둔다(§5에서 한다).

```
COMPOSE_PROJECT_NAME=drleader
```

이렇게 하면 폴더 이름이 무엇이든 볼륨이 바뀌지 않는다.

---

## 2. 사전 조건 — 하나라도 아니면 멈춘다

- [ ] 대회가 끝났고, 참가자 제출이 더 이상 들어오지 않는다
- [ ] 백업이 방금 성공했다 — `bash scripts/backup.sh` 후 `STATUS`가 `OK`
- [ ] **그 백업을 격리 DB에 복원해봤다** ([operations.md](operations.md) "백업 대상" 참고).
      복원해본 적 없는 백업은 백업이 아니다
- [ ] 백업본이 Google Drive에도 올라가 있다
- [ ] 서버에 접속된다 — `ssh ubuntu@15.164.198.36`

---

## 3. 서버에 git에 없는 파일이 무엇인지 먼저 확인한다

`~/drleader`에는 저장소에 없는 것들이 섞여 있다. **이것들을 잃지 않는 게 이 작업의 전부다.**

```bash
ssh ubuntu@15.164.198.36 "ls -la ~/drleader && du -sh ~/drleader/storage"
```

옮겨야 하는 것:

| 대상 | 정체 | 크기 |
|---|---|---|
| `.env` | DB 비밀번호·세션 시크릿·워커 토큰·비밀 관리자 경로 | 작음 |
| `storage/` | 제출 모델·평가 영상·metrics | 큼 (GB 단위일 수 있음) |
| 그 외 눈에 띄는 파일 | 손으로 만든 스크립트·메모가 있을 수 있다 | — |

> **DB는 옮기지 않아도 된다.** named volume(`drleader_db_data`)에 있어서 디렉터리와 무관하다.
> **단, §1의 프로젝트 이름 함정에 걸리지만 않으면** 그렇다.

---

## 4. 전환 — 새 폴더에 clone 후 이름을 맞바꾼다

제자리에서 `git init`하지 않는다. **옛 폴더를 그대로 남겨두는 것이 롤백 수단**이기 때문이다.

**① 새 폴더에 clone** (기동하지 않는다)

```bash
ssh ubuntu@15.164.198.36
```

```bash
cd ~ && git clone https://github.com/jeonghun43/spg-deepracer-leaderboard.git drleader-new
```

**② 서비스를 내린다** — `storage/`를 옮기는 동안 쓰기가 있으면 안 된다

```bash
cd ~/drleader && docker compose -f docker-compose.prod.yml down
```

> `down`은 컨테이너만 지운다. **named volume은 남는다** (`-v`를 붙이면 지워진다 — 절대 붙이지 않는다).

**③ `.env`는 복사, `storage/`는 이동**

```bash
cp ~/drleader/.env ~/drleader-new/.env
```

```bash
mv ~/drleader/storage ~/drleader-new/storage
```

`.env`는 작으니 복사해서 옛 폴더에도 남겨둔다(롤백용). `storage/`는 용량이 커서 이동한다 —
되돌릴 때는 반대로 `mv` 하면 된다.

**④ 이름을 맞바꾼다 — 이게 핵심 단계다**

```bash
mv ~/drleader ~/drleader-old && mv ~/drleader-new ~/drleader
```

이제 폴더 이름이 다시 `drleader`라서 **원래 볼륨을 그대로 쓴다.**

**⑤ 기동**

```bash
cd ~/drleader && docker compose -f docker-compose.prod.yml up -d --build
```

---

## 5. 프로젝트 이름 못박기 (권장)

같은 사고가 다시 나지 않게 서버 `.env` 맨 위에 한 줄 넣는다.

```bash
cd ~/drleader && nano .env
```

```
COMPOSE_PROJECT_NAME=drleader
```

넣은 뒤 다시 `up -d`. **볼륨 이름이 그대로인지 반드시 확인한다.**

```bash
docker volume ls | grep -E 'db_data|caddy'
```

`drleader_db_data`가 그대로 보여야 한다. 다른 이름이 새로 생겼다면 **즉시 §6으로 롤백한다.**

---

## 6. 검증 — 눈으로 사이트를 보는 것으로는 부족하다

**① 볼륨이 그대로인가** (가장 중요)

```bash
docker volume ls | grep -E 'db_data|caddy'
```

**② 데이터가 살아 있는가**

```bash
cd ~/drleader && docker compose -f docker-compose.prod.yml exec -T db psql -U drleader -d drleader -c "SELECT (SELECT count(*) FROM teams) AS 팀, (SELECT count(*) FROM submissions) AS 제출, (SELECT count(*) FROM evaluation_results) AS 결과;"
```

전환 전에 세어둔 값과 **정확히 일치**해야 한다. 미리 세어두는 걸 잊지 말 것.

**③ 파일이 살아 있는가**

```bash
du -sh ~/drleader/storage && ls ~/drleader/storage/videos | head
```

**④ 서비스가 정상인가**

```bash
docker compose -f docker-compose.prod.yml ps && curl -s https://spg-deepracer.doublejeong.com/healthz
```

**⑤ 인증서가 재발급되지 않았는가** — 브라우저에서 자물쇠를 눌러 발급일을 본다.
오늘 날짜로 바뀌었다면 캐디가 새 볼륨을 쓴 것이다. 롤백한다.

**⑥ git이 동작하는가**

```bash
cd ~/drleader && git log --oneline -1 && git status --short
```

`git status`가 깨끗해야 한다. 뭔가 나온다면 서버에만 있던 변경이 있었다는 뜻이니
지우기 전에 내용을 확인한다.

---

## 롤백

무엇이든 어긋나면 되돌린다. **옛 폴더가 그대로 있으니 확실하게 돌아갈 수 있다.**

```bash
cd ~/drleader && docker compose -f docker-compose.prod.yml down
```

```bash
mv ~/drleader/storage ~/drleader-old/storage && mv ~/drleader ~/drleader-new && mv ~/drleader-old ~/drleader
```

```bash
cd ~/drleader && docker compose -f docker-compose.prod.yml up -d --build
```

---

## 7. 저장소가 비공개(private)로 바뀐 경우에만

공개 저장소라면 이 절은 건너뛴다.

서버에 **읽기 전용 deploy key**를 만든다. 개인 GitHub 계정 키를 서버에 두지 않는다 —
서버가 털리면 계정 전체가 털린다.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/deploy_key -N "" -C "drleader-web-deploy"
```

```bash
cat ~/.ssh/deploy_key.pub
```

출력된 공개키를 GitHub 저장소 → **Settings → Deploy keys → Add deploy key**에 붙이고,
**"Allow write access"는 체크하지 않는다.**

```bash
printf 'Host github.com\n  IdentityFile ~/.ssh/deploy_key\n  IdentitiesOnly yes\n' >> ~/.ssh/config
```

clone 주소를 SSH 형식으로 쓴다.

```bash
git clone git@github.com:jeonghun43/spg-deepracer-leaderboard.git drleader-new
```

---

## 8. 전환 후의 배포 절차

```bash
ssh ubuntu@15.164.198.36 "cd ~/drleader && git pull && docker compose -f docker-compose.prod.yml up -d --build"
```

바뀌는 것과 그대로인 것:

| 항목 | 전환 전 | 전환 후 |
|---|---|---|
| 코드 전송 | tar 파이프 (WSL/Git Bash 필수) | `git pull` (PowerShell에서도 됨) |
| 삭제한 파일 | 서버에 남는다 | 반영된다 |
| 서버 버전 확인 | 파일 타임스탬프 | `git log --oneline -1` |
| 롤백 | 노트북에서 재전송 | `git checkout <커밋> && up -d --build` |
| `.env` | 전송 안 됨 | **그대로 전송 안 됨** (`.gitignore`에 있음) |
| `storage/` | 전송 안 됨 | **그대로 전송 안 됨** (`.gitignore`에 있음) |
| 빌드 | `up -d --build` | **그대로** |

> **`.env`와 `storage/`가 여전히 git 밖에 있다는 점은 바뀌지 않는다.** 새 설정 키가 생긴
> 배포는 여전히 서버의 `.env`를 먼저 고쳐야 하고, 백업도 여전히 필요하다.

---

## 9. 전환하면 함께 고쳐야 하는 문서

이 작업을 끝냈다면 아래를 같이 갱신한다. **안 고치면 다음 사람이 없는 절차를 따라 한다.**

| 문서 | 고칠 곳 |
|---|---|
| [server-access.md](server-access.md) | §5 "코드를 고친 뒤 서버에 반영하기" — tar 파이프 → `git pull` |
| [handover.md](handover.md) | §3 배포 3단계, "다 했는데 그대로예요" 함정 설명 |
| [../CLAUDE.md](../CLAUDE.md) | §1 표의 "웹 서버" 행 |
| [study/07-ops.md](study/07-ops.md) | §7 배포 절차 그림 |
| 이 문서 | 맨 위에 "완료" 배너를 달고 날짜를 적는다 |
