# API 参考

下面每一段响应都是从**生产环境真实抓取**的（2026-09-02，机器人 `R30_2026_001` / 别名 `ntu-dog-00001`），
不是手写的示意。字段名、大小写、嵌套层数都可以直接照抄。

* 基址：`https://certaintyx.sg:8443`
* 鉴权：`X-API-Key: cx_...`，或 `Authorization: Bearer cx_...`
* `{robot}` 处填**别名**（如 `ntu-dog-00001`）；填机器人 ID 也认，但别名才是稳定的

---

## 目录

* [GET /v1](#get-v1) —— 自描述，免鉴权
* [GET /v1/robots](#get-v1robots) —— 机器人列表
* [GET /v1/robots/{robot}](#get-v1robotsrobot) —— 单台概览
* [GET …/telemetry](#get-telemetry) —— 遥测快照
* [GET …/position](#get-position) —— 当前位姿
* [GET …/perception](#get-perception) —— 定位/障碍状态
* [GET …/maps](#get-maps) —— 地图列表
* [GET …/maps/{name}/waypoints](#get-mapsnamewaypoints) —— 航点拓扑
* [GET …/task](#get-task) —— 任务状态
* [POST …/task](#post-task) —— 下发巡检
* [DELETE …/task](#delete-task) —— 停止任务
* [POST …/estop](#post-estop) —— 急停
* [GET /v1/status-codes](#get-v1status-codes) —— 状态码权威表，免鉴权
* [GET …/events](#get-events) —— 事件流（到达提醒）
* [控制权](#控制权)
* [任务状态词](#任务状态词) ← **必读**
* [判断定位是否就绪](#判断定位是否就绪) ← **必读**

---

## GET /v1

自描述。**免鉴权**，用来确认版本与端点清单。

```bash
curl -s "$CX_HOST/v1"
```

```json
{
  "success": true,
  "data": {
    "version": "v1",
    "robotAddress": "/v1/robots/{alias|robotId}",
    "auth": "X-API-Key: cx_... 或 Authorization: Bearer cx_...",
    "endpoints": [
      { "method": "GET",  "path": "/v1/robots/{robot}/telemetry", "desc": "电池、状态码等遥测快照" },
      { "method": "POST", "path": "/v1/robots/{robot}/task",      "desc": "下发巡检任务（需控制权）" }
    ]
  }
}
```

（`endpoints` 实际返回 12 条，这里只摘两条示意。）

---

## GET /v1/robots

你的密钥有权访问的机器人。

```json
{
  "success": true,
  "data": [
    {
      "robotId": "R30_2026_001",
      "name": "R30_2026_001",
      "robotType": "R30_v1",
      "site": "",
      "rtspPath": "cam-1c697ada870c",
      "publicIp": "155.69.183.252",
      "lanIp": "192.168.0.122",
      "location": "Singapore, SG",
      "agentVersion": "0.1.0",
      "online": true,
      "lastSeen": 1788337184949,
      "lease": { "owner": "admin", "heldSince": 1788337153477, "expiresAt": 1788337213501 },
      "alias": "ntu-dog-00001",
      "apiBase": "/v1/robots/ntu-dog-00001"
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `robotId` | 跟着硬件走。换主板/网卡会变，**不要拿它当对外地址** |
| `alias` | 对外稳定地址。管理员改硬件后把别名指向新 ID，你的代码不用动 |
| `apiBase` | 直接拼在基址后面即可，已经用了别名 |
| `online` | `false` 时后续所有调用返回 502 |
| `lastSeen` | Unix 毫秒 |
| `lease` | 当前控制权持有者。`null` = 空闲。`owner` 以 `api:` 开头表示是某把密钥持有 |
| `rtspPath` | 拉全景流用，见 [video.md](video.md) |
| `location` | 由出口 IP 离线库反查，精度只到城市级 |
| `publicIp` / `lanIp` | 用于区分现场的多台机器 |

---

## GET /v1/robots/{robot}

同上，但只返回一台（`data` 是对象而不是数组）。字段完全一致。

---

## GET …/telemetry

遥测快照。这是**信息量最大**的一个端点，机器人内部状态基本都在这里。

```json
{
  "success": true,
  "data": {
    "alarm_active_mode": 0,
    "alarm_muted": false,
    "emergency_active": false,
    "ros_available": true,
    "ros_error": "",
    "telemetry": {
      "robot_info": {
        "id": "R30_2026_001",
        "name": "R30_v1",
        "status": "待机中",
        "message": "",
        "received": true,
        "stamp": 1788337185.464737
      },
      "global_localization": {
        "received": true,
        "received_at": 1788337185.5674942,
        "stamp": 53483.614,
        "x": 11.7498675672731,
        "y": 140.0281515248416,
        "z": 0.09999999990200036,
        "yaw": -1.6545449445229374
      },
      "odom": {
        "received": true, "stamp": 53483.614,
        "linear": 0.9999999945966995, "angular": 0,
        "x": 11.7498675672731, "y": 140.0281515248416, "z": 0.0999999999, "yaw": -1.6545449445
      },
      "initial_pose":  { "received": false, "stamp": 0, "x": 0, "y": 0, "yaw": 0, "z": 0 },
      "clicked_point": { "received": false, "stamp": 0, "x": 0, "y": 0 }
    }
  }
}
```

注意嵌套是 **`data.telemetry.<话题>`**，多了一层。

| 字段 | 说明 |
|------|------|
| `emergency_active` | 机器人端**是否正在持续下发急停指令**。注意它是软件标志，不是硬件急停状态 —— 为 `true` 时机器人仍可能被别的指令（如现场遥控）推动。见 [POST …/estop](#post-estop) |
| `ros_available` | 机器人端 ROS 是否可用。`false` 时读到的都是陈旧值 |
| `robot_info.status` | **中文**状态词（`待机中` 等），给人看的，别用来做程序判断 |
| `global_localization` | 全局定位结果，就是「机器人认为自己在哪」 |
| `odom.linear` / `angular` | 当前线速度 / 角速度（m/s、rad/s） |
| `received` | 该话题**从来有没有**收到过数据。`false` 表示模块没起来 |

> ⚠️ **两个时间戳含义不同，别混用**
> * `received_at` —— **Unix 时间**（秒）。机器人收到这条数据的墙上时间。判断新鲜度用这个。
> * `stamp` —— 对 `global_localization` / `odom` 是 **ROS 时间**（自 ROS master 启动的秒数，
>   例子里 53483 ≈ 14.8 小时）。拿它和 `time.time()` 相减是没有意义的。
>
> 偏偏 `robot_info.stamp` 又是 Unix 时间。所以：**只信 `received_at`**。

---

## GET …/position

只要位姿，比 `/telemetry` 轻。

```json
{
  "success": true,
  "source": "global_localization",
  "data": {
    "x": 11.745658073933003,
    "y": 139.9719099025644,
    "z": 0.09999999990200001,
    "yaw": -1.6545449441536424,
    "timestamp": 53483.664
  }
}
```

注意 `source` 在**顶层**，不在 `data` 里面。`timestamp` 是 ROS 时间（同上）。

**定位没就绪时这个端点返回 503**，`error` 为「机器人位置信息未就绪」。
那不是故障，是还没做[定位](full-patrol.md)（第 ④ 步）。

---

## GET …/perception

```json
{ "success": true, "data": { "Location": 1, "ObsState": 0 } }
```

| 字段 | 取值 | 含义 |
|------|------|------|
| `Location` | `0` = 定位有效，`1` = 定位无效 | **注意 0 才是好的**，和直觉相反 |
| `ObsState` | `0` = 前方无障碍，`1` = 正在避障 | 机器人绕障时会变成 1 |

> ⚠️ **别用 `Location` 判断定位好了没** —— 见本文末[判断定位是否就绪](#判断定位是否就绪)。
> `ObsState` 是可靠的，可以用来解释「机器人为什么走得慢/停住了」。

---

## GET …/maps

```json
{ "success": true, "data": ["map_20260818_132055", "map_20260828_230337"] }
```

就是个字符串数组。地图名带建图时间戳，**不要硬编码** —— 重新建图后名字会变。

---

## GET …/maps/{name}/waypoints

某地图的航点拓扑。键是航点 ID（字符串形式的数字）。

```json
{
  "success": true,
  "data": {
    "1": {
      "neighbors": ["2"],
      "pose": {
        "position":    { "x": 0.006628, "y": -0.003865, "z": 0.003206 },
        "orientation": { "x": 5e-06, "y": -8.1e-05, "z": 9e-06, "w": 1 }
      }
    },
    "2": {
      "neighbors": ["1", "3"],
      "pose": {
        "position":    { "x": -0.005938, "y": 0.115527, "z": 1.094704 },
        "orientation": { "x": 0.489026, "y": 0.103743, "z": 0.069723, "w": 0.863267 }
      }
    }
  }
}
```

上面那张地图有 **87 个航点**。

| 要点 | 说明 |
|------|------|
| ID 是**字符串** | `"1"` 而不是 `1`。下发任务时也要传字符串 |
| 顺序是**字典序** | 遍历时拿到的是 `1, 10, 11, 12 … 2, 20 …`，不是数字顺序。要按编号排序得自己 `sorted(ids, key=int)` |
| `pose` 是**四元数** | `orientation` 是 `{x,y,z,w}`，不是 yaw。要 yaw 得自己换算 |
| `neighbors` | 拓扑连通关系。规划路径时可以用它判断两点是否可达 |
| 编号相邻 ≠ 位置相邻 | 但通常也确实是沿路径顺序编的；**相邻编号可能只差几十厘米** |

> 选巡检点的建议：全挑相邻编号（如 `1,2,3`）机器人几秒就跑完，看不出效果。
> 挑彼此拉开距离的点（如 `1,20,45,70`）更有意义。

---

## GET …/task

```json
{
  "success": true,
  "data": {
    "status": "idle",
    "map_name": "",
    "path": [],
    "visited": [],
    "current_target": "",
    "current_index": -1,
    "checkpoint_id": "",
    "error_code": 0,
    "message": "",
    "gait": 12290,
    "speed": 0,
    "manner": 0,
    "nav_mode": 0,
    "obs_mode": 0
  }
}
```

| 字段 | 说明 |
|------|------|
| `status` | 见下面的[状态词表](#任务状态词)，**这里有个必须知道的不对称**。云端另加了 `status_code` / `status_name` / `active` / `terminal`，见 [status.md](status.md) |
| `path` | 你下发的完整航点序列 |
| `visited` | 已经走过的航点。**下发瞬间就会包含起点** |
| `current_target` | 正在前往的航点 |
| `current_index` | 在 `path` 里的下标，空闲时是 `-1` |
| `error_code` | 非 0 表示有故障码；`0x234B`（9035）是障碍物相关 |
| `gait` | 步态码，`12290` = `0x3002` |

---

## POST …/task

下发巡检。**需要控制权**（`auto` 密钥由云端自动接管）。

```bash
curl -s -X POST "$CX_HOST/v1/robots/$CX_ROBOT/task" \
  -H "X-API-Key: $CX_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: mission-2026-09-02-07" \
  -d '{"map_name":"map_20260818_132055","path":["1","20","45","70"]}'
```

| 请求字段 | 必填 | 说明 |
|---------|------|------|
| `map_name` | ✅ | 必须是 `/maps` 里存在的名字 |
| `path` | ✅ | 航点 ID 字符串数组，**至少两个**，且都要存在于该地图 |
| `checkpoint_id` | | 默认取 `path` 最后一个 |
| `gait` / `speed` / `manner` / `nav_mode` / `obs_mode` | | 不填用机器人默认值 |

> ⚠️ **字段名是 `map_name` 和 `path`**，不是 `map` / `waypoints`。
> 写错会得到 `400`：「map_name 不能为空」或「path 至少需要两个航点」。

---

## DELETE …/task

停止当前任务。需要控制权。建议也带 `Idempotency-Key`。

```bash
curl -s -X DELETE "$CX_HOST/v1/robots/$CX_ROBOT/task" \
  -H "X-API-Key: $CX_KEY" -H "Idempotency-Key: stop-2026-09-02-07"
```

停任务**不会停设备**。机器人会停下但导航等模块仍在跑（还在耗电）。
完整收尾见 [full-patrol.md 第 ⑧ 步](full-patrol.md)。

---

## POST …/estop

急停。**不需要控制权**，但仍需 operator 权限。免控制权是刻意的：
安全动作不该排在别人的租约后面等。

> ⚠️ **请求体必须带 `{"active": true}`。**
> 机器人端读的是 `active = bool(payload.get("active", False))` ——
> 发空体 `{}` 会被解读为 `active: false`，也就是**取消急停**，和你的意图正好相反。
> 这是这套 API 目前最危险的一个坑。

```bash
# 触发急停
curl -s -X POST "$CX_HOST/v1/robots/$CX_ROBOT/estop" \
  -H "X-API-Key: $CX_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: estop-$(date +%s)" \
  -d '{"active": true}'

# 取消急停
curl -s -X POST "$CX_HOST/v1/robots/$CX_ROBOT/estop" \
  -H "X-API-Key: $CX_KEY" -H "Content-Type: application/json" \
  -d '{"active": false}'
```

可选参数：`linear_velocity`（默认 `-0.02`，急停期间持续下发的线速度）、
`publish_rate`（默认 `20.0` Hz）。一般不用改。

**急停是什么、不是什么**：它让机器人端持续以 20Hz 下发一个停止/微后退的速度指令，
直到你显式取消。它**不是硬件急停**，也**不是一把锁** —— 别的运动指令
（比如现场有人在遥控）仍然会被发出去，两者会互相竞争。
真正要确保机器人不动，得按物理急停按钮。

急停生效后 `/telemetry` 的 `emergency_active` 变 `true`；取消后变回 `false`。

---

## GET /v1/status-codes

机器人本地 SDK 的权威状态码表。**免鉴权** —— 对接方需要在登录之前就能看懂字段含义。

```bash
curl -s "$CX_HOST/v1/status-codes"
```

返回 `status`（7 个状态码 + activeCodes/terminalCodes + 写入别名）、
`errorCode`（7 个）、`location`、`obsState`，以及 `control`
（gait/speed/manner/obsMode/navMode 五项控制配置）。
每组还带 `caveat` 说明容易踩的地方。

完整解释见 [status.md](status.md)。**别把这张表抄进自己代码** —— 抄了就会漂移。

---

## GET …/events

事件流：到达航点、任务完成/失败、避障、丢定位。

```bash
curl -s  -H "X-API-Key: $CX_KEY" "$CX_HOST/v1/robots/$CX_ROBOT/events?since=21"
curl -sN -H "X-API-Key: $CX_KEY" "$CX_HOST/v1/robots/$CX_ROBOT/events?stream=1"
```

| 参数 | 说明 |
|------|------|
| `since` | 只要这个 `seq` 之后的事件。**省略时只从当下开始**，不倒带历史 |
| `stream=1` | 改为 SSE 长连接（`text/event-stream`），事件一产生就推 |

请求头 `Last-Event-ID` 也被当作 `since`（浏览器 `EventSource` 自动重连靠它）。

只读权限即可（viewer 密钥能听）。SSE 是一条长连接、只占一个请求，
所以听事件比高频轮询省得多。

完整用法与事件类型表见 [arrival-events.md](arrival-events.md)。

---

## 控制权

`auto` 模式的密钥用不到这一节 —— 云端会在每次写操作时替你接管。
只有 `explicit` 模式才需要自己管：

```bash
curl -s -X POST "$CX_HOST/api/robots/$CX_ROBOT/control/acquire" -H "X-API-Key: $CX_KEY"
curl -s -X POST "$CX_HOST/api/robots/$CX_ROBOT/control/renew"   -H "X-API-Key: $CX_KEY"
curl -s -X POST "$CX_HOST/api/robots/$CX_ROBOT/control/release" -H "X-API-Key: $CX_KEY"
```

租约 **30 秒**过期，所以要**每 10 秒续期一次**。不续期就会被自然释放。

抢占是**不对称**的：登录的人可以抢走密钥持有的租约，密钥抢不走人的。
所以现场操作员总能拿回一台正在自主跑动的机器人 —— 这是安全设计，别试图绕过。

---

## 任务状态词

**你写进去的词和读回来的词不是同一套。** 这是最容易写出死循环的地方。

机器人内部用数字码，字符串只是它的名字，而多个名字映射到同一个码：

| 数字码 | 读回来的名字 | 可以写进去的别名 |
|--------|-------------|-----------------|
| 0 | `idle` | `idle` |
| 1 | `exit_charger` | `exit_charger` |
| 2 | `nav_preprocess` | `nav_preprocess` |
| 3 | **`navigating`** | `running` / `patrolling` / `navigating` |
| 4 | `completed` | `completed` |
| 5 | `enter_charger` | `enter_charger` |
| 255 | **`paused`** | `failed` / `task_failed` / `paused` |

于是：

```python
# ✗ 永远不成立 —— 你下发的是 running，读回来是 navigating
while status == 'running': ...

# ✗ 任务失败时你永远等不到 'failed' —— 读回来是 paused
if status == 'failed': ...

# ✓ 用集合判断
ACTIVE   = {'running', 'navigating', 'patrolling', 'exit_charger', 'nav_preprocess', 'enter_charger'}
TERMINAL = {'completed', 'failed', 'paused', 'idle', 'stopped', ''}
while status in ACTIVE: ...
```

`examples/python/certaintyx.py` 里导出了 `ACTIVE_STATUS` / `TERMINAL_STATUS` 两个集合，直接用。

---

## 判断定位是否就绪

直觉做法是轮询 `/perception` 等 `Location == 0`。**这样写会死循环。**

现状（2026-09-02 实测）：机器人端计算这个字段时，拿 `time.time()`（Unix 时间）
去减 `global_localization.stamp`（ROS 时间），差值恒为十几亿秒，永远判定为「过期」，
于是 **`Location` 一直是 1，哪怕定位完全正常**。上面 `/perception` 那段真实响应就是
在定位新鲜（`received_at` 就是当下）的情况下抓到的 `Location: 1`。

可靠的判断方式，二选一：

```python
# 方式一：看定位话题的新鲜度（推荐）
import time
t = client.telemetry()['telemetry']['global_localization']
ready = t['received'] and (time.time() - t['received_at']) < 5.0

# 方式二：看 /position 通不通
# 定位未就绪时它返回 503，就绪后返回 200 + 坐标
```

用 `received_at`（Unix 时间）而**不是** `stamp`（ROS 时间）—— 这正是机器人端搞错的那一处。
