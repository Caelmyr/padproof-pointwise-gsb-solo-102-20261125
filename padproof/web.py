"""Browser entry points for inspecting and archiving a printed circuit board proof."""

from io import BytesIO
from pathlib import Path

import numpy as np
from flask import Blueprint, jsonify, render_template, request, send_file
from werkzeug.exceptions import NotFound

from image_lab import create_app as preparation_app
from image_lab.validation import Invalid, object_value

from . import demo, inspection, raster, reporting
from .workspace import Workspace, find


def body():
    return object_value(request.get_json(silent=True), '板面操作')


def create_app(root):
    app = preparation_app(root / 'prepare')
    workspace = Workspace(root)
    api = Blueprint('pad', __name__, url_prefix='/pad')

    @app.get('/')
    def home():
        return render_template('padproof.html')

    @api.get('/health')
    def health():
        return jsonify(status='ready', service='padproof')

    @api.get('/boards')
    def list_boards():
        return jsonify(workspace.boards())

    @api.post('/demo')
    def practice_board():
        return jsonify(workspace.add('P-06 连接器试印 · 合成图', demo.image(), synthetic=True)), 201

    @api.post('/boards')
    def add_board():
        upload = request.files.get('image')
        if upload is None or not upload.filename:
            raise Invalid('请选择一张显微板面照片。')
        name = Path(upload.filename).name[:120]
        pixels = raster.decode(upload.stream.read(raster.MAX_BYTES + 1))
        return jsonify(workspace.add(request.form.get('name') or Path(name).stem[:84],
                                     pixels, filename=name)), 201

    @api.get('/boards/<key>')
    def read_board(key):
        return jsonify(workspace.board(key))

    @api.get('/boards/<key>/source.png')
    def source(key):
        return send_file(workspace.source(key), mimetype='image/png', max_age=0)

    @api.patch('/boards/<key>/settings')
    def settings(key):
        return jsonify(workspace.configure(key, body()))

    @api.post('/boards/<key>/<kind>')
    @api.route('/boards/<key>/<kind>/<item>', methods=['PATCH', 'DELETE'])
    def annotations(key, kind, item=None):
        if kind not in ('pads', 'exclusions'):
            raise NotFound()
        return jsonify(workspace.annotate(key, kind, body(), item, request.method == 'DELETE')), (
            201 if request.method == 'POST' else 200)

    @api.get('/boards/<key>/measurements')
    def measurements(key):
        record = workspace.board(key)
        return jsonify(revision=record['revision'], rows=workspace.rows(record))

    @api.get('/boards/<key>/pixel')
    def pixel(key):
        xy = [request.args.get('x', type=int), request.args.get('y', type=int)]
        return jsonify(inspection.pixel(workspace.pixels(key), workspace.board(key), xy))

    @api.get('/boards/<key>/mask/<pad>.png')
    def mask(key, pad):
        record = workspace.board(key)
        window = find(record['pads'], pad)
        luminance, useful = inspection.prepare(workspace.pixels(key), record['exclusions'])
        data = inspection.native_mask(luminance, useful, window, record['settings'])
        return send_file(BytesIO(raster.png(data)), mimetype='image/png', max_age=0)

    @api.route('/boards/<key>/reviews', methods=['GET', 'POST'])
    def reviews(key):
        if request.method == 'GET':
            return jsonify(workspace.reviews(key))
        return jsonify(workspace.save(key, body().get('revision'))), 201

    @api.get('/boards/<key>/reviews/<review>')
    def read_review(key, review):
        return jsonify(workspace.review(key, review))

    @api.get('/boards/<key>/reviews/<review>/package.zip')
    def download_review(key, review):
        record = workspace.review(key, review)
        data = reporting.build_package(workspace.source(key).read_bytes(), workspace.pixels(key), record)
        return send_file(BytesIO(data), mimetype='application/zip', as_attachment=True,
                         download_name=f"padproof-{record['board_revision']}-{record['id'][:8]}.zip",
                         max_age=0)

    @api.post('/boards/<key>/prepare')
    def transfer(key):
        record = workspace.board(key)
        pixels = workspace.pixels(key).astype(np.float32) / 255
        asset = app.extensions['assets'].put(pixels, record['name'], {'padproof_source_sha256': record['source_sha256']})
        draft = app.extensions['store'].create('板面准备 · ' + record['name'])
        project = app.extensions['frames'].add(draft['id'], draft['revision'], [asset['id']])['project']
        return jsonify(url=f"/prepare?project={project['id']}"), 201

    app.register_blueprint(api)
    return app
