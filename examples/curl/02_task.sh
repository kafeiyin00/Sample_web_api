#!/usr/bin/env bash
# 下发巡检 → 跟踪进度 → 停止。**这会让机器人真的走起来**，先确认现场安全。
#
# 前提：机器人已完成初始化（启动设备 + 定位），见 docs/full-patrol.md。
# 只跑这个脚本而没做初始化的话，任务会下发成功但机器人不动。
. "$(dirname "$0")/_common.sh"

MAP="${1:-}"
shift || true
POINTS=("$@")

if [ -z "$MAP" ]; then
  echo "用法: $0 <地图名> <航点1> <航点2> [更多航点...]"
  echo
  echo "可用地图："
  curl -sS -H "X-API-Key: $CX_KEY" "$R/maps" | pretty
  exit 1
fi
if [ "${#POINTS[@]}" -lt 2 ]; then
  echo "至少要两个航点" >&2
  exit 1
fi

# 航点数组 → JSON 数组
PATH_JSON=$(printf '%s\n' "${POINTS[@]}" | python3 -c 'import json,sys; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))')

# 幂等键：同一次任务重试时必须复用同一个键，否则等于没做幂等
IDEM="patrol-$(date +%Y%m%d-%H%M%S)-$$"

printf '\n\033[1m下发任务\033[0m  map=%s path=%s\n' "$MAP" "$PATH_JSON"
curl -sS -X POST "$R/task" \
  -H "X-API-Key: $CX_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $IDEM" \
  -w '\n[HTTP %{http_code}]\n' \
  -d "{\"map_name\":\"$MAP\",\"path\":$PATH_JSON}" | pretty

echo
echo "同一个幂等键再发一次 —— 应该拿回一模一样的响应，且不会重复执行："
curl -sS -X POST "$R/task" \
  -H "X-API-Key: $CX_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $IDEM" \
  -D - -o /dev/null \
  -d "{\"map_name\":\"$MAP\",\"path\":$PATH_JSON}" 2>/dev/null \
  | grep -iE '^(HTTP/|idempotent-replay)' || true

printf '\n\033[1m跟踪进度\033[0m（Ctrl-C 退出跟踪，不会停止任务）\n'
while true; do
  curl -sS -H "X-API-Key: $CX_KEY" "$R/task" | python3 "$HERE/_status.py" || break
  sleep 2      # 别小于 1 秒，会撞限流（默认 5 请求/秒）
done

printf '\n\033[1m停止任务\033[0m\n'
curl -sS -X DELETE "$R/task" -H "X-API-Key: $CX_KEY" \
  -H "Idempotency-Key: stop-$IDEM" -w '\n[HTTP %{http_code}]\n' | pretty

echo
echo "注意：停任务不等于停设备。完整收尾见 docs/full-patrol.md 第 ⑧ 步。"
