import hashlib
import random
import string
import time
import urllib.parse


def now_ts() -> int:
    """秒级时间戳."""
    return int(time.time())


def now_ms() -> int:
    """毫秒级时间戳."""
    return int(time.time() * 1000)


def md5(text) -> str:
    """md5 十六进制摘要."""
    if isinstance(text, str):
        text = text.encode('utf-8')
    return hashlib.md5(text).hexdigest()


def trans_cookies(cookies_str: str) -> dict:
    """Cookie 字符串转 dict，容忍多余空格与值里带 '='."""
    cookies = {}
    for item in (cookies_str or '').split(';'):
        item = item.strip()
        if not item or '=' not in item:
            continue
        key, value = item.split('=', 1)
        cookies[key.strip()] = value.strip()
    return cookies


def cookie_header(cookies: dict) -> str:
    """dict 转 Cookie 请求头字符串."""
    return '; '.join(f'{k}={v}' for k, v in cookies.items())


def splice_url(params: dict) -> str:
    """按 URL query 格式拼接（不编码，仅用于日志/调试）."""
    return '&'.join(f'{k}={v}' for k, v in params.items())


def url_encode(params: dict) -> str:
    """标准 URL query 编码，供签名使用."""
    return urllib.parse.urlencode(params)


def random_string(length: int, alphabet: str = string.digits + string.ascii_letters) -> str:
    """指定字符集的随机串."""
    return ''.join(random.choice(alphabet) for _ in range(length))


def random_hex(length: int, upper: bool = True) -> str:
    """随机十六进制串."""
    chars = '0123456789ABCDEF' if upper else '0123456789abcdef'
    return ''.join(random.choice(chars) for _ in range(length))
