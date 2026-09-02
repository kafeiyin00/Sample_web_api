# 读机器人状态

机器人的状态码定义在它自己的本地 SDK 里（`RobotStatus` / `ErrorCode` 两个枚举）。
你不用去翻那份 Python 源码 —— **云端把权威表原样给你**：

```bash
curl -s "$CX_HOST/v1/status-codes"      # 免鉴权
```

拿它把裸数值翻成人能看的东西，而**不要在自己代码里抄一份表** —— 抄一份就会漂移。

---

## 一、状态怎么读

三个端点，按需要的详细程度选：

| 端点 | 给什么 | 什么时候用 |
|------|--------|-----------|
| `GET …/task` | 任务状态、走到哪了、错误码 | 最常用。**判断任务进展看这个** |
| `GET …/perception` | 定位有效性、是否在避障 | 解释「为什么走得慢/停住了」 |
| `GET …/telemetry` | 全部内部状态（含急停、ROS 是否可用、定位新鲜度） | 排查时用，信息量最大 |

### `GET …/task` 现在返回什么

原始字段之外，云端**额外加了语义字段**（标 ★ 的），省掉你自己查表：

```json
{
  "success": true,
  "data": {
    "status": "navigating",          "status_code": 3,          ★
                                     "status_name": "NAVIGATING", ★
                                     "status_text": "导航中",     ★
                                     "active": true,             ★
                                     "terminal": false,          ★
    "error_code": 0,                 "error_name": "SUCCESS",    ★
                                     "error_text": "无错误",      ★
                                     "error_hex": "0x0000",      ★
    "map_name": "map_20260818_132055",
    "path": ["85", "82", "5"],
    "visited": ["85", "82"],         "progress": {"visited": 2, "total": 3}, ★
    "current_target": "5",
    "current_index": 2,
    "gait": 12290,                   "gait_name": "FLAT",        ★
    "speed": 0,                      "speed_name": "NORMAL",     ★
    "manner": 0,                     "manner_name": "FORWARD",   ★
    "nav_mode": 0,                   "nav_mode_name": "STRAIGHT",★
    "obs_mode": 0,                   "obs_mode_name": "ENABLED"  ★
  }
}
```

原字段一个都没动 —— `/v1` 是冻结契约，只增不改。

---

## 二、状态码表

### 任务状态 `status` / `status_code`

| 码 | 名字 | 含义 | 算「进行中」吗 |
|----|------|------|--------------|
| 0 | `IDLE` | 空闲 | 否（terminal） |
| 1 | `EXIT_CHARGER` | 正在离开充电桩 | 是（active） |
| 2 | `NAV_PREPROCESS` | 导航预处理 | 是 |
| 3 | `NAVIGATING` | 导航中 | 是 |
| 4 | `COMPLETED` | 任务完成 | 否 |
| 5 | `ENTER_CHARGER` | 正在回充电桩 | 是 |
| 255 | `PAUSED` | 已暂停 / 任务失败 | 否 |

> ⚠️ **写进去的词和读回来的词不一样。** 下发时写 `running`，读回来是 `navigating`；
> 任务失败读回来是 `paused` 而不是 `failed`（因为它们映射到同一个数值码）。
>
> 所以 `if status == 'running'` **永远不成立**。用云端给的布尔字段：
>
> ```python
> if t['active']:      ...   # 任务还在推进
> if t['terminal']:    ...   # 任务已结束（完成/失败/空闲都算）
> ```

### 错误码 `error_code`

| 码 | 名字 | 含义 |
|----|------|------|
| `0x0000` | `SUCCESS` | 无错误 |
| `0x2341` | `TASK_EXECUTING` | 已有任务在执行 |
| `0x234B` | `OBSTACLE_FAILURE` | 避障失败 |
| `0x234C` | `PLANNING_FAILURE` | 路径规划失败 |
| `0x2352` | `MAP_NOT_LOADED` | 地图未加载 |
| `0x2353` | `CHECKPOINT_NOT_EXISTS` | 航点不存在 |
| `0xFFFF` | `INVALID_COMMAND` | 无效指令 |

> ⚠️ **`error_code` 与 `status` 是正交的。** `status=navigating` 的同时
> `error_code` 完全可能非 0（比如正在避障失败）。判断「一切正常」要两个都看：
>
> ```python
> healthy = t['active'] and t['error_code'] == 0
> ```

### 感知 `GET …/perception`

```json
{ "success": true,
  "data": { "Location": 1, "ObsState": 0,
            "location_valid": false, "avoiding": false,   ★
            "location_text": "定位无效", "obs_text": "前方无障碍" } }
```

| 字段 | 取值 | 含义 |
|------|------|------|
| `Location` | `0` 有效 / `1` 无效 | **0 才是好的**，和直觉相反。用 ★ 的 `location_valid` 就不会搞反 |
| `ObsState` | `0` 无障碍 / `1` 避障中 | 用 ★ 的 `avoiding` |

> ⚠️ **别用 `Location` 判断定位就绪。** 它目前恒为 1（机器人端拿 Unix 时间减
> ROS 时间戳做新鲜度判断），哪怕定位完全正常。可靠做法见下。

### 控制配置（下发任务时可选，不是状态）

| 字段 | 取值 |
|------|------|
| `gait` | `0x3002` 平地 / `0x3003` 楼梯 |
| `speed` | `0` 正常 / `1` 低速 / `2` 高速 |
| `manner` | `0` 前进 / `1` 倒退 |
| `obs_mode` | `0` 避障开启 / `1` 关闭 |
| `nav_mode` | `0` 直线导航 / `1` 自主导航 |

---

## 三、判断定位是否就绪

正确做法二选一，**都不要用 `/perception` 的 `Location`**：

```python
# 方式一：定位调用本身返回成功就说明收敛了（见 full-patrol.md 第 ④ 步）
r = bot.localize(map_name, '85')      # 失败会抛 RobotError

# 方式二：看定位话题的新鲜度。用 received_at（Unix 时间），不是 stamp（ROS 时间）
import time
gl = bot.telemetry()['telemetry']['global_localization']
fresh = gl['received'] and (time.time() - gl['received_at']) < 5.0
```

```bash
# 或者最简单：/position 通不通。定位未就绪时它是 503
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "X-API-Key: $CX_KEY" "$CX_HOST/v1/robots/$CX_ROBOT/position"
```

---

## 四、还要看的三个「隐性」状态

这三个都在 `GET …/telemetry` 的 `data` 顶层，容易被忽略，但它们能解释掉大部分
「明明下发成功了却不动」：

| 字段 | 为 true / false 时意味着什么 |
|------|---------------------------|
| `emergency_active` | 机器人端在以 20Hz 持续下发停止指令。**不是硬件急停、也不是锁** —— 现场遥控会与它竞争 |
| `ros_available` | 为 `false` 时机器人端 ROS 挂了：接口照样返回，但内容是最后一次成功读到的陈旧值 |
| `telemetry.<话题>.received` | 该话题**从来有没有**收到过数据。`false` = 对应模块没起来 |

---

## 五、别再轮询了 —— 用事件

上面讲的都是「拉」。如果你要的是「到达某个航点时通知我」，
**不要轮询 `/task` 做差分** —— 用事件流：[arrival-events.md](arrival-events.md)。

轮询能用，但两个缺点很实际：到达时刻的精度就是你的轮询间隔，
而想把间隔压小又会撞限流（默认 5 请求/秒）。
