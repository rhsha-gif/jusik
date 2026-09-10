"""Self-contained candlestick chart renderer.

``render_chart`` returns one HTML string with zero external references --
data, styles, and interaction code are all inlined -- so the file keeps
working offline and never leaks a symbol name to a CDN.

The canvas code is deliberately dependency-free: the whole interaction model
is one visible index range ``[i0, i1)`` plus a redraw, which is easy to audit
and reuse for any asset class that satisfies the :class:`~marketdata.types.Bar`
contract.
"""

from __future__ import annotations

import html
import json

from marketdata.types import Bar

_TEMPLATE = """<!doctype html>
<html lang="ko">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE_HTML__</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #0e1116; color: #c9d1d9; font: 13px/1.4 system-ui, sans-serif;
         height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
  header { display: flex; align-items: center; gap: 16px; padding: 8px 12px; }
  header h1 { font-size: 14px; font-weight: 600; }
  header label { display: flex; align-items: center; gap: 5px; cursor: pointer; color: #8b949e; }
  #stage { position: relative; flex: 1; min-height: 0; }
  canvas { position: absolute; inset: 0; width: 100%; height: 100%; cursor: crosshair; }
  #info { position: absolute; top: 8px; left: 84px; background: #161b22cc; border: 1px solid #1f2530;
          border-radius: 4px; padding: 6px 9px; pointer-events: none; display: none;
          font-variant-numeric: tabular-nums; white-space: pre; }
</style>
<body>
<header>
  <h1>__TITLE_HTML__</h1>
  <label><input type="checkbox" id="logToggle"> 로그 스케일</label>
</header>
<div id="stage"><canvas id="chart"></canvas><div id="info"></div></div>
<script>
'use strict';
const DATA = __DATA__; /* [date, open, high, low, close, volume] */
const N = DATA.length;
const BG = '#0e1116', GRID = '#1f2530', FG = '#c9d1d9', DIM = '#8b949e';
const UP = '#e5484d', DOWN = '#3b82f6'; /* Korean convention: red up, blue down */
const MARGIN = { left: 76, right: 14, top: 8, bottom: 26 };
const PRICE_SHARE = 0.78, PANEL_GAP = 8, MIN_SPAN = Math.min(20, N);

const canvas = document.getElementById('chart');
const ctx = canvas.getContext('2d');
const info = document.getElementById('info');
const logToggle = document.getElementById('logToggle');

let i0 = 0, i1 = N;          /* visible range, [i0, i1) */
let logScale = false;
let mouse = null;            /* {x, y} in CSS px, or null */
let drag = null;             /* {startX, startI0} while panning */

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

function fmtPrice(p) {
  const digits = p >= 1000 ? 0 : (p >= 1 ? 2 : 6);
  return p.toLocaleString('en-US', { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}
function fmtVol(v) { return v.toLocaleString('en-US', { maximumFractionDigits: 3 }); }

function layout() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  const plotW = w - MARGIN.left - MARGIN.right;
  const plotH = h - MARGIN.top - MARGIN.bottom;
  const priceH = (plotH - PANEL_GAP) * PRICE_SHARE;
  return { w, h, plotW,
           priceTop: MARGIN.top, priceH,
           volTop: MARGIN.top + priceH + PANEL_GAP,
           volH: (plotH - PANEL_GAP) * (1 - PRICE_SHARE) };
}

function xCenter(i, L) { return MARGIN.left + (i - i0 + 0.5) / (i1 - i0) * L.plotW; }

function priceY(p, lo, hi, L) {
  const t = logScale
    ? (Math.log10(p) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo))
    : (p - lo) / (hi - lo);
  return L.priceTop + (1 - t) * L.priceH;
}

function visibleExtents() {
  let lo = Infinity, hi = -Infinity, maxV = 0;
  for (let i = i0; i < i1; i++) {
    if (DATA[i][3] < lo) lo = DATA[i][3];
    if (DATA[i][2] > hi) hi = DATA[i][2];
    if (DATA[i][5] > maxV) maxV = DATA[i][5];
  }
  if (hi <= lo) { hi = lo * 1.01 + 1; }        /* flat data guard */
  const pad = logScale ? 1.02 : (hi - lo) * 0.03;
  if (logScale) { lo /= pad; hi *= pad; } else { lo -= pad; hi += pad; }
  if (logScale && lo <= 0) lo = Math.min(...DATA.map(d => d[3])) / 2;
  return { lo, hi, maxV: maxV || 1 };
}

function draw() {
  const L = layout();
  ctx.fillStyle = BG;
  ctx.fillRect(0, 0, L.w, L.h);
  const span = i1 - i0;
  const ext = visibleExtents();
  ctx.font = '11px system-ui, sans-serif';

  /* horizontal grid + 5 y-axis ticks, evenly spaced in display space */
  for (let k = 0; k < 5; k++) {
    const t = k / 4;
    const price = logScale
      ? Math.pow(10, Math.log10(ext.lo) + t * (Math.log10(ext.hi) - Math.log10(ext.lo)))
      : ext.lo + t * (ext.hi - ext.lo);
    const y = priceY(price, ext.lo, ext.hi, L);
    ctx.strokeStyle = GRID;
    ctx.beginPath(); ctx.moveTo(MARGIN.left, y); ctx.lineTo(L.w - MARGIN.right, y); ctx.stroke();
    ctx.fillStyle = DIM; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    ctx.fillText(fmtPrice(price), MARGIN.left - 6, y);
  }

  /* vertical grid + 6 date labels */
  for (let k = 0; k < 6; k++) {
    const i = i0 + Math.round((span - 1) * k / 5);
    const x = xCenter(i, L);
    ctx.strokeStyle = GRID;
    ctx.beginPath(); ctx.moveTo(x, MARGIN.top); ctx.lineTo(x, L.volTop + L.volH); ctx.stroke();
    ctx.fillStyle = DIM; ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    ctx.fillText(DATA[i][0], x, L.volTop + L.volH + 6);
  }

  const bodyW = L.plotW / span * 0.7;
  for (let i = i0; i < i1; i++) {
    const [, o, h, l, c, v] = DATA[i];
    const x = xCenter(i, L);
    const color = c >= o ? UP : DOWN;
    ctx.strokeStyle = color; ctx.fillStyle = color;
    const yH = priceY(h, ext.lo, ext.hi, L), yL = priceY(l, ext.lo, ext.hi, L);
    if (bodyW < 1) {                          /* too dense: high-low line only */
      ctx.beginPath(); ctx.moveTo(x, yH); ctx.lineTo(x, yL); ctx.stroke();
    } else {
      ctx.beginPath(); ctx.moveTo(x, yH); ctx.lineTo(x, yL); ctx.stroke();
      const yO = priceY(o, ext.lo, ext.hi, L), yC = priceY(c, ext.lo, ext.hi, L);
      const top = Math.min(yO, yC);
      ctx.fillRect(x - bodyW / 2, top, bodyW, Math.max(1, Math.abs(yC - yO)));
    }
    const vh = v / ext.maxV * L.volH;
    ctx.globalAlpha = 0.55;
    ctx.fillRect(x - Math.max(bodyW, 1) / 2, L.volTop + L.volH - vh, Math.max(bodyW, 1), vh);
    ctx.globalAlpha = 1;
  }

  if (mouse) drawCrosshair(L, ext); else info.style.display = 'none';
}

function drawCrosshair(L, ext) {
  const span = i1 - i0;
  const idx = clamp(i0 + Math.floor((mouse.x - MARGIN.left) / L.plotW * span), i0, i1 - 1);
  const x = xCenter(idx, L);
  ctx.save();
  ctx.strokeStyle = DIM; ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(x, MARGIN.top); ctx.lineTo(x, L.volTop + L.volH); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(MARGIN.left, mouse.y); ctx.lineTo(L.w - MARGIN.right, mouse.y); ctx.stroke();
  ctx.restore();

  const [date, o, h, l, c, v] = DATA[idx];
  const prevClose = idx > 0 ? DATA[idx - 1][4] : null;
  const changeLine = prevClose === null ? '전일대비 -'
    : '전일대비 ' + ((c / prevClose - 1) * 100).toFixed(2) + '%';
  info.textContent = [date,
    '시 ' + fmtPrice(o), '고 ' + fmtPrice(h), '저 ' + fmtPrice(l), '종 ' + fmtPrice(c),
    '거래량 ' + fmtVol(v), changeLine].join('\\n');
  info.style.display = 'block';
}

function resize() {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(canvas.clientWidth * dpr);
  canvas.height = Math.round(canvas.clientHeight * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}

canvas.addEventListener('wheel', e => {
  e.preventDefault();
  const L = layout();
  const span = i1 - i0;
  const frac = clamp((e.offsetX - MARGIN.left) / L.plotW, 0, 1);
  const anchor = i0 + frac * span;             /* index under the cursor stays put */
  const newSpan = clamp(Math.round(span * (e.deltaY > 0 ? 1.15 : 1 / 1.15)), MIN_SPAN, N);
  i0 = clamp(Math.round(anchor - frac * newSpan), 0, N - newSpan);
  i1 = i0 + newSpan;
  draw();
}, { passive: false });

canvas.addEventListener('mousedown', e => { drag = { startX: e.offsetX, startI0: i0 }; });
window.addEventListener('mouseup', () => { drag = null; });

canvas.addEventListener('mousemove', e => {
  mouse = { x: e.offsetX, y: e.offsetY };
  if (drag) {
    const L = layout();
    const span = i1 - i0;
    const di = Math.round((drag.startX - e.offsetX) / (L.plotW / span));
    i0 = clamp(drag.startI0 + di, 0, N - span);
    i1 = i0 + span;
  }
  draw();
});

canvas.addEventListener('mouseleave', () => { mouse = null; drag = null; draw(); });
logToggle.addEventListener('change', () => { logScale = logToggle.checked; draw(); });
window.addEventListener('resize', resize);
resize();
</script>
</body>
</html>
"""


def render_chart(bars: list[Bar], title: str) -> str:
    """Render *bars* as a standalone interactive candlestick chart."""
    if not bars:
        raise ValueError("cannot render a chart from zero bars")
    data = [
        [b["date"], b["open"], b["high"], b["low"], b["close"], b["volume"]] for b in bars
    ]
    return _TEMPLATE.replace("__TITLE_HTML__", html.escape(title)).replace(
        "__DATA__", json.dumps(data, separators=(",", ":"))
    )
