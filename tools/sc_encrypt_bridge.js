/**
 * ExClimbCongLing 加密桥接：在 Node 里加载 B站 风控 SDK 的 WASM，调用 encrypt_data。
 *
 * 用法（Python 侧用 subprocess 调）：
 *   echo '{"envInfo":{...},"publicKeyData":{...},"userId":"...","securityInfo":"..."}' \
 *     | node tools/sc_encrypt_bridge.js
 * 输出：{"key":"<base64>","data":"<base64>"}
 *
 * 实现依据（全部读自 _gt/bili-sc-sdk.js，来源 https://s1.hdslb.com/bfs/seed/jinkela/
 * short/minntaki-wasm-sdk/bili-sc-sdk.umd.js，377583 字节）：
 *
 * 1. 调用点 @373913：
 *      this.wasmModule.encrypt_data(JSON.stringify(A), JSON.stringify(this.publicKeyData), I, g)
 *    其中 A=envInfo、I=getUserId()（即 buvid3 Cookie）、g=securityInfoStr（NA() 的结果）。
 *    返回值是 **JSON 字符串**，SDK 里 `JSON.parse(Q)` 后取 `.key` / `.data`。
 *
 * 2. WASM 以 data URL 内嵌在 SDK 里（@6020 起 `new URL("data:application/wasm;base64,...")`），
 *    解出来 231536 字节。K() 走 `WebAssembly.instantiate`，不依赖任何浏览器专有 API，
 *    31 个导入全部是标准 wasm-bindgen `wbg` 胶水（crypto.getRandomValues / globalThis 访问器等），
 *    Node 原生就能满足 —— 这是它能在 Node 里跑通的根本原因。
 *
 * 3. Node 22 里 `globalThis.navigator` 是只读 getter，直接赋值会抛
 *    `TypeError: Cannot set property navigator of #<Object> which has only a getter`，
 *    所以这里用 `vm.createContext` 建独立沙箱，而不是污染 global。
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SDK_PATH = path.join(__dirname, '..', '_gt', 'bili-sc-sdk.js');

const noop = () => {};

/** 建一个最小 DOM/浏览器沙箱.
 *
 * 只补 SDK 加载期与 encrypt_data 路径真正会读到的东西。指纹采集（getEnvironmentInfo）
 * 我们不在 Node 里跑——envInfo 由 Python 侧构造后传进来，所以 canvas/webgl 这些
 * 只需要不抛异常即可。
 *
 * @returns {object} vm context
 */
