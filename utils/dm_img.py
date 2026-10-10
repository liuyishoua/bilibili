"""播放器 / 空间页的 WebGL 与交互指纹参数（dm_img_*）.

/x/player/wbi/v2、首页推荐、空间页 acc/info 与 arc/search 都带这组参数，
并且它们**参与 WBI 签名**，所以必须在算 w_rid 之前装配好。
空间类接口不带这组参数会直接返回 -352 风控校验失败。

实测对齐（2026-08-15，Chrome 151）：
- dm_img_str      = base64(WebGL VERSION) 去掉 padding
- dm_cover_img_str= base64((RENDERER + VENDOR) 去掉最后一个字符) 去掉 padding
  截断发生在**明文**而不是 base64 上：浏览器发的 142 字符解码后末尾的 ')' 缺失。
- dm_img_list     = 鼠标采样数组
- dm_img_inter    = {"ds":[元素采样], "wh":[窗口三元组], "of":[偏移三元组]}

⚠️ **缺省一律留空，不要伪造采样**（2026-08-16 实测）。

无交互时（页面刚加载、鼠标没动过）浏览器发的就是空数组，
`acc/info` / `arc/search` / `player/wbi/v2` 三处实抓全是 `[]`。
早先这里按"刚打开时有 2 条采样"去造数据，结果空间接口单发成功率只有 1/6，
其余全是 -352；改成空数组后 6/6。

真实采样的形状（两次实抓）：

    {"x":1300,"y":200, "z":0,  "timestamp":9024, "k":122,"type":0}
    {"x":3237,"y":-4585,"z":0, "timestamp":34267,"k":123,"type":0}
    {"x":3256,"y":-4566,"z":19,"timestamp":34373,"k":71, "type":1}

y **可以是负数**，所以坐标不是简单的视口像素，别按"必须为正"去校验；
`type` 有 0 和 1 两种（成对出现，像是按下/抬起）；
z 从 0 起随位移增长；timestamp 是页面存活毫秒数，相邻约差 100ms。

另有一处曾记错的"规律"：`of` **不是**恒为 `[a, 2a, a]`。
那只是无交互状态下的巧合（`[88,176,88]`、`[349,698,349]`），
真实交互后是三个独立值，实测 `[3121,4198,55]`、`[2511,3436,132]`。

`ds` 元素采样的形状：`{"t":事件类型, "c":base64(元素className), "p":[…], "s":[…]}`。

**每个接口带不带采样是不一样的**：`acc/info` / `arc/search` / `player/wbi/v2`
在页面刚加载时是空数组；而 `reply/add` 是用户交互之后才发的，
实抓里带了 11 条采样。所以缺省留空是对的，
需要"像交互过"的场合再显式传 sample_count。
"""

import base64
import json
import random

from utils.fingerprint import get_profile


def _b64(text: str) -> str:
    return base64.b64encode(text.encode('utf-8')).decode().rstrip('=')


def dm_img_str() -> str:
    """WebGL VERSION 串的 base64."""
    return _b64(get_profile()['webgl_version'])


def dm_cover_img_str() -> str:
    """WebGL RENDERER+VENDOR 串截去末字符后的 base64."""
    profile = get_profile()
    return _b64((profile['webgl_renderer'] + profile['webgl_vendor'])[:-1])


def dm_img_list(count: int = 0) -> str:
    """鼠标采样序列.

    :param count: 采样点个数。**缺省 0（空数组）才是浏览器的行为**，
        伪造采样会触发 -352，见模块开头的说明.
    """
    samples = []
    # 按实抓形状走：坐标累积移动、z 从 0 起随位移增长、
    # timestamp 相邻约差 100ms，type 在 0/1 之间成对出现
    x = random.randint(1000, 4000)
    y = random.choice([random.randint(150, 2000), -random.randint(200, 5000)])
    timestamp, z = random.randint(3000, 40000), 0
    for i in range(count):
        samples.append({
            'x': x, 'y': y, 'z': z,
            'timestamp': timestamp,
            'k': random.randint(60, 128),
            'type': i % 2,
        })
        step_x, step_y = random.randint(-400, 500), random.randint(-300, 400)
        x, y = x + step_x, y + step_y
        z = max(0, z + abs(step_x) + abs(step_y) - random.randint(0, 300))
        timestamp += random.randint(96, 120)
    return json.dumps(samples, separators=(',', ':'))


def dm_img_inter(with_ds: bool = False) -> str:
    """窗口与元素交互采样.

    wh 前两位实测落在 7500~7900 与 9000~9200。
    of 在**无交互**时呈 [a, 2a, a]，有交互后是三个独立值——
    这里按无交互形态生成，与缺省的空 ds 保持自洽。

    :param with_ds: 是否带一条元素采样。**缺省 False 才与浏览器一致**，
        无交互时实抓的 ds 就是空数组，伪造会触发 -352.
    """
    offset = random.randint(1, 350)
    inter = {
        'ds': [],
        'wh': [random.randint(7500, 7900), random.randint(9000, 9200), random.randint(0, 120)],
        'of': [offset, offset * 2, offset],
    }
    if with_ds:
        # c 是被交互元素 className 的 base64，t 是事件类型（实抓点击为 7）
        inter['ds'] = [{
            't': 7,
            'c': _b64('vui_button vui_pagenation--btn vui_pagenation--btn-side'),
            'p': [random.randint(3000, 7500), random.randint(10, 200),
                  random.randint(3000, 7500)],
            's': [random.randint(300, 500), random.randint(600, 900),
                  random.randint(900, 1200)],
        }]
    return json.dumps(inter, separators=(',', ':'))


def build_dm_img_params(sample_count: int = 0, with_ds: bool = False) -> dict:
    """一次性产出四个 dm_img 参数.

    :param sample_count: dm_img_list 的采样点个数，缺省 0（空数组，与浏览器一致）.
    :param with_ds: dm_img_inter 是否带元素采样，缺省 False（与浏览器一致）.
    :return: 可直接并入 query 的 dict.
    """
    return {
        'dm_img_list': dm_img_list(sample_count),
        'dm_img_str': dm_img_str(),
        'dm_cover_img_str': dm_cover_img_str(),
        'dm_img_inter': dm_img_inter(with_ds),
    }
