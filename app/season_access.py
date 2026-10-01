"""누가 어떤 시즌을 볼 수 있는가 — 시즌 목록·리더보드·평가 영상이 같은 규칙을 쓴다 (plan.md §5.7).

숨김 시즌은 관리자와 **그 시즌의 참가팀**만 본다. 팀까지 막지 않는 이유는 숨김이 지난 시즌·테스트
시즌을 공개 목록에서 치우거나 비공개 리허설을 돌리는 용도라서다 — 참가팀은 자기 기록을 봐야 한다.

규칙을 한 곳에 두는 이유: 목록에서만 빼고 리더보드 주소나 영상 주소를 막지 않으면, 번호만 바꿔
넣어 그대로 열린다. 세 군데가 따로 판단하면 언젠가 하나가 어긋난다.
"""

from app.models import AdminAccount, Season, Team


def can_view_season(season: Season, team: Team | None, admin: AdminAccount | None) -> bool:
    if not season.hidden:
        return True
    if admin is not None:
        return True
    return team is not None and team.season_id == season.id
