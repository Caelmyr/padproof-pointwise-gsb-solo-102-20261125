import io
import tempfile
import unittest
import zipfile

import numpy as np
import cv2

from padproof import demo, inspection, reporting, splitting
from padproof.workspace import Workspace


def foreground_grid(rows):
    """Build a boolean foreground image from a list of '#' / '.' strings."""
    height = len(rows)
    width = max(len(row) for row in rows)
    grid = np.zeros((height, width), dtype=bool)
    for y, row in enumerate(rows):
        for x, char in enumerate(row):
            grid[y, x] = char == '#'
    return grid


class SideBySideTests(unittest.TestCase):
    def test_accepts_overlapping_pair_with_exclusive_bands(self):
        left, right = splitting.side_by_side([10, 4, 30, 20], [24, 6, 44, 22])
        self.assertEqual(left, [10, 4, 30, 20])
        self.assertEqual(right, [24, 6, 44, 22])

    def test_normalises_request_order(self):
        left, right = splitting.side_by_side([24, 6, 44, 22], [10, 4, 30, 20])
        self.assertEqual(left, [10, 4, 30, 20])

    def test_rejects_disjoint_windows(self):
        with self.assertRaises(inspection.Invalid):
            splitting.side_by_side([0, 0, 8, 8], [9, 0, 17, 8])

    def test_rejects_fully_contained_window(self):
        with self.assertRaises(inspection.Invalid):
            splitting.side_by_side([0, 0, 40, 20], [4, 2, 36, 18])

    def test_rejects_vertical_stacking_without_exclusive_columns(self):
        # Same horizontal span, stacked vertically: no left/right exclusive band.
        with self.assertRaises(inspection.Invalid):
            splitting.side_by_side([0, 0, 20, 10], [0, 8, 20, 18])


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        # 14 columns x 12 rows. Left box cols 1..8, right box cols 6..13.
        # Overlap strip is columns 6..7 (half-open); exclusive bands on each side.
        self.rect_left = [1, 1, 8, 11]
        self.rect_right = [6, 1, 13, 11]

    def test_exclusive_paste_stays_with_owner(self):
        rows = ['.' * 14 for _ in range(12)]
        rows[2] = '.##..........'   # left exclusive only
        rows[3] = '..........###.'   # right exclusive only
        foreground = foreground_grid(rows)
        owner, _, _ = splitting.apply_pair(foreground, self.rect_left, self.rect_right)
        self.assertTrue(np.all(owner[foreground & (owner == splitting.OWN_LEFT)]))
        self.assertEqual(int((owner == splitting.OWN_LEFT).sum()), 2)
        self.assertEqual(int((owner == splitting.OWN_RIGHT).sum()), 3)
        self.assertEqual(int((owner == splitting.OWN_REVIEW).sum()), 0)

    def test_single_sided_tongue_goes_to_reachable_pad(self):
        # Deposit starts in left exclusive area and reaches into the overlap,
        # but never touches the right exclusive area.
        rows = ['.' * 14 for _ in range(12)]
        rows[5] = '.#####.......'
        foreground = foreground_grid(rows)
        owner, _, _ = splitting.apply_pair(foreground, self.rect_left, self.rect_right)
        paste_x = [x for x, char in enumerate(rows[5]) if char == '#']
        self.assertTrue(all(owner[5, x] == splitting.OWN_LEFT for x in paste_x))
        self.assertEqual(int((owner == splitting.OWN_RIGHT).sum()), 0)

    def test_orphan_island_in_overlap_is_manual_review(self):
        rows = ['.' * 14 for _ in range(12)]
        rows[6] = '......##.....'   # columns 6,7 live only inside the overlap
        foreground = foreground_grid(rows)
        owner, _, _ = splitting.apply_pair(foreground, self.rect_left, self.rect_right)
        self.assertTrue(np.all(owner[6, 6:8] == splitting.OWN_REVIEW))
        self.assertEqual(int((owner == splitting.OWN_LEFT).sum()), 0)
        self.assertEqual(int((owner == splitting.OWN_RIGHT).sum()), 0)

    def test_bridge_tongue_is_split_once_and_kept_connected(self):
        # Solid paste tongue spanning both exclusive regions across every row.
        rows = []
        for y in range(12):
            line = list('.' * 14)
            if 1 <= y <= 10:
                for x in range(2, 12):
                    line[x] = '#'
            rows.append(''.join(line))
        foreground = foreground_grid(rows)
        owner, seam, overlap = splitting.apply_pair(foreground, self.rect_left, self.rect_right)
        # Every paste pixel gets exactly one owner; nothing is double counted.
        paste = foreground & (owner != splitting.OWN_NONE)
        review = foreground & (owner == splitting.OWN_REVIEW)
        self.assertEqual(int(paste.sum()), int(foreground.sum()))
        self.assertEqual(int(review.sum()), 0)
        self.assertEqual(len(seam), overlap[3] - overlap[1])
        # Seam is 4-connected: adjacent rows move by at most one pixel.
        self.assertLessEqual(int(np.max(np.abs(np.diff(seam)))), 1)
        # Each owned region still reaches its exclusive area via same-owner paste.
        overlap_region = splitting.region_mask(foreground.shape, [6, 1, 8, 11])
        for code, rect in ((splitting.OWN_LEFT, self.rect_left),
                           (splitting.OWN_RIGHT, self.rect_right)):
            region = splitting.region_mask(foreground.shape, rect)
            exclusive = region & ~overlap_region
            count, labels = cv2.connectedComponents(
                ((owner == code) & foreground).astype(np.uint8), connectivity=4)
            seed_labels = set(np.unique(labels[exclusive & (labels > 0)]).tolist())
            owned_labels = set(np.unique(labels[(owner == code) & (labels > 0)]).tolist())
            self.assertTrue(owned_labels and owned_labels <= seed_labels)

    def test_seam_prefers_background_gap_then_centre(self):
        # Two rows: the upper row has a solid tongue, the lower row leaves a
        # background gap at the centre column. The seam must take the free gap.
        rows = []
        for y in range(12):
            line = list('.' * 14)
            if 1 <= y <= 10:
                for x in range(2, 12):
                    line[x] = '#'
            rows.append(''.join(line))
        rows[6] = '..####.####..'  # column 7 is background, inside the overlap
        foreground = foreground_grid(rows)
        _, seam, overlap = splitting.apply_pair(foreground, self.rect_left, self.rect_right)
        self.assertEqual(seam[6 - overlap[1]], 7)

    def test_solid_tongue_seam_hugs_centre_line(self):
        rows = []
        for y in range(12):
            line = list('.' * 14)
            if 1 <= y <= 10:
                for x in range(2, 12):
                    line[x] = '#'
            rows.append(''.join(line))
        foreground = foreground_grid(rows)
        _, seam, overlap = splitting.apply_pair(foreground, self.rect_left, self.rect_right)
        centre = (overlap[0] + overlap[2]) / 2
        # A fully solid strip has no cheaper background route, so every row
        # should sit on the centre-line boundary (x == overlap right edge here).
        self.assertTrue(all(abs(x - centre) <= 1 for x in seam))

    def test_avoiding_solid_paste_outranks_centre_preference(self):
        # Background corridor near the overlap edge versus a fully solid centre.
        rows = []
        for y in range(12):
            line = list('.' * 14)
            if 1 <= y <= 10:
                for x in range(2, 12):
                    line[x] = '#'
            rows.append(''.join(line))
        for y in range(1, 11):
            rows[y] = rows[y][:7] + '.' + rows[y][8:]  # free vertical corridor at x=7
        foreground = foreground_grid(rows)
        _, seam_raw, overlap = splitting.apply_pair(foreground, self.rect_left, self.rect_right)
        seam = np.asarray(seam_raw)
        seam_rows = seam[1 - overlap[1]:11 - overlap[1]]
        # The seam follows the free corridor even though it hugs the strip edge.
        self.assertTrue(np.all(seam_rows == 7))
        # And no solid paste edge is cut along the corridor.
        solid_cuts = sum(
            1 for r, y in enumerate(range(overlap[1], overlap[3]))
            if foreground[y, seam[r] - 1] and foreground[y, seam[r]])
        self.assertEqual(solid_cuts, 0)

    def test_excluded_pixels_break_connectivity(self):
        rows = []
        for y in range(12):
            line = list('.' * 14)
            if 1 <= y <= 10:
                for x in range(2, 12):
                    line[x] = '#'
            rows.append(''.join(line))
        foreground = foreground_grid(rows)
        # Knock out the overlap columns entirely: the remaining right-side
        # deposit can no longer walk back to the left exclusive region.
        foreground[:, 6:8] = False
        owner, _, _ = splitting.apply_pair(foreground, self.rect_left, self.rect_right)
        right_only = foreground & splitting.region_mask(foreground.shape, self.rect_right)
        self.assertTrue(np.all(owner[right_only] == splitting.OWN_RIGHT))

    def test_stored_seam_reapplies_identically(self):
        rows = []
        for y in range(12):
            line = list('.' * 14)
            if 1 <= y <= 10:
                for x in range(2, 12):
                    line[x] = '#'
            rows.append(''.join(line))
        foreground = foreground_grid(rows)
        owner_a, seam, _ = splitting.apply_pair(foreground, self.rect_left, self.rect_right)
        owner_b, _, _ = splitting.apply_pair(foreground, self.rect_left, self.rect_right, seam)
        np.testing.assert_array_equal(owner_a, owner_b)

    def test_request_order_does_not_change_ownership(self):
        rows = ['.' * 14 for _ in range(12)]
        rows[4] = '.###.........'   # left exclusive deposit
        rows[7] = '..........###.'   # right exclusive deposit
        foreground = foreground_grid(rows)
        owner_direct, seam, _ = splitting.apply_pair(foreground, self.rect_left, self.rect_right)
        owner_reversed, _, _ = splitting.apply_pair(foreground, self.rect_right, self.rect_left, seam)
        np.testing.assert_array_equal(owner_direct, owner_reversed)
        self.assertEqual(int((owner_direct == splitting.OWN_LEFT).sum()), 3)
        self.assertEqual(int((owner_direct == splitting.OWN_RIGHT).sum()), 3)


class WorkspaceFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(self.tmp.name)
        self.board = self.workspace.add('P-06', demo.image(), synthetic=True)
        self.key = self.board['id']
        self.by_name = {pad['name']: pad for pad in self.board['pads']}

    def tearDown(self):
        self.tmp.cleanup()

    def _split_demo_pairs(self):
        j1, j2 = self.by_name['J1']['id'], self.by_name['J2']['id']
        j3, j4 = self.by_name['J3']['id'], self.by_name['J4']['id']
        board, split_12 = self.workspace.create_split(
            self.key, {'revision': self.board['revision'], 'left': j1, 'right': j2})
        board, split_34 = self.workspace.create_split(
            self.key, {'revision': board['revision'], 'left': j4, 'right': j3})
        return board, (split_12, split_34)

    def test_demo_pair_eliminates_double_count_and_flags_island(self):
        board, _ = self._split_demo_pairs()
        rows = {row['name']: row for row in self.workspace.rows(board)}
        # J1/J2 paste is fully assignable; J3/J4 keeps the disconnected island.
        self.assertEqual(rows['J1']['review_px'], 0)
        self.assertEqual(rows['J2']['review_px'], 0)
        self.assertGreater(rows['J3']['review_px'], 0)
        self.assertEqual(rows['J3']['review_px'], rows['J4']['review_px'])
        # Union paste equals assigned paste plus the once-counted review island.
        pixels = self.workspace.pixels(self.key)
        luminance, useful = inspection.prepare(pixels, board['exclusions'])
        foreground = (luminance >= board['settings']['threshold']) & useful
        for left_name, right_name in (('J1', 'J2'), ('J3', 'J4')):
            left_rect = self.by_name[left_name]['rect']
            right_rect = self.by_name[right_name]['rect']
            region = (splitting.region_mask(foreground.shape, left_rect)
                      | splitting.region_mask(foreground.shape, right_rect))
            total = int((foreground & region).sum())
            counted = rows[left_name]['paste_px'] + rows[right_name]['paste_px']
            reviewed = rows[left_name]['review_px']
            self.assertEqual(total, counted + reviewed)

    def test_review_package_is_reproducible_after_later_changes(self):
        board, _ = self._split_demo_pairs()
        review = self.workspace.save(self.key, board['revision'])
        package = reporting.build_package(
            self.workspace.source(self.key).read_bytes(),
            self.workspace.pixels(self.key), review)
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            names = archive.namelist()
            self.assertIn('inspection.csv', names)
            self.assertTrue(any(name.startswith('review-masks/') for name in names))
            for pad in review['pads']:
                self.assertIn(f"masks/{pad['id']}.png", names)
        # Change the threshold afterwards; the saved review must replay unchanged.
        changed = self.workspace.configure(self.key, {
            **board['settings'], 'revision': board['revision'], 'threshold': 90})
        live = {row['name']: row for row in self.workspace.rows(changed)}
        saved = {row['name']: row for row in review['rows']}
        self.assertNotEqual(live['J1']['paste_px'], saved['J1']['paste_px'])
        replayed = inspection.inspect(
            self.workspace.pixels(self.key), review['pads'], review['exclusions'],
            review['settings'], self.workspace._pad_splits(review))
        self.assertEqual(
            [(row['paste_px'], row['review_px'], row['coverage']) for row in review['rows']],
            [(row['paste_px'], row['review_px'], row['coverage']) for row in replayed])

    def test_same_pair_cannot_be_split_twice(self):
        j1, j2 = self.by_name['J1']['id'], self.by_name['J2']['id']
        board, _ = self.workspace.create_split(
            self.key, {'revision': self.board['revision'], 'left': j1, 'right': j2})
        with self.assertRaises(inspection.Invalid):
            self.workspace.create_split(
                self.key, {'revision': board['revision'], 'left': j1, 'right': j2})

    def test_pad_cannot_join_two_pairs(self):
        j1, j2, j3 = (self.by_name[name]['id'] for name in ('J1', 'J2', 'J3'))
        board, _ = self.workspace.create_split(
            self.key, {'revision': self.board['revision'], 'left': j1, 'right': j2})
        with self.assertRaises(inspection.Invalid):
            self.workspace.create_split(
                self.key, {'revision': board['revision'], 'left': j1, 'right': j3})

    def test_deleting_split_restores_independent_measurement(self):
        j1, j2 = self.by_name['J1']['id'], self.by_name['J2']['id']
        board, split = self.workspace.create_split(
            self.key, {'revision': self.board['revision'], 'left': j1, 'right': j2})
        split_rows = {row['name']: row for row in self.workspace.rows(board)}
        board = self.workspace.delete_split(
            self.key, {'revision': board['revision'], 'split': split['id']})
        plain_rows = {row['name']: row for row in self.workspace.rows(board)}
        self.assertEqual(plain_rows['J1']['review_px'], 0)
        self.assertGreaterEqual(plain_rows['J1']['paste_px'], split_rows['J1']['paste_px'])
        self.assertGreaterEqual(plain_rows['J2']['paste_px'], split_rows['J2']['paste_px'])

    def test_editing_pad_geometry_prunes_invalid_split(self):
        j1, j2 = self.by_name['J1']['id'], self.by_name['J2']['id']
        board, split = self.workspace.create_split(
            self.key, {'revision': self.board['revision'], 'left': j1, 'right': j2})
        moved = self.by_name['J2']['rect'].copy()
        moved[0] += 220
        moved[2] += 220
        board = self.workspace.annotate(
            self.key, 'pads',
            {'revision': board['revision'], 'name': 'J2', 'rect': moved}, window=j2)
        self.assertFalse(any(item and item['id'] == split['id']
                             for item in board['splits']))


if __name__ == '__main__':
    unittest.main()
