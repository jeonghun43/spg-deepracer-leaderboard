# 2단계. 데이터 모델 — `models.py`, `migrations/`

> 이 단계의 목표: **DB 스키마가 곧 이 서비스의 규칙집**이라는 것을 이해하는 것.
> "팀당 동시 제출 1건"은 파이썬 코드에도 있고 DB 인덱스에도 있다. **왜 두 곳에 다 있어야 하는가?**
> 이 질문에 답할 수 있으면 이 단계는 끝이다.

---

## 0. 왜 애초에 데이터베이스인가

### 무엇을(What)

**[쉬움]**
제출 기록을 그냥 파일(엑셀이나 JSON)에 적어도 되지 않을까?
안 된다. 이유는 **두 사람이 동시에 적으면 한쪽이 지워지기 때문**이다.

두 팀이 정확히 같은 순간에 제출하면:
```
팀A 프로그램: 파일 읽음 → [기록1, 기록2]  →  [기록1, 기록2, A기록] 저장
팀B 프로그램: 파일 읽음 → [기록1, 기록2]  →  [기록1, 기록2, B기록] 저장  ← A기록 사라짐!
```

**[전공]**
DB가 제공하는, 파일로는 직접 구현하기 매우 어려운 4가지 (ACID):

| | 의미 | 우리 코드에서 |
|---|---|---|
| **A**tomicity | 여러 변경이 전부 되거나 전부 안 됨 | `admin.py`의 팀+계정 일괄 등록 — 중간에 실패하면 전부 롤백 |
| **C**onsistency | 제약조건이 항상 지켜짐 | `uq_team_active_submission` 인덱스 |
| **I**solation | 동시 트랜잭션이 서로 간섭 안 함 | 워커의 `FOR UPDATE SKIP LOCKED` |
| **D**urability | 커밋되면 전원이 나가도 남음 | WAL(Write-Ahead Log) |

**여기에 더해**: 인덱스로 빠른 조회, 선언적 쿼리(SQL), 여러 프로세스(웹+워커)가 안전하게 공유.
**우리 시스템은 웹 컨테이너와 호스트 워커가 같은 데이터를 만진다.** 파일로는 답이 없다.

---

## 1. ORM — 객체와 테이블 사이의 번역기

### 무엇을(What)

**[쉬움]**
DB는 **표(엑셀)** 로 생각하고, 파이썬은 **객체**로 생각한다.
ORM은 그 사이의 통역사다.

```
파이썬 세계                     DB 세계
─────────────                  ────────────────────────────
class Team              ↔      CREATE TABLE teams (...)
team = Team(name="A")   ↔      INSERT INTO teams (name) VALUES ('A')
team.name               ↔      teams.name 컬럼
team.submissions        ↔      SELECT * FROM submissions WHERE team_id = ?
```

**[전공]**
ORM은 **impedance mismatch**(객체 모델과 관계 모델의 근본적 불일치)를 메우는 계층이다.
불일치의 예: 객체는 참조로 연결되지만 테이블은 외래키로 연결된다.
객체는 상속이 있지만 테이블은 없다.

**ORM의 대가(cost)**:
- 생성되는 SQL이 안 보인다 → N+1 문제 (아래에서 다룸)
- 복잡한 쿼리는 오히려 SQL보다 쓰기 어렵다
- 학습 곡선이 있다

**이 프로젝트가 ORM과 raw SQL을 섞어 쓰는 것을 주목하라.**
```python
# worker/run.py:38 — 이건 raw SQL이다
CLAIM_NEXT_SQL = text("""
    UPDATE submissions SET status = 'running' ...
    FOR UPDATE SKIP LOCKED
    RETURNING id
""")
```
**왜?** `FOR UPDATE SKIP LOCKED` + `UPDATE ... RETURNING`을 ORM으로 표현하면
읽기가 더 어려워진다. **ORM은 도구지 종교가 아니다.** 적절한 판단이다.

### 어떻게(How) — SQLAlchemy 2.0 스타일

```python
class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    disqualified: Mapped[bool] = mapped_column(Boolean, default=False)
```

**`Mapped[int]` 라는 타입 힌트가 실제로 동작에 영향을 준다.** 이게 2.0의 핵심 변화다.

| 타입 힌트 | 추론되는 것 |
|---|---|
| `Mapped[int]` | `NOT NULL`, 정수 컬럼 |
| `Mapped[int \| None]` | **`NULL` 허용** |
| `Mapped[str]` | `NOT NULL`, 문자열 (길이는 `String(100)`으로 명시) |
| `Mapped[list["Team"]]` | 1:N 관계 |
| `Mapped["Season"]` | N:1 관계 |

```python
daily_count_adjustment: Mapped[int | None] = mapped_column(Integer, nullable=True)
```
`| None` 과 `nullable=True`가 **중복**이다. `| None`만 써도 되지만,
명시적으로 적어 두면 읽는 사람이 헷갈리지 않는다. 취향의 문제.

> **1.x vs 2.0 구분법** (다른 AI가 옛날 문법으로 설명할 때 잡아내기 위해)
> | | 1.x | 2.0 |
> |---|---|---|
> | Base | `declarative_base()` | `class Base(DeclarativeBase)` |
> | 컬럼 | `id = Column(Integer, primary_key=True)` | `id: Mapped[int] = mapped_column(primary_key=True)` |
> | 조회 | `db.query(Team).filter(...).all()` | `db.execute(select(Team).where(...)).scalars().all()` |
> | 단건 | `db.query(Team).get(6)` | `db.get(Team, 6)` |
>
> **이 프로젝트는 2.0이다.** 단 한 곳만 1.x 스타일이 남아 있다:
> ```python
> # worker/run.py:79
> stale = db.query(Submission).filter(...).all()
> ```
> 동작은 하지만(2.0에서도 legacy API 지원) **일관성 관점에서는 `select()`로 바꿀 만하다.**

