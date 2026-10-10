"""极验 v3 `w` 参数的加密原语（纯 Python）.

`w` = 自定义 base64(AES-CBC 密文) + 可选 RSA(AES 密钥) 十六进制段。
fullpage 首发使用 AES+RSA，随后 ajax 复用该 AES；切换到 click 插件后会生成
新 AES，并在 click ajax 再附一段 RSA 密钥。

常量来源（2026-08-16）：fullpage.js 的字符串表是加密的，静态搜不到任何明文；
在浏览器中调用解码器 `window.Vwtrj.$_CV(index)` 后取得关键常量：

    [   0] "0000000000000000"   AES 的 IV
    [  14] "Pkcs7"  [17] "CBC"  [64] "AES"      CryptoJS 组合
    [ 468] 自定义 base64 字母表（标准表把 "+/" 换成 "()"）
    [ 559] "10001"              RSA 公钥指数
    [ 586] RSA-1024 模数
    [1101] "|jordan"            seccode 后缀
    [1126] "aeskey"

本模块包含完整链路需要的加密原语与载荷编码辅助函数。
"""

import random

# ---------------------------------------------------------------- 提取出的常量

AES_IV = b'0000000000000000'

RSA_N_HEX = (
    '00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74'
    'C7977D02DC1D9451F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F'
    '09AF627715919221AEF91899CAE08C0D686D748B20A3603BE2318CA6BC2B5970'
    '6592A9219D0BF05C9F65023A21D2330807252AE0066D59CEEFA5F2748EA80BAB81'
)
RSA_E = 0x10001

# 标准 base64 表把 '+/' 换成 '()'，填充符用 '.'
B64_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789()'
B64_PAD = '.'

# 极验的 base64 **不是**标准算法换字母表：它用四个掩码从每 24 位分组里
# 「按位挑选」出四个 6 位下标，而不是顺序切分。四个掩码各含 6 个 1 位、
# 互不重叠、合起来正好是 0xFFFFFF，所以本质是一次固定的位置换。
# 常量取自运行时导出的编码器对象（$_GCC/$_GDn/$_GE_/$_GFq/$_GGH）。
B64_MASKS = (0x6F0000, 0x90B400, 0x004B14, 0x0000EB)
B64_WIDTH = 24

# 鼠标轨迹 / tt 字段用的另一张表
TRACK_ALPHABET = '()*,-./0123456789:?@ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz~'

SECCODE_SUFFIX = '|jordan'
GEETEST_VERSION = '9.2.0-guwyxh'


def gen_aes_key(length: int = 16) -> str:
    """随机 AES 密钥。实抓样本形如 `8b94c72ddc2a57a9`，即 16 位小写十六进制."""
    return ''.join(random.choice('0123456789abcdef') for _ in range(length))


def aes_encrypt(plaintext: str, key: str) -> bytes:
    """CryptoJS 等价的 AES-128-CBC + Pkcs7，IV 固定 16 个 '0'.

    :param plaintext: 明文 JSON 串.
    :param key: 16 位密钥.
    :return: 密文字节.
    """
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode('utf-8')) + padder.finalize()
    cipher = Cipher(algorithms.AES(key.encode()), modes.CBC(AES_IV))
    enc = cipher.encryptor()
    return enc.update(data) + enc.finalize()


def _gather_bits(value: int, mask: int, width: int = B64_WIDTH) -> int:
    """从高位向低位扫，把 mask 为 1 的那些位从 value 里挑出来依次拼成新数.

    对应 JS 里的内层函数（`$_HBZ(e,t)` 就是 `e>>t&1`）：

        for (var n=0, r=width-1; 0<=r; r-=1)
            1 === bit(mask, r) && (n = (n<<1) + bit(value, r));
    """
    n = 0
    for r in range(width - 1, -1, -1):
        if (mask >> r) & 1:
            n = (n << 1) + ((value >> r) & 1)
    return n