function buildSandbox() {
  const mkStorage = () => {
    const m = new Map();
    return {
      getItem: (k) => (m.has(k) ? m.get(k) : null),
      setItem: (k, v) => m.set(k, String(v)),
      removeItem: (k) => m.delete(k),
      clear: () => m.clear(),
      key: (i) => Array.from(m.keys())[i] ?? null,
      get length() { return m.size; },
    };
  };

  const mkEl = () => ({
    style: {}, className: '', id: '', innerHTML: '', textContent: '',
    childNodes: [], children: [], offsetWidth: 0, offsetHeight: 0,
    setAttribute: noop, removeAttribute: noop, hasAttribute: () => false,
    appendChild: noop, removeChild: noop, insertBefore: noop,
    addEventListener: noop, removeEventListener: noop,
    getElementsByTagName: () => [], getElementsByClassName: () => [],
    querySelector: () => null, querySelectorAll: () => [],
    getContext: () => null,           // canvas/webgl 一律 null → SDK 走 "not available" 分支
    toDataURL: () => 'data:image/png;base64,',
    cloneNode: () => mkEl(), contains: () => false,
    getBoundingClientRect: () => ({ top: 0, left: 0, width: 0, height: 0 }),
    focus: noop, blur: noop, click: noop, remove: noop,
  });

  const win = {
    // --- 基础 JS 内建（vm context 里需要显式给出宿主对象）---
    console, Math, Date, JSON, RegExp, Error, TypeError, RangeError,
    String, Number, Boolean, Array, Object, Function, Symbol, Promise,
    Map, Set, WeakMap, WeakSet, Proxy, Reflect, BigInt,
    parseInt, parseFloat, isNaN, isFinite,
    encodeURIComponent, decodeURIComponent, encodeURI, decodeURI,
    escape: typeof escape !== 'undefined' ? escape : noop,
    unescape: typeof unescape !== 'undefined' ? unescape : noop,
    setTimeout, clearTimeout, setInterval, clearInterval,
    queueMicrotask, structuredClone,

    // --- WASM 与二进制（wasm-bindgen 胶水要用）---
    WebAssembly, ArrayBuffer, SharedArrayBuffer, DataView,
    Uint8Array, Uint16Array, Uint32Array, Int8Array, Int16Array, Int32Array,
    Float32Array, Float64Array, BigInt64Array, BigUint64Array, Uint8ClampedArray,
    TextEncoder, TextDecoder, URL, URLSearchParams,
    // wasm-bindgen 的 __wbg_crypto_* 导入要 crypto.getRandomValues；
    // Node 的 webcrypto 完全兼容，AES key/IV 的随机性就来自这里。
    crypto: require('crypto').webcrypto,
    Buffer, require,

    // --- 浏览器 API ---
    navigator: {
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        + ' (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
      appName: 'Netscape', appVersion: '5.0 (Windows)', appCodeName: 'Mozilla',
      language: 'zh-CN', languages: ['zh-CN', 'zh', 'en'],
      platform: 'Win32', product: 'Gecko', productSub: '20030107',
      vendor: 'Google Inc.', vendorSub: '', oscpu: undefined,
      plugins: [], mimeTypes: [], cookieEnabled: true, doNotTrack: null,
      hardwareConcurrency: 8, deviceMemory: 8, maxTouchPoints: 0,
      webdriver: false, onLine: true,
    },
    screen: {
      width: 1920, height: 1080, availWidth: 1920, availHeight: 1032,
      colorDepth: 24, pixelDepth: 24,
    },
    location: {
      href: 'https://www.bilibili.com/', protocol: 'https:',
      host: 'www.bilibili.com', hostname: 'www.bilibili.com',
      origin: 'https://www.bilibili.com', pathname: '/', search: '', hash: '',
    },
    history: { length: 1, pushState: noop, replaceState: noop },
    localStorage: mkStorage(), sessionStorage: mkStorage(),
    performance: { now: () => Date.now(), timing: { navigationStart: Date.now() } },
    devicePixelRatio: 1,
    innerWidth: 1920, innerHeight: 969, outerWidth: 1920, outerHeight: 1032,
    screenX: 0, screenY: 0, scrollX: 0, scrollY: 0,
    addEventListener: noop, removeEventListener: noop, dispatchEvent: noop,
    getComputedStyle: () => ({ display: 'block', fontSize: '16px',
                               getPropertyValue: () => '' }),
    requestIdleCallback: (fn) => setTimeout(() => fn({ didTimeout: false,
                                                       timeRemaining: () => 50 }), 0),
    cancelIdleCallback: clearTimeout,
    requestAnimationFrame: (fn) => setTimeout(() => fn(Date.now()), 16),
    cancelAnimationFrame: clearTimeout,
    // 这些 SDK 会 typeof 检测，给 undefined 让它走"不支持"分支即可
    AudioContext: undefined, webkitAudioContext: undefined,
    indexedDB: undefined, openDatabase: undefined, Notification: undefined,
    Worker: undefined, WebSocket: undefined,
    Image: function () { return mkEl(); },
    XMLHttpRequest: function () {
      return { open: noop, send: noop, setRequestHeader: noop, abort: noop,
               addEventListener: noop, removeEventListener: noop,
               readyState: 0, status: 0, responseText: '' };
    },
    // K()（WASM default init）内部用 `fetch(data:URL)` 来加载 WASM 字节，
    // 然后检查 `A instanceof Response` 决定走 instantiateStreaming 还是 arrayBuffer 路径。
    // Node 22 原生 fetch 支持 data: URL，必须把真实 fetch 和 Response/Request/Headers 注入
    // 沙箱——否则 WASM 初始化会永远 pending。
    // 对于 SDK 的网络上报（ExClimbCongLing 等），沙箱里 document/cookie 为空，
    // 不会真正发出有效请求，即便发出也只是 CORS 失败，不影响我们要的本地加密结果。
    fetch: (...args) => globalThis.fetch(...args),
    Response: globalThis.Response,
    Request: globalThis.Request,
    Headers: globalThis.Headers,
    atob: (s) => Buffer.from(s, 'base64').toString('binary'),
    btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
  };

  win.window = win;
  win.self = win;
  win.top = win;
  win.parent = win;
  win.globalThis = win;
  win.frames = win;

  win.document = {
    createElement: mkEl, createTextNode: () => mkEl(),
    createElementNS: mkEl, createDocumentFragment: mkEl,
    documentElement: mkEl(), body: mkEl(), head: mkEl(),
    cookie: '', readyState: 'complete', domain: 'bilibili.com',
    title: '', baseURI: 'https://www.bilibili.com/',
    location: win.location,
    currentScript: null,
    getElementById: () => null,
    getElementsByTagName: () => [], getElementsByClassName: () => [],
    querySelector: () => null, querySelectorAll: () => [],
    addEventListener: noop, removeEventListener: noop,
    createEvent: () => ({ initEvent: noop }),
    // NA() 的第 1 个探测器会读 document.$cdc_... / __webdriver_script_fn，
    // 保持 undefined 才不会被判成自动化环境。
  };

  const ctx = vm.createContext(win);
  return ctx;
}

