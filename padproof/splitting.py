"""Split overlapping side-by-side pad windows so every paste pixel is owned once.

Two overlapping tolerance boxes share an edge strip.  Paste inside each box's
exclusive region stays with that box.  Paste inside the shared strip is assigned
with a 4-neighbour connectivity rule: a deposit that only reaches one exclusive
region belongs to that pad, deposits reaching neither are flagged for manual
review and a paste tongue reaching both is cut by a minimum-cost vertical seam.
The seam first avoids cutting through solid paste and, as a tie breaker, stays
close to the centre of the shared strip.
"""

import cv2
import numpy as np

from image_lab.validation import Invalid

# Weight of the centre-line preference relative to one solid-paste edge cut.
CENTRE_WEIGHT = 0.5
OWN_NONE = 0
OWN_LEFT = 1
OWN_RIGHT = 2
OWN_REVIEW = 3


def side_by_side(rect_a, rect_b):
    """Return ``(left, right)`` rectangles for a horizontally overlapping pair."""
    left_a, top_a, right_a, bottom_a = rect_a
    left_b, top_b, right_b, bottom_b = rect_b
    horizontal_overlap = max(0, min(right_a, right_b) - max(left_a, left_b))
    vertical_overlap = max(0, min(bottom_a, bottom_b) - max(top_a, top_b))
    if not horizontal_overlap or not vertical_overlap:
        raise Invalid('分盘需要两个互相搭边的焊盘框。')
    # A side-by-side pair must keep an exclusive column band on each side.
    if horizontal_overlap >= min(right_a - left_a, right_b - left_b):
        raise Invalid('两个框需要左右并排，并各自保留不搭边的独占区域。')
    if left_a <= left_b:
        return list(rect_a), list(rect_b)
    return list(rect_b), list(rect_a)


def region_mask(shape, rectangle):
    mask = np.zeros(shape, dtype=bool)
    left, top, right, bottom = rectangle
    mask[top:bottom, left:right] = True
    return mask


def compute_seam(tongue, overlap):
    """Minimum-cost 4-connected seam (one x per overlap row) across the strip."""
    left, top, right, bottom = overlap
    height, width = tongue.shape
    columns = np.arange(left, right)
    centre = (left + right) / 2.0
    row_count = bottom - top

    solid_h = tongue[:, left - 1:right - 1] & tongue[:, left:right]
    centre_term = CENTRE_WEIGHT * np.abs(columns.astype(np.float64) - centre) / width
    horizontal = np.where(solid_h[top:bottom], 1.0, 0.0) + centre_term[np.newaxis, :]

    # Vertical edges crossed when the seam shifts between adjacent rows.
    if top > 0:
        upper_cells = tongue[top - 1:bottom - 1, left:right - 1]
        lower_cells = tongue[top:bottom, left:right - 1]
        solid_v = upper_cells & lower_cells
    else:
        solid_v = np.zeros((max(row_count - 1, 0), max(right - left - 1, 0)), dtype=bool)
    edge_x = columns[:-1] + 1
    vertical_term = CENTRE_WEIGHT * np.abs(edge_x.astype(np.float64) - centre) / width
    vertical_cost = np.where(solid_v, 1.0, 0.0) + vertical_term[np.newaxis, :]

    previous = horizontal[0].copy()
    backtrack = np.zeros((row_count, len(columns)), dtype=np.int8)
    for row in range(1, row_count):
        stay = previous
        shift_right = np.concatenate((previous[:-1] + vertical_cost[row - 1], [np.inf]))
        shift_left = np.concatenate(([np.inf], previous[1:] + vertical_cost[row - 1]))
        # Ties resolve to the straight move so the seam hugs the centre line.
        previous = np.minimum(stay, np.minimum(shift_right, shift_left)) + horizontal[row]
        backtrack[row] = np.where(
            shift_right < np.minimum(stay, shift_left), 1,
            np.where(shift_left < np.minimum(stay, shift_right), -1, 0))

    seam = np.empty(row_count, dtype=np.int32)
    j = int(np.argmin(previous))
    seam[-1] = columns[j]
    for row in range(row_count - 1, 0, -1):
        j += int(backtrack[row, j])
        seam[row - 1] = columns[j]
    return seam


