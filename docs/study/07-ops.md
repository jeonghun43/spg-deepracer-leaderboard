# 7단계. 운영과 배포 — Docker, 볼륨, 그리고 인터넷 공개

> 이 단계의 목표: **"내 노트북에서만 도는 프로그램"을 "누구나 접속하는 서비스"로 만드는 과정**을 이해하는 것.
> 그리고 이 프로젝트의 가장 특이한 구조 — **웹은 컨테이너, 워커는 호스트** — 가 왜 필요했고
> 어떤 장애를 낳았는지 아는 것.

---

## 0. 컨테이너란 무엇인가

### 무엇을(What)

**[쉬움]**
프로그램을 **도시락통**에 담는 것이다.
- 파이썬 버전, 라이브러리, 설정… 필요한 걸 **전부 통 안에** 넣는다
- 다른 컴퓨터로 통째로 옮겨도 안에 있는 건 그대로다
- 내 노트북에 파이썬이 없어도 통 안에는 있다

"내 컴퓨터에서는 되는데요?" 문제를 없애는 도구다.

**[전공]**
컨테이너는 **가상 머신이 아니다.** 호스트 커널을 공유한다.

| | 가상 머신 | 컨테이너 |
|---|---|---|
| 격리 수단 | 하이퍼바이저 (하드웨어 가상화) | 커널 기능 (namespace + cgroup) |
| 게스트 OS | 있음 (수 GB) | **없음** (커널 공유) |
| 부팅 | 수십 초 | **수백 ms** |
| 오버헤드 | 큼 | 거의 없음 |

**핵심 리눅스 커널 기능:**
- **namespace**: PID, 네트워크, 마운트, 유저 등을 **격리**한다.
  컨테이너 안에서 `ps`를 치면 자기 프로세스만 보인다
- **cgroup**: CPU/메모리 사용량을 **제한**한다
- **union filesystem**(overlayfs): 이미지 레이어를 겹쳐 하나의 파일시스템처럼 보이게 한다

**이미지 vs 컨테이너:**
```
이미지(image)     = 클래스, 설계도, 읽기 전용    ← Dockerfile로 만든다
컨테이너(container) = 인스턴스, 실행 중인 것       ← 이미지에 쓰기 가능 레이어를 얹은 것
```
같은 이미지로 컨테이너를 100개 띄울 수 있다.

---

## 1. `Dockerfile` — 이미지 만들기

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app app
COPY migrations migrations
COPY alembic.ini .

