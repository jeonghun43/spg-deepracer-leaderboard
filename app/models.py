from __future__ import annotations

import datetime as dt
import enum

from functools import partial

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)

# SQLAlchemy의 Enum은 기본적으로 파이썬 Enum 멤버의 .name(예: "QUEUED")을 DB에 저장한다.
# 우리는 소문자 .value(예: "queued")를 SQL에서 그대로 비교하므로(quota.py, worker/run.py의
# raw SQL), 반드시 .value를 저장하도록 values_callable을 지정해야 한다.
Enum = partial(SAEnum, values_callable=lambda enum_cls: [e.value for e in enum_cls])
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SeasonStatus(str, enum.Enum):
    PREPARING = "preparing"  # 준비중
    ACTIVE = "active"  # 진행중
    CLOSED = "closed"  # 마감 (제출 종료, 순위 확정)
    ARCHIVED = "archived"  # 아카이브됨 (비필수 데이터 삭제 완료)


class SubmissionStatus(str, enum.Enum):
    QUEUED = "queued"  # 대기
    RUNNING = "running"  # 평가중
    DONE = "done"  # 완료 (완주/미완주 모두 포함)
    ERROR = "error"  # 오류 (업로드 실패 / DRFC 실행 실패 - 하루 한도에서 제외)


class FinishStatus(str, enum.Enum):
    FINISHED = "finished"  # 완주
    TIMEOUT = "timeout"  # 미완주(시간 초과)


ACTIVE_SUBMISSION_STATUSES = (SubmissionStatus.QUEUED.value, SubmissionStatus.RUNNING.value)


class Season(Base):
    """시즌/대회. 대회당 트랙 1개 고정 (spec.md §8)."""

    __tablename__ = "seasons"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    track_name: Mapped[str] = mapped_column(String(100))
    start_date: Mapped[dt.date] = mapped_column(Date)
    end_date: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[SeasonStatus] = mapped_column(
        Enum(SeasonStatus, native_enum=False, length=20), default=SeasonStatus.PREPARING
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    teams: Mapped[list["Team"]] = relationship(back_populates="season", cascade="all, delete-orphan")


class Team(Base):
    """참가팀. 매 시즌 완전히 새로 등록한다 (plan.md §4)."""

    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("season_id", "name", name="uq_team_season_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    disqualified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # 관리자가 테스트/운영 편의로 "오늘 완료 카운트"를 조정한 보정값 (plan.md §5.3).
    # 절대값이 아니라 실제 완료 건수에 더해지는 델타다 — 절대값으로 두면 관리자가 지정한 뒤에
    # 실제로 완료된 평가가 카운트에 반영되지 않아 하루 한도가 영영 걸리지 않는다.
    daily_count_adjustment: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_count_adjustment_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    season: Mapped["Season"] = relationship(back_populates="teams")
    account: Mapped["Account"] = relationship(
        back_populates="team", uselist=False, cascade="all, delete-orphan"
    )
    submissions: Mapped[list["Submission"]] = relationship(
        back_populates="team", cascade="all, delete-orphan", order_by="Submission.submitted_at"
    )


class Account(Base):
    """팀 로그인 계정. 시즌 아카이브 시 삭제 대상 (plan.md §5.4)."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), unique=True)
    login_id: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))

    team: Mapped["Team"] = relationship(back_populates="account")


class AdminAccount(Base):
    """관리자 계정. 팀 계정과 완전히 분리된 권한 체계 (plan.md §6)."""

    __tablename__ = "admin_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    login_id: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))


class Submission(Base):
    """모델 제출. 팀당 대기/평가중 상태는 동시에 최대 1건 (spec.md §8, plan.md §5.1)."""

    __tablename__ = "submissions"
    __table_args__ = (
        # 팀별로 "대기" 또는 "평가중" 상태 레코드는 동시에 최대 1개만 허용한다.
        Index(
            "uq_team_active_submission",
            "team_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    submitted_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    model_path: Mapped[str] = mapped_column(String(500))
    status: Mapped[SubmissionStatus] = mapped_column(
        Enum(SubmissionStatus, native_enum=False, length=20), default=SubmissionStatus.QUEUED
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    team: Mapped["Team"] = relationship(back_populates="submissions")
    result: Mapped["EvaluationResult"] = relationship(
        back_populates="submission", uselist=False, cascade="all, delete-orphan"
    )

    @property
    def counts_toward_daily_limit(self) -> bool:
        """하루 제출 한도는 '완료'(완주든 미완주든)만 카운트하고 '오류'는 제외한다."""
        return self.status == SubmissionStatus.DONE


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


class EvaluationResult(Base):
    """평가 결과. Submission 1건당 1개, 완주 실패(미완주-타임아웃)도 유효한 결과다."""

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

    submission: Mapped["Submission"] = relationship(back_populates="result")
