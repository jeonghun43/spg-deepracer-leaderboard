# 2단계. 데이터 모델 — `models.py`, `migrations/`

> 이 단계의 목표: **DB 스키마가 곧 이 서비스의 규칙집**이라는 것을 이해하는 것.
> "팀당 동시 제출 1건"은 파이썬 코드에도 있고 DB 인덱스에도 있다. **왜 두 곳에 다 있어야 하는가?**
> 그리고 **운영 중인 DB에 컬럼을 추가하는 안전한 방법**은 무엇인가?

---

## 0. 왜 애초에 데이터베이스인가

### 무엇을(What)

**[쉬움]**
제출 기록을 그냥 파일(엑셀이나 JSON)에 적어도 되지 않을까?
안 된다. 이유는 **두 사람이 동시에 적으면 한쪽이 지워지기 때문**이다.

```
팀A 프로그램: 파일 읽음 → [기록1, 기록2]  →  [기록1, 기록2, A기록] 저장
팀B 프로그램: 파일 읽음 → [기록1, 기록2]  →  [기록1, 기록2, B기록] 저장  ← A기록 사라짐!
```

**[전공]**
DB가 제공하는, 파일로는 직접 구현하기 매우 어려운 4가지 (ACID):

| | 의미 | 우리 코드에서 |
|---|---|---|
| **A**tomicity | 여러 변경이 전부 되거나 전부 안 됨 | `admin.py`의 팀+계정 일괄 등록 |
| **C**onsistency | 제약조건이 항상 지켜짐 | `uq_team_active_submission` 인덱스 |
| **I**solation | 동시 트랜잭션이 서로 간섭 안 함 | 워커의 `FOR UPDATE SKIP LOCKED` |
| **D**urability | 커밋되면 전원이 나가도 남음 | WAL(Write-Ahead Log) |

**우리 시스템은 웹 컨테이너와 (다른 기기의) 워커가 같은 데이터를 만진다.**
게다가 워커는 **Tailscale 사설망을 건너** 접속한다. 파일로는 답이 없다.

---

## 1. ORM — 객체와 테이블 사이의 번역기

### 무엇을(What)

**[쉬움]**
DB는 **표(엑셀)** 로 생각하고, 파이썬은 **객체**로 생각한다. ORM은 그 사이의 통역사다.

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

**ORM의 대가(cost)**:
- 생성되는 SQL이 안 보인다 → N+1 문제 (§11)
- 복잡한 쿼리는 오히려 SQL보다 쓰기 어렵다

**이 프로젝트가 ORM과 raw SQL을 섞어 쓰는 것을 주목하라.**
```python
# worker/run.py:43 — 이건 raw SQL이다
CLAIM_NEXT_SQL = text("""
    UPDATE submissions SET status = 'running' ...
    FOR UPDATE SKIP LOCKED
    RETURNING id
""")
```
**왜?** `FOR UPDATE SKIP LOCKED` + `UPDATE ... RETURNING`을 ORM으로 표현하면 읽기가 더 어려워진다.
**ORM은 도구지 종교가 아니다.**

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
| `Mapped[float \| None]` | `NULL` 허용 실수 — 컬럼 타입 인자를 생략해도 추론된다 |
| `Mapped[list["Team"]]` | 1:N 관계 |
| `Mapped["Season"]` | N:1 관계 |

실제 예:
```python
lap_time_seconds: Mapped[float | None] = mapped_column(nullable=True)
best_progress_percent: Mapped[float | None] = mapped_column(nullable=True)
```
**`mapped_column()`에 타입 인자가 없다.** `Mapped[float | None]` 만으로 `Float NULL`이 추론된다.

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
> # worker/run.py:96
> stale = db.query(Submission).filter(...).all()
> ```
> 동작은 하지만(2.0도 legacy API 지원) **일관성 관점에서는 `select()`로 바꿀 만하다.**

---

## 2. 테이블 7개 — 무엇을, 왜 이렇게 쪼갰나

```
Season (시즌/대회)
  └─1:N─> Team (참가팀)
            ├─1:1─> Account (로그인 계정)
            └─1:N─> Submission (제출)
                      └─1:1─> EvaluationResult (평가 결과)

AdminAccount (관리자)       ← 아무와도 연결 안 됨. 독립.
WorkerHeartbeat (워커 생존)  ← 아무와도 연결 안 됨. 독립.
```

### 왜 이렇게 쪼갰나 — 각 분리의 이유

**Q. Team과 Account를 왜 나눴나?**

`plan.md §5.4`에 답이 있다. **시즌이 끝나면 계정만 삭제하고 팀 기록은 영구 보존**한다.
```python
# app/season_archive.py:25-26
if team.account is not None:
    db.delete(team.account)