RUN mkdir -p storage/models storage/videos

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```

**16줄인데 배울 게 많다.** 한 줄씩 본다.

### 1-1. `FROM python:3.12-slim`

**베이스 이미지 선택**. 파이썬이 이미 설치된 리눅스에서 시작한다.

| 태그 | 크기 | 특징 |
|---|---|---|
| `python:3.12` | ~1GB | 빌드 도구, git 등 전부 포함 |
| `python:3.12-slim` | ~150MB | **최소한의 데비안** ← 선택됨 |
| `python:3.12-alpine` | ~50MB | musl libc. **C 확장 호환성 문제** 가능 |

**왜 slim인가?**
- 이미지가 작으면 빌드/전송/시작이 빠르다
- **공격면이 작다.** 안 쓰는 패키지에 취약점이 있어도 영향 없음
- alpine은 `psycopg2-binary` 같은 C 확장에서 **미리 컴파일된 wheel을 못 쓰고** 직접 빌드해야 한다.
  빌드 시간이 몇 배로 늘고 실패 위험도 있다

**`3.12` 고정**: `python:latest`를 쓰면 어느 날 3.13이 되어 갑자기 깨진다.
**버전을 고정하는 것이 재현 가능한 빌드의 기본이다.**

> **[전공] 더 엄격하게 하려면** 다이제스트까지 고정한다:
> `FROM python:3.12-slim@sha256:abc...`
> 같은 태그도 재빌드되면 내용이 바뀔 수 있기 때문. 소규모에선 과잉.

### 1-2. **레이어 캐시 — `COPY requirements.txt`가 먼저인 이유**

```dockerfile
COPY requirements.txt .            # ← 먼저
RUN pip install --no-cache-dir -r requirements.txt
COPY app app                        # ← 나중
```

**[쉬움]**
케이크를 만들 때 **밑에서부터** 쌓는다.
위층만 바꾸고 싶으면 아래층은 그대로 두면 된다.
근데 **아래층을 바꾸면 위층을 전부 다시 만들어야 한다.**

**[전공]**
Docker는 각 명령을 **레이어**로 만들고 캐시한다.
어떤 레이어가 바뀌면 **그 이후 레이어는 전부 무효화**된다.

**만약 순서가 반대라면:**
```dockerfile
COPY app app                    # 코드를 먼저 복사
RUN pip install -r requirements.txt
```
→ `app/main.py`를 한 글자만 고쳐도 **모든 패키지를 다시 설치**한다. 매번 2~3분.

**현재 순서라면:**
→ `requirements.txt`가 안 바뀌면 pip 레이어는 **캐시에서 재사용**. 빌드가 몇 초.

> **원칙: 자주 바뀌는 것을 뒤에, 잘 안 바뀌는 것을 앞에.**
> 이건 모든 Dockerfile에 적용되는 가장 중요한 최적화다.

**`--no-cache-dir`**: pip이 다운로드한 wheel 캐시를 이미지에 안 남긴다.
컨테이너에서는 다시 설치할 일이 없으므로 **수십~수백 MB를 절약**한다.

### 1-3. `COPY`가 선택적인 것 — 무엇이 안 들어갔나

```dockerfile
COPY app app
COPY migrations migrations
COPY alembic.ini .
```

**들어간 것**: `app/`, `migrations/`, `alembic.ini`
**안 들어간 것**: `worker/`, `tests/`, `specs/`, `docs/`, `storage/`, `.venv/`, `.env`

**왜 `worker/`가 없는가?**
워커는 컨테이너에서 안 돈다. **호스트에서 돈다.** (§3에서 자세히)

**왜 `.env`가 없는가?**
**비밀값을 이미지에 굽지 않는다.** 이미지는 레지스트리에 올라가거나 공유될 수 있다.
설정은 실행 시점에 환경변수로 주입한다(12-factor, 1단계 참고).

**왜 `storage/`가 없는가?**
런타임 데이터다. 이미지에 넣으면 컨테이너를 재생성할 때마다 **초기 상태로 돌아간다.**
볼륨으로 마운트한다(§2).

> **[전공] `.dockerignore` 가 있는지 확인해볼 것.**
> 없으면 `COPY app app` 이 `app/__pycache__`까지 복사한다.
> 이미지가 커지고, 호스트에서 만든 `.pyc`가 컨테이너 파이썬과 안 맞을 수 있다.
> ```
> __pycache__/
> *.pyc
> .venv/
> .env
> storage/
> ```

### 1-4. `EXPOSE 8000` — 문서화일 뿐

**흔한 오해: `EXPOSE`가 포트를 여는 것이 아니다.**

실제 포트 공개는 `docker-compose.yml`의 `ports:` 또는 `docker run -p`가 한다.
`EXPOSE`는 **"이 이미지는 8000번을 씁니다"라는 메타데이터**일 뿐이다.

용도: `docker run -P`(대문자)로 자동 포트 할당할 때 참조되고, 사람이 읽는 문서 역할.

### 1-5. **`CMD`에 마이그레이션이 들어있는 것**

```dockerfile
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```

**동작**: 컨테이너가 시작될 때마다 마이그레이션을 먼저 실행하고, 성공해야(`&&`) 서버를 띄운다.

**`--host 0.0.0.0`이 필수인 이유:**
기본값은 `127.0.0.1`(루프백)인데, 컨테이너 안에서 루프백에 바인드하면
**컨테이너 밖에서 접속할 수 없다.** `0.0.0.0`은 "모든 인터페이스"를 뜻한다.

**[전공] 이 배포 방식의 트레이드오프**

**장점**: `docker compose up -d --build` 한 줄로 코드+스키마가 함께 배포된다. 절차가 단순하다.

**단점 1 — 다중 인스턴스에서 위험.**
`web` 컨테이너를 2개로 늘리면 **동시에 `alembic upgrade`를 실행**한다.
alembic은 `alembic_version` 테이블에 락을 걸지 않으므로 충돌 가능.
지금은 1개라 안전하다.

**단점 2 — 실패 시 재시작 루프.**
마이그레이션이 실패하면 `&&` 때문에 uvicorn이 안 뜬다.
`restart: unless-stopped`라 컨테이너가 계속 재시작을 반복한다.
**로그를 안 보면 "왜 안 되지?"만 반복하게 된다.**

**단점 3 — 롤백 불가.**
새 코드가 마이그레이션을 적용한 뒤 문제가 발견되면,
이미지를 되돌려도 **DB 스키마는 앞서 있다.** 구버전 코드가 새 스키마에서 동작한다는 보장이 없다.

> **정석**: 마이그레이션을 별도 잡으로 분리한다.
> ```bash
> docker compose run --rm web alembic upgrade head
> docker compose up -d web
> ```
> **하지만 소규모 단일 인스턴스에서는 현재 방식이 훨씬 실용적이다.**
> 배포 절차를 잊어버릴 여지가 없다는 것 자체가 가치다.

### 1-6. `sh -c`를 쓰는 이유

```dockerfile
CMD ["sh", "-c", "A && B"]
```
`&&`는 셸 문법이다. exec 형식(`["alembic", "upgrade", ...]`)은 셸을 안 거치므로 `&&`를 못 쓴다.

**부작용**: PID 1이 `sh`가 되고 uvicorn이 자식이 된다.
→ `docker stop`이 보내는 SIGTERM을 `sh`가 받고 **자식에게 전달하지 않을 수 있다.**
→ 컨테이너 종료가 10초(기본 타임아웃) 걸리고 강제 종료된다.

**개선**: `exec`를 쓴다.
```dockerfile
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```
`exec`가 셸을 uvicorn으로 **교체**하므로 PID 1이 uvicorn이 되고 시그널을 직접 받는다.
(6단계 `run_worker.sh`의 `exec`와 같은 원리다. **여기엔 없다** — 개선 여지.)

---

## 2. `docker-compose.yml` — 여러 컨테이너 묶기

```yaml
services:
  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: drleader
      POSTGRES_PASSWORD: drleader
      POSTGRES_DB: drleader
    volumes:
      - db_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  web:
    build: .
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql+psycopg2://drleader:drleader@db:5432/drleader
      SESSION_SECRET: ${SESSION_SECRET:-change-me-in-production}
      SESSION_HTTPS_ONLY: ${SESSION_HTTPS_ONLY:-false}
    depends_on:
      - db
    ports:
      - "8000:8000"
    volumes:
      - ./storage:/app/storage

