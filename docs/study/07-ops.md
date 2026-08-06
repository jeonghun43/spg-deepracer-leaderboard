# 7단계. 운영과 배포 — Docker, 볼륨, Caddy, 그리고 두 개의 compose 파일

> 이 단계의 목표: **"내 노트북에서만 도는 프로그램"을 "누구나 접속하는 서비스"로 만드는 과정**을 이해하는 것.
> 그리고 **개발용과 운영용 설정이 왜 달라야 하는지**, **필수 환경변수를 왜 기동 실패로 강제하는지**.

---

## 0. 컨테이너란 무엇인가

### 무엇을(What)

**[쉬움]**
프로그램을 **도시락통**에 담는 것이다.
- 파이썬 버전, 라이브러리, 설정… 필요한 걸 **전부 통 안에** 넣는다
- 다른 컴퓨터로 통째로 옮겨도 안에 있는 건 그대로다

"내 컴퓨터에서는 되는데요?" 문제를 없애는 도구다.

**[전공]**
컨테이너는 **가상 머신이 아니다.** 호스트 커널을 공유한다.

| | 가상 머신 | 컨테이너 |
|---|---|---|
| 격리 수단 | 하이퍼바이저 (하드웨어 가상화) | 커널 기능 (namespace + cgroup) |
| 게스트 OS | 있음 (수 GB) | **없음** (커널 공유) |
| 부팅 | 수십 초 | **수백 ms** |

**핵심 리눅스 커널 기능:**
- **namespace**: PID, 네트워크, 마운트, 유저 등을 **격리**
- **cgroup**: CPU/메모리 사용량을 **제한** ← `mem_limit: 900m`이 이걸 쓴다
- **union filesystem**(overlayfs): 이미지 레이어를 겹쳐 하나로 보이게

**이미지 vs 컨테이너:**
```
이미지(image)     = 클래스, 설계도, 읽기 전용    ← Dockerfile로 만든다
컨테이너(container) = 인스턴스, 실행 중인 것       ← 이미지에 쓰기 가능 레이어를 얹은 것
```

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

**16줄인데 배울 게 많다.**

### 1-1. `FROM python:3.12-slim`

| 태그 | 크기 | 특징 |
|---|---|---|
| `python:3.12` | ~1GB | 빌드 도구, git 등 전부 포함 |
| `python:3.12-slim` | ~150MB | **최소한의 데비안** ← 선택됨 |
| `python:3.12-alpine` | ~50MB | musl libc. **C 확장 호환성 문제** 가능 |

**왜 slim인가?**
- 이미지가 작으면 빌드/전송/시작이 빠르다 — **Lightsail의 좁은 대역폭에서 특히**
- **공격면이 작다**
- alpine은 `psycopg2-binary` 같은 C 확장에서 **미리 컴파일된 wheel을 못 쓴다**

**`3.12` 고정**: `python:latest`를 쓰면 어느 날 3.13이 되어 갑자기 깨진다.

> **주의: 워커는 `.venv`의 파이썬 3.10을 쓴다.** (`worker/run_worker.sh`의 `exec` 참고)
> **같은 `app/` 코드가 3.10과 3.12에서 모두 돌아야 한다.**
> 3.11+ 전용 문법(`typing.Self`, `ExceptionGroup` 등)을 쓰면 워커가 깨진다.
> **알고 있어야 할 제약이다.**

### 1-2. **레이어 캐시 — `COPY requirements.txt`가 먼저인 이유**

**[쉬움]**
케이크를 **밑에서부터** 쌓는다. 위층만 바꾸고 싶으면 아래층은 그대로 두면 된다.
근데 **아래층을 바꾸면 위층을 전부 다시 만들어야 한다.**

**[전공]**
Docker는 각 명령을 **레이어**로 만들고 캐시한다.
어떤 레이어가 바뀌면 **그 이후 레이어는 전부 무효화**된다.

**순서가 반대라면** `app/main.py`를 한 글자만 고쳐도 **모든 패키지를 다시 설치**한다.

> **원칙: 자주 바뀌는 것을 뒤에, 잘 안 바뀌는 것을 앞에.**

**`--no-cache-dir`**: pip 캐시를 이미지에 안 남긴다. **수십~수백 MB 절약.**

### 1-3. `COPY`가 선택적인 것 — 무엇이 안 들어갔나

**들어간 것**: `app/`, `migrations/`, `alembic.ini`
**안 들어간 것**: `worker/`, `tests/`, `specs/`, `docs/`, `storage/`, `.venv/`, `.env`, `Caddyfile`

**왜 `worker/`가 없는가?** 워커는 컨테이너에서 안 돈다. **다른 기기에서 돈다.** (§4)

**왜 `.env`가 없는가?** **비밀값을 이미지에 굽지 않는다.**
이미지는 레지스트리에 올라가거나 공유될 수 있다.

**왜 `storage/`가 없는가?** 런타임 데이터. 이미지에 넣으면 컨테이너 재생성 시 초기화된다.

> **[전공] `.dockerignore` 가 있는지 확인해볼 것.**
> 없으면 `COPY app app` 이 `app/__pycache__`까지 복사한다.
> 호스트에서 만든 `.pyc`가 컨테이너 파이썬과 안 맞을 수 있다.
> ```
> __pycache__/
> *.pyc
> .venv/
> .env
> storage/
> ```

### 1-4. `EXPOSE 8000` — 문서화일 뿐

**흔한 오해: `EXPOSE`가 포트를 여는 것이 아니다.**
실제 포트 공개는 compose의 `ports:` 가 한다.
`EXPOSE`는 **"이 이미지는 8000번을 씁니다"라는 메타데이터**일 뿐이다.

> 운영 compose는 `ports:` 대신 `expose: - "8000"` 을 쓴다 — **호스트에 안 열고 내부망에만.** (§3)

### 1-5. **`CMD`에 마이그레이션이 들어있는 것**

```dockerfile
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```

**동작**: 컨테이너가 시작될 때마다 마이그레이션을 먼저 실행하고, 성공해야(`&&`) 서버를 띄운다.

**`--host 0.0.0.0`이 필수인 이유:**
기본값은 `127.0.0.1`(루프백)인데, 컨테이너 안에서 루프백에 바인드하면
**컨테이너 밖(Caddy 포함)에서 접속할 수 없다.**

**`--workers` 가 없다는 사실이 중요하다** — uvicorn 워커 프로세스가 **1개**다.
→ 3단계 `admin_lockout.py`가 프로세스 메모리에 카운터를 두는 근거.

**[전공] 이 배포 방식의 트레이드오프**

**장점**: `docker compose up -d --build` 한 줄로 코드+스키마가 함께 배포된다.

**단점 1 — 다중 인스턴스에서 위험.** `web`을 2개로 늘리면 동시에 마이그레이션을 실행한다.
**단점 2 — 실패 시 재시작 루프.** 마이그레이션이 실패하면 uvicorn이 안 뜨고 계속 재시작한다.
**단점 3 — 롤백 불가.** 이미지를 되돌려도 **DB 스키마는 앞서 있다.**

