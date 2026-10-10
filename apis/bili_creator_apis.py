"""创作接口（视频投稿 / 图文动态 / 专栏 / 稿件管理）.

全部需要登录态：Cookie 里要有 SESSDATA，写操作要带 bili_jct 作为 csrf。

本模块的字段已按实际登录态接口校正。
测试投稿一律先设为**仅自己可见**（`post_video(..., private=True)`）。
"""

import base64
import os
import random
import time

from builder.header import HeaderBuilder, HeaderType
from utils.common_util import now_ms
from utils.http_util import get_json, post_json, request
from utils.upos import upload_video


class BiliCreatorApi:
    api = 'https://api.bilibili.com'
    member = 'https://member.bilibili.com'

    # ------------------------------------------------------------------ 元数据

    UPLOAD_PAGE = 'https://member.bilibili.com/platform/upload/video/frame'

    @staticmethod
    def get_archive_pre(auth) -> dict:
        """投稿页初始化数据，含分区树 typelist、活动、模板、我的信息等.

        实抓校正（2026-08-16）：网上常说的 `x/vupre/web/archive/types` **返回 404**，
        真实端点是这个 `archive/pre`，分区树在 `data.typelist`。

        :param auth: BiliAuth object，需登录态.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET, HeaderBuilder.member_origin, same_origin=True).set_referer(
            BiliCreatorApi.UPLOAD_PAGE).get()
        return get_json(auth, f'{BiliCreatorApi.member}/x/vupre/web/archive/pre',
                        headers=headers, params={'lang': 'cn', 't': now_ms()})

    @staticmethod
    def get_archive_types(auth) -> list:
        """取投稿分区树，投稿时的 tid 从这里选.

        :param auth: BiliAuth object，需登录态.
        :return: [{id, name, children:[{id, name, desc}]}, ...].
        """
        res = BiliCreatorApi.get_archive_pre(auth)
        if res.get('code') != 0:
            raise RuntimeError(f'取分区失败: {res}')
        return (res.get('data') or {}).get('typelist') or []

    @staticmethod
    def get_my_archives(auth, page: int = 1, page_size: int = 20, status: str = 'is_pubing,pubed,not_pubed') -> dict:
        """我的稿件列表.

        :param auth: BiliAuth object，需登录态.
        :param page: 页码.
        :param page_size: 每页条数.
        :param status: 稿件状态筛选.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET, HeaderBuilder.member_origin, same_origin=True).get()
        return get_json(auth, f'{BiliCreatorApi.member}/x/web/archives',
                        headers=headers,
                        params={'status': status, 'pn': page, 'ps': page_size,
                                'coop': 1, 'interactive': 1})

    # ------------------------------------------------------------------ 视频

    @staticmethod
    def upload_cover(auth, image_path: str) -> dict:
        """上传封面，返回图片 URL.

        :param auth: BiliAuth object，需登录态.
        :param image_path: 本地封面图片路径.
        :return: JSON，data.url 为封面地址.
        """
        with open(image_path, 'rb') as f:
            payload = base64.b64encode(f.read()).decode()
        suffix = os.path.splitext(image_path)[1].lstrip('.').lower() or 'jpeg'
        headers = HeaderBuilder.build(HeaderType.FORM, HeaderBuilder.member_origin, same_origin=True).get()
        data = {'cover': f'data:image/{suffix};base64,{payload}', 'csrf': auth.csrf}
        return post_json(auth, f'{BiliCreatorApi.member}/x/vu/web/cover/up',
                         headers=headers, data=data)

    @staticmethod
    def submit_archive(auth, videos: list, title: str, tid: int, tag: str,
                       cover: str = '', desc: str = '', copyright_: int = 1,
                       source: str = '', private: bool = True, dynamic: str = '',
                       no_reprint: int = 1) -> dict:
        """提交投稿.

        :param auth: BiliAuth object，需登录态.
        :param videos: upload_video 的返回列表，每项至少含 filename.
        :param title: 稿件标题.
        :param tid: 分区 ID，用 get_archive_types 查.
        :param tag: 标签，逗号分隔，至少一个.
        :param cover: 封面 URL，来自 upload_cover.
        :param desc: 简介.
        :param copyright_: 1=自制 2=转载.
        :param source: 转载来源，copyright_=2 时必填.
        :param private: 是否仅自己可见，测试期务必为 True.
        :param dynamic: 同步到动态的文案.
        :param no_reprint: 1=禁止转载.
        :return: JSON，成功返回 data.bvid / data.aid.
        """
        headers = HeaderBuilder.build(HeaderType.POST, HeaderBuilder.member_origin, same_origin=True).set_referer(
            BiliCreatorApi.UPLOAD_PAGE).get()
        body = {
            'copyright': copyright_,
            'source': source,
            'cover': cover,
            'title': title,
            'tid': tid,
            'tag': tag,
            'desc': desc,
            'desc_format_id': 0,
            'dynamic': dynamic,
            'recreate': -1,
            'interactive': 0,
            'no_reprint': no_reprint,
            'subtitle': {'open': 0, 'lan': ''},
            'videos': [{'filename': v['filename'], 'title': v.get('title') or title,
                        'desc': v.get('desc', ''), 'cid': v.get('biz_id')}
                       for v in videos],
            'human_type2': 0,
            'topic_id': 0,
            'mission_id': 0,
            'topic_name': '',
            'topic_from': '',
            'act_reserve_create': 0,
            'is_only_self': 1 if private else 0,
            'web_os': 2,
            'csrf': auth.csrf,
        }
        return post_json(auth, f'{BiliCreatorApi.member}/x/vu/web/add/v3',
                         headers=headers,
                         params={'web_location': '333.1024', 't': now_ms(),
                                 'csrf': auth.csrf},
                         json=body)

    @staticmethod
    def post_video(auth, file_path: str, title: str, tid: int, tag: str,
                   cover_path: str = '', desc: str = '', private: bool = True,
                   on_progress=None) -> tuple:
        """一站式投稿：上传视频 → 上传封面 → 提交.

        :param auth: BiliAuth object，需登录态.
        :param file_path: 本地视频路径.
        :param title: 稿件标题.
        :param tid: 分区 ID.
        :param tag: 标签，逗号分隔.
        :param cover_path: 本地封面路径，可省略.
        :param desc: 简介.
        :param private: 是否仅自己可见，默认 True.
        :param on_progress: 上传进度回调 (已传分片, 总分片).
        :return: (success, msg, res_json).
        """
        try:
            video = upload_video(auth, file_path, on_progress=on_progress)
            cover = ''
            if cover_path:
                cover_res = BiliCreatorApi.upload_cover(auth, cover_path)
                if cover_res.get('code') != 0:
                    return False, f'封面上传失败: {cover_res}', cover_res
                cover = cover_res['data']['url']
            res = BiliCreatorApi.submit_archive(
                auth, [video], title=title, tid=tid, tag=tag, cover=cover,
                desc=desc, private=private)
            if res.get('code') != 0:
                return False, f"投稿失败: {res.get('message')}", res
            return True, '投稿成功', res
        except Exception as e:
            return False, str(e), {}

    @staticmethod
    def delete_archive(auth, aid, validate: str = '', seccode: str = '',
                       challenge: str = '') -> dict:
        """删除自己的稿件（撤稿）.

        ⚠️ **撤稿被人机验证挡着，这是平台设计而不是本项目的缺陷。**
        已在浏览器里实证：网页端点「删除稿件」会弹出标题为「验证并删除此视频」
        的极验点选弹窗（容器类名 `risk-captcha-adapt-pc`），真人也必须过一遍。
        不带验证结果直接调用固定返回 `340022 验证码错误`。

        端点本身是对的——另外两个常见写法 `/x/vu/web/delete`、
        `/x/vupre/web/archive/delete` 都是 404。

        所以这里把验证结果做成入参；取得验证结果后传入
        `validate` / `seccode` 即可继续调用。

        :param auth: BiliAuth object，需登录态.
        :param aid: 稿件 av 号.
        :param validate: 极验二次验证返回的 validate.
        :param seccode: 极验的 seccode，通常是 `validate + '|jordan'`.
        :param challenge: 本轮极验的 challenge.
        :return: JSON；没有验证结果时 code=340022.
        """
        headers = HeaderBuilder.build(
            HeaderType.FORM, HeaderBuilder.member_origin, same_origin=True).set_referer(
            'https://member.bilibili.com/platform/upload-manager/article').get()
        data = {'aid': aid, 'csrf': auth.csrf}
        if validate:
            data.update({'validate': validate,
                         'seccode': seccode or f'{validate}|jordan',
                         'challenge': challenge})
        return post_json(auth, f'{BiliCreatorApi.member}/x/web/archive/delete',
                         headers=headers, data=data)

    # ------------------------------------------------------------------ 动态

    @staticmethod
    def upload_dynamic_image(auth, image_path: str) -> dict:
        """上传动态配图.

        :param auth: BiliAuth object，需登录态.
        :param image_path: 本地图片路径.
        :return: JSON，data.image_url / image_width / image_height.
        """
        # 不预设 content-type：multipart 的 boundary 要由传输层生成。
        # curl_cffi 不支持 requests 那套 `files=`，得用 CurlMime 显式拼表单。
        from curl_cffi import CurlMime

        headers = HeaderBuilder.build(
            HeaderType.GET, HeaderBuilder.dynamic_origin).get()
        mime = CurlMime()
        mime.addpart(name='file_up', filename=os.path.basename(image_path),
                     local_path=image_path)
        for key, value in (('biz', 'new_dyn'), ('category', 'daily'),
                           ('csrf', auth.csrf)):
            mime.addpart(name=key, data=value.encode())
        try:
            return request(auth, 'post',
                           f'{BiliCreatorApi.api}/x/dynamic/feed/draw/upload_bfs',
                           headers=headers, multipart=mime, timeout=120).json()
        finally:
            mime.close()

    @staticmethod
    def post_dynamic(auth, text: str, image_paths: list = None) -> tuple:
        """发一条图文动态.

        :param auth: BiliAuth object，需登录态.
        :param text: 正文.
        :param image_paths: 本地图片路径列表，可为空即纯文字动态.
        :return: (success, msg, res_json).
        """
        try:
            pics = []
            for path in image_paths or []:
                res = BiliCreatorApi.upload_dynamic_image(auth, path)
                if res.get('code') != 0:
                    return False, f'配图上传失败: {res}', res
                data = res['data']
                pics.append({
                    'img_src': data['image_url'],
                    'img_width': data['image_width'],
                    'img_height': data['image_height'],
                    'img_size': data.get('img_size', 0),
                })

            headers = HeaderBuilder.build(
                HeaderType.POST, HeaderBuilder.dynamic_origin).get()
            body = {
                'dyn_req': {
                    'content': {'contents': [{'raw_text': text, 'type': 1, 'biz_id': ''}]},
                    'scene': 2 if pics else 1,
                    'attach_card': None,
                    'upload_id': f'{auth.mid}_{int(time.time())}_{random.randint(1000, 9999)}',
                    'meta': {'app_meta': {'from': 'create.dynamic.web', 'mobi_app': 'web'}},
                }
            }
            if pics:
                body['dyn_req']['pics'] = pics
            res = post_json(auth, f'{BiliCreatorApi.api}/x/dynamic/feed/create/dyn',
                            headers=headers, params={'platform': 'web', 'csrf': auth.csrf},
                            json=body)
            if res.get('code') != 0:
                return False, f"发布失败: {res.get('message')}", res
            return True, '发布成功', res
        except Exception as e:
            return False, str(e), {}

    @staticmethod
    def remove_dynamic(auth, dyn_id_str) -> dict:
        """删除一条动态.

        :param auth: BiliAuth object，需登录态.
        :param dyn_id_str: 动态 ID（create/dyn 返回的 dyn_id_str）.
        :return: JSON.
        """
        headers = HeaderBuilder.build(
            HeaderType.POST, HeaderBuilder.dynamic_origin).get()
        return post_json(auth, f'{BiliCreatorApi.api}/x/dynamic/feed/operate/remove',
                         headers=headers, params={'csrf': auth.csrf},
                         json={'dyn_id_str': str(dyn_id_str)})

    # ------------------------------------------------------------------ 专栏

    @staticmethod
    def save_article_draft(auth, title: str, content: str, category: int = 0,
                           tags: str = '', summary: str = '', aid=None) -> dict:
        """保存专栏草稿.

        :param auth: BiliAuth object，需登录态.
        :param title: 标题.
        :param content: HTML 正文.
        :param category: 专栏分区.
        :param tags: 标签，逗号分隔.
        :param summary: 摘要.
        :param aid: 已有草稿 ID，续写时传.
        :return: JSON，data.aid 为草稿 ID.
        """
        headers = HeaderBuilder.build(HeaderType.FORM).set_referer(
            'https://member.bilibili.com/read/editor/').get()
        data = {
            'title': title, 'content': content, 'summary': summary,
            'banner_url': '', 'category': category, 'tags': tags,
            'list_id': 0, 'reprint': 0, 'media_id': 0, 'spoiler': 0,
            'original': 1, 'csrf': auth.csrf,
        }
        if aid:
            data['aid'] = aid
        return post_json(auth, f'{BiliCreatorApi.api}/x/article/creative/draft/addupdate',
                         headers=headers, data=data)

    @staticmethod
    def get_article_draft(auth, aid) -> dict:
        """按 ID 读取专栏草稿，用于回验草稿是否写入成功.

        注：`draft/list` 实测恒返回 `data:null`（不是本接口的问题，
        专栏管理页走的是另一套列表），按 aid 单查是可靠的。

        :param auth: BiliAuth object，需登录态.
        :param aid: 草稿 ID，来自 save_article_draft.
        :return: JSON，data 含 title / content / summary / tags.
        """
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(
            'https://member.bilibili.com/read/editor/').get()
        return get_json(auth, f'{BiliCreatorApi.api}/x/article/creative/draft/view',
                        headers=headers, params={'aid': aid})

    @staticmethod
    def delete_article_draft(auth, aid) -> dict:
        """删除专栏草稿.

        :param auth: BiliAuth object，需登录态.
        :param aid: 草稿 ID.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.FORM).set_referer(
            'https://member.bilibili.com/read/editor/').get()
        return post_json(auth, f'{BiliCreatorApi.api}/x/article/creative/draft/delete',
                         headers=headers, data={'aid': aid, 'csrf': auth.csrf})

    @staticmethod
    def submit_article(auth, aid, title: str, content: str, category: int = 0,
                       tags: str = '', summary: str = '') -> dict:
        """提交专栏投稿.

        :param auth: BiliAuth object，需登录态.
        :param aid: save_article_draft 返回的草稿 ID.
        :param title: 标题.
        :param content: HTML 正文.
        :param category: 专栏分区.
        :param tags: 标签.
        :param summary: 摘要.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.FORM).set_referer(
            'https://member.bilibili.com/read/editor/').get()
        data = {
            'aid': aid, 'title': title, 'content': content, 'summary': summary,
            'banner_url': '', 'category': category, 'tags': tags,
            'list_id': 0, 'reprint': 0, 'media_id': 0, 'spoiler': 0,
            'original': 1, 'csrf': auth.csrf,
        }
        return post_json(auth, f'{BiliCreatorApi.api}/x/article/creative/article/submit',
                         headers=headers, data=data)
