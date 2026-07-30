#!/usr/bin/env bash
# 대회 데이터 백업 — DB 덤프 + storage 압축 (tasks.md Phase 11)
#
# 왜 필요한가: 대회 데이터가 이 노트북 한 곳에만 있다. 특히 DB는 Windows 폴더가 아니라
# WSL 내부 Docker 볼륨에 있어서 C 드라이브를 백업해도 들어가지 않는다. 랩타임·순위는
# 한 번 잃으면 재현이 불가능하다(모델은 참가자가 재업로드 가능, 비밀번호는 재발급 가능).
#
# 사용법:  bash scripts/backup.sh
# 자동 실행: scripts/systemd/ 의 유닛 등록 (docs/operations.md 참고)
#
# 환경변수 (기본값)
#   BACKUP_DIR              /mnt/c/Users/jjh03/drleader-backup
#                           Google Drive 데스크톱의 "내 컴퓨터 폴더 백업"으로 이 폴더를
#                           올리면 노트북이 죽어도 사본이 남는다. Drive 가상 드라이브(G:)에는
#                           WSL에서 직접 쓸 수 없어 이렇게 방향을 뒤집는다.
#   BACKUP_KEEP             14      (보관할 벌 수. 오래된 것부터 삭제)
#   BACKUP_INCLUDE_MODELS   false   (제출 모델 포함 여부. 건당 250MB라 기본 제외)

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/mnt/c/Users/jjh03/drleader-backup}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"
BACKUP_INCLUDE_MODELS="${BACKUP_INCLUDE_MODELS:-false}"

DB_SERVICE="db"
DB_USER="drleader"
DB_NAME="drleader"
# 압축을 푼 기준 최소 크기. 압축 크기로 재면 안 된다 — SQL은 압축률이 높아 정상 덤프도
# 수 KB로 줄어든다(실제로 팀 8개 DB가 20KB → 4.4KB).
MIN_DUMP_BYTES=5000
# 이 테이블들이 덤프에 없으면 스키마가 통째로 빠진 것이다. 크기보다 확실한 검사다.
REQUIRED_TABLES=(seasons teams accounts submissions evaluation_results)

DATE="$(date +%F)"
LOG_FILE="$BACKUP_DIR/backup.log"
DB_FILE="$BACKUP_DIR/db_$DATE.sql.gz"
STORAGE_FILE="$BACKUP_DIR/storage_$DATE.tar.gz"

mkdir -p "$BACKUP_DIR"

log() {
    printf '%s  %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG_FILE"
}

fail() {
    log "실패: $*"
    printf 'FAILED %s  %s\n' "$(date '+%F %T')" "$*" > "$BACKUP_DIR/STATUS"
    exit 1
}

human() {
    du -h "$1" 2>/dev/null | cut -f1
}

cd "$PROJECT_DIR"

log "백업 시작 (대상: $BACKUP_DIR, 보관: ${BACKUP_KEEP}벌, 모델 포함: $BACKUP_INCLUDE_MODELS)"

# ── 1. DB 덤프 ────────────────────────────────────────────────────────────
# 파이프 중간(pg_dump)의 실패를 놓치지 않도록 PIPESTATUS를 확인한다. gzip만 성공하고
# 덤프가 비어 있는 백업이 남는 것이 가장 위험한 실패 방식이다.
log "DB 덤프 중..."
set +e
docker compose exec -T "$DB_SERVICE" pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$DB_FILE.tmp"
PIPE_STATUS=("${PIPESTATUS[@]}")
set -e
[ "${PIPE_STATUS[0]}" -eq 0 ] || fail "pg_dump 실패 (exit=${PIPE_STATUS[0]}). DB 컨테이너가 떠 있는지 확인하세요."
[ "${PIPE_STATUS[1]}" -eq 0 ] || fail "gzip 실패 (exit=${PIPE_STATUS[1]})"

