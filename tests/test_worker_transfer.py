"""워커 ↔ 웹 파일 전송 모드 검증 (cloud-migration.md §4).

`WORKER_TOKEN` 하나로 두 배포 형태를 가른다.
- 비어 있음: 웹과 워커가 같은 디스크를 공유하는 현행 구성(local 모드)
- 설정됨: 웹이 다른 기기에 있는 구성(http 모드)

이관 전후로 워커 코드를 바꾸지 않아도 되는지가 핵심이라, 두 모드를 모두 고정한다.
"""

import pytest

from app.config import settings
from worker import transfer


@pytest.fixture
def storage(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    (root / "models" / "1" / "1").mkdir(parents=True)
    (root / "videos").mkdir(parents=True)
    monkeypatch.setattr(settings, "storage_dir", root)
    monkeypatch.setattr(settings, "worker_token", "")  # 기본은 local 모드
    return root


def make_model(storage, rel="models/1/1/7.tar.gz"):
    path = storage.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"model-archive")
    return path


# ── local 모드 (현행 배포) ────────────────────────────────────────────────


def test_local_mode_reads_model_in_place(storage, tmp_path):
    """같은 디스크를 쓰면 수백 MB를 복사하지 않고 원본 경로를 그대로 넘긴다."""
    original = make_model(storage)
    path = transfer.fetch_model(7, "models/1/1/7.tar.gz", tmp_path / "work")
    assert path == original
    assert path.read_bytes() == b"model-archive"


def test_local_mode_missing_model_raises_transfer_error(storage, tmp_path):
    with pytest.raises(transfer.TransferError):
        transfer.fetch_model(7, "models/1/1/없는파일.tar.gz", tmp_path / "work")


def test_local_mode_copies_video_into_storage(storage, tmp_path):
    local_video = tmp_path / "evaluation.mp4"
    local_video.write_bytes(b"video-bytes")

    stored = transfer.deliver_video(7, local_video, "1/1/7.mp4")

    assert stored == "1/1/7.mp4"
    assert (storage / "videos" / "1" / "1" / "7.mp4").read_bytes() == b"video-bytes"


def test_missing_video_is_reported_as_none(storage, tmp_path):
    assert transfer.deliver_video(7, tmp_path / "없는영상.mp4", "1/1/7.mp4") is None


# ── http 모드 (이관 후) ───────────────────────────────────────────────────


@pytest.fixture
def http_mode(storage, monkeypatch):
    monkeypatch.setattr(settings, "worker_token", "secret-token")
    monkeypatch.setattr(settings, "web_base_url", "https://example.test")
    return storage


class FakeStreamResponse:
    def __init__(self, status_code, chunks=(b"downloaded",)):
        self.status_code = status_code
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_bytes(self, _size):
        yield from self._chunks


def test_http_mode_downloads_model_with_token(http_mode, tmp_path, monkeypatch):
    captured = {}

    def fake_stream(method, url, headers=None, timeout=None):
        captured.update(method=method, url=url, headers=headers)
        return FakeStreamResponse(200)

    monkeypatch.setattr(transfer.httpx, "stream", fake_stream)

    path = transfer.fetch_model(7, "models/1/1/7.tar.gz", tmp_path / "work")

    assert path.read_bytes() == b"downloaded"
    assert captured["url"] == "https://example.test/internal/submissions/7/model"
    assert captured["headers"]["X-Worker-Token"] == "secret-token"


def test_http_mode_rejects_error_response(http_mode, tmp_path, monkeypatch):
    """토큰이 틀리면 서버가 404를 준다 — 평가를 진행하면 안 된다."""
    monkeypatch.setattr(
        transfer.httpx, "stream", lambda *a, **kw: FakeStreamResponse(404, chunks=())
    )
    with pytest.raises(transfer.TransferError):
        transfer.fetch_model(7, "models/1/1/7.tar.gz", tmp_path / "work")


