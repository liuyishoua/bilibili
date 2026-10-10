"""BiliAuth：Cookie、设备身份、票据与签名密钥的生命周期管理.

显式工厂，禁止裸构造::

    auth = BiliAuth.anonymous()                 # 只读场景，自动跑完设备初始化
    auth = BiliAuth.from_cookie(cookies_str)    # 已有登录 Cookie
    auth = BiliAuth.from_qrcode_login()         # 终端渲染二维码，App 扫码
    auth = BiliAuth.from_session()              # 从落盘会话恢复，顺带自动续期

mixin_key 与 bili_ticket 都是惰性属性，过期自动换新，调用方不需要手动刷新。
登录态同理：`from_session()` 会顺手跑一遍官方续期链路，所以只要扫过一次码，
之后的运行都不需要真人再介入（见 `apis/bili_login_apis.py` 的 refresh_cookies）。
"""

import time

from builder.header import HeaderBuilder, HeaderType
from utils.common_util import cookie_header, now_ts, trans_cookies
from utils.device import build_device_cookies, gen_b_lsid, gen_sid, sort_cookies
from utils.ticket import gen_web_ticket
from utils.wbi import get_mixin_key

NAV_API = 'https://api.bilibili.com/x/web-interface/nav'

# mixin_key 每日轮换，缓存半小时后重新派生
_MIXIN_TTL = 1800
# bili_ticket ttl 是 3 天，提前 1 小时换新
_TICKET_SKEW = 3600

_AUTH_FACTORY_TOKEN = object()


