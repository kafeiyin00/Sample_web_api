# 让机器人真正走起来：完整八步

> **只下发任务，机器人是不会动的。**
>
> 「任务下发返回 200 了但机器人站着不动」是最常见的求助，原因几乎总是漏了第 ② 步
> （启动设备）或第 ④ 步（定位）。这两步在 `/v1` 上没有暴露，必须走透传通道 ——
> 所以只读 `/v1` 的文档是不够的，这一篇就是补这个缺口。

```
① 连接与体检        确认在线
② 启动设备          在机器人上执行启动脚本，拉起导航等模块   ← 漏了它，任务不会执行
③ 等启动完成        轮询脚本状态（十几秒到一分多钟）
④ 定位              告诉机器人「你在哪个航点附近」，它据此收敛出全局定位
                    ← 漏了它，机器人不知道自己在哪
⑤ 确认定位可用      /position 从 503 变 200，/global_localization 开始有数据
⑥ 下发巡检          给一串航点
⑦ 跟踪进度 + 取全景  轮询状态，同时抓画面
⑧ 收尾              停任务 → 停设备 → 等停完
```

② ③ ④ 走**透传通道**（`/api/robots/{机器人ID}/api/...`，**只认机器人 ID**）；
① ⑤ ⑥ ⑦ ⑧ 走 `/v1`（认别名）。

> **别把 ④ 和 ⑤ 的因果搞反。** `/global_localization` 是定位的**产物**，不是前提：
> 你先给一个航点当初值，机器人才开始收敛，收敛出来的结果才是全局定位。
> 在调用 ④ 之前去等 `/global_localization` 出数据，是在等一个还不可能发生的事 ——
> 刚开机的机器人上会一直等到超时。

完整可运行代码：[`examples/python/03_full_patrol.py`](../examples/python/03_full_patrol.py)

---

## ① 连接与体检

```python
from certaintyx import RobotClient, RobotError, ACTIVE_STATUS

bot = RobotClient('https://certaintyx.sg:8443', 'ntu-dog-00001', 'cx_xxx_...')

info = bot.info()
print(info['online'], info['location'], info['rtspPath'])
if not info['online']:
    raise SystemExit('机器人不在线：先确认它开机、联网')
```

`info()` 顺带把 `robotId` 缓存下来了 —— 后面透传通道要用，SDK 自动处理。

---

## ② 启动设备

在机器人上执行启动脚本（导航、定位等模块）。**这一步不做，第 ⑥ 步下发的任务不会被执行。**

```python
task_id = bot.device_start()      # POST /api/robots/{id}/api/device/start
```

裸 HTTP：

```bash
curl -s -X POST "$CX_HOST/api/robots/R30_2026_001/api/device/start" -H "X-API-Key: $CX_KEY"
# → {"success": true, "task_id": "..."}
```

注意路径里是**机器人 ID**（`R30_2026_001`），不是别名。传别名会得到 502「机器人不在线」。

---

## ③ 等启动完成

脚本要跑十几秒到一分多钟，必须轮询：

```python
bot.wait_device(task_id, starting=True, timeout=180)
```

```bash
curl -s "$CX_HOST/api/robots/R30_2026_001/api/device/start_status?task_id=$TASK_ID" \
  -H "X-API-Key: $CX_KEY"
```

> ⚠️ `completed: true` 只表示**脚本跑完了**，不代表成功。还要看 `result_success`。
> 自己写轮询的话别忘了这一层 —— SDK 的 `wait_device()` 已经判断，失败抛 `RobotError`。

---

## ④ 定位：告诉机器人它在哪个航点附近

**机器人开机后不知道自己在地图上的位置。** 你给它一个「大概在哪」的初值 ——
用某个已知航点 —— 它据此启动定位并收敛出全局位姿。

```python
maps = bot.maps()
map_name = maps[0]

start_node = '1'          # 机器人当前**实际**所在（或最接近）的航点
bot.localize(map_name, start_node, timeout=60)
```

裸 HTTP：

```bash
curl -s -X POST "$CX_HOST/api/robots/R30_2026_001/api/localization/execute" \
  -H "X-API-Key: $CX_KEY" -H "Content-Type: application/json" \
  --max-time 60 \
  -d '{"map_name":"map_20260818_132055","node_id":"1"}'
```

| 请求字段 | 必填 | 说明 |
|---------|------|------|
| `map_name` | ✅ | 必须存在且有 pcd 文件，否则 404 |
| `node_id` | ✅ | 航点 ID。**这就是「你在哪个航点附近」** |
| `pose` | | 省略时服务端自动从 `node_id` 取该航点的位姿。只有需要给一个不在航点上的初值时才手工传 |
| `verify` | | 默认 `true`。传 `false` 变成「发完即返回」，不等收敛也不校验 —— 不建议 |

### 这一步内部到底做了什么

理解它能省掉很多困惑：

1. 把**地图 pcd + 你给的初值位姿**通过 `/relocalization` 发给定位算法；
2. 然后**同步等**一条新的 `/global_localization` 结果（要求它比发布时刻至少晚 1.5 秒，
   以排除发布瞬间还在途的旧数据），最长等 **20 秒**（含 fastlio 节点重启耗时）；
