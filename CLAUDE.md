# 이 저장소에서 일할 때의 규칙

이 프로젝트는 **실제로 운영 중인 대회 서비스**다. 참가자 기록과 제출 파일은 재현이 불가능하다.
아래는 이 저장소에서 실제로 났던 실수를 막기 위한 규칙이다.

---

## 1. 운영 명령은 지어내지 않는다 — 문서에 적힌 것을 쓴다

**가장 자주 나는 실수다.** 일반적인 관행(`git pull`, `docker compose restart` 등)으로 답하면
이 프로젝트에서는 **틀린 명령**이 된다. 두 서버의 배포 방식이 서로 다르기 때문이다.

| 대상 | 코드를 올리는 방법 | 근거 문서 |
|---|---|---|
| **웹 서버** (Lightsail, `~/drleader`) | **tar를 SSH로 밀어 넣는다.** `git pull` 아님 — git 저장소가 아니다 | [server-access.md](docs/server-access.md) §5 |
| **워커 서버** (EC2, `~/spg-deepracer-leaderboard`) | `git pull` | [worker-server-setup.md](docs/worker-server-setup.md) |

**운영 절차를 답하기 전에 반드시 해당 문서를 먼저 읽는다.** 기억이나 일반론으로 답하지 않는다.
문서에 절차가 없으면 "문서에 없다"고 말하고, 지어내는 대신 문서를 만든다.

주요 절차 문서:

| 하려는 일 | 문서 |
|---|---|
| 웹 서버 접속·배포·복구 | [docs/server-access.md](docs/server-access.md) |
| 평가 서버(EC2) 구축·비용·로그·정리 | [docs/worker-server-setup.md](docs/worker-server-setup.md) |
| 일상 운영·백업·트랙 변경·기록 삭제 | [docs/operations.md](docs/operations.md) |
| 인수인계·장애 대응 | [docs/handover.md](docs/handover.md) |

---

## 2. 배포에서 반복적으로 틀리는 것들

- **`docker compose restart`로는 코드가 안 바뀐다.** `Dockerfile`이 `COPY app app`로 코드를
  이미지에 굽고, prod compose는 소스를 bind mount 하지 않는다(`./storage`만 마운트).
  반드시 **`up -d --build`**.
- **`.env`는 전송 대상에서 제외된다.** 서버의 비밀값을 덮어쓰지 않기 위해서다. 새 설정 키가
  생긴 변경은 **서버의 `.env`를 먼저 고쳐야** 컨테이너가 뜬다.
- **tar 파이프를 PowerShell에서 돌리지 않는다.** PowerShell은 파이프를 텍스트로 취급해
  바이너리 스트림을 깨뜨린다. **WSL 또는 Git Bash**에서 실행하도록 안내한다.
- **`tar xzf`는 덮어쓰기만 하고 지우지 않는다.** 로컬에서 지운 파일은 서버에 남는다.
  (git 방식으로 전환하면 해결된다 — [docs/git-deploy-migration.md](docs/git-deploy-migration.md))

---

## 3. 사용자가 직접 하는 일 — 대신 실행하지 않는다

- **`git commit` / `git push`** — 명령을 **제안만** 하고 절대 직접 실행하지 않는다.
- **서버에 대한 배포·재기동** — 명령을 제시하고, 실행은 사용자가 한다.
- **데이터 삭제** (제출·기록·볼륨·AMI·스냅샷) — 되돌릴 수 없다. 반드시 백업과
  dry-run 목록을 먼저 제시하고 확인을 받는다.

---

## 4. 비밀값 취급

- **`.env` 값을 채팅에 붙여달라고 요청하지 않는다.** 키 **이름**이나 일치 여부(해시 비교)만
  확인한다.
- 사용자가 실수로 자격증명을 붙여넣으면 **폐기·교체 절차를 가장 먼저** 안내한다.
- **평가 서버에 실제 AWS 자격증명을 두지 않는다.** 로컬 `[minio]` 프로파일만 허용
  (`~/.aws/credentials`에 실 키가 있으면 DRFC가 진짜 S3를 호출한다).
- **Postgres 5432를 공개 인터넷에 열지 않는다.** Tailscale 사설망으로만 접근한다
  (`DB_BIND_ADDRESS`).
- 관리자 로그인 경로는 `.env`의 `ADMIN_LOGIN_PATH`로 숨긴다. 새 라우트를 그 아래 붙일 때는
  **`include_in_schema=False`** 를 반드시 준다.

### 4-1. 커밋·푸시 명령을 안내하기 전에 비밀값 검사를 반드시 한다

**왜**: 2026-10-01, 참가자 문서(`docs/participants/01-환경설정.md`)에 붙여 넣은 DRFC 설치 에러
로그 안에 Docker Swarm join 토큰이 들어 있었다. 아무도 모른 채 `develop`에 푸시됐다.
터미널 출력이나 로그를 문서에 붙이는 일은 계속 생긴다. 사용자는 git에 익숙하지 않아 직접
걸러내기 어렵다. 사용자의 요청으로, **검사는 자동 장치(git 훅 등)가 아니라 Claude가 매번 직접 한다.**

**언제**: `git add` / `git commit` / `git push` 명령을 제안하기 **직전**, 매번. 사용자가 "커밋할게",
"올릴게"라고만 해도 같다. 검사를 끝내기 전에는 커밋 명령을 주지 않는다.

