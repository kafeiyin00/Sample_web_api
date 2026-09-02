#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""持续读机器人位置，顺便把「怎么正确轮询」讲清楚。只读，不会让机器人动。

轮询看着简单，但三件事做错就会踩坑：
  1. 间隔小于 1 秒会撞限流（默认 5 请求/秒）
  2. 收到 429 要照 Retry-After 退避，不能硬重试
  3. 判断定位是否可信要用 received_at（Unix 时间），不是 stamp（ROS 时间）

    python3 02_watch_position.py --robot ntu-dog-00001 --key cx_xxx_... --interval 1.5
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def get(host, key, path, timeout=15):
    """GET 一次。429 时按 Retry-After 睡一觉再重试，最多三次。"""
    url = f'{host}{path}'
    req = urllib.request.Request(url, headers={'X-API-Key': key})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode('utf-8', 'replace')
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read().decode('utf-8', 'replace')
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw
            if e.code == 429 and attempt < 2:
                # 服务端明确告诉了该等多久，照做 —— 自己猜一个间隔硬重试只会更糟
                wait = float(e.headers.get('Retry-After') or 1)
                print(f'  [限流] 等 {wait:.0f} 秒后重试', file=sys.stderr)
                time.sleep(wait)
                continue
            return e.code, body
        except urllib.error.URLError as e:
            if attempt < 2:
                time.sleep(1.0)
                continue
            return 0, str(e)
    return 0, 'retries exhausted'


def main():
    ap = argparse.ArgumentParser(description='持续读位置（只读）')
    ap.add_argument('--host', default='https://certaintyx.sg:8443')
    ap.add_argument('--robot', required=True)
    ap.add_argument('--key', required=True)
    ap.add_argument('--interval', type=float, default=1.5,
                    help='轮询间隔秒数，别小于 1（默认 1.5）')
    ap.add_argument('--count', type=int, default=0, help='读多少次后退出，0 = 一直读')
    args = ap.parse_args()

    if args.interval < 1.0:
        print('间隔小于 1 秒会撞限流，已自动抬到 1.0', file=sys.stderr)
        args.interval = 1.0

    R = f'/v1/robots/{urllib.parse.quote(args.robot)}'

    st, body = get(args.host, args.key, R)
    if st != 200:
        raise SystemExit(f'读概览失败 HTTP {st}: {body}')
    if not body['data'].get('online'):
        raise SystemExit('机器人不在线')
    print(f"机器人 {body['data']['robotId']}（{body['data'].get('location')}）"
          f"  Ctrl-C 退出\n")

    n = 0
    last = None
    while args.count == 0 or n < args.count:
        n += 1
        # 一次拿到位姿 + 定位新鲜度：/telemetry 比 /position 多一点信息但只要一个请求
        st, body = get(args.host, args.key, f'{R}/telemetry')
        if st != 200:
            print(f'[HTTP {st}] {body}')
            time.sleep(args.interval)
            continue

        data = body.get('data') or {}
        gl = (data.get('telemetry') or {}).get('global_localization') or {}
        if not gl.get('received'):
            print('定位话题还没有数据 —— 定位模块没起来，或需要先重定位')
            time.sleep(args.interval)
            continue

        age = time.time() - float(gl.get('received_at') or 0)
        fresh = age < 5.0            # 用 received_at，不是 stamp
        moved = ''
        if last is not None:
            d = ((gl['x'] - last[0]) ** 2 + (gl['y'] - last[1]) ** 2) ** 0.5
            moved = f'  位移={d:.3f}m'
        last = (gl['x'], gl['y'])

        flags = []
        if data.get('emergency_active'):
            flags.append('急停激活')     # 这时候机器人不会动，别以为是任务没下发成功
        if not data.get('ros_available'):
            flags.append('ROS 不可用')
        if not fresh:
            flags.append(f'定位数据已过期 {age:.0f}s')

        print(f"x={gl['x']:8.3f} y={gl['y']:8.3f} yaw={gl['yaw']:6.3f}"
              f"{moved}"
              f"{'  ⚠ ' + ' / '.join(flags) if flags else ''}")
        time.sleep(args.interval)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print()
