"""평가 결과에 최고 진행률·실패 사유 추가

완주하지 못한 제출을 화면이 모두 "미완주(시간 초과)"로 표시해, 실제로는 차가 트랙 중간에
멈춘 경우(immobilized)에도 참가자가 시간 초과로 오해했다. 어디까지 갔고 왜 멈췄는지를
저장해 정확히 알려준다.

기존 레코드는 NULL로 남고, 화면은 값이 없으면 예전 문구를 그대로 쓴다.

Revision ID: d4f1a2c86b73
Revises: c3e7a91b45d2
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4f1a2c86b73"
down_revision: Union[str, None] = "c3e7a91b45d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "evaluation_results", sa.Column("best_progress_percent", sa.Float(), nullable=True)
    )
    op.add_column(
        "evaluation_results", sa.Column("failure_reason", sa.String(length=50), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("evaluation_results", "failure_reason")
    op.drop_column("evaluation_results", "best_progress_percent")
