'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const boardBase = '/pad/boards';
const elements = {
  canvas: $('#stageCanvas'), wrap: $('#canvasWrap'), stage: $('#imageStage'),
  fields: $('#settingsFields'), settings: $('#settingsForm'),
  edit: $('#editDialog'), editForm: $('#editForm'),
  scale: $('#scaleDialog'), scaleForm: $('#scaleForm'),
  review: $('#reviewDialog'),
};
const context = elements.canvas.getContext('2d');
const ui = {
  board: null, rowData: [], reviews: [], selected: null, image: null, mask: null,
  drag: null, editing: null, scalePoints: [], scaleDistance: 0, zoom: 1, view: 'photo', tool: 'pixel',
  splitPick: null, loadId: 0, maskId: 0, noticeTimer: 0,
};
const statuses = {pass: '通过', under: '待补', over: '超印', unreadable: '无可测像素'};

function splitOf(padId) {
  return (ui.board?.splits || []).find(split => split && (split.left === padId || split.right === padId)) || null;
}

function splitPartnerName(split, padId) {
  const partnerId = split.left === padId ? split.right : split.left;
  return ui.board.pads.find(pad => pad.id === partnerId)?.name || '另一焊盘';
}

function element(tag, value, className) {
  const node = document.createElement(tag);
  if (value !== undefined) node.textContent = value;
  if (className) node.className = className;
  return node;
}

function message(text) {
  const toast = $('#toast');
  toast.textContent = text;
  toast.classList.add('visible');
  clearTimeout(ui.noticeTimer);
  ui.noticeTimer = setTimeout(() => toast.classList.remove('visible'), 4100);
}

async function request(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `操作失败 (${response.status})`);
  return data;
}

function update(path, method, value) {
  return request(path, {
    method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value),
  });
}

function invoke(operation) {
  return async event => {
    try {
      await operation(event);
    } catch (error) {
      message(error.message);
    }
  };
}

function boardUrl(suffix = '') {
  return `${boardBase}/${ui.board.id}${suffix}`;
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('无法读取该图像。'));
    image.src = url;
  });
}

async function refreshBoards() {
  const boards = await request(boardBase);
  const list = $('#boardList');
  list.replaceChildren();
  if (!boards.length) list.append(element('p', '还没有板面', 'muted'));
  for (const board of boards) {
    const button = element('button', board.name, 'board-select');
    button.title = board.name;
    button.dataset.board = board.id;
    button.setAttribute('aria-current', String(ui.board?.id === board.id));
    button.addEventListener('click', invoke(() => openBoard(board.id)));
    list.append(button);
  }
}

async function openBoard(key) {
  const load = ++ui.loadId;
  const base = `${boardBase}/${key}`;
  const [record, image] = await Promise.all([request(base), loadImage(`${base}/source.png`)]);
  if (load !== ui.loadId) return;
  ui.board = record;
  ui.image = image;
  ui.selected = record.pads[0]?.id || null;
  ui.zoom = 1;
  ui.mask = null;
  ui.scalePoints = [];
  ui.drag = null;
  ui.splitPick = null;
  ui.rowData = [];
  ui.reviews = [];
  elements.canvas.width = record.size[0];
  elements.canvas.height = record.size[1];
  $('#welcome').hidden = true;
  elements.wrap.hidden = false;
  elements.fields.disabled = false;
  $('#sendPrepare').disabled = false;
  $('#downloadOriginal').href = `${base}/source.png`;
  $('#downloadOriginal').download = `board-${key.slice(0, 8)}.png`;
  $('#downloadOriginal').hidden = false;
  $('#pixelRead').textContent = '点击原图查看像素。';
  const url = new URL(location.href);
  url.searchParams.set('board', key);
  history.replaceState({}, '', url);
  setTool('pixel');
  setZoom(1);
  setView('photo');
  renderBoard();
  await Promise.all([refreshRows(), refreshBoards(), refreshReviews()]);
}

function renderSettings() {
  const config = ui.board.settings;
  for (const name of ['threshold', 'low', 'high', 'scale']) {
    elements.settings.elements[name].value = config[name] ?? '';
  }
  $('#thresholdValue').textContent = config.threshold;
}

