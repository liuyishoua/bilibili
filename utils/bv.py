"""av 号与 bv 号互转（纯算，无网络）."""

XOR_CODE = 23442827791579
MASK_CODE = 2251799813685247
MAX_AID = 1 << 51
ALPHABET = 'FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf'
ENCODE_MAP = (8, 7, 0, 5, 1, 3, 2, 4, 6)
DECODE_MAP = tuple(reversed(ENCODE_MAP))
BASE = 58
PREFIX = 'BV1'


def av2bv(aid) -> str:
    """av 号转 bv 号.

    :param aid: 数字稿件 ID.
    :return: 形如 BV1GJ411x7h7.
    """
    aid = int(aid)
    chars = [''] * len(ENCODE_MAP)
    tmp = (MAX_AID | aid) ^ XOR_CODE
    for i in range(len(ENCODE_MAP)):
        chars[ENCODE_MAP[i]] = ALPHABET[tmp % BASE]
        tmp //= BASE
    return PREFIX + ''.join(chars)


def bv2av(bvid: str) -> int:
    """bv 号转 av 号.

    :param bvid: 形如 BV1GJ411x7h7，大小写敏感.
    :return: 数字稿件 ID.
    """
    if not bvid.startswith(PREFIX):
        raise ValueError(f'不是合法的 bvid: {bvid}')
    body = bvid[len(PREFIX):]
    if len(body) != len(ENCODE_MAP):
        raise ValueError(f'bvid 长度不对: {bvid}')
    tmp = 0
    for i in range(len(ENCODE_MAP)):
        tmp = tmp * BASE + ALPHABET.index(body[DECODE_MAP[i]])
    return (tmp & MASK_CODE) ^ XOR_CODE
