"""风控指纹上报（`ExClimbWuzhi` / `ExGetAxe` / `ExClimbCongLing`）.

**为什么必须实现**：浏览器每次打开主站或登录页都会打这三发
（2026-08-16 实抓：reqid=39 ExGetAxe、40 ExClimbCongLing、133/305 ExClimbWuzhi）。
本项目此前一发都不发——只读接口确实不受影响，但这是"设备从未上报过指纹"的
明显特征，频繁写操作时会累积风险。

三个端点的分工（实抓确认，**新旧两版是并存的，不是替换关系**）：

===========================  ====  ==============================================
端点                          方法  作用
===========================  ====  ==============================================
`ExClimbWuzhi`                POST  **明文**指纹上报，body 是 `{"payload":"<JSON 串>"}`
`ExGetAxe`                    GET   取 RSA 公钥（`version` / `public_key` / `deadline`）
`ExClimbCongLing`             POST  **加密**指纹上报，RSA 加密 AES 密钥 + AES 加密载荷
===========================  ====  ==============================================

需要注意：`ExClimbWuzhi` 并未被 `ExClimbCongLing` 替换；
同一个页面里两个都发，先发明文那个，再发加密那个。

指纹字段的键名是四位十六进制的混淆名，值的含义从实抓逐个对出来（见
`FINGER_FIELD_NOTES`）。这份明文载荷同时解开了两个悬案：

- **`buvid_fp` 的真实载荷就是 `3c43` 这个子对象**，murmur3_x64_128 的输入；
- **`qrcode/poll` 的 `b_ret` 就是 `13ab` 字段**，即 canvas 指纹的尾串，
  不是此前猜的"二维码 canvas 渲染证明"。
"""

import json
import os
import subprocess

from builder.header import HeaderBuilder, HeaderType
from builder.params import Params
from utils.common_util import now_ms
from utils.fingerprint import get_profile
from utils.http_util import get_json, post_json

API = 'https://api.bilibili.com'
WUZHI_API = f'{API}/x/internal/gaia-gateway/ExClimbWuzhi'
AXE_API = f'{API}/x/internal/gaia-gateway/ExGetAxe'
CONGLING_API = f'{API}/x/internal/gaia-gateway/ExClimbCongLing'

# ---------------------------------------------------------------------------
# ExClimbCongLing 的 SDK 常量。全部读自 _gt/bili-sc-sdk.js
# （来源 https://s1.hdslb.com/bfs/seed/jinkela/short/minntaki-wasm-sdk/
#  bili-sc-sdk.umd.js，377583 字节，UMD 压缩单行）。
# 括号里的 @N 是该常量在那份文件里的字节偏移，方便复核。
# ---------------------------------------------------------------------------

# @354518 `const EA="333.40181",iA="0.1.15",oA="application/json; charset=utf-8; */*;",
#          DA="zh-CN,zh;q=0.8,en;q=0.7"`
SDK_VERSION = '0.1.15'          # iA → envInfo.sdk_version
SDK_ACCEPT = 'application/json; charset=utf-8; */*;'        # oA → envInfo.accept
SDK_ACCEPT_LANGUAGE = 'zh-CN,zh;q=0.8,en;q=0.7'             # DA → envInfo.accept_language
SDK_SPM_PREFIX = '333.40181'    # EA（埋点 spm 前缀，非 envInfo 字段，仅备查）

# NA() 的探测器数组 MA 共 21 个元素（@355170~367107，用 _gt/count_ma.js 括号平衡
# 解析得出）。每个返回 0/1，NA() 把它们按下标顺序 join 成 21 位字符串。
SECURITY_INFO_LEN = 21

# Node 桥接脚本
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BRIDGE = os.path.join(_ROOT, 'tools', 'sc_encrypt_bridge.js')
# WASM 加密要用的风控 SDK。它是从 CDN 下载的第三方资产，不入库（`_gt/` 已 gitignore），
# 所以多数环境里并不存在——`congling_available()` 就是为了判断这一点。
_SDK = os.path.join(_ROOT, '_gt', 'bili-sc-sdk.js')
# Node 可执行文件，可用环境变量覆盖
NODE_BIN = os.environ.get('NODE_BIN', 'node')