---

## 2. 테이블 6개 — 무엇을, 왜 이렇게 쪼갰나

```
Season (시즌/대회)
  └─1:N─> Team (참가팀)
            ├─1:1─> Account (로그인 계정)
            └─1:N─> Submission (제출)
                      └─1:1─> EvaluationResult (평가 결과)

AdminAccount (관리자)   ← 아무와도 연결 안 됨. 독립.
```

### 왜 이렇게 쪼갰나 — 각 분리의 이유

**Q. Team과 Account를 왜 나눴나? 한 테이블에 `login_id`, `password_hash`를 넣으면 안 되나?**

`plan.md §5.4`에 답이 있다. **시즌이 끝나면 계정만 삭제하고 팀 기록은 영구 보존**한다.
```python
# app/season_archive.py:25-26
if team.account is not None:
    db.delete(team.account)
```
한 테이블이면 "계정 정보만 지우기"가 컬럼을 NULL로 만드는 지저분한 작업이 된다.
분리하면 **레코드를 통째로 지우면 끝**이다.

이건 일반 원칙이기도 하다: **생명주기가 다른 데이터는 테이블을 나눈다.**

**Q. Submission과 EvaluationResult를 왜 나눴나? 1:1인데?**

1. **생기는 시점이 다르다.** Submission은 업로드 순간, Result는 10분 뒤.
2. **Submission은 결과가 없을 수 있다** (queued/running/error 상태).
   한 테이블이면 `lap_time_seconds`, `off_track_count`, `video_path`가 전부 NULL인 행이 대부분이 된다.
3. **의미가 다르다.** Submission은 "참가자의 행위", Result는 "시스템의 측정".

> **[전공] 이게 정규화의 실질적 의미다.** "1:1이면 합쳐라"가 아니라
> **"항상 함께 존재하고 함께 변하는가?"** 를 묻는다. 아니면 나눈다.

**Q. AdminAccount를 왜 Account와 분리했나? `is_admin` 불린 하나면 되지 않나?**

```python
class AdminAccount(Base):
    """관리자 계정. 팀 계정과 완전히 분리된 권한 체계 (plan.md §6)."""
```
- Account는 `team_id`가 **NOT NULL**이다. 관리자는 팀이 없다 → nullable로 바꿔야 함 → 제약이 약해짐
- **권한 상승 공격면이 사라진다.** `is_admin` 플래그 방식은 어딘가에서 그 플래그를 잘못 세팅하면 끝이다.
  테이블이 다르면 팀 계정으로 로그인해서 관리자가 될 **경로 자체가 없다.**
- 세션 키도 다르다: `session["team_id"]` vs `session["admin_id"]`

**[쉬움]** 학생증과 교직원증을 아예 다른 카드로 만든 것. 학생증에 스티커를 붙여 교직원인 척할 수 없다.

---

## 3. `relationship` — ORM의 마법과 함정

```python
class Team(Base):
    season: Mapped["Season"] = relationship(back_populates="teams")
    account: Mapped["Account"] = relationship(
        back_populates="team", uselist=False, cascade="all, delete-orphan"
    )
    submissions: Mapped[list["Submission"]] = relationship(
        back_populates="team", cascade="all, delete-orphan", order_by="Submission.submitted_at"
    )
```

### 무엇을(What)

**[쉬움]**
`team.submissions` 라고 쓰면 그 팀의 제출 목록이 나온다.
SQL을 안 써도 되게 해주는 지름길.

**[전공]**
`relationship()`은 **컬럼이 아니다.** DB에 아무것도 안 만든다.
ForeignKey가 이미 만든 연결을 **파이썬에서 객체로 탐색할 수 있게** 해주는 매핑일 뿐이다.

```python
season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"))  # ← 실제 컬럼
season: Mapped["Season"] = relationship(back_populates="teams")                        # ← 탐색 도구
```

### `back_populates` — 양방향 동기화

```python
# Team 쪽
season: Mapped["Season"] = relationship(back_populates="teams")
# Season 쪽
teams: Mapped[list["Team"]] = relationship(back_populates="season", ...)
```

**무엇을 해주나**: 한쪽을 바꾸면 **메모리 상에서** 반대쪽도 즉시 반영된다.
```python
team.season = some_season      # 이 순간
assert team in some_season.teams   # 이미 True (커밋 전에도)
```

**없으면?** 두 개의 독립된 관계가 되어 한쪽만 갱신되고 다른 쪽은 낡은 상태로 남는다.
같은 세션 안에서 반대쪽을 읽으면 **틀린 값**을 본다.

> `backref`라는 옛 방식도 있다(한쪽에만 쓰면 반대쪽이 자동 생성). **2.0에서는 `back_populates` 권장.**
> 이유: 양쪽이 코드에 명시적으로 보여야 읽기 쉽다.

### `uselist=False` — 1:1 관계 만들기

```python
account: Mapped["Account"] = relationship(back_populates="team", uselist=False, ...)
```
없으면 `team.account`가 **리스트**가 된다. `uselist=False`면 객체 하나(또는 `None`).

