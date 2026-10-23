'use strict';
const $ = (s, r = document) => r.querySelector(s);
const api = (p) => fetch(p).then(async r => {
  if (r.status === 401) { signedOut(); throw new Error('signed out'); }
  // Set by the service worker when the Mac could not be reached and this came
  // off the phone instead. Saying so beats letting you read last week's
  // drafts believing they are today's.
  markStale(r.headers.get('x-mg-from-cache') === '1');
  const data = await r.json().catch(() => ({}));
  // A 404 or a 500 used to come back as though it were the answer, so a
  // profile that no longer existed threw on p.name and the click did nothing.
  if (!r.ok) {
    const e = new Error(data.detail || `Request failed (${r.status})`);
    e.status = r.status;
    e.detail = data.detail || '';
    throw e;
  }
  return data;
});
// What a box says when what it was waiting for never came. The placeholder
// ("Drafting…") otherwise stays up for good.
const COULDNT = (what) => `<div class="empty">Couldn't load ${what}. Reload the page to try again.</div>`;

// The session cookie is only valid for one run of the server. After a restart the
// old tab would otherwise fail silently on every click.
function signedOut() {
  // A phone has no terminal to open a link from, and its pairing lasts far
  // longer than one run of the server -- so it is told the truth for a phone,
  // which is that the Mac restarted and it should try again.
  const paired = document.body.classList.contains('phone') ||
    matchMedia('(display-mode: standalone)').matches;
  document.body.innerHTML = paired
    ? `<div class="signedout"><h2>Lost the Mac</h2>
       <p>Messaging Graph isn't answering. It may be asleep, off this Wi-Fi, or
       restarting.</p><button type="button" class="btn primary" onclick="location.reload()">Try again</button></div>`
    : `<div class="signedout"><h2>This session has ended</h2>
       <p>The server was restarted, so the link it printed earlier no longer works.
       Open the new link from your terminal.</p></div>`;
}
// JSON POST that surfaces the server's own error message instead of failing silently.
async function postJSON(path, body) {
  const r = await fetch(path, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body) });
  if (r.status === 401) { signedOut(); throw new Error('signed out'); }
  const data = await r.json().catch(() => ({}));
  return { ok: r.ok, data, error: r.ok ? '' : (data.detail || 'Something went wrong. Try again.') };
}

// n with its noun, agreeing: n(1, 'plan') -> "1 plan". Written out by hand in
// a few places already, and missing in the places this fixes.
const n = (k, singular, plural) => `${k} ${k === 1 ? singular : (plural || singular + 's')}`;

const COPY_HINT = 'Ready — press ⌘C.';

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// A contact with no saved name is just a number, and its first word is the area code.
// Show the last four digits instead, so two unnamed people don't both read "(408)".
// How long ago, in the roundest unit that is still true.
const ago = (iso) => {
  const d = (Date.now() - new Date(iso).getTime()) / 86400000;
  if (d < 1) return 'today';
  if (d < 2) return 'yesterday';
  if (d < 31) return `${Math.round(d)} days ago`;
  if (d < 365) {
    const m = Math.round(d / 30.4);
    return m === 1 ? 'a month ago' : `${m} months ago`;
  }
  const y = d / 365.25;
  return y < 1.5 ? 'about a year ago' : `${Math.round(y)} years ago`;
};
// null means the two of you have never replied to each other inside the window.
// It is not a slow reply and it is not zero, so it does not get a number.
const secs = s => s === null || s === undefined ? '—'
  : s < 90 ? `${s | 0}s` : s < 5400 ? `${(s / 60) | 0}m`
  : s < 172800 ? `${(s / 3600).toFixed(1)}h` : `${(s / 86400).toFixed(1)}d`;
const since = (iso) => new Date(iso).toLocaleDateString(undefined, { month: 'long', year: 'numeric' });

const isNumberName = (n) =>
  /^\(\d{3}\) \d{3}-\d{4}$|^\+?[\d\s().-]{7,}$/.test(String(n ?? '').trim());
// A face where there is only a name. Initials, in a colour the name itself picks,
// so the same person is the same colour on every screen. The colour arrives as a
// class, never a literal, so both themes keep control of what it actually is.
const AV_COLOURS = 6;
function initials(name) {
  const t = String(name ?? '').trim();
  if (isNumberName(t)) return '#';
  const parts = t.split(/\s+/).filter(Boolean);
  if (!parts.length) return '?';
  // Spread, not [0]: an emoji or an accented letter is more than one code unit.
  const first = [...parts[0]][0] || '';
  const last = parts.length > 1 ? ([...parts[parts.length - 1]][0] || '') : '';
  return (first + last).toUpperCase();
}
function avClass(key) {
  let h = 0;
  for (const ch of String(key ?? '')) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  return 'c' + (h % AV_COLOURS);
}
// aria-hidden: the name is always right there in text, so a screen reader
// reading "PR" first would only be in the way.
const avatar = (name, size = '') =>
  `<span class="av ${avClass(name)} ${size}" aria-hidden="true">${esc(initials(name))}</span>`;

const shortName = (n) => {
  const s = String(n ?? '').trim();
  if (isNumberName(s)) return '…' + s.replace(/\D/g, '').slice(-4);
  if (/^[^\s@]+@[^\s@]+$/.test(s)) return s.split('@')[0];
  return s.split(' ')[0];
};

// ---------------------------------------------------------------- colour
// The stylesheet owns every colour, including the ones that end up as SVG
// attributes. These maps are filled from it by readTheme() and refilled when the
// theme changes, so a chart drawn in light and the same chart drawn in dark come
// from one definition instead of two that drift apart.
const cssVar = (n) =>
  getComputedStyle(document.documentElement).getPropertyValue('--' + n).trim();
const PAINT = {};
const STATE_COLOR = {};
// Descriptive states, not grades -- so no red. Red on a human reads as an alarm,
// and nothing a texting statistic can see justifies one.
const STATE_COLOR_PERSON = {};
const SERIES = {};
const PAINT_KEYS = ['bg', 'panel', 'panel2', 'line', 'line-strong', 'ink', 'dim', 'faint',
  'adds', 'functional', 'neutral', 'costs', 'unclear', 'accent', 'stale', 'plan'];

function readTheme() {
  for (const k of PAINT_KEYS) PAINT[k] = cssVar(k);
  Object.assign(STATE_COLOR, {
    scheduled: PAINT.adds, confirmed: PAINT.adds, proposed: PAINT.accent,
    floated: PAINT.plan, stale: PAINT.stale, done: PAINT.faint, dropped: PAINT.unclear,
  });
  Object.assign(STATE_COLOR_PERSON, {
    close: PAINT.adds, steady: PAINT.accent, practical: PAINT.functional,
    uneven: PAINT.neutral, quiet: PAINT.unclear, new: PAINT.unclear, group: PAINT.unclear,
  });
  Object.assign(SERIES, { you: cssVar('series-you'), them: cssVar('series-them') });
}
const STATE_LABEL = {
  close: 'close', steady: 'steady', practical: 'practical',
  uneven: 'uneven', quiet: 'quiet', new: 'too early', group: 'groups only',
};

const S = { view: 'now', overview: null, people: null, graph: null, theme: null,
  includeStale: false, sort: 'importance', weeks: 40, focus: 'connected',
  // Which kinds of conversation put a line on the graph. Independent, because
  // they answer different questions: who you talk to, and who knows whom.
  ties: { direct: false, group: true } };

// ---------------------------------------------------------------- charts
// The two series are --series-you / --series-them, set per theme in styles.css
// and checked by validate.py: each stays above 3:1 against its own chart surface,
// and the pair stays far apart under normal vision and all three kinds of colour
// blindness, so "you" and "them" never collapse into one another.
const NS = 'http://www.w3.org/2000/svg';
const el = (n, a = {}) => {
  const e = document.createElementNS(NS, n);
  for (const k in a) e.setAttribute(k, a[k]);
  return e;
};
const fmtDay = (iso) => new Date(iso).toLocaleDateString(undefined,
  { month: 'short', day: 'numeric' });

/** Rounded-top column: square at the baseline, 4px radius on the data end. */
function colPath(x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h));
  return `M${x},${y + h}V${y + r}a${r},${r} 0 0 1 ${r},${-r}h${w - 2 * r}`
       + `a${r},${r} 0 0 1 ${r},${r}V${y + h}Z`;
}

/**
 * Weekly messaging rate as stacked columns (you / them).
 * Part-to-whole over time: the column height is the rate, the split is who
 * carried it. Hover gives the exact counts; only the peak is direct-labelled.
 */
function rateChart(host, data, opts = {}) {
  const B = data.buckets || [];
  if (!B.length) { host.innerHTML = '<div class="empty">No messages to chart.</div>'; return; }

  const W = Math.max(280, host.clientWidth || 520);
  const H = opts.height || 156;
  const M = { t: 16, r: 10, b: 22, l: 34 };
  const iw = W - M.l - M.r, ih = H - M.t - M.b;

  const max = Math.max(1, ...B.map(b => b.total));
  // Clean axis top: 1 / 2 / 5 × a power of ten, at or above the peak.
  const pow = Math.pow(10, Math.floor(Math.log10(max)));
  const top = [1, 2, 5, 10].map(m => m * pow).find(v => v >= max) || max;
  const y = v => M.t + ih - (v / top) * ih;

  const band = iw / B.length;
  const cw = Math.min(24, Math.max(2, band - 2));   // 2px surface gap between columns

  const svg = el('svg', { width: W, height: H, role: 'img',
    'aria-label': opts.aria || 'Messages per week' });

  // hairline, solid, recessive grid
  for (const t of [0, top / 2, top]) {
    svg.append(el('line', { x1: M.l, x2: M.l + iw, y1: y(t), y2: y(t),
      stroke: PAINT.line, 'stroke-width': 1 }));
    const lab = el('text', { x: M.l - 7, y: y(t) + 3.5, 'text-anchor': 'end',
      fill: PAINT.faint, 'font-size': 9.5, 'font-family': 'ui-monospace,monospace' });
    lab.textContent = t;
    svg.append(lab);
  }

  // "before you met" — absence of history is not the same as silence
  const first = data.first_message ? new Date(data.first_message) : null;
  if (first) {
    const i = B.findIndex(b => new Date(b.end) > first);
    if (i > 0) {
      const x = M.l + i * band;
      svg.append(el('rect', { x: M.l, y: M.t, width: x - M.l, height: ih,
        fill: PAINT.panel2, opacity: .5 }));
      svg.append(el('line', { x1: x, x2: x, y1: M.t, y2: M.t + ih,
        stroke: PAINT['line-strong'], 'stroke-width': 1 }));
      const t = el('text', { x: x + 4, y: M.t + 9, fill: PAINT.faint, 'font-size': 9,
        'font-family': 'system-ui,sans-serif' });
      t.textContent = 'first message';
      svg.append(t);
    }
  }

  const peak = B.reduce((a, b, i) => B[a].total >= b.total ? a : i, 0);

  B.forEach((b, i) => {
    const x = M.l + i * band + (band - cw) / 2;
    const hYou = (b.mine / top) * ih, hThem = (b.theirs / top) * ih;
    const gap = (hYou > 3 && hThem > 3) ? 2 : 0;   // 2px surface gap in the stack
    let cursor = M.t + ih;
    if (hYou > 0) {
      cursor -= hYou;
      const topmost = hThem <= 0;
      svg.append(el('path', { d: colPath(x, cursor, cw, hYou, topmost ? 4 : 0),
        fill: SERIES.you }));
    }
    if (hThem > 0) {
      cursor -= hThem + gap;
      svg.append(el('path', { d: colPath(x, cursor, cw, hThem, 4), fill: SERIES.them }));
    }
    if (i === peak && b.total > 0) {
      const t = el('text', { x: x + cw / 2, y: y(b.total) - 5, 'text-anchor': 'middle',
        fill: PAINT.dim, 'font-size': 9.5, 'font-family': 'ui-monospace,monospace' });
      t.textContent = b.total;
      svg.append(t);
    }
  });

  // x labels: first, middle, last only
  [0, Math.floor(B.length / 2), B.length - 1].forEach(i => {
    const t = el('text', { x: M.l + i * band + band / 2, y: H - 6,
      'text-anchor': i === 0 ? 'start' : i === B.length - 1 ? 'end' : 'middle',
      fill: PAINT.faint, 'font-size': 9.5, 'font-family': 'system-ui,sans-serif' });
    t.textContent = fmtDay(B[i].start);
    svg.append(t);
  });

  // hover layer — hit targets span the full band, not just the column
  const hi = el('rect', { y: M.t, width: band, height: ih, fill: PAINT.ink,
    opacity: 0, 'pointer-events': 'none' });
  svg.append(hi);
  const hit = el('rect', { x: M.l, y: M.t, width: iw, height: ih, fill: 'transparent' });
  svg.append(hit);
  host.append(svg);

  const tip = document.createElement('div');
  tip.className = 'tip';
  host.append(tip);
  hit.onpointermove = ev => {
    const r = svg.getBoundingClientRect();
    const i = Math.max(0, Math.min(B.length - 1, Math.floor((ev.clientX - r.left - M.l) / band)));
    const b = B[i];
    hi.setAttribute('x', M.l + i * band); hi.setAttribute('opacity', .07);
    tip.innerHTML = `<b>${fmtDay(b.start)} – ${fmtDay(b.end)}</b>
      <span><i style="background:${SERIES.you}"></i>you<em>${b.mine}</em></span>
      <span><i style="background:${SERIES.them}"></i>them<em>${b.theirs}</em></span>
      <span class="tot">total<em>${b.total}</em></span>`;
    tip.style.opacity = 1;
    const tw = tip.offsetWidth || 130;
    tip.style.left = Math.max(0, Math.min(W - tw, M.l + i * band + band / 2 - tw / 2)) + 'px';
    tip.style.top = Math.max(0, y(b.total) - tip.offsetHeight - 10) + 'px';
  };
  hit.onpointerleave = () => { tip.style.opacity = 0; hi.setAttribute('opacity', 0); };

  if (opts.legend !== false) {
    const lg = document.createElement('div');
    lg.className = 'clegend';
    lg.innerHTML = `<span><i style="background:${SERIES.you}"></i>you</span>
      <span><i style="background:${SERIES.them}"></i>them</span>
      <span class="ax">messages per week</span>`;
    host.append(lg);
  }
}

/**
 * Render a chart into a host element and keep it correct when the box resizes.
 *
 * The SVG is drawn at exact pixel width rather than scaled through a viewBox, so
 * strokes stay hairline and 9.5px axis text stays 9.5px. That only works if a
 * stale measurement can't survive, hence the observer.
 */
const CHART_RO = typeof ResizeObserver === 'undefined' ? null
  : new ResizeObserver(entries => {
      for (const e of entries) {
        const h = e.target;
        if (h._chart && Math.abs((h._w || 0) - h.clientWidth) > 4) paintChart(h);
      }
    });

function paintChart(host) {
  host._w = host.clientWidth;
  host.innerHTML = '';
  rateChart(host, host._chart, host._opts || {});
}

function mountChart(host, data, opts = {}) {
  host._chart = data; host._opts = opts;
  paintChart(host);
  if (CHART_RO) CHART_RO.observe(host);
}

/** Single-series trajectory sparkline for a table row. No axes, no legend. */
function spark(values, w = 78, h = 20) {
  if (!values || !values.length) return '';
  const max = Math.max(1, ...values);
  const step = w / (values.length - 1 || 1);
  const pts = values.map((v, i) => `${(i * step).toFixed(1)},${(h - (v / max) * (h - 3) - 1.5).toFixed(1)}`);
  const last = values[values.length - 1];
  return `<svg width="${w}" height="${h}" class="spark" aria-hidden="true">
    <polyline points="${pts.join(' ')}" fill="none" stroke="${SERIES.you}"
      stroke-width="1.75" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${(w).toFixed(1)}" cy="${(h - (last / max) * (h - 3) - 1.5).toFixed(1)}"
      r="2.5" fill="${SERIES.you}" stroke="${PAINT.bg}" stroke-width="1.5"/></svg>`;
}

// ---------------------------------------------------------------- boot
// Only where the browser will have it: on this Mac it changes nothing, and on
// a phone it is the difference between the app opening and a blank screen.
function keepOffline() {
  if (!('serviceWorker' in navigator) || !window.isSecureContext) return;
  navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {});
}

// Shown when what you are reading came off the phone rather than the Mac.
function markStale(stale) {
  document.body.classList.toggle('stale', stale);
  let bar = $('#stale');
  if (!stale) { bar?.remove(); return; }
  if (bar) return;
  bar = document.createElement('div');
  bar.id = 'stale';
  bar.className = 'stalebar';
  bar.textContent = "Can't reach your Mac — showing what you last saw.";
  document.body.prepend(bar);
}

// Each platform is its own history, so the whole app -- people, plans, the
// graph -- changes with it. They are never merged: a group chat on WhatsApp
// is not the same room as one on iMessage.
async function drawPlatforms() {
  const bar = $('#platforms');
  if (!bar) return;
  let p;
  try { p = await api('/api/platforms'); } catch { return; }
  S.platform = p.active;
  if (p.platforms.length < 2) { bar.hidden = true; return; }
  bar.hidden = false;
  bar.innerHTML = p.platforms.map(x => `
    <button type="button" class="plat ${x.id === p.active ? 'on' : ''}"
      data-platform="${esc(x.id)}" aria-pressed="${x.id === p.active}">${esc(x.label)}</button>`).join('');
  bar.querySelectorAll('[data-platform]').forEach(b => b.onclick = async () => {
    if (b.classList.contains('on')) return;
    bar.querySelectorAll('[data-platform]').forEach(o => o.disabled = true);
    const r = await postJSON('/api/platform', { platform: b.dataset.platform });
    if (!r.ok) { bar.querySelectorAll('[data-platform]').forEach(o => o.disabled = false); return; }
    await refreshAll();
  });
}

