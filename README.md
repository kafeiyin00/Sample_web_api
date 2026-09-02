# certaintyX 机器狗云端 API 教程

用 HTTP 调一台四足机器人：读位置、下发巡检、取全景画面、急停。

机器人在 4G 网络后面，**没有公网 IP**。所有流量都是机器人主动向云端拨出的隧道，
所以你只要能访问 `https://certaintyx.sg:8443` 就够了 —— 不需要和机器人在同一个网络，
也不需要在任何一侧开端口。

这个仓库是**自包含**的：不需要机器人端代码，不需要 pip 装东西，示例可以直接跑。

```
你的程序 ──HTTPS──> 云端网关 ──既有隧道──> 机器人
                  certaintyx.sg:8443
```

---

## 1. 三十秒确认能通

只需要两样东西，找管理员在云端界面「API 接入」里拿：

| 项 | 长什么样 | 说明 |
|----|---------|------|
| **机器人别名** | `ntu-dog-00001` | 对外稳定地址。**别用机器人 ID** —— ID 跟着硬件走，换主板/网卡就变 |
| **API 密钥** | `cx_1a2b3c4d_<48 位十六进制>` | 只在签发时显示一次，云端只存哈希。丢了只能重新生成 |

```bash
export CX_HOST=https://certaintyx.sg:8443
export CX_ROBOT=ntu-dog-00001
export CX_KEY=cx_xxxxxxxx_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

curl -s -H "X-API-Key: $CX_KEY" "$CX_HOST/v1/robots/$CX_ROBOT" | python3 -m json.tool
```

```json
{
  "success": true,
  "data": {
    "robotId": "R30_2026_001",
    "alias": "ntu-dog-00001",
    "online": true,
    "location": "Singapore, SG",
    "rtspPath": "cam-1c697ada870c",
    "apiBase": "/v1/robots/ntu-dog-00001",
    "lease": null
  }
}
```

`"online": true` 就通了。如果是 `false`，先去确认机器人开机并联网 —— 后面每一步都会 502。

**不知道有哪些端点？问它自己**（这个地址免鉴权）：

```bash
curl -s "$CX_HOST/v1" | python3 -m json.tool
```

---

## 2. 五个必须先知道的规则

这五条基本涵盖了新接入的人踩的所有坑。

### 2.1 响应信封

成功一律是 `{"success": true, "data": ...}`，失败一律是 `{"success": false, "error": "中文说明"}`，
并且 HTTP 状态码同时表达同一件事。**先看状态码，再看 `error`**。

```json
{ "success": false, "error": "无控制权限（viewer 只读）" }   // HTTP 403
```

### 2.2 有两条通道，用错会得到误导性的报错

| | `/v1/robots/{别名}/…` | `/api/robots/{机器人ID}/api/…` |
|---|---|---|
| 定位 | **对外冻结契约**，内部重构不影响 | 透传直达机器人内部接口 |
| 认别名吗 | **认** | **不认，只认机器人 ID** |
| 稳定性 | 承诺不变 | 机器人端升级时路径可能变 |
| 什么时候用 | 默认都用这条 | `/v1` 没暴露的功能（启动设备、定位） |

> ⚠️ 往透传通道传别名，会得到 **502「机器人不在线」** —— 看着像机器人掉线了，
> 其实只是名字用错了。这是最容易浪费半天的一个坑。

日常读写只用 `/v1` 这一条就够。只有[完整巡检流程](docs/full-patrol.md)里的
「启动设备」和「定位」两步必须走透传。

### 2.3 写操作需要控制权，读操作不需要

同一时刻只有一个人/一个程序能控制一台机器人 —— 这是安全要求，不是限制。
你的密钥有三种模式，签发时选定：

| 模式 | 行为 | 适合 |
|------|------|------|
| `none`（viewer） | 只读。任何写操作直接 **403** | 看板、取数、监控 |
| `auto` | **写操作时云端自动帮你接管**，你不用写续期循环 | 调度系统（**推荐**） |
| `explicit` | 自己调 `/control/acquire` 并每 10 秒续期 | 需要长时间独占机器人的任务系统 |