> **2단계 §10에서 본 "확장 후 수축" 원칙이 여기서 실질적 의미를 갖는다.**
> 컬럼 추가(nullable) 마이그레이션은 옛 코드가 무시하므로 롤백해도 안전하다.
> 컬럼 삭제·이름 변경은 롤백하면 즉시 깨진다.

> **정석**: 마이그레이션을 별도 잡으로 분리한다.
> ```bash
> docker compose -f docker-compose.prod.yml run --rm web alembic upgrade head
> docker compose -f docker-compose.prod.yml up -d web
> ```
> **소규모 단일 인스턴스에서는 현재 방식이 실용적이다.**

### 1-6. `sh -c`의 부작용 — 시그널 전달

```dockerfile
CMD ["sh", "-c", "A && B"]
```
`&&`는 셸 문법이라 exec 형식으로는 못 쓴다.

**부작용**: PID 1이 `sh`가 되고 uvicorn이 자식이 된다.
→ `docker stop`이 보내는 SIGTERM을 `sh`가 받고 **자식에게 전달하지 않을 수 있다.**
→ 컨테이너 종료가 10초(기본 타임아웃) 걸리고 강제 종료된다.

**개선**: `exec`를 쓴다.
```dockerfile
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```
`exec`가 셸을 uvicorn으로 **교체**하므로 PID 1이 uvicorn이 되고 시그널을 직접 받는다.

**6단계 `run_worker.sh`는 이미 `exec`를 쓴다.** 여기엔 없다 — **개선 여지.**

**왜 중요한가?** 강제 종료(SIGKILL)되면 진행 중인 업로드가 잘리고,
DB 커넥션이 정상적으로 닫히지 않는다.

---

## 2. **개발용 `docker-compose.yml`**

```yaml
# 평가 워커(worker/)는 이 compose에 포함하지 않는다.
# dr-start-evaluation이 호스트(WSL Ubuntu)의 DRFC(Docker Swarm)를 직접 호출해야 하므로,
# 워커는 컨테이너가 아니라 호스트에서 직접 실행한다 (README "워커 실행" 참고).
# DB는 호스트 포트로 노출해 호스트에서 도는 워커가 접속할 수 있게 한다.
services:
  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-drleader}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-drleader}
      POSTGRES_DB: ${POSTGRES_DB:-drleader}
    volumes:
      - db_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  web:
    build: .
    restart: unless-stopped
    environment:
      # 컨테이너 안에서는 호스트가 db(서비스명)이므로 .env의 DATABASE_URL을
      # 그대로 쓰지 않고 여기서 조립한다. 계정 정보는 위 db 서비스와 같은 값.
      DATABASE_URL: postgresql+psycopg2://${POSTGRES_USER:-drleader}:${POSTGRES_PASSWORD:-drleader}@db:5432/${POSTGRES_DB:-drleader}
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
DATABASE_URL: postgresql+psycopg2://...@db:5432/...
                                        ^^ 서비스 이름
```

**[쉬움]** 같은 아파트 단지 안에서는 "101동"이라고만 하면 통한다.
Docker가 단지 안에 **자체 주소록(DNS)** 을 만들어 준다.

**[전공]**
compose는 기본 브리지 네트워크를 만들고, 각 서비스를 **서비스 이름으로 DNS 등록**한다.
**IP를 직접 쓰면 안 되는 이유**: 컨테이너를 재생성하면 IP가 바뀐다.

### 2-2. **`DATABASE_URL`을 여기서 조립하는 이유**

```yaml
# 컨테이너 안에서는 호스트가 db(서비스명)이므로 .env의 DATABASE_URL을
# 그대로 쓰지 않고 여기서 조립한다. 계정 정보는 위 db 서비스와 같은 값.
```

`.env`의 `DATABASE_URL`은 **호스트에서 도는 워커와 alembic용**이다:
```
DATABASE_URL=postgresql+psycopg2://drleader:drleader@localhost:5432/drleader
                                                     ^^^^^^^^^
```

**같은 DB인데 접속 주소가 두 개다:**
| 누가 | 주소 |
|---|---|
| 웹 컨테이너 | `db:5432` (compose 내부 DNS) |
| 호스트 워커 / alembic | `localhost:5432` (포트 매핑) |

**한 변수로는 표현할 수 없다.** 그래서 compose가 조립한다.

**계정 정보는 `${POSTGRES_USER}` 등으로 공유한다** — db 서비스와 web 서비스가 같은 값을 본다.
비밀번호를 바꾸면 두 곳이 동시에 바뀐다. **단일 진실 공급원.**

> **`.env.example`이 이 함정을 경고한다:**
> ```
> # 주의: 이 값들은 DB 볼륨(db_data)을 처음 만들 때만 적용된다. 이미 돌고 있는
> # DB의 비밀번호를 바꾸려면 컨테이너 안에서 ALTER USER를 실행하거나
> # 볼륨을 지우고 다시 만들어야 한다.
> ```
> **PostgreSQL 이미지는 데이터 디렉터리가 비어 있을 때만 초기화 스크립트를 돈다.**
> 이미 데이터가 있으면 `POSTGRES_PASSWORD`를 바꿔도 무시된다. **매우 흔한 함정이다.**

### 2-3. **`ports:` — 여기에 중요한 결정이 있다**

```yaml
db:
  ports:
    - "5432:5432"     # ← 호스트에 노출
```

**보통은 DB 포트를 호스트에 노출하지 않는다.** 그런데 여기선 노출한다.
파일 최상단 주석이 답한다:
> DB는 호스트 포트로 노출해 호스트에서 도는 워커가 접속할 수 있게 한다.

**보안 함의:**
`"5432:5432"` 는 **`0.0.0.0:5432`** 에 바인드한다. 즉 **모든 네트워크 인터페이스**.
같은 네트워크의 다른 기기가 접속할 수 있다.

**개선:**
```yaml
ports:
  - "127.0.0.1:5432:5432"      # 루프백에만 바인드
```

> **운영 compose는 이미 이 개선을 적용했다.** (§3-2) **개발용에는 아직 없다.**

---

## 3. **운영용 `docker-compose.prod.yml` — 무엇이 달라지는가**

```yaml
# 클라우드 서버용 구성 (cloud-migration.md §3·§6).
#
# 노트북용 docker-compose.yml과의 차이:
#   - 웹을 호스트 포트에 직접 노출하지 않는다. Caddy가 앞단에서 HTTPS로 받아 넘긴다.
#   - DB 포트를 공개 인터넷에 열지 않는다. 워커는 Tailscale 사설망으로 접속한다.
#   - 비밀값(DB 비밀번호·세션 시크릿·워커 토큰)을 .env에서 읽는다.
#
# 사용법: docker compose -f docker-compose.prod.yml up -d
```

### 왜 파일을 두 개로 나눴나

**[쉬움]**
집에서 입는 옷과 밖에서 입는 옷이 다르다.
집에서는 편한 게 우선, 밖에서는 단정한 게 우선.

