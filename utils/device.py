"""匿名设备身份：buvid3 / buvid4 / _uuid / b_nut / b_lsid / sid / buvid_fp 等.

**CDP 受控实验结论（2026-08-16，全新 Chrome 配置首访 www.bilibili.com）**：
服务端只 `Set-Cookie` 两个键 —— `buvid3` 与 `b_nut`；
`_uuid` / `buvid4` / `buvid_fp` / `browser_resolution` / `home_feed_column` /
`b_lsid` / `bili_ticket` 全部由页面 JS 本地写入（`buvid4` 的值取自
`finger/spi` 的响应 JSON，但落 Cookie 这一步是 JS 干的）。
所以本模块"服务端拿两个、其余本地生成"的分工与浏览器一致。

Cookie 的**名字集合与顺序**照抄 Chrome 151 实抓（见 COOKIE_ORDER）。
顺序会影响 HTTP/2 里 cookie 头的排列，属于指纹的一部分。

⚠️ **`rpdid` 首访时不存在**。同一实验里全新配置访问首页后，jar 里
**没有** `rpdid`（登录态的浏览器才有，实抓值形如
`0zbfAHypv6|14No88fst|2Q|3w1WVvSG`，四段用 `|` 分隔）。
既然浏览器首访也不带，匿名链**不带它才是对的**；
登录态则由 `from_cookie()` 从落盘 Cookie 中原样带出，不需要伪造。

⚠️ 同理 `CURRENT_QUALITY` 也不在首访 jar 里（登录后记录用户选的画质才出现），
匿名链不带。
"""

import random
import time

from utils.common_util import now_ms, now_ts, random_hex, random_string
from utils.fingerprint import get_profile
from utils.murmur3 import murmur3_hex

SPI_API = 'https://api.bilibili.com/x/frontend/finger/spi'

# 浏览器实抓的 Cookie 顺序（2026-08-16，Chrome 151，api.bilibili.com XHR）。
# 依据 reqid=76 /x/player/wbi/v2 与 reqid=82 /x/web-interface/nav 两次抓包确认。
# 不在表里的键排到最后，保持相对顺序。
COOKIE_ORDER = [
    'buvid3', 'b_nut', '_uuid', 'home_feed_column', 'buvid4', 'browser_resolution',
    'buvid_fp', 'LIVE_BUVID',
    'SESSDATA', 'bili_jct', 'DedeUserID', 'DedeUserID__ckMd5',
    'theme-tip-show', 'CURRENT_FNVAL', 'sid', 'rpdid',
    'PVID', 'CURRENT_QUALITY',
    'bili_ticket', 'bili_ticket_expires',
    'ogv_device_support_dolby', 'ogv_device_support_hdr',
    'b_lsid',
]


def sort_cookies(cookies: dict) -> dict:
    """把 Cookie 按浏览器的顺序重排.

    HTTP/2 下 cookie 会被拆成多个头字段发送，顺序照抄浏览器才对得上指纹。

    :param cookies: 原始 Cookie dict.
    :return: 新的有序 dict，表外的键按原相对顺序排在最后.
    """
    index = {name: i for i, name in enumerate(COOKIE_ORDER)}
    rest = [k for k in cookies if k not in index]
    ordered = [k for k in COOKIE_ORDER if k in cookies] + rest
    return {k: cookies[k] for k in ordered}

# _uuid 的字符集：'10' 是双字符条目，故各段实际长度会略大于取样次数
_UUID_CHARS = list('123456789ABCDEF') + ['10']
_UUID_SEGMENTS = (8, 4, 4, 4, 12)


def gen_uuid_infoc() -> str:
    """生成 _uuid Cookie.

    形如 1472108AE-54CB-1010FA-E928-1D4107F14B6C238761infoc：
    五段随机 + 5 位毫秒尾数 + 固定后缀 infoc.
    """
    parts = [''.join(random.choice(_UUID_CHARS) for _ in range(n)) for n in _UUID_SEGMENTS]
    tail = str(now_ms() % 100000).ljust(5, '0')
    return '-'.join(parts) + tail + 'infoc'


def gen_b_lsid() -> str:
    """生成 b_lsid（本次会话 ID）：8 位随机十六进制 + '_' + 毫秒时间戳的十六进制."""
    return f'{random_hex(8)}_{format(now_ms(), "X")}'