사실 `Mapped["Account"]`(리스트가 아닌 타입)만으로도 2.0은 추론한다.
**명시가 중복이지만 의도가 드러나서 나쁘지 않다.**

진짜로 1:1을 **보장**하는 것은 relationship이 아니라 이것이다:
```python
team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ...), unique=True)
                                                                  ^^^^^^^^^^^
```
**`unique=True`가 없으면 한 팀에 계정 2개가 들어갈 수 있다.** relationship은 못 막는다.

### `order_by="Submission.submitted_at"` — 정렬을 관계에 박아두기

```python
submissions: Mapped[list["Submission"]] = relationship(..., order_by="Submission.submitted_at")
```

**왜 중요한가?** `records.py`의 최고기록 계산이 **동점 처리에서 순서에 의존**한다:
```python
# app/records.py:20-26
is_better = best_result is None or (
    result.lap_time_seconds < best_result.lap_time_seconds
    or (result.lap_time_seconds == best_result.lap_time_seconds
        and submission.submitted_at < best_submission.submitted_at)
)
```
여기서는 명시적으로 `submitted_at`을 비교하므로 순서에 의존하지 않는다. **잘 짠 코드다.**
하지만 `order_by`가 있어서 `retention.py`의 순회도 시간순이 되고, 디버깅할 때 예측 가능하다.

**문자열로 쓴 이유**: `Submission` 클래스가 `Team`보다 **아래에 정의**되어 있어서,
`Team` 정의 시점에는 아직 존재하지 않는다. 문자열로 주면 SQLAlchemy가 나중에 해석한다.
같은 이유로 `Mapped["Submission"]`도 따옴표 안에 있고, 파일 맨 위에 `from __future__ import annotations`가 있다.

---

## 4. **cascade — 가장 헷갈리는 부분 (반드시 이해할 것)**

이 프로젝트에는 **두 종류의 cascade가 동시에** 쓰인다. 이름이 같아서 혼동하기 쉽다.

```python
season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"))
                                                                 ^^^^^^^^^^^^^^^^^^ (1) DB 레벨
teams: Mapped[list["Team"]] = relationship(back_populates="season", cascade="all, delete-orphan")
                                                                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^ (2) ORM 레벨
```

### (1) `ondelete="CASCADE"` — DB가 하는 일

**[쉬움]** DB에게 "부모가 지워지면 자식도 알아서 지워"라고 미리 말해두는 것.

**[전공]**
DDL에 들어간다:
```sql
FOREIGN KEY(season_id) REFERENCES seasons(id) ON DELETE CASCADE
```
`DELETE FROM seasons WHERE id=1` 을 **psql에서 직접** 실행해도 teams가 함께 지워진다.
**애플리케이션과 무관하게 DB 엔진이 보장한다.**

### (2) `cascade="all, delete-orphan"` — ORM이 하는 일

**[쉬움]** 파이썬이 "부모 객체를 지우라고 했으니 자식 객체 지우는 SQL도 같이 보내자"라고 하는 것.

**[전공]**
- `all` = `save-update, merge, refresh-expire, expunge, delete` 전부
- `delete-orphan` = **부모와의 연결이 끊긴 자식은 고아이므로 삭제**

```python
team.submissions.remove(sub)   # 리스트에서 뺐을 뿐인데
db.commit()                     # → DELETE FROM submissions WHERE id=... 가 나감
```

### 왜 둘 다 필요한가?

| | ORM cascade만 | DB ondelete만 | 둘 다 (현재) |
|---|---|---|---|
| `db.delete(season)` | ✅ 동작 | ✅ 동작 | ✅ |
| psql에서 직접 DELETE | ❌ FK 위반 에러 | ✅ 동작 | ✅ |
| `team.submissions.remove(x)` | ✅ 삭제됨 | ❌ 아무 일 없음 | ✅ |
| 안전성 | 앱을 우회하면 깨짐 | ORM 리스트 조작이 안 먹음 | 어느 쪽이든 일관 |

**핵심 교훈**: **DB 제약은 최후의 방어선, ORM은 편의.** 둘은 대체재가 아니라 보완재다.
이 원칙이 다음 절(부분 유니크 인덱스)에서 다시 나온다.

> **[전공] 성능 함정**: ORM cascade로 시즌을 지우면 SQLAlchemy는
> **자식을 전부 메모리에 로드한 뒤 하나씩 DELETE**를 보낸다.
> 팀 10개 × 제출 50개면 500개의 DELETE 문. 10팀 규모라 괜찮지만,
> 대량이면 `passive_deletes=True`를 줘서 "DB의 ON DELETE CASCADE에 맡겨라"라고 해야 한다.

---

## 5. **부분 유니크 인덱스 — 이 프로젝트에서 가장 영리한 한 줄**

```python
class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        Index(
            "uq_team_active_submission",
            "team_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )
```

생성되는 DDL:
```sql
CREATE UNIQUE INDEX uq_team_active_submission ON submissions (team_id)
WHERE status IN ('queued', 'running');
```

### 무엇을(What)

**[쉬움]**
"한 팀은 **아직 결과가 안 나온 제출**을 동시에 하나만 가질 수 있다."
DB가 이걸 강제한다. 두 번째를 넣으려 하면 DB가 거부한다.

그런데 **완료된 제출은 몇 개든 상관없다.** 그래서 "부분(partial)"이다 — 조건에 맞는 행만 검사.