// Everything the app holds belongs to one platform, so none of it survives
// the switch.
async function loadData() {
  S.overview = await api('/api/overview');
  S.people = await api('/api/people');
  const o = S.overview;
  $('#src').textContent = SOURCE_LABEL[o.source] || o.source;
  $('#c-now').textContent = o.suggestions.length;
  $('#c-people').textContent = o.contacts;
  $('#c-graph').textContent = o.plans.open + o.plans.stale;
  // Every other tab carries its number; this one was the exception.
  $('#c-insights').textContent = o.threads;
  $('#foot-span').textContent = o.span.from.slice(0, 10) + ' → ' + o.span.to.slice(0, 10);
  syncFoot();
}

async function refreshAll() {
  // None of what is held belongs to the platform being switched to.
  S.graph = null; S.drafts = {}; S.draftSeeds = {};
  await loadData();
  await drawPlatforms();
  go(S.view === 'search' ? 'now' : S.view, false);
}

async function boot() {
  keepOffline();
  wireAsk();
  // Before anything draws, so a draft you put away yesterday doesn't flash up.
  try {
    const prefs = await api('/api/prefs');
    hiddenIds = new Set(prefs.hidden || []);
    S.theme = prefs.theme;
  } catch { /* a fresh server with nothing saved yet */ }
  wireTheme();
  drawPlatforms();
  wireReload();
  wirePhoneLayout();
  await loadData();
  document.querySelectorAll('nav button').forEach(b =>
    b.onclick = () => go(b.dataset.view));
  $('#scrim').onclick = closeDrawer;
  document.addEventListener('keydown', e => e.key === 'Escape' && closeDrawer());
  document.addEventListener('keydown', trapTab);
  window.onpopstate = () => go(viewFromUrl(), false);
  go(viewFromUrl(), false);
}

// On a phone the sidebar becomes the bottom tab bar, which leaves no room for the
// theme switch or the source/reload line. They are moved rather than copied: the
// same elements, so every handler wired in boot() still points at them and there
// is no second set to keep in step. Watched rather than read once, because a
// phone turned on its side can cross the breakpoint.
function wirePhoneLayout() {
  const narrow = matchMedia('(max-width:760px)');
  const foot = $('#phonefoot'), side = $('aside'), nav = $('nav');
  const place = () => {
    const picker = $('.themepick'), feet = $('.sidefoot');
    if (narrow.matches) {
      foot.append(picker, feet);          // append moves a node that already has a parent
      foot.hidden = false;
    } else {
      side.append(feet);                  // back where the stylesheet expects them:
      side.append(picker);                // the foot under nav, the picker last
      nav.after(feet);
      foot.hidden = true;
    }
  };
  place();
  narrow.addEventListener('change', place);
}

// The view lives in the address bar, so a reload comes back where you were and the
// browser's own Back button walks through the app rather than out of it.
// Insights was missing, so reloading it or coming Back to it landed on Now.
const VIEWS = ['now', 'graph', 'people', 'privacy', 'insights'];
function viewFromUrl() {
  const v = new URLSearchParams(location.search).get('view');
  return VIEWS.includes(v) ? v : 'now';
}

// ---------------------------------------------------------------- theme
// Auto follows the system. Light and dark pin it, and the choice is remembered
// on this Mac. Charts and the force graph are drawn with SVG attributes rather
// than CSS, so switching re-reads the palette and repaints the open view.
// An import that finishes while this tab is open leaves the server holding the old
// corpus. Reloading re-reads it in place, so you don't have to restart anything.
function wireReload() {
  const b = $('#reload');
  if (!b) return;
  const label = b.textContent;
  const settle = (text, ms) => {
    b.textContent = text;
    setTimeout(() => { b.textContent = label; b.disabled = false; }, ms);
  };
  b.onclick = async () => {
    b.disabled = true;
    b.textContent = 'Reading\u2026';
    try {
      const { ok } = await postJSON('/api/reload', {});
      if (!ok) return settle("Couldn't reload", 2400);
      await refreshData();
      if (S.view) go(S.view);
      settle('Up to date', 1600);
    } catch {
      // signedOut() has already replaced the page; leave the button alone.
    }
  };
}

function wireTheme() {
  // The server is the authority; localStorage only got there first, to avoid a
  // flash of the wrong colours before this script ran.
  let saved = S.theme;
  if (!saved) { try { saved = localStorage.getItem('mg-theme'); } catch { /* private window */ } }
  const mode = saved === 'light' || saved === 'dark' ? saved : 'auto';
  // Put the server's answer in the box the pre-paint script reads, so the next
  // load on this origin starts in the right colours.
  try {
    if (mode === 'auto') localStorage.removeItem('mg-theme');
    else localStorage.setItem('mg-theme', mode);
  } catch { /* nothing to cache into */ }
  const system = window.matchMedia('(prefers-color-scheme: dark)');

  const apply = (m, repaint) => {
    if (m === 'auto') delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = m;
    document.querySelectorAll('[data-theme-set]').forEach(b =>
      b.classList.toggle('on', b.dataset.themeSet === m));
    readTheme();
    if (repaint && S.view) go(S.view);
  };

  document.querySelectorAll('[data-theme-set]').forEach(b => {
    b.onclick = () => {
      const m = b.dataset.themeSet;
      // localStorage is still written, but only so theme.js can paint the right
      // colours before the first byte of script runs. The Mac moved to a new
      // port each launch and took this box with it, so the server is where the
      // choice actually lives now -- and it reaches your phone too.
      try {
        if (m === 'auto') localStorage.removeItem('mg-theme');
        else localStorage.setItem('mg-theme', m);
      } catch { /* the theme still applies, it just won't be remembered here */ }
      postJSON('/api/prefs', { theme: m }).catch(() => {});
      apply(m, true);
    };
  });
  // On auto, follow the Mac flipping to dark at sunset without a reload. CSS
  // repaints itself either way; this is what redraws the SVG charts and the
  // force graph, which carry their colours as attributes.
  system.addEventListener('change', () => {
    if (!document.documentElement.dataset.theme) apply('auto', true);
  });
  apply(mode, false);
}

function syncFoot() {
  const s = S.overview.privacy, lan = S.overview.lan || {};
  // Two separate facts, and the footer has been wrong when it told only one of
  // them: local_only is about calls leaving for the internet, lan.on is about
  // this Mac answering the Wi-Fi. "No network" while a phone is reading is a lie.
  const out = s.local_only ? 'local only' : 'network open: ' + s.network_consent.join(', ');
  const here = !lan.on ? 'no network'
    : lan.devices === 1 ? 'on Wi-Fi · 1 phone'
    : `on Wi-Fi · ${lan.devices} phones`;
  const lic = S.overview.licence;
  const paid = !lic ? '' : lic.state === 'trial' ? ` · trial, ${n(lic.trial_days_left, 'day')} left`
    : lic.state === 'licensed' ? '' : ' · imports need a licence';
  $('#foot-mode').textContent = `${out} · ${here}${paid}`;
  $('#c-priv').textContent = s.excluded_contact_ids.length
    ? s.excluded_contact_ids.length + ' hidden' : '';
}

const VIEW_TITLE = { now: 'Now', graph: 'My brainstorm', people: 'People', privacy: 'Privacy',
  search: 'Search', insights: 'Insights' };

// The whole history at once. Everything else here is about somebody; this is
// the other question, and the rate chart was the only part of it anywhere --
// sitting oddly on the People page, which is about individuals.
async function viewInsights() {
  $('#main').innerHTML = `<h1>Insights</h1>
    <div class="sub tight">What a year of this looks like from a distance. Facts about
      when and how much, not marks out of ten.</div>
    <div id="ins"><div class="empty">Working it out…</div></div>`;
  let d;
  try { d = await api('/api/insights'); } catch {
    $('#ins').innerHTML = `<div class="empty">Couldn't work that out.
      <button type="button" class="linkish" onclick="viewInsights()">Try again</button></div>`;
    return;
  }
  if (d.empty) {
    $('#ins').innerHTML = '<div class="empty">Nothing imported yet.</div>';
    return;
  }
  const hourMax = Math.max(...d.by_hour, 1), dayMax = Math.max(...d.by_day, 1);
  const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  $('#ins').innerHTML = `
    <div class="bignums">
      <div class="bignum"><b>${d.messages.toLocaleString()}</b><span>messages</span></div>
      <div class="bignum"><b>${d.per_day}</b><span>a day, on average</span></div>
      <div class="bignum"><b>${d.people}</b><span>people</span></div>
      <div class="bignum"><b>${d.mine_share}%</b><span>of it is you</span></div>
    </div>
    <div class="sub tight">${esc(d.first_month)} to ${esc(d.last_month)} — ${n(d.days, 'day')}.
      ${d.group_share}% of it in group chats.</div>

    <h2>When you text</h2>
    <div class="sub tight">Mostly the ${esc(d.busiest_part)}, and more on
      ${esc(d.busiest_day)} than any other day.</div>
    <div class="whenwrap">
      <div class="whenbars" role="img" aria-label="Messages by hour of day">
        ${d.by_hour.map((v, h) => `<i style="height:${Math.max(2, 100 * v / hourMax)}%"
          title="${h}:00 — ${n(v.toLocaleString(), 'message')}"></i>`).join('')}
      </div>
      <div class="whenaxis"><span>midnight</span><span>noon</span><span>midnight</span></div>
    </div>
    <div class="daybars" role="img" aria-label="Messages by day of the week">
      ${d.by_day.map((v, i) => `<div class="daybar">
        <span class="dl">${DAYS[i]}</span>
        <i><b style="width:${Math.max(1, 100 * v / dayMax)}%"></b></i>
        <span class="dv">${v.toLocaleString()}</span></div>`).join('')}
    </div>

    <h2>Who that is, mostly</h2>
    <div class="sub tight">By how much of the year they are. Open anyone for the rest of it.</div>
    <div class="topppl" role="list" aria-label="The people you message most">
      ${d.top.map(t => `<button type="button" class="topp" data-id="${esc(t.id)}">
        ${avatar(t.name)}
        <span class="tn">${esc(t.name)}</span>
        <i><b style="width:${t.share}%"></b></i>
        <span class="tv">${t.messages.toLocaleString()}</span></button>`).join('')}
    </div>`;
  $('#ins').querySelectorAll('.topp').forEach(b =>
    b.onclick = () => openPerson(b.dataset.id));
}

// The same plain voice as the draft labels: what it is, not what the code
// that found it is called.
const NOTICE_KIND = {
  uneven: 'a bit one-sided', reflect: 'worth a thought', growing: 'picking up',
  warm: 'going well', drifting: 'drifting', quiet: 'gone quiet',
};

const HIT_LABEL = { plan: 'plan', person: 'person', message: 'message', group: 'group chat' };

// Asking the whole app at once. The question goes nowhere near a model or a
// network -- the words are weighted against your own history on this machine.
async function viewSearch() {
  const q = S.query || '';
  $('#main').innerHTML = `<h1>Search</h1>
    <div class="sub tight">Everything the app knows — people, plans, group chats and the
      messages themselves. Nothing is sent anywhere to answer it.</div>
    <div id="hits"><div class="empty">Looking…</div></div>`;
  let r;
  try {
    r = await api('/api/search?q=' + encodeURIComponent(q));
  } catch {
    $('#hits').innerHTML = `<div class="empty">Couldn't search just now.
      <button type="button" class="linkish" onclick="viewSearch()">Try again</button></div>`;
    return;
  }
  const box = $('#hits');
  if (!r.hits.length) {
    box.innerHTML = `<div class="empty">Nothing matched ${esc(q)}.
      ${r.terms.length ? `Looked for ${r.terms.map(t => `<b>${esc(t)}</b>`).join(', ')} across
      ${n(r.searched.toLocaleString(), 'message')}.` : ''}</div>`;
    return;
  }
  // Said plainly rather than left for you to notice: you asked when, and
  // nothing that matched names a date.
  // The subject itself was not found: say which word, rather than offering
  // whatever shared the other words as though it were an answer.
  const offSubject = r.off_subject
    ? `<div class="notice quiet"><div>Nothing here mentions <b>${esc(r.off_subject)}</b>.
       What's below matched the rest of the question.</div></div>` : '';
  const noDate = r.no_date
    ? `<div class="notice quiet"><div>Nothing that matched mentions a date. What's below is
       what the thread does say.</div></div>` : '';
  box.innerHTML = `
    <div class="asked">Looked for ${r.terms.map(t => `<b>${esc(t)}</b>`).join(', ')}
      across ${n(r.searched.toLocaleString(), 'message')}.</div>
    ${offSubject}${noDate}
    <div class="hits">${r.hits.map((h, i) => `
      <button type="button" class="hit" data-i="${i}">
        <div class="htop"><span class="hkind">${HIT_LABEL[h.kind] || h.kind}</span>
          ${h.when ? `<span class="hwhen">${esc(h.when)}</span>` : ''}</div>
        <div class="hbody">${esc(h.title)}</div>
        <div class="hmeta">${esc([h.who, h.detail, h.ts ? ago(h.ts) : ''].filter(Boolean).join(' · '))}</div>
      </button>`).join('')}</div>`;
  box.querySelectorAll('.hit').forEach(b => b.onclick = () => {
    const h = r.hits[+b.dataset.i];
    if (h.kind === 'person') openPerson(h.target_id);
    else if (h.kind === 'plan') openPlan(h.target_id);
    else {
      // A message or a group chat: open whoever the thread is with, rather
      // than dropping you on the People list to go and find them.
      const who = (S.people || []).find(x => x.name === h.who);
      if (who) openPerson(who.contact_id); else go('people');
    }
  });
}

function wireAsk() {
  const form = $('#askform'), input = $('#ask');
  if (!form) return;
  form.onsubmit = e => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    S.query = q;
    go('search');
  };
}

function go(v, push = true) {
  // Re-read the palette on every render rather than caching it once. The CSS is
  // the authority; this way a theme that changed underneath us -- a system flip,
  // or a `change` event the browser never delivered -- can never leave the charts
  // drawn in the other theme's colours.
  readTheme();
  S.view = v;
  // Search lives in the box rather than the address, so it is not a place to
  // come back to: pushing it stacked up ?view=search entries that reloaded as Now.
  if (push && VIEWS.includes(v) && viewFromUrl() !== v) {
    history.pushState({ view: v }, '', v === 'now' ? location.pathname : `?view=${v}`);
  }
  document.querySelectorAll('nav button').forEach(b => {
    const here = b.dataset.view === v;
    b.classList.toggle('on', here);
    if (here) b.setAttribute('aria-current', 'page');
    else b.removeAttribute('aria-current');
  });
  // Replacing the contents does not move the scrollbar, so leaving People
  // half way down and opening Now put you half way down Now.
  const main = $('#main');
  if (main) main.scrollTop = 0;
  // Every view was called "messaging graph", which makes the back button and
  // a row of tabs equally unhelpful.
  document.title = `${VIEW_TITLE[v] || 'messaging graph'} · messaging graph`;
  ({ now: viewNow, graph: viewGraph, people: viewPeople, privacy: viewPrivacy,
     search: viewSearch, insights: viewInsights })[v]();
}

// ---------------------------------------------------------------- now
// The one place the app says hello. Time of day, nothing more personal than that.
function greeting() {
  const h = new Date().getHours();
  const part = h < 5 ? 'Up late' : h < 12 ? 'Morning' : h < 17 ? 'Afternoon' : 'Evening';
  // The page used to promise a status report on everyone and then hand you five
  // counters. It answers one question now, so it says which one.
  return `${part}. Here's who to write to.`;
}

const SOURCE_LABEL = { synthetic: 'demo data', imessage: 'your messages' };

// Fourteen invented people, in an app whose whole premise is that these are
// yours. Say so at the top, and say what to type to change it.
// Inside the Mac app the import is a button: the app is what macOS grants the
// permission to, so it is the only thing that can do the reading. In a browser,
// or on a phone over the Wi-Fi, mg:// means nothing and the commands stand.
const inDesktopApp = () => / MessagingGraph\/desktop\b/.test(navigator.userAgent);

// Links that leave the app. The Mac app's window hands any outside address to the
// browser itself, and a target=_blank there would go nowhere.
const OUT = / MessagingGraph\/desktop\b/.test(navigator.userAgent) ? '' : 'target="_blank" rel="noopener"';

// After the trial nothing here goes away: what was imported stays, and only a new
// import waits. Said once, on the page you land on, with the way out next to it.
function licenceNotice(o) {
  const l = o.licence;
  if (!l || l.can_import) return '';
  return `<div class="notice"><div><b>${l.state === 'check_needed'
      ? 'Your licence needs checking.' : 'Your trial has ended.'}</b>
    ${esc(l.message.replace(/^Your trial has ended\. /, ''))}</div>
    <div class="cmds one"><a class="btn primary" href="${esc(l.buy_url)}" ${OUT}>Buy — $${l.price_usd}</a>
    <button type="button" class="btn" data-goto-licence>Enter a key</button></div></div>`;
}

function demoBanner(o) {
  if (o.source !== 'synthetic') return '';
  const how = inDesktopApp()
    ? `<div class="cmds one"><a class="btn primary" href="mg://import">Import my messages</a></div>
       macOS will ask you to allow it the first time. Nothing is read until you do.`
    : `To read your own, quit this and run two commands in Terminal — the first asks
       macOS for permission to read Messages once, the second starts the app again.
       <div class="cmds"><code>./mg import</code><code>./mg serve</code></div>`;
  return `<div class="notice demo"><div><b>These people aren't real.</b>
    You're looking at a made-up history so the app has something to show.
    ${how}
    Nothing is uploaded either way: the import writes an encrypted copy on this Mac
    and the key goes in your Keychain.</div></div>`;
}