def congling_available() -> bool:
    """加密指纹上报能不能跑：需要 Node 桥接脚本与本地那份风控 SDK 都在.

    缺任一个就只能打明文那一发。已实证明文足够——不带加密上报，
    投稿/互动等写接口照样 code=0。
    """
    return os.path.exists(_BRIDGE) and os.path.exists(_SDK)


# 混淆键名 → 含义。全部来自 2026-08-16 实抓 body 的逐字段比对，
# 不是猜的：每一条都能在同一份 payload 里找到对应的浏览器可观测值。
FINGER_FIELD_NOTES = {
    '3064': '固定 1（协议版本）',
    '5062': '毫秒时间戳（字符串）',
    '03bf': 'urlencode 后的 correspond 页面 URL',
    '39c8': '埋点位，固定 333.1193.fp.risk',
    '6e7c': '固定 "0x0"',
    '3c43': '指纹主体（buvid_fp 的 murmur3 输入就是它）',
    '54ef': '固定 "{}"',
    '8b94': 'urlencode 后的 referrer',
    'df35': '_uuid Cookie 原值',
    '07a4': '语言，zh-CN',
    'db46': '固定 0',
    # --- 3c43 内部
    '5766': 'screen.colorDepth',
    'b8ce': 'userAgent',
    '1c57': 'deviceMemory 相关（实抓 32）',
    '0bd0': 'hardwareConcurrency（实抓 20）',
    '748e': '[screen.width, screen.height]',
    'd61f': '[availWidth, availHeight]',
    'fc9d': '时区偏移分钟数（东八区 -480）',
    '6aa9': 'Intl 时区名',
    'adca': 'navigator.platform',
    '80c9': 'navigator.plugins 三元组数组',
    '13ab': 'canvas 指纹尾串（= qrcode/poll 的 b_ret）',
    'bfe9': '第二个 canvas 指纹尾串',
    'a3c1': 'WebGL 参数与扩展的长数组',
    '6bc5': 'unmasked vendor + "~" + unmasked renderer',
    '52cd': '固定 [10,0,0]',
    'a658': '字体探测结果（实抓为空数组）',
    'd02f': 'AudioContext 指纹（浮点字符串）',
}


def build_finger_payload(auth=None, referer: str = 'https://www.bilibili.com/',
                         correspond_url: str = '') -> dict:
    """按浏览器实抓的字段与顺序拼出 `3c43` 指纹主体外的完整 payload 对象.

    键序照抄实抓（JSON 对象顺序在 B站 这里是有意义的，
    因为服务端会拿整串去比对，也因为 buvid_fp 是对它做 murmur3）。

    :param auth: BiliAuth object，用来取 `_uuid`；可为 None.
    :param referer: 上报时的来源页，进 `8b94`（会被 urlencode）.
    :param correspond_url: correspond 页面 URL，进 `03bf`；留空则不带路径部分.
    :return: 可直接 json.dumps 的 dict.
    """
    import urllib.parse

    profile = get_profile()
    uuid = (auth.cookie.get('_uuid') if auth else '') or ''
    width, height = profile['screen_width'], profile['screen_height']
    avail_w, avail_h = width, height - 48

    return {
        '3064': 1,
        '5062': str(now_ms()),
        '03bf': urllib.parse.quote(correspond_url, safe=''),
        '39c8': '333.1193.fp.risk',
        '34f1': '',
        'd402': '',
        '654a': '',
        '6e7c': '0x0',
        '3c43': build_finger_core(),
        '54ef': '{}',
        '8b94': urllib.parse.quote(referer, safe=''),
        'df35': uuid,
        '07a4': 'zh-CN',
        '5f45': None,
        'db46': 0,
    }


def build_finger_core() -> dict:
    """`3c43` 指纹主体，也就是 `buvid_fp` 的 murmur3 输入.

    字段与顺序逐个对齐实抓；取值来自 `utils/fingerprint.py` 的设备画像，
    保证同一进程内自洽（UA 说 Windows，platform 就得是 Win32）。
    """
    profile = get_profile()
    width, height = profile['screen_width'], profile['screen_height']
    return {
        '2673': 0,
        '5766': profile['color_depth'],
        '6527': 0,
        '7003': 1,
        '807e': 1,
        'b8ce': profile['ua'],
        '641c': 0,
        '07a4': 'zh-CN',
        '1c57': 32,
        '0bd0': profile['hardware_concurrency'],
        '748e': [width, height],
        'd61f': [width, height - 48],
        'fc9d': -480,
        '6aa9': profile['timezone'],
        '75b8': 1,
        '3b21': 1,
        '8a1c': 0,
        'd52f': 'not available',
        'adca': 'Win32',
        '80c9': PLUGINS,
        '13ab': CANVAS_1,
        'bfe9': CANVAS_2,
        'a3c1': WEBGL_PARAMS,
        '6bc5': f"{profile['webgl_vendor']}~{profile['webgl_renderer']}",
        'ed31': 0,
        '72bd': 0,
        '097b': 0,
        '52cd': [10, 0, 0],
        'a658': [],
        'd02f': AUDIO_FP,
    }


