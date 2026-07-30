"""웹 서버와 모델·영상을 주고받는다 (cloud-migration.md §4).

두 배포 형태를 하나의 스위치로 지원한다.

- `WORKER_TOKEN`이 비어 있음 → **local 모드**: 웹과 워커가 같은 디스크를 공유하는 지금 구성.
  모델을 `storage/`에서 바로 읽고 영상도 직접 쓴다.
- `WORKER_TOKEN`이 설정됨 → **http 모드**: 웹이 클라우드에 있는 구성. 모델을 내려받아
  임시 디렉터리에서 평가하고, 영상은 업로드한다.

이렇게 두면 이관 전후로 워커 코드를 바꾸지 않아도 되고, 이관 전에 한 대에서 http 모드를
그대로 시험해볼 수 있다.
"""

import logging
import shutil
from pathlib import Path

import httpx

from app.config import settings
from app.storage_paths import resolve_storage_path

logger = logging.getLogger("worker.transfer")

# 모델이 수백 MB라 넉넉히 잡는다. 연결 자체가 죽은 경우는 connect 타임아웃이 먼저 걸린다.
TRANSFER_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=600.0, pool=10.0)


class TransferError(Exception):
    """웹 서버와의 파일 송수신 실패. 제출을 대기열로 되돌려 다시 시도하게 만드는 신호다."""


def uses_http() -> bool:
    return bool(settings.worker_token)


def _headers() -> dict[str, str]:
    return {"X-Worker-Token": settings.worker_token}


def fetch_model(submission_id: int, stored_path: str, work_dir: Path) -> Path:
    """평가할 모델 아카이브의 로컬 경로를 돌려준다.

    local 모드에서는 원본 경로를 그대로 쓰고(복사하지 않는다), http 모드에서는 work_dir에
    내려받는다. 내려받은 파일은 호출자가 work_dir을 지울 때 함께 사라진다.
    """
    if not uses_http():
        path = resolve_storage_path(stored_path)
        if not path.is_file():
            raise TransferError(
                f"업로드된 모델 파일을 찾을 수 없습니다 (기록: {stored_path}, 확인: {path})"
            )
        return path

    url = f"{settings.web_base_url.rstrip('/')}/internal/submissions/{submission_id}/model"
    dest = work_dir / f"{submission_id}.tar.gz"
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        with httpx.stream("GET", url, headers=_headers(), timeout=TRANSFER_TIMEOUT) as response:
            if response.status_code != 200:
                raise TransferError(
                    f"모델 다운로드 실패 (HTTP {response.status_code}) — 토큰 설정과 제출 상태를 확인하세요"
                )
            with open(dest, "wb") as out:
                for chunk in response.iter_bytes(1024 * 1024):
                    out.write(chunk)
    except httpx.HTTPError as exc:
        raise TransferError(f"웹 서버에 연결하지 못했습니다: {exc}") from exc

    if dest.stat().st_size == 0:
        raise TransferError("모델 파일을 0바이트로 받았습니다.")
    logger.info("모델 수신: submission=%s bytes=%s", submission_id, dest.stat().st_size)
    return dest


def deliver_video(submission_id: int, local_video: Path, video_rel_path: str) -> str | None:
    """평가 영상을 최종 위치로 보낸다. 성공하면 저장된 상대 경로를 돌려준다.

    영상은 순위에 영향이 없는 부가 정보라, 실패해도 예외를 올리지 않고 None을 돌려준다
    (리더보드는 영상이 없으면 "—"로 표시한다).
    """
    if not local_video.is_file():
        return None

    if not uses_http():
        dest = settings.videos_dir / video_rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if local_video.resolve() != dest.resolve():
            shutil.copy2(local_video, dest)
        return video_rel_path

    url = f"{settings.web_base_url.rstrip('/')}/internal/submissions/{submission_id}/video"
    try:
        with open(local_video, "rb") as f:
            response = httpx.post(
                url,
                headers=_headers(),
                files={"video": (local_video.name, f, "video/mp4")},
                timeout=TRANSFER_TIMEOUT,
            )
    except httpx.HTTPError as exc:
        logger.warning("영상 업로드 실패(무시하고 진행): submission=%s %s", submission_id, exc)
        return None

    if response.status_code != 200:
        logger.warning(
            "영상 업로드 거부(무시하고 진행): submission=%s HTTP %s",
            submission_id,
            response.status_code,
        )
        return None
    return response.json().get("video_path", video_rel_path)


def deliver_metrics(submission_id: int, local_metrics: Path) -> bool:
    """원본 metrics json을 최종 위치로 보낸다.

    MinIO의 원본은 다음 평가에 덮어써지므로 이 사본이 유일한 기록이다. 워커에만 두면
    백업(서버 기준)에서 빠지므로 http 모드에서는 서버로 올린다.
    """
    if not local_metrics.is_file():
        return False
    if not uses_http():
        return True  # 이미 서버와 같은 디스크에 쓰여 있다

    url = f"{settings.web_base_url.rstrip('/')}/internal/submissions/{submission_id}/metrics"
    try:
        with open(local_metrics, "rb") as f:
            response = httpx.post(
                url,
                headers=_headers(),
                files={"metrics": (local_metrics.name, f, "application/json")},
                timeout=TRANSFER_TIMEOUT,
            )
    except httpx.HTTPError as exc:
        logger.warning("metrics 업로드 실패(무시하고 진행): submission=%s %s", submission_id, exc)
        return False
    if response.status_code != 200:
        logger.warning(
            "metrics 업로드 거부(무시하고 진행): submission=%s HTTP %s",
            submission_id,
            response.status_code,
        )
        return False
    return True


def request_prune(submission_id: int) -> int:
    """서버에 보존 정책 적용을 요청하고 삭제된 파일 수를 돌려준다.

    파일이 서버에 있으므로 워커가 직접 지울 수 없다. 실패해도 평가 결과에는 영향이 없으므로
    예외를 올리지 않는다 — 다음 평가에서 다시 정리된다.
    """
    url = f"{settings.web_base_url.rstrip('/')}/internal/submissions/{submission_id}/prune"
    try:
        response = httpx.post(url, headers=_headers(), timeout=TRANSFER_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.warning("보존 정책 요청 실패: submission=%s %s", submission_id, exc)
        return 0
    if response.status_code != 200:
        logger.warning(
            "보존 정책 요청 거부: submission=%s HTTP %s", submission_id, response.status_code
        )
        return 0
    removed = response.json().get("removed", 0)
    if removed:
        logger.info("서버 보존 정책 적용: submission=%s 파일 %s개 삭제", submission_id, removed)
    return removed
