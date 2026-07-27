# 클로드(Claude) 100% 활용 코딩 가이드

> Anthropic 공식 문서(code.claude.com/docs, platform.claude.com/docs)를 참고해 정리한 실전 가이드입니다.
> 모델 이름·가격·명령어 목록 등은 시간이 지나면 바뀔 수 있으니, 중요한 결정을 내리기 전에는 공식 문서로 최신 정보를 다시 확인하세요.

---

## 목차

1. [Claude Code란?](#1-claude-code란)
2. [설치 및 시작하기](#2-설치-및-시작하기)
3. [핵심 기능](#3-핵심-기능)
4. [효과적으로 프롬프트 작성하기](#4-효과적으로-프롬프트-작성하기)
5. [생산성을 높이는 워크플로우](#5-생산성을-높이는-워크플로우)
6. [모델 선택 가이드](#6-모델-선택-가이드)
7. [요금제 및 비용 관리](#7-요금제-및-비용-관리)
8. [바로 써먹는 추천 설정 체크리스트](#8-바로-써먹는-추천-설정-체크리스트)
9. [GitHub 저장소(anthropics/claude-code) 실전 분석](#9-github-저장소anthropicsclaude-code-실전-분석)
10. [지금 이 세션의 클로드는 실제로 어떻게 동작하는가](#10-지금-이-세션의-클로드는-실제로-어떻게-동작하는가)
11. [참고 링크 모음](#11-참고-링크-모음)

---

## 1. Claude Code란?

Claude Code는 **코드베이스 전체를 읽고, 파일을 수정하고, 명령어를 실행하며, 개발 도구와 연동하는 에이전틱(agentic) 코딩 도구**입니다. 아래와 같은 다양한 환경에서 사용할 수 있습니다.

- **터미널 CLI** (가장 기본이 되는 핵심 경험)
- **VS Code 확장 프로그램** (인라인 diff가 표시되는 패널)
- **JetBrains 플러그인** (IntelliJ, PyCharm, WebStorm 등)
- **데스크톱 앱** (시각적 diff, 예약 작업 지원)
- **웹(claude.ai/code)** (GitHub 연동 클라우드 세션)
- **모바일 앱** (iOS/Android, 원격 제어)
- **Slack 연동** (`@Claude` 태그로 채널에서 호출)

### 기본 동작 루프
1. 코드베이스를 필요할 때마다 읽는다
2. 변경안을 제안하거나 직접 수정한다
3. 테스트·빌드 등으로 검증한다
4. 결과를 바탕으로 통과할 때까지 반복한다

### 핵심 역량
- 여러 파일에 걸친 코드 생성·리팩터링·버그 수정
- `Bash` 도구로 테스트·빌드·배포·커밋 등 명령 실행
- Git 연동(커밋, 브랜치 생성, PR 오픈, 충돌 해결)
- `CLAUDE.md`와 자동 메모리를 통한 세션 간 프로젝트 기억
- 사용자가 통제하는 권한 기반 도구 접근(파일 읽기/쓰기, 셸 명령)
- 세션 이어가기(여러 번에 걸친 작업 재개)
- 서브에이전트·백그라운드 세션·워크트리를 통한 병렬 작업

---

## 2. 설치 및 시작하기

| 방법 | 명령어 | 자동 업데이트 | 플랫폼 |
|---|---|---|---|
| 네이티브(권장) | `curl -fsSL https://claude.ai/install.sh \| bash` (macOS/Linux/WSL) | O | 전체 |
| 네이티브(Windows) | `irm https://claude.ai/install.ps1 \| iex` (PowerShell) | O | Windows |
| Homebrew | `brew install --cask claude-code` | X (`brew upgrade`) | macOS |
| WinGet | `winget install Anthropic.ClaudeCode` | X (`winget upgrade`) | Windows |
| apt/dnf/apk | `apt install claude-code` 등 | O | Linux |

### 처음 실행하기
```bash
claude --version                 # 설치 확인
cd /내/프로젝트/경로 && claude    # 첫 세션 시작 (브라우저에서 로그인)
```
로그인은 Claude Pro/Max/Team/Enterprise 구독, Claude Console(API) 계정, Amazon Bedrock, Google Cloud, Microsoft Foundry 중 선택할 수 있습니다.

### 생성되는 주요 파일/경로
- `~/.claude/settings.json` — 사용자 전역 설정
- `~/.claude/CLAUDE.md` — 사용자 전역 프로젝트 메모리
- `~/.claude/projects/<프로젝트>/memory/MEMORY.md` — 프로젝트별 자동 메모리
- `.claude/` — 프로젝트 로컬 설정(git으로 관리 가능)

---

## 3. 핵심 기능

### 3.1 CLAUDE.md — 프로젝트 메모리

Claude는 **세션을 시작할 때마다** `CLAUDE.md` 파일을 읽습니다. 두 종류가 함께 동작합니다.

- **CLAUDE.md** — 사람이 직접 작성하는 지침(빌드 명령, 코딩 규칙 등)
- **자동 메모리(Auto memory)** — Claude가 스스로 기록하는 학습 내용, 발견한 패턴

| 범위 | 경로 | 대상 | 용도 |
|---|---|---|---|
| 관리형(엔터프라이즈) | OS별 시스템 경로 | 조직 전체 | 보안 정책, 조직 표준 |
| 사용자 | `~/.claude/CLAUDE.md` | 나, 모든 프로젝트 | 개인 선호(테마, 단축키 등) |
| 프로젝트 | `./CLAUDE.md` 또는 `./.claude/CLAUDE.md` | 팀(git 공유) | 빌드 명령, 코딩 표준, 아키텍처 |
| 로컬 | `./.claude/CLAUDE.local.md` | 나, 이 프로젝트만(.gitignore 대상) | 개인 샌드박스 URL, 테스트 데이터 |

로드 순서는 관리형 → 사용자 → 프로젝트 → 로컬이며, 뒤에 로드된 내용이 컨텍스트에서 더 최근에 등장합니다.

**효과적인 CLAUDE.md 작성 팁 (200줄 이내 권장)**
- 빌드/테스트 명령: `npm test`, `npm run build`
- 코드 스타일 규칙: "CommonJS 대신 ES 모듈 사용", "들여쓰기는 2칸"
- 폴더 구조: "API 핸들러는 `src/api/handlers/`에 위치"
- 워크플로우 규칙: "커밋 전 `npm run lint` 실행"
- 아키텍처 결정 사항, 자주 겪는 함정(gotcha)

**넣지 말아야 할 것**: 코드만 봐도 유추 가능한 내용, 긴 튜토리얼(링크로 대체), 자주 바뀌는 정보.

**시작 파일 자동 생성**
```bash
/init
```
코드베이스를 분석해 초안 CLAUDE.md를 생성해 줍니다. 이후 직접 다듬으면 됩니다.

**다른 파일 임포트**
```markdown
개요는 @README.md, 명령어는 @package.json 참고.
```

**대형 프로젝트는 `.claude/rules/`로 경로별 규칙 분리**
```markdown
---
paths:
  - "src/api/**/*.ts"
---
# API 설계 규칙
- URL 경로는 kebab-case
- 모든 엔드포인트에 검증(validation) 포함
```

로드된 CLAUDE.md 목록은 `/context`로 확인할 수 있습니다.

### 3.2 슬래시 명령어(자주 쓰는 것 위주)

`/`만 입력하면 사용 가능한 전체 명령어와 스킬 목록이 뜹니다.

| 명령어 | 기능 |
|---|---|
| `/init` | 코드베이스 분석 후 CLAUDE.md 생성/개선 |
| `/memory` | CLAUDE.md·자동 메모리 편집, 자동 메모리 on/off |
| `/mcp` | MCP 서버 추가/조회/삭제, 인증 |
| `/permissions` | 도구 허용/차단 규칙 설정 |
| `/plan` | 플랜 모드로 전환(수정 없이 제안만) |
| `/model [이름]` | 모델 전환(opus, sonnet, haiku, fable) |
| `/effort [단계]` | 추론 강도 설정(low/medium/high/xhigh/max) |
| `/context [all]` | 현재 컨텍스트 사용량 확인 |
| `/compact [지침]` | 대화 이력을 요약해 공간 확보 |
| `/diff` | 대기 중인 변경사항을 대화형 diff로 표시 |
| `/code-review [수준]` | 버그·정리 관점 리뷰(ultra는 클라우드 리뷰) |
| `/security-review` | 보안 취약점 스캔 |
| `/clear` | 새 대화 시작(파일은 유지) |
| `/resume` | 이전 대화 목록에서 재개 |
| `/branch [이름]` | 현재 지점에서 새 대화 브랜치 생성 |
| `/background [작업]` | 현재 세션을 백그라운드로 분리 |
| `/tasks` | 백그라운드 서브에이전트/세션 목록 |
| `/config` | 설정 UI 열기 |
| `/status` | 현재 설정 상태 확인 |
| `/doctor` | 설치·설정 상태 점검 |
| `/help` | 사용 가능한 명령어·스킬 확인 |

### 3.3 커스텀 명령어 & 스킬

- **스킬(Skills)**: 필요할 때(호출 시 또는 관련성이 인식될 때)만 로드 — 긴 절차나 도메인 지식에 적합
- **CLAUDE.md**: 매 세션 항상 로드 — 항상 필요한 사실에 적합

경로: `.claude/skills/<스킬이름>/SKILL.md`

```markdown
---
name: fix-issue
description: GitHub 이슈를 처음부터 끝까지 해결
---

## 절차
1. 이슈 상세 확인: `gh issue view $ARGUMENTS`
2. 문제 파악
3. 관련 파일 탐색
4. 수정 구현
5. 테스트 작성 및 실행
6. 린트 통과 확인
7. 설명이 담긴 커밋 작성
8. PR 생성
```

호출: `/fix-issue 1234`

### 3.4 서브에이전트(Subagents) — 전문화된 병렬 작업자

독립된 컨텍스트 창에서 실행되는 특화된 보조 에이전트입니다. 탐색·리뷰처럼 파일을 많이 읽어야 하는 작업, 독립적인 검증이 필요한 작업에 유용합니다.

경로: `.claude/agents/<이름>.md`
```markdown
---
name: security-reviewer
description: 보안 관점 코드 리뷰 전문가
tools: Read, Grep, Glob, Bash
model: opus
---

당신은 시니어 보안 엔지니어입니다. 다음을 점검하세요:
- 인젝션 취약점(SQL, XSS, 커맨드 인젝션)
- 인증/인가 결함
- 코드 내 비밀정보 노출
```

호출 예: `"이 코드를 서브에이전트로 보안 검토해줘"`

### 3.5 훅(Hooks) — 결정론적 자동화

CLAUDE.md는 권고사항이지만, 훅은 특정 시점에 **반드시 실행**되는 셸 명령입니다.

```json
{
  "hooks": {
    "PostFileEdit": [
      {
        "pathPattern": "**/*.{js,ts}",
        "command": "npm run lint -- --fix $FILEPATH"
      }
    ]
  }
}
```
`.claude/settings.json` 또는 `~/.claude/settings.json`에 설정합니다. (자세한 이벤트 목록·변수는 최신 공식 문서 참고)

### 3.6 MCP(Model Context Protocol) — 외부 도구 연동

GitHub, Jira, Notion, 데이터베이스 등 외부 도구/데이터 소스를 연결하는 개방형 프로토콜입니다.

```bash
claude mcp add --transport http claude-code-docs https://code.claude.com/docs/mcp
claude mcp add playwright -- npx -y @playwright/mcp@latest
claude mcp list
claude mcp remove playground
```

프로젝트 전체 공유 설정은 저장소 루트의 `.mcp.json`에 작성해 git으로 관리할 수 있습니다.

### 3.7 플랜 모드(Plan Mode)

Claude가 파일을 수정하지 않고 **변경 계획만 제안**하고, 사용자가 검토·승인한 뒤에 실제 작업을 진행합니다.

- 전환: `Shift+Tab`으로 모드 순환, 또는 `/plan`
- 낯선 코드베이스 탐색, 여러 파일에 걸친 대규모 리팩터링 전에 특히 유용
- 사소한 수정(오타, 단일 변수 변경)에는 생략해도 무방

### 3.8 확장 사고(Extended Thinking) / Effort

복잡한 문제를 풀기 전에 모델이 더 깊이 사고하도록 조절합니다.
```bash
/effort low | medium | high | xhigh | max
```
디버깅이 어려운 버그, 복잡한 알고리즘, 아키텍처 결정, 보안 리뷰에는 `high` 이상을 권장하고, 단순 반복 작업에는 `low`로 속도를 높입니다.

### 3.9 권한 모드(Permission Modes)

`Shift+Tab`으로 순환합니다.

| 모드 | 설명 |
|---|---|
| default(수동) | 읽기만 자동, 쓰기/명령은 매번 확인 |
| acceptEdits | 파일 수정 + 안전한 Bash 명령 자동 승인 |
| plan | 제안만 하고 실행하지 않음 |
| auto | 대부분 자동 승인(위험 동작은 별도 차단 규칙 적용) — 신뢰할 수 있는 장시간 작업에 적합 |
| bypassPermissions | 모든 것을 검사 없이 허용 — **격리된 컨테이너 등에서만** 사용 |

`settings.json`의 `permissions.allow` / `permissions.deny`로 특정 도구를 사전 승인·차단할 수 있습니다. (`Bash(npm test)` 처럼 패턴 지정)

### 3.10 백그라운드 작업 & 병렬 세션

- 현재 세션을 백그라운드로 분리: `/background "작업 설명"`
- 진행 중인 백그라운드 작업 목록: `/tasks`
- 워크트리로 완전히 격리된 병렬 세션: `claude --worktree feature-이름`

### 3.11 Git 연동

자연어로 상태 확인, diff 보기, 브랜치 생성, 커밋 메시지 작성, PR 생성, 충돌 해결까지 요청할 수 있습니다.
```
"변경된 파일 보여줘" / "설명이 담긴 커밋 메시지로 커밋해줘" / "이 기능으로 PR 만들어줘"
```

### 3.12 IDE 연동

- **VS Code**: 확장 프로그램 설치 → 인라인 diff, `Ctrl+Esc`(포커스 전환), `Alt+K`(파일+라인 참조 삽입)
- **JetBrains**: 플러그인 설치(CLI도 함께 필요) → IDE 통합 터미널에서 `claude` 실행, `/ide`로 외부 터미널과 연결

---

## 4. 효과적으로 프롬프트 작성하기

**핵심 원칙: 컨텍스트 창은 빨리 찬다. 적극적으로 관리하라.**

1. **검증 방법을 함께 제시하라**
   - ❌ "이메일 검증 함수를 구현해줘"
   - ✅ "이메일 검증 함수를 구현해줘. `user@example.com`은 true, `invalid`는 false여야 해. 구현 후 테스트를 실행해줘."

2. **탐색 → 계획 → 구현 → 커밋 순서로 진행하라** (5장 참고)

3. **구체적으로 요청하라**
   - ❌ "로그인 버그 고쳐줘"
   - ✅ "세션 타임아웃 이후 로그인이 실패한다는 리포트가 있어. `src/auth/`의 토큰 갱신 로직을 확인하고, 재현하는 실패 테스트를 먼저 작성한 뒤 고쳐줘."

4. **풍부한 컨텍스트를 제공하라**: `@파일명`으로 특정 파일 참조, 에러 메시지·스택트레이스·스크린샷 붙여넣기, 참고할 기존 패턴 지정

5. **빠르게 피드백하고 궤도를 수정하라**: `Esc`로 중단, "그거 되돌려줘"로 취소, 같은 문제로 2번 이상 헛돌면 `/clear` 후 더 나은 프롬프트로 재시작

6. **Claude가 직접 조사하게 하라**: 모든 맥락을 미리 다 주기보다 "인증 시스템이 토큰 갱신을 어떻게 처리하는지 조사해줘"처럼 위임하면 컨텍스트를 아낄 수 있다

7. **역할 부여(role prompting)와 예시(few-shot)를 활용하라**: "당신은 15년 차 백엔드 아키텍트입니다..." / "좋은 커밋 메시지 예시는 다음과 같습니다: ..."

8. **복잡한 작업은 프롬프트를 체인으로 나눠라**: (1)접근법 계획 → (2)계획대로 구현 → (3)구현 리뷰

---

## 5. 생산성을 높이는 워크플로우

### "탐색 → 계획 → 코드 → 커밋" (Anthropic이 권장하는 기본 패턴)

1. **탐색(플랜 모드)**: "`/src/auth`를 읽고 세션 처리 방식을 파악해줘" (수정 없이 읽기만)
2. **계획**: "Google OAuth를 추가하려는데, 어떤 파일을 바꿔야 할지 계획을 세워줘" → `Ctrl+G`로 계획을 직접 편집 가능
3. **구현**: 계획을 승인하고 실행 모드로 전환 → "계획대로 OAuth 플로우를 구현하고, 테스트를 작성·실행해줘"
4. **커밋**: "설명이 담긴 메시지로 커밋하고 PR을 열어줘"

간단한 변경(오타 수정, 단일 파일 리네임)은 탐색·계획 단계를 생략해도 됩니다.

### 컨텍스트 관리 전략
- 탐색·리서치는 서브에이전트에 위임해 메인 세션을 깨끗하게 유지
- 관련 없는 작업 사이에는 `/clear`
- 대화가 길어지면 `/compact "API 변경 관련해서만 요약해줘"`
- `/context`로 현재 사용량 수시 확인

### 병렬 작업 패턴
- **작성자 + 검토자**: 한 세션에서 구현, 다른(신선한 컨텍스트) 세션/서브에이전트에서 독립적으로 리뷰
- **워크트리 병렬 작업**: `claude --worktree feature-a`, `claude --worktree feature-b`로 브랜치별 완전 격리
- **비대화형(CI/스크립팅) 모드**:
  ```bash
  claude -p "최근 커밋을 보안 관점에서 리뷰해줘" --output-format json
  git log --oneline -20 | claude -p "최근 커밋들을 요약해줘"
  ```

---

## 6. 모델 선택 가이드

| 모델 | 용도 | 특징 |
|---|---|---|
| **Opus 5** | 복잡한 에이전틱 작업, 깊은 추론이 필요한 보안 리뷰·아키텍처 설계 | 품질 우선, 속도는 다소 느림 |
| **Sonnet 5** | 일상적인 코딩(기능 구현, 버그 수정, 리팩터링) | 속도와 지능의 균형이 좋아 기본값으로 적합 |
| **Fable 5** | 가장 길고 복잡한 코딩 작업, 최고 수준의 역량이 필요할 때 | 지연 시간이 다소 더 걸릴 수 있음 |
| **Haiku 4.5** | 빠른 설명, 단순 편집(변수명 변경 등), 비용에 민감한 대량 병렬 작업 | 가장 빠르고 저렴, 서브에이전트 라우팅에 적합 |

전환: `/model opus`, `/model sonnet`, `/model fable`, `/model haiku`

**추론 강도(effort)**: 어려운 디버깅·복잡한 알고리즘·아키텍처 결정·보안 리뷰에는 `high`~`max`, 단순 반복 작업에는 `low`로 속도와 비용을 아낄 수 있습니다.

---

## 7. 요금제 및 비용 관리

Claude Code는 Claude Pro/Max/Team/Enterprise 구독 또는 API(Console) 종량제 계정으로 사용할 수 있습니다. 정확한 최신 가격과 플랜별 세부 기능은 반드시 [claude.com/pricing](https://claude.com/pricing) 공식 페이지에서 확인하세요(모델 가격, 플랜 구성은 자주 바뀝니다).

### 비용을 아끼는 방법
1. 작업 난이도에 맞는 모델 선택(단순 작업엔 Haiku, 복잡한 작업엔 Opus/Fable)
2. 필요하지 않으면 `/effort low`로 사고 토큰 절약
3. 탐색·리서치는 서브에이전트(저렴한 모델)에 위임
4. 프롬프트 캐싱 활용(반복되는 컨텍스트는 비용이 절감됨)
5. 관련 없는 작업 사이에는 `/clear`로 컨텍스트 크기 관리
6. 스크립팅에는 `claude -p` 비대화형 모드 사용

현재 사용량은 `/usage`(또는 `/status`)로 확인할 수 있습니다.

---

## 8. 바로 써먹는 추천 설정 체크리스트

- [ ] Claude Code 설치 후 `claude --version`으로 확인
- [ ] 프로젝트 루트에서 `/init` 실행 → CLAUDE.md 초안 생성 후 직접 다듬기
- [ ] 처음엔 `default`(수동 승인) 모드로 익숙해진 뒤 `acceptEdits`로 전환
- [ ] VS Code/JetBrains 사용 시 확장 프로그램·플러그인 설치, IDE 통합 터미널에서 `claude` 실행
- [ ] 자주 쓰는 외부 도구(GitHub 등)를 MCP로 연결 (`claude mcp add ...`)
- [ ] 반복되는 작업 절차는 `.claude/skills/`에 스킬로 만들어두기
- [ ] 탐색이 많이 필요한 리뷰/조사는 서브에이전트에 위임해 메인 컨텍스트 보호
- [ ] `Shift+Tab`(모드 전환), `Esc`(중단), `Esc Esc`(되돌리기) 단축키 익히기
- [ ] 큰 작업은 "탐색 → 계획 → 구현 → 커밋" 순서로 진행
- [ ] 대화가 길어지면 `/context`로 확인 후 `/compact` 또는 `/clear`

---

## 9. GitHub 저장소(anthropics/claude-code) 실전 분석

공식 문서 사이트 외에, Claude Code의 **소스와 예제가 실제로 담겨 있는 GitHub 저장소** [github.com/anthropics/claude-code](https://github.com/anthropics/claude-code)를 직접 뜯어보면 "이걸 어떻게 설정해야 하는가"에 대한 답이 코드/설정 파일 형태로 그대로 들어있습니다. (139k+ stars, Node.js 18+, 라이선스는 Anthropic 상용 이용약관 적용 — 오픈소스 라이선스가 아닙니다.)

### 9.1 저장소 구조 한눈에 보기

```
claude-code/
├── .claude/commands/        # 이 저장소 자체가 쓰는 커스텀 슬래시 명령
├── .claude-plugin/          # 플러그인 매니페스트
├── .devcontainer/           # 팀 개발용 컨테이너 (Dockerfile, 방화벽 스크립트)
├── .github/workflows/       # Claude를 활용한 이슈 트리아지 등 12개 CI 워크플로
├── examples/
│   ├── gateway/aws|gcp/     # 엔터프라이즈용 클라우드 게이트웨이(Bedrock/Vertex) 예제
│   ├── hooks/                # PreToolUse 훅 예제(Python)
│   ├── mdm/                  # macOS/Windows MDM 배포 템플릿
│   └── settings/             # lax/strict/sandbox 3단계 settings.json 예제
├── plugins/                  # 공식 플러그인 13종
├── scripts/                  # 이슈 관리 자동화 스크립트
├── README.md / CHANGELOG.md / SECURITY.md
```

### 9.2 공식 플러그인 13종 — 무엇을 할 수 있는가

플러그인은 명령어·서브에이전트·훅·MCP 설정을 하나로 묶어 배포하는 단위입니다. `/plugin install <이름>@<소스>`로 설치합니다.

| 플러그인 | 대표 명령/트리거 | 하는 일 |
|---|---|---|
| `code-review` | `/code-review` | 4개 에이전트 병렬 실행 — CLAUDE.md 준수, 버그, git 히스토리 맥락까지 확인, 확신도(신뢰도 80 이상)만 리포트 |
| `pr-review-toolkit` | `/pr-review-toolkit:review-pr` | 댓글·테스트·에러·타입·단순화 관점 6개 에이전트로 PR 리뷰 |
| `feature-dev` | `/feature-dev` | 요구사항 파악→코드베이스 탐색→질문→아키텍처 3안 제시→구현→3에이전트 품질 검토→요약까지 7단계 자동 진행 |
| `commit-commands` | `/commit`, `/commit-push-pr`, `/clean_gone` | 브랜치 생성→커밋→푸시→PR 생성까지 한 번에 |
| `security-guidance` | PreToolUse 훅(자동) | 3단계 보안 감시: 위험 패턴 25종 실시간 경고 → LLM diff 리뷰 → 데이터 흐름 추적 커밋 리뷰 |
| `hookify` | `/hookify` | 마크다운만으로 커스텀 방지 훅 생성(코딩 불필요) |
| `plugin-dev` | `/plugin-dev:create-plugin` | 훅/커맨드/에이전트/스킬 개발까지 아우르는 8단계 플러그인 제작 툴킷 |
| `agent-sdk-dev` | `/new-sdk-app <이름>` | Python/TypeScript Agent SDK 프로젝트 스캐폴딩 + 자동 검증 |
| `ralph-wiggum` | `/ralph-loop` | 자율 반복 개발 루프 |
| `frontend-design` | 자동 트리거 | 프로덕션 수준 프론트엔드 디자인 가이드 적용 |
| `explanatory-output-style` / `learning-output-style` | SessionStart 훅 | 설명 중심/학습 중심 출력 스타일로 전환 |
| `claude-opus-4-5-migration` | 자동 트리거 | 구버전 모델 대상 코드를 신모델로 마이그레이션 지원 |

**플러그인 폴더 구조**(직접 만들 때 참고):
```
plugin-name/
├── .claude-plugin/plugin.json   # 메타데이터
├── commands/*.md                 # 슬래시 명령 (선택)
├── agents/*/AGENT.md             # 서브에이전트 (선택)
├── skills/*.md                   # 스킬 (선택)
├── hooks/*.py                    # 이벤트 훅 (선택)
├── .mcp.json                     # 외부 MCP 서버 (선택)
└── README.md
```

### 9.3 신뢰 수준별 settings.json 3단계 예제

저장소 `examples/settings/`에 실제로 들어있는, 신뢰 수준별 참고 설정입니다. 그대로 복사해서 팀 상황에 맞게 조정하면 됩니다.

**① `settings-lax.json`** — 개인용, 느슨한 설정
```json
{
  "permissions": { "disableBypassPermissionsMode": "disable" },
  "strictKnownMarketplaces": []
}
```

**② `settings-strict.json`** — 팀/조직용, 엄격한 설정 (웹 검색·조회 차단, 관리형 규칙만 허용)
```json
{
  "permissions": {
    "disableBypassPermissionsMode": "disable",
    "ask": ["Bash"],
    "deny": ["WebSearch", "WebFetch"]
  },
  "allowManagedPermissionRulesOnly": true,
  "allowManagedHooksOnly": true,
  "strictKnownMarketplaces": [],
  "sandbox": {
    "autoAllowBashIfSandboxed": false,
    "network": { "allowedDomains": [], "allowAllUnixSockets": false }
  }
}
```

**③ `settings-bash-sandbox.json`** — 셸 명령까지 샌드박스로 강제 격리
```json
{
  "allowManagedPermissionRulesOnly": true,
  "sandbox": {
    "enabled": true,
    "autoAllowBashIfSandboxed": false,
    "allowUnsandboxedCommands": false,
    "network": { "allowedDomains": [], "allowAllUnixSockets": false }
  }
}
```

**적용 방법**: 위 내용을 `.claude/settings.json`(팀 공유, git 커밋) 또는 엔터프라이즈라면 MDM으로 배포하는 `managed-settings.json`에 넣으면 됩니다. 개인 노트북에서 손쉽게 시작하고 싶다면 lax, 사내 표준으로 강제하고 싶다면 strict/bash-sandbox를 기반으로 커스터마이징하세요.

### 9.4 훅(Hook) 실전 예제 — 위험한 Bash 명령 차단

`examples/hooks/bash_command_validator_example.py`는 Claude가 Bash 도구를 쓰기 **직전(PreToolUse)**에 명령어를 검사하는 Python 훅입니다. 예를 들어 `grep` 대신 `rg`를, `find -name` 대신 `rg --files`를 쓰도록 권고합니다.

`settings.json`에 연결하는 방법:
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "python3 /path/to/bash_command_validator_example.py" }
        ]
      }
    ]
  }
}
```
훅 스크립트의 종료 코드 의미: `0`=통과, `1`=사용자에게만 에러 표시, `2`=실행 차단하고 Claude에게 사유 전달.

### 9.5 팀 개발 표준화 — Devcontainer

`.devcontainer/devcontainer.json`은 팀 전체가 동일한 환경에서 Claude Code를 쓰도록 하는 표준 예제입니다.
- Node.js 20 베이스, VS Code 확장(Claude Code, ESLint, Prettier, GitLens) 자동 설치
- bash 히스토리와 `~/.claude` 설정을 볼륨으로 영속화
- `postStartCommand`로 `init-firewall.sh` 실행 → **기본 차단(DROP), GitHub·npm·api.anthropic.com 등만 허용**하는 아웃바운드 방화벽 자동 구성
- `NET_ADMIN`, `NET_RAW` 권한으로 컨테이너 내부에서 네트워크 정책 제어

즉, "Claude에게 컨테이너 안에서는 좀 더 자유롭게 맡기되, 네트워크는 화이트리스트로 봉쇄"하는 패턴을 그대로 가져다 쓸 수 있습니다.

### 9.6 GitHub Actions 연동 — 저장소 자체가 실사용 사례

`.github/workflows/`의 12개 워크플로 중 다수가 **Claude 자신을 이슈 관리에 활용**합니다.
- `claude.yml` — PR/이슈에서 `@claude` 멘션 시 반응하는 메인 워크플로
- `claude-issue-triage.yml` — 새 이슈를 자동으로 라벨링(버그/기능요청/질문 등)
- `claude-dedupe-issues.yml` — 중복 이슈 자동 탐지
- `auto-close-duplicates.yml`, `lock-closed-issues.yml`, `issue-lifecycle-comment.yml` 등 — 이슈 생명주기 자동화

**내 저장소에 적용하는 팁**: `claude.yml` 패턴을 그대로 가져와 PR 코멘트에 `@claude`가 멘션되면 리뷰/수정 제안을 자동으로 달게 하거나, 신규 이슈에 자동 라벨링을 붙이는 식으로 CI에 통합할 수 있습니다.

### 9.7 엔터프라이즈 배포 — Gateway & MDM

- **클라우드 게이트웨이** (`examples/gateway/aws|gcp/`): OIDC(Okta/Google) 인증 + Bedrock/Vertex AI를 백엔드로 하는 사내 게이트웨이를 Terraform으로 구성하는 예제. 여러 팀이 하나의 관문을 통해 안전하게 Claude를 쓰게 할 때 사용.
- **MDM 배포** (`examples/mdm/`): Windows(그룹 정책 ADMX, Intune PowerShell), macOS(Jamf/Kandji용 `.mobileconfig`)로 `managed-settings.json`을 조직 전체 PC에 강제 배포하는 템플릿. IT 관리자가 개별 개발자 설정을 덮어쓸 수 없는 "관리형 규칙"으로 강제하고 싶을 때 사용.

### 9.8 저장소 자체의 커스텀 명령 (참고용 실전 예시)

`.claude/commands/`에 있는 이 저장소 전용 명령들도 그대로 참고할 만합니다.
- `commit-push-pr.md` — 브랜치 생성→커밋→푸시→PR 생성을 한 번의 응답으로 처리
- `triage-issue.md` — Claude Code 관련 이슈인지 신호(⁠`claude` CLI, `.claude/`, 확장 프로그램, MCP 언급 등)로 판별 후 카테고리·생명주기 라벨 자동 부여
- `dedupe.md` — 중복 이슈 탐지 워크플로

### 9.9 보안 취약점 제보

`SECURITY.md`에 따르면 **공개 이슈로 보안 취약점을 등록하지 말 것**을 명시하고 있으며, [HackerOne](https://hackerone.com/anthropic)을 통한 비공개 제보와 버그 바운티 프로그램을 운영합니다.

### 9.10 결론 — 사용자 유형별 추천 시작점

| 상황 | 추천 시작점 |
|---|---|
| 혼자 빠르게 실험 | `examples/settings/settings-lax.json` 기반 설정 + `plugins/commit-commands`, `plugins/code-review` 설치 |
| 팀 표준화 필요 | `.devcontainer/` 그대로 도입 + `settings-strict.json` 기반 `.claude/settings.json` 팀 공유 |
| 대규모 기능 개발 | `plugins/feature-dev`의 7단계 워크플로 활용 |
| 보안이 중요한 조직 | `plugins/security-guidance` 설치 + `settings-bash-sandbox.json` + 훅으로 위험 명령 사전 차단 |
| 엔터프라이즈 전사 배포 | `examples/mdm/`로 관리형 정책 강제 + 필요 시 `examples/gateway/`로 사내 게이트웨이 구축 |
| CI에서 Claude 활용 | `.github/workflows/claude.yml` 패턴을 자체 저장소에 이식 |

---

## 10. 지금 이 세션의 클로드는 실제로 어떻게 동작하는가

앞의 내용이 "공식 문서/저장소가 설명하는 Claude Code"라면, 이 절은 **지금 이 대화에서 실제로 동작 중인 나(클로드)를 있는 그대로** 설명한 것입니다. 환경/설정에 따라 달라질 수 있는 부분이니, 다른 프로젝트·다른 권한 설정에서는 다를 수 있습니다.

### 10.1 기본 정체성
- 이 세션은 **Claude Sonnet 5** 모델이 **Claude Code CLI 에이전트**로 동작하고 있는 것입니다. Windows 11 PowerShell(주 셸)과 Bash 도구를 함께 쓸 수 있는 환경입니다.
- 지식 컷오프는 2026년 1월이며, 그 이후의 사실은 웹 조사(WebFetch/WebSearch)나 사용자가 제공한 정보로만 알 수 있습니다.
- 대화가 길어지면 이전 내용이 자동으로 요약(압축)되어, 대화 도중 컨텍스트가 부족해 작업을 중단할 필요는 없습니다.

### 10.2 반복되는 동작 루프(에이전틱 루프)
1. 사용자 요청을 읽는다
2. 필요하면 도구를 호출해 코드/파일/웹을 조사한다
3. 파일을 읽고, 수정하고, 명령을 실행한다
4. 결과를 검증한다(테스트 실행, diff 확인 등)
5. 사용자에게 짧게 진행 상황을 보고하고, 필요하면 반복한다

### 10.3 지금 쓸 수 있는 도구 목록과 역할

| 도구 | 역할 |
|---|---|
| `Bash` / `PowerShell` | 셸 명령 실행(git, npm, 테스트, 빌드 등). 이 환경은 PowerShell이 기본, Bash는 POSIX 스크립트용 별도 도구 |
| `Read` / `Edit` / `Write` | 파일 읽기/부분 수정/새 파일 작성(전체 재작성). 기존 파일 수정은 Edit을 우선 사용 |
| `Grep` / `Glob` | 코드 내용 검색(ripgrep 기반) / 파일 이름 패턴 검색 |
| `Agent` | 서브에이전트를 띄워 조사·리서치·독립적 작업을 위임(Explore, general-purpose, claude-code-guide 등 특화 에이전트 포함) |
| `Workflow` | 여러 서브에이전트를 결정론적으로 오케스트레이션(병렬 리뷰, 파이프라인 등) — 사용자가 명시적으로 요청할 때만 사용 |
| `Artifact` | HTML/Markdown 결과물을 공유 가능한 웹 페이지로 발행 |
| `AskUserQuestion` | 코드/맥락으로 판단할 수 없는, 사용자만 답할 수 있는 선택지를 물을 때 사용 |
| `ScheduleWakeup` / `CronCreate` 등 | 반복·예약 작업 설정 |
| `Skill` | 프로젝트나 사용자가 등록한 스킬(슬래시 명령) 호출 |
| `ToolSearch` | 처음엔 이름만 보이는 "지연 로드" 도구(예: WebFetch, TaskCreate, SendMessage 등)의 전체 스키마를 불러와 사용 가능하게 함 |
| 메모리 시스템 | `~/.claude/projects/<프로젝트>/memory/`에 사용자·피드백·프로젝트·참고 정보를 파일로 저장해 다음 대화에도 이어감(이 문서 자체도 이 세션의 산출물) |

### 10.4 안전 관련 원칙(이 환경에 적용 중인 규칙)
- **되돌리기 어렵거나 파급력이 큰 작업**(강제 푸시, `git reset --hard`, 파일/브랜치 삭제, 외부 서비스에 메시지 발송·게시 등)은 먼저 설명하고 **사용자 확인을 받은 뒤** 실행합니다. 한 번의 승인이 같은 종류의 모든 미래 행동에 대한 승인은 아닙니다.
- 커밋은 **명시적으로 요청받았을 때만** 생성하고, `--no-verify` 같은 훅 우회나 강제 옵션은 사용자가 분명히 요청하지 않는 한 쓰지 않습니다.
- 코드 변경 시 불필요한 리팩터링·과잉 설계를 피하고, 요청한 범위만큼만 수정합니다.
- 보안 취약점(인젝션, XSS 등)을 만들지 않도록 주의하고, 발견하면 스스로 수정합니다.
- 여러 개의 서브에이전트/워크플로를 동시에 띄우는 것은 **사용자가 명시적으로 요청했을 때만** 하며(비용이 크기 때문), 그렇지 않으면 직접 도구로 처리하거나 필요성을 먼저 설명하고 물어봅니다.

### 10.5 지금 이 세션에서 할 수 있는 일 (예시)
- 코드베이스 탐색, 버그 수정, 기능 구현, 리팩터링, 테스트 작성·실행
- Git 작업(브랜치, 커밋, PR 생성) — 사용자 승인 하에
- 웹 조사(공식 문서, GitHub 저장소 등)를 통해 최신 정보 확인 후 문서화 (이번 대화에서 한 작업이 정확히 이 예시입니다)
- 여러 파일에 걸친 대규모 작업을 서브에이전트/워크플로로 나누어 병렬 처리(요청 시)
- 결과를 Markdown 문서나 공유 가능한 웹 아티팩트로 정리
- 예약/반복 작업 설정(`/loop`, `ScheduleWakeup`, 크론 기반 스케줄)
- 이 대화의 맥락(선호, 진행 중인 프로젝트 사실)을 메모리에 남겨 다음 대화에서 이어가기

### 10.6 한계
- 웹 검색·조사 없이는 지식 컷오프 이후 정보(최신 버전, 최신 가격 등)를 확신할 수 없습니다 — 그래서 이 가이드의 최신성이 중요한 항목(가격, 명령어 목록)은 공식 링크로 대신 안내했습니다.
- 파일시스템·터미널 접근 권한은 사용자의 권한 설정(permission mode)에 따라 제한될 수 있습니다.
- 이 절 자체도 "현재 세션 기준" 스냅샷이므로, 도구 구성이나 정책이 바뀌면 달라질 수 있습니다.

---

## 11. 참고 링크 모음

| 주제 | 링크 |
|---|---|
| Claude Code 개요 | https://code.claude.com/docs/en/overview |
| 빠른 시작 | https://code.claude.com/docs/en/quickstart |
| 메모리(CLAUDE.md) | https://code.claude.com/docs/en/memory |
| 명령어 목록 | https://code.claude.com/docs/en/commands |
| 서브에이전트 | https://code.claude.com/docs/en/sub-agents |
| 훅(Hooks) | https://code.claude.com/docs/en/hooks-guide |
| MCP 빠른 시작 | https://code.claude.com/docs/en/mcp-quickstart |
| 권한 모드 | https://code.claude.com/docs/en/permission-modes |
| 베스트 프랙티스 | https://code.claude.com/docs/en/best-practices |
| 자주 쓰는 워크플로우 | https://code.claude.com/docs/en/common-workflows |
| VS Code 연동 | https://code.claude.com/docs/en/vs-code |
| JetBrains 연동 | https://code.claude.com/docs/en/jetbrains |
| 스킬(Skills) | https://code.claude.com/docs/en/skills |
| 설정(Settings) | https://code.claude.com/docs/en/settings |
| 모델 개요 | https://platform.claude.com/docs/en/about-claude/models/overview |
| 프롬프트 엔지니어링 | https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/overview |
| 가격 정책 | https://claude.com/pricing |

---

*이 문서는 2026년 7월 기준 공식 문서를 참고해 작성되었습니다. Claude Code는 빠르게 발전하는 도구이므로, 명령어·단축키·가격 등 세부 사항은 주기적으로 공식 문서와 비교해 갱신하는 것을 권장합니다.*