用 `auto` 就好。它的租约在你停止写入约 30 秒后自然过期 —— 刻意不做后台续期，
否则高频调度会永久占锁，现场的人就再也拿不回机器人了。

**现场的人随时可以抢走你的控制权**，反过来不行（你抢不走人的）。
被抢后你的写操作会返回 409，这是正常的，重试或等对方放手即可。

**急停是例外**：`POST /estop` 不需要控制权（但仍需 operator 权限）。安全动作不该排队。

### 2.4 写操作请带 `Idempotency-Key`

4G 下超时重试极其常见。不带幂等键，一次「超时后重试」就会让机器人**收到两次巡检任务**。

```bash
curl -s -X POST "$CX_HOST/v1/robots/$CX_ROBOT/task" \
  -H "X-API-Key: $CX_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: mission-2026-09-02-07" \
  -d '{"map_name":"map_20260818_132055","path":["1","8","12","16"]}'
```

十分钟内用**同一个键**重发，会拿回首次那个字节级完全相同的响应，并多一个响应头
`Idempotent-Replay: true`，机器人那边不会再执行一次。

> 重试时**必须复用同一个键** —— 每次重试换新键，等于没做幂等。
> 键的作用域包含调用方、机器人和端点，所以你不必担心和别人撞键。

两种诚实的失败也要会处理：首次请求还在进行中会返回 **409**；
首次响应太大（>1MB）没能留存，重放也返回 409 并提示你自己去查任务状态。
这比假装成功要好得多。

### 2.5 限流：默认 5 请求/秒

超了返回 **429** 并带 `Retry-After`（秒）。轮询间隔别小于 1 秒；
收到 429 就照 `Retry-After` 退避，别硬重试。

**要「到达通知」就别轮询** —— 用事件流（`GET …/events?stream=1`）：
延迟低，而且一条长连接只占一个请求。见 [arrival-events.md](docs/arrival-events.md)。

### 2.6 状态码不要自己抄一份表

`error_code: 9035`、`gait: 12290` 这种裸值的权威定义在机器人本地 SDK 里。
云端把整张表给你了，**免鉴权**：

```bash
curl -s "$CX_HOST/v1/status-codes"
```

而且 `GET …/task` 与 `GET …/perception` 的响应里已经附上了语义字段
（`status_name`、`active`/`terminal`、`error_text`、`location_valid`…），
多数情况下你连查表都不用。三个反直觉点见 [status.md](docs/status.md)：
写 `running` 读回 `navigating`、`error_code` 与 `status` 正交、
`Location=0` 才是「定位有效」。

---

## 3. 跑起来

三种语言，做同一件事，选你顺手的。都不需要装依赖。

```bash
# curl
cp examples/curl/env.example.sh examples/curl/env.sh   # 填进你的密钥与别名
bash examples/curl/01_read.sh

# Python（只用标准库）
python3 examples/python/01_hello_raw.py --host $CX_HOST --robot $CX_ROBOT --key $CX_KEY

# Node（零依赖，需 Node 18+）
node examples/node/01_hello.mjs --host $CX_HOST --robot $CX_ROBOT --key $CX_KEY
```

全部示例：