**[전공]**
일반 유니크 인덱스라면 `UNIQUE(team_id)` — 팀당 제출이 평생 1건뿐이 된다. 말이 안 된다.
`WHERE` 절을 붙여 **인덱스에 포함되는 행 자체를 제한**한다.
포함 안 된 행(`done`, `error`)은 유니크 검사 대상이 아니다.

PostgreSQL의 partial index는 인덱스 크기도 줄여준다 —
활성 제출은 항상 0~10건이므로 인덱스가 극단적으로 작다.

### 왜(Why) — 파이썬 `if`문으로는 왜 부족한가

`submissions.py`에는 이미 검사가 있다:
```python
if has_active_submission(db, team) is not None:
    return redirect_with_error("이전 제출의 결과가 아직 나오지 않았습니다...")
```

**이걸로 충분해 보이지만 아니다. 시나리오를 보자:**

참가자가 제출 버튼을 **더블클릭**했다. 요청 두 개가 거의 동시에 도착한다.

```
시각   요청 A                              요청 B
────────────────────────────────────────────────────────────────
t=0    SELECT ... status IN (queued,running)
t=1                                        SELECT ... status IN (queued,running)
t=2    결과: 0건 → 통과!
t=3                                        결과: 0건 → 통과!     ← A가 아직 INSERT 안 함
t=4    INSERT (queued)
t=5                                        INSERT (queued)       ← 중복 발생!!
t=6    COMMIT                              COMMIT
```

**결과**: 한 팀이 큐에 2건. 하루 한도 2회가 한 번에 소모되고, 워커가 같은 팀을 연속 처리한다.

이것이 고전적인 **TOCTOU(Time-Of-Check to Time-Of-Use)** 경쟁 조건이다.
"확인한 시점"과 "사용한 시점" 사이에 세상이 바뀐다.

**DB 유니크 인덱스는 왜 이걸 막나?**
인덱스 삽입은 **원자적**이다. B-tree에 키를 넣는 순간 락이 걸리고,
두 번째 INSERT는 **커밋 여부와 무관하게** 첫 번째가 끝날 때까지 대기했다가 위반이면 실패한다.
경쟁 조건이 원천적으로 존재할 수 없다.

### 어떻게(How) — 그런데 지금 코드에 남은 문제

**중요**: 현재 `submissions.py`는 `IntegrityError`를 **잡지 않는다**.

```python
db.add(submission)
db.commit()          # ← 여기서 유니크 위반이면 IntegrityError 예외
return RedirectResponse("/submit", status_code=303)
```

경쟁이 실제로 일어나면 두 번째 요청은 **500 Internal Server Error**가 뜬다.
데이터는 안전하다(중복이 안 들어감). 하지만 사용자 경험은 나쁘다.

**제대로 하려면:**
```python
from sqlalchemy.exc import IntegrityError

try:
    db.add(submission)
    db.commit()
except IntegrityError:
    db.rollback()
    dest_path.unlink(missing_ok=True)   # 업로드한 파일도 정리
    return redirect_with_error("이전 제출의 결과가 아직 나오지 않았습니다.")
```

> **이건 개선 여지로 기록해 둘 만하다.** 데이터 무결성은 지켜지므로 심각도는 낮지만,
> **"방어선은 있는데 그 방어선에 걸렸을 때의 UX가 없다"** 는 상태다.

### 이 패턴의 일반화

**애플리케이션 검사 = 친절한 안내, DB 제약 = 진짜 보장.**

| 규칙 | 앱 검사 | DB 제약 |
|---|---|---|
| 동시 제출 1건 | `has_active_submission()` | `uq_team_active_submission` |
| 시즌 내 팀명 중복 | `admin.py`의 `existing_names` 체크 | `uq_team_season_name` |
| 로그인 ID 중복 | (없음) | `Account.login_id unique=True` |
| 제출당 결과 1개 | (없음) | `EvaluationResult.submission_id unique=True` |

`admin.py`의 주석이 이 철학을 정확히 말한다:
```python
# 팀명은 시즌 안에서 유일해야 한다(uq_team_season_name). 중복 한 건 때문에
# 트랜잭션 전체가 깨지지 않도록 DB 예외에 기대지 않고 미리 걸러낸다.
```
→ **DB 제약은 있다. 하지만 50팀 중 1팀이 중복이라고 49팀 등록이 통째로 실패하면 안 되니
앱에서 먼저 걸러 "이 팀은 건너뜀"으로 알려준다.** 정확한 판단이다.

---

## 6. Enum — 실제로 버그를 낸 곳

```python
Enum = partial(SAEnum, values_callable=lambda enum_cls: [e.value for e in enum_cls])
```

파일 상단에 이런 게 있다. 주석도 있다:
```python
# SQLAlchemy의 Enum은 기본적으로 파이썬 Enum 멤버의 .name(예: "QUEUED")을 DB에 저장한다.
# 우리는 소문자 .value(예: "queued")를 SQL에서 그대로 비교하므로(quota.py, worker/run.py의
# raw SQL), 반드시 .value를 저장하도록 values_callable을 지정해야 한다.
```

### 무엇이 문제였나 — 실제 장애 재현

```python
class SubmissionStatus(str, enum.Enum):
    QUEUED = "queued"    # name="QUEUED", value="queued"
```

**기본 동작**: SQLAlchemy는 DB에 `'QUEUED'`(대문자 name)를 저장한다.

그런데 워커의 raw SQL은:
```sql
SELECT id FROM submissions WHERE status = 'queued'   -- 소문자!
```

