#!/usr/bin/env python3
"""从 stdin 读 GET /task 的响应，打印一行进度；任务已结束时退出码为 7。

单独成文件而不是内联进 shell —— 内联要嵌三层引号，很容易写错还难看。
"""
import json
import sys

ACTIVE = {'running', 'navigating', 'patrolling',
          'exit_charger', 'nav_preprocess', 'enter_charger'}

d = (json.load(sys.stdin) or {}).get('data') or {}
status = d.get('status') or ''
err = d.get('error_code') or 0
print(f"{status:<16} 目标={d.get('current_target') or '-':<6} "
      f"已访={len(d.get('visited') or [])}/{len(d.get('path') or [])}"
      f"{'  错误码=' + hex(err) if err else ''}")
# 下发时写 running，读回来是 navigating；失败读回来是 paused —— 所以用集合判断
sys.exit(0 if status in ACTIVE else 7)
