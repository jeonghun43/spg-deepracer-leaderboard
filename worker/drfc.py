"""DRFC(dr-start-evaluation) 연동 — 모델 주입, 평가 실행, 결과 조회/파싱.

이 모듈의 S3 레이아웃 가정은 실제 동작 중인 WSL2 DRFC 환경(~/deepracer-for-cloud)의
로컬 MinIO 버킷을 직접 조사해서 확인한 값이다 (2026-07-24):

  - 평가 결과 metrics json: s3://{bucket}/{DR_LOCAL_S3_MODEL_PREFIX}/metrics/evaluation/evaluation-*.json
    형태: {"metrics": [{"trial": 1, "completion_percentage": 100,
                        "elapsed_time_in_milliseconds": 36798, "off_track_count": 0, ...}, ...]}
    trial 1개 = 1바퀴. trial 개수는 run.env의 DR_EVAL_NUMBER_OF_TRIALS(=3, 온라인 3바퀴)로 결정됨.
  - 평가 영상: s3://{bucket}/{DR_LOCAL_S3_MODEL_PREFIX}/mp4/camera-topview/0-video.mp4
    주의: 이 경로는 평가마다 같은 키로 덮어써진다. 평가 직후 즉시 내려받아야 한다
    (지금 설계상 워커가 한 번에 한 건씩만 순차 처리하므로 안전하다).
  - 모델은 s3://{bucket}/{DR_LOCAL_S3_MODEL_PREFIX}/model/ 아래에 있어야 dr-start-evaluation이 읽는다
    (scripts/evaluation/start.sh 확인 결과 별도 모델 경로 인자가 없고 항상 이 prefix를 평가함).
  - DR_LOCAL_S3_ENDPOINT_URL이 localhost를 가리키면 WSL2 환경에서 타임아웃이 나서,
    실제 WSL IP(`ip route get`으로 확인)로 자동 대체한다 (사용자가 직접 확인한 우회 방법).
"""

import json
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

METRICS_KEY_SUBSTR = "valuation"

# DRFC는 같은 평가를 여러 카메라 앵글로 저장하는데, 환경에 따라 특정 앵글이 실패해
# 껍데기 파일만 남는다 (2026-07-26: camera-topview가 261바이트로만 생성되고 있었고,
# 그걸 고정으로 받는 바람에 리더보드 영상이 계속 비어 있었다). 우선순위대로 훑어
# 크기가 정상인 첫 번째 앵글을 쓴다.
VIDEO_KEY_SUFFIXES = (
    "mp4/camera-pip/0-video.mp4",
    "mp4/camera-45degree/0-video.mp4",
    "mp4/camera-topview/0-video.mp4",
)
# 정상 영상은 10MB를 넘고 깨진 것은 수백 바이트라, 그 사이 어디를 잘라도 판별된다.
MIN_VALID_VIDEO_BYTES = 10 * 1024


class EvaluationError(Exception):
    """모델 업로드/평가 실행/결과 파싱 등 워커 파이프라인에서 발생한 오류.

    Submission.status를 'error'로 전이시키는 신호로 쓰인다 (하루 제출 한도에서 제외됨).
    """


def _get_wsl_ip() -> str:
    result = subprocess.run(
        ["ip", "-4", "route", "get", "1.1.1.1"],
        capture_output=True,
        text=True,
        check=True,
    )
    tokens = result.stdout.split()
    for i, tok in enumerate(tokens):
        if tok == "src" and i + 1 < len(tokens):
            return tokens[i + 1]
    raise EvaluationError("WSL IP를 확인할 수 없습니다 ('ip route get 1.1.1.1' 출력 파싱 실패)")


def resolve_s3_endpoint() -> str:
    endpoint = os.environ.get("DR_LOCAL_S3_ENDPOINT_URL", "")
    if endpoint and "localhost" not in endpoint and "127.0.0.1" not in endpoint:
        return endpoint
    return f"http://{_get_wsl_ip()}:9000"


def get_s3_client():
    profile = os.environ.get("DR_LOCAL_S3_PROFILE", "minio")
    session = boto3.Session(profile_name=profile)
    return session.client(
        "s3",
        endpoint_url=resolve_s3_endpoint(),
        config=BotoConfig(connect_timeout=5, read_timeout=15, retries={"max_attempts": 3}),
    )


def get_bucket() -> str:
    return os.environ["DR_LOCAL_S3_BUCKET"]


def get_model_prefix() -> str:
    return os.environ["DR_LOCAL_S3_MODEL_PREFIX"]


