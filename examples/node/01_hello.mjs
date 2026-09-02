#!/usr/bin/env node
/**
 * 用 Node 内置 fetch 调 certaintyX 云端 API。零依赖，需要 Node 18+。只读，不会让机器人动。
 *
 * 这份和 examples/python/01_hello_raw.py 做同一件事 —— 放在这里是为了说明
 * 这套 API 就是普通的 HTTP + JSON，不绑定任何语言或 SDK。
 *
 *   node 01_hello.mjs --robot ntu-dog-00001 --key cx_xxx_...
 */

const argv = process.argv.slice(2);
const arg = (name, fallback) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : fallback;
};

const HOST = arg('host', 'https://certaintyx.sg:8443');
const ROBOT = arg('robot');
const KEY = arg('key');

if (!ROBOT || !KEY) {
  console.error('用法: node 01_hello.mjs --robot <别名> --key <cx_...> [--host <地址>]');
  process.exit(1);
}

const R = `/v1/robots/${encodeURIComponent(ROBOT)}`;

/**
 * 一次 API 调用。
 *
 * 刻意不把 4xx/5xx 当异常抛：这套 API 用状态码表达业务结果
 * （403 无权限、409 控制权被占、503 定位未就绪），当异常处理反而更绕。
 * 429 会照 Retry-After 退避重试。
 */
async function call(path, { method = 'GET', body, idempotencyKey } = {}) {
  const headers = { 'X-API-Key': KEY };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  // 写操作必须带：4G 下超时重试很常见，不带的话重试会让机器人执行两次
  if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;

  for (let attempt = 0; attempt < 3; attempt += 1) {
    const res = await fetch(`${HOST}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    if (res.status === 429 && attempt < 2) {
      const wait = Number(res.headers.get('retry-after') || 1);
      console.error(`  [限流] 等 ${wait} 秒后重试`);
      await new Promise((r) => setTimeout(r, wait * 1000));
      continue;
    }
    let parsed = null;
    try { parsed = await res.json(); } catch { parsed = null; }
    return { status: res.status, body: parsed, headers: res.headers };
  }
  return { status: 0, body: null, headers: new Headers() };
}

const bold = (s) => `\x1b[1m${s}\x1b[0m`;

// ── 1. 自描述（免鉴权）──────────────────────────────────────────
{
  const { body } = await call('/v1');
  const eps = body?.data?.endpoints ?? [];
  console.log(`${bold(`可用端点 ${eps.length} 个`)}（GET /v1 免鉴权）`);
  for (const e of eps.slice(0, 4)) {
    console.log(`  ${e.method.padEnd(6)} ${e.path.padEnd(48)} ${e.desc}`);
  }
  console.log('  ...');
}

// ── 2. 概览：先确认在线 ─────────────────────────────────────────
const info = await call(R);
if (info.status === 401) {
  console.error('密钥无效或已吊销（401 不区分“不存在/已吊销/已过期”）');
  process.exit(1);
}
if (info.status !== 200) {
  console.error(`读概览失败 HTTP ${info.status}:`, info.body);
  process.exit(1);
}
const d = info.body.data;
console.log(`\n${bold('概览')}`);
console.log(`  robotId=${d.robotId}  alias=${d.alias}`);
console.log(`  online=${d.online}  位置=${d.location}  内网=${d.lanIp}`);
console.log(`  控制权=${d.lease ? d.lease.owner : '空闲'}`);
console.log(`  推流路径=${d.rtspPath}`);
if (!d.online) {
  console.error('\n机器人不在线 —— 后面每一步都会 502');
  process.exit(1);
}

// ── 3. 遥测：注意嵌套多一层 data.telemetry.<话题> ────────────────
{
  const { body } = await call(`${R}/telemetry`);
  const data = body?.data ?? {};
  const gl = data.telemetry?.global_localization ?? {};
  console.log(`\n${bold('遥测要点')}`);
  console.log(`  急停指令在下发=${data.emergency_active}   ← 软件标志，不是硬件急停`);
  console.log(`  ROS 可用=${data.ros_available}`);
  console.log(`  自述状态=${data.telemetry?.robot_info?.status}（中文，给人看的）`);
  if (gl.received) {
    // received_at 是 Unix 秒；stamp 是 ROS 时间，拿它和 Date.now() 比毫无意义
    const age = Date.now() / 1000 - Number(gl.received_at || 0);
    console.log(`  定位数据年龄=${age.toFixed(1)}s  可信=${age < 5}  ← 用 received_at 算`);
  } else {
    console.log('  定位话题从未收到数据');
  }
}

// ── 4. 地图与航点 ──────────────────────────────────────────────
{
  const { body } = await call(`${R}/maps`);
  const maps = body?.data ?? [];
  console.log(`\n${bold('地图')} ${JSON.stringify(maps)}`);
  if (maps.length) {
    const wp = await call(`${R}/maps/${encodeURIComponent(maps[0])}/waypoints`);
    const wps = wp.body?.data ?? {};
    // 键是字符串且按字典序（1, 10, 11, ... 2, 20）——要按编号看得显式排序
    const ids = Object.keys(wps).sort((a, b) => Number(a) - Number(b));
    console.log(`  ${maps[0]}: 共 ${ids.length} 个航点，前 8 = ${JSON.stringify(ids.slice(0, 8))}`);
  }
}

// ── 5. 任务状态 ────────────────────────────────────────────────
{
  const { body } = await call(`${R}/task`);
  const t = body?.data ?? {};
  console.log(`\n${bold('任务')} status=${JSON.stringify(t.status)} `
    + `目标=${JSON.stringify(t.current_target)} `
    + `已访=${(t.visited ?? []).length}/${(t.path ?? []).length}`);
  console.log("  提醒：下发时写 'running'，读回来是 'navigating'；失败读回来是 'paused'。");
  console.log("  所以 status === 'running' 永远不成立，要用集合判断。");
}

// ── 6. 探测写权限（故意用非法请求体，两种密钥下都不会真的执行）────
{
  const { status, body } = await call(`${R}/task`, {
    method: 'POST',
    body: { map_name: '', path: [] }
  });
  console.log(`\n${bold('写权限探测')}  [HTTP ${status}] ${body?.error ?? ''}`);
  if (status === 403) console.log('  只读（viewer）密钥的预期结果。');
  else if (status === 400) console.log('  400 说明鉴权过了、请求到达了机器人 —— 这把密钥可以下发任务（本次无动作）。');
  else if (status === 409) console.log('  有写权限，但控制权正被别人占着。');
}

console.log('\n完成。想让机器人真的走起来，读 docs/full-patrol.md（八步，缺一不可）。');
