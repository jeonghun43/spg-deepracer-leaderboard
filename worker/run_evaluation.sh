#!/usr/bin/env bash
# DRFC(dr-start-evaluation)를 실행하고 완료될 때까지 대기하는 래퍼.
#
# dr-start-evaluation -q 는 Docker Swarm 스택을 "배포"만 하고 즉시 반환한다
# (scripts/evaluation/start.sh 확인 결과 -q 옵션은 로그 추적 없이 exit 0).
# 따라서 이 스크립트가 직접 스택의 컨테이너가 모두 내려갈 때까지 폴링한다
# (utils/evaluate.sh의 사전 대기 로직과 동일한 패턴을 완료 후 대기에 적용).
#
# 평가 대상 모델은 이 스크립트를 부르기 전에 이미 S3(${DR_LOCAL_S3_MODEL_PREFIX}/model/)에
# 올려져 있어야 한다 (Python 워커가 boto3로 미리 업로드).
#
# 사용법: run_evaluation.sh
# 환경변수: DRFC_DIR(기본 ~/deepracer-for-cloud), DR_ENV_FILE(기본 run.env), MAX_WAIT_SECONDS(기본 1800)

# set -u는 쓰지 않는다: DRFC의 dr-* 함수들이 내부적으로 activate.sh를 다시 source하는데
# (dr-update-env), 그 안에서 미설정 변수를 참조해 unbound variable로 죽는다.
# 대신 이 스크립트의 실패는 아래에서 명시적으로 감지한다.
set -eo pipefail

DRFC_DIR="${DRFC_DIR:-$HOME/deepracer-for-cloud}"
DR_ENV_FILE="${DR_ENV_FILE:-run.env}"
MAX_WAIT_SECONDS="${MAX_WAIT_SECONDS:-1800}"
START_TIMEOUT_SECONDS="${START_TIMEOUT_SECONDS:-300}"

cd "$DRFC_DIR"
# activate.sh 내부의 grep 등이 정상적으로 0이 아닌 종료 코드를 낼 수 있어
# 이 구간만 set -e/pipefail을 잠깐 끈다 (worker/run_worker.sh와 동일한 이유).
set +e +o pipefail
# shellcheck disable=SC1091
source bin/activate.sh "$DR_ENV_FILE"
set -e -o pipefail

# WSL2에서 localhost:9000 접속 시 타임아웃이 나는 문제 우회 (사용자 확인된 해결책)
if [[ -z "${DR_LOCAL_S3_ENDPOINT_URL:-}" || "$DR_LOCAL_S3_ENDPOINT_URL" == *localhost* || "$DR_LOCAL_S3_ENDPOINT_URL" == *127.0.0.1* ]]; then
  DRFC_WSL_IP=$(ip -4 route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')
  export DR_LOCAL_S3_ENDPOINT_URL="http://${DRFC_WSL_IP}:9000"
  export DR_LOCAL_PROFILE_ENDPOINT_URL="--profile ${DR_LOCAL_S3_PROFILE} --endpoint-url ${DR_LOCAL_S3_ENDPOINT_URL}"
fi
echo "[run_evaluation] DR_LOCAL_S3_ENDPOINT_URL=${DR_LOCAL_S3_ENDPOINT_URL}"
echo "[run_evaluation] DR_LOCAL_S3_MODEL_PREFIX=${DR_LOCAL_S3_MODEL_PREFIX}"

STACK_NAME="deepracer-eval-${DR_RUN_ID}"

# 아직 살아있는(=끝나지 않은) 태스크 수.
#
# 주의: 그냥 `docker stack ps`를 쓰면 이미 끝난 태스크도 이력으로 계속 남아 있어
# 카운트가 절대 0이 되지 않는다(실제로 이 때문에 평가가 7분 만에 끝났는데도
# 30분 타임아웃까지 기다리는 버그가 있었다). 평가 컨테이너는 restart_policy가
# none이라 완료되면 DESIRED STATE가 shutdown으로 바뀌므로,
# desired-state=running인 것만 세야 "아직 돌고 있는지"를 정확히 알 수 있다.
running_task_count() {
  docker stack ps "$STACK_NAME" --filter desired-state=running -q 2>/dev/null | wc -l || true
}

# 스택 자체가 남아있는지(이력 태스크 포함) — 이전 평가 잔재 정리 확인용.
stack_exists() {
  [[ "$(docker stack ps "$STACK_NAME" -q 2>/dev/null | wc -l || true)" -gt 0 ]]
}

wait_for_stack_removed() {
  while stack_exists; do
    sleep 5
  done
}

# DRFC는 DR_ROBOMAKER_MOUNT_LOGS=False이면 시뮬레이션 로그를 디스크에 남기지 않는다.
# 로그는 Swarm 서비스가 살아있는 동안만 `docker service logs`로 볼 수 있고,
# dr-stop-evaluation으로 서비스를 지우는 순간 영구히 사라진다.
# 평가가 실패했을 때 원인을 추적하려면 스택을 내리기 전에 반드시 받아둬야 한다.
capture_logs() {
  [[ -n "${EVAL_LOG_PATH:-}" ]] || return 0
  mkdir -p "$(dirname "$EVAL_LOG_PATH")"
  {
    echo "=== robomaker ==="
    docker service logs "${STACK_NAME}_robomaker" --no-task-ids 2>&1 || echo "(로그 없음)"
    echo
    echo "=== rl_coach ==="
    docker service logs "${STACK_NAME}_rl_coach" --no-task-ids 2>&1 || echo "(로그 없음)"
  } > "$EVAL_LOG_PATH" 2>&1 || true
  echo "[run_evaluation] 시뮬레이션 로그 저장: $EVAL_LOG_PATH"
}

echo "[run_evaluation] 이전 평가가 남아있지 않은지 확인 중..."
dr-stop-evaluation || true
wait_for_stack_removed

echo "[run_evaluation] 평가 시작..."
if ! dr-start-evaluation -q; then
  echo "[run_evaluation] dr-start-evaluation 호출 실패" >&2
  exit 1
fi

# 1) 태스크가 실제로 뜰 때까지 대기 (이미지 준비 등으로 몇십 초 걸릴 수 있다).
#    끝까지 하나도 안 뜨면 시작 자체가 실패한 것으로 본다.
started=0
for _ in $(seq 1 "$START_TIMEOUT_SECONDS"); do
  if [[ "$(running_task_count)" -gt 0 ]]; then
    started=1
    break
  fi
  sleep 1
done

if [[ "$started" -eq 0 ]]; then
  echo "[run_evaluation] 평가 컨테이너가 ${START_TIMEOUT_SECONDS}초 안에 시작되지 않았습니다" >&2
  capture_logs
  dr-stop-evaluation || true
  exit 3
fi
echo "[run_evaluation] 평가 컨테이너 기동 확인, 완료 대기 중..."

# 2) 살아있는 태스크가 0이 되면 평가가 끝난 것이다.
elapsed=0
while [[ "$(running_task_count)" -gt 0 ]]; do
  if [[ "$elapsed" -ge "$MAX_WAIT_SECONDS" ]]; then
    echo "[run_evaluation] TIMEOUT: ${MAX_WAIT_SECONDS}초 초과" >&2
    capture_logs
    dr-stop-evaluation || true
    exit 2
  fi
  sleep 10
  elapsed=$((elapsed + 10))
done
echo "[run_evaluation] 평가 완료 (${elapsed}초 경과, 실행 중인 컨테이너 없음)"

capture_logs
dr-stop-evaluation || true
echo "[run_evaluation] DONE"