→ **매칭이 하나도 안 된다. 워커가 큐를 영원히 못 집는다.**
증상: 제출은 되는데 아무리 기다려도 "대기 중"에서 안 넘어감. 에러도 안 남.
**로그도 정상이라 원인을 찾기 극도로 어렵다.** (memory에 기록된 실제 사건)

`values_callable`은 "DB에 저장할 값을 이 함수로 결정해라"라는 훅이다.
`[e.value for e in enum_cls]` → `["queued","running","done","error"]` 소문자로 저장.

### 교훈 — 왜 이런 일이 생기는가

**표현(representation)이 두 곳에 존재하면 반드시 어긋난다.**
- 파이썬: `SubmissionStatus.QUEUED`
- SQL 문자열: `'queued'`

**근본 해결책 3가지**:
1. `values_callable`로 맞춘다 (현재 방식) — 간단
2. raw SQL을 안 쓰고 전부 ORM으로 (`FOR UPDATE SKIP LOCKED` 때문에 어려움)
3. raw SQL에서도 `:status` 바인드 파라미터로 `SubmissionStatus.QUEUED.value`를 넘긴다

**3번이 사실 가장 안전하다.** 현재 코드의 `CLAIM_NEXT_SQL`은 `'queued'`, `'running'`이
문자열로 하드코딩되어 있어, 나중에 enum 값을 바꾸면 또 어긋난다.

같은 위험이 `models.py`의 인덱스 정의에도 있다:
```python
postgresql_where=text("status IN ('queued', 'running')")
```
그리고 `retention.py`:
```python
if submission.status.value in ACTIVE_SUBMISSION_STATUSES:
```
여기는 `.value`를 써서 상수를 참조한다 — **이쪽이 올바른 패턴**이다.

> **자가 점검**: `SubmissionStatus.QUEUED`의 값을 `"pending"`으로 바꾸면
> 코드 몇 군데를 같이 고쳐야 하는가? 세어보라. (답: 최소 3곳 + 마이그레이션)

### `native_enum=False` — 왜 PostgreSQL ENUM 타입을 안 쓰나

```python
status: Mapped[SeasonStatus] = mapped_column(Enum(SeasonStatus, native_enum=False, length=20), ...)
```

PostgreSQL에는 진짜 `CREATE TYPE ... AS ENUM` 이 있다. 왜 안 쓰나?

| | native enum (`CREATE TYPE`) | `native_enum=False` (VARCHAR + CHECK) |
|---|---|---|
| 저장 | 4바이트 | 문자열 |
| 값 추가 | `ALTER TYPE ... ADD VALUE` — **트랜잭션 안에서 제약이 있고 마이그레이션이 까다롭다** | 그냥 CHECK 제약 수정 |
| 값 제거 | **사실상 불가능** | 가능 |
| DB 이식성 | PostgreSQL 전용 | 어디서나 |

**결론**: 상태 값이 나중에 늘어날 가능성이 있는 초기 프로젝트에서는
`native_enum=False`가 **운영 난이도를 크게 낮춘다.** 옳은 선택이다.

실제 마이그레이션 파일을 보면:
```python
sa.Column('status', sa.Enum('queued','running','done','error',
          name='submissionstatus', native_enum=False, length=20), nullable=False)
```
→ `VARCHAR(20)` + `CHECK (status IN (...))` 로 생성된다.

---

## 7. 시간 컬럼 — `server_default` vs `default`

```python
submitted_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
started_at:   Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

### `default` vs `server_default`

| | 누가 값을 만드나 | 생성되는 SQL |
|---|---|---|
| `default=datetime.utcnow` | **파이썬**이 계산해서 INSERT에 넣음 | `INSERT ... VALUES ('2026-07-26 10:00:00')` |
| `server_default=func.now()` | **DB**가 계산 | `DEFAULT now()` (DDL에 박힘) |

**`server_default`가 나은 이유:**
1. **시계가 하나다.** 웹 컨테이너, 워커, DB가 각자 다른 시계를 갖는데, DB 시계 하나로 통일된다.
   → 컨테이너 시간대가 UTC고 호스트가 KST면, `default`는 뒤죽박죽이 된다.
2. psql에서 직접 INSERT해도 값이 채워진다.
3. **동시성**: 여러 프로세스가 동시에 INSERT해도 순서가 DB 기준으로 일관된다.

**단점**: INSERT 직후 파이썬 객체의 `submitted_at`은 `None`이다.
읽으려면 `db.refresh(obj)`가 필요하다. 우리 코드는 INSERT 직후 그 값을 안 읽으므로 문제없다.

### `DateTime(timezone=True)` — 왜 반드시 필요한가

**[쉬움]** "밤 12시"라고만 적으면 어디의 12시인지 모른다. "한국 시간 12시"라고 적어야 한다.

**[전공]**
PostgreSQL에서:
- `TIMESTAMP` (naive) — 시간대 정보 없음. **의미가 모호하다**
- `TIMESTAMPTZ` (aware) — 내부적으로 UTC로 저장하고, 조회 시 세션 타임존으로 변환

`timezone=True`면 `TIMESTAMPTZ`가 된다.

**이게 없으면 4단계에서 하루 한도 계산이 깨진다:**
```python
# app/quota.py:24-31
day_start = dt.datetime.combine(on_date, dt.time.min, tzinfo=KST)   # aware
stmt = select(...).where(Submission.finished_at >= day_start)        # 비교
```
`finished_at`이 naive인데 `day_start`가 aware면 **파이썬은 TypeError를 내고,
DB는 조용히 틀린 결과를 낸다.** 후자가 훨씬 무섭다.

**철칙: 저장은 UTC(aware), 표시할 때만 로컬 타임존으로 변환.**
워커도 이걸 지킨다:
```python
# worker/run.py:54-55
def now_utc() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)
```

---

## 8. `@property counts_toward_daily_limit` — 규칙을 코드에 심기

```python
@property
def counts_toward_daily_limit(self) -> bool:
    """하루 제출 한도는 '완료'(완주든 미완주든)만 카운트하고 '오류'는 제외한다."""
    return self.status == SubmissionStatus.DONE
