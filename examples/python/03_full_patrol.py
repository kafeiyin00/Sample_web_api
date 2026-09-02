#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云端 SDK 完整巡检示例（八步齐全）
=================================
教程正文见 ../../docs/full-patrol.md。这份是可直接跑的完整代码。

    # 对着 Gazebo 仿真跑（跳过设备启动与定位，仿真已提供定位）
    python3 03_full_patrol.py --robot ntu-dog-00001 --key cx_xxx_... --sim

    # 对着真机跑（八步全走）
    python3 03_full_patrol.py --robot ntu-dog-00001 --key cx_xxx_...

流程：
  ① 连接与体检 →（①b 读状态：状态码表与隐性状态）→ ② 启动设备 → ③ 等启动完成
  → ④ 定位（告诉机器人在哪个航点附近，它据此收敛出全局定位）
  → ⑤ 确认定位可用 → ⑥ 下发巡检 → ⑦ 跟踪进度并抓全景 → ⑧ 收尾（停任务 + 停设备）

⑧ 放在 finally 里：任何一步出异常，设备都要停掉，否则机器人会一直空转。

⚠️ 这个脚本会让**真实的机器人走起来**。跑之前确认：
   现场没人站在它的路径上、周围没有台阶或障碍、有人能随时按下物理急停。
   第一次接触建议先在仿真里跑（--sim）。
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

# certaintyx.py 就在同目录。实际项目里把它拷进你的包即可 —— 它只依赖标准库。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from certaintyx import ACTIVE_STATUS, RobotClient, RobotError  # noqa: E402


def step(n: str, title: str) -> None:
    print(f'\n===== {n} {title} ' + '=' * max(0, 44 - len(title)))


def pick_waypoints(wps: dict, count: int, min_leg: float = 4.0) -> list[str]:
    """
    挑彼此拉开距离的航点。
    相邻编号的航点可能只差几十厘米，全挑相邻的会让机器人几秒就跑完，看不出效果。
    """
    def xy(k):
        p = wps[k]['pose']['position']
        return float(p['x']), float(p['y'])

    ordered = sorted(wps, key=lambda s: (len(s), s))
    picked = [ordered[0]]
    for k in ordered[1:]:
        if len(picked) >= max(2, count):
            break
        lx, ly = xy(picked[-1])
        cx, cy = xy(k)
        if math.hypot(cx - lx, cy - ly) >= min_leg:
            picked.append(k)
    return picked if len(picked) >= 2 else ordered[:2]


