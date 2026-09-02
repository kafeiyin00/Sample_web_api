#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""不用 SDK，只用标准库，把 API 本身讲清楚。

这个脚本刻意**不**依赖 certaintyx.py —— 它要展示的就是底下那几个 HTTP 请求长什么样，
你用 Java / Go / C# 照着写也一样。只读，不会让机器人动。

    python3 01_hello_raw.py --host https://certaintyx.sg:8443 \
                            --robot ntu-dog-00001 --key cx_xxx_...
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def call(host, key, path, method='GET', body=None, idempotency_key=None, timeout=30):
    """一次 API 调用。返回 (状态码, 解析后的 JSON 或原始文本)。

    这里有意把 4xx/5xx 也当正常返回值处理 —— API 用状态码表达业务结果
    （403 无权限、409 控制权被占、503 定位未就绪），把它们当异常抛掉
    就得在 except 里再解析一遍，反而更绕。
    """
    url = f'{host}{path}'
    data = json.dumps(body).encode() if body is not None else None

    headers = {'X-API-Key': key}                    # 或 Authorization: Bearer cx_...
    if data is not None:
        headers['Content-Type'] = 'application/json'
    if idempotency_key:
        # 写操作必须带：4G 下超时重试很常见，不带的话重试会让机器人执行两次
        headers['Idempotency-Key'] = idempotency_key

    req = urllib.request.Request(url, data=data, method=method, headers=headers)
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
    except urllib.error.URLError as e:
        print(f'网络层失败（连不上云端）: {e}', file=sys.stderr)
        raise SystemExit(2)


def show(title, status, body):
    print(f'\n\033[1m{title}\033[0m  [HTTP {status}]')
    if isinstance(body, (dict, list)):
        print(json.dumps(body, ensure_ascii=False, indent=2)[:1200])
    else:
        print(body)


