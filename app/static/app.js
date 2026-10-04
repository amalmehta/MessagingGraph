'use strict';
const $ = (s, r = document) => r.querySelector(s);
const api = (p) => fetch(p).then(r => {
  if (r.status === 401) { signedOut(); throw new Error('signed out'); }
  return r.json();
});

// The session cookie is only valid for one run of the server. After a restart the
// old tab would otherwise fail silently on every click.
function signedOut() {
  document.body.innerHTML = `<div class="signedout"><h2>This session has ended</h2>
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

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// A contact with no saved name is just a number, and its first word is the area code.
// Show the last four digits instead, so two unnamed people don't both read "(408)".
const shortName = (n) => {
  const s = String(n ?? '').trim();
  if (/^\(\d{3}\) \d{3}-\d{4}$|^\+?[\d\s().-]{7,}$/.test(s)) return '…' + s.replace(/\D/g, '').slice(-4);
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

const S = { view: 'now', overview: null, people: null, graph: null,
  includeStale: false, sort: 'importance', weeks: 40, focus: 'everyone' };

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
async function boot() {
  wireTheme();
  wireReload();
  S.overview = await api('/api/overview');
  S.people = await api('/api/people');
  const o = S.overview;
  $('#src').textContent = o.source + ' source';
  $('#c-now').textContent = o.suggestions.length;
  $('#c-people').textContent = o.contacts;
  $('#c-graph').textContent = o.plans.open + o.plans.stale;
  $('#foot-span').textContent = o.span.from.slice(0, 10) + ' → ' + o.span.to.slice(0, 10);
  syncFoot();
  document.querySelectorAll('nav button').forEach(b =>
    b.onclick = () => go(b.dataset.view));
  $('#scrim').onclick = closeDrawer;
  document.addEventListener('keydown', e => e.key === 'Escape' && closeDrawer());
  go('now');
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
  let saved = null;
  try { saved = localStorage.getItem('mg-theme'); } catch { /* private window */ }
  const mode = saved === 'light' || saved === 'dark' ? saved : 'auto';
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
      try {
        if (m === 'auto') localStorage.removeItem('mg-theme');
        else localStorage.setItem('mg-theme', m);
      } catch { /* the theme still applies, it just won't be remembered */ }
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
  const s = S.overview.privacy;
  $('#foot-mode').textContent = s.local_only ? 'local only · no network'
    : 'network open: ' + s.network_consent.join(', ');
  $('#c-priv').textContent = s.excluded_contact_ids.length
    ? s.excluded_contact_ids.length + ' hidden' : '';
}

function go(v) {
  // Re-read the palette on every render rather than caching it once. The CSS is
  // the authority; this way a theme that changed underneath us -- a system flip,
  // or a `change` event the browser never delivered -- can never leave the charts
  // drawn in the other theme's colours.
  readTheme();
  S.view = v;
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('on', b.dataset.view === v));
  ({ now: viewNow, graph: viewGraph, people: viewPeople, privacy: viewPrivacy })[v]();
}

// ---------------------------------------------------------------- now
// The one place the app says hello. Time of day, nothing more personal than that.
function greeting() {
  const h = new Date().getHours();
  const part = h < 5 ? 'Up late' : h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
  return `${part}. Here's what's going on.`;
}

function viewNow() {
  const o = S.overview, v = o.states, pc = o.plans;
  const noticing = o.suggestions.filter(a => !['reconnect', 'revive_plan', 'check_in'].includes(a.kind));
  $('#main').innerHTML = `
    <h1>${greeting()}</h1>
    <div class="sub">Computed on this machine from ${o.messages.toLocaleString()}
      messages across ${o.threads} threads. Nothing has left your computer — and
      nothing here knows what kind of year anyone is having, so treat it as a
      prompt to think, not a scoreboard.</div>
    <div class="stats">
      <button class="stat jump" data-jump="plans-confirmed"><div class="n" style="color:var(--adds)">${pc.confirmed}</div>
        <div class="l">plans confirmed</div></button>
      <button class="stat jump" data-jump="plans-motion"><div class="n" style="color:var(--accent)">${pc.in_motion}</div>
        <div class="l">still being discussed</div></button>
      <button class="stat jump" data-jump="plans-revive"><div class="n" style="color:var(--stale)">${pc.revive}</div>
        <div class="l">worth reviving <span class="hint">· last 90 days</span></div></button>
      <div class="stat"><div class="n" style="color:var(--adds)">${v.close}</div><div class="l">close right now</div></div>
      <div class="stat"><div class="n" style="color:var(--unclear)">${v.quiet}</div><div class="l">gone quiet</div></div>
    </div>
    ${o.unnamed ? `<div class="notice"><div><b>${o.unnamed} ${o.unnamed === 1 ? 'person you talk to has' : 'people you talk to have'} no name yet.</b>
      Name them so their profiles and drafts make sense.</div>
      <button class="btn primary name-them">Name them</button></div>` : ''}
    ${o.filtered ? `<div class="notice quiet"><div>${o.filtered} automated or marketing
      ${o.filtered === 1 ? 'thread is' : 'threads are'} set aside and left out of everything here.</div></div>` : ''}
    ${locationNotice(o)}
    <h2>Messaging rate</h2>
    <div class="chartbox"><div class="chead">
      <div><b id="rate-head">—</b><span id="rate-sub">across every thread</span></div>
      <div class="ranges">${[20, 40, 80].map(w =>
        `<span class="chip sm ${S.weeks === w ? 'on' : ''}" data-w="${w}">${w}w</span>`).join('')}</div>
    </div><div class="plot" id="rate"></div></div>
    <h2>Messages worth sending</h2>
    <div class="sub tight">Drafted from your history and written the way you've been texting lately.
      Copy one into Messages, ask for another version, or skip it. Nothing sends from here.</div>
    <div class="voicenote" id="voice-note"></div>
    <div id="drafts" class="drafts"><div class="empty">Drafting…</div></div>
    <h2>Say thanks</h2>
    <div class="sub tight">Thank-you notes tied to something that actually happened: a favour you thanked
      someone for, a year of texting, something you did together. Copy one, or write your own.</div>
    <div id="thanks" class="drafts"><div class="empty">Looking back…</div></div>
    <h2>Get a crew together</h2>
    <div class="sub tight">For something that needs more than one person — a league, a trip, a
      standing game night. Say what it is and this picks who to ask, based on who talks about it,
      who's actually done it with you, and who already knows each other.</div>
    <div id="crew-panel"><div class="empty">Loading…</div></div>
    <h2>Plans</h2>
    <div id="plans-panel"><div class="empty">Loading plans…</div></div>
    <h2>Who's connected</h2>
    <div class="sub tight">Connected means sharing a group chat, or coming up by name in your chat with
      the other. Interests come from what you talk about and actually do together.</div>
    <div id="circles-panel"><div class="empty">Loading…</div></div>
    ${noticing.length ? `<h2>Worth noticing</h2>
    <div class="cards">${noticing.map(a => `
      <div class="card ${a.tone}" data-id="${esc(a.target_id)}" data-kind="${a.kind}">
        <div class="top"><span class="who">${esc(a.who)}</span>
          <span class="kind">${a.kind.replace('_', ' ')}</span></div>
        <div class="note">${esc(a.observation)}</div>
        <div class="act">${esc(a.action)}</div>
      </div>`).join('')}</div>` : ''}
    <div class="foot-note">None of this is a to-do list. Skipping all of it is a
      perfectly good week.</div>`;
  document.querySelectorAll('.card').forEach(c => c.onclick = () => openPerson(c.dataset.id));
  document.querySelectorAll('.name-them').forEach(b => b.onclick = openNaming);
  document.querySelectorAll('.add-locations').forEach(b => b.onclick = openLocations);
  document.querySelectorAll('.stat.jump').forEach(b => b.onclick = () =>
    document.getElementById(b.dataset.jump)?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  document.querySelectorAll('.ranges .chip').forEach(c =>
    c.onclick = () => { S.weeks = +c.dataset.w; viewNow(); });
  drawDrafts();
  drawThanks();
  drawCrew();
  drawPlans();
  drawCircles();
  drawOverall();
}

// ---------------------------------------------------------------- drafts
const DRAFT_KIND = {
  reply: 'waiting on you', follow_up: 'follow up', revive_plan: 'plan',
  group_plan: 'group plan', reconnect: 'reconnect', check_in: 'check in',
  thanked: 'thank you', anniversary: 'anniversary', together: 'thank you', appreciation: 'appreciation',
};
const HIDE_KEY = 'mg-drafts-hidden';
const HIDE_DAYS = { skip: 7, copied: 3 };
function hiddenDrafts() {
  try { return JSON.parse(localStorage.getItem(HIDE_KEY) || '{}'); } catch { return {}; }
}
function hideDraft(id, why) {
  const h = hiddenDrafts(); h[id] = { why, at: Date.now() };
  try { localStorage.setItem(HIDE_KEY, JSON.stringify(h)); } catch { /* private window */ }
}
function isHidden(id) {
  const e = hiddenDrafts()[id];
  return !!e && Date.now() - e.at < (HIDE_DAYS[e.why] || 7) * 86400000;
}

async function drawDrafts() {
  const box = $('#drafts');
  if (!box) return;
  api('/api/voice').then(v => {
    const note = $('#voice-note');
    if (!note || !v.traits) return;
    const span = v.window_days ? `your last ${v.window_days} days of messages` : 'all your messages';
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
    <div class="dtop"><span class="who ${person ? 'link' : ''}" ${person ? `data-person="${esc(d.target_ids[0])}"` : ''}>${esc(d.who)}</span>
      <span class="kind">${DRAFT_KIND[d.kind] || esc(d.kind)}</span></div>
    <div class="why">${esc(d.why)}</div>
    ${d.detail ? `<div class="detail">${quoted ? '“' + esc(d.detail) + '”' : 'Proposed: ' + esc(d.detail)}</div>` : ''}
    <textarea class="dtext" rows="2" aria-label="Draft message to ${esc(d.who)}">${esc(d.draft)}</textarea>
    <div class="dact">
      <button class="btn primary copy">Copy</button>
      ${d.options > 1 ? '<button class="btn another">Another version</button>' : ''}
      <button class="btn ghost skip">Not now</button>
      <span class="dnote" role="status"></span>
    </div></div>`;
}

