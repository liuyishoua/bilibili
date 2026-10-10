<div align="center">
  <p align="center">
    <a href="https://github.com/cv-cat/BilibiliApis" target="_blank">
      <img width="220" src="./author/logo.png" alt="BilibiliApis logo">
    </a>
  </p>

  <a href="https://www.python.org/">
    <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+">
  </a>
  <a href="https://github.com/lexiforest/curl_cffi">
    <img src="https://img.shields.io/badge/curl__cffi-0.15%2B-orange" alt="curl_cffi 0.15+">
  </a>
  <img src="https://img.shields.io/badge/Geetest_v3-pure_python-success" alt="Geetest v3 pure Python">
  <img src="https://img.shields.io/badge/browser-not_required-brightgreen" alt="Browser not required">

  # 🎬 BilibiliApis
</div>

**✨ 面向开发者的 B站 Web API 工具箱，支持内容获取、账号登录、视频投稿、互动操作与直播长连。**

大模型时代，自动化程序不仅要能“看见”内容，还要能稳定地登录、上传、互动和监听实时消息。
浏览器自动化能完成这些事，但资源占用高、运行环境重，也不适合长期部署。

BilibiliApis 把常用的 B站 Web 能力封装成 Python 接口：核心签名、设备参数和极验 v3
均在本地计算，常规流程不需要启动浏览器，也不需要运行前端页面。

> [!WARNING]
> 本项目仅供学习与技术研究。请遵守 B站 用户协议和相关法律法规，不要用于批量骚扰、隐私爬取、违规发布或商业滥用。

## 🌟 功能特性

- 🔍 **内容获取**
  - 关键词搜索与自动翻页
  - 视频详情、播放地址、评论与弹幕
  - 用户资料、用户投稿、推荐与热门内容
- 🔐 **账号登录**
  - 二维码登录
  - 手机号短信登录
  - 极验 v3 `fullpage → click` 纯 Python 验证
  - Cookie 与 `refresh_token` 持久化、到期自动续期
- 🎬 **创作与投稿**
  - UPOS 视频分片上传
  - 自定义标题、分区、标签、简介与封面
  - 默认仅自己可见，提交后自动回查稿件状态
  - 图文动态、专栏草稿与专栏投稿
- 🤝 **互动操作**
  - 点赞、投币、收藏与一键三连
  - 发布、回复、删除评论
  - 发送视频弹幕
- 🎙️ **直播能力**
  - 房间信息、播放流、分区与礼物列表
  - WebSocket 弹幕、礼物等实时事件
  - 发送直播弹幕、赠送礼物、开播与下播
- 🚀 **运行稳定**
  - `curl_cffi` 模拟 Chrome TLS/HTTP2 请求特征
  - WBI、bili_ticket、设备指纹等参数自动维护
  - 风控与限流错误自动退避重试

## 🛠️ 快速开始

### ⛳ 运行环境

- Python 3.11+
- Windows / Linux / macOS

### 🎯 安装依赖

```bash
git clone https://github.com/cv-cat/BilibiliApis.git
cd BilibiliApis
pip install -r requirements.txt
```

### 🚀 运行第一个示例

```bash
python main.py
```

默认运行 `view` 示例，只读取一个公开视频，不需要登录，也不会修改任何账号数据。

打开 `main.py`，修改顶部的 `DEMO` 即可切换功能：

| `DEMO` | 功能 | 是否需要登录 |
|---|---|:---:|
| `view` | 获取视频详情 | 否 |
| `search` | 搜索视频 | 否 |
| `user` | 获取用户资料与投稿 | 否 |
| `live` | 获取直播间信息 | 否 |
| `danmaku` | 监听直播间消息 | 否 |
| `login` | 二维码登录并保存会话 | 是 |
| `archives` | 查看自己的稿件 | 是 |
| `types` | 查看投稿分区及 `tid` | 是 |

搜索关键词、视频 BV 号、用户 UID、直播间号等参数，也都集中在 `main.py` 顶部。

## 🔑 登录账号

### 方式一：二维码登录

把 `main.py` 顶部的配置改为：

```python
DEMO = "login"
```

然后运行：

```bash
python main.py
```

终端会输出二维码，同时在项目目录生成 `login_qrcode.png`。使用 B站 App 扫码并确认后，
会话会自动保存到 `session.json`。

### 方式二：手机号登录

```bash
python quick_sms_login.py
```

按提示输入手机号。程序会纯算极验、发送短信，再等待输入 6 位验证码；登录成功后自动保存会话。
默认不会调用浏览器。

如需脚本化调用，也可以使用分步命令：

```bash
python -m tools.sms_login start --tel "$BILIBILI_PHONE"
python -m tools.sms_login code --code "$BILIBILI_SMS_CODE"
```

> `session.json` 和 `cookies.txt` 都包含完整登录凭据，项目已通过 `.gitignore` 排除。
> 请勿提交、截图或发送给他人。

## 📤 发布视频

项目提供了单独的投稿入口：

```bash
python quick_publish.py
```

第一次运行前，修改 `quick_publish.py` 顶部配置：

```python
ENABLE_PUBLISH = False          # 先保持 False 预览配置
VIDEO_PATH = r"D:\media\demo.mp4"
TITLE = "我的视频"
TID = 21
TAG = "日常,记录"
DESC = "视频简介"
COVER_PATH = ""                # 可留空
PUBLIC = False                  # False=仅自己可见，True=公开
```

建议先保持 `ENABLE_PUBLISH = False` 运行一次，确认账号、文件、标题和可见性。
确认无误后改为 `True`，脚本会执行：

