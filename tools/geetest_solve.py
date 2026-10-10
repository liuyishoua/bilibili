"""极验 v3 纯 Python 全链路：fullpage 初始化 → click → 识别 → validate。

协议顺序、脚本版本和 RSA 段位置均由 Chrome DevTools 实抓确认：
``fullpage.9.2.0-guwyxh.js`` 首发 AES+RSA，fullpage ajax 复用 AES；切换到
``click.3.1.2.js`` 后重新生成 AES，并在 click ajax 再附 RSA 密钥段。

直接运行：``python -m tools.geetest_solve solve --attempts 4``。
识别失败会调用 refresh.php 换题；所有中间图与 session 都落在 ``--dir``。
"""

import argparse
import json
import os
import re
import time

from PIL import Image

from apis.bili_login_apis import BiliLoginApi, requests_cookies_to_dict
from builder.auth import BiliAuth
from utils import geetest_w
from utils.geetest_vision import annotate, assemble, prepare
from utils.http_util import request

GEETEST_GET = 'https://api.geetest.com/get.php'
GEETEST_AJAX = 'https://api.geetest.com/ajax.php'
GEETEST_REFRESH = 'https://api.geetest.com/refresh.php'
GEETEST_TYPE = 'https://api.geetest.com/gettype.php'
REFERER = 'https://passport.bilibili.com/'
SHOT_DIR = '_gt/shot'


def _jsonp(text: str) -> dict:
    """扒掉 JSONP 外壳."""
    match = re.search(r'^[^(]*\((.*)\)\s*$', text.strip(), re.S)
    return json.loads(match.group(1) if match else text)


def probe_type(auth, gt: str) -> dict:
    """问 `gettype.php` 这个 gt 走哪种验证形态.

    ⚠️ 2026-08-16 实测：B站 passport 的 gt 返回 **`type": "fullpage"`**，
    即「智能验证」通道，而不是点选。fullpage 会先尝试无感放行，
    只有风控判定可疑时才降级下发具体题面（点选或滑动）。

    这也解释了为什么带 `w` 的 `get.php` 只回 c/s 不回 `pic`——
    题面本来就不在这一步下发。而 `is_next=true&type=click` 能拿到图，
    是**强行指定题型**绕过了 fullpage 判定，取到的图不属于当前会话的
    可提交状态，据此算出的坐标提交回去不会通过。

    :param auth: BiliAuth object，可为 None.
    :param gt: 极验 gt.
    :return: gettype.php 的 data 段.
    """
    resp = request(auth, 'GET', GEETEST_TYPE, headers={'referer': REFERER},
                   params={'gt': gt, 'callback': f'geetest_{int(time.time() * 1000)}'},
                   cookies={}, timeout=30)
    return _jsonp(resp.text).get('data') or {}