def gen_sid() -> str:
    """生成 sid：8 位小写字母数字."""
    return random_string(8, '0123456789abcdefghijklmnopqrstuvwxyz')


def gen_qv_id() -> str:
    """搜索接口用的 qv_id：32 位字母数字随机串.

    替代原 static/bili.js 里的 getqvId()，无需 Node 运行时。
    """
    return random_string(32)


def gen_buvid_fp(payload: str = '', seed: int = 31) -> str:
    """由指纹载荷算 buvid_fp.

    :param payload: 指纹载荷字符串。为空时用**实抓对齐的真实载荷**.
    :param seed: murmur3 种子，B站 用 31.
    :return: 32 位十六进制.

    载荷来源（2026-08-16 实证）：`ExClimbWuzhi` 的 body 是**明文**的，
    里面 `payload.3c43` 那个子对象就是 buvid_fp 的 murmur3 输入。
    此前这里用的是"拿设备画像随便拼一串"的等价实现，格式合法但值对不上；
    现在改成照抄实抓的字段与顺序（见 `apis/bili_gaia_apis.build_finger_core`）。
    """
    if not payload:
        import json

        # 延迟导入：bili_gaia_apis 会反过来用到 http_util / header，
        # 在模块顶层导入会形成环
        from apis.bili_gaia_apis import build_finger_core

        payload = json.dumps(build_finger_core(), separators=(',', ':'),
                             ensure_ascii=False)
    return murmur3_hex(payload, seed)


def fetch_spi(auth=None, headers: dict = None) -> dict:
    """获取服务端下发的 buvid3 / buvid4.

    :param auth: BiliAuth object，可为 None（设备初始化时还没有）.
    :param headers: 请求头.
    :return: {"b_3": buvid3, "b_4": buvid4}.
    """
    from utils.http_util import get_json

    res_json = get_json(auth, SPI_API, headers=headers or {})
    if res_json.get('code') != 0:
        raise RuntimeError(f'finger/spi 失败: {res_json}')
    return res_json['data']


def gen_live_buvid() -> str:
    """生成 LIVE_BUVID Cookie.

    实抓格式（2026-08-16）：`AUTO` 前缀 + 19 位数字，其中前 10 位是秒级时间戳，
    后 9 位是随机数字，之后再拼 5 位随机数字。
    示例：AUTO8617867764269219
    """
    ts = str(now_ts())
    rand = ''.join(random.choice('0123456789') for _ in range(9))
    return f'AUTO{ts}{rand}'


def build_device_cookies(auth=None, headers: dict = None) -> dict:
    """跑一遍设备初始化，返回一整套匿名 Cookie（不含 bili_ticket）.

    Cookie 名字集合与顺序完全照抄 2026-08-16 Chrome 151 实抓（reqid=76/82），
    CDP 受控实验也确认：服务端只 Set-Cookie `buvid3` 和 `b_nut`，
    其余全部是 JS 本地生成的。

    `LIVE_BUVID` 是直播侧 JS 生成的会话 ID，所有接口（包括主站）都带。
    `ogv_device_support_dolby/hdr` 是播放器探测硬解能力后写入的，值固定为 '0'
    （表示不支持）或 '1'（支持）；不支持时也必须带这两个键，否则缺字段。
    `CURRENT_QUALITY` 不在首次匿名访问时出现，登录后才会有用户选择的画质记录；
    匿名时不带此 Cookie 才与浏览器行为一致。

    :return: 可直接用于请求的 Cookie dict.
    """
    spi = fetch_spi(auth, headers=headers)
    profile = get_profile()
    return sort_cookies({
        'buvid3': spi['b_3'],
        'b_nut': str(now_ts()),
        '_uuid': gen_uuid_infoc(),
        'home_feed_column': '5',
        'buvid4': spi['b_4'],
        'browser_resolution': profile['browser_resolution'],
        'buvid_fp': gen_buvid_fp(),
        'LIVE_BUVID': gen_live_buvid(),
        'theme-tip-show': 'SHOWED',
        'CURRENT_FNVAL': '4048',
        'sid': gen_sid(),
        'PVID': '1',
        'ogv_device_support_dolby': '0',
        'ogv_device_support_hdr': '0',
        'b_lsid': gen_b_lsid(),
    })
