"""report.html — a self-contained, interactive view of report.json (works offline, no external libraries).

Everything in report.json is shown: brand Pages, every location, every review with its text, verdict, exact date,
tags, reactions, photos, comment count and the replies (Page replies with their generated / manual classification),
plus statistics and charts computed in the browser from the same data.
"""

from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = r"""<!doctype html>
<html lang="bg">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--muted:#5d6472;--line:#e3e6eb;--accent:#2f6fde;--pos:#1f9d55;--neg:#d64545;
--warn:#c98a00;--chip:#eef2f8;--shadow:0 1px 2px rgba(0,0,0,.06),0 2px 8px rgba(0,0,0,.04)}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--ink:#e8eaee;--muted:#9aa3b2;--line:#2a2f3a;
--accent:#6aa0ff;--pos:#3fc47c;--neg:#ff6b6b;--warn:#e0a92e;--chip:#222836;--shadow:none}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1240px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:0}h2{font-size:18px;margin:32px 0 12px}h3{font-size:15px;margin:0 0 6px}
.sub{color:var(--muted);margin-top:4px}
.grid{display:grid;gap:12px}.kpis{grid-template-columns:repeat(auto-fit,minmax(160px,1fr));margin-top:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;box-shadow:var(--shadow)}
.kpi .v{font-size:24px;font-weight:650;letter-spacing:-.02em}.kpi .l{color:var(--muted);font-size:12.5px}
.two{grid-template-columns:repeat(auto-fit,minmax(360px,1fr))}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:8px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;cursor:pointer;white-space:nowrap;user-select:none}th:hover{color:var(--ink)}
td.n,th.n{text-align:right}.tablewrap{overflow-x:auto}
.bar{height:8px;border-radius:4px;background:var(--line);overflow:hidden;min-width:60px}.bar>i{display:block;height:100%;background:var(--pos)}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;background:var(--chip);color:var(--muted);margin:2px 4px 2px 0}
.pill.pos{background:color-mix(in srgb,var(--pos) 15%,transparent);color:var(--pos)}.pill.neg{background:color-mix(in srgb,var(--neg) 15%,transparent);color:var(--neg)}
.pill.gen{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}.pill.man{background:color-mix(in srgb,var(--accent) 15%,transparent);color:var(--accent)}
.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:12px}
select,input[type=search]{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:7px 10px;font:inherit}
input[type=search]{min-width:220px;flex:1}label.ck{display:flex;gap:6px;align-items:center;color:var(--muted)}
button{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:7px 12px;font:inherit;cursor:pointer}button.ghost{background:transparent;color:var(--accent);border:1px solid var(--line)}
.rev{padding:14px 16px}.rev+.rev{margin-top:10px}.rev .head{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.rev .who{font-weight:600}.rev .meta{color:var(--muted);font-size:12.5px}.rev .txt{margin:8px 0;white-space:pre-wrap}
.reply{margin:8px 0 0 18px;padding:8px 12px;border-left:3px solid var(--line);background:color-mix(in srgb,var(--chip) 60%,transparent);border-radius:0 8px 8px 0}
.reply.page{border-left-color:var(--accent)}.reply .who{font-weight:600}.small{font-size:12px;color:var(--muted)}
svg text{fill:var(--muted);font-size:11px}.legend{display:flex;gap:12px;color:var(--muted);font-size:12px;margin-top:6px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.brandcard .row{display:flex;flex-wrap:wrap;gap:6px 16px;color:var(--muted);font-size:13px;margin-top:6px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.empty{color:var(--muted);padding:16px;text-align:center}
</style>
</head>
<body>
<div class="wrap">
  <h1 id="title"></h1>
  <div class="sub" id="subtitle"></div>
  <div class="grid kpis" id="kpis"></div>

  <h2>По държави</h2>
  <div class="filters"><select id="fCountry"></select><span class="small">филтърът важи за целия отчет отдолу</span></div>
  <div class="card tablewrap"><table id="ctable"></table></div>

  <h2>Страници на бранда</h2>
  <div class="grid two" id="brands"></div>

  <h2>Локации — сравнение</h2>
  <div class="card tablewrap"><table id="loctable"></table></div>

  <div class="grid two" style="margin-top:12px">
    <div class="card"><h3>% препоръка по локации (Facebook)</h3><div id="chartPct"></div></div>
    <div class="card"><h3>Ревюта по години</h3><div id="chartYears"></div>
      <div class="legend"><span><i style="background:var(--pos)"></i>препоръчват</span><span><i style="background:var(--neg)"></i>не препоръчват</span></div></div>
  </div>
  <div class="grid two" style="margin-top:12px">
    <div class="card"><h3>Най-чести етикети в ревютата</h3><div id="chartTags"></div>
      <div class="legend"><span><i style="background:var(--pos)"></i>в препоръки</span><span><i style="background:var(--neg)"></i>в непрепоръки</span></div></div>
    <div class="card"><h3>Отговори от страниците</h3><div id="replyStats"></div></div>
  </div>

  <h2>Всички ревюта</h2>
  <div class="filters">
    <select id="fLoc"></select>
    <select id="fVerdict"><option value="">Всички мнения</option><option value="pos">Препоръчва</option><option value="neg">Не препоръчва</option></select>
    <select id="fSort"><option value="new">Най-нови</option><option value="old">Най-стари</option><option value="react">Най-много реакции</option><option value="long">Най-дълги</option></select>
    <label class="ck"><input type="checkbox" id="fReply">с отговор от страницата</label>
    <label class="ck"><input type="checkbox" id="fPhoto">със снимки</label>
    <input type="search" id="fText" placeholder="Търсене в текста, автора, етикетите…">
    <button class="ghost" id="dlCsv">CSV</button><button class="ghost" id="dlJson">JSON</button>
  </div>
  <div class="small" id="count"></div>
  <div id="reviews"></div>
  <div style="text-align:center;margin-top:14px"><button id="more">Покажи още</button></div>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const STATUS = {ok:'готово',partial:'частично',summary_only:'само публични данни',no_reviews:'няма ревюта',reviews_disabled:'ревютата са изключени',not_found:'не е намерена',error:'грешка',login_required:'иска вход',blocked:'блокирано'};
const MONTHS = {'януари':1,'февруари':2,'март':3,'април':4,'май':5,'юни':6,'юли':7,'август':8,'септември':9,'октомври':10,'ноември':11,'декември':12,
  january:1,february:2,march:3,april:4,may:5,june:6,july:7,august:8,september:9,october:10,november:11,december:12};
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = n => n == null ? '—' : Number(n).toLocaleString('bg-BG');
const pct = (a, b) => b ? Math.round(100 * a / b) : null;
const genYear = new Date(D.generated_at || Date.now()).getFullYear();
function parseDate(r) {
  const s = (r.date_full || r.date_text || '').toLowerCase();
  let m = s.match(/(\d{1,2})\s+([a-zа-я]+)\s*,?\s*(\d{4})?/i);
  if (m && MONTHS[m[2]]) { const t = s.match(/(\d{1,2}):(\d{2})/);
    return new Date(+(m[3] || genYear), MONTHS[m[2]] - 1, +m[1], t ? +t[1] : 0, t ? +t[2] : 0); }
  m = s.match(/([a-z]+)\s+(\d{1,2}),?\s*(\d{4})?/i);
  if (m && MONTHS[m[1]]) return new Date(+(m[3] || genYear), MONTHS[m[1]] - 1, +m[2]);
  return null;
}
function methodBg(m) {
  if (m.startsWith('no page replies')) return 'Няма отговори от страницата за класифициране.';
  return 'Метод: ' + m.replace('template clusters', 'шаблонни клъстери').replace('style heuristics', 'стилови сигнали').replace('model judge', 'модел');
}
const pages = [...(D.brand_sections || []).map(p => ({...p, _brand: true})), ...(D.locations || [])];
const countryOf = p => (p.location && p.location.country) || (p._brand ? 'Глобална' : 'Без държава');
const ALL_REVIEWS = [];
pages.forEach((p, pi) => (p.reviews || []).forEach((r, ri) => {
  const d = parseDate(r);
  ALL_REVIEWS.push({...r, _page: p, _pi: pi, _date: d, _ts: d ? d.getTime() : 0, _country: countryOf(p),
    _pageReplies: (r.replies || []).filter(x => x.is_page_reply)});
}));
const ALL_LOCS = D.locations || [];
let selCountry = '';
const inC = p => !selCountry || countryOf(p) === selCountry;

document.title = `${D.brand} — Facebook ревюта`;
document.getElementById('title').textContent = `${D.brand} — Facebook ревюта и отговори`;
const COUNTRIES = [...new Set(pages.map(countryOf))].sort((a, b) => a.localeCompare(b, 'bg'));
document.getElementById('subtitle').textContent =
  `Генерирано ${new Date(D.generated_at).toLocaleString('bg-BG')} · ${COUNTRIES.length} държави · ${ALL_LOCS.length} локации · ${(D.brand_sections||[]).length} страници на бранда · модел ${D.model || '—'}`;

// countries table + filter
const fCountry = document.getElementById('fCountry');
fCountry.innerHTML = '<option value="">Всички държави</option>' + COUNTRIES.map(c => `<option>${esc(c)}</option>`).join('');
function renderCountries() {
  const rows = COUNTRIES.map(c => {
    const ps = pages.filter(p => countryOf(p) === c), ls = ps.filter(p => !p._brand), rs = ALL_REVIEWS.filter(r => r._country === c);
    const rec = rs.filter(r => r.recommends === true).length, nrec = rs.filter(r => r.recommends === false).length;
    const pcts = ls.map(l => l.recommend_pct).filter(v => v != null);
    return {c, brands: ps.length - ls.length, locs: ls.length, fb: ls.reduce((a, l) => a + (l.review_total || 0), 0), got: rs.length,
      share: pct(rec, rec + nrec), avg: pcts.length ? Math.round(pcts.reduce((a, b) => a + b, 0) / pcts.length) : null,
      replies: rs.filter(r => r._pageReplies.length).length};
  }).sort((a, b) => b.locs - a.locs || b.got - a.got);
  document.getElementById('ctable').innerHTML = `<thead><tr><th>Държава</th><th class="n">Стр. на бранда</th><th class="n">Локации</th>
    <th class="n">Ревюта във FB</th><th class="n">Извлечени</th><th>Препоръчват (извлечени)</th><th class="n">Средно % (FB)</th><th class="n">С отговор от стр.</th></tr></thead>
    <tbody>${rows.map(r => `<tr data-c="${esc(r.c)}" style="cursor:pointer${selCountry === r.c ? ';background:var(--chip)' : ''}"><td><b>${esc(r.c)}</b></td>
    <td class="n">${r.brands}</td><td class="n">${r.locs}</td><td class="n">${fmt(r.fb)}</td><td class="n">${fmt(r.got)}</td>
    <td>${r.share == null ? '—' : `<div style="display:flex;gap:6px;align-items:center"><div class="bar" style="flex:1"><i style="width:${r.share}%"></i></div>${r.share}%</div>`}</td>
    <td class="n">${r.avg == null ? '—' : r.avg + '%'}</td><td class="n">${r.replies}</td></tr>`).join('')}</tbody>`;
  document.querySelectorAll('#ctable tbody tr').forEach(tr => tr.onclick = () => setCountry(selCountry === tr.dataset.c ? '' : tr.dataset.c));
}
function setCountry(c) { selCountry = c; fCountry.value = c; renderCountries(); renderAll(); shown = 50; render(); }
fCountry.onchange = () => setCountry(fCountry.value);

function renderAll() {
const reviews = ALL_REVIEWS.filter(r => !selCountry || r._country === selCountry);
const locs = ALL_LOCS.filter(inC);
const rec = reviews.filter(r => r.recommends === true).length, nrec = reviews.filter(r => r.recommends === false).length;
const fbTotal = locs.reduce((a, l) => a + (l.review_total || 0), 0);
const withPage = reviews.filter(r => r._pageReplies.length).length;
const pageReplies = reviews.flatMap(r => r._pageReplies);
const gen = pageReplies.filter(x => x.reply_type === 'generated').length, man = pageReplies.filter(x => x.reply_type === 'manual').length;
const kpis = [
  ['Локации', locs.length], ['Ревюта във Facebook', fmt(fbTotal)], ['Извлечени ревюта', fmt(reviews.length)],
  ['Препоръчват', pct(rec, rec + nrec) == null ? '—' : pct(rec, rec + nrec) + '%'], ['Не препоръчват', fmt(nrec)],
  ['Ревюта с отговор от страницата', withPage ? `${withPage} (${pct(withPage, reviews.length)}%)` : '0'],
  ['Генерирани / ръчни отговори', `${gen} / ${man}`], ['Снимки в ревютата', fmt(reviews.reduce((a, r) => a + (r.photos || 0), 0))],
  ['Реакции', fmt(reviews.reduce((a, r) => a + (r.reactions || 0), 0))]];
document.getElementById('kpis').innerHTML = kpis.map(([l, v]) => `<div class="card kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');

// brand pages
const bwrap = document.getElementById('brands');
bwrap.innerHTML = (D.brand_sections || []).filter(b => inC({...b, _brand: true})).map(b => { const L = b.location || {};
  return `<div class="card brandcard"><h3><a href="${esc(L.url)}" target="_blank">${esc(L.name)}</a></h3>
  <div class="row"><span class="pill">${esc(countryOf({...b, _brand: true}))}</span>${[L.category, L.price, L.followers && L.followers + ' последователи', L.website, L.phone, L.email, L.address].filter(Boolean).map(esc).join(' · ')}</div>
  <div class="row"><span>${b.recommend_pct != null ? `<b>${b.recommend_pct}%</b> препоръка` : 'без препоръки на страницата'}</span>
  <span>${b.review_total != null ? fmt(b.review_total) + ' ревюта във FB' : ''}</span><span>${(b.reviews||[]).length ? (b.reviews.length + ' извлечени') : ''}</span>
  <span class="pill">${STATUS[b.status] || b.status}</span></div></div>`; }).join('') || '<div class="card empty">Не е намерена официална страница на бранда.</div>';

// locations table
const cols = [
  ['Локация', l => l.location.name, (l, v) => `<a href="${esc(l.location.url)}" target="_blank">${esc(v)}</a><div class="small">${esc([l.location.category, l.location.address].filter(Boolean).join(' · '))}</div>`],
  ['Държава', l => countryOf(l), (l, v) => esc(v)],
  ['Град', l => l.location.city || '', (l, v) => esc(v) || '—'],
  ['Статус', l => STATUS[l.status] || l.status, (l, v) => `<span class="pill">${esc(v)}</span>`],
  ['% препоръка', l => l.recommend_pct ?? -1, (l, v) => v < 0 ? '—' : `<div style="display:flex;gap:6px;align-items:center"><div class="bar" style="flex:1"><i style="width:${v}%"></i></div>${v}%</div>`],
  ['Ревюта във FB', l => l.review_total ?? -1, (l, v) => v < 0 ? '—' : fmt(v), 'n'],
  ['Извлечени', l => (l.reviews || []).length, (l, v) => fmt(v), 'n'],
  ['Препоръчват', l => (l.reviews || []).filter(r => r.recommends === true).length, (l, v) => fmt(v), 'n'],
  ['Не препоръчват', l => (l.reviews || []).filter(r => r.recommends === false).length, (l, v) => fmt(v), 'n'],
  ['С отговор от стр.', l => (l.reviews || []).filter(r => (r.replies || []).some(x => x.is_page_reply)).length, (l, v) => fmt(v), 'n'],
  ['Последно ревю', l => Math.max(0, ...(l.reviews || []).map(r => (parseDate(r) || {getTime: () => 0}).getTime())),
    (l, v) => v ? new Date(v).toLocaleDateString('bg-BG') : '—', 'n'],
  ['Последователи', l => l.location.followers || '', (l, v) => esc(v) || '—', 'n'],
];
let sortCol = 4, sortDir = -1;
function renderTable() {
  const rows = [...locs].sort((a, b) => { const x = cols[sortCol][1](a), y = cols[sortCol][1](b);
    return (x > y ? 1 : x < y ? -1 : 0) * sortDir; });
  document.getElementById('loctable').innerHTML = `<thead><tr>${cols.map((c, i) => `<th class="${c[3] || ''}" data-i="${i}">${c[0]}${i === sortCol ? (sortDir > 0 ? ' ▲' : ' ▼') : ''}</th>`).join('')}</tr></thead>
  <tbody>${rows.map(l => `<tr>${cols.map(c => `<td class="${c[3] || ''}">${c[2](l, c[1](l))}</td>`).join('')}</tr>`).join('') || `<tr><td colspan="${cols.length}" class="empty">Няма локации</td></tr>`}</tbody>`;
  document.querySelectorAll('#loctable th').forEach(th => th.onclick = () => { const i = +th.dataset.i; sortDir = i === sortCol ? -sortDir : -1; sortCol = i; renderTable(); });
}
renderTable();

// charts (plain SVG)
function hbars(el, items, max, colorA, colorB) {
  if (!items.length) { el.innerHTML = '<div class="empty">Няма данни</div>'; return; }
  const W = 520, rowH = 22, lab = 190, H = items.length * rowH + 4;
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img">${items.map((it, i) => {
    const y = i * rowH + 2, wa = (W - lab - 40) * (it.a / max), wb = (W - lab - 40) * ((it.b || 0) / max);
    return `<text x="0" y="${y + 14}">${esc(it.label.length > 30 ? it.label.slice(0, 29) + '…' : it.label)}</text>
      <rect x="${lab}" y="${y + 4}" width="${Math.max(wa, 0)}" height="12" rx="3" fill="var(${colorA})"/>
      ${it.b ? `<rect x="${lab + wa}" y="${y + 4}" width="${wb}" height="12" rx="3" fill="var(${colorB})"/>` : ''}
      <text x="${lab + wa + wb + 6}" y="${y + 14}">${esc(it.text ?? (it.a + (it.b || 0)))}</text>`; }).join('')}</svg>`;
}
hbars(document.getElementById('chartPct'),
  locs.filter(l => l.recommend_pct != null).sort((a, b) => b.recommend_pct - a.recommend_pct)
    .map(l => ({label: l.location.name.replace(D.brand, '').replace(/^[\s\-–(]+|[)\s]+$/g, '') || l.location.name, a: l.recommend_pct, text: l.recommend_pct + '% · ' + fmt(l.review_total) + ' ревюта'})),
  100, '--pos');
(function years() {
  const by = {}; reviews.forEach(r => { if (!r._date) return; const y = r._date.getFullYear();
    by[y] = by[y] || {p: 0, n: 0}; if (r.recommends === false) by[y].n++; else by[y].p++; });
  const ys = Object.keys(by).sort();
  const el = document.getElementById('chartYears');
  if (!ys.length) { el.innerHTML = '<div class="empty">Няма дати</div>'; return; }
  const max = Math.max(...ys.map(y => by[y].p + by[y].n)), W = 520, H = 170, T = 18, bw = Math.min(46, (W - 40) / ys.length - 8);
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H + T + 22}" width="100%">${ys.map((y, i) => {
    const x = 20 + i * ((W - 40) / ys.length), hp = H * by[y].p / max, hn = H * by[y].n / max, base = T + H;
    return `<rect x="${x}" y="${base - hp - hn}" width="${bw}" height="${hn}" rx="3" fill="var(--neg)"/>
      <rect x="${x}" y="${base - hp}" width="${bw}" height="${hp}" rx="3" fill="var(--pos)"/>
      <text x="${x + bw / 2}" y="${base + 14}" text-anchor="middle">${y}</text>
      <text x="${x + bw / 2}" y="${base - hp - hn - 4}" text-anchor="middle">${by[y].p + by[y].n}${by[y].n ? ' (' + by[y].n + ' не)' : ''}</text>`; }).join('')}</svg>`;
})();
(function tags() {
  const t = {}; reviews.forEach(r => (r.tags || []).forEach(g => { t[g] = t[g] || {p: 0, n: 0}; if (r.recommends === false) t[g].n++; else t[g].p++; }));
  const items = Object.entries(t).map(([k, v]) => ({label: k, a: v.p, b: v.n})).sort((x, y) => (y.a + y.b) - (x.a + x.b)).slice(0, 15);
  hbars(document.getElementById('chartTags'), items, Math.max(1, ...items.map(i => i.a + i.b)), '--pos', '--neg');
})();
(function replies() {
  const unc = pageReplies.filter(x => x.reply_type === 'uncertain').length;
  const hidden = reviews.filter(r => r.replies_unavailable).length, cc = reviews.reduce((a, r) => a + (r.comment_count || 0), 0);
  const tpl = {}; pageReplies.forEach(x => { const k = (x.text || '').slice(0, 70); if (k) tpl[k] = (tpl[k] || 0) + 1; });
  const top = Object.entries(tpl).filter(([, n]) => n > 1).sort((a, b) => b[1] - a[1]).slice(0, 3);
  document.getElementById('replyStats').innerHTML = `
    <div class="grid" style="grid-template-columns:repeat(3,1fr)">
      <div><div class="kpi"><div class="v">${pageReplies.length}</div><div class="l">отговора от страниците</div></div></div>
      <div><div class="kpi"><div class="v">${withPage ? pct(withPage, reviews.length) + '%' : '0%'}</div><div class="l">от ревютата имат отговор</div></div></div>
      <div><div class="kpi"><div class="v">${fmt(cc)}</div><div class="l">коментара (брой по FB)</div></div></div></div>
    <p><span class="pill gen">генерирани ${gen}</span><span class="pill man">ръчни ${man}</span><span class="pill">неясни ${unc}</span>
    ${hidden ? `<span class="pill">${hidden} ревюта с коментари, които Facebook не показва</span>` : ''}</p>
    ${top.length ? '<div class="small">Повтарящи се отговори (шаблони):</div>' + top.map(([k, n]) => `<div class="reply page"><span class="pill gen">${n}×</span>${esc(k)}…</div>`).join('') : ''}
    <div class="small" style="margin-top:8px">${esc(methodBg(D.classification_method || ''))}</div>`;
})();

}  // end renderAll

// reviews explorer
const fLoc = document.getElementById('fLoc');
fLoc.innerHTML = '<option value="">Всички страници</option>' + pages.map((p, i) => `<option value="${i}">${p._brand ? '[Бранд] ' : ''}${esc(p.location.name)} · ${esc(countryOf(p))} (${(p.reviews || []).length})</option>`).join('');
let shown = 50, current = [];
function filtered() {
  const loc = fLoc.value, v = document.getElementById('fVerdict').value, q = document.getElementById('fText').value.trim().toLowerCase();
  const onlyReply = document.getElementById('fReply').checked, onlyPhoto = document.getElementById('fPhoto').checked, s = document.getElementById('fSort').value;
  let out = ALL_REVIEWS.filter(r => (!selCountry || r._country === selCountry) && (loc === '' || r._pi === +loc) && (!v || (v === 'pos' ? r.recommends === true : r.recommends === false))
    && (!onlyReply || r._pageReplies.length) && (!onlyPhoto || r.photos > 0)
    && (!q || [r.text, r.reviewer, (r.tags || []).join(' '), (r.replies || []).map(x => x.text).join(' ')].join(' ').toLowerCase().includes(q)));
  const by = {new: (a, b) => b._ts - a._ts, old: (a, b) => a._ts - b._ts, react: (a, b) => (b.reactions || 0) - (a.reactions || 0), long: (a, b) => (b.text || '').length - (a.text || '').length};
  return out.sort(by[s]);
}
function replyHtml(x) {
  const cls = x.reply_type === 'generated' ? 'gen' : x.reply_type === 'manual' ? 'man' : '';
  const lbl = {generated: 'генериран' + (x.generated_kind === 'template' ? ' (шаблон)' : x.generated_kind === 'ai' ? ' (AI)' : ''), manual: 'ръчен', uncertain: 'неясен'}[x.reply_type];
  return `<div class="reply ${x.is_page_reply ? 'page' : ''}"><span class="who">${esc(x.author || '—')}</span>
    ${x.is_page_reply ? '<span class="pill">страницата</span>' : ''}${lbl ? `<span class="pill ${cls}" title="${esc((x.signals || []).join('; ') + (x.llm_reason ? ' — ' + x.llm_reason : ''))}">${lbl}${x.confidence != null ? ' · ' + Math.round(x.confidence * 100) + '%' : ''}</span>` : ''}
    <span class="small">${esc(x.date_text || '')}</span><div class="txt" style="margin:4px 0 0">${esc(x.text || '(без текст: стикер/снимка)')}</div></div>`;
}
function render() {
  current = filtered();
  document.getElementById('count').textContent = `${current.length} ревюта${current.length > shown ? ` · показани ${shown}` : ''}`;
  document.getElementById('reviews').innerHTML = current.slice(0, shown).map(r => `
    <div class="card rev"><div class="head">
      <span class="who">${esc(r.reviewer || '—')}</span>
      ${r.recommends === true ? '<span class="pill pos">препоръчва</span>' : r.recommends === false ? '<span class="pill neg">не препоръчва</span>' : ''}
      <span class="meta">${esc(r.date_full || r.date_text || '')} · ${r._page._brand ? '[Бранд] ' : ''}${esc(r._page.location.name)} · ${esc(r._country)}</span>
      ${r.permalink ? `<a class="meta" href="${esc(r.permalink)}" target="_blank">виж във Facebook ↗</a>` : ''}</div>
      ${(r.tags || []).length ? `<div>${r.tags.map(t => `<span class="pill">${esc(t)}</span>`).join('')}</div>` : ''}
      <div class="txt">${esc(r.text || '(без текст)')}</div>
      <div class="small">${r.reactions ? '👍 ' + r.reactions + ' · ' : ''}${r.photos ? '📷 ' + r.photos + ' · ' : ''}${r.comment_count ? '💬 ' + r.comment_count + ' коментара' : ''}${r.replies_unavailable ? ' (Facebook не показва текста им)' : ''}</div>
      ${(r.replies || []).map(replyHtml).join('')}
    </div>`).join('') || '<div class="card empty">Няма ревюта по тези филтри.</div>';
  document.getElementById('more').style.display = current.length > shown ? '' : 'none';
}
['fLoc', 'fVerdict', 'fSort', 'fReply', 'fPhoto'].forEach(id => document.getElementById(id).onchange = () => { shown = 50; render(); });
document.getElementById('fText').oninput = () => { shown = 50; render(); };
document.getElementById('more').onclick = () => { shown += 50; render(); };
function download(name, text, type) { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], {type})); a.download = name; a.click(); }
document.getElementById('dlJson').onclick = () => download(`${D.brand}-reviews.json`, JSON.stringify(current.map(({_page, _pi, _date, _ts, _pageReplies, ...r}) => ({page: _page.location.name, ...r})), null, 2), 'application/json');
document.getElementById('dlCsv').onclick = () => {
  const head = ['Държава', 'Страница', 'Автор', 'Препоръчва', 'Дата', 'Текст', 'Етикети', 'Реакции', 'Снимки', 'Коментари', 'Отговор от страницата', 'Тип отговор', 'Линк'];
  const q = v => '"' + String(v ?? '').replace(/"/g, '""') + '"';
  const rows = current.map(r => [r._country, r._page.location.name, r.reviewer, r.recommends === true ? 'да' : r.recommends === false ? 'не' : '', r.date_full || r.date_text,
    r.text, (r.tags || []).join('; '), r.reactions, r.photos, r.comment_count, (r._pageReplies[0] || {}).text, (r._pageReplies[0] || {}).reply_type, r.permalink].map(q).join(','));
  download(`${D.brand}-reviews.csv`, '﻿' + [head.map(q).join(','), ...rows].join('\n'), 'text/csv');
};
renderCountries();
renderAll();
render();
</script>
</body>
</html>
"""


def write_html(data: dict, path: Path) -> None:
    """data = the report.json content (BrandReport.model_dump + stats)."""
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = f"{data.get('brand', '')} — Facebook ревюта"
    html = TEMPLATE.replace("__TITLE__", title.replace("<", "&lt;")).replace("__DATA__", blob)
    path.write_text(html, encoding="utf-8")