# 实抓的 Chrome 插件列表（Chrome 内置 PDF 的五个别名，所有 Chrome 都一样）
PLUGINS = [
    ['PDF Viewer', 'Portable Document Format',
     [['application/pdf', 'pdf'], ['text/pdf', 'pdf']]],
    ['Chrome PDF Viewer', 'Portable Document Format',
     [['application/pdf', 'pdf'], ['text/pdf', 'pdf']]],
    ['Chromium PDF Viewer', 'Portable Document Format',
     [['application/pdf', 'pdf'], ['text/pdf', 'pdf']]],
    ['Microsoft Edge PDF Viewer', 'Portable Document Format',
     [['application/pdf', 'pdf'], ['text/pdf', 'pdf']]],
    ['WebKit built-in PDF', 'Portable Document Format',
     [['application/pdf', 'pdf'], ['text/pdf', 'pdf']]],
]

# 两个 canvas 指纹尾串。**13ab 同时就是 qrcode/poll 的 b_ret 参数**，
# 两处实抓值完全一致，这是它们同源的直接证据。
CANVAS_1 = 'mW9qAAAAAElFTkSuQmCC'
CANVAS_2 = '//TgNIfAAAAAZJREFUAwBde+3wgcxEHQAAAABJRU5ErkJggg=='

# AudioContext 指纹，同一台机器稳定
AUDIO_FP = '124.04347527516074'