**[전공] 대안 3가지**

| 방법 | 장단 |
|---|---|
| 파일 하나 + 환경변수로 전부 제어 | **불가능하다.** `ports` 유무, 서비스 개수(caddy) 자체가 다르다 |
| `docker-compose.override.yml` | compose가 자동 병합. 편하지만 **어느 게 적용됐는지 헷갈린다** |
| **파일 두 개 (현재)** | 명시적. `-f` 로 어느 것인지 항상 드러난다 |

**운영에서 실수하면 큰일이므로 "명시적"이 이긴다.**
`-f docker-compose.prod.yml` 을 안 붙이면 개발용이 뜨는데, **그건 즉시 티가 난다**
(HTTPS가 안 되고 포트가 다르므로).

### 3-1. **필수 환경변수 — `${VAR:?메시지}`**

```yaml
POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD를 .env에 설정하세요}
SESSION_SECRET: ${SESSION_SECRET:?SESSION_SECRET을 .env에 설정하세요}
SITE_DOMAIN: ${SITE_DOMAIN:?SITE_DOMAIN을 .env에 설정하세요}
ADMIN_LOGIN_PATH: "${ADMIN_LOGIN_PATH:?ADMIN_LOGIN_PATH를 .env에 설정하세요. 예) /_ops/무작위문자열}"
```

**[쉬움]**
"비밀번호를 안 넣으면 **아예 문을 안 연다**."
대충 기본값으로 열어두면, 문이 열려 있는 줄도 모른 채 며칠이 지난다.

**[전공] 셸 파라미터 확장 문법**

| 문법 | 동작 |
|---|---|
| `${VAR}` | 없으면 빈 문자열 |
| `${VAR:-기본값}` | 없으면 기본값 사용 |
| `${VAR:?메시지}` | **없거나 비면 에러를 내고 중단** |

`docker compose`가 이 문법을 해석한다. 값이 없으면:
```
error while interpolating services.web.environment.[]:
required variable SESSION_SECRET is missing a value: SESSION_SECRET을 .env에 설정하세요
```
**컨테이너가 아예 안 뜬다.**

**`ADMIN_LOGIN_PATH`에 붙은 긴 주석이 이 설계의 이유를 정확히 말한다:**
```yaml
# 관리자 로그인 폼이 열리는 비밀 경로 (예: /_ops/k7f3q9x2p1).
# **일부러 필수로 둔다.** 값이 없을 때 기본값(/admin/login)으로 조용히 넘어가면,
# 배포는 성공한 것처럼 보이는데 관리자 로그인이 다시 공개된 상태가 된다.
# 그 사실을 아무도 모르는 것보다 기동이 실패해 즉시 드러나는 편이 낫다.
```

> **[전공] "조용한 실패" vs "시끄러운 실패"**
>
> | | 조용한 실패 | 시끄러운 실패 |
> |---|---|---|
> | 증상 | 배포 성공. 그런데 보안이 약해짐 | 배포 실패. 즉시 인지 |
> | 발견 시점 | **영원히 모를 수도 있다** | **즉시** |
> | 비용 | 사고 후 수습 | 5분 안에 `.env` 수정 |
>
> **보안 설정에서는 항상 시끄러운 실패를 택해야 한다.**

**1단계 §3-5에서 본 `field_validator`와 짝을 이룬다:**
- **애플리케이션 층**: 빈 값이면 기본값으로 (개발 편의)
- **배포 설정 층**: 빈 값이면 기동 실패 (운영 안전)

**같은 문제에 층마다 다른 답.** 이게 심층 방어의 실제 모습이다.

### **YAML 따옴표 함정 — 실제로 겪은 것**

```yaml
# 값을 따옴표로 감싼다 — 안내 문구에 콜론+공백이 들어가면 YAML이 매핑으로 읽어
# 파일 전체가 파싱 실패한다.
ADMIN_LOGIN_PATH: "${ADMIN_LOGIN_PATH:?ADMIN_LOGIN_PATH를 .env에 설정하세요. 예) /_ops/무작위문자열}"
```

**[전공]**
YAML에서 `키: 값` 의 `: `(콜론+공백)은 **매핑 구분자**다.
따옴표가 없으면:
```yaml
ADMIN_LOGIN_PATH: ${...설정하세요. 예) /_ops/...}
```
파서가 안내 문구 안의 `: ` 를 만나 혼란스러워한다.

> **원칙: 환경변수 값에 특수문자(`:`, `#`, `{`, `}`, `,`)가 들어갈 가능성이 있으면 따옴표로 감싼다.**
> 이건 YAML을 쓰는 모든 곳에 적용된다.

**선택값은 `:-`(기본값)을 쓴다:**
```yaml
# 아래 둘은 선택값. 없으면 앱 기본값(5회 / 15분)을 쓴다.
ADMIN_LOGIN_MAX_ATTEMPTS: ${ADMIN_LOGIN_MAX_ATTEMPTS:-5}
ADMIN_LOGIN_LOCKOUT_MINUTES: ${ADMIN_LOGIN_LOCKOUT_MINUTES:-15}
# 값이 설정되면 워커가 HTTP로 모델을 받고 영상을 올리는 방식으로 동작한다.
WORKER_TOKEN: ${WORKER_TOKEN:-}
```

**`WORKER_TOKEN: ${WORKER_TOKEN:-}`** — 기본값이 **빈 문자열**이다.
→ 6단계에서 본 대로 **빈 값이면 local 모드**이고 `/internal/*`가 닫힌다.
**"설정 안 하면 안전한 쪽"** 이 기본이다.

**`SESSION_HTTPS_ONLY: ${SESSION_HTTPS_ONLY:-true}`** — 개발용은 `false`, 운영용은 **`true`가 기본**.
**환경에 맞는 안전한 기본값을 각 파일이 갖는다.**

### 3-2. **DB 바인드 주소 — 실수 하나가 DB를 인터넷에 노출한다**

```yaml
ports:
  # **반드시 특정 주소에 바인딩한다.** 포트만 쓰면(`5432:5432`) 0.0.0.0에 열려 공개
  # 인터넷에 DB가 노출된다 — 5432는 상시 스캔 대상이다.
  # 서버 .env에 `DB_BIND_ADDRESS=<Tailscale IP>`를 넣어 사설망에만 열고, 값이 없으면
  # 안전한 기본값(루프백)으로 떨어져 외부에서 접근할 수 없게 한다.
  - "${DB_BIND_ADDRESS:-127.0.0.1}:5432:5432"
```

**[쉬움]**
문을 여는데 **어느 방향으로 열지**를 정한다.
- 안뜰 쪽으로만 열면 → 집 안 사람만 들어온다
- 큰길 쪽으로 열면 → **누구나 들어온다**

**[전공] 포트 바인딩 문법**