function viewNow() {
  const o = S.overview, v = o.states, pc = o.plans;
  $('#main').innerHTML = `
    <h1>${greeting()}</h1>
    <div class="sub tight">Who's worth a message, and something to say. Copy one into
      Messages or skip it — nothing sends from here.</div>
    ${demoBanner(o)}
    ${licenceNotice(o)}
    <div class="voicenote" id="voice-note"></div>
    <div id="drafts" class="drafts"><div class="empty">Drafting…</div></div>
    <h2>Say thanks</h2>
    <div class="sub tight">Tied to something that actually happened — a favour, a year of
      texting, something you did together.</div>
    <div id="thanks" class="drafts"><div class="empty">Looking back…</div></div>
    ${notices(o)}
    <div class="statline">
      ${pc.confirmed ? `<button type="button" class="jump" data-goto="graph" data-jump="plans-confirmed"><b>${pc.confirmed}</b> confirmed</button>` : ''}
      ${pc.in_motion ? `<button type="button" class="jump" data-goto="graph" data-jump="plans-motion"><b>${pc.in_motion}</b> being discussed</button>` : ''}
      ${pc.revive ? `<button type="button" class="jump" data-goto="graph" data-jump="plans-revive"><b>${pc.revive}</b> worth reviving</button>` : ''}
      ${v.close ? `<span><b>${v.close}</b> close right now</span>` : ''}
      ${v.quiet ? `<span><b>${v.quiet}</b> gone quiet</span>` : ''}
    </div>
    <div class="foot-note">From ${o.messages.toLocaleString()} messages across ${o.threads.toLocaleString()}
      threads on this Mac. None of it is a to-do list — skipping all of it is a perfectly
      good week.</div>`;
  document.querySelectorAll('.card').forEach(c => c.onclick = () => openPerson(c.dataset.id));
  document.querySelectorAll('.name-them').forEach(b => b.onclick = openNaming);
  document.querySelectorAll('.add-locations').forEach(b => b.onclick = openLocations);
  document.querySelectorAll('[data-goto-licence]').forEach(b => b.onclick = () => go('privacy'));
  wireJumps();
  drawDrafts().catch(() => { const b = $('#drafts'); if (b) b.innerHTML = COULDNT('drafts'); });
  drawThanks().catch(() => { const b = $('#thanks'); if (b) b.innerHTML = COULDNT('thank-yous'); });
}

// The three plan counts live on Now but the plans themselves are on the
// brainstorm, so the jump has to switch view first and scroll afterwards. It
// waits to be told the panel has rendered rather than polling for it: the first
// attempt gave up after two seconds and the plans routinely take longer.
function wireJumps() {
  document.querySelectorAll('.jump[data-goto]').forEach(b => b.onclick = () => {
    S.pendingJump = b.dataset.jump;
    if (S.view !== b.dataset.goto) go(b.dataset.goto);
    else settleJump();
  });
}

// Called by each panel once it has drawn. Whoever renders the element the jump
// was aiming for is the one that scrolls to it.
function settleJump() {
  if (!S.pendingJump) return;
  const id = S.pendingJump;
  const el = document.getElementById(id);
  if (!el) return;
  S.pendingJump = null;
  // Not smooth. The two panels below this one are still filling in, and every
  // insertion cancelled the animation, so the page sat at the top instead. A
  // jump that crosses views should land anyway, and instantly is the right feel
  // for one: re-asserted on the next frame to absorb whatever lands late.
  el.scrollIntoView({ block: 'start' });
  requestAnimationFrame(() =>
    document.getElementById(id)?.scrollIntoView({ block: 'start' }));
}

// Both the Now page and People open with the same two corrections to offer, and
// a third that is only ever an aside. One place decides how they are worded.
function notices(o) {
  // When most of the address book is still digits, this is not one chore among
  // several -- it is the thing holding everything else back, and the notice
  // should say which. 91 of 104 here, and it showed: the drafts and the
  // observations were mostly addressed to phone numbers.
  const total = (S.people || []).length;
  const most = total && o.unnamed / total > 0.5;
  return `${o.unnamed ? `<div class="notice"><div><b>${n(o.unnamed, 'person', 'people')} you talk to ${
      o.unnamed === 1 ? 'is' : 'are'} still just a number.</b>
      ${most ? `That's most of them, and it is why the drafts and the observations
      keep going to digits instead of names — everything here reads better once
      the app knows who is who.`
      : 'Name them so their profiles and drafts make sense.'}</div>
      <button type="button" class="btn primary name-them">Name them</button></div>` : ''}
    ${locationNotice(o)}
    ${o.filtered ? `<div class="notice quiet"><div>${o.filtered} automated or marketing
      ${o.filtered === 1 ? 'thread is' : 'threads are'} set aside and left out of everything here.</div></div>` : ''}`;
}

// ---------------------------------------------------------------- drafts
const DRAFT_KIND = {
  reply: 'your turn', follow_up: 'ask how it went', revive_plan: 'pick a date',
  group_plan: 'group plan', reconnect: 'say hi', check_in: 'check in',
  thanked: 'say thanks', anniversary: 'a year of texting', together: 'say thanks',
  appreciation: 'something nice',
};
// Which drafts you have waved away, and until when, is the server's to keep.
// It used to be localStorage: scoped to an origin, and the Mac app hands out a
// new port every launch, so "Not now" lasted exactly as long as the window did.
let hiddenIds = new Set();
function isHidden(id) { return hiddenIds.has(id); }
function hideDraft(id, why) {
  hiddenIds.add(id);                      // the card goes now; the save follows
  postJSON('/api/prefs', { hide_draft: id, hide_why: why })
    .then(r => { if (r?.data?.hidden) hiddenIds = new Set(r.data.hidden); })
    .catch(() => {});
}

async function drawDrafts() {
  const box = $('#drafts');
  if (!box) return;
  api('/api/voice').then(v => {
    const note = $('#voice-note');
    if (!note || !v.traits) return;
    const span = v.window_days ? `your last ${n(v.window_days, 'day')} of messages` : 'all your messages';
    note.textContent = `Your style, from ${span}: ${v.traits.join(' · ')}`;
  }).catch(() => {});
  const all = await api('/api/messages');
  S.drafts = { ...(S.drafts || {}), ...Object.fromEntries(all.map(d => [d.id, d])) };
  S.draftSeeds = S.draftSeeds || {};
  const shown = all.filter(d => !isHidden(d.id));
  if (!shown.length) {
    box.innerHTML = `<div class="empty">${all.length ? "You've been through today's drafts."
      : 'Nothing pressing. The people you talk to regularly have heard from you recently.'}</div>`;
    return;
  }
  box.innerHTML = shown.map(d => draftCard(d)).join('');
  box.querySelectorAll('.draft').forEach(wireDraft);
}

async function drawThanks() {
  const box = $('#thanks');
  if (!box) return;
  const all = await api('/api/gratitude');
  S.drafts = { ...(S.drafts || {}), ...Object.fromEntries(all.map(d => [d.id, d])) };
  S.draftSeeds = S.draftSeeds || {};
  const shown = all.filter(d => !isHidden(d.id));
  if (!shown.length) {
    box.innerHTML = `<div class="empty">${all.length ? "You've been through today's thank-yous."
      : "Nothing to point to yet. Thank-yous show up here once there's a real moment to mention."}</div>`;
    return;
  }
  box.innerHTML = shown.map(d => draftCard(d, '/api/gratitude')).join('');
  box.querySelectorAll('.draft').forEach(wireDraft);
}

function draftCard(d, src = '/api/messages') {
  const quoted = ['reply', 'follow_up', 'thanked'].includes(d.kind);
  const person = d.kind !== 'group_plan';
  return `<div class="draft" data-id="${esc(d.id)}" data-src="${esc(src)}">
    <div class="dtop">
      <span class="dwho ${person ? 'link' : ''}" ${person ? `data-person="${esc(d.target_ids[0])}"` : ''}>
        ${avatar(d.who)}<span class="who">${esc(d.who)}</span></span>
      <span class="kind">${DRAFT_KIND[d.kind] || esc(d.kind)}</span></div>
    <textarea class="dtext" rows="2" aria-label="Draft message to ${esc(d.who)}">${esc(d.draft)}</textarea>
    <div class="why">${esc(d.why)}</div>
    ${d.detail ? `<div class="detail">${quoted ? '“' + esc(d.detail) + '”' : 'Proposed: ' + esc(d.detail)}</div>` : ''}
    <div class="dact">
      <button type="button" class="btn primary copy">Copy</button>
      ${d.options > 1 ? '<button type="button" class="btn another">Another version</button>' : ''}
      <button type="button" class="btn ghost skip">Not now</button>
      <span class="dnote" role="status"></span>
    </div></div>`;
}

function wireDraft(card) {
  const id = card.dataset.id, ta = card.querySelector('.dtext'), note = card.querySelector('.dnote');
  fitText(ta);
  const who = card.querySelector('.dwho[data-person]');
  if (who) who.onclick = () => openPerson(who.dataset.person);
  card.querySelector('.copy').onclick = async () => {
    try {
      await navigator.clipboard.writeText(ta.value);
      note.textContent = 'Copied ✓ — paste it into Messages.';
    } catch {
      ta.select();
      note.textContent = COPY_HINT;
    }
    card.classList.add('sent');
    hideDraft(id, 'copied');
  };
  const another = card.querySelector('.another');
  if (another) another.onclick = async () => {
    const d = S.drafts[id];
    S.draftSeeds[id] = ((S.draftSeeds[id] || 0) + 1) % d.options;
    another.disabled = true;
    let list;
    try {
      list = await api(`${card.dataset.src}?redraft=${encodeURIComponent(id)}&seed=${S.draftSeeds[id]}`);
    } catch {
      note.textContent = "Couldn't get another version. Try again.";
      return;
    } finally {
      another.disabled = false;
    }
    const fresh = list.find(x => x.id === id);
    if (!fresh) return;
    S.drafts[id] = fresh;
    const next = document.createElement('div');
    next.innerHTML = draftCard(fresh, card.dataset.src);
    card.replaceWith(next.firstElementChild);
    wireDraft($(`.draft[data-id="${CSS.escape(id)}"]`));
  };
  card.querySelector('.skip').onclick = () => {
    hideDraft(id, 'skip');
    const host = card.parentElement;
    card.remove();
    if (host && !host.querySelector('.draft')) host.innerHTML = `<div class="empty">You've been through these for now.</div>`;
  };
}

// ---------------------------------------------------------------- crews
// One thing, the handful of people to ask, and why each of them. Sizes come from
// the activity (a league needs six, a trip four) but you can change it.
async function drawCrew() {
  const box = $('#crew-panel');
  if (!box) return;
  S.crew = S.crew || { activity: '', size: '', nearby: true, seed: 0, crewSeed: 0 };
  let ideas = [];
  try { ideas = (await api('/api/crew/activities')).activities || []; } catch { /* offline */ }
  box.innerHTML = `
    <div class="crewform">
      <input id="crew-what" class="search" type="text" maxlength="60" placeholder="a volo league, a ski trip, board game night…"
        aria-label="What's the plan?" value="${esc(S.crew.activity)}">
      <label class="mini">how many
        <select id="crew-size" aria-label="Crew size">
          <option value="">fits the plan</option>
          ${[2, 3, 4, 5, 6, 7, 8].map(n => `<option value="${n}" ${String(S.crew.size) === String(n) ? 'selected' : ''}>${n}</option>`).join('')}
        </select></label>
      <label class="mini"><input id="crew-near" type="checkbox" ${S.crew.nearby ? 'checked' : ''}> keep it local</label>
      <button type="button" class="btn primary crew-go">Who should I ask?</button>
    </div>
    ${ideas.length ? `<div class="crewideas"><span class="mini">or try:</span>
      ${ideas.map(i => `<button type="button" class="chip sm crew-idea" data-a="${esc(i.activity)}" title="${esc(i.why)}">${esc(i.activity)}</button>`).join('')}</div>` : ''}
    <div id="crew-out"></div>`;
  const run = () => {
    S.crew.activity = $('#crew-what').value;
    S.crew.size = $('#crew-size').value;
    S.crew.nearby = $('#crew-near').checked;
    S.crew.seed = 0;
    S.crew.crewSeed = 0;
    loadCrew();
  };
  $('.crew-go').onclick = run;
  $('#crew-what').onkeydown = e => { if (e.key === 'Enter') run(); };
  box.querySelectorAll('.crew-idea').forEach(c => c.onclick = () => {
    $('#crew-what').value = c.dataset.a;
    run();
  });
  if (S.crew.activity) loadCrew();
}

async function loadCrew() {
  const out = $('#crew-out');
  if (!out) return;
  const c = S.crew;
  if (!c.activity.trim()) {
    out.innerHTML = `<div class="empty">Say what the plan is and this will suggest who to ask.</div>`;
    return;
  }
  out.innerHTML = `<div class="empty">Working out who'd be up for it…</div>`;
  let crew;
  try {
    crew = await api(`/api/crew?activity=${encodeURIComponent(c.activity)}`
      + (c.size ? `&size=${encodeURIComponent(c.size)}` : '')
      + `&nearby=${c.nearby}&seed=${c.seed}&crew_seed=${c.crewSeed}`);
  } catch {
    out.innerHTML = `<div class="empty">Couldn't work that out. Try saying it a different way.</div>`;
    return;
  }
  if (!crew.members || !crew.members.length) {
    out.innerHTML = `<div class="empty">${esc(crew.note || 'No one to suggest for that yet.')}</div>`;
    return;
  }
  out.innerHTML = `<div class="crew">
    <div class="crewnote">${esc(crew.note)}</div>
    <div class="members">${crew.members.map(m => `
      <div class="member"><div class="mname link" data-id="${esc(m.id)}">${esc(m.name)}</div>
        <ul class="why">${m.reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul></div>`).join('')}</div>
    ${crew.alternates.length ? `<div class="alts mini">If someone can't make it:
      ${crew.alternates.map(m => `<span class="link alt" data-id="${esc(m.id)}">${esc(m.name)}</span>`).join(', ')}</div>` : ''}
    <div class="crewact"><button type="button" class="btn another crew-others">Suggest different people</button></div>
    <div class="draft plain">
      <div class="dtop"><span class="who">The ask</span>${crew.when_label
        ? `<span class="kind">${esc(crew.when_label)}</span>` : ''}</div>
      <textarea class="dtext" rows="2" aria-label="Invitation to send">${esc(crew.draft)}</textarea>
      <div class="dact">
        <button type="button" class="btn primary crew-copy">Copy</button>
        <button type="button" class="btn another crew-again">Another version</button>
        <span class="dnote" role="status"></span>
      </div></div></div>`;
  fitText(out.querySelector('.dtext'));
  out.querySelectorAll('.link[data-id]').forEach(el => el.onclick = () => openPerson(el.dataset.id));
  out.querySelector('.crew-copy').onclick = async () => {
    const ta = out.querySelector('.dtext'), note = out.querySelector('.dnote');
    try {
      await navigator.clipboard.writeText(ta.value);
      note.textContent = 'Copied ✓ — start a group in Messages and paste it.';
    } catch {
      ta.select();
      note.textContent = COPY_HINT;
    }
  };
  out.querySelector('.crew-again').onclick = () => {
    S.crew.seed = (S.crew.seed + 1) % Math.max(1, crew.options || 1);
    loadCrew();
  };
  out.querySelector('.crew-others').onclick = () => {
    S.crew.crewSeed = (S.crew.crewSeed + 1) % 3;   // only three starting picks exist
    loadCrew();
  };
}

// ---------------------------------------------------------------- connections + proposals
// Grow a draft box to fit its text instead of hiding the end of the message
// behind a scrollbar -- a draft you can't fully see is one you can't check.
function fitText(ta) {
  if (!ta) return;
  ta.style.height = 'auto';
  ta.style.height = ta.scrollHeight + 2 + 'px';
  ta.oninput = () => { ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 2 + 'px'; };
}

async function copyInto(ta, note) {
  try {
    await navigator.clipboard.writeText(ta.value);
    note.textContent = 'Copied. Paste it into the chat.';
  } catch {
    ta.select();
    note.textContent = COPY_HINT;
  }
}

async function drawCircles() {
  try {
    const host = $('#circles-panel');
    if (!host) return;
    const d = await api('/api/connections');
    S.circles = Object.fromEntries(d.circles.map(c => [c.thread_id, c]));
    const ago = n => n === null ? '—' : n === 0 ? 'active today' : `last active ${n}d ago`;
    const person = (id, name, extra = '') =>
      `<button type="button" class="mchip" data-person="${esc(id)}">${avatar(name, 'sm')}${esc(name)}${extra}</button>`;
    const groups = d.circles.map(c => `
      <div class="circle">
        <div class="ctop"><b>${esc(c.name)}</b><span class="cmeta">${ago(c.days_idle)}</span></div>
        <div class="cmembers">${c.members.map(m => person(m.id, m.name)).join('')}</div>
        <div class="cfoot"><span>${c.messages.toLocaleString()} messages · ${n(c.plans_done, 'plan')} happened${
          c.plans_open ? ` · ${c.plans_open} live` : ''}</span>
          <button type="button" class="btn primary propose" data-thread="${esc(c.thread_id)}">Propose a plan</button></div>
      </div>`).join('');
    const interests = d.interests.map(g => `
      <div class="interest"><div class="itop"><b>${esc(g.interest)}</b><span class="cmeta">${n(g.members.length, 'person', 'people')}</span></div>
        <div class="cmembers">${g.members.map(m => person(m.id, m.name)).join('')}</div></div>`).join('');
    host.innerHTML = `
      <div class="netsec"><div class="nethead">Group chats <span>${d.circles.length}</span></div>
        <div class="circles">${groups || '<div class="pempty">No group chats.</div>'}</div></div>
      <div class="netsec"><div class="nethead">Shared interests <span>${d.interests.length}</span></div>
        <div class="pdef">What stands out in each person's thread compared with your others, not just what everyone talks about.</div>
        <div class="interests">${interests || '<div class="pempty">Not enough shared topics yet.</div>'}</div></div>
      ${d.mention_links ? `<div class="pfoot">${d.mention_links} connection${d.mention_links === 1 ? '' : 's'} found from people coming up by name in other chats. They show on each person's profile.</div>` : ''}`;
    host.querySelectorAll('.mchip[data-person]').forEach(el => el.onclick = () => openPerson(el.dataset.person));
    host.querySelectorAll('.propose').forEach(b => b.onclick = () => {
      const c = S.circles[b.dataset.thread];
      openProposal(c, c.members.map(m => m.id), 0);
    });
  } catch {
    const box = $('#circles-panel');
    if (box) box.innerHTML = `<div class="empty">Couldn't load who's connected's connected. <button type="button" class="linkish" onclick="drawCircles()">Try again</button></div>`;
  }
}

// ---------------------------------------------------------------- filtered threads
async function drawFiltered() {
  const box = $('#filtered');
  if (!box) return;
  const d = await api('/api/filtered');
  if (!d.threads.length) {
    box.innerHTML = '<div class="pempty">Nothing set aside. Every thread looks like a real conversation.</div>';
    return;
  }
  const status = t => !t.automated ? 'restored by you'
    : t.decided_by === 'you' ? 'set aside by you' : `looks automated · ${Math.round(t.score * 100)}%`;
  box.innerHTML = `<div class="filts">${d.threads.map(t => `
    <div class="filt ${t.automated ? '' : 'restored'}">
      <div class="ctop"><b>${esc(t.name)}</b><span class="cmeta">${status(t)}</span></div>
      ${t.sample ? `<div class="detail">“${esc(t.sample)}”</div>` : ''}
      ${t.reasons.length ? `<ul class="pp-why">${t.reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}
      <div class="dact"><button type="button" class="btn ${t.automated ? 'primary' : 'ghost'} verdict"
        data-tid="${esc(t.thread_id)}" data-real="${t.automated}">${t.automated ? "It's a real person" : 'Set aside again'}</button>
        <span class="dnote" role="status"></span></div>
    </div>`).join('')}</div>`;
  box.querySelectorAll('.verdict').forEach(b => b.onclick = async () => {
    b.disabled = true;
    const res = await postJSON(`/api/threads/${encodeURIComponent(b.dataset.tid)}/verdict`,
      { real: b.dataset.real === 'true' });
    if (!res.ok) { b.disabled = false; b.nextElementSibling.textContent = res.error; return; }
    await refreshData();
    drawFiltered();
  });
}

// ---------------------------------------------------------------- a thank-you for one person
function wireThanks(cid, seed) {
  const btn = $('#thank-btn');
  if (!btn) return;
  btn.onclick = async () => {
    btn.disabled = true;
    const box = $('#thankbox');
    let res;
    try {
      res = await api(`/api/people/${encodeURIComponent(cid)}/gratitude?seed=${seed}`);
    } catch {
      btn.disabled = false;
      if (box && !box.querySelector('.thank-err')) box.insertAdjacentHTML('beforeend',
        `<div class="pdef thank-err" role="status">Couldn't draft one just now. Try again.</div>`);
      return;
    }
    if (!res.draft) { box.innerHTML = `<div class="pdef">${esc(res.reason)}</div>`; return; }
    const d = res.draft;
    box.innerHTML = `<div class="draft">
      <div class="dtop"><span class="who">A thank-you for ${esc(shortName(d.who))}</span>
        <span class="kind">${DRAFT_KIND[d.kind] || ''}</span></div>
      <div class="why">${esc(d.why)}</div>
      <textarea class="dtext" id="thank-text" aria-label="Thank-you draft">${esc(d.draft)}</textarea>
      <div class="dact"><button type="button" class="btn primary" id="thank-copy">Copy</button>
        ${d.options > 1 ? '<button type="button" class="btn" id="thank-next">Another version</button>' : ''}
        <span class="dnote" id="thank-note" role="status"></span></div></div>`;
    fitText($('#thank-text'));
    $('#thank-copy').onclick = () => copyInto($('#thank-text'), $('#thank-note'));
    const next = $('#thank-next');
    if (next) next.onclick = async () => {
      box.innerHTML = '<button type="button" class="btn ghost" id="thank-btn">Draft a thank-you</button>';
      wireThanks(cid, (seed + 1) % d.options);
      $('#thank-btn').click();
    };
  };
}

