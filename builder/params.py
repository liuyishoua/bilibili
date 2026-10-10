from utils.dm_img import build_dm_img_params
from utils.wbi import enc_wbi


class Params:
    """query 参数链式装配器.

    约定：`with_wbi()` 必须是最后一步，因为 w_rid 覆盖此前的全部参数。
    """

    def __init__(self, params: dict = None):
        self.params = dict(params or {})

    def add_param(self, key, value):
        self.params[key] = value
        return self

    def update_params(self, params: dict):
        self.params.update(params or {})
        return self

    def with_web_location(self, web_location):
        """页面埋点位标识，多数 WBI 接口都会带."""
        self.params['web_location'] = web_location
        return self

    def with_dm_img(self, sample_count: int = 0, with_ds: bool = False):
        """WebGL 与交互指纹，必须在 with_wbi 之前调用.

        缺省不带任何鼠标/元素采样——这才是浏览器的行为，
        伪造采样会让空间接口大概率返回 -352，详见 utils/dm_img.py 开头。

        :param sample_count: dm_img_list 采样点个数，缺省 0（空数组）.
        :param with_ds: dm_img_inter 是否带元素采样，缺省 False.
        """
        self.params.update(build_dm_img_params(sample_count, with_ds))
        return self

    def with_csrf(self, auth):
        """写操作的 CSRF token（等于 Cookie 里的 bili_jct）."""
        self.params['csrf'] = auth.csrf
        return self

    def with_wbi(self, auth, wts: int = None):
        """WBI 签名，产出 wts + w_rid，务必最后调用."""
        self.params = enc_wbi(self.params, auth.mixin_key, wts=wts)
        return self

    def get(self):
        return self.params

    def to_string(self):
        return '&'.join(f'{k}={v}' for k, v in self.params.items())