```

### 무엇을(What)

DB 컬럼이 아니다. **파생 속성**이다. `status`로부터 계산된다.

### 왜(Why) — 이 규칙이 왜 이렇게 미묘한가

memory에 기록된 정정 사항:

> 하루 제출 한도(5회) 카운트 기준: "완주 성공 여부"가 아니라 **"평가가 끝까지 정상 실행됐는지"**.
> 모델이 업로드·실행은 정상적으로 됐지만 3바퀴를 다 못 돌아 시간 초과(미완주)로 끝나도 **카운트됨**.
> 업로드 파일 자체가 손상/형식 오류이거나 DRFC 실행이 인프라 문제로 비정상 종료된 경우만 카운트 제외.

**공정성 논리**:
- 미완주(`DONE` + `TIMEOUT`) = **참가자 모델이 못 한 것** → 기회 소진이 공정
- 오류(`ERROR`) = **시스템이 못 한 것** → 참가자에게 책임을 물으면 안 됨

**만약 이 구분이 없다면?**
- 전부 카운트: 서버가 죽어서 실패해도 참가자가 기회를 잃음 → 항의
- 완주만 카운트: 일부러 못 도는 모델을 무한 제출 → 서버 점유

### 어떻게(How) — 그런데 이 property는 실제로 안 쓰인다

`quota.py`를 보면:
```python
stmt = select(func.count()).where(
    Submission.status == SubmissionStatus.DONE,   # ← property를 안 쓰고 직접 비교
    ...
)
```

**왜?** `@property`는 **파이썬 객체 위에서만** 동작한다. SQL로 번역할 수 없다.
카운트를 SQL로 세려면 조건을 SQL 표현식으로 써야 한다.

> **[전공] ORM의 근본적 한계 중 하나다.** 해결책은 `hybrid_property`:
> ```python
> from sqlalchemy.ext.hybrid import hybrid_property
>
> @hybrid_property
> def counts_toward_daily_limit(self):
>     return self.status == SubmissionStatus.DONE
> ```
> 이러면 **파이썬에서는 bool, SQL에서는 WHERE 절**로 둘 다 동작한다.
> 지금은 규칙이 한 곳(quota.py)에서만 쓰여 과잉 설계라 판단할 만하다.
> 다만 현재 property는 **문서 역할만** 하고 있고, 규칙이 실제로는 quota.py에 중복되어 있다.

---

## 9. 마이그레이션 — 스키마의 버전 관리

### 무엇을(What)

**[쉬움]**
코드는 git으로 버전 관리한다. 그럼 **DB 표 구조**는? 그게 마이그레이션이다.
"1번 버전에서 2번 버전으로 갈 때 이 컬럼을 추가해라"를 파일로 남긴다.

**[전공]**
Alembic은 `alembic_version` 테이블 하나를 DB에 만들어 현재 리비전 ID를 저장한다.
`alembic upgrade head`는 그 값부터 최신까지의 `upgrade()`를 순서대로 실행한다.

리비전은 **연결 리스트**다:
```
685df3cb9303 (initial schema)
     ↑ down_revision
a1c4f2b8d907 (daily_count_adjustment)  ← head
```

### 왜(Why) — `Base.metadata.create_all()` 로 하면 안 되나?

`create_all()`은 **없는 테이블을 만들기만 한다.** 이미 있는 테이블은 손대지 않는다.
- 컬럼 추가? 안 됨
- 컬럼 이름 변경? 안 됨
- 데이터 변환? 안 됨

**운영 중인 DB에는 데이터가 들어있다.** 지우고 다시 만들 수 없다.

### 어떻게(How) — 실제 마이그레이션 두 개 읽기

**#1 `685df3cb9303_initial_schema.py`** — autogenerate로 만든 초기 스키마.
`# ### commands auto generated by Alembic - please adjust! ###` 주석이 그대로 남아 있다.
6개 테이블 + 부분 유니크 인덱스를 만든다.

주목할 부분:
```python
op.create_index('uq_team_active_submission', 'submissions', ['team_id'], unique=True,
                postgresql_where=sa.text("status IN ('queued', 'running')"))
```
**autogenerate가 `postgresql_where`까지 제대로 잡아냈다.** 다행이지만
alembic autogenerate는 인덱스/제약을 놓치는 경우가 많으므로 **항상 사람이 검토해야 한다.**

**#2 `a1c4f2b8d907_daily_count_adjustment.py`** — **이 파일이 진짜 교재다.**

```python
def upgrade() -> None:
    op.alter_column("teams", "daily_count_override", new_column_name="daily_count_adjustment")
    op.alter_column("teams", "daily_count_override_date", new_column_name="daily_count_adjustment_date")
    # 기존 값은 절대값이라 델타로서는 의미가 없다. 그대로 두면 카운트가 잘못 부풀려지므로 비운다.
    op.execute("UPDATE teams SET daily_count_adjustment = NULL, daily_count_adjustment_date = NULL")
```