function renderBoard() {
  const board = ui.board;
  $('#boardTitle').textContent = board.name;
  $('#crumbName').textContent = board.name;
  $('#boardMeta').textContent = board.synthetic ? '练习素材 · 合成板面' : (board.source_name || '原图坐标');
  $('#revision').textContent = `版本 ${board.revision}`;
  $('#imageSize').textContent = `${board.size.join(' × ')} px`;
  $('#saveReview').disabled = board.pads.length === 0;
  $('#glareCount').textContent = `${board.raster.saturated.toLocaleString()} 过亮像素`;
  const top = Math.max(...board.raster.histogram, 1);
  const bars = board.raster.histogram.map(value => {
    const bar = element('span');
    bar.style.setProperty('--ratio', Math.sqrt(value / top));
    return bar;
  });
  $('#histogramBars').replaceChildren(...bars);
  renderSettings();
  renderWindows();
  draw();
}

function renderWindows() {
  for (const kind of ['pads', 'exclusions']) {
    const isPad = kind === 'pads';
    const list = $(isPad ? '#padList' : '#exclusionList');
    $(isPad ? '#padCount' : '#exclusionCount').textContent = ui.board[kind].length;
    list.replaceChildren();
    if (!ui.board[kind].length) {
      list.append(element('p', isPad ? '在图上拖拉添加焊盘框。' : '暂无排除区域。', 'muted'));
    }
    for (const item of ui.board[kind]) {
      const row = element('div', undefined, 'item-row');
      const select = element('button', undefined, 'list-select');
      const size = `${item.rect[2] - item.rect[0]} × ${item.rect[3] - item.rect[1]}`;
      const split = isPad ? splitOf(item.id) : null;
      const detail = split ? `⇆ ${splitPartnerName(split, item.id)} · ${size}` : size;
      select.append(element('span', item.name), element('span', detail, 'item-info'));
      select.setAttribute('aria-current', String(isPad && item.id === ui.selected));
      select.addEventListener('click', invoke(() => isPad ? selectPad(item.id) : openEditor(kind, item)));
      row.append(select);
      if (split && split.left === item.id) {
        const undo = element('button', '删除分盘', 'item-edit danger-text');
        undo.setAttribute('aria-label', `删除 ${item.name} 的分盘`);
        undo.addEventListener('click', invoke(async () => {
          await changeBoard(await update(boardUrl(`/splits/${split.id}`), 'DELETE', {revision: ui.board.revision}));
          message('分盘已删除，两个焊盘恢复独立测量。');
        }));
        row.append(undo);
      } else {
        const edit = element('button', '编辑', 'item-edit');
        edit.setAttribute('aria-label', `编辑 ${item.name}`);
        edit.addEventListener('click', () => openEditor(kind, item));
        row.append(edit);
      }
      list.append(row);
    }
  }
}

function renderRows() {
  const list = $('#measurements');
  list.replaceChildren();
  if (!ui.rowData.length) {
    const blank = element('td', '添加焊盘窗口后显示测量。', 'muted');
    blank.colSpan = 9;
    const tr = element('tr');
    tr.append(blank);
    list.append(tr);
  }
  for (const row of ui.rowData) {
    const tr = element('tr');
    tr.dataset.pad = row.name;
    const name = element('td');
    const select = element('button', row.name);
    select.addEventListener('click', invoke(() => selectPad(row.id)));
    name.append(select);
    tr.append(name);
    const values = [
      row.coverage === null ? '—' : `${row.coverage.toFixed(2)}%`,
      row.paste_px.toLocaleString(),
      row.review_px ? row.review_px.toLocaleString() : '—',
      row.paste_mm2?.toFixed(3) || '—',
      row.deposits, row.holes, row.overload_px,
    ];
    for (const value of values) tr.append(element('td', value));
    const status = element('td');
    status.append(element('span', statuses[row.state], `state ${row.state}`));
    tr.append(status);
    list.append(tr);
  }
  const needsWork = ui.rowData.filter(row => row.state === 'under' || row.state === 'over').length;
  $('#summary').textContent = ui.rowData.length ? `${ui.rowData.length} 个窗口 · ${needsWork} 个待处理` : '待检测';
}

