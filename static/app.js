'use strict';

const TYPE_COLOR = {
  delete: '#e5484d', insert: '#46a758', replace: '#f5a524', audio_changed: '#8e6fd8',
};
const TYPE_LABEL = {
  delete: 'Removed', insert: 'Added', replace: 'Replaced', audio_changed: 'Audio changed',
};
const TYPE_BLURB = {
  delete: 'Present in A, absent from B.',
  insert: 'Absent from A, present in B.',
  replace: 'Both versions have content here, but the picture differs.',
  audio_changed: 'Picture matches shot for shot; the audio does not.',
};

const S = {
  jobId: null, job: null, es: null, health: null,
  report: null, thumbs: null, filters: new Set(), pickerTarget: null,
};

const $ = (sel) => document.querySelector(sel);
const main = () => $('#main');

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}
function fmtTc(sec) {
  sec = Math.max(0, Number(sec) || 0);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${s.toFixed(3).padStart(6, '0')}`;
}
function shortTc(sec) {
  sec = Math.max(0, Number(sec) || 0);
  return `${Math.floor(sec / 60)}:${(sec % 60).toFixed(1).padStart(4, '0')}`;
}
function fmtBytes(n) {
  if (!n) return '';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}
function ago(ts) {
  if (!ts) return '';
  const d = Math.max(0, Date.now() / 1000 - ts);
  if (d < 60) return 'just now';
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* non-JSON error body */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

/* ------------------------------------------------------------------ env */

async function loadHealth() {
  try { S.health = await api('/api/health'); } catch (e) { S.health = {}; }
  const box = $('#env');
  box.innerHTML = '';
  const badge = (label, state, title) => {
    const b = el('div', `badge ${state}`);
    b.appendChild(el('span', 'dot'));
    b.appendChild(el('span', null, label));
    if (title) b.title = title;
    box.appendChild(b);
  };
  badge(S.health.ffmpeg ? 'ffmpeg' : 'ffmpeg missing',
        S.health.ffmpeg ? 'on' : 'off', S.health.ffmpeg_error || '');
  badge(S.health.videotoolbox ? 'videotoolbox' : 'software encode',
        S.health.videotoolbox ? 'on' : 'warn',
        S.health.videotoolbox ? 'Hardware encode available'
                              : 'h264_videotoolbox unavailable; using libx264');
  badge(S.health.ollama ? `ollama (${(S.health.models || []).length} models)` : 'ollama offline',
        S.health.ollama ? 'on' : 'warn',
        S.health.ollama ? (S.health.models || []).join(', ')
                        : 'Descriptions unavailable until `ollama serve` is running');
}

/* ------------------------------------------------------------- job list */

async function loadJobs() {
  let jobs = [];
  try { jobs = await api('/api/jobs'); } catch (e) { /* server may be starting */ }
  const list = $('#job-list');
  list.innerHTML = '';
  if (!jobs.length) {
    list.appendChild(el('div', 'empty-note', 'No comparisons yet.'));
    return;
  }
  for (const j of jobs) {
    const item = el('div', `job-item${j.id === S.jobId ? ' active' : ''}`);
    item.appendChild(el('div', 'names', `${j.label_a}  ↔  ${j.label_b}`));
    const meta = el('div', 'meta');
    const dot = el('span', 'dot');
    dot.style.cssText = 'width:7px;height:7px;border-radius:50%;flex:none;background:' +
      ({ done: 'var(--ok)', failed: 'var(--err)', running: 'var(--accent)',
         queued: 'var(--muted)', cancelled: 'var(--muted)' }[j.status] || 'var(--muted)');
    meta.appendChild(dot);
    const bits = [j.status];
    if (j.summary) bits.push(`${j.summary.region_count} region(s)`);
    bits.push(ago(j.created_at));
    meta.appendChild(el('span', null, bits.join(' · ')));
    item.appendChild(meta);
    item.onclick = () => openJob(j.id);
    list.appendChild(item);
  }
}

/* -------------------------------------------------------- new job form */

function showNew() {
  if (S.es) { S.es.close(); S.es = null; }
  S.jobId = null;
  loadJobs();
  main().innerHTML = '';
  main().appendChild($('#tpl-new').content.cloneNode(true));

  const hint = $('#model-hint');
  if (S.health && S.health.ollama && (S.health.models || []).length) {
    hint.textContent = `Installed: ${S.health.models.join(', ')}`;
  } else {
    hint.textContent = 'Ollama is not reachable right now.';
  }

  main().querySelectorAll('.browse').forEach((b) => {
    b.onclick = () => openPicker(b.dataset.target);
  });
  $('#run-btn').onclick = runJob;
}

async function runJob() {
  const btn = $('#run-btn');
  const err = $('#run-error');
  err.textContent = '';
  const body = {
    video_a: $('#video_a').value.trim(),
    video_b: $('#video_b').value.trim(),
    threshold: parseFloat($('#threshold').value) || 27,
    audio_threshold: parseInt($('#audio_threshold').value, 10) || 16,
    explain: $('#explain').checked,
    model: $('#model').value.trim() || 'qwen3-vl:8b',
  };
  if (!body.video_a || !body.video_b) {
    err.textContent = 'Pick both versions first.';
    return;
  }
  btn.disabled = true;
  try {
    const { id } = await api('/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    openJob(id);
  } catch (e) {
    err.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

/* --------------------------------------------------------- file picker */

async function openPicker(targetId) {
  S.pickerTarget = targetId;
  $('#picker').hidden = false;
  const current = $(`#${targetId}`).value.trim();
  await browseTo(current ? current.replace(/\/[^/]*$/, '') : '');
}
$('#picker-close').onclick = () => { $('#picker').hidden = true; };
$('#picker').onclick = (e) => { if (e.target.id === 'picker') $('#picker').hidden = true; };