# ── 2. DB 덤프 무결성 검사 ────────────────────────────────────────────────
# 복원해본 적 없는 백업은 백업이 아니다. 최소한 압축이 온전하고, 내용이 실제
# pg_dump 산출물이며, 크기가 그럴듯한지는 매번 확인한다.
gzip -t "$DB_FILE.tmp" 2>/dev/null || fail "덤프 압축이 손상됐습니다"

DUMP_BYTES="$(gunzip -c "$DB_FILE.tmp" | wc -c)"
[ "$DUMP_BYTES" -ge "$MIN_DUMP_BYTES" ] || fail "덤프가 너무 작습니다 (압축 해제 ${DUMP_BYTES}바이트)"

gunzip -c "$DB_FILE.tmp" | head -20 | grep -q "PostgreSQL database dump" \
    || fail "덤프 내용이 pg_dump 산출물이 아닙니다"
# pg_dump는 정상 종료 시 마지막에 완료 표시를 남긴다. 중간에 끊긴 덤프를 걸러내는 핵심 검사다.
gunzip -c "$DB_FILE.tmp" | tail -5 | grep -q "PostgreSQL database dump complete" \
    || fail "덤프가 끝까지 기록되지 않았습니다 (중간에 끊김)"

DUMP_TEXT="$(gunzip -c "$DB_FILE.tmp")"
for table in "${REQUIRED_TABLES[@]}"; do
    grep -q "CREATE TABLE public.$table" <<<"$DUMP_TEXT" \
        || fail "덤프에 $table 테이블이 없습니다"
done
unset DUMP_TEXT
mv "$DB_FILE.tmp" "$DB_FILE"
log "DB 덤프 완료: $(basename "$DB_FILE") ($(human "$DB_FILE"))"

# ── 3. storage 압축 ───────────────────────────────────────────────────────
# work/ 는 평가 중 임시 작업본이라 백업 의미가 없다. models/ 는 건당 250MB이고
# 참가자가 원본을 갖고 있어 기본 제외한다(BACKUP_INCLUDE_MODELS=true로 포함).
TAR_EXCLUDES=(--exclude="storage/work")
if [ "$BACKUP_INCLUDE_MODELS" != "true" ]; then
    TAR_EXCLUDES+=(--exclude="storage/models")
fi

log "storage 압축 중..."
tar -czf "$STORAGE_FILE.tmp" "${TAR_EXCLUDES[@]}" -C "$PROJECT_DIR" storage \
    || fail "storage 압축 실패"
gzip -t "$STORAGE_FILE.tmp" 2>/dev/null || fail "storage 압축이 손상됐습니다"
tar -tzf "$STORAGE_FILE.tmp" >/dev/null 2>&1 || fail "storage 아카이브를 읽을 수 없습니다"
mv "$STORAGE_FILE.tmp" "$STORAGE_FILE"
log "storage 압축 완료: $(basename "$STORAGE_FILE") ($(human "$STORAGE_FILE"))"

# ── 4. 오래된 백업 정리 ───────────────────────────────────────────────────
# DB와 storage는 같은 날짜끼리 짝이므로 각각 같은 기수만 남긴다.
prune() {
    local pattern="$1"
    local removed=0
    # 이름에 날짜(YYYY-MM-DD)가 들어가 사전순 = 시간순이다.
    while IFS= read -r old; do
        rm -f "$old"
        removed=$((removed + 1))
    done < <(ls -1 "$BACKUP_DIR"/$pattern 2>/dev/null | sort | head -n -"$BACKUP_KEEP")
    [ "$removed" -gt 0 ] && log "오래된 백업 $removed개 삭제 ($pattern)"
    return 0
}
prune "db_*.sql.gz"
prune "storage_*.tar.gz"

# ── 5. 상태 기록 ──────────────────────────────────────────────────────────
KEPT="$(ls -1 "$BACKUP_DIR"/db_*.sql.gz 2>/dev/null | wc -l)"
printf 'OK %s  db=%s storage=%s 보관=%s벌\n' \
    "$(date '+%F %T')" "$(human "$DB_FILE")" "$(human "$STORAGE_FILE")" "$KEPT" \
    > "$BACKUP_DIR/STATUS"
log "백업 성공 (총 ${KEPT}벌 보관 중)"