volumes:
  db_data:
```

### 2-1. 서비스 이름이 곧 호스트명

```yaml
DATABASE_URL: postgresql+psycopg2://drleader:drleader@db:5432/drleader
                                                       ^^ 서비스 이름
```

**[쉬움]**
같은 아파트 단지 안에서는 "101동"이라고만 하면 통한다.
Docker가 단지 안에 **자체 주소록(DNS)** 을 만들어 준다.

**[전공]**
compose는 기본 브리지 네트워크를 만들고, 각 서비스를 **서비스 이름으로 DNS 등록**한다.
`web` 컨테이너에서 `db`를 조회하면 컨테이너의 내부 IP가 나온다.

**IP를 직접 쓰면 안 되는 이유**: 컨테이너를 재생성하면 IP가 바뀐다.

### 2-2. **`ports:` — 여기에 중요한 결정이 있다**

```yaml
db:
  ports:
    - "5432:5432"     # ← 호스트에 노출
```

**보통은 DB 포트를 호스트에 노출하지 않는다.** `web`이 내부 네트워크로 접속하면 되니까.
그런데 여기선 노출한다. 왜?

파일 최상단 주석이 답한다:
```yaml
# DB는 호스트 포트로 노출해 호스트에서 도는 워커가 접속할 수 있게 한다.
```

**워커가 컨테이너 밖(WSL 호스트)에서 돌기 때문이다.**
워커의 `DATABASE_URL`은 `localhost:5432`를 가리킨다.

**보안 함의:**
`"5432:5432"` 는 **`0.0.0.0:5432`** 에 바인드한다. 즉 **모든 네트워크 인터페이스**.
같은 네트워크의 다른 기기가 `drleader/drleader`로 접속할 수 있다.

**개선:**
```yaml
ports:
  - "127.0.0.1:5432:5432"      # 루프백에만 바인드
```
호스트의 워커는 여전히 `localhost:5432`로 접속 가능하고, 외부에서는 못 온다.

> **비밀번호가 `drleader/drleader`라는 점도 짚고 넘어가야 한다.**
> 로컬 전용이면 괜찮지만, Cloudflare Tunnel로 웹을 공개하는 서비스다.
> **터널은 8000번만 노출하므로 DB는 안 뚫린다.** 하지만 같은 Wi-Fi의 기기는 접근 가능하다.
> 대회장이 공용 네트워크라면 **실제 위험**이다.

`web`의 `"8000:8000"`은 의도된 노출이다. Cloudflare Tunnel이 이 포트를 바라본다.

### 2-3. **볼륨 두 종류 — 명명 볼륨 vs 바인드 마운트**

```yaml
db:
  volumes:
    - db_data:/var/lib/postgresql/data     # ← 명명 볼륨 (named volume)

web:
  volumes:
    - ./storage:/app/storage                # ← 바인드 마운트 (bind mount)