**여기서 배울 것 3가지:**

1. **마이그레이션은 스키마만이 아니라 데이터도 옮긴다.**
   컬럼 이름만 바꾸고 값을 그대로 두면 **의미가 달라진 값**이 남는다.
   `override=3`(절대값 3회 사용)이 `adjustment=3`(3회 더하기)으로 해석되어
   실제 카운트가 부풀려진다. → `NULL`로 비우는 것이 정답.

2. **왜 override → adjustment로 바꿨나 (설계 버그의 교훈)**

   **[쉬움]**
   - 옛날 방식: "이 팀은 오늘 3번 썼다고 쳐라" (절대값)
     → 그 뒤에 실제로 2번 더 하면? 여전히 3번으로 표시된다. **한도가 영영 안 걸린다.**
   - 새 방식: "이 팀 카운트에 +3 해라" (델타)
     → 실제 2번 + 보정 3 = 5. **정상 동작.**

   **[전공]** 이건 **파생값을 저장하려다 생긴 문제**다.
   "오늘 사용 횟수"는 submissions 테이블에서 **계산되는 값**인데,
   override는 그 계산 결과를 통째로 덮어써서 계산을 무력화했다.
   델타는 계산을 유지하면서 오프셋만 준다. → **계산 가능한 값은 저장하지 말고, 꼭 필요하면 보정만 하라.**

   실제 적용 코드:
   ```python
   # app/quota.py:36-37
   if team.daily_count_adjustment is not None and team.daily_count_adjustment_date == on_date:
       done_count += team.daily_count_adjustment      # 더한다 (덮어쓰지 않는다)
   ```

   관리자 화면에서는 "절대값 입력"을 받는데, 내부에서 델타로 변환한다:
   ```python
   # app/routers/admin.py:334-338
   team.daily_count_adjustment = None          # 보정 비우고
   actual_done = get_daily_done_count(db, team, today)   # 실제 건수 구해서
   team.daily_count_adjustment = max(count, 0) - actual_done   # 차이를 보정으로
   ```
   **UI는 직관적으로(절대값), 저장은 안전하게(델타).** 좋은 패턴이다.

3. **`downgrade()`도 작성했다.** 이름만 되돌린다.
   데이터 복구는 불가능(NULL로 지웠으므로)하지만, 스키마는 되돌아간다.
   **downgrade가 완벽하지 않아도 적어두는 것이 낫다.**

### `migrations/env.py` 의 중요한 한 줄

```python
from app import models  # noqa: F401  (모델을 등록하기 위해 import)
```

**왜 이게 필요한가?** `target_metadata = Base.metadata`인데,
`models.py`를 import하지 않으면 **어떤 클래스도 정의되지 않아 metadata가 비어 있다.**
→ autogenerate가 "모든 테이블을 삭제하라"는 마이그레이션을 만들어낸다. **재앙이다.**

`# noqa: F401`은 "이 import는 안 쓰는 것처럼 보이지만 의도적이다"라고 린터에게 알리는 것.

```python
config.set_main_option("sqlalchemy.url", settings.database_url)
```
`alembic.ini`에 URL을 하드코딩하지 않고 **앱 설정을 재사용**한다. 12-factor 일관성.

```python
poolclass=pool.NullPool
```
마이그레이션은 한 번 실행하고 끝이므로 커넥션 풀이 필요 없다.

### 실행 시점 — Dockerfile을 다시 보라

```dockerfile
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```

**컨테이너가 뜰 때마다 마이그레이션이 먼저 돌고, 성공해야 서버가 뜬다.**

**장점**: 배포 절차가 단순하다. `docker compose up -d --build` 한 줄.
**단점**: 웹 컨테이너를 여러 개(replica)로 늘리면 **동시에 마이그레이션을 실행**해 충돌한다.
지금은 1개뿐이라 안전하다. 늘릴 때는 별도 마이그레이션 잡으로 분리해야 한다.

---

## 10. 알아둬야 할 함정 — Lazy Loading과 N+1

### 무엇이 문제인가

```python
# app/routers/leaderboard.py:16-28
teams = db.execute(select(Team).where(Team.season_id == season.id, ...)).scalars().all()

for team in teams:
    best_submission, best_result = get_team_best(team)   # ← team.submissions 접근
    total_submissions = sum(1 for s in team.submissions if s.status == SubmissionStatus.DONE)
```

`get_team_best(team)` 안에서 `team.submissions`를 순회한다:
```python
# app/records.py:13-16
for submission in team.submissions:
    if submission.status != SubmissionStatus.DONE or submission.result is None:
```

**실제로 나가는 쿼리:**
```sql
SELECT * FROM teams WHERE season_id=1 AND disqualified=false;      -- 1번
-- 팀1 처리
SELECT * FROM submissions WHERE team_id=1 ORDER BY submitted_at;    -- +1
SELECT * FROM evaluation_results WHERE submission_id=1;             -- +1
SELECT * FROM evaluation_results WHERE submission_id=2;             -- +1
...
-- 팀2 처리
SELECT * FROM submissions WHERE team_id=2 ...;                      -- +1
...
```

**팀 10개 × (제출 쿼리 1 + 제출당 결과 쿼리 N)** → 리더보드 한 번에 **수십~수백 쿼리**.

이것이 **N+1 문제**다. ORM을 쓰면 반드시 만나는 고전 함정.