# WebGL 参数数组。第一项是分号分隔的扩展列表，其余是逐项能力值。
# 照抄实抓，改任何一项都会让 buvid_fp 变化。
WEBGL_PARAMS = [
    ('extensions:ANGLE_instanced_arrays;EXT_blend_minmax;EXT_clip_control;'
     'EXT_color_buffer_half_float;EXT_depth_clamp;EXT_disjoint_timer_query;'
     'EXT_float_blend;EXT_frag_depth;EXT_polygon_offset_clamp;'
     'EXT_shader_texture_lod;EXT_texture_compression_bptc;'
     'EXT_texture_compression_rgtc;EXT_texture_filter_anisotropic;'
     'EXT_texture_mirror_clamp_to_edge;EXT_sRGB;KHR_parallel_shader_compile;'
     'OES_element_index_uint;OES_fbo_render_mipmap;OES_standard_derivatives;'
     'OES_texture_float;OES_texture_float_linear;OES_texture_half_float;'
     'OES_texture_half_float_linear;OES_vertex_array_object;'
     'WEBGL_blend_func_extended;WEBGL_color_buffer_float;'
     'WEBGL_compressed_texture_s3tc;WEBGL_compressed_texture_s3tc_srgb;'
     'WEBGL_debug_renderer_info;WEBGL_debug_shaders;WEBGL_depth_texture;'
     'WEBGL_draw_buffers;WEBGL_lose_context;WEBGL_multi_draw;'
     'WEBGL_polygon_mode'),
    'webgl aliased line width range:[1, 1]',
    'webgl aliased point size range:[1, 1024]',
    'webgl alpha bits:8',
    'webgl antialiasing:yes',
    'webgl blue bits:8',
    'webgl depth bits:24',
    'webgl green bits:8',
    'webgl max anisotropy:16',
    'webgl max combined texture image units:32',
    'webgl max cube map texture size:16384',
    'webgl max fragment uniform vectors:1024',
    'webgl max render buffer size:16384',
    'webgl max texture image units:16',
    'webgl max texture size:16384',
    'webgl max varying vectors:30',
    'webgl max vertex attribs:16',
    'webgl max vertex texture image units:16',
    'webgl max vertex uniform vectors:4095',
    'webgl max viewport dims:[32767, 32767]',
    'webgl red bits:8',
    'webgl renderer:WebKit WebGL',
    'webgl shading language version:WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)',
    'webgl stencil bits:0',
    'webgl vendor:WebKit',
    'webgl version:WebGL 1.0 (OpenGL ES 2.0 Chromium)',
    'webgl unmasked vendor:Google Inc. (NVIDIA)',
    ('webgl unmasked renderer:ANGLE (NVIDIA, NVIDIA GeForce RTX 5060 Ti '
     '(0x00002D04) Direct3D11 vs_5_0 ps_5_0, D3D11)'),
    'webgl vertex shader high float precision:23',
    'webgl vertex shader high float precision rangeMin:127',
    'webgl vertex shader high float precision rangeMax:127',
    'webgl vertex shader medium float precision:23',
    'webgl vertex shader medium float precision rangeMin:127',
    'webgl vertex shader medium float precision rangeMax:127',
    'webgl vertex shader low float precision:23',
    'webgl vertex shader low float precision rangeMin:127',
    'webgl vertex shader low float precision rangeMax:127',
    'webgl fragment shader high float precision:23',
    'webgl fragment shader high float precision rangeMin:127',
    'webgl fragment shader high float precision rangeMax:127',
    'webgl fragment shader medium float precision:23',
    'webgl fragment shader medium float precision rangeMin:127',
    'webgl fragment shader medium float precision rangeMax:127',
    'webgl fragment shader low float precision:23',
    'webgl fragment shader low float precision rangeMin:127',
    'webgl fragment shader low float precision rangeMax:127',
    'webgl vertex shader high int precision:0',
    'webgl vertex shader high int precision rangeMin:31',
    'webgl vertex shader high int precision rangeMax:30',
    'webgl vertex shader medium int precision:0',
    'webgl vertex shader medium int precision rangeMin:31',
    'webgl vertex shader medium int precision rangeMax:30',
    'webgl vertex shader low int precision:0',
    'webgl vertex shader low int precision rangeMin:31',
    'webgl vertex shader low int precision rangeMax:30',
    'webgl fragment shader high int precision:0',
    'webgl fragment shader high int precision rangeMin:31',
    'webgl fragment shader high int precision rangeMax:30',
    'webgl fragment shader medium int precision:0',
    'webgl fragment shader medium int precision rangeMin:31',
    'webgl fragment shader medium int precision rangeMax:30',
    'webgl fragment shader low int precision:0',
    'webgl fragment shader low int precision rangeMin:31',
    'webgl fragment shader low int precision rangeMax:30',
]