```

| | 명명 볼륨 | 바인드 마운트 |
|---|---|---|
| 실제 위치 | Docker가 관리 (`/var/lib/docker/volumes/`) | **내가 지정한 호스트 경로** |
| 호스트에서 접근 | 어렵다 (root 권한 필요) | **쉽다. 그냥 파일** |
| 이식성 | 좋음 | 호스트 경로에 의존 |
| 권한 문제 | 적음 | **UID 불일치 문제 자주 발생** |
| 성능 (macOS/Windows) | 좋음 | **느릴 수 있음** |

**왜 DB는 명명 볼륨인가?**
- PostgreSQL 데이터 디렉터리는 **사람이 직접 볼 일이 없다**
- 권한이 까다롭다 (postgres 유저 소유여야 함)
- Docker가 관리하는 게 안전하다

**왜 storage는 바인드 마운트인가?**
- **호스트의 워커가 같은 파일을 읽어야 한다** ← 이게 결정적 이유
- 업로드된 모델, 영상을 직접 확인/백업하고 싶다

**[쉬움]**
- 명명 볼륨 = 은행 금고. 안전하지만 내가 직접 못 열어본다
- 바인드 마운트 = 내 책상 서랍. 내가 언제든 열어본다

### 2-4. **`./storage:/app/storage` — 4단계의 그 장애가 여기서 나온다**

```
호스트(WSL)                                   컨테이너
/mnt/c/Users/jjh03/spg_deepracer_leaderboard/storage  ←→  /app/storage
```

**같은 디스크 영역, 완전히 다른 경로 이름.**

웹이 `/app/storage/models/1/6/x.tar.gz`를 DB에 저장하면,
워커는 호스트에서 그 경로를 못 찾는다.

**그래서 `app/storage_paths.py`가 존재한다.** (4단계 §6 참고)
```python
"""웹은 `web` 컨테이너 안에서(`/app/storage/...`), 워커는 WSL 호스트에서
(`/mnt/c/.../spg_deepracer_leaderboard/storage/...`) 같은 디렉터리를 bind mount로
공유한다. 그래서 절대 경로를 그대로 DB에 적으면 한쪽에서 만든 경로를 다른 쪽이
찾지 못한다 (2026-07-26 운영 장애: 업로드한 모델을 워커가 못 찾아 평가 실패)."""
```

> **[전공] 이것이 이 프로젝트에서 가장 배울 만한 아키텍처 교훈이다.**
> **"경계를 넘어 전달되는 값은 양쪽에서 같은 의미여야 한다."**
> 절대 경로는 실행 환경에 의존하므로 경계를 못 넘는다.
> 상대 경로 + 각자의 루트 = 경계를 넘는다.
>
> 같은 원리가 적용되는 곳들: URL(상대 vs 절대), 시각(UTC vs 로컬), ID(로컬 시퀀스 vs UUID).

### 2-5. `restart: unless-stopped`

| 정책 | 동작 |
|---|---|
| `no` (기본) | 안 재시작 |
| `on-failure` | 종료 코드가 0이 아니면 재시작 |
| `always` | 항상. **`docker stop`으로 멈춰도 데몬 재시작 시 다시 뜸** |
| `unless-stopped` | 항상. 단 **내가 명시적으로 멈춘 건 그대로 둔다** ← 선택됨 |

**왜 이게 맞나?** 대회 중 노트북을 재부팅해도 서비스가 자동으로 올라온다.
그런데 내가 점검하려고 `docker compose stop`으로 멈춘 걸 마음대로 켜지는 않는다.

### 2-6. `depends_on`의 한계 — 흔한 오해

```yaml
web:
  depends_on:
    - db
```

**`depends_on`은 "db 컨테이너를 먼저 시작한다"만 보장한다.**
**"PostgreSQL이 연결을 받을 준비가 됐다"는 보장하지 않는다.**

PostgreSQL은 프로세스가 뜬 뒤에도 초기화에 몇 초가 걸린다. 그 사이에 `web`이 시작되면:
```
alembic upgrade head
→ could not connect to server: Connection refused
→ && 뒤가 실행 안 됨 → 컨테이너 종료
→ restart: unless-stopped → 재시작
→ 이번엔 db가 준비됨 → 성공
```

**결과적으로 동작한다.** 재시작 정책이 우연히 헬스체크 역할을 하고 있다.

**정석:**
```yaml
db:
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U drleader"]
    interval: 5s
    timeout: 3s
    retries: 10
web:
  depends_on:
    db:
      condition: service_healthy