def custom_b64(data: bytes) -> str:
    """极验自定义 base64（`$_HCB` 的等价实现）.

    与标准 base64 的差别在于 6 位下标不是顺序切出来的，而是按 B64_MASKS
    做位挑选；尾部不足 3 字节时补 '.'（一个 '.' 对应少的一个字节）。

    :param data: 待编码字节.
    :return: 编码串（含尾部填充）。
    """
    res, end, size = [], '', len(data)
    for a in range(0, size, 3):
        if a + 2 < size:
            v = (data[a] << 16) + (data[a + 1] << 8) + data[a + 2]
            take = 4
        elif size % 3 == 2:
            v = (data[a] << 16) + (data[a + 1] << 8)
            take, end = 3, B64_PAD
        else:
            v = data[a] << 16
            take, end = 2, B64_PAD * 2
        res += [B64_ALPHABET[_gather_bits(v, m)] for m in B64_MASKS[:take]]
    return ''.join(res) + end


def rsa_encrypt_key(key: str) -> str:
    """用极验公钥加密 AES 密钥，返回十六进制（PKCS#1 v1.5）.

    :param key: 明文 AES 密钥.
    :return: 256 位十六进制密文.
    """
    from cryptography.hazmat.primitives.asymmetric import padding as apad
    from cryptography.hazmat.primitives.asymmetric import rsa

    n = int(RSA_N_HEX, 16)
    pub = rsa.RSAPublicNumbers(RSA_E, n).public_key()
    cipher = pub.encrypt(key.encode(), apad.PKCS1v15())
    return cipher.hex()


def cs_cipher(track: str, c: list, s: str) -> str:
    """`tt` 字段用的插入式混淆，密钥就是 get.php 返回的 `c` 数组与 `s` 串.

    反混淆后的原始实现（fullpage.js）：

        var s0 = c[0], a = c[2], _ = c[4], o = 0, i = track;
        while (r = s.substr(o, 2)) {
            o += 2;
            var ch = parseInt(r, 16);
            var u  = (s0*ch*ch + a*ch + _) % track.length;
            i = i.substr(0, u) + String.fromCharCode(ch) + i.substr(u);
        }

    注意取模用的是**原始 track 的长度**，不是每轮更新后的长度。

    :param track: 轨迹编码串.
    :param c: get.php 返回的 c 数组.
    :param s: get.php 返回的 s 十六进制串.
    :return: 混淆后的 tt.
    """
    if not c or not s:
        return track
    s0, a, tail = c[0], c[2], c[4]
    result, offset, base_len = track, 0, len(track)
    while offset < len(s) - 1:
        ch = int(s[offset:offset + 2], 16)
        offset += 2
        pos = (s0 * ch * ch + a * ch + tail) % base_len
        result = result[:pos] + chr(ch) + result[pos:]
    return result


def md5_hex(text: str) -> str:
    """fullpage.js 里的 `H()` 就是标准 MD5（常量 2147483648/1073741823 可证）."""
    import hashlib

    return hashlib.md5(text.encode('utf-8')).hexdigest()


# ------------------------------------------------- 无交互场景下的确定性常量
#
# 载荷里的 s/h/hh/hi 是四个行为缓冲区的 MD5。没有鼠标交互时缓冲区是
# 74 个 -1（"无数据"标记），各自用不同分隔符 join 后再 hash，因此是常量。
# 原文由打了探针的 fullpage.js 在运行时导出，两次不同会话取到的值完全一致。
EMPTY_TRACK = 'M(*((1((M(('
EMPTY_HDL_TRACK = 'tEQOYESJYERVYEQ.'
EMPTY_BUF_MAGIC = '-1magic data' * 73 + '-1'
EMPTY_BUF_BANG = '-1!!' * 73 + '-1'
EMPTY_HDL_N = 'dGFdxFsdzEBYxHgZ' * 73 + 'dGE.'


