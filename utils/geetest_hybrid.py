"""极验点选的**混合识别**：YOLO 检测 + 共享模型度量匹配.

单独用任何一种都不够（2026-08-16 实测）：

* **纯 ddddocr**：对拼图候选很稳（跨 3 个尺度给出完全一致的答案，
  `菇`/`钱` 与肉眼一致），但对**提示条**失败——提示条的字只有 30 px 高、
  相邻字框互相重叠，识别结果是 `金/熊贤/z/端` 这种带多字和非汉字的脏输出。
  两边字面对不上，端到端 overlap 为 0。

* **纯字形匹配**（`geetest_ocr.solve`）：不认字，只比对提示条与拼图里的
  同一批字形实例。不受字小影响，但拼图里的字叠在照片上、带彩色描边和旋转，
  互相关在低对比背景下会退化。

组合思路：**点选题根本不需要知道字叫什么**，只需要「提示条第 k 个字对应
哪张候选图」。所以：

1. YOLO 检测提示字与候选字，并合并同一彩色字的重叠框；
2. 两边通过同一个 OCR 模型，比较完整字符概率向量，而不是只比最终字面；
3. 匈牙利算法求一一对应，OCR 字面只作为日志旁证。

ddddocr 在这里的价值不是提供答案，而是提供**独立的一票**：
两种完全不同的方法（字形互相关 vs 神经网络分类）对同一张图的判断如果
矛盾，就该怀疑；一致则可信度高。
"""

import io

from utils.geetest_ocr import detect_boxes, split_sprite

PAD = 4

_ocr = None


def _get_ocr():
    global _ocr
    if _ocr is None:
        import ddddocr
        _ocr = ddddocr.DdddOcr(show_ad=False)
    return _ocr


def _classify(image) -> str:
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    return _get_ocr().classification(buf.getvalue())


def _han(text: str) -> list:
    import re
    return re.findall(r'[一-鿿]', text)


def read_candidates(sprite, boxes=None, scale: int = 5) -> list:
    """用 ddddocr 认出拼图里每个候选的字面.

    实测要点：
    * 必须用**原始彩色裁剪**，不能先洗成黑白线稿——
      ddddocr 是在彩色验证码上训练的，洗成线稿后信息丢失，识别全错。
    * 四周留 PAD=4 px，贴着笔画切会掉边缘。
    * 放大倍数对结果无影响（4/5/6 倍给出完全相同的答案），
      说明模型内部会自己归一化尺寸。

    :param sprite: 极验下发的 sprite 原图.
    :param boxes: 候选框；None 时自己检测.
    :param scale: 放大倍数，实测不敏感.
    :return: 与 boxes 同序的汉字列表，认不出的位置为 ''.
    """
    puzzle, _ = split_sprite(sprite)
    boxes = boxes or detect_boxes(puzzle)

    out = []
    for x1, y1, x2, y2 in boxes:
        crop = puzzle.crop((max(0, x1 - PAD), max(0, y1 - PAD),
                            min(puzzle.width, x2 + PAD),
                            min(puzzle.height, y2 + PAD)))
        if scale > 1:
            from PIL import Image
            crop = crop.resize((crop.width * scale, crop.height * scale),
                               Image.LANCZOS)
        chars = _han(_classify(crop))
        out.append(chars[0] if chars else '')
    return out


def solve(sprite) -> dict:
    """给出点击顺序，并附带 ddddocr 的旁证.

    :param sprite: 极验下发的 sprite 原图.
    :return: 在 `geetest_ocr.solve()` 结果基础上追加::

        {"cand_chars": ddddocr 认出的候选字面,
         "ordered_chars": 按点击顺序排列的字面,
         "warnings": 可疑之处的说明列表}
    """
    from utils.geetest_metric import solve as metric_solve

    result = metric_solve(sprite)
    chars = result['cand_chars']

    warnings = []
    blank = [i + 1 for i, c in enumerate(chars) if not c]
    if blank:
        warnings.append(f'候选 {blank} ddddocr 认不出，字形匹配无旁证')

    picked = result['ordered_chars']
    dupes = {c for c in picked if c and picked.count(c) > 1}
    if dupes:
        warnings.append(f'点击序列里出现重复字 {sorted(dupes)}，'
                        f'可能有两个提示位指到了同一类字形')

    low = [m for m in result['match'] if m['score'] < 0.20]
    if low:
        warnings.append(f'{len(low)} 个模型匹配低于 0.20，建议刷新题面')

    result['warnings'] = list(dict.fromkeys(result['warnings'] + warnings))
    return result