```

**[전공] "재시작으로 해결되는 문제"는 로그를 지저분하게 만들고 진짜 장애를 숨긴다.**
개선 여지로 기록할 만하다.

### 2-7. `${SESSION_SECRET:-change-me-in-production}`

셸 파라미터 확장 문법. compose가 해석한다.
- 호스트 환경변수 또는 프로젝트 루트의 `.env`에 `SESSION_SECRET`이 있으면 그 값
- 없으면 기본값

**주의: 여기서 읽는 `.env`는 compose가 읽는 것이고,
`app/config.py`의 `env_file=".env"`가 읽는 것과는 다른 경로다.**
컨테이너 안에는 `.env`가 없으므로(COPY 안 함), 컨테이너의 설정은 **오직 `environment:`로만** 온다.
**로컬에서 직접 uvicorn을 띄울 때만 `app/config.py`의 `.env` 읽기가 작동한다.**

이 이중 구조를 이해 못 하면 "`.env`를 고쳤는데 왜 반영이 안 되지?"로 헤맨다.
**답: `docker compose up -d --force-recreate web` 로 컨테이너를 다시 만들어야 한다.**

---

## 3. **웹은 컨테이너, 워커는 호스트 — 이 비대칭의 이유**

```yaml
# 평가 워커(worker/)는 이 compose에 포함하지 않는다.
# dr-start-evaluation이 호스트(WSL Ubuntu)의 DRFC(Docker Swarm)를 직접 호출해야 하므로,
# 워커는 컨테이너가 아니라 호스트에서 직접 실행한다 (README "워커 실행" 참고).
```

### 왜 워커를 컨테이너에 못 넣나

**워커가 하는 일:**
1. `bash run_evaluation.sh` 실행
2. 그 안에서 `source bin/activate.sh` → DRFC 셸 함수 로드
3. `dr-start-evaluation` → **`docker stack deploy`** 호출
4. `docker stack ps`로 폴링
5. `docker service logs`로 로그 수집

**즉 워커는 Docker를 조종한다.** 컨테이너 안에서 Docker를 조종하려면:

| 방법 | 문제 |
|---|---|
| **DinD** (Docker in Docker) | 중첩 컨테이너. Swarm 모드는 특히 까다롭다 |
| **소켓 마운트** (`/var/run/docker.sock`) | **컨테이너가 호스트 전체를 장악할 수 있다** (사실상 root) |
| 호스트에서 실행 (현재) | 간단하고 안전 |

**그리고 더 근본적인 문제:**
DRFC 설치 자체가 `~/deepracer-for-cloud`에 있고,
`run.env`/`system.env`, `~/.aws/credentials`, MinIO 데이터 등이 **호스트 파일시스템에 흩어져 있다.**
전부 마운트해서 컨테이너에 넣는 것보다 **호스트에서 그냥 돌리는 게 압도적으로 단순하다.**

**[쉬움]**
로봇 팔을 조종하는 프로그램을 **상자 안에 넣으면** 팔에 손이 안 닿는다.
그냥 상자 밖에서 돌리는 게 낫다.

### 이 구조가 만드는 결과

| | 웹 | 워커 |
|---|---|---|
| 실행 위치 | 컨테이너 | WSL 호스트 |
| 파이썬 | 이미지 안의 3.12 | `.venv/bin/python` (3.10) |
| DB 접속 | `db:5432` | `localhost:5432` |
| storage 경로 | `/app/storage` | `/mnt/c/.../storage` |
| 설정 주입 | `environment:` | `run_worker.sh`의 `source activate.sh` |
| 재시작 | Docker가 자동 | **수동** |

**주목: 파이썬 버전이 다르다.**
`.venv`는 3.10(`.venv/lib/python3.10`), 컨테이너는 3.12.
같은 `app/` 코드가 **두 버전에서 돌아야 한다.**
3.11+ 전용 문법(`typing.Self` 등)을 쓰면 워커가 깨진다. **알고 있어야 할 제약이다.**

**주목: 워커에는 재시작 정책이 없다.**
워커가 죽으면 아무도 안 살린다. 대회 중이면 평가가 멈춘다.
**개선**: `systemd` 유닛이나 `supervisord`로 관리하거나, 최소한 `while true; do ...; done` 래퍼.

---

## 4. 인터넷 공개 — Cloudflare Tunnel

### 문제

```
[인터넷의 참가자]  →  ???  →  [내 노트북의 localhost:8000]
```

**왜 그냥 안 되나?**
- 노트북은 **사설 IP**(공유기 뒤)를 갖는다. 인터넷에서 직접 못 찾는다
- 공인 IP가 있어도 대개 **동적**이라 바뀐다
- 포트포워딩을 하려면 공유기 설정 권한이 필요하고, **내 노트북이 인터넷에 직접 노출**된다

### 왜 GitHub Pages는 안 되나

memory에 기록된 결론:
> **GitHub Pages**: 정적 파일만 지원, FastAPI+PostgreSQL+로그인 불가능 → 채택 불가

GitHub Pages는 **HTML/CSS/JS 파일을 그냥 내보내는** 서비스다.
서버에서 파이썬이 돌지 않는다. DB도 없다. 로그인 처리도 불가능하다.
**정적 사이트와 동적 서비스의 근본적 차이다.**

### Cloudflare Tunnel의 원리

```
[참가자] --HTTPS--> [Cloudflare 엣지] <==아웃바운드 터널== [내 노트북의 cloudflared]
                                                                  ↓
                                                          localhost:8000
