"""会话持久化：Cookie 和 refresh_token 一起落盘，让"扫一次码"能长期复用.

为什么不能只存 Cookie 串：续期必须带 `refresh_token`（浏览器把它放在
localStorage 的 `ac_time_value`），而它**只在登录响应的 JSON 里出现一次**，
不在 Set-Cookie 里。之前只落 `cookies.txt` 等于把它扔了，于是 Cookie 一失效
就只能再扫一次码——这是"全自动"最后差的那一步。

落盘格式（`session.json`）::

    {
      "cookie": "buvid3=...; SESSDATA=...; bili_jct=...",
      "refresh_token": "<your_refresh_token>",
      "mid": "<your_mid>",
      "updated_at": 1755000000
    }

同时仍会写一份 `cookies.txt`，方便其他工具读取 Cookie。
两份文件里 `session.json` 是权威，`cookies.txt` 只是兼容镜像。

⚠️ 两个文件都含完整登录凭证，已在 `.gitignore` 里排除，别提交、别外传。
"""

import json
import os

from utils.common_util import now_ts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSION_FILE = os.path.join(ROOT, 'session.json')
COOKIE_FILE = os.path.join(ROOT, 'cookies.txt')


def load_session(path: str = '') -> dict:
    """读出落盘的会话.

    `session.json` 不存在时退回读 `cookies.txt`（老版本留下的），
    此时没有 refresh_token，只能续到当前 Cookie 自然失效为止。

    :param path: 自定义 session.json 路径.
    :return: {"cookie","refresh_token","mid","updated_at"}，都读不到时返回空 dict.
    """
    if os.path.exists(path or SESSION_FILE):
        with open(path or SESSION_FILE, encoding='utf-8') as f:
            try:
                data = json.load(f)
            except ValueError:
                data = {}
        if data.get('cookie'):
            return data

    # 同上，只有默认路径才回落到 cookies.txt
    if not path and os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE, encoding='utf-8') as f:
            cookie = f.read().strip()
        if cookie:
            return {'cookie': cookie, 'refresh_token': '', 'mid': '', 'updated_at': 0}
    return {}


def save_session(auth, path: str = '') -> str:
    """把 auth 的 Cookie 与 refresh_token 落盘.

    :param auth: BiliAuth object.
    :param path: 自定义 session.json 路径.
    :return: 实际写入的 session.json 路径.
    """
    payload = {
        'cookie': auth.cookies_str,
        'refresh_token': getattr(auth, 'refresh_token', '') or '',
        'mid': auth.mid,
        'updated_at': now_ts(),
    }
    with open(path or SESSION_FILE, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    # 只有默认路径才镜像 cookies.txt，否则自检用临时路径会把真会话覆盖掉
    if not path:
        with open(COOKIE_FILE, 'w', encoding='utf-8') as f:
            f.write(payload['cookie'])
    return path or SESSION_FILE


def has_session(path: str = '') -> bool:
    return bool(load_session(path).get('cookie'))
