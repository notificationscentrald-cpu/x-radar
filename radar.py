#!/usr/bin/env python3
"""X Radar: шукає пости, що летять на малих акаунтах, і шле їх у Telegram.

Режими:
  python radar.py            один прохід: нові гарячі пости -> Telegram
  python radar.py --digest   ранковий топ за добу одним повідомленням
  python radar.py --dry      нічого не шле, друкує в консоль
  python radar.py --loop 60  крутиться на компі, прохід кожні 60 хв
  python radar.py --preview 5  тест вигляду на 5 останніх постах, без пошуку

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
    state["sent"] = {k: v for k, v in state.get("sent", {}).items() if v > cutoff}
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
    """Claude перевіряє, чи пост у ніші, і пише про що він та чому летить.
    Повертає dict з ключами fit, about, why або None, якщо Claude недоступний."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not (key and cfg.get("claude_analysis")):
        return None
    prompt = (
        f"Ніша автора, для якого шукаємо пости: {cfg.get('niche', '')}\n\n"
        "Ось пост з X, який летить на малому акаунті.\n"
        f"Автор: @{p['user']}, {p['followers']} підписників. Перегляди: {p['views']} за {p['age_h']} год.\n"
        f"Текст:\n{p['text'][:1500]}\n\n"
        "Відповідай лише JSON без markdown: "
        '{"fit": true/false, "about": "...", "why": "..."}\n'
        "fit: чи підходить пост під нішу, щоб автор зробив свою версію. "
        "Дивись на опис ніші: що в ньому названо, те підходить.\n"
        "about: українською одне коротке речення, про що пост (до 15 слів).\n"
        "why: українською одне коротке речення, чим саме зачепив (до 15 слів). "
        "Назви конкретний прийом цього поста, без загальних фраз на кшталт «вау-ефект» чи «цікава тема»."
    )
    try:
        res = http_json("https://api.anthropic.com/v1/messages",
                        {"x-api-key": key, "anthropic-version": "2023-06-01"},
                        {"model": cfg.get("claude_model", "claude-haiku-5-5"), "max_tokens": 600,
                         # Haiku 5.5 думає за замовчуванням і може витратити весь ліміт на роздуми,
                         # тоді тексту немає. Для короткої оцінки роздуми не потрібні.
                         "thinking": {"type": "disabled"},
                         "output_config": {"effort": "low"},
                         "messages": [{"role": "user", "content": prompt}]})
        text = "".join(b.get("text", "") for b in res.get("content", [])).strip()
        out = json.loads(text[text.index("{"):text.rindex("}") + 1])
        return {"fit": bool(out.get("fit", True)), "about": str(out.get("about", "")).strip(),
                "why": str(out.get("why", "")).strip()}
    except Exception as e:  # оцінка необов'язкова, радар не повинен падати через неї
        print("Claude analysis failed:", e, file=sys.stderr)
        return None


def card(p, note=None):
    note = note or {}
    lines = [
        f"⚡ <b>X-ФАКТОР ×{p['x_factor']:g}</b>",
        f"👁 <b>{fmt_num(p['views'])}</b> переглядів · 💬 {fmt_num(p['replies'])} коментарів",
        f"👤 @{html.escape(p['user'])} · {fmt_num(p['followers'])} підп.",
        "",
    ]
    if note.get("about"):
        lines.append(f"<b>{html.escape(note['about'], quote=False)}</b>")
    else:  # без Claude показуємо перший рядок поста
        lines.append(html.escape(p["text"].split("\n")[0][:140], quote=False))
    if note.get("why"):
        lines.append(f"<i>Чому летить: {html.escape(note['why'], quote=False)}</i>")
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
    # GitHub інколи пропускає запуски за розкладом, тому розклад частіший,
    # а зайві запуски виходять одразу, не витрачаючи кредитів.
    gap_h = (time.time() - state.get("last_run", 0)) / 3600
    if not dry and gap_h < cfg.get("min_hours_between_runs", 0):
        print(f"skip: last run {gap_h:.1f}h ago")
        return
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
    sent = rejected = 0
    # Claude відсіює пости не з ніші; ліміт перевірок тримає ціну під контролем.
    for p in hot[: cfg.get("max_checks_per_run", 30)]:
        if sent >= cfg["max_alerts_per_run"]:
            break
        note = analyze(p, cfg)
        state["seen"][p["id"]] = time.time()
        if note and not note["fit"]:
            rejected += 1
            print(f"off-niche: @{p['user']}: {p['text'][:80]!r}")
            continue
        send(card(p, note), dry)
        state.setdefault("sent", {})[p["id"]] = time.time()
        sent += 1
    print(f"checked {len(found)} posts, hot {len(hot)}, off-niche {rejected}, sent {sent}")
    if not dry:
        state["last_run"] = time.time()
        save_state(state)


def digest(cfg, dry):
    state = load_state()
    day_ago = time.time() - 86400
    # У дайджест ідуть лише пости, які радар надіслав за добу, тобто ті, що пройшли перевірку ніші.
    sent = state.get("sent") or state["seen"]  # до 28.10 надіслані пости писались лише в seen
    pool = [state["pool"][i] for i, ts in sent.items() if ts > day_ago and i in state["pool"]]
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


def preview(cfg, n, dry):
    """Перевірка нового вигляду: бере останні надіслані пости з пам'яті, без витрати кредитів на пошук."""
    state = load_state()
    sent = state.get("sent") or state["seen"]
    ids = sorted((i for i in sent if i in state["pool"]), key=lambda i: -sent[i])[:n]
    send(f"🧪 Тест нового вигляду на {len(ids)} останніх постах. Пости не з ніші позначені ❌.", dry)
    for i in ids:
        p = state["pool"][i]
        note = analyze(p, cfg)
        mark = "❌ НЕ З НІШІ, радар би це не надіслав\n\n" if note and not note["fit"] else ""
        print("fit" if not mark else "off-niche", p["user"])
        send(mark + card(p, note), dry)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--digest", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--loop", type=int, metavar="MIN")
    ap.add_argument("--preview", type=int, metavar="N")
    args = ap.parse_args()
    load_env()
    cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    if args.digest:
        return digest(cfg, args.dry)
    if args.preview:
        return preview(cfg, args.preview, args.dry)
    while True:
        run(cfg, args.dry)
        if not args.loop:
            break
        time.sleep(args.loop * 60)


if __name__ == "__main__":
    main()