// ---------------------------------------------------------------- locations
// A guess is a question the table was asking without giving you anywhere to
// answer it. Now the guess is the button: press it and it's settled.
function locTag(loc, cid) {
  if (!loc) return '<span class="num dim">—</span>';
  if (loc.source === 'you') return `<span class="loc">${esc(loc.place)}</span>`;
  const why = esc('From messages: “' + loc.quote + '”');
  if (!cid) return `<span class="loc guess" title="${why}">${esc(loc.place)}?</span>`;
  return `<button type="button" class="loc guess ask" data-loc-for="${esc(cid)}"
    data-place="${esc(loc.place)}"
    title="${why} — press to confirm">${esc(loc.place)}?</button>`;
}

async function confirmLocation(btn) {
  const cid = btn.dataset.locFor, place = btn.dataset.place;
  // Settled on screen before the recompute behind it finishes, the same way
  // archiving is. If the save is refused, the re-render puts the guess back.
  const settled = document.createElement('span');
  settled.className = 'loc';
  settled.textContent = place;
  btn.replaceWith(settled);
  toast(`${place} it is.`);
  await saveLocation(cid, { place });
  if (S.view === 'people') viewPeople();
}

function locationNotice(o) {
  const l = o.locations || { unknown: 0, to_confirm: 0 };
  const n = l.unknown + l.to_confirm;
  if (!n) return '';
  const detail = l.to_confirm ? ` (${l.to_confirm} with a guess to confirm)` : '';
  return `<div class="notice"><div><b>${n} of the people you talk to most ${n === 1 ? "doesn't" : "don't"} have a confirmed location${detail}.</b>
    Add where they live so groups and plans can use it.</div>
    <button type="button" class="btn primary add-locations">Add locations</button></div>`;
}

async function saveLocation(cid, body) {
  const res = await postJSON(`/api/contacts/${encodeURIComponent(cid)}/location`, body);
  if (res.ok) await refreshData();
  return res;
}

function renderLocation(cid, loc, unknown) {
  const box = $('#locline');
  if (!box) return;
  const view = () => {
    if (loc && loc.source === 'you') {
      box.innerHTML = `Lives in <b>${esc(loc.place)}</b> <button type="button" class="linkish" data-act="edit">Change</button>`;
    } else if (loc) {
      box.innerHTML = `${loc.confidence === 'likely' ? 'Might live' : 'Probably lives'} in <b>${esc(loc.place)}</b>
        <span class="quote">from “${esc(loc.quote)}”</span>
        <button type="button" class="linkish" data-act="confirm">That's right</button><button type="button" class="linkish" data-act="edit">Change</button>`;
    } else {
      box.innerHTML = `${unknown ? "Location: you said you don't know" : 'Location unknown'}
        <button type="button" class="linkish" data-act="edit">Add</button>`;
    }
    box.querySelectorAll('[data-act]').forEach(b => b.onclick = async () => {
      if (b.dataset.act === 'edit') return edit();
      const res = await saveLocation(cid, { place: loc.place });
      if (res.ok) { loc = res.data.location; unknown = res.data.unknown; view(); }
    });
  };
  const edit = () => {
    box.innerHTML = `<form class="locform">
      <input class="search" maxlength="60" placeholder="City or area" aria-label="Where they live" value="${loc ? esc(loc.place) : ''}">
      <button class="btn primary" type="submit">Save</button>
      <button class="btn ghost" type="button" data-act="unknown">Don't know</button>
      <button class="btn ghost" type="button" data-act="cancel">Cancel</button>
      <span class="dnote" role="status"></span></form>`;
    const form = box.querySelector('form'), note = box.querySelector('.dnote');
    form.querySelector('input').focus();
    form.onsubmit = async e => {
      e.preventDefault();
      const res = await saveLocation(cid, { place: form.querySelector('input').value });
      if (!res.ok) { note.className = 'dnote err'; note.textContent = res.error; return; }
      loc = res.data.location; unknown = res.data.unknown; view();
    };
    form.querySelector('[data-act="unknown"]').onclick = async () => {
      const res = await saveLocation(cid, { unknown: true });
      if (res.ok) { loc = res.data.location; unknown = res.data.unknown; view(); }
    };
    form.querySelector('[data-act="cancel"]').onclick = view;
  };
  view();
}

async function openLocations() {
  const list = await api('/api/locations/ask');
  $('#drawer').innerHTML = `
    <button type="button" class="close" aria-label="Close">&times;</button>
    <h3>Where do they live?</h3>
    <div class="headline">The people you talk to most, a few at a time. Confirm a guess, type a place,
      or say you don't know and it won't ask again. Saved on this Mac, encrypted with your data.</div>
    ${list.length ? list.map(a => `
      <div class="nameme" data-id="${esc(a.id)}">
        <div class="ctop"><b>${esc(a.name)}</b><span class="tag s-${a.state}">${STATE_LABEL[a.state] || ''}</span></div>
        ${a.suggestion ? `<div class="pdef">From their messages: <b>${esc(a.suggestion.place)}</b>
          (${esc(a.suggestion.confidence)}) · “${esc(a.suggestion.quote)}”</div>` : ''}
        <form class="nm-form">
          ${a.suggestion ? `<button class="btn primary" type="button" data-act="yes">Yes, ${esc(a.suggestion.place)}</button>` : ''}
          <input class="search nm-input" maxlength="60" placeholder="${a.suggestion ? 'Somewhere else' : 'City or area'}"
            aria-label="Where ${esc(a.name)} lives" autocomplete="off">
          <button class="btn ${a.suggestion ? '' : 'primary'}" type="submit">Save</button>
          <button class="btn ghost" type="button" data-act="unknown">Don't know</button>
          <span class="dnote" role="status"></span>
        </form>
      </div>`).join('') : '<div class="empty">Everyone you talk to most has a location, or you\'ve said you don\'t know.</div>'}`;
  $('#drawer .close').onclick = () => { closeDrawer(); go(S.view); };
  openDrawer();
  $('#drawer').scrollTop = 0;
  $('#drawer').querySelectorAll('.nameme').forEach(card => {
    const form = card.querySelector('form'), note = card.querySelector('.dnote');
    const done = res => {
      if (!res.ok) { note.className = 'dnote err'; note.textContent = res.error; return; }
      note.className = 'dnote';
      note.textContent = res.data.unknown ? "Got it. It won't ask again." : `Saved: ${res.data.location.place}.`;
      card.classList.add('named');
      form.querySelectorAll('button, input').forEach(el => { el.disabled = true; });
    };
    const a = list.find(x => x.id === card.dataset.id);
    const yes = form.querySelector('[data-act="yes"]');
    if (yes) yes.onclick = async () => done(await saveLocation(a.id, { place: a.suggestion.place }));
    form.querySelector('[data-act="unknown"]').onclick = async () => done(await saveLocation(a.id, { unknown: true }));
    form.onsubmit = async e => {
      e.preventDefault();
      done(await saveLocation(a.id, { place: form.querySelector('.nm-input').value }));
    };
  });
}

// ---------------------------------------------------------------- naming
async function refreshData() {
  S.overview = await api('/api/overview');
  S.people = await api('/api/people');
  $('#c-people').textContent = S.overview.contacts;
  syncFoot();
}

async function openNaming() {
  const list = await api('/api/unnamed');
  $('#drawer').innerHTML = `
    <button type="button" class="close" aria-label="Close">&times;</button>
    <h3>Who are these people?</h3>
    <div class="headline">Real conversations with numbers that aren't in your Contacts. A name you give
      is saved on this Mac, encrypted with the rest of your data, and kept if you import again.
      Your Contacts app isn't changed.</div>
    ${list.length ? list.map(u => `
      <div class="nameme" data-id="${esc(u.id)}">
        <div class="ctop"><b>${esc(u.label)}</b><span class="cmeta">${n(u.messages.toLocaleString(), 'message')}${
          u.last ? ' · last ' + esc(u.last.slice(0, 10)) : ''}</span></div>
        ${u.groups.length ? `<div class="pdef">Also in: ${u.groups.map(esc).join(', ')}</div>` : ''}
        <div class="msgs nm-recent">${u.recent.map(m =>
          `<div class="msg ${m.mine ? 'mine' : 'them'}">${esc(m.text)}</div>`).join('')}</div>
        <form class="nm-form">
          <input class="search nm-input" maxlength="60" placeholder="Their name"
            aria-label="Name for ${esc(u.label)}" autocomplete="off">
          <button class="btn primary" type="submit">Save</button>
          <span class="dnote" role="status"></span>
        </form>
      </div>`).join('') : '<div class="empty">Everyone you talk to has a name.</div>'}`;
  $('#drawer .close').onclick = () => { closeDrawer(); go(S.view); };
  openDrawer();
  $('#drawer').scrollTop = 0;
  $('#drawer').querySelectorAll('.nameme').forEach(card => {
    const form = card.querySelector('.nm-form'), input = card.querySelector('.nm-input'),
      note = card.querySelector('.dnote');
    form.onsubmit = async e => {
      e.preventDefault();
      const res = await postJSON(`/api/contacts/${encodeURIComponent(card.dataset.id)}/name`,
        { name: input.value });
      if (!res.ok) { note.className = 'dnote err'; note.textContent = res.error; return; }
      note.className = 'dnote';
      note.textContent = `Saved as ${res.data.name}.`;
      card.querySelector('.ctop b').textContent = res.data.name;
      input.value = '';
      card.classList.add('named');
      await refreshData();
    };
  });
}

const BASIS = { revive: 'revives a plan they wanted', repeat: 'something you do together', fresh: 'a simple start' };

async function openProposal(circle, selected, seed) {
  const everyone = selected.length === circle.members.length;
  const qs = new URLSearchParams({ members: selected.join(','), seed: String(seed) });
  if (everyone) qs.set('thread_id', circle.thread_id);   // a subset isn't "the group chat"
  const p = await api('/api/proposals?' + qs);
  $('#drawer').innerHTML = `
    <button type="button" class="close" aria-label="Close">&times;</button>
    <h3>A plan for ${esc(circle.name)}</h3>
    <div class="headline">A starting point built from this group's own history. Change who's invited,
      edit the wording, then copy it into the chat yourself.</div>
    <div class="pp-label">Who's invited</div>
    <div class="cmembers">${circle.members.map(m => {
      const on = selected.includes(m.id);
      return `<button type="button" class="mchip toggle ${on ? 'on' : ''}" aria-pressed="${on}" data-id="${esc(m.id)}">${esc(m.name)}</button>`;
    }).join('')}</div>
    <div class="proposal">
      <div class="pp-act">${esc(p.activity.charAt(0).toUpperCase() + p.activity.slice(1))}
        <span class="pp-basis">${BASIS[p.basis] || ''}</span></div>
      <div class="pp-when">${esc(p.when_label)}</div>
      <ul class="pp-why">${p.reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul>
      ${p.alternatives.length ? `<div class="pp-alt">Other ideas from your history: ${p.alternatives.map(esc).join(', ')}</div>` : ''}
    </div>
    <div class="pp-label">Draft</div>
    <textarea class="dtext" id="pp-text" rows="3" aria-label="Draft plan message">${esc(p.draft)}</textarea>
    <div class="dact">
      <button type="button" class="btn primary" id="pp-copy">Copy</button>
      ${p.options > 1 ? '<button type="button" class="btn" id="pp-next">Another idea</button>' : ''}
      <span class="dnote" id="pp-note" role="status"></span>
    </div>`;
  $('#drawer .close').onclick = closeDrawer;
  openDrawer();
  $('#drawer').querySelectorAll('.mchip.toggle').forEach(b => b.onclick = () => {
    const id = b.dataset.id;
    const next = selected.includes(id) ? selected.filter(x => x !== id) : [...selected, id];
    if (!next.length) { $('#pp-note').textContent = 'Keep at least one person invited.'; return; }
    openProposal(circle, circle.members.map(m => m.id).filter(x => next.includes(x)), 0);
  });
  fitText($('#pp-text'));
  $('#pp-copy').onclick = () => copyInto($('#pp-text'), $('#pp-note'));
  const nx = $('#pp-next');
  if (nx) nx.onclick = () => openProposal(circle, selected, (seed + 1) % p.options);
}

// ---------------------------------------------------------------- plans panel
const firstNames = list => list.map(n => esc(shortName(n))).join(', ');

function planRow(r) {
  const c = STATE_COLOR[r.state] || PAINT.unclear;
  const who = r.is_group ? `${esc(r.thread_name)} · ${firstNames(r.people)}` : firstNames(r.people);
  return `<div class="pr" tabindex="0" data-plan="${esc(r.id)}">
    <span class="st" style="background:${c}22;color:${c}">${r.state}</span>
    <div class="pmain"><b>${esc(r.activity)}</b><span class="pwho" title="${who}">${who}</span></div>
    <span class="pwhen">${r.when_text ? esc(r.when_text) : r.days_idle + 'd ago'}</span></div>`;
}

