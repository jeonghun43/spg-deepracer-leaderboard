"""평가 서버 자동 켜기·끄기 표 추가

평가 서버(EC2 온디맨드)를 평소에는 꺼 두고, 제출이 들어오면 웹 서버의 autopilot 컨테이너가
켜고, 할 일이 없으면 평가 서버가 스스로 끈다(worker-auto-start-stop-plan.md).

- autopilot_state: 관리자 페이지 스위치와 autopilot이 마지막으로 본 EC2 상태. 항상 id=1 한 줄
- autopilot_events: 사건 기록 겸 디스코드 알림 대기열

스위치 행을 여기서 미리 넣어 둔다. 코드가 "행이 없으면" 분기를 매번 두지 않아도 되고,
기본값이 꺼짐이라 배포 직후 아무것도 저절로 움직이지 않는다.

Revision ID: b7d3e9f15a62
Revises: f3a9d6e21c58
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7d3e9f15a62"
down_revision: Union[str, None] = "f3a9d6e21c58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "autopilot_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled_changed_by", sa.String(length=50), nullable=True),
        sa.Column("instance_state", sa.String(length=20), nullable=True),
        sa.Column("instance_state_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_worker_id", sa.String(length=100), nullable=True),
        sa.Column("last_start_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attention_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("INSERT INTO autopilot_state (id, enabled) VALUES (1, false)")

    op.create_table(
        "autopilot_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_autopilot_events_created_at", "autopilot_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_autopilot_events_created_at", table_name="autopilot_events")
    op.drop_table("autopilot_events")
    op.drop_table("autopilot_state")