class BiliGaiaApi:
    """风控网关三个端点."""

    @staticmethod
    def climb_wuzhi(auth, referer: str = 'https://www.bilibili.com/',
                    correspond_url: str = '') -> dict:
        """明文指纹上报.

        实抓（reqid=133）：`content-type: application/json;charset=UTF-8`、
        `accept: */*`，body 是 `{"payload": "<把指纹对象 json.dumps 后的字符串>"}`
        ——注意 payload 是**字符串套字符串**，不是嵌套对象。

        :param auth: BiliAuth object.
        :param referer: 来源页，进 `8b94`.
        :param correspond_url: correspond 页 URL，进 `03bf`.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.POST, accept='*/*').set_referer(referer).get()
        headers['content-type'] = 'application/json;charset=UTF-8'
        payload = build_finger_payload(auth, referer=referer,
                                       correspond_url=correspond_url)
        body = {'payload': json.dumps(payload, separators=(',', ':'),
                                      ensure_ascii=False)}
        return post_json(auth, WUZHI_API, headers=headers, json=body)

    @staticmethod
    def get_axe(auth) -> dict:
        """取 RSA 公钥，`ExClimbCongLing` 的加密要用它.

        实抓（reqid=39）：GET，无参数，返回
        `{"version":"v1","public_key":"-----BEGIN rsa public key-----...","deadline":...}`。

        :param auth: BiliAuth object.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET, accept='*/*').get()
        return get_json(auth, AXE_API, headers=headers)

    @staticmethod
    def climb_congling(auth, referer: str = 'https://www.bilibili.com/',
                       public_key_data: dict | None = None) -> dict:
        """加密指纹上报.

        body 结构（实抓，content-length 约 9.6KB）::

            {"header":{"encode_type":2,"payload_type":4,
                       "encoded_aes_key":"<base64, RSA 加密的 AES 密钥>",
                       "ts":<毫秒时间戳>,"encoded_version":"v1"},
             "encrypt_payload":"<base64, AES 加密的指纹 JSON>"}

        加密**全部在 SDK 的 WASM 里**（wasm-bindgen 胶水 @315750，WASM 内嵌 @6020，
        231536 字节）。通过 `tools/sc_encrypt_bridge.js` 在 Node 进程里调用。

        :param auth: BiliAuth object.
        :param referer: 来源页.
        :param public_key_data: ExGetAxe 返回的整个 data 对象；留空则自动去取.
        :return: JSON.
        """
        if not public_key_data:
            axe = BiliGaiaApi.get_axe(auth)
            public_key_data = axe.get('data') or {}
            if not public_key_data.get('public_key'):
                raise RuntimeError(f'ExGetAxe 未返回 public_key: {axe}')

        ck = (auth.cookie if auth else {}) or {}
        env_info = build_env_info(auth, collect_api='spontaneous', path=referer)
        security_info = build_security_info()
        user_id = ck.get('buvid3', '')

        encoded_key, encrypt_payload = encrypt_finger_wasm(
            env_info, public_key_data, user_id, security_info,
        )

        headers = HeaderBuilder.build(HeaderType.POST, accept='*/*').set_referer(referer).get()
        headers['content-type'] = 'application/json;charset=UTF-8'
        body = {
            'header': {
                'encode_type': 2, 'payload_type': 4,
                'encoded_aes_key': encoded_key,
                'ts': now_ms(),
                'encoded_version': public_key_data.get('version', 'v1'),
            },
            'encrypt_payload': encrypt_payload,
        }
        return post_json(auth, CONGLING_API, headers=headers, json=body)


def encrypt_finger(plaintext: str, public_key_pem: str) -> tuple:
    """⚠️ **已废弃：这是被证伪的猜测实现，不要用**.

    早先按 `encode_type=2` / `payload_type=4` 的"常规含义"猜的
    AES-128-CBC + RSA-PKCS1v15，服务端返回 **-400**，证明猜错了。

    真实算法在 SDK 的 WASM 里，无法用标准库拼出来——正确实现见
    `encrypt_finger_wasm()`（走 `tools/sc_encrypt_bridge.js` 调 WASM）。

    保留此函数仅为记录"哪条路走不通"，避免以后再猜一遍。

    :param plaintext: 指纹 JSON 串.
    :param public_key_pem: PEM 公钥.
    :return: (encoded_aes_key, encrypt_payload)，都是 base64.
    """
    import base64
    import os

    from cryptography.hazmat.primitives import padding as sym_padding
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    aes_key, iv = os.urandom(16), os.urandom(16)
    padder = sym_padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode('utf-8')) + padder.finalize()
    encryptor = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).encryptor()
    ciphertext = iv + encryptor.update(data) + encryptor.finalize()

    pem = public_key_pem.replace('rsa public key', 'RSA PUBLIC KEY')
    pub = serialization.load_pem_public_key(pem.encode())
    encoded_key = pub.encrypt(aes_key, asym_padding.PKCS1v15())

    return (base64.b64encode(encoded_key).decode(),
            base64.b64encode(ciphertext).decode())


def finger_b_ret() -> str:
    """`qrcode/poll` 的 `b_ret` 参数.

    实证：它就是指纹里的 `13ab`（canvas 指纹尾串），
    两处实抓值逐字符相同，此前"二维码 canvas 渲染证明"的猜测是错的。
    """
    return CANVAS_1


# ===========================================================================
# ExClimbCongLing：envInfo / securityInfo / WASM 加密
# ===========================================================================