async function drawPlans() {
  try {
    const host = $('#plans-panel');
    if (!host) return;
    const d = await api('/api/plans/overview'), k = d.counts;
    S.planRows = Object.fromEntries([...d.confirmed, ...d.in_motion, ...d.revive].map(r => [r.id, r]));
    const col = (id, color, title, def, rows, empty) => `
      <section class="pcol" id="${id}">
        <div class="phead"><span class="dot" style="background:${color}"></span><b>${title}</b>
          <span class="cnt">${rows.length}</span></div>
        <div class="pdef">${def}</div>
        ${rows.length ? rows.map(planRow).join('') : `<div class="pempty">${empty}</div>`}
      </section>`;
    const groups = d.groups.length ? d.groups.map(g => `
      <div class="pgroup"><div class="pgname">${esc(g.name)}</div>
        <div class="pgmem">${firstNames(g.members)}</div>
        ${g.plans.map(planRow).join('')}</div>`).join('')
      : '<div class="pempty">No live plans in any group chat.</div>';
    const revive = S.showAllRevive ? d.revive : d.revive.slice(0, 6);
    host.innerHTML = `
      <div class="pgrid">
        ${col('plans-confirmed', PAINT.adds, 'Confirmed', 'Someone said yes, or it has a date.',
              d.confirmed, 'Nothing confirmed right now.')}
        ${col('plans-motion', PAINT.accent, 'Still being discussed', 'Floated or proposed, no firm yes yet.',
              d.in_motion, 'Nothing under discussion right now.')}
        <section class="pcol" id="plans-groups">
          <div class="phead"><span class="dot" style="background:${PAINT.plan}"></span><b>Between groups</b>
            <span class="cnt">${d.groups.reduce((n, g) => n + g.plans.length, 0)}</span></div>
          <div class="pdef">Live plans in group chats, and who's in each group.</div>
          ${groups}
        </section>
      </div>
      <section class="previve" id="plans-revive">
        <div class="phead"><span class="dot" style="background:${PAINT.stale}"></span><b>Worth reviving</b>
          <span class="cnt">${k.revive}</span></div>
        <div class="pdef">Wanted in the last ${n(d.revive_window_days, 'day')}, and nobody picked a day.</div>
        <div class="rgrid">${revive.map(planRow).join('') || '<div class="pempty">Nothing stalled recently.</div>'}</div>
        ${d.revive.length > 6 ? `<button type="button" class="btn ghost more-revive" aria-expanded="${!!S.showAllRevive}">${S.showAllRevive ? 'Show fewer' : `Show all ${d.revive.length}`}</button>` : ''}
        <div class="pfoot">Not shown: ${k.lapsed} older plans that quietly lapsed, ${k.done} that happened,
          and ${k.dropped} that someone called off.</div>
      </section>`;
    host.querySelectorAll('.pr').forEach(el => {
      const open = () => openPlan(el.dataset.plan);
      el.onclick = open;
      el.onkeydown = e => { if (e.key === 'Enter') open(); };
    });
    const more = host.querySelector('.more-revive');
    if (more) more.onclick = () => { S.showAllRevive = !S.showAllRevive; drawPlans(); };
    settleJump();              // a count on Now may have been aiming at one of these
  } catch {
    const box = $('#plans-panel');
    if (box) box.innerHTML = `<div class="empty">Couldn't load plans. <button type="button" class="linkish" onclick="drawPlans()">Try again</button></div>`;
  }
}

async function drawOverall() {
  const d = await api('/api/series?weeks=' + S.weeks);
  const host = $('#rate');
  if (!host) return;
  mountChart(host, d, { height: 168, aria: 'Messages per week across all threads' });
  const sm = d.summary;
  $('#rate-head').textContent = `${n(sm.recent_avg, 'message')} a week`;
  $('#rate-sub').textContent = sm.change === null ? 'across every thread'
    : `across every thread · ${sm.change > 0 ? '+' : ''}${sm.change}% against the previous quarter`;
}

// ---------------------------------------------------------------- people
// [sort key, heading, cell, what the heading means]. The fourth is new: these
// are scores with names you would have to be told, and the only explanation
// lived inside the profile, which you had to already be curious enough to open.
const COLS = [
  ['name', 'Person', p => `<span class="pcell">${avatar(p.name)}<span class="name">${esc(p.name)}</span></span>`,
    'Who the thread is with.'],
  ['state', 'Connection', p => `<span class="tag s-${p.state}">${STATE_LABEL[p.state]}</span>`,
    'Shorthand for how this thread is running lately — not a grade on the person.'],
  ['vibe', 'Vibe', p => `<span class="vcol">${esc(p.vibe ? p.vibe.name : '—')}</span>`,
    'The tone that stands out with them, set against your other threads.'],
  ['location', 'Location', p => locTag(p.location, p.contact_id),
    'Where they live. Yours if you said so; otherwise a guess from their messages, with a "?" — press it to confirm.'],
  ['trend', 'Trend', p => p.trend === 'none' ? '<span class="num dim">—</span>'
    : `<span class="tag t-${p.trend}">${p.trend}</span>`,
    'Whether you have been talking more or less than usual lately.'],
  ['msgs_per_week_recent', 'Msgs/wk', p => `<span class="num">${p.msgs_per_week_recent.toFixed(1)}</span>`,
    'Messages a week between you, recently.'],
  ['warmth', 'Warmth', p => bar(p.warmth, PAINT.adds, p),
    'Affection, laughter, reactions, and them asking how you are. 0–100.'],
  ['importance', 'Importance', p => bar(p.importance, PAINT.accent, p),
    'How much of your messaging life this thread is at the moment. 0–100.'],
  ['self_editing', 'Your care level', p => bar(p.self_editing, p.self_editing > 55 ? PAINT.neutral : PAINT.dim, p),
    'How much more carefully you write to them than you do to everyone else.'],
  ['open_plans', 'Plans', p => `<span class="num">${p.open_plans || '·'}</span>`,
    'Things the two of you have talked about doing that have not happened yet.'],
  // Who you have not spoken to longest is the question this app exists to answer,
  // and until now it was the one thing the table could not be sorted by.
  ['last_contact', 'Last spoke', p => `<span class="num dim">${esc(ago(p.last_contact))}</span>`,
    'When the most recent message either way was.'],
  ['their_median_reply_s', 'They reply', p => `<span class="num${p.their_median_reply_s === null
    ? ' dim" title="You two have never replied to each other inside the window' : '"'}>${
    esc(secs(p.their_median_reply_s))}</span>`,
    'Typical time they take to answer. A dash means the two of you have never replied to each other inside the window.'],
  ['my_median_reply_s', 'You reply', p => `<span class="num${p.my_median_reply_s === null
    ? ' dim" title="You two have never replied to each other inside the window' : '"'}>${
    esc(secs(p.my_median_reply_s))}</span>`,
    'Typical time you take to answer. A dash means there is nothing to time.'],
  // Its own key: sharing "trend" put the sort arrow on two headers at once and made
  // clicking the second reverse the first instead of sorting by itself.
  [null, 'Last 20 weeks', p => spark(p.spark), 'Messages a week, drawn as a shape.'],
];
const THIN = 0.35;        // the app's own confidence, below which it is guessing
const THIN_READING = 0.2; // and below this it says so in words, as the server does
const bar = (v, c, p) => (p && p.confidence < THIN)
  ? `<span class="num dim" title="Too little between you to read — ${
      p.total_messages} message${p.total_messages === 1 ? '' : 's'} in all">·</span>`
  : `<span class="num">${v.toFixed(0)}</span>
  <div class="bar"><i style="width:${Math.min(100, v)}%;background:${c}"></i></div>`;