def fetch(workdir: str = SHOT_DIR, auth=None) -> dict:
    """拉一道新题，切图落盘.

    顺序严格对齐 Chrome DevTools 实抓：
    1. `get.php(w=init AES+RSA)` 建立 fullpage 会话，拿 c/s；
    2. `ajax.php(w=fullpage AES)` 做无感判定，低分时返回 result=click；
    3. `get.php?is_next=true&type=click` 不带 w，下发题图和新的 c/s。

    :param workdir: 切图与会话状态的输出目录.
    :return: 会话状态 dict.
    """
    auth = auth or BiliAuth.anonymous()
    captcha = BiliLoginApi.get_captcha(auth)
    if captcha.get('code') != 0:
        raise RuntimeError(f'B站 captcha 失败: {captcha}')
    info = captcha['data']
    gt, challenge = info['geetest']['gt'], info['geetest']['challenge']

    kind = probe_type(auth, gt).get('type')
    print(f'gt 产品类型 = {kind}')
    if kind != 'fullpage':
        print(f'[warn] 预期 fullpage，实际为 {kind!r}，仍按服务端结果继续。')

    # 极验 Cookie 必须在三发之间延续。统一 HTTP 层会主动清理自己的 jar，
    # 所以在这里显式收集每次 Set-Cookie，再作为下一发 cookies 传回去。
    geetest_cookies = {}
    aes_key = geetest_w.gen_aes_key()
    payload = geetest_w.build_init_payload(gt, challenge)
    w = geetest_w.build_w(payload, key=aes_key, with_rsa=True)
    common = {'gt': gt, 'challenge': challenge, 'lang': 'zh-cn',
              'pt': 0, 'client_type': 'web'}
    init_params = {
        **common, 'w': w,
        'callback': f'geetest_{int(time.time() * 1000)}',
    }
    resp = request(auth, 'GET', GEETEST_GET,
                   headers={'referer': REFERER}, params=init_params,
                   cookies=geetest_cookies, timeout=30)
    geetest_cookies.update(requests_cookies_to_dict(resp))
    body = _jsonp(resp.text)
    if body.get('status') != 'success':
        raise RuntimeError(f'get.php 初始化拒绝: {body}')
    data = body.get('data') or {}
    init_c = data.get('c')
    init_s = data.get('s') or ''

    started_at = int(time.time() * 1000)
    fullpage_payload = geetest_w.build_payload(
        gt, challenge, passtime=800, c=init_c, s=init_s)
    fullpage_w = geetest_w.build_w(
        fullpage_payload, key=aes_key, with_rsa=False)
    ajax_params = {
        **common, 'w': fullpage_w,
        'callback': f'geetest_{int(time.time() * 1000)}',
    }
    resp = request(auth, 'GET', GEETEST_AJAX,
                   headers={'referer': REFERER}, params=ajax_params,
                   cookies=geetest_cookies, timeout=30)
    geetest_cookies.update(requests_cookies_to_dict(resp))
    decision = _jsonp(resp.text)
    result = (decision.get('data') or {}).get('result')
    print(f'fullpage 判定 = {result}')
    if result != 'click':
        # 某些设备可无感直过，直接把 validate 交给调用方。
        validate = (decision.get('data') or {}).get('validate')
        if validate:
            return {'gt': gt, 'token': info['token'], 'challenge': challenge,
                    'validate': validate, 'seccode': f'{validate}|jordan',
                    'raw': decision}
        raise RuntimeError(f'fullpage 未降级到 click: {decision}')

    puzzle_params = {
        'is_next': 'true', 'type': 'click', 'gt': gt, 'challenge': challenge,
        'lang': 'zh-cn', 'https': 'true', 'protocol': 'https://',
        'offline': 'false', 'product': 'embed', 'api_server': 'api.geetest.com',
        'isPC': 'true', 'autoReset': 'true', 'width': '100%',
        'callback': f'geetest_{int(time.time() * 1000)}',
    }
    resp = request(auth, 'GET', GEETEST_GET,
                   headers={'referer': REFERER}, params=puzzle_params,
                   cookies=geetest_cookies, timeout=30)
    geetest_cookies.update(requests_cookies_to_dict(resp))
    body = _jsonp(resp.text)
    if body.get('status') != 'success':
        raise RuntimeError(f'get.php 题面拒绝: {body}')
    data = body.get('data') or {}
    c = data.get('c')
    s = data.get('s') or ''

    pic = data.get('pic')
    if not pic:
        raise SystemExit(
            f'没拿到 click 题面（gt={kind}, decision={result}, data={data}）')
    servers = data.get('static_servers') or ['static.geetest.com/']
    url = f"https://{servers[0].rstrip('/')}/{pic.lstrip('/')}"
    raw = request(auth, 'GET', url, headers={'referer': REFERER}, timeout=30).content

    os.makedirs(workdir, exist_ok=True)
    sprite_path = os.path.join(workdir, 'captcha.jpg')
    with open(sprite_path, 'wb') as f:
        f.write(raw)

    prep = prepare(Image.open(sprite_path), workdir=workdir)
    state = {
        'gt': gt, 'challenge': challenge, 'token': info['token'],
        'pic': pic, 'c': c, 's': s, 'aes_key': aes_key,
        'cookies': '; '.join(f'{k}={v}' for k, v in geetest_cookies.items()),
        'fetched_at': started_at,
        'puzzle_boxes': prep['puzzle_boxes'],
        'hint_boxes': prep['hint_boxes'],
        'puzzle_size': list(prep['puzzle_size']),
    }
    with open(os.path.join(workdir, 'session.json'), 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    print(f"题面已落盘: {sprite_path}")
    print(f"  gt        = {gt}")
    print(f"  challenge = {challenge}")
    print(f"  候选 {len(prep['puzzle_boxes'])} 个，提示 {len(prep['hint_boxes'])} 个")
    print(f"\n请看图认字：")
    print(f"  提示条 {os.path.join(workdir, 'hint.png')}")
    for i in range(len(prep['puzzle_boxes'])):
        print(f"  候选{i + 1}  {os.path.join(workdir, f'cand{i + 1}.png')}")
    print(f"\n然后跑：python -m tools.geetest_solve submit "
          f"--hint 字,字,字,字 --cands 字,字,字,字,字")
    return state


def crop(workdir: str = SHOT_DIR) -> dict:
    """对已经落盘的 sprite 做切图，供人/模型认字.

    用于**浏览器拦截**流程：sprite 与 session.json 由你从 Network 里抄下来，
    这一步只负责切图，不联网。

    :param workdir: 目录，需已有 captcha.jpg 与 session.json.
    :return: prepare() 的返回值.
    """
    sprite_path = os.path.join(workdir, 'captcha.jpg')
    if not os.path.exists(sprite_path):
        raise SystemExit(f'找不到 {sprite_path}，请先把 sprite 存到这里')

    prep = prepare(Image.open(sprite_path), workdir=workdir)

    # 把检测到的框回写进 session.json，submit 时要用
    session_path = os.path.join(workdir, 'session.json')
    state = {}
    if os.path.exists(session_path):
        with open(session_path, encoding='utf-8') as f:
            state = json.load(f)
    state.update({
        'puzzle_boxes': prep['puzzle_boxes'],
        'hint_boxes': prep['hint_boxes'],
        'puzzle_size': list(prep['puzzle_size']),
    })
    state.setdefault('fetched_at', int(time.time() * 1000))
    with open(session_path, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    print(f"候选 {len(prep['puzzle_boxes'])} 个，提示 {len(prep['hint_boxes'])} 个")
    print(f"  提示条 {os.path.join(workdir, 'hint.png')}")
    for i in range(len(prep['puzzle_boxes'])):
        box = prep['puzzle_boxes'][i]
        center = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
        print(f"  候选{i + 1}  {os.path.join(workdir, f'cand{i + 1}.png')}  center={center}")
    return prep


def submit(hint_chars, cand_chars, workdir: str = SHOT_DIR) -> dict:
    """用认出来的字提交，换 validate.

    :param hint_chars: 提示条汉字，按从左到右（即点击顺序）.
    :param cand_chars: 拼图候选汉字，顺序与 session.json 的 puzzle_boxes 一致.
    :param workdir: fetch 时用的目录.
    :return: ajax.php 的响应 JSON.
    """
    with open(os.path.join(workdir, 'session.json'), encoding='utf-8') as f:
        state = json.load(f)

    prep = {'puzzle_boxes': state['puzzle_boxes'],
            'hint_boxes': state['hint_boxes'],
            'puzzle_size': tuple(state['puzzle_size'])}
    result = assemble(prep, hint_chars, cand_chars)
    if result['missing']:
        raise RuntimeError(f"提示里这些字没在候选里找到: {result['missing']}")

    sprite = Image.open(os.path.join(workdir, 'captcha.jpg'))
    anno = annotate(sprite, result, os.path.join(workdir, 'annotated.png'))
    print('点击顺序：')
    for i, item in enumerate(result['picked'], 1):
        print(f"  {i}. {item['char']}  cand{item['cand_index']}  {item['center']}")
    print(f"  a = {result['a']}")
    print(f"  标注图 = {anno}")

    # passtime 是「题面出现到提交」的毫秒数。真人点 4 个字要好几秒，
    # 太小会被判为机器；这里按实际耗时算，并兜一个下限。
    passtime = max(int(time.time() * 1000) - state['fetched_at'], 3000)

    payload = geetest_w.build_click_payload(
        state['gt'], state['challenge'], result['a'], state['pic'],
        passtime, c=state.get('c'), s=state.get('s') or '')
    aes_key = state.get('aes_key')
    w = geetest_w.build_w(payload, key=aes_key, with_rsa=not aes_key)

    auth = BiliAuth.from_cookie(state['cookies'], fill_device=False)
    resp = request(auth, 'GET', GEETEST_AJAX, headers={'referer': REFERER},
                   params={'gt': state['gt'], 'challenge': state['challenge'],
                           'lang': 'zh-cn', 'pt': 0, 'client_type': 'web',
                           'w': w,
                           'callback': f'geetest_{int(time.time() * 1000)}'},
                   timeout=30)
    body = _jsonp(resp.text)
    print(f"\najax.php -> {json.dumps(body, ensure_ascii=False)}")

    validate = (body.get('data') or {}).get('validate')
    if validate:
        print(f"\n通过！validate = {validate}")
        print(f"  seccode   = {validate}|jordan")
        print(f"  token     = {state['token']}")
        print(f"  challenge = {state['challenge']}")
    else:
        print(f"\n未通过：{body.get('data', {}).get('result') or body}")
    return body


def auto(workdir: str = SHOT_DIR, dry_run: bool = False,
         attempts: int = 3) -> dict:
    """全自动：切图 → 模型度量匹配 → 提交，失败时刷新题面重试.

    识别用 `utils.geetest_hybrid`：字形匹配定点击顺序（不认字，
    避开提示条那些 30 px 小字的识别难题），ddddocr 认候选字面作旁证。

    ⚠️ 识别可靠性有限，务必看 `warnings`。相似度低于 0.45 的匹配
    基本等于蒙，提交多半不过。

    :param workdir: 需已有 captcha.jpg 与 session.json.
    :param dry_run: 只算坐标不提交，用来先看识别质量.
    :param attempts: 服务端返回 fail 后最多尝试的题面数.
    :return: dry_run 时返回识别结果，否则返回 ajax.php 响应.
    """
    from utils.geetest_hybrid import solve as hybrid_solve

    session_path = os.path.join(workdir, 'session.json')
    if not os.path.exists(session_path):
        raise SystemExit(f'找不到 {session_path}，无法提交')
    with open(session_path, encoding='utf-8') as f:
        state = json.load(f)

    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        sprite_path = os.path.join(workdir, 'captcha.jpg')
        if not os.path.exists(sprite_path):
            raise SystemExit(f'找不到 {sprite_path}')

        sprite = Image.open(sprite_path)
        result = hybrid_solve(sprite)

        print(f'识别结果（第 {attempt}/{attempts} 题）：')
        print(f"  提示字面 = {' '.join(c or '?' for c in result.get('hint_text', []))}")
        print(f"  候选字面 = {' '.join(c or '?' for c in result['cand_chars'])}")
        print('  点击顺序：')
        for i, (point, char, match) in enumerate(
                zip(result['order'], result['ordered_chars'], result['match']), 1):
            print(f"    {i}. {char or '?'}  cand{match['puzzle'] + 1}  "
                  f"{point}  模型相似度={match['score']}")

        for warn in result['warnings']:
            print(f'  [warn] {warn}')

        width, height = result['puzzle_size']
        ratios = [(x / width, y / height) for x, y in result['order']]
        a = geetest_w.encode_click_a_from_ratio(ratios)
        print(f'  a = {a}')

        from utils.geetest_vision import annotate as _annotate
        picked = [{'char': c or '?', 'cand_index': m['puzzle'] + 1,
                   'box': result['puzzle_boxes'][m['puzzle']], 'center': p}
                  for c, m, p in zip(result['ordered_chars'], result['match'],
                                     result['order'])]
        anno = _annotate(sprite, {'picked': picked},
                         os.path.join(workdir, f'annotated-{attempt}.png'))
        print(f'  标注图 = {anno}')

        if dry_run:
            return {'a': a, 'result': result}

        body = _post_ajax(state, a, workdir)
        if (body.get('data') or {}).get('validate'):
            return body
        if ((body.get('data') or {}).get('result') != 'fail'
                or attempt == attempts):
            return body
        state = _refresh(state, workdir)

    return body


def _refresh(state: dict, workdir: str) -> dict:
    """对齐 click.3.1.2.js：验证失败后从 refresh.php 换一张题图。"""
    auth = BiliAuth.from_cookie(state.get('cookies') or '', fill_device=False)
    resp = request(auth, 'GET', GEETEST_REFRESH, headers={'referer': REFERER},
                   params={'gt': state['gt'], 'challenge': state['challenge'],
                           'lang': 'zh-cn', 'type': 'click',
                           'callback': f'geetest_{int(time.time() * 1000)}'},
                   timeout=30)
    auth.update_cookies(requests_cookies_to_dict(resp))
    body = _jsonp(resp.text)
    data = body.get('data') or {}
    pic = data.get('pic')
    if body.get('status') != 'success' or not pic:
        raise RuntimeError(f'refresh.php 拒绝: {body}')

    servers = data.get('image_servers') or ['static.geetest.com/']
    url = f"https://{servers[0].rstrip('/')}/{pic.lstrip('/')}"
    raw = request(auth, 'GET', url, headers={'referer': REFERER}, timeout=30).content
    sprite_path = os.path.join(workdir, 'captcha.jpg')
    with open(sprite_path, 'wb') as f:
        f.write(raw)

    prep = prepare(Image.open(sprite_path), workdir=workdir)
    state.update({
        'pic': pic,
        'cookies': auth.cookies_str,
        'fetched_at': int(time.time() * 1000),
        'puzzle_boxes': prep['puzzle_boxes'],
        'hint_boxes': prep['hint_boxes'],
        'puzzle_size': list(prep['puzzle_size']),
    })
    with open(os.path.join(workdir, 'session.json'), 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print(f'\n已刷新题面: {pic}')
    return state


def solve(workdir: str = SHOT_DIR, attempts: int = 3, auth=None) -> dict:
    """从 B站 challenge 开始，一次跑完纯 Python 极验链路。"""
    first = fetch(workdir, auth=auth)
    if first.get('validate'):
        return first
    body = auto(workdir, attempts=attempts)
    validate = (body.get('data') or {}).get('validate')
    if not validate:
        raise RuntimeError(f'极验在 {attempts} 次题面内未通过: {body}')
    with open(os.path.join(workdir, 'session.json'), encoding='utf-8') as f:
        state = json.load(f)
    return {
        'gt': state['gt'], 'token': state['token'],
        'challenge': state['challenge'], 'validate': validate,
        'seccode': f'{validate}|jordan', 'score': (body.get('data') or {}).get('score'),
        'raw': body,
    }


def _post_ajax(state: dict, a: str, workdir: str) -> dict:
    """把算好的 a 字段提交给 ajax.php.

    :param state: session.json 的内容，需含 gt/challenge/pic/c/s/cookies.
    :param a: 点击坐标串.
    :param workdir: 工作目录，仅用于报错提示.
    :return: ajax.php 的响应 JSON.
    """
    # 只把 passtime 字段伪装成 3 秒不够，服务端还会拿请求到达时间校验。
    # 模型推理太快时补足真实等待，避免正常坐标被 `duration short` 拒掉。
    elapsed = int(time.time() * 1000) - state.get('fetched_at', 0)
    if elapsed < 3500:
        time.sleep((3500 - elapsed) / 1000)
    passtime = int(time.time() * 1000) - state.get('fetched_at', 0)
    payload = geetest_w.build_click_payload(
        state['gt'], state['challenge'], a, state['pic'],
        passtime, c=state.get('c'), s=state.get('s') or '')

    # Chrome DevTools 实抓确认：fullpage 的第二发 ajax.php 复用 init 密钥，
    # 但切换到 click.3.1.2.js 后会重新生成 AES 密钥，且 click ajax 的 w
    # 始终追加 256 hex 的 RSA 密钥段。复用 fullpage 密钥但省略 RSA 会稳定报
    # `param decrypt error / error_03`。
    w = geetest_w.build_w(payload, with_rsa=True)

    auth = BiliAuth.from_cookie(state['cookies'], fill_device=False)
    resp = request(auth, 'GET', GEETEST_AJAX, headers={'referer': REFERER},
                   params={'gt': state['gt'], 'challenge': state['challenge'],
                           'lang': 'zh-cn', 'pt': 0, 'client_type': 'web',
                           'w': w,
                           'callback': f'geetest_{int(time.time() * 1000)}'},
                   timeout=30)
    body = _jsonp(resp.text)
    print(f"\najax.php -> {json.dumps(body, ensure_ascii=False)}")

    validate = (body.get('data') or {}).get('validate')
    if validate:
        print(f"\n通过！validate = {validate}")
        print(f"  seccode   = {validate}|jordan")
        print(f"  token     = {state.get('token')}")
        print(f"  challenge = {state['challenge']}")
    else:
        print(f"\n未通过：{(body.get('data') or {}).get('result') or body}")
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description='极验点选全链路')
    sub = parser.add_subparsers(dest='cmd', required=True)
    for name, help_text in [('solve', '全链路：拉题、识别并提交'),
                            ('fetch', '拉题并切图'),
                            ('crop', '对已有 captcha.jpg 切图（浏览器拦截流程）'),
                            ('auto', '全自动：切图 → 混合识别 → 提交')]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument('--dir', default=SHOT_DIR)
        if name in ('solve', 'auto'):
            p.add_argument('--attempts', type=int, default=3,
                           help='失败后换题重试次数（默认 3）')
        if name == 'auto':
            p.add_argument('--dry-run', action='store_true',
                           help='只算坐标不提交，看识别质量')
    sp = sub.add_parser('submit', help='用认出的字提交')
    sp.add_argument('--dir', default=SHOT_DIR)
    sp.add_argument('--hint', required=True, help='提示条汉字，逗号分隔，按点击顺序')
    sp.add_argument('--cands', required=True, help='候选汉字，逗号分隔，按 cand 编号')
    args = parser.parse_args()

    if args.cmd == 'solve':
        solve(args.dir, attempts=args.attempts)
    elif args.cmd == 'fetch':
        fetch(args.dir)
    elif args.cmd == 'crop':
        crop(args.dir)
    elif args.cmd == 'auto':
        auto(args.dir, dry_run=args.dry_run, attempts=args.attempts)
    else:
        submit([c.strip() for c in args.hint.split(',') if c.strip()],
               [c.strip() for c in args.cands.split(',') if c.strip()],
               args.dir)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
