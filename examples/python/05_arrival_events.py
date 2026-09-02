#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""到达提醒：下发巡检，然后每到一个航点就收到通知。

两种模式，默认第一种：

    # 听事件（只读，不会让机器人动）—— 先跑这个看看现在有什么事件
    python3 05_arrival_events.py --robot ntu-dog-00001 --key cx_xxx_... --listen

    # 下发一趟巡检并跟踪到达（⚠️ 会让真实机器人走起来）
    python3 05_arrival_events.py --robot ntu-dog-00001 --key cx_xxx_... \
        --patrol --points 3

`--patrol` 需要 operator + auto 模式的密钥；`--listen` 只读密钥就够。

用 SSE 长连接（`stream=1`）而不是轮询：延迟低，而且一条长连接只占一个请求，
不会像高频轮询那样撞限流。断线会自动带上次的 seq 重连，所以不丢事件。
"""
import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# 事件类型 → 一行人话。照着云端 docs/arrival-events.md 的表
LABEL = {
    'waypoint_reached': '到达航点',
    'task_started': '任务开始',
    'task_completed': '任务完成',
    'task_failed': '任务失败',
    'task_stopped': '任务被停止',
    'obstacle': '避障状态变化',
    'localization': '定位状态变化',
    'emergency': '急停状态变化',
    'online': '机器人上线',
    'offline': '机器人掉线',
}


def api(host, key, path, method='GET', body=None, idem=None, timeout=40):
    url = f'{host}{path}'
    data = json.dumps(body).encode() if body is not None else None
    h = {'X-API-Key': key}
    if data is not None:
        h['Content-Type'] = 'application/json'
    if idem:
        h['Idempotency-Key'] = idem
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode('utf-8', 'replace')
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


def describe(e):
    """把一条事件说成人话"""
    d = e.get('data') or {}
    t = e.get('type')
    if t == 'waypoint_reached':
        pos = f"{d.get('index', 0) + 1}/{d.get('total', '?')}"
        nxt = d.get('nextTarget')
        return (f"到达航点 {d.get('waypoint')}（第 {pos} 个）"
                + (f"，下一个 {nxt}" if nxt else '，这是最后一个'))
    if t == 'task_completed':
        return f"任务完成，共走了 {len(d.get('visited') or [])}/{d.get('total')} 个点"
    if t == 'task_failed':
        return f"任务失败，错误码 {d.get('errorHex')}（用 /v1/status-codes 查含义）"
    if t == 'obstacle':
        return '开始避障' if d.get('avoiding') else '避障结束，继续前进'
    if t == 'localization':
        return '定位恢复' if d.get('valid') else '⚠️ 丢定位了'
    if t == 'emergency':
        return '急停指令开始下发' if d.get('active') else '急停已取消'
    if t == 'task_started':
        return f"任务开始：{d.get('map')} 共 {len(d.get('path') or [])} 个点"
    return json.dumps(d, ensure_ascii=False)


def stream(host, key, robot, since, on_event, timeout=None):
    """SSE 长连接。断线时带最后的 seq 重连 —— 这才是不丢事件的关键。"""
    cursor = since
    t0 = time.time()
    while timeout is None or time.time() - t0 < timeout:
        url = (f'{host}/v1/robots/{urllib.parse.quote(robot)}/events'
               f'?since={cursor}&stream=1')
        req = urllib.request.Request(url, headers={'X-API-Key': key})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                if r.status != 200:
                    print(f'事件流返回 {r.status}', file=sys.stderr)
                    return cursor
                buf = b''
                while timeout is None or time.time() - t0 < timeout:
                    chunk = r.read(1)
                    if not chunk:
                        break                     # 服务端关了，外层重连
                    buf += chunk
                    if not buf.endswith(b'\n\n'):
                        continue
                    block = buf.decode('utf-8', 'replace').strip()
                    buf = b''
                    if block.startswith(':'):
                        continue                  # 心跳注释帧
                    for line in block.split('\n'):
                        if line.startswith('data: '):
                            e = json.loads(line[6:])
                            cursor = max(cursor, int(e.get('seq') or 0))
                            if on_event(e) is False:
                                return cursor
        except KeyboardInterrupt:
            raise
        except Exception as exc:                   # noqa: BLE001
            print(f'  （连接中断，2 秒后从 seq={cursor} 重连: {exc}）', file=sys.stderr)
            time.sleep(2)
    return cursor


def pick_waypoints(wps, pos, count):
    """挑彼此拉开距离的航点。全挑相邻编号的话机器人几秒就跑完，看不出效果。"""
    d = sorted((math.hypot(v['pose']['position']['x'] - pos['x'],
                           v['pose']['position']['y'] - pos['y']), k)
               for k, v in wps.items())
    chosen = [d[0][1]]
    for _dist, k in d[1:]:
        pk = wps[k]['pose']['position']
        if all(math.hypot(pk['x'] - wps[c]['pose']['position']['x'],
                          pk['y'] - wps[c]['pose']['position']['y']) > 2.5 for c in chosen):
            chosen.append(k)
        if len(chosen) >= max(2, count):
            break
    return chosen


def main():
    ap = argparse.ArgumentParser(description='到达提醒（事件流）')
    ap.add_argument('--host', default='https://certaintyx.sg:8443')
    ap.add_argument('--robot', required=True)
    ap.add_argument('--key', required=True)
    ap.add_argument('--listen', action='store_true', help='只听事件，不下发任务（默认）')
    ap.add_argument('--patrol', action='store_true',
                    help='⚠️ 下发一趟真实巡检并跟踪到达。需要 operator+auto 密钥')
    ap.add_argument('--points', type=int, default=3, help='--patrol 时的航点数量')
    ap.add_argument('--timeout', type=float, default=300, help='最多听多少秒')
    args = ap.parse_args()

    R = f'/v1/robots/{urllib.parse.quote(args.robot)}'

    st, b = api(args.host, args.key, R)
    if st != 200:
        raise SystemExit(f'读概览失败 HTTP {st}: {b}')
    if not (b.get('data') or {}).get('online'):
        raise SystemExit('机器人不在线')

    # 起点：不带 since 只给当前 seq，不倒带历史
    st, b = api(args.host, args.key, f'{R}/events')
    cursor = int((b.get('data') or {}).get('seq') or 0)
    print(f'当前事件位置 seq={cursor}\n')

    t0 = time.time()
    seen = {'reached': [], 'done': False}

    def on_event(e):
        print(f"[+{time.time() - t0:6.1f}s] {LABEL.get(e['type'], e['type']):<12} "
              f"{describe(e)}")
        if e['type'] == 'waypoint_reached':
            seen['reached'].append(e['data'].get('waypoint'))
        if e['type'] in ('task_completed', 'task_failed'):
            seen['done'] = True
            return False        # 任务结束就退出
        return True

    if args.patrol:
        st, b = api(args.host, args.key, f'{R}/maps')
        maps = b.get('data') or []
        map_name, wps = None, {}
        for m in maps:
            st, b = api(args.host, args.key, f'{R}/maps/{urllib.parse.quote(m)}/waypoints')
            w = b.get('data') or {}
            if len(w) >= 2:
                map_name, wps = m, w
                break
        if not map_name:
            raise SystemExit('没有带航点的地图')
        st, b = api(args.host, args.key, f'{R}/position')
        if st != 200:
            raise SystemExit('定位未就绪（503）—— 先做定位，见 docs/full-patrol.md')
        path = pick_waypoints(wps, b.get('data') or {}, args.points)

        print(f'下发巡检 {map_name} path={path}')
        print('（现场确认没人在机器人路径上）\n')
        st, b = api(args.host, args.key, f'{R}/task', 'POST',
                    {'map_name': map_name, 'path': path},
                    idem=f'arrival-demo-{int(time.time())}')
        if st != 200 or not b.get('success'):
            raise SystemExit(f'下发失败 HTTP {st}: {json.dumps(b, ensure_ascii=False)}')
        print('已下发，开始听到达提醒：\n')
    else:
        print('只听事件（不下发任务）。现在去界面上或用 02_task.sh 下发一趟巡检，')
        print('到达提醒会实时出现在下面。Ctrl-C 退出。\n')

    try:
        stream(args.host, args.key, args.robot, cursor, on_event, timeout=args.timeout)
    except KeyboardInterrupt:
        print('\n已退出')
        return 0

    if args.patrol:
        print(f"\n共收到 {len(seen['reached'])} 条到达：{seen['reached']}")
        if not seen['done']:
            print('注意：等到超时也没收到任务结束事件 —— 机器人可能还在走，'
                  '或者卡住了。查 GET /task 的 status 与 error_code。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