function viewPeople() {
  const noticing = (S.overview.suggestions || []).filter(
    a => !['reconnect', 'revive_plan', 'check_in'].includes(a.kind));
  // Almost half these rows are threads with too little in them to say anything
  // about. Still here, one click away, but not in front of the ones that read.
  const thin = S.people.filter(p => p.confidence < THIN);
  const rows = [...(S.showThin ? S.people : S.people.filter(p => p.confidence >= THIN))]
    .sort((a, b) => {
    const k = S.sort;
    const key = p => k === 'vibe' ? (p.vibe?.name ?? '') : k === 'location' ? (p.location?.place ?? '') : p[k];
    const x = key(a), y = key(b);
    // Reversible, because the end of this list that is worth looking at is often the
    // bottom: who you hear from least, who waits longest, who has gone quietest.
    return (typeof x === 'string' ? String(x).localeCompare(y) : y - x) * (S.sortDir || 1);
  });
  $('#main').innerHTML = `
    <h1>People</h1>
    <div class="sub">Everyone you talk to, and how things are going with them lately.
      This is read from when and how you text, nothing more — it's a nudge to think of
      someone, not a verdict on them. Open anyone to see where each number came from.</div>
    ${S.overview.unnamed ? `<div class="notice"><div><b>${S.overview.unnamed} without a name.</b>
      Name them so you can tell who's who.</div><button type="button" class="btn primary name-them">Name them</button></div>` : ''}
    ${locationNotice(S.overview)}
    <div class="chartbox"><div class="chead">
      <div><b id="rate-head">—</b><span id="rate-sub">across every thread</span></div>
      <div class="ranges">${[20, 40, 80].map(w =>
        `<span class="chip sm ${S.weeks === w ? 'on' : ''}" data-w="${w}">${w}w</span>`).join('')}</div>
    </div><div class="plot" id="rate"></div></div>
    ${noticing.length ? `<h2>Worth noticing</h2>
    <div class="sub tight">Where the pattern is worth a second look rather than a message.</div>
    <div class="cards">${noticing.map(a => `
      <div class="card ${a.tone}" data-id="${esc(a.target_id)}" data-kind="${a.kind}">
        <div class="top"><span class="cwho">${avatar(a.who)}<span class="who">${esc(a.who)}</span></span>
          <span class="kind">${esc(NOTICE_KIND[a.kind] || a.kind.replace('_', ' '))}</span></div>
        <div class="note">${esc(a.observation)}</div>
        <div class="act">${esc(a.action)}</div>
      </div>`).join('')}</div>` : ''}
    <h2>Everyone</h2>
    <div class="peoplebar">
      <input type="search" id="people-find" class="exfind" autocomplete="off"
        placeholder="Find someone…" aria-label="Filter these people">
      <label class="sortpick"><span>Sort</span><select id="people-sort"
        aria-label="Sort these people by">${COLS.filter(c => c[0]).map(c =>
          `<option value="${c[0]}"${S.sort === c[0] ? ' selected' : ''}>${esc(c[1])}</option>`
        ).join('')}</select></label>
      <span class="hint" id="people-count"></span>
      ${thin.length ? `<button type="button" class="btn ghost sm" id="t-thin" aria-expanded="${!!S.showThin}">${S.showThin ? 'Hide' : 'Show'} ${
        n(thin.length, 'thread')} with too little to read</button>` : ''}
    </div>
    <div id="archwrap"></div>
    ${rows.length ? '' : `<div class="notice quiet"><div><b>Nobody here.</b>
      ${archivedIds().length ? 'Everyone you talk to is archived — Show above brings them back.'
        : 'No conversations in this window yet.'}</div></div>`}
    <div class="tablewrap"${rows.length ? '' : ' hidden'}><table><thead><tr>${COLS.map(c => {
      const on = c[0] && S.sort === c[0], up = (S.sortDir || 1) !== 1;
      if (!c[0]) return `<th class="nosort" title="${esc(c[3] || '')}">${c[1]}</th>`;
      return `<th aria-sort="${on ? (up ? 'ascending' : 'descending') : 'none'}"><button
        type="button" class="sortb" data-k="${c[0]}" title="${esc(c[3] || '')}"
        >${c[1]}${on ? (up ? ' ↑' : ' ↓') : ''}</button></th>`;
    }).join('')}</tr></thead>
    <tbody>${rows.map(p => `<tr data-id="${esc(p.contact_id)}">${COLS.map(c =>
      `<td class="c-${c[0] || 'spark'}" data-label="${esc(c[1])}">${c[2](p)}</td>`
      ).join('')}</tr>`).join('')}</tbody></table></div>`;
  document.querySelectorAll('.sortb').forEach(b => b.onclick = () => {
    if (S.sort === b.dataset.k) S.sortDir = (S.sortDir || 1) * -1;
    else { S.sort = b.dataset.k; S.sortDir = 1; }
    viewPeople();
    // The table was replaced underneath the press; put focus back on the same
    // header so a keyboard can keep going.
    document.querySelector(`.sortb[data-k="${CSS.escape(b.dataset.k)}"]`)?.focus();
    say(`Sorted by ${b.textContent.replace(/[↑↓]/g, '').trim()}`);
  });
  // Before the row handlers, so stopping the click here stops it opening the
  // profile too -- and the keydown, because Enter on a button is a click that
  // the row would otherwise also hear.
  document.querySelectorAll('button[data-loc-for]').forEach(b => {
    b.onclick = e => { e.stopPropagation(); confirmLocation(b); };
    b.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') e.stopPropagation(); };
  });
  document.querySelectorAll('tbody tr').forEach(tr => {
    tr.onclick = () => openPerson(tr.dataset.id);
    tr.tabIndex = 0;                      // plan rows already answer Enter; these should too
    tr.onkeydown = e => { if (e.key === 'Enter') openPerson(tr.dataset.id); };
    // contextmenu, not a right-click listener: the keyboard's menu key raises it too.
    tr.oncontextmenu = e => {
      e.preventDefault();
      rowMenu(e, tr.dataset.id, tr.querySelector('td').textContent.trim());
    };
  });
  paintArchBar();
  document.querySelectorAll('.cards .card').forEach(c => c.onclick = () => openPerson(c.dataset.id));
  document.querySelectorAll('.ranges .chip').forEach(c =>
    c.onclick = () => { S.weeks = +c.dataset.w; viewPeople(); });
  drawOverall();
  // The sortable headers go with the thead on a phone, so sorting gets its own
  // control there rather than quietly disappearing.
  const pick = $('#people-sort');
  if (pick) pick.onchange = () => { S.sort = pick.value; S.sortDir = 1; viewPeople(); };
  // Filtering hides rows rather than re-rendering, so what you have typed keeps focus.
  const find = $('#people-find'), count = $('#people-count');
  const thinBtn = $('#t-thin');
  if (thinBtn) thinBtn.onclick = () => { S.showThin = !S.showThin; viewPeople(); };
  const applyFind = () => {
    const q = find.value.trim().toLowerCase();
    const trs = [...document.querySelectorAll('tbody tr')];
    let shown = 0;
    for (const tr of trs) {
      // The name only. The box says "find someone", and matching every cell made
      // two letters pull up people whose vibe happened to contain them.
      const hit = !q || (tr.querySelector('td')?.textContent || '').toLowerCase().includes(q);
      tr.hidden = !hit;
      shown += hit;
    }
    // Folding the unreadable threads away made the search lie: typing a name
    // that is in those 47 returned "0 of 57" and nothing said where they went.
    const alsoHidden = q && !S.showThin
      ? thin.filter(p => p.name.toLowerCase().includes(q)).length : 0;
    count.textContent = !q ? ''
      : alsoHidden && !shown ? `not here — ${n(alsoHidden, 'match', 'matches')} among the folded-away threads`
      : alsoHidden ? `${shown} of ${trs.length} · ${alsoHidden} more folded away`
      : `${shown} of ${trs.length}`;
    if (thinBtn) thinBtn.classList.toggle('nudge', !!alsoHidden);
  };
  find.oninput = applyFind;
  if (S.peopleQ) { find.value = S.peopleQ; applyFind(); }
  find.addEventListener('input', () => { S.peopleQ = find.value; });
  document.querySelectorAll('.name-them').forEach(b => b.onclick = openNaming);
  document.querySelectorAll('.add-locations').forEach(b => b.onclick = openLocations);
}

// Putting someone away is the same switch the privacy screen used to offer as a
// wall of checkboxes: they are dropped before any analysis runs, including out of
// group threads so they cannot come back through group statistics. It says nothing
// to Messages and changes nothing about your actual history.
const archivedIds = () => (S.overview?.privacy?.excluded_contact_ids) || [];

// The caller has already taken the row (or the chip) off the screen: recomputing
// everyone's numbers without them takes a second or two, and there is nothing in
// that wait worth watching a row you just archived sit there for. So the list S
// carries moves now, and only goes back if the save is refused.
async function setArchived(ids, note) {
  const before = archivedIds();
  if (S.overview?.privacy) S.overview.privacy.excluded_contact_ids = ids;
  if (note) toast(note);
  let res;
  try {
    res = await postJSON('/api/privacy', { excluded_contact_ids: ids });
  } catch {
    return;                                   // signedOut() has taken the page over
  }
  if (!res.ok && S.overview?.privacy) S.overview.privacy.excluded_contact_ids = before;
  else await refreshData();
  if (S.view === 'people') viewPeople();
}

function archBar() {
  const n = archivedIds().length;
  return n ? `<div class="archbar">
    <span>${n} archived — left out of every profile, plan and draft here.
      Your messages are untouched.</span>
    <button type="button" class="btn ghost" id="arch-show">Show</button>
    <div class="archlist" id="arch-list" hidden></div></div>` : '';
}

function paintArchBar() {
  const wrap = $('#archwrap');
  if (!wrap) return;
  wrap.innerHTML = archBar();
  const show = $('#arch-show');
  if (show) show.onclick = () => drawArchived(show);
}

function toast(text) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = text;
  document.body.append(el);
  say(text);
  setTimeout(() => el.remove(), 3200);
}

// Everything the app says in passing goes through here as well as on screen.
// Cleared first, because setting a live region to the text it already holds
// announces nothing -- archive two people in a row and you'd hear it once.
function say(text) {
  const live = $('#live');
  if (!live) return;
  live.textContent = '';
  setTimeout(() => { live.textContent = text; }, 60);
}

let rowMenuReturn = null;
function closeRowMenu() {
  const m = document.querySelector('.rowmenu');
  if (!m) return;
  m.remove();
  // Back to the row it came from. Without this, dismissing the menu dropped
  // focus to the top of the document and you tabbed the whole table again.
  if (rowMenuReturn?.isConnected) rowMenuReturn.focus();
  rowMenuReturn = null;
}

// Right-click on a row and Remove inside a profile are the same act, so they are
// the same function -- including taking the row away before the recompute lands.
// Safe from the graph view too, where there is no table and nothing to remove.
function archivePerson(id, name) {
  document.querySelector(`tbody tr[data-id="${CSS.escape(id)}"]`)?.remove();
  setArchived([...archivedIds(), id], `${shortName(name)} archived.`);
  paintArchBar();
  $('#people-find')?.dispatchEvent(new Event('input'));   // "13 of 14" was true a moment ago
}

function rowMenu(ev, id, name) {
  closeRowMenu();
  rowMenuReturn = ev.currentTarget instanceof HTMLElement ? ev.currentTarget : document.activeElement;
  const m = document.createElement('div');
  m.className = 'rowmenu';
  m.setAttribute('role', 'menu');
  m.setAttribute('aria-label', `Actions for ${shortName(name)}`);
  m.innerHTML = `<button type="button" role="menuitem">Archive ${esc(shortName(name))}</button>`;
  m.style.left = Math.min(ev.clientX, innerWidth - 220) + 'px';
  m.style.top = Math.min(ev.clientY, innerHeight - 70) + 'px';
  m.querySelector('button').onclick = () => {
    closeRowMenu();
    archivePerson(id, name);
  };
  document.body.append(m);
  m.querySelector('button').focus();
  const onKey = e => {
    if (e.key !== 'Escape') return;
    document.removeEventListener('keydown', onKey);
    closeRowMenu();
  };
  setTimeout(() => {
    document.addEventListener('pointerdown', closeRowMenu, { once: true });
    document.addEventListener('keydown', onKey);
  });
}

async function drawArchived(btn) {
  const box = $('#arch-list');
  if (!box) return;
  if (!box.hidden) { box.hidden = true; btn.textContent = 'Show'; return; }
  box.hidden = false;
  btn.textContent = 'Hide';
  box.innerHTML = '<span class="pempty">Loading…</span>';
  // /api/people only returns who is still in the analysis, so the names of the
  // people who are not have to come from the unfiltered list.
  let all;
  try { all = await api('/api/contacts'); } catch { return; }
  const away = all.filter(c => archivedIds().includes(c.id));
  box.innerHTML = away.length
    ? away.map(c => `<span class="archchip">${avatar(c.name, 'sm')}${esc(c.name)}<button type="button"
        data-id="${esc(c.id)}" title="Bring ${esc(c.name)} back">bring back</button></span>`).join('')
    : '<span class="pempty">Nobody is archived.</span>';
  box.querySelectorAll('button[data-id]').forEach(b => b.onclick = () => {
    b.closest('.archchip')?.remove();
    setArchived(archivedIds().filter(x => x !== b.dataset.id),
                `${shortName(away.find(c => c.id === b.dataset.id)?.name || 'They')} is back.`);
  });
}

// ---------------------------------------------------------------- the phone
// Reading this on a phone means the Mac answers on the network, which is the one
// place the app stops being "nothing leaves this machine". So the screen says so
// plainly, the door is shut unless the server was started with --phone, and a
// code is only on offer for the two minutes after you ask for one.
let phoneTick = null;

async function drawPhone() {
  const box = $('#phonebox');
  if (!box) return;                        // navigated away while the fetch was out
  // On the phone itself there is nothing to administer: pairing is a Mac job, and
  // asking anyway would come back 403 and be read as "not on the network at all".
  if (S.overview.lan?.via === 'device') {
    box.innerHTML = `<div class="notice quiet"><div>You're reading this on a paired
      phone, over your local network. Codes are shown, and phones removed, on the Mac
      — under Privacy there.</div></div>`;
    return;
  }

  let p;
  try { p = await api('/api/phone'); } catch { return; }
  if (!$('#phonebox')) return;

  if (!p.on) {
    box.innerHTML = `<div class="notice quiet"><div>This server is answering
      <b>127.0.0.1 only</b>, so nothing on your network can see it — not even your phone.
      To read it on a phone, stop the server and start it again with
      <code>./mg serve --phone</code>.</div></div>`;
    return;
  }

  const devices = p.devices.length
    ? `<div class="archlist">${p.devices.map(d => `<span class="archchip">${esc(d.name)}
        <button type="button" data-revoke="${esc(d.id)}"
          title="Stop ${esc(d.name)} reading this Mac">remove</button></span>`).join('')}</div>`
    : '<span class="pempty">No phone is paired.</span>';

  box.innerHTML = `
    <div class="notice"><div><b>Answering on your local network.</b>
      Anyone on this Wi-Fi can load the pairing page; only a phone that has typed a
      code gets past it. Your messages stay here — the phone reads what this Mac
      has already worked out.</div></div>
    <ol class="steps">
      <li>On the phone, open <b>${p.addresses.map(a => `<code>${esc(a)}</code>`).join('</b> or <b>')}</b></li>
      <li><b>Add it to the Home Screen first</b>, then open it from there.
        iOS keeps a home-screen app's cookies apart from Safari's, so pairing in
        Safari would only have to be done again.</li>
      <li>Press the button below and type the six digits into the phone.</li>
    </ol>
    ${p.shut_by_guessing && !p.offering ? `<div class="notice"><div><b>Pairing shut itself.</b>
      Five wrong codes were tried from your network, which is what should happen — but
      nobody you meant to let in got through. Show a new code only when your phone is in
      your hand.</div></div>` : ''}
    ${p.offering
      ? `<div class="paircode"><span class="code">${esc(p.code)}</span>
         <span class="left" id="pair-left">${p.seconds_left}s left</span>
         <button type="button" class="btn ghost" id="pair-close">Stop showing it</button></div>`
      : '<button type="button" class="btn primary" id="pair-open">Show a pairing code</button>'}
    <h2>Paired</h2>
    ${devices}`;

  clearInterval(phoneTick);
  if (p.offering) {
    // Counts down on screen rather than only in the server: a code that has quietly
    // expired while you were walking to the phone should say so. Every other second
    // it also asks the Mac, because the thing that spends the code happens on the
    // phone and this screen would otherwise sit there offering a code that is gone.
    let left = p.seconds_left;
    phoneTick = setInterval(async () => {
      const el = $('#pair-left');
      if (!el) return clearInterval(phoneTick);
      left -= 1;
      if (left <= 0) { clearInterval(phoneTick); drawPhone(); return; }
      el.textContent = `${left}s left`;
      if (left % 2) return;
      let now;
      try { now = await api('/api/phone'); } catch { return; }
      if (!now.offering) { clearInterval(phoneTick); refreshFoot(); drawPhone(); }
    }, 1000);
  }

  const open = $('#pair-open'), close = $('#pair-close');
  if (open) open.onclick = async () => { await postJSON('/api/phone/open', {}); drawPhone(); };
  if (close) close.onclick = async () => { await postJSON('/api/phone/close', {}); drawPhone(); };
  box.querySelectorAll('button[data-revoke]').forEach(b => b.onclick = async () => {
    b.closest('.archchip')?.remove();
    await postJSON(`/api/phone/revoke/${b.dataset.revoke}`, {});
    refreshFoot();
    drawPhone();
  });
}

// The footer carries the phone count, so it goes stale whenever that changes.
async function refreshFoot() {
  try { S.overview = await api('/api/overview'); } catch { return; }
  syncFoot();
}

// ---------------------------------------------------------------- drawer
// The drawer covers the page behind a scrim, which makes it a modal -- and it
// used to say none of that. Assistive tech read it as part of the page, focus
// stayed on the row behind it, Tab walked off into the hidden page, and closing
// it dropped focus back to the top. Six callers set the two classes by hand;
// now they call these.
let drawerReturn = null;

function openDrawer() {
  drawerGen++;                       // a plan or a form replacing a person is moving on too
  const d = $('#drawer');
  drawerReturn = document.activeElement;
  d.classList.add('on');
  $('#scrim').classList.add('on');
  d.setAttribute('role', 'dialog');
  d.setAttribute('aria-modal', 'true');
  const title = d.querySelector('h3');
  if (title) {
    title.id = title.id || 'drawer-title';
    d.setAttribute('aria-labelledby', 'drawer-title');
  } else {
    d.setAttribute('aria-label', 'Details');
  }
  (d.querySelector('.close') || d).focus();
}

// Which opening of the drawer is current. Everything that fills it awaits the
// server, and a reply that lands after you have moved on -- to another person,
// a plan, or nothing -- is about someone you are no longer looking at.
let drawerGen = 0;

function closeDrawer() {
  drawerGen++;
  const d = $('#drawer');
  const was = d.classList.contains('on');
  d.classList.remove('on');
  $('#scrim').classList.remove('on');
  d.removeAttribute('role');
  d.removeAttribute('aria-modal');
  // Back to whatever opened it, so Tab carries on from where you were rather
  // than restarting at the top of the page.
  if (was && drawerReturn?.isConnected) drawerReturn.focus();
  drawerReturn = null;
}

// Tab stays inside while it is open; there is nothing behind it to reach.
function trapTab(e) {
  if (e.key !== 'Tab') return;
  const d = $('#drawer');
  if (!d.classList.contains('on')) return;
  const items = [...d.querySelectorAll(
    'a[href],button:not([disabled]),input:not([disabled]),select,textarea,[tabindex]:not([tabindex="-1"])')]
    .filter(el => el.offsetParent !== null);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

async function openPerson(id) {
  if (!id || !id.startsWith('p_')) return;
  // Opening B before A has arrived drew A's chart into B's profile and A's
  // messages under B's name.
  const gen = ++drawerGen;
  let d;
  try {
    d = await api('/api/people/' + id);
  } catch (e) {
    if (gen !== drawerGen || e.message === 'signed out') return;
    $('#drawer').innerHTML = `<button type="button" class="close" aria-label="Close">&times;</button>
      <div class="empty">${esc(e.detail || "That person couldn't be loaded. Reload the page.")}</div>`;
    $('#drawer .close').onclick = closeDrawer;
    openDrawer();
    drawerGen = gen;                           // opening is not moving on
    return;
  }
  if (gen !== drawerGen) return;
  const p = d.profile;
  $('#drawer').innerHTML = `
    <button type="button" class="close" aria-label="Close">&times;</button>
    <h2>${avatar(p.name, 'big')}<span id="pname">${esc(p.name)}</span>
      <button type="button" class="editname" id="edit-name"
        title="Change the name you have for them">edit</button>
      <button type="button" class="editname danger" id="remove-person"
        title="Archive ${esc(p.name)} — leave them out of this app, not out of Messages"
        >remove</button>
      <span class="tag s-${p.state}">${STATE_LABEL[p.state]}</span></h2>
    ${d.handle ? `<div class="handleline">${esc(d.handle)}</div>` : ''}
    <div class="sinceline">Talking since ${esc(since(p.first_contact))}
      <span class="dot-sep">·</span> last message ${esc(ago(p.last_contact))}</div>
    <div class="locline" id="locline"></div>
    <div class="headline">${esc(p.headline)}</div>
    ${p.vibe ? vibeCard(p.vibe, p) : ''}
    <div class="thankbox" id="thankbox"><button type="button" class="btn ghost" id="thank-btn">Draft a thank-you</button></div>
    <div class="metrics">
      <div class="metric"><div class="n">${p.msgs_per_week_recent.toFixed(1)}</div><div class="l">messages a week</div></div>
      <div class="metric"><div class="n">${p.warmth.toFixed(0)}</div><div class="l">warmth</div></div>
      <div class="metric"><div class="n">${p.importance.toFixed(0)}</div><div class="l">importance</div></div>
      <div class="metric"><div class="n">${p.depth.toFixed(0)}</div><div class="l">depth</div></div>
      <div class="metric"><div class="n">${secs(p.their_median_reply_s)}</div><div class="l">${
        p.their_median_reply_s === null ? 'no reply of theirs to time yet'
        : esc(shortName(p.name)) + ' writes back in'}</div></div>
      <div class="metric"><div class="n">${secs(p.my_median_reply_s)}</div><div class="l">${
        p.my_median_reply_s === null ? 'no reply of yours to time yet'
        : 'you write back in'}</div></div>
    </div>
    ${p.confidence < .7 ? `<div class="empty">Only ${p.total_messages.toLocaleString()} messages here, so
      this is a sketch rather than a reading (confidence ${(p.confidence*100).toFixed(0)}%).</div>` : ''}
    <h3>What the numbers say — and what else they could mean</h3>
    ${p.signals.map(sig).join('')}
    <h3>Connected to</h3>
    <div class="cmembers">${d.connections.length ? d.connections.map(x =>
      `<button type="button" class="mchip" data-person="${esc(x.id)}">${avatar(x.name, 'sm')}${esc(x.name)}<small>${esc(x.via.join(', '))}</small></button>`).join('')
      : '<div class="pempty">No group chats together, just your one-to-one thread.</div>'}</div>
    <h3>Plans together</h3>
    <div class="plist">${d.plans.length ? d.plans.map(pl => `
      <div class="prow" tabindex="0" data-plan="${esc(pl.id)}"><span class="st" style="background:${STATE_COLOR[pl.state]}22;
        color:${STATE_COLOR[pl.state]}">${pl.state}</span>
        <span>${esc(pl.title.split(' with ')[0])}</span>
        <span class="when">${esc(pl.when_text || '—')} · ${pl.last_activity.slice(5,10)}</span></div>`).join('')
      : '<div class="empty">No plans found in this thread.</div>'}</div>
    <h3>Messaging rate</h3>
    <div class="chartbox"><div class="chead">
      <div><b id="p-head">—</b><span id="p-sub"></span></div></div>
      <div class="plot" id="p-rate"></div></div>
    <h3>Chat history</h3>
    <div class="hbar">
      <input id="hq" class="search" type="search" placeholder="Search this conversation…"
        autocomplete="off" spellcheck="false">
      <span class="hcount" id="hcount"></span>
    </div>
    <div id="hist"></div>
    ${d.thread_id ? `<div class="drawer-foot">Not a real conversation?
      <button type="button" class="btn ghost hide-thread" data-tid="${esc(d.thread_id)}">Set it aside as automated</button>
      <span class="dnote" role="status"></span></div>` : ''}`;
  $('#drawer .close').onclick = closeDrawer;
  openDrawer();
  drawerGen = gen;
  $('#drawer').scrollTop = 0;
  $('#drawer').querySelectorAll('.mchip[data-person]').forEach(el =>
    el.onclick = () => openPerson(el.dataset.person));
  renderLocation(id, d.location, d.location_unknown);
  // The name is yours to set; the handle underneath it is what the message store
  // calls them and is not editable, so it is shown rather than offered.
  // Reversible, and the toast and the archived bar say how -- so no confirm step
  // between pressing it and seeing it happen.
  $('#remove-person').onclick = () => {
    closeDrawer();
    archivePerson(id, p.name);
  };
  $('#edit-name').onclick = () => {
    const h3 = $('#drawer h3'), label = $('#pname'), btn = $('#edit-name');
    if (h3.querySelector('.namingform')) return;
    label.hidden = btn.hidden = true;
    const form = document.createElement('form');
    form.className = 'namingform';
    form.innerHTML = '<input class="nameinput" maxlength="60" autocomplete="off" '
      + 'aria-label="Their name"><span class="dnote" role="status"></span>';
    h3.prepend(form);
    const input = form.querySelector('input'), note = form.querySelector('.dnote');
    input.value = label.textContent;
    input.focus();
    input.select();
    const close = () => { form.remove(); label.hidden = btn.hidden = false; };
    input.onkeydown = e => { if (e.key === 'Escape') close(); };
    form.onsubmit = async e => {
      e.preventDefault();
      const name = input.value.trim();
      if (!name || name === label.textContent) return close();
      input.disabled = true;
      let res;
      try {
        res = await postJSON(`/api/contacts/${encodeURIComponent(id)}/name`, { name });
      } catch {
        return;                                // signedOut() has taken the page over
      }
      if (!res.ok) { input.disabled = false; note.textContent = res.error; return; }
      label.textContent = res.data.name;
      close();
      await refreshData();
      if (S.view) go(S.view);                  // the list behind still says the old name
    };
  };
  wireThanks(id, 0);
  $('#drawer').querySelectorAll('.prow[data-plan]').forEach(el => {
    el.onclick = () => openPlan(el.dataset.plan);
    el.onkeydown = e => { if (e.key === 'Enter') openPlan(el.dataset.plan); };
  });
  const hide = $('#drawer .hide-thread');
  if (hide) hide.onclick = async () => {
    hide.disabled = true;
    const res = await postJSON(`/api/threads/${encodeURIComponent(hide.dataset.tid)}/verdict`, { real: false });
    if (!res.ok) { hide.disabled = false; hide.nextElementSibling.textContent = res.error; return; }
    closeDrawer();
    await refreshData();
    go(S.view);
  };

  let sd = null;
  try { sd = await api(`/api/people/${id}/series?weeks=40`); } catch { /* the rest still stands */ }
  if (gen !== drawerGen) return;
  const host = $('#p-rate');
  if (host && sd) {
    mountChart(host, sd, { height: 150, aria: `Messages per week with ${p.name}` });
    const sm = sd.summary;
    $('#p-head').textContent = `${sm.recent_avg} a week lately`;
    $('#p-sub').textContent = sm.change === null ? ''
      : `· ${sm.change > 0 ? '+' : ''}${sm.change}% against the previous quarter`;
  }

  H.cid = id; H.q = ''; H.before = null; H.messages = []; H.total = 0;
  let timer;
  $('#hq').oninput = e => {
    clearTimeout(timer);
    const v = e.target.value;
    timer = setTimeout(() => { H.q = v; H.before = null; H.messages = []; loadHistory(); }, 220);
  };
  await loadHistory();
}

// ---------------------------------------------------------------- history
const H = { cid: null, q: '', before: null, messages: [], total: 0, matched: 0, more: false };

async function loadHistory(append = false) {
  const qs = new URLSearchParams({ limit: '60' });
  if (H.q) qs.set('q', H.q);
  if (append && H.before) qs.set('before', H.before);
  // The answer belongs to this person and this search. Typing on, or opening
  // someone else, makes it someone else's.
  const gen = drawerGen, cid = H.cid, q = H.q;
  let d;
  try {
    d = await api(`/api/people/${cid}/history?` + qs);
  } catch {
    if (gen === drawerGen && cid === H.cid && q === H.q) {
      const box = $('#hist');
      if (box) box.innerHTML = COULDNT('this conversation');
    }
    return;
  }
  if (gen !== drawerGen || cid !== H.cid || q !== H.q) return;
  H.messages = append ? [...d.messages, ...H.messages] : d.messages;
  H.total = d.total; H.matched = d.matched; H.more = d.has_more;
  H.before = d.oldest;
  renderHistory();
}

function renderHistory() {
  const box = $('#hist');
  if (!box) return;
  $('#hcount').textContent = H.q
    ? `${H.matched.toLocaleString()} of ${n(H.total.toLocaleString(), 'message')} match`
    : `showing ${H.messages.length} of ${H.total}`;

  if (!H.messages.length) {
    box.innerHTML = '<div class="empty">Nothing in this conversation matches that.</div>';
    return;
  }
  const parts = [];
  if (H.more) parts.push('<button type="button" class="more" id="older">Load older messages</button>');
  let lastDay = null;
  for (const m of H.messages) {
    // A six-hour silence is a new conversation, the same rule the analysis uses.
    const day = m.ts.slice(0, 10);
    if (m.gap > 21600 || day !== lastDay) {
      parts.push(`<div class="daysep"><span>${new Date(m.ts).toLocaleDateString(undefined,
        { weekday: 'short', month: 'short', day: 'numeric' })}</span></div>`);
      lastDay = day;
    }
    parts.push(`<div class="msg ${m.mine ? 'mine' : 'them'}">${hl(m.text)}
      <span class="t">${m.ts.slice(11, 16)}${m.reactions.length
        ? ' · ' + m.reactions.join(' ') : ''}</span></div>`);
  }
  box.innerHTML = `<div class="msgs">${parts.join('')}</div>`;
  const older = $('#older');
  if (older) older.onclick = async () => {
    older.textContent = 'Loading…';
    const keep = box.scrollHeight;
    await loadHistory(true);
    $('#drawer').scrollTop += box.scrollHeight - keep;
  };
}

function hl(text) {
  const out = esc(text);
  if (!H.q) return out;
  const needle = esc(H.q).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return out.replace(new RegExp(needle, 'gi'), m => `<mark>${m}</mark>`);
}

function sig(s) {
  const pct = Math.min(50, Math.abs(s.contribution) / 0.2 * 50);
  const pos = s.contribution >= 0;
  return `<div class="sig">
    <div class="row"><span class="lab">${esc(s.label)}</span>
      <span class="val">${esc(s.display)}</span></div>
    <div class="gauge"><div class="mid"></div>
      <i class="${pos ? 'pos' : 'neg'}" style="${pos ? 'left:50%' : `right:50%`};width:${pct}%"></i></div>
    <div class="note">${esc(s.note)}</div>
    ${s.benign ? `<div class="benign">${esc(s.benign)}</div>` : ''}</div>`;
}

const DIM_LABEL = {
  playful: 'playful', tender: 'tender', lively: 'lively', deep: 'deep',
  steady: 'steady', practical: 'practical', planning: 'planner', night_owl: 'night owl',
};
// "Heavy-hearted", not "negative": a low mood usually means hard things are being
// shared, and the note under the card says so.
const moodWord = m => m >= 35 ? 'upbeat' : m >= 15 ? 'warm' : m > -10 ? 'even-keeled' : 'heavy-hearted';

function vibeCard(v, p) {
  const signed = n => (n > 0 ? '+' : '') + Math.round(n);
  // The signals above this already refuse to quote a percentage off a handful
  // of messages. This line was still doing it: "20% upbeat · 0% heavy" from a
  // five-message thread, directly under the sentence saying there is not
  // enough here to read.
  const thin = p && p.confidence < THIN_READING;
  const dims = Object.keys(DIM_LABEL).map(k => `
    <div class="vd ${v.top.includes(k) ? 'top' : ''}"><span>${DIM_LABEL[k]}</span>
      <i><b style="width:${Math.max(2, v.dimensions[k] || 0)}%"></b></i></div>`).join('');
  return `<div class="vibe">
    <div class="veyebrow">vibe</div>
    <div class="vname">${esc(v.name)}</div>
    <div class="vline">${esc(v.line)}</div>
    <div class="vdims">${dims}</div>
    <div class="vmood">
      <span>mood <b>${moodWord(v.mood)}</b></span>
      <div class="gauge vg"><div class="mid"></div><i class="${v.mood >= 0 ? 'pos' : 'neg'}"
        style="${v.mood >= 0 ? 'left:50%' : 'right:50%'};width:${Math.min(50, Math.abs(v.mood) / 2)}%"></i></div>
      <span>theirs ${signed(v.mood_theirs)} · yours ${signed(v.mood_mine)}</span>
      <span>${thin ? 'too little to tell'
        : `${Math.round(v.positive_share * 100)}% upbeat · ${Math.round(v.heavy_share * 100)}% heavy`}</span>
      <span class="shift">${esc(v.mood_shift)}</span>
    </div>
    <div class="benign">${esc(v.mood_note)}</div>
  </div>`;
}

async function openPlan(id) {
  let d;
  try { d = await api('/api/plans/' + encodeURIComponent(id)); } catch (e) { d = { detail: e.detail }; }
  const drawer = $('#drawer');
  if (!d.id) {
    drawer.innerHTML = `<button type="button" class="close" aria-label="Close">&times;</button>
      <div class="empty">${esc(d.detail || "That plan couldn't be loaded.")}</div>`;
  } else {
    const c = STATE_COLOR[d.state] || PAINT.unclear;
    const word = d.activity.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const mark = t => esc(t).replace(new RegExp(`\\b(${word})\\b`, 'gi'), '<mark>$1</mark>');
    const day = ts => new Date(ts).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
    const time = ts => new Date(ts).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    const person = !d.is_group && d.people.length ? d.people[0] : null;
    drawer.innerHTML = `
      <button type="button" class="close" aria-label="Close">&times;</button>
      <h3>${esc(d.activity)} <span class="tag" style="background:${c}22;color:${c}">${d.state}</span></h3>
      <div class="headline">In <b>${esc(d.thread_name)}</b>${d.when_text ? ` · proposed for <b>${esc(d.when_text)}</b>` : ''}
        · last mentioned ${d.days_idle === 0 ? 'today' : d.days_idle === 1 ? 'yesterday' : d.days_idle + ' days ago'}</div>
      ${d.state === 'stale' ? `<div class="pdef">Wanted, and nobody picked a day. There's often a drafted nudge for
        plans like this under Messages worth sending.</div>` : ''}
      ${d.assumed ? `<div class="pdef">Counted as done because the day came and went. Nobody
        actually said it happened, so this one is a guess — nothing here will thank anyone
        for it.</div>` : ''}
      ${d.first_seen ? `<div class="pdef">First came up ${esc(ago(d.first_seen))}${
        // Only worth saying when it ran on for a while; otherwise both dates are the
        // same sentence twice.
        (new Date(d.last_activity) - new Date(d.first_seen)) / 86400000 >= 2
          ? `, and ran on until ${esc(ago(d.last_activity))}` : ''}.</div>` : ''}
      <h2>Where it came up</h2>
      ${d.mentions.length ? d.mentions.map(g => `
        <div class="daysep"><span>${day(g.started)}</span></div>
        <div class="msgs planmsgs">${g.messages.map(m => `
          <div class="msg ${m.mine ? 'mine' : 'them'} ${m.names_it ? 'seed' : ''}">
            ${d.is_group && !m.mine ? `<span class="who">${esc(shortName(m.who))}</span>` : ''}${mark(m.text)}
            <span class="t">${time(m.ts)}</span></div>`).join('')}</div>`).join('')
        : '<div class="empty">The messages behind this plan are no longer in the data.</div>'}
      <div class="pfoot">Highlighted messages are the ones that name it; the replies around them are how the
        app judged it ${esc(d.state)} (confidence ${Math.round(d.confidence * 100)}%).</div>
      ${person ? `<div class="drawer-foot"><button type="button" class="btn ghost open-person" data-person="${esc(person.id)}">Open
        ${esc(shortName(person.name))}'s profile</button></div>` : ''}`;
    const op = drawer.querySelector('.open-person');
    if (op) op.onclick = () => openPerson(op.dataset.person);
  }
  drawer.querySelector('.close').onclick = closeDrawer;
  openDrawer();
  drawer.scrollTop = 0;
}

// ---------------------------------------------------------------- graph
async function viewGraph() {
  $('#main').innerHTML = `
    <h1>My brainstorm</h1>
    <div class="sub">People and the live plans connecting them, pulled out of message text.
      Turn on stale plans to see what was agreed to and never landed. Drag to rearrange,
      scroll to zoom, hover to isolate, click anything for detail.</div>
    <div class="controls">
      <span class="chip ${S.includeStale ? 'on' : ''}" id="t-stale" role="switch"
        tabindex="0" aria-checked="${!!S.includeStale}">show stale plans</span>
      <span class="focus" role="group" aria-label="Which conversations to draw">${
        [['direct', 'one-to-one'], ['group', 'group chats']].map(([k, label]) =>
          `<span class="chip ${S.ties[k] ? 'on' : ''}" data-tie="${k}"
            role="checkbox" tabindex="0" aria-checked="${!!S.ties[k]}">${label}</span>`).join('')}</span>
      <span class="focus" role="group" aria-label="How much of the graph to show">${
        [['everyone', 'everyone'], ['connected', 'connected'], ['strongest', 'strongest ties']]
          .map(([k, label]) => `<span class="chip ${S.focus === k ? 'on' : ''}"
            data-focus="${k}">${label}</span>`).join('')}</span>
      <span class="hint" id="g-count">node size = importance · thicker line = more of it</span>
    </div>
    <div class="gwrap"><svg id="g"></svg>
      <div class="legend"><b>plan state</b>
        ${['scheduled', 'confirmed', 'proposed', 'floated', 'stale'].map(k =>
          `<span><i style="background:${STATE_COLOR[k]}"></i>${k}</span>`).join('')}
        <b class="gap">how a person is doing</b>
        ${['close', 'steady', 'practical', 'uneven', 'quiet'].map(k =>
          `<span><i style="background:${STATE_COLOR_PERSON[k]}"></i>${STATE_LABEL[k]}</span>`).join('')}
        <b class="gap">lines</b>
        <span><i class="ln solid"></i>just the two of you</span>
        <span><i class="ln dash"></i>a group chat you share</span>
        <span><i class="ln thin"></i>in this plan</span>
      </div></div>
    <h2>Plans</h2>
    <div class="sub tight">Pulled out of message text, and the same plans the graph
      draws between people.</div>
    <div id="plans-panel"><div class="empty">Loading plans…</div></div>
    <h2>Get a crew together</h2>
    <div class="sub tight">For something that needs more than one person — a league, a trip, a
      standing game night. Say what it is and this picks who to ask, based on who talks about it,
      who's actually done it with you, and who already knows each other.</div>
    <div id="crew-panel"><div class="empty">Loading…</div></div>
    <h2>Who's connected</h2>
    <div class="sub tight">Connected means sharing a group chat, or coming up by name in your chat with
      the other. Interests come from what you talk about and actually do together.</div>
    <div id="circles-panel"><div class="empty">Loading…</div></div>`;
  // It is a switch now, so it has to answer the keyboard like one.
  const stale = $('#t-stale');
  const flipStale = () => { S.includeStale = !S.includeStale; viewGraph(); };
  stale.onclick = flipStale;
  stale.onkeydown = e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); flipStale(); }
  };
  document.querySelectorAll('[data-tie]').forEach(c => {
    const flip = () => {
      S.ties[c.dataset.tie] = !S.ties[c.dataset.tie];
      c.classList.toggle('on', S.ties[c.dataset.tie]);
      c.setAttribute('aria-checked', String(!!S.ties[c.dataset.tie]));
      if (S.graph) simulate(S.graph);
    };
    c.onclick = flip;
    c.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); flip(); } };
  });
  document.querySelectorAll('[data-focus]').forEach(c => c.onclick = () => {
    S.focus = c.dataset.focus;
    document.querySelectorAll('[data-focus]').forEach(o =>
      o.classList.toggle('on', o.dataset.focus === S.focus));
    if (S.graph) simulate(S.graph);
  });
  drawPlans();
  drawCrew();
  drawCircles();
  S.graph = await api('/api/graph?include_stale=' + S.includeStale);
  if (S.view === 'graph') simulate(S.graph);
}

// Most people in a real history share no group chat and are in no live plan, so the
// graph arrives as a handful of clusters in a field of loose dots and the structure is
// the hardest thing to see. These narrow it: "connected" drops anyone with no edge at
// all, "strongest" keeps the busiest quarter of what's left and the ties between them.
// Nothing is deleted -- it's a view, and "everyone" puts it all back.
// Turning a kind off removes its lines, and the "you" node with the last of
// them -- a lone dot labelled You, joined to nothing, is just clutter.
function tieGraph(data) {
  const want = k => k === 'in_plan' || S.ties[k === 'direct' ? 'direct' : 'group'];
  const links = data.links.filter(l => want(l.kind));
  const nodes = S.ties.direct ? data.nodes : data.nodes.filter(n => n.type !== 'self');
  return { ...data, nodes, links };
}

function focusGraph(data, mode) {
  if (mode === 'everyone') return data;
  const strength = new Map(data.nodes.map(n => [n.id, 0]));
  const planMates = new Map();                 // plan id -> the people in it
  for (const l of data.links) {
    // Your own thread with someone does not make them connected -- this asks
    // how people relate to each other. Counting it made "connected" keep
    // everybody, because everybody has a line to you.
    if (l.kind === 'direct') continue;
    const w = l.weight || 1;
    for (const end of [l.source, l.target]) {
      if (strength.has(end)) strength.set(end, strength.get(end) + w);
    }
    if (l.kind === 'in_plan') {
      if (!planMates.has(l.target)) planMates.set(l.target, []);
      planMates.get(l.target).push(l.source);
    }
  }
  let people = new Set(data.nodes
    .filter(n => n.type === 'person' && strength.get(n.id) > 0).map(n => n.id));
  if (mode === 'strongest') {
    // The busiest quarter, by how much connection a person carries rather than how
    // many lines touch them. A floor of 2 so this never means "one tie each".
    const scores = [...people].map(id => strength.get(id)).sort((a, b) => b - a);
    const cut = Math.max(2, scores[Math.floor(scores.length / 4)] ?? 2);
    people = new Set([...people].filter(id => strength.get(id) >= cut));
  }
  // A plan is the point of this view, so it stays as long as someone in it stayed.
  const plans = new Set();
  for (const [plan, mates] of planMates) {
    if (mates.some(m => people.has(m))) plans.add(plan);
  }
  const keep = new Set([...people, ...plans,
    ...data.nodes.filter(n => n.type === 'self').map(n => n.id)]);
  return { ...data,
           nodes: data.nodes.filter(n => keep.has(n.id)),
           links: data.links.filter(l => keep.has(l.source) && keep.has(l.target)) };
}

function simulate(raw) {
  const data = focusGraph(tieGraph(raw), S.focus);
  const note = $('#g-count');
  if (note) {
    const shown = data.nodes.filter(n => n.type === 'person').length;
    const all = raw.nodes.filter(n => n.type === 'person').length;
    note.textContent = S.focus === 'everyone'
      ? 'node size = importance \u00b7 thicker line = more of it'
      : `${shown} of ${n(all, 'person', 'people')} \u00b7 ${data.links.length} of ${n(raw.links.length, 'connection')}`;
  }
  const svg = $('#g'), W = svg.clientWidth || 900, H = svg.clientHeight || 560;
  const NS = 'http://www.w3.org/2000/svg';
  const nodes = data.nodes.map((n, i) => ({
    ...n,
    x: W / 2 + Math.cos(i * 2.4) * (60 + (i % 17) * 14),
    y: H / 2 + Math.sin(i * 2.4) * (60 + (i % 13) * 16),
    vx: 0, vy: 0,
    r: n.type === 'self' ? 13 : n.type === 'person' ? 9 + n.importance / 7 : 3.5 + n.size * 1.5,
  }));
  const idx = new Map(nodes.map(n => [n.id, n]));
  const links = data.links
    .map(l => ({ ...l, s: idx.get(l.source), t: idx.get(l.target) }))
    .filter(l => l.s && l.t);

  const nbr = new Map(nodes.map(n => [n.id, new Set([n.id])]));
  for (const l of links) { nbr.get(l.source).add(l.target); nbr.get(l.target).add(l.source); }

  const root = document.createElementNS(NS, 'g');
  svg.replaceChildren(root);
  const gl = document.createElementNS(NS, 'g'), gn = document.createElementNS(NS, 'g');
  root.append(gl, gn);

  const lineEls = links.map(l => {
    const e = document.createElementNS(NS, 'line');
    // Three kinds of line, and they have to be told apart at a glance: a
    // one-to-one line was coming out identical to a person-to-plan line.
    //   dashed, by how many chats shared  -- a group chat
    //   solid, by how much you say        -- just the two of you
    //   thin solid                        -- someone is in this plan
    const shared = l.kind === 'shared_thread', direct = l.kind === 'direct';
    e.setAttribute('stroke', shared ? PAINT.line : PAINT['line-strong']);
    e.setAttribute('stroke-width',
      shared ? Math.min(3.5, 1 + ((l.weight || 1) - 1) * 0.8)
      : direct ? Math.min(3.2, 0.9 + Math.log10(1 + (l.messages || 0)))
      : 1.4);
    if (shared) e.setAttribute('stroke-dasharray', '3 4');
    if (direct) e.setAttribute('stroke-opacity', .75);
    gl.append(e); return e;
  });

  const nodeEls = nodes.map(n => {
    const g = document.createElementNS(NS, 'g');
    g.style.cursor = 'pointer';
    const c = document.createElementNS(NS, 'circle');
    c.setAttribute('r', n.r);
    c.setAttribute('fill', n.type === 'self' ? PAINT.ink      // nothing else is ink, so you are unmistakable
      : n.type === 'person' ? STATE_COLOR_PERSON[n.state] : STATE_COLOR[n.state]);
    c.setAttribute('fill-opacity', n.type === 'plan' ? .78 : .9);
    c.setAttribute('stroke', PAINT.bg);
    c.setAttribute('stroke-width', 1.5);
    g.append(c);
    const person = n.type === 'person' || n.type === 'self';
    if (person || n.state !== 'stale') {
      const t = document.createElementNS(NS, 'text');
      t.textContent = n.type === 'person' ? shortName(n.label) : n.label;
      t.setAttribute('y', n.r + 12);
      t.setAttribute('text-anchor', 'middle');
      t.setAttribute('fill', person ? PAINT.ink : PAINT.dim);
      t.setAttribute('font-size', person ? 11.5 : 9.5);
      t.setAttribute('font-weight', person ? 600 : 400);
      t.setAttribute('font-family', 'system-ui,sans-serif');
      t.setAttribute('paint-order', 'stroke');
      t.setAttribute('stroke', PAINT.panel);
      t.setAttribute('stroke-width', 3);
      t.style.pointerEvents = 'none';
      g.append(t);
    }
    if (n.type === 'self') g.style.cursor = 'grab';
    g.onclick = () => n.type === 'self' ? null
      : n.type === 'person' ? openPerson(n.id) : openPlan(n.id);
    g.onpointerenter = () => highlight(n.id);
    g.onpointerleave = () => highlight(null);
    g.onpointerdown = ev => {
      ev.stopPropagation(); n.fixed = true; g.setPointerCapture(ev.pointerId);
      const move = e => {
        const p = toLocal(e); n.x = p.x; n.y = p.y; n.vx = n.vy = 0; kick(.12);
      };
      const up = e => {
        n.fixed = false; g.releasePointerCapture(ev.pointerId);
        g.removeEventListener('pointermove', move); g.removeEventListener('pointerup', up);
      };
      g.addEventListener('pointermove', move); g.addEventListener('pointerup', up);
    };
    gn.append(g); return g;
  });

  function highlight(id) {
    const near = id ? nbr.get(id) : null;
    nodes.forEach((n, i) => {
      nodeEls[i].style.opacity = !near || near.has(n.id) ? 1 : .18;
    });
    links.forEach((l, i) => {
      lineEls[i].style.opacity = !near ? 1
        : (l.source === id || l.target === id) ? 1 : .08;
    });
  }

  // pan + zoom
  let k = 1, tx = 0, ty = 0;
  const applyT = () => root.setAttribute('transform', `translate(${tx},${ty}) scale(${k})`);
  const toLocal = e => {
    const b = svg.getBoundingClientRect();
    return { x: (e.clientX - b.left - tx) / k, y: (e.clientY - b.top - ty) / k };
  };
  svg.onwheel = e => {
    e.preventDefault();
    const b = svg.getBoundingClientRect(), mx = e.clientX - b.left, my = e.clientY - b.top;
    const nk = Math.min(3, Math.max(.35, k * (e.deltaY < 0 ? 1.1 : 1 / 1.1)));
    tx = mx - (mx - tx) * (nk / k); ty = my - (my - ty) * (nk / k); k = nk; applyT();
  };
  svg.onpointerdown = e => {
    const sx = e.clientX - tx, sy = e.clientY - ty;
    svg.classList.add('drag');
    const move = ev => { tx = ev.clientX - sx; ty = ev.clientY - sy; applyT(); };
    const up = () => {
      svg.classList.remove('drag');
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  };

  // force loop
  let alpha = 1;
  // The loop used to floor alpha at .02 and schedule itself forever, so the graph
  // kept nudging and the tab kept waking the CPU for as long as it was open. It
  // settles and stops now, and anything that disturbs the layout -- a drag, a
  // filter -- calls kick() to start it again.
  const STILL = 0.05;            // px the busiest node moves in a frame: settled
  let frame = null;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const kick = (to = .3) => {
    alpha = Math.max(alpha, to);
    if (frame === null) frame = requestAnimationFrame(run);
  };
  // tick() does one step and says whether there is more to do; run() is the only
  // thing that schedules. Keeping those apart is what lets the reduced-motion
  // path step the layout to rest without queueing a single frame.
  const run = () => { frame = tick() ? requestAnimationFrame(run) : null; };
  const CHARGE = 2600, DAMP = .82;
  function tick() {            // one step; true while there is more to settle
    if (!document.body.contains(svg)) return;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = (Math.random() - .5); dy = (Math.random() - .5); d2 = 1; }
        if (d2 > 250000) continue;
        const f = CHARGE / d2, d = Math.sqrt(d2), fx = dx / d * f, fy = dy / d * f;
        a.vx -= fx; a.vy -= fy; b.vx += fx; b.vy += fy;
      }
    }
    for (const l of links) {
      const want = l.kind === 'in_plan' ? 62 : 210;
      const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y;
      const d = Math.hypot(dx, dy) || 1;
      const f = (d - want) * (l.kind === 'in_plan' ? .04 : .004);
      const fx = dx / d * f, fy = dy / d * f;
      l.s.vx += fx; l.s.vy += fy; l.t.vx -= fx; l.t.vy -= fy;
    }
    // collision: nodes and their labels should not sit on top of each other
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        const pad = (a.type === 'plan' && b.type === 'plan') ? 8 : 18;
        const min = a.r + b.r + pad;
        let dx = b.x - a.x, dy = b.y - a.y;
        const d = Math.hypot(dx, dy) || .01;
        if (d >= min) continue;
        const push = (min - d) / d * .5;
        dx *= push; dy *= push;
        if (!a.fixed) { a.x -= dx; a.y -= dy; }
        if (!b.fixed) { b.x += dx; b.y += dy; }
      }
    }
    let busiest = 0;
    for (const n of nodes) {
      n.vx += (W / 2 - n.x) * .0015; n.vy += (H / 2 - n.y) * .0019;
      if (n.fixed) { n.vx = n.vy = 0; continue; }
      n.vx *= DAMP; n.vy *= DAMP;
      const sx = n.vx * alpha, sy = n.vy * alpha;
      n.x += sx; n.y += sy;
      busiest = Math.max(busiest, Math.abs(sx), Math.abs(sy));
    }
    links.forEach((l, i) => {
      lineEls[i].setAttribute('x1', l.s.x); lineEls[i].setAttribute('y1', l.s.y);
      lineEls[i].setAttribute('x2', l.t.x); lineEls[i].setAttribute('y2', l.t.y);
    });
    nodes.forEach((n, i) => nodeEls[i].setAttribute('transform', `translate(${n.x},${n.y})`));
    alpha *= .99;
    // Nodes start at rest, so the first frames barely move and a displacement
    // test on its own would call it finished before it began. Let the layout
    // run while alpha is still meaningful, then stop once it stops moving.
    return alpha > .12 || busiest > STILL;
  }

  applyT();
  if (reduced.matches) {
    // Someone who has asked for less motion should not be shown a layout finding
    // itself. Step it to rest in one go and let the last step be what they see.
    for (let i = 0; i < 400 && tick(); i++);
    frame = null;
  } else {
    frame = requestAnimationFrame(run);
  }
}

// ---------------------------------------------------------------- privacy

// What the schedule last managed, said plainly: a job that quietly
// cannot do half its work is worse than no job.
async function drawRefresh() {
  const line = $('#refreshline');
  if (!line) return;
  let r;
  try { r = await api('/api/refresh'); } catch { line.textContent = ''; return; }
  if (!r.last_run) {
    line.textContent = r.enabled
      ? 'The Mac app re-reads your messages once a day. It has not run yet.'
      : 'The daily refresh is switched off in the menu bar.';
    return;
  }
  const bits = r.last_results.map(o => o.ok
    ? `${PLATFORM_LABEL[o.platform] || o.platform}: ${n(o.messages.toLocaleString(), 'message')}`
    : `${PLATFORM_LABEL[o.platform] || o.platform} couldn't be read`);
  line.innerHTML = `Last checked ${esc(ago(r.last_run))}. ${esc(bits.join(' · '))}
    ${r.enabled ? '' : '<b>The daily refresh is off.</b>'}
    ${r.last_results.some(o => !o.ok)
      ? `<br><span class="hint">${esc(r.last_results.filter(o => !o.ok)
          .map(o => o.note).join(' '))}</span>` : ''}`;
}

