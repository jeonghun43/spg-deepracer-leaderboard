"""평가 워커 — 대기열을 폴링하며 dr-start-evaluation을 순차 실행한다.

실행: (WSL Ubuntu, DRFC가 설치된 호스트에서) `python -m worker.run`
DB(PostgreSQL)는 docker-compose의 db 서비스를 localhost:5432로 접속하고,
DRFC 자체(run.env/system.env로 설정된 DR_* 값)는 이 프로세스가 실행되는 쉘 환경을
그대로 물려받는다 (plan.md §1: DR 관련 환경변수는 앱이 따로 관리하지 않는다).
"""

import datetime as dt
import json
import logging
import os
import shutil
import socket
import threading
import time
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import EvaluationResult, FinishStatus, Submission, SubmissionStatus
from app.retention import prune_team_files
from app.worker_status import touch_heartbeat
from worker import drfc, transfer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("worker")

WORKER_ID = socket.gethostname()
POLL_INTERVAL_SECONDS = 5
# 웹 서버가 내려가 있을 때 같은 제출을 초당 몇 번씩 다시 집지 않도록 잠시 쉰다.
TRANSFER_RETRY_SLEEP_SECONDS = 30
# 화면의 "중지" 판정 기준(기본 3분)보다 충분히 짧아야 평가 중에도 살아있다고 인식된다.
HEARTBEAT_INTERVAL_SECONDS = 30
DRFC_DIR = os.environ.get("DRFC_DIR", str(Path.home() / "deepracer-for-cloud"))
DR_ENV_FILE = os.environ.get("DR_ENV_FILE", "run.env")
MAX_WAIT_SECONDS = int(os.environ.get("EVAL_MAX_WAIT_SECONDS", "1800"))
RUN_EVAL_SCRIPT = Path(__file__).parent / "run_evaluation.sh"

CLAIM_NEXT_SQL = text(
    """
    UPDATE submissions
    SET status = 'running', worker_id = :worker_id, started_at = now()
    WHERE id = (
        SELECT id FROM submissions
        WHERE status = 'queued'
        ORDER BY submitted_at ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING id
    """
)


def now_utc() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def log_path_for(submission_id: int) -> Path:
    """제출별 시뮬레이션 로그 저장 경로 (실패 원인 추적용)."""
    settings.eval_logs_dir.mkdir(parents=True, exist_ok=True)
    return settings.eval_logs_dir / f"{submission_id}.log"


def claim_next_submission(db: Session) -> int | None:
    """대기 중인 제출 하나를 원자적으로 확보한다.

    워커는 지금 1대만 운영하기로 했지만(2026-07-24 논의), FOR UPDATE SKIP LOCKED로
    작성해두는 비용이 거의 없어 처음부터 여러 워커가 동시에 폴링해도 안전하게 작성한다
    (multi-laptop-worker-pool.md에서 실제로 여러 대를 쓰기로 하면 그대로 재사용 가능).
    """
    row = db.execute(CLAIM_NEXT_SQL, {"worker_id": WORKER_ID}).first()
    db.commit()
    return row[0] if row else None


def recover_stale_running(db: Session) -> None:
    """워커가 중간에 죽어 '평가중'에 멈춰있는 제출을 재시작 시 다시 대기열로 되돌린다."""
    threshold = now_utc() - dt.timedelta(seconds=MAX_WAIT_SECONDS + 300)
    stale = db.query(Submission).filter(
        Submission.status == SubmissionStatus.RUNNING,
        Submission.started_at < threshold,
    ).all()
    for submission in stale:
        logger.warning("오래된 '평가중' 제출을 대기열로 되돌립니다: submission=%s", submission.id)
        submission.status = SubmissionStatus.QUEUED
        submission.worker_id = None
        submission.started_at = None
    if stale:
        db.commit()


