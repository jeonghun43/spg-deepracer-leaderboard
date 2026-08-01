"""업로드 응답 형식 분기 규칙 검증 (upload-progress-ux.md §2-4).

진행률을 표시하는 업로드 스크립트만 JSON을 받고, 그 외에는 지금까지와 똑같이
303 리다이렉트를 받아야 한다. 이 경계가 흔들리면 **스크립트 없이 제출하는 경로가
깨진다** — JS가 꺼진 브라우저에서도 제출은 되어야 한다는 것이 이 설계의 전제다.
"""

import pytest

from app.routers.submissions import wants_json_response


@pytest.mark.parametrize(
    "accept",
    [
        "application/json",
        "application/json, text/plain, */*",  # 업로드 스크립트가 실제로 보내는 형태
        "text/html, application/json;q=0.9",
        " APPLICATION/JSON ",
    ],
)
def test_json_요청은_json으로_답한다(accept):
    assert wants_json_response(accept) is True


@pytest.mark.parametrize(
    "accept",
    [
        None,
        "",
        # 브라우저가 폼을 전송할 때 보내는 헤더. 뒤에 */*가 붙어 있다고 해서 JSON으로
        # 답하면 화면 전환이 사라져 제출이 먹통이 된다.
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,*/*;q=0.8",
        "*/*",  # curl 기본값 — 기존 리다이렉트 동작을 유지한다
        "application/json-patch+json",  # 접두사만 같은 다른 타입
    ],
)
def test_그_외에는_기존_리다이렉트_경로를_쓴다(accept):
    assert wants_json_response(accept) is False
