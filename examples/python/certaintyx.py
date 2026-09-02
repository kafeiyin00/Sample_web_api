#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CertaintyX 云端机器人 SDK（Python）
==================================
对接方用这一个类就能通过云端调机器人：下发点位巡检、读实时位置、急停。
只依赖标准库 —— 对接方的环境不保证有 requests。

    from certaintyx import RobotClient

    bot = RobotClient('https://certaintyx.sg:8443', 'ntu-dog-001', 'cx_xxxx_...')
    print(bot.position())                       # 实时位置
    maps = bot.maps()                           # 可用地图
    wps  = bot.waypoints(maps[0])               # 该地图的航点
    bot.start_patrol(maps[0], ['1', '2', '3'])  # 按点位巡检
    for st in bot.watch_task():                 # 跟踪进度直到结束
        print(st['status'], st['current_target'])

设计要点（都是踩过的坑，改之前先看 docs/HANDOFF.md §6.3）：

* **写操作要控制权**。密钥签发时选 `auto` 的话，网关会在每次写时代为接管，
  对接方不必自己实现续期循环；停止写入约 30 秒后自然释放。选 `explicit` 则要自己
  调 acquire 并续期。只读密钥（`none`）任何写操作直接 403。
* **写操作默认带 `Idempotency-Key`**。4G 下超时重试极常见，不带幂等键重试会重复下发
  任务。本 SDK 自动生成，重试时**复用同一个键**——这正是幂等的意义所在，
  不要每次重试都换新键。
* **429 自动退避**。网关按密钥令牌桶限流（默认 5 rps），SDK 读 `Retry-After` 后重试。
* **急停不需要控制权**，但仍需 operator 权限。
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Iterator


#: 任务仍在执行中的状态词（下发时写 running，读回来是 navigating）
ACTIVE_STATUS = frozenset({'running', 'navigating', 'patrolling',
                           'exit_charger', 'nav_preprocess', 'enter_charger'})
#: 任务已结束的状态词（failed 读回来是 paused）
TERMINAL_STATUS = frozenset({'completed', 'failed', 'paused', 'idle', 'stopped', ''})


