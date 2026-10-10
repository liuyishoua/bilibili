"""极验点选的共享模型度量匹配。

检测框由 ddddocr 的 YOLO 模型给出。提示字和候选字分别通过同一个 OCR
模型，取 CTC 各时间步的字符概率最大值作为向量；两边向量做余弦相似度，
再用匈牙利算法求一一对应。即使 OCR 把同一个字都认错，只要错误分布相近，
仍能匹配到同一字形。
"""

import io

import numpy as np

from utils.geetest_ocr import _split_overlaps, detect_boxes, split_sprite

PAD = 4

_ocr = None


def _get_ocr():
    global _ocr
    if _ocr is None:
        import ddddocr
        _ocr = ddddocr.DdddOcr(show_ad=False)
    return _ocr


def _intersection_ratio(a, b) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area_a = max(1, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(1, (b[2] - b[0]) * (b[3] - b[1]))
    return inter / min(area_a, area_b)


def merge_duplicate_boxes(boxes: list, threshold: float = 0.25) -> list:
    """合并 YOLO 对同一个彩色字给出的重叠框。

    极验的描边、填充有时会被检测成两到三个嵌套框。按交集占较小框面积
    的比例建连通分量并取并集，避免把一个字当成多个候选。
    """
    groups = []
    for box in sorted((list(map(int, b)) for b in boxes), key=lambda b: b[0]):
        hits = [i for i, group in enumerate(groups)
                if any(_intersection_ratio(box, old) >= threshold for old in group)]
        if not hits:
            groups.append([box])
            continue
        target = hits[0]
        groups[target].append(box)
        for index in reversed(hits[1:]):
            groups[target].extend(groups.pop(index))

    merged = []
    for group in groups:
        merged.append([min(b[0] for b in group), min(b[1] for b in group),
                       max(b[2] for b in group), max(b[3] for b in group)])
    return sorted(merged, key=lambda b: b[0])


def _crop(image, box, pad: int):
    x1, y1, x2, y2 = box
    return image.crop((max(0, x1 - pad), max(0, y1 - pad),
                       min(image.width, x2 + pad), min(image.height, y2 + pad)))


def _signature(image):
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    result = _get_ocr().classification(buf.getvalue(), probability=True)
    vector = probability_vector(result['probabilities'])
    return result['text'], vector


def probability_vector(probabilities) -> np.ndarray:
    """把 ddddocr 的 CTC 输出压成单位字符概率向量。

    1.6.x 的主形态是 ``[time, batch=1, charset]``，旧版也可能返回
    ``[batch=1, time, charset]``；两种都兼容。
    """
    probabilities = np.asarray(probabilities, dtype=np.float32)
    if probabilities.ndim == 3:
        if probabilities.shape[1] == 1:
            probabilities = probabilities[:, 0, :]
        elif probabilities.shape[0] == 1:
            probabilities = probabilities[0, :, :]
        else:
            probabilities = probabilities[0, :, :]
    vector = probabilities.max(axis=0)
    vector[0] = 0.0  # CTC blank 对每张图都接近 1，没有区分度
    vector /= np.linalg.norm(vector) + 1e-9
    return vector


def solve(sprite) -> dict:
    """返回按提示顺序排列的点击坐标和模型相似度证据。"""
    from scipy.optimize import linear_sum_assignment

    puzzle, hint = split_sprite(sprite)
    raw_puzzle_boxes = detect_boxes(puzzle)
    puzzle_boxes = merge_duplicate_boxes(raw_puzzle_boxes)
    # 提示条相邻字会轻微相交，只合并高度嵌套的重复框，再切开相邻边界。
    hint_boxes = _split_overlaps(
        merge_duplicate_boxes(detect_boxes(hint), threshold=0.75))

    hint_samples = [_signature(_crop(hint, box, 2)) for box in hint_boxes]
    puzzle_samples = [_signature(_crop(puzzle, box, PAD)) for box in puzzle_boxes]
    if not hint_samples or len(puzzle_samples) < len(hint_samples):
        raise ValueError(
            f'检测框不足：提示 {len(hint_samples)}，候选 {len(puzzle_samples)}')

    score = np.asarray([[float(np.dot(hv, pv)) for _, pv in puzzle_samples]
                        for _, hv in hint_samples], dtype=np.float32)
    rows, cols = linear_sum_assignment(-score)

    order, matches = [], []
    for hint_index, puzzle_index in sorted(zip(rows.tolist(), cols.tolist())):
        box = puzzle_boxes[puzzle_index]
        order.append(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2))
        matches.append({
            'hint': hint_index,
            'puzzle': puzzle_index,
            'score': round(float(score[hint_index, puzzle_index]), 4),
        })

    warnings = []
    low = [m for m in matches if m['score'] < 0.20]
    if low:
        warnings.append(f'{len(low)} 个模型匹配低于 0.20，建议刷新题面')

    return {
        'order': order,
        'match': matches,
        'score_matrix': score.round(4).tolist(),
        'hint_text': [text for text, _ in hint_samples],
        'cand_chars': [text for text, _ in puzzle_samples],
        'ordered_chars': [puzzle_samples[m['puzzle']][0] for m in matches],
        'warnings': warnings,
        'hint_boxes': hint_boxes,
        'puzzle_boxes': puzzle_boxes,
        'raw_puzzle_boxes': raw_puzzle_boxes,
        'puzzle_size': puzzle.size,
    }