async function refreshRows() {
  const data = await request(boardUrl('/measurements'));
  if (data.revision !== ui.board.revision) return;
  ui.rowData = data.rows;
  renderRows();
}

async function changeBoard(record) {
  ui.board = record;
  if (!record.pads.some(pad => pad.id === ui.selected)) ui.selected = record.pads[0]?.id || null;
  renderBoard();
  await Promise.all([refreshRows(), refreshMask()]);
}

async function selectPad(id) {
  ui.selected = id;
  renderWindows();
  draw();
  await refreshMask();
}

async function refreshMask() {
  const id = ++ui.maskId;
  ui.mask = null;
  if (!ui.board || !ui.selected || ui.view !== 'mask') return draw();
  const mask = await loadImage(boardUrl(`/mask/${ui.selected}.png?r=${ui.board.revision}`));
  if (id !== ui.maskId) return;
  ui.mask = mask;
  draw();
}

function box(rect, stroke, fill, dashed, title) {
  const [x0, y0, x1, y1] = rect;
  const ratio = elements.canvas.width / (elements.canvas.clientWidth || elements.canvas.width);
  context.save();
  context.lineWidth = ratio * 1.65;
  context.strokeStyle = stroke;
  if (dashed) context.setLineDash([6 * ratio, 4 * ratio]);
  if (fill) {
    context.fillStyle = fill;
    context.fillRect(x0, y0, x1 - x0, y1 - y0);
  }
  context.strokeRect(x0, y0, x1 - x0, y1 - y0);
  if (title) {
    context.setLineDash([]);
    context.font = `600 ${12 * ratio}px sans-serif`;
    context.fillStyle = '#13251b';
    const top = y0 > 19 * ratio ? y0 - 19 * ratio : y0;
    context.fillRect(x0, top, context.measureText(title).width + 9 * ratio, 19 * ratio);
    context.fillStyle = stroke;
    context.textBaseline = 'middle';
    context.fillText(title, x0 + 4 * ratio, top + 10 * ratio);
  }
  context.restore();
}

function drawSplit(split) {
  const ratio = elements.canvas.width / (elements.canvas.clientWidth || elements.canvas.width);
  const [left, top, right, bottom] = split.overlap;
  context.save();
  context.fillStyle = '#8fb4ff22';
  context.fillRect(left, top, right - left, bottom - top);
  context.strokeStyle = '#9fc0ff';
  context.lineWidth = ratio * 1.4;
  context.setLineDash([5 * ratio, 4 * ratio]);
  context.strokeRect(left, top, right - left, bottom - top);
  context.setLineDash([]);
  context.strokeStyle = '#ffd479';
  context.lineWidth = ratio * 2;
  context.beginPath();
  split.seam.forEach((x, index) => {
    const y = top + index + 0.5;
    if (index === 0) context.moveTo(x + 0.5, y);
    else context.lineTo(x + 0.5, y);
  });
  context.stroke();
  context.restore();
}

function draw() {
  if (!ui.board || !ui.image) return;
  const {width, height} = elements.canvas;
  context.clearRect(0, 0, width, height);
  context.drawImage(ui.mask || ui.image, 0, 0);
  for (const item of ui.board.exclusions) box(item.rect, '#f2bd72', '#94673644', true, null);
  const activeSplit = ui.selected ? splitOf(ui.selected) : null;
  for (const split of ui.board.splits || []) {
    if (split && split !== activeSplit) drawSplit(split);
  }
  for (const item of ui.board.pads) {
    const active = item.id === ui.selected;
    const paired = splitOf(item.id) !== null;
    const colour = active ? '#ceffd5' : (paired ? '#8fb4ff' : '#99cfad');
    box(item.rect, colour, null, !active, item.name);
  }
  if (activeSplit) drawSplit(activeSplit);
  if (ui.splitPick) {
    const picked = ui.board.pads.find(pad => pad.id === ui.splitPick);
    if (picked) box(picked.rect, '#ffd479', '#ffd47924', true, `${picked.name} · 再选一个`);
  }
  if (ui.drag) box(dragRect(ui.drag), '#f3d599', '#f3d59924', true, null);
  if (ui.scalePoints.length) {
    context.save();
    context.strokeStyle = '#fff0bb';
    context.lineWidth = 2.5;
    for (const [x, y] of ui.scalePoints) {
      context.beginPath();
      context.arc(x, y, 5, 0, 2 * Math.PI);
      context.stroke();
    }
    if (ui.scalePoints.length === 2) {
      context.beginPath();
      context.moveTo(...ui.scalePoints[0]);
      context.lineTo(...ui.scalePoints[1]);
      context.stroke();
    }
    context.restore();
  }
}