class BiliAuth:
    def __init__(self, cookies_str: str = '', proxies: dict = None, _factory_token=None):
        if _factory_token is not _AUTH_FACTORY_TOKEN:
            raise TypeError(
                'BiliAuth 不能直接构造，请用 BiliAuth.anonymous() / '
                'BiliAuth.from_cookie() / BiliAuth.from_qrcode_login()'
            )
        self.cookie = trans_cookies(cookies_str)
        self.proxies = proxies
        # 持久化刷新口令（浏览器放在 localStorage 的 ac_time_value）。
        # 只在登录响应体里出现一次，续期时必需，所以随会话一起落盘。
        self.refresh_token = ''
        self._mixin_key = ''
        self._mixin_ts = 0
        self._ticket_expires = 0

    # ------------------------------------------------------------------ 工厂

    @classmethod
    def anonymous(cls, proxies: dict = None, report_finger: bool = True) -> 'BiliAuth':
        """匿名设备：拉 buvid3/4，本地生成其余设备值，再换 bili_ticket.

        :param report_finger: 是否补一发 `ExClimbWuzhi` 指纹上报。
            浏览器每次打开主站都会发（2026-08-16 实抓 reqid=133/305），
            不发等于"这台设备从没上报过指纹"，是很显眼的特征。
            实测 code=0，成本只有一次请求，缺省打开。
        """
        auth = cls(proxies=proxies, _factory_token=_AUTH_FACTORY_TOKEN)
        headers = HeaderBuilder.build(HeaderType.GET).get()
        auth.cookie.update(build_device_cookies(auth, headers=headers))
        auth.refresh_ticket()
        if report_finger:
            auth.report_finger()
        return auth

    @classmethod
    def from_cookie(cls, cookies_str: str, proxies: dict = None,
                    fill_device: bool = True) -> 'BiliAuth':
        """用已有 Cookie 构造.

        :param cookies_str: 完整 Cookie 请求头字符串，登录态需含 SESSDATA / bili_jct.
        :param fill_device: 缺少 buvid3 等设备 Cookie 时是否自动补齐.
        """
        auth = cls(cookies_str, proxies=proxies, _factory_token=_AUTH_FACTORY_TOKEN)
        if fill_device and not auth.cookie.get('buvid3'):
            headers = HeaderBuilder.build(HeaderType.GET).get()
            device = build_device_cookies(auth, headers=headers)
            for key, value in device.items():
                auth.cookie.setdefault(key, value)
        # b_lsid / sid / PVID 是**会话级**的，浏览器每开一次页面都会重新生成。
        # 从落盘的 Cookie 串恢复时它们往往缺失或是上次的旧值，补一份新的，
        # 否则请求里少了浏览器一定会带的字段。
        auth.cookie.setdefault('sid', gen_sid())
        auth.cookie.setdefault('PVID', '1')
        auth.cookie['b_lsid'] = gen_b_lsid()
        auth.cookie = sort_cookies(auth.cookie)
        if auth.cookie.get('bili_ticket_expires'):
            auth._ticket_expires = int(auth.cookie['bili_ticket_expires'])
        return auth

    @classmethod
    def from_qrcode_login(cls, proxies: dict = None, timeout: int = 180,
                          show_in_terminal: bool = True) -> 'BiliAuth':
        """扫码登录：终端渲染二维码，用 B站 App 扫描确认."""
        from apis.bili_login_apis import BiliLoginApi

        auth = cls.anonymous(proxies=proxies)
        success, msg, _ = BiliLoginApi.qrcode_login(
            auth, timeout=timeout, show_in_terminal=show_in_terminal
        )
        if not success:
            raise RuntimeError(f'扫码登录失败: {msg}')
        return auth

    @classmethod
    def from_sms_login(cls, tel, code_provider, cid: int = 86,
                       geetest: dict = None, proxies: dict = None) -> 'BiliAuth':
        """短信登录.

        :param tel: 手机号.
        :param code_provider: 无参可调用对象，返回用户实时提供的验证码.
        :param cid: 国际区号，中国大陆 86.
        :param geetest: 极验结果 {"challenge":..., "validate":..., "seccode":..., "token":...}；
                        缺省时调用 tools.geetest_solve 纯算.
        """
        from apis.bili_login_apis import BiliLoginApi

        auth = cls.anonymous(proxies=proxies)
        if geetest is None:
            from tools.geetest_solve import solve as solve_geetest
            geetest = solve_geetest('_gt/sms', attempts=4, auth=auth)
        success, msg, _ = BiliLoginApi.sms_login(
            auth, tel, code_provider, cid=cid, geetest=geetest,
        )
        if not success:
            raise RuntimeError(f'短信登录失败: {msg}')
        return auth

    @classmethod
    def from_session(cls, path: str = '', proxies: dict = None,
                     auto_refresh: bool = True) -> 'BiliAuth':
        """从落盘会话恢复登录态，需要时自动续期并回写.

        :param path: 自定义 session.json 路径.
        :param proxies: 代理配置.
        :param auto_refresh: 是否跑一遍续期检查。关掉可省一次 `cookie/info` 请求，
            但 Cookie 到期后就会开始报 -101.
        :raise RuntimeError: 没有可用的落盘会话.
        """
        from utils.session import load_session

        session = load_session(path)
        if not session.get('cookie'):
            raise RuntimeError('没有可用的落盘会话，请先跑 python main.py login')
        auth = cls.from_cookie(session['cookie'], proxies=proxies)
        auth.refresh_token = session.get('refresh_token') or ''
        if auto_refresh and auth.is_login:
            if auth.refresh_token:
                auth.ensure_fresh(path=path)
            else:
                # 老的 cookies.txt 里没有它。别每条命令都报一遍警告，
                # 给一句能照着做的提示就够
                print('[hint] 会话里没有 refresh_token，无法自动续期；'
                      '重新跑一次 python main.py login 即可补上')
        return auth

    # -------------------------------------------------------------- 状态属性

    @property
    def cookies_str(self) -> str:
        return cookie_header(self.cookie)

    @property
    def csrf(self) -> str:
        """写操作用的 CSRF token."""
        return self.cookie.get('bili_jct', '')

    @property
    def mid(self) -> str:
        """当前登录用户的数字 ID."""
        return self.cookie.get('DedeUserID', '')

    @property
    def is_login(self) -> bool:
        return bool(self.cookie.get('SESSDATA'))

    @property
    def ticket(self) -> str:
        """bili_ticket，过期自动换新."""
        if not self.cookie.get('bili_ticket') or now_ts() > self._ticket_expires - _TICKET_SKEW:
            self.refresh_ticket()
        return self.cookie['bili_ticket']

    @property
    def mixin_key(self) -> str:
        """WBI mixin_key，缓存过期后重新派生."""
        if self._mixin_key and time.time() - self._mixin_ts < _MIXIN_TTL:
            return self._mixin_key
        return self.refresh_mixin_key()

    # ------------------------------------------------------------------ 刷新

    def refresh_ticket(self) -> str:
        """强制换取 bili_ticket；顺带更新 WBI 密钥（响应里带 nav.img/sub）."""
        headers = HeaderBuilder.build(HeaderType.POST).get()
        data = gen_web_ticket(self, csrf=self.csrf, headers=headers)
        self.cookie['bili_ticket'] = data['ticket']
        self._ticket_expires = int(data.get('created_at', now_ts())) + int(data.get('ttl', 259200))
        self.cookie['bili_ticket_expires'] = str(self._ticket_expires)
        # 新键会落到字典末尾，重排回浏览器顺序
        self.cookie = sort_cookies(self.cookie)
        nav = data.get('nav') or {}
        if nav.get('img') and nav.get('sub'):
            self._set_mixin_key(nav['img'], nav['sub'])
        return self.cookie['bili_ticket']

    def refresh_mixin_key(self) -> str:
        """从 nav 拉取 WBI 密钥并派生 mixin_key."""
        from utils.http_util import get_json

        headers = HeaderBuilder.build(HeaderType.GET).get()
        res_json = get_json(self, NAV_API, headers=headers)
        wbi_img = ((res_json.get('data') or {}).get('wbi_img')) or {}
        if not wbi_img.get('img_url'):
            raise RuntimeError('nav 未返回 wbi_img，无法派生 mixin_key')
        return self._set_mixin_key(wbi_img['img_url'], wbi_img['sub_url'])

    def ensure_fresh(self, force: bool = False, path: str = '') -> bool:
        """按需走一遍官方 Cookie 续期链路，刷新成功就落盘.

        续期失败只打警告不抛异常：当前 Cookie 往往还能用一阵，
        直接崩掉反而会让本来能跑完的任务白跑。

        :param force: 忽略 `cookie/info` 的判断强制刷一次.
        :param path: 自定义 session.json 路径.
        :return: 是否真的换了新 Cookie.
        """
        from apis.bili_login_apis import BiliLoginApi

        success, msg, refreshed = BiliLoginApi.refresh_cookies(self, force=force)
        if refreshed:
            self.save_session(path)
        if not success:
            print(f'[warn] Cookie 续期未完成: {msg}')
        elif refreshed:
            print('Cookie 已续期并落盘')
        return refreshed

    def save_session(self, path: str = '') -> str:
        """把 Cookie 与 refresh_token 落盘，返回写入路径."""
        from utils.session import save_session

        return save_session(self, path)

    def update_cookies(self, cookies) -> 'BiliAuth':
        """并入新的 Cookie（dict 或字符串）."""
        if isinstance(cookies, str):
            cookies = trans_cookies(cookies)
        self.cookie.update(cookies or {})
        self.cookie = sort_cookies(self.cookie)
        return self

    def report_finger(self, referer: str = 'https://www.bilibili.com/',
                      encrypted: bool = True) -> dict:
        """按浏览器的顺序打指纹上报：先明文 `ExClimbWuzhi`，再加密 `ExClimbCongLing`.

        实抓（2026-08-16）确认这两发是**并存**的，不是新旧替换：
        同一个页面里 reqid=133/305 打明文、reqid=40/311 打加密。
        两发都实测 code=0。

        失败时只记录、不抛异常——指纹上报不影响接口功能，
        只影响设备在风控侧的长期评分。

        :param referer: 来源页 URL.
        :param encrypted: 是否连加密那一发一起打。
            加密走 WASM 桥接（需要 Node + `_gt/bili-sc-sdk.js`），
            两者缺一就自动跳过，只发明文那一发——不然每次初始化都会甩一段
            Node 的报错栈，而这一发本身已实证非必需。
        :return: {"wuzhi": bool, "congling": bool}，各自是否 code==0.
        """
        from apis.bili_gaia_apis import BiliGaiaApi, congling_available

        result = {'wuzhi': False, 'congling': False}
        try:
            r = BiliGaiaApi.climb_wuzhi(self, referer=referer)
            result['wuzhi'] = r.get('code') == 0
        except Exception as e:
            print(f'[warn] ExClimbWuzhi 上报失败: {e}')

        if encrypted and congling_available():
            try:
                r = BiliGaiaApi.climb_congling(self, referer=referer)
                result['congling'] = r.get('code') == 0
            except Exception as e:
                print(f'[warn] ExClimbCongLing 上报失败: {e}')
        return result

    def _set_mixin_key(self, img_url: str, sub_url: str) -> str:
        self._mixin_key = get_mixin_key(img_url, sub_url)
        self._mixin_ts = time.time()
        return self._mixin_key