3. 拿收敛结果和你给的初值比，**水平偏差超过 3 米就判定定位失败**，
   响应里会给出 `drift` / `threshold` / `init_pose` / `result_pose`。

所以：

> ⚠️ **HTTP 超时必须给足。** 服务端要同步等最长 20 秒，客户端超时设成 10 秒
> 会被你自己掐断，看起来像「定位失败」，其实是超时太短。
> SDK 默认 60 秒；用 curl 记得加 `--max-time 60`。

> ⚠️ **`node_id` 必须是机器人真实所在（或最接近）的航点。** 给错了会有两种结果：
> 算法收敛到别处、偏差超 3 米被判失败（好情况），
> 或者勉强收敛在一个错的位置（坏情况）—— 后面巡检就朝着错误方向走，
> 现场是有物理风险的。

失败时响应里的 `reason` 告诉你是哪一种：

| `reason` | 含义 | 怎么办 |
|----------|------|--------|
| `timeout` | 20 秒内没等到收敛结果 | 定位模块没起来（第 ②③ 步是否成功？），或点云与地图对不上 |
| `drift_exceeded` | 收敛了，但离你给的初值超过 3 米 | `node_id` 给错了。换成机器人真实所在的航点 |

调用返回 `success: false` 时**不要往下走** —— 此时机器人的位置认知是错的。

---

## ⑤ 确认定位可用

④ 返回成功就说明已经收敛了。要再确认一次，读位姿：

```python
pos = bot.position()      # 定位好之前这里是 503
print(f"x={pos['x']:.2f} y={pos['y']:.2f} yaw={pos['yaw']:.2f}")
```

```bash
curl -s -H "X-API-Key: $CX_KEY" "$CX_HOST/v1/robots/$CX_ROBOT/position"
```

判断定位是否**持续**新鲜（比如巡检途中想确认没丢定位），用遥测里的 `received_at`：

```python
import time
gl = bot.telemetry()['telemetry']['global_localization']
fresh = gl['received'] and (time.time() - gl['received_at']) < 5.0
```

> ⚠️ **别用 `/perception` 的 `Location` 字段判断** —— 它目前恒为 1（定位无效），
> 哪怕定位完全正常。原因见
> [api-reference.md](api-reference.md#判断定位是否就绪)。

---

## ⑥ 下发巡检

```python
path = ['1', '20', '45', '70']       # 至少两个，且都要存在于该地图
bot.start_patrol(map_name, path)
```

字段名是 `map_name` / `path`（不是 `map` / `waypoints`），
详见 [api-reference.md 的 POST …/task](api-reference.md#post-task)。

选点建议：相邻编号的航点可能只差几十厘米，全挑相邻的机器人几秒就跑完，看不出效果。
挑彼此拉开距离的更有意义。

---

## ⑦ 跟踪进度 + 取全景

```python
for st in bot.watch_task(interval=1.5):
    print(st['status'], st['current_target'], st['visited'])
```

> ⚠️ **别用 `if status == 'running'`** —— 你下发的是 `running`，读回来是 `navigating`；
> 失败读回来是 `paused`。用 `ACTIVE_STATUS` / `TERMINAL_STATUS` 集合判断，
> 见 [状态词表](api-reference.md#任务状态词)。

同时可以抓全景画面：

```python
bot.snapshot('pano.jpg')       # 需要本机有 ffmpeg
print(bot.hls_url())           # 浏览器直接播
```

细节见 [video.md](video.md)。

---

## ⑧ 收尾

**别忘了这一步。** 任务停了但设备没停，机器人会一直空转耗电。

```python
bot.stop_task()                                    # 停巡检
sid = bot.device_stop()                            # 停设备
bot.wait_device(sid, starting=False, timeout=180)  # 等停完
```

出异常时先急停，再收尾：

```python
try:
    ...  # ②~⑦
except Exception:
    bot.estop()          # 急停不需要控制权。SDK 会发 {"active": true}
    raise
finally:
    bot.stop_task()
    sid = bot.device_stop()
    bot.wait_device(sid, starting=False, timeout=180)
```

> ⚠️ 自己拼 HTTP 请求做急停时，**请求体必须是 `{"active": true}`**。
> 发空体 `{}` 会被机器人端解读为「取消急停」—— 和你的意图正好相反。
> 详见 [api-reference.md 的 POST …/estop](api-reference.md#post-estop)。

整段用 `try/finally` 包起来，保证任何异常路径下设备都能停掉。
示例脚本就是这么写的 —— 建议照抄这个结构。

---

## 常被问到的

**能跳过 ②③④ 吗？**
在仿真环境里可以（仿真器直接提供定位，机器人一开始就知道自己在哪）。
对着真机**不行**。

**每次巡检都要走一遍八步吗？**
不用。② 到 ④ 是**一次性初始化**，机器人不重启、也没丢定位的话做一次就够。
之后重复 ⑥⑦ 即可。收尾（⑧）只在你确定不再用它时做。

**巡检途中丢了定位怎么办？**
重新做一次 ④ 就行 —— 用机器人**当前**最接近的航点当初值，不是原来的起点。
用 ⑤ 里那段 `received_at` 的新鲜度判断可以发现丢定位。

**多个程序能同时控制一台机器人吗？**
不能，这是安全要求。第二个写操作会得到 409。读操作不受限制，随便几个程序同时读都行。