**[쉬움]**
반 학생 30명의 성적을 알아보려고, 먼저 명단을 받고(1번),
학생마다 교무실에 한 번씩 가서 성적을 물어본다(30번). 31번 왕복.
한 번에 "전원 성적표 주세요" 하면 1번이면 될 일.

### 왜 지금은 괜찮은가

- 팀 10개, 팀당 제출 최대 ~70건(5회/일 × 2주)
- 로컬 DB라 쿼리당 왕복 **1ms 미만**
- 리더보드 조회는 초당 몇 건 수준

→ 최악이라도 수백 ms. **체감상 문제없다.** `plan.md §4`에서 "규모가 작아 쿼리로 즉시 계산"이라
명시적으로 판단한 결과다. **의식하고 내린 결정이면 그건 설계지 버그가 아니다.**

### 어떻게 고치는가 (알아만 둘 것)

```python
from sqlalchemy.orm import selectinload

teams = db.execute(
    select(Team)
    .where(Team.season_id == season.id, Team.disqualified.is_(False))
    .options(selectinload(Team.submissions).selectinload(Submission.result))
).scalars().all()
```
→ 쿼리 3개로 끝난다 (teams 1 + submissions 1 + results 1).

| 전략 | 방식 | 언제 |
|---|---|---|
| `selectinload` | `WHERE id IN (...)` 추가 쿼리 | 1:N에 기본 추천 |
| `joinedload` | LEFT JOIN 한 방 | 1:1, N:1에 좋음 |
| `subqueryload` | 서브쿼리 | 구식, 거의 안 씀 |

> **실험 과제**: `create_engine(..., echo=True)`로 켜고 리더보드를 열어
> 실제 쿼리 개수를 세어보라. 그리고 `selectinload`를 넣고 다시 세어보라.
> **N+1을 직접 눈으로 보는 경험은 값지다.**

---

## 11. 자가 점검 질문

1. 파일(JSON) 대신 DB를 쓰는 결정적 이유 하나를 동시성 시나리오로 설명하라.
2. `ForeignKey(ondelete="CASCADE")`와 `relationship(cascade="all, delete-orphan")`의 차이는? 왜 둘 다 필요한가?
3. `uselist=False`만으로 1:1이 보장되는가? 아니면 무엇이 더 필요한가?
4. 부분 유니크 인덱스가 없고 파이썬 `if`만 있을 때, 더블클릭이 만드는 문제를 시각 순서대로 설명하라.
5. 현재 코드에서 그 유니크 인덱스에 실제로 걸리면 사용자는 무엇을 보게 되는가? 어떻게 고쳐야 하는가?
6. `values_callable`이 없으면 워커에 무슨 일이 생기는가? 그 증상은 왜 진단하기 어려운가?
7. `native_enum=False`를 선택한 이유는? native enum의 어떤 점이 운영을 어렵게 하는가?
8. `default`와 `server_default`의 차이는? 우리 시스템(컨테이너+호스트)에서 왜 후자가 나은가?
9. `DateTime(timezone=True)`가 없으면 `quota.py`에서 무슨 일이 생기는가?
10. `daily_count_override`(절대값)가 왜 버그였는가? 델타로 바꾸면 왜 해결되는가?
11. `migrations/env.py`에서 `from app import models`를 지우면 어떤 재앙이 일어나는가?
12. 리더보드 조회 시 N+1이 발생하는 정확한 지점은 어디인가? 왜 지금은 문제가 아닌가?

---

## 12. 실험 과제

**실험 A — 부분 유니크 인덱스를 직접 때려보기**
```sql
-- psql 접속: docker exec -it spg_deepracer_leaderboard-db-1 psql -U drleader
INSERT INTO submissions (team_id, model_path, status) VALUES (1, 'a', 'queued');
INSERT INTO submissions (team_id, model_path, status) VALUES (1, 'b', 'queued');  -- 에러!
INSERT INTO submissions (team_id, model_path, status) VALUES (1, 'c', 'done');    -- 성공!
```
**세 번째가 왜 성공하는지** 설명할 수 있어야 한다. (테스트용 DB에서 하고 정리할 것)

**실험 B — Enum 저장 형태 확인**
```sql
SELECT id, status FROM submissions LIMIT 5;
```
소문자인가 대문자인가? `models.py`의 `Enum = partial(...)` 줄을 주석 처리하고
새 DB에 마이그레이션을 돌리면 어떻게 되는가?

**실험 C — N+1 눈으로 보기**
`app/db.py`에서 `create_engine(..., echo=True)`로 바꾸고 리더보드를 연다.
콘솔에 쏟아지는 SELECT를 세어본다. 그다음 `selectinload`를 적용하고 다시 센다.

**실험 D — 마이그레이션 왕복**
```bash
alembic current          # 지금 리비전
alembic downgrade -1     # 한 칸 되돌리기
alembic current          # 확인
alembic upgrade head     # 복구
```
테스트 DB에서만. `teams` 테이블 컬럼명이 실제로 바뀌는지 psql로 확인하라.

**실험 E — autogenerate가 무엇을 잡아내는지**
`models.py`에 `Team.memo: Mapped[str | None] = mapped_column(String(200), nullable=True)`를 추가하고
```bash
alembic revision --autogenerate -m "add memo"
```
생성된 파일을 열어보라. 그리고 **적용하지 말고 삭제**한다.
autogenerate가 무엇을 감지하고 무엇을 놓치는지 감을 잡는 실험이다.

---

→ 다음: [03-auth.md](03-auth.md) — 서버는 요청을 보낸 사람이 누구인지 어떻게 아는가