def _extract_archive(archive_path: Path, dest_dir: Path) -> None:
    name = archive_path.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(dest_dir)
    elif name.endswith(".tar.gz") or name.endswith(".tgz"):
        with tarfile.open(archive_path, "r:gz") as tf:
            tf.extractall(dest_dir)
    else:
        raise EvaluationError(f"지원하지 않는 압축 형식: {archive_path.name}")


MINIO_RAW_FORMAT_HELP = (
    "MinIO의 내부 저장 폴더를 그대로 압축한 것으로 보입니다 "
    "(xl.meta / part.N 파일이 들어 있음). 이 형식은 평가에 사용할 수 없습니다.\n"
    "DRFC 환경에서 아래처럼 모델을 정상적으로 내보낸 뒤 그 폴더를 압축해 주세요:\n"
    "  aws s3 sync s3://$DR_LOCAL_S3_BUCKET/$DR_LOCAL_S3_MODEL_PREFIX/model/ ./my-model/ "
    "$DR_LOCAL_PROFILE_ENDPOINT_URL\n"
    "  tar -zcvf rl-deepracer-sagamer.tar.gz ./my-model"
)


def _looks_like_minio_raw_dump(extract_dir: Path) -> bool:
    """MinIO 데이터 디렉터리를 통째로 압축한 경우인지 판별한다.

    MinIO는 오브젝트를 '폴더 + xl.meta(+ part.N)' 형태로 디스크에 저장하기 때문에,
    참가자가 S3에서 정상적으로 내보내지 않고 MinIO 데이터 폴더를 그대로 압축하면
    파일 이름은 그럴듯해 보여도 실제 모델 파일이 하나도 없다.
    """
    return next(extract_dir.rglob("xl.meta"), None) is not None


def _find_model_root(extract_dir: Path) -> Path:
    """압축 안에서 model_metadata.json이 있는 실제 모델 폴더를 찾는다.

    참가자가 model/ 폴더 자체를 압축했을 수도, 한 단계 위(상위 폴더 포함)에서
    압축했을 수도 있어 재귀적으로 찾는다. 여러 개면 가장 얕은 것을 쓴다.
    """
    if (extract_dir / "model_metadata.json").is_file():
        return extract_dir

    candidates = [p for p in extract_dir.rglob("model_metadata.json") if p.is_file()]
    if not candidates:
        if _looks_like_minio_raw_dump(extract_dir):
            raise EvaluationError(MINIO_RAW_FORMAT_HELP)
        raise EvaluationError(
            "압축 파일에서 model_metadata.json을 찾을 수 없습니다. "
            "DRFC 학습 결과의 model/ 폴더 내용을 압축했는지 확인해 주세요."
        )

    return min(candidates, key=lambda p: len(p.relative_to(extract_dir).parts)).parent


CHECKPOINT_INDEX_FILE = "deepracer_checkpoints.json"
# DR_EVAL_CHECKPOINT가 이 값이면 아카이브 안에 해당 항목이 있어야 평가할 수 있다.
# 특정 체크포인트 이름을 직접 지정한 경우는 검사하지 않는다(무엇이 맞는지 알 수 없다).
_CHECKPOINT_KINDS = {"best": "best_checkpoint", "last": "last_checkpoint"}

CHECKPOINT_MISSING_HELP = (
    "압축 파일에서 평가에 쓸 체크포인트 정보를 찾을 수 없습니다.\n"
    "이 대회는 학습 중 성적이 가장 좋았던 체크포인트(best)로 평가합니다. "
    "DRFC 학습 결과의 model/ 폴더를 통째로 내보내면 그 정보가 함께 들어갑니다:\n"
    "  aws s3 sync s3://$DR_LOCAL_S3_BUCKET/$DR_LOCAL_S3_MODEL_PREFIX/model/ ./my-model/ "
    "$DR_LOCAL_PROFILE_ENDPOINT_URL\n"
    "  tar -zcvf my-model.tar.gz ./my-model"
)