```
한 테이블이면 "계정 정보만 지우기"가 컬럼을 NULL로 만드는 지저분한 작업이 된다.

**일반 원칙: 생명주기가 다른 데이터는 테이블을 나눈다.**

**Q. Submission과 EvaluationResult를 왜 나눴나? 1:1인데?**

1. **생기는 시점이 다르다.** Submission은 업로드 순간, Result는 10분 뒤
2. **Submission은 결과가 없을 수 있다** (queued/running/error)
3. **의미가 다르다.** Submission은 "참가자의 행위", Result는 "시스템의 측정"

> **[전공] 이게 정규화의 실질적 의미다.** "1:1이면 합쳐라"가 아니라
> **"항상 함께 존재하고 함께 변하는가?"** 를 묻는다.

**Q. AdminAccount를 왜 Account와 분리했나? `is_admin` 불린 하나면 되지 않나?**

- Account는 `team_id`가 **NOT NULL**이다. 관리자는 팀이 없다 → nullable로 바꿔야 함 → 제약이 약해짐
- **권한 상승 공격면이 사라진다.** 테이블이 다르면 팀 계정으로 관리자가 될 **경로 자체가 없다**
- 세션 키도 다르다: `session["team_id"]` vs `session["admin_id"]`
- **잠금 카운터도 관리자 로그인에만 붙는다** (`admin_lockout` — 3단계)

**[쉬움]** 학생증과 교직원증을 아예 다른 카드로 만든 것. 학생증에 스티커를 붙여 교직원인 척할 수 없다.

---

## 3. **`WorkerHeartbeat` — 관계가 없는 테이블**

```python
class WorkerHeartbeat(Base):
    """평가 워커의 생존 신호 (cloud-migration.md §5).

    워커는 웹과 다른 기기(운영자 노트북)에서 돌기 때문에, 노트북이 꺼지면 제출은 접수되지만
    평가만 멈춘다. 참가자 화면에 "고장"이 아니라 "대기 중"임을 알려주려면 이 신호가 필요하다.
    """

    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

### 무엇을(What)

**[쉬움]**
평가 서버(노트북)가 30초마다 **"나 살아있어"** 라고 도장을 찍는다.
웹은 그 도장 시각을 보고 "3분 넘게 소식이 없으면 꺼진 것"이라고 판단한다.

**[전공]**
분산 시스템의 고전 패턴 — **하트비트(heartbeat) / 임차(lease)**.
"살아있음"을 **주기적 갱신**으로 표현하고, 읽는 쪽은 **경과 시간**으로 판단한다.

### 왜(Why) — 이 테이블이 없으면 무슨 일이

**워커가 웹과 같은 기기에 있을 때**는 사실 필요 없었다.
노트북이 꺼지면 웹도 같이 꺼져서 참가자가 "사이트가 안 열리네"라고 알 수 있다.

**클라우드로 웹을 옮기고 나서 문제가 생긴다:**
```
웹(클라우드)    : 24시간 정상. 제출도 받는다
워커(노트북)    : 밤에 꺼져 있다
참가자          : 제출은 됐는데 30분째 "대기 중"
                  → "고장 났나? 내가 뭘 잘못했나?"
```

**시스템은 정상인데 사용자에게는 고장으로 보인다.** 이게 가장 나쁜 상태다.

하트비트가 있으면 화면이 정직해진다:
```jinja
{# app/templates/_worker_status.html #}
{% if worker_status and not worker_status.online %}
<div class="card" style="border-color:#c98a00;">
  <strong>평가 서버가 잠시 중지되어 있습니다.</strong>
  <p class="muted">제출은 정상적으로 접수되며, 서버가 재개되면 접수된 순서대로 처리됩니다.
    {% if worker_status.minutes_ago is not none %}(마지막 응답: 약 {{ worker_status.minutes_ago }}분 전){% endif %}
  </p>
</div>
{% endif %}
```

### 어떻게(How) — 스키마 설계 결정 3가지

**결정 1: `worker_id`가 기본 키다 (별도 `id` 없음)**
```python
worker_id: Mapped[str] = mapped_column(String(100), primary_key=True)
```
- 워커 한 대당 행 하나. **자연 키(natural key)** 를 그대로 PK로 썼다
- 덕분에 `db.get(WorkerHeartbeat, worker_id)` 로 바로 찾을 수 있다
- 여러 워커로 늘려도 그대로 동작한다 (행이 늘 뿐)

`worker_id`는 `socket.gethostname()` 이다(`worker/run.py:32`).

> **[전공] 자연 키 vs 대리 키(surrogate key)**: 보통은 `id` 정수를 쓴다(이름이 바뀔 수 있으므로).
> 여기서는 (a) 호스트명이 안 바뀌고, (b) 조회가 항상 호스트명 기준이며,
> (c) 다른 테이블이 참조하지 않으므로 **자연 키가 더 단순하다.**

**결정 2: FK가 하나도 없다**

`Submission.worker_id`(String)와 `WorkerHeartbeat.worker_id`가 같은 값을 담지만 **FK가 아니다.**

**왜?** FK를 걸면:
- 워커가 하트비트를 남기기 전에 작업을 집으면 FK 위반
- 하트비트 행을 지우려면 모든 제출의 `worker_id`를 먼저 정리해야 함
- **두 테이블의 생명주기가 무관한데 결합이 생긴다**

**"값이 같다"와 "참조 관계다"는 다르다.** 여기선 전자다.

**결정 3: 이력을 안 남긴다 (UPDATE, not INSERT)**
```python
# app/worker_status.py:33-41
def touch_heartbeat(db: Session, worker_id: str) -> None:
    heartbeat = db.get(WorkerHeartbeat, worker_id)
    now = dt.datetime.now(tz=dt.timezone.utc)
    if heartbeat is None:
        db.add(WorkerHeartbeat(worker_id=worker_id, last_seen_at=now))
    else:
        heartbeat.last_seen_at = now
    db.commit()
```
30초마다 INSERT하면 하루 2,880행, 한 달 8만 행이 쌓인다. **필요한 건 "마지막"뿐이다.**
→ 있으면 UPDATE, 없으면 INSERT (**upsert** 패턴).

> **[전공] 경쟁 조건**: 두 워커가 동시에 같은 `worker_id`로 첫 INSERT를 하면 PK 충돌이 난다.
> 실제로는 `worker_id`가 호스트명이라 한 기기에 워커 두 개를 띄우지 않는 한 안 생긴다.
> PostgreSQL의 `INSERT ... ON CONFLICT DO UPDATE`를 쓰면 원자적으로 처리된다 — 개선 여지.
> 지금은 실패해도 하트비트 스레드가 예외를 삼키고 30초 뒤 다시 시도한다(`worker/run.py:154`).

