"""短信登录驱动脚本.

极验默认由本项目纯 Python 完成；短信码仍由用户实时输入。
需要对照官方控件时可加 ``--manual-geetest``。

流程压成两步，中间不需要你复制粘贴任何 gt / challenge / validate：

    python -m tools.sms_login start --tel 手机号
        纯算极验 → 自动发短信

    python -m tools.sms_login code --code 123456
        用验证码换会话，写入 session.json

会话状态存在 `tools/.sms_state.json`，同一套 Cookie 必须贯穿全程，
所以两步之间不要另起匿名会话。
"""

import argparse
import json
import os
import shutil
import sys
import time
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apis.bili_apis import BiliApi              # noqa: E402
from apis.bili_login_apis import BiliLoginApi   # noqa: E402
from builder.auth import BiliAuth               # noqa: E402
from tools.geetest_helper import serve          # noqa: E402
from utils.common_util import cookie_header     # noqa: E402
from utils.session import SESSION_FILE          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, '.sms_state.json')


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        raise SystemExit('缺少会话状态，请先跑 python -m tools.sms_login start --tel 手机号')
    with open(STATE_FILE, encoding='utf-8') as f:
        return json.load(f)


def save_state(state: dict) -> None:
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def cmd_start(args):
    auth = BiliAuth.anonymous()
    if args.manual_geetest:
        res = BiliLoginApi.get_captcha(auth)
        if res.get('code') != 0:
            raise SystemExit(f'申请极验失败: {res}')
        info = res['data']
        gt, challenge = info['geetest']['gt'], info['geetest']['challenge']
        print(f'已申请极验  gt={gt}  challenge={challenge}')

        url = f'http://127.0.0.1:{args.port}'
        print(f'即将打开 {url}，请完成点选（{args.timeout} 秒内）')
        if not args.no_open:
            import threading
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()

        result = serve(gt, challenge, args.port, args.timeout)
        if not result or not result.get('geetest_validate'):
            raise SystemExit('没拿到验证结果（超时或未完成）')
        validate = result['geetest_validate']
        geetest = {
            'token': info['token'],
            'challenge': result.get('geetest_challenge') or challenge,
            'validate': validate,
            'seccode': result.get('geetest_seccode') or f'{validate}|jordan',
        }
    else:
        from tools.geetest_solve import solve

        geetest = solve(os.path.join(HERE, '.geetest'),
                        attempts=args.attempts, auth=auth)
        validate = geetest['validate']
    print(f'验证通过  validate={validate[:24]}...')
    send = BiliLoginApi.sms_send(auth, args.tel, geetest, cid=args.cid)
    if send.get('code') != 0:
        raise SystemExit(f"发短信失败: code={send.get('code')} {send.get('message')}")

    save_state({
        'cookies': auth.cookie,
        'tel': args.tel,
        'cid': args.cid,
        'captcha_key': send['data']['captcha_key'],
        'sent_at': int(time.time()),
    })
    print('\n短信已发出。收到后跑：')
    print('    python -m tools.sms_login code --code 收到的6位数字')


def cmd_code(args):
    state = load_state()
    age = int(time.time()) - state.get('sent_at', 0)
    if age > 300:
        print(f'⚠️ 验证码是 {age} 秒前发的，可能已过期；失败的话重跑 start')

    auth = BiliAuth.from_cookie(cookie_header(state['cookies']), fill_device=False)
    success, msg, res = BiliLoginApi.sms_login(
        auth, state['tel'], lambda: args.code, cid=state.get('cid', 86),
        captcha_key=state['captcha_key'])
    print(msg)
    if not success:
        print(json.dumps(res, ensure_ascii=False)[:400])
        raise SystemExit(1)

    # 别把已有的可用会话覆盖没了
    if os.path.exists(SESSION_FILE):
        shutil.copyfile(SESSION_FILE, SESSION_FILE + '.bak')
        print(f'原会话已备份到 {SESSION_FILE}.bak')
    # 落 session.json 而不是只落 cookies.txt：短信登录同样会下发
    # refresh_token，丢了它就无法自动续期（见 utils/session.py）
    path = auth.save_session()

    nav = BiliApi.get_nav(auth)
    data = nav.get('data') or {}
    print(f"isLogin={data.get('isLogin')} uname={data.get('uname')} mid={data.get('mid')}")
    print(f"会话已保存到 {path}（refresh_token: {'有' if auth.refresh_token else '无'}）")
    os.remove(STATE_FILE)


def build_parser():
    parser = argparse.ArgumentParser(description='B站短信登录（极验默认纯算）')
    sub = parser.add_subparsers(dest='step', required=True)

    p = sub.add_parser('start', help='纯算极验 → 发短信')
    p.add_argument('--tel', required=True, help='手机号')
    p.add_argument('--cid', type=int, default=86, help='国际区号')
    p.add_argument('--port', type=int, default=8777)
    p.add_argument('--timeout', type=int, default=300)
    p.add_argument('--no-open', action='store_true', help='不自动打开浏览器')
    p.add_argument('--attempts', type=int, default=4, help='纯算识别换题次数')
    p.add_argument('--manual-geetest', action='store_true',
                   help='改用本地官方控件人工点选')
    p.set_defaults(func=cmd_start)

    p = sub.add_parser('code', help='用收到的验证码换 Cookie')
    p.add_argument('--code', required=True)
    p.set_defaults(func=cmd_code)

    return parser


if __name__ == '__main__':
    args = build_parser().parse_args()
    args.func(args)
