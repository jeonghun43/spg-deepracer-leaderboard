"""하루 완료 카운트 조정값을 절대값(override)에서 델타(adjustment)로 변경

관리자가 카운트를 절대값으로 덮어쓰면 그 이후에 완료된 평가가 카운트에 반영되지 않아
하루 한도가 영영 걸리지 않는 문제가 있었다. 실제 완료 건수에 더해지는 보정 델타로 바꾼다.

Revision ID: a1c4f2b8d907
Revises: 685df3cb9303
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a1c4f2b8d907"
down_revision: Union[str, None] = "685df3cb9303"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("teams", "daily_count_override", new_column_name="daily_count_adjustment")
    op.alter_column(
        "teams", "daily_count_override_date", new_column_name="daily_count_adjustment_date"
    )
    # 기존 값은 절대값이라 델타로서는 의미가 없다. 그대로 두면 카운트가 잘못 부풀려지므로 비운다.
    op.execute("UPDATE teams SET daily_count_adjustment = NULL, daily_count_adjustment_date = NULL")


def downgrade() -> None:
    op.alter_column("teams", "daily_count_adjustment", new_column_name="daily_count_override")
    op.alter_column(
        "teams", "daily_count_adjustment_date", new_column_name="daily_count_override_date"
    )