class RobotError(RuntimeError):
    """调用失败。`status` 是 HTTP 状态码，便于对接方区分 403/404/429。"""

    def __init__(self, message: str, status: int = 0, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class RobotClient:
    def __init__(self, base_url: str, robot: str, api_key: str, *,
                 timeout: float = 30.0, verify_tls: bool = True,
                 max_retries: int = 3):
        """
        base_url : https://certaintyx.sg:8443
        robot    : 别名（推荐，地址稳定）或机器人 ID
        api_key  : cx_<keyId>_<secret>
        verify_tls: 自签证书环境下可设 False（生产不建议）
        """
        self.base = base_url.rstrip('/')
        self.robot = robot
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self._ctx = None
        if not verify_tls:
            self._ctx = ssl.create_default_context()
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    # ── 传输层 ───────────────────────────────────────────────────────────────
    @property
    def robot_base(self) -> str:
        return f'{self.base}/v1/robots/{urllib.parse.quote(self.robot)}'

    def _request(self, method: str, path: str, payload: Any = None,
                 idempotency_key: str | None = None) -> Any:
        url = f'{self.robot_base}{path}'
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {'X-API-Key': self.api_key}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        # 幂等键在重试之间必须保持不变，否则重试会被当成新的一次下发
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key

        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            req = urllib.request.Request(url, data=body, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as r:
                    raw = r.read().decode('utf-8', 'replace')
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                raw = e.read().decode('utf-8', 'replace')
                try:
                    parsed = json.loads(raw)
                except Exception:
                    parsed = raw
                if e.code == 429 and attempt < self.max_retries - 1:
                    # 限流：按服务端给的 Retry-After 退避，别自作主张
                    wait = float(e.headers.get('Retry-After') or 1.0)
                    time.sleep(min(wait, 10.0))
                    last_err = e
                    continue
                msg = parsed.get('error') if isinstance(parsed, dict) else str(parsed)
                raise RobotError(f'{method} {path} 失败 [{e.code}]: {msg}', e.code, parsed)
            except urllib.error.URLError as e:
                # 网络抖动（4G 常见）：退避重试。写操作因为带了同一个幂等键，重试安全
                last_err = e
                if attempt < self.max_retries - 1:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise RobotError(f'{method} {path} 网络失败: {e}', 0, None)
        raise RobotError(f'{method} {path} 重试耗尽: {last_err}', 0, None)

    def _request_raw_once(self, method: str, path: str, payload: Any = None) -> Any:
        """
        只发一次、不做 429 退避重试。给限流测试用——正常业务请用 _request，
        它会按 Retry-After 退避，这才是对接方该有的行为。
        """
        saved, self.max_retries = self.max_retries, 1
        try:
            return self._request(method, path, payload)
        finally:
            self.max_retries = saved

    @staticmethod
    def _data(resp: Any) -> Any:
        if isinstance(resp, dict) and 'data' in resp:
            return resp['data']
        return resp

    # ── 只读 ─────────────────────────────────────────────────────────────────
    def telemetry(self) -> dict:
        """电池、状态码等遥测快照。"""
        return self._data(self._request('GET', '/telemetry'))

    def position(self) -> dict:
        """实时位置 {x, y, z, yaw, timestamp}。机器人定位未就绪时抛 RobotError(503)。"""
        return self._data(self._request('GET', '/position'))

    def perception(self) -> dict:
        return self._data(self._request('GET', '/perception'))

    def maps(self) -> list:
        return self._data(self._request('GET', '/maps')) or []

    def waypoints(self, map_name: str) -> dict:
        """该地图的航点 {id: {pose, neighbors}}。巡检路径就从这里的 id 里选。"""
        return self._data(self._request(
            'GET', f'/maps/{urllib.parse.quote(map_name)}/waypoints')) or {}

    def task(self) -> dict:
        """当前任务状态：status / current_target / current_index / visited。"""
        return self._data(self._request('GET', '/task'))

    # ── 写操作 ───────────────────────────────────────────────────────────────
    def start_patrol(self, map_name: str, path: list[str], *,
                     idempotency_key: str | None = None, **options) -> dict:
        """
        按点位巡检。path 是航点 id 列表，至少两个，且必须都存在于该地图。
        options 可传 gait / speed / manner / obs_mode / nav_mode / checkpoint_id。
        """
        if len(path) < 2:
            raise ValueError('path 至少需要两个航点')
        payload = {'map_name': map_name, 'path': list(path)}
        payload.update(options)
        return self._data(self._request(
            'POST', '/task', payload,
            idempotency_key=idempotency_key or f'patrol-{uuid.uuid4().hex[:16]}'))

    def stop_task(self, *, idempotency_key: str | None = None) -> dict:
        return self._data(self._request(
            'DELETE', '/task', None,
            idempotency_key=idempotency_key or f'stop-{uuid.uuid4().hex[:16]}'))

    def estop(self, *, idempotency_key: str | None = None) -> dict:
        """
        急停：让机器人持续收到停止指令直到你显式取消。
        不需要控制权租约（安全动作不该排队），但仍需 operator 权限。

        请求体**必须**带 `active: true`。机器人端是
        `active = bool(payload.get("active", False))` ——
        发空体 `{}` 会被解读为 active=False，也就是**取消急停**，
        与调用者的意图正好相反。
        """
        return self._data(self._request(
            'POST', '/estop', {'active': True},
            idempotency_key=idempotency_key or f'estop-{uuid.uuid4().hex[:16]}'))

    def clear_estop(self, *, idempotency_key: str | None = None) -> dict:
        """取消急停。取消之前机器人会一直被推向停止。"""
        return self._data(self._request(
            'POST', '/estop', {'active': False},
            idempotency_key=idempotency_key or f'estop-clear-{uuid.uuid4().hex[:16]}'))

    # ── 机器人概览 / 透传通道 ─────────────────────────────────────────────────
    def info(self) -> dict:
        """
        机器人概览：online / rtspPath / alias / robotId / lanIp / location 等。
        顺带缓存 robotId —— 透传通道只认 robotId，不认别名（见下）。
        """
        d = self._data(self._request('GET', ''))
        if isinstance(d, dict) and d.get('robotId'):
            self._robot_id = d['robotId']
        return d

    @property
    def robot_id(self) -> str:
        if not getattr(self, '_robot_id', ''):
            self.info()
        return self._robot_id

    def _passthrough(self, method: str, path: str, payload: Any = None,
                     timeout: float | None = None, idempotency_key: str | None = None) -> Any:
        """
        透传通道：调 /v1 没暴露的机器人端接口（设备启停、定位、话题就绪等）。

        两点必须知道：
        * **只认 robotId，不认别名**。/v1 两者都收，这条路径传别名会得到 502
          「机器人不在线」——很容易误判成机器人真的掉线了。这里自动用 robot_id。
        * **不是冻结契约**。/v1 那 9 条路径是对外承诺、内部怎么改都不动；
          这里是机器人 Flask 的原始路径，随版本可能变。能用 /v1 就别用这条。
        """
        url = (f'{self.base}/api/robots/{urllib.parse.quote(self.robot_id)}'
               f'/api{path}')
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {'X-API-Key': self.api_key}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout,
                                        context=self._ctx) as r:
                raw = r.read().decode('utf-8', 'replace')
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode('utf-8', 'replace')
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
            msg = parsed.get('error') or parsed.get('message') \
                if isinstance(parsed, dict) else str(parsed)
            raise RobotError(f'{method} {path} 失败 [{e.code}]: {msg}', e.code, parsed)
        except urllib.error.URLError as e:
            raise RobotError(f'{method} {path} 网络失败: {e}', 0, None)

    # ── 系统初始化 / 收尾（都在透传通道上）───────────────────────────────────
    def device_start(self) -> str:
        """启动设备（导航等脚本）。返回 task_id，用它轮询启动状态。"""
        r = self._passthrough('POST', '/device/start')
        if not r.get('success'):
            raise RobotError(f"设备启动失败: {r.get('message')}", 0, r)
        return r.get('task_id', '')

    def device_start_status(self, task_id: str) -> dict:
        return self._passthrough('GET', f'/device/start_status?task_id={urllib.parse.quote(task_id)}')

    def device_stop(self) -> str:
        r = self._passthrough('POST', '/device/stop')
        return r.get('task_id', '')

    def device_stop_status(self, task_id: str) -> dict:
        return self._passthrough('GET', f'/device/stop_status?task_id={urllib.parse.quote(task_id)}')

    def wait_device(self, task_id: str, *, starting: bool = True,
                    timeout: float = 180.0, interval: float = 2.0) -> dict:
        """轮询设备启停脚本直到结束。completed 只表示跑完了，还要看 result_success。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = self.device_start_status(task_id) if starting else self.device_stop_status(task_id)
            if st.get('completed'):
                if not st.get('result_success', True):
                    raise RobotError(f"设备脚本执行失败: {st.get('message')}", 0, st)
                return st
            time.sleep(interval)
        raise RobotError(f'等待设备脚本超时（{timeout}s）')

    def topic_ready(self, topic: str = '/global_localization') -> bool:
        """某个 ROS 话题是否已有数据。

        ⚠️ **别拿 /global_localization 当 localize() 的前置条件** ——
        它是 localize() 的产物：你得先告诉机器人在哪个航点附近，它才开始收敛
        并产出这个话题。刚开机的机器人上等它只会等到超时。
        """
        r = self._passthrough('GET', f'/device/topic_ready?topic={urllib.parse.quote(topic)}')
        d = r.get('data') or {}
        return bool(d.get('ready', r.get('ready', False)))

    def wait_topic(self, topic: str = '/global_localization',
                   *, timeout: float = 120.0, interval: float = 2.0) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if self.topic_ready(topic):
                    return True
            except RobotError:
                pass
            time.sleep(interval)
        raise RobotError(f'等待话题 {topic} 超时（{timeout}s）')

    def localize(self, map_name: str, node_id: str, pose: dict | None = None,
                 *, timeout: float = 60.0) -> dict:
        """
        定位：告诉机器人「你在 node_id 这个航点附近」，它据此收敛出全局位姿。

        注意因果关系：/global_localization 是这一步的**产物**，不是前提。
        调用它之前去等 /global_localization 出数据，在刚开机的机器人上会一直等到超时。

        服务端内部：发 /relocalization（地图 pcd + 初值）→ 同步等一条新的
        /global_localization（最长 20 秒）→ 与初值比对，水平偏差超 3 米判失败。
        所以客户端超时必须给足（这里默认 60s），否则请求会被自己提前掐断，
        看起来像「定位失败」其实是超时太短。

        pose 省略时服务端自动取 node_id 那个航点的位姿 —— 一般不用传。
        只有需要给一个不落在航点上的初值时才手工传。
        """
        payload: dict = {'map_name': map_name, 'node_id': node_id}
        if pose is not None:
            payload['pose'] = pose
        r = self._passthrough('POST', '/localization/execute', payload, timeout=timeout)
        if not r.get('success'):
            # reason=timeout（定位模块没起来/点云对不上）还是
            # reason=drift_exceeded（node_id 给错了），失败原因差别很大
            data = r.get('data') or {}
            reason = data.get('reason') or ''
            detail = f"（reason={reason}，drift={data.get('drift')}m）" if reason else ''
            raise RobotError(f"定位失败: {r.get('error') or r.get('message')}{detail}", 0, r)
        return r.get('data') or {}

    # ── 事件流（到达提醒等）─────────────────────────────────────────────────
    def events(self, since: int | None = None, limit: int = 200) -> dict:
        """取事件。返回 {'events': [...], 'seq': N, 'nextSince': N}。

        since 省略时只从当下开始（不倒带历史），先记下返回的 seq，
        之后每次带上 nextSince 就能保证一条不漏。
        """
        q = f'?since={int(since)}' if since is not None else ''
        return self._data(self._request('GET', f'/events{q}')) or {}

    def watch_events(self, *, since: int | None = None, interval: float = 1.0,
                     timeout: float | None = None,
                     types: set[str] | None = None) -> Iterator[dict]:
        """持续产出事件，直到超时或调用方 break。

        用轮询而不是 SSE：标准库没有 SSE 客户端，而这里只依赖标准库。
        想要真正的推送（延迟更低、不吃限流额度）就直接连
        `GET /v1/robots/{robot}/events?stream=1`，那是标准的 text/event-stream。

        **断线不丢事件的关键**：内部始终用服务端给的 nextSince 续接，
        而不是「从现在开始听」。所以中间断几秒也能补回来。
        """
        cursor = since
        t0 = time.time()
        if cursor is None:
            cursor = int(self.events().get('seq') or 0)
        while timeout is None or time.time() - t0 < timeout:
            batch = self.events(since=cursor)
            for e in batch.get('events') or []:
                cursor = max(cursor, int(e.get('seq') or 0))
                if types and e.get('type') not in types:
                    continue
                yield e
            nxt = batch.get('nextSince')
            if isinstance(nxt, int):
                cursor = max(cursor, nxt)
            time.sleep(interval)

    def wait_arrival(self, waypoint: str | None = None, *, since: int | None = None,
                     timeout: float = 600.0, interval: float = 1.0) -> dict:
        """等一条「到达」事件。waypoint 为 None 表示任意航点。

        任务在此期间失败会直接抛 RobotError —— 否则就会一直等到超时，
        而真正的原因（避障失败、规划失败）早就在事件里了。
        """
        for e in self.watch_events(since=since, interval=interval, timeout=timeout,
                                   types={'waypoint_reached', 'task_failed', 'task_completed'}):
            if e['type'] == 'task_failed':
                raise RobotError(
                    f"任务失败: errorCode={e['data'].get('errorHex')}", 0, e)
            if e['type'] == 'waypoint_reached' and (
                    waypoint is None or str(e['data'].get('waypoint')) == str(waypoint)):
                return e
            if e['type'] == 'task_completed' and waypoint is not None:
                raise RobotError(f'任务已结束但从未到达航点 {waypoint}', 0, e)
        raise RobotError(f'等待到达超时（{timeout}s）')

    def status_codes(self) -> dict:
        """状态码/错误码权威表（免鉴权）。与机器人本地 SDK 同源。

        拿它来把 status_code / error_code 翻成人能看的名字，
        而不是在你自己的代码里抄一份表 —— 抄一份就会漂移。
        """
        url = f'{self.base}/v1/status-codes'
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as r:
            return (json.loads(r.read().decode('utf-8')) or {}).get('data') or {}

    # ── 全景视频 ─────────────────────────────────────────────────────────────
    def rtsp_url(self) -> str:
        """全景 RTSP 拉流地址（只读，无需凭据）。"""
        host = urllib.parse.urlparse(self.base).hostname or ''
        return f'rtsp://{host}:8554/{self.info().get("rtspPath") or "camera"}'

    def hls_url(self) -> str:
        """全景 HLS 地址，浏览器里可直接播（延迟比 RTSP 高）。"""
        return f'{self.base}/hls/{self.info().get("rtspPath") or "camera"}/index.m3u8'

    def snapshot(self, out_path: str, *, timeout: float = 40.0) -> str:
        """
        从 RTSP 抓一帧全景存成图片。需要本机有 ffmpeg。
        用 TCP 传输：UDP 在很多企业网里会被丢，表现为一直抓不到帧。
        """
        import shutil
        import subprocess
        if not shutil.which('ffmpeg'):
            raise RobotError('本机没有 ffmpeg，无法抓帧；可改用 hls_url() 在浏览器里看')
        cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error',
               '-rtsp_transport', 'tcp', '-i', self.rtsp_url(),
               '-frames:v', '1', '-y', out_path]
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if r.returncode != 0:
            raise RobotError(f"抓帧失败: {r.stderr.decode('utf-8', 'replace')[:200]}")
        return out_path

    # ── 便利封装 ─────────────────────────────────────────────────────────────
    def watch_task(self, *, interval: float = 1.0, timeout: float = 600.0) -> Iterator[dict]:
        """
        轮询任务状态直到进入终止态，逐次 yield 状态。
        间隔别设太小：网关按密钥限流，默认 5 rps。

        ⚠️ 状态词是**写入与读回不对称**的（机器人端把状态存成数字码再渲染回名字）：
            下发时写 running  → 读回来是 "navigating"
            失败时写 failed   → 读回来是 "paused"
        所以判断「还在跑」要用 ACTIVE_STATUS，判断「结束了」要用 TERMINAL_STATUS，
        不要直接比 == 'running'，那样永远不成立。

        云端现在会在响应里直接给 `terminal` / `active` 布尔字段（由状态码算出），
        有就优先用它 —— 这样连状态词集合都不必自己维护。老网关没有这个字段时
        回落到 TERMINAL_STATUS。
        """
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = self.task()
            yield st
            done = st.get('terminal')
            if done if isinstance(done, bool) else (st.get('status') in TERMINAL_STATUS):
                return
            time.sleep(interval)
        raise RobotError(f'等待任务结束超时（{timeout}s）')