def build_security_info() -> str:
    """`securityInfoStr`——SDK 里 `NA()` 的返回值.

    **不是随机串，也不是哈希**：它是一个 21 位的 "0"/"1" 字符串，
    每一位对应 `MA` 数组里一个反自动化探测器的返回值（1=检测到异常）。

    源码依据（`_gt/bili-sc-sdk.js`）：

    - `NA()` @367109::

          function NA(){return w(this,null,function*(){try{
            const A=new Array(MA.length).fill("1").join("");
            return(yield (function(A){... 逐位执行 MA[B] ...})(A)).join("")
          }...

      即：先造一个全 "1" 的掩码（表示"每个探测器都要跑"），把每个
      `MA[B]()` 的结果写进 `I[B]`，最后 `I.join("")`。探测器抛异常时该位记 0。

    - `MA` 数组 @355170~367107，用括号平衡解析出**恰好 21 个**顶层元素
      （`_gt/count_ma.js`）。逐个是：

      ===  ==========================================================
      下标  探测内容
      ===  ==========================================================
      0    navigator.webdriver / $cdc_ / _phantom / selenium 等全局
      1    UA 含 jsdom|Node.js|happy-dom；window.name==="nodejs"；无头窗口尺寸
      2    Object.defineProperty 等原生函数是否被改写（toString 检测）
      3    Intl 时区与 navigator.language 的地区是否矛盾
      4    浏览器隐私模式（AA() 返回 isPrivate）
      5    outerWidth/innerWidth 等窗口尺寸是否异常
      6    UA 是否 WebView
      7    页面上的 script[src]/link[href] 是否全部来自 B站 白名单域
      8    webdriver 相关全局变量与 UA 关键字
      9    广告拦截 / canvas 稳定性 / AudioContext / 字体 / new Function 可用性
      10   页面是否存在 angular/jQuery/React/Vue
      11   UA 是否是微信/微博/QQ/支付宝/淘宝/B站/抖音/贴吧 内置浏览器
      12   productSub 与 eval.toString().length 是否与 UA 声称的浏览器矛盾
      13   navigator.languages 与 navigator.language 是否矛盾
      14   UA / oscpu / platform / touch 能力 是否与操作系统矛盾
      15   screen.availWidth/width 比值是否 > 1.1
      16   window.indexedDB 是否可用
      17   `IA`——cookieEnabled 探测（同时用作 envInfo.cookieEnabled）
      18   localStorage 是否可写
      19   window.openDatabase 或 indexedDB 是否存在
      20   sessionStorage 是否可写
      ===  ==========================================================

    这里返回一台**正常 Windows Chrome** 应有的结果：所有"是否被检测到自动化"
    的位为 0，所有"能力是否可用"的位为 1。

    - 下标 16/17/18/19/20 是能力探测（indexedDB / cookie / localStorage /
      openDatabase / sessionStorage），真实浏览器全为 1。
    - 其余下标是异常检测，正常浏览器全为 0。

    :return: 21 位的 0/1 字符串.
    """
    bits = ['0'] * SECURITY_INFO_LEN
    # 能力探测位：正常浏览器都为 1
    for i in (16, 17, 18, 19, 20):
        bits[i] = '1'
    return ''.join(bits)


