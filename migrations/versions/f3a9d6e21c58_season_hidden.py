"""시즌에 숨김 여부 추가

모든 시즌이 공개 목록에 그대로 보였다. 지난 시즌·테스트 시즌을 방문자에게서 치우거나 비공개로
리허설 대회를 돌릴 방법이 없었다. 관리자와 그 시즌의 참가팀에게는 계속 보이고, 방문자에게만
감춘다 (plan.md §5.7).

기존 시즌은 false(공개)로 채워진다.

Revision ID: f3a9d6e21c58
Revises: e8b2c5d17f40
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3a9d6e21c58"
down_revision: Union[str, None] = "e8b2c5d17f40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "seasons",
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("seasons", "hidden")
