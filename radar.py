#!/usr/bin/env python3
"""X Radar: шукає пости, що летять на малих акаунтах, і шле їх у Telegram.

Режими:
  python radar.py            один прохід: нові гарячі пости -> Telegram
  python radar.py --digest   ранковий топ за добу одним повідомленням
  python radar.py --dry      нічого не шле, друкує в консоль
  python radar.py --loop 60  крутиться на компі, прохід кожні 60 хв
  python radar.py --preview 5  тест вигляду на 5 останніх постах, без пошуку
  python radar.py --replies  нові пости акаунтів зі списку reply_watch -> «НАПИШИ РЕПЛАЙ» + варіанти
  python radar.py --fresh    пости молодші за 2 год, що швидко ростуть -> «реплай з відео зараз»
  python radar.py --test-bait  перевірка фільтра «напиши слово, отримаєш» на прикладах

Реплай-сповіщення (--replies, --fresh) ідуть лише під пости, що вже летять:
5K+ переглядів, або 🚀 від 1K/год, або 30+ коментарів. Під кожним показано,
скільки переглядів збирають чужі реплаї під цим постом (🗣), і є денний ліміт.
Пости-приманки («comment AGENT and I'll DM you») радар пропускає завжди:
за реплаї під такими вже був тіньовий бан.

Кожне сповіщення має мітку дії за віком поста:
  🟢 до 2 год   реплай під оригіналом зараз (можна з нашим відео)
  🟡 до 24 год  можна квотувати оригінал
  ⚪ старше     лише як ідея, ремейк окремим постом не робити

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
import re
import statistics
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "state.json")
SEARCH_URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"
REPLIES_URL = "https://api.twitterapi.io/twitter/tweet/replies"
NY = ZoneInfo("America/New_York")

# Пости-приманки: «напиши слово / постав лайк, і я скину». Реплаї під ними X рахує спамом
# (на акаунті вже був тіньовий бан), тому такі пости пропускаємо в усіх режимах.
BAIT_PATTERNS = [re.compile(p) for p in [
    # comment "AGENT" / reply with PROMPT / type YES: дієслово будь-яким регістром, кодове слово капсом
    r"(?i:\b(comment|reply|respond|type|drop)\b(\s+(with|below|down|the\s+word|word|me))*)\s*[:\-]?\s*[\"“'‘«(\[]?"
    r"(?!(AI|API|GPT|LLM|JSON|HTML|CSS|SQL|CLI|UI|UX|3D|2D|PDF|URL|ASAP)\b)[A-Z][A-Z0-9_\-]{1,20}\b(?![a-z])",
    r"(?i)\b(comment|reply|type|drop)\b[^.\n]{0,60}\b(i'?ll|i\s+will|and\s+(i|get|receive)|to\s+(get|receive)|for\s+(the|my|a|access))\b",
    r"(?i)\b(i'?ll|i\s+will|we'?ll)\s+(dm|send|message)\s+(you|it|the)\b",
    r"(?i)\b(like|rt|repost|retweet|follow|bookmark)\s*(\+|&|and)\s*(comment|reply|rt|repost|follow)\b",
    r"(?i)\bmust\s+be\s+follow(ing|ed)\b",
    r"(?i)\bfollow\s+(me\s+)?(so|to\s+make\s+sure)\s+i\s+can\s+dm\b",
    r"(?i)\b(link|guide|prompts?|template|access)\s+in\s+(the\s+)?(dms?|comments?)\b",
]]


def is_bait(text):
    return any(p.search(text or "") for p in BAIT_PATTERNS)


def blocked(text, cfg):
    low = (text or "").lower()
    return is_bait(text) or any(w.lower() in low for w in cfg.get("exclude_words", []))


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
    state["reply_seen"] = {k: v for k, v in state.get("reply_seen", {}).items() if v > cutoff}
    json.dump(state, open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def search(query, pages, key, query_type="Top"):
    tweets, cursor = [], ""
    for _ in range(pages):
        params = urllib.parse.urlencode({"query": query, "queryType": query_type, "cursor": cursor})
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


def comment_stats(p, key):
    """Що відбувається в коментарях поста: скільки переглядів збирають чужі реплаї
    і чи це не приманка, де всі пишуть одне кодове слово. 1 запит до twitterapi.io."""
    time.sleep(6)
    try:
        res = http_json(f"{REPLIES_URL}?{urllib.parse.urlencode({'tweetId': p['id']})}", {"X-API-Key": key})
    except Exception as e:  # необов'язково: без цього сповіщення все одно піде
        print("comment stats failed:", e, file=sys.stderr)
        return None
    others = [t for t in res.get("tweets") or []
              if ((t.get("author") or {}).get("userName") or "").lower() != p["user"].lower()]
    if not others:
        return {"n": 0, "median": 0, "top": 0, "bait": False}
    views = [int(t.get("viewCount") or 0) for t in others]
    # Приманка видно з коментарів: багато людей пишуть одне й те саме коротке слово.
    words = []
    for t in others:
        txt = re.sub(r"@\w+", "", t.get("text") or "").strip().lower()
        txt = re.sub(r"[^\w ]", "", txt).strip()
        if txt and len(txt.split()) <= 2:
            words.append(txt)
    same = max((words.count(w) for w in set(words)), default=0)
    return {"n": len(others), "median": int(statistics.median(views)), "top": max(views),
            "bait": same >= 4 and same >= len(others) * 0.3}


def comments_line(cs):
    if not cs or not cs["n"]:
        return None
    return (f"🗣 чужі реплаї тут: медіана ~{fmt_num(cs['median'])}, найкращий {fmt_num(cs['top'])} переглядів "
            f"(з {cs['n']} останніх)")


def takes_off(p, c):
    """Пост уже летить: під ним реплай має шанс на перегляди. Під пости на кількасот переглядів не йдемо."""
    return (p["views"] >= c.get("min_views", 500)
            and (p["views"] >= c.get("hot_views", 5000)
                 or p["per_hour"] >= c.get("min_per_hour", 1000)
                 or p["replies"] >= c.get("min_replies", 30)))


def day_quota_left(state, cfg):
    """Скільки реплай-сповіщень ще можна за сьогоднішній день (за Нью-Йорком)."""
    today = datetime.now(NY).strftime("%Y-%m-%d")
    day = state.setdefault("reply_day", {})
    if day.get("date") != today:
        day.clear()
        day.update({"date": today, "count": 0})
    return cfg.get("max_reply_alerts_per_day", 8) - day["count"]


def count_alert(state):
    state["reply_day"]["count"] += 1


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
    if blocked(p["text"], cfg):
        return False
    return (p["views"] >= cfg["min_views"]
            and p["x_factor"] >= cfg["min_x_factor"]
            and p["followers"] <= cfg["max_followers"])


def age_now_h(p):
    """Вік поста зараз, а не в момент пошуку (для дайджесту і старих записів у пам'яті)."""
    return p["age_h"] + max(time.time() - p.get("ts", time.time()), 0) / 3600


def action_label(p):
    age = age_now_h(p)
    if age < 2:
        return "🟢 <b>РЕПЛАЙ З ВІДЕО ЗАРАЗ</b> (до 2 год)"
    if age < 24:
        return "🟡 <b>МОЖНА КВОТУВАТИ</b> (до 24 год)"
    return "⚪ <b>ЛИШЕ ІДЕЯ</b>, ремейк окремим постом не робити"


def fmt_age(p):
    age = age_now_h(p)
    if age < 1:
        return f"{int(age * 60)} хв"
    return f"{age:.1f} год" if age < 10 else f"{age:.0f} год"


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
        f"Готові відео автора, які ще не публікувались: {'; '.join(cfg.get('ready_videos', [])) or 'немає'}\n\n"
        "Відповідай лише JSON без markdown: "
        '{"fit": true/false, "about": "...", "why": "...", "video": "..."}\n'
        "fit: чи підходить пост під нішу, щоб автор зробив свою версію. "
        "Дивись на опис ніші: що в ньому названо, те підходить.\n"
        "about: українською одне коротке речення, про що пост (до 15 слів).\n"
        "why: українською одне коротке речення, чим саме зачепив (до 15 слів). "
        "Назви конкретний прийом цього поста, без загальних фраз на кшталт «вау-ефект» чи «цікава тема».\n"
        "video: назва одного готового відео зі списку, яке доречно показати в реплаї чи квоті під цим постом "
        "(та сама тема або той самий тип сцени). Якщо жодне не пасує, порожній рядок."
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
                "why": str(out.get("why", "")).strip(), "video": str(out.get("video", "") or "").strip()}
    except Exception as e:  # оцінка необов'язкова, радар не повинен падати через неї
        print("Claude analysis failed:", e, file=sys.stderr)
        return None


