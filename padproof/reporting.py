"""Download a self-contained revision of the inspection in original image coordinates."""

import csv
from io import BytesIO, StringIO
import json
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from . import inspection, raster

FIELD_NAMES = ('id', 'name', 'x0', 'y0', 'x1', 'y1', 'usable_px', 'paste_px', 'excluded_px',
               'coverage', 'paste_mm2', 'state', 'deposits', 'largest_px', 'holes', 'hole_px', 'overload_px')


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
    with ZipFile(output, 'w', compression=ZIP_DEFLATED, compresslevel=3) as files:
        def add(name, data):
            files.writestr(ZipInfo(name), data, compress_type=ZIP_DEFLATED, compresslevel=3)

        add('source.png', source_png)
        add('inspection.csv', table(report['rows']))
        add('inspection.json', json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
        for pad in report['pads']:
            image = inspection.native_mask(luminance, useful, pad, report['settings'])
            add(f"masks/{pad['id']}.png", raster.png(image))
    return output.getvalue()
