// X Radar у стрічці: під кожним постом показує X-фактор = перегляди / підписники автора.
// Формула і пороги ті самі, що в radar.py (score / is_hot). Дані беремо з відповідей самого X
// (GraphQL), тож жодних ключів API не треба.
(() => {
  "use strict";

  // Ті самі числа, що в config.json радара.
  const MIN_X_FACTOR = 15;
  const MIN_VIEWS = 50000;
  const MAX_FOLLOWERS = 50000;
  const FOLLOWER_FLOOR = 100; // max(followers, 100), як у radar.py
  const WARM_X_FACTOR = 5;

  // Реплай-бейдж. Обґрунтування чисел: документ «Формула "де реплаїти"» у проєкті.
  // Pfeffer et al., ICWSM 2023: медіанний твіт з 20-ї хвилини до доби набирає ще ×3.75 переглядів.
  // Експонента з піврозпадом 45 хв дає саме ×3.75 на 20-й хвилині, тож беремо 45, а не їхню медіану 80:
  // на свіжих постах, які нас і цікавлять, 80 хв завищує прогноз майже вдвічі.
  const HALF_LIFE_MIN = 45;
  const REPLY_CROWD = 20; // при 20 реплаях під постом «місце» = 0.5 (порада практиків, не перевірено)
  const REPLY_MAX_AGE_MIN = 60;
  const REPLY_MAX_REPLIES = 50;
  const REPLY_TOP_N = 3; // з акаунтом з міткою 2–5 реплаїв на день, тож світимо лише найкращі
  const REPLY_WINDOW_MIN = 30; // топ рахуємо серед постів, які ти бачив за останні 30 хв
  const NICHE_BONUS = 1.5; // вигадане число, калібруємо журналом реплаїв
  const NICHE_RE = /claude|anthropic|opus|sonnet|three\.?js|\b3d\b|diorama|\bgame\b|vibe.?cod|one prompt|\bgpt|openai|gemini|grok|kimi|\bai video|tracking|computer vision|\bagents?\b|cursor|codex/i;
  const LOG_KEY = "xr-reply-log";

  const tweets = new Map(); // id -> {views, followers, handle}
  const users = new Map(); // handle (lowercase) -> followers

  // ---------- збір даних з відповідей X ----------

  const num = (v) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };

  function userInfo(u) {
    if (!u) return null;
    if (u.__typename === "UserUnavailable") return null;
    const handle = (u.core && u.core.screen_name) || (u.legacy && u.legacy.screen_name);
    const followers = num(
      (u.legacy && u.legacy.followers_count) ??
      (u.relationship_counts && u.relationship_counts.followers)
    );
    if (handle && followers != null) users.set(handle.toLowerCase(), followers);
    return { handle, followers };
  }

  function recordTweet(t) {
    const id = t.rest_id;
    if (!id) return;
    const u = userInfo(t.core && t.core.user_results && t.core.user_results.result);
    const prev = tweets.get(id) || {};
    const views = num(t.views && t.views.count) ?? prev.views ?? null;
    const cur = {
      views,
      followers: (u && u.followers) ?? prev.followers ?? null,
      handle: (u && u.handle) || prev.handle || null,
      replies: num(t.legacy && t.legacy.reply_count) ?? prev.replies ?? null,
      isReply: !!(t.legacy && t.legacy.in_reply_to_status_id_str) || !!prev.isReply,
      snap: prev.snap,
      prevSnap: prev.prevSnap,
    };
    // Два знімки переглядів щонайменше з різницею 2 хв дають справжню швидкість зараз.
    if (views != null) {
      const now = Date.now();
      if (!cur.snap) cur.snap = { t: now, v: views };
      else if (now - cur.snap.t >= 120000 && views > cur.snap.v) {
        cur.prevSnap = cur.snap;
        cur.snap = { t: now, v: views };
      }
    }
    tweets.set(id, cur);
    trackOwnReplyViews(id, cur);
  }

  function walk(o, depth) {
    if (!o || typeof o !== "object" || depth > 60) return;
    if (Array.isArray(o)) {
      for (const x of o) walk(x, depth + 1);
      return;
    }
    if (o.__typename === "Tweet" || (o.rest_id && o.legacy && "full_text" in o.legacy)) recordTweet(o);
    else if (o.__typename === "User") userInfo(o);
    for (const k in o) {
      const v = o[k];
      if (v && typeof v === "object") walk(v, depth + 1);
    }
  }

  function ingest(text) {
    try {
      walk(typeof text === "string" ? JSON.parse(text) : text, 0);
      schedule();
    } catch (_) {}
  }

  const isGraphql = (url) => typeof url === "string" && /\/graphql\/|\/i\/api\//.test(url);

  const origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__xrUrl = String(url);
    return origOpen.apply(this, arguments);
  };
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (body) {
    if (isGraphql(this.__xrUrl)) {
      const parent = replyParentOf(this.__xrUrl, body);
      this.addEventListener("load", () => {
        let data = null;
        if (this.responseType === "" || this.responseType === "text") data = this.responseText;
        else if (this.responseType === "json") data = this.response;
        if (parent) logOwnReply(parent, data);
        ingest(data);
      });
    }
    return origSend.apply(this, arguments);
  };

  const origFetch = window.fetch;
  window.fetch = function (input, init) {
    const p = origFetch.apply(this, arguments);
    const url = typeof input === "string" ? input : input && input.url;
    if (isGraphql(url)) {
      const parent = replyParentOf(url, init && init.body);
      p.then((r) => r.clone().text().then((text) => {
        if (parent) logOwnReply(parent, text);
        ingest(text);
      })).catch(() => {});
    }
    return p;
  };

  // ---------- журнал твоїх реплаїв (для калібрування формули) ----------

  let logCache = null;
  function loadLog() {
    if (!logCache) {
      try { logCache = JSON.parse(localStorage.getItem(LOG_KEY)) || []; } catch (_) { logCache = []; }
    }
    return logCache;
  }
  function saveLog(log) {
    logCache = log.slice(-300);
    try { localStorage.setItem(LOG_KEY, JSON.stringify(logCache)); } catch (_) {}
  }

  // Публікація реплаю = запит CreateTweet з variables.reply.in_reply_to_tweet_id.
  function replyParentOf(url, body) {
    if (!/CreateTweet/.test(url) || typeof body !== "string") return null;
    try {
      const v = JSON.parse(body).variables;
      return (v && v.reply && v.reply.in_reply_to_tweet_id) || null;
    } catch (_) { return null; }
  }

  function logOwnReply(parentId, response) {
    let replyId = null;
    try {
      const d = typeof response === "string" ? JSON.parse(response) : response;
      replyId = d.data.create_tweet.tweet_results.result.rest_id;
    } catch (_) {}
    if (!replyId) return;
    const p = tweets.get(parentId) || {};
    const s = replyScore(parentId);
    const log = loadLog().slice();
    log.push({
      replyId,
      parentId,
      parentHandle: p.handle || null,
      at: Date.now(),
      parentAgeMin: Math.round(ageMin(parentId)),
      parentViews: p.views ?? null,
      parentReplies: p.replies ?? null,
      ahead: s ? Math.round(s.ahead) : null,
      score: s ? Math.round(s.score) : null,
      wasTop: topIds.has(parentId),
      replyViews: null,
      replyViewsAt: null,
      parentViewsLater: null,
    });
    saveLog(log);
    renderLogButton();
  }

  // Будь-яка відповідь X, де трапився твій реплай (вкладка Replies, тред), оновлює його покази.
  function trackOwnReplyViews(id, t) {
    if (t.views == null) return;
    const log = loadLog();
    if (!log.some((r) => r.replyId === id || r.parentId === id)) return;
    let changed = false;
    for (const r of log) {
      if (r.replyId === id && t.views !== r.replyViews) {
        r.replyViews = t.views;
        r.replyViewsAt = Date.now();
        changed = true;
      }
      if (r.parentId === id && Date.now() - r.at > 3 * 3600e3 && t.views !== r.parentViewsLater) {
        r.parentViewsLater = t.views;
        changed = true;
      }
    }
    if (changed) saveLog(log);
  }

  // ---------- малювання бейджа ----------

  function fmt(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
    return String(n);
  }

  // "12,345 views" / "12 345 переглядів" -> 12345 (запасний варіант, якщо X не віддав views у JSON)
  function domViews(article) {
    const a = article.querySelector('a[href$="/analytics"]');
    const label = a && a.getAttribute("aria-label");
    const m = label && label.match(/^[\d\s.,  ]+/);
    return m ? num(m[0].replace(/[^\d]/g, "")) : null;
  }

  function tweetIdOf(article) {
    const t = article.querySelector('a[href*="/status/"] time');
    const m = t && t.parentElement.getAttribute("href").match(/\/status\/(\d+)/);
    return m ? m[1] : null;
  }

  function handleOf(article) {
    const a = article.querySelector('[data-testid="User-Name"] a[href^="/"]');
    return a ? a.getAttribute("href").slice(1).toLowerCase() : null;
  }

  // Час публікації з самого id (snowflake), тож працює навіть без JSON.
  function ageMin(id) {
    try {
      const ms = Number((BigInt(id) >> 22n) + 1288834974657n);
      return Math.max((Date.now() - ms) / 60000, 1);
    } catch (_) { return null; }
  }

  function domCount(article, testid) {
    const b = article.querySelector(`[data-testid="${testid}"]`);
    const label = b && b.getAttribute("aria-label");
    const m = label && label.match(/^[\d\s.,\u00a0\u202f]+/);
    return m ? num(m[0].replace(/[^\d]/g, "")) : 0;
  }

  // Скільки переглядів пост ще збере (модель: половина переглядів за HALF_LIFE_MIN),
  // поділене на конкуренцію в реплаях і помножене на нішу.
  const domInfo = new Map(); // id -> {views, replies, text} з розмітки, запасний варіант до JSON

  function readDom(article, id) {
    domInfo.set(id, {
      views: domViews(article),
      replies: domCount(article, "reply"),
      text: ((article.querySelector('[data-testid="tweetText"]') || {}).textContent) || "",
    });
  }

  function replyScore(id) {
    const t = tweets.get(id) || {};
    const d = domInfo.get(id) || {};
    const age = ageMin(id);
    const views = t.views ?? d.views ?? null;
    const replies = t.replies ?? d.replies ?? null;
    if (age == null || views == null || replies == null) return null;
    let ahead;
    if (t.prevSnap && t.snap && t.snap.t - t.prevSnap.t >= 120000) {
      const perMin = (t.snap.v - t.prevSnap.v) / ((t.snap.t - t.prevSnap.t) / 60000);
      ahead = perMin * (HALF_LIFE_MIN / Math.LN2) * Math.pow(2, -(Date.now() - t.snap.t) / 60000 / HALF_LIFE_MIN);
    } else {
      const q = Math.pow(2, -Math.max(age, 5) / HALF_LIFE_MIN); // перші хвилини занадто шумні
      ahead = (views * q) / (1 - q);
    }
    const niche = NICHE_RE.test(d.text || "");
    const room = 1 / (1 + replies / REPLY_CROWD);
    return { age, views, replies, ahead, niche, score: ahead * room * (niche ? NICHE_BONUS : 1) };
  }

  const seen = new Map(); // id -> коли востаннє бачив у стрічці
  let topIds = new Set();

  function eligible(s, t) {
    return s && !t.isReply && s.age <= REPLY_MAX_AGE_MIN && s.replies <= REPLY_MAX_REPLIES;
  }

  function computeTop() {
    const now = Date.now();
    const list = [];
    for (const [id, at] of seen) {
      if (now - at > REPLY_WINDOW_MIN * 60000) { seen.delete(id); continue; }
      const s = replyScore(id);
      if (eligible(s, tweets.get(id) || {})) list.push([id, s.score]);
    }
    list.sort((a, b) => b[1] - a[1]);
    topIds = new Set(list.slice(0, REPLY_TOP_N).map((x) => x[0]));
  }

  function replyBadge(article, id, t) {
    const row = article.querySelector(":scope .xr-row");
    let b = row && row.querySelector(".xr-reply");
    const s = replyScore(id);
    if (!eligible(s, t)) { if (b) b.remove(); return; }
    if (!b) {
      b = document.createElement("div");
      row.appendChild(b);
    }
    const top = topIds.has(id);
    b.className = "xr-badge xr-reply" + (top ? " xr-top" : "");
    b.innerHTML = `💬 ${top ? "реплай" : "можна"} <span class="xr-meta">${Math.round(s.age)} хв · ${s.replies} репл. · ~${fmt(Math.round(s.ahead))} попереду${s.niche ? " · ніша" : ""}</span>`;
    b.title = top
      ? `Топ-${REPLY_TOP_N} для реплаю серед постів, які ти бачив за ${REPLY_WINDOW_MIN} хв. «Попереду» = скільки переглядів пост іще збере (оцінка).`
      : "Свіжий пост, але не в топі зараз. «Попереду» = скільки переглядів пост іще збере (оцінка).";
  }

  function decorate(article) {
    const id = tweetIdOf(article);
    if (!id) return;
    const t = tweets.get(id) || {};
    const handle = t.handle ? t.handle.toLowerCase() : handleOf(article);
    const views = t.views ?? domViews(article);
    const followers = t.followers ?? (handle != null ? users.get(handle) : undefined) ?? null;
    if (views == null) return;

    let row = article.querySelector(":scope .xr-row");
    if (!row) {
      const bar = [...article.querySelectorAll('div[role="group"]')].pop();
      if (!bar) return;
      row = document.createElement("div");
      row.className = "xr-row";
      bar.parentElement.insertBefore(row, bar);
    }
    const sig = `${id}|${views}|${followers}|${t.replies}|${topIds.has(id)}|${Math.floor(Date.now() / 60000)}`;
    if (row.dataset.sig === sig) return;
    row.dataset.sig = sig;
    replyBadge(article, id, t);

    let badge = row.querySelector(".xr-xf");
    if (!badge) {
      badge = document.createElement("div");
      row.prepend(badge);
    }

    if (followers == null) {
      badge.className = "xr-badge xr-xf";
      badge.innerHTML = `×? <span class="xr-meta">${fmt(views)} перегл. · підписників не видно</span>`;
      badge.title = "X ще не віддав кількість підписників автора. Наведи на автора або прокрути далі.";
      return;
    }

    const xf = Math.round((views / Math.max(followers, FOLLOWER_FLOOR)) * 10) / 10;
    const big = followers > MAX_FOLLOWERS;
    const hot = xf >= MIN_X_FACTOR && views >= MIN_VIEWS && !big;
    const cls = hot ? "xr-hot" : xf >= MIN_X_FACTOR || xf >= WARM_X_FACTOR ? "xr-warm" : "";
    badge.className = ["xr-badge", "xr-xf", cls, big && "xr-big"].filter(Boolean).join(" ");
    badge.innerHTML =
      `${hot ? "⚡ " : ""}×${xf} <span class="xr-meta">${fmt(views)} перегл. · ${fmt(followers)} підп.</span>`;
    badge.title = hot
      ? "Радар би це спіймав: X-фактор ≥ 15, ≥ 50K переглядів, автор до 50K підписників."
      : big
        ? "Великий акаунт (понад 50K підписників): радар такі пропускає."
        : `X-фактор = перегляди / підписники. Поріг радара ×${MIN_X_FACTOR} і ${fmt(MIN_VIEWS)} переглядів.`;
  }

  let queued = false;
  function schedule() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      const articles = document.querySelectorAll('article[data-testid="tweet"]');
      articles.forEach((a) => {
        const id = tweetIdOf(a);
        if (id) { seen.set(id, Date.now()); readDom(a, id); }
      });
      computeTop();
      articles.forEach(decorate);
    });
  }

  const start = () => {
    new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
    setInterval(schedule, 30000); // вік постів росте, топ треба перераховувати
    schedule();
    renderLogButton();
  };

  // ---------- панель журналу реплаїв ----------

  function renderLogButton() {
    if (!document.body) return;
    let btn = document.getElementById("xr-log-btn");
    if (!btn) {
      btn = document.createElement("button");
      btn.id = "xr-log-btn";
      btn.addEventListener("click", toggleLogPanel);
      document.body.appendChild(btn);
    }
    btn.textContent = `💬 ${loadLog().length}`;
    btn.title = "Журнал твоїх реплаїв (X Radar)";
  }

  const csvCell = (v) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));

  function logCsv(log) {
    const cols = ["at", "parentHandle", "parentId", "replyId", "parentAgeMin", "parentViews", "parentReplies",
      "ahead", "score", "wasTop", "replyViews", "replyViewsAt", "parentViewsLater"];
    const rows = log.map((r) => cols.map((c) =>
      csvCell(c === "at" || c === "replyViewsAt" ? (r[c] ? new Date(r[c]).toISOString() : null) : r[c])).join(","));
    return [cols.join(","), ...rows].join("\n");
  }

  function toggleLogPanel() {
    const old = document.getElementById("xr-log-panel");
    if (old) { old.remove(); return; }
    const log = loadLog().slice().reverse();
    const p = document.createElement("div");
    p.id = "xr-log-panel";
    const esc = (x) => String(x ?? "—").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
    const rows = log.map((r) => {
      const hrs = r.replyViewsAt ? Math.round((r.replyViewsAt - r.at) / 3600e3) : null;
      return `<tr><td>${new Date(r.at).toLocaleString("uk-UA", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</td>` +
        `<td><a href="/i/status/${esc(r.parentId)}" target="_blank">@${esc(r.parentHandle)}</a></td>` +
        `<td>${esc(r.parentAgeMin)} хв</td><td>${esc(r.parentReplies)}</td>` +
        `<td>${r.ahead != null ? fmt(r.ahead) : "—"}${r.wasTop ? " ⭐" : ""}</td>` +
        `<td><a href="/i/status/${esc(r.replyId)}" target="_blank">${r.replyViews != null ? fmt(r.replyViews) : "?"}</a>${hrs != null ? ` <span class="xr-dim">(${hrs} год)</span>` : ""}</td></tr>`;
    }).join("");
    p.innerHTML =
      `<div class="xr-log-head"><b>Твої реплаї: ${log.length}</b>` +
      `<span><button id="xr-log-copy">Скопіювати CSV</button> <button id="xr-log-close">✕</button></span></div>` +
      `<p class="xr-dim">Покази реплаю оновлюються, коли X їх завантажує: відкрий свій профіль → Replies через добу. Скопіюй CSV і кинь мені, щоб підігнати пороги.</p>` +
      `<table><tr><th>Коли</th><th>Під постом</th><th>Вік</th><th>Репл.</th><th>Попереду</th><th>Покази реплаю</th></tr>${rows}</table>`;
    document.body.appendChild(p);
    p.querySelector("#xr-log-close").onclick = () => p.remove();
    p.querySelector("#xr-log-copy").onclick = (e) => {
      navigator.clipboard.writeText(logCsv(loadLog())).then(() => { e.target.textContent = "Скопійовано"; });
    };
  }
  if (document.body) start();
  else document.addEventListener("DOMContentLoaded", start);
})();
