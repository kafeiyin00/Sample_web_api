# 到达提醒（事件流）

机器人走到一个航点时通知你，而不是让你轮询去猜。

```bash
# 一条命令就能看到事件往外冒（Ctrl-C 退出）
curl -sN -H "X-API-Key: $CX_KEY" \
  "$CX_HOST/v1/robots/$CX_ROBOT/events?stream=1"
```

```
: connected seq=21

id: 22
event: waypoint_reached
data: {"seq":22,"ts":1788400000123,"robotId":"R30_2026_001","type":"waypoint_reached",
       "data":{"waypoint":"82","map":"map_20260818_132055","index":1,"total":3,"nextTarget":"5"}}

id: 23
event: task_completed
data: {"seq":23,...,"type":"task_completed","data":{"map":"...","visited":["85","82","5"],"total":3}}
```

---

## 两种取法，同一个端点

| | 用法 | 适合 |
|---|------|------|
| **立即返回** | `GET …/events?since=N` | 无状态后端、定时任务。返回 N 之后的事件，不阻塞 |
| **SSE 长连接** | `GET …/events?since=N&stream=1` | 看板、常驻进程。事件一产生就推给你 |

```bash
# 立即返回：拿 N 之后攒下的事件
curl -s -H "X-API-Key: $CX_KEY" "$CX_HOST/v1/robots/$CX_ROBOT/events?since=21"
```

```json
{
  "success": true,
  "data": {
    "events": [ { "seq": 22, "type": "waypoint_reached", "..." : "..." } ],
    "seq": 23,
    "nextSince": 22
  }
}
```

下一轮带上 `nextSince` 就能保证一条不漏。**不带 `since` 时只从当下开始**
（不会倒带历史），返回的 `seq` 就是你的起点。

---

## 事件类型

| `type` | 什么时候产生 | `data` 里有什么 |
|--------|------------|---------------|
| `waypoint_reached` | **到达一个航点** | `waypoint`、`map`、`index`、`total`、`nextTarget` |
| `task_started` | 任务开始推进 | `map`、`path`、`statusCode` |
| `task_completed` | 任务正常结束 | `map`、`visited`、`total` |
| `task_failed` | 任务失败 | `errorCode`、`errorHex`、`visited`、`total` |
| `task_stopped` | 任务被停止 | `map` |
| `obstacle` | 开始/结束避障 | `avoiding`（bool） |
| `localization` | 定位有效性变化 | `valid`（bool） |
| `emergency` | 急停状态变化 | `active`（bool） |
| `online` / `offline` | 机器人上线/掉线 | `name`、`robotType` |

每条事件都有 `seq`（单调递增）、`ts`（Unix 毫秒）、`robotId`、`type`、`data`。

---

## 断线不丢事件 —— 这一节是重点

4G 上断线是常态，而「到达」这种消息**丢一条就等于没有**。所以：

* 每条事件带 `seq`，云端保留最近 **500** 条；
* 重连时带 `?since=<上次收到的最大 seq>`，中间漏掉的会**补回来**；
* 浏览器 `EventSource` 自动重连带的 `Last-Event-ID` 头也被当作 `since` —— 你不用额外做什么。

```python
# 正确：始终从上次的位置续接
cursor = client.events()['seq']          # 记下起点
while True:
    batch = client.events(since=cursor)
    for e in batch['events']:
        handle(e)
    cursor = batch['nextSince']          # ← 关键：用服务端给的游标
    time.sleep(1)
```

```python
# 错误：每轮都「从现在开始听」，两轮之间的事件全丢
while True:
    for e in client.events()['events']:  # 不带 since = 只给当下之后
        handle(e)
    time.sleep(1)
```

---

## 三种写法

### 1. Python，用 SDK（最省事）

```python
from certaintyx import RobotClient

bot = RobotClient('https://certaintyx.sg:8443', 'ntu-dog-00001', 'cx_xxx_...')

# 下发任务后，等到达某个航点
bot.start_patrol('map_20260818_132055', ['85', '82', '5'])
e = bot.wait_arrival('82', timeout=300)      # 任务失败会直接抛 RobotError
print('到了 82 号点', e['data'])

# 或者听全部事件
for e in bot.watch_events(types={'waypoint_reached', 'task_completed'}):
    print(e['type'], e['data'])
```

`wait_arrival` 在任务失败时**直接抛错**而不是干等到超时 —— 真正的原因
（避障失败、规划失败）早就在事件里了。

### 2. 浏览器，`EventSource`

```javascript
// 注意：EventSource 不能自定义请求头，所以密钥要走 URL —— 别在公开页面里这么用
const es = new EventSource(`/v1/robots/${robot}/events?stream=1&token=...`);

es.addEventListener('waypoint_reached', (ev) => {
  const e = JSON.parse(ev.data);
  console.log(`到达 ${e.data.waypoint}（${e.data.index + 1}/${e.data.total}）`);
});
es.addEventListener('task_failed', (ev) => {
  const e = JSON.parse(ev.data);
  alert(`任务失败：${e.data.errorHex}`);
});
// 断线由浏览器自动重连，并带上 Last-Event-ID，所以不会丢事件
```

### 3. 任何语言，裸 HTTP 轮询

```bash
CURSOR=$(curl -s -H "X-API-Key: $CX_KEY" "$CX_HOST/v1/robots/$CX_ROBOT/events" \
         | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["seq"])')

while true; do
  RESP=$(curl -s -H "X-API-Key: $CX_KEY" \
         "$CX_HOST/v1/robots/$CX_ROBOT/events?since=$CURSOR")
  echo "$RESP" | python3 -c '
import json, sys
d = json.load(sys.stdin)["data"]
for e in d["events"]:
    print(e["type"], json.dumps(e["data"], ensure_ascii=False))
'
  CURSOR=$(echo "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["nextSince"])')
  sleep 2
done
```

---

## 几个实现细节，知道了能少踩坑

**到达是逐个补发的。** 如果两次观测之间机器人走过了两个点，你会收到**两条**
`waypoint_reached`，而不是只有最后一个。

**首个航点也会报到达。** 下发任务时 `visited` 立刻包含起点，所以你会先收到一条
起点的 `waypoint_reached`，紧跟着 `task_started`。想跳过它就看 `data.index != 0`。

**机器人重连后不会补出假到达。** 掉线时云端会清掉差分基线，重新上线后第一次观测
只用来建基线、不产事件。所以你不会在重连后收到一串早已发生过的「到达」。

**事件不是持久化的。** 云端只保留最近 500 条在内存里，网关重启就没了。
它是「实时通知 + 短期补漏」，不是审计日志。要长期留存请自己落库。

**限流按请求数算。** SSE 是一条长连接、只占一个请求，所以听事件比高频轮询
省得多 —— 这也是推荐用 `stream=1` 的实际理由。

---

## 和本地 SDK 的关系

机器人本地 SDK（TCP/APDU，同一局域网内用）有 `1007/2` 的 10Hz 主动推送，
推的是位姿 + 状态快照。云端这条事件流是它的对应能力，但**刻意做成语义事件**：

* 你要的是「到了 3 号点」这个时刻，而不是每秒十条位姿；
* 位姿本来就能按需拉 `GET …/position`，用不着推；
* 每秒十条 JSON × N 个订阅者，在 4G 上是实打实的带宽。

底下的机制是：机器人端 agent 以 2Hz 读本机状态（走 localhost，代价可忽略），
**只在变化时**把观测发给云端；云端比较相邻两次观测，产出语义事件。
所以正常巡检一趟也就十几帧上行。