| 표기 | 실제 바인딩 | 누가 접근 가능 |
|---|---|---|
| `"5432:5432"` | `0.0.0.0:5432` | **인터넷 전체** ⚠️ |
| `"127.0.0.1:5432:5432"` | 루프백만 | 그 서버 안에서만 |
| `"100.x.y.z:5432:5432"` | Tailscale IP | **사설망 안에서만** ← 원하는 것 |

**왜 이게 치명적인가?**
- Lightsail 인스턴스는 **공인 IP를 가진다**
- 5432 포트는 **자동 스캐너의 상시 표적**이다
- 뚫리면 대회 데이터 전체 + 비밀번호 해시가 유출된다

**`:-127.0.0.1` 기본값이 중요하다.**
`DB_BIND_ADDRESS`를 깜빡해도 **루프백으로 떨어진다** → 워커가 못 붙어서 **즉시 알게 된다.**
**"실수하면 동작이 멈추지만 노출은 안 된다."**

> **[전공] 이것이 "안전한 기본값(secure by default)"의 정확한 예다.**
> 잘못 설정했을 때 **덜 안전한 쪽이 아니라 덜 편한 쪽**으로 떨어져야 한다.
>
> `ADMIN_LOGIN_PATH`는 `:?`(기동 실패), `DB_BIND_ADDRESS`는 `:-127.0.0.1`(안전한 기본값).
> **왜 다를까?** 전자는 "안전한 기본값이 존재하지 않는다"(어떤 경로를 쓸지 우리가 못 정한다).
> 후자는 **명백히 안전한 기본값이 있다.** → 있으면 쓰고, 없으면 실패시킨다.

**웹은 아예 호스트에 안 연다:**
```yaml
web:
  expose:
    - "8000"      # ports가 아니라 expose — 내부 네트워크에만
```
**`expose`는 호스트 포트를 열지 않는다.** compose 네트워크 안의 다른 컨테이너(caddy)만 접근 가능.
→ **인터넷에서 8000번으로 직접 들어올 방법이 없다.** Caddy를 반드시 거쳐야 한다.

### 3-3. **헬스체크 — `depends_on`의 한계를 고친다**

```yaml
db:
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U drleader"]
    interval: 10s
    timeout: 5s
    retries: 5

web:
  depends_on:
    db:
      condition: service_healthy
```

**개발용은 이게 없다:**
```yaml
depends_on:
  - db          # 짧은 형식 — "먼저 시작한다"만 보장
```

**[쉬움]**
"먼저 출발해"와 "준비 다 됐는지 확인하고 출발해"는 다르다.

**[전공]**
**`depends_on`의 짧은 형식은 "컨테이너를 먼저 시작한다"만 보장한다.**
**"PostgreSQL이 연결을 받을 준비가 됐다"는 보장하지 않는다.**

PostgreSQL은 프로세스가 뜬 뒤에도 초기화에 몇 초가 걸린다. 그 사이에 `web`이 시작되면:
```
alembic upgrade head
→ could not connect to server: Connection refused
→ && 뒤가 실행 안 됨 → 컨테이너 종료
→ restart: unless-stopped → 재시작
→ 이번엔 db가 준비됨 → 성공
```

**개발에서는 결과적으로 동작한다.** 재시작 정책이 우연히 헬스체크 역할을 한다.
**하지만 로그가 지저분해지고 진짜 장애를 숨긴다.**

**`condition: service_healthy` 는 헬스체크가 통과할 때까지 기다린다.**

**`pg_isready -U drleader`** — PostgreSQL이 제공하는 전용 도구.
`SELECT 1`을 날리는 것보다 가볍고 정확하다.

> **아쉬운 점**: `web`에는 헬스체크가 없다. `/healthz`(1단계 §2-6)가 있는데 안 쓰인다.
> ```yaml
> web:
>   healthcheck:
>     test: ["CMD", "python", "-c",
>            "import urllib.request;urllib.request.urlopen('http://localhost:8000/healthz')"]
>     interval: 30s
> ```
> Caddy가 `depends_on: web` 만 쓰고 있으므로, 웹이 준비되기 전에 요청이 오면 502가 난다.
> **개선 여지.**

### 3-4. **메모리 상한 — 이웃과 공존하기**

```yaml
web:
  # 개인 프로젝트를 같은 서버에 얹더라도 대회 서비스가 메모리 부족으로 죽지 않게 상한을 둔다.
  mem_limit: 900m

caddy:
  mem_limit: 256m
```

**[쉬움]**
한 방에 여러 사람이 살면, **한 사람이 짐을 다 쌓아두면 다른 사람이 못 산다.**
각자 쓸 수 있는 공간을 미리 정해둔다.

**[전공]**
`mem_limit`은 cgroup의 메모리 제한을 설정한다.
컨테이너가 그 이상을 쓰려 하면 **OOM Killer가 그 컨테이너 안의 프로세스를 죽인다.**

**주석의 의도가 흥미롭다:**
> 개인 프로젝트를 같은 서버에 얹더라도 대회 서비스가 메모리 부족으로 죽지 않게

**보통 `mem_limit`은 "이 컨테이너가 폭주하는 것을 막기 위해" 쓴다.**
여기서는 **"다른 것이 폭주해도 이건 살아남게"** 하려는 의도다.

**실제로는 두 효과가 다 있다:**
- 웹이 900MB를 넘으면 웹만 죽는다 (호스트 전체가 아니라)
- 다른 프로세스가 폭주해도 웹의 900MB는 **예약되어 있지 않다** ← 주의!

> **[전공] 정확히 말하면 `mem_limit`은 "상한"이지 "예약"이 아니다.**
> 예약은 `mem_reservation`(soft limit)이다.
> 다른 프로세스가 메모리를 다 먹으면 웹도 결국 못 쓴다.
> **다만 웹이 900MB 이상 쓰려다 호스트 전체를 OOM으로 몰고 가는 것은 확실히 막는다.**

**900MB라는 숫자와 4단계의 연결:**
```python
model_file: UploadFile = File(None)     # 스풀링 — 메모리에 250MB를 안 올린다
```
**만약 `bytes = File(...)` 였다면 동시 업로드 3건에 OOM Kill이다.**
**배포 제약이 코드 선택을 검증해 준 셈이다.**

### 3-5. Caddy — TLS와 리버스 프록시

```yaml
caddy:
  image: caddy:2
  restart: unless-stopped
  ports:
    - "80:80"
    - "443:443"
  environment:
    SITE_DOMAIN: ${SITE_DOMAIN:?SITE_DOMAIN을 .env에 설정하세요}
  volumes:
    - ./Caddyfile:/etc/caddy/Caddyfile:ro
    - caddy_data:/data      # 발급받은 인증서. 지우면 재발급 한도에 걸릴 수 있다
    - caddy_config:/config
  depends_on:
    - web
  mem_limit: 256m
```

**`:ro` (read-only)** — Caddy가 설정 파일을 수정할 수 없다. **최소 권한 원칙.**

**`caddy_data` 볼륨의 주석이 중요하다:**
> 발급받은 인증서. 지우면 재발급 한도에 걸릴 수 있다

