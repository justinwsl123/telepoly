#!/usr/bin/env bash
# Railway 启动脚本：拉起主 Bot polling（Day 1 版）
# Day 2 起追加：deposit_watcher / admin_web / 多 Bot 矩阵
# ==========================================================
set -e

DATA_DIR="${DATA_DIR:-/app/data}"
mkdir -p "$DATA_DIR" logs

# 让 SQLite 落到持久卷
if [ -z "$DATABASE_URL" ]; then
  export DATABASE_URL="sqlite:///${DATA_DIR}/telepoly.db"
fi

# uv 安装的 venv 一般在 .venv；Nixpacks 默认放在项目下
PY="${PY:-python}"
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
fi

echo "=========================================================="
echo "🚀 TelePoly starting"
echo "   DATABASE_URL = $DATABASE_URL"
echo "   Owners       = ${OWNER_TG_IDS:-(none)}"
echo "   Channel      = ${ANNOUNCE_CHANNEL_ID:-(none)}"
echo "   Python       = $PY"
echo "=========================================================="

# 1) 幂等建表 / 后续可改 alembic
$PY -m db.init

# 2) 拉主 Bot
$PY -m telepoly_bot.main &
BOT_PID=$!
echo "[bot] pid=$BOT_PID"

cleanup() {
  echo "[supervisor] shutting down…"
  kill -TERM "$BOT_PID" 2>/dev/null || true
  wait "$BOT_PID" 2>/dev/null || true
  exit 0
}
trap cleanup SIGTERM SIGINT

wait -n
EXIT=$?
echo "[supervisor] child exited ($EXIT), killing siblings…"
kill -TERM "$BOT_PID" 2>/dev/null || true
exit "$EXIT"
