# 让机器人真正走起来：完整八步

> **只下发任务，机器人是不会动的。**
>
> 「任务下发返回 200 了但机器人站着不动」是最常见的求助，原因几乎总是漏了第 ② 步
> （启动设备）或第 ⑤ 步（重定位）。这两步在 `/v1` 上没有暴露，必须走透传通道 ——
> 所以只读 `/v1` 的文档是不够的，这一篇就是补这个缺口。

```
① 连接与体检        确认在线
② 启动设备          在机器人上执行启动脚本，拉起导航等模块   ← 漏了它，任务不会执行
③ 等启动完成        轮询脚本状态（十几秒到一分多钟）
④ 等定位话题就绪    /global_localization 开始出数据
⑤ 重定位            告诉机器人「你现在在哪个航点」          ← 漏了它，机器人不知道自己在哪
⑥ 下发巡检          给一串航点
⑦ 跟踪进度 + 取全景  轮询状态，同时抓画面
⑧ 收尾              停任务 → 停设备 → 等停完
```

② ③ ④ ⑤ 走**透传通道**（`/api/robots/{机器人ID}/api/...`，**只认机器人 ID**）；
① ⑥ ⑦ ⑧ 走 `/v1`（认别名）。

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

## ④ 等定位话题就绪

导航起来之后，定位模块还要一会儿才开始出数据：

```python
bot.wait_topic('/global_localization', timeout=120)
```

```bash
curl -s "$CX_HOST/api/robots/R30_2026_001/api/device/topic_ready?topic=/global_localization" \
  -H "X-API-Key: $CX_KEY"
```

---

## ⑤ 重定位：告诉机器人它在哪

**机器人开机后不知道自己在地图上的位置。** 必须指定一个它当前实际所在的已知航点，
让定位算法从那里开始收敛。

```python
maps = bot.maps()
map_name = maps[0]
wps = bot.waypoints(map_name)

start_node = '1'                     # 机器人当前**实际**所在的航点
bot.localize(map_name, start_node, wps[start_node]['pose'], timeout=60)
```

```bash
curl -s -X POST "$CX_HOST/api/robots/R30_2026_001/api/localization/execute" \
  -H "X-API-Key: $CX_KEY" -H "Content-Type: application/json" \
  --max-time 60 \
  -d '{"map_name":"map_20260818_132055","node_id":"1",
       "pose":{"position":{"x":0.006628,"y":-0.003865,"z":0.003206},
               "orientation":{"x":5e-06,"y":-8.1e-05,"z":9e-06,"w":1}}}'
```

两个坑，都会浪费很多时间：

> ⚠️ **HTTP 超时要给足。** 服务端收到请求后会**同步等待**定位算法收敛，默认最长约 20 秒。
> 客户端超时设成 10 秒的话，请求会被你自己掐断，看起来像「定位失败」，其实是超时太短。
> SDK 默认 60 秒；用 curl 记得加 `--max-time 60`。

> ⚠️ **`node_id` 必须是机器人真实所在的点位。** 给错了定位会收敛到错的地方，
> 后面巡检就朝着错误方向走 —— 这在现场是有物理风险的。

定位成功后，`/position` 就从 503 变成 200：

```python
pos = bot.position()
print(f"x={pos['x']:.2f} y={pos['y']:.2f} yaw={pos['yaw']:.2f}")
```

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

**能跳过 ②③⑤ 吗？**
在仿真环境里可以（仿真器直接提供定位，机器人一开始就知道自己在哪）。
对着真机**不行**。

**每次巡检都要走一遍八步吗？**
不用。② 到 ⑤ 是**一次性初始化**，机器人不重启的话做一次就够。
之后重复 ⑥⑦ 即可。收尾（⑧）只在你确定不再用它时做。

**多个程序能同时控制一台机器人吗？**
不能，这是安全要求。第二个写操作会得到 409。读操作不受限制，随便几个程序同时读都行。