def main() -> int:
    ap = argparse.ArgumentParser(description='云端 SDK 完整巡检示例')
    ap.add_argument('--base', default='https://certaintyx.sg:8443')
    ap.add_argument('--robot', required=True, help='机器人别名，例如 ntu-dog-00001')
    ap.add_argument('--key', required=True, help='API 密钥 cx_<keyId>_<secret>')
    ap.add_argument('--sim', action='store_true',
                    help='Gazebo 仿真模式：跳过 ②③⑤（仿真器已直接提供定位）')
    ap.add_argument('--points', type=int, default=4, help='巡检点位数量（默认 4）')
    ap.add_argument('--start-node', default='',
                    help='机器人当前所在（或最接近）的航点 ID，作为定位初值；不给则用第一个航点')
    ap.add_argument('--snapshot', default='pano.jpg', help='全景截图保存路径')
    args = ap.parse_args()

    bot = RobotClient(args.base, args.robot, args.key)
    device_task = ''          # 记下设备启动的 task_id，收尾时判断要不要停设备

    try:
        # ── ① 连接与体检 ────────────────────────────────────────────────────
        step('①', '连接与体检')
        info = bot.info()
        print(f"  机器人 : {info['robotId']}（别名 {info.get('alias')}）")
        print(f"  在线   : {info['online']}")
        print(f"  位置   : {info.get('location')}  内网 {info.get('lanIp')}")
        print(f"  推流路径: {info.get('rtspPath')}")
        if not info['online']:
            print('  机器人不在线：先确认它已开机并联网，否则后面每一步都会 502')
            return 1

        # ── ①b 读状态：先把权威状态码表拉下来，别在代码里抄表 ────────────────
        step('①b', '读状态（状态码表 + 三个容易漏的隐性状态）')
        codes = bot.status_codes()
        print(f"  /v1/status-codes: {len(codes['status']['values'])} 个状态码、"
              f"{len(codes['errorCode']['values'])} 个错误码（与机器人本地 SDK 同源）")
        print('  ' + codes['status']['caveat'].replace('\n', ' '))

        tele = bot.telemetry()
        # 这三个在 telemetry 的 data 顶层，最容易被忽略，
        # 但它们能解释掉大部分「明明下发成功了却不动」
        print(f"  急停指令在下发: {tele.get('emergency_active')}"
              '   ← true 时机器人端在以 20Hz 持续下发停止指令')
        print(f"  ROS 可用       : {tele.get('ros_available')}"
              '   ← false 时读到的都是陈旧值')
        for topic in ('global_localization', 'odom'):
            t = (tele.get('telemetry') or {}).get(topic) or {}
            print(f"  话题 {topic:<20} received={t.get('received')}")
        if tele.get('emergency_active'):
            print('  ⚠️ 急停正在下发，巡检指令会和它竞争。'
                  '先取消（POST /estop {"active": false}）再继续。')

        # ── ② 系统初始化：启动设备 ──────────────────────────────────────────
        step('②', '系统初始化（启动设备）')
        if args.sim:
            print('  [仿真模式] 跳过：Gazebo 里导航与定位由仿真器提供')
        else:
            device_task = bot.device_start()
            print(f'  启动脚本已下发，task_id={device_task}')

            # ── ③ 等启动完成 ────────────────────────────────────────────────
            step('③', '等待启动完成')
            # completed 只表示脚本跑完了，还要看 result_success 才知道成没成功；
            # wait_device 已经帮忙判断，失败会抛 RobotError
            bot.wait_device(device_task, starting=True, timeout=180)
            print('  设备启动完成')

        # ── 取地图与航点（④⑥ 都要用） ──────────────────────────────────────
        maps = bot.maps()
        map_name, wps = '', {}
        for m in maps:
            w = bot.waypoints(m)
            if len(w) >= 2:
                map_name, wps = m, w
                break
        if not map_name:
            print('  没有任何带航点的地图，无法巡检')
            return 1
        print(f'\n  使用地图 {map_name}（{len(wps)} 个航点）')

        # ── ④ 定位：告诉机器人它在哪个航点附近 ──────────────────────────────
        #
        # 因果关系别搞反：/global_localization 是这一步的**产物**，不是前提。
        # 先给一个航点当初值，机器人才开始收敛并产出全局定位。
        # 所以这里**不**在调用之前去等 /global_localization —— 刚开机的机器人上
        # 那个话题还不会有数据，等它只会等到超时。
        step('④', '定位（告诉机器人它在哪个航点附近）')
        if args.sim:
            print('  [仿真模式] 跳过：仿真器直接发布 /global_localization')
        else:
            start_node = args.start_node or sorted(wps, key=lambda s: (len(s), s))[0]
            if start_node not in wps:
                print(f'  航点 {start_node} 不在地图里')
                return 1
            print(f'  初值取航点 {start_node} —— 必须是机器人**真实所在**（或最接近）的点位；'
                  '给错了要么偏差超 3m 被判失败，要么收敛到错的位置')
            # 服务端内部：发 /relocalization → 同步等一条新的 /global_localization
            #（最长 20s）→ 与初值比对，偏差超 3m 判失败。所以客户端超时必须给足。
            # pose 省略即可，服务端会自己取该航点的位姿。
            r = bot.localize(map_name, start_node, timeout=60)
            print(f"  定位成功（与初值偏差 {r.get('drift')} m，阈值 {r.get('threshold')} m）")

        # ── ⑤ 确认定位可用 ──────────────────────────────────────────────────
        step('⑤', '确认定位可用')
        # 定位好之前 /position 是 503；能读到坐标就说明收敛了
        pos = bot.position()
        print(f"  当前位置 x={pos['x']:.2f} y={pos['y']:.2f} yaw={pos['yaw']:.2f}")
        gl = bot.telemetry()['telemetry']['global_localization']
        age = time.time() - float(gl.get('received_at') or 0)
        print(f'  定位数据年龄 {age:.1f}s（用 received_at 算，不是 stamp）')

        # 感知状态：云端已经把反直觉的 0/1 翻成布尔了
        pc = bot.perception()
        print(f"  perception: Location={pc.get('Location')} → "
              f"location_valid={pc.get('location_valid')}"
              '   ← 原始值 0 才是「有效」，用布尔字段就不会搞反')
        print(f"              ObsState={pc.get('ObsState')} → avoiding={pc.get('avoiding')}")
        if not pc.get('location_valid'):
            print('  注意：location_valid=false 是已知问题（该字段恒为无效），'
                  '以上面的定位年龄为准')

        # ── ⑥ 下发巡检 ──────────────────────────────────────────────────────
        step('⑥', '下发巡检任务')
        path = pick_waypoints(wps, args.points)
        total = sum(
            math.hypot(
                wps[path[i + 1]]['pose']['position']['x'] - wps[path[i]]['pose']['position']['x'],
                wps[path[i + 1]]['pose']['position']['y'] - wps[path[i]]['pose']['position']['y'])
            for i in range(len(path) - 1))
        print(f'  路径: {" → ".join(path)}  （总长约 {total:.1f} m）')
        bot.start_patrol(map_name, path)
        print('  已下发')

        # ── ⑦ 跟踪进度 + 取全景 ─────────────────────────────────────────────
        step('⑦', '跟踪进度并取全景图')
        print(f'  RTSP: {bot.rtsp_url()}')
        print(f'  HLS : {bot.hls_url()}')
        grabbed = False
        last_target = None
        warned_error = False
        # 状态判断用云端给的 active / terminal 布尔字段，不要比 == 'running'
        #（写进去是 running、读回来是 navigating，那个判断永远不成立）
        for st in bot.watch_task(interval=1.5, timeout=600):
            prog = st.get('progress') or {}
            # error_code 与 status 是**正交**的：还在导航的同时也可能已经有错误码
            if st.get('error_code') and not warned_error:
                warned_error = True
                print(f"  ⚠️ 出现错误码 {st.get('error_hex')} "
                      f"{st.get('error_name')}（{st.get('error_text')}）"
                      f" —— 注意它与 status={st.get('status_name')} 是正交的")
            tgt = st.get('current_target')
            if tgt and tgt != last_target:
                last_target = tgt
                p = bot.position()
                print(f"  → 前往 {tgt}  "
                      f"[{st.get('status_name')} active={st.get('active')} "
                      f"进度 {prog.get('visited')}/{prog.get('total')}]  "
                      f"位置 x={p['x']:.2f} y={p['y']:.2f}")
                # 在路上抓一张全景，证明画面与位置对得上
                if not grabbed:
                    try:
                        bot.snapshot(args.snapshot)
                        print(f'  已保存全景截图 {args.snapshot}')
                        grabbed = True
                    except RobotError as e:
                        print(f'  抓帧失败（不影响巡检）: {e}')
        # 结束时把语义字段一起打出来：光看 status 字符串分不清是完成还是失败
        print(f"  任务结束: status={st.get('status')} "
              f"({st.get('status_code')} {st.get('status_name')} / {st.get('status_text')})")
        print(f"           terminal={st.get('terminal')} "
              f"error={st.get('error_hex')} {st.get('error_name')}")
        print(f"           已访问 {st.get('visited')}")
        if st.get('status_code') == 255:
            print('  ⚠️ 255 既是「暂停」也是「失败」—— 具体原因看 error_code，'
                  '0x234B 是避障失败、0x234C 是规划失败')

        if not grabbed:
            try:
                bot.snapshot(args.snapshot)
                print(f'  已保存全景截图 {args.snapshot}')
            except RobotError as e:
                print(f'  抓帧失败: {e}')
        return 0

    except RobotError as e:
        print(f'\n[错误] HTTP {e.status}: {e}')
        try:
            bot.estop()          # 出问题先让机器人停住，急停不需要控制权
            print('  已发送急停')
        except RobotError:
            pass
        return 1
    except KeyboardInterrupt:
        print('\n[中断] 正在停止机器人 ...')
        try:
            bot.estop()
        except RobotError:
            pass
        return 130
    finally:
        # ── ⑧ 收尾 ──────────────────────────────────────────────────────────
        step('⑧', '收尾（停任务 + 停设备）')
        try:
            bot.stop_task()
            print('  巡检任务已停止')
        except RobotError as e:
            print(f'  停止任务失败: {e}')
        if device_task:          # 只有我们启动过设备才需要停
            try:
                sid = bot.device_stop()
                bot.wait_device(sid, starting=False, timeout=180)
                print('  设备已停止')
            except RobotError as e:
                print(f'  停止设备失败: {e}')
        elif args.sim:
            print('  [仿真模式] 未启动过设备，无需停止')


if __name__ == '__main__':
    sys.exit(main())
