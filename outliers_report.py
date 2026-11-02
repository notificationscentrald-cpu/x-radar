#!/usr/bin/env python3
"""Розкладає результат outliers.py по темах і хуках та збирає Excel-базу.

  python outliers_report.py DIR      (DIR = той самий --out, що в outliers.py)

Читає DIR/posts.csv, DIR/accounts.csv, DIR/article_titles.json (необов'язково),
пише DIR/x-factor-base.xlsx і DIR/summary.json. Потрібен openpyxl.
"""
import collections
import csv
import json
import os
import re
import statistics
import sys

# Перша тема, що збіглась, перемагає, тож порядок важливий.
TOPICS = [
    ("18+ біографії", r"p\*?o?rn|adult star|blowjob|\bcum\b|strip club|stripping|onlyfans|slept with \d|loads in"),
    ("кримінал", r"sentence|death row|murder|prison|san quentin"),
    ("страшилка: телефон/приватність", r"photos?\b|spyware|hidden camera|airbnb|hotel|door lock|smoke detector|listening|"
                                       r"tracking|deleted|\btv\b|router|wi-?fi|watched|camera roll|snitch|virus|backdoor|"
                                       r"teddy bear|private|our faces"),
    ("гроші: AI-трейдинг і заробіток", r"trad|polymarket|profit|pnl|wallet|hedge fund|quant|make money|\$[\d,.]+k?\s*(→|->|into|to)\s*\$|"
                                       r"/month|a month|binance|pump\.fun|\$uni|made \$|first \$"),
    ("Grok Bot (продукт)", r"grok ?bot"),
    ("моделі / 3D / ігри з коду", r"\bvs\.?\b|same prompt|3d|jelly|\bgame|octopus|pixel|unity|unreal|motion|animation|"
                                  r"simulator|gummy|made .* in (grok|gemini|claude)"),
    ("гайди, репо, промпти", r"github|repos?\b|course|guide|second brain|harness|plugin|skill|prompt|karpathy|loop|"
                             r"engineering|token|setup|how to"),
    ("новини / цитати відомих", r"breaking|said|just dropped|elon|dario|update|announced|released|leaked"),
]
OFF_NICHE = {"18+ біографії", "кримінал"}

HOOKS = [
    ("«it is becoming f*cking dangerous»", r"^(it'?s|it is|this is) (becoming|getting) f\*?\w*ing dangerous"),
    ("«holy sh*t / what the f*ck»", r"^(holy|what the|wtf|brooo|damn)"),
    ("«this is f*cking insane/wild/gold»", r"^(this is|that'?s|f\*?\w*ing) .{0,20}(insane|wild|gold|crazy)"),
    ("«BREAKING / 🚨»", r"^(🚨|breaking|braking)"),
    ("«Claude Code (just) found…»", r"^(claude( code)?|grok bot|elon musk's grok bot) (just )?(found|caught|traced|detected|upgraded|turned|reverse)"),
    ("«I gave AI $X…» / «$X → $Y»", r"^(i gave|i just crossed|i made|my first|\$\d)|→ \$"),
    ("«Someone needs to stop…» / «I'm scared of…»", r"someone needs to stop|scared of|shaking|no way this is legal|bigger balls"),
    ("історія людини (вік, роль)", r"^(a |an |at |he |she |ex[- .]|\w+ was \d|\w+ \w+ was \d|my friend|ken griffin|andrej)"),
]


def topic_of(text):
    low = text.lower()
    for name, rx in TOPICS:
        if re.search(rx, low):
            return name
    return "інше"


def hook_of(text):
    low = text.strip().lower()
    for name, rx in HOOKS:
        if re.search(rx, low):
            return name
    return "інший хук"


