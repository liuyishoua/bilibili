"""统一 HTTP 出口：Chrome 指纹伪装 + JSON 安全解析 + 风控退避重试.

为什么不用 requests：B站 全站走 HTTP/2，Chrome 的伪头顺序
（`:method` / `:authority` / `:scheme` / `:path`）和真实 header 顺序、
以及 TLS(JA3)/HTTP2 SETTINGS 指纹，requests 一个都复现不了。
这里用 curl_cffi 的浏览器伪装，请求在传输层就与 Chrome 一致；
业务 header 交给调用方补，顺序由 curl 按浏览器模板归并。

伪装目标固定为 `chrome146`（curl_cffi 当前支持的最新 Chrome），
`utils/fingerprint.py` 的 UA / sec-ch-ua 也同步锁在 146，
避免"UA 报一个版本、TLS 指纹是另一个版本"这种自相矛盾的破绽。
"""

import threading
import time

from curl_cffi import requests as _cffi
from curl_cffi.requests.headers import Headers

IMPERSONATE = 'chrome146'

# 触发重试的业务错误码：风控校验失败 / 请求过于频繁 / 被降级 /
# -412 是"request was banned"，以业务码而非 HTTP 状态回来，同属 IP 级限流
RETRYABLE_CODES = (-352, -799, -509, -412)
# 412 是 B站 的反爬拦截页，退避后重试往往能过
RETRYABLE_STATUS = (412, 429, 502, 503, 504)
DEFAULT_TIMEOUT = 20

_local = threading.local()


class BiliRequestError(RuntimeError):
    """HTTP 层面无法解析为 JSON 时抛出."""

    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body
        super().__init__(f'HTTP {status_code} 返回非 JSON: {body[:200]!r}')


def session() -> '_cffi.Session':
    """线程内复用一个伪装会话，保持连接与指纹稳定."""
    sess = getattr(_local, 'session', None)
    if sess is None:
        # default_headers=False：模板里没有的头（origin/referer/content-type）
        # 会被 curl 追加到队尾，顺序就和 Chrome 对不上了。关掉之后由
        # builder.header 给出完整且有序的头，curl 原样发送。
        # 只影响头，不影响 impersonate 的 TLS/JA3 与 HTTP2 SETTINGS 指纹。
        sess = _cffi.Session(impersonate=IMPERSONATE, default_headers=False)
        _local.session = sess
    return sess


def build_headers(headers: dict, cookies: dict) -> 'Headers':
    """把 cookie 按 Chrome 的方式并进有序请求头.

    两个细节都对照过实抓：

    - **位置**：Chrome 把 cookie 放在 `accept-language` 之后、`priority` 之前。
      交给 curl 的 `cookies=` 参数处理的话，它会排到伪头后面的最前端，位置就错了。
    - **拆分**：HTTP/2 下 Chrome 每个 cookie 单独占一个头字段，
      而不是拼成一条 `a=1; b=2`。所以这里用多值 Headers 而非单个字符串。

    :param headers: 有序请求头，值为 None 的键会被丢弃.
    :param cookies: Cookie dict，顺序即发送顺序.
    :return: curl_cffi Headers.
    """
    crumbs = [('cookie', f'{name}={value}') for name, value in (cookies or {}).items()]
    pairs, inserted = [], False
    for key, value in (headers or {}).items():
        if key == 'priority' and crumbs:
            pairs += crumbs
            inserted = True
        if value is not None:
            pairs.append((key, value))
    if crumbs and not inserted:
        pairs += crumbs
    return Headers(pairs)


def request(auth, method: str, url: str, retries: int = 3, backoff: float = 1.0,
            **kwargs):
    """带风控重试的原始请求.

    :param auth: BiliAuth object，提供 cookie 与 proxies.
    :param method: get / post / put.
    :param url: 完整 URL.
    :param retries: 失败后的额外尝试次数.
    :param backoff: 退避基数（秒），按重试次数指数增长.
    :return: curl_cffi Response.
    """
    if getattr(auth, 'workspace_no_retry', False):
        retries = 0
    cookies = kwargs.pop('cookies', None)
    if cookies is None:
        cookies = auth.cookie if auth else {}
    kwargs['headers'] = build_headers(kwargs.get('headers') or {}, cookies)
    if auth is not None and auth.proxies:
        kwargs.setdefault('proxies', auth.proxies)
    kwargs.setdefault('timeout', DEFAULT_TIMEOUT)

    last_resp = None
    for attempt in range(retries + 1):
        # 让 auth.cookie 成为唯一来源：curl 自己的 jar 会累积响应里的 Set-Cookie，
        # 不清掉就会和我们手工拼的那份重复。清空后 resp.cookies 里
        # 恰好只剩本次响应新设的，正好是 requests_cookies_to_dict 想要的。
        sess = session()
        sess.cookies.clear()
        resp = sess.request(method.upper(), url, **kwargs)
        last_resp = resp
        if resp.status_code == 200:
            if _peek_code(resp) not in RETRYABLE_CODES:
                return resp
        elif resp.status_code not in RETRYABLE_STATUS:
            return resp
        if attempt < retries:
            time.sleep(backoff * (2 ** attempt))
    return last_resp


def request_json(auth, method: str, url: str, **kwargs) -> dict:
    """请求并解析 JSON，非 JSON 时抛 BiliRequestError."""
    resp = request(auth, method, url, **kwargs)
    try:
        return resp.json()
    except Exception:
        raise BiliRequestError(resp.status_code, resp.text)


def get_json(auth, url: str, **kwargs) -> dict:
    return request_json(auth, 'get', url, **kwargs)


def post_json(auth, url: str, **kwargs) -> dict:
    return request_json(auth, 'post', url, **kwargs)


def get_bytes(auth, url: str, **kwargs) -> bytes:
    """请求并返回原始字节（弹幕 protobuf 等）."""
    return request(auth, 'get', url, **kwargs).content


def _peek_code(resp):
    """尽力取出业务 code，取不到返回 None."""
    if 'json' not in (resp.headers.get('content-type') or ''):
        return None
    try:
        return resp.json().get('code')
    except Exception:
        return None