def card(p, note=None, variants=None, cs=None):
    note = note or {}
    lines = [
        action_label(p),
        f"⚡ <b>X-ФАКТОР ×{p['x_factor']:g}</b> · ⏱ {fmt_age(p)} · 🚀 {fmt_num(p['per_hour'])}/год",
        f"👁 <b>{fmt_num(p['views'])}</b> переглядів · 💬 {fmt_num(p['replies'])} коментарів",
        f"👤 @{html.escape(p['user'])} · {fmt_num(p['followers'])} підп.",
    ]
    if comments_line(cs):
        lines.append(comments_line(cs))
    lines.append("")
    if note.get("about"):
        lines.append(f"<b>{html.escape(note['about'], quote=False)}</b>")
    else:  # без Claude показуємо перший рядок поста
        lines.append(html.escape(p["text"].split("\n")[0][:140], quote=False))
    if note.get("why"):
        lines.append(f"<i>Чому летить: {html.escape(note['why'], quote=False)}</i>")
    if note.get("video"):
        lines.append(f"🎬 Наше готове відео під це: <b>{html.escape(note['video'], quote=False)}</b>")
    if variants:
        lines += ["", "<b>Варіанти реплаю (тапни, щоб скопіювати):</b>"]
        lines += [f"{i}. <code>{html.escape(v, quote=False)}</code>" for i, v in enumerate(variants, 1)]
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
        variants = suggest_replies(p, cfg, note) if age_now_h(p) < 24 else []
        send(card(p, note, variants), dry)
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
    lines = [f"📡 <b>Радар: топ-{len(top)} за добу</b> (сортування за X-фактором)\n"
             "🟡 ще можна квотувати · ⚪ лише як ідея\n"]
    for i, p in enumerate(top, 1):
        first = html.escape(p["text"].split("\n")[0][:90])
        mark = "🟡" if age_now_h(p) < 24 else "⚪"
        lines.append(f"{i}. {mark} ×{p['x_factor']} · {fmt_num(p['views'])} · @{html.escape(p['user'])} ({fmt_num(p['followers'])})\n"
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


def suggest_replies(p, cfg, note=None):
    """Claude пропонує 4 короткі реплаї англійською. Повертає список рядків або []."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    rw = cfg.get("reply_watch", {})
    if not (key and rw.get("suggest", True)):
        return []
    video = (note or {}).get("video")
    video_hint = (f"Roman may attach his own ready video to the reply: {video}. Make at least one variant work as "
                  "a one-line caption for that video, without claiming anything the video does not show.\n"
                  if video else "")
    prompt = (
        "You write X (Twitter) replies for Roman. He builds 3D scenes, browser games, simulations and short "
        "videos with Claude and other AI models every day.\n\n"
        f"Post by @{p['user']} ({fmt_age(p)} old, {p['views']} views so far):\n{p['text'][:1500]}\n\n"
        f"{video_hint}"
        "Write 4 replies with clearly different angles: (1) a concrete fact, number or second-order "
        "consequence the post misses; (2) a short take from someone who builds with these tools daily; "
        "(3) a sharp, slightly contrarian one-liner; (4) a dry, funny one-liner.\n"
        "Rules: English; one line each, under 120 characters, one idea; sounds like a real person, not a brand; "
        "no hashtags, no emoji, no em dashes, no ALL-CAPS words for emphasis; never 'great post', 'this', 'so true', 'thoughts?', 'agree?'; "
        "no question at the end; do not invent personal results, numbers or events: if a fact is not in the "
        "post and you are not sure it is true, do not state it; no links; no self-promotion.\n"
        'Answer only with JSON, no markdown: {"replies": ["...", "...", "...", "..."]}'
    )
    try:
        res = http_json("https://api.anthropic.com/v1/messages",
                        {"x-api-key": key, "anthropic-version": "2023-06-01"},
                        {"model": rw.get("model", "claude-sonnet-5-5"), "max_tokens": 1500,
                         "thinking": {"type": "disabled"},
                         "messages": [{"role": "user", "content": prompt}]}, timeout=60)
        text = "".join(b.get("text", "") for b in res.get("content", [])).strip()
        out = json.loads(text[text.index("{"):text.rindex("}") + 1])
        return [str(r).strip() for r in out.get("replies", []) if str(r).strip()][:4]
    except Exception as e:  # варіанти необов'язкові, сповіщення піде і без них
        print("reply suggestion failed:", e, file=sys.stderr)
        return []


def reply_card(p, acc, variants, cs=None):
    lines = [
        "✍️ <b>НАПИШИ РЕПЛАЙ ЗАРАЗ</b>",
        f"👤 @{html.escape(p['user'])} · {p['age_min']} хв тому · 👁 {fmt_num(p['views'])} · "
        f"🚀 {fmt_num(p['per_hour'])}/год · 💬 {p['replies']} реплаїв",
    ]
    if acc.get("median"):
        lines.append(f"📈 звичайний пост автора: ~{fmt_num(acc['median'])} переглядів")
    if comments_line(cs):
        lines.append(comments_line(cs))
    lines += ["", html.escape(p["text"][:280], quote=False)]
    if variants:
        lines += ["", "<b>Варіанти (тапни, щоб скопіювати):</b>"]
        lines += [f"{i}. <code>{html.escape(v, quote=False)}</code>" for i, v in enumerate(variants, 1)]
    lines += ["", p["url"]]
    return "\n".join(lines)


def replies_run(cfg, dry):
    """Свіжі пости акаунтів, під якими реплай окупається (список з бази outliers.py)."""
    rw = cfg.get("reply_watch") or {}
    # Лише акаунти, де звичайний пост 10K+: під меншими реплаї 11.11 збирали 3–16 переглядів.
    accounts = {a["user"].lower(): a for a in rw.get("accounts", [])
                if a.get("median", 0) >= rw.get("min_median", 0)}
    if not accounts:
        print("reply_watch.accounts is empty")
        return
    state = load_state()
    gap_min = (time.time() - state.get("reply_last_run", 0)) / 60
    if not dry and gap_min < rw.get("min_minutes_between_runs", 8):
        print(f"skip replies: last run {gap_min:.0f} min ago")
        return
    max_age = rw.get("max_age_min", 45)
    since = int(time.time() - max_age * 60)
    who = " OR ".join(f"from:{a['user']}" for a in accounts.values())
    query = f"({who}) since_time:{since} -filter:replies -filter:retweets"
    seen = state.setdefault("reply_seen", {})
    key = os.environ["TWITTERAPI_IO_KEY"]
    sent = slow = 0
    for t in search(query, 1, key, "Latest"):
        p = score(t)
        acc = accounts.get(p["user"].lower())
        p["age_min"] = int(p["age_h"] * 60)
        if not acc or p["id"] in seen or p["age_min"] > max_age:
            continue
        if blocked(p["text"], cfg):
            seen[p["id"]] = time.time()
            print(f"replies bait/excluded: @{p['user']}: {p['text'][:80]!r}")
            continue
        if not takes_off(p, rw):
            slow += 1  # не позначаємо як побачений: наступний запуск перевірить ще раз, поки пост свіжий
            continue
        if sent >= rw.get("max_alerts_per_run", 6) or day_quota_left(state, cfg) <= 0:
            break
        seen[p["id"]] = time.time()
        cs = comment_stats(p, key)
        if cs and cs["bait"]:
            print(f"replies bait by comments: @{p['user']}")
            continue
        send(reply_card(p, acc, suggest_replies(p, cfg), cs), dry)
        count_alert(state)
        sent += 1
    print(f"replies: sent {sent}, still slow {slow}")
    if not dry:
        state["reply_last_run"] = time.time()
        save_state(state)


def fresh_run(cfg, dry):
    """Пости молодші за 2 год, що швидко ростуть: під ними реплай з нашим відео ще ловить аудиторію оригіналу."""
    fc = cfg.get("fresh") or {}
    if not fc.get("enabled", True) or not fc.get("queries"):
        print("fresh mode is off")
        return
    state = load_state()
    gap_min = (time.time() - state.get("fresh_last_run", 0)) / 60
    if not dry and gap_min < fc.get("min_minutes_between_runs", 45):
        print(f"skip fresh: last run {gap_min:.0f} min ago")
        return
    max_age_h = fc.get("max_age_min", 120) / 60
    since = int(time.time() - max_age_h * 3600)
    found = {}
    for q in fc["queries"]:
        full = f"{q} since_time:{since} -filter:replies -filter:retweets"
        if cfg.get("lang"):
            full += f" lang:{cfg['lang']}"
        try:
            for t in search(full, fc.get("pages_per_query", 1), os.environ["TWITTERAPI_IO_KEY"]):
                p = score(t)
                found[p["id"]] = p
        except Exception as e:
            print(f"Fresh query failed: {q!r}: {e}", file=sys.stderr)
    hot = [p for p in found.values()
           if p["id"] not in state["seen"] and p["age_h"] <= max_age_h
           and takes_off(p, fc)
           and p["followers"] <= fc.get("max_followers", 300000)
           and not blocked(p["text"], cfg)]
    # Живі коментарі теж сигнал: пост із розмовою в коментарях показує реплаї більшій кількості людей.
    hot.sort(key=lambda p: p["per_hour"] + 50 * p["replies"], reverse=True)
    sent = rejected = 0
    for p in hot[: fc.get("max_checks_per_run", 10)]:
        if sent >= fc.get("max_alerts_per_run", 4) or day_quota_left(state, cfg) <= 0:
            break
        note = analyze(p, cfg)
        state["seen"][p["id"]] = time.time()
        if note and not note["fit"]:
            rejected += 1
            print(f"fresh off-niche: @{p['user']}: {p['text'][:80]!r}")
            continue
        cs = comment_stats(p, os.environ["TWITTERAPI_IO_KEY"])
        if cs and cs["bait"]:
            print(f"fresh bait by comments: @{p['user']}")
            continue
        send(card(p, note, suggest_replies(p, cfg, note), cs), dry)
        count_alert(state)
        state["pool"][p["id"]] = p
        state.setdefault("sent", {})[p["id"]] = time.time()
        sent += 1
    print(f"fresh: checked {len(found)}, fast {len(hot)}, off-niche {rejected}, sent {sent}")
    if not dry:
        state["fresh_last_run"] = time.time()
        save_state(state)


def test_bait():
    """Перевірка фільтра приманок без мережі: python radar.py --test-bait"""
    bait = ['Comment "AGENT" and I\'ll send you the full guide', "Reply with PROMPT and I will DM it",
            'Like + comment "3D" to get the file', "Type YES below", "drop a 🔥 and i'll send the prompts",
            "Want the template? Comment CLAUDE", "Must be following so I can DM", "Full prompt in the comments",
            "Comment “blender” and get the workflow"]
    clean = ["give FABLE 5.2 a 3-view drawing and it sculpts your game boss in blender by code FOR FREE",
             "THIS ANIMAL SURVIVED 10 DAYS IN OPEN SPACE. MOST JOBS WON'T SURVIVE 2027",
             "I wrote a game with Claude in one prompt", "I asked it to write AI code and reply in JSON",
             "Say what you want about GPT, the 3D is good", 'Write a prompt like this: "Build..."']
    bad = [t for t in bait if not is_bait(t)] + [t for t in clean if is_bait(t)]
    for t in bad:
        print("WRONG:", t)
    print("bait filter ok" if not bad else f"{len(bad)} wrong")
    sys.exit(1 if bad else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--digest", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--loop", type=int, metavar="MIN")
    ap.add_argument("--preview", type=int, metavar="N")
    ap.add_argument("--replies", action="store_true")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--test-bait", action="store_true")
    args = ap.parse_args()
    if args.test_bait:
        return test_bait()
    load_env()
    cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    if args.digest:
        return digest(cfg, args.dry)
    if args.replies:
        return replies_run(cfg, args.dry)
    if args.fresh:
        return fresh_run(cfg, args.dry)
    if args.preview:
        return preview(cfg, args.preview, args.dry)
    while True:
        run(cfg, args.dry)
        if not args.loop:
            break
        time.sleep(args.loop * 60)


if __name__ == "__main__":
    main()