```text
恢复并续期会话 → 分片上传 → 提交稿件 → 创作中心回查
```

测试投稿建议始终保持 `PUBLIC = False`。该配置会向投稿接口提交 `is_only_self=1`，
稿件不会对外公开。需要查询分区编号时，将 `main.py` 的 `DEMO` 改为 `types`。

## 🧩 在代码中调用

无需登录的内容搜索：

```python
from apis.bili_apis import BiliApi
from builder.auth import BiliAuth

auth = BiliAuth.anonymous()
success, message, videos = BiliApi.search_by_num(
    auth,
    keyword="Python",
    num=20,
    order="totalrank",
)

if not success:
    raise RuntimeError(message)

for video in videos:
    print(video["bvid"], video["title"])
```

恢复已经保存的登录会话：

```python
from builder.auth import BiliAuth

auth = BiliAuth.from_session()
print(auth.mid, auth.is_login)
```

视频投稿：

```python
from apis.bili_creator_apis import BiliCreatorApi

success, message, response = BiliCreatorApi.post_video(
    auth,
    file_path=r"D:\media\demo.mp4",
    title="我的视频",
    tid=21,
    tag="日常,记录",
    desc="视频简介",
    private=True,
)

print(success, message, response)
```

## 🐳 Docker

```bash
docker build -t bilibili-apis .
docker run --rm bilibili-apis
```

需要登录态时，将本地会话以只读方式挂载进容器：

```bash
docker run --rm \
  -v "${PWD}/session.json:/app/session.json:ro" \
  bilibili-apis
```

## 📁 项目结构

```text
BilibiliApis/
├── apis/                    # B站 API 封装
│   ├── bili_apis.py         # 搜索、视频、评论、弹幕、用户等只读接口
│   ├── bili_login_apis.py   # 二维码、短信登录与 Cookie 续期
│   ├── bili_creator_apis.py # 视频、动态、专栏与稿件管理
│   ├── bili_interact_apis.py# 点赞、投币、收藏、评论与视频弹幕
│   └── bili_live_apis.py    # 直播 REST 接口
├── builder/                 # 鉴权、请求头与参数装配
├── live/server.py           # 直播 WebSocket 客户端
├── tools/
│   ├── geetest_solve.py     # 极验 v3 纯算链路
│   └── sms_login.py         # 分步短信登录入口
├── utils/
│   ├── geetest_w.py         # 极验 AES/RSA、w 参数与载荷
│   ├── session.py           # 会话保存与恢复
│   ├── upos.py              # 视频分片上传
│   └── wbi.py               # WBI 签名
├── main.py                  # 只读、登录与直播示例入口
├── quick_sms_login.py       # 手机号登录
└── quick_publish.py         # 视频投稿
```

## 🗝️ 注意事项

- 只读接口可以匿名使用；投稿、互动和账号管理必须登录。
- 短信登录仍需要用户输入手机收到的一次性验证码。
- 公开投稿、投币、送礼、开播等操作会产生真实账号影响，请在调用前确认参数。
- 批量请求请控制频率。遇到 `-352`、`-412`、`-799` 等风控或限流错误时不要高频重试。
- B站 接口可能随时调整；如果发现字段或端点变化，欢迎提交 Issue。

## 🍥 更新日志

| 日期 | 说明 |
|---|---|
| 26/09/13 | 完成极验 v3 纯算短信登录；验证视频私密投稿与回查链路 |
| 26/08/16 | 完善 WBI、设备参数、会话续期、创作、互动与直播接口 |
| 26/04/10 | 项目初始化，完成视频搜索接口封装 |

## 🤝 欢迎贡献 PR

本项目欢迎功能补充、Bug 修复与使用体验改进。

- Fork 本仓库并在新分支开发
- 保持现有 Python 代码风格与类型提示
- 提交前运行 `python -m compileall .`
- PR 中说明改动目的、验证方式和可能影响
- 也欢迎通过 [Issue](https://github.com/cv-cat/BilibiliApis/issues) 提交建议

## 🧸 额外说明

1. 感谢 star ⭐ 和 follow 📰，项目会持续适配更新
2. 作者联系方式在 GitHub 主页，有问题可以联系
3. 欢迎关注作者的其他项目，也欢迎 PR 和 Issue

## 📈 Star 趋势

<a href="https://cvcat.site/star-history/svg?repos=cv-cat/BilibiliApis&type=Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://cvcat.site/star-history/svg?repos=cv-cat/BilibiliApis&type=Date&theme=dark">
    <source media="(prefers-color-scheme: light)" srcset="https://cvcat.site/star-history/svg?repos=cv-cat/BilibiliApis&type=Date">
    <img alt="BilibiliApis Star History" src="https://cvcat.site/star-history/svg?repos=cv-cat/BilibiliApis&type=Date">
  </picture>
</a>

## 🍔 交流群

如果你对爬虫和 AI Agent 感兴趣，可以加入群聊一起交流。

如群满或二维码过期，请通过 Issue、微信或 QQ 提醒更新。

| group-1 | group-2 | group-3 | group-4 (2000人qq群) |
|:--:|:--:|:--:|:--:|
| <img width="280" alt="group1" src="https://cvcat.site/assets/group1.jpg"> | <img width="280" alt="group2" src="https://cvcat.site/assets/group2.jpg"> | <img width="280" alt="group3" src="https://cvcat.site/assets/group3.jpg"> | <img width="280" alt="group4" src="https://cvcat.site/assets/group4.jpg"> |
