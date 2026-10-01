"""시즌에 업로드 일시 중지 스위치 추가

지난 대회 온라인 주행 기간에 평가 서버 문제로 긴급 수정을 했는데, 그동안 참가자 업로드를
막을 방법이 없었다. 시즌 상태는 한 방향(진행중 → 마감 → 아카이브)으로만 바뀌어서 막는 용도로
쓸 수 없다. 관리자가 켜고 끌 수 있는 별도 플래그와 참가자 공지 문구를 둔다.

기존 시즌은 false(업로드 허용)로 채워진다.

Revision ID: e8b2c5d17f40
Revises: d4f1a2c86b73
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e8b2c5d17f40"
down_revision: Union[str, None] = "d4f1a2c86b73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "seasons",
        sa.Column("uploads_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "seasons", sa.Column("uploads_paused_message", sa.String(length=500), nullable=True)
    )
    op.add_column(
        "seasons", sa.Column("uploads_paused_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("seasons", "uploads_paused_at")
    op.drop_column("seasons", "uploads_paused_message")
    op.drop_column("seasons", "uploads_paused")
