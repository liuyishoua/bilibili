"""upos 分片上传（B站 自建对象存储）.

投稿视频走三段式：
1. `member.bilibili.com/preupload` 换上传凭证，拿到 endpoint / auth / upos_uri / chunk_size；
2. 向 endpoint 初始化分片会话拿 upload_id，再逐片 PUT 上传；
3. 提交分片清单完成合并。

鉴权全靠 `X-Upos-Auth` 头（值就是 preupload 下发的 auth 串），不需要额外签名。

字段来源：读投稿页 bundle `creator-monorepo/videoup/static/js/{951,index}.js`
的 `uploadsQuery` / `completeQuery` 构造处，2026-08-16 实测三段全部 200 通过。
"""

import math
import os

from utils.fingerprint import get_profile
from utils.http_util import request

PREUPLOAD_API = 'https://member.bilibili.com/preupload'
DEFAULT_PROFILE = 'ugcfx/bup'


def _headers(upos_auth: str = '') -> dict:
    headers = {
        'user-agent': get_profile()['ua'],
        'origin': 'https://member.bilibili.com',
        'referer': 'https://member.bilibili.com/',
    }
    if upos_auth:
        headers['x-upos-auth'] = upos_auth
    return headers


def preupload(auth, file_path: str, profile: str = DEFAULT_PROFILE) -> dict:
    """申请上传凭证.

    :param auth: BiliAuth object，需登录态.
    :param file_path: 本地视频路径.
    :param profile: 上传业务线，视频用 ugcfx/bup.
    :return: JSON，含 endpoint / upos_uri / auth / biz_id / chunk_size.
    """
    size = os.path.getsize(file_path)
    params = {
        'name': os.path.basename(file_path),
        'size': size,
        'r': 'upos',
        'profile': profile,
        'ssl': 0,
        'version': '2.14.0',
        'build': 2140000,
        'webVersion': '2.14.0',
    }
    res_json = request(auth, 'get', PREUPLOAD_API, params=params,
                       headers=_headers(), timeout=30).json()
    if res_json.get('OK') != 1:
        raise RuntimeError(f'preupload 失败: {res_json}')
    return res_json


def _upload_url(pre: dict) -> tuple:
    """从 preupload 结果拼出上传地址与对象 key.

    :return: (url, key)，key 去掉扩展名后就是投稿时要填的 filename.
    """
    endpoint = pre['endpoint']
    if endpoint.startswith('//'):
        endpoint = 'https:' + endpoint
    key = pre['upos_uri'].replace('upos://', '')
    return f'{endpoint}/{key}', key




def upload_video(auth, file_path: str, profile: str = DEFAULT_PROFILE,
                 on_progress=None) -> dict:
    """完整上传一个视频文件.

    :param auth: BiliAuth object，需登录态.
    :param file_path: 本地视频路径.
    :param profile: 上传业务线.
    :param on_progress: 可选回调 (已传分片数, 总分片数).
    :return: {"filename": 投稿用文件名, "biz_id": ..., "key": ...}.
    """
    pre = preupload(auth, file_path, profile)
    url, key = _upload_url(pre)
    upos_auth = pre['auth']
    chunk_size = int(pre.get('chunk_size') or 10 * 1024 * 1024)
    biz_id = pre.get('biz_id')
    size = os.path.getsize(file_path)
    chunks = max(1, math.ceil(size / chunk_size))
    name = os.path.basename(file_path)

    # 初始化分片会话。这四个 query 字段来自投稿页 JS 的 uploadsQuery，
    # 少任何一个都会被 upos 以 InvalidArgument(400001001) 拒绝
    init = request(auth, 'post', f'{url}?uploads&output=json', params={
        'profile': profile,
        'filesize': size,
        'partsize': chunk_size,
        'biz_id': biz_id,
    }, headers=_headers(upos_auth), timeout=60).json()
    if init.get('OK') != 1:
        raise RuntimeError(f'初始化分片失败: {init}')
    upload_id = init['upload_id']

    parts = []
    with open(file_path, 'rb') as f:
        for index in range(chunks):
            start = index * chunk_size
            data = f.read(chunk_size)
            end = start + len(data)
            put_url = (f'{url}?partNumber={index + 1}&uploadId={upload_id}'
                       f'&chunk={index}&chunks={chunks}&size={len(data)}'
                       f'&start={start}&end={end}&total={size}&output=json')
            resp = request(auth, 'put', put_url, data=data,
                           headers=_headers(upos_auth), timeout=300)
            if resp.status_code != 200:
                raise RuntimeError(f'分片 {index + 1}/{chunks} 上传失败: {resp.text[:200]}')
            parts.append({'partNumber': index + 1, 'eTag': 'etag'})
            if on_progress:
                on_progress(index + 1, chunks)

    done = request(auth, 'post', url, params={
        'output': 'json', 'name': name, 'profile': profile,
        'uploadId': upload_id, 'biz_id': biz_id,
    }, json={'parts': parts}, headers=_headers(upos_auth), timeout=120).json()
    if done.get('OK') != 1:
        raise RuntimeError(f'分片合并失败: {done}')

    return {
        'filename': os.path.splitext(os.path.basename(key))[0],
        'biz_id': biz_id,
        'key': key,
    }