**읽는 쪽:**
```python
# app/worker_status.py:22
last_seen = db.execute(select(func.max(WorkerHeartbeat.last_seen_at))).scalar_one_or_none()
```
**`MAX()`를 쓴다** — 워커가 여러 대여도 "하나라도 살아있으면 온라인"이 된다.
`worker_id`로 필터하지 않는 것이 의도적이다.

---

## 4. `EvaluationResult` — 나중에 자란 테이블

```python
class EvaluationResult(Base):
    __tablename__ = "evaluation_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), unique=True
    )
    finish_status: Mapped[FinishStatus] = mapped_column(Enum(FinishStatus, native_enum=False, length=20))
    lap_time_seconds: Mapped[float | None] = mapped_column(nullable=True)
    off_track_count: Mapped[int] = mapped_column(Integer, default=0)
    # 완주하지 못했을 때 "어디까지 갔고 왜 멈췄는지"를 알려주기 위한 값.
    # 이것이 없으면 화면이 모든 실패를 "시간 초과"로 뭉뚱그려 참가자가 원인을 오해한다.
    best_progress_percent: Mapped[float | None] = mapped_column(nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    video_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metrics_raw_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    completed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

### 왜 컬럼 두 개가 나중에 추가됐나 — 실제 사건

마이그레이션 `d4f1a2c86b73` 의 docstring:
> 완주하지 못한 제출을 화면이 모두 "미완주(시간 초과)"로 표시해, 실제로는 차가 트랙 중간에
> 멈춘 경우(immobilized)에도 참가자가 시간 초과로 오해했다. 어디까지 갔고 왜 멈췄는지를
> 저장해 정확히 알려준다.

**[쉬움]**
"실패했습니다"만 알려주면 참가자는 뭘 고쳐야 할지 모른다.
"67.8%까지 갔고 차가 멈췄습니다"라고 하면 **모델의 어느 부분이 문제인지 짐작할 수 있다.**

**[전공]**
초기 설계는 `finish_status`(finished/timeout) 두 값뿐이었다.
그런데 `timeout`이라는 이름 자체가 **거짓말**이었다 — 실제로는
- 차가 멈춤(`immobilized`)
- 트랙 이탈(`off_track`)
- 충돌(`crashed`)
- 역주행(`reversed`)
- 진짜 시간 초과(`time_up`)

전부 `timeout`으로 뭉뚱그려졌다.

**교훈: enum 값의 이름이 실제 의미보다 좁으면, 그 enum은 언젠가 거짓말을 한다.**
`FinishStatus.TIMEOUT`을 `NOT_FINISHED`로 두었다면 나았을 것이다.

**현재 해법**: `finish_status`는 그대로 두고(마이그레이션 비용이 크다),
**보조 컬럼 두 개를 추가**해 상세를 담았다.
- `best_progress_percent`: 얼마나 갔나 (0~100)
- `failure_reason`: 왜 멈췄나 (DRFC 원문 문자열)

그리고 표현은 `render.py`의 필터가 맡는다(1단계 §5).

### `failure_reason`이 `String(50)`이고 enum이 아닌 이유

```python
failure_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
```

**DRFC가 뱉는 값을 우리가 다 알지 못한다.** enum으로 못 박으면 모르는 값이 왔을 때 저장 실패한다.

`render.py`의 대응이 정확하다:
```python
# 이 표에 없는 값은 원문을 그대로 보여준다 — 모르는 사유를 감추면 원인 추적이 어려워진다.
return " · ".join(parts)   # FAILURE_REASON_LABELS.get(reason, reason)
```

> **[전공] "외부 시스템의 값"과 "내 도메인의 값"을 구분하라.**
> 내가 정의하는 상태(`SubmissionStatus`)는 enum이 맞다.
> 남이 정의하는 값(`failure_reason`)은 **문자열로 받고 표시할 때만 번역**한다.
> 그래야 상대가 새 값을 추가해도 내 시스템이 안 깨진다.

---

## 5. `relationship` — ORM의 마법과 함정

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

**[전공]**
`relationship()`은 **컬럼이 아니다.** DB에 아무것도 안 만든다.
ForeignKey가 이미 만든 연결을 **파이썬에서 객체로 탐색할 수 있게** 해주는 매핑일 뿐이다.

```python
season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"))  # ← 실제 컬럼
season: Mapped["Season"] = relationship(back_populates="teams")                        # ← 탐색 도구
```

### `back_populates` — 양방향 동기화

한쪽을 바꾸면 **메모리 상에서** 반대쪽도 즉시 반영된다.
```python
team.season = some_season
assert team in some_season.teams   # 이미 True (커밋 전에도)
```
없으면 두 개의 독립된 관계가 되어 한쪽만 갱신된다 → 같은 세션에서 **틀린 값**을 본다.

> `backref`(한쪽에만 쓰면 반대쪽 자동 생성)라는 옛 방식도 있다. **2.0에서는 `back_populates` 권장.**

### `uselist=False` — 1:1 관계 만들기

없으면 `team.account`가 **리스트**가 된다. 하지만 진짜로 1:1을 **보장**하는 것은 이것이다:
```python
team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ...), unique=True)
                                                                  ^^^^^^^^^^^
