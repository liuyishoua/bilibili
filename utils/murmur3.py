"""murmur3_x64_128 纯算实现，用于生成 buvid_fp."""

_MASK64 = 0xFFFFFFFFFFFFFFFF

_C1 = 0x87C37B91114253D5
_C2 = 0x4CF5AD432745937F


def _rotl(x: int, r: int) -> int:
    return ((x << r) | (x >> (64 - r))) & _MASK64


def _fmix64(k: int) -> int:
    k ^= k >> 33
    k = (k * 0xFF51AFD7ED558CCD) & _MASK64
    k ^= k >> 33
    k = (k * 0xC4CEB9FE1A85EC53) & _MASK64
    k ^= k >> 33
    return k


def murmur3_x64_128(data, seed: int = 0) -> tuple:
    """计算 128 位 murmur3 哈希.

    :param data: bytes 或 str.
    :param seed: 种子，B站 用 31.
    :return: (h1, h2) 两个 64 位整数.
    """
    if isinstance(data, str):
        data = data.encode('utf-8')

    length = len(data)
    nblocks = length // 16
    h1 = h2 = seed & _MASK64

    for i in range(nblocks):
        offset = i * 16
        k1 = int.from_bytes(data[offset:offset + 8], 'little')
        k2 = int.from_bytes(data[offset + 8:offset + 16], 'little')

        k1 = (k1 * _C1) & _MASK64
        k1 = _rotl(k1, 31)
        k1 = (k1 * _C2) & _MASK64
        h1 ^= k1
        h1 = _rotl(h1, 27)
        h1 = (h1 + h2) & _MASK64
        h1 = (h1 * 5 + 0x52DCE729) & _MASK64

        k2 = (k2 * _C2) & _MASK64
        k2 = _rotl(k2, 33)
        k2 = (k2 * _C1) & _MASK64
        h2 ^= k2
        h2 = _rotl(h2, 31)
        h2 = (h2 + h1) & _MASK64
        h2 = (h2 * 5 + 0x38495AB5) & _MASK64

    tail = data[nblocks * 16:]
    k1 = k2 = 0
    tail_len = len(tail)
    if tail_len > 8:
        for i in range(tail_len - 1, 7, -1):
            k2 = (k2 << 8) | tail[i]
        k2 = (k2 * _C2) & _MASK64
        k2 = _rotl(k2, 33)
        k2 = (k2 * _C1) & _MASK64
        h2 ^= k2
    if tail_len > 0:
        for i in range(min(tail_len, 8) - 1, -1, -1):
            k1 = (k1 << 8) | tail[i]
        k1 = (k1 * _C1) & _MASK64
        k1 = _rotl(k1, 31)
        k1 = (k1 * _C2) & _MASK64
        h1 ^= k1

    h1 ^= length
    h2 ^= length
    h1 = (h1 + h2) & _MASK64
    h2 = (h2 + h1) & _MASK64
    h1 = _fmix64(h1)
    h2 = _fmix64(h2)
    h1 = (h1 + h2) & _MASK64
    h2 = (h2 + h1) & _MASK64
    return h1, h2


def murmur3_hex(data, seed: int = 31) -> str:
    """返回 32 位十六进制摘要（B站 buvid_fp 的形态）."""
    h1, h2 = murmur3_x64_128(data, seed)
    return f'{h1:016x}{h2:016x}'
