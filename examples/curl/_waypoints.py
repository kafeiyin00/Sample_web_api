#!/usr/bin/env python3
"""从 stdin 读 GET /maps/{name}/waypoints 的响应，摘要打印前几个航点。"""
import json
import sys

d = (json.load(sys.stdin) or {}).get('data') or {}
ids = list(d)
more = ' ...' if len(ids) > 8 else ''
print(f'共 {len(ids)} 个航点，ID: {ids[:8]}{more}')
for k in ids[:3]:
    pos = d[k]['pose']['position']
    print(f"  {k}: 邻居={d[k]['neighbors']} "
          f"位置=({pos['x']:.3f}, {pos['y']:.3f}, {pos['z']:.3f})")
if ids:
    print('注意：pose.orientation 是四元数 {x,y,z,w}，不是 yaw。')
