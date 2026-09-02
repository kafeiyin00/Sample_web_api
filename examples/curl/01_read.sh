#!/usr/bin/env bash
# 只读：确认能通，并把一台机器人的状态看个遍。不会让机器人动。
. "$(dirname "$0")/_common.sh"

printf '\n\033[1m自描述（免鉴权）\033[0m\n'
curl -sS "$CX_HOST/v1" | pretty

show "机器人概览" "$R"
show "遥测快照"   "$R/telemetry"
show "当前位姿（定位未就绪时是 503，属正常）" "$R/position"
show "定位/障碍状态" "$R/perception"
show "地图列表"   "$R/maps"
show "当前任务"   "$R/task"

# 取第一张地图的航点，只打印前几个（一张图可能有几十上百个点）
MAP=$(curl -sS -H "X-API-Key: $CX_KEY" "$R/maps" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]; print(d[0] if d else "")')
if [ -n "$MAP" ]; then
  printf '\n\033[1m地图 %s 的航点\033[0m\n' "$MAP"
  curl -sS -H "X-API-Key: $CX_KEY" "$R/maps/$MAP/waypoints" | python3 "$HERE/_waypoints.py"
fi
