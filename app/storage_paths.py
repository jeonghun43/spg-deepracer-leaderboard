"""storage/ 아래 파일 경로를 웹과 워커가 동일하게 해석하기 위한 유틸.

웹은 `web` 컨테이너 안에서(`/app/storage/...`), 워커는 WSL 호스트에서
(`/mnt/c/.../spg_deepracer_leaderboard/storage/...`) 같은 디렉터리를 bind mount로
공유한다. 그래서 절대 경로를 그대로 DB에 적으면 한쪽에서 만든 경로를 다른 쪽이
찾지 못한다 (2026-07-26 운영 장애: 업로드한 모델을 워커가 못 찾아 평가 실패).

규칙: **DB에는 storage_dir 기준 상대 경로를 적고, 읽는 쪽이 자기 환경의
settings.storage_dir에 붙여 해석한다.** 이미 절대 경로로 쌓인 기존 레코드도
그대로 동작하도록 해석 단계에서 흡수한다.
"""

from pathlib import Path

from app.config import settings

STORAGE_DIR_NAME = "storage"


def _split(raw: str) -> list[str]:
    """OS와 무관하게 경로를 세그먼트로 쪼갠다 (컨테이너가 만든 POSIX 경로를
    Windows에서 해석하는 경우까지 감안해 두 구분자를 모두 취급한다)."""
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
    if ".." in parts:
        raise ValueError(f"상위 디렉터리 참조가 포함된 경로는 허용하지 않습니다: {raw}")
    return parts


def _looks_absolute(raw: str) -> bool:
    return raw.startswith("/") or raw.startswith("\\") or (len(raw) > 1 and raw[1] == ":")


def _reroot(raw: str) -> Path | None:
    """경로에서 마지막 `storage` 세그먼트 뒤쪽을 잘라 현재 환경의 storage_dir에 붙인다.

    `/app/storage/models/1/6/x.tar.gz` → `<settings.storage_dir>/models/1/6/x.tar.gz`
    """
    parts = _split(raw)
    if STORAGE_DIR_NAME not in parts:
        return None
    idx = len(parts) - 1 - parts[::-1].index(STORAGE_DIR_NAME)
    tail = parts[idx + 1 :]
    if not tail:
        return None
    return settings.storage_dir.joinpath(*tail)


def to_storage_relative(path: Path) -> str:
    """저장용 경로 문자열을 만든다 — storage_dir 기준 상대 경로(POSIX 구분자).

    storage_dir 밖의 경로가 들어오면 변환하지 않고 그대로 돌려준다. 업로드 자체가
    이 함수 때문에 실패하는 일은 없어야 하기 때문이다.
    """
    try:
        return path.relative_to(settings.storage_dir).as_posix()
    except ValueError:
        return str(path)


def resolve_storage_path(stored: str | Path) -> Path:
    """DB에 적힌 경로를 현재 환경에서 실제로 열 수 있는 경로로 바꾼다.

    1. 상대 경로 → storage_dir 기준으로 해석 (지금 방식)
    2. 절대 경로인데 존재 → 그대로 사용 (2026-07-25 이전 레코드 호환)
    3. 절대 경로인데 없음 → `storage/` 이후를 잘라 재루팅 (컨테이너↔호스트 경로 차이 흡수)
    """
    raw = str(stored)
    if not _looks_absolute(raw):
        return settings.storage_dir.joinpath(*_split(raw))

    path = Path(raw)
    if path.exists():
        return path
    return _reroot(raw) or path