const PLATFORM_LABEL = { imessage: 'iMessage', whatsapp: 'WhatsApp', instagram: 'Instagram' };

// The trial or the licence, and -- right under it -- every call the app can make,
// in the same words the server uses to decide them. The licence check and the
// update check are the only two, and neither carries anything from your messages.
async function drawLicence(note) {
  const box = $('#licencebox'), net = $('#netbox');
  if (!box) return;
  let l;
  try { l = await api('/api/licence'); } catch { box.innerHTML = COULDNT('the licence'); return; }
  if (!$('#licencebox')) return;
  const onMac = S.overview.lan?.via !== 'device';
  box.innerHTML = `<div class="sub tight">${esc(l.message)}
      ${l.key_hint ? `<br>Key ${esc(l.key_hint)} · last checked
        ${l.last_checked ? esc(ago(l.last_checked)) : 'never'}.` : ''}
      ${l.update.newer ? `<br><b>Version ${esc(l.update.latest)} is out</b> (you have
        ${esc(l.version)}). <a href="${esc(l.update.url)}" ${OUT}>Get it</a>.` : ''}
    </div>
    ${!onMac ? '' : l.state === 'licensed' || l.state === 'check_needed'
      ? `<div class="cmds one"><button type="button" class="btn" id="lic-check">Check now</button>
         <button type="button" class="btn" id="lic-off">Move to another Mac</button></div>`
      : `<form class="cmds one" id="lic-form" autocomplete="off">
           <input class="search" id="lic-key" maxlength="128" placeholder="Paste your licence key"
             aria-label="Licence key" spellcheck="false">
           <button type="submit" class="btn primary">Activate</button>
           <a class="btn" href="${esc(l.buy_url)}" ${OUT}>Buy — $${l.price_usd}</a>
         </form>`}
    <div class="dnote ${note && note.err ? 'err' : ''}" id="lic-note">${esc(note ? note.text : '')}</div>`;
  if (net) {
    net.innerHTML = `<div class="sub tight">Your messages never leave this Mac. These are the
        only two calls the app can make, and each goes through the same gate as everything
        else — pinned to one address, with nothing from your messages allowed in it.</div>
      ${l.network.map(u => `<div class="notice quiet"><div><b>${u.purpose === 'licence'
          ? 'Licence check' : 'Update check'}</b> → ${esc(u.to)}<br>Sends ${esc(u.sends)},
          ${esc(u.when)}.</div></div>`).join('')}
      <div class="toggle ${l.update_checks ? 'on' : ''}" id="upd-toggle">
        <div class="sw"></div><div class="tx"><b>Check for updates</b>
        <span>Once a week. Off means the app never asks; you can still download new
        versions from the site.</span></div></div>`;
    $('#upd-toggle').onclick = async () => {
      await postJSON('/api/privacy', { update_checks: !l.update_checks });
      drawLicence();
    };
  }
  const done = (res, ok) => drawLicence(res.ok ? { text: ok } : { text: res.error, err: true });
  const form = $('#lic-form');
  if (form) form.onsubmit = async (e) => {
    e.preventDefault();
    const key = $('#lic-key').value.trim();
    if (!key) return;
    $('#lic-note').textContent = 'Checking the key with Lemon Squeezy…';
    const res = await postJSON('/api/licence/activate', { key });
    if (res.ok) { S.overview = await api('/api/overview'); syncFoot(); }
    done(res, 'Activated. Thank you!');
  };
  const chk = $('#lic-check');
  if (chk) chk.onclick = async () => done(await postJSON('/api/licence/check', {}), 'Checked.');
  const off = $('#lic-off');
  if (off) off.onclick = async () => {
    const res = await postJSON('/api/licence/deactivate', {});
    if (res.ok) { S.overview = await api('/api/overview'); syncFoot(); }
    done(res, 'This Mac has handed its licence back. Activate it on the other one.');
  };
}

