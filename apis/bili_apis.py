"""主站只读接口（搜索 / 稿件 / 播放 / 弹幕 / 评论 / 用户 / 推荐）.

约定（对齐 ../DouYin_Spider）：
- 所有方法为 @staticmethod，首参 auth: BiliAuth；
- 数据接口直接返回 JSON dict，编排类方法返回 (success, msg, data)。
"""

import json
import random
import urllib.parse

from builder.header import HeaderBuilder, HeaderType
from builder.params import Params
from utils.bv import bv2av
from utils.common_util import now_ts, random_hex
from utils.device import gen_qv_id
from utils.fingerprint import get_profile
from utils.http_util import get_bytes, get_json, post_json

# 视频页埋点位，实抓 spmid/from_spmid 都是这个值
VIDEO_SPMID = '333.788.0.0'
# 播放上报用的四键 statistics，与弹幕接口同款（互动接口是两键版）
STATISTICS_FULL = '{"appId":100,"platform":5,"abtest":"","version":""}'


class BiliApi:
    api = 'https://api.bilibili.com'
    main = 'https://www.bilibili.com'

    # ------------------------------------------------------------------ 账号

    @staticmethod
    def get_nav(auth) -> dict:
        """导航栏信息，用于判断登录态与取 WBI 密钥.

        实抓（2026-08-16，reqid=82）`accept` 是 `*/*` 而非 axios 那串——
        nav 是被另一层（非 axios）拉的，照抄。

        :param auth: BiliAuth object.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET, accept='*/*').get()
        return get_json(auth, f'{BiliApi.api}/x/web-interface/nav', headers=headers)

    # ------------------------------------------------------------------ 搜索

    @staticmethod
    def search_type(auth, keyword: str, order: str = 'totalrank', page: int = 1,
                    search_type: str = 'video') -> dict:
        """分类搜索.

        :param auth: BiliAuth object.
        :param keyword: 搜索关键词.
        :param order: 排序，totalrank 综合 / click 播放多 / pubdate 新发布 / dm 弹幕多 / stow 收藏多.
        :param page: 页码，从 1 开始.
        :param search_type: 类型，video / media_bangumi / live / article / bili_user.
        :return: JSON.
        """
        headers = HeaderBuilder.build(
            HeaderType.GET, HeaderBuilder.search_origin).set_referer(
            f'https://search.bilibili.com/all?keyword={urllib.parse.quote(keyword)}').get()
        params = Params({
            '__refresh__': 'true',
            '_extra': '',
            'ad_resource': '5654',
            'category_id': '',
            'context': '',
            'dynamic_offset': (page - 1) * 24,
            'from_source': '',
            'from_spmid': '333.337',
            'gaia_vtoken': '',
            'highlight': 1,
            'keyword': keyword,
            'order': order,
            'page': page,
            'page_size': 42,
            'platform': 'pc',
            'qv_id': gen_qv_id(),
            'search_type': search_type,
            'single_column': 0,
            'source_tag': 3,
        }).with_web_location('1430654').with_wbi(auth)
        return get_json(auth, f'{BiliApi.api}/x/web-interface/wbi/search/type',
                        headers=headers, params=params.get())

    @staticmethod
    def search_by_num(auth, keyword: str, num: int, order: str = 'totalrank',
                      search_type: str = 'video') -> tuple:
        """按数量翻页搜索.

        :param auth: BiliAuth object.
        :param keyword: 搜索关键词.
        :param num: 期望条数.
        :param order: 排序方式.
        :param search_type: 搜索类型.
        :return: (success, msg, work_list).
        """
        success, msg, work_list = True, '成功', []
        try:
            page = 1
            while len(work_list) < num:
                res_json = BiliApi.search_type(auth, keyword, order, page, search_type)
                if res_json.get('code') != 0:
                    raise RuntimeError(f"搜索失败 code={res_json.get('code')} "
                                       f"message={res_json.get('message')}")
                data = res_json.get('data') or {}
                results = data.get('result') or []
                if not results:
                    break
                # video 搜索会混入课程/广告卡片；有些甚至伪装了 bvid，但 type
                # 是 video_ad_* 且标题/链接为空，不能当普通作品返回。
                # 先过滤再判断数量，当前页不足时继续翻页补齐调用方要求的条数。
                if search_type == 'video':
                    results = [
                        item for item in results
                        if item.get('type') == 'video' and item.get('bvid')
                    ]
                work_list.extend(results)
                if page >= int(data.get('numPages') or 0):
                    break
                page += 1
            work_list = work_list[:num]
        except Exception as e:
            success, msg = False, str(e)
        return success, msg, work_list

    # ------------------------------------------------------------------ 稿件

    @staticmethod
    def get_video_info(auth, bvid: str = '', aid=None) -> dict:
        """稿件基础信息（标题 / UP / 分P / 统计）.

        :param auth: BiliAuth object.
        :param bvid: BV 号，与 aid 二选一.
        :param aid: av 号.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(
            f'{BiliApi.main}/video/{bvid or ""}').get()
        params = Params({'bvid': bvid} if bvid else {'aid': aid}).with_wbi(auth)
        return get_json(auth, f'{BiliApi.api}/x/web-interface/wbi/view',
                        headers=headers, params=params.get())

    @staticmethod
    def get_video_detail(auth, bvid: str = '', aid=None, page: int = 1) -> dict:
        """稿件完整信息（含相关推荐、UP 主统计等）.

        实抓参数序：`aid, p, isGaiaAvoided, platform, web_location, w_rid, wts`。
        早先只发了 bvid + 签名，缺 4 个字段。

        :param auth: BiliAuth object.
        :param bvid: BV 号，与 aid 二选一.
        :param aid: av 号.
        :param page: 分 P 序号，从 1 开始.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(
            f'{BiliApi.main}/video/{bvid or ""}').get()
        params = Params({
            'aid': aid if aid else bv2av(bvid), 'p': page,
            'isGaiaAvoided': 'false', 'platform': 'web',
        }).with_web_location('1315873').with_wbi(auth)
        return get_json(auth, f'{BiliApi.api}/x/web-interface/wbi/view/detail',
                        headers=headers, params=params.get())

    @staticmethod
    def get_player_info(auth, aid, cid) -> dict:
        """播放器信息（字幕 / 互动 / 进度），带 dm_img 指纹.

        :param auth: BiliAuth object.
        :param aid: av 号.
        :param cid: 分 P 的 cid.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(f'{BiliApi.main}/').get()
        params = (Params({'aid': aid, 'cid': cid, 'isGaiaAvoided': 'false'})
                  .with_web_location('1315873').with_dm_img().with_wbi(auth))
        return get_json(auth, f'{BiliApi.api}/x/player/wbi/v2',
                        headers=headers, params=params.get())

    @staticmethod
    def get_play_url(auth, bvid: str, cid, qn: int = 80, fnval: int = 4048,
                     gaia_source: str = '', is_main_page: bool = True) -> dict:
        """取播放地址.

        参数**以浏览器 Network 抓包为准**（2026-08-16，在播放列表页
        `bilibili.com/list/ml<mid>` 抓到的主站版请求，24 个参数）：

            avid, bvid, cid, qn, fnver, fnval, fourk, gaia_source, from_client,
            is_main_page, need_fragment, isGaiaAvoided, client_attr,
            version_name, app_id, session, voice_balance, web_location,
            dm_img_list, dm_img_str, dm_cover_img_str, dm_img_inter, w_rid, wts

        教训：先前只照播放器源码的 `getPlayUrlParams()` 实现，结果**少了 5 项**——
        `client_attr` / `version_name` / `app_id` / `voice_balance` 与整组
        `dm_img_*` 都是别的层追加的，源码里那个对象看不到。所以只认抓包。

        `gaia_source` 按页面类型变：主站页与播放列表是**空串**，
        嵌入式外链是 `external-link`（此时 `is_main_page=false` 且不带 dm_img）。

        :param auth: BiliAuth object.

        :param auth: BiliAuth object.
        :param bvid: BV 号.
        :param cid: 分 P 的 cid.
        :param qn: 清晰度，16=360P 32=480P 64=720P 80=1080P，更高需登录/大会员.
        :param fnval: 流格式标志，4048 为 DASH 全量.
        :param gaia_source: 页面来源，缺省空串即主站视频页.
        :param is_main_page: 是否主站页面.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(
            f'{BiliApi.main}/video/{bvid}').get()
        params = Params({
            'avid': bv2av(bvid), 'bvid': bvid, 'cid': cid, 'qn': qn,
            'fnver': 0, 'fnval': fnval, 'fourk': 1,
            'gaia_source': gaia_source, 'from_client': 'BROWSER',
            'is_main_page': str(is_main_page).lower(),
            'need_fragment': 'false', 'isGaiaAvoided': 'false',
            'session': random_hex(32),
        }).with_web_location('1315873').with_wbi(auth)
        return get_json(auth, f'{BiliApi.api}/x/player/wbi/playurl',
                        headers=headers, params=params.get())

    # ------------------------------------------------------------------ 弹幕

    @staticmethod
    def get_danmaku_seg(auth, aid, cid, segment_index: int = 1) -> bytes:
        """取一段弹幕（每段 6 分钟），返回 protobuf 原始字节.

        :param auth: BiliAuth object.
        :param aid: av 号，作为 pid.
        :param cid: 分 P 的 cid，作为 oid.
        :param segment_index: 分段序号，从 1 开始.
        :return: protobuf bytes.
        """
        # 二进制接口：播放器用裸 XHR 拉 protobuf，accept 是 */* 而非 axios 的那串
        headers = HeaderBuilder.build(HeaderType.GET, accept='*/*').set_referer(
            f'{BiliApi.main}/').get()
        params = Params({
            'type': 1, 'oid': cid, 'pid': aid, 'segment_index': segment_index,
            'pull_mode': 1, 'ps': 0, 'pe': 120000,
        }).with_web_location('1315873').with_wbi(auth)
        return get_bytes(auth, f'{BiliApi.api}/x/v2/dm/wbi/web/seg.so',
                         headers=headers, params=params.get())

    @staticmethod
    def get_danmaku_view(auth, aid, cid, duration: int = 0) -> bytes:
        """弹幕元信息（总数 / 分段数 / 屏蔽词），protobuf 字节.

        :param auth: BiliAuth object.
        :param aid: av 号.
        :param cid: 分 P 的 cid.
        :param duration: 视频秒数，浏览器会带上；取不到时传 0.
        :return: protobuf bytes.
        """
        headers = HeaderBuilder.build(HeaderType.GET, accept='*/*').set_referer(
            f'{BiliApi.main}/').get()
        params = {'type': 1, 'oid': cid, 'pid': aid, 'duration': duration,
                  'without_subtitle': 'true'}
        return get_bytes(auth, f'{BiliApi.api}/x/v2/dm/web/view',
                         headers=headers, params=params)

    # ------------------------------------------------------------------ 评论

    @staticmethod
    def get_replies(auth, oid, type_: int = 1, offset: str = '', mode: int = 3) -> dict:
        """取评论区.

        :param auth: BiliAuth object.
        :param oid: 目标 ID，视频传 av 号.
        :param type_: 评论区类型，1=视频 12=专栏 17=动态.
        :param offset: 翻页游标。首页传空串；下一页传上一页响应里的
            data.cursor.pagination_reply.next_offset。旧的页码参数 next 服务端已经不认
            （data.cursor.next 恒为 0），只靠它翻页会一直拿到首页。
            热门排序时 next_offset 可能连续几页不变（进度记在服务端的 session 里），
            照样把它传回去就是下一页，直到 data.cursor.is_end 为 true.
        :param mode: 排序，0/3=热门 2=时间.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(f'{BiliApi.main}/').get()
        params = Params({
            'oid': oid, 'type': type_, 'mode': mode,
            'pagination_str': json.dumps({'offset': offset}, separators=(',', ':')),
            'plat': 1, 'seek_rpid': '',
        })
        params.with_web_location('1315875').with_wbi(auth)
        return get_json(auth, f'{BiliApi.api}/x/v2/reply/wbi/main',
                        headers=headers, params=params.get())

    # ------------------------------------------------------------------ 用户

    @staticmethod
    def get_user_info(auth, mid) -> dict:
        """用户空间基础信息.

        空间类接口的风控门槛是 dm_img 指纹，不带就返回 -352 风控校验失败；
        旧版依赖的 w_webid 已被前端移除（2026-08-15 实抓确认）。

        :param auth: BiliAuth object.
        :param mid: 用户数字 ID.
        :return: JSON.
        """
        headers = HeaderBuilder.build(
            HeaderType.GET, HeaderBuilder.space_origin).set_referer(
            f'https://space.bilibili.com/{mid}').get()
        params = (Params({'mid': mid, 'token': '', 'platform': 'web'})
                  .with_web_location('1550101').with_dm_img().with_wbi(auth))
        return get_json(auth, f'{BiliApi.api}/x/space/wbi/acc/info',
                        headers=headers, params=params.get())

    @staticmethod
    def get_user_videos(auth, mid, page: int = 1, page_size: int = 42,
                        order: str = 'pubdate', keyword: str = '') -> dict:
        """用户投稿视频列表.

        这个接口的 IP 级限流很激进，短时间连打会返回 HTTP 412（浏览器同样中招），
        http_util 已对 412 退避重试，批量抓取仍建议放慢节奏。

        :param auth: BiliAuth object.
        :param mid: 用户数字 ID.
        :param page: 页码.
        :param page_size: 每页条数，浏览器用 42.
        :param order: pubdate 最新 / click 最多播放 / stow 最多收藏.
        :param keyword: 站内筛选关键词.
        :return: JSON.
        """
        headers = HeaderBuilder.build(
            HeaderType.GET, HeaderBuilder.space_origin).set_referer(
            f'https://space.bilibili.com/{mid}/video').get()
        params = (Params({
            'pn': page, 'ps': page_size, 'tid': 0, 'special_type': '', 'order': order,
            'mid': mid, 'index': 0, 'keyword': keyword, 'order_avoided': 'true',
            'platform': 'web',
        }).with_web_location('333.1387').with_dm_img().with_wbi(auth))
        return get_json(auth, f'{BiliApi.api}/x/space/wbi/arc/search',
                        headers=headers, params=params.get())

    @staticmethod
    def get_all_user_videos(auth, mid, order: str = 'pubdate') -> tuple:
        """翻页取用户全部投稿.

        :param auth: BiliAuth object.
        :param mid: 用户数字 ID.
        :param order: 排序方式.
        :return: (success, msg, work_list).
        """
        success, msg, work_list = True, '成功', []
        try:
            page = 1
            while True:
                res_json = BiliApi.get_user_videos(auth, mid, page=page, order=order)
                if res_json.get('code') != 0:
                    raise RuntimeError(f"拉取投稿失败 code={res_json.get('code')} "
                                       f"message={res_json.get('message')}")
                data = res_json.get('data') or {}
                videos = ((data.get('list') or {}).get('vlist')) or []
                if not videos:
                    break
                work_list.extend(videos)
                total = ((data.get('page') or {}).get('count')) or 0
                if len(work_list) >= total:
                    break
                page += 1
        except Exception as e:
            success, msg = False, str(e)
        return success, msg, work_list

    # ------------------------------------------------------------------ 推荐

    @staticmethod
    def get_rcmd_feed(auth, fresh_idx: int = 1, ps: int = 12,
                      last_showlist: str = '') -> dict:
        """首页推荐流.

        实抓参数序（2026-08-16，首页滚动触发）：`web_location` 排在**最前**，
        且**不带 `dm_img_*`**——早先这里画蛇添足加了那一组，浏览器根本不发。
        另外缺 `device=win` 与 `tt_exp`。

        :param auth: BiliAuth object.
        :param fresh_idx: 刷新序号，翻页时递增.
        :param ps: 每次条数.
        :param last_showlist: 上一批已展示的条目，形如 `av_123,ad_456_789`.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(f'{BiliApi.main}/').get()
        params = Params({
            'web_location': '1430650', 'y_num': 4, 'fresh_type': 4,
            'feed_version': 'V8', 'fresh_idx_1h': fresh_idx, 'fetch_row': 4,
            'fresh_idx': fresh_idx, 'brush': fresh_idx, 'device': 'win',
            'homepage_ver': 1, 'ps': ps, 'last_y_num': 5,
            'screen': get_profile()['browser_resolution'], 'seo_info': '',
            'tt_exp': '', 'last_showlist': last_showlist,
            'uniq_id': str(random.randint(10 ** 12, 10 ** 13 - 1)),
        }).with_wbi(auth)
        return get_json(auth, f'{BiliApi.api}/x/web-interface/wbi/index/top/feed/rcmd',
                        headers=headers, params=params.get())

    @staticmethod
    def get_popular(auth, page: int = 1, page_size: int = 20) -> dict:
        """热门榜.

        :param auth: BiliAuth object.
        :param page: 页码.
        :param page_size: 每页条数.
        :return: JSON.
        """
        # 实抓确认它**也走 WBI 签名**并带 web_location=333.934；
        # 早先只发了 ps/pn，既没签名也没埋点位。referer 是热门页而非主站首页。
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(
            f'{BiliApi.main}/v/popular/all/').get()
        params = (Params({'ps': page_size, 'pn': page})
                  .with_web_location('333.934').with_wbi(auth))
        return get_json(auth, f'{BiliApi.api}/x/web-interface/popular',
                        headers=headers, params=params.get())

    # -------------------------------------------------------------- 视频页附属

    @staticmethod
    def get_conclusion_judge(auth, bvid: str, cid, up_mid) -> dict:
        """AI 总结是否可用（WBI 签名）.

        实抓（reqid=125）：`bvid, cid, up_mid, web_location=333.788, w_rid, wts`，
        且 `accept` 是 `*/*`。

        :param auth: BiliAuth object.
        :param bvid: BV 号.
        :param cid: 分 P 的 cid.
        :param up_mid: UP 主 mid.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET, accept='*/*').set_referer(
            f'{BiliApi.main}/video/{bvid}/').get()
        params = (Params({'bvid': bvid, 'cid': cid, 'up_mid': up_mid})
                  .with_web_location('333.788').with_wbi(auth))
        return get_json(auth, f'{BiliApi.api}/x/web-interface/view/conclusion/judge',
                        headers=headers, params=params.get())

    @staticmethod
    def get_archive_relation(auth, bvid: str, aid=None) -> dict:
        """当前账号与该稿件的关系（是否已赞/投币/收藏/关注）.

        实抓（reqid=126）：`aid, bvid`，无签名。需登录态才有意义。

        :param auth: BiliAuth object.
        :param bvid: BV 号.
        :param aid: av 号，缺省由 bvid 换算.
        :return: JSON，data.like / coin / favorite / attention.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(
            f'{BiliApi.main}/video/{bvid}/').get()
        params = {'aid': aid if aid else bv2av(bvid), 'bvid': bvid}
        return get_json(auth, f'{BiliApi.api}/x/web-interface/archive/relation',
                        headers=headers, params=params)

    @staticmethod
    def get_view_cards(auth, bvid: str, cid, aid=None) -> dict:
        """稿件关联的卡片（UP 主 / 相关推荐等）.

        实抓（reqid=129）：`bvid, aid, cid, platform=web`.

        :param auth: BiliAuth object.
        :param bvid: BV 号.
        :param cid: 分 P 的 cid.
        :param aid: av 号，缺省由 bvid 换算.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(
            f'{BiliApi.main}/video/{bvid}/').get()
        params = {'bvid': bvid, 'aid': aid if aid else bv2av(bvid),
                  'cid': cid, 'platform': 'web'}
        return get_json(auth, f'{BiliApi.api}/x/web-interface/view/cards',
                        headers=headers, params=params)

    @staticmethod
    def get_relation(auth, mid) -> dict:
        """当前账号与某个 UP 主的关注关系.

        实抓（reqid=130）：只有 `mid`.

        :param auth: BiliAuth object.
        :param mid: UP 主 mid.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(f'{BiliApi.main}/').get()
        return get_json(auth, f'{BiliApi.api}/x/web-interface/relation',
                        headers=headers, params={'mid': mid})

    @staticmethod
    def get_uplikeimg(auth, aid, vmid) -> dict:
        """UP 主自定义的"一键三连"图.

        实抓（reqid=127）：`aid, vmid`.

        :param auth: BiliAuth object.
        :param aid: av 号.
        :param vmid: UP 主 mid.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(f'{BiliApi.main}/').get()
        return get_json(auth, f'{BiliApi.api}/x/web-interface/view/uplikeimg',
                        headers=headers, params={'aid': aid, 'vmid': vmid})

    @staticmethod
    def get_note_forbid(auth, aid) -> dict:
        """该稿件是否禁止笔记.

        实抓（reqid=128）：只有 `aid`.

        :param auth: BiliAuth object.
        :param aid: av 号.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(f'{BiliApi.main}/').get()
        return get_json(auth, f'{BiliApi.api}/x/note/is_forbid',
                        headers=headers, params={'aid': aid})

    @staticmethod
    def get_subtitle_view(auth, aid, cid, video_type: int = 1) -> bytes:
        """字幕列表（protobuf 字节）.

        实抓（reqid=122）：`oid, pid, context_ext, type, cur_production_type,
        playlist_switch`。响应是 protobuf，不是 JSON。

        :param auth: BiliAuth object.
        :param aid: av 号，作为 pid.
        :param cid: 分 P 的 cid，作为 oid.
        :param video_type: context_ext 里的 video_type，普通稿件为 1.
        :return: protobuf bytes.
        """
        headers = HeaderBuilder.build(HeaderType.GET, accept='*/*').set_referer(
            f'{BiliApi.main}/').get()
        params = {
            'oid': cid, 'pid': aid,
            'context_ext': json.dumps({'video_type': video_type}, separators=(',', ':')),
            'type': 1, 'cur_production_type': 0, 'playlist_switch': 0,
        }
        return get_bytes(auth, f'{BiliApi.api}/x/v2/subtitle/web/view',
                         headers=headers, params=params)

    @staticmethod
    def get_broadcast_servers(auth) -> dict:
        """站内长连（broadcast）的接入点.

        实抓（reqid=168）：`platform=web`.

        :param auth: BiliAuth object.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(f'{BiliApi.main}/').get()
        return get_json(auth, f'{BiliApi.api}/x/web-interface/broadcast/servers',
                        headers=headers, params={'platform': 'web'})

    @staticmethod
    def get_click_now(auth) -> dict:
        """服务端当前时间戳，播放上报用它对齐 ftime/stime.

        实抓（reqid=123/124）：无任何参数.

        :param auth: BiliAuth object.
        :return: JSON，data.now 为秒级时间戳.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(f'{BiliApi.main}/').get()
        return get_json(auth, f'{BiliApi.api}/x/click-interface/click/now',
                        headers=headers)

    @staticmethod
    def get_online_total(auth, aid, cid, bvid: str = '') -> dict:
        """当前在线观看人数.

        实抓（视频页轮询）：`aid, cid, bvid, ts`，`ts` 是秒级时间戳。

        :param auth: BiliAuth object.
        :param aid: av 号.
        :param cid: 分 P 的 cid.
        :param bvid: BV 号.
        :return: JSON，data.total / data.count.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(
            f'{BiliApi.main}/video/{bvid}/' if bvid else f'{BiliApi.main}/').get()
        params = {'aid': aid, 'cid': cid, 'bvid': bvid, 'ts': now_ts()}
        return get_json(auth, f'{BiliApi.api}/x/player/online/total',
                        headers=headers, params=params)

    @staticmethod
    def get_reply_subject(auth, oid, type_: int = 1) -> dict:
        """评论区的置顶说明 / 主题描述.

        实抓：`oid, type, web_location`（无签名）.

        :param auth: BiliAuth object.
        :param oid: 目标 ID，视频传 av 号.
        :param type_: 评论区类型.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(f'{BiliApi.main}/').get()
        params = {'oid': oid, 'type': type_, 'web_location': '1315875'}
        return get_json(auth, f'{BiliApi.api}/x/v2/reply/subject/description',
                        headers=headers, params=params)

    @staticmethod
    def get_note_list(auth, oid, oid_type: int = 0) -> dict:
        """该稿件下我的笔记列表.

        实抓：`csrf, oid, oid_type`，**csrf 排在最前**（少见，别按习惯放末尾）.

        :param auth: BiliAuth object，需登录态.
        :param oid: 稿件 av 号.
        :param oid_type: 目标类型，视频为 0.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(f'{BiliApi.main}/').get()
        params = {'csrf': auth.csrf, 'oid': oid, 'oid_type': oid_type}
        return get_json(auth, f'{BiliApi.api}/x/note/list/archive',
                        headers=headers, params=params)

    @staticmethod
    def get_public_notes(auth, oid, uper_mid, oid_type: int = 0,
                         page: int = 1, page_size: int = 3) -> dict:
        """该稿件下已公开发布的笔记.

        实抓：`csrf, oid, oid_type, pn, ps, uper_mid`.

        :param auth: BiliAuth object.
        :param oid: 稿件 av 号.
        :param uper_mid: UP 主 mid.
        :param oid_type: 目标类型，视频为 0.
        :param page: 页码.
        :param page_size: 每页条数，实抓为 3.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(f'{BiliApi.main}/').get()
        params = {'csrf': auth.csrf, 'oid': oid, 'oid_type': oid_type,
                  'pn': page, 'ps': page_size, 'uper_mid': uper_mid}
        return get_json(auth, f'{BiliApi.api}/x/note/publish/list/archive',
                        headers=headers, params=params)

    @staticmethod
    def get_live_room_base_info(auth, uids, req_biz: str = 'video') -> dict:
        """按 mid 批量查直播间基础信息（视频页展示 UP 是否在播）.

        实抓（reqid=102）：`uids, req_biz`，走的是 `api.live.bilibili.com`.

        :param auth: BiliAuth object.
        :param uids: 单个 mid 或 mid 列表.
        :param req_biz: 调用场景，视频页为 video.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(f'{BiliApi.main}/').get()
        if not isinstance(uids, (list, tuple)):
            uids = [uids]
        params = [('uids', u) for u in uids] + [('req_biz', req_biz)]
        return get_json(auth,
                        'https://api.live.bilibili.com/xlive/web-room/v1/index/getRoomBaseInfo',
                        headers=headers, params=params)

    # ---------------------------------------------------------------- 播放上报

    @staticmethod
    def report_click(auth, aid, cid, bvid: str = '', part: int = 1, level: int = 0,
                     ftime: int = 0, stime: int = 0, session: str = '',
                     play_method: int = 2, play_volume: int = 1,
                     auto_play: int = 0) -> dict:
        """播放开始上报（`click/web/h5`）.

        实抓（reqid=142）。**query 走 WBI 签名，且签名的是 `w_` 前缀的那一份**，
        body 则是不带前缀的完整版——同一批值出现两次，前缀版进签名、
        无前缀版进 body，这是这条链路最容易搞错的地方。

        query 字段序：
            `w_aid, w_part, w_ftime, w_stime, w_type, web_location, w_rid, wts`

        body 字段序（22 个）：
            `mid, aid, cid, part, lv, ftime, stime, type, sub_type, refer_url,
            outer, statistics, mobi_app, device, platform, cur_language,
            perfer_type, play_mode, spmid, from_spmid, session, track_id,
            extra, csrf`

        :param auth: BiliAuth object，需登录态.
        :param aid: av 号.
        :param cid: 分 P 的 cid.
        :param bvid: BV 号，仅用于 referer.
        :param part: 分 P 序号.
        :param level: 账号等级，进 body 的 lv.
        :param ftime: 页面打开的秒级时间戳.
        :param stime: 开始播放的秒级时间戳.
        :param session: 本次播放会话 ID，32 位十六进制；留空自动生成.
        :param play_method: 起播方式，实抓为 2.
        :param play_volume: 音量.
        :param auto_play: 是否自动播放.
        :return: JSON.
        """
        now = now_ts()
        ftime = ftime or now
        stime = stime or now
        session = session or random_hex(32)
        headers = HeaderBuilder.build(HeaderType.FORM).set_referer(
            f'{BiliApi.main}/video/{bvid}/' if bvid else f'{BiliApi.main}/').get()
        params = (Params({
            'w_aid': aid, 'w_part': part, 'w_ftime': ftime,
            'w_stime': stime, 'w_type': 3,
        }).with_web_location('1315873').with_wbi(auth))
        data = {
            'mid': auth.mid, 'aid': aid, 'cid': cid, 'part': part, 'lv': level,
            'ftime': ftime, 'stime': stime, 'type': 3, 'sub_type': 0,
            'refer_url': '', 'outer': 0, 'statistics': STATISTICS_FULL,
            'mobi_app': 'web', 'device': 'web', 'platform': 'web',
            'cur_language': '', 'perfer_type': '', 'play_mode': 1,
            'spmid': VIDEO_SPMID, 'from_spmid': VIDEO_SPMID,
            'session': session, 'track_id': '',
            'extra': json.dumps({'play_method': play_method,
                                 'play_volume': play_volume,
                                 'auto_play': auto_play}, separators=(',', ':')),
            'csrf': auth.csrf,
        }
        return post_json(auth, f'{BiliApi.api}/x/click-interface/click/web/h5',
                         headers=headers, params=params.get(), data=data)

    @staticmethod
    def report_heartbeat(auth, aid, cid, bvid: str = '', played_time: int = 0,
                         video_duration: int = 0, start_ts: int = 0,
                         quality: int = 32, session: str = '',
                         player_version: str = '4.9.92', video_dye_id: str = '',
                         video_file_name: str = '', play_method: int = 2,
                         play_volume: int = 1, auto_play: int = 0) -> dict:
        """播放心跳上报（`web/heartbeat`）.

        实抓（reqid=133）。和 `report_click` 一样是"前缀版进签名、
        无前缀版进 body"的双份结构。

        query 字段序：
            `w_start_ts, w_mid, w_aid, w_dt, w_realtime, w_played_time,
            w_real_played_time, w_video_duration, w_last_play_progress_time,
            web_location, w_rid, wts`

        body 字段序（30 个）：
            `start_ts, mid, aid, cid, type, sub_type, dt, play_type, realtime,
            played_time, real_played_time, refer_url, quality, is_auto_qn,
            video_duration, last_play_progress_time, max_play_progress_time,
            outer, statistics, mobi_app, device, platform, cur_language_vt,
            perfer_type, play_mode, spmid, from_spmid, session, track_id,
            extra, csrf`

        :param auth: BiliAuth object，需登录态.
        :param aid: av 号.
        :param cid: 分 P 的 cid.
        :param bvid: BV 号，仅用于 referer.
        :param played_time: 已播放秒数.
        :param video_duration: 视频总秒数.
        :param start_ts: 起播的秒级时间戳；留空取当前.
        :param quality: 当前清晰度，实抓为 32.
        :param session: 播放会话 ID，需与 report_click 用同一个.
        :param player_version: 播放器版本，进 extra.
        :param video_dye_id: 播放染色 ID，进 extra.
        :param video_file_name: 当前分片文件名，进 extra.
        :param play_method: 起播方式.
        :param play_volume: 音量.
        :param auto_play: 是否自动播放.
        :return: JSON.
        """
        start_ts = start_ts or now_ts()
        session = session or random_hex(32)
        headers = HeaderBuilder.build(HeaderType.FORM).set_referer(
            f'{BiliApi.main}/video/{bvid}/' if bvid else f'{BiliApi.main}/').get()
        params = (Params({
            'w_start_ts': start_ts, 'w_mid': auth.mid, 'w_aid': aid, 'w_dt': 2,
            'w_realtime': played_time, 'w_played_time': played_time,
            'w_real_played_time': played_time,
            'w_video_duration': video_duration,
            'w_last_play_progress_time': played_time,
        }).with_web_location('1315873').with_wbi(auth))
        extra = {'player_version': player_version}
        if video_dye_id:
            extra['video_dye_id'] = video_dye_id
        if video_file_name:
            extra['video_file_name'] = video_file_name
        extra.update({'play_method': play_method, 'play_volume': play_volume,
                      'auto_play': auto_play})
        data = {
            'start_ts': start_ts, 'mid': auth.mid, 'aid': aid, 'cid': cid,
            'type': 3, 'sub_type': 0, 'dt': 2, 'play_type': 1,
            'realtime': played_time, 'played_time': played_time,
            'real_played_time': played_time, 'refer_url': '',
            'quality': quality, 'is_auto_qn': 0,
            'video_duration': video_duration,
            'last_play_progress_time': played_time,
            'max_play_progress_time': played_time, 'outer': 0,
            'statistics': STATISTICS_FULL, 'mobi_app': 'web', 'device': 'web',
            'platform': 'web', 'cur_language_vt': '{}', 'perfer_type': '{}',
            'play_mode': 1, 'spmid': VIDEO_SPMID, 'from_spmid': VIDEO_SPMID,
            'session': session, 'track_id': '',
            'extra': json.dumps(extra, separators=(',', ':')),
            'csrf': auth.csrf,
        }
        return post_json(auth, f'{BiliApi.api}/x/click-interface/web/heartbeat',
                         headers=headers, params=params.get(), data=data)
