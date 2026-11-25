// Facebook Page "Reviews" (recommendations) extractor. Evaluated in the page by the browser-use agent:
//   js(open('fb_extract.js').read() + "\n__fbx.scroll(" + json.dumps(opts) + ")")   -> loads more, expands replies
//   js(open('fb_extract.js').read() + "\n__fbx.extract(" + json.dumps(opts) + ")")  -> {reviews, diagnostics}
// opts: {pageName, brand, maxReviews, steps, delayMs}
// Language-agnostic where possible (structure + ARIA), with keyword lists for en/bg and common EU languages.
//
// Repairable: every internal call goes through the returned object F (= window.__fbx), and the keyword patterns /
// selectors live in F.re / F.sel. When Facebook changes its UI, an override script (fb_extract_override.js in the
// agent workspace, loaded right after this file by the skill's helpers) can replace any part, e.g.
//   __fbx.sel.unit = '[data-pagelet^="Review"]';  __fbx.re.POS = /(recommends|препоръчва|new phrase)/i;
//   __fbx.reviewTotal = function () { ... };      __fbx.parsePost = function (P, opts) { ... same output keys ... };
// pageState() (login / checkpoint detection) is always taken from this bundled file, never from an override.
window.__fbx = (function () {
  var F = {};
  var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };

  var EXPAND = new RegExp('^(' + [
    'see more', 'read more', 'вижте повече', 'вижте още', '(… |…)?see \\d+ more', '(… |…)?вижте още \\d+', 'покажи повече', 'показване на още', 'view more comments', 'view previous comments', 'view more replies', 'view all \\d+ replies',
    'view \\d+ (more )?repl(y|ies)', '\\d+ repl(y|ies)', 'view 1 reply', 'more replies',
    'виж още', 'виж още коментари', 'виж предишните коментари', 'виж още отговори', 'виж всички \\d+ отговора',
    'виж \\d+ (още )?отговор(а)?', '\\d+ отговор(а)?', 'виж 1 отговор', 'още отговори',
    'ver más', 'ver \\d+ respuestas?', 'voir plus', 'afficher plus', 'mehr anzeigen', 'vedi altro', 'mostrar mais',
    'ver mais', 'zobacz więcej', 'vezi mai mult', 'δείτε περισσότερα', 'daha fazla gör'
  ].join('|') + ')( …|…|\\.\\.\\.)?$', 'i');

  var COMMENT_LABEL = /^(comment|reply|коментар|отговор|comentario|respuesta|commentaire|réponse|kommentar|antwort|commento|risposta|comentário|resposta|komentarz|odpowiedź|comentariu|răspuns|σχόλιο|απάντηση|yorum|yanıt)/i;
  var NEG = /(doesn['’]t recommend|does not recommend|не препоръчва|no recomienda|ne recommande pas|empfiehlt .{0,40}nicht|non consiglia|não recomenda|nie poleca|nu recomandă|δεν προτείνει|önermiyor)/i;
  var POS = /(recommends|препоръчва|recomienda|recommande|empfiehlt|consiglia|recomenda|poleca|recomandă|προτείνει|öneriyor)/i;
  var UI_NOISE = /^(like|reply|share|харесва(м|не)?|отговор(и)?|сподели|see translation|виж превода|вижте превода|see original|вижте оригинала|rate this translation|оценете превода|author|автор|top fan|топ фен|edited|редактирано|follow|последвай|\d+[smhdwy]|\d+ ?(мин|ч|д|с|г)\.?)$/i;
  var COMMENT_COUNT = /(\d+)\s*(коментар|comment|comentario|commentaire|kommentar|commento|komentarz|comentário|σχόλι|yorum)/i;
  var POST_LINK = /(\/posts\/|story_fbid=|\/permalink\/|\/reviews\/|pfbid|\/activity\/|multi_permalinks)/;

  function txt(el) { return ((el && (el.innerText || el.textContent)) || '').replace(/\s+\n/g, '\n').trim(); }

  function clickExpanders() {
    var n = 0;
    var els = document.querySelectorAll('div[role="button"], span[role="button"], a[role="button"]');
    for (var i = 0; i < els.length; i++) {
      var t = txt(els[i]);
      if (t && t.length < 60 && F.re.EXPAND.test(t)) {
        try { els[i].click(); n++; } catch (e) { /* detached */ }
      }
    }
    return n;
  }

  function isCommentEl(a) { return F.re.COMMENT_LABEL.test((a.getAttribute('aria-label') || '').trim()); }

  // Top-level review containers.
  function posts() {
    var units = Array.prototype.slice.call(document.querySelectorAll(F.sel.unit));
    if (units.length) return units;
    var arts = Array.prototype.slice.call(document.querySelectorAll(F.sel.article));
    return arts.filter(function (a) {
      return !F.isCommentEl(a) && !(a.parentElement && a.parentElement.closest('[role="article"]'));
    });
  }

  function commentSet(P) {
    var set = new Set();
    var arts = P.querySelectorAll('[role="article"]');
    var top = null;
    for (var i = 0; i < arts.length; i++) {
      var a = arts[i];
      if (F.isCommentEl(a)) { set.add(a); continue; }
      if (!top) { top = a; continue; }               // the review's own article inside a feed unit
      if (top.contains(a) || P === a) set.add(a);    // unlabeled nested article = a comment
    }
    return set;
  }

  function ownerComment(n, P, set) {
    var x = n;
    while (x && x !== P) { if (set.has(x)) return x; x = x.parentElement; }
    return null;
  }

  function cleanUrl(href) {
    if (!href) return null;
    try {
      var u = new URL(href, location.href);
      var keep = ['story_fbid', 'id', 'comment_id', 'reply_comment_id', 'fbid'];
      var q = new URLSearchParams();
      keep.forEach(function (k) { if (u.searchParams.get(k)) q.set(k, u.searchParams.get(k)); });
      var s = q.toString();
      return u.origin + u.pathname + (s ? '?' + s : '');
    } catch (e) { return href; }
  }

  function textBlocks(root, exclude) {
    var out = [];
    var seen = new Set();
    var blocks = root.querySelectorAll(F.sel.textBlock);
    for (var i = 0; i < blocks.length; i++) {
      var b = blocks[i];
      if (exclude(b)) continue;
      // keep the outermost dir=auto block only
      var p = b.parentElement && b.parentElement.closest('[dir="auto"]');
      if (p && root.contains(p) && !exclude(p)) continue;
      if (b.closest('h2, h3, h4, a[role="link"]')) continue;
      var t = txt(b);
      if (!t || F.re.UI_NOISE.test(t) || seen.has(t)) continue;
      seen.add(t);
      out.push(t);
    }
    return out;
  }

  var REPLY_LABEL = /^(reply|отговор|respuesta|réponse|antwort|risposta|resposta|odpowiedź|răspuns|απάντηση|yanıt)/i;
  var TIME_TXT = /^(\d+\s*\S{0,10}\.?|(преди|hace|il y a|vor) .+|.+ ago|just now|сега|вчера|yesterday|\d{1,2}\s+\S+(\s+\d{4})?(\s*г\.)?)$/i;

  function labelAuthor(C) {
    var label = (C.getAttribute('aria-label') || '').trim();
    if (!F.re.COMMENT_LABEL.test(label)) return null;
    var rest = label.replace(F.re.COMMENT_LABEL, '').replace(/^\s*(by|от|de|par|von|di|przez|από|tarafından)\s+/i, '');
    return rest.split(/\s+(?=\d|on |about |преди |hace |il y a |vor |vorgestern|yesterday|вчера|на коментар|to \S+.{0,40}comment|replying to )/i)[0].trim() || null;
  }

  function parseComment(C, P, set) {
    var inner = function (n) { return ownerComment(n, P, set) !== C; };
    var author = F.labelAuthor(C), date = null, permalink = null, linkAuthor = null;
    var links = C.querySelectorAll('a[href]');
    for (var i = 0; i < links.length; i++) {
      var a = links[i];
      if (inner(a)) continue;
      var href = a.getAttribute('href') || '';
      var t = txt(a);
      if (!t) continue;
      var isTime = F.re.TIME_TXT.test(t) && t !== author;
      if (/comment_id=/.test(href) && isTime) {
        if (!date) { date = t; permalink = F.cleanUrl(href); }
      } else if (!linkAuthor && t.length < 120 && !isTime && !/hashtag|\/photo/.test(href)) {
        linkAuthor = t;
        if (!permalink && /comment_id=/.test(href)) permalink = F.cleanUrl(href);
      }
    }
    author = author || linkAuthor;
    var texts = F.textBlocks(C, inner).filter(function (t) { return t !== author && !F.re.TIME_TXT.test(t); });
    // Facebook puts a badge ("от автора" / "Author") before the text and "… Вижте повече" after a cut-off preview.
    var joined = texts.join('\n')
      .replace(/^(от автора|автор|author|by the author|top fan|топ фен)\s*/i, '');
    var truncated = /(…|\.\.\.)?\s*(вижте повече|виж още|see more|read more)\s*$/i.test(joined);
    joined = joined.replace(/\s*(…|\.\.\.)?\s*(вижте повече|виж още|see more|read more)\s*$/i, '').trim();
    var depth = 0, x = C;
    while (x && x !== P) { if (set.has(x)) depth++; x = x.parentElement; }
    if (F.re.REPLY_LABEL.test((C.getAttribute('aria-label') || '').trim())) depth = Math.max(depth, 2);
    var full = txt(C);
    return {
      author: author,
      text: joined,
      truncated: truncated,
      date_text: date,
      permalink: permalink,
      depth: Math.max(1, depth),
      author_badge: /(^|\n)(author|автор|autor|auteur|autore)(\n|$)/i.test(full)
    };
  }

  function parsePost(P, opts) {
    var set = F.commentSet(P);
    var own = function (n) { return ownerComment(n, P, set) !== null; }; // true => excluded (inside a comment)
    var header = null;
    var hs = P.querySelectorAll('h2, h3, h4, strong');
    for (var i = 0; i < hs.length; i++) { if (!own(hs[i]) && txt(hs[i])) { header = hs[i]; break; } }
    var headerText = txt(header);
    var reviewer = null;
    if (header) {
      var ha = header.querySelector('a');
      reviewer = txt(ha) || headerText.split(/\s+(recommends|doesn|does not|препоръчва|не препоръчва)/i)[0] || null;
    }
    // The verdict is in the header line ("X recommends Page"); fall back to the first lines of the unit.
    var headScan = (F.re.NEG.test(headerText) || F.re.POS.test(headerText)) ? headerText : txt(P).slice(0, 300);
    var recommends = F.re.NEG.test(headScan) ? false : (F.re.POS.test(headScan) ? true : null);
    var rating = null;
    var rm = headScan.match(/(\d)\s*(star|звезд|estrella|étoile|stern|stell)/i);
    if (rm) rating = parseInt(rm[1], 10);

    var msgEl = P.querySelector(F.sel.message);
    var text = msgEl && !own(msgEl) ? txt(msgEl) : null;
    if (!text) {
      text = F.textBlocks(P, function (n) { return own(n) || (header && header.contains(n)); })
        .filter(function (t) { return t !== headerText && t !== reviewer; })
        .join('\n') || null;
    }

    var date = null, permalink = null;
    var links = P.querySelectorAll('a[href]');
    for (var j = 0; j < links.length; j++) {
      var a = links[j];
      if (own(a)) continue;
      var href = a.getAttribute('href') || '';
      if (F.re.POST_LINK.test(href) && !/comment_id=/.test(href)) {
        var t = txt(a) || a.getAttribute('aria-label');
        if (t && t.length < 60) { date = t; permalink = F.cleanUrl(href); break; }
        if (!permalink) permalink = F.cleanUrl(href);
      }
    }

    var replies = [];
    set.forEach(function (C) {
      var c = F.parseComment(C, P, set);
      if (c.text || c.author) replies.push(c);
    });
    // How many comments Facebook says the review has ("2 коментара", aria-label "..., 2 comments").
    var commentCount = null;
    var cands = P.querySelectorAll('[aria-label], div[role="button"], span[role="button"], span');
    for (var m = 0; m < cands.length && commentCount === null; m++) {
      if (own(cands[m])) continue;
      var lab = (cands[m].getAttribute('aria-label') || '') + ' ' + (cands[m].children.length ? '' : txt(cands[m]));
      var cm = lab.match(F.re.COMMENT_COUNT);
      if (cm) commentCount = parseInt(cm[1], 10);
    }
    if (text) text = text.replace(/\s*(вижте по-малко|виж по-малко|see less|вижте превода|see translation)\s*$/i, '').trim();
    // Extra details shown on the review itself: exact date-time, tags ("Kid friendly · Fast delivery"), reactions,
    // photos. Read from the review's own lines (comments excluded).
    var plines = [];
    (function () {
      var all = txt(P).split('\n').map(function (l) { return l.trim(); }).filter(Boolean);
      for (var q = 0; q < all.length; q++) {
        if (/^(коментиране като|comment as|напишете коментар|write a comment)/i.test(all[q])) break;
        plines.push(all[q]);
      }
    })();
    var dateFull = null;
    for (var d = 0; d < Math.min(plines.length, 8); d++) {
      if (/^(понеделник|вторник|сряда|четвъртък|петък|събота|неделя|monday|tuesday|wednesday|thursday|friday|saturday|sunday),\s/i.test(plines[d])) { dateFull = plines[d]; break; }
    }
    var tags = [];
    if (text) {
      var lastText = text.split('\n').map(function (l) { return l.trim(); }).filter(Boolean).pop();
      var ti = plines.lastIndexOf(lastText);
      if (ti >= 0) {
        for (var u = ti + 1; u < plines.length; u++) {
          var tl = plines[u];
          if (/^\d[\d\s.,]*$/.test(tl)) break;                 // counters start: reactions / comments
          if (tl === '·' || F.re.UI_NOISE.test(tl) || /^(…\s*)?(see \d+ more|вижте още \d+)/i.test(tl)) continue;
          if (tl.length <= 40 && !/[.!?:]$/.test(tl)) tags.push(tl.replace(/^·\s*|\s*·$/g, '')); else break;
        }
      }
    }
    var reactions = null;
    var rl = Array.prototype.slice.call(P.querySelectorAll('[aria-label]')).filter(function (e) { return !own(e); });
    for (var w = 0; w < rl.length && reactions === null; w++) {
      var lab2 = rl[w].getAttribute('aria-label') || '';
      var rm2 = lab2.match(/^(харесване|like|всички реакции|all reactions|реакции|reactions)\s*:\s*(\d[\d\s.,]*)/i);
      if (rm2) reactions = parseInt(rm2[2].replace(/[^\d]/g, ''), 10);
      else if (/^(реагирайте с харесване|react with like|like)/i.test(lab2) && /^\d+$/.test(txt(rl[w]))) reactions = parseInt(txt(rl[w]), 10);
    }
    var photos = Array.prototype.filter.call(P.querySelectorAll('img'), function (im) {
      return !own(im) && (im.naturalHeight || im.height || 0) >= 120 && !/emoji|rsrc\.php/.test(im.src || '');
    }).length;
    return {
      date_full: dateFull,
      tags: tags,
      reactions: reactions,
      photos: photos,
      comment_count: commentCount,
      reviewer: reviewer,
      recommends: recommends,
      rating: rating,
      text: text,
      date_text: date,
      permalink: permalink,
      header: headerText.slice(0, 200),
      replies: replies
    };
  }

  // Facebook virtualizes the list: reviews far above the viewport are emptied again. Parse while scrolling and keep
  // every review seen in window.__fbxAcc (keyed by permalink), so nothing is lost.
  function keyOf(r) { return r.permalink || ((r.reviewer || '') + '|' + (r.text || '').slice(0, 80)); }
  function accumulate(opts) {
    var acc = window.__fbxAcc || (window.__fbxAcc = { order: [], map: {} });
    var ps = F.posts();
    for (var i = 0; i < ps.length; i++) {
      if (!txt(ps[i])) continue;
      var r;
      try { r = F.parsePost(ps[i], opts); } catch (e) { continue; }
      if (!r.text && !r.reviewer) continue;
      var k = F.keyOf(r), old = acc.map[k];
      if (!old) { acc.order.push(k); acc.map[k] = r; continue; }
      // keep the richer replies: more of them, or the same ones with longer (expanded) text
      var tl = function (rs) { return (rs || []).reduce(function (n, c) { return n + (c.text || '').length; }, 0); };
      if ((r.replies || []).length > (old.replies || []).length ||
          ((r.replies || []).length === (old.replies || []).length && tl(r.replies) > tl(old.replies))) old.replies = r.replies;
      if (r.text && (!old.text || r.text.length > old.text.length)) old.text = r.text;
      if (old.comment_count == null && r.comment_count != null) old.comment_count = r.comment_count;
      if (old.reactions == null && r.reactions != null) old.reactions = r.reactions;
      if (!old.date_full && r.date_full) old.date_full = r.date_full;
      if ((r.tags || []).length > (old.tags || []).length) old.tags = r.tags;
      if ((r.photos || 0) > (old.photos || 0)) old.photos = r.photos;
    }
    return acc;
  }

  // Loads the list gradually. Facebook can be slow: keep going until the count reaches
  // min(maxReviews, the page's own "(N reviews)" total), or nothing new arrived for a real wait (patience steps AND
  // minWaitMs since the last new review), with a nudge (up a little, then down to the end) before giving up.
  // opts: steps (per call), delayMs, maxReviews, patience (stable steps, default 10), minWaitMs (default 20000).
  async function scroll(opts) {
    opts = opts || {};
    var steps = opts.steps || 12, delay = opts.delayMs || 1500, max = opts.maxReviews || 100000;
    var patience = opts.patience || 10, minWait = opts.minWaitMs == null ? 20000 : opts.minWaitMs;
    var st = window.__fbxState || (window.__fbxState = { last: 0, stable: 0, lastNewAt: Date.now(), nudges: 0 });
    var total = F.reviewTotal();
    var want = total ? Math.min(max, total) : max;
    var expanded = 0, c = 0, reason = null;
    for (var i = 0; i < steps; i++) {
      // Expand "Вижте повече" on what is in view now and read it BEFORE scrolling on: Facebook empties reviews
      // that leave the viewport, so an expansion read one step later is lost.
      if (F.clickExpanders()) { await sleep(1200); F.accumulate(opts); }
      // Facebook fills placeholders only when they enter the viewport: walk down gradually, not straight to the end.
      var ps = F.posts();
      var firstEmpty = ps.filter(function (p) { return !txt(p); })[0];
      var target = firstEmpty || ps[ps.length - 1];
      if (target) target.scrollIntoView({ block: 'center' });
      window.scrollBy(0, Math.round(window.innerHeight * 0.8));
      await sleep(firstEmpty ? delay * 2 : delay);   // a placeholder is still loading: give it time
      var clicked = F.clickExpanders();
      expanded += clicked;
      await sleep(clicked ? 1000 : 300);   // let "Вижте повече" expand before reading
      c = F.accumulate(opts).order.length;
      if (c !== st.last) { st.stable = 0; st.last = c; st.lastNewAt = Date.now(); st.nudges = 0; }
      else st.stable += firstEmpty ? 0.5 : 1;
      if (c >= want) { reason = total && want === total ? 'reached_total' : 'reached_max'; break; }
      // Nothing new for a while: nudge (up a little, then to the very end) to make Facebook load the next batch.
      if (st.stable >= Math.ceil(patience / 2) && st.nudges < 3 && st.stable >= (st.nudges + 1) * Math.ceil(patience / 3)) {
        st.nudges++;
        window.scrollBy(0, -Math.round(window.innerHeight * 1.5));
        await sleep(delay);
        window.scrollTo(0, document.documentElement.scrollHeight);
        await sleep(delay * 2);
        c = F.accumulate(opts).order.length;
        if (c !== st.last) { st.stable = 0; st.last = c; st.lastNewAt = Date.now(); st.nudges = 0; }
      }
      if (st.stable >= patience && Date.now() - st.lastNewAt >= minWait) { reason = 'no_more'; break; }
    }
    c = F.accumulate(opts).order.length;
    if (!reason && c >= want) reason = total && want === total ? 'reached_total' : 'reached_max';
    return { count: c, target: want, review_total: total, expanded: expanded, done: !!reason, reason: reason,
             stable: st.stable, nudges: st.nudges, waited_ms: Date.now() - st.lastNewAt,
             visible: document.visibilityState };
  }

  // Second, slow pass over the already loaded list (no new page loads): Facebook renders the Page's reply previews
  // under reviews with a delay and empties reviews that leave the viewport, so one pass misses some. Walk from the
  // top to the end, expanding "Вижте повече" and reading at every step; the accumulator keeps the richest version.
  async function sweep(opts) {
    // Runs in short chunks (each js() call must return within ~25 s): call it again until `done`. It stops once the
    // last review has been passed — not at the very end of the page, which Facebook keeps extending.
    opts = opts || {};
    var delay = opts.delayMs || 1200, budget = opts.chunkMs || 12000, t0 = Date.now();
    var st = window.__fbxSweep;
    if (!st || opts.restart) {
      st = window.__fbxSweep = { started: Date.now(), hits: 0 };
      window.scrollTo(0, 0);
      await sleep(delay);
    }
    var done = false, reason = null;
    while (Date.now() - t0 < budget) {
      if (F.clickExpanders()) await sleep(delay);
      F.accumulate(opts);
      await sleep(400);                        // previews that render late
      F.accumulate(opts);
      var ps = F.posts(), last = ps[ps.length - 1];
      var pastLast = !last || last.getBoundingClientRect().bottom < window.innerHeight * 0.4;
      var atEnd = window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 80;
      if (pastLast || atEnd) {
        if (++st.hits >= 2) { done = true; reason = pastLast ? 'past_last_review' : 'page_end'; break; }
      } else st.hits = 0;
      if (opts.maxMs && Date.now() - st.started > opts.maxMs) { done = true; reason = 'time_limit'; break; }
      window.scrollBy(0, Math.round(window.innerHeight * 0.6));
      await sleep(delay);
    }
    var acc = F.accumulate(opts);
    var withReplies = acc.order.filter(function (k) { return (acc.map[k].replies || []).length; }).length;
    return { done: done, reason: reason, reviews: acc.order.length, with_replies: withReplies, ms: Date.now() - st.started };
  }

  async function extract(opts) {
    opts = opts || {};
    for (var k = 0; k < 4; k++) { if (!F.clickExpanders()) break; await sleep(1200); }
    var ps = F.posts();
    var acc = F.accumulate(opts);
    var max = opts.maxReviews || acc.order.length;
    var out = acc.order.slice(0, max).map(function (k) { return acc.map[k]; });
    var body = txt(document.body).slice(0, 3000);
    return {
      reviews: out,
      diagnostics: {
        url: location.href.slice(0, 300),
        title: document.title,
        posts_found: ps.length,
        visibility: document.visibilityState,
        reviews_with_unloaded_comments: out.filter(function (r) { return (r.comment_count || 0) > r.replies.length; }).length,
        reviews_extracted: out.length,
        replies_extracted: out.reduce(function (s, r) { return s + r.replies.length; }, 0),
        login_wall: !!document.querySelector('form[action*="login"], input[name="pass"]'),
        checkpoint_or_captcha: /\/checkpoint|captcha/i.test(location.pathname + location.search) ||
          /security check|проверка за сигурност|captcha/i.test(body.slice(0, 600)),
        tab_state: F.tabState(out.length),
        review_total: F.reviewTotal(),
        recommend_pct: F.recommendPct(),
        sample_headers: out.slice(0, 3).map(function (r) { return r.header; }),
        override: F.override,
        override_error: F.overrideError || null
      }
    };
  }

  // On a single review's own page (its permalink), or with the post open in a dialog: the replies of that one post.
  async function extractPost(opts) {
    opts = opts || {};
    for (var k = 0; k < 6; k++) {
      window.scrollTo(0, document.documentElement.scrollHeight);
      await sleep(opts.delayMs || 1200);
      if (!F.clickExpanders() && k > 1) break;
    }
    var root = document.querySelector('[role="dialog"] [role="article"]') ? document.querySelector('[role="dialog"]') : document;
    var arts = Array.prototype.slice.call(root.querySelectorAll('[role="article"]'));
    var top = arts.filter(function (a) { return !F.isCommentEl(a) && !(a.parentElement && a.parentElement.closest('[role="article"]')); })[0];
    var P = top || (arts.length ? arts[0].parentElement : null);
    if (!P) return { review: null, diagnostics: { url: location.href.slice(0, 300), articles: arts.length } };
    // comments may sit beside the post article, not inside it: widen to the common container
    if (top && !top.querySelector('[role="article"]')) {
      var box = top.parentElement;
      while (box && box !== document.body && !Array.prototype.some.call(box.querySelectorAll('[role="article"]'), F.isCommentEl)) box = box.parentElement;
      if (box && box !== document.body) P = box;
    }
    var r = F.parsePost(P, opts);
    return { review: r, diagnostics: { url: location.href.slice(0, 300), articles: arts.length, replies: r.replies.length } };
  }

  // What kind of page this is right now: ok | login_required | blocked | not_found.
  function pageState() {
    var path = location.pathname, body = txt(document.body).slice(0, 4000);
    if (/^\/login/.test(path) || document.querySelector('form#login_form, form[action*="/login"] input[name="pass"]'))
      return { state: 'login_required', url: location.href };
    if (/^\/checkpoint/.test(path) || document.querySelector('iframe[src*="captcha"], iframe[title*="captcha" i]') ||
        /(security check|проверка за сигурност|confirm (that )?you'?re (a )?human|потвърдете, че сте човек|temporarily blocked|временно блокиран|you'?re temporarily blocked)/i.test(body))
      return { state: 'blocked', url: location.href };
    if (/(this content isn'?t available|това съдържание (в момента )?не е налично|page not found|страницата не е намерена|this page isn'?t available|тази страница не е налична|връзката може да е повредена)/i.test(body))
      return { state: 'not_found', url: location.href };
    return { state: 'ok', url: location.href };
  }

  // Search results (/search/pages or /search/places): one entry per result Page.
  function searchResults() {
    var main = document.querySelector('[role="main"]') || document.body;
    var out = [], seen = new Set();
    var links = main.querySelectorAll('a[href*="facebook.com/"], a[href^="/"]');
    for (var i = 0; i < links.length; i++) {
      var a = links[i], name = txt(a).split('\n')[0];
      var href = a.href.split('?')[0].replace(/\/$/, '');
      if (/profile\.php/.test(a.href)) href = a.href.replace(/&.*$/, '');
      if (!name || name.length > 120 || seen.has(href) || /\/(search|groups|events|hashtag|watch)\//.test(href)) continue;
      var box = a, k = 0;
      while (box.parentElement && k < 8) {
        var up = box.parentElement;
        var hrefs = new Set(Array.prototype.map.call(up.querySelectorAll('a[href]'), function (x) { return x.href.split('?')[0]; }));
        if (hrefs.size > 3) break;
        box = up; k++;
      }
      seen.add(href);
      out.push({ name: name, href: href, snippet: txt(box).replace(name, '').trim().slice(0, 400) });
    }
    return out;
  }

  function tabState(diagCount) {
    var body = txt(document.body);
    if (/(все още няма (оценка|отзиви)|no reviews yet|няма отзиви|\(0 (отзива|reviews)\))/i.test(body)) return 'no_reviews';
    if (!/(reviews|отзиви|sk=reviews)/i.test(location.href)) return 'reviews_disabled';
    if (/(\d+% (препоръка|recommend))/i.test(body) && !diagCount) return 'unknown';
    return diagCount ? 'ok' : 'unknown';
  }

  function reviewTotal() {
    var body = txt(document.querySelector('[role="main"]') || document.body);
    var m = body.match(/\((\d[\d\s.,]*)\s*(отзив|review)/i)                       // "80% препоръка (8 отзива)"
         || body.match(/(?:^|\n)\s*(\d[\d\s.,]*)\s*(отзив|отзива|reviews?)\s*(?:\n|$)/i);  // "1 отзив" on its own line
    if (m) return parseInt(m[1].replace(/[^\d]/g, ''), 10);
    if (/(?:^|\n)\s*(няма отзиви|no reviews( yet)?)\s*(?:\n|$)/i.test(body)) return 0;
    return null;
  }

  function recommendPct() {
    var m = txt(document.querySelector('[role="main"]') || document.body)
      .match(/(\d{1,3})\s*%\s*(препоръка|recommend|recomienda|recommandent|empfehlen|consigliano|recomenda|poleca|recomandă)/i);
    return m ? parseInt(m[1], 10) : null;
  }

  F.re = { EXPAND: EXPAND, COMMENT_LABEL: COMMENT_LABEL, NEG: NEG, POS: POS, UI_NOISE: UI_NOISE,
           COMMENT_COUNT: COMMENT_COUNT, POST_LINK: POST_LINK, REPLY_LABEL: REPLY_LABEL, TIME_TXT: TIME_TXT };
  F.sel = {
    unit: '[aria-posinset]',                          // one review in the list (feed unit)
    article: '[role="article"]',                      // fallback review container / comment
    message: '[data-ad-rendering-role="story_message"], [data-ad-preview="message"], [data-ad-comet-preview="message"]',
    textBlock: 'div[dir="auto"], span[dir="auto"]'    // text paragraphs
  };
  F.txt = txt; F.sleep = sleep;
  F.clickExpanders = clickExpanders; F.isCommentEl = isCommentEl; F.posts = posts; F.commentSet = commentSet;
  F.ownerComment = ownerComment; F.cleanUrl = cleanUrl; F.textBlocks = textBlocks; F.labelAuthor = labelAuthor;
  F.parseComment = parseComment; F.parsePost = parsePost; F.keyOf = keyOf; F.accumulate = accumulate;
  // ---- logged-out ("lite") mode -------------------------------------------------------------------------------
  // Public data of a Page as Facebook shows it WITHOUT login: name, category, address, phone, website, followers,
  // "% recommend (N reviews)" and the first visible review. Reads the DOM only (the login / cookie dialogs stay up).
  function pageSummary() {
    // Works for the logged-out view (EN/BG) and the logged-in one; reads the Page's main column, never dialogs
    // (the notifications panel has its own <h1>).
    var main = document.querySelector('[role="main"]') || document.body;
    var dialogLines = new Set();
    Array.prototype.forEach.call(document.querySelectorAll('[role="dialog"]'), function (d) {
      txt(d).split('\n').forEach(function (l) { if (l.trim()) dialogLines.add(l.trim()); });
    });
    var clean = function (t) {
      return t.split('\n').map(function (l) { return l.trim(); })
        .filter(function (l) { return l && !dialogLines.has(l) && !/^[{\[]|"require"/.test(l); });
    };
    // logged-out pages keep the Page header outside [role=main]: use the whole page when main lacks it
    var lines = clean(txt(main));
    if (!lines.some(function (l) { return /%|·|последовател|followers/i.test(l); })) { main = document.body; lines = clean(txt(document.body)); }
    var body = lines.join('\n');
    var LABEL = /^(log in|forgot account\?|влизане|забравен профил\?|въведение|още|more|всички|all|информация|about|последователи|followers|снимки|photos|споменавания|mentions|публикации|posts|видеоклипове|videos|reels|събития|events|следване|follow|търсене|search|обадете се сега|call now|съобщение|message|харесване|like|подробности|details|intro|представяне|връзки|links|препоръчани|featured|филтри|filters)$/i;
    var h1 = Array.prototype.filter.call(document.querySelectorAll('h1'), function (h) { return !h.closest('[role="dialog"]'); })[0];
    var name = h1 ? txt(h1).replace(/\s+/g, ' ').trim() : null;
    if (!name) name = lines.filter(function (l) { return !LABEL.test(l); })[0] || null;
    if (!name || /^(известия|notifications|facebook)$/i.test(name))
      name = document.title.replace(/^\(\d+\)\s*/, '').replace(/\s*\|\s*Facebook.*$/, '').trim() || null;
    var rec = body.match(/(\d{1,3})\s*%\s*(recommend|препоръка|recomienda|recommandent|empfehlen|consigliano|recomenda|poleca|recomandă|συνιστούν|tavsiye)[^(\n]*\((\d[\d\s.,]*)\s*([^)\n]*)\)/i);
    var followers = body.match(/([\d.,]+\s*(хил\.|млн\.|[KkMm])?)\s*(followers|последователи|seguidores|abonnés|follower)/i);
    var head = lines.slice(0, 20);
    var category = null, catIdx = -1;
    for (var i = 0; i < lines.length; i++) {
      var m = lines[i].match(/^(Page|Страница|Página|Seite|Pagina|Strona|Σελίδα|Sayfa)\s*·\s*(.+)$/i);
      if (m) { catIdx = i; category = m[2].trim(); break; }
    }
    var price = null;
    if (!category) {  // logged-in: "<description>" then "<category>" [" · $$"] then "Още" in the header
      var more = head.findIndex(function (l) { return /^(още|more)$/i.test(l); });
      for (var k = more - 1; k > 0 && k >= more - 4; k--) {
        var cand = head[k];
        var pm = cand.match(/^[·\s]*(\${1,4})\s*$/);
        if (pm) { price = pm[1]; continue; }
        if (!cand.replace(/[·\s]/g, '')) continue;
        if (cand.length < 50 && !/\d|%/.test(cand) && !LABEL.test(cand) && cand !== name) { category = cand.replace(/\s*·.*$/, ''); catIdx = k; }
        break;
      }
    }
    var emailM = body.match(/[\w.+-]+@[\w-]+(\.[\w-]+)+/);
    var det = lines.findIndex(function (l) { return /^(подробности|details|intro|представяне|въведение)$/i.test(l); });
    // logged-out: "Intro/Въведение" → "Page · category" → address, phone, site;  logged-in: category in the header,
    // address etc. under "Подробности/Details" further down
    var from = catIdx >= 0 ? catIdx : 0;
    if (det >= 0 && (catIdx < 0 || det > catIdx)) from = det;
    var address = null, phone = null, website = null, hours = null;
    for (var j = from; j < Math.min(lines.length, from + 25); j++) {
      var l = lines[j];
      if (j > from + 1 && /^(препоръчани|featured|публикации|posts|филтри|filters|снимки|photos)$/i.test(l)) break;
      if (!phone && /^[+0(][\d\s()\-]{6,}$/.test(l)) { phone = l; continue; }
      if (!website && /^(https?:\/\/)?[a-z0-9\-]+(\.[a-z0-9\-]+)+(\/\S*)?$/i.test(l) && !/facebook\.com/i.test(l)) { website = l; continue; }
      if (!hours && /^(open now|closed now|отворено|затворено|always open|винаги отворено|opens|отваря|temporarily closed|временно затворено)/i.test(l)) { hours = l; continue; }
      if (!address && /,/.test(l) && !/%|followers|последовател|\(/i.test(l) && l.length < 160 && !/[.!?]$/.test(l)) {
        address = l.replace(/,\s*,/g, ',');
      }
    }
    var first = null;
    var art = main.querySelector('[role="article"]');
    if (art && (F.re.POS.test(txt(art).slice(0, 200)) || F.re.NEG.test(txt(art).slice(0, 200)))) {   // a review, not a post
      var t = txt(art);
      first = { header: (t.split('\n')[0] || '').slice(0, 200), recommends: F.re.NEG.test(t.slice(0, 300)) ? false : (F.re.POS.test(t.slice(0, 300)) ? true : null), text: t.slice(0, 600) };
    }
    return {
      url: location.href.split('?')[0], name: name, category: category, address: address, phone: phone,
      website: website, hours: hours, followers: followers ? followers[1].trim() : null,
      price: price, email: emailM ? emailM[0] : null,
      recommend_pct: rec ? parseInt(rec[1], 10) : null,
      review_total: rec ? parseInt(rec[3].replace(/[^\d]/g, ''), 10) : null,
      first_review: first, state: pageState().state,
      login_dialog: /see more (on|from)|вижте повече (във|от)|log in|влезте/i.test(Array.prototype.map.call(document.querySelectorAll('[role="dialog"]'), function (d) { return txt(d); }).join(' '))
    };
  }

  // Results of a web search page (DuckDuckGo HTML or Bing) that point to facebook.com: [{title, url, snippet}].
  function webResults() {
    var out = [], seen = new Set();
    var links = document.querySelectorAll('a[href]');
    for (var i = 0; i < links.length; i++) {
      var a = links[i], href = a.href;
      try {
        var u = new URL(href);
        var target = u.searchParams.get('uddg') || u.searchParams.get('u') || href;   // DuckDuckGo / redirectors
        if (/^a1aHR0/.test(target)) { try { target = atob(target.slice(2).replace(/-/g, '+').replace(/_/g, '/')); } catch (e) {} }  // Bing
        if (!/^https?:\/\/([a-z\-]+\.)?facebook\.com\//i.test(target)) continue;
        if (seen.has(target)) continue;
        var box = a.closest('.result, .b_algo, li, article, div') || a.parentElement;
        var h = box && box.querySelector('h2, h3');
        var title = (h ? txt(h) : txt(a)).split('\n').filter(function (x) { return x && !/^https?:|^[a-z0-9.\-]+\.[a-z]{2,}$/i.test(x); })[0] || txt(a);
        if (!title) continue;
        seen.add(target);
        out.push({ title: title.slice(0, 200), url: target, snippet: txt(box).replace(title, '').trim().slice(0, 300) });
      } catch (e) { /* not a URL */ }
    }
    return out;
  }

  F.scroll = scroll; F.sweep = sweep; F.pageSummary = pageSummary; F.webResults = webResults; F.extract = extract; F.extractPost = extractPost; F.pageState = pageState;
  F.searchResults = searchResults; F.tabState = tabState; F.reviewTotal = reviewTotal; F.recommendPct = recommendPct;
  F.reset = function () { window.__fbxAcc = null; window.__fbxState = null; window.scrollTo(0, 0); return true; };
  F.override = null;   // set by the override script: {version, notes}
  return F;
})();