```

**[쉬움]**
집에 손님을 부르는 대신, **내가 카페로 나가서 줄을 연결한다.**
손님은 카페 주소로 오면 되고, 우리 집 주소는 아무도 모른다.

**[전공] 핵심: 연결을 내가 건다(outbound).**
`cloudflared`가 Cloudflare 엣지로 **나가는 연결**을 만들고 유지한다.
방화벽은 나가는 연결을 대개 허용하므로 **포트포워딩이 필요 없다.**
외부에서 내 노트북으로 들어오는 연결은 **하나도 없다.**

**이득:**
- 공인 IP 불필요, 포트포워딩 불필요
- **HTTPS가 공짜** (Cloudflare가 인증서 처리)
- 내 IP가 노출 안 됨
- DDoS 방어를 Cloudflare가 함

**Quick Tunnel의 제약** (memory 기록):
- **재시작할 때마다 주소가 바뀐다** (`afternoon-bears-notify-realm.trycloudflare.com`)
- 동시 요청 200 제한 (Cloudflare 권고 — 개발/테스트용)
- 도메인을 사면 Named Tunnel로 고정 주소를 쓸 수 있다

### `SESSION_HTTPS_ONLY` — 터널과 짝을 이루는 설정

```python
# app/config.py
# Cloudflare Tunnel 등으로 공인 인터넷에 노출할 때만 true로 설정한다.
# true인 상태에서 http://localhost:8000으로 직접 접속하면(터널 없이) 브라우저가
# Secure 쿠키를 저장하지 않아 로그인이 깨지므로, 로컬 전용 운영/테스트 중에는 false로 둔다.
session_https_only: bool = False
```

**왜 터널을 쓸 때 `true`여야 하나?**

터널 구간은 HTTPS지만, **참가자가 실수로 `http://`로 접속하면** 쿠키가 평문으로 흐른다.
`Secure` 플래그가 있으면 브라우저가 **HTTP로는 쿠키를 아예 안 보낸다.**

**왜 로컬 테스트 때 `false`여야 하나?**
`http://localhost:8000`은 HTTPS가 아니므로 `Secure` 쿠키가 저장 안 된다.
→ 로그인해도 다음 요청에 쿠키가 없다 → **로그인 화면 무한 반복.**
증상만 보면 "비밀번호가 틀렸나?" 싶어서 원인 찾기 어렵다.

**[전공] 실무에서 매우 흔한 함정이다.** 주석으로 남겨둔 것이 훌륭하다.

**한 가지 더 필요한 설정 — 프록시 헤더**

터널 뒤에 있으면 uvicorn이 보는 클라이언트 IP는 **Cloudflare의 IP**다.
원래 IP는 `X-Forwarded-For` 헤더에 있다. 프로토콜은 `X-Forwarded-Proto`.

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
```
현재 `Dockerfile`의 `CMD`에는 없다.
**지금은 IP를 안 쓰므로 문제없지만**, 로그에 IP를 남기거나 rate limit을 붙이면 필요해진다.

---

## 5. 디스크 관리 — 175GB 문제

`app/retention.py` docstring:
> 모델 아카이브가 건당 250MB 안팎이라 전부 남기면 시즌 하나로 디스크가 찬다
> (10팀 × 5회/일 × 2주 ≈ 175GB).

### 계산

```
10팀 × 5회/일 × 14일 = 700건
700건 × 250MB = 175,000MB ≈ 171GB
```

**노트북 디스크가 먼저 찬다.** 그것도 대회 중간에.

### 3단계 방어

**1) 평가 직후 즉시 정리** (`worker/run.py` → `prune_finished_team_files`)
```python
finally:
    shutil.rmtree(work_dir, ignore_errors=True)          # 압축 푼 것
    prune_finished_team_files(db, submission_id)          # 최고기록 외 원본
```
→ 팀당 **모델 파일 1개 + 영상 1개**만 남는다. 10팀이면 ~2.5GB.

**2) 시즌 아카이브 시** (`app/season_archive.py`)
```python
for team in season.teams:
    prune_team_files(team, videos_dir)
    if team.account is not None:
        db.delete(team.account)
