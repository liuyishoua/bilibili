#!/usr/bin/env python
# coding=utf-8
"""B站快速投稿：改顶部配置后直接运行 ``python quick_publish.py``。

默认 ``ENABLE_PUBLISH=False``，只检查登录态并打印投稿预览，不会上传或发布。
确认配置无误后把开关改成 True；投稿默认仅自己可见。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from apis.bili_apis import BiliApi
from apis.bili_creator_apis import BiliCreatorApi
from builder.auth import BiliAuth
from utils.session import has_session


# ============================== 用户配置 ============================== #
ENABLE_PUBLISH = False           # 安全开关：True 才会真正上传并投稿

VIDEO_PATH = r"D:\media\demo.mp4"
TITLE = "测试投稿"
TID = 21                         # 先在 main.py 里把 DEMO 改为 types 查询
TAG = "测试,日常"
DESC = ""
COVER_PATH = ""
PUBLIC = False                   # False=仅自己可见，True=公开

ALLOW_SCAN = True                # 无有效会话时是否允许现场扫码
VERIFY_RETRIES = 5
VERIFY_INTERVAL = 3.0


ARCHIVE_STATES = {
    0: "已发布", -1: "审核中", -2: "未通过", -3: "未通过",
    -4: "未通过", -6: "修改中", -16: "转码失败",
    -30: "审核中", -40: "定时发布", -50: "仅自己可见",
}


def get_login() -> BiliAuth:
    """恢复并续期会话；失效时按配置允许扫一次码。"""
    if has_session():
        try:
            auth = BiliAuth.from_session(auto_refresh=ENABLE_PUBLISH)
            nav = BiliApi.get_nav(auth)
            if nav.get("code") == 0 and (nav.get("data") or {}).get("isLogin"):
                return auth
        except Exception as exc:
            print(f"会话恢复失败：{exc}", file=sys.stderr)

    if not ALLOW_SCAN:
        raise RuntimeError("没有有效会话；请先在 main.py 中运行 login demo")

    print("需要扫一次码建立会话，之后会由 refresh_token 自动续期。")
    auth = BiliAuth.from_qrcode_login()
    auth.save_session()
    return auth


def validate_config() -> Path:
    if not TITLE.strip():
        raise ValueError("TITLE 不能为空")
    if TID <= 0:
        raise ValueError("TID 必须是有效分区 ID")
    if not TAG.strip():
        raise ValueError("TAG 不能为空")
    video = Path(VIDEO_PATH).expanduser().resolve()
    if not video.is_file():
        raise FileNotFoundError(f"视频不存在：{video}")
    if video.stat().st_size <= 0:
        raise ValueError(f"视频为空：{video}")
    if COVER_PATH and not Path(COVER_PATH).expanduser().is_file():
        raise FileNotFoundError(f"封面不存在：{COVER_PATH}")
    return video


def preview(auth: BiliAuth) -> None:
    nav = BiliApi.get_nav(auth)
    account = (nav.get("data") or {}).get("uname")
    print(f"当前账号：{account} (mid={auth.mid})")
    print("投稿预览：")
    print(f"  视频：{VIDEO_PATH}")
    print(f"  标题：{TITLE}")
    print(f"  分区：{TID}")
    print(f"  标签：{TAG}")
    print(f"  可见性：{'公开' if PUBLIC else '仅自己可见'}")


def publish(auth: BiliAuth, video: Path) -> tuple:
    def progress(done: int, total: int) -> None:
        print(f"  分片 {done}/{total}", end="\r", flush=True)

    print(f"开始上传：{video}")
    result = BiliCreatorApi.post_video(
        auth,
        str(video),
        title=TITLE,
        tid=TID,
        tag=TAG,
        cover_path=str(Path(COVER_PATH).expanduser().resolve()) if COVER_PATH else "",
        desc=DESC,
        private=not PUBLIC,
        on_progress=progress,
    )
    print()
    return result


def verify(auth: BiliAuth, bvid: str) -> bool:
    for attempt in range(1, VERIFY_RETRIES + 1):
        time.sleep(VERIFY_INTERVAL)
        response = BiliCreatorApi.get_my_archives(auth)
        archives = ((response.get("data") or {}).get("arc_audits")) or []
        found = next(
            (item for item in archives if (item.get("Archive") or {}).get("bvid") == bvid),
            None,
        )
        if found:
            archive = found.get("Archive") or {}
            state = ARCHIVE_STATES.get(archive.get("state"), archive.get("state"))
            print(f"回查成功：{bvid}，状态={state}")
            return True
        print(f"回查中：{attempt}/{VERIFY_RETRIES}")
    return False


def main() -> int:
    try:
        auth = get_login()
        preview(auth)
        if not ENABLE_PUBLISH:
            print("\n当前是预演模式，没有上传或投稿。确认后把 ENABLE_PUBLISH 改为 True。")
            return 0

        video = validate_config()
        success, message, response = publish(auth, video)
        if not success:
            print(f"投稿失败：{message}", file=sys.stderr)
            print(json.dumps(response, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1

        data = response.get("data") or {}
        bvid = data.get("bvid")
        print(f"投稿成功：{message}，bvid={bvid}，aid={data.get('aid')}")
        if bvid and verify(auth, bvid):
            return 0
        print("投稿接口成功，但暂未在稿件列表回查到结果。", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"快速投稿失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