def apply_pair(foreground, rect_left, rect_right, seam=None):
    """Build the ownership mask and seam for one side-by-side pad pair.

    ``foreground`` is the full-image boolean mask of thresholded, non-excluded
    paste.  Returns ``(owner, seam, overlap)`` where ``owner`` uses the
    ``OWN_*`` constants and ``seam`` stores the chosen boundary for reuse.
    """
    rect_left, rect_right = side_by_side(rect_left, rect_right)
    shape = foreground.shape
    mask_left = region_mask(shape, rect_left)
    mask_right = region_mask(shape, rect_right)
    overlap_mask = mask_left & mask_right
    exclusive_left = mask_left & ~overlap_mask
    exclusive_right = mask_right & ~overlap_mask
    overlap_rect = [int(v) for v in (np.nonzero(overlap_mask.any(axis=0))[0].min(),
                                     np.nonzero(overlap_mask.any(axis=1))[0].min(),
                                     np.nonzero(overlap_mask.any(axis=0))[0].max() + 1,
                                     np.nonzero(overlap_mask.any(axis=1))[0].max() + 1)]

    working = np.zeros(shape, dtype=np.uint8)
    working[(mask_left | mask_right) & foreground] = 1
    count, labels = cv2.connectedComponents(working, connectivity=4)
    reaches_left = np.zeros(count, dtype=bool)
    reaches_right = np.zeros(count, dtype=bool)
    if count > 1:
        reaches_left[np.unique(labels[exclusive_left & (labels > 0)])] = True
        reaches_right[np.unique(labels[exclusive_right & (labels > 0)])] = True

    only_left = reaches_left & ~reaches_right
    only_right = reaches_right & ~reaches_left
    orphan = ~reaches_left & ~reaches_right
    orphan[0] = False
    tongue_labels = reaches_left & reaches_right
    tongue = tongue_labels[labels]

    owner = np.zeros(shape, dtype=np.uint8)
    owner[only_left[labels]] = OWN_LEFT
    owner[only_right[labels]] = OWN_RIGHT
    owner[orphan[labels]] = OWN_REVIEW
    # Tongue paste outside the shared strip belongs to the box that contains it.
    owner[tongue & exclusive_left] = OWN_LEFT
    owner[tongue & exclusive_right] = OWN_RIGHT

    if seam is None:
        seam = compute_seam(tongue, overlap_rect)
    seam = np.asarray(seam, dtype=np.int32)
    if seam.ndim != 1 or seam.shape[0] != overlap_rect[3] - overlap_rect[1]:
        raise Invalid('保存的切缝与当前叠边尺寸不一致。')
    left, top, right, bottom = overlap_rect
    rows = np.arange(top, bottom)
    if np.any(seam < left) or np.any(seam > right):
        raise Invalid('保存的切缝超出了叠边范围。')
    for y, boundary in zip(rows, seam):
        strip = tongue[y, left:right]
        choices = np.where(strip, np.where(np.arange(left, right) < boundary, OWN_LEFT, OWN_RIGHT), 0)
        owner[y, left:right] = np.where(choices != 0, choices, owner[y, left:right])

    # Guarantee the connectivity rule: anything that cannot walk back through
    # same-owner paste to its exclusive region becomes a manual-review island.
    owner = _enforce_reachability(owner, foreground, exclusive_left, exclusive_right)
    return owner, seam.tolist(), overlap_rect


def _enforce_reachability(owner, foreground, exclusive_left, exclusive_right):
    owner = owner.copy()
    for code, exclusive in ((OWN_LEFT, exclusive_left), (OWN_RIGHT, exclusive_right)):
        owned = (owner == code) & foreground
        count, labels = cv2.connectedComponents(owned.astype(np.uint8), connectivity=4)
        if count <= 1:
            continue
        seed_labels = np.unique(labels[exclusive & (labels > 0)])
        reachable = np.isin(labels, seed_labels)
        owner[owned & ~reachable] = OWN_REVIEW
    return owner


def pair_masks(foreground, rect_left, rect_right, seam):
    """Reapply a stored seam and return the two per-pad paste masks plus review."""
    owner, _, _ = apply_pair(foreground, rect_left, rect_right, seam)
    return ((owner == OWN_LEFT) & foreground,
            (owner == OWN_RIGHT) & foreground,
            (owner == OWN_REVIEW) & foreground)
