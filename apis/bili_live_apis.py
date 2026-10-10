"""直播接口（房间 / 流地址 / 弹幕长连入口 / 礼物 / 开播 / 发弹幕）.

看播侧全部匿名可用；开播与发弹幕需要登录态（SESSDATA + bili_jct）。
"""

import json

from builder.header import HeaderBuilder, HeaderType
from builder.params import Params
from utils.common_util import now_ms, now_ts
from utils.http_util import get_json, post_json, request


class BiliLiveApi:
    api = 'https://api.live.bilibili.com'
    live = 'https://live.bilibili.com'

    # ------------------------------------------------------------------ 看播

    @staticmethod
    def get_room_init(auth, room_id) -> dict:
        """短号转真实房间号，并给出开播状态.

        :param auth: BiliAuth object.
        :param room_id: 直播间短号或真实房间号.
        :return: JSON，data.room_id 为真实房间号，data.uid 为主播 mid.
        """
        headers = HeaderBuilder.build(HeaderType.GET, HeaderBuilder.live_origin).set_referer(
            f'{BiliLiveApi.live}/{room_id}').get()
        return get_json(auth, f'{BiliLiveApi.api}/room/v1/Room/room_init',
                        headers=headers, params={'id': room_id})

    @staticmethod
    def get_room_by_mid(auth, mid) -> dict:
        """按 UP 主 mid 查其直播间，没开通过直播则 roomStatus=0.

        开播 / 自测发弹幕都需要先拿到自己的房间号。

        :param auth: BiliAuth object.
        :param mid: 用户数字 ID.
        :return: JSON，data.roomid 为房间号，data.roomStatus 表示是否开通.
        """
        headers = HeaderBuilder.build(HeaderType.GET, HeaderBuilder.live_origin).get()
        return get_json(auth, f'{BiliLiveApi.api}/room/v1/Room/getRoomInfoOld',
                        headers=headers, params={'mid': mid})

    @staticmethod
    def get_room_info(auth, room_id) -> dict:
        """直播间详细信息（标题 / 分区 / 人气 / 封面）.

        :param auth: BiliAuth object.
        :param room_id: 真实房间号.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET, HeaderBuilder.live_origin).set_referer(
            f'{BiliLiveApi.live}/{room_id}').get()
        params = (Params({'room_id': room_id})
                  .with_web_location('444.8').with_wbi(auth))
        return get_json(auth, f'{BiliLiveApi.api}/xlive/web-room/v1/index/getInfoByRoom',
                        headers=headers, params=params.get())

    @staticmethod
    def get_room_play_info(auth, room_id, qn: int = 0) -> dict:
        """直播流地址（WBI 签名）.

        :param auth: BiliAuth object.
        :param room_id: 真实房间号.
        :param qn: 清晰度，0 自动，10000 原画，400 蓝光，250 超清，150 高清.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET, HeaderBuilder.live_origin).set_referer(
            f'{BiliLiveApi.live}/{room_id}').get()
        params = Params({
            'room_id': room_id, 'protocol': '0,1', 'format': '0,1,2', 'codec': '0,1,2',
            'qn': qn, 'platform': 'web', 'ptype': 8, 'dolby': 5, 'panorama': 1,
            'eotf': '0,1,2', 'supported_drms': '0,1,2,3', 'req_reason': 0,
        }).with_web_location('444.8').with_wbi(auth)
        return get_json(auth, f'{BiliLiveApi.api}/xlive/web-room/v2/index/getRoomPlayInfo',
                        headers=headers, params=params.get())

    @staticmethod
    def get_danmu_info(auth, room_id) -> dict:
        """弹幕长连入口（WBI 签名），返回 token 与 host_list.

        :param auth: BiliAuth object.
        :param room_id: 真实房间号.
        :return: JSON，data.token 与 data.host_list 用于建立 WebSocket.
        """
        headers = HeaderBuilder.build(HeaderType.GET, HeaderBuilder.live_origin).set_referer(
            f'{BiliLiveApi.live}/{room_id}').get()
        params = (Params({'id': room_id, 'type': 0})
                  .with_web_location('444.8').with_wbi(auth))
        return get_json(auth, f'{BiliLiveApi.api}/xlive/web-room/v1/index/getDanmuInfo',
                        headers=headers, params=params.get())

    @staticmethod
    def get_danmaku_history(auth, room_id) -> dict:
        """进房时的历史弹幕.

        :param auth: BiliAuth object.
        :param room_id: 真实房间号.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET, HeaderBuilder.live_origin).set_referer(
            f'{BiliLiveApi.live}/{room_id}').get()
        return get_json(auth, f'{BiliLiveApi.api}/xlive/web-room/v1/dM/gethistory',
                        headers=headers, params={'roomid': room_id, 'room_type': 0})

    @staticmethod
    def get_gift_list(auth, room_id, area_parent_id: int = 1, area_id: int = 21,
                      ruid=0) -> dict:
        """直播间礼物列表（WBI 签名）.

        返回结构不是 `data.list`，而是
        `data.gift_data.room_gift_list.gold_list[]`（每项含 `gift_id`），
        礼物名称与单价在 `data.gift_config` 里按 id 查。
        分区参数要用该房间真实的 parent_area_id / area_id，可从 get_room_info 取。

        :param auth: BiliAuth object.
        :param room_id: 真实房间号.
        :param area_parent_id: 一级分区 ID.
        :param area_id: 二级分区 ID.
        :param ruid: 主播 mid.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET, HeaderBuilder.live_origin).set_referer(
            f'{BiliLiveApi.live}/{room_id}').get()
        params = Params({
            'platform': 'pc', 'room_id': room_id, 'area_parent_id': area_parent_id,
            'area_id': area_id, 'source': 'live', 'build': 0, 'ruid': ruid,
            'base_version': 0, 'receive_users': '',
        }).with_web_location('444.8').with_wbi(auth)
        return get_json(auth, f'{BiliLiveApi.api}/xlive/web-room/v1/giftPanel/roomGiftList',
                        headers=headers, params=params.get())

    @staticmethod
    def get_area_list(auth) -> dict:
        """直播分区列表.

        :param auth: BiliAuth object.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET, HeaderBuilder.live_origin).get()
        return get_json(auth, f'{BiliLiveApi.api}/xlive/web-interface/v1/index/getWebAreaList',
                        headers=headers, params={'source_id': 2})

    # ------------------------------------------------------------ 礼物（需登录）

    @staticmethod
    def get_bag_list(auth, room_id) -> dict:
        """我的背包礼物（辣条等免费/已购礼物）.

        `room_id` **必须是真实房间号**，传 0 或不传会返回 40000 网络异常。
        背包为空时 `data.list` 是 null 而不是空数组。

        :param auth: BiliAuth object，需登录态.
        :param room_id: 真实房间号.
        :return: JSON，data.list[] 含 bag_id / gift_id / gift_name / gift_num.
        """
        headers = HeaderBuilder.build(HeaderType.GET, HeaderBuilder.live_origin).set_referer(
            f'{BiliLiveApi.live}/{room_id}').get()
        return get_json(auth, f'{BiliLiveApi.api}/xlive/web-room/v1/gift/bag_list',
                        headers=headers, params={'t': now_ms(), 'room_id': room_id})

    @staticmethod
    def send_gift(auth, room_id, ruid, gift_id, gift_num: int = 1, bag_id=0,
                  coin_type: str = 'silver', price: int = 0) -> dict:
        """送礼.

        端点按 bag_id / coin_type 三选一（对齐直播间 app.js 里的送礼函数）：
        - `bag_id` 非 0 → `sendBagMultiUser`，走背包库存，不花钱
        - `coin_type=gold`  → `sendGoldMultiUser`，**真实扣金瓜子**
        - `coin_type=silver`→ `sendSilverMultiUser`

        注意所有字段都在 **query** 上（POST 但无 body），且 `receive_users`
        是 JSON 数组而不是空串，缺了会被判参数错误。

        :param auth: BiliAuth object，需登录态.
        :param room_id: 真实房间号，作为 biz_id.
        :param ruid: 主播 mid.
        :param gift_id: 礼物 ID，背包礼物取自 get_bag_list，其余取自 get_gift_list.
        :param gift_num: 数量.
        :param bag_id: 背包条目 ID；送背包礼物时必填.
        :param coin_type: gold 金瓜子 / silver 银瓜子；送背包礼物时忽略.
        :param price: 礼物单价，背包礼物填 0.
        :return: JSON.
        """
        if bag_id:
            path = '/xlive/revenue/v2/gift/sendBagMultiUser'
        elif coin_type == 'gold':
            path = '/xlive/revenue/v2/gift/sendGoldMultiUser'
        else:
            path = '/xlive/revenue/v2/gift/sendSilverMultiUser'

        headers = HeaderBuilder.build(HeaderType.FORM, HeaderBuilder.live_origin).set_referer(
            f'{BiliLiveApi.live}/{room_id}').get()
        params = {
            'uid': auth.mid, 'gift_id': gift_id, 'ruid': ruid, 'send_ruid': 0,
            'gift_num': gift_num, 'coin_type': coin_type, 'bag_id': bag_id,
            'platform': 'pc', 'biz_code': 'Live', 'biz_id': room_id,
            'storm_beat_id': 0, 'metadata': '', 'price': price,
            'receive_users': json.dumps([{'uid': ruid}], separators=(',', ':')),
            'all_flag': 1,
            'live_statistics': json.dumps({
                'pc_client': 'pc_web', 'jumpfrom': '-99998',
                'room_category': '-99998', 'source_event': 0,
                'official_channel': {'program_room_id': '-99998',
                                     'program_up_id': '-99998'},
            }, separators=(',', ':')),
            'statistics': json.dumps({'platform': 5, 'pc_client': 'pc_web', 'appId': 100},
                                     separators=(',', ':')),
            'web_location': '444.8',
            'csrf_token': auth.csrf, 'csrf': auth.csrf, 'visit_id': '',
        }
        return post_json(auth, f'{BiliLiveApi.api}{path}', headers=headers, params=params)

    # ------------------------------------------------------------ 互动（需登录）

    @staticmethod
    def danmaku_fields(auth, room_id, msg: str, color: int = 16777215,
                       fontsize: int = 25, mode: int = 1, bubble: int = 0,
                       reply_mid: int = 0, reply_uname: str = '',
                       trackid: str = '-99998') -> list:
        """直播弹幕的 multipart 字段，**列表顺序就是发送顺序**.

        单独抽出以便复用和检查，不必真发请求或去解 multipart。
        顺序按实抓结果排列，别随手改。
        """
        return [
            ('bubble', bubble), ('msg', msg), ('color', color), ('mode', mode),
            ('room_type', 0), ('jumpfrom', 0), ('reply_mid', reply_mid),
            ('reply_attr', 0), ('replay_dmid', ''),
            ('statistics', '{"appId":100,"platform":5}'),
            ('reply_type', 0), ('reply_uname', reply_uname),
            ('data_extend', json.dumps({'trackid': trackid}, separators=(',', ':'))),
            ('fontsize', fontsize), ('rnd', now_ts()), ('roomid', room_id),
            ('csrf', auth.csrf), ('csrf_token', auth.csrf),
        ]

    @staticmethod
    def send_danmaku(auth, room_id, msg: str, color: int = 16777215, fontsize: int = 25,
                     mode: int = 1, bubble: int = 0, reply_mid: int = 0,
                     reply_uname: str = '', trackid: str = '-99998') -> dict:
        """在直播间发弹幕.

        实抓形态（2026-08-16，在自己的直播间真发一条抓下来的）有三个
        容易想当然搞错的地方：

        1. **body 是 `multipart/form-data`，不是 urlencoded。**
           页面用的是 `FormData`，所以 content-type 必须交给传输层带 boundary 生成。
        2. **query 要 WBI 签名**，带 `web_location=444.8`。
        3. `data_extend` 是 `{"trackid":"-99998"}` 而不是空串；
           而浏览器**没有** `rnd_str` 这个字段（早先我们凭空加了一个）。

        body 字段顺序照抄实抓：

            bubble, msg, color, mode, room_type, jumpfrom, reply_mid, reply_attr,
            replay_dmid, statistics, reply_type, reply_uname, data_extend,
            fontsize, rnd, roomid, csrf, csrf_token

        `rnd` 是秒级时间戳；`csrf` 与 `csrf_token` 两个都要给，值相同。

        :param auth: BiliAuth object，需登录态.
        :param room_id: 真实房间号.
        :param msg: 弹幕内容.
        :param color: 十进制颜色，默认白色.
        :param fontsize: 字号.
        :param mode: 1 滚动 4 底部 5 顶部.
        :param bubble: 气泡样式.
        :param reply_mid: 回复某人时的 mid.
        :param reply_uname: 回复某人时的昵称.
        :param trackid: data_extend 里的埋点 id，实抓恒为 -99998.
        :return: JSON.
        """
        from curl_cffi import CurlMime

        # 不预设 content-type：multipart 的 boundary 要由传输层生成
        headers = HeaderBuilder.build(
            HeaderType.GET, HeaderBuilder.live_origin).set_referer(
            f'{BiliLiveApi.live}/{room_id}').get()
        params = (Params({'web_location': '444.8'}).with_wbi(auth))
        fields = BiliLiveApi.danmaku_fields(
            auth, room_id, msg, color=color, fontsize=fontsize, mode=mode,
            bubble=bubble, reply_mid=reply_mid, reply_uname=reply_uname,
            trackid=trackid)
        mime = CurlMime()
        for name, value in fields:
            mime.addpart(name=name, data=str(value).encode('utf-8'))
        try:
            return request(auth, 'post', f'{BiliLiveApi.api}/msg/send',
                           headers=headers, params=params.get(),
                           multipart=mime).json()
        finally:
            mime.close()

    # ------------------------------------------------------------ 开播（需登录）

    @staticmethod
    def start_live(auth, room_id, area_v2: int, platform: str = 'pc_link') -> dict:
        """开播，返回 rtmp 推流地址与串流码.

        :param auth: BiliAuth object，需登录态.
        :param room_id: 自己的真实房间号.
        :param area_v2: 二级分区 ID，需在开播前用 get_area_list 选好.
        :param platform: 开播端，pc_link 为直播姬.
        :return: JSON，data.rtmp.addr + data.rtmp.code 为推流参数.
        """
        headers = HeaderBuilder.build(HeaderType.FORM, HeaderBuilder.live_origin).set_referer(
            f'{BiliLiveApi.live}/{room_id}').get()
        data = {
            'room_id': room_id, 'platform': platform, 'area_v2': area_v2,
            'backup_stream': 0, 'csrf': auth.csrf, 'csrf_token': auth.csrf,
        }
        return post_json(auth, f'{BiliLiveApi.api}/room/v1/Room/startLive',
                         headers=headers, data=data)

    @staticmethod
    def stop_live(auth, room_id, platform: str = 'pc_link') -> dict:
        """关播.

        :param auth: BiliAuth object，需登录态.
        :param room_id: 自己的真实房间号.
        :param platform: 开播端.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.FORM, HeaderBuilder.live_origin).set_referer(
            f'{BiliLiveApi.live}/{room_id}').get()
        data = {
            'room_id': room_id, 'platform': platform,
            'csrf': auth.csrf, 'csrf_token': auth.csrf,
        }
        return post_json(auth, f'{BiliLiveApi.api}/room/v1/Room/stopLive',
                         headers=headers, data=data)