async function viewPrivacy() {
  const d = await api('/api/privacy'), s = d.settings;
  const T = (key, on, title, note) => `
    <div class="toggle ${on ? 'on' : ''}" data-k="${key}">
      <div class="sw"></div><div class="tx"><b>${title}</b><span>${note}</span></div></div>`;
  $('#main').innerHTML = `
    <h1>Privacy</h1>
    <div class="sub">These are enforced in code, not by convention. Analysis only ever sees what
      survives these settings, and the only path to the network is a gate that refuses by default.</div>
    <div class="banner"><b>Currently: ${s.local_only ? 'fully local.' : 'network open for ' +
      s.network_consent.join(', ') + '.'}</b> ${s.local_only
      ? 'Every profile and plan on this machine was computed without a single network call.' : ''}</div>
    ${T('local_only', s.local_only, 'Local only',
        'Master switch for your messages. While on, nothing from them can leave this machine for any reason.')}
    ${T('stats_only', s.stats_only, 'Never send message text',
        'Derived numbers may cross the boundary; raw message bodies may not.')}
    ${T('redact_on_send', s.redact_on_send, 'Redact identifiers',
        'Strips phone numbers, emails, addresses, URLs, and card-shaped digits before anything is sent.')}
    <h2>Your messages</h2>
    <div class="sub tight">${S.overview.source === 'synthetic'
      ? 'This is the made-up history. Importing replaces it with your own.'
      : `Imported from Messages on this Mac — ${n(S.overview.messages, 'message')} across
         ${n(S.overview.threads, 'thread')}. Import again whenever you want fresher data;
         your names, locations and other corrections are kept.`}</div>
    ${S.overview.licence && !S.overview.licence.can_import
      ? `<div class="sub tight"><b>Importing needs a licence now.</b> What you already imported
         stays here; enter a key below to import again.</div>`
      : inDesktopApp()
      ? `<div class="cmds one"><a class="btn primary" href="mg://import">${
          S.overview.source === 'synthetic' ? 'Import my messages' : 'Import again'}</a></div>`
      : `<div class="sub tight">In a browser this is a terminal command:
         <div class="cmds"><code>./mg import</code></div>
         Open the Mac app and it is a button.</div>`}
    <h2>Keeping up to date</h2>
    <div class="sub tight" id="refreshline">Checking…</div>
    <h2 id="licence">Licence</h2>
    <div id="licencebox"><span class="pempty">Loading…</span></div>
    <h2>What goes online</h2>
    <div id="netbox"><span class="pempty">Loading…</span></div>
    <h2>Your phone</h2>
    <div id="phonebox"><span class="pempty">Loading…</span></div>
    <h2>What a Claude call would contain</h2>
    ${S.people.length ? `<div class="sub">Pass 2 uses Claude to draft messages. This is the exact
      payload that would be sent for ${esc(S.people[0].name)} — shown in full, before anything
      leaves, every time.</div>
    <pre id="preview">select a person…</pre>`
    : `<div class="sub">Nothing to show: everyone is archived, so there is nobody to draft to.
      Bring someone back from the People tab and this fills in.</div>`}
`;
  drawPhone();
  drawRefresh();
  drawLicence();

  document.querySelectorAll('.toggle').forEach(t => t.onclick = async () => {
    const key = t.dataset.k;
    const body = {}; body[key] = !t.classList.contains('on');
    await fetch('/api/privacy', { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body) });
    S.overview = await api('/api/overview'); syncFoot(); viewPrivacy();
  });
  if (!S.people.length) return;
  const pv = await api('/api/privacy/preview/' + S.people[0].contact_id);
  $('#preview').textContent = (pv.allowed ? '' :
    `[BLOCKED — ${pv.reason}]\n[redacted: ${pv.redactions.join(', ') || 'nothing matched'}]\n\n`)
    + pv.would_send;
}

// A rejected fetch in here used to abort the boot halfway: no navigation wired, no
// message, an empty window. Say so instead.
boot().catch(err => {
  document.body.innerHTML = `<div class="signedout"><h2>Couldn't load your data</h2>
    <p>The app reached the server but something went wrong reading from it. Reload the
    page; if it keeps happening, restart the server from your terminal.</p></div>`;
  console.error(err);
});
