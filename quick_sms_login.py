#!/usr/bin/env python
# coding=utf-8
"""B站手机号登录 demo：纯算极验 → 发短信 → 输入短信码 → 保存会话。"""

from __future__ import annotations

import json
import shutil
import sys
import threading
import webbrowser
from pathlib import Path

from apis.bili_apis import BiliApi
from apis.bili_login_apis import BiliLoginApi
from builder.auth import BiliAuth
from tools.geetest_helper import serve
from utils.session import SESSION_FILE


# ============================== 用户配置 ============================== #
PHONE = ""                  # 手机号；留空时在终端输入
COUNTRY_CODE = 86           # 中国大陆 86
SMS_CODE = ""              # 留空时收到短信后在终端输入
HELPER_PORT = 8777
GEETEST_TIMEOUT = 300
OPEN_BROWSER = True         # 自动打开本地官方极验页面
PURE_GEETEST = True         # True=纯 Python；False=官方控件人工点选
GEETEST_ATTEMPTS = 4        # 识别失败时自动换题
GEETEST_DIR = Path("_gt/sms")


def get_phone() -> str:
    phone = PHONE.strip() or input("手机号：").strip()
    if not phone:
        raise ValueError("手机号不能为空")
    return phone


def complete_geetest(auth: BiliAuth) -> tuple[dict, dict]:
    if PURE_GEETEST:
        from tools.geetest_solve import solve

        print("正在纯算极验……", flush=True)
        result = solve(str(GEETEST_DIR), attempts=GEETEST_ATTEMPTS, auth=auth)
        solved = {key: result[key]
                  for key in ("token", "challenge", "validate", "seccode")}
        return solved, result.get("raw") or {}

    response = BiliLoginApi.get_captcha(auth)
    if response.get("code") != 0:
        raise RuntimeError(f"申请极验失败：{response}")
    info = response["data"]
    geetest = info["geetest"]
    gt = geetest["gt"]
    challenge = geetest["challenge"]

    url = f"http://127.0.0.1:{HELPER_PORT}"
    print(f"请在 {GEETEST_TIMEOUT} 秒内完成官方极验：{url}", flush=True)
    if OPEN_BROWSER:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    result = serve(gt, challenge, HELPER_PORT, GEETEST_TIMEOUT)
    if not result or not result.get("geetest_validate"):
        raise RuntimeError("未取得极验结果（超时或未完成）")

    validate = result["geetest_validate"]
    solved = {
        "token": info["token"],
        "challenge": result.get("geetest_challenge") or challenge,
        "validate": validate,
        "seccode": result.get("geetest_seccode") or f"{validate}|jordan",
    }
    return solved, response


def backup_session() -> None:
    session = Path(SESSION_FILE)
    if session.exists():
        backup = session.with_suffix(session.suffix + ".bak")
        shutil.copyfile(session, backup)
        print(f"原会话已备份：{backup}")


def main() -> int:
    try:
        phone = get_phone()
        auth = BiliAuth.anonymous()
        solved, _ = complete_geetest(auth)
        print("极验通过，正在发送短信……", flush=True)
        sent = BiliLoginApi.sms_send(auth, phone, solved, cid=COUNTRY_CODE)
        if sent.get("code") != 0:
            raise RuntimeError(
                f"短信发送失败：code={sent.get('code')} {sent.get('message')}"
            )

        print("短信已发送。", flush=True)
        code = SMS_CODE.strip() or input("请输入短信验证码：").strip()
        if not code:
            raise ValueError("短信验证码不能为空")
        success, message, response = BiliLoginApi.sms_login(
            auth,
            phone,
            code,
            cid=COUNTRY_CODE,
            captcha_key=sent["data"]["captcha_key"],
        )
        if not success:
            raise RuntimeError(
                f"{message}：{json.dumps(response, ensure_ascii=False)[:400]}"
            )

        backup_session()
        path = auth.save_session()
        nav = BiliApi.get_nav(auth)
        account = nav.get("data") or {}
        print(
            f"手机号登录成功：{account.get('uname')} (mid={account.get('mid')})\n"
            f"会话已保存：{path}"
        )
        return 0
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"手机号登录失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