| 文件 | 做什么 | 会让机器人动吗 |
|------|--------|---------------|
| [examples/curl/01_read.sh](examples/curl/01_read.sh) | 把一台机器人的状态看个遍 | 否 |
| [examples/curl/02_task.sh](examples/curl/02_task.sh) | 下发巡检 → 跟踪 → 停止 | **是** |
| [examples/curl/03_estop.sh](examples/curl/03_estop.sh) | 急停 / 取消急停 | **是** |
| [examples/python/01_hello_raw.py](examples/python/01_hello_raw.py) | 只用 urllib，把 HTTP 层讲清楚 | 否 |
| [examples/python/02_watch_position.py](examples/python/02_watch_position.py) | 持续读位置，示范正确的轮询与限流退避 | 否 |
| [examples/python/03_full_patrol.py](examples/python/03_full_patrol.py) | 完整八步，含仿真模式 `--sim` | **是** |
| [examples/python/04_verify_flow.py](examples/python/04_verify_flow.py) | 自检：逐条验证本教程说的和系统做的是否一致 | **是** |
| [examples/python/05_arrival_events.py](examples/python/05_arrival_events.py) | 到达提醒：`--listen` 只听事件（否）；`--patrol` 下发巡检并跟踪（**是**） | 见左 |
| [examples/node/01_hello.mjs](examples/node/01_hello.mjs) | 同 01_hello_raw.py，换成 Node | 否 |

标「是」的会让**真实机器人走起来**：跑之前确认现场没人在它路径上、有人能按下物理急停。
第一次接触建议先用 `03_full_patrol.py --sim` 在仿真里跑一遍，流程完全一致。

接入一套新环境时，先跑 `04_verify_flow.py` —— 它把本教程的每条断言都对着真实系统
核一遍（状态词、幂等重放、定位的成功与失败路径、`/perception` 的已知问题…），
哪条不符会直接指出来，比逐条人工核对省事得多。

想少写点样板代码，可以直接用 [`examples/python/certaintyx.py`](examples/python/certaintyx.py)
这个单文件 SDK（标准库，拷进你的项目即可）：

```python
from certaintyx import RobotClient

bot = RobotClient('https://certaintyx.sg:8443', 'ntu-dog-00001', 'cx_xxx_...')
print(bot.position())                                  # {'x':…, 'y':…, 'yaw':…}
bot.start_patrol('map_20260818_132055', ['1', '8', '12'])
for st in bot.watch_task():
    print(st['status'], st['current_target'])
```

---

## 4. 接下来读什么

| 文档 | 内容 |
|------|------|
| [docs/api-reference.md](docs/api-reference.md) | 每个端点的**真实**请求与响应（从生产环境抓的，逐字段说明） |
| [docs/full-patrol.md](docs/full-patrol.md) | 让机器人真正走起来的完整八步 —— 只下发任务它是不会动的 |
| [docs/status.md](docs/status.md) | 读状态：状态码/错误码表、三个反直觉点、怎么判断定位就绪 |
| [docs/arrival-events.md](docs/arrival-events.md) | **到达提醒**：机器人走到航点时通知你，别再轮询 |
| [docs/video.md](docs/video.md) | 全景画面：RTSP / HLS / 抓单帧 |
| [docs/troubleshooting.md](docs/troubleshooting.md) | 错误码与「明明没报错但不对」的症状对照表 |

**如果你只想让机器人动起来，直接看 [full-patrol.md](docs/full-patrol.md)。**
「下发了任务但机器人不动」是最常见的求助，原因几乎总是漏了那八步里的第 ②（启动设备）或第 ④（定位）步。

---

## 5. 几件不要做的事

* **不要把密钥写进前端代码或提交进仓库。** 它等价于对这台机器人的操作权。
  泄露了就在界面上「重新生成」，旧密钥立刻失效。
* **不要用机器人 ID 当对外地址。** 换硬件它就变了；别名才是稳定的那个。
* **不要用 `if status == 'running'` 判断任务在跑** —— 永远不成立，见
  [api-reference.md 的状态词表](docs/api-reference.md#任务状态词)。
* **不要靠轮询 `/perception` 的 `Location` 判断定位好了没** —— 见同一份文档里的说明。
* **不要把轮询间隔设到 1 秒以下**，会撞限流 —— 想要低延迟就用事件流。
* **不要在自己代码里抄一份状态码表**，会漂移。用 `GET /v1/status-codes`，
  或者直接读响应里已经附上的 `*_name` / `*_text` 字段。
