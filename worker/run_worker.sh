#!/usr/bin/env bash
# 평가 워커 실행 진입점.
#
# worker/run.py(및 drfc.py)는 DR_LOCAL_S3_BUCKET 등 DR_* 환경변수가 이미
# 프로세스 환경에 있다고 가정한다 (plan.md §1: DRFC의 run.env/system.env를
# 앱이 따로 관리하지 않고 그대로 물려받는다는 결정). 이 값들은 DRFC의
# bin/activate.sh를 source해야 계산되므로, 이 스크립트가 그 작업을 먼저 하고
# 같은 쉘 환경에서 파이썬 워커를 실행한다.
#
# 사용법: worker/run_worker.sh
# 환경변수: DRFC_DIR(기본 ~/deepracer-for-cloud), DR_ENV_FILE(기본 run.env)

# set -u는 쓰지 않는다 — DRFC의 activate.sh가 미설정 변수를 참조한다 (run_evaluation.sh와 동일).
set -eo pipefail

DRFC_DIR="${DRFC_DIR:-$HOME/deepracer-for-cloud}"
DR_ENV_FILE="${DR_ENV_FILE:-run.env}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pushd "$DRFC_DIR" >/dev/null
# activate.sh 내부에는 (grep 매치 실패 등) 정상적으로 0이 아닌 종료 코드를 내는
# 명령이 섞여 있어, 이 구간만 set -e/pipefail을 잠깐 끈다.
set +e +o pipefail
# shellcheck disable=SC1090
source bin/activate.sh
set -e -o pipefail
popd >/dev/null

# activate.sh가 조용히 실패하거나(예: run.env 경로 오류) 워커가 이 스크립트를
# 거치지 않고 직접 실행되면, DR_* 변수가 없는 채로 몇 시간을 떠 있다가
# 참가자 제출 시점에야 깊은 스택트레이스의 KeyError로 드러난다
# (2026-07-25 실제 발생). 여기서 즉시, 명확하게 실패시킨다.
REQUIRED_DR_VARS=(DR_LOCAL_S3_BUCKET DR_LOCAL_S3_MODEL_PREFIX DR_LOCAL_S3_PROFILE)
missing=0
for var in "${REQUIRED_DR_VARS[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    echo "[run_worker] ERROR: 필수 환경변수 $var 가 로드되지 않았습니다." >&2
    missing=1
  fi
done
if [[ "$missing" -eq 1 ]]; then
  echo "[run_worker] DRFC_DIR=$DRFC_DIR DR_ENV_FILE=$DR_ENV_FILE 를 확인하세요." >&2
  echo "[run_worker] (예: $DRFC_DIR/$DR_ENV_FILE 파일이 존재하는지, activate.sh가 정상 종료했는지)" >&2
  exit 1
fi
echo "[run_worker] DR_LOCAL_S3_BUCKET=$DR_LOCAL_S3_BUCKET (환경변수 확인됨)"

# DRFC는 평가 시작 시 _dr_ensure_sagemaker_dir로 /tmp/sagemaker가 있는지 확인하고,
# 없으면 sudo로 만들려 한다 — 비대화형 워커는 여기서 비밀번호를 못 넣어 실패한다.
# 이 디렉터리는 원래 SageMaker 로컬 모드(학습)가 쓰는 것이라 평가에는 실제로 필요 없고
# (docker-compose-eval.yml에는 마운트되지 않는다), /tmp가 world-writable이라
# sudo 없이도 만들 수 있다. 있기만 하면 DRFC가 sudo를 호출하지 않으므로 미리 만들어 둔다.
# /tmp는 WSL 재시작 시 비워지므로 워커를 띄울 때마다 확인한다.
mkdir -p /tmp/sagemaker 2>/dev/null || true

cd "$PROJECT_DIR"
exec "$PROJECT_DIR/.venv/bin/python" -m worker.run