function setZoom(zoom) {
  ui.zoom = Math.min(2.5, Math.max(1, zoom));
  elements.wrap.style.width = `${100 * ui.zoom}%`;
  elements.canvas.style.maxHeight = ui.zoom === 1 ? '' : 'none';
  $('#zoomRead').textContent = ui.zoom === 1 ? '适配' : `${Math.round(ui.zoom * 100)}%`;
  $('#zoomOut').disabled = !ui.board || ui.zoom === 1;
  $('#zoomIn').disabled = !ui.board || ui.zoom === 2.5;
  draw();
}

function setView(view) {
  if (view === 'mask' && (!ui.board || !ui.selected)) return message('请先选择一个焊盘窗口。');
  ui.view = view;
  for (const button of document.querySelectorAll('[data-view]')) button.classList.toggle('on', button.dataset.view === view);
  return refreshMask();
}

function setTool(tool) {
  ui.tool = tool;
  ui.scalePoints = [];
  ui.drag = null;
  ui.splitPick = null;
  for (const button of document.querySelectorAll('[data-tool]')) button.classList.toggle('on', button.dataset.tool === tool);
  if (ui.board) {
    $('#pixelRead').textContent = {
      pixel: '点击原图查看像素。', pads: '在锡膏周围拖拉一个焊盘容差框。',
      exclusions: '拖拉一个区域排除眩光或异物。',
      split: '依次点选两个左右并排、各有独占区的焊盘。', scale: '在原图尺标上依次点选两个端点。',
    }[tool];
  }
  draw();
}

function padAt(point) {
  const [x, y] = point;
  return ui.board.pads.find(item =>
    item.rect[0] <= x && x < item.rect[2] && item.rect[1] <= y && y < item.rect[3]) || null;
}

async function chooseSplitPair(point) {
  const pad = padAt(point);
  if (!pad) return message('请点在一个焊盘容差框内。');
  if (!ui.splitPick) {
    if (splitOf(pad.id)) return message('这个焊盘已参与分盘，请先删除原分盘。');
    ui.splitPick = pad.id;
    $('#pixelRead').textContent = `已选 ${pad.name}，再点一个与它左右搭边的焊盘。`;
    draw();
    return;
  }
  if (pad.id === ui.splitPick) {
    ui.splitPick = null;
    draw();
    return;
  }
  const first = ui.splitPick;
  ui.splitPick = null;
  try {
    const result = await update(boardUrl('/splits'), 'POST',
      {revision: ui.board.revision, left: first, right: pad.id});
    await changeBoard(result.board);
    ui.selected = result.split.left;
    renderWindows();
    draw();
    message('分盘已采用，叠边锡膏按连通归属分配，接不回的膏岛已标为待核。');
    if (ui.view === 'mask') await refreshMask();
  } catch (error) {
    draw();
    throw error;
  }
}

function pointer(event) {
  const canvas = elements.canvas;
  const box = canvas.getBoundingClientRect();
  return [
    Math.max(0, Math.min(canvas.width, Math.round((event.clientX - box.left) * canvas.width / box.width))),
    Math.max(0, Math.min(canvas.height, Math.round((event.clientY - box.top) * canvas.height / box.height))),
  ];
}

function dragRect({start, end}) {
  return [Math.min(start[0], end[0]), Math.min(start[1], end[1]),
    Math.max(start[0], end[0]), Math.max(start[1], end[1])];
}

