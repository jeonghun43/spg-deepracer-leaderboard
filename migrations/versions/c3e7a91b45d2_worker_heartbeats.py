"""평가 워커 하트비트 테이블 추가

워커가 웹과 다른 기기에서 돌게 되면(cloud-migration.md) 노트북이 꺼졌을 때 평가만 멈춘다.
참가자 화면에 "고장"이 아니라 "대기 중"임을 알리려면 워커의 생존 신호가 필요하다.

Revision ID: c3e7a91b45d2
Revises: a1c4f2b8d907
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3e7a91b45d2"
down_revision: Union[str, None] = "a1c4f2b8d907"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(length=100), primary_key=True),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
