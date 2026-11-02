#!/usr/bin/env python3
"""База постів, що вистрілили: X-фактор від норми самого акаунта.

X-фактор тут = перегляди поста / медіана переглядів постів цього акаунта за період.
(У radar.py X-фактор рахується від підписників; тут від звичайного поста автора,
бо саме це показує, що вистрілив пост, а не просто великий акаунт.)

Кроки:
  1. targets  із файлів твітів «насіннєвих» акаунтів беремо, кому вони реплаять
  2. screen   перша сторінка (20 постів) кожного кандидата: чи буває там 10K+
  3. deep     пости за останні N днів для насіння і для тих, хто пройшов скринінг
  4. report   CSV з постами й акаунтами + короткий markdown-звіт

  python outliers.py --out DIR --seeds-dir DIR [--days 30] [--min-target-replies 2]

Кеш по акаунтах лежить у DIR/raw, тож перерваний запуск продовжується з місця зупинки.
Ключ: TWITTERAPI_IO_KEY (змінна середовища або .env поруч). Тільки стандартна бібліотека.
"""
import argparse
import collections
import csv
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.twitterapi.io/twitter/user/last_tweets"
THRESHOLDS = (10, 15, 20)


def load_env():
    path = os.path.join(HERE, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


class Client:
    """Підлаштовує паузу між запитами: на 429 сповільнюється, інакше потроху пришвидшується."""

    def __init__(self, key):
        self.key, self.gap, self.calls = key, 1.0, 0

    def get(self, params):
        url = f"{API}?{urllib.parse.urlencode(params)}"
        for attempt in range(6):
            time.sleep(self.gap)
            try:
                req = urllib.request.Request(url, headers={"X-API-Key": self.key})
                with urllib.request.urlopen(req, timeout=40) as r:
                    self.calls += 1
                    self.gap = max(0.5, self.gap * 0.9)
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    self.gap = min(10, self.gap * 2 + 1)
                    continue
                if e.code in (400, 404):
                    return None
                if attempt == 5:
                    raise
            except (urllib.error.URLError, TimeoutError):
                if attempt == 5:
                    raise
                time.sleep(3)
        return None


def created(t):
    try:
        return parsedate_to_datetime(t["createdAt"])
    except (KeyError, TypeError, ValueError):
        return None


def slim(t):
    a = t.get("author") or {}
    media = ((t.get("extendedEntities") or {}).get("media") or [])
    return {
        "id": t["id"],
        "url": t.get("url"),
        "created": t.get("createdAt"),
        "text": (t.get("text") or "").strip(),
        "views": int(t.get("viewCount") or 0),
        "likes": int(t.get("likeCount") or 0),
        "reposts": int(t.get("retweetCount") or 0),
        "quotes": int(t.get("quoteCount") or 0),
        "replies": int(t.get("replyCount") or 0),
        "bookmarks": int(t.get("bookmarkCount") or 0),
        "is_quote": bool(t.get("quoted_tweet")),
        "is_article": bool(t.get("article")),
        "media": sorted({m.get("type", "?") for m in media}),
        "followers": int(a.get("followers") or 0),
        "lang": t.get("lang"),
    }


def fetch_posts(client, user, days, max_pages):
    """Власні пости й квоти акаунта за days днів, без реплаїв і ретвітів."""
    since = datetime.now(timezone.utc).timestamp() - days * 86400
    posts, cursor, followers = {}, "", None
    for _ in range(max_pages):
        res = client.get({"userName": user, "includeReplies": "false", "cursor": cursor})
        if not res:
            break
        data = res.get("data") or {}
        tweets = data.get("tweets") or []
        if not tweets:
            break
        oldest = None
        for t in tweets:
            c = created(t)
            if c:
                oldest = c if oldest is None or c < oldest else oldest
            if t.get("retweeted_tweet") or t.get("isReply"):
                continue
            if (t.get("author") or {}).get("userName", "").lower() != user.lower():
                continue
            if c and c.timestamp() < since:
                continue
            p = slim(t)
            followers = followers or p["followers"]
            posts[p["id"]] = p
        if oldest and oldest.timestamp() < since:
            break
        if not res.get("has_next_page"):
            break
        cursor = res.get("next_cursor", "")
    return {"user": user, "fetched": time.time(), "followers": followers, "posts": list(posts.values())}


def cached(out, stage, user, fn):
    path = os.path.join(out, "raw", stage, f"{user}.json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = fn()
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return data


def reply_targets(seeds_dir, seeds):
    """Кому і скільки разів реплаїть кожен seed (із збережених tweets.json)."""
    per = {}
    for s in seeds:
        path = os.path.join(seeds_dir, s, "tweets.json")
        if not os.path.exists(path):
            print(f"no tweets.json for {s}, skip its targets", file=sys.stderr)
            continue
        c = collections.Counter(
            t["inReplyToUsername"] for t in json.load(open(path, encoding="utf-8"))
            if t.get("isReply") and t.get("inReplyToUsername")
            and t["inReplyToUsername"].lower() != s.lower())
        per[s] = c
    total = collections.defaultdict(lambda: {"replies": 0, "by": []})
    for s, c in per.items():
        for u, n in c.items():
            total[u]["replies"] += n
            total[u]["by"].append(s)
    return total


def account_stats(user, posts, settle_h=24):
    """Медіана рахується по постах, старших за settle_h годин: свіжі ще ростуть."""
    now = datetime.now(timezone.utc)
    settled = [p for p in posts
               if (now - parsedate_to_datetime(p["created"])).total_seconds() > settle_h * 3600]
    base = settled if len(settled) >= 5 else posts
    if len(base) < 3:
        return None
    med = statistics.median(p["views"] for p in base) or 1
    for p in posts:
        p["x"] = round(p["views"] / max(med, 50), 1)
        p["age_h"] = round((now - parsedate_to_datetime(p["created"])).total_seconds() / 3600, 1)
    views = sorted(p["views"] for p in base)
    return {
        "user": user,
        "posts": len(posts),
        "median_views": int(med),
        "p90_views": views[int(0.9 * (len(views) - 1))],
        "max_views": max(p["views"] for p in posts),
        "share_10k": round(sum(p["views"] >= 10_000 for p in base) / len(base), 2),
        **{f"x{t}": sum(p["x"] >= t for p in posts) for t in THRESHOLDS},
        "hits_10k": sum(p["x"] >= 10 and p["views"] >= 10_000 for p in posts),
        "posts_per_day": None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seeds-dir", required=True)
    ap.add_argument("--seeds", default="coolerwave,Bober_smart,starmexxx,Cranefomo,make_claude")
    ap.add_argument("--extra", default="", help="ще акаунти через кому, одразу в глибокий збір")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-target-replies", type=int, default=2)
    ap.add_argument("--screen-min-views", type=int, default=10_000,
                    help="у глибокий збір іде акаунт, у якого на першій сторінці є пост з такими переглядами")
    ap.add_argument("--max-pages", type=int, default=15)
    ap.add_argument("--limit", type=int, default=0, help="тест: лише перші N кандидатів")
    args = ap.parse_args()
    load_env()
    client = Client(os.environ["TWITTERAPI_IO_KEY"])
    os.makedirs(args.out, exist_ok=True)

    seeds = [s.strip() for s in args.seeds.split(",") if s.strip()]
    extra = [s.strip() for s in args.extra.split(",") if s.strip()]
    targets = reply_targets(args.seeds_dir, seeds)
    seed_low = {s.lower() for s in seeds}
    cands = sorted((u for u, v in targets.items()
                    if v["replies"] >= args.min_target_replies and u.lower() not in seed_low),
                   key=lambda u: -targets[u]["replies"])
    if args.limit:
        cands = cands[: args.limit]
    print(f"{len(targets)} reply targets, {len(cands)} with >= {args.min_target_replies} replies")

    # 2. скринінг
    deep = list(dict.fromkeys(seeds + extra))
    screen_rows = []
    for i, u in enumerate(cands, 1):
        d = cached(args.out, "screen", u, lambda: fetch_posts(client, u, args.days, 1))
        top = max((p["views"] for p in d["posts"]), default=0)
        screen_rows.append({"user": u, "followers": d["followers"], "page1_posts": len(d["posts"]),
                            "page1_max_views": top, "seed_replies": targets[u]["replies"],
                            "replied_by": " ".join(targets[u]["by"])})
        if top >= args.screen_min_views:
            deep.append(u)
        if i % 20 == 0:
            print(f"screen {i}/{len(cands)} · calls {client.calls} · gap {client.gap:.1f}s", flush=True)
    deep = list(dict.fromkeys(deep))
    print(f"deep fetch: {len(deep)} accounts")

    # 3. глибокий збір і 4. звіт
    accounts, all_posts = [], []
    for i, u in enumerate(deep, 1):
        d = cached(args.out, f"deep{args.days}", u, lambda: fetch_posts(client, u, args.days, args.max_pages))
        st = account_stats(u, d["posts"])
        if not st:
            continue
        st["followers"] = d["followers"]
        st["role"] = "seed" if u in seeds else ("extra" if u in extra else "target")
        st["seed_replies"] = targets.get(u, {}).get("replies", 0)
        st["replied_by"] = " ".join(targets.get(u, {}).get("by", []))
        ages = [p["age_h"] for p in d["posts"]]
        st["posts_per_day"] = round(len(ages) / max(max(ages) / 24, 1), 1)
        accounts.append(st)
        for p in d["posts"]:
            all_posts.append({"user": u, "followers": d["followers"], "median": st["median_views"], **p})
        if i % 10 == 0:
            print(f"deep {i}/{len(deep)} · calls {client.calls}", flush=True)

    accounts.sort(key=lambda a: (-a["hits_10k"], -a["x10"], -a["median_views"]))
    write_csv(os.path.join(args.out, "accounts.csv"), accounts)
    write_csv(os.path.join(args.out, "screen.csv"), screen_rows)
    rows = [{**p, "media": "+".join(p["media"]), "text": p["text"].replace("\n", " ")[:400]} for p in all_posts]
    rows.sort(key=lambda p: -p["x"])
    write_csv(os.path.join(args.out, "posts.csv"), rows)
    write_csv(os.path.join(args.out, "hits.csv"), [r for r in rows if r["x"] >= 10 and r["views"] >= 10_000])
    print(f"done: {len(accounts)} accounts, {len(rows)} posts, "
          f"{sum(r['x'] >= 10 and r['views'] >= 10_000 for r in rows)} hits; API calls {client.calls}")


def write_csv(path, rows):
    if not rows:
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
