"""互动接口（点赞 / 投币 / 收藏 / 三连 / 评论 / 视频弹幕）.

全部是写操作，必须登录态：Cookie 里要有 SESSDATA，表单里要带 bili_jct 作为 csrf。

**body 字段与顺序照抄浏览器**（2026-08-16 从 video bundle 的源码里读出，
并用实抓 body 复核）。早先这里只发了最小可用字段集，能成功但形态和浏览器差很多：
点赞实抓是 10 个字段，我们只发了 3 个，而且用的是 `bvid` 而非 `aid`。

播放器相关的四个字段来自源码里的 `ec()`：

    function ec() {
      return window.player ? {
        eab_x: window.player.isPaused() ? 2 : 1,
        ramval: window.player.getMediaInfo().absolutePlayTime,
        source: "web_normal", ga: 1
      } : {};
    }

即 `eab_x` 暂停为 2、播放中为 1，`ramval` 是已播放的绝对秒数。
点赞 / 投币 / 三连 都经过同一个包装器所以都带这四个；收藏走的是另一条
请求链路，**不带**。
"""

import itertools

from builder.header import HeaderBuilder, HeaderType
from builder.params import Params
from utils.bv import bv2av
from utils.http_util import get_json, post_json

# 视频页的埋点前缀，spmid 形如 "333.788.0.0"
VIDEO_SPMID_PREFIX = '333.788'
# 源码里是 JSON.stringify({appId:100, platform:5})，键序固定
STATISTICS = '{"appId":100,"platform":5}'
# 弹幕接口用的是四键版本，比互动接口多 abtest / version（实抓两者都是空串）
STATISTICS_DM = '{"appId":100,"platform":5,"abtest":"","version":""}'

# 弹幕的 rnd 实抓是个很小的整数（观测到 2），像是本次会话内的发送序号，
# 不是常说的大随机数。这里按会话内自增来模拟。
_dm_seq = itertools.count(1)


def _player_fields(paused: bool = False, played: int = 0) -> dict:
    """浏览器在页面有播放器时附带的四个字段（源码中的 `ec()`）.

    :param paused: 播放器是否处于暂停态.
    :param played: 已播放的绝对秒数.
    """
    return {'eab_x': 2 if paused else 1, 'ramval': played,
            'source': 'web_normal', 'ga': 1}


def _spmid(prefix: str = VIDEO_SPMID_PREFIX) -> str:
    return f'{prefix}.0.0'