**무엇을** — 이번에 올라갈 변경 전체(추가·수정된 파일, 추적 안 되던 새 파일 포함):
- **파일 자체**: `.env`(`.env.example`은 허용), `*.pem`, `*.key`, `id_rsa` 같은 SSH 키,
  `~/.aws/credentials`, `*.p12`. DB 덤프(`*.sql`, `*.sql.gz`)와 백업 압축본에는 계정·비밀번호 해시가 들어 있다.
- **내용**:
  - `SWMTKN-1-…` (Swarm 토큰)
  - `AKIA…` / `ASIA…` / `aws_secret_access_key` (AWS)
  - `-----BEGIN … PRIVATE KEY-----`
  - `ghp_…` / `github_pat_…` (GitHub)
  - `tskey-…` (Tailscale)
  - `xox[bp]-…` (Slack)
  - `sk-…` (API 키)
  - `SESSION_SECRET=` / `WORKER_TOKEN=` / `POSTGRES_PASSWORD=` / `ADMIN_LOGIN_PATH=/_ops/…` 뒤에
    자리표시(`change-me`, `<…>`, `${…}`)가 아닌 실제 값
  - 팀·관리자 비밀번호 발급 결과
- 특히 **로그·터미널 출력·스크린샷 설명을 붙여 넣은 문서**를 의심한다. 비밀값은 대개 거기서 새어 나간다.

**어떻게 보고하나**:
- 검사한 범위와 결과를 한 줄로 말하고 나서 커밋 명령을 준다.
- 찾은 값은 **채팅에 그대로 옮기지 않는다** — 파일·줄 번호와 종류만 말하고 값은 가린다.
- 걸린 것이 있으면 커밋 명령 대신 ① 문서에서 가리기 ② 실제 쓰던 값이면 폐기·재발급 절차를 먼저 안내한다.

**이미 푸시된 뒤에 발견하면**: git 기록 재작성(force push)보다 **그 값을 무효화(재발급)하는 것이 먼저이고
대개 그것으로 충분하다**. 기록에 남은 옛 값은 무효화되면 쓸모가 없다. 2026-10-01 Swarm 토큰 건의 처리 방법은
그 토큰이 나온 노트북에서 `docker swarm join-token --rotate worker`를 실행하는 것이다.

---

## 5. 코드를 고칠 때

- **문서와 코드는 함께 고친다.** `docs/study/*.md`는 실제 소스를 인용하고 있어서, 코드를
  바꾸면 그 인용도 같이 낡는다. 바꾼 함수가 어느 문서에 인용돼 있는지 검색해서 함께 갱신한다.
- **테스트를 돌려서 확인한다.** 저장소의 `.venv`는 WSL(ELF)이라 Windows에서 실행되지 않는다.
  스크래치패드에 임시 venv를 만들어 `requirements.txt`를 설치해 돌린다
  (Windows에서는 `tzdata`도 추가로 필요하다 — 프로젝트 의존성에는 넣지 않는다).
  - 스크래치 venv로 Git Bash에서 돌릴 때 함정이 둘 있다(2026-10-01 실제로 겪음):
    - **`MSYS_NO_PATHCONV=1`을 붙인다.** 안 붙이면 `ADMIN_LOGIN_PATH=/admin/login` 같은 환경변수를
      Git Bash가 `C:/Program Files/Git/admin/login`으로 바꿔 버려 관리자 라우트가 엉뚱한 경로에 붙는다.
    - **화면 확인용으로 export한 환경변수(`ADMIN_LOGIN_PATH`, `DATABASE_URL` 등)를 지운 셸에서 pytest를 돌린다.**
      남아 있으면 `test_admin_access.py`가 실패한다(그 테스트는 비밀 경로가 설정된 상태를 전제로 한다).
- **파일을 고칠 때 줄바꿈(LF/CRLF)을 바꾸지 않는다.** 저장소에 LF 파일과 CRLF 파일이 섞여 있다.
  Windows에서 Python `Path.write_text()`나 `open(..., "w")`로 쓰면 **LF 파일이 CRLF로 통째로 바뀌어**
  diff가 파일 전체가 된다. 2026-10-01에 24개 파일에서 실제로 일어났다. 파이썬으로 고칠 때는
  `open(p, encoding="utf-8", newline="")`로 읽고 써서 원래 줄바꿈을 유지하고, 끝나면 HEAD와 줄바꿈을 비교한다.
- **화면까지 확인할 때는 로컬 데모 데이터를 쓴다.** 로컬에는 평가 워커가 없어 제출이 끝나지 않는다.
  `scripts/dev_seed_video_demo.py`가 평가가 끝난 상태(최고기록·직전 제출 영상, 탈선 횟수, 데모 팀 2개,
  관리자)를 DB에 넣는다. 사용법은 그 파일 머리말에 있다. 운영 환경(`ADMIN_LOGIN_PATH` 설정됨)에서는 스스로 멈춘다.
- **문서는 "왜"를 남긴다.** 이 저장소의 문서는 결론만이 아니라 **그 선택을 한 이유와 실제로
  겪은 사고**를 적는 형식이다. 고칠 때도 그 형식을 지킨다.