def build_payload(gt: str, challenge: str, passtime: int, track: str = EMPTY_TRACK,
                  c: list = None, s: str = '', lang: str = 'zh-cn',
                  type_: str = 'fullpage', ep: dict = None,
                  extra: dict = None) -> str:
    """按 fullpage.js 的字段顺序拼明文载荷.

    极验用自研 stringify 手工拼 `"key":value,`，所以钩 `JSON.stringify` 抓不到。
    字段顺序照抄源码，便于和实抓样本逐字段比对。

    :param gt: 极验 gt.
    :param challenge: 极验 challenge.
    :param passtime: 从初始化到提交的毫秒数.
    :param track: 轨迹串，缺省用无交互时的固定值.
    :param c: get.php 返回的 c 数组；有值时 tt 会走 cs_cipher.
    :param s: get.php 返回的 s 串.
    :param ep: 环境指纹对象，缺省用 default_ep().
    :param extra: 覆盖或追加字段.
    :return: 明文 JSON 串（不含最外层花括号之外的东西）.
    """
    import json as _json

    fields = [
        ('lang', lang),
        ('type', type_),
        ('tt', cs_cipher(track, c, s) if c and s else track),
        ('light', -1),
        ('s', md5_hex(EMPTY_HDL_TRACK)),
        ('h', md5_hex(EMPTY_HDL_N)),
        ('hh', md5_hex(EMPTY_BUF_MAGIC)),
        ('hi', md5_hex(EMPTY_BUF_BANG)),
        ('vip_order', -1),
        ('ct', -1),
        ('ep', ep if ep is not None else default_ep()),
        ('passtime', passtime),
        ('rp', md5_hex(f'{gt}{challenge}{passtime}')),
    ]
    if extra:
        seen = {k for k, _ in fields}
        fields = [(k, extra.get(k, v)) for k, v in fields]
        fields += [(k, v) for k, v in extra.items() if k not in seen]

    body = ','.join(f'{_json.dumps(k)}:{_json.dumps(v, ensure_ascii=False)}'
                    for k, v in fields)
    return '{' + body + '}'


EMPTY_I_BUF = '-1!!' * 73 + '-1'


def build_init_payload(gt: str, challenge: str, product: str = 'bind',
                       type_: str = 'fullpage', cc: int = 20,
                       ww: bool = True, config: dict = None) -> str:
    """链路第一发 `get.php` 的明文载荷.

    和后续 `ajax.php` 完全不是一套。这里就是**调用方传给 `initGeetest`
    的配置对象原样序列化**，末尾再由运行时追加 `cc` / `ww` / `i` 三项——
    极验自己不往里塞默认值，所以有哪些字段完全由调用方决定。
    结构由打了探针的 fullpage.js 在 Node 里跑出来的真实明文确认。

    早先这里多塞了 `static_servers` / `aspect_radio` 等一堆字段，
    那些只在配置来自 `gettype.php` 时才出现，属实误加。

    :param gt: 极验 gt.
    :param challenge: 极验 challenge.
    :param product: 展现形式，本项目用 bind.
    :param type_: 验证类型.
    :param cc: 运行时算出的工作量因子，浏览器里实抓为 20.
    :param ww: 是否支持 WebWorker，浏览器里为 True.
    :param config: 覆盖整份配置；给了就只在末尾追加 cc/ww/i.
    :return: 明文 JSON 串.
    """
    import json as _json

    base = config if config is not None else {
        'gt': gt,
        'challenge': challenge,
        'offline': False,
        'new_captcha': True,
        'product': product,
        'https': True,
        'lang': 'zh-cn',
        'type': type_,
        'protocol': 'https://',
        'width': '300px',
    }
    return _json.dumps({**base, 'cc': cc, 'ww': ww, 'i': EMPTY_I_BUF},
                       separators=(',', ':'), ensure_ascii=False)


def default_ep(now_ms: int = None) -> dict:
    """环境指纹对象，字段与顺序照抄反混淆后的 `$_CEDb()`.

    注意 `$_BBn` 这个键名不是笔误——它在极验自己的字符串表里就是这个值
    （对应 mouseEvent），实抓载荷里原样出现。
    """
    import time as _time

    from utils.fingerprint import get_profile

    ts = now_ms or int(_time.time() * 1000)
    profile = get_profile()
    return {
        'v': GEETEST_VERSION,
        'te': False,
        '$_BBn': False,
        'ven': profile['webgl_vendor'],
        'ren': profile['webgl_renderer'],
        'fp': None,
        'lp': None,
        'em': {'ph': 0, 'cp': 0, 'ek': '11', 'wd': 1, 'nt': 0, 'si': 0, 'sc': 0},
        'tm': {k: (ts + i if k in 'afghijlmnopqr' else 0)
               for i, k in enumerate('abcdefghijklmnopqrstu')},
        'dnf': 'dnf',
        'by': 2,
    }


