"""bili_ticket 纯算实现.

bili_ticket 是 B站 Web 的反爬票据（JWT，ttl 3 天），换取时需要用固定密钥
对时间戳做 HMAC-SHA256。响应里除了 ticket 还会顺带下发 WBI 的 img/sub key，
因此匿名初始化只打这一发就能同时拿到票据和签名密钥。

实测对齐（2026-08-15）：浏览器 ts=1786776379 → hexsign=2fce51f6...683080，
本实现复算结果逐字符相同。
"""

import hashlib
import hmac
import time

TICKET_API = 'https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket'
TICKET_HMAC_KEY = b'XgwSnGZ1p'
TICKET_KEY_ID = 'ec02'


def hex_sign(ts: int) -> str:
    """计算 GenWebTicket 的 hexsign.

    :param ts: 秒级时间戳.
    :return: 64 位十六进制签名.
    """
    return hmac.new(TICKET_HMAC_KEY, f'ts{ts}'.encode(), hashlib.sha256).hexdigest()


def gen_web_ticket(auth, csrf: str = '', headers: dict = None) -> dict:
    """换取 bili_ticket.

    :param auth: BiliAuth object，提供 Cookie 与代理.
    :param csrf: 登录态下传 bili_jct，匿名传空串.
    :param headers: 请求头（可选）.
    :return: 形如 {"ticket": ..., "created_at": ..., "ttl": ..., "nav": {"img":..., "sub":...}}.
    """
    from utils.http_util import post_json

    ts = int(time.time())
    params = {
        'key_id': TICKET_KEY_ID,
        'hexsign': hex_sign(ts),
        'context[ts]': ts,
        'csrf': csrf or '',
    }
    res_json = post_json(auth, TICKET_API, params=params, headers=headers or {})
    if res_json.get('code') != 0:
        raise RuntimeError(f'GenWebTicket 失败: {res_json}')
    return res_json['data']