```
**`unique=True`가 없으면 한 팀에 계정 2개가 들어갈 수 있다.** relationship은 못 막는다.

같은 원리가 `EvaluationResult.submission_id`에도 적용된다 — **제출 1건당 결과 1개를 DB가 강제한다.**
이게 6단계의 "재평가되어도 결과는 하나"를 보장하는 장치다.

### `order_by="Submission.submitted_at"`

문자열로 쓴 이유: `Submission` 클래스가 `Team`보다 **아래에 정의**되어 있어서
`Team` 정의 시점에는 아직 존재하지 않는다. SQLAlchemy가 나중에 해석한다.
같은 이유로 파일 맨 위에 `from __future__ import annotations`가 있다.

---

## 6. **cascade — 가장 헷갈리는 부분**

이 프로젝트에는 **두 종류의 cascade가 동시에** 쓰인다.

```python
season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"))
                                                                 ^^^^^^^^^^^^^^^^^^ (1) DB 레벨
teams: Mapped[list["Team"]] = relationship(back_populates="season", cascade="all, delete-orphan")
                                                                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^ (2) ORM 레벨
```

### (1) `ondelete="CASCADE"` — DB가 하는 일

DDL에 들어간다: `FOREIGN KEY(season_id) REFERENCES seasons(id) ON DELETE CASCADE`
psql에서 직접 `DELETE FROM seasons WHERE id=1` 해도 teams가 함께 지워진다.
**애플리케이션과 무관하게 DB 엔진이 보장한다.**

### (2) `cascade="all, delete-orphan"` — ORM이 하는 일

- `all` = `save-update, merge, refresh-expire, expunge, delete` 전부
- `delete-orphan` = **부모와의 연결이 끊긴 자식은 고아이므로 삭제**

### 왜 둘 다 필요한가?

| | ORM cascade만 | DB ondelete만 | 둘 다 (현재) |
|---|---|---|---|
| `db.delete(season)` | ✅ | ✅ | ✅ |
| psql에서 직접 DELETE | ❌ FK 위반 | ✅ | ✅ |
| `team.submissions.remove(x)` | ✅ | ❌ 아무 일 없음 | ✅ |

**핵심 교훈**: **DB 제약은 최후의 방어선, ORM은 편의.** 둘은 대체재가 아니라 보완재다.

> **[전공] 성능 함정**: ORM cascade로 시즌을 지우면 SQLAlchemy는
> **자식을 전부 메모리에 로드한 뒤 하나씩 DELETE**를 보낸다.
> 대량이면 `passive_deletes=True`로 DB에 맡겨야 한다. 10팀 규모라 괜찮다.

---

## 7. **부분 유니크 인덱스 — 이 프로젝트에서 가장 영리한 한 줄**

```python
class Submission(Base):
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
DB가 이걸 강제한다. 그런데 **완료된 제출은 몇 개든 상관없다.** 그래서 "부분(partial)"이다.

**[전공]**
일반 유니크 인덱스라면 `UNIQUE(team_id)` — 팀당 제출이 평생 1건뿐이 된다. 말이 안 된다.
`WHERE` 절을 붙여 **인덱스에 포함되는 행 자체를 제한**한다.
포함 안 된 행(`done`, `error`)은 유니크 검사 대상이 아니다.

### 왜(Why) — 파이썬 `if`문으로는 왜 부족한가

`submissions.py`에는 이미 검사가 있다:
```python
if has_active_submission(db, team) is not None:
    return redirect_with_error("이전 제출의 결과가 아직 나오지 않았습니다...")
```

**이걸로 충분해 보이지만 아니다.** 참가자가 제출 버튼을 **더블클릭**했다:

```
시각   요청 A                              요청 B
────────────────────────────────────────────────────────────────
t=0    SELECT ... status IN (queued,running)
t=1                                        SELECT ...
t=2    결과: 0건 → 통과!
t=3                                        결과: 0건 → 통과!  ← A가 아직 INSERT 안 함
t=4    INSERT (queued)
t=5                                        INSERT (queued)   ← 중복 발생!!
```

이것이 고전적인 **TOCTOU(Time-Of-Check to Time-Of-Use)** 경쟁 조건이다.

**DB 유니크 인덱스는 왜 이걸 막나?**
인덱스 삽입은 **원자적**이다. B-tree에 키를 넣는 순간 락이 걸리고,
두 번째 INSERT는 첫 번째가 끝날 때까지 대기했다가 위반이면 실패한다.

> **참고: `upload.js`가 이 상황을 줄여준다.** 업로드 중에는 버튼을 비활성화한다:
> ```javascript
> function enterUploadingState() {
>   uploading = true;
>   submitButton.disabled = true;
> ```
> **하지만 이건 UX 개선이지 방어가 아니다.** JS를 끄거나 `curl`로 직접 쏘면 그만이다.

### 어떻게(How) — 그런데 지금 코드에 남은 문제

**중요**: `submissions.py`는 여전히 `IntegrityError`를 **잡지 않는다**.

```python
db.add(submission)
db.commit()          # ← 여기서 유니크 위반이면 IntegrityError 예외
```

경쟁이 실제로 일어나면 두 번째 요청은 **500 Internal Server Error**가 뜬다.
데이터는 안전하다(중복이 안 들어감). 하지만 사용자 경험은 나쁘고,
**업로드한 250MB 파일이 고아로 남는다.**

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

> **개선 여지로 기록해 둘 만하다.** 데이터 무결성은 지켜지므로 심각도는 낮지만,
> **"방어선은 있는데 그 방어선에 걸렸을 때의 UX가 없다"** 는 상태다.

### 이 패턴의 일반화

**애플리케이션 검사 = 친절한 안내, DB 제약 = 진짜 보장.**

