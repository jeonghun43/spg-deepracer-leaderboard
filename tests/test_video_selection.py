"""평가 영상 카메라 앵글 선택 검증 (ux-improvements.md §2-5-1).

DRFC가 같은 평가를 여러 앵글로 저장하는데, 환경에 따라 특정 앵글이 껍데기(수백 바이트)로만
생성된다. 2026-07-26까지 그 깨진 앵글(camera-topview)을 고정으로 받아 리더보드 영상이
계속 비어 있었다. 정상 크기인 첫 후보를 고르는 규칙을 고정한다.
"""

import pytest
from botocore.exceptions import ClientError

from worker import drfc

BUCKET = "bucket"
PREFIX = "rl-deepracer-sagemaker"


@pytest.fixture(autouse=True)
def drfc_env(monkeypatch):
    monkeypatch.setenv("DR_LOCAL_S3_BUCKET", BUCKET)
    monkeypatch.setenv("DR_LOCAL_S3_MODEL_PREFIX", PREFIX)


class FakeS3:
    """head_object/download_file만 흉내 낸다. sizes에 없는 키는 존재하지 않는 것으로 본다."""

    def __init__(self, sizes):
        self.sizes = sizes
        self.downloaded = []

    def head_object(self, Bucket, Key):  # noqa: N803 - boto3 시그니처
        if Key not in self.sizes:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": self.sizes[Key]}

    def download_file(self, bucket, key, dest):
        self.downloaded.append(key)
        with open(dest, "wb") as out:
            out.write(b"x" * self.sizes[key])


def key(angle):
    return f"{PREFIX}/mp4/{angle}/0-video.mp4"


def test_broken_angle_is_skipped_for_a_valid_one(tmp_path):
    """실제로 겪은 상황 — topview만 261바이트, 나머지는 13.8MB."""
    s3 = FakeS3({
        key("camera-pip"): 13_800_000,
        key("camera-45degree"): 13_800_000,
        key("camera-topview"): 261,
    })
    used = drfc.download_video(s3, tmp_path / "out.mp4")

    assert used == key("camera-pip"), "우선순위가 가장 높은 정상 앵글을 써야 한다"
    assert (tmp_path / "out.mp4").stat().st_size == 13_800_000


def test_falls_back_to_next_angle_when_preferred_is_broken(tmp_path):
    s3 = FakeS3({
        key("camera-pip"): 300,
        key("camera-45degree"): 13_800_000,
        key("camera-topview"): 261,
    })
    assert drfc.download_video(s3, tmp_path / "out.mp4") == key("camera-45degree")


def test_missing_angles_are_skipped(tmp_path):
    s3 = FakeS3({key("camera-topview"): 13_800_000})
    assert drfc.download_video(s3, tmp_path / "out.mp4") == key("camera-topview")


def test_returns_none_when_every_angle_is_unusable(tmp_path):
    """영상이 없어도 평가 결과 자체는 저장돼야 하므로 예외를 던지지 않는다."""
    s3 = FakeS3({key("camera-pip"): 261, key("camera-topview"): 100})
    assert drfc.download_video(s3, tmp_path / "out.mp4") is None
    assert not (tmp_path / "out.mp4").exists()