**[전공] Let's Encrypt의 발급 한도(rate limit)**
- 같은 도메인에 **주당 5회** 중복 인증서 발급 제한
- 한도에 걸리면 **일주일간 HTTPS를 못 쓴다**

`docker compose down -v` 로 볼륨을 지우면 인증서가 날아가고 재발급을 시도한다.
**몇 번 반복하면 대회 중에 사이트가 HTTPS로 안 열린다.**

> **`down -v` 는 이 프로젝트에서 두 번 위험하다:**
> 1. `db_data` — **모든 대회 기록이 사라진다**
> 2. `caddy_data` — **인증서 재발급 한도**
>
> **`down` 대신 `stop`을 쓰는 습관**을 들이는 것이 안전하다.

---

## 4. `Caddyfile` — HTTPS와 프록시 설정

```
# 리버스 프록시 + HTTPS 인증서 자동 발급/갱신 (cloud-migration.md §6).
# 도메인은 .env의 SITE_DOMAIN에서 읽는다.

{$SITE_DOMAIN} {
	encode gzip

	# 참가자가 올리는 모델이 250MB 안팎이다. 앱의 상한(500MB)보다 넉넉히 잡아,
	# 크기 제한은 프록시가 아니라 앱이 판단하고 한글 안내 메시지를 주게 한다.
	request_body {
		max_size 600MB
	}

	reverse_proxy web:8000 {
		# 250MB 업로드는 느린 회선에서 수 분이 걸린다. 그 사이에 프록시가 연결을
		# 끊어버리면 참가자는 원인을 알 수 없는 실패를 겪는다.
		transport http {
			read_timeout 30m
			write_timeout 30m
		}
	}

	log {
		output file /data/access.log {
			roll_size 10MB
			roll_keep 5
		}
	}
}
```

### 4-1. 왜 Caddy인가 — nginx 대신

**[쉬움]**
HTTPS(자물쇠)를 쓰려면 **인증서**가 필요하다. 무료로 받을 수 있지만
**3개월마다 갱신**해야 하고 절차가 번거롭다. Caddy는 그걸 **자동으로** 한다.

**[전공]**

| | nginx | Caddy |
|---|---|---|
| TLS 인증서 | certbot 별도 설치 + cron | **내장. 자동 발급·갱신** |
| 설정 문법 | 장황함 | 간결함 |
| HTTP→HTTPS 리다이렉트 | 직접 설정 | **자동** |
| 성능 | 약간 우위 | 충분함 |

**우리 규모(동시 10명)에서 성능 차이는 무의미하다.**
**"인증서 갱신을 잊어서 대회 중에 사이트가 안 열리는" 위험을 없애는 것이 훨씬 크다.**

**`{$SITE_DOMAIN}`** — Caddyfile의 환경변수 문법(파이썬/셸과 다르다).
compose가 `SITE_DOMAIN`을 컨테이너 환경으로 넘기고, Caddy가 읽는다.
**도메인이 코드에 하드코딩되지 않는다** — 12-factor 일관성.

**도메인을 적기만 하면 Caddy가:**
1. Let's Encrypt에 인증서를 요청한다 (ACME 프로토콜)
2. HTTP-01 챌린지를 자동 처리한다 (80 포트가 열려 있어야 하는 이유)
3. 60일마다 자동 갱신한다
4. HTTP(80)로 온 요청을 HTTPS(443)로 리다이렉트한다

### 4-2. **`request_body max_size 600MB` — 왜 앱보다 크게 잡는가**

```
# 참가자가 올리는 모델이 250MB 안팎이다. 앱의 상한(500MB)보다 넉넉히 잡아,
# 크기 제한은 프록시가 아니라 앱이 판단하고 한글 안내 메시지를 주게 한다.
```

**[쉬움]**
경비원이 "너무 큰 짐은 안 됩니다"라고 막으면, **손님은 왜 안 되는지 모른다.**
안내 데스크까지 들여보내서 **"최대 500MB까지입니다"라고 한국어로 설명**하게 한다.

**[전공] 계층별 제한값 설계**

```
Caddy:  600MB  ← 넉넉히
앱:     500MB  ← 실제 정책 (config.py)
JS:     500MB  ← 앱에서 내려받은 값 (data-max-bytes)
실제:   250MB  ← 참가자 파일 크기
```

**Caddy가 500MB로 막으면 어떻게 되나?**
```
HTTP/1.1 413 Request Entity Too Large
Content-Type: text/plain

413 Request Entity Too Large
```
**영어 한 줄.** 참가자는 이게 무슨 뜻인지, 얼마까지 올릴 수 있는지 모른다.
게다가 `upload.js`의 `xhr.onload`는 JSON 파싱에 실패해
"로그인이 만료되었을 수 있습니다"라는 **엉뚱한 안내**를 한다(4단계 §5).

**앱까지 도달하면:**
```python
if size > settings.model_upload_max_bytes:
    max_mb = settings.model_upload_max_bytes // (1024 * 1024)
    return redirect_with_error(f"파일 용량이 너무 큽니다 (최대 {max_mb}MB).")
```
**한국어 + 구체적 수치 + JSON 응답(진행률 화면에 그대로 표시).**

> **[전공] 원칙: "정책은 애플리케이션이 판단하고, 인프라는 극단만 막는다."**
> 인프라 층의 제한은 **DoS 방어용 안전망**이지 **사용자 정책이 아니다.**
>
> 완전히 없애면(무제한) 악의적 요청이 디스크를 채울 수 있으므로 600MB는 남긴다.
> **"넉넉하지만 무한하지 않게."**

**그리고 `upload.js`가 세 번째 층을 만든다:**
```javascript
if (maxBytes && file.size > maxBytes) {
  return "파일 용량이 너무 큽니다 (최대 " + formatBytes(maxBytes) + "). 선택한 파일은 " +
    formatBytes(file.size) + "입니다.";
}
```
**전송을 아예 시작하지 않는다** — 가장 좋은 경험. (4단계 §4-3)

**세 층의 역할:**
| 층 | 시점 | 목적 |
|---|---|---|
| JS | 파일 선택 즉시 | **최고의 UX** (0바이트 전송) |
| 앱 | 500MB 수신 시점 | **정책 강제** + 한국어 안내 |
| Caddy | 600MB | **극단 방어** (DoS) |

### 4-3. **타임아웃 30분 — 없으면 무슨 일이**

```
reverse_proxy web:8000 {
	# 250MB 업로드는 느린 회선에서 수 분이 걸린다. 그 사이에 프록시가 연결을
	# 끊어버리면 참가자는 원인을 알 수 없는 실패를 겪는다.
	transport http {
		read_timeout 30m
		write_timeout 30m
	}
}
```

**[전공] 계산해보자**

느린 회선(업로드 5 Mbps = 0.6 MB/s)에서 250MB를 올리면:
```
250 MB ÷ 0.6 MB/s ≈ 417초 ≈ 7분
```

