"""correspondPath：Cookie 续期链路的入场券（纯算）.

B站 Web 每日首访会查一次 `cookie/info`，若 `data.refresh` 为 true，页面就用
iframe 加载 `www.bilibili.com/correspond/1/{correspondPath}`，从返回的 HTML 里
取一次性口令 `refresh_csrf`，再拿它去换新的 SESSDATA / bili_jct。

`correspondPath` 的实现藏在首页的 wasm 里
（`wasm_rsa_encrypt_bg.wasm` + `wasm_ras_umd.js`），但算法本身很朴素：
明文 `refresh_{毫秒时间戳}`，用下面这把写死在前端的 1024 位公钥做
RSA-OAEP(SHA-256) 加密，密文按小写十六进制输出（定长 256 个字符）。
公钥固定、无盐、无随机以外的状态，所以这一步纯算，既不需要 Node 也不需要 wasm。

⚠️ OAEP 自带随机填充，同一个时间戳每次算出来的 path 都不同，这是正常的；
服务端只解密比对里面的时间戳，所以别拿两次结果去对拍。
"""

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from utils.common_util import now_ms

# 取自 B站 首页 wasm 导出的 JWK（kty=RSA, e=AQAB），转成 PEM 后写死在这里
PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDLgd2OAkcGVtoE3ThUREbio0Eg
Uc/prcajMKXvkCKFCWhJYJcLkcM2DKKcSeFpD/j6Boy538YXnR6VhcuUJOhH2x71
nzPjfdTcqMz7djHum0qSZA0AyCBDABUqCrfNgCiJ00Ra7GmRj+YCK1NJEuewlb40
JNrRuoEUXpabUzGB8QIDAQAB
-----END PUBLIC KEY-----"""

CORRESPOND_URL = 'https://www.bilibili.com/correspond/1/{path}'

_public_key = None


def gen_correspond_path(ts: int = 0) -> str:
    """算出 correspond 页面的路径段.

    :param ts: 毫秒时间戳，留空取当前时间。有 `cookie/info` 回的
        `data.timestamp` 时应当传它——浏览器就是这么用的，能避开本地时钟偏移.
    :return: 256 个字符的小写十六进制串.
    """
    global _public_key
    if _public_key is None:
        _public_key = serialization.load_pem_public_key(PUBLIC_KEY_PEM)
    message = f'refresh_{ts or now_ms()}'.encode()
    cipher = _public_key.encrypt(
        message,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                     algorithm=hashes.SHA256(), label=None),
    )
    return cipher.hex()
