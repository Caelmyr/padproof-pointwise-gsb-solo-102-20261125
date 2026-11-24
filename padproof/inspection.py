"""Native-coordinate masks, connected deposits and coverage checks for pad windows."""

import cv2
import numpy as np

from image_lab.validation import Invalid, integer, number, text

from . import splitting
from .raster import gray

DEFAULT_SETTINGS = {'threshold': 143, 'low': 43.0, 'high': 83.0, 'scale': None}


def rect(value, size):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise Invalid('矩形需包含原图的四个坐标。')
    width, height = size
    left, top, right, bottom = (
        integer(raw, '矩形坐标', 0, maximum)
        for raw, maximum in zip(value, [width, height, width, height])
    )
    if right - left < 4 or bottom - top < 4:
        raise Invalid('检测框至少需要 4 × 4 原图像素。')
    return [left, top, right, bottom]


def label(value, size):
    return {'name': text(value.get('name'), '框名称', 24, False), 'rect': rect(value.get('rect'), size)}


def settings(value):
    scale = value.get('scale')
    clean_scale = None if scale is None else number(scale, '每毫米像素数', .01, 100000)
    low = number(value.get('low'), '待补阈值', 0, 100)
    high = number(value.get('high'), '超印阈值', low, 100)
    return {'threshold': integer(value.get('threshold'), '亮度阈值', 0, 255),
            'low': low, 'high': high, 'scale': clean_scale}


def local_slice(rectangle):
    left, top, right, bottom = rectangle
    return np.s_[top:bottom, left:right]


def prepare(source, exclusions):
    luminance = gray(source)
    useful = np.ones(luminance.shape, dtype=bool)
    for exclusion in exclusions:
        useful[local_slice(exclusion['rect'])] = False
    return luminance, useful


def window(luminance, useful, pad, threshold):
    section = local_slice(pad['rect'])
    patch = luminance[section]
    valid = useful[section]
    return patch, valid, (patch >= threshold) & valid


def components(mask):
    count, _, stats, _ = cv2.connectedComponentsWithStats(np.ascontiguousarray(mask, dtype=np.uint8), connectivity=4)
    return count - 1, int(max(stats[1:, cv2.CC_STAT_AREA], default=0))


def holes(paste, useful):
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        np.ascontiguousarray(~paste, dtype=np.uint8), connectivity=4)
    edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1], labels[~useful]]))
    included = np.setdiff1d(np.arange(1, count), edge, assume_unique=True)
    return len(included), int(stats[included, cv2.CC_STAT_AREA].sum())


def verdict(coverage, config):
    if coverage is None:
        return 'unreadable'
    if coverage < config['low']:
        return 'under'
    if coverage > config['high']:
        return 'over'
    return 'pass'


def measure(patch, valid, foreground, config, review=None):
    pixels = int(valid.sum())
    paste = int(foreground.sum())
    coverage = 100 * paste / pixels if pixels else None
    deposits, largest = components(foreground)
    cavities, cavity_pixels = holes(foreground, valid)
    scale = config['scale']
    return {'usable_px': pixels, 'paste_px': paste, 'excluded_px': int(valid.size - pixels),
            'review_px': 0 if review is None else int(review.sum()),
            'coverage': round(coverage, 4) if coverage is not None else None,
            'paste_mm2': round(paste / scale ** 2, 5) if scale is not None else None,
            'state': verdict(coverage, config), 'deposits': deposits, 'largest_px': largest,
            'holes': cavities, 'hole_px': cavity_pixels,
            'overload_px': int(np.count_nonzero((patch >= 248) & valid & foreground))}


def valid_split(splits, pad_index, pad_id, rectangles):
    """Check a stored split still matches two existing side-by-side windows."""
    split = splits[pad_index]
    if split is None:
        return None
    partner = split.get('partner')
    if partner not in rectangles or pad_id not in rectangles:
        return None
    try:
        splitting.side_by_side(rectangles[pad_id], rectangles[partner])
    except Invalid:
        return None
    return split


def inspect(source, pads, exclusions, config, splits=None):
    luminance, useful = prepare(source, exclusions)
    threshold = config['threshold']
    rows = []
    if not splits:
        for pad in pads:
            patch, valid, foreground = window(luminance, useful, pad, threshold)
            rows.append({**pad, **measure(patch, valid, foreground, config)})
        return rows

    full_foreground = (luminance >= threshold) & useful
    rectangles = {pad['id']: pad['rect'] for pad in pads}
    handled = {}
    review_masks = {}
    for index, split in enumerate(splits):
        split = valid_split(splits, index, pads[index]['id'], rectangles)
        if split is None:
            continue
        partner_id = split['partner']
        if index in handled:
            continue
        partner_index = next(i for i, pad in enumerate(pads) if pad['id'] == partner_id)
        if partner_index in handled:
            continue
        left_mask, right_mask, review_mask = splitting.pair_masks(
            full_foreground, rectangles[pads[index]['id']], rectangles[partner_id], split['seam'])
        left_index = index if split['left'] == pads[index]['id'] else partner_index
        right_index = partner_index if left_index == index else index
        handled[left_index] = left_mask
        handled[right_index] = right_mask
        review_masks[left_index] = review_mask
        review_masks[right_index] = review_mask

    for index, pad in enumerate(pads):
        section = local_slice(pad['rect'])
        patch = luminance[section]
        valid = useful[section]
        if index in handled:
            foreground = handled[index][section]
            review = review_masks[index][section]
        else:
            foreground = full_foreground[section] & valid
            review = None
        rows.append({**pad, **measure(patch, valid, foreground, config, review)})
    return rows


def native_mask(luminance, useful, pad, config):
    _, _, foreground = window(luminance, useful, pad, config['threshold'])
    mask = np.zeros(luminance.shape, dtype=np.uint8)
    mask[local_slice(pad['rect'])] = foreground.astype(np.uint8) * 255
    return mask


def split_mask(luminance, useful, pads, splits, pad_index, config):
    """Colour-coded full-image mask: white owned paste, amber review islands."""
    pad = pads[pad_index]
    split = splits[pad_index]
    partner = next(item for item in pads if item['id'] == split['partner'])
    foreground = (luminance >= config['threshold']) & useful
    left_mask, right_mask, review_mask = splitting.pair_masks(
        foreground, pad['rect'], partner['rect'], split['seam'])
    image = np.zeros((*luminance.shape, 3), dtype=np.uint8)
    owned = left_mask if split['left'] == pad['id'] else right_mask
    image[owned] = (255, 255, 255)
    image[review_mask] = (238, 193, 124)
    return image, owned, review_mask


def pixel(source, record, xy):
    if not isinstance(xy, (list, tuple)) or len(xy) != 2:
        raise Invalid('请提供一个原图像素坐标。')
    x, y = [integer(raw, '像素位置', 0, end - 1) for raw, end in zip(xy, record['size'])]
    config = record['settings']
    luminance = int(gray(source[y:y+1, x:x+1])[0, 0])

    def names(items):
        return [item['name'] for item in items
                if item['rect'][0] <= x < item['rect'][2] and item['rect'][1] <= y < item['rect'][3]]

    blocked = names(record['exclusions'])
    return {'x': x, 'y': y, 'rgb': source[y, x].tolist(), 'luminance': luminance,
            'windows': names(record['pads']), 'exclusions': blocked,
            'threshold_match': not blocked and luminance >= config['threshold']}