def main(out):
    posts = list(csv.DictReader(open(os.path.join(out, "posts.csv"), encoding="utf-8")))
    accounts = list(csv.DictReader(open(os.path.join(out, "accounts.csv"), encoding="utf-8")))
    titles = {}
    tp = os.path.join(out, "article_titles.json")
    if os.path.exists(tp):
        titles = {k: v.get("title") or "" for k, v in json.load(open(tp, encoding="utf-8")).items()}

    for p in posts:
        for k in ("views", "likes", "reposts", "quotes", "replies", "bookmarks", "followers", "median"):
            p[k] = int(p[k] or 0)
        p["x"] = float(p["x"])
        p["title"] = titles.get(p["id"], "")
        full = (p["title"] + " " + p["text"]).strip()
        p["format"] = ("стаття" if p["is_article"] == "True" else
                       "відео" if "video" in p["media"] else
                       "фото" if "photo" in p["media"] else "текст")
        p["topic"] = topic_of(full)
        p["hook"] = hook_of(p["title"] or p["text"])
        p["hit"] = p["x"] >= 10 and p["views"] >= 10_000

    by_user = collections.defaultdict(list)
    for p in posts:
        by_user[p["user"]].append(p)
    for a in accounts:
        for k in ("posts", "median_views", "p90_views", "max_views", "x10", "x15", "x20", "hits_10k",
                  "seed_replies", "followers"):
            a[k] = int(float(a[k] or 0))
        a["share_10k"] = float(a["share_10k"])
        a["posts_per_day"] = float(a["posts_per_day"])
        ps = by_user[a["user"]]
        hits = [p for p in ps if p["hit"]]
        tc = collections.Counter(p["topic"] for p in (hits or ps))
        a["main_topic"] = tc.most_common(1)[0][0] if tc else ""
        a["off_niche"] = sum(tc[t] for t in OFF_NICHE) > sum(tc.values()) / 2
        # Скільки постів на 10K+ цей автор дає за день: стільки шансів на реплай, що окупиться.
        a["posts_10k_per_day"] = round(a["share_10k"] * a["posts_per_day"], 2)
        a["link"] = f"https://x.com/{a['user']}"

    hits = sorted((p for p in posts if p["hit"]), key=lambda p: -p["x"])
    rest = [p for p in posts if not p["hit"]]

    def share(rows, key):
        c = collections.Counter(r[key] for r in rows)
        n = len(rows) or 1
        return {k: round(v / n * 100) for k, v in c.most_common()}

    summary = {
        "accounts": len(accounts), "posts": len(posts), "hits": len(hits),
        "hit_topics": share(hits, "topic"), "rest_topics": share(rest, "topic"),
        "hit_formats": share(hits, "format"), "rest_formats": share(rest, "format"),
        "hit_hooks": share(hits, "hook"), "rest_hooks": share(rest, "hook"),
        "hit_quote_pct": round(sum(p["is_quote"] == "True" for p in hits) / max(len(hits), 1) * 100),
        "rest_quote_pct": round(sum(p["is_quote"] == "True" for p in rest) / max(len(rest), 1) * 100),
        "hit_rate_by_format": {f: round(sum(p["hit"] for p in posts if p["format"] == f) /
                                        max(sum(p["format"] == f for p in posts), 1) * 100)
                               for f in ("відео", "фото", "стаття", "текст")},
        "hit_rate_by_topic": {t: round(sum(p["hit"] for p in posts if p["topic"] == t) /
                                       max(sum(p["topic"] == t for p in posts), 1) * 100)
                              for t in [n for n, _ in TOPICS] + ["інше"]},
        "median_x_hits": statistics.median(p["x"] for p in hits) if hits else 0,
    }
    json.dump(summary, open(os.path.join(out, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    write_xlsx(out, accounts, hits, posts, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def write_xlsx(out, accounts, hits, posts, summary):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="1F3A5F")

    def sheet(ws, cols, rows, widths):
        ws.append([c[0] for c in cols])
        for c in ws[1]:
            c.font, c.fill = head, fill
            c.alignment = Alignment(wrap_text=True, vertical="top")
        for r in rows:
            ws.append([c[1](r) for c in cols])
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = ws.dimensions
        for row in ws.iter_rows(min_row=2):
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("https://"):
                    c.hyperlink = c.value
                    c.font = Font(color="1F5FBF", underline="single")

    # 1. Де реплаїти: лише ніша, без гігантів (1M+), де реплай тоне серед тисяч інших,
    # сортування за кількістю постів 10K+ на день.
    ws = wb.active
    ws.title = "Де реплаїти"
    reply = sorted((a for a in accounts
                    if not a["off_niche"] and a["role"] != "seed" and a["followers"] < 1_000_000),
                   key=lambda a: -a["posts_10k_per_day"])
    acc_cols = [
        ("Акаунт", lambda a: "@" + a["user"]),
        ("Постів 10K+ на день", lambda a: a["posts_10k_per_day"]),
        ("Частка постів 10K+", lambda a: a["share_10k"]),
        ("Медіана переглядів", lambda a: a["median_views"]),
        ("Постів на день", lambda a: a["posts_per_day"]),
        ("Хітів ×10 і 10K+ за 30 днів", lambda a: a["hits_10k"]),
        ("×10+", lambda a: a["x10"]), ("×15+", lambda a: a["x15"]), ("×20+", lambda a: a["x20"]),
        ("Макс. переглядів", lambda a: a["max_views"]),
        ("Підписники", lambda a: a["followers"]),
        ("Головна тема хітів", lambda a: a["main_topic"]),
        ("Реплаїв від розібраних акаунтів", lambda a: a["seed_replies"]),
        ("Хто з них реплаїть", lambda a: a["replied_by"]),
        ("Посилання", lambda a: a["link"]),
    ]
    sheet(ws, acc_cols, reply, [18, 12, 11, 12, 10, 12, 7, 7, 7, 12, 11, 30, 12, 30, 30])

    # 2. Фабрики хітів: усі акаунти, сортування за кількістю хітів.
    ws = wb.create_sheet("Фабрики хітів")
    fact = sorted(accounts, key=lambda a: (-a["hits_10k"], -a["x20"]))
    sheet(ws, [("Роль", lambda a: {"seed": "розібраний", "target": "кому реплаять", "extra": "доданий"}[a["role"]]),
               ("Не ніша", lambda a: "так" if a["off_niche"] else "")] + acc_cols,
          fact, [12, 8, 18, 12, 11, 12, 10, 12, 7, 7, 7, 12, 11, 30, 12, 30, 30])

    # 3. Хіти: пости ×10+ від медіани автора і 10K+ переглядів.
    post_cols = [
        ("X від медіани", lambda p: p["x"]),
        ("Акаунт", lambda p: "@" + p["user"]),
        ("Перегляди", lambda p: p["views"]),
        ("Медіана автора", lambda p: p["median"]),
        ("Тема", lambda p: p["topic"]),
        ("Хук", lambda p: p["hook"]),
        ("Формат", lambda p: p["format"]),
        ("Квота", lambda p: "так" if p["is_quote"] == "True" else ""),
        ("Текст / назва статті", lambda p: (p["title"] + " · " if p["title"] else "") + p["text"][:300]),
        ("Лайки", lambda p: p["likes"]), ("Закладки", lambda p: p["bookmarks"]),
        ("Репости", lambda p: p["reposts"]), ("Відповіді", lambda p: p["replies"]),
        ("Дата", lambda p: p["created"][4:10] + " " + p["created"][-4:]),
        ("Посилання", lambda p: p["url"]),
    ]
    ws = wb.create_sheet("Хіти")
    sheet(ws, post_cols, hits, [9, 16, 11, 10, 26, 26, 8, 6, 80, 8, 9, 8, 9, 12, 30])
    for row in ws.iter_rows(min_row=2, min_col=9, max_col=9):
        row[0].alignment = Alignment(wrap_text=True, vertical="top")

    # 4. Усі пости.
    ws = wb.create_sheet("Усі пости")
    sheet(ws, post_cols, sorted(posts, key=lambda p: -p["x"]), [9, 16, 11, 10, 26, 26, 8, 6, 80, 8, 9, 8, 9, 12, 30])

    # 5. Що стріляє: частки тем, форматів, хуків серед хітів і решти.
    ws = wb.create_sheet("Що стріляє")
    ws.append(["Розріз", "Значення", "% серед хітів", "% серед решти", "Шанс стати хітом, %"])
    for c in ws[1]:
        c.font, c.fill = head, fill
    for key, rate in (("topics", "hit_rate_by_topic"), ("formats", "hit_rate_by_format"), ("hooks", None)):
        label = {"topics": "Тема", "formats": "Формат", "hooks": "Хук"}[key]
        names = list(dict.fromkeys(list(summary[f"hit_{key}"]) + list(summary[f"rest_{key}"])))
        for n in names:
            ws.append([label, n, summary[f"hit_{key}"].get(n, 0), summary[f"rest_{key}"].get(n, 0),
                       summary[rate].get(n, "") if rate else ""])
    ws.append(["Квота", "пост квотує інший пост", summary["hit_quote_pct"], summary["rest_quote_pct"], ""])
    for i, w in enumerate([10, 40, 14, 14, 18], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    path = os.path.join(out, "x-factor-base.xlsx")
    wb.save(path)
    print("saved", path)


if __name__ == "__main__":
    main(sys.argv[1])