def validate_checkpoint_selection(model_root: Path) -> None:
    """평가에 쓸 체크포인트가 아카이브 안에 실제로 있는지 미리 확인한다.

    `DR_EVAL_CHECKPOINT=best`로 운영하는데 아카이브에 best 정보가 없으면, 시뮬레이터가
    한참 뒤에 알 수 없는 오류로 죽어 참가자는 원인을 모른다. 여기서 먼저 걸러 한국어로 알린다.
    **모델을 S3에 주입하기 전에 호출해야 한다** — 그래야 잘못된 제출이 이전 모델을 지우지 않는다.
    """
    kind = os.environ.get("DR_EVAL_CHECKPOINT", "last").strip().lower()
    key = _CHECKPOINT_KINDS.get(kind)
    if key is None:
        return  # 특정 체크포인트를 직접 지정한 운영 — 검사 대상이 아니다

    index_path = model_root / CHECKPOINT_INDEX_FILE
    if not index_path.is_file():
        raise EvaluationError(CHECKPOINT_MISSING_HELP)

    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"{CHECKPOINT_INDEX_FILE}을 읽을 수 없습니다: {exc}") from exc

    entry = index.get(key) or {}
    if not entry.get("name"):
        raise EvaluationError(CHECKPOINT_MISSING_HELP)


def inject_model(s3_client, archive_path: Path, work_dir: Path) -> None:
    """참가자가 올린 모델 아카이브(자신의 DRFC model/ 폴더를 압축한 것)를
    dr-start-evaluation이 읽는 S3 경로(model/)에 올린다. 기존 내용은 지운다.
    """
    bucket = get_bucket()
    prefix = get_model_prefix()

    extract_dir = work_dir / "extracted"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        _extract_archive(archive_path, extract_dir)
    except (zipfile.BadZipFile, tarfile.TarError) as exc:
        raise EvaluationError(f"모델 압축 파일을 열 수 없습니다: {exc}") from exc

    extract_dir = _find_model_root(extract_dir)
    # 아래에서 기존 모델을 지우기 전에 검사한다 — 잘못된 제출이 이전 모델을 날리면 안 된다.
    validate_checkpoint_selection(extract_dir)

    model_key_prefix = f"{prefix}/model/"
    existing = s3_client.list_objects_v2(Bucket=bucket, Prefix=model_key_prefix)
    for obj in existing.get("Contents", []):
        s3_client.delete_object(Bucket=bucket, Key=obj["Key"])

    uploaded = 0
    for file_path in extract_dir.rglob("*"):
        if file_path.is_file():
            rel_key = model_key_prefix + str(file_path.relative_to(extract_dir)).replace("\\", "/")
            s3_client.upload_file(str(file_path), bucket, rel_key)
            uploaded += 1
    if uploaded == 0:
        raise EvaluationError("압축 파일 안에 업로드할 모델 파일이 없습니다.")


def find_latest_metrics_key(s3_client, after=None) -> str | None:
    bucket = get_bucket()
    prefix = get_model_prefix()
    paginator = s3_client.get_paginator("list_objects_v2")
    candidates = []
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/metrics/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if METRICS_KEY_SUBSTR not in key or not key.endswith(".json"):
                continue
            if after is not None and obj["LastModified"] <= after:
                continue
            candidates.append((obj["LastModified"], key))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


def download_json(s3_client, key: str) -> dict:
    obj = s3_client.get_object(Bucket=get_bucket(), Key=key)
    return json.loads(obj["Body"].read())


def download_video(s3_client, dest_path: Path) -> str | None:
    """평가 영상을 내려받고 사용한 S3 키를 돌려준다. 쓸 만한 앵글이 없으면 None.

    영상이 없어도 순위·기록에는 영향이 없으므로(리더보드가 "—"로 표시) 예외를 던지지 않는다.
    """
    bucket = get_bucket()
    prefix = get_model_prefix()

    for suffix in VIDEO_KEY_SUFFIXES:
        key = f"{prefix}/{suffix}"
        try:
            head = s3_client.head_object(Bucket=bucket, Key=key)
        except ClientError:
            continue
        if head.get("ContentLength", 0) < MIN_VALID_VIDEO_BYTES:
            continue  # 생성에 실패한 앵글 — 다음 후보로
        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            s3_client.download_file(bucket, key, str(dest_path))
        except ClientError:
            continue
        return key
    return None


# SIM_TRACE_LOG의 필드 위치(0-based). DRFC 시뮬레이터가 매 스텝 찍는 로그로,
# metrics json이 비어 있어도 여기에는 진행률과 종료 사유가 남는다.
#   SIM_TRACE_LOG:episode,step,x,y,yaw,steer,throttle,action,reward,done,
#                 all_wheels_on_track,progress,closest_waypoint,track_len,tstamp,episode_status,...
SIM_TRACE_PROGRESS_INDEX = 11
SIM_TRACE_STATUS_INDEX = 15
# 주행이 시작되기 전 상태들. 실패 사유로 보여줄 값이 아니다.
_NON_TERMINAL_STATUSES = {"in_progress", "prepare", "pause"}