def prune_finished_team_files(db: Session, submission_id: int) -> None:
    """평가가 끝난 팀의 파일을 보존 정책대로 정리한다 (최고기록만 남긴다).

    시즌 종료까지 기다리면 디스크가 먼저 찬다 (모델 1건 약 250MB).

    **파일이 있는 쪽에서 지워야 한다.** 웹이 다른 기기에 있으면(http 모드) 이 워커의 디스크에는
    지울 파일이 없다 — 서버에 정리를 요청해야 한다. 로컬 모드에서만 직접 지운다.
    정리에 실패하더라도 평가 결과는 이미 저장됐으므로 예외를 삼키고 경고만 남긴다.
    """
    try:
        if transfer.uses_http():
            transfer.request_prune(submission_id)
            return
        submission = db.get(Submission, submission_id)
        if submission is None:
            return
        if prune_team_files(submission.team):
            db.commit()
    except Exception:  # noqa: BLE001 - 정리 실패가 평가 결과를 되돌리면 안 된다
        db.rollback()
        logger.exception("보존 정책 적용 실패: submission=%s", submission_id)


def start_heartbeat_thread() -> threading.Thread:
    """생존 신호를 별도 스레드에서 주기적으로 남긴다.

    폴링 루프에서만 갱신하면, 평가 한 건에 붙잡혀 있는 10~30분 동안 신호가 끊긴다.
    그러면 워커가 가장 열심히 일하는 중에 참가자 화면에 "평가 서버 중지"가 뜬다
    (2026-07-30 실제 발생). 데몬 스레드라 워커가 죽으면 함께 죽으므로, 진짜 중단은
    그대로 감지된다.
    """

    def loop() -> None:
        while True:
            db = SessionLocal()
            try:
                touch_heartbeat(db, WORKER_ID)
            except Exception:  # noqa: BLE001 - 하트비트 실패로 워커가 죽으면 안 된다
                db.rollback()
                logger.warning("하트비트 갱신 실패", exc_info=True)
            finally:
                db.close()
            time.sleep(HEARTBEAT_INTERVAL_SECONDS)

    thread = threading.Thread(target=loop, daemon=True, name="heartbeat")
    thread.start()
    return thread