function openEditor(kind, item) {
  ui.editing = {kind, id: item.id || null};
  const form = elements.editForm;
  $('#editTitle').textContent = kind === 'pads' ? '焊盘窗口' : '排除窗口';
  $('#deleteWindow').hidden = !item.id;
  form.elements.name.value = item.name;
  for (const [index, name] of ['x0', 'y0', 'x1', 'y1'].entries()) {
    form.elements[name].value = item.rect[index];
    form.elements[name].max = ui.board.size[index % 2];
  }
  elements.edit.showModal();
}

function configValues() {
  const form = elements.settings.elements;
  return {
    revision: ui.board.revision, threshold: Number(form.threshold.value),
    low: Number(form.low.value), high: Number(form.high.value), scale: form.scale.value === '' ? null : Number(form.scale.value),
  };
}

async function refreshReviews() {
  ui.reviews = await request(boardUrl('/reviews'));
  $('#reviewCount').textContent = ui.reviews.length;
  const list = $('#reviewList');
  list.replaceChildren();
  if (!ui.reviews.length) list.append(element('p', '复核包含原图、面积表和分盘蒙版。', 'muted'));
  for (const review of ui.reviews) {
    const row = element('div', undefined, 'item-row');
    const button = element('button', undefined, 'list-select');
    button.dataset.review = review.id;
    const localDate = new Date(review.created).toLocaleString('zh-CN', {hour12: false});
    button.append(element('span', localDate), element('span', `V${review.board_revision}`, 'item-info'));
    button.addEventListener('click', invoke(async () => openReview(await request(boardUrl(`/reviews/${review.id}`)))));
    row.append(button);
    list.append(row);
  }
}

function openReview(review) {
  const splitCount = (review.splits || []).length;
  const reviewPixels = review.rows.reduce((sum, row) => sum + (row.review_px || 0), 0);
  $('#reviewDetail').textContent = `版本 ${review.board_revision} · 亮度阈值 ${review.settings.threshold}`
    + ` · ${review.rows.length} 个窗口 · ${splitCount} 组分盘 · ${reviewPixels.toLocaleString()} 待核像素`;
  $('#reviewRows').replaceChildren(...review.rows.map(row =>
    element('li', `${row.name} · ${row.coverage === null ? '无可测像素' : row.coverage.toFixed(2) + '%'}`
      + ` · ${statuses[row.state]}${row.review_px ? ` · 待核 ${row.review_px.toLocaleString()} px` : ''}`)));
  $('#downloadReview').href = boardUrl(`/reviews/${review.id}/package.zip`);
  if (!elements.review.open) elements.review.showModal();
}

