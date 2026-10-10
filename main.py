# coding=utf-8
"""BilibiliApis 直接体验入口。

和 ``../DouYin_Spider/main.py`` 一样：修改下面“用户配置”，然后直接运行
``python main.py``。默认只读取一个公开视频，不会修改账号数据。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import qrcode

from apis.bili_apis import BiliApi
from apis.bili_creator_apis import BiliCreatorApi
from apis.bili_live_apis import BiliLiveApi
from apis.bili_login_apis import BiliLoginApi
from builder.auth import BiliAuth
from live.server import BiliLiveDanmaku
from utils.session import has_session


# ============================== 用户配置 ============================== #
# 选择一个 demo：view / search / user / live / danmaku / login / archives / types
DEMO = "view"

BVID = "BV1GJ411x7h7"       # view
KEYWORD = "编程"             # search
SEARCH_NUM = 10
SEARCH_ORDER = "totalrank"  # totalrank / click / pubdate / dm / stow
USER_MID = os.getenv("BILIBILI_USER_MID", "2")  # user；可用环境变量覆盖
ROOM_ID = "1"                # live / danmaku
LISTEN_SECONDS = 30           # danmaku；0 表示一直监听

QR_TIMEOUT = 180             # login 扫码最长等待秒数
SHOW_QR = True               # 是否在终端绘制二维码
QR_IMAGE = "login_qrcode.png"  # 同时生成标准 PNG，避免终端字符比例导致无法扫描


def get_auth(require_login: bool = False, auto_refresh: bool = False) -> BiliAuth:
    """优先使用落盘会话；只读 demo 没有会话时自动创建匿名设备。"""
    if has_session():
        auth = BiliAuth.from_session(auto_refresh=auto_refresh)
        if auth.is_login or not require_login:
            return auth
    if require_login:
        raise RuntimeError("需要登录，请把 DEMO 改为 login，扫码建立会话后再运行")
    return BiliAuth.anonymous()


def save_auth(auth: BiliAuth) -> None:
    path = auth.save_session()
    print(f"会话已保存：{path}")


def dump(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def demo_view() -> None:
    data = BiliApi.get_video_info(get_auth(), bvid=BVID)
    if data.get("code") != 0:
        return dump(data)
    video = data["data"]
    print(f"标题   : {video['title']}")
    print(f"UP 主  : {video['owner']['name']} (mid={video['owner']['mid']})")
    print(f"aid/cid: {video['aid']} / {video['cid']}")
    stat = video["stat"]
    print(f"统计   : 播放 {stat['view']} 弹幕 {stat['danmaku']} 点赞 {stat['like']}")
    print(f"简介   : {(video.get('desc') or '').strip()[:160]}")


def demo_search() -> None:
    success, message, works = BiliApi.search_by_num(
        get_auth(), KEYWORD, SEARCH_NUM, SEARCH_ORDER
    )
    if not success:
        raise RuntimeError(message)
    print(f"关键词“{KEYWORD}”共取得 {len(works)} 条：")
    for item in works:
        print(f"  {item['bvid']}  播放 {item['play']:>8}  {item['title']}")


def demo_user() -> None:
    auth = get_auth()
    user = BiliApi.get_user_info(auth, USER_MID)
    if user.get("code") != 0:
        return dump(user)
    info = user["data"]
    print(f"用户: {info.get('name')} (mid={info.get('mid')})")
    print(f"签名: {info.get('sign')}")
    success, message, works = BiliApi.get_all_user_videos(auth, USER_MID)
    if not success:
        raise RuntimeError(message)
    print(f"投稿: {len(works)} 条")
    for item in works[:20]:
        print(f"  {item.get('bvid')}  {item.get('title')}")


def demo_live() -> None:
    auth = get_auth()
    init = BiliLiveApi.get_room_init(auth, ROOM_ID)
    if init.get("code") != 0:
        return dump(init)
    room_id = init["data"]["room_id"]
    info = BiliLiveApi.get_room_info(auth, room_id)
    room = (info.get("data") or {}).get("room_info") or {}
    print(f"房间号: {room_id}")
    print(f"状态  : {'直播中' if init['data']['live_status'] == 1 else '未开播'}")
    print(f"标题  : {room.get('title')}")
    print(f"分区  : {room.get('parent_area_name')} / {room.get('area_name')}")
    print(f"人气  : {room.get('online')}")


def demo_danmaku() -> None:
    client = BiliLiveDanmaku(get_auth(), ROOM_ID)
    client.on(
        "SEND_GIFT",
        lambda payload: print(
            f"[礼物] {payload['data']['uname']} -> {payload['data']['giftName']}"
        ),
    )
    client.start(listen=LISTEN_SECONDS)


def demo_login() -> None:
    print("请用 B站 App 扫描二维码并确认……")
    auth = BiliAuth.anonymous()
    response = BiliLoginApi.qrcode_generate(auth)
    if response.get("code") != 0:
        raise RuntimeError(f"申请二维码失败：{response}")

    data = response["data"]
    url = data["url"]
    key = data["qrcode_key"]
    image_path = Path(QR_IMAGE).resolve()
    qrcode.make(url).save(image_path)
    print(f"二维码图片：{image_path}", flush=True)
    print(f"二维码链接：{url}", flush=True)
    if SHOW_QR:
        from apis.bili_login_apis import print_qrcode

        print_qrcode(url)

    deadline = time.time() + QR_TIMEOUT
    last_state = None
    while time.time() < deadline:
        poll, cookies = BiliLoginApi.qrcode_poll(auth, key)
        state = poll.get("data") or {}
        code = state.get("code")
        if code == 0:
            auth.update_cookies(cookies)
            auth.refresh_token = state.get("refresh_token") or ""
            nav = BiliApi.get_nav(auth)
            print(f"登录成功：{(nav.get('data') or {}).get('uname')}")
            save_auth(auth)
            image_path.unlink(missing_ok=True)
            return
        if code == 86038:
            raise RuntimeError("二维码已失效，请重新运行 login demo")
        if code != last_state:
            print(f"扫码状态：{code} {state.get('message') or ''}", flush=True)
            last_state = code
        time.sleep(2)
    raise RuntimeError(f"{QR_TIMEOUT} 秒内未完成扫码")


def demo_archives() -> None:
    response = BiliCreatorApi.get_my_archives(get_auth(require_login=True))
    if response.get("code") != 0:
        return dump(response)
    archives = ((response.get("data") or {}).get("arc_audits")) or []
    print(f"我的稿件：{len(archives)} 条")
    for item in archives:
        archive = item.get("Archive") or {}
        print(f"  {archive.get('bvid')}  state={archive.get('state')}  {archive.get('title')}")


def demo_types() -> None:
    for parent in BiliCreatorApi.get_archive_types(get_auth(require_login=True)):
        print(parent.get("name"))
        for child in parent.get("children") or []:
            print(f"    tid={child.get('id'):<6} {child.get('name')}")


DEMOS = {
    "view": demo_view,
    "search": demo_search,
    "user": demo_user,
    "live": demo_live,
    "danmaku": demo_danmaku,
    "login": demo_login,
    "archives": demo_archives,
    "types": demo_types,
}


def main() -> int:
    demo = DEMOS.get(DEMO)
    if demo is None:
        raise ValueError(f"未知 DEMO={DEMO!r}，可选：{', '.join(DEMOS)}")
    print(f"正在运行 demo：{DEMO}\n")
    demo()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