기본 타임아웃이 몇 분이면 **7분짜리 업로드가 중간에 끊긴다.**

**참가자가 겪는 것:**
- 진행률이 60%쯤에서 멈춤
- `xhr.onerror` 발동 → "네트워크 연결이 끊겨 업로드에 실패했습니다"
- **본인 인터넷 문제인 줄 안다.** 다시 시도해도 같은 지점에서 끊긴다
- 문의가 온다: "인터넷은 멀쩡한데 계속 실패해요"

**진단이 극도로 어렵다** — 서버 로그에는 "클라이언트가 끊었다"로만 남는다.

**30분은 충분히 넉넉하다:**
```
250 MB ÷ 30분 = 0.14 MB/s = 1.1 Mbps
```
**1 Mbps만 나와도 성공한다.**

> **6단계 §13-2의 계층적 타임아웃과 같은 원리다.**
> ```
> Caddy: 30분  ≥  transfer.py read: 10분  ≥  ...
> ```
> **다만 이 둘은 서로 다른 경로다** (참가자→웹, 워커→웹).
> 워커의 모델 다운로드도 Caddy를 거친다면 30분 안에 끝나야 한다.
> **Tailscale로 직접 붙는다면 Caddy를 안 거친다** — 구성에 따라 다르다.

### 4-4. `encode gzip` — 압축

**[전공]**
HTML/CSS/JS를 gzip으로 압축해 전송한다. **텍스트는 보통 70~80% 줄어든다.**

**이미 압축된 것(mp4, tar.gz, zip)은 Caddy가 알아서 건너뛴다** — 압축률이 없고 CPU만 쓴다.
`Content-Type`으로 판단한다.

### 4-5. 로그 로테이션

```
log {
	output file /data/access.log {
		roll_size 10MB
		roll_keep 5
	}
}
```

**[쉬움]** 일기장이 다 차면 새 공책을 쓰고, 오래된 것 5권만 보관한다.

**[전공]**
로테이션이 없으면 **액세스 로그가 무한히 자란다.**
Lightsail 디스크가 40GB인데 로그가 10GB를 차지하면 대회가 멈춘다.
`10MB × 5 = 최대 50MB`로 **상한이 확정된다.**

**`/data`에 쓴다** → `caddy_data` 볼륨 → **컨테이너를 재생성해도 로그가 남는다.**

> **로그에 `X-Forwarded-For`가 남는다는 점이 3단계와 연결된다.**
> `admin_lockout`이 이 헤더로 IP를 판별하는데, **위조 가능하다.**
> Caddy 액세스 로그를 보면 **실제 TCP 연결의 원격 주소**도 남으므로,
> 사후 조사에서 위조 여부를 대조할 수 있다.

---

## 5. **볼륨 두 종류 — 명명 볼륨 vs 바인드 마운트**

```yaml
db:
  volumes:
    - db_data:/var/lib/postgresql/data     # ← 명명 볼륨 (named volume)

web:
  volumes:
    - ./storage:/app/storage                # ← 바인드 마운트 (bind mount)

caddy:
  volumes:
    - ./Caddyfile:/etc/caddy/Caddyfile:ro   # ← 바인드 마운트 (파일 하나, 읽기 전용)
    - caddy_data:/data                       # ← 명명 볼륨
```

| | 명명 볼륨 | 바인드 마운트 |
|---|---|---|
| 실제 위치 | Docker가 관리 (`/var/lib/docker/volumes/`) | **내가 지정한 호스트 경로** |
| 호스트에서 접근 | 어렵다 (root 권한 필요) | **쉽다. 그냥 파일** |
| 권한 문제 | 적음 | **UID 불일치 문제 자주 발생** |

**왜 DB는 명명 볼륨인가?**
- PostgreSQL 데이터 디렉터리는 **사람이 직접 볼 일이 없다**
- 권한이 까다롭다 (postgres 유저 소유여야 함)

**왜 storage는 바인드 마운트인가?**
- **호스트에서 백업/확인이 쉬워야 한다**
- local 모드에서는 **호스트의 워커가 같은 파일을 읽어야 한다** ← 결정적 이유

**[쉬움]**
- 명명 볼륨 = 은행 금고. 안전하지만 내가 직접 못 열어본다
- 바인드 마운트 = 내 책상 서랍. 내가 언제든 열어본다

### **`./storage:/app/storage` — 4단계의 그 장애가 여기서 나온다**

```
호스트                                        컨테이너
/home/ubuntu/spg_deepracer_leaderboard/storage  ←→  /app/storage
```

**같은 디스크 영역, 완전히 다른 경로 이름.**

웹이 절대 경로를 DB에 저장하면 워커가 못 찾는다.
그래서 `app/storage_paths.py`가 존재한다 (4단계 §8):
> (2026-07-26 운영 장애: 업로드한 모델을 워커가 못 찾아 평가 실패)

> **[전공] 이것이 이 프로젝트에서 가장 배울 만한 아키텍처 교훈이다.**
> **"경계를 넘어 전달되는 값은 양쪽에서 같은 의미여야 한다."**
> 절대 경로는 실행 환경에 의존하므로 경계를 못 넘는다.
> 상대 경로 + 각자의 루트 = 경계를 넘는다.
>
> 같은 원리가 적용되는 곳들: URL(상대 vs 절대), 시각(UTC vs 로컬), ID(로컬 시퀀스 vs UUID).

**http 모드로 옮기면서 이 문제의 성격이 바뀌었다:**
- **local 모드**: 두 프로세스가 같은 파일을 **다른 경로 이름**으로 본다 → `storage_paths`가 필요
- **http 모드**: 워커는 파일을 **아예 갖고 있지 않다** → HTTP로 받는다

**그런데 `storage_paths`는 여전히 필요하다** — 서버 쪽이 파일을 찾을 때 쓴다:
```python
# app/routers/internal.py:51
path = resolve_storage_path(submission.model_path)
```

### `restart: unless-stopped`

| 정책 | 동작 |
|---|---|
| `no` (기본) | 안 재시작 |
| `always` | 항상. **`docker stop`으로 멈춰도 데몬 재시작 시 다시 뜸** |
| `unless-stopped` | 항상. 단 **내가 명시적으로 멈춘 건 그대로 둔다** ← 선택됨 |

대회 중 서버를 재부팅해도 서비스가 자동으로 올라온다.
그런데 내가 점검하려고 멈춘 걸 마음대로 켜지는 않는다.

---

## 6. **웹은 컨테이너, 워커는 별도 기기 — 이 비대칭의 이유**

```yaml
# 평가 워커(worker/)는 이 compose에 포함하지 않는다.
# dr-start-evaluation이 호스트(WSL Ubuntu)의 DRFC(Docker Swarm)를 직접 호출해야 하므로,
# 워커는 컨테이너가 아니라 호스트에서 직접 실행한다.
```

### 왜 워커를 컨테이너에 못 넣나