function wireDraft(card) {
  const id = card.dataset.id, ta = card.querySelector('.dtext'), note = card.querySelector('.dnote');
  fitText(ta);
  const who = card.querySelector('.who.link');
  if (who) who.onclick = () => openPerson(who.dataset.person);
  card.querySelector('.copy').onclick = async () => {
    try {
      await navigator.clipboard.writeText(ta.value);
      note.textContent = 'Copied. Paste it into Messages.';
    } catch {
      ta.select();
      note.textContent = 'Selected. Press ⌘C to copy.';
    }
    card.classList.add('sent');
    hideDraft(id, 'copied');
  };
  const another = card.querySelector('.another');
  if (another) another.onclick = async () => {
    const d = S.drafts[id];
    S.draftSeeds[id] = ((S.draftSeeds[id] || 0) + 1) % d.options;
    another.disabled = true;
    const list = await api(`${card.dataset.src}?redraft=${encodeURIComponent(id)}&seed=${S.draftSeeds[id]}`);
    const fresh = list.find(x => x.id === id);
    another.disabled = false;
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
      <button class="btn primary crew-go">Who should I ask?</button>
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
    out.innerHTML = `<div class="empty">Couldn't work that one out. Try saying it a different way.</div>`;
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
    <div class="crewact"><button class="btn another crew-others">Suggest different people</button></div>
    <div class="draft plain">
      <div class="dtop"><span class="who">The ask</span>${crew.when_label
        ? `<span class="kind">${esc(crew.when_label)}</span>` : ''}</div>
      <textarea class="dtext" rows="2" aria-label="Invitation to send">${esc(crew.draft)}</textarea>
      <div class="dact">
        <button class="btn primary crew-copy">Copy</button>
        <button class="btn another crew-again">Another version</button>
        <span class="dnote" role="status"></span>
      </div></div></div>`;
  fitText(out.querySelector('.dtext'));
  out.querySelectorAll('.link[data-id]').forEach(el => el.onclick = () => openPerson(el.dataset.id));
  out.querySelector('.crew-copy').onclick = async () => {
    const ta = out.querySelector('.dtext'), note = out.querySelector('.dnote');
    try {
      await navigator.clipboard.writeText(ta.value);
      note.textContent = 'Copied. Start a group in Messages and paste it.';
    } catch {
      ta.select();
      note.textContent = 'Selected. Press ⌘C to copy.';
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
    note.textContent = 'Selected. Press ⌘C to copy.';
  }
}

async function drawCircles() {
  const host = $('#circles-panel');
  if (!host) return;
  const d = await api('/api/connections');
  S.circles = Object.fromEntries(d.circles.map(c => [c.thread_id, c]));
  const ago = n => n === null ? '—' : n === 0 ? 'active today' : `last active ${n}d ago`;
  const person = (id, name, extra = '') =>
    `<button class="mchip" data-person="${esc(id)}">${esc(name)}${extra}</button>`;
  const groups = d.circles.map(c => `
    <div class="circle">
      <div class="ctop"><b>${esc(c.name)}</b><span class="cmeta">${ago(c.days_idle)}</span></div>
      <div class="cmembers">${c.members.map(m => person(m.id, m.name)).join('')}</div>
      <div class="cfoot"><span>${c.messages.toLocaleString()} messages · ${c.plans_done} plans happened${
        c.plans_open ? ` · ${c.plans_open} live` : ''}</span>
        <button class="btn primary propose" data-thread="${esc(c.thread_id)}">Propose a plan</button></div>
    </div>`).join('');
  const interests = d.interests.map(g => `
    <div class="interest"><div class="itop"><b>${esc(g.interest)}</b><span class="cmeta">${g.members.length} people</span></div>
      <div class="cmembers">${g.members.map(m => person(m.id, m.name)).join('')}</div></div>`).join('');
  host.innerHTML = `
    <div class="netsec"><div class="nethead">Group chats <span>${d.circles.length}</span></div>
      <div class="circles">${groups || '<div class="pempty">No group chats.</div>'}</div></div>
    <div class="netsec"><div class="nethead">Shared interests <span>${d.interests.length}</span></div>
      <div class="pdef">What stands out in each person's thread compared with your others, not just what everyone talks about.</div>
      <div class="interests">${interests || '<div class="pempty">Not enough shared topics yet.</div>'}</div></div>
    ${d.mention_links ? `<div class="pfoot">${d.mention_links} connection${d.mention_links === 1 ? '' : 's'} found from people coming up by name in other chats. They show on each person's profile.</div>` : ''}
    ${d.solo.length ? `<div class="pfoot">Not connected to anyone else here: ${d.solo.map(esc).join(', ')}</div>` : ''}`;
  host.querySelectorAll('.mchip[data-person]').forEach(el => el.onclick = () => openPerson(el.dataset.person));
  host.querySelectorAll('.propose').forEach(b => b.onclick = () => {
    const c = S.circles[b.dataset.thread];
    openProposal(c, c.members.map(m => m.id), 0);
  });
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
      <div class="dact"><button class="btn ${t.automated ? 'primary' : 'ghost'} verdict"
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
    const res = await api(`/api/people/${encodeURIComponent(cid)}/gratitude?seed=${seed}`);
    const box = $('#thankbox');
    if (!res.draft) { box.innerHTML = `<div class="pdef">${esc(res.reason)}</div>`; return; }
    const d = res.draft;
    box.innerHTML = `<div class="draft">
      <div class="dtop"><span class="who">A thank-you for ${esc(shortName(d.who))}</span>
        <span class="kind">${DRAFT_KIND[d.kind] || ''}</span></div>
      <div class="why">${esc(d.why)}</div>
      <textarea class="dtext" id="thank-text" aria-label="Thank-you draft">${esc(d.draft)}</textarea>
      <div class="dact"><button class="btn primary" id="thank-copy">Copy</button>
        ${d.options > 1 ? '<button class="btn" id="thank-next">Another version</button>' : ''}
        <span class="dnote" id="thank-note" role="status"></span></div></div>`;
    fitText($('#thank-text'));
    $('#thank-copy').onclick = () => copyInto($('#thank-text'), $('#thank-note'));
    const next = $('#thank-next');
    if (next) next.onclick = async () => {
      box.innerHTML = '<button class="btn ghost" id="thank-btn">Draft a thank-you</button>';
      wireThanks(cid, (seed + 1) % d.options);
      $('#thank-btn').click();
    };
  };
}

// ---------------------------------------------------------------- locations
function locTag(loc) {
  if (!loc) return '<span class="num dim">—</span>';
  if (loc.source === 'you') return `<span class="loc">${esc(loc.place)}</span>`;
  return `<span class="loc guess" title="${esc('From messages: “' + loc.quote + '”')}">${esc(loc.place)}?</span>`;
}

function locationNotice(o) {
  const l = o.locations || { unknown: 0, to_confirm: 0 };
  const n = l.unknown + l.to_confirm;
  if (!n) return '';
  const detail = l.to_confirm ? ` (${l.to_confirm} with a guess to confirm)` : '';
  return `<div class="notice"><div><b>${n} of the people you talk to most ${n === 1 ? "doesn't" : "don't"} have a confirmed location${detail}.</b>
    Add where they live so groups and plans can use it.</div>
    <button class="btn primary add-locations">Add locations</button></div>`;
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
      box.innerHTML = `Lives in <b>${esc(loc.place)}</b> <button class="linkish" data-act="edit">Change</button>`;
    } else if (loc) {
      box.innerHTML = `${loc.confidence === 'likely' ? 'Might live' : 'Probably lives'} in <b>${esc(loc.place)}</b>
        <span class="quote">from “${esc(loc.quote)}”</span>
        <button class="linkish" data-act="confirm">That's right</button><button class="linkish" data-act="edit">Change</button>`;
    } else {
      box.innerHTML = `${unknown ? "Location: you said you don't know" : 'Location unknown'}
        <button class="linkish" data-act="edit">Add</button>`;
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
    <span class="close">&times;</span>
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
  $('#drawer').classList.add('on'); $('#scrim').classList.add('on');
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
    <span class="close">&times;</span>
    <h3>Who are these people?</h3>
    <div class="headline">Real conversations with numbers that aren't in your Contacts. A name you give
      is saved on this Mac, encrypted with the rest of your data, and kept if you import again.
      Your Contacts app isn't changed.</div>
    ${list.length ? list.map(u => `
      <div class="nameme" data-id="${esc(u.id)}">
        <div class="ctop"><b>${esc(u.label)}</b><span class="cmeta">${u.messages} messages${
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
  $('#drawer').classList.add('on'); $('#scrim').classList.add('on');
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
    <span class="close">&times;</span>
    <h3>A plan for ${esc(circle.name)}</h3>
    <div class="headline">A starting point built from this group's own history. Change who's invited,
      edit the wording, then copy it into the chat yourself.</div>
    <div class="pp-label">Who's invited</div>
    <div class="cmembers">${circle.members.map(m => {
      const on = selected.includes(m.id);
      return `<button class="mchip toggle ${on ? 'on' : ''}" aria-pressed="${on}" data-id="${esc(m.id)}">${esc(m.name)}</button>`;
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
      <button class="btn primary" id="pp-copy">Copy</button>
      ${p.options > 1 ? '<button class="btn" id="pp-next">Another idea</button>' : ''}
      <span class="dnote" id="pp-note" role="status"></span>
    </div>`;
  $('#drawer .close').onclick = closeDrawer;
  $('#drawer').classList.add('on'); $('#scrim').classList.add('on');
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
      <div class="pdef">Wanted in the last ${d.revive_window_days} days, and nobody picked a day.</div>
      <div class="rgrid">${revive.map(planRow).join('') || '<div class="pempty">Nothing stalled recently.</div>'}</div>
      ${d.revive.length > 6 ? `<button class="btn ghost more-revive">${S.showAllRevive ? 'Show fewer' : `Show all ${d.revive.length}`}</button>` : ''}
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
}

async function drawOverall() {
  const d = await api('/api/series?weeks=' + S.weeks);
  const host = $('#rate');
  if (!host) return;
  mountChart(host, d, { height: 168, aria: 'Messages per week across all threads' });
  const sm = d.summary;
  $('#rate-head').textContent = `${sm.recent_avg} messages a week`;
  $('#rate-sub').textContent = sm.change === null ? 'across every thread'
    : `across every thread · ${sm.change > 0 ? '+' : ''}${sm.change}% against the previous quarter`;
}

// ---------------------------------------------------------------- people
const COLS = [
  ['name', 'Person', p => `<span class="name">${esc(p.name)}</span>`],
  ['state', 'Connection', p => `<span class="tag s-${p.state}">${STATE_LABEL[p.state]}</span>`],
  ['vibe', 'Vibe', p => `<span class="vcol">${esc(p.vibe ? p.vibe.name : '—')}</span>`],
  ['location', 'Location', p => locTag(p.location)],
  ['trend', 'Trend', p => p.trend === 'none' ? '<span class="num dim">—</span>'
    : `<span class="tag t-${p.trend}">${p.trend}</span>`],
  ['msgs_per_week_recent', 'Msgs/wk', p => `<span class="num">${p.msgs_per_week_recent.toFixed(1)}</span>`],
  ['warmth', 'Warmth', p => bar(p.warmth, PAINT.adds)],
  ['importance', 'Importance', p => bar(p.importance, PAINT.accent)],
  ['self_editing', 'Your care level', p => bar(p.self_editing, p.self_editing > 55 ? PAINT.neutral : PAINT.dim)],
  ['open_plans', 'Plans', p => `<span class="num">${p.open_plans || '·'}</span>`],
  ['trend', 'Last 20 weeks', p => spark(p.spark)],
];
const bar = (v, c) => `<span class="num">${v.toFixed(0)}</span>
  <div class="bar"><i style="width:${Math.min(100, v)}%;background:${c}"></i></div>`;

function viewPeople() {
  const rows = [...S.people].sort((a, b) => {
    const k = S.sort;
    const key = p => k === 'vibe' ? (p.vibe?.name ?? '') : k === 'location' ? (p.location?.place ?? '') : p[k];
    const x = key(a), y = key(b);
    return typeof x === 'string' ? String(x).localeCompare(y) : y - x;
  });
  $('#main').innerHTML = `
    <h1>People</h1>
    <div class="sub">A profile per person, built only from message timing and language.
      These describe how a connection is running at the moment, not how good anyone is
      to have in your life. Open a row to see every number behind the summary — and the
      ordinary explanation for each one.</div>
    ${S.overview.unnamed ? `<div class="notice"><div><b>${S.overview.unnamed} without a name.</b>
      Name them so you can tell who's who.</div><button class="btn primary name-them">Name them</button></div>` : ''}
    ${locationNotice(S.overview)}
    <div class="tablewrap"><table><thead><tr>${COLS.map(c =>
      `<th data-k="${c[0]}">${c[1]}${S.sort === c[0] ? ' ↓' : ''}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(p => `<tr data-id="${esc(p.contact_id)}">${COLS.map(c =>
      `<td>${c[2](p)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  document.querySelectorAll('th').forEach(th =>
    th.onclick = () => { S.sort = th.dataset.k; viewPeople(); });
  document.querySelectorAll('tbody tr').forEach(tr =>
    tr.onclick = () => openPerson(tr.dataset.id));
  document.querySelectorAll('.name-them').forEach(b => b.onclick = openNaming);
  document.querySelectorAll('.add-locations').forEach(b => b.onclick = openLocations);
}

// ---------------------------------------------------------------- drawer
function closeDrawer() { $('#drawer').classList.remove('on'); $('#scrim').classList.remove('on'); }

async function openPerson(id) {
  if (!id || !id.startsWith('p_')) return;
  const d = await api('/api/people/' + id), p = d.profile;
  const secs = s => s < 90 ? `${s|0}s` : s < 5400 ? `${(s/60)|0}m`
    : s < 172800 ? `${(s/3600).toFixed(1)}h` : `${(s/86400).toFixed(1)}d`;
  $('#drawer').innerHTML = `
    <span class="close">&times;</span>
    <h3>${esc(p.name)} <span class="tag s-${p.state}">${STATE_LABEL[p.state]}</span></h3>
    <div class="locline" id="locline"></div>
    <div class="headline">${esc(p.headline)}</div>
    ${p.vibe ? vibeCard(p.vibe) : ''}
    <div class="thankbox" id="thankbox"><button class="btn ghost" id="thank-btn">Draft a thank-you</button></div>
    <div class="metrics">
      <div class="metric"><div class="n">${p.msgs_per_week_recent.toFixed(1)}</div><div class="l">msgs / week now</div></div>
      <div class="metric"><div class="n">${p.warmth.toFixed(0)}</div><div class="l">warmth</div></div>
      <div class="metric"><div class="n">${p.importance.toFixed(0)}</div><div class="l">importance</div></div>
      <div class="metric"><div class="n">${p.depth.toFixed(0)}</div><div class="l">depth</div></div>
      <div class="metric"><div class="n">${secs(p.their_median_reply_s)}</div><div class="l">their reply</div></div>
      <div class="metric"><div class="n">${secs(p.my_median_reply_s)}</div><div class="l">your reply</div></div>
    </div>
    ${p.confidence < .7 ? `<div class="empty">Only ${p.total_messages} messages here, so
      this is a sketch rather than a reading (confidence ${(p.confidence*100).toFixed(0)}%).</div>` : ''}
    <h2>What the numbers say — and what else they could mean</h2>
    ${p.signals.map(sig).join('')}
    <h2>Connected to</h2>
    <div class="cmembers">${d.connections.length ? d.connections.map(x =>
      `<button class="mchip" data-person="${esc(x.id)}">${esc(x.name)}<small>${esc(x.via.join(', '))}</small></button>`).join('')
      : '<div class="pempty">No group chats together, just your one-to-one thread.</div>'}</div>
    <h2>Plans together</h2>
    <div class="plist">${d.plans.length ? d.plans.map(pl => `
      <div class="prow" tabindex="0" data-plan="${esc(pl.id)}"><span class="st" style="background:${STATE_COLOR[pl.state]}22;
        color:${STATE_COLOR[pl.state]}">${pl.state}</span>
        <span>${esc(pl.title.split(' with ')[0])}</span>
        <span class="when">${esc(pl.when_text || '—')} · ${pl.last_activity.slice(5,10)}</span></div>`).join('')
      : '<div class="empty">No plans found in this thread.</div>'}</div>
    <h2>Messaging rate</h2>
    <div class="chartbox"><div class="chead">
      <div><b id="p-head">—</b><span id="p-sub"></span></div></div>
      <div class="plot" id="p-rate"></div></div>
    <h2>Chat history</h2>
    <div class="hbar">
      <input id="hq" class="search" type="search" placeholder="Search this conversation…"
        autocomplete="off" spellcheck="false">
      <span class="hcount" id="hcount"></span>
    </div>
    <div id="hist"></div>
    ${d.thread_id ? `<div class="drawer-foot">Not a real conversation?
      <button class="btn ghost hide-thread" data-tid="${esc(d.thread_id)}">Set it aside as automated</button>
      <span class="dnote" role="status"></span></div>` : ''}`;
  $('#drawer .close').onclick = closeDrawer;
  $('#drawer').classList.add('on'); $('#scrim').classList.add('on');
  $('#drawer').scrollTop = 0;
  $('#drawer').querySelectorAll('.mchip[data-person]').forEach(el =>
    el.onclick = () => openPerson(el.dataset.person));
  renderLocation(id, d.location, d.location_unknown);
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

  const sd = await api(`/api/people/${id}/series?weeks=40`);
  const host = $('#p-rate');
  if (host) {
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
  const d = await api(`/api/people/${H.cid}/history?` + qs);
  H.messages = append ? [...d.messages, ...H.messages] : d.messages;
  H.total = d.total; H.matched = d.matched; H.more = d.has_more;
  H.before = d.oldest;
  renderHistory();
}

function renderHistory() {
  const box = $('#hist');
  if (!box) return;
  $('#hcount').textContent = H.q
    ? `${H.matched} of ${H.total} messages match`
    : `showing ${H.messages.length} of ${H.total}`;

  if (!H.messages.length) {
    box.innerHTML = '<div class="empty">Nothing in this conversation matches that.</div>';
    return;
  }
  const parts = [];
  if (H.more) parts.push('<button class="more" id="older">Load older messages</button>');
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

function vibeCard(v) {
  const signed = n => (n > 0 ? '+' : '') + Math.round(n);
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
      <span>${Math.round(v.positive_share * 100)}% upbeat · ${Math.round(v.heavy_share * 100)}% heavy</span>
      <span class="shift">${esc(v.mood_shift)}</span>
    </div>
    <div class="benign">${esc(v.mood_note)}</div>
  </div>`;
}

async function openPlan(id) {
  const d = await api('/api/plans/' + encodeURIComponent(id));
  const drawer = $('#drawer');
  if (!d.id) {
    drawer.innerHTML = `<span class="close">&times;</span>
      <div class="empty">${esc(d.detail || "That plan couldn't be loaded.")}</div>`;
  } else {
    const c = STATE_COLOR[d.state] || PAINT.unclear;
    const word = d.activity.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const mark = t => esc(t).replace(new RegExp(`\\b(${word})\\b`, 'gi'), '<mark>$1</mark>');
    const day = ts => new Date(ts).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
    const time = ts => new Date(ts).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    const person = !d.is_group && d.people.length ? d.people[0] : null;
    drawer.innerHTML = `
      <span class="close">&times;</span>
      <h3>${esc(d.activity)} <span class="tag" style="background:${c}22;color:${c}">${d.state}</span></h3>
      <div class="headline">In <b>${esc(d.thread_name)}</b>${d.when_text ? ` · proposed for <b>${esc(d.when_text)}</b>` : ''}
        · last mentioned ${d.days_idle === 0 ? 'today' : d.days_idle === 1 ? 'yesterday' : d.days_idle + ' days ago'}</div>
      ${d.state === 'stale' ? `<div class="pdef">Wanted, and nobody picked a day. There's often a drafted nudge for
        plans like this under Messages worth sending.</div>` : ''}
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
      ${person ? `<div class="drawer-foot"><button class="btn ghost open-person" data-person="${esc(person.id)}">Open
        ${esc(shortName(person.name))}'s profile</button></div>` : ''}`;
    const op = drawer.querySelector('.open-person');
    if (op) op.onclick = () => openPerson(op.dataset.person);
  }
  drawer.querySelector('.close').onclick = closeDrawer;
  drawer.classList.add('on'); $('#scrim').classList.add('on');
  drawer.scrollTop = 0;
}

// ---------------------------------------------------------------- graph
async function viewGraph() {
  $('#main').innerHTML = `
    <h1>Plan graph</h1>
    <div class="sub">People and the live plans connecting them, pulled out of message text.
      Turn on stale plans to see what was agreed to and never landed. Drag to rearrange,
      scroll to zoom, hover to isolate, click anything for detail.</div>
    <div class="controls">
      <span class="chip ${S.includeStale ? 'on' : ''}" id="t-stale">show stale plans</span>
      <span class="focus" role="group" aria-label="How much of the graph to show">${
        [['everyone', 'everyone'], ['connected', 'connected'], ['strongest', 'strongest ties']]
          .map(([k, label]) => `<span class="chip ${S.focus === k ? 'on' : ''}"
            data-focus="${k}">${label}</span>`).join('')}</span>
      <span class="hint" id="g-count">node size = importance · line = shared thread</span>
    </div>
    <div class="gwrap"><svg id="g"></svg>
      <div class="legend"><b>plan state</b>
        ${['scheduled', 'confirmed', 'proposed', 'floated', 'stale'].map(k =>
          `<span><i style="background:${STATE_COLOR[k]}"></i>${k}</span>`).join('')}
        <b style="margin-top:4px">connection</b>
        ${['close', 'steady', 'practical', 'uneven', 'quiet'].map(k =>
          `<span><i style="background:${STATE_COLOR_PERSON[k]}"></i>${STATE_LABEL[k]}</span>`).join('')}
      </div></div>`;
  $('#t-stale').onclick = () => { S.includeStale = !S.includeStale; viewGraph(); };
  document.querySelectorAll('[data-focus]').forEach(c => c.onclick = () => {
    S.focus = c.dataset.focus;
    document.querySelectorAll('[data-focus]').forEach(o =>
      o.classList.toggle('on', o.dataset.focus === S.focus));
    if (S.graph) simulate(S.graph);
  });
  S.graph = await api('/api/graph?include_stale=' + S.includeStale);
  if (S.view === 'graph') simulate(S.graph);
}

// Most people in a real history share no group chat and are in no live plan, so the
// graph arrives as a handful of clusters in a field of loose dots and the structure is
// the hardest thing to see. These narrow it: "connected" drops anyone with no edge at
// all, "strongest" keeps the busiest quarter of what's left and the ties between them.
// Nothing is deleted -- it's a view, and "everyone" puts it all back.
function focusGraph(data, mode) {
  if (mode === 'everyone') return data;
  const strength = new Map(data.nodes.map(n => [n.id, 0]));
  const planMates = new Map();                 // plan id -> the people in it
  for (const l of data.links) {
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
  const keep = new Set([...people, ...plans]);
  return { ...data,
           nodes: data.nodes.filter(n => keep.has(n.id)),
           links: data.links.filter(l => keep.has(l.source) && keep.has(l.target)) };
}

function simulate(raw) {
  const data = focusGraph(raw, S.focus);
  const note = $('#g-count');
  if (note) {
    const shown = data.nodes.filter(n => n.type === 'person').length;
    const all = raw.nodes.filter(n => n.type === 'person').length;
    note.textContent = S.focus === 'everyone'
      ? 'node size = importance \u00b7 line = shared thread'
      : `${shown} of ${all} people \u00b7 ${data.links.length} of ${raw.links.length} connections`;
  }
  const svg = $('#g'), W = svg.clientWidth || 900, H = svg.clientHeight || 560;
  const NS = 'http://www.w3.org/2000/svg';
  const nodes = data.nodes.map((n, i) => ({
    ...n,
    x: W / 2 + Math.cos(i * 2.4) * (60 + (i % 17) * 14),
    y: H / 2 + Math.sin(i * 2.4) * (60 + (i % 13) * 16),
    vx: 0, vy: 0,
    r: n.type === 'person' ? 9 + n.importance / 7 : 3.5 + n.size * 1.5,
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
    const shared = l.kind === 'shared_thread';
    e.setAttribute('stroke', shared ? PAINT.line : PAINT['line-strong']);
    // Sharing four group chats should look like more than sharing one.
    e.setAttribute('stroke-width', shared ? Math.min(3.5, 1 + ((l.weight || 1) - 1) * 0.8) : 1.4);
    if (shared) e.setAttribute('stroke-dasharray', '3 4');
    gl.append(e); return e;
  });

  const nodeEls = nodes.map(n => {
    const g = document.createElementNS(NS, 'g');
    g.style.cursor = 'pointer';
    const c = document.createElementNS(NS, 'circle');
    c.setAttribute('r', n.r);
    c.setAttribute('fill', n.type === 'person'
      ? STATE_COLOR_PERSON[n.state] : STATE_COLOR[n.state]);
    c.setAttribute('fill-opacity', n.type === 'person' ? .9 : .78);
    c.setAttribute('stroke', PAINT.bg);
    c.setAttribute('stroke-width', 1.5);
    g.append(c);
    const person = n.type === 'person';
    if (person || n.state !== 'stale') {
      const t = document.createElementNS(NS, 'text');
      t.textContent = person ? shortName(n.label) : n.label;
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
    g.onclick = () => n.type === 'person' ? openPerson(n.id) : openPlan(n.id);
    g.onpointerenter = () => highlight(n.id);
    g.onpointerleave = () => highlight(null);
    g.onpointerdown = ev => {
      ev.stopPropagation(); n.fixed = true; g.setPointerCapture(ev.pointerId);
      const move = e => {
        const p = toLocal(e); n.x = p.x; n.y = p.y; n.vx = n.vy = 0; alpha = Math.max(alpha, .12);
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
  const CHARGE = 2600, DAMP = .82;
  function tick() {
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
        const pad = (a.type === 'person' || b.type === 'person') ? 18 : 8;
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
    for (const n of nodes) {
      n.vx += (W / 2 - n.x) * .0015; n.vy += (H / 2 - n.y) * .0019;
      if (n.fixed) { n.vx = n.vy = 0; continue; }
      n.vx *= DAMP; n.vy *= DAMP;
      n.x += n.vx * alpha; n.y += n.vy * alpha;
    }
    links.forEach((l, i) => {
      lineEls[i].setAttribute('x1', l.s.x); lineEls[i].setAttribute('y1', l.s.y);
      lineEls[i].setAttribute('x2', l.t.x); lineEls[i].setAttribute('y2', l.t.y);
    });
    nodes.forEach((n, i) => nodeEls[i].setAttribute('transform', `translate(${n.x},${n.y})`));
    alpha = Math.max(.02, alpha * .995);
    requestAnimationFrame(tick);
  }
  applyT(); tick();
}

// ---------------------------------------------------------------- privacy
// Excluding someone re-reads your whole history, so the old handler -- post on every
// click, then rebuild this page -- made four exclusions four passes, with the chip dead
// until each finished. The chip flips straight away and the passes are batched into one.
let exclTimer = null;
function queueExclusions() {
  clearTimeout(exclTimer);
  const note = $('#excl-note');
  if (note) note.textContent = 'Saving\u2026';
  exclTimer = setTimeout(async () => {
    const ids = [...document.querySelectorAll('.chip.x.off')].map(c => c.dataset.id);
    let res;
    try {
      res = await postJSON('/api/privacy', { excluded_contact_ids: ids });
    } catch {
      return;                                  // signedOut() has taken the page over
    }
    if (!res.ok) {
      if (note) note.textContent = res.error;
      return viewPrivacy();                    // put the chips back the way the server has them
    }
    S.overview = await api('/api/overview');
    S.people = await api('/api/people');
    $('#c-people').textContent = S.overview.contacts;
    syncFoot();
    if (note) {
      note.textContent = ids.length
        ? `${ids.length} excluded \u00b7 ${S.overview.messages.toLocaleString()} messages in view`
        : 'Nobody excluded.';
    }
  }, 400);
}

async function viewPrivacy() {
  const d = await api('/api/privacy'), s = d.settings;
  const all = await api('/api/contacts');
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
        'Master switch. While on, no request can leave this machine for any reason.')}
    ${T('stats_only', s.stats_only, 'Never send message text',
        'Derived numbers may cross the boundary; raw message bodies may not.')}
    ${T('redact_on_send', s.redact_on_send, 'Redact identifiers',
        'Strips phone numbers, emails, addresses, URLs, and card-shaped digits before anything is sent.')}
    <h2>Exclude people entirely</h2>
    <div class="sub">An excluded person is removed before analysis runs — including from any group
      thread they are in, so they cannot leak back in through group statistics.</div>
    <div class="excl">${all.map(p =>
      `<span class="chip x ${p.excluded ? 'off' : 'on'}"
        data-id="${esc(p.id)}">${esc(p.name)}</span>`).join('')}</div>
    <div class="sub tight" id="excl-note" role="status"></div>
    <h2>What a Claude call would contain</h2>
    <div class="sub">Pass 2 uses Claude to draft messages. This is the exact payload that would
      be sent for ${esc(S.people[0].name)} — shown in full, before anything leaves, every time.</div>
    <pre id="preview">select a person…</pre>
`;

  document.querySelectorAll('.toggle').forEach(t => t.onclick = async () => {
    const key = t.dataset.k;
    const body = {}; body[key] = !t.classList.contains('on');
    await fetch('/api/privacy', { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body) });
    S.overview = await api('/api/overview'); syncFoot(); viewPrivacy();
  });
  document.querySelectorAll('.chip.x').forEach(c => c.onclick = () => {
    c.classList.toggle('off');
    c.classList.toggle('on');
    queueExclusions();
  });
  const pv = await api('/api/privacy/preview/' + S.people[0].contact_id);
  $('#preview').textContent = (pv.allowed ? '' :
    `[BLOCKED — ${pv.reason}]\n[redacted: ${pv.redactions.join(', ') || 'nothing matched'}]\n\n`)
    + pv.would_send;
}

boot();
