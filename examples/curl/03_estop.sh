#!/usr/bin/env bash
# 急停 / 取消急停。不需要控制权（安全动作不该排队），但仍需 operator 权限。
#
#   bash 03_estop.sh on     # 触发急停
#   bash 03_estop.sh off    # 取消急停
#
# ⚠️ 请求体必须带 active。机器人端读的是 bool(payload.get("active", False))，
#    所以发空体 {} 等于「取消急停」—— 和意图正好相反。
. "$(dirname "$0")/_common.sh"

case "${1:-}" in
  on)  ACTIVE=true ;;
  off) ACTIVE=false ;;
  *)   echo "用法: $0 on|off" >&2; exit 1 ;;
esac

echo "下发 active=$ACTIVE ..."
curl -sS -X POST "$R/estop" \
  -H "X-API-Key: $CX_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: estop-$ACTIVE-$(date +%s)-$$" \
  -w '\n[HTTP %{http_code}]\n' -d "{\"active\": $ACTIVE}" | pretty

echo
sleep 1
curl -sS -H "X-API-Key: $CX_KEY" "$R/telemetry" | python3 -c '
import json, sys
d = json.load(sys.stdin).get("data", {})
print("emergency_active =", d.get("emergency_active"))
print()
print("注意：这是软件标志，表示机器人端正在持续下发停止指令，")
print("不是硬件急停状态。现场有人同时遥控时两者会互相竞争。")
'