```

**3) DB 레코드는 남긴다** — retention.py 주석:
> **지우는 것은 파일뿐이고 DB 레코드는 남긴다** — 리더보드의 "제출 횟수"와 이력이 그대로여야 한다.

**[전공] 이 분리가 핵심이다.**
- **파일**: 크다. 최고기록 외에는 가치가 낮다 → 삭제
- **메타데이터**: 작다. 통계와 이력에 필요하다 → 보존

`Submission` 레코드는 수백 바이트다. 700건이어도 1MB 미만.
**"큰 것만 지운다"** 는 정확한 판단이다.

### 안전장치 재확인

```python
# app/retention.py
if best_submission is not None and submission.id == best_submission.id:
    continue                    # 최고기록은 안 지운다
if submission.status.value in ACTIVE_SUBMISSION_STATUSES:
    continue                    # 평가 대기/진행 중인 것은 안 지운다
```

**두 번째가 없으면 워커가 평가하려는 파일을 스스로 지운다.**
그리고 삭제 후 `video_path = None`으로 **깨진 링크를 방지**한다(5단계).

---

## 6. 백업 — 지금 없는 것

**[전공] 이 프로젝트에 명시적 백업 절차가 보이지 않는다.**
`docs/operations.md`에 있을 수 있으니 확인해볼 것.

**무엇을 백업해야 하나?**

| 대상 | 위치 | 중요도 | 방법 |
|---|---|---|---|
| DB | `db_data` 명명 볼륨 | **최상** | `pg_dump` |
| 영상 | `storage/videos/` | 높음 | 파일 복사 |
| metrics 원본 | `storage/metrics/` | 중간 | 파일 복사 |
| 모델 파일 | `storage/models/` | 낮음 | 재제출 가능 |
| 코드 | git | — | 이미 됨 |

**DB 백업 명령:**
```bash
docker exec spg_deepracer_leaderboard-db-1 pg_dump -U drleader drleader > backup_$(date +%F).sql
```

**복원:**
```bash
cat backup_2026-07-26.sql | docker exec -i spg_deepracer_leaderboard-db-1 psql -U drleader drleader
```

**대회 중이라면 최소한 하루 한 번은 받아야 한다.**
DB가 날아가면 **모든 순위와 기록이 사라진다.** 파일은 남아도 의미가 없다.

> **명명 볼륨의 함정**: `docker compose down -v` 를 실행하면 **볼륨이 삭제된다.**
> `-v` 하나로 대회 전체가 날아간다. **`down` 대신 `stop`을 쓰는 습관**을 들이는 것이 안전하다.

---

## 7. 관측성 — 지금 상태

### 로그

**웹**: uvicorn 기본 액세스 로그. `docker compose logs -f web`
**워커**: `logging.basicConfig(level=logging.INFO, ...)` → stdout.
`run_worker.sh`를 그냥 실행하면 터미널에만 남는다.

memory에 `/tmp/worker.log`로 리다이렉트한다는 기록이 있다:
```bash
worker/run_worker.sh > /tmp/worker.log 2>&1 &
```
**`/tmp`는 WSL 재시작 시 비워진다.** 장애 조사 기록으로는 부적절하다.

**개선**: `storage/logs/worker.log` 같은 영속 경로 + 로테이션.
```python
from logging.handlers import RotatingFileHandler
```

### 제출별 시뮬레이션 로그

```
storage/eval_logs/{submission_id}.log
```
실제로 `15.log`, `17.log`, `4.log`가 있다. **이게 가장 유용한 진단 자료다.**
6단계에서 본 `capture_logs`가 만든다.

**아쉬운 점**: 이 로그를 볼 수 있는 화면이 없다. 파일을 직접 열어야 한다.
관리자 화면에 링크 하나 두면 운영이 훨씬 편해진다.

### 없는 것

- 메트릭 (평가 성공률, 평균 소요 시간, 큐 길이 추이)
- 알림 (워커가 죽었을 때 아무도 모른다)
- 헬스체크 연동 (`/healthz`가 있지만 아무도 안 부른다)

**[전공] 소규모라도 "워커가 죽었는지"는 알아야 한다.**
가장 싼 방법: 관리자 대시보드에 "가장 오래된 queued 제출의 대기 시간"을 표시.
30분을 넘으면 워커가 죽은 것이다.

---

## 8. 자가 점검 질문

1. 컨테이너와 가상 머신의 차이를 커널 관점에서 설명하라.
2. 이미지와 컨테이너의 관계는? 같은 이미지로 컨테이너를 여러 개 띄우면?
3. `COPY requirements.txt`가 `COPY app app`보다 먼저인 이유는? 반대면 빌드 시간이 어떻게 되나?
4. `alpine` 대신 `slim`을 고른 이유는?
5. `EXPOSE 8000`이 실제로 하는 일은? 포트를 여는 것은 무엇인가?
6. `--host 0.0.0.0`이 없으면 어떻게 되는가?
7. `CMD`에 마이그레이션을 넣은 것의 장점 1개와 단점 3개는?
8. `sh -c` 때문에 생기는 시그널 문제는? `exec`가 어떻게 해결하는가?
9. `db:5432`가 동작하는 원리는? IP를 직접 쓰면 왜 안 되나?
10. DB 포트를 호스트에 노출한 이유는? 어떤 보안 문제가 있고 어떻게 완화하는가?
11. 명명 볼륨과 바인드 마운트의 차이는? 왜 db는 전자, storage는 후자인가?
12. `./storage:/app/storage` 가 2026-07-26 장애와 어떻게 연결되는가?
13. `depends_on`이 보장하는 것과 보장하지 않는 것은? 지금은 왜 결과적으로 동작하는가?
14. `.env` 파일이 컨테이너 안에는 없는데 설정이 어떻게 전달되는가?
15. 워커를 컨테이너에 넣지 못하는 이유 2가지는?
16. 웹(3.12)과 워커(3.10)의 파이썬 버전 차이가 만드는 제약은?
17. GitHub Pages로 이 서비스를 못 올리는 근본적 이유는?
18. Cloudflare Tunnel이 포트포워딩 없이 동작하는 원리는?
19. `SESSION_HTTPS_ONLY=true`인데 `http://localhost:8000`으로 접속하면 어떤 증상이 나오는가?
20. 175GB 계산을 재현하라. 3단계 방어는 각각 무엇인가?
21. `retention.py`의 안전장치 2개가 없으면 각각 무슨 사고가 나는가?
22. DB 백업이 없으면 대회 중 무엇을 잃는가? `docker compose down -v`가 왜 위험한가?
23. 워커가 죽었는지 지금 어떻게 아는가? 가장 싼 개선책은?