/** 在沙箱里加载 SDK，返回 wasm-bindgen 模块（含 encrypt_data / default）.
 *
 * SDK 是 UMD（开头 `var g,f;g=this,f=function(){...}`），末尾按
 * `"object"==typeof exports&&"undefined"!=typeof module?module.exports=f():...`
 * 决定导出方式。这里在沙箱里预置 module/exports，让它走 CommonJS 分支。
 *
 * 注意：`f()` 的返回值是 SDK 单例 JA，末尾还会 `JA.init().then(()=>JA.startPeriodicReport(60))`
 * 自动启动周期上报。init() 正是加载 WASM 的地方（`yield this.wasmModule.default()`），
 * 所以照常让它跑。周期上报的第一步 `reportInfo` 会先 `getUserId()`，沙箱里
 * document.cookie 为空 → buvid3 取不到 → 直接 return，不会发出任何网络请求。
 *
 * @param {object} ctx vm context
 * @returns {Promise<object>} SDK 单例（含 .wasmModule）
 */
async function loadSdk(ctx) {
  const code = fs.readFileSync(SDK_PATH, 'utf8');
  ctx.module = { exports: {} };
  ctx.exports = ctx.module.exports;
  ctx.define = undefined;
  ctx.__filename = SDK_PATH;
  ctx.__dirname = path.dirname(SDK_PATH);

  vm.runInContext(code, ctx, { filename: 'bili-sc-sdk.js', timeout: 60000 });

  const sdk = ctx.module.exports;
  if (!sdk) throw new Error('SDK 未导出任何东西');
  return sdk;
}

/** 调 encrypt_data.
 *
 * @param {object} sdk SDK 单例
 * @param {object} envInfo 环境指纹对象
 * @param {object} publicKeyData ExGetAxe 返回的 data（{version, public_key, deadline}）
 * @param {string} userId buvid3
 * @param {string} securityInfo NA() 生成的 0/1 串
 * @returns {{key: string, data: string}}
 */
function encrypt(sdk, envInfo, publicKeyData, userId, securityInfo) {
  const wm = sdk.wasmModule;
  if (!wm || typeof wm.encrypt_data !== 'function') {
    throw new Error('wasmModule.encrypt_data 不可用；isInitialized='
      + sdk.isInitialized + ' isSupportedWASM=' + sdk.isSupportedWASM);
  }
  // 参数顺序照抄 SDK @373913
  const raw = wm.encrypt_data(
    JSON.stringify(envInfo),
    JSON.stringify(publicKeyData),
    userId,
    securityInfo,
  );
  // WASM 失败时不抛异常，而是返回一段中文错误串（例如 "加密失败: RSA加密失败..."），
  // 不是 JSON。这里显式识别，避免把它当成成功结果。
  let out;
  try {
    out = JSON.parse(raw);
  } catch (e) {
    throw new Error('encrypt_data 返回非 JSON（WASM 内部失败）: ' + String(raw).slice(0, 300));
  }
  if (!out || !out.key || !out.data) {
    throw new Error('encrypt_data 返回缺少 key/data: ' + String(raw).slice(0, 300));
  }
  return { key: out.key, data: out.data };
}

async function main() {
  // --- 读 stdin ---
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  const inputRaw = Buffer.concat(chunks).toString('utf8').trim();
  if (!inputRaw) throw new Error('stdin 为空，需要 JSON 输入');
  const input = JSON.parse(inputRaw);

  const envInfo = input.envInfo;
  const publicKeyData = input.publicKeyData;
  const userId = input.userId ?? '';
  const securityInfo = input.securityInfo ?? '';
  if (!envInfo) throw new Error('缺少 envInfo');
  if (!publicKeyData) throw new Error('缺少 publicKeyData');

  const ctx = buildSandbox();
  const sdk = await loadSdk(ctx);

  // SDK 尾部已经自动调了 init()（加载 WASM）。等它就绪：
  // init() 内部 `yield this.wasmModule.default()` 是 async 的，
  // 这里轮询 isInitialized，最多等 30s。
  const deadline = Date.now() + 30000;
  while (!sdk.isInitialized && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 20));
  }
  if (!sdk.isInitialized) {
    // 兜底：自己再调一次 init()
    await sdk.init();
  }
  if (!sdk.isInitialized) {
    throw new Error('WASM 初始化失败：isSupportedWASM=' + sdk.isSupportedWASM);
  }

  const result = encrypt(sdk, envInfo, publicKeyData, userId, securityInfo);
  process.stdout.write(JSON.stringify(result));
  process.exit(0);
}

main().catch((e) => {
  process.stderr.write('BRIDGE_ERROR: ' + (e && e.stack || e) + '\n');
  process.exit(1);
});
