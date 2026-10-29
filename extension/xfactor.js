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
    tweets.set(id, {
      views: num(t.views && t.views.count) ?? prev.views ?? null,
      followers: (u && u.followers) ?? prev.followers ?? null,
      handle: (u && u.handle) || prev.handle || null,
    });
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
  XMLHttpRequest.prototype.send = function () {
    if (isGraphql(this.__xrUrl)) {
      this.addEventListener("load", () => {
        if (this.responseType === "" || this.responseType === "text") ingest(this.responseText);
        else if (this.responseType === "json") ingest(this.response);
      });
    }
    return origSend.apply(this, arguments);
  };

  const origFetch = window.fetch;
  window.fetch = function (input) {
    const p = origFetch.apply(this, arguments);
    const url = typeof input === "string" ? input : input && input.url;
    if (isGraphql(url)) {
      p.then((r) => r.clone().text().then(ingest)).catch(() => {});
    }
    return p;
  };

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

  function decorate(article) {
    const id = tweetIdOf(article);
    if (!id) return;
    const t = tweets.get(id) || {};
    const handle = t.handle ? t.handle.toLowerCase() : handleOf(article);
    const views = t.views ?? domViews(article);
    const followers = t.followers ?? (handle != null ? users.get(handle) : undefined) ?? null;
    if (views == null) return;

    const sig = `${id}|${views}|${followers}`;
    let badge = article.querySelector(":scope .xr-badge");
    if (badge && badge.dataset.sig === sig) return;

    const bar = [...article.querySelectorAll('div[role="group"]')].pop();
    if (!bar) return;
    if (!badge) {
      badge = document.createElement("div");
      badge.className = "xr-badge";
      bar.parentElement.insertBefore(badge, bar);
    }
    badge.dataset.sig = sig;

    if (followers == null) {
      badge.className = "xr-badge";
      badge.innerHTML = `×? <span class="xr-meta">${fmt(views)} перегл. · підписників не видно</span>`;
      badge.title = "X ще не віддав кількість підписників автора. Наведи на автора або прокрути далі.";
      return;
    }

    const xf = Math.round((views / Math.max(followers, FOLLOWER_FLOOR)) * 10) / 10;
    const big = followers > MAX_FOLLOWERS;
    const hot = xf >= MIN_X_FACTOR && views >= MIN_VIEWS && !big;
    const cls = hot ? "xr-hot" : xf >= MIN_X_FACTOR || xf >= WARM_X_FACTOR ? "xr-warm" : "";
    badge.className = ["xr-badge", cls, big && "xr-big"].filter(Boolean).join(" ");
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
      document.querySelectorAll('article[data-testid="tweet"]').forEach(decorate);
    });
  }

  const start = () => {
    new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
    schedule();
  };
  if (document.body) start();
  else document.addEventListener("DOMContentLoaded", start);
})();
