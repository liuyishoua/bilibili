"""登录接口（扫码 / 短信 / 账密 / Cookie 续期 / 退出）.

扫码需要 App 确认；短信与账密登录需要调用方提供真人完成的极验结果，
短信验证码也需要实时提供。

登录之后就不再需要真人了：`refresh_cookies()` 走官方续期链路
（`cookie/info` → `correspondPath` → `refresh_csrf` → `cookie/refresh` →
`confirm/refresh`）换新的 SESSDATA，扫一次码即可长期无人值守。
这也是为什么每条登录路径都要收下响应里的 `refresh_token`——它只出现一次。

约定：查询类方法返回 JSON，编排类方法返回 (success, msg, data)。

**passport 域的形态与主站不同**（2026-08-16 在未登录的隔离窗口里实抓，
reqid=11/13）：

- 每个 GET 都额外带 `x-bili-redirect=1` 与 `x-bili-locale-json`
  两个 query 参数，缺了就和浏览器对不上；
- `accept` 是 `*/*`，不是 axios 那串；
- passport 调自己的接口属**同源**，`sec-fetch-site` 必须是 `same-origin`，
  且 GET 不带 `origin`；
- 登录页的 `accept-language` 只有 `zh-CN,zh;q=0.9` 两项，
  比主站那串（五项）短。
"""

import base64
import json
import re
import time

from builder.header import HeaderBuilder, HeaderType
from utils.common_util import now_ms
from utils.correspond import CORRESPOND_URL, gen_correspond_path
from utils.http_util import get_json, post_json, request

# 登录页每个请求都带的本地化参数，值照抄实抓（键序也照抄）
LOCALE_JSON = json.dumps(
    {'c_locale': {'language': 'zh', 'region': 'CN'}, 'always_translate': True},
    separators=(',', ':'), ensure_ascii=False)

# 登录页的 accept-language 比主站短，只有两项
PASSPORT_ACCEPT_LANGUAGE = 'zh-CN,zh;q=0.9'


def passport_params(params: dict = None) -> dict:
    """给 passport 域的 query 补上浏览器一定会带的三个公共参数.

    实抓顺序是 `<业务参数...>, web_location, x-bili-redirect, x-bili-locale-json`。

    :param params: 业务参数，顺序即发送顺序.
    :return: 补齐后的新 dict.
    """
    merged = dict(params or {})
    merged.setdefault('web_location', '333.1228')
    merged['x-bili-redirect'] = 1
    merged['x-bili-locale-json'] = LOCALE_JSON
    return merged


def passport_params_bare() -> dict:
    """只有两个公共参数、**不带** `web_location` 的版本.

    `sms/send` 与 `login/sms` 两个 POST 实抓就是这个形态
    （reqid=105/116），比 GET 那几个少一个 `web_location`。
    """
    return {'x-bili-redirect': 1, 'x-bili-locale-json': LOCALE_JSON}


def passport_headers(header_type=HeaderType.GET) -> dict:
    """passport 域的请求头：同源 + `accept: */*` + 短 accept-language.

    适用于 GET / FORM（POST urlencoded）两种场景，content-type 由 header_type 决定。
    实抓 sms/send 与 login/sms 的头顺序：
        user-agent, content-type, referer, :authority, :method, :path, :scheme,
        accept, accept-encoding, accept-language, content-length, cookie,
        origin, priority, sec-fetch-dest, sec-fetch-mode, sec-fetch-site
    """
    header = HeaderBuilder.build(header_type, BiliLoginApi.passport, accept='*/*',
                                 same_origin=True)
    header.set_header('accept-language', PASSPORT_ACCEPT_LANGUAGE)
    return header.set_referer(f'{BiliLoginApi.passport}/login').get()