def build_env_info(auth=None, *, collect_api: str = 'spontaneous',
                   path: str = 'https://www.bilibili.com/') -> dict:
    """`envInfo`——`encrypt_data` 的第一个参数.

    **和 `ExClimbWuzhi` 的明文载荷不是一个东西**：那边是 `3c43` 这种四位十六进制
    混淆键名，这边是可读键名。它由三部分拼成（SDK `getEnvironmentInfo()` @370901
    与 `collectDeviceInfo()` @373568）::

        A(i(i({buvid_fp: B}, g), Q))          // getEnvironmentInfo 的返回
        this.envInfo = o(i({}, I), {collect_api, buvid, buvid4, mid, sdk_version})

    1. `buvid_fp`：Cookie 里的 buvid_fp（SDK 里若 Cookie 缺失会用
       `b.x64hash128(所有组件值拼接, 31)` 现算并种 Cookie）。
    2. `g`：内嵌 Fingerprint2 **v2.1.4**（`N.VERSION="2.1.4"` @346699）
       `b.get()` 的组件，按 key 摊平。其中两个 key 被特殊处理（@370901）::

           case "canvas": g.canvas = String(value[1]).slice(-20)
           case "webgl":  g.webgl_str    = String(value[0]).slice(-50)
                          g.webgl_params = value.slice(1).map(yA)
           default:       g[key] = Array.isArray(value) ? value.map(yA) : yA(value)

       `yA` @367982 是 `A=>"true"===A?1:"false"===A?0:"boolean"!=typeof A?A:Number(A)`，
       即把布尔与 "true"/"false" 字符串统一成 1/0。
    3. `Q`：一批浏览器元信息（@372350 起的对象字面量）+ `hA()`
       （WebGL vendor/renderer，@368053）+ cookieEnabled / browser_build_version /
       notify_message_api / spmid / path / lsid / b_nut_h。

    Fingerprint2 默认配置里 `excludes` 排除了 5 个组件
    （`enumerateDevices` / `pixelRatio` / `doNotTrack` / `fontsFlash` / `adBlock`），
    所以实际只有 28 个组件出现在 envInfo 里。

    :param auth: BiliAuth object，用来取 buvid3 / buvid4 / DedeUserID / buvid_fp 等 Cookie.
    :param collect_api: 触发来源，SDK 里周期上报传 "spontaneous".
    :param path: 当前页 URL，进 `path` 字段（源码是 document.location.href）.
    :return: 可直接 json.dumps 的 dict.
    """
    profile = get_profile()
    ck = (auth.cookie if auth else {}) or {}
    ua = profile['ua']
    width, height = profile['screen_width'], profile['screen_height']
    avail_w, avail_h = width, height - 48
    color_depth = profile['color_depth']

    # b_nut_h：nA() @367809——把 b_nut（秒级时间戳）截到「整点」再转回秒
    b_nut_h = None
    b_nut = ck.get('b_nut')
    if b_nut:
        import datetime
        dt = datetime.datetime.fromtimestamp(int(b_nut))
        b_nut_h = int(dt.replace(minute=0, second=0, microsecond=0).timestamp())

    env = {
        # --- buvid_fp（Cookie 原值）---
        'buvid_fp': ck.get('buvid_fp', ''),

        # --- Fingerprint2 v2.1.4 的 28 个活跃组件 ---
        # b.get(callback) 的 default 分支：Array.isArray(value) ? value.join(",") : value
        # 所以数组类型的组件在 callback 里被 join 成了字符串再塞进 g。
        'userAgent': ua,
        'webdriver': 0,                       # yA(false) → 0
        'language': 'zh-CN',
        'colorDepth': color_depth,
        'deviceMemory': profile['device_memory'],
        'hardwareConcurrency': profile['hardware_concurrency'],
        # detectScreenOrientation:1 时 FP2 会对 [w,h] 排序后 join(",")
        'screenResolution': f'{width},{height}',
        # availableScreenResolution value 是 [availHeight, availWidth]，join(",")
        'availableScreenResolution': f'{avail_h},{avail_w}',
        'timezoneOffset': -480,               # 东八区
        'timezone': profile['timezone'],
        'sessionStorage': 1,
        'localStorage': 1,
        'indexedDb': 1,
        'addBehavior': 0,                     # IE 专属，Chrome 为 false→0
        'openDatabase': 0,                    # Chrome 已移除 WebSQL
        'cpuClass': 'not available',          # IE 专属
        'platform': 'Win32',
        'plugins': PLUGINS_FP2_STR,
        'canvas': CANVAS_1,                   # String(value[1]).slice(-20)
        'webgl_str': WEBGL_STR,               # String(value[0]).slice(-50)
        'webgl_params': WEBGL_PARAMS,         # value.slice(1).map(yA)，字符串数组
        # webglVendorAndRenderer：FP2 组件是 "vendor~renderer"，但 hA()（Q 部分）
        # 会以 "vendor|renderer|version" 格式覆盖它，最终值看下面 Q 部分。
        'hasLiedLanguages': 0,
        'hasLiedResolution': 0,
        'hasLiedOs': 0,
        'hasLiedBrowser': 0,
        # touchSupport value=[maxTouchPoints, hasTouchEvent, hasOntouchstart]，join(",")
        'touchSupport': '0,0,0',
        'fonts': [],
        'audio': AUDIO_FP,

        # --- @372350 起的浏览器元信息 ---
        'os_source': 'pc',
        'nav_oscpu': None,                    # Chrome 没有 navigator.oscpu
        'nav_languages': ['zh-CN', 'zh', 'en'],
        'nav_productsub': '20030107',
        'eval_length': 33,                    # Chrome 的 eval.toString().length
        'user_agent': ua,
        # [screen.height, screen.width, colorDepth, pixelDepth, devicePixelRatio].join("x")
        'screen_size_info': f'{height}x{width}x{color_depth}x{color_depth}x1',
        # [innerWidth, innerHeight, outerWidth, outerHeight].join("x")
        'window_size_info': f'{width}x{avail_h - 91}x{width}x{avail_h}',
        'local_time': now_ms(),
        'os_platform': 'Win32',
        'accept': SDK_ACCEPT,
        'accept_encoding': 'gzip, deflate, br, zstd',
        'accept_language': SDK_ACCEPT_LANGUAGE,

        # --- hA() @368053：覆盖 webglVendorAndRenderer 为 vendor|renderer|version ---
        'webglVendorAndRenderer': '|'.join([
            profile['webgl_vendor'], profile['webgl_renderer'], profile['webgl_version'],
        ]),

        # --- 尾部字段 ---
        'cookieEnabled': 1,
        # navigator.appVersion == UA 去掉开头的 "Mozilla/"
        'browser_build_version': ua.split('Mozilla/', 1)[-1],
        'notify_message_api': 'default',      # Notification.permission
        'spmid': '',                          # QA()：<meta name="spm_prefix"> 的 content
        'path': path,
        'lsid': ck.get('b_lsid', ''),
        'b_nut_h': b_nut_h,

        # --- collectDeviceInfo() @373568 追加 ---
        'collect_api': collect_api,
        'buvid': ck.get('buvid3', ''),
        'buvid4': ck.get('buvid4', ''),
        'mid': ck.get('DedeUserID', ''),
        'sdk_version': SDK_VERSION,
    }
    return env