**워커가 하는 일:**
1. `bash run_evaluation.sh` 실행
2. `source bin/activate.sh` → DRFC 셸 함수 로드
3. `dr-start-evaluation` → **`docker stack deploy`** 호출
4. `docker stack ps`로 폴링, `docker service logs`로 로그 수집

**즉 워커는 Docker를 조종한다.** 컨테이너 안에서 Docker를 조종하려면:

| 방법 | 문제 |
|---|---|
| **DinD** (Docker in Docker) | 중첩 컨테이너. Swarm 모드는 특히 까다롭다 |
| **소켓 마운트** (`/var/run/docker.sock`) | **컨테이너가 호스트 전체를 장악할 수 있다** (사실상 root) |
| 호스트에서 실행 (현재) | 간단하고 안전 |

**그리고 더 근본적인 문제:**
DRFC 설치 자체가 `~/deepracer-for-cloud`에 있고,
`run.env`/`system.env`, `~/.aws/credentials`, MinIO 데이터가 **호스트 파일시스템에 흩어져 있다.**

**[쉬움]**
로봇 팔을 조종하는 프로그램을 **상자 안에 넣으면** 팔에 손이 안 닿는다.

### 이 구조가 만드는 결과

| | 웹 | 워커 |
|---|---|---|
| 실행 위치 | 컨테이너 (Lightsail) | **다른 기기** (EC2 / 노트북) |
| 파이썬 | 이미지 안의 3.12 | `.venv/bin/python` (3.10) |
| DB 접속 | `db:5432` | Tailscale IP:5432 |
| 파일 접근 | `/app/storage` (직접) | **HTTP** (`/internal/*`) |
| 설정 주입 | `environment:` | `run_worker.sh`의 `source activate.sh` |
| 재시작 | Docker가 자동 | **수동** ⚠️ |

**주목: 워커에는 재시작 정책이 없다.**
워커가 죽으면 아무도 안 살린다. 대회 중이면 평가가 멈춘다.

**하지만 이제 하트비트가 그걸 "보이게" 만든다** (6단계 §6).
참가자 화면에 배너가 뜨고, 운영자가 인지할 수 있다.

> **개선**: `systemd` 유닛으로 관리하면 자동 재시작 + 부팅 시 자동 시작이 된다.
> `docs/worker-server-setup.md`에 그 절차가 있을 가능성이 높다 — 확인해볼 것.

---

## 7. 배포 절차와 안전장치

### 전체 그림

```
[개발 노트북]                    [Lightsail 서버]              [평가 서버]
 코드 수정                        git pull                     git pull
 git push        ─────────>      docker compose -f prod up -d   worker/run_worker.sh
                                    ├─ alembic upgrade head
                                    └─ uvicorn
                                 (Caddy가 HTTPS 처리)
```

### 배포 시 확인할 것

```bash
# 1. 어느 compose를 쓰는지 명시
docker compose -f docker-compose.prod.yml up -d --build

# 2. 마이그레이션이 성공했는지
docker compose -f docker-compose.prod.yml logs web | head -30

# 3. 인증서가 정상인지
docker compose -f docker-compose.prod.yml logs caddy | grep -i certificate

# 4. 워커가 붙어 있는지
docker compose -f docker-compose.prod.yml exec db psql -U drleader -c \
  "SELECT worker_id, now() - last_seen_at AS 경과 FROM worker_heartbeats;"
```

**4번이 특히 중요하다** — 웹만 배포하고 워커를 잊는 실수를 잡는다.

### 백업 — 무엇을 잃을 수 있나

| 대상 | 위치 | 중요도 | 방법 |
|---|---|---|---|
| DB | `db_data` 명명 볼륨 | **최상** | `pg_dump` |
| 영상 | `storage/videos/` | 높음 | 파일 복사 |
| metrics 원본 | `storage/metrics/` | 중간 | 파일 복사 |
| 모델 파일 | `storage/models/` | 낮음 | 재제출 가능 |
| 인증서 | `caddy_data` | 중간 | 재발급 가능하나 **한도 주의** |
| 코드 | git | — | 이미 됨 |

**DB 백업:**
```bash
docker compose -f docker-compose.prod.yml exec db \
  pg_dump -U drleader drleader > backup_$(date +%F).sql
```

**복원:**
```bash
cat backup_2026-08-01.sql | docker compose -f docker-compose.prod.yml exec -T db \
  psql -U drleader drleader
```

**DB가 날아가면 모든 순위와 기록이 사라진다.** 파일은 남아도 의미가 없다.

> **6단계에서 본 `deliver_metrics`가 여기와 연결된다:**
> ```python
> """MinIO의 원본은 다음 평가 때 같은 키로 덮어써지므로 이 사본이 유일한 기록이다.
> 워커(노트북)에만 두면 백업 대상에서 빠지므로 서버로 올려 함께 백업되게 한다."""
> ```
> **"백업 대상에서 빠진다"** 는 이유로 파일을 서버로 옮긴 것이다.
> 백업 전략이 애플리케이션 설계에 영향을 준 예다.

### **`down -v` 의 위험 — 다시 강조**

```bash
docker compose down -v      # ⚠️ 절대 하지 말 것
```
`-v` 하나로:
- `db_data` → **모든 대회 기록 소실**
- `caddy_data` → **인증서 소실 + 재발급 한도 위험**

**`down` 대신 `stop`을 쓰는 습관.**

---

## 8. 관측성 — 지금 상태

### 로그

| 대상 | 위치 | 로테이션 |
|---|---|---|
| Caddy 액세스 | `caddy_data:/data/access.log` | ✅ 10MB × 5 |
| 웹 (uvicorn) | `docker compose logs web` | Docker 기본(json-file, 무제한) ⚠️ |
| 워커 | stdout → 실행 방식에 따라 | ⚠️ |
| 제출별 시뮬레이션 | `storage/eval_logs/{id}.log` | ❌ 무한 |

**Docker 기본 로그 드라이버는 무제한으로 자란다.** 설정이 필요하다:
```yaml
logging:
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"
```
**개선 여지.**

**`storage/eval_logs/`는 제출마다 파일 하나씩 쌓인다.**
`retention.py`가 모델·영상은 지우지만 **로그는 안 지운다.**
크기가 작아 당장 문제는 없지만 정리 대상에 넣을 만하다.

**그리고 이 로그가 이제 기능의 일부다** — `summarize_progress`가 읽는다(6단계 §9-4).
**함부로 지우면 안 되는 이유가 생겼다.**

### 없는 것

- 메트릭 (평가 성공률, 평균 소요 시간, 큐 길이 추이)
- 알림 (워커가 죽었을 때 **화면에는 뜨지만 운영자에게 안 간다**)
- `/healthz` 활용

**가장 싼 개선**: 관리자 대시보드에 워커 상태와 큐 길이를 표시.
`get_worker_status()`가 이미 있으므로 몇 줄이면 된다.

```sql
-- 워커가 죽었는지 알아내는 쿼리
SELECT id, team_id, submitted_at, now() - submitted_at AS waiting
FROM submissions WHERE status = 'queued'
ORDER BY submitted_at LIMIT 1;
```

