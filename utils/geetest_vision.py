"""极验点选验证码的**视觉识别**通路：切图 → 认字 → 出坐标.

与 `geetest_ocr` 的区别在于识别策略：

* `geetest_ocr` 走**形状匹配**——不认字，只比对提示条与拼图里的同一批字形实例。
  优点是不依赖任何外部模型，缺点是拼图里的字叠在照片上、还带彩色描边和旋转，
  互相关在低对比背景下会退化到噪声水平，实测 5 选 4 的稳定性不够。

* 本模块走**认字**——把每个候选字单独抠出来、放大、增强对比，交给
  多模态模型逐个读出汉字，再按提示条的字序去拼图里找对应的框。
  只要模型认得出字，背景干扰、旋转、描边全都无所谓。

模块本身**不绑定任何具体模型**：`prepare()` 负责产出人/模型可读的切图，
`assemble()` 负责把识别结果拼回坐标，可用于调试或接入其他识别器。

用法::

    prep = prepare(Image.open('captcha.jpg'), workdir='_gt/shot')
    # -> 产出 hint.png / cand1.png ... candN.png，人或模型逐张读出汉字
    res = assemble(prep, hint_chars=['金','钱','豪','菇'],
                   cand_chars=['钱','菇','豪','金','酒'])
    res['a']       # 直接塞进点选载荷的 a 字段
    res['clicks']  # [(x,y), ...] 拼图像素坐标，便于叠图自检
"""

from PIL import Image, ImageEnhance, ImageFilter

from utils.geetest_ocr import _split_overlaps, detect_boxes, split_sprite

# 单字放大倍数。极验的候选字在原图里只有 60~70 px，
# 直接喂给多模态模型偏小；放大到 ~350 px 后笔画间的连断关系才看得清。
CAND_SCALE = 5
# 提示条整条放大倍数。提示条只有 40 px 高，放大后才能分辨字形。
HINT_SCALE = 8
# 抠单字时向外扩的像素，避免检测框贴着笔画把边缘切掉。
PAD = 3


def _enhance(crop: Image.Image, scale: int) -> Image.Image:
    """放大 + 锐化 + 提对比，让笔画从照片背景里浮出来.

    LANCZOS 放大后笔画边缘会糊，先 UnsharpMask 把描边拉回来，
    再拉对比度压暗背景——描边是高饱和纯色，照片背景是低对比自然图像，
    提对比对前者几乎无损、对后者是压制。
    """
    big = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    big = big.filter(ImageFilter.UnsharpMask(radius=3, percent=180, threshold=2))
    return ImageEnhance.Contrast(big).enhance(1.6)


def prepare(sprite: Image.Image, workdir: str = None) -> dict:
    """切分 sprite，产出可供认字的放大切图.

    :param sprite: 极验下发的 344x384 sprite 原图.
    :param workdir: 切图落盘目录；为 None 时只返回 Image 对象不落盘.
    :return: {"puzzle_boxes": [[x1,y1,x2,y2],...] 按 x 从左到右,
              "hint_boxes":   同上,
              "puzzle_size":  (w,h),
              "cands":        [Image, ...] 与 puzzle_boxes 一一对应,
              "hint":         Image 放大后的整条提示条,
              "paths":        落盘路径（workdir 为 None 时缺省）}
    """
    import os

    puzzle, hint = split_sprite(sprite)
    p_boxes = detect_boxes(puzzle)
    h_boxes = _split_overlaps(detect_boxes(hint))

    cands = []
    for box in p_boxes:
        x1, y1, x2, y2 = box
        wide = (max(0, x1 - PAD), max(0, y1 - PAD),
                min(puzzle.width, x2 + PAD), min(puzzle.height, y2 + PAD))
        cands.append(_enhance(puzzle.crop(wide), CAND_SCALE))

    result = {
        'puzzle_boxes': p_boxes,
        'hint_boxes': h_boxes,
        'puzzle_size': puzzle.size,
        'cands': cands,
        'hint': _enhance(hint, HINT_SCALE),
    }

    if workdir:
        os.makedirs(workdir, exist_ok=True)
        paths = {'hint': os.path.join(workdir, 'hint.png'), 'cands': []}
        result['hint'].save(paths['hint'])
        for i, img in enumerate(cands, 1):
            path = os.path.join(workdir, f'cand{i}.png')
            img.save(path)
            paths['cands'].append(path)
        sprite.save(os.path.join(workdir, 'sprite.png'))
        result['paths'] = paths

    return result


def assemble(prep: dict, hint_chars, cand_chars) -> dict:
    """把识别出的汉字拼回点击坐标.

    :param prep: `prepare()` 的返回值.
    :param hint_chars: 提示条里的汉字，**按从左到右**，即要求的点击顺序.
    :param cand_chars: 拼图里各候选的汉字，顺序与 `prep['puzzle_boxes']` 一致.
    :return: {"clicks": [(x,y),...] 拼图像素坐标，按点击顺序,
              "ratios": [(rx,ry),...] 0~1 相对坐标,
              "a":      点选载荷的 a 字段,
              "picked": [{"char","cand_index","box","center"},...],
              "missing": 提示里有、候选里没认出来的字}
    """
    from utils.geetest_w import encode_click_a_from_ratio

    boxes = prep['puzzle_boxes']
    width, height = prep['puzzle_size']
    if len(cand_chars) != len(boxes):
        raise ValueError(f'候选字数 {len(cand_chars)} 与检测框数 {len(boxes)} 不一致')

    # 同一个字可能在拼图里出现多次，用过就划掉，避免两个提示字指向同一个框
    remaining = list(enumerate(cand_chars))
    clicks, ratios, picked, missing = [], [], [], []

    for char in hint_chars:
        hit = next((pair for pair in remaining if pair[1] == char), None)
        if hit is None:
            missing.append(char)
            continue
        remaining.remove(hit)
        index = hit[0]
        x1, y1, x2, y2 = boxes[index]
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        clicks.append((cx, cy))
        ratios.append((cx / width, cy / height))
        picked.append({'char': char, 'cand_index': index + 1,
                       'box': boxes[index], 'center': (cx, cy)})

    return {
        'clicks': clicks,
        'ratios': ratios,
        'a': encode_click_a_from_ratio(ratios),
        'picked': picked,
        'missing': missing,
    }


def annotate(sprite: Image.Image, result: dict, path: str) -> str:
    """把点击点画回原图，用来肉眼确认认字和坐标没错位.

    :param sprite: 原始 sprite.
    :param result: `assemble()` 的返回值.
    :param path: 输出路径.
    :return: 输出路径.
    """
    from PIL import ImageDraw

    canvas = sprite.convert('RGB').copy()
    draw = ImageDraw.Draw(canvas)
    for step, item in enumerate(result['picked'], 1):
        x1, y1, x2, y2 = item['box']
        draw.rectangle((x1, y1, x2, y2), outline=(0, 255, 0), width=2)
        cx, cy = item['center']
        draw.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=(255, 0, 0))
        draw.text((x1 + 2, y1 - 12), f"{step}.{item['char']}", fill=(0, 255, 0))
    canvas.save(path)
    return path
