#!/usr/bin/env python3
"""X Radar: шукає пости, що летять на малих акаунтах, і шле їх у Telegram.

Режими:
  python radar.py            один прохід: нові гарячі пости -> Telegram
  python radar.py --digest   ранковий топ за добу одним повідомленням
  python radar.py --dry      нічого не шле, друкує в консоль
  python radar.py --loop 60  крутиться на компі, прохід кожні 60 хв

Ключі беруться зі змінних середовища (або з файлу .env поруч):
  TWITTERAPI_IO_KEY   ключ twitterapi.io (джерело даних)
  TELEGRAM_BOT_TOKEN  токен бота від @BotFather
  TELEGRAM_CHAT_ID    куди слати (твій чат або канал)
  ANTHROPIC_API_KEY   необов'язково: короткий розбір «чому летить» від Claude
Тільки стандартна бібліотека Python 3.9+, нічого ставити не треба.
"""
import argparse
import html
import json
import os
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "state.json")
SEARCH_URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"


def load_env():
    path = os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))


def http_json(url, headers=None, data=None, timeout=40):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers or {})
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def load_state():
    try:
        return json.load(open(STATE_PATH, encoding="utf-8"))
    except (OSError, ValueError):
        return {"seen": {}, "pool": {}}


def save_state(state):
    # Тримаємо лише останні 3 доби, щоб файл не ріс вічно.
    cutoff = time.time() - 3 * 86400
    state["seen"] = {k: v for k, v in state["seen"].items() if v > cutoff}
    state["pool"] = {k: v for k, v in state["pool"].items() if v["ts"] > cutoff}
    json.dump(state, open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def search(query, pages, key):
    tweets, cursor = [], ""
    for _ in range(pages):
        params = urllib.parse.urlencode({"query": query, "queryType": "Top", "cursor": cursor})
        for attempt in range(4):
            # Безкоштовний рівень twitterapi.io пускає 1 запит на 5 секунд.
            time.sleep(6)
            try:
                res = http_json(f"{SEARCH_URL}?{params}", {"X-API-Key": key})
                break
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == 3:
                    raise
        tweets += res.get("tweets") or []
        if not res.get("has_next_page"):
            break
        cursor = res.get("next_cursor", "")
    return tweets


def score(t):
    """X-фактор = перегляди / підписники. Малий акаунт + багато переглядів = летить тема, а не автор."""
    a = t.get("author") or {}
    views = int(t.get("viewCount") or 0)
    followers = int(a.get("followers") or 0)
    try:
        created = parsedate_to_datetime(t["createdAt"])
    except (KeyError, TypeError, ValueError):
        created = datetime.now(timezone.utc)
    age_h = max((datetime.now(timezone.utc) - created).total_seconds() / 3600, 0.25)
    return {
        "id": t["id"],
        "url": t.get("url") or f"https://x.com/{a.get('userName')}/status/{t['id']}",
        "text": (t.get("text") or "").strip(),
        "user": a.get("userName", "?"),
        "followers": followers,
        "views": views,
        "likes": int(t.get("likeCount") or 0),
        "reposts": int(t.get("retweetCount") or 0) + int(t.get("quoteCount") or 0),
        "replies": int(t.get("replyCount") or 0),
        "bookmarks": int(t.get("bookmarkCount") or 0),
        "age_h": round(age_h, 1),
        "x_factor": round(views / max(followers, 100), 1),
        "per_hour": int(views / age_h),
        "ts": time.time(),
    }


def is_hot(p, cfg):
    low = p["text"].lower()
    if any(w.lower() in low for w in cfg.get("exclude_words", [])):
        return False
    return (p["views"] >= cfg["min_views"]
            and p["x_factor"] >= cfg["min_x_factor"]
            and p["followers"] <= cfg["max_followers"])


def fmt_num(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def analyze(p, cfg):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not (key and cfg.get("claude_analysis")):
        return ""
    prompt = (
        "Ось пост з X, який летить на малому акаунті.\n"
        f"Автор: @{p['user']}, {p['followers']} підписників. Перегляди: {p['views']} за {p['age_h']} год.\n"
        f"Текст:\n{p['text'][:1500]}\n\n"
        "Українською, коротко, без вступу: 1) чому летить (хук, тема, формат) одним реченням; "
        "2) два свіжі кути, як зробити свій пост на цю тему, не копіюючи. Максимум 60 слів."
    )
    try:
        res = http_json("https://api.anthropic.com/v1/messages",
                        {"x-api-key": key, "anthropic-version": "2023-06-01"},
                        {"model": cfg.get("claude_model", "claude-haiku-5-5"), "max_tokens": 1500,
                         # Haiku 5.5 думає за замовчуванням і може витратити весь ліміт на роздуми,
                         # тоді тексту немає. Для короткого розбору роздуми не потрібні.
                         "thinking": {"type": "disabled"},
                         "output_config": {"effort": "low"},
                         "messages": [{"role": "user", "content": prompt}]})
        text = "".join(b.get("text", "") for b in res.get("content", [])).strip()
        if not text:
            print("Claude analysis empty, stop_reason:", res.get("stop_reason"), file=sys.stderr)
        return text
    except Exception as e:  # розбір необов'язковий, радар не повинен падати через нього
        print("Claude analysis failed:", e, file=sys.stderr)
        return ""


def card(p, note=""):
    snippet = p["text"][:280] + ("…" if len(p["text"]) > 280 else "")
    lines = [
        f"🔥 <b>{fmt_num(p['views'])} переглядів</b> · @{html.escape(p['user'])} ({fmt_num(p['followers'])} підп.)",
        f"X-фактор <b>×{p['x_factor']}</b> · {fmt_num(p['per_hour'])}/год · вік {p['age_h']} год",
        f"❤️ {fmt_num(p['likes'])}  🔁 {fmt_num(p['reposts'])}  💬 {fmt_num(p['replies'])}  🔖 {fmt_num(p['bookmarks'])}",
        "",
        html.escape(snippet),
    ]
    if note:
        lines += ["", "🧠 " + html.escape(note)]
    lines += ["", p["url"]]
    return "\n".join(lines)


def send(text, dry):
    if dry:
        print(text, "\n" + "-" * 60)
        return
    token, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    http_json(f"https://api.telegram.org/bot{token}/sendMessage", data={
        "chat_id": chat, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": False,
    })


def run(cfg, dry):
    key = os.environ["TWITTERAPI_IO_KEY"]
    state = load_state()
    since = int(time.time() - cfg["lookback_hours"] * 3600)
    found = {}
    for q in cfg["queries"]:
        full = f"{q} since_time:{since} -filter:replies -filter:retweets"
        if cfg.get("lang"):
            full += f" lang:{cfg['lang']}"
        try:
            for t in search(full, cfg["pages_per_query"], key):
                p = score(t)
                found[p["id"]] = p
        except Exception as e:
            print(f"Query failed: {q!r}: {e}", file=sys.stderr)

    for p in found.values():
        state["pool"][p["id"]] = p  # для ранкового дайджесту

    hot = [p for p in found.values() if is_hot(p, cfg) and p["id"] not in state["seen"]]
    hot.sort(key=lambda p: (p["x_factor"], p["per_hour"]), reverse=True)
    for p in hot[: cfg["max_alerts_per_run"]]:
        send(card(p, analyze(p, cfg)), dry)
        state["seen"][p["id"]] = time.time()
    print(f"checked {len(found)} posts, hot {len(hot)}, sent {min(len(hot), cfg['max_alerts_per_run'])}")
    if not dry:
        save_state(state)


def digest(cfg, dry):
    state = load_state()
    day_ago = time.time() - 86400
    pool = [p for p in state["pool"].values() if p["ts"] > day_ago and is_hot(p, cfg)]
    pool.sort(key=lambda p: p["x_factor"], reverse=True)
    top = pool[: cfg["digest_size"]]
    if not top:
        send("📡 Радар: за добу нічого гарячого під твої фільтри. Може, знизити min_views?", dry)
        return
    lines = [f"📡 <b>Радар: топ-{len(top)} за добу</b> (сортування за X-фактором)\n"]
    for i, p in enumerate(top, 1):
        first = html.escape(p["text"].split("\n")[0][:90])
        lines.append(f"{i}. ×{p['x_factor']} · {fmt_num(p['views'])} · @{html.escape(p['user'])} ({fmt_num(p['followers'])})\n"
                     f"   {first}\n   {p['url']}")
    # Telegram обмежує 4096 символів, тому ріжемо на шматки.
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) > 3800:
            send(chunk, dry)
            chunk = ""
        chunk += line + "\n"
    send(chunk, dry)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--digest", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--loop", type=int, metavar="MIN")
    args = ap.parse_args()
    load_env()
    cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    if args.digest:
        return digest(cfg, args.dry)
    while True:
        run(cfg, args.dry)
        if not args.loop:
            break
        time.sleep(args.loop * 60)


if __name__ == "__main__":
    main()