| 규칙 | 앱 검사 | DB 제약 |
|---|---|---|
| 동시 제출 1건 | `has_active_submission()` + `upload.js` 버튼 비활성 | `uq_team_active_submission` |
| 시즌 내 팀명 중복 | `admin.py`의 `existing_names` | `uq_team_season_name` |
| 로그인 ID 중복 | (없음) | `Account.login_id unique=True` |
| 제출당 결과 1개 | (없음) | `EvaluationResult.submission_id unique=True` |
| 워커당 하트비트 1개 | (없음) | `WorkerHeartbeat.worker_id` PK |

---

## 8. Enum — 실제로 버그를 낸 곳

```python
Enum = partial(SAEnum, values_callable=lambda enum_cls: [e.value for e in enum_cls])
```

주석:
```python
# SQLAlchemy의 Enum은 기본적으로 파이썬 Enum 멤버의 .name(예: "QUEUED")을 DB에 저장한다.
# 우리는 소문자 .value(예: "queued")를 SQL에서 그대로 비교하므로(quota.py, worker/run.py의
# raw SQL), 반드시 .value를 저장하도록 values_callable을 지정해야 한다.
```

### 무엇이 문제였나 — 실제 장애 재현

**기본 동작**: SQLAlchemy는 DB에 `'QUEUED'`(대문자 name)를 저장한다.
그런데 워커의 raw SQL은 `WHERE status = 'queued'` (소문자).

→ **매칭이 하나도 안 된다. 워커가 큐를 영원히 못 집는다.**
증상: 제출은 되는데 아무리 기다려도 "대기 중"에서 안 넘어감. **에러도 안 남.**

### 교훈 — 왜 이런 일이 생기는가

**표현(representation)이 두 곳에 존재하면 반드시 어긋난다.**

**근본 해결책 3가지**:
1. `values_callable`로 맞춘다 (현재 방식) — 간단
2. raw SQL을 안 쓰고 전부 ORM으로 (`FOR UPDATE SKIP LOCKED` 때문에 어려움)
3. raw SQL에서도 바인드 파라미터로 `SubmissionStatus.QUEUED.value`를 넘긴다

**3번이 사실 가장 안전하다.** 현재 `CLAIM_NEXT_SQL`은 `'queued'`, `'running'`이
문자열로 하드코딩되어 있어, 나중에 enum 값을 바꾸면 또 어긋난다.

같은 위험이 인덱스 정의에도 있다:
```python
postgresql_where=text("status IN ('queued', 'running')")
```

반면 `retention.py`는 올바른 패턴이다:
```python
if submission.status.value in ACTIVE_SUBMISSION_STATUSES:
```
`ACTIVE_SUBMISSION_STATUSES` 상수를 참조한다.

> **자가 점검**: `SubmissionStatus.QUEUED`의 값을 `"pending"`으로 바꾸면
> 코드 몇 군데를 같이 고쳐야 하는가? 세어보라. (답: 최소 3곳 + 마이그레이션)

### `native_enum=False` — 왜 PostgreSQL ENUM 타입을 안 쓰나

| | native enum (`CREATE TYPE`) | `native_enum=False` (VARCHAR + CHECK) |
|---|---|---|
| 저장 | 4바이트 | 문자열 |
| 값 추가 | `ALTER TYPE ... ADD VALUE` — **트랜잭션 제약이 있고 까다롭다** | CHECK 제약 수정 |
| 값 제거 | **사실상 불가능** | 가능 |
| DB 이식성 | PostgreSQL 전용 | 어디서나 |

**결론**: 상태 값이 나중에 늘어날 가능성이 있는 초기 프로젝트에서는
`native_enum=False`가 **운영 난이도를 크게 낮춘다.**

**§4에서 본 `FinishStatus.TIMEOUT` 문제가 이 선택의 가치를 보여준다.**
만약 native enum이었다면 값을 고치는 마이그레이션이 훨씬 무서웠을 것이다.
(결국 값을 안 고치고 컬럼을 추가하는 쪽을 택했지만, 선택지가 있는 것과 없는 것은 다르다.)

---

## 9. 시간 컬럼 — `server_default` vs `default`

```python
submitted_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
started_at:   Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

| | 누가 값을 만드나 | 생성되는 SQL |
|---|---|---|
| `default=datetime.utcnow` | **파이썬** | `INSERT ... VALUES ('2026-07-26 10:00:00')` |
| `server_default=func.now()` | **DB** | `DEFAULT now()` (DDL에 박힘) |

**`server_default`가 나은 이유:**
1. **시계가 하나다.** 웹(클라우드), 워커(다른 기기), DB가 각자 다른 시계를 갖는데 DB 시계로 통일된다
2. psql에서 직접 INSERT해도 값이 채워진다
3. **동시성**: 여러 프로세스가 동시에 INSERT해도 순서가 DB 기준으로 일관된다

**1번이 원격 워커 구조에서 훨씬 중요해졌다.** 서버는 UTC, 노트북은 KST일 수 있다.

### `DateTime(timezone=True)` — 왜 반드시 필요한가

**[쉬움]** "밤 12시"라고만 적으면 어디의 12시인지 모른다.

**[전공]**
- `TIMESTAMP` (naive) — 시간대 정보 없음. **의미가 모호하다**
- `TIMESTAMPTZ` (aware) — 내부적으로 UTC로 저장하고, 조회 시 세션 타임존으로 변환

이게 없으면 하루 한도 계산이 깨진다:
```python
# app/quota.py:24-31
day_start = dt.datetime.combine(on_date, dt.time.min, tzinfo=KST)   # aware
stmt = select(...).where(Submission.finished_at >= day_start)        # 비교
```
`finished_at`이 naive면 **파이썬은 TypeError를 내고, DB는 조용히 틀린 결과**를 낸다.

그리고 `worker_status.py`도 aware 비교에 의존한다:
```python
now = dt.datetime.now(tz=dt.timezone.utc)
elapsed = now - last_seen          # last_seen이 naive면 TypeError
```

**철칙: 저장은 UTC(aware), 표시할 때만 로컬 타임존으로 변환.**

---

## 10. 마이그레이션 — 스키마의 버전 관리

### 무엇을(What)

**[쉬움]**
코드는 git으로 버전 관리한다. 그럼 **DB 표 구조**는? 그게 마이그레이션이다.

**[전공]**
Alembic은 `alembic_version` 테이블에 현재 리비전 ID를 저장한다.
`alembic upgrade head`는 그 값부터 최신까지의 `upgrade()`를 순서대로 실행한다.

**현재 리비전 체인 (4개):**
```
685df3cb9303  initial schema
     ↓
