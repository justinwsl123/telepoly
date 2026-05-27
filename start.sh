#!/usr/bin/env bash
# Railway 启动脚本
# 拉起：主 Bot（必启） + 充值扫块器（钱包配齐时启） + admin web + 矩阵副 Bot
# ==========================================================
set -e

DATA_DIR="${DATA_DIR:-/app/data}"
mkdir -p "$DATA_DIR" logs

if [ -z "$DATABASE_URL" ]; then
  export DATABASE_URL="sqlite:///${DATA_DIR}/telepoly.db"
fi

PY="${PY:-python}"
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
fi
PORT="${PORT:-8080}"

echo "=========================================================="
echo "🚀 TelePoly starting"
echo "   DATABASE_URL = $DATABASE_URL"
echo "   Owners       = ${OWNER_TG_IDS:-(none)}"
echo "   Channel      = ${ANNOUNCE_CHANNEL_ID:-(none)}"
echo "   HD wallet    = $([ -n "$WALLET_MNEMONIC" ] && echo configured || echo NOT-configured)"
echo "   Admin web    = port $PORT"
echo "   Matrix       = ${TELEPOLY_BOT_MATRIX:-(main only)}"
echo "=========================================================="

# 1) 幂等建表
$PY -m db.init

PIDS=()

# 2) 主 Bot（默认 BOT_ID=main）
echo "[bot:main] launching…"
BOT_ID=main $PY -m telepoly_bot.main &
PIDS+=($!)
echo "[bot:main] pid=${PIDS[-1]}"

# 3) 矩阵副 Bot（可选）
# TELEPOLY_BOT_MATRIX 格式："id:token:username:lang|id2:token2:username2:lang2"
if [ -n "$TELEPOLY_BOT_MATRIX" ]; then
  IFS='|' read -ra ENTRIES <<< "$TELEPOLY_BOT_MATRIX"
  for entry in "${ENTRIES[@]}"; do
    IFS=':' read -ra P <<< "$entry"
    SUB_ID="${P[0]}"
    SUB_TOKEN="${P[1]}"
    SUB_USERNAME="${P[2]:-TelePoly_${SUB_ID}_Bot}"
    SUB_LANG="${P[3]:-en}"
    if [ -z "$SUB_TOKEN" ] || [ "$SUB_TOKEN" = "$TELEPOLY_BOT_TOKEN" ]; then
      echo "[bot:$SUB_ID] skip (empty or duplicate token)"
      continue
    fi
    echo "[bot:$SUB_ID] launching @${SUB_USERNAME} lang=${SUB_LANG}…"
    BOT_ID="$SUB_ID" \
      TELEPOLY_BOT_TOKEN="$SUB_TOKEN" \
      TELEPOLY_BOT_USERNAME="$SUB_USERNAME" \
      BOT_LANG="$SUB_LANG" \
      DEFAULT_LANG="$SUB_LANG" \
      $PY -m telepoly_bot.main &
    PIDS+=($!)
    echo "[bot:$SUB_ID] pid=${PIDS[-1]}"
  done
fi

# 4) 充值扫块器（仅在助记词配置时启）
if [ -n "$WALLET_MNEMONIC" ]; then
  echo "[watcher] launching deposit_watcher…"
  $PY -m wallet.deposit_watcher &
  PIDS+=($!)
  echo "[watcher] pid=${PIDS[-1]}"
fi

# 5) Admin Web（FastAPI / 8080，Railway 会把 $PORT 注入）
echo "[web] launching admin_web…"
$PY -m uvicorn admin_web.main:app --host 0.0.0.0 --port "$PORT" --log-level info &
PIDS+=($!)
echo "[web] pid=${PIDS[-1]}"

# Supervisor: 任一进程退出 → 全部退出 → Railway 重启容器
cleanup() {
  echo "[supervisor] received signal, shutting down ${#PIDS[@]} children…"
  for pid in "${PIDS[@]}"; do
    kill -TERM "$pid" 2>/dev/null || true
  done
  for pid in "${PIDS[@]}"; do
    wait "$pid" 2>/dev/null || true
  done
  exit 0
}
trap cleanup SIGTERM SIGINT

wait -n
EXIT=$?
echo "[supervisor] one child died (exit $EXIT), tearing down siblings…"
for pid in "${PIDS[@]}"; do
  kill -TERM "$pid" 2>/dev/null || true
done
exit "$EXIT"