async function browseTo(path) {
  let data;
  try {
    data = await api(`/api/browse?path=${encodeURIComponent(path || '')}`);
  } catch (e) {
    $('#picker-body').innerHTML = `<div class="banner err">${esc(e.message)}</div>`;
    return;
  }
  $('#picker-path').textContent = data.path;
  const body = $('#picker-body');
  body.innerHTML = '';

  if (data.parent) {
    const row = el('div', 'fs-row');
    row.appendChild(el('span', 'fs-icon', '↰'));
    row.appendChild(el('span', null, '.. (up one level)'));
    row.onclick = () => browseTo(data.parent);
    body.appendChild(row);
  }
  for (const d of data.dirs) {
    const row = el('div', 'fs-row');
    row.appendChild(el('span', 'fs-icon', '▸'));
    row.appendChild(el('span', null, d.name));
    row.onclick = () => browseTo(d.path);
    body.appendChild(row);
  }
  for (const v of data.videos) {
    const row = el('div', 'fs-row');
    row.appendChild(el('span', 'fs-icon', '▦'));
    row.appendChild(el('span', null, v.name));
    row.appendChild(el('span', 'fs-size', fmtBytes(v.size)));
    row.onclick = () => {
      $(`#${S.pickerTarget}`).value = v.path;
      $('#picker').hidden = true;
    };
    body.appendChild(row);
  }
  if (!data.dirs.length && !data.videos.length) {
    body.appendChild(el('div', 'empty-note', 'Nothing here.'));
  }
}

/* -------------------------------------------------------------- job view */

async function openJob(id) {
  if (S.es) { S.es.close(); S.es = null; }
  S.jobId = id;
  S.report = null;
  S.thumbs = null;
  S.filters = new Set();
  if (location.hash !== `#job/${id}`) location.hash = `#job/${id}`;
  loadJobs();

  try {
    S.job = await api(`/api/jobs/${id}`);
  } catch (e) {
    main().innerHTML = `<div class="banner err">${esc(e.message)}</div>`;
    return;
  }
  renderJob();

  if (['queued', 'running'].includes(S.job.status)) {
    subscribe(id);
  } else {
    loadLog(id);
    if (S.job.status === 'done') loadResults(id);
  }
}

