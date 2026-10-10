"""设备画像：UA、屏幕、WebGL 等浏览器侧常量.

同一进程内保持稳定，避免同一会话前后指纹跳变被风控识别。
需要模拟另一台设备时用 set_profile() 整体覆盖。
"""

_PROFILE = {
    # 版本锁在 146：curl_cffi 的 TLS/HTTP2 伪装模板最新支持到 chrome146，
    # UA 报别的版本就会和传输层指纹自相矛盾。
    # 实抓（2026-08-16）显示浏览器端已是 Chrome/151，但 curl_cffi 伪装目标
    # 仍是 chrome146，所以 UA 也必须保持 146，否则 UA 与 TLS 指纹自相矛盾。
    'ua': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
           '(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36'),
    'sec_ch_ua': '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
    'sec_ch_ua_platform': '"Windows"',
    'accept_language': 'zh-CN,zh;q=0.9,en;q=0.8,zh-TW;q=0.7,ja;q=0.6',
    'screen_width': 2560,
    'screen_height': 1440,
    'browser_resolution': '2560-1215',
    'color_depth': 24,
    'device_memory': 8,
    # 实抓 ExClimbWuzhi 的 `0bd0` 字段是 20（navigator.hardwareConcurrency）
    'hardware_concurrency': 20,
    'timezone': 'Asia/Shanghai',
    # WebGL 两串，dm_img_str / dm_cover_img_str 的原文
    'webgl_version': 'WebGL 1.0 (OpenGL ES 2.0 Chromium)',
    'webgl_renderer': ('ANGLE (NVIDIA, NVIDIA GeForce RTX 5060 Ti (0x00002D04) '
                       'Direct3D11 vs_5_0 ps_5_0, D3D11)'),
    'webgl_vendor': 'Google Inc. (NVIDIA)',
}


def get_profile() -> dict:
    """取当前设备画像."""
    return _PROFILE


def set_profile(**kwargs) -> dict:
    """覆盖设备画像字段.

    :param kwargs: 需要覆盖的字段，未知字段会被拒绝.
    :return: 更新后的画像.
    """
    unknown = set(kwargs) - set(_PROFILE)
    if unknown:
        raise ValueError(f'未知的设备画像字段: {sorted(unknown)}')
    _PROFILE.update(kwargs)
    return _PROFILE
