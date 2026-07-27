"""제출 아카이브 안에서 실제 모델 폴더를 찾아내는 로직 검증.

실제 운영 중 참가자가 MinIO 데이터 폴더를 통째로 압축해 올리는 사례가 확인되어
(2026-07-24 테스트), 그 경우를 명확한 안내 문구와 함께 걸러내는지도 확인한다.
"""

import pytest

from worker.drfc import EvaluationError, _find_model_root


def test_finds_metadata_at_archive_root(tmp_path):
    (tmp_path / "model_metadata.json").write_text("{}")
    (tmp_path / ".coach_checkpoint").write_text("")
    assert _find_model_root(tmp_path) == tmp_path


def test_finds_metadata_inside_wrapper_folder(tmp_path):
    inner = tmp_path / "my-model"
    inner.mkdir()
    (inner / "model_metadata.json").write_text("{}")
    assert _find_model_root(tmp_path) == inner


def test_finds_metadata_nested_several_levels_deep(tmp_path):
    inner = tmp_path / "export" / "rl-deepracer-sagemaker" / "model"
    inner.mkdir(parents=True)
    (inner / "model_metadata.json").write_text("{}")
    assert _find_model_root(tmp_path) == inner


def test_prefers_shallowest_metadata_when_multiple(tmp_path):
    shallow = tmp_path / "model"
    shallow.mkdir()
    (shallow / "model_metadata.json").write_text("{}")
    deep = tmp_path / "model" / "backup" / "old"
    deep.mkdir(parents=True)
    (deep / "model_metadata.json").write_text("{}")
    assert _find_model_root(tmp_path) == shallow


def test_minio_raw_dump_gives_actionable_error(tmp_path):
    """MinIO 내부 저장 형식: 오브젝트가 '폴더 + xl.meta'로 저장돼 실제 모델 파일이 없다."""
    obj_dir = tmp_path / "rl-deepracer-sagemaker" / "model" / "model_metadata.json"
    obj_dir.mkdir(parents=True)
    (obj_dir / "xl.meta").write_bytes(b"\x00binary")

    with pytest.raises(EvaluationError) as exc_info:
        _find_model_root(tmp_path)
    message = str(exc_info.value)
    assert "MinIO" in message
    assert "aws s3 sync" in message


def test_missing_metadata_gives_plain_error(tmp_path):
    (tmp_path / "readme.txt").write_text("hello")
    with pytest.raises(EvaluationError) as exc_info:
        _find_model_root(tmp_path)
    assert "model_metadata.json" in str(exc_info.value)