async function loadLog(id) {
  let events = [];
  try { events = await api(`/api/jobs/${id}/log`); } catch (e) { return; }
  const box = $('#log');
  if (!box) return;
  if (!events.length) {
    box.appendChild(el('div', 'log-line', 'No output recorded.'));
    return;
  }
  for (const ev of events) {
    appendLog(box, ev.type === 'error'
      ? { lane: 'merge', line: ev.message, warn: true }
      : ev);
  }
}

function subscribe(id) {
  const es = new EventSource(`/api/jobs/${id}/events?since=0`);
  S.es = es;
  const logBox = () => $('#log');
  es.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    if (ev.type === 'stage') {
      const stage = S.job.stages[ev.stage];
      if (stage) {
        stage.status = ev.status;
        stage.detail = ev.detail || stage.detail;
        stage.elapsed = ev.elapsed;
        updateStep(ev.stage, stage);
      }
    } else if (ev.type === 'log') {
      appendLog(logBox(), ev);
    } else if (ev.type === 'error') {
      appendLog(logBox(), { lane: 'merge', line: ev.message, warn: true });
    } else if (ev.type === 'finished' || ev.type === 'closed') {
      es.close();
      S.es = null;
      openJob(id);
      loadJobs();
    }
  };
  es.onerror = () => { es.close(); S.es = null; };
}