a1c4f2b8d907  daily_count_override → daily_count_adjustment  (컬럼 이름 변경 + 데이터 정리)
     ↓
c3e7a91b45d2  worker_heartbeats                              (테이블 추가)
     ↓
d4f1a2c86b73  best_progress_percent, failure_reason          (컬럼 추가)   ← head
```

### 왜(Why) — `Base.metadata.create_all()` 로 하면 안 되나?

`create_all()`은 **없는 테이블을 만들기만 한다.** 컬럼 추가·이름 변경·데이터 변환을 못 한다.
**운영 중인 DB에는 데이터가 들어있다.** 지우고 다시 만들 수 없다.

### 어떻게(How) — 마이그레이션 4개가 각각 다른 것을 가르쳐준다

#### #1 `685df3cb9303` — autogenerate로 만든 초기 스키마

`# ### commands auto generated by Alembic - please adjust! ###` 주석이 그대로 남아 있다.

주목할 부분:
```python
op.create_index('uq_team_active_submission', 'submissions', ['team_id'], unique=True,
                postgresql_where=sa.text("status IN ('queued', 'running')"))
```
**autogenerate가 `postgresql_where`까지 제대로 잡아냈다.** 다행이지만
alembic autogenerate는 인덱스/제약을 놓치는 경우가 많으므로 **항상 사람이 검토해야 한다.**

#### #2 `a1c4f2b8d907` — **이름 변경 + 데이터 정리** (가장 어려운 유형)

```python
def upgrade() -> None:
    op.alter_column("teams", "daily_count_override", new_column_name="daily_count_adjustment")
    op.alter_column("teams", "daily_count_override_date", new_column_name="daily_count_adjustment_date")
    # 기존 값은 절대값이라 델타로서는 의미가 없다. 그대로 두면 카운트가 잘못 부풀려지므로 비운다.
    op.execute("UPDATE teams SET daily_count_adjustment = NULL, daily_count_adjustment_date = NULL")
```

**여기서 배울 것 3가지:**

**(a) 마이그레이션은 스키마만이 아니라 데이터도 옮긴다.**
컬럼 이름만 바꾸고 값을 그대로 두면 **의미가 달라진 값**이 남는다.
`override=3`(절대값 3회 사용)이 `adjustment=3`(3회 더하기)으로 해석되어 카운트가 부풀려진다.

**(b) 왜 override → adjustment로 바꿨나 (설계 버그의 교훈)**

**[쉬움]**
- 옛날 방식: "이 팀은 오늘 3번 썼다고 쳐라" (절대값)
  → 그 뒤에 실제로 2번 더 하면? 여전히 3번. **한도가 영영 안 걸린다.**
- 새 방식: "이 팀 카운트에 +3 해라" (델타)
  → 실제 2번 + 보정 3 = 5. **정상 동작.**

**[전공]** **파생값을 저장하려다 생긴 문제**다.
"오늘 사용 횟수"는 submissions에서 **계산되는 값**인데, override는 계산을 무력화했다.
→ **계산 가능한 값은 저장하지 말고, 꼭 필요하면 보정만 하라.**

관리자 화면은 여전히 절대값을 입력받되, 내부에서 델타로 변환한다:
```python
# app/routers/admin.py:379-383
team.daily_count_adjustment = None
actual_done = get_daily_done_count(db, team, today)
team.daily_count_adjustment = max(count, 0) - actual_done
```
**UI는 직관적으로(절대값), 저장은 안전하게(델타).**

**(c) `downgrade()`도 작성했다.** 데이터 복구는 불가능하지만 스키마는 되돌아간다.

#### #3 `c3e7a91b45d2` — **테이블 추가** (가장 안전한 유형)

```python
def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(length=100), primary_key=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
```

**왜 안전한가?** 기존 테이블을 건드리지 않는다. 롤백도 `drop_table` 한 줄.

**하지만 애플리케이션은 이 테이블이 **비어 있는 상태**를 견뎌야 한다:**
```python
# app/worker_status.py:22-24
last_seen = db.execute(select(func.max(WorkerHeartbeat.last_seen_at))).scalar_one_or_none()
if last_seen is None:
    return {"online": False, "last_seen_at": None, "minutes_ago": None}
```
docstring이 명시한다:
> 하트비트가 한 번도 없으면(기능 도입 직후·워커 미기동) online=False로 본다.

**"보수적으로 실패"** 를 택했다 — 모르면 "온라인"이 아니라 "오프라인"이다.
잘못 "온라인"이라고 하면 참가자가 기다리다 지치지만, 잘못 "오프라인"이라고 하면
불필요한 안내가 잠깐 뜰 뿐이다. **덜 나쁜 쪽을 골랐다.**