def build_w(payload: str, key: str = None, with_rsa: bool = True) -> str:
    """把明文载荷组装成 `w`.

    :param payload: 明文 JSON 串.
    :param key: AES 密钥，缺省随机生成.
    :param with_rsa: 是否附带 RSA 段；fullpage 首发 get.php 和 click ajax.php
                     需要，只有 fullpage 自身的第二发 ajax.php 复用旧密钥。
    :return: w 字符串.
    """
    key = key or gen_aes_key()
    body = custom_b64(aes_encrypt(payload, key))
    return body + rsa_encrypt_key(key) if with_rsa else body


# ------------------------------------------------------- click.3.1.2.js 点选载荷
#
# ⚠️ 重要更正：click.3.1.2.js（v3 点选）里**不存在** `userresponse` 字段。
# `userresponse` 是 v3 滑动（fullpage）的字段名；点选模式把点击坐标放在字段
# `a` 里，且**不经 challenge 编码**。challenge 只参与 `rp` 的 md5。
#
# 静态与反混淆结果中全文搜索 `userresponse` 均为 0 命中。
#
# 载荷装配 `$_BJJQ`（click.deobf.js 去样板后 pos 111424）原文：
#
#     "$_BJJQ":function(e,t){
#       var n=this,r=n["$_BJK"],...,s=n["$_BHBM"]["$_BGCY"]();
#       o={"lang":r["lang"]||"zh-cn","passtime":t,"a":e,"pic":r["pic"],
#          "tt":function(e,t,n){...}(s,r["c"],r["s"]),"ep":n["$_CAAe"]()};
#       ...
#       if(o["a"])try{o["rp"]=function(e){...md5...}(
#           r["gt"]+r["challenge"]+o["passtime"]);
#
# 即：a = 点击坐标串（原样），rp = md5(gt + challenge + passtime)。
#
# 其中 `rp` 与 fullpage 完全一致，本模块已有的 `md5_hex(f'{gt}{challenge}{passtime}')`
# （见 build_payload 第 216 行）即可直接复用，这里不再重复实现。


def encode_click_a(clicks) -> str:
    """把点选坐标编码成极验点选载荷的 `a` 字段.

    ⚠️ 这就是外界常被误称为 "点选 userresponse" 的东西。click.3.1.2.js 里
    该字段名为 `a`，且**与 challenge 无关**——不存在坐标级的 challenge 编码。

    证据一，坐标换算（click.deobf.js pos 134731，`$_CBCK` 的 click 回调）::

        var i=e["$_BECh"](),s=e["$_BEDJ"](),
            o=t["left"],_=t["top"],
            a=t["right"]-t["left"],c=t["bottom"]-t["top"],
            l=(i-o)/a*100,u=(s-_)/c*100;
        ...
        f["$_BFGw"](new oe("div")...,
                    Math["round"](100*l), Math["round"](100*u),
                    g["$_BJK"]["pic_type"]);

    也就是 `l`/`u` 先算成百分比（0~100），再 `Math.round(100*l)` 放大成
    0~10000 的整数，写进元素的 `$_CEGH` / `$_CEHq`。

    证据二，拼接（click.deobf.js pos 150874，`Le.prototype.$_FAA`）::

        "$_FAA":function(){
          var e=this["$_FCf"],t=new ie();
          return e["$_BCO"](function(e){
            t["$_BABE"](e["$_CEGH"]+"_"+e["$_CEHq"]);
          }),t["$_BDk"](",");
        }

    即 `"x1_y1,x2_y2,..."`，下划线连坐标、逗号连点。

    :param clicks: 点击序列，每项 `(x, y)`，取值为
                   `round(相对百分比 * 100)` 后的整数（0~10000）；
                   也可直接传 `Le` 收集到的原始整数对.
    :return: `a` 字段字符串，形如 `"2833_4512,1200_3600"`.
    """
    return ','.join('{}_{}'.format(int(x), int(y)) for x, y in clicks)


def encode_click_a_from_ratio(clicks) -> str:
    """从 0~1 的相对坐标算出 `a` 字段，替你做两级放大.

    对应 JS 里的 `l=(i-o)/a*100` 与 `Math.round(100*l)` 两步，
    合并后即 `round(ratio * 10000)`.

    :param clicks: 点击序列，每项 `(rx, ry)`，`rx`/`ry` 为 0~1 的相对位置
                   （相对图片左上角，除以图片宽/高）.
    :return: `a` 字段字符串.
    """
    return encode_click_a(
        (_js_round(rx * 10000), _js_round(ry * 10000)) for rx, ry in clicks)


