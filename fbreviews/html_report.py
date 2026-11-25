"""report.html / index.html — self-contained, interactive views of report.json (offline, no external libraries).

* report.html (one run): everything in report.json — brand Pages, every location, every review with its text,
  verdict, exact date, tags, reactions, photos, comment count and the replies (generated / manual), plus statistics
  and charts computed in the browser from the same data.
* output/index.html (dashboard): the same view for every brand extracted so far, one tab per brand (its latest run
  with data), plus an overview tab comparing the brands. Rebuilt after every run (and every interim round).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

TEMPLATE = r"""<!doctype html>
<html lang="bg">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#f4f5f8;--card:#fff;--ink:#14161b;--muted:#5f6675;--line:#e4e7ec;--accent:#2f6fde;--accent2:#7b5cff;--pos:#1f9d55;--neg:#d64545;
--warn:#c98a00;--chip:#eef1f6;--shadow:0 1px 2px rgba(16,24,40,.05),0 4px 16px rgba(16,24,40,.05);--top:rgba(255,255,255,.86)}
@media (prefers-color-scheme:dark){:root{--bg:#0d0f13;--card:#161920;--ink:#e9ebef;--muted:#9aa3b2;--line:#272c36;
--accent:#6aa0ff;--accent2:#a48bff;--pos:#3fc47c;--neg:#ff6b6b;--warn:#e0a92e;--chip:#20252f;--shadow:none;--top:rgba(13,15,19,.86)}}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:120px}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1240px;margin:0 auto;padding:0 16px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
/* top bar + tabs */
.top{position:sticky;top:0;z-index:20;background:var(--top);backdrop-filter:saturate(1.6) blur(12px);-webkit-backdrop-filter:saturate(1.6) blur(12px);border-bottom:1px solid var(--line)}
.topin{display:flex;align-items:center;gap:14px;min-height:56px}
.logo{display:flex;align-items:center;gap:8px;font-weight:700;white-space:nowrap}
.logo i{width:28px;height:28px;border-radius:8px;background:linear-gradient(135deg,var(--accent),var(--accent2));display:grid;place-items:center;color:#fff;font-style:normal;font-size:13px}
.tabs{display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;padding:10px 0;flex:1}.tabs::-webkit-scrollbar{display:none}
.tab{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:6px 12px;font:inherit;font-size:13px;cursor:pointer;white-space:nowrap;display:flex;gap:6px;align-items:center}
.tab b{font-weight:600}.tab small{color:var(--muted);font-size:11.5px}
.tab.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}.tab.on small{color:inherit;opacity:.7}
.secnav{display:flex;gap:4px;overflow-x:auto;scrollbar-width:none;padding:0 0 8px}.secnav::-webkit-scrollbar{display:none}
.secnav a{color:var(--muted);font-size:12.5px;padding:4px 10px;border-radius:6px;white-space:nowrap}.secnav a:hover{background:var(--chip);color:var(--ink);text-decoration:none}
/* hero */
.hero{margin:22px 0 4px;padding:22px 24px;border-radius:16px;background:linear-gradient(120deg,color-mix(in srgb,var(--accent) 14%,var(--card)),color-mix(in srgb,var(--accent2) 10%,var(--card)));border:1px solid var(--line)}
.hero h1{font-size:28px;margin:0;letter-spacing:-.02em}.hero .sub{color:var(--muted);margin-top:4px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.chip{background:color-mix(in srgb,var(--card) 75%,transparent);border:1px solid var(--line);border-radius:999px;padding:3px 10px;font-size:12.5px;color:var(--muted)}
.chip b{color:var(--ink);font-weight:600}
h2{font-size:18px;margin:34px 0 12px;display:flex;align-items:baseline;gap:10px}h2 small{font-size:12.5px;color:var(--muted);font-weight:400}
h3{font-size:14.5px;margin:0 0 8px}
.grid{display:grid;gap:12px}
.kpis{grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-top:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:var(--shadow)}
.kpi{position:relative;overflow:hidden}.kpi:before{content:"";position:absolute;inset:0 0 auto 0;height:3px;background:var(--k,var(--accent))}
.kpi .v{font-size:24px;font-weight:680;letter-spacing:-.02em}.kpi .l{color:var(--muted);font-size:12.5px}
.two{grid-template-columns:repeat(auto-fit,minmax(min(100%,380px),1fr))}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:9px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
tbody tr:last-child td{border-bottom:0}tbody tr:hover{background:color-mix(in srgb,var(--chip) 55%,transparent)}
th{color:var(--muted);font-weight:600;font-size:12px;cursor:pointer;white-space:nowrap;user-select:none}th:hover{color:var(--ink)}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}.tablewrap{overflow-x:auto;padding:4px 8px}
.bar{height:8px;border-radius:4px;background:var(--line);overflow:hidden;min-width:60px}.bar>i{display:block;height:100%;background:var(--pos);border-radius:4px}
.barrow{display:flex;gap:8px;align-items:center}.barrow .bar{flex:1}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;background:var(--chip);color:var(--muted);margin:2px 4px 2px 0}
.pill.pos{background:color-mix(in srgb,var(--pos) 15%,transparent);color:var(--pos)}.pill.neg{background:color-mix(in srgb,var(--neg) 15%,transparent);color:var(--neg)}
.pill.gen{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}.pill.man{background:color-mix(in srgb,var(--accent) 15%,transparent);color:var(--accent)}
.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:12px}
select,input[type=search]{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:9px;padding:7px 10px;font:inherit;max-width:100%}
input[type=search]{min-width:200px;flex:1}label.ck{display:flex;gap:6px;align-items:center;color:var(--muted)}
button{background:var(--accent);color:#fff;border:0;border-radius:9px;padding:7px 14px;font:inherit;cursor:pointer}button.ghost{background:var(--card);color:var(--accent);border:1px solid var(--line)}
.rev{padding:14px 16px}.rev+.rev{margin-top:10px}.rev .head{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.av{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;color:#fff;font-weight:650;font-size:13px;flex:none}
.rev .who{font-weight:620}.rev .meta{color:var(--muted);font-size:12.5px}.rev .txt{margin:8px 0;white-space:pre-wrap}
.reply{margin:8px 0 0 18px;padding:8px 12px;border-left:3px solid var(--line);background:color-mix(in srgb,var(--chip) 60%,transparent);border-radius:0 10px 10px 0}
.reply.page{border-left-color:var(--accent)}.reply .who{font-weight:600}.small{font-size:12px;color:var(--muted)}
svg text{fill:var(--muted);font-size:11px}.legend{display:flex;gap:12px;color:var(--muted);font-size:12px;margin-top:6px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.brandcard .row{display:flex;flex-wrap:wrap;gap:6px 16px;color:var(--muted);font-size:13px;margin-top:6px}
.empty{color:var(--muted);padding:16px;text-align:center}
.sig{border-left:4px solid var(--warn);padding:10px 14px}.sig.high{border-left-color:var(--neg)}.sig b{display:block}
.tone{display:flex;height:14px;border-radius:7px;overflow:hidden;background:var(--line);margin:8px 0}.tone i{display:block;height:100%}
.summ{color:var(--muted);font-size:12.5px;font-style:italic;margin-top:4px}
#loctable td:first-child{min-width:230px}
/* overview */
.ov{grid-template-columns:repeat(auto-fill,minmax(min(100%,280px),1fr))}
.ovc{cursor:pointer;transition:transform .12s,box-shadow .12s}.ovc:hover{transform:translateY(-2px);box-shadow:0 6px 24px rgba(16,24,40,.10)}
.ovc h3{font-size:17px;margin:0}.ovc .d{color:var(--muted);font-size:12px}
.ovc .nums{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0}.ovc .nums b{display:block;font-size:19px}.ovc .nums span{color:var(--muted);font-size:11.5px}
.ring{--p:0;width:54px;height:54px;border-radius:50%;background:conic-gradient(var(--pos) calc(var(--p)*1%),var(--line) 0);display:grid;place-items:center;flex:none}
.ring span{width:42px;height:42px;border-radius:50%;background:var(--card);display:grid;place-items:center;font-weight:680;font-size:13px}
.foot{color:var(--muted);font-size:12px;text-align:center;margin:40px 0 30px}
[hidden]{display:none!important}
@media (max-width:640px){.hero{padding:16px}.hero h1{font-size:22px}.logo span{display:none}}
</style>
</head>
<body>
<header class="top">
  <div class="wrap topin"><div class="logo"><i>FB</i><span>Facebook ревюта</span></div><nav class="tabs" id="tabs"></nav></div>
  <div class="wrap secnav" id="secnav">
    <a href="#sec-top">Обзор</a><a href="#sec-countries" id="navCountries">Държави</a><a href="#sec-brand">Бранд</a>
    <a href="#sec-locs">Локации</a><a href="#sec-topics" id="navTopics">Теми</a><a href="#sec-reply" id="navReply">Отговаряне</a><a href="#sec-charts">Графики</a><a href="#sec-reviews">Ревюта</a>
  </div>
</header>

<main class="wrap" id="overview" hidden>
  <div class="hero"><h1>Всички извлечени брандове</h1><div class="sub" id="ovSub"></div></div>
  <h2>Брандове <small>кликнете, за да отворите</small></h2>
  <div class="grid ov" id="ovCards"></div>
  <h2>Сравнение</h2>
  <div class="card tablewrap"><table id="ovTable"></table></div>
</main>

<main class="wrap" id="brandview">
  <div class="hero" id="sec-top"><h1 id="title"></h1><div class="sub" id="subtitle"></div><div class="chips" id="chips"></div></div>
  <div class="grid kpis" id="kpis"></div>

  <section id="sec-countries">
    <h2>По държави <small>кликнете ред, за да филтрирате целия отчет</small></h2>
    <div class="filters"><select id="fCountry"></select></div>
    <div class="card tablewrap"><table id="ctable"></table></div>
  </section>

  <section id="sec-brand"><h2>Страници на бранда</h2><div class="grid two" id="brands"></div></section>

  <section id="sec-locs"><h2>Локации — сравнение <small id="locHint"></small></h2>
    <div class="card tablewrap"><table id="loctable"></table></div></section>

  <section id="sec-topics"><h2>За какво пишат клиентите <small id="topicsHint"></small></h2>
    <div class="grid" id="signals" style="margin-bottom:12px"></div>
    <div class="grid two">
      <div class="card"><h3>Теми в ревютата</h3><div id="chartTopics"></div>
        <div class="legend"><span><i style="background:var(--pos)"></i>хвалят</span><span><i style="background:var(--neg)"></i>оплакват се</span></div></div>
      <div class="card"><h3>Тон на ревютата</h3><div id="toneBox"></div></div>
    </div>
    <div class="card tablewrap" style="margin-top:12px"><table id="topicTable"></table></div>
  </section>

  <section id="sec-reply"><h2>Как отговаря брандът <small>отговори от страницата под ревютата</small></h2>
    <div class="card tablewrap"><table id="replyTable"></table></div>
    <div class="small" style="margin-top:6px">Времето за отговор е приблизително: Facebook показва датата на отговора само като „преди 3 д.“ / „1 сед.“; отговорите с дата „1 г.“ не се мерят.</div>
  </section>

  <section id="sec-charts"><h2>Графики</h2>
  <div class="grid two">
    <div class="card"><h3>% препоръка по локации (Facebook)</h3><div id="chartPct"></div></div>
    <div class="card"><h3>Ревюта по години</h3><div id="chartYears"></div>
      <div class="legend"><span><i style="background:var(--pos)"></i>препоръчват</span><span><i style="background:var(--neg)"></i>не препоръчват</span></div></div>
  </div>
  <div class="grid two" style="margin-top:12px">
    <div class="card"><h3>Най-чести етикети в ревютата</h3><div id="chartTags"></div>
      <div class="legend"><span><i style="background:var(--pos)"></i>в препоръки</span><span><i style="background:var(--neg)"></i>в непрепоръки</span></div></div>
    <div class="card"><h3>Отговори от страниците</h3><div id="replyStats"></div></div>
  </div></section>

  <section id="sec-reviews"><h2>Всички ревюта</h2>
  <div class="filters">
    <select id="fLoc"></select>
    <select id="fVerdict"><option value="">Всички мнения</option><option value="pos">Препоръчва</option><option value="neg">Не препоръчва</option></select>
    <select id="fSort"><option value="new">Най-нови</option><option value="old">Най-стари</option><option value="react">Най-много реакции</option><option value="long">Най-дълги</option></select>
    <select id="fTone"><option value="">Всеки тон</option><option value="positive">Положителни</option><option value="neutral">Неутрални</option><option value="negative">Негативни</option></select>
    <select id="fTopic"><option value="">Всички теми</option></select>
    <label class="ck"><input type="checkbox" id="fReply">с отговор от страницата</label>
    <label class="ck"><input type="checkbox" id="fPhoto">със снимки</label>
    <input type="search" id="fText" placeholder="Търсене в текста, автора, етикетите…">
    <button class="ghost" id="dlCsv">CSV</button><button class="ghost" id="dlJson">JSON</button>
  </div>
  <div class="small" id="count"></div>
  <div id="reviews" style="margin-top:8px"></div>
  <div style="text-align:center;margin-top:14px"><button id="more">Покажи още</button></div>
  </section>
  <div class="foot" id="foot"></div>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
const BOOT = JSON.parse(document.getElementById('data').textContent);
const SETS = BOOT.datasets || [];
const INDEX = BOOT.mode === 'index';
const $ = id => document.getElementById(id);
const STATUS = {ok:'готово',partial:'частично',summary_only:'само публични данни',no_own_page:'без своя страница',no_reviews:'няма ревюта',reviews_disabled:'ревютата са изключени',not_found:'не е намерена',error:'грешка',login_required:'иска вход',blocked:'блокирано',pending:'чака'};
const MONTHS = {'януари':1,'февруари':2,'март':3,'април':4,'май':5,'юни':6,'юли':7,'август':8,'септември':9,'октомври':10,'ноември':11,'декември':12,
  january:1,february:2,march:3,april:4,may:5,june:6,july:7,august:8,september:9,october:10,november:11,december:12};
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = n => n == null ? '—' : Number(n).toLocaleString('bg-BG');
const pct = (a, b) => b ? Math.round(100 * a / b) : null;
const when = s => { const d = new Date(s); return isNaN(d) ? '—' : d.toLocaleString('bg-BG', {day:'numeric', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit'}); };
const AV = ['#2f6fde','#7b5cff','#1f9d55','#d64545','#c98a00','#0e9aa7','#c2417c','#5b6b82'];
const avatar = n => { const s = String(n || '?'); let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const ini = s.split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase() || '?';
  return `<span class="av" style="background:${AV[h % AV.length]}">${esc(ini)}</span>`; };
function parseDate(r, genYear) {
  const s = (r.date_full || r.date_text || '').toLowerCase();
  let m = s.match(/(\d{1,2})\s+([a-zа-я]+)\s*,?\s*(\d{4})?/i);
  if (m && MONTHS[m[2]]) { const t = s.match(/(\d{1,2}):(\d{2})/);
    return new Date(+(m[3] || genYear), MONTHS[m[2]] - 1, +m[1], t ? +t[1] : 0, t ? +t[2] : 0); }
  m = s.match(/([a-z]+)\s+(\d{1,2}),?\s*(\d{4})?/i);
  if (m && MONTHS[m[1]]) return new Date(+(m[3] || genYear), MONTHS[m[1]] - 1, +m[2]);
  return null;
}
function methodBg(m) {
  if (!m || m.startsWith('no page replies')) return 'Няма отговори от страницата за класифициране.';
  return 'Метод: ' + m.replace('template clusters', 'шаблонни клъстери').replace('style heuristics', 'стилови сигнали').replace('model judge', 'модел');
}
const TOPIC_BG = {food:'храна',service:'обслужване',wait:'чакане',price:'цена',cleanliness:'чистота',staff:'персонал',atmosphere:'атмосфера',delivery:'доставка',availability:'наличност',other:'друго'};
const TONE_BG = {positive:'положително', neutral:'неутрално', negative:'негативно'};
const tl = t => TOPIC_BG[t] || t;
// numbers for one dataset (used by the overview and the tabs)
function statsOf(D) {
  const locs = D.locations || [], brands = D.brand_sections || [];
  const revs = [...brands, ...locs].flatMap(p => p.reviews || []);
  const rec = revs.filter(r => r.recommends === true).length, nrec = revs.filter(r => r.recommends === false).length;
  const pcts = locs.map(l => l.recommend_pct).filter(v => v != null);
  const countries = new Set([...brands, ...locs].map(p => p.location && p.location.country).filter(Boolean));
  return {locs: locs.length, brands: brands.length, fb: locs.reduce((a, l) => a + (l.review_total || 0), 0), got: revs.length,
    rec, nrec, share: pct(rec, rec + nrec), avg: pcts.length ? Math.round(pcts.reduce((a, b) => a + b, 0) / pcts.length) : null,
    replied: revs.filter(r => (r.replies || []).some(x => x.is_page_reply)).length, countries: countries.size,
    topNeg: (() => { const c = {}; revs.forEach(r => (r.aspects || []).forEach(a => { if (a.sentiment === 'negative' && a.topic !== 'other') c[a.topic] = (c[a.topic] || 0) + 1; }));
      return Object.entries(c).sort((a, b) => b[1] - a[1]).map(e => e[0])[0]; })(),
    signals: ((D.insights || {}).signals || []).length};
}

/* ---------- one brand ---------- */
function mount(set) {
const D = set.data;
const genYear = new Date(D.generated_at || Date.now()).getFullYear();
const pages = [...(D.brand_sections || []).map(p => ({...p, _brand: true})), ...(D.locations || [])];
const countryOf = p => (p.location && p.location.country) || (p._brand ? 'Глобална' : 'Без държава');
const ALL_REVIEWS = [];
pages.forEach((p, pi) => (p.reviews || []).forEach(r => {
  const d = parseDate(r, genYear);
  ALL_REVIEWS.push({...r, _page: p, _pi: pi, _date: d, _ts: d ? d.getTime() : 0, _country: countryOf(p),
    _pageReplies: (r.replies || []).filter(x => x.is_page_reply)});
}));
const ALL_LOCS = D.locations || [];
let selCountry = '', shown = 50, current = [];
const inC = p => !selCountry || countryOf(p) === selCountry;

document.title = `${D.brand} — Facebook ревюта`;
$('title').textContent = D.brand;
const COUNTRIES = [...new Set(pages.map(countryOf))].sort((a, b) => a.localeCompare(b, 'bg'));
$('subtitle').textContent = 'Facebook ревюта и отговори — по локации, с отделна секция за страниците на бранда';
const st = statsOf(D);
$('chips').innerHTML = [
  `Обновено <b>${when(D.generated_at)}</b>`, `<b>${COUNTRIES.length}</b> ${COUNTRIES.length === 1 ? 'държава' : 'държави'}`,
  `<b>${st.locs}</b> локации`, `<b>${st.brands}</b> стр. на бранда`, `модел <b>${esc(D.model || '—')}</b>`,
  ...(set.run ? [`папка <b>${esc(set.run)}</b>`] : []),
  ...(set.runs && set.runs.length > 1 ? [`${set.runs.length} пускания · показано последното с данни`] : []),
].map(c => `<span class="chip">${c}</span>`).join('');
$('sec-countries').hidden = $('navCountries').hidden = COUNTRIES.length < 2;
$('foot').innerHTML = `Данни: ${esc(set.run ? set.run + '/report.json' : 'report.json')} · ${methodBg(D.classification_method || '')}` +
  (set.runs && set.runs.length > 1 ? '<br>Други пускания: ' + set.runs.map(r => r.href ? `<a href="${esc(r.href)}">${esc(r.run)}</a> (${r.reviews})` : `${esc(r.run)} (${r.reviews})`).join(' · ') : '');

// countries table + filter
const fCountry = $('fCountry');
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
  $('ctable').innerHTML = `<thead><tr><th>Държава</th><th class="n">Стр. на бранда</th><th class="n">Локации</th>
    <th class="n">Ревюта във FB</th><th class="n">Извлечени</th><th>Препоръчват (извлечени)</th><th class="n">Средно % (FB)</th><th class="n">С отговор от стр.</th></tr></thead>
    <tbody>${rows.map(r => `<tr data-c="${esc(r.c)}" style="cursor:pointer${selCountry === r.c ? ';background:var(--chip)' : ''}"><td><b>${esc(r.c)}</b></td>
    <td class="n">${r.brands}</td><td class="n">${r.locs}</td><td class="n">${fmt(r.fb)}</td><td class="n">${fmt(r.got)}</td>
    <td>${r.share == null ? '—' : `<div class="barrow"><div class="bar"><i style="width:${r.share}%"></i></div>${r.share}%</div>`}</td>
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
const share = pct(rec, rec + nrec);
const kpis = [
  ['Локации', locs.length, '--accent'], ['Ревюта във Facebook', fmt(fbTotal), '--accent2'], ['Извлечени ревюта', fmt(reviews.length), '--accent2'],
  ['Препоръчват', share == null ? '—' : share + '%', '--pos'], ['Не препоръчват', fmt(nrec), '--neg'],
  ['С отговор от страницата', withPage ? `${withPage} (${pct(withPage, reviews.length)}%)` : '0', '--accent'],
  ['Генерирани / ръчни', `${gen} / ${man}`, '--warn'], ['Снимки', fmt(reviews.reduce((a, r) => a + (r.photos || 0), 0)), '--muted'],
  ['Реакции', fmt(reviews.reduce((a, r) => a + (r.reactions || 0), 0)), '--muted']];
$('kpis').innerHTML = kpis.map(([l, v, k]) => `<div class="card kpi" style="--k:var(${k})"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');

// brand pages
$('brands').innerHTML = (D.brand_sections || []).filter(b => inC({...b, _brand: true})).map(b => { const L = b.location || {};
  return `<div class="card brandcard"><div style="display:flex;gap:12px;align-items:center">
  ${b.recommend_pct != null ? `<div class="ring" style="--p:${b.recommend_pct}"><span>${b.recommend_pct}%</span></div>` : ''}
  <div><h3 style="margin:0"><a href="${esc(L.url)}" target="_blank">${esc(L.name)}</a></h3><span class="pill">${esc(countryOf({...b, _brand: true}))}</span><span class="pill">${STATUS[b.status] || esc(b.status)}</span></div></div>
  <div class="row">${[L.category, L.price, L.followers && L.followers + ' последователи', L.website, L.phone, L.email, L.address].filter(Boolean).map(esc).join(' · ')}</div>
  <div class="row"><span>${b.recommend_pct != null ? `<b>${b.recommend_pct}%</b> препоръка` : 'без препоръки на страницата'}</span>
  <span>${b.review_total != null ? fmt(b.review_total) + ' ревюта във FB' : ''}</span><span>${(b.reviews||[]).length ? (b.reviews.length + ' извлечени') : ''}</span></div></div>`; }).join('')
  || '<div class="card empty">Не е намерена официална страница на бранда.</div>';

// locations table
$('locHint').textContent = 'кликнете заглавие, за да сортирате';
const cols = [
  ['Локация', l => l.location.name, (l, v) => `<a href="${esc(l.location.url)}" target="_blank"><b>${esc(v)}</b></a><div class="small">${esc([l.location.category, l.location.address].filter(Boolean).join(' · '))}</div>`],
  ['Държава', l => countryOf(l), (l, v) => esc(v)],
  ['Град', l => l.location.city || '', (l, v) => esc(v) || '—'],
  ['Статус', l => STATUS[l.status] || l.status, (l, v) => `<span class="pill">${esc(v)}</span>`],
  ['% препоръка', l => l.recommend_pct ?? -1, (l, v) => v < 0 ? '—' : `<div class="barrow"><div class="bar"><i style="width:${v}%"></i></div>${v}%</div>`],
  ['Ревюта във FB', l => l.review_total ?? -1, (l, v) => v < 0 ? '—' : fmt(v), 'n'],
  ['Извлечени', l => (l.reviews || []).length, (l, v) => fmt(v), 'n'],
  ['Препоръчват', l => (l.reviews || []).filter(r => r.recommends === true).length, (l, v) => fmt(v), 'n'],
  ['Не препоръчват', l => (l.reviews || []).filter(r => r.recommends === false).length, (l, v) => fmt(v), 'n'],
  ['С отговор от стр.', l => (l.reviews || []).filter(r => (r.replies || []).some(x => x.is_page_reply)).length, (l, v) => fmt(v), 'n'],
  ['Последно ревю', l => Math.max(0, ...(l.reviews || []).map(r => (parseDate(r, genYear) || {getTime: () => 0}).getTime())),
    (l, v) => v ? new Date(v).toLocaleDateString('bg-BG') : '—', 'n'],
  ['Последователи', l => l.location.followers || '', (l, v) => esc(v) || '—', 'n'],
];
let sortCol = 4, sortDir = -1;
function renderTable() {
  const rows = [...locs].sort((a, b) => { const x = cols[sortCol][1](a), y = cols[sortCol][1](b);
    return (x > y ? 1 : x < y ? -1 : 0) * sortDir; });
  $('loctable').innerHTML = `<thead><tr>${cols.map((c, i) => `<th class="${c[3] || ''}" data-i="${i}">${c[0]}${i === sortCol ? (sortDir > 0 ? ' ▲' : ' ▼') : ''}</th>`).join('')}</tr></thead>
  <tbody>${rows.map(l => `<tr>${cols.map(c => `<td class="${c[3] || ''}">${c[2](l, c[1](l))}</td>`).join('')}</tr>`).join('') || `<tr><td colspan="${cols.length}" class="empty">Няма локации</td></tr>`}</tbody>`;
  document.querySelectorAll('#loctable th').forEach(th => th.onclick = () => { const i = +th.dataset.i; sortDir = i === sortCol ? -sortDir : -1; sortCol = i; renderTable(); });
}
renderTable();

// charts (plain SVG)
function hbars(el, items, max, colorA, colorB) {
  if (!items.length) { el.innerHTML = '<div class="empty">Няма данни</div>'; return; }
  const W = 520, rowH = 24, lab = 190, H = items.length * rowH + 4;
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img">${items.map((it, i) => {
    const y = i * rowH + 2, wa = (W - lab - 90) * (it.a / max), wb = (W - lab - 90) * ((it.b || 0) / max);
    return `<text x="0" y="${y + 15}">${esc(it.label.length > 30 ? it.label.slice(0, 29) + '…' : it.label)}</text>
      <rect x="${lab}" y="${y + 5}" width="${W - lab - 90}" height="12" rx="4" fill="var(--chip)"/>
      <rect x="${lab}" y="${y + 5}" width="${Math.max(wa, 0)}" height="12" rx="4" fill="var(${colorA})"/>
      ${it.b ? `<rect x="${lab + wa}" y="${y + 5}" width="${wb}" height="12" rx="4" fill="var(${colorB})"/>` : ''}
      <text x="${W - 84}" y="${y + 15}">${esc(it.text ?? (it.a + (it.b || 0)))}</text>`; }).join('')}</svg>`;
}
hbars($('chartPct'),
  locs.filter(l => l.recommend_pct != null).sort((a, b) => b.recommend_pct - a.recommend_pct)
    .map(l => ({label: l.location.name.replace(D.brand, '').replace(/^[\s\-–(]+|[)\s]+$/g, '') || l.location.name, a: l.recommend_pct, text: l.recommend_pct + '% · ' + fmt(l.review_total)})),
  100, '--pos');
(function years() {
  const by = {}; reviews.forEach(r => { if (!r._date) return; const y = r._date.getFullYear();
    by[y] = by[y] || {p: 0, n: 0}; if (r.recommends === false) by[y].n++; else by[y].p++; });
  const ys = Object.keys(by).sort();
  const el = $('chartYears');
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
  hbars($('chartTags'), items, Math.max(1, ...items.map(i => i.a + i.b)), '--pos', '--neg');
})();
(function replies() {
  const unc = pageReplies.filter(x => x.reply_type === 'uncertain').length;
  const hidden = reviews.filter(r => r.replies_unavailable).length, cc = reviews.reduce((a, r) => a + (r.comment_count || 0), 0);
  const tpl = {}; pageReplies.forEach(x => { const k = (x.text || '').slice(0, 70); if (k) tpl[k] = (tpl[k] || 0) + 1; });
  const top = Object.entries(tpl).filter(([, n]) => n > 1).sort((a, b) => b[1] - a[1]).slice(0, 3);
  $('replyStats').innerHTML = `
    <div class="grid" style="grid-template-columns:repeat(3,1fr)">
      <div class="kpi"><div class="v">${pageReplies.length}</div><div class="l">отговора от страниците</div></div>
      <div class="kpi"><div class="v">${withPage ? pct(withPage, reviews.length) + '%' : '0%'}</div><div class="l">от ревютата имат отговор</div></div>
      <div class="kpi"><div class="v">${fmt(cc)}</div><div class="l">коментара (брой по FB)</div></div></div>
    <p><span class="pill gen">генерирани ${gen}</span><span class="pill man">ръчни ${man}</span><span class="pill">неясни ${unc}</span>
    ${hidden ? `<span class="pill">${hidden} ревюта с коментари, които Facebook не показва</span>` : ''}</p>
    ${top.length ? '<div class="small">Повтарящи се отговори (шаблони):</div>' + top.map(([k, n]) => `<div class="reply page"><span class="pill gen">${n}×</span>${esc(k)}…</div>`).join('') : ''}
    <div class="small" style="margin-top:8px">${esc(methodBg(D.classification_method || ''))}</div>`;
})();
// topics, tone, signals, replying (insights.py)
(function topics() {
  const I = D.insights || {}, has = reviews.some(r => r.sentiment);
  $('sec-topics').hidden = $('navTopics').hidden = !has;
  $('sec-reply').hidden = $('navReply').hidden = !(I.pages || []).length;
  if (!has) return;
  $('topicsHint').textContent = I.method ? 'анализ: ' + I.method.replace('model', 'модел').replace(' for ', ' за ').replace(' of ', ' от ').replace(' reviews, keywords for the rest', ' ревюта, ключови думи за останалите').replace('keywords and Facebook tags (no model)', 'ключови думи и етикетите на Facebook (без модел)') : '';
  const byUrl = {}; pages.forEach(p => byUrl[p.location.url] = p);
  const pageOk = u => !byUrl[u] || inC(byUrl[u]);
  $('signals').innerHTML = (I.signals || []).filter(s => { const p = pages.find(x => x.location.name === s.page); return !p || inC(p); })
    .map(s => `<div class="card sig ${s.level}"><b>${esc(s.page)}</b>${esc(s.text)}</div>`).join('');
  const t = {}; reviews.forEach(r => (r.aspects || []).forEach(a => { t[a.topic] = t[a.topic] || {p: 0, n: 0};
    if (a.sentiment === 'positive') t[a.topic].p++; else if (a.sentiment === 'negative') t[a.topic].n++; }));
  const items = Object.entries(t).map(([k, v]) => ({label: tl(k), a: v.p, b: v.n, text: `+${v.p} / −${v.n}`})).filter(i => i.a + i.b).sort((x, y) => (y.a + y.b) - (x.a + x.b));
  hbars($('chartTopics'), items, Math.max(1, ...items.map(i => i.a + i.b)), '--pos', '--neg');
  const n = {positive: 0, neutral: 0, negative: 0}; reviews.forEach(r => { if (n[r.sentiment] != null) n[r.sentiment]++; });
  const tot = n.positive + n.neutral + n.negative || 1;
  $('toneBox').innerHTML = `<div class="tone"><i style="width:${100 * n.positive / tot}%;background:var(--pos)"></i><i style="width:${100 * n.neutral / tot}%;background:var(--muted)"></i><i style="width:${100 * n.negative / tot}%;background:var(--neg)"></i></div>
    <div class="grid" style="grid-template-columns:repeat(3,1fr)">${['positive', 'neutral', 'negative'].map(k => `<div class="kpi"><div class="v">${pct(n[k], tot)}%</div><div class="l">${TONE_BG[k]} · ${n[k]}</div></div>`).join('')}</div>
    <p class="small">Тонът е от текста на ревюто (не само от „препоръчва / не препоръчва“): ревю с „препоръчва“ може да е неутрално, ако текстът има оплаквания.</p>`;
  const P = (I.pages || []).filter(p => pageOk(p.url));
  const pills = (ks, cls, d) => ks.length ? ks.map(k => `<span class="pill ${cls}">${esc(tl(k))} ${d[k]}</span>`).join('') : '—';
  $('topicTable').innerHTML = `<thead><tr><th>Страница</th><th class="n">Ревюта</th><th>Тон</th><th>Хвалят</th><th>Оплакват се от</th></tr></thead><tbody>${P.map(p => {
    const T = p.positive + p.neutral + p.negative || 1;
    return `<tr><td><b>${p.is_brand ? '[Бранд] ' : ''}${esc(p.name)}</b></td><td class="n">${p.reviews}</td>
    <td style="min-width:140px"><div class="tone" style="margin:4px 0"><i style="width:${100 * p.positive / T}%;background:var(--pos)"></i><i style="width:${100 * p.neutral / T}%;background:var(--muted)"></i><i style="width:${100 * p.negative / T}%;background:var(--neg)"></i></div><span class="small">${pct(p.negative, T)}% негативни</span></td>
    <td>${pills(p.top_positive, 'pos', p.topics_positive)}</td><td>${pills(p.top_negative, 'neg', p.topics_negative)}</td></tr>`; }).join('')}</tbody>`;
  const v = (x, s = '%') => x == null ? '—' : x + s;
  $('replyTable').innerHTML = `<thead><tr><th>Страница</th><th class="n">Ревюта</th><th>С отговор</th><th>Отговорени негативни</th><th class="n">Негативни без отговор</th><th class="n">Шаблонни отговори</th><th class="n">Отговор след (дни)</th></tr></thead><tbody>${P.map(p => `<tr>
    <td><b>${p.is_brand ? '[Бранд] ' : ''}${esc(p.name)}</b></td><td class="n">${p.reviews}</td>
    <td>${p.reply_rate == null ? '—' : `<div class="barrow"><div class="bar"><i style="width:${p.reply_rate}%;background:var(--accent)"></i></div>${p.reply_rate}%</div>`}</td>
    <td>${p.negative_reply_rate == null ? '—' : `<div class="barrow"><div class="bar"><i style="width:${p.negative_reply_rate}%;background:var(--accent)"></i></div>${p.negative_reply_rate}%</div>`}</td>
    <td class="n">${p.negative_unanswered ? `<span class="pill neg">${p.negative_unanswered}</span>` : '0'}</td><td class="n">${v(p.template_share)}</td>
    <td class="n">${p.reply_delay_days == null ? '—' : `~${p.reply_delay_days}<span class="small"> (${p.reply_delay_measured})</span>`}</td></tr>`).join('')}</tbody>`;
})();
}  // end renderAll

// reviews explorer
const fLoc = $('fLoc');
fLoc.innerHTML = '<option value="">Всички страници</option>' + pages.map((p, i) => `<option value="${i}">${p._brand ? '[Бранд] ' : ''}${esc(p.location.name)} · ${esc(countryOf(p))} (${(p.reviews || []).length})</option>`).join('');
const usedTopics = [...new Set(ALL_REVIEWS.flatMap(r => (r.aspects || []).map(a => a.topic)))];
$('fTopic').innerHTML = '<option value="">Всички теми</option>' + usedTopics.map(t => `<option value="${t}:negative">оплакване: ${esc(tl(t))}</option><option value="${t}:positive">похвала: ${esc(tl(t))}</option><option value="${t}:">${esc(tl(t))} (всички)</option>`).join('');
$('fTopic').hidden = !usedTopics.length; $('fTone').hidden = !ALL_REVIEWS.some(r => r.sentiment);
['fVerdict', 'fSort', 'fText', 'fTone', 'fTopic'].forEach(id => $(id).value = $(id).tagName === 'SELECT' ? $(id).options[0].value : '');
$('fReply').checked = $('fPhoto').checked = false;
function filtered() {
  const loc = fLoc.value, v = $('fVerdict').value, q = $('fText').value.trim().toLowerCase();
  const onlyReply = $('fReply').checked, onlyPhoto = $('fPhoto').checked, s = $('fSort').value;
  const tone = $('fTone').value, [tt, tp] = ($('fTopic').value || ':').split(':');
  const out = ALL_REVIEWS.filter(r => (!selCountry || r._country === selCountry) && (loc === '' || r._pi === +loc) && (!v || (v === 'pos' ? r.recommends === true : r.recommends === false))
    && (!onlyReply || r._pageReplies.length) && (!onlyPhoto || r.photos > 0) && (!tone || r.sentiment === tone)
    && (!tt || (r.aspects || []).some(a => a.topic === tt && (!tp || a.sentiment === tp)))
    && (!q || [r.text, r.reviewer, r.summary, (r.tags || []).join(' '), (r.replies || []).map(x => x.text).join(' ')].join(' ').toLowerCase().includes(q)));
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
  $('count').textContent = `${current.length} ревюта${current.length > shown ? ` · показани ${shown}` : ''}`;
  $('reviews').innerHTML = current.slice(0, shown).map(r => `
    <div class="card rev"><div class="head">${avatar(r.reviewer)}
      <div style="flex:1;min-width:0"><span class="who">${esc(r.reviewer || '—')}</span>
      ${r.recommends === true ? '<span class="pill pos">препоръчва</span>' : r.recommends === false ? '<span class="pill neg">не препоръчва</span>' : ''}
      <div class="meta">${esc(r.date_full || r.date_text || '')} · ${r._page._brand ? '[Бранд] ' : ''}${esc(r._page.location.name)} · ${esc(r._country)}
      ${r.permalink ? ` · <a href="${esc(r.permalink)}" target="_blank">виж във Facebook ↗</a>` : ''}</div></div></div>
      ${(r.aspects || []).length || r.sentiment ? `<div style="margin-top:6px">${r.sentiment ? `<span class="pill ${r.sentiment === 'positive' ? 'pos' : r.sentiment === 'negative' ? 'neg' : ''}">тон: ${TONE_BG[r.sentiment]}</span>` : ''}${(r.aspects || []).map(a => `<span class="pill ${a.sentiment === 'positive' ? 'pos' : a.sentiment === 'negative' ? 'neg' : ''}">${esc(tl(a.topic))} ${a.sentiment === 'positive' ? '+' : a.sentiment === 'negative' ? '−' : ''}</span>`).join('')}</div>` : ''}
      ${r.summary ? `<div class="summ">„${esc(r.summary)}“</div>` : ''}
      ${(r.tags || []).length ? `<div style="margin-top:6px">${r.tags.map(t => `<span class="pill">${esc(t)}</span>`).join('')}</div>` : ''}
      <div class="txt">${esc(r.text || '(без текст)')}</div>
      <div class="small">${r.reactions ? '👍 ' + r.reactions + ' · ' : ''}${r.photos ? '📷 ' + r.photos + ' · ' : ''}${r.comment_count ? '💬 ' + r.comment_count + ' коментара' : ''}${r.replies_unavailable ? ' (Facebook не показва текста им)' : ''}</div>
      ${(r.replies || []).map(replyHtml).join('')}
    </div>`).join('') || '<div class="card empty">Няма ревюта по тези филтри.</div>';
  $('more').style.display = current.length > shown ? '' : 'none';
}
['fLoc', 'fVerdict', 'fSort', 'fReply', 'fPhoto', 'fTone', 'fTopic'].forEach(id => $(id).onchange = () => { shown = 50; render(); });
$('fText').oninput = () => { shown = 50; render(); };
$('more').onclick = () => { shown += 50; render(); };
function download(name, text, type) { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], {type})); a.download = name; a.click(); }
$('dlJson').onclick = () => download(`${D.brand}-reviews.json`, JSON.stringify(current.map(({_page, _pi, _date, _ts, _pageReplies, ...r}) => ({page: _page.location.name, ...r})), null, 2), 'application/json');
$('dlCsv').onclick = () => {
  const head = ['Държава', 'Страница', 'Автор', 'Препоръчва', 'Дата', 'Текст', 'Тон', 'Теми', 'Накратко', 'Етикети', 'Реакции', 'Снимки', 'Коментари', 'Отговор от страницата', 'Тип отговор', 'Линк'];
  const q = v => '"' + String(v ?? '').replace(/"/g, '""') + '"';
  const rows = current.map(r => [r._country, r._page.location.name, r.reviewer, r.recommends === true ? 'да' : r.recommends === false ? 'не' : '', r.date_full || r.date_text,
    r.text, TONE_BG[r.sentiment] || '', (r.aspects || []).map(a => tl(a.topic) + (a.sentiment === 'positive' ? ' +' : a.sentiment === 'negative' ? ' −' : '')).join('; '), r.summary || '', (r.tags || []).join('; '), r.reactions, r.photos, r.comment_count, (r._pageReplies[0] || {}).text, (r._pageReplies[0] || {}).reply_type, r.permalink].map(q).join(','));
  download(`${D.brand}-reviews.csv`, '﻿' + [head.map(q).join(','), ...rows].join('\n'), 'text/csv');
};
renderCountries();
renderAll();
render();
}  // end mount

/* ---------- overview of all brands (dashboard) ---------- */
function overview() {
  document.title = 'Facebook ревюта — всички брандове';
  const rows = SETS.map((s, i) => ({s, i, st: statsOf(s.data)}));
  $('ovSub').textContent = `${SETS.length} бранда · ${fmt(rows.reduce((a, r) => a + r.st.locs, 0))} локации · ${fmt(rows.reduce((a, r) => a + r.st.got, 0))} извлечени ревюта · последното пускане на всеки бранд с данни`;
  $('ovCards').innerHTML = rows.map(({s, st}) => `<div class="card ovc" data-k="${esc(s.key)}">
    <div style="display:flex;gap:12px;align-items:center;justify-content:space-between">
      <div style="min-width:0"><h3>${esc(s.data.brand)}</h3><div class="d">${when(s.data.generated_at)}${s.runs && s.runs.length > 1 ? ' · ' + s.runs.length + ' пускания' : ''}</div></div>
      ${st.share != null ? `<div class="ring" style="--p:${st.share}" title="препоръчват (извлечени)"><span>${st.share}%</span></div>` : ''}</div>
    <div class="nums"><div><b>${st.locs}</b><span>локации</span></div><div><b>${fmt(st.fb)}</b><span>ревюта във FB</span></div><div><b>${fmt(st.got)}</b><span>извлечени</span></div></div>
    <div><span class="pill">${st.brands} стр. на бранда</span>${st.countries ? `<span class="pill">${st.countries} ${st.countries === 1 ? 'държава' : 'държави'}</span>` : ''}
      <span class="pill man">${st.got ? pct(st.replied, st.got) : 0}% с отговор</span>${st.nrec ? `<span class="pill neg">${st.nrec} не препоръчват</span>` : ''}
      ${st.topNeg ? `<span class="pill neg">оплакват се най-вече от: ${esc(tl(st.topNeg))}</span>` : ''}${st.signals ? `<span class="pill gen">⚠ ${st.signals} сигнала</span>` : ''}</div></div>`).join('')
    || '<div class="card empty">Още няма извлечени брандове.</div>';
  document.querySelectorAll('.ovc').forEach(c => c.onclick = () => go(c.dataset.k));
  $('ovTable').innerHTML = `<thead><tr><th>Бранд</th><th>Обновено</th><th class="n">Държави</th><th class="n">Стр. на бранда</th><th class="n">Локации</th><th class="n">Ревюта във FB</th>
    <th class="n">Извлечени</th><th>Препоръчват (извлечени)</th><th class="n">Средно % (FB)</th><th class="n">С отговор от стр.</th></tr></thead><tbody>${rows.map(({s, st}) => `<tr>
    <td><a href="#${encodeURIComponent(s.key)}"><b>${esc(s.data.brand)}</b></a></td><td>${when(s.data.generated_at)}</td><td class="n">${st.countries}</td><td class="n">${st.brands}</td>
    <td class="n">${st.locs}</td><td class="n">${fmt(st.fb)}</td><td class="n">${fmt(st.got)}</td>
    <td>${st.share == null ? '—' : `<div class="barrow"><div class="bar"><i style="width:${st.share}%"></i></div>${st.share}%</div>`}</td>
    <td class="n">${st.avg == null ? '—' : st.avg + '%'}</td><td class="n">${st.replied}</td></tr>`).join('')}</tbody>`;
}

/* ---------- tabs ---------- */
function tabsHtml(active) {
  if (!INDEX && SETS.length < 2) return '';
  const t = (k, label, sub) => `<button class="tab${k === active ? ' on' : ''}" data-k="${esc(k)}"><b>${esc(label)}</b>${sub ? `<small>${esc(sub)}</small>` : ''}</button>`;
  return (INDEX ? t('', 'Всички', SETS.length + '') : '') + SETS.map(s => t(s.key, s.data.brand, fmt(statsOf(s.data).got))).join('');
}
function go(key) { location.hash = key ? encodeURIComponent(key) : 'all'; }
function show() {
  const h = decodeURIComponent(location.hash.slice(1));
  const set = SETS.find(s => s.key === h) || (INDEX ? null : SETS[0]);
  $('tabs').innerHTML = tabsHtml(set ? set.key : '');
  document.querySelectorAll('.tab').forEach(b => b.onclick = () => go(b.dataset.k));
  const on = document.querySelector('.tab.on'); if (on) on.scrollIntoView({block: 'nearest', inline: 'nearest'});
  $('overview').hidden = !!set; $('brandview').hidden = !set; $('secnav').hidden = !set;
  if (set) mount(set); else overview();
  window.scrollTo(0, 0);
}
document.querySelectorAll('#secnav a').forEach(a => a.onclick = e => { e.preventDefault(); const t = document.getElementById(a.getAttribute('href').slice(1)); if (t) t.scrollIntoView({behavior: 'smooth'}); });
window.addEventListener('hashchange', () => { const h = decodeURIComponent(location.hash.slice(1)); if (!h.startsWith('sec-')) show(); });
show();
</script>
</body>
</html>
"""


def _render(datasets: list[dict], mode: str, title: str) -> str:
    blob = json.dumps({"mode": mode, "datasets": datasets}, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.replace("__TITLE__", title.replace("<", "&lt;")).replace("__DATA__", blob)


def write_html(data: dict, path: Path) -> None:
    """report.html for one run. data = the report.json content (BrandReport.model_dump + stats)."""
    html = _render([{"key": "report", "data": data, "run": path.parent.name}], "single",
                   f"{data.get('brand', '')} — Facebook ревюта")
    path.write_text(html, encoding="utf-8")


_RUN = re.compile(r"^(?P<slug>.+)_(?P<ts>\d{8}-\d{6})$")


def _count(data: dict) -> tuple[int, int]:
    pages = (data.get("locations") or []) + (data.get("brand_sections") or [])
    return sum(len(p.get("reviews") or []) for p in pages), len(pages)


def collect_runs(out_dir: Path) -> list[dict]:
    """One dataset per brand: its latest run with reviews (else the latest with any Page), newest brand first."""
    groups: dict[str, list[tuple[str, Path, dict, int, int]]] = {}
    for f in sorted(Path(out_dir).glob("*/report.json")):
        m = _RUN.match(f.parent.name)
        if not m:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # a report being written right now: picked up next time
        reviews, pages = _count(data)
        groups.setdefault(m["slug"], []).append((m["ts"], f.parent, data, reviews, pages))
    sets = []
    for key, runs in groups.items():
        runs.sort(key=lambda r: r[0], reverse=True)
        pick = next((r for r in runs if r[3]), None) or next((r for r in runs if r[4]), None)
        if not pick:
            continue
        sets.append({
            "key": key, "run": pick[1].name, "data": pick[2],
            "runs": [{"run": r[1].name, "reviews": r[3],
                      "href": f"{r[1].name}/report.html" if (r[1] / "report.html").exists() else None}
                     for r in runs if r[3] or r[4]],
        })
    sets.sort(key=lambda s: s["data"].get("generated_at") or "", reverse=True)
    return sets


def write_index(out_dir: Path, path: Path | None = None) -> Path:
    """output/index.html: every brand extracted so far, one tab each, plus an overview tab."""
    path = path or Path(out_dir) / "index.html"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(_render(collect_runs(out_dir), "index", "Facebook ревюта — всички брандове"), encoding="utf-8")
    tmp.replace(path)  # atomic: a browser reloading it never sees half a file
    return path


if __name__ == "__main__":  # python -m fbreviews.html_report [output]
    import sys

    print(write_index(Path(sys.argv[1] if len(sys.argv) > 1 else "output")))