def extract_progress_from_log(log_path: Path) -> tuple[float | None, str | None]:
    """평가 로그에서 최고 진행률과 종료 사유를 뽑는다.

    metrics json이 비어 있는 경우(차가 한 바퀴도 못 끝냈을 때)에도 참가자에게 "어디까지
    갔고 왜 멈췄는지"를 알려주기 위한 보조 경로다 (2026-07-30 submission 18에서 필요성 확인).
    """
    if not log_path.is_file():
        return None, None

    best_progress: float | None = None
    last_status: str | None = None
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                marker = line.find("SIM_TRACE_LOG:")
                if marker == -1:
                    continue
                fields = line[marker + len("SIM_TRACE_LOG:") :].strip().split(",")
                if len(fields) <= SIM_TRACE_STATUS_INDEX:
                    continue
                try:
                    progress = float(fields[SIM_TRACE_PROGRESS_INDEX])
                except ValueError:
                    continue
                if best_progress is None or progress > best_progress:
                    best_progress = progress
                status = fields[SIM_TRACE_STATUS_INDEX].strip().lower().replace(" ", "_")
                if status and status not in _NON_TERMINAL_STATUSES:
                    last_status = status
    except OSError:
        return None, None

    return best_progress, last_status


def summarize_progress(
    metrics: dict, log_path: Path | None = None
) -> tuple[float | None, str | None]:
    """참가자에게 보여줄 '최고 진행률'과 '종료 사유'를 정한다.

    metrics의 trial 기록을 우선 쓰고, 비어 있으면 평가 로그로 넘어간다.
    """
    trials = metrics.get("metrics", [])
    percentages = [
        t["completion_percentage"] for t in trials if t.get("completion_percentage") is not None
    ]
    if percentages:
        best = max(percentages)
        statuses = [
            str(t.get("episode_status", "")).strip().lower().replace(" ", "_") for t in trials
        ]
        terminal = [s for s in statuses if s and s not in _NON_TERMINAL_STATUSES]
        return float(best), (terminal[-1] if terminal else None)

    if log_path is not None:
        return extract_progress_from_log(log_path)
    return None, None


def parse_evaluation_result(metrics: dict, required_laps: int) -> tuple[str, float | None, int]:
    """실제 evaluation-*.json 구조를 완주 여부/랩타임/이탈 횟수로 변환한다.

    required_laps개의 trial이 모두 completion_percentage==100이어야 '완주'다.
    랩타임은 그 required_laps개 trial의 elapsed_time_in_milliseconds 합(초)이다.
    """
    trials = sorted(metrics.get("metrics", []), key=lambda t: t.get("trial", 0))
    off_track_total = sum(t.get("off_track_count", 0) for t in trials)
    completed = [t for t in trials if t.get("completion_percentage") == 100]

    if len(completed) >= required_laps:
        total_ms = sum(t["elapsed_time_in_milliseconds"] for t in completed[:required_laps])
        return "finished", total_ms / 1000.0, off_track_total
    return "timeout", None, off_track_total


def run_evaluation_blocking(
    script_path: Path,
    drfc_dir: str,
    env_file: str,
    max_wait_seconds: int,
    log_path: Path | None = None,
) -> None:
    """run_evaluation.sh를 실행하고 완료(또는 타임아웃)될 때까지 블로킹한다.

    log_path를 주면 스택을 내리기 전에 시뮬레이션 로그를 그 경로에 받아둔다
    (DRFC는 로그를 디스크에 남기지 않아, 스택을 지우면 사라지기 때문).
    """
    env = os.environ.copy()
    env["DRFC_DIR"] = drfc_dir
    env["DR_ENV_FILE"] = env_file
    env["MAX_WAIT_SECONDS"] = str(max_wait_seconds)
    if log_path is not None:
        env["EVAL_LOG_PATH"] = str(log_path)
    try:
        result = subprocess.run(
            ["bash", str(script_path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=max_wait_seconds + 120,
        )
    except subprocess.TimeoutExpired as exc:
        raise EvaluationError(f"run_evaluation.sh가 응답 없이 멈췄습니다: {exc}") from exc

    if result.returncode != 0:
        tail_out = result.stdout[-2000:]
        tail_err = result.stderr[-2000:]
        raise EvaluationError(
            f"dr-start-evaluation 실행 실패 (exit={result.returncode})\nSTDOUT: {tail_out}\nSTDERR: {tail_err}"
        )