def test_http_mode_connection_failure_raises_transfer_error(http_mode, tmp_path, monkeypatch):
    """웹 서버가 내려가 있으면 '오류'가 아니라 대기열 복귀 신호여야 한다."""

    def boom(*args, **kwargs):
        raise transfer.httpx.ConnectError("connection refused")

    monkeypatch.setattr(transfer.httpx, "stream", boom)
    with pytest.raises(transfer.TransferError):
        transfer.fetch_model(7, "models/1/1/7.tar.gz", tmp_path / "work")


def test_http_mode_uploads_video(http_mode, tmp_path, monkeypatch):
    local_video = tmp_path / "evaluation.mp4"
    local_video.write_bytes(b"video-bytes")
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"video_path": "1/1/7.mp4", "bytes": 11}

    def fake_post(url, headers=None, files=None, timeout=None):
        captured.update(url=url, headers=headers)
        return FakeResponse()

    monkeypatch.setattr(transfer.httpx, "post", fake_post)

    assert transfer.deliver_video(7, local_video, "1/1/7.mp4") == "1/1/7.mp4"
    assert captured["url"] == "https://example.test/internal/submissions/7/video"
    assert captured["headers"]["X-Worker-Token"] == "secret-token"


def test_video_upload_failure_does_not_break_evaluation(http_mode, tmp_path, monkeypatch):
    """영상은 순위에 영향이 없으므로 실패해도 예외를 올리지 않는다."""
    local_video = tmp_path / "evaluation.mp4"
    local_video.write_bytes(b"video-bytes")

    def boom(*args, **kwargs):
        raise transfer.httpx.ConnectError("connection refused")

    monkeypatch.setattr(transfer.httpx, "post", boom)
    assert transfer.deliver_video(7, local_video, "1/1/7.mp4") is None


# ── 보존 정책·metrics 위임 (클라우드 분리 배포) ───────────────────────────


def test_prune_is_delegated_to_server_in_http_mode(http_mode, monkeypatch):
    """파일이 서버에 있으므로 정리도 서버가 해야 한다. 워커가 자기 디스크를 지워봐야
    서버의 250MB 모델은 그대로 쌓인다 (2026-07-30 실제 발생한 문제)."""
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"removed": 2}

    def fake_post(url, headers=None, timeout=None):
        captured.update(url=url, headers=headers)
        return FakeResponse()

    monkeypatch.setattr(transfer.httpx, "post", fake_post)

    assert transfer.request_prune(18) == 2
    assert captured["url"].endswith("/internal/submissions/18/prune")
    assert captured["headers"]["X-Worker-Token"] == "secret-token"


def test_prune_failure_is_not_fatal(http_mode, monkeypatch):
    """정리에 실패해도 평가 결과는 이미 저장돼 있다. 다음 평가에서 다시 정리된다."""

    def boom(*args, **kwargs):
        raise transfer.httpx.ConnectError("connection refused")

    monkeypatch.setattr(transfer.httpx, "post", boom)
    assert transfer.request_prune(18) == 0


def test_metrics_uploaded_in_http_mode(http_mode, tmp_path, monkeypatch):
    metrics_file = tmp_path / "18.json"
    metrics_file.write_text('{"metrics": []}', encoding="utf-8")
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"metrics_path": "1/1/18.json", "bytes": 15}

    def fake_post(url, headers=None, files=None, timeout=None):
        captured.update(url=url)
        return FakeResponse()

    monkeypatch.setattr(transfer.httpx, "post", fake_post)

    assert transfer.deliver_metrics(18, metrics_file) is True
    assert captured["url"].endswith("/internal/submissions/18/metrics")


def test_metrics_upload_skipped_in_local_mode(storage, tmp_path):
    """같은 디스크면 이미 제자리에 쓰여 있으므로 전송할 필요가 없다."""
    metrics_file = tmp_path / "18.json"
    metrics_file.write_text('{"metrics": []}', encoding="utf-8")
    assert transfer.deliver_metrics(18, metrics_file) is True