def encrypt_finger_wasm(env_info: dict, public_key_data: dict,
                        user_id: str, security_info: str) -> tuple:
    """调 Node 桥接跑 SDK 的 WASM `encrypt_data`——**这是已验证可用的实现**.

    加密全部在 WASM 里（`_gt/bili-sc-sdk.js` @315750 是 wasm-bindgen 胶水，
    WASM 本体以 `data:application/wasm;base64,` 内嵌在 @6020，解出 231536 字节）。
    调用点 @373913::

        this.wasmModule.encrypt_data(JSON.stringify(A), JSON.stringify(this.publicKeyData), I, g)

    返回一个 **JSON 字符串**，`JSON.parse` 后取 `.key` / `.data`。

    :param env_info: build_env_info() 的结果.
    :param public_key_data: ExGetAxe 返回的整个 data 对象（version/public_key/deadline）.
    :param user_id: buvid3.
    :param security_info: build_security_info() 的 21 位 0/1 串.
    :return: (key, data)，分别填 header.encoded_aes_key 与 encrypt_payload.
    """
    payload = json.dumps({
        'envInfo': env_info,
        'publicKeyData': public_key_data,
        'userId': user_id,
        'securityInfo': security_info,
    }, ensure_ascii=False)

    proc = subprocess.run(
        [NODE_BIN, _BRIDGE], input=payload,
        capture_output=True, text=True, encoding='utf-8', timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f'Node 桥接失败（rc={proc.returncode}）: '
                           f'{(proc.stderr or "").strip()[:500]}')
    out = json.loads(proc.stdout)
    return out['key'], out['data']


# Fingerprint2 的 plugins 组件：FP2 内部每个插件被格式化成
# "名字::描述::mime类型~后缀,..."，整个组件是这些串组成的数组；
# 到了 getEnvironmentInfo 的 default 分支又被 join(",")。
_PLUGINS_FP2_ITEMS = [
    'PDF Viewer::Portable Document Format::application/pdf~pdf,text/pdf~pdf',
    'Chrome PDF Viewer::Portable Document Format::application/pdf~pdf,text/pdf~pdf',
    'Chromium PDF Viewer::Portable Document Format::application/pdf~pdf,text/pdf~pdf',
    'Microsoft Edge PDF Viewer::Portable Document Format::application/pdf~pdf,text/pdf~pdf',
    'WebKit built-in PDF::Portable Document Format::application/pdf~pdf,text/pdf~pdf',
]
PLUGINS_FP2_STR = ','.join(_PLUGINS_FP2_ITEMS)

# webgl_str：FP2 webgl 组件的 value[0]（canvas.toDataURL()）取**末 50 字符**
# （源码 @370901：`String(A.value?.[0]).slice(-50)`）。
WEBGL_STR = CANVAS_2[-50:]