for (const id of ['#welcomeDemo', '#demoBoard']) {
  $(id).addEventListener('click', invoke(async () => openBoard((await update('/pad/demo', 'POST', {})).id)));
}
$('#importBoard').addEventListener('click', () => $('#imageFile').click());
$('#imageFile').addEventListener('change', invoke(async event => {
  const file = event.target.files[0];
  if (!file) return;
  event.target.value = '';
  const body = new FormData();
  body.append('image', file);
  await openBoard((await request(boardBase, {method: 'POST', body})).id);
}));
$('#sendPrepare').addEventListener('click', invoke(async () => {
  const prepared = await update(boardUrl('/prepare'), 'POST', {});
  location.assign(prepared.url);
}));
$('#saveReview').addEventListener('click', invoke(async () => {
  const snapshot = await update(boardUrl('/reviews'), 'POST', {revision: ui.board.revision});
  await refreshReviews();
  openReview(snapshot);
}));
for (const button of document.querySelectorAll('[data-view]')) {
  button.addEventListener('click', invoke(() => setView(button.dataset.view)));
}
for (const button of document.querySelectorAll('[data-tool]')) {
  button.addEventListener('click', () => setTool(button.dataset.tool));
}
for (const button of document.querySelectorAll('[data-close]')) {
  button.addEventListener('click', () => document.getElementById(button.dataset.close).close());
}
$('#zoomIn').addEventListener('click', () => setZoom(ui.zoom + .25));
$('#zoomOut').addEventListener('click', () => setZoom(ui.zoom - .25));
$('#threshold').addEventListener('input', () => { $('#thresholdValue').textContent = $('#threshold').value; });
elements.settings.addEventListener('submit', invoke(async event => {
  event.preventDefault();
  await changeBoard(await update(boardUrl('/settings'), 'PATCH', configValues()));
  message('检测设置已应用。');
}));
elements.canvas.addEventListener('pointerdown', event => {
  if (!ui.board || event.button !== 0 || !['pads', 'exclusions'].includes(ui.tool)) return;
  const point = pointer(event);
  ui.drag = {start: point, end: point};
  elements.canvas.setPointerCapture(event.pointerId);
});
elements.canvas.addEventListener('pointermove', event => {
  if (!ui.drag) return;
  ui.drag.end = pointer(event);
  draw();
});
elements.canvas.addEventListener('pointercancel', () => {
  ui.drag = null;
  draw();
});
elements.canvas.addEventListener('pointerup', invoke(async event => {
  if (!ui.board || event.button !== 0) return;
  if (ui.drag) {
    const rect = dragRect({...ui.drag, end: pointer(event)});
    ui.drag = null;
    draw();
    if (rect[2] - rect[0] < 4 || rect[3] - rect[1] < 4) return message('检测框至少需要 4 × 4 原图像素。');
    const prefix = ui.tool === 'pads' ? 'PAD' : '排除';
    openEditor(ui.tool, {name: `${prefix} ${ui.board[ui.tool].length + 1}`, rect});
    return;
  }
  const point = pointer(event).map((value, index) => Math.min(value, ui.board.size[index] - 1));
  if (ui.tool === 'split') {
    await chooseSplitPair(point);
  } else if (ui.tool === 'pixel') {
    const params = new URLSearchParams({x: point[0], y: point[1]});
    const data = await request(boardUrl(`/pixel?${params}`));
    const membership = data.exclusions.length ? '已排除' : data.windows.join(' / ') || '窗口外';
    $('#pixelRead').textContent = `(${data.x}, ${data.y}) · RGB ${data.rgb.join('/')} · 亮度 ${data.luminance} · ${membership}`;
  } else if (ui.tool === 'scale') {
    ui.scalePoints.push(point);
    draw();
    if (ui.scalePoints.length < 2) return;
    const [start, end] = ui.scalePoints;
    ui.scaleDistance = Math.hypot(end[0] - start[0], end[1] - start[1]);
    if (!ui.scaleDistance) return setTool('scale');
    $('#scaleDistance').textContent = `原片上两点相距 ${ui.scaleDistance.toFixed(2)} px。`;
    elements.scale.showModal();
  }
}));
elements.editForm.addEventListener('submit', invoke(async event => {
  event.preventDefault();
  const {kind, id} = ui.editing;
  const input = elements.editForm.elements;
  const rect = ['x0', 'y0', 'x1', 'y1'].map(name => Number(input[name].value));
  const suffix = id ? `/${id}` : '';
  const board = await update(boardUrl(`/${kind}${suffix}`), id ? 'PATCH' : 'POST', {
    revision: ui.board.revision, name: input.name.value, rect,
  });
  if (kind === 'pads') ui.selected = id || board.pads.at(-1).id;
  elements.edit.close();
  if (!id) setTool('pixel');
  await changeBoard(board);
  if (kind === 'exclusions') $('#exclusionDetails').open = true;
}));
$('#deleteWindow').addEventListener('click', invoke(async () => {
  const {kind, id} = ui.editing;
  const record = await update(boardUrl(`/${kind}/${id}`), 'DELETE', {revision: ui.board.revision});
  elements.edit.close();
  await changeBoard(record);
}));
elements.scaleForm.addEventListener('submit', invoke(async event => {
  event.preventDefault();
  const mm = Number(elements.scaleForm.elements.namedItem('length').value);
  const record = await update(boardUrl('/settings'), 'PATCH',
    {...ui.board.settings, revision: ui.board.revision, scale: ui.scaleDistance / mm});
  elements.scale.close();
  await changeBoard(record);
  message('原图比例已标定。');
}));
elements.scale.addEventListener('close', () => setTool('pixel'));
window.addEventListener('resize', draw);

const board = new URLSearchParams(location.search).get('board');
invoke(() => board ? openBoard(board) : refreshBoards())();