class BiliLoginApi:
    passport = 'https://passport.bilibili.com'

    # ------------------------------------------------------------------ 扫码

    @staticmethod
    def qrcode_generate(auth) -> dict:
        """申请登录二维码.

        实抓（reqid=11）query：
        `source, go_url, web_location, x-bili-redirect, x-bili-locale-json`

        :param auth: BiliAuth object.
        :return: JSON，data.url 为二维码内容，data.qrcode_key 用于轮询.
        """
        return get_json(auth, f'{BiliLoginApi.passport}/x/passport-login/web/qrcode/generate',
                        headers=passport_headers(),
                        params=passport_params({'source': 'main_web', 'go_url': ''}))

    @staticmethod
    def qrcode_poll(auth, qrcode_key: str, b_ret: str = None):
        """轮询扫码状态.

        实抓（reqid=43）query 末尾带一个 `b_ret`。此前以为是"二维码 canvas
        渲染证明"，**实证是错的**：它逐字符等于 `ExClimbWuzhi` 指纹里的
        `13ab` 字段，也就是 **canvas 指纹的尾串**，和二维码无关。
        所以我们能算出来，缺省就带上（与浏览器一致）。

        :param auth: BiliAuth object.
        :param qrcode_key: qrcode_generate 返回的 key.
        :param b_ret: canvas 指纹尾串；缺省取 `finger_b_ret()`，传 '' 可显式不带.
        :return: (res_json, cookies)，data.code 0=成功 86090=已扫未确认
                 86101=未扫码 86038=二维码失效.
        """
        from apis.bili_gaia_apis import finger_b_ret

        params = passport_params({'qrcode_key': qrcode_key, 'source': 'main_web'})
        b_ret = finger_b_ret() if b_ret is None else b_ret
        if b_ret:
            params['b_ret'] = b_ret
        resp = request(auth, 'get',
                       f'{BiliLoginApi.passport}/x/passport-login/web/qrcode/poll',
                       headers=passport_headers(), params=params)
        return resp.json(), requests_cookies_to_dict(resp)

    @staticmethod
    def qrcode_login(auth, timeout: int = 180, interval: float = 2.0,
                     show_in_terminal: bool = True) -> tuple:
        """完整扫码登录流程，成功后把 Cookie 写回 auth.

        :param auth: BiliAuth object.
        :param timeout: 最长等待秒数.
        :param interval: 轮询间隔秒数.
        :param show_in_terminal: 是否在终端渲染二维码.
        :return: (success, msg, res_json).
        """
        res_json = BiliLoginApi.qrcode_generate(auth)
        if res_json.get('code') != 0:
            return False, f'申请二维码失败: {res_json}', res_json
        url = res_json['data']['url']
        qrcode_key = res_json['data']['qrcode_key']

        if show_in_terminal:
            print_qrcode(url)
        print(f'二维码链接（也可手动打开）: {url}')

        deadline = time.time() + timeout
        last_state = None
        while time.time() < deadline:
            poll_json, cookies = BiliLoginApi.qrcode_poll(auth, qrcode_key)
            data = poll_json.get('data') or {}
            code = data.get('code')
            if code == 0:
                auth.update_cookies(cookies)
                # refresh_token 只在这一次响应体里出现，不在 Set-Cookie 里；
                # 丢了它以后就只能重新扫码，所以必须马上收下（见 utils/session.py）
                auth.refresh_token = data.get('refresh_token') or ''
                return True, '登录成功', poll_json
            if code == 86038:
                return False, '二维码已失效，请重新发起', poll_json
            if code != last_state:
                last_state = code
                print(f'[{code}] {data.get("message") or ""}')
            time.sleep(interval)
        return False, f'{timeout} 秒内未完成扫码', {}

    # ------------------------------------------------------------------ 极验

    @staticmethod
    def get_captcha(auth) -> dict:
        """申请极验人机验证.

        实抓（reqid=13）query：
        `source, web_location, x-bili-redirect, x-bili-locale-json`

        :param auth: BiliAuth object.
        :return: JSON，data.geetest.gt / data.geetest.challenge / data.token.
        """
        return get_json(auth, f'{BiliLoginApi.passport}/x/passport-login/captcha',
                        headers=passport_headers(),
                        params=passport_params({'source': 'main-fe'}))

    # ------------------------------------------------------------------ 短信

    @staticmethod
    def sms_send(auth, tel, geetest: dict, cid: int = 86) -> dict:
        """发送短信验证码.

        **形态以浏览器实抓为准**（2026-08-16，在未登录的隔离窗口里真发一条，
        reqid=105）。此前这里是照文档写的，字段顺序、`source` 取值、
        `go_url` 取值三处全错，query 也整个漏了。

        query（只有两个公共参数，**没有** `web_location`）::

            x-bili-redirect=1
            x-bili-locale-json={"c_locale":{"language":"zh","region":"CN"},"always_translate":true}

        body 字段与顺序（8 个）::

            source, tel, cid, go_url, token, validate, seccode, challenge

        三个容易搞错的点：

        - `source` 是 **`main_web`**，不是 `main-fe-header`；
        - `go_url` 是**空串**，不是 `https://www.bilibili.com/`；
        - `seccode` 恒等于 `validate + "|jordan"`（与极验字符串表 [1101] 一致），
          所以调用方只给 validate 也能推出来。

        :param auth: BiliAuth object.
        :param tel: 手机号.
        :param geetest: {"token","challenge","validate"[,"seccode"]}，
                        可由 tools.geetest_solve 纯算得到.
        :param cid: 国际区号，中国大陆 86.
        :return: JSON，data.captcha_key 供 sms_login 使用.
        """
        validate = geetest['validate']
        seccode = geetest.get('seccode') or f'{validate}|jordan'
        data = {
            'source': 'main_web', 'tel': tel, 'cid': cid, 'go_url': '',
            'token': geetest['token'], 'validate': validate,
            'seccode': seccode, 'challenge': geetest['challenge'],
        }
        return post_json(auth, f'{BiliLoginApi.passport}/x/passport-login/web/sms/send',
                         headers=passport_headers(HeaderType.FORM),
                         params=passport_params_bare(), data=data)

    @staticmethod
    def sms_login(auth, tel, code_provider, cid: int = 86, geetest: dict = None,
                  captcha_key: str = '') -> tuple:
        """完整短信登录流程.

        验证码只有几分钟有效期，所以拿到 captcha_key 之后才向 code_provider 要码，
        最大限度减少过期风险。

        `login/sms` 的形态同样以实抓为准（reqid=116）：

        - query 与 sms/send 一样只有两个公共参数，浏览器还额外带了一个
          `b_ret`（二维码 canvas 的渲染证明），实测**不带也能登录成功**；
        - body 字段与顺序（6 个）::

              source, captcha_key, cid, tel, code, go_url

          `source` 同样是 `main_web`；`go_url` 空串；
          **没有 `keep` 字段**——那是我们此前凭空加的。

        :param auth: BiliAuth object.
        :param tel: 手机号.
        :param code_provider: 无参可调用对象，返回用户实时提供的验证码.
        :param cid: 国际区号.
        :param geetest: 真人完成的极验结果；未发短信时必须提供.
        :param captcha_key: 已经发过短信时直接传 captcha_key，跳过 sms_send.
        :return: (success, msg, res_json).
        """
        if not captcha_key:
            if not geetest:
                captcha = BiliLoginApi.get_captcha(auth)
                if captcha.get('code') != 0:
                    return False, f'申请极验失败: {captcha}', captcha
                return False, (
                    '需要先完成人工极验；可用 tools/geetest_helper.py 托管官方控件，'
                    '再把 token/challenge/validate/seccode 作为 geetest 传入'
                ), captcha

            send_json = BiliLoginApi.sms_send(auth, tel, geetest, cid=cid)
            if send_json.get('code') != 0:
                return False, f'发送验证码失败: {send_json}', send_json
            captcha_key = send_json['data']['captcha_key']

        sms_code = code_provider() if callable(code_provider) else code_provider
        if not sms_code:
            return False, '未提供验证码', {}

        data = {
            'source': 'main_web', 'captcha_key': captcha_key, 'cid': cid,
            'tel': tel, 'code': sms_code, 'go_url': '',
        }
        resp = request(auth, 'post', f'{BiliLoginApi.passport}/x/passport-login/web/login/sms',
                       headers=passport_headers(HeaderType.FORM),
                       params=passport_params_bare(), data=data)
        res_json = resp.json()
        if res_json.get('code') != 0:
            return False, f"登录失败: {res_json.get('message')}", res_json
        auth.update_cookies(requests_cookies_to_dict(resp))
        auth.refresh_token = (res_json.get('data') or {}).get('refresh_token') or ''
        return True, '登录成功', res_json

    # ------------------------------------------------------------------ 账密

    @staticmethod
    def get_login_key(auth) -> dict:
        """取密码加密用的 RSA 公钥与 salt.

        :param auth: BiliAuth object.
        :return: JSON，data.hash 是 salt，data.key 是 PEM 公钥.
        """
        headers = HeaderBuilder.build(HeaderType.GET, BiliLoginApi.passport).set_referer(
            f'{BiliLoginApi.passport}/login').get()
        return get_json(auth, f'{BiliLoginApi.passport}/x/passport-login/web/key',
                        headers=headers)

    @staticmethod
    def password_login(auth, username: str, password: str, geetest: dict) -> tuple:
        """账号密码登录.

        :param auth: BiliAuth object.
        :param username: 手机号或邮箱.
        :param password: 明文密码，内部会用服务端公钥加密.
        :param geetest: 真人完成的极验结果.
        :return: (success, msg, res_json).
        """
        if not username or not password:
            return False, '用户名和密码不能为空', {}
        if not geetest:
            return False, '账密登录需要先提供真人完成的极验结果', {}
        key_json = BiliLoginApi.get_login_key(auth)
        if key_json.get('code') != 0:
            return False, f'取公钥失败: {key_json}', key_json
        enc_password = encrypt_password(key_json['data']['hash'], password,
                                        key_json['data']['key'])

        headers = HeaderBuilder.build(HeaderType.FORM, BiliLoginApi.passport).set_referer(
            f'{BiliLoginApi.passport}/login').get()
        data = {
            'username': username, 'password': enc_password, 'keep': 0,
            'token': geetest['token'], 'challenge': geetest['challenge'],
            'validate': geetest['validate'],
            'seccode': geetest.get('seccode') or f"{geetest['validate']}|jordan",
            'source': 'main-fe-header', 'go_url': 'https://www.bilibili.com/',
        }
        resp = request(auth, 'post', f'{BiliLoginApi.passport}/x/passport-login/web/login',
                       headers=headers, data=data)
        res_json = resp.json()
        if res_json.get('code') != 0:
            return False, f"登录失败: {res_json.get('message')}", res_json
        auth.update_cookies(requests_cookies_to_dict(resp))
        auth.refresh_token = (res_json.get('data') or {}).get('refresh_token') or ''
        return True, '登录成功', res_json

    # ------------------------------------------------------------ Cookie 维护

    @staticmethod
    def cookie_info(auth) -> dict:
        """查询 Cookie 是否需要刷新.

        :param auth: BiliAuth object.
        :return: JSON，data.refresh 为 true 时应尽快刷新.
        """
        headers = HeaderBuilder.build(HeaderType.GET, BiliLoginApi.passport).get()
        # 实抓是 web_location=333.788 在前、csrf 在后
        return get_json(auth, f'{BiliLoginApi.passport}/x/passport-login/web/cookie/info',
                        headers=headers,
                        params={'web_location': '333.788', 'csrf': auth.csrf})

    @staticmethod
    def get_refresh_csrf(auth, correspond_path: str) -> str:
        """从 correspond 页面取一次性刷新口令 `refresh_csrf`.

        这是个 SSR 页面，口令就明文躺在 `<div id="1-name">` 里，
        浏览器用 iframe 加载它。所以请求头按**子框架导航**装：
        `sec-fetch-dest: iframe`、`sec-fetch-site: same-origin`，
        并且没有 `sec-fetch-user`（那只出现在用户手动触发的导航上）。

        路径算错或过期会返回 404 页面，此时取不到口令。

        :param auth: BiliAuth object，需登录态.
        :param correspond_path: `gen_correspond_path()` 的结果.
        :return: refresh_csrf，取不到时返回空串.
        """
        header = HeaderBuilder.build(HeaderType.DOC)
        header.remove_header('sec-fetch-user')
        header.set_header('sec-fetch-site', 'same-origin')
        header.set_header('sec-fetch-dest', 'iframe')
        header.insert_before('accept-encoding', 'referer', 'https://www.bilibili.com/')
        resp = request(auth, 'get', CORRESPOND_URL.format(path=correspond_path),
                       headers=header.get())
        if resp.status_code != 200:
            return ''
        matched = re.search(r'id="1-name"[^>]*>\s*([^<\s]+)\s*<', resp.text)
        return matched.group(1) if matched else ''

    @staticmethod
    def cookie_refresh(auth, refresh_csrf: str, refresh_token: str):
        """用刷新口令换一套新 Cookie.

        发起方是 correspond 页里的 token-iframe 脚本，跑在 `www.bilibili.com`
        下，所以 origin / referer 是主站而非 passport（同源判定 same-site）。

        :param auth: BiliAuth object.
        :param refresh_csrf: `get_refresh_csrf()` 拿到的一次性口令.
        :param refresh_token: 当前持有的持久化刷新口令（**旧的那个**）.
        :return: (res_json, cookies)。res_json.data.refresh_token 是新口令，
                 cookies 里是新的 SESSDATA / bili_jct / DedeUserID / sid。
                 code 86095 = 口令错或 refresh_token 与 Cookie 不匹配.
        """
        headers = HeaderBuilder.build(HeaderType.FORM).get()
        data = {'csrf': auth.csrf, 'refresh_csrf': refresh_csrf,
                'source': 'main_web', 'refresh_token': refresh_token}
        resp = request(auth, 'post',
                       f'{BiliLoginApi.passport}/x/passport-login/web/cookie/refresh',
                       headers=headers, data=data)
        return resp.json(), requests_cookies_to_dict(resp)

    @staticmethod
    def confirm_refresh(auth, old_refresh_token: str) -> dict:
        """确认更新，让旧 refresh_token 对应的会话失效.

        ⚠️ 两个参数分属新旧两代，最容易搞反：`csrf` 要用**刷新后**的
        `bili_jct`，`refresh_token` 要用**刷新前**的那个。
        不调这一步，旧 Cookie 会一直有效，等于账号上挂着一个收不回的会话。

        :param auth: BiliAuth object，Cookie 必须已经换成新的.
        :param old_refresh_token: 刷新**前**持有的 refresh_token.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.FORM).get()
        return post_json(auth,
                         f'{BiliLoginApi.passport}/x/passport-login/web/confirm/refresh',
                         headers=headers,
                         data={'csrf': auth.csrf, 'refresh_token': old_refresh_token})

    @staticmethod
    def refresh_cookies(auth, refresh_token: str = '', force: bool = False) -> tuple:
        """完整续期流程：查是否要刷 → 算 correspondPath → 取口令 → 换 Cookie → 确认.

        成功后新 Cookie 与新 refresh_token 都已写回 auth，调用方只需要落盘。

        :param auth: BiliAuth object，需登录态.
        :param refresh_token: 覆盖 auth 上带的那个；一般不用传.
        :param force: 忽略 `cookie/info` 的判断，强制走一遍（自检用）.
        :return: (success, msg, refreshed)。refreshed 为 False 且 success 为 True
                 表示服务端说还不用刷，什么都没动.
        """
        if not auth.is_login:
            return False, '未登录，无法续期', False
        token_old = refresh_token or getattr(auth, 'refresh_token', '')
        if not token_old:
            return False, ('缺少 refresh_token，无法续期——它只在登录响应里出现一次。'
                           '请重新 `python main.py login` 一次，之后会自动落盘'), False

        info = BiliLoginApi.cookie_info(auth)
        if info.get('code') != 0:
            return False, f"查 cookie/info 失败: {info.get('code')} {info.get('message')}", False
        data = info.get('data') or {}
        if not data.get('refresh') and not force:
            return True, '服务端判定无需刷新', False

        # 用服务端给的毫秒时间戳，避开本地时钟偏移
        path = gen_correspond_path(data.get('timestamp') or now_ms())
        refresh_csrf = BiliLoginApi.get_refresh_csrf(auth, path)
        if not refresh_csrf:
            return False, 'correspond 页面未返回 refresh_csrf（路径过期或登录态失效）', False

        res_json, cookies = BiliLoginApi.cookie_refresh(auth, refresh_csrf, token_old)
        if res_json.get('code') != 0:
            return False, f"换 Cookie 失败: {res_json.get('code')} {res_json.get('message')}", False
        if not cookies.get('SESSDATA'):
            return False, f'换 Cookie 成功但响应没带新 SESSDATA: {res_json}', False

        auth.update_cookies(cookies)
        auth.refresh_token = (res_json.get('data') or {}).get('refresh_token') or ''

        confirm = BiliLoginApi.confirm_refresh(auth, token_old)
        if confirm.get('code') != 0:
            # 新 Cookie 已经生效，所以 refreshed=True，调用方仍应落盘，
            # 否则新旧两份都丢了就得重新扫码
            return False, (f"确认更新失败: {confirm.get('code')} {confirm.get('message')}"
                           '（新 Cookie 可用，但旧会话未失效）'), True
        return True, '续期完成', True

    @staticmethod
    def logout(auth) -> dict:
        """退出登录.

        :param auth: BiliAuth object.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.FORM, BiliLoginApi.passport).get()
        return post_json(auth, f'{BiliLoginApi.passport}/login/exit/v2',
                         headers=headers, data={'biliCSRF': auth.csrf})


def requests_cookies_to_dict(resp) -> dict:
    """把响应里的 Set-Cookie 收成 dict."""
    return {name: value for name, value in resp.cookies.items()}


def encrypt_password(salt: str, password: str, public_key_pem: str) -> str:
    """用服务端 RSA 公钥加密 salt+密码.

    :param salt: 服务端下发的 hash 字段.
    :param password: 明文密码.
    :param public_key_pem: PEM 格式公钥.
    :return: base64 密文.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    public_key = serialization.load_pem_public_key(public_key_pem.encode())
    cipher = public_key.encrypt(
        (salt + password).encode(),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                     algorithm=hashes.SHA256(), label=None),
    )
    return base64.b64encode(cipher).decode()


def print_qrcode(url: str) -> None:
    """在终端画二维码."""
    import qrcode

    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