def main():
    ap = argparse.ArgumentParser(description='用标准库调 certaintyX 云端 API（只读）')
    ap.add_argument('--host', default='https://certaintyx.sg:8443')
    ap.add_argument('--robot', required=True, help='机器人别名，如 ntu-dog-00001')
    ap.add_argument('--key', required=True, help='API 密钥 cx_...')
    args = ap.parse_args()

    R = f'/v1/robots/{urllib.parse.quote(args.robot)}'

    # ── 1. 自描述：免鉴权，用来确认版本与端点清单 ──────────────────
    st, body = call(args.host, args.key, '/v1')
    eps = (body.get('data') or {}).get('endpoints') or []
    print(f'\033[1m可用端点 {len(eps)} 个\033[0m（GET /v1 免鉴权，随时可查）')
    for e in eps[:5]:
        print(f"  {e['method']:<6} {e['path']:<48} {e['desc']}")
    print('  ...')

    # ── 2. 概览：先确认在线，否则后面每一步都是 502 ────────────────
    st, body = call(args.host, args.key, R)
    if st == 401:
        raise SystemExit('密钥无效或已吊销（注意：401 不区分“不存在/已吊销/已过期”）')
    if st != 200:
        show('概览', st, body)
        raise SystemExit(f'读概览失败，HTTP {st}')
    info = body['data']
    show('概览', st, info)
    if not info.get('online'):
        raise SystemExit('机器人不在线 —— 先确认它开机并联网，否则后面全是 502')

    # ── 3. 遥测：注意嵌套是 data.telemetry.<话题>，多一层 ───────────
    st, body = call(args.host, args.key, f'{R}/telemetry')
    tele = (body.get('data') or {})
    topics = tele.get('telemetry') or {}
    print('\n\033[1m遥测要点\033[0m')
    print(f"  急停指令在下发: {tele.get('emergency_active')}   ← 软件标志，不是硬件急停")
    print(f"  ROS 可用: {tele.get('ros_available')}      ← false 时读到的都是陈旧值")
    print(f"  机器人自述状态: {topics.get('robot_info', {}).get('status')}（中文，给人看的）")

    # 判断定位是否新鲜：用 received_at（Unix 时间），不是 stamp（ROS 时间）
    gl = topics.get('global_localization') or {}
    if gl.get('received'):
        age = time.time() - float(gl.get('received_at') or 0)
        print(f'  定位数据年龄: {age:.1f} 秒  ← 用 received_at 算，别用 stamp')
        print(f"  定位是否可信: {age < 5.0}")
    else:
        print('  定位话题从未收到数据 —— 定位模块没起来')

    # ── 4. 位姿：定位未就绪时是 503，这是正常状态不是故障 ───────────
    st, body = call(args.host, args.key, f'{R}/position')
    if st == 503:
        print('\n\033[1m位姿\033[0m  [HTTP 503] 定位未就绪 —— 需要先做定位（第 ④ 步），见 docs/full-patrol.md')
    else:
        p = body.get('data') or {}
        print(f"\n\033[1m位姿\033[0m  x={p.get('x'):.3f} y={p.get('y'):.3f} yaw={p.get('yaw'):.3f}"
              f"  (来源 {body.get('source')})")

    # ── 5. 地图与航点 ────────────────────────────────────────────
    st, body = call(args.host, args.key, f'{R}/maps')
    maps = body.get('data') or []
    print(f'\n\033[1m地图\033[0m {maps}')
    if maps:
        m = maps[0]
        st, body = call(args.host, args.key,
                        f'{R}/maps/{urllib.parse.quote(m)}/waypoints')
        wps = body.get('data') or {}
        # 键是字符串且按字典序，要按编号看就得显式排序
        ids = sorted(wps, key=lambda k: int(k) if k.isdigit() else 0)
        print(f'  {m}: 共 {len(ids)} 个航点，按编号排序后前 8 个 = {ids[:8]}')
        if ids:
            first = wps[ids[0]]
            print(f"  航点 {ids[0]}: 邻居={first['neighbors']} "
                  f"位置={first['pose']['position']}")
            print('  （pose.orientation 是四元数 {x,y,z,w}，不是 yaw）')

    # ── 6. 任务状态：注意状态词的不对称 ─────────────────────────────
    st, body = call(args.host, args.key, f'{R}/task')
    t = body.get('data') or {}
    print(f"\n\033[1m任务\033[0m status={t.get('status')!r} "
          f"目标={t.get('current_target')!r} "
          f"已访={len(t.get('visited') or [])}/{len(t.get('path') or [])}")
    print("  提醒：下发时写 'running'，读回来是 'navigating'；失败读回来是 'paused'。")
    print("  所以 if status == 'running' 永远不成立，要用集合判断。")

    # ── 7. 探测密钥有没有写权限 ──────────────────────────────────
    #
    # 刻意用一个**故意非法**的请求体（空 map_name），这样两种密钥下都不可能
    # 真的下发任务：只读密钥在网关就被 403 挡掉，可写密钥会被机器人端以
    # 400「map_name 不能为空」拒绝。教程脚本不该有让真机动起来的副作用。
    st, body = call(args.host, args.key, f'{R}/task', method='POST',
                    body={'map_name': '', 'path': []})
    if st == 403:
        print(f"\n\033[1m写权限探测\033[0m  [HTTP 403] {body.get('error')}")
        print('  这是只读（viewer）密钥的预期结果。要下发任务得用 operator + auto 模式的密钥。')
    elif st == 400:
        print(f"\n\033[1m写权限探测\033[0m  [HTTP 400] {body.get('error')}")
        print('  400 说明鉴权过了、请求到达了机器人 —— 你这把密钥可以下发任务。')
        print('  （这次请求因为参数非法被拒，机器人没有任何动作。）')
    elif st == 409:
        print(f"\n\033[1m写权限探测\033[0m  [HTTP 409] {body.get('error')}")
        print('  你有写权限，但控制权正被别人占着（现场有人在操作）。')
    else:
        show('写权限探测', st, body)

    print('\n完成。想让机器人真的走起来，读 docs/full-patrol.md（八步，缺一不可）。')


if __name__ == '__main__':
    main()
