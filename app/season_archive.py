"""시즌 종료 처리 (아카이빙) — plan.md §5.4.

1. 각 팀의 최종 리더보드 항목(최고기록 제출의 모델/영상)만 남기고,
2. 최고기록이 아닌 나머지 제출의 모델·영상 원본 파일은 디스크에서 삭제하며,
3. 팀 로그인 계정(Account)은 삭제한다.
"""

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Season, SeasonStatus
from app.retention import prune_team_files

logger = logging.getLogger(__name__)


def archive_season(db: Session, season: Season, videos_dir: Path) -> None:
    for team in season.teams:
        # 최고기록 외 파일 삭제 규칙은 평가 직후 정리와 동일하므로 공용 함수를 쓴다
        # (app/retention.py). 평가 중에 이미 정리됐다면 여기서는 남은 것만 지운다.
        prune_team_files(team, videos_dir)

        if team.account is not None:
            db.delete(team.account)

    season.status = SeasonStatus.ARCHIVED
    db.commit()