class BiliInteractApi:
    api = 'https://api.bilibili.com'
    main = 'https://www.bilibili.com'

    @staticmethod
    def like(auth, bvid: str, like: bool = True, played: int = 0,
             paused: bool = False) -> dict:
        """点赞 / 取消点赞.

        字段与顺序照抄浏览器：`aid, like, from_spmid, spmid, statistics,
        eab_x, ramval, source, ga, csrf`。注意用的是 **aid** 而不是 bvid。

        :param auth: BiliAuth object，需登录态.
        :param bvid: BV 号（内部换算成 aid 发送）.
        :param like: True 点赞，False 取消.
        :param played: 已播放秒数，进 ramval.
        :param paused: 播放器是否暂停，进 eab_x.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.FORM).set_referer(
            f'{BiliInteractApi.main}/video/{bvid}').get()
        data = {
            'aid': bv2av(bvid), 'like': 1 if like else 2,
            'from_spmid': '', 'spmid': _spmid(), 'statistics': STATISTICS,
            **_player_fields(paused, played), 'csrf': auth.csrf,
        }
        return post_json(auth, f'{BiliInteractApi.api}/x/web-interface/archive/like',
                         headers=headers, data=data)

    @staticmethod
    def add_coin(auth, bvid: str, num: int = 1, also_like: bool = False,
                 played: int = 0, paused: bool = False) -> dict:
        """投币.

        字段序照抄浏览器：`aid, multiply, select_like, cross_domain,
        from_spmid, spmid, statistics, eab_x, ramval, source, ga, csrf`。

        :param auth: BiliAuth object，需登录态.
        :param bvid: BV 号（内部换算成 aid 发送）.
        :param num: 硬币数，1 或 2.
        :param also_like: 是否同时点赞.
        :param played: 已播放秒数.
        :param paused: 播放器是否暂停.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.FORM).set_referer(
            f'{BiliInteractApi.main}/video/{bvid}').get()
        data = {
            'aid': bv2av(bvid), 'multiply': num,
            'select_like': 1 if also_like else 0, 'cross_domain': 'true',
            'from_spmid': '', 'spmid': _spmid(), 'statistics': STATISTICS,
            **_player_fields(paused, played), 'csrf': auth.csrf,
        }
        return post_json(auth, f'{BiliInteractApi.api}/x/web-interface/coin/add',
                         headers=headers, data=data)

    @staticmethod
    def get_fav_folders(auth, mid=None) -> dict:
        """我创建的收藏夹列表，收藏时需要其中的 media_id.

        :param auth: BiliAuth object，需登录态.
        :param mid: 用户数字 ID，默认取当前登录账号.
        :return: JSON，data.list[].id 即 media_id.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(f'{BiliInteractApi.main}/').get()
        return get_json(auth, f'{BiliInteractApi.api}/x/v3/fav/folder/created/list-all',
                        headers=headers,
                        params={'up_mid': mid or auth.mid, 'web_location': '333.999'})

    @staticmethod
    def favour(auth, aid, add_media_ids='', del_media_ids='') -> dict:
        """收藏 / 取消收藏.

        `add_media_ids` 留空会返回 2001000 参数错误，所以缺省时自动取默认收藏夹。

        :param auth: BiliAuth object，需登录态.
        :param aid: av 号.
        :param add_media_ids: 要加入的收藏夹 ID，逗号分隔；留空则用默认收藏夹.
        :param del_media_ids: 要移出的收藏夹 ID，逗号分隔.
        :return: JSON.
        """
        if not add_media_ids and not del_media_ids:
            folders = ((BiliInteractApi.get_fav_folders(auth).get('data') or {}).get('list')) or []
            if not folders:
                raise RuntimeError('没有可用的收藏夹')
            add_media_ids = str(folders[0]['id'])
        headers = HeaderBuilder.build(HeaderType.FORM).set_referer(f'{BiliInteractApi.main}/').get()
        # 源码里 add / del 是互斥的：只放实际用到的那一个键，不会两个都发。
        # 字段序：rid, type, <add|del>, platform, from_spmid, spmid, statistics
        data = {'rid': aid, 'type': 2}
        if add_media_ids:
            data['add_media_ids'] = add_media_ids
        if del_media_ids:
            data['del_media_ids'] = del_media_ids
        data.update({'platform': 'web', 'from_spmid': '', 'spmid': _spmid(),
                     'statistics': STATISTICS, 'csrf': auth.csrf})
        return post_json(auth, f'{BiliInteractApi.api}/x/v3/fav/resource/deal',
                         headers=headers, data=data)

    @staticmethod
    def triple(auth, bvid: str, played: int = 0, paused: bool = False) -> dict:
        """一键三连（点赞 + 投币 + 收藏）.

        字段序照抄浏览器：`aid, from_spmid, spmid, statistics,
        eab_x, ramval, source, ga, csrf`。

        :param auth: BiliAuth object，需登录态.
        :param bvid: BV 号（内部换算成 aid 发送）.
        :param played: 已播放秒数.
        :param paused: 播放器是否暂停.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.FORM).set_referer(
            f'{BiliInteractApi.main}/video/{bvid}').get()
        data = {
            'aid': bv2av(bvid), 'from_spmid': '', 'spmid': _spmid(),
            'statistics': STATISTICS,
            **_player_fields(paused, played), 'csrf': auth.csrf,
        }
        return post_json(auth, f'{BiliInteractApi.api}/x/web-interface/archive/like/triple',
                         headers=headers, data=data)

    @staticmethod
    def add_reply(auth, oid, message: str, type_: int = 1, root: int = 0,
                  parent: int = 0) -> dict:
        """发评论 / 回复.

        实抓形态（2026-08-16）比想象的复杂：**query 走 WBI 签名并带整套
        `dm_img_*` 指纹**，body 才是评论内容。早先这里只发了个裸 body，
        既没签名也少字段，还多发了一个浏览器根本不存在的 `ordering`。

        body 字段与顺序照抄实抓：
        `plat, oid, type, message, at_name_to_mid, gaia_source, csrf, statistics`

        ⚠️ 浏览器还会在 query 末尾追加 `b_wet=<密文>.v1`，那是另一套
        SecureCollectSDK 的风险环境令牌（源码里的 `RISK_ENV_TOKEN`），
        本项目没有复现；实测不带它评论照样发得出去。

        :param auth: BiliAuth object，需登录态.
        :param oid: 目标 ID，视频传 av 号.
        :param message: 评论内容.
        :param type_: 评论区类型，1=视频 12=专栏 17=动态.
        :param root: 根评论 rpid，回复楼中楼时传.
        :param parent: 父评论 rpid，回复楼中楼时传.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.FORM).set_referer(f'{BiliInteractApi.main}/').get()
        params = Params().with_dm_img().with_wbi(auth)
        data = {'plat': 1, 'oid': oid, 'type': type_, 'message': message,
                'at_name_to_mid': '{}'}
        if root:
            data.update({'root': root, 'parent': parent or root})
        data.update({'gaia_source': 'main_web', 'csrf': auth.csrf,
                     'statistics': STATISTICS})
        return post_json(auth, f'{BiliInteractApi.api}/x/v2/reply/add',
                         headers=headers, params=params.get(), data=data)

    @staticmethod
    def delete_reply(auth, oid, rpid, type_: int = 1) -> dict:
        """删除自己发的评论.

        :param auth: BiliAuth object，需登录态.
        :param oid: 目标 ID，视频传 av 号.
        :param rpid: 评论 ID，来自 add_reply 的 data.rpid.
        :param type_: 评论区类型，1=视频 12=专栏 17=动态.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.FORM).set_referer(f'{BiliInteractApi.main}/').get()
        data = {'oid': oid, 'type': type_, 'rpid': rpid, 'csrf': auth.csrf}
        return post_json(auth, f'{BiliInteractApi.api}/x/v2/reply/del',
                         headers=headers, data=data)

    @staticmethod
    def send_danmaku(auth, aid, cid, message: str, progress: int = 0,
                     color: int = 16777215, fontsize: int = 25, mode: int = 1) -> dict:
        """给视频发弹幕.

        实抓形态（2026-08-16，在自己的稿件上真发一条抓下来的）：
        **query 走 WBI 签名**，带 `web_location=1315873`、`csrf` 与整套
        `dm_img_*`；body 有 20 个字段，顺序如下——

            color, fontsize, pool, mode, type, oid, msg, aid, progress,
            rnd, plat, checkbox_type, colorful, gaiasource,
            polaris_app_id, polaris_platform, spmid, from_spmid,
            statistics, csrf

        几个容易搞错的点：`oid` 是 **cid** 不是 aid；`csrf` 在 query 和 body
        里各出现一次；`gaiasource` **没有下划线**（评论那边是 `gaia_source`）；
        `statistics` 用的是四键版本，比互动接口多 `abtest` 与 `version`。

        :param auth: BiliAuth object，需登录态.
        :param aid: av 号.
        :param cid: 分 P 的 cid（作为 oid 发送）.
        :param message: 弹幕内容.
        :param progress: 出现时间，毫秒.
        :param color: 十进制颜色.
        :param fontsize: 字号.
        :param mode: 1 滚动 4 底部 5 顶部.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.FORM).set_referer(f'{BiliInteractApi.main}/').get()
        params = (Params({'web_location': '1315873', 'csrf': auth.csrf})
                  .with_dm_img().with_wbi(auth))
        data = {
            'color': color, 'fontsize': fontsize, 'pool': 0, 'mode': mode,
            'type': 1, 'oid': cid, 'msg': message, 'aid': aid,
            'progress': progress, 'rnd': next(_dm_seq), 'plat': 1,
            'checkbox_type': 0, 'colorful': '', 'gaiasource': 'main_web',
            'polaris_app_id': 100, 'polaris_platform': 5,
            'spmid': _spmid(), 'from_spmid': _spmid(),
            'statistics': STATISTICS_DM, 'csrf': auth.csrf,
        }
        return post_json(auth, f'{BiliInteractApi.api}/x/v2/dm/post',
                         headers=headers, params=params.get(), data=data)
