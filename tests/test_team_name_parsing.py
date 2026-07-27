"""팀 일괄 등록 입력 파싱 규칙 검증 (ux-improvements.md §2-2).

관리자가 엑셀·메모장에서 붙여넣는 실제 입력을 그대로 흘려보내도 팀 이름만 남아야 한다.
"""

from app.routers.admin import MAX_BULK_TEAMS, parse_team_names


def test_one_name_per_line():
    assert parse_team_names("1조\n2조\n3조") == ["1조", "2조", "3조"]


def test_blank_lines_and_surrounding_spaces_are_dropped():
    assert parse_team_names("  1조  \n\n\n  2조\n   \n") == ["1조", "2조"]


def test_comma_and_tab_are_also_separators():
    """엑셀에서 한 행을 복사하면 탭으로, 손으로 적으면 쉼표로 구분되는 경우가 많다."""
    assert parse_team_names("1조, 2조\t3조\n4조") == ["1조", "2조", "3조", "4조"]


def test_duplicate_names_within_input_are_deduplicated():
    assert parse_team_names("1조\n2조\n1조") == ["1조", "2조"]


def test_empty_input_yields_no_names():
    assert parse_team_names("") == []
    assert parse_team_names("  \n \t \n") == []


def test_windows_line_endings():
    assert parse_team_names("1조\r\n2조\r\n") == ["1조", "2조"]


def test_bulk_limit_is_enforced_by_caller_not_parser():
    """파서는 개수를 자르지 않는다 — 상한 초과는 라우터가 '아무것도 만들지 않고 거부'로 처리한다."""
    names = parse_team_names("\n".join(f"팀{i}" for i in range(MAX_BULK_TEAMS + 5)))
    assert len(names) == MAX_BULK_TEAMS + 5
