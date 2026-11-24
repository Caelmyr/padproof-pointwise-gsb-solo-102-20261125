"""Download a self-contained revision of the inspection in original image coordinates."""

import csv
from io import BytesIO, StringIO
import json
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import numpy as np

from . import inspection, raster, splitting

FIELD_NAMES = ('id', 'name', 'x0', 'y0', 'x1', 'y1', 'usable_px', 'paste_px', 'review_px',
               'excluded_px', 'coverage', 'paste_mm2', 'state', 'deposits', 'largest_px',
               'holes', 'hole_px', 'overload_px')

# Grayscale values used inside the review-package masks.
MASK_PASTE = 255
MASK_REVIEW = 128


def table(rows):
    out = StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=FIELD_NAMES, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        coordinates = dict(zip(('x0', 'y0', 'x1', 'y1'), row['rect']))
        # Keep user-entered labels as text when the CSV is opened in a spreadsheet.
        name = row['name']
        if name.lstrip()[:1] in ('=', '+', '-', '@'):
            name = "'" + name
        writer.writerow({**row, **coordinates, 'name': name})
    return out.getvalue().encode('utf-8-sig')


def build_package(source_png, source_rgb, report):
    output = BytesIO()
    luminance, useful = inspection.prepare(source_rgb, report['exclusions'])
    settings = report['settings']
    foreground = (luminance >= settings['threshold']) & useful
    rectangles = {pad['id']: pad['rect'] for pad in report['pads']}
    with ZipFile(output, 'w', compression=ZIP_DEFLATED, compresslevel=3) as files:
        def add(name, data):
            files.writestr(ZipInfo(name), data, compress_type=ZIP_DEFLATED, compresslevel=3)

        add('source.png', source_png)
        add('inspection.csv', table(report['rows']))
        add('inspection.json', json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))

        split_pairs = {tuple(sorted((split['left'], split['right']))): split
                       for split in report.get('splits', [])}
        emitted_pairs = set()
        for pad in report['pads']:
            pair = next((key for key in split_pairs if pad['id'] in key), None)
            if pair is None:
                image = inspection.native_mask(luminance, useful, pad, settings)
            else:
                split = split_pairs[pair]
                left_id, right_id = pair
                left_mask, right_mask, review_mask = splitting.pair_masks(
                    foreground, rectangles[left_id], rectangles[right_id], split['seam'])
                owned = left_mask if pad['id'] == left_id else right_mask
                image = np.zeros(luminance.shape, dtype=np.uint8)
                image[owned] = MASK_PASTE
                image[review_mask] = MASK_REVIEW
                if pair not in emitted_pairs:
                    review_image = np.zeros(luminance.shape, dtype=np.uint8)
                    review_image[review_mask] = MASK_PASTE
                    add(f"review-masks/{split['id']}.png", raster.png(review_image))
                    emitted_pairs.add(pair)
            add(f"masks/{pad['id']}.png", raster.png(image))
    return output.getvalue()
