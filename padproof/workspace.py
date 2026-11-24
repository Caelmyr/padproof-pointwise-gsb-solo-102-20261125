"""Revisioned working copy and immutable review packs."""

from hashlib import sha256
import json
from pathlib import Path
from shutil import rmtree
from threading import RLock
from uuid import uuid4

from werkzeug.exceptions import NotFound

from image_lab.validation import Conflict, Invalid, identifier, integer, text, timestamp

from . import demo, inspection, raster


def read(path):
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        raise NotFound('找不到该板面或复核记录。') from None


def write(path, value):
    pending = path.with_name('.' + path.name + '-' + uuid4().hex)
    try:
        pending.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def find(items, key):
    key = identifier(key)
    for item in items:
        if item['id'] == key:
            return item
    raise NotFound('找不到这个检测框。')


class Workspace:
    def __init__(self, location):
        self.root = Path(location) / 'boards'
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()

    def directory(self, key):
        return self.root / identifier(key)

    def board(self, key):
        return read(self.directory(key) / 'board.json')

    def boards(self):
        return sorted((self.summary(read(path)) for path in self.root.glob('[0-9a-f]*/board.json')),
                      key=lambda record: record['created'], reverse=True)

    @staticmethod
    def summary(record):
        return {key: record[key] for key in ('id', 'name', 'created', 'synthetic', 'size')}

    def add(self, name, pixels, synthetic=False, filename=None):
        name = text(name, '板面名称', 84, False)
        key = uuid4().hex
        original = raster.png(pixels)
        size = [int(pixels.shape[1]), int(pixels.shape[0])]
        state = {'id': key, 'name': name, 'created': timestamp(), 'revision': 1, 'size': size,
                 'synthetic': synthetic, 'source_name': filename, 'source_sha256': sha256(original).hexdigest(),
                 'settings': dict(inspection.DEFAULT_SETTINGS), 'pads': [], 'exclusions': [],
                 'raster': raster.histogram(pixels)}
        if synthetic:
            state['pads'] = [{'id': uuid4().hex, 'name': label, 'rect': box} for label, box in demo.PADS]
            state['exclusions'] = [{'id': uuid4().hex, 'name': '工位灯高光', 'rect': [618, 222, 650, 250]}]
            state['settings']['scale'] = 54.0
        staging = self.root / ('.' + key)
        staging.mkdir()
        try:
            (staging / 'reviews').mkdir()
            (staging / 'source.png').write_bytes(original)
            write(staging / 'board.json', state)
            staging.rename(self.directory(key))
        finally:
            if staging.exists():
                rmtree(staging)
        return state

    def source(self, key):
        self.board(key)
        return self.directory(key) / 'source.png'

    def pixels(self, key):
        return raster.read(self.source(key))

    def current(self, key, revision):
        record = self.board(key)
        if integer(revision, '修订号', 1) != record['revision']:
            raise Conflict('这张板面的检测设置已经改变，请刷新后重试。')
        return record

    def commit(self, board):
        board['revision'] += 1
        write(self.directory(board['id']) / 'board.json', board)
        return board

    def configure(self, key, value):
        with self.lock:
            board = self.current(key, value.get('revision'))
            board['settings'] = inspection.settings(value)
            return self.commit(board)

    def annotate(self, key, kind, value, window=None, drop=False):
        capacities = {'pads': 30, 'exclusions': 20}
        with self.lock:
            board = self.current(key, value.get('revision'))
            items = board[kind]
            if drop:
                items.remove(find(items, window))
            else:
                detail = inspection.label(value, board['size'])
                if window is None:
                    if len(items) >= capacities[kind]:
                        raise Invalid(f'此类检测框最多可标记 {capacities[kind]} 个。')
                    items.append({'id': uuid4().hex, **detail})
                else:
                    find(items, window).update(detail)
            return self.commit(board)

    def rows(self, record):
        return inspection.inspect(self.pixels(record['id']), record['pads'],
                                  record['exclusions'], record['settings'])

    def save(self, key, revision):
        with self.lock:
            record = self.current(key, revision)
            if not record['pads']:
                raise Invalid('请先标记至少一个焊盘窗口。')
            identity = uuid4().hex
            review = {'schema': 1, 'id': identity, 'created': timestamp(), 'board': key, 'board_name': record['name'],
                      'board_revision': record['revision'], 'source_sha256': record['source_sha256'],
                      'synthetic': record['synthetic'], 'size': record['size'], 'pads': record['pads'],
                      'exclusions': record['exclusions'], 'settings': record['settings'], 'rows': self.rows(record)}
            write(self.directory(key) / 'reviews' / (identity + '.json'), review)
            return review

    def reviews(self, key):
        self.board(key)
        records = (read(path) for path in (self.directory(key) / 'reviews').glob('[0-9a-f]*.json'))
        summary = [{name: record[name] for name in ('id', 'created', 'board_revision')} for record in records]
        return sorted(summary, key=lambda item: item['created'], reverse=True)

    def review(self, key, review):
        return read(self.directory(key) / 'reviews' / (identifier(review) + '.json'))
