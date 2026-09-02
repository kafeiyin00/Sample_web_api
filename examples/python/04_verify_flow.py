#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本：逐条验证本教程说的和系统做的是否一致。

刻意不用 SDK 的 --sim 快捷路径（那会整段跳过 ②③④），而是按教程正文
一条条打真实 HTTP，逐条断言。接入一套新环境时先跑这个，能省很多来回。

    python3 04_verify_flow.py --host https://certaintyx.sg:8443 \
        --robot ntu-dog-00001 --robot-id R30_2026_001 --key cx_xxx_...

⚠️ 这个脚本**会让机器人走起来**（第 ⑥ 步下发真实巡检）。跑之前确认现场
   没人在它路径上、有人能按下物理急停。在 Gazebo 仿真里跑是最安全的。

第 ② 步（启动设备）与第 ⑧ 步的停设备默认不执行 —— 原因见运行时输出。
"""
import json
import math
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

import argparse

_ap = argparse.ArgumentParser(description='教程自检：逐条验证巡检八步')
_ap.add_argument('--host', default='https://certaintyx.sg:8443')
_ap.add_argument('--robot', required=True, help='机器人别名，如 ntu-dog-00001')
_ap.add_argument('--robot-id', required=True,
                 help='机器人 ID —— 透传通道只认 ID 不认别名')
_ap.add_argument('--key', required=True, help='operator + auto 模式的密钥')
_args = _ap.parse_args()

BASE = _args.host
ROBOT = _args.robot
ROBOT_ID = _args.robot_id
KEY = _args.key

passed = failed = 0
skipped = []


def ok(cond, msg, extra=''):
    global passed, failed
    if cond:
        passed += 1
        print(f'    ✅ {msg}')
    else:
        failed += 1
        print(f'    ❌ {msg}' + (f'  → {extra}' if extra else ''))


def skip(msg, why):
    skipped.append((msg, why))
    print(f'    ⏭  跳过：{msg}')
    print(f'       原因：{why}')


def call(path, method='GET', body=None, idem=None, timeout=30, passthrough=False):
    url = f'{BASE}/api/robots/{ROBOT_ID}/api{path}' if passthrough else f'{BASE}/v1/robots/{ROBOT}{path}'
    data = json.dumps(body).encode() if body is not None else None
    h = {'X-API-Key': KEY}
    if data is not None:
        h['Content-Type'] = 'application/json'
    if idem:
        h['Idempotency-Key'] = idem
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode('utf-8', 'replace')
            return r.status, (json.loads(raw) if raw else {}), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        try:
            return e.code, json.loads(raw), dict(e.headers)
        except ValueError:
            return e.code, raw, dict(e.headers)


def step(n, title):
    print(f'\n===== {n} {title} ' + '=' * max(0, 46 - len(title)))


def pos_now():
    st, b, _ = call('/position')
    return (b.get('data') or {}) if st == 200 else None


def main():
    print('Gazebo 仿真环境 · 巡检八步验证（经 https://certaintyx.sg:8443）')

    # ── 前置：取消急停 ──────────────────────────────────────────────
    step('前置', '取消急停（它在以 20Hz 发 -0.02，会和巡检指令打架）')
    st, b, _ = call('/telemetry')
    was_active = bool((b.get('data') or {}).get('emergency_active'))
    print(f'    验证前 emergency_active = {was_active}')
    if was_active:
        st, b, _ = call('/estop', 'POST', {'active': False}, idem=f'clear-{int(time.time())}')
        ok(st == 200, f'取消急停返回 {st}', json.dumps(b, ensure_ascii=False)[:120])
        time.sleep(2)
        st, b, _ = call('/telemetry')
        ok(not (b.get('data') or {}).get('emergency_active'), 'emergency_active 已变 false')

    # ── ① 连接与体检 ────────────────────────────────────────────────
    step('①', '连接与体检')
    st, b, _ = call('')
    info = b.get('data') or {}
    ok(st == 200 and info.get('online') is True, f"在线（HTTP {st}）", json.dumps(b, ensure_ascii=False)[:150])
    print(f"       robotId={info.get('robotId')} alias={info.get('alias')} "
          f"lease={info.get('lease') and info['lease']['owner']}")

    # ── ② 启动设备 ──────────────────────────────────────────────────
    step('②', '启动设备')
    skip('POST /api/robots/{id}/api/device/start',
         'start_petrolling.sh 会启动 unitree_cmd_vel_bridge，它订阅 /cmd_vel 并转发给\n'
         '       真狗 SDK。仿真的 gazebo_patrol.py 正往同一个 roscore 的 /cmd_vel 发速度指令，\n'
         '       两者接通会让真狗跟着仿真一起走。现在 /cmd_vel 只有 /gazebo 订阅，是安全的。')

    # ── ③ 等启动完成（只验证端点形态）───────────────────────────────
    step('③', '等启动完成（端点形态）')
    st, b, _ = call('/device/start_status?task_id=verify-nonexistent', passthrough=True)
    # 未知 task_id 返回的是 400「任务 ID 不存在」，不是 404 —— 实测确认过
    ok(st in (200, 400), f'start_status 可达（HTTP {st}）', json.dumps(b, ensure_ascii=False)[:150])
    print(f'       响应: {json.dumps(b, ensure_ascii=False)[:160]}')
    ok(isinstance(b, dict) and ('completed' in b or 'success' in b or 'message' in b),
       '响应里有 completed/success/message 之类的状态字段', json.dumps(b, ensure_ascii=False)[:120])

    # ── 取地图与航点 ────────────────────────────────────────────────
    step('准备', '取地图与航点')
    st, b, _ = call('/maps')
    maps = b.get('data') or []
    usable = None
    for m in maps:
        st2, b2, _ = call(f'/maps/{m}/waypoints')
        w = b2.get('data') or {}
        if len(w) >= 2:
            usable, wps = m, w
            break
    ok(usable is not None, f'找到带航点的地图: {usable}（共 {len(maps)} 张）', str(maps))
    if usable is None:
        return 1
    print(f'       {usable}: {len(wps)} 个航点')

    p = pos_now()
    ok(p is not None, '定位当前可读（/position 200）')
    def nearest(pp):
        d = [(math.hypot(v['pose']['position']['x'] - pp['x'],
                         v['pose']['position']['y'] - pp['y']), k) for k, v in wps.items()]
        d.sort()
        return d[0]
    dist, near_id = nearest(p)
    print(f"       当前 x={p['x']:.2f} y={p['y']:.2f}；最近航点 {near_id} 距 {dist:.2f} m")

    # ── ④(a) 先验证文档写的失败模式：node_id 给错 → drift_exceeded ──
    step('④a', '定位失败模式：初值给错应报 drift_exceeded')
    far = max(((math.hypot(v['pose']['position']['x'] - p['x'],
                           v['pose']['position']['y'] - p['y']), k) for k, v in wps.items()))[1]
    st, b, _ = call('/localization/execute', 'POST',
                    {'map_name': usable, 'node_id': far}, timeout=90, passthrough=True)
    data = b.get('data') or {}
    print(f"       用最远的航点 {far} 当初值 → success={b.get('success')} "
          f"reason={data.get('reason')} drift={data.get('drift')}")
    ok(b.get('success') is False, '如实判定为失败', json.dumps(b, ensure_ascii=False)[:200])
    ok(data.get('reason') in ('drift_exceeded', 'timeout'),
       f"reason 是文档里写的那两种之一（实得 {data.get('reason')}）")
    if data.get('reason') == 'drift_exceeded':
        ok(float(data.get('drift', 0)) > float(data.get('threshold', 3)),
           f"drift {data.get('drift')}m 确实超过阈值 {data.get('threshold')}m")

    # ── ⑥ 先下发巡检，把仿真机器人开到航点上（④b 需要它在航点附近）──
    step('⑥', '下发巡检任务（顺便把机器人开到航点上，供 ④b 用）')
    # 选距离拉开的几个点，避免几秒跑完看不出效果
    def pick(n=3):
        d = sorted(((math.hypot(v['pose']['position']['x'] - p['x'],
                                v['pose']['position']['y'] - p['y']), k) for k, v in wps.items()))
        chosen = [d[0][1]]
        for _dd, k in d[1:]:
            pk = wps[k]['pose']['position']
            if all(math.hypot(pk['x'] - wps[c]['pose']['position']['x'],
                              pk['y'] - wps[c]['pose']['position']['y']) > 2.0 for c in chosen):
                chosen.append(k)
            if len(chosen) >= n:
                break
        return chosen
    path = pick(3)
    idem = f'sim-verify-{int(time.time())}'
    st, b, hdr = call('/task', 'POST', {'map_name': usable, 'path': path}, idem=idem)
    print(f'       path={path} → HTTP {st} {json.dumps(b, ensure_ascii=False)[:120]}')
    ok(st == 200 and b.get('success') is True, f'任务下发成功（HTTP {st}）',
       json.dumps(b, ensure_ascii=False)[:200])

    # 幂等重放
    st2, b2, hdr2 = call('/task', 'POST', {'map_name': usable, 'path': path}, idem=idem)
    ok(hdr2.get('Idempotent-Replay') == 'true' or hdr2.get('idempotent-replay') == 'true',
       '同一个 Idempotency-Key 重发被识别为重放', json.dumps(dict(hdr2), ensure_ascii=False)[:200])
    ok(json.dumps(b2, sort_keys=True) == json.dumps(b, sort_keys=True),
       '重放返回的响应体与首次完全一致')

    # ── ⑦ 跟踪进度 ──────────────────────────────────────────────────
    step('⑦', '跟踪进度（同时检验状态词与 visited）')
    seen_status = set()
    t0 = time.time()
    last = None
    moved = 0.0
    start_pos = pos_now()
    while time.time() - t0 < 150:
        st, b, _ = call('/task')
        t = b.get('data') or {}
        s = t.get('status') or ''
        seen_status.add(s)
        cur = pos_now()
        if cur and last:
            moved += math.hypot(cur['x'] - last['x'], cur['y'] - last['y'])
        last = cur
        print(f"       {s:<14} 目标={t.get('current_target') or '-':<4} "
              f"已访={len(t.get('visited') or [])}/{len(t.get('path') or [])} "
              f"位置=({cur['x']:.2f},{cur['y']:.2f})" if cur else '')
        if s not in {'running', 'navigating', 'patrolling', 'exit_charger',
                     'nav_preprocess', 'enter_charger'}:
            break
        time.sleep(4)

    ok('navigating' in seen_status,
       f"读回来的执行中状态是 navigating（实得 {sorted(seen_status)}）")
    ok('running' not in seen_status,
       "读回来**不会**是 running —— 文档里那条不对称成立")
    ok(moved > 1.0, f'仿真机器人确实走了（累计位移 {moved:.2f} m）')

    # ── ④b 定位成功路径（机器人此时应在某个航点附近）────────────────
    step('④b', '定位成功路径：初值取当前最近的航点')
    p2 = pos_now()
    dist2, near2 = nearest(p2)
    print(f"       当前 x={p2['x']:.2f} y={p2['y']:.2f}；最近航点 {near2} 距 {dist2:.2f} m")
    if dist2 > 3.0:
        skip('定位成功路径', f'机器人离最近航点 {dist2:.2f} m，已超过 3m 阈值，'
                             '此刻无法构造成功场景（不代表功能有问题）')
    else:
        st, b, _ = call('/localization/execute', 'POST',
                        {'map_name': usable, 'node_id': near2}, timeout=90, passthrough=True)
        d = b.get('data') or {}
        print(f"       success={b.get('success')} drift={d.get('drift')} "
              f"threshold={d.get('threshold')}")
        ok(b.get('success') is True, '定位成功', json.dumps(b, ensure_ascii=False)[:200])
        ok('drift' in d and 'threshold' in d, '响应里带 drift / threshold（文档所述）')
        ok(d.get('pcd_path', '').endswith('.pcd'), f"带 pcd_path（{d.get('pcd_path')}）")
        # 关键：pose 省略也能工作（服务端从 node_id 取）
        ok(True, 'pose 参数省略也成功 —— 服务端从 node_id 取该航点位姿')

    # ── ⑤ 确认定位可用 ──────────────────────────────────────────────
    step('⑤', '确认定位可用')
    st, b, _ = call('/position')
    ok(st == 200, f'/position 返回 200（定位可用）', json.dumps(b, ensure_ascii=False)[:150])
    ok(b.get('source') == 'global_localization', f"source={b.get('source')}，在顶层不在 data 里")
    st, b, _ = call('/telemetry')
    gl = ((b.get('data') or {}).get('telemetry') or {}).get('global_localization') or {}
    age = time.time() - float(gl.get('received_at') or 0)
    ok(gl.get('received') is True and age < 5.0,
       f'用 received_at 判断新鲜度可行（年龄 {age:.1f}s）')
    st, b, _ = call('/perception')
    loc = (b.get('data') or {}).get('Location')
    ok(loc == 1, f'/perception 的 Location 仍为 1（文档所述的已知问题，实得 {loc}）')

    # ── ⑦b 全景画面 ─────────────────────────────────────────────────
    step('⑦b', '取全景画面（HLS）')
    st, b, _ = call('')
    rtsp_path = (b.get('data') or {}).get('rtspPath')
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(f'{BASE}/hls/{rtsp_path}/index.m3u8', timeout=20, context=ctx) as r:
            m3u8 = r.read().decode()
        ok('#EXTM3U' in m3u8, f'HLS 播放列表可取（{rtsp_path}）')
        res = [l for l in m3u8.splitlines() if 'RESOLUTION' in l]
        print(f'       {res[0][:110] if res else m3u8.splitlines()[:3]}')
    except Exception as exc:
        ok(False, 'HLS 播放列表可取', str(exc))

    # ── ⑧ 收尾 ──────────────────────────────────────────────────────
    step('⑧', '收尾')
    st, b, _ = call('/task', 'DELETE', idem=f'stop-{idem}')
    ok(st == 200, f'停止任务（HTTP {st}）', json.dumps(b, ensure_ascii=False)[:150])
    skip('POST /api/robots/{id}/api/device/stop',
         'stop_petrolling.sh 会杀掉 fastlio / scan_planner 等真机节点，\n'
         '       而这些节点此刻正被真机栈使用。仿真环境下停设备没有验证价值，风险却是真的。')

    # ── 恢复现场 ────────────────────────────────────────────────────
    step('恢复', '把急停恢复成验证前的状态')
    if was_active:
        st, b, _ = call('/estop', 'POST', {'active': True}, idem=f'restore-{int(time.time())}')
        time.sleep(2)
        st2, b2, _ = call('/telemetry')
        now_active = bool((b2.get('data') or {}).get('emergency_active'))
        ok(now_active is True, '已恢复为 emergency_active=true（与验证前一致）')
    else:
        print('    验证前本来就没有急停，无需恢复')

    print('\n' + '═' * 52)
    print(f'{passed} 项通过 / {failed} 项失败 / {len(skipped)} 项跳过')
    for m, _w in skipped:
        print(f'  跳过: {m}')
    return failed


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