#### #4 `d4f1a2c86b73` — **nullable 컬럼 추가** (하위 호환의 정석)

```python
def upgrade() -> None:
    op.add_column("evaluation_results", sa.Column("best_progress_percent", sa.Float(), nullable=True))
    op.add_column("evaluation_results", sa.Column("failure_reason", sa.String(length=50), nullable=True))
```

docstring:
> 기존 레코드는 NULL로 남고, 화면은 값이 없으면 예전 문구를 그대로 쓴다.

### **[전공] 왜 `nullable=True`가 핵심인가 — 무중단 배포의 기본기**

`nullable=False`로 컬럼을 추가하려면 **모든 기존 행에 값이 있어야 한다.**
그래서 `server_default`를 주거나, 3단계로 나눠야 한다:
1. nullable로 추가 → 2. 기존 행 채우기 → 3. NOT NULL로 변경

**게다가 PostgreSQL에서 `ALTER TABLE ... ADD COLUMN ... NOT NULL DEFAULT ...`는
버전에 따라 테이블 전체 재작성(rewrite)을 유발해 큰 테이블에서 락이 오래 걸린다.**
(PG 11+에서 상수 default는 개선됐지만, 습관은 여전히 nullable 우선이 안전하다.)

**우리 규모에선 상관없지만 습관이 중요하다.**

### **컬럼 추가가 "안전한" 진짜 이유 — 배포 순서**

```
시각    DB 스키마                코드
─────────────────────────────────────────────────
t=0     옛 스키마                옛 코드           정상
t=1     새 스키마(컬럼 추가됨)    옛 코드           ← 여기가 중요!
t=2     새 스키마                새 코드           정상
```

**t=1 구간이 반드시 존재한다.** `Dockerfile`의 `CMD`가
`alembic upgrade head && uvicorn ...` 이므로 마이그레이션이 **먼저** 돌고 서버가 뜬다.
그 사이 짧은 시간(그리고 재시작 실패 시 긴 시간) 동안 옛 코드가 새 스키마를 본다.

**컬럼 추가는 옛 코드가 무시하면 그만이다.** 그래서 안전하다.
**컬럼 삭제·이름 변경은 옛 코드를 즉시 깨뜨린다.** 그래서 위험하다.

> **[전공] 이것이 "확장 후 수축(expand-contract)" 패턴이다.**
> 1. **확장**: 새 컬럼 추가 (옛 코드도 동작)
> 2. 새 코드 배포 (둘 다 씀)
> 3. **수축**: 옛 컬럼 삭제 (충분히 지난 뒤)
>
> `a1c4f2b8d907`(이름 변경)은 이 원칙을 어긴 마이그레이션이다.
> 단일 인스턴스 + 짧은 다운타임 허용이라 문제가 없었을 뿐이다.

### `migrations/env.py` 의 중요한 한 줄

```python
from app import models  # noqa: F401  (모델을 등록하기 위해 import)
```

**왜 이게 필요한가?** `target_metadata = Base.metadata`인데,
`models.py`를 import하지 않으면 **어떤 클래스도 정의되지 않아 metadata가 비어 있다.**
→ autogenerate가 "모든 테이블을 삭제하라"는 마이그레이션을 만들어낸다. **재앙이다.**

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

**장점**: 배포 절차가 단순하다. `docker compose up -d --build` 한 줄.
**단점**: 웹 컨테이너를 여러 개로 늘리면 **동시에 마이그레이션을 실행**해 충돌한다.
지금은 1개뿐이라 안전하다. (`docker-compose.prod.yml`도 `web` 하나다.)

---

## 11. 알아둬야 할 함정 — Lazy Loading과 N+1

### 무엇이 문제인가

```python
# app/routers/leaderboard.py:35-47
teams = db.execute(select(Team).where(...)).scalars().all()

for team in teams:
    best_submission, best_result = get_team_best(team)          # team.submissions 접근
    total_submissions = sum(1 for s in team.submissions if ...)
    ...
    unranked.append({..., "best_attempt": _best_attempt(team)}) # 또 team.submissions 접근
```

**실제로 나가는 쿼리:**
```sql
SELECT * FROM teams WHERE season_id=1 AND disqualified=false;      -- 1번
SELECT * FROM submissions WHERE team_id=1 ORDER BY submitted_at;    -- +1
SELECT * FROM evaluation_results WHERE submission_id=1;             -- +1
SELECT * FROM evaluation_results WHERE submission_id=2;             -- +1
...  (팀마다 반복)
```

이것이 **N+1 문제**다.

**[쉬움]**
반 학생 30명의 성적을 알아보려고, 먼저 명단을 받고(1번),
학생마다 교무실에 한 번씩 가서 성적을 물어본다(30번). 31번 왕복.

### 왜 지금은 괜찮은가

- 팀 10개, 팀당 제출 최대 ~70건
- 로컬 DB라 쿼리당 왕복 **1ms 미만**
- 리더보드 조회는 초당 몇 건 수준

`plan.md §4`에서 "규모가 작아 쿼리로 즉시 계산"이라 명시적으로 판단한 결과다.
**의식하고 내린 결정이면 그건 설계지 버그가 아니다.**

> **다만 클라우드로 옮기면서 조건이 바뀌었다.**
> 웹과 DB가 **같은 서버의 다른 컨테이너**라 여전히 왕복이 짧다(유닉스 소켓은 아니지만 로컬 네트워크).
> 만약 DB를 별도 호스트로 분리하면 왕복이 수 ms가 되어 **체감되기 시작한다.**
> `mem_limit: 900m` 같은 제약도 있으니, 규모가 커지면 가장 먼저 손볼 곳이다.

