"""极验点选验证码的识别层.

极验 v3 点选题下发的是一张 sprite JPG：上半部 344x344 是拼图，底部 40px 是提示条。
关键性质（2026-08-15 实测确认）：**提示条里的字形与拼图里的字形是同一批渲染实例**，
畸变、旋转、笔画粗细完全一致，只是提示条为灰度、拼图为彩色描边叠在照片上。

所以识别不需要认字，只需要做形状匹配：
1. ddddocr 的检测模型给出两边的字符框；
2. 各自抠图 → 提取笔画掩码（拼图用饱和度阈值剥离照片背景，提示条用灰度阈值）；
3. 归一化到同尺寸后算相似度，用匈牙利算法做全局最优指派；
4. 按提示条从左到右的顺序输出拼图坐标，即点击顺序。
"""

import numpy as np
from PIL import Image, ImageFilter

PUZZLE_SIZE = 344
HINT_HEIGHT = 40
NORM = 48

_det = None


def _detector():
    global _det
    if _det is None:
        import ddddocr
        _det = ddddocr.DdddOcr(det=True, show_ad=False)
    return _det


def split_sprite(sprite: Image.Image):
    """把 sprite 切成拼图和提示条.

    :param sprite: 344x384 的原图.
    :return: (puzzle, hint) 两个 PIL Image.
    """
    width, height = sprite.size
    puzzle = sprite.crop((0, 0, width, height - HINT_HEIGHT))
    hint = sprite.crop((0, height - HINT_HEIGHT, width, height))
    return puzzle, hint


def detect_boxes(image: Image.Image) -> list:
    """检测字符框，按 x 从左到右排序.

    :param image: PIL Image.
    :return: [[x1,y1,x2,y2], ...].
    """
    import io

    buf = io.BytesIO()
    image.save(buf, format='PNG')
    boxes = _detector().detection(buf.getvalue())
    return sorted([list(map(int, b)) for b in boxes], key=lambda b: b[0])


def _edges(crop: Image.Image) -> np.ndarray:
    """笔画提取：描边是强边缘，用 Sobel 梯度幅值比颜色阈值稳得多.

    拼图里的字是彩色描边叠在照片上，饱和度阈值在低对比背景（红描边压粉色天空）
    会整块丢失；梯度对背景亮度不敏感，能同时吃住彩色描边和灰度提示字。
    """
    gray = np.asarray(crop.convert('L'), dtype=np.float32)
    gx = np.zeros_like(gray)
    gy = np.zeros_like(gray)
    gx[:, 1:-1] = gray[:, 2:] - gray[:, :-2]
    gy[1:-1, :] = gray[2:, :] - gray[:-2, :]
    mag = np.hypot(gx, gy)
    peak = np.percentile(mag, 88)
    return mag / peak if peak > 0 else mag


def _normalize(field: np.ndarray) -> np.ndarray:
    """裁到笔画外接框、缩放到统一尺寸、再高斯模糊.

    模糊很关键：两边字形虽然是同一实例，但经过不同缩放/JPEG 压缩后
    像素级不会对齐，模糊后互相关才对小错位有容忍度。
    """
    strong = field > 0.55
    rows, cols = np.any(strong, axis=1), np.any(strong, axis=0)
    if rows.any() and cols.any():
        y1, y2 = np.where(rows)[0][[0, -1]]
        x1, x2 = np.where(cols)[0][[0, -1]]
        field = field[y1:y2 + 1, x1:x2 + 1]
    img = Image.fromarray(np.clip(field * 255, 0, 255).astype(np.uint8))
    img = img.resize((NORM, NORM), Image.BILINEAR).filter(ImageFilter.GaussianBlur(1.6))
    return np.asarray(img, dtype=np.float32) / 255.0


def _similarity(a: np.ndarray, b: np.ndarray) -> float:
    """归一化互相关，两者都减均值后做余弦相似度."""
    av, bv = a.ravel() - a.mean(), b.ravel() - b.mean()
    denom = np.linalg.norm(av) * np.linalg.norm(bv)
    return float(av.dot(bv) / denom) if denom else 0.0


def _rotations(field: np.ndarray, angles) -> list:
    """产出若干旋转版本，用于旋转不敏感匹配.

    提示条与拼图虽是同一字形实例，但摆放角度不保证一致，
    固定角度的互相关会把真匹配压到和噪声同一水平。
    """
    base = Image.fromarray(np.clip(field * 255, 0, 255).astype(np.uint8))
    out = []
    for angle in angles:
        rot = base if angle == 0 else base.rotate(angle, resample=Image.BILINEAR, fillcolor=0)
        out.append(np.asarray(rot, dtype=np.float32) / 255.0)
    return out


def _best_similarity(hint_feat: np.ndarray, puzzle_rots: list) -> float:
    """取所有旋转角下的最高相似度."""
    return max(_similarity(hint_feat, rot) for rot in puzzle_rots)


def _split_overlaps(boxes: list) -> list:
    """提示条里的字框会互相重叠，按相邻框中点切开，避免抠进邻字笔画."""
    fixed = [list(b) for b in boxes]
    for i in range(len(fixed) - 1):
        right, left = fixed[i][2], fixed[i + 1][0]
        if right > left:
            mid = (right + left) // 2
            fixed[i][2] = mid
            fixed[i + 1][0] = mid
    return fixed


def solve(sprite: Image.Image) -> dict:
    """给出点击顺序.

    :param sprite: 极验下发的 sprite 原图.
    :return: {"order": [(x,y), ...] 拼图坐标(相对拼图左上角),
              "scores": 匹配得分, "hint_boxes":..., "puzzle_boxes":...}
    """
    from scipy.optimize import linear_sum_assignment

    puzzle, hint = split_sprite(sprite)
    p_boxes = detect_boxes(puzzle)
    h_boxes = _split_overlaps(detect_boxes(hint))

    angles = range(-10, 11, 5)
    p_rots = [_rotations(_normalize(_edges(puzzle.crop(tuple(b)))), angles) for b in p_boxes]
    h_feats = [_normalize(_edges(hint.crop(tuple(b)))) for b in h_boxes]

    score = np.zeros((len(h_feats), len(p_boxes)))
    for i, hf in enumerate(h_feats):
        for j, rots in enumerate(p_rots):
            score[i, j] = _best_similarity(hf, rots)

    rows, cols = linear_sum_assignment(-score)
    order, picked = [], []
    for i, j in sorted(zip(rows.tolist(), cols.tolist())):
        box = p_boxes[j]
        order.append(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2))
        picked.append({'hint': i, 'puzzle': j, 'score': round(float(score[i, j]), 4)})

    return {
        'order': order,
        'match': picked,
        'score_matrix': score.round(3).tolist(),
        'hint_boxes': h_boxes,
        'puzzle_boxes': p_boxes,
        'puzzle_size': puzzle.size,
    }
