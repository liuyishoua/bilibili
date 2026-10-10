"""请求头装配：字段与**顺序**都对齐 Chrome.

顺序为什么重要：B站 走 HTTP/2，头顺序是 HTTP/2 指纹的一部分，
和 TLS(JA3)、SETTINGS 一起被用来识别非浏览器客户端。

基准怎么来的：让 Chrome 从 B站 页面 fetch 本地回显服务器
（通过本地回显服务实抓，Chrome 把 127.0.0.1 当可信源，
所以 HTTPS 页面 fetch 明文 localhost 不会被拦），原样记下到达顺序：

    GET  : user-agent, accept, origin, sec-fetch-site, sec-fetch-mode,
           sec-fetch-dest, referer, accept-encoding, accept-language
    POST : content-length, user-agent, content-type, accept, origin,
           sec-fetch-site, sec-fetch-mode, sec-fetch-dest, referer,
           accept-encoding, accept-language

明文回环拿不到 `sec-ch-ua*`（客户端提示只发给安全源）和 `priority`
（HTTP/2 专有），这两组的位置取自 curl_cffi 的 chrome 模板——
该模板本身就是照真实 Chrome 抓包做的：客户端提示在 `user-agent` 之前，
`priority` 在最后。

⚠️ 必须配合 `http_util` 里的 `default_headers=False`：
curl 对模板中**已有**的头是就地替换、模板中**没有**的头则追加到末尾。
`origin` / `referer` / `content-type` 都不在模板里，若不关掉默认头，
它们会被甩到队尾，与 Chrome 不符。
"""

from enum import Enum

from utils.fingerprint import get_profile


class HeaderType(Enum):
    DOC = 'DOC'
    GET = 'GET'
    POST = 'POST'
    FORM = 'FORM'


class Header:
    def __init__(self):
        self.headers = {}

    def set_header(self, key, value):
        """写入一个头。⚠️ 新键会落到末尾，改值不影响既有顺序."""
        self.headers[key] = value
        return self

    def set_referer(self, url):
        self.set_header('referer', url)
        return self

    def set_origin(self, url):
        self.set_header('origin', url)
        return self

    def remove_header(self, key):
        self.headers.pop(key, None)
        return self

    def insert_before(self, anchor: str, key: str, value):
        """在 anchor 之前插入，用于补 Chrome 里位置固定的业务头.

        :param anchor: 已存在的键；不存在时退化为追加.
        """
        if anchor not in self.headers:
            return self.set_header(key, value)
        rebuilt = {}
        for k, v in self.headers.items():
            if k == anchor:
                rebuilt[key] = value
            if k != key:
                rebuilt[k] = v
        self.headers = rebuilt
        return self

    def get(self):
        return self.headers

    def __call__(self):
        return self.headers


class HeaderBuilder:
    ua = get_profile()['ua']
    sec_ch_ua = get_profile()['sec_ch_ua']
    sec_ch_ua_platform = get_profile()['sec_ch_ua_platform']
    accept_language = get_profile()['accept_language']

    accept_encoding = 'gzip, deflate, br, zstd'
    xhr_accept = 'application/json, text/plain, */*'
    doc_accept = ('text/html,application/xhtml+xml,application/xml;q=0.9,'
                  'image/avif,image/webp,image/apng,*/*;q=0.8')

    main_origin = 'https://www.bilibili.com'
    live_origin = 'https://live.bilibili.com'
    passport_origin = 'https://passport.bilibili.com'
    member_origin = 'https://member.bilibili.com'
    dynamic_origin = 'https://t.bilibili.com'
    space_origin = 'https://space.bilibili.com'
    search_origin = 'https://search.bilibili.com'

    @staticmethod
    def build(header_type=HeaderType.GET, origin: str = None,
              accept: str = None, same_origin: bool = False):
        """构造一组顺序与 Chrome 一致的请求头.

        :param header_type: DOC 是页面导航，GET/POST/FORM 是 XHR.
        :param origin: 站点来源，决定 origin / referer，默认主站.
        :param accept: 覆盖 accept；二进制接口（弹幕 protobuf）浏览器发 `*/*`.
        :param same_origin: 目标与来源同源时置 True（如 member.bilibili.com
            调自己的接口）。按 Fetch 规范，此时 `sec-fetch-site` 是 `same-origin`，
            且 **GET 不带 `origin` 头**（只有带副作用的方法才带）。
        :return: Header 对象，其 dict 顺序即发送顺序.
        """
        origin = origin or HeaderBuilder.main_origin
        h = Header()

        h.set_header('sec-ch-ua', HeaderBuilder.sec_ch_ua)
        h.set_header('sec-ch-ua-mobile', '?0')
        h.set_header('sec-ch-ua-platform', HeaderBuilder.sec_ch_ua_platform)

        if header_type == HeaderType.DOC:
            h.set_header('upgrade-insecure-requests', '1')
            h.set_header('user-agent', HeaderBuilder.ua)
            h.set_header('accept', accept or HeaderBuilder.doc_accept)
            h.set_header('sec-fetch-site', 'none')
            h.set_header('sec-fetch-mode', 'navigate')
            h.set_header('sec-fetch-user', '?1')
            h.set_header('sec-fetch-dest', 'document')
            h.set_header('accept-encoding', HeaderBuilder.accept_encoding)
            h.set_header('accept-language', HeaderBuilder.accept_language)
            h.set_header('priority', 'u=0, i')
            return h

        h.set_header('user-agent', HeaderBuilder.ua)
        if header_type == HeaderType.POST:
            h.set_header('content-type', 'application/json')
        elif header_type == HeaderType.FORM:
            h.set_header('content-type', 'application/x-www-form-urlencoded')
        h.set_header('accept', accept or HeaderBuilder.xhr_accept)
        if not (same_origin and header_type == HeaderType.GET):
            h.set_header('origin', origin)
        h.set_header('sec-fetch-site', 'same-origin' if same_origin else 'same-site')
        h.set_header('sec-fetch-mode', 'cors')
        h.set_header('sec-fetch-dest', 'empty')
        h.set_header('referer', origin + '/')
        h.set_header('accept-encoding', HeaderBuilder.accept_encoding)
        h.set_header('accept-language', HeaderBuilder.accept_language)
        h.set_header('priority', 'u=1, i')
        return h
