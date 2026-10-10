"""WBI 签名纯算实现.

B站 Web 端绝大多数接口（主站 + 直播 + 日志上报）都走同一套 WBI 中间件：
query 参数按 key 排序后拼接，追加 mixin_key 取 md5 得到 w_rid，配合 wts 时间戳。

mixin_key 由服务端下发的两张图片文件名（img_key / sub_key）经固定乱序表重排得到，
**每日轮换**，因此不可硬编码，必须从 nav 或 GenWebTicket 响应动态派生。
"""

import time
import urllib.parse

from utils.common_util import md5

# img_key + sub_key 拼成 64 位后的重排下标表，取重排结果前 32 位为 mixin_key
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

# 参与签名前需要从参数值里剔除的字符
_FILTERED_CHARS = "!'()*"


def extract_key(url: str) -> str:
    """从 wbi 图片 URL 中取出不带扩展名的文件名.

    :param url: 形如 https://i0.hdslb.com/bfs/wbi/7cd0849413...c.png
    :return: 32 位十六进制 key.
    """
    return url.rsplit('/', 1)[-1].split('.')[0]


def get_mixin_key(img_key: str, sub_key: str) -> str:
    """由 img_key / sub_key 派生 mixin_key.

    :param img_key: nav 下发的 img_url 文件名，也可直接传完整 URL.
    :param sub_key: nav 下发的 sub_url 文件名，也可直接传完整 URL.
    :return: 32 位 mixin_key.
    """
    if '/' in img_key:
        img_key = extract_key(img_key)
    if '/' in sub_key:
        sub_key = extract_key(sub_key)
    raw = img_key + sub_key
    return ''.join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def enc_wbi(params: dict, mixin_key: str, wts: int = None) -> dict:
    """为一组 query 参数补上 wts 与 w_rid.

    两个容易搞错的点，都已对照浏览器实抓修正：

    1. **排序只用于算签名，不是最终的参数顺序。** 发出去的 query 必须保持
       调用方给的原始顺序，末尾依次追加 `w_rid`、`wts`（注意是这个先后）。
       早先这里直接把排序后的字典返回，导致 URL 变成字母序，
       和浏览器完全不同——签名照样能过，但请求形态是错的。
    2. **剔除 `!'()*` 也只作用于签名字符串**，不能改真正发出去的值。
       服务端会做同样的剔除再校验，所以原值照发即可。

    :param params: 原始 query 参数（顺序即发送顺序）.
    :param mixin_key: 当前有效的 mixin_key.
    :param wts: 可选的时间戳，默认取当前秒级时间.
    :return: 原顺序 + w_rid + wts 的新 dict（不修改入参）.
    """
    wts = wts if wts is not None else int(time.time())
    to_sign = dict(params)
    to_sign['wts'] = wts
    query = urllib.parse.urlencode({
        key: ''.join(ch for ch in str(value) if ch not in _FILTERED_CHARS)
        for key, value in sorted(to_sign.items())
    })
    signed = dict(params)
    signed['w_rid'] = md5(query + mixin_key)
    signed['wts'] = wts
    return signed