---

## 9. 자가 점검 질문

**컨테이너 기초**
1. 컨테이너와 가상 머신의 차이를 커널 관점에서 설명하라. `mem_limit`은 어느 기능을 쓰는가?
2. `COPY requirements.txt`가 `COPY app app`보다 먼저인 이유는?
3. `alpine` 대신 `slim`을 고른 이유는? 워커의 파이썬 버전과 어떤 제약이 생기는가?
4. `EXPOSE 8000`이 실제로 하는 일은? 운영 compose가 `ports` 대신 `expose`를 쓰는 이유는?
5. `CMD`에 마이그레이션을 넣은 것의 장점 1개와 단점 3개는?
6. `sh -c` 때문에 생기는 시그널 문제는? `exec`가 어떻게 해결하는가? 왜 중요한가?

**개발용 compose**
7. `db:5432`가 동작하는 원리는? IP를 직접 쓰면 왜 안 되나?
8. `.env`의 `DATABASE_URL`과 compose가 조립하는 것이 다른 이유는? 각각 누가 쓰는가?
9. `POSTGRES_PASSWORD`를 바꿔도 반영이 안 되는 이유는? 어떻게 바꿔야 하는가?
10. `"5432:5432"` 가 실제로 어느 주소에 바인딩되는가? 왜 위험한가?

**운영용 compose**
11. compose 파일을 두 개로 나눈 이유는? override 방식과 비교하면?
12. `${VAR:?메시지}` 와 `${VAR:-기본값}` 의 차이는? 각각 언제 쓰는가?
13. `ADMIN_LOGIN_PATH`를 필수로 둔 이유를 "조용한 실패" 개념으로 설명하라.
14. `ADMIN_LOGIN_PATH`는 `:?`인데 `DB_BIND_ADDRESS`는 `:-127.0.0.1`이다. 왜 다른가?
15. YAML에서 값을 따옴표로 감싸야 했던 이유는?
16. `WORKER_TOKEN: ${WORKER_TOKEN:-}` 의 기본값이 빈 문자열인 것의 의미는?
17. `depends_on: - db` 와 `condition: service_healthy` 의 차이는? 전자는 왜 결과적으로 동작하는가?
18. `mem_limit`은 "상한"인가 "예약"인가? 900MB가 4단계의 어떤 코드 선택을 검증하는가?

**Caddy**
19. nginx 대신 Caddy를 고른 결정적 이유는?
20. `request_body max_size`를 앱 상한(500MB)보다 크게 잡은 이유는? 반대로 하면 참가자가 무엇을 보는가?
21. 크기 제한 3개 층(JS/앱/Caddy)의 역할을 각각 설명하라.
22. `read_timeout 30m`이 없으면 느린 회선의 참가자가 겪는 일을 순서대로 서술하라. 왜 진단이 어려운가?
23. 로그 로테이션이 없으면 무슨 일이 생기는가?

**볼륨과 배포**
24. 명명 볼륨과 바인드 마운트의 차이는? 왜 db는 전자, storage는 후자인가?
25. `./storage:/app/storage` 가 2026-07-26 장애와 어떻게 연결되는가? http 모드에서는 문제의 성격이 어떻게 바뀌는가?
26. `docker compose down -v` 가 이 프로젝트에서 두 번 위험한 이유는?
27. `caddy_data`를 지우면 왜 인증서 재발급 한도가 문제가 되는가?
28. 워커를 컨테이너에 넣지 못하는 이유 2가지는? 소켓 마운트가 왜 위험한가?
29. 배포 후 확인해야 할 4가지는? 그중 가장 잊기 쉬운 것은?
30. `deliver_metrics`가 서버로 파일을 올리는 이유가 백업과 무슨 관계인가?

---

## 10. 실험 과제

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
python -c "from app.config import settings; print(settings.storage_dir)"
exit
```
호스트에서 `ls storage/models` 와 비교. **같은 파일인데 경로가 어떻게 다른가?**

**실험 C — 환경변수 주입 확인**
```bash
docker compose exec web env | grep -E "SESSION|DATABASE|ADMIN|WORKER"
```
`.env`를 고치고 다시 확인 → 안 바뀐다.
```bash
docker compose up -d --force-recreate web
docker compose exec web env | grep SESSION
```
이제 바뀐다. **왜인지 설명하라.**

**실험 D — 필수 환경변수 확인**
```bash
# .env를 잠시 다른 이름으로 옮기고
mv .env .env.bak
docker compose -f docker-compose.prod.yml config
mv .env.bak .env
```
**어떤 에러 메시지가 나오는가?** 컨테이너가 뜨는가?
`config` 하위 명령은 실제로 띄우지 않고 **해석 결과만 보여준다** — 안전한 검증 방법이다.

**실험 E — 바인드 주소 확인**
```bash
docker compose ps
ss -tlnp | grep 5432        # 또는 netstat -tlnp
```
`0.0.0.0:5432` 인가 `127.0.0.1:5432` 인가?
개발용 compose와 운영용 compose를 비교하라.

**실험 F — depends_on의 한계 재현**
```bash
docker compose down
docker compose up            # -d 없이 로그를 보면서
```
`web`이 처음에 DB 연결 실패로 죽었다가 재시작하는지 로그를 관찰하라.
그다음 운영용으로:
```bash
docker compose -f docker-compose.prod.yml up
```
헬스체크 덕에 그런 로그가 없는지 확인.

**실험 G — Caddy 크기 제한 확인**
```bash
# 700MB 더미 파일 (Caddy 한도 초과)
dd if=/dev/zero of=/tmp/huge.zip bs=1M count=700
curl -k -X POST https://<도메인>/submit \
  -H "Cookie: session=<쿠키>" -F "model_file=@/tmp/huge.zip" -i | head -20
```
**413이 어디서 나오는가?** Caddy의 영어 메시지인가, 앱의 한국어 메시지인가?
그다음 550MB(앱 한도 초과, Caddy 통과)로 해보라. 메시지가 다른가?

**실험 H — 백업/복원 리허설**
```bash
docker compose exec db pg_dump -U drleader drleader > /tmp/bk.sql
wc -l /tmp/bk.sql
head -50 /tmp/bk.sql
grep -c "INSERT INTO" /tmp/bk.sql
```
덤프 안에 스키마와 데이터가 다 있는지 확인. **복원은 테스트 DB에서만 연습할 것.**

**실험 I — 워커 생존 확인 쿼리**
```bash
docker compose exec db psql -U drleader -c \
  "SELECT worker_id, last_seen_at, now() - last_seen_at AS 경과 FROM worker_heartbeats;"
docker compose exec db psql -U drleader -c \
  "SELECT status, count(*) FROM submissions GROUP BY status;"
```
**이 두 쿼리가 운영 중 가장 자주 쓰게 될 것이다.** 별칭으로 만들어 두라.

---

→ 다음: [08-crosscutting.md](08-crosscutting.md) — 전체를 관통하는 주제 정리 + 졸업 시험
