# 공인 인터넷 노출 — GitHub Pages 검토 및 Cloudflare Tunnel 적용

> 참가자가 같은 LAN이 아니어도(다른 네트워크에서도) 리더보드에 접속·제출할 수 있어야 한다는
> 요구가 생겨, "돈 안 드는" 노출 방법을 조사했다. 두 공식 문서를 확인한 결과는 아래와 같다.

## 1. GitHub Pages — 우리 서비스에는 사용할 수 없음

참고: [GitHub Pages 공식 문서](https://docs.github.com/ko/pages/getting-started-with-github-pages),
[About GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/about-github-pages)

**핵심 사실 (공식 문서에서 확인):**
- GitHub Pages는 리포지토리의 **HTML/CSS/JavaScript 파일만** 그대로(또는 Jekyll로 빌드해서)
  서빙하는 **정적 사이트 전용** 호스팅이다. 원문: "GitHub Pages is a static site hosting service
  that takes HTML, CSS, and JavaScript files straight from a repository on GitHub."
- **서버 사이드 코드 실행이 불가능하다** — Python/FastAPI 같은 백엔드 프로세스를 띄울 수 없다.
- **데이터베이스 연결이 불가능하다** — PostgreSQL 등 외부 DB에 붙는 것 자체가 개념적으로 성립하지 않는다
  (실행되는 서버 프로세스가 없으므로).
- 커스텀 도메인 연결은 지원한다.
- 계정당 1개(개인/조직 사이트), 저장소당 1개(프로젝트 사이트)로 사이트 개수 제한이 있다.

**우리 서비스와 대조:**

| 우리 서비스에 필요한 것 | GitHub Pages 지원 여부 |
|---|---|
| 참가자 로그인(세션 기반 인증) | ❌ 서버 프로세스 필요 |
| 모델 파일 업로드 처리 | ❌ 서버 프로세스 필요 |
| PostgreSQL 조회/쓰기 (제출·시즌·팀·기록) | ❌ DB 연결 불가 |
| 평가 대기열 관리, 워커와의 연동 | ❌ 서버 프로세스 필요 |
| 관리자 페이지(시즌 생성, 실격 처리 등) | ❌ 서버 프로세스 필요 |

**결론: GitHub Pages는 이번 서비스(FastAPI + PostgreSQL + 로그인 + 파일 업로드)를 호스팅할 수 없다.**
이는 설정으로 우회할 수 있는 제약이 아니라 서비스 자체의 성격(정적 파일 전용) 때문이다.
"리더보드 결과만 주기적으로 정적 HTML로 내보내 GitHub Pages에 올리는" 방식은 기술적으로는
가능하지만, 그렇게 해도 참가자 로그인·제출 기능은 여전히 별도의 서버가 필요해 문제가 해결되지
않는다. 이번 요구(어디서든 접속해서 제출까지 가능)에는 맞지 않아 채택하지 않는다.

## 2. Cloudflare Tunnel — 채택

참고: [Cloudflare Tunnel Downloads](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/),
[TryCloudflare](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)

**`cloudflared`란:** 로컬 인프라(우리 WSL 호스트)와 Cloudflare 엣지 사이를 연결하는 경량
데몬. 공유기 포트포워딩이나 고정 공인 IP 없이도, `cloudflared`가 WSL에서 Cloudflare로
아웃바운드 연결을 맺어 외부 트래픽을 우리 로컬 서버(`localhost:8000`)로 중계한다.

**두 가지 운영 방식:**

| | Quick Tunnel (도메인 없음) | Named Tunnel (도메인 있음) |
|---|---|---|
| 명령어 | `cloudflared tunnel --url http://localhost:8000` | Cloudflare 대시보드/CLI로 터널 생성 후 DNS 레코드 연결 |
| 주소 | 실행할 때마다 무작위 `*.trycloudflare.com` | 내가 정한 고정 서브도메인(예: `leaderboard.내도메인.com`) |
| 도메인 필요 여부 | 불필요 | 필요 (Cloudflare에 도메인을 등록/연결해야 함, 도메인 자체는 유료) |
| 비용 | 완전 무료 | Cloudflare 서비스 자체는 무료, 도메인 등록비만 별도 |
| 제한사항 | 동시 요청 200개 초과 시 429, SSE(Server-Sent Events) 미지원, **Cloudflare 공식 문서가 "프로덕션 부적합, 테스트/개발용"이라 명시** | 별도 제한 없음(정식 서비스용) |
| 안정성 | 터널 재시작마다 URL이 바뀜 | URL 고정 |

**우리 상황(도메인 없음, 최대한 무료로) → Quick Tunnel 채택.**

우리 앱은 SSE를 쓰지 않고(서버 렌더링 + 페이지 새로고침 방식, spec.md에서 "평가 완료 알림 없음
— 새로고침으로 확인"이 이미 확정 사항), 동시 접속자도 시즌당 10팀 규모라 200 동시 요청 제한에
걸릴 가능성이 낮다. "프로덕션 부적합"이라는 Cloudflare의 경고는 대규모 상용 서비스 기준이며,
우리처럼 소규모 자체 운영 대회에는 충분하다고 판단한다.

**감수해야 할 제약:**
- **터널을 껐다 켜면 URL이 바뀐다.** 대회 기간 내내 `cloudflared` 프로세스를 죽이지 않고
  유지해야 참가자에게 안내한 링크가 계속 유효하다. (노트북을 계속 켜둬야 하는 건 워커/DRFC도
  마찬가지라 이번 결정으로 새로 생기는 제약은 아니다.)
- 나중에 도메인을 구입하면 Named Tunnel로 전환해 고정 주소를 쓸 수 있다 — 전환 시 이 문서를
  갱신한다.

## 3. 설정 절차

### 3.1 `cloudflared` 설치 (WSL Ubuntu)

`.deb` 패키지 설치는 `sudo`가 필요해 비대화형 환경에서 비밀번호 입력에 막힌다. 단일 실행 파일을
홈 디렉터리에 받는 방식이 sudo 없이 되고 더 간단하다 (2026-07-25 이 방식으로 설치·검증 완료):

```bash
mkdir -p ~/bin
curl -L --output ~/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x ~/bin/cloudflared
~/bin/cloudflared --version
```

### 3.2 Quick Tunnel 실행

```bash
~/bin/cloudflared tunnel --url http://localhost:8000
```

실행하면 콘솔에 `https://<무작위문자열>.trycloudflare.com` 형태의 주소가 출력된다. 이 주소를
참가자에게 공지한다.

### 3.3 백그라운드로 유지

워커·웹 앱과 마찬가지로 대회 기간 내내 떠 있어야 하므로:

```bash
setsid nohup ~/bin/cloudflared tunnel --url http://localhost:8000 > /tmp/cloudflared.log 2>&1 < /dev/null &
```

발급된 주소는 `/tmp/cloudflared.log`에서 확인한다 (터널이 올라오기까지 10초 정도 걸린다):

```bash
grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' /tmp/cloudflared.log | head -1
```

### 3.4 종료

```bash
pkill -f 'cloudflared tunnel'
```

### 3.5 검증 기록 (2026-07-25)

실제로 위 절차대로 설치·실행해 다음을 확인했다:

- `cloudflared version 2026.7.3` 설치 성공 (sudo 없이 `~/bin`에)
- 발급 주소로 `GET /healthz` → `HTTP 200 {"status":"ok"}`
- 발급 주소로 리더보드 시즌 목록 페이지, 팀 로그인 페이지 정상 렌더링

주의: 이 검증 과정에서 `web` 컨테이너가 뜨지 않는 문제를 겪었는데, 원인은 Cloudflare와 무관했다 —
WSL에서 `uvicorn`을 직접 띄워둔 프로세스가 8000번 포트를 점유하고 있어 컨테이너가 포트 바인딩에
실패했고, 그 상태로 남은 컨테이너가 네트워크에 붙지 못한 채 재시작 루프에 빠져 있었다
(`could not translate host name "db"`). `docker compose up -d --force-recreate web`로 해결했다.
자세한 대응은 [../../docs/operational-error-handling.md](../../docs/operational-error-handling.md) 참고.

## 4. 애플리케이션 쪽 변경 사항

- `SESSION_SECRET` 기본값(`change-me-in-production`)을 실제 운영 전 반드시 `.env`에서
  무작위 값으로 바꿔야 한다 — 지금까지는 LAN 내부용이라 위험이 낮았지만, 공인 인터넷에 노출되면
  기본값 그대로 두는 것은 세션 위조 위험으로 이어진다.
- 세션 쿠키의 HTTPS 강제 여부를 `SESSION_HTTPS_ONLY` 환경변수로 제어할 수 있게 했다
  (`app/config.py`의 `session_https_only`, 기본값 `false`). Cloudflare Tunnel로 실제 대회를
  운영할 때는 `.env`에 `SESSION_HTTPS_ONLY=true`를 설정한다 — 참가자 브라우저 ↔ Cloudflare
  구간이 HTTPS이므로 쿠키도 HTTPS에서만 전송되게 맞추는 것이 안전하다.
  **주의**: 이 값이 `true`인 상태에서 터널 없이 `http://localhost:8000`으로 직접 접속하면
  브라우저가 Secure 쿠키를 저장하지 않아 로그인이 깨진다 — 그래서 기본값은 `false`로 두고,
  터널을 실제로 띄워서 운영하는 시점에만 켠다.
