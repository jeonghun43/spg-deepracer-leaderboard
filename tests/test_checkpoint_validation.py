"""평가에 쓸 체크포인트가 아카이브에 있는지 미리 검사하는 규칙 (tasks.md 12-2).

배경: 대회 규칙을 `DR_EVAL_CHECKPOINT=best`로 바꿨다. 아카이브에 best 정보가 없으면
시뮬레이터가 한참 뒤에 알 수 없는 오류로 죽어 참가자가 원인을 모른다. 주입 **전에** 걸러야
잘못된 제출이 이전 모델을 지우는 일도 막을 수 있다.
"""

import json

import pytest

from worker import drfc


def make_model_root(tmp_path, index=None):
    root = tmp_path / "model"
    root.mkdir()
    (root / "model_metadata.json").write_text("{}", encoding="utf-8")
    if index is not None:
        (root / drfc.CHECKPOINT_INDEX_FILE).write_text(
            json.dumps(index), encoding="utf-8"
        )
    return root


BOTH = {
    "best_checkpoint": {"name": "4_Step-2337.ckpt", "avg_eval_metric": 100},
    "last_checkpoint": {"name": "5_Step-3241.ckpt", "avg_eval_metric": 85.8},
}


def test_best_present_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("DR_EVAL_CHECKPOINT", "best")
    drfc.validate_checkpoint_selection(make_model_root(tmp_path, BOTH))


def test_best_missing_is_rejected_with_korean_guidance(tmp_path, monkeypatch):
    monkeypatch.setenv("DR_EVAL_CHECKPOINT", "best")
    index = {"last_checkpoint": {"name": "5_Step-3241.ckpt"}}
    with pytest.raises(drfc.EvaluationError) as exc:
        drfc.validate_checkpoint_selection(make_model_root(tmp_path, index))
    assert "체크포인트" in str(exc.value)


def test_index_file_missing_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("DR_EVAL_CHECKPOINT", "best")
    with pytest.raises(drfc.EvaluationError):
        drfc.validate_checkpoint_selection(make_model_root(tmp_path))


def test_broken_json_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("DR_EVAL_CHECKPOINT", "best")
    root = make_model_root(tmp_path, BOTH)
    (root / drfc.CHECKPOINT_INDEX_FILE).write_text("{깨진", encoding="utf-8")
    with pytest.raises(drfc.EvaluationError):
        drfc.validate_checkpoint_selection(root)


def test_last_mode_checks_last_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("DR_EVAL_CHECKPOINT", "last")
    index = {"best_checkpoint": {"name": "4_Step-2337.ckpt"}}
    with pytest.raises(drfc.EvaluationError):
        drfc.validate_checkpoint_selection(make_model_root(tmp_path, index))


def test_explicit_checkpoint_name_skips_validation(tmp_path, monkeypatch):
    """특정 체크포인트를 직접 지정한 운영에서는 무엇이 맞는지 알 수 없어 검사하지 않는다."""
    monkeypatch.setenv("DR_EVAL_CHECKPOINT", "7_Step-5908.ckpt")
    drfc.validate_checkpoint_selection(make_model_root(tmp_path))


def test_default_is_last_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("DR_EVAL_CHECKPOINT", raising=False)
    drfc.validate_checkpoint_selection(make_model_root(tmp_path, BOTH))