def build_click_payload(gt: str, challenge: str, a: str, pic: str, passtime: int,
                        track: str = EMPTY_TRACK, c: list = None, s: str = '',
                        lang: str = 'zh-cn', ep: dict = None,
                        extra: dict = None) -> str:
    """点选提交（`ajax.php`）的明文载荷.

    字段与顺序照抄 `$_BJJQ`（click.deobf.js 去样板后 pos 111424）::

        o={"lang":r["lang"]||"zh-cn","passtime":t,"a":e,"pic":r["pic"],
           "tt":function(e,t,n){...}(s,r["c"],r["s"]),"ep":n["$_CAAe"]()};
        ...
        if(o["a"])try{o["rp"]=function(e){...md5...}(
            r["gt"]+r["challenge"]+o["passtime"]);

    与 fullpage（滑动）的载荷是**两套**：这里没有 `light`/`s`/`h`/`hh`/`hi`/
    `vip_order`/`ct`，多了 `a` 和 `pic`。`rp` 的算法两边一致。

    :param gt: 极验 gt.
    :param challenge: 极验 challenge.
    :param a: 点击坐标串，由 `encode_click_a_from_ratio()` 产出.
    :param pic: `get.php` 返回的题面图路径，原样回填.
    :param passtime: 从题面出现到提交的毫秒数.
    :param track: 轨迹串，缺省用无交互时的固定值.
    :param c: `get.php` 返回的 c 数组.
    :param s: `get.php` 返回的 s 串.
    :param ep: 环境指纹对象，缺省用 default_ep().
    :param extra: 覆盖或追加字段.
    :return: 明文 JSON 串.
    """
    import json as _json

    fields = [
        ('lang', lang),
        ('passtime', passtime),
        ('a', a),
        ('pic', pic),
        ('tt', cs_cipher(track, c, s) if c and s else track),
        ('ep', ep if ep is not None else default_ep()),
    ]
    if a:
        fields.append(('rp', md5_hex(f'{gt}{challenge}{passtime}')))
    if extra:
        seen = {k for k, _ in fields}
        fields = [(k, extra.get(k, v)) for k, v in fields]
        fields += [(k, v) for k, v in extra.items() if k not in seen]

    body = ','.join(f'{_json.dumps(k)}:{_json.dumps(v, ensure_ascii=False)}'
                    for k, v in fields)
    return '{' + body + '}'


def encode_click_a_nine(cells) -> str:
    """九宫格（`pic_type == "nine"`）模式的 `a` 字段.

    与图片模式不同，九宫格存的是 1 起始的**格子下标**而非百分比坐标。
    证据（click.deobf.js pos 150186 附近，`Me.prototype.$_BIDk`）::

        "$_BIDk":function(e,t){
          var n=this["$_FCf"],r=e+"_"+t,i=n["$_BAFr"](r);
          return -1===i?n["$_BABE"](r):n["$_BACF"](i),this;
        },
        "$_FAA":function(){return this["$_FCf"]["$_BDk"](",");}

    调用方 `$_CCAF`（pos 见同文件）里 `a["$_BIDk"](e,t)` 的 `e` 是列号
    （1..cols）、`t` 是行号（1..rows）。

    :param cells: 格子序列，每项 `(col, row)`，均为 1 起始.
    :return: `a` 字段字符串，形如 `"2_1,3_3"`.
    """
    return ','.join('{}_{}'.format(int(c), int(r)) for c, r in cells)


def _js_round(x: float) -> int:
    """复刻 JS `Math.round` 的半数进位规则（.5 一律向 +∞ 取整）.

    Python 的 `round` 是银行家舍入（2.5 -> 2），JS 是 2.5 -> 3、-2.5 -> -2，
    坐标放大到 0~10000 后 .5 边界确实会出现，必须对齐.

    :param x: 待取整的浮点数.
    :return: 取整后的整数.
    """
    import math as _math
    return int(_math.floor(x + 0.5))
