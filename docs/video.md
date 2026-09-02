# 全景画面

机器人头上是一台 Insta360 全景相机，画面经机器人推到云端，你从云端拉。

**拉流地址是只读的，不需要凭据** —— 直接丢给 VLC 或 `<video>` 标签就能播。
（页面本身有登录保护，但流地址本身没有；如果这对你的场景不可接受，找管理员给
mediamtx 接 JWT 外部认证。）

---

## 先拿到路径

每台机器人的流路径按网卡 MAC 派生，形如 `cam-1c697ada870c`。
从概览接口读，**别硬编码**：

```bash
curl -s -H "X-API-Key: $CX_KEY" "$CX_HOST/v1/robots/$CX_ROBOT" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['rtspPath'])"
# → cam-1c697ada870c
```

---

## 三种拿法

| 方式 | 地址 | 延迟 | 适合 |
|------|------|------|------|
| **HLS** | `https://certaintyx.sg:8443/hls/<path>/index.m3u8` | ~2–3 秒 | 浏览器里直接播，最省事 |
| **RTSP** | `rtsp://certaintyx.sg:8554/<path>` | ~1–2 秒 | VLC、ffmpeg、OpenCV |
| **WebRTC** | `https://certaintyx.sg:8443/whep/<path>` (WHEP) | ~0.3–0.5 秒 | 需要低延迟的遥操作界面 |

```bash
# 浏览器：直接打开这个地址（Safari 原生支持；Chrome 需 hls.js）
echo "$CX_HOST/hls/cam-1c697ada870c/index.m3u8"

# VLC
vlc rtsp://certaintyx.sg:8554/cam-1c697ada870c --network-caching=200

# 抓一帧存成图片
ffmpeg -rtsp_transport tcp -i rtsp://certaintyx.sg:8554/cam-1c697ada870c \
       -frames:v 1 -q:v 2 pano.jpg
```

Python 里用 SDK 一行：

```python
bot.snapshot('pano.jpg')     # 内部就是上面那条 ffmpeg 命令
print(bot.rtsp_url(), bot.hls_url())
```

> ⚠️ **抓帧必须用 TCP**（`-rtsp_transport tcp`）。用默认的 UDP 在很多企业网里会被丢包，
> 表现为**一直抓不到帧却不报任何错** —— 干等到超时。

---

## 画面是什么样的

**2:1 的等距投影全景**（equirectangular），当前分辨率 `1280x640`。

* 左右首尾相接（最左和最右是同一条经线）
* 上边缘是天顶，下边缘是地面，两端拉伸严重
* 想看某个方向就自己做视角变换（`py360convert`、`equirect2perspective` 之类都行）

分辨率与帧率由机器人端配置决定，会随带宽策略调整 —— 别把数值写死在代码里，
需要的话从 HLS 主播放列表里读：

```bash
curl -s "$CX_HOST/hls/cam-1c697ada870c/index.m3u8"
# #EXT-X-STREAM-INF:...,RESOLUTION=1280x640,FRAME-RATE=15.000
```

---

## 没有画面时怎么查

按这个顺序，一步步都能自己确认：

1. **先在浏览器里打开 HLS 地址。** 有画面 → 流是好的，问题在你的客户端。
2. **HLS 返回 404** → 云端没有这一路的发布者。可能是：
   相机 USB 没插好 / 相机驱动没起来 / 机器人端推流进程没跑。
   这时候读 `/v1/robots/{robot}` 看 `online` —— 机器人在线但没画面，就是相机侧的问题。
3. **HLS 有画面但 RTSP 拉不到** → 8554 端口被你所在网络的防火墙挡了。改用 HLS。
4. **能拉到但一直抓不到帧** → 十有八九是没加 `-rtsp_transport tcp`（见上）。
5. **WebRTC 连不上/一直转圈** → 媒体流走 8189，UDP 被挡时会退到 TCP，
   企业网里两个都挡就只能用 HLS。