### 어떻게 고치는가

```python
from sqlalchemy.orm import selectinload
from app.models import Submission

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

---

## 12. 자가 점검 질문

1. 파일(JSON) 대신 DB를 쓰는 결정적 이유를 동시성 시나리오로 설명하라.
2. `ForeignKey(ondelete="CASCADE")`와 `relationship(cascade="all, delete-orphan")`의 차이는? 왜 둘 다?
3. `uselist=False`만으로 1:1이 보장되는가? 무엇이 더 필요한가?
4. `WorkerHeartbeat`에 FK가 없는 이유는? "값이 같다"와 "참조 관계다"의 차이는?
5. 하트비트를 INSERT가 아니라 UPDATE로 하는 이유는? 30초 주기로 INSERT하면 한 달에 몇 행인가?
6. `get_worker_status`가 `worker_id`로 필터하지 않고 `MAX()`를 쓰는 이유는?
7. `failure_reason`이 enum이 아니라 `String(50)`인 이유는? 이 원칙을 일반화하면?
8. `FinishStatus.TIMEOUT`이라는 이름이 왜 거짓말이 됐는가? 어떻게 이름 지었어야 했나?
9. 부분 유니크 인덱스가 없고 파이썬 `if`만 있을 때, 더블클릭이 만드는 문제를 시각 순서대로 설명하라.
10. `upload.js`의 버튼 비활성화는 왜 방어가 아닌가?
11. 현재 코드에서 그 유니크 인덱스에 실제로 걸리면 사용자는 무엇을 보게 되는가? 파일은 어떻게 되는가?
12. `values_callable`이 없으면 워커에 무슨 일이 생기는가? 그 증상은 왜 진단하기 어려운가?
13. `native_enum=False` 선택이 `FinishStatus` 문제에서 어떤 선택지를 열어줬는가?
14. `server_default`가 원격 워커 구조에서 더 중요해진 이유는?
15. 마이그레이션 4개를 유형별로 분류하고, 각각의 위험도를 설명하라.
16. `nullable=True`로 컬럼을 추가하는 것이 왜 무중단 배포의 기본기인가?
17. "확장 후 수축(expand-contract)" 패턴이란? `a1c4f2b8d907`은 그 원칙을 지켰는가?
18. `worker_heartbeats` 테이블이 비어 있을 때 앱이 어떻게 동작하는가? 왜 그 방향인가?
19. `migrations/env.py`에서 `from app import models`를 지우면 어떤 재앙이 일어나는가?
20. 리더보드 조회 시 N+1이 발생하는 정확한 지점 3개는? 클라우드 이관으로 무엇이 바뀌었는가?

---

## 13. 실험 과제

**실험 A — 부분 유니크 인덱스를 직접 때려보기**
```sql
-- 테스트용 DB에서만!
INSERT INTO submissions (team_id, model_path, status) VALUES (1, 'a', 'queued');
INSERT INTO submissions (team_id, model_path, status) VALUES (1, 'b', 'queued');  -- 에러!
INSERT INTO submissions (team_id, model_path, status) VALUES (1, 'c', 'done');    -- 성공!
```
**세 번째가 왜 성공하는지** 설명할 수 있어야 한다.

**실험 B — Enum 저장 형태 확인**
```sql
SELECT id, status FROM submissions LIMIT 5;
SELECT worker_id, last_seen_at, now() - last_seen_at AS 경과 FROM worker_heartbeats;
```
소문자인가 대문자인가? 하트비트가 30초 이내인가?

**실험 C — 하트비트 동작 관찰**
워커를 띄운 채 위 쿼리를 30초 간격으로 두 번 실행하라. `last_seen_at`이 갱신되는가?
그다음 워커를 Ctrl+C로 끄고 4분 뒤 리더보드를 열어보라. 안내 배너가 뜨는가?
```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_worker_status.py -v
```

**실험 D — N+1 눈으로 보기**
`app/db.py`에서 `create_engine(..., echo=True)`로 바꾸고 리더보드를 연다.
콘솔에 쏟아지는 SELECT를 센다. 그다음 `selectinload`를 적용하고 다시 센다.

**실험 E — 마이그레이션 왕복**
```bash
alembic current          # 지금 리비전 (d4f1a2c86b73 여야 한다)
alembic history          # 체인 확인
alembic downgrade -1     # 한 칸 되돌리기
alembic current
alembic upgrade head     # 복구
```
테스트 DB에서만. `evaluation_results` 컬럼이 실제로 사라졌다 돌아오는지 psql로 확인하라.

**실험 F — 하위 호환 확인**
```sql
UPDATE evaluation_results SET best_progress_percent = NULL, failure_reason = NULL WHERE id = 1;
```
그 결과를 가진 팀의 `/submit`과 리더보드를 열어보라.
화면이 깨지는가, "완주 실패"만 뜨는가? **`render.py`의 `if progress is not None`이 여기서 일한다.**

**실험 G — autogenerate가 무엇을 잡아내는지**
`models.py`에 `Team.memo: Mapped[str | None] = mapped_column(String(200), nullable=True)`를 추가하고
```bash
alembic revision --autogenerate -m "add memo"
```
생성된 파일을 열어보라. 그리고 **적용하지 말고 삭제**한다.

---

→ 다음: [03-auth.md](03-auth.md) — 서버는 요청자가 누구인지 어떻게 알고, 어떻게 숨기는가