---

## 9. 실험 과제

**실험 A — 레이어 캐시 체감**
```bash
docker compose build web          # 첫 빌드 (몇 분)
# app/main.py에 주석 한 줄 추가
docker compose build web          # 두 번째 (몇 초?)
# requirements.txt에 줄바꿈 하나 추가
docker compose build web          # 세 번째 (다시 몇 분?)
```
**세 번의 시간 차이를 측정하고 이유를 설명하라.**

**실험 B — 컨테이너 안에서 경로 확인**
```bash
docker compose exec web sh
ls /app/storage/models
pwd
python -c "from app.config import settings; print(settings.storage_dir)"
exit
```
그리고 호스트에서:
```bash
ls storage/models
pwd
```
**같은 파일인데 경로가 어떻게 다른가?**

**실험 C — 환경변수 주입 확인**
```bash
docker compose exec web env | grep -E "SESSION|DATABASE"
```
`.env`를 고치고 다시 확인 → 안 바뀐다.
```bash
docker compose up -d --force-recreate web
docker compose exec web env | grep SESSION
```
이제 바뀐다. **왜인지 설명하라.**

**실험 D — depends_on의 한계 재현**
```bash
docker compose down
docker compose up            # -d 없이 로그를 보면서
```
`web`이 처음에 DB 연결 실패로 죽었다가 재시작하는지 로그를 관찰하라.

**실험 E — 네트워크 격리 확인**
```bash
docker compose exec web python -c "import socket; print(socket.gethostbyname('db'))"
```
컨테이너 내부 IP가 나온다. 컨테이너를 재생성한 뒤 다시 실행하면 IP가 바뀌는가?

**실험 F — 백업/복원 리허설**
```bash
docker exec spg_deepracer_leaderboard-db-1 pg_dump -U drleader drleader > /tmp/bk.sql
wc -l /tmp/bk.sql
head -50 /tmp/bk.sql
```
덤프 안에 `INSERT` 문이 있는지, 스키마가 있는지 확인하라.
**복원은 테스트 DB에서만 연습할 것.**

**실험 G — 워커 죽음 감지 쿼리 만들기**
```sql
SELECT id, team_id, submitted_at, now() - submitted_at AS waiting
FROM submissions
WHERE status = 'queued'
ORDER BY submitted_at
LIMIT 1;
```
이 값이 30분을 넘으면 알림을 보내는 스크립트를 상상해보라.

---

→ 다음: [08-crosscutting.md](08-crosscutting.md) — 전체를 관통하는 주제 정리
