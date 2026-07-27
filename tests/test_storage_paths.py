"""업로드 모델 경로 해석 규칙 검증 (ux-improvements.md §0).

웹은 컨테이너(`/app/storage/...`), 워커는 호스트(`/mnt/c/.../storage/...`)에서 돌기 때문에
DB에 절대 경로를 적으면 워커가 파일을 찾지 못한다. 여기서 고정하는 규칙:
상대 경로는 현재 환경의 storage_dir 기준으로, 절대 경로는 존재하면 그대로,
없으면 `storage/` 뒤쪽을 잘라 재루팅한다.
"""

import pytest

from app.config import settings
from app.storage_paths import resolve_storage_path, to_storage_relative


@pytest.fixture
def storage_dir(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    root.mkdir()
    monkeypatch.setattr(settings, "storage_dir", root)
    return root


def make_model_file(storage_dir, rel="models/1/6/model.tar.gz"):
    path = storage_dir.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("dummy")
    return path


def test_upload_path_is_stored_relative_to_storage_dir(storage_dir):
    path = make_model_file(storage_dir)
    assert to_storage_relative(path) == "models/1/6/model.tar.gz"


def test_relative_path_resolves_under_current_storage_dir(storage_dir):
    expected = make_model_file(storage_dir)
    assert resolve_storage_path("models/1/6/model.tar.gz") == expected


def test_container_absolute_path_is_rerooted_to_host(storage_dir):
    """운영 장애 재현 — 컨테이너가 적은 /app/storage/... 를 호스트 경로로 되살린다."""
    expected = make_model_file(storage_dir)
    resolved = resolve_storage_path("/app/storage/models/1/6/model.tar.gz")
    assert resolved == expected
    assert resolved.is_file()


def test_existing_absolute_path_is_used_as_is(storage_dir):
    """7/25 이전에 호스트 절대 경로로 저장된 기존 레코드는 그대로 열려야 한다."""
    path = make_model_file(storage_dir)
    assert resolve_storage_path(str(path)) == path


def test_unknown_absolute_path_is_returned_unchanged(storage_dir):
    """재루팅 단서(storage 세그먼트)가 없으면 원래 경로를 그대로 돌려준다 —
    워커가 '파일 없음'으로 명확히 실패하게 하기 위함."""
    resolved = resolve_storage_path("/somewhere/else/model.tar.gz")
    assert not resolved.is_file()
    assert resolved.as_posix() == "/somewhere/else/model.tar.gz"


def test_parent_traversal_is_rejected(storage_dir):
    with pytest.raises(ValueError):
        resolve_storage_path("models/../../etc/passwd")


def test_path_outside_storage_dir_is_kept_as_is(tmp_path, storage_dir):
    """storage_dir 밖 경로가 들어와도 업로드가 실패하지 않고 원래 값이 유지된다."""
    outside = tmp_path / "elsewhere" / "model.tar.gz"
    assert to_storage_relative(outside) == str(outside)