def process_submission(submission_id: int) -> None:
    db = SessionLocal()
    try:
        submission = db.get(Submission, submission_id)
        if submission is None:
            return
        logger.info("평가 시작: submission=%s team=%s", submission.id, submission.team_id)

        work_dir = settings.storage_dir / "work" / str(submission.id)
        work_dir.mkdir(parents=True, exist_ok=True)
        season_id = submission.team.season_id
        team_id = submission.team_id

        try:
            s3 = drfc.get_s3_client()

            start_marker = None
            existing_key = drfc.find_latest_metrics_key(s3)
            if existing_key:
                head = s3.head_object(Bucket=drfc.get_bucket(), Key=existing_key)
                start_marker = head["LastModified"]

            # 웹과 같은 디스크를 쓰면 원본 경로를 그대로, 다른 기기에 있으면 HTTP로 받아온다
            # (worker/transfer.py). 받지 못하면 TransferError가 올라가 대기열로 되돌린다.
            model_file = transfer.fetch_model(submission.id, submission.model_path, work_dir)

            drfc.inject_model(s3, model_file, work_dir)
            drfc.run_evaluation_blocking(
                RUN_EVAL_SCRIPT, DRFC_DIR, DR_ENV_FILE, MAX_WAIT_SECONDS,
                log_path=log_path_for(submission.id),
            )

            metrics_key = drfc.find_latest_metrics_key(s3, after=start_marker)
            if metrics_key is None:
                raise drfc.EvaluationError("평가가 끝났지만 새 metrics 파일을 찾지 못했습니다.")
            metrics = drfc.download_json(s3, metrics_key)

            # 원본 metrics json을 파일로도 남긴다 — MinIO 버킷이 정리되거나 덮어써져도
            # 나중에 결과를 다시 확인/재파싱할 수 있어야 하기 때문 (plan.md §5.1a).
            metrics_file = settings.metrics_dir / f"{season_id}/{team_id}/{submission.id}.json"
            metrics_file.parent.mkdir(parents=True, exist_ok=True)
            metrics_file.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
            # 웹이 다른 기기에 있으면 이 사본이 백업 대상 밖에 남는다. 서버로도 올린다.
            transfer.deliver_metrics(submission.id, metrics_file)

            finish_status, lap_time, off_track = drfc.parse_evaluation_result(
                metrics, settings.online_eval_laps
            )
            # metrics가 비어 있으면(한 바퀴도 못 끝낸 경우) 평가 로그에서 뽑는다.
            best_progress, failure_reason = drfc.summarize_progress(
                metrics, log_path_for(submission.id)
            )

            # 영상 키는 다음 평가 때 덮어써지므로 결과 저장 전에 먼저 내려받아 둔다.
            local_video = work_dir / "evaluation.mp4"
            video_key = drfc.download_video(s3, local_video)
            if video_key is None:
                logger.warning("쓸 만한 평가 영상을 찾지 못했습니다: submission=%s", submission.id)
            else:
                logger.info("평가 영상 수집: submission=%s key=%s", submission.id, video_key)

            # 순위를 좌우하는 결과를 먼저 확정한다. 영상은 부가 정보라 그 다음이다
            # (전송 대상 제출이 DB에 있어야 웹이 영상을 받아줄 수 있기도 하다).
            result = EvaluationResult(
                submission_id=submission.id,
                finish_status=FinishStatus.FINISHED if finish_status == "finished" else FinishStatus.TIMEOUT,
                lap_time_seconds=lap_time,
                off_track_count=off_track,
                best_progress_percent=best_progress,
                failure_reason=failure_reason,
                video_path=None,
                metrics_raw_path=metrics_key,
            )
            db.add(result)
            submission.status = SubmissionStatus.DONE
            submission.finished_at = now_utc()
            db.commit()
            logger.info(
                "평가 완료: submission=%s finish_status=%s lap_time=%s off_track=%s",
                submission.id, finish_status, lap_time, off_track,
            )

            if video_key is not None:
                video_rel_path = f"{season_id}/{team_id}/{submission.id}.mp4"
                stored_video = transfer.deliver_video(submission.id, local_video, video_rel_path)
                if stored_video:
                    result.video_path = stored_video
                    db.commit()
        except transfer.TransferError as exc:
            # 웹 서버에 연결하지 못한 것은 참가자 잘못이 아니다. '오류'로 끝내면 결과 없이
            # 제출이 소진되므로, 대기열로 되돌려 서버가 돌아왔을 때 다시 처리되게 한다.
            db.rollback()
            submission = db.get(Submission, submission_id)
            submission.status = SubmissionStatus.QUEUED
            submission.worker_id = None
            submission.started_at = None
            db.commit()
            logger.warning("파일 전송 실패 — 대기열로 되돌립니다: submission=%s %s", submission_id, exc)
            # 곧바로 같은 건을 다시 집어 로그만 쌓는 것을 막는다.
            time.sleep(TRANSFER_RETRY_SLEEP_SECONDS)
        except drfc.EvaluationError as exc:
            db.rollback()
            submission = db.get(Submission, submission_id)
            submission.status = SubmissionStatus.ERROR
            submission.error_message = str(exc)[:2000]
            submission.finished_at = now_utc()
            db.commit()
            logger.error("평가 오류: submission=%s error=%s", submission_id, exc)
        except Exception as exc:  # noqa: BLE001 - 예상 못한 오류도 '오류' 상태로 남겨 재제출 가능하게 함
            db.rollback()
            submission = db.get(Submission, submission_id)
            submission.status = SubmissionStatus.ERROR
            submission.error_message = f"예상치 못한 오류: {exc}"[:2000]
            submission.finished_at = now_utc()
            db.commit()
            logger.exception("예상치 못한 오류: submission=%s", submission_id)
        finally:
            # 압축을 푼 모델은 건당 수백 MB라 그대로 두면 디스크가 금방 찬다.
            # 원본 아카이브(storage/models/)는 보존 정책에 따라 아래에서 따로 정리한다.
            shutil.rmtree(work_dir, ignore_errors=True)
            prune_finished_team_files(db, submission_id)
    finally:
        db.close()


def main() -> None:
    logger.info("워커 시작 (worker_id=%s, drfc_dir=%s)", WORKER_ID, DRFC_DIR)
    db = SessionLocal()
    try:
        recover_stale_running(db)
    finally:
        db.close()

    # 평가에 붙잡혀 있는 동안에도 신호가 끊기지 않도록 별도 스레드에서 갱신한다.
    start_heartbeat_thread()

    while True:
        db = SessionLocal()
        try:
            submission_id = claim_next_submission(db)
        finally:
            db.close()

        if submission_id is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        process_submission(submission_id)


if __name__ == "__main__":
    main()