function appendLog(box, ev) {
  if (!box) return;
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  const line = el('div', `log-line ${ev.lane || ''}${ev.warn ? ' warn' : ''}`);
  line.appendChild(el('span', 'tag', ev.lane === 'merge' ? '' : (ev.lane || '').toUpperCase()));
  line.appendChild(el('span', null, ev.line));
  box.appendChild(line);
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function updateStep(stageId, stage) {
  const node = document.querySelector(`[data-step="${stageId}"]`);
  if (!node) return;
  node.dataset.status = stage.status;
  const detail = node.querySelector('.step-detail');
  const bits = [];
  if (stage.detail) bits.push(stage.detail);
  if (stage.elapsed) bits.push(`${stage.elapsed}s`);
  detail.textContent = bits.join(' · ');
  detail.title = bits.join(' · ');
}

function renderJob() {
  const job = S.job;
  const m = main();
  m.innerHTML = '';

  const head = el('div', 'head-row');
  const h = el('div');
  h.appendChild(el('h2', null, `${job.label_a}  ↔  ${job.label_b}`));
  const sub = el('div', 'muted');
  sub.style.fontSize = '12.5px';
  sub.textContent = `${job.status} · threshold ${job.threshold} · audio distance ${job.audio_threshold}`
    + (job.explain ? ` · ${job.model}` : '');
  h.appendChild(sub);
  head.appendChild(h);

  const actions = el('div', 'head-actions');
  if (['queued', 'running'].includes(job.status)) {
    const cancel = el('button', 'btn danger', 'Cancel');
    cancel.onclick = async () => {
      try { await api(`/api/jobs/${job.id}/cancel`, { method: 'POST' }); } catch (e) { /* already finished */ }
    };
    actions.appendChild(cancel);
  }
  if (job.status === 'done') {
    const open = el('button', 'btn', 'Open standalone report');
    open.onclick = () => window.open(`/api/jobs/${job.id}/report.html`, '_blank');
    actions.appendChild(open);
    const dl = el('button', 'btn', 'Download HTML');
    dl.onclick = () => { window.location = `/api/jobs/${job.id}/download`; };
    actions.appendChild(dl);
  }
  const again = el('button', 'btn', 'New comparison');
  again.onclick = showNew;
  actions.appendChild(again);
  head.appendChild(actions);
  m.appendChild(head);

  if (job.error) {
    const banner = el('div', 'banner err');
    banner.appendChild(el('strong', null,
      job.status === 'cancelled' ? 'Cancelled' : 'This comparison failed'));
    const pre = el('pre', null, job.error);
    banner.appendChild(pre);
    m.appendChild(banner);
  }

  m.appendChild(renderWorkflow(job));

  const logPanel = el('div', 'panel');
  logPanel.appendChild(el('h3', null, 'Pipeline output'));
  const log = el('div', 'log');
  log.id = 'log';
  logPanel.appendChild(log);
  m.appendChild(logPanel);

  const results = el('div');
  results.id = 'results';
  m.appendChild(results);
}

function renderWorkflow(job) {
  const panel = el('div', 'panel');
  panel.appendChild(el('h3', null, 'Workflow'));
  const wf = el('div', 'wf');

  const lanes = el('div', 'wf-lanes');
  for (const [lane, label] of [['a', job.label_a], ['b', job.label_b]]) {
    const box = el('div', 'lane');
    box.dataset.lane = lane;
    const head = el('div', 'lane-head');
    head.appendChild(el('span', 'lane-tag', lane.toUpperCase()));
    const name = el('span', 'lane-name', label);
    name.title = label;
    head.appendChild(name);
    box.appendChild(head);
    box.appendChild(renderSteps(job, lane));
    lanes.appendChild(box);
  }
  wf.appendChild(lanes);

  // The two lanes genuinely run in parallel, so the join is real, not decorative.
  const join = el('div', 'wf-join');
  join.innerHTML = `<svg width="46" height="130" viewBox="0 0 46 130" aria-hidden="true">
    <path d="M0 34 C 24 34, 22 65, 46 65" stroke="#3a4049" fill="none" stroke-width="1.5"/>
    <path d="M0 96 C 24 96, 22 65, 46 65" stroke="#3a4049" fill="none" stroke-width="1.5"/>
  </svg>`;
  wf.appendChild(join);
  wf.appendChild(renderSteps(job, 'merge', 'wf-merge'));
  panel.appendChild(wf);
  return panel;
}

function renderSteps(job, lane, cls) {
  const wrap = el('div', cls || 'steps');
  const ids = Object.values(job.stages).filter((s) => s.lane === lane);
  ids.forEach((stage, i) => {
    const step = el('div', 'step');
    step.dataset.step = stage.id;
    step.dataset.status = stage.status;
    const top = el('div', 'step-top');
    top.appendChild(el('span', 'step-dot'));
    top.appendChild(el('span', 'step-label', stage.label));
    step.appendChild(top);
    const bits = [];
    if (stage.detail) bits.push(stage.detail);
    if (stage.elapsed) bits.push(`${stage.elapsed}s`);
    const detail = el('div', 'step-detail', bits.join(' · '));
    detail.title = bits.join(' · ');
    step.appendChild(detail);
    wrap.appendChild(step);
    if (i < ids.length - 1) {
      wrap.appendChild(el('span', 'step-arrow', lane === 'merge' ? '↓' : '→'));
    }
  });
  return wrap;
}

/* --------------------------------------------------------------- results */

async function loadResults(id) {
  try {
    S.report = await api(`/api/jobs/${id}/report.json`);
    S.thumbs = (await api(`/api/jobs/${id}/thumbs.json`)).regions;
  } catch (e) {
    return;
  }
  renderResults();
}

function renderResults() {
  const box = $('#results');
  if (!box || !S.report) return;
  box.innerHTML = '';
  const rep = S.report;
  const regions = rep.regions;

  // Stats
  const stats = el('div', 'stats');
  const total = el('div', 'stat');
  total.appendChild(el('div', 'n', String(rep.region_count)));
  total.appendChild(el('div', 'k', 'changed regions'));
  stats.appendChild(total);
  for (const kind of ['delete', 'insert', 'replace', 'audio_changed']) {
    const n = rep.summary[kind] || 0;
    if (!n) continue;
    const s = el('div', `stat ${kind}`);
    s.appendChild(el('div', 'n', String(n)));
    s.appendChild(el('div', 'k', TYPE_LABEL[kind]));
    stats.appendChild(s);
  }
  const delta = rep.version_b.duration_seconds - rep.version_a.duration_seconds;
  const d = el('div', 'stat');
  d.appendChild(el('div', 'n', `${delta >= 0 ? '+' : ''}${delta.toFixed(1)}s`));
  d.appendChild(el('div', 'k', 'runtime difference'));
  stats.appendChild(d);
  box.appendChild(stats);

  // Timelines
  const tl = el('div', 'panel');
  tl.appendChild(el('h3', null, 'Timelines (shared scale)'));
  const maxDur = Math.max(rep.version_a.duration_seconds, rep.version_b.duration_seconds, 1);
  tl.appendChild(renderTrack('A', rep.version_a, regions, 'a', maxDur));
  tl.appendChild(renderTrack('B', rep.version_b, regions, 'b', maxDur));

  const ruler = el('div', 'ruler');
  for (let i = 0; i <= 8; i++) {
    const t = el('div', 'tick', shortTc(maxDur * (i / 8)));
    t.style.left = `${(i / 8) * 100}%`;
    ruler.appendChild(t);
  }
  tl.appendChild(ruler);

  const legend = el('div', 'legend');
  for (const kind of ['delete', 'insert', 'replace', 'audio_changed']) {
    if (!rep.summary[kind]) continue;
    const s = el('span');
    const sw = el('i', 'swatch');
    sw.style.background = TYPE_COLOR[kind];
    s.appendChild(sw);
    s.appendChild(el('span', null, `${TYPE_LABEL[kind]} (${rep.summary[kind]})`));
    legend.appendChild(s);
  }
  tl.appendChild(legend);
  box.appendChild(tl);

  if (!regions.length) {
    const ok = el('div', 'banner ok');
    ok.textContent = 'No differences found. The two versions align shot for shot, '
      + 'with matching picture and audio throughout.';
    box.appendChild(ok);
    return;
  }

  // Filters
  const kinds = [...new Set(regions.map((r) => r.type))];
  if (kinds.length > 1) {
    const filters = el('div', 'filters');
    for (const kind of kinds) {
      const chip = el('div', `chip${S.filters.size === 0 || S.filters.has(kind) ? ' on' : ''}`);
      const sw = el('i', 'swatch');
      sw.style.cssText = `background:${TYPE_COLOR[kind]};margin-right:7px`;
      chip.appendChild(sw);
      chip.appendChild(el('span', null, TYPE_LABEL[kind]));
      chip.onclick = () => {
        if (S.filters.has(kind)) S.filters.delete(kind); else S.filters.add(kind);
        renderResults();
      };
      filters.appendChild(chip);
    }
    box.appendChild(filters);
  }

  const cards = el('div');
  regions.forEach((region, i) => {
    if (S.filters.size && !S.filters.has(region.type)) return;
    cards.appendChild(renderRegionCard(i, region, S.thumbs[i] || {}));
  });
  box.appendChild(cards);
}

function renderTrack(tag, info, regions, side, maxDur) {
  const dur = info.duration_seconds || 1;
  const track = el('div', 'track');
  const head = el('div', 'track-head');
  const left = el('div');
  const t = el('span', 'lane-tag');
  t.textContent = tag;
  t.style.cssText = 'display:inline-grid;margin-right:8px;width:18px;height:18px;'
    + 'place-items:center;border-radius:5px;background:var(--panel-2);font-size:10.5px;'
    + `font-weight:700;color:${tag === 'A' ? 'var(--accent)' : 'var(--audio)'}`;
  left.appendChild(t);
  left.appendChild(el('b', null, info.source));
  head.appendChild(left);
  head.appendChild(el('div', 'dur', `${fmtTc(dur)} · ${info.shot_count} shots`));
  track.appendChild(head);

  const bar = el('div', 'bar');
  bar.style.width = `${(100 * dur) / maxDur}%`;
  regions.forEach((r, i) => {
    const start = r[`${side}_start`], end = r[`${side}_end`];
    const mark = el('div', `region${end - start <= 0.01 ? ' point' : ''}`);
    mark.style.left = `${(100 * start) / dur}%`;
    mark.style.width = `${(100 * Math.max(end - start, 0)) / dur}%`;
    mark.style.background = TYPE_COLOR[r.type] || '#888';
    mark.title = `${TYPE_LABEL[r.type] || r.type} — ${fmtTc(start)} to ${fmtTc(end)} `
      + `(${r.shot_count} shot(s))`;
    mark.onclick = () => {
      const card = document.getElementById(`r${i}`);
      if (!card) return;
      card.classList.add('open', 'flash');
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      setTimeout(() => card.classList.remove('flash'), 900);
    };
    bar.appendChild(mark);
  });
  track.appendChild(bar);
  return track;
}

function renderRegionCard(i, region, thumbs) {
  const colour = TYPE_COLOR[region.type] || '#888';
  const card = el('div', 'region-card open');
  card.id = `r${i}`;
  card.style.borderLeftColor = colour;

  const head = el('div', 'rc-head');
  head.appendChild(el('span', 'chev', '▶'));
  const sw = el('span', 'swatch');
  sw.style.background = colour;
  head.appendChild(sw);
  head.appendChild(el('span', 'rc-type', TYPE_LABEL[region.type] || region.type));
  head.appendChild(el('span', 'none', `${region.shot_count} shot(s)`));
  const times = el('div', 'rc-times');
  times.innerHTML = `A ${fmtTc(region.a_start)} → ${fmtTc(region.a_end)}<br>`
    + `B ${fmtTc(region.b_start)} → ${fmtTc(region.b_end)}`;
  head.appendChild(times);
  head.onclick = () => card.classList.toggle('open');
  card.appendChild(head);

  const body = el('div', 'rc-body');
  body.appendChild(el('div', 'muted', TYPE_BLURB[region.type] || ''));

  const exp = el('div', `explanation${region.explanation ? '' : ' absent'}`);
  exp.textContent = region.explanation
    || 'No description (re-run with descriptions enabled to add one).';
  body.appendChild(exp);

  const sides = el('div', 'sides');
  for (const [side, name] of [['a', 'Version A'], ['b', 'Version B']]) {
    const start = region[`${side}_start`], end = region[`${side}_end`];
    const col = el('div', 'side');
    col.appendChild(el('div', 'side-title', name));
    col.appendChild(el('div', 'side-time',
      end - start <= 0.01
        ? `at ${fmtTc(start)} — nothing here`
        : `${fmtTc(start)} → ${fmtTc(end)}  (${(end - start).toFixed(2)}s)`));
    const shots = el('div', 'shots');
    const images = thumbs[`thumbnails_${side}`] || [];
    if (images.length) {
      for (const img of images) {
        const im = document.createElement('img');
        im.src = `data:image/jpeg;base64,${img}`;
        im.alt = `${name} frame`;
        shots.appendChild(im);
      }
    } else {
      shots.appendChild(el('div', 'none', 'No frames on this side.'));
    }
    col.appendChild(shots);
    const desc = region[`description_${side}`];
    if (desc) {
      const d = el('div', 'muted', desc);
      d.style.cssText = 'font-size:12.5px;margin-top:9px';
      col.appendChild(d);
    }
    sides.appendChild(col);
  }
  body.appendChild(sides);
  card.appendChild(body);
  return card;
}

/* ------------------------------------------------------------------ init */

$('#new-job-btn').onclick = () => { location.hash = ''; showNew(); };

// Hash routing, so a job has its own URL and the back button works.
function route() {
  const match = /^#job\/([a-z0-9]+)$/.exec(location.hash);
  if (match) {
    if (match[1] !== S.jobId) openJob(match[1]);
  } else if (S.jobId !== null) {
    showNew();
  }
}
window.addEventListener('hashchange', route);

(async function init() {
  await loadHealth();
  if (/^#job\//.test(location.hash)) route(); else showNew();
  await loadJobs();
  setInterval(loadJobs, 3000);
  // Ollama is often started after this page is open; re-check rather than
  // leaving a stale "offline" badge until the next reload.
  setInterval(loadHealth, 10000);
})();
