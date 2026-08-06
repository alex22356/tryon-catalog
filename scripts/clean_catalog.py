"""
Чистка каталога: то, что определяется достоверно, без догадок.

Что делает:
  1. помечает детские вещи (Junior/Kids в названии) — им не место во взрослом каталоге;
  2. вытаскивает пол из ТЕКСТА ОПИСАНИЯ ленты Awin ("The Women's Nelson Sweatshirt...").
     Колонка description в ingest_awin.py не читается вообще, а пол там иногда есть.

Чего НЕ делает и почему:
  * не выводит пол по бренду. Проверено: при строгих порогах правило накрывает
    НОЛЬ неопределённых позиций — все они у марок вроде Columbia и Brave Soul,
    которые честно выпускают обе линейки. Мягкие пороги давали 111 правок,
    но все они были догадками, а метки-основания сами получены эвристикой
    ("размер UK <= 8 значит женский") — ошибка усиливала бы саму себя.
  * не ходит на сайт DV8: он за Azure WAF с JS-проверкой, обход не делаем.

Остаток закрывается распознаванием по фото товара — см. classify_by_photo.py.

    python scripts/clean_catalog.py            # показать
    python scripts/clean_catalog.py --apply    # записать (с резервной копией)
"""
import argparse
import collections
import csv
import gzip
import html
import json
import os
import re
import shutil
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEED_URL_FILE = os.path.join(ROOT, "feed_url.txt")
FEED_CACHE = os.path.join(os.environ.get("TEMP", "/tmp"), "dv8_feed.csv.gz")

WORD = lambda *w: re.compile(r"\b(" + "|".join(w) + r")\b", re.I)
# «baby tee» и «babydoll» — взрослые женские фасоны, поэтому голого «baby» нет
KIDS = WORD("junior", "kids", "childrens", "infant", "toddler")
FEMALE = WORD("women", "womens", "woman", "ladies", "female")
MALE = WORD("men", "mens", "man", "male", "gents")


def load_json(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return d, (d if isinstance(d, list) else (d.get("items") or d.get("products") or []))


def get_feed():
    """Строки фида по merchant id. Фид кешируется во временной папке."""
    if not os.path.exists(FEED_CACHE) or os.path.getsize(FEED_CACHE) < 1000:
        if not os.path.exists(FEED_URL_FILE):
            print("  фида нет и ссылка не найдена — шаг с описаниями пропускаю")
            return {}
        url = re.search(r"(https?://\S+)", open(FEED_URL_FILE, encoding="utf-8",
                                                errors="ignore").read()).group(1)
        print("  качаю фид Awin...")
        urllib.request.urlretrieve(url, FEED_CACHE)

    by_id = {}
    with gzip.open(FEED_CACHE, "rt", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f, delimiter="|"):   # разделитель именно '|'
            m = re.search(r"/m-(\d+)\.aspx", row.get("merchant_deep_link") or "")
            if m and m.group(1) not in by_id:
                by_id[m.group(1)] = row
    return by_id


def gender_from_description(row):
    if not row:
        return None
    # в фиде мнемоники: Women&apos;s -> Women's
    desc = html.unescape(row.get("description") or "")
    f, m = bool(FEMALE.search(desc)), bool(MALE.search(desc))
    if f and not m:
        return "female"
    if m and not f:
        return "male"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="*",
                    default=[os.path.join("public", "catalog.json"), "dv8_products.json"])
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    main_path = os.path.join(ROOT, a.files[0])
    _, items = load_json(main_path)
    print(f"каталог: {len(items)} позиций")

    feed = get_feed()
    print(f"строк фида: {len(feed)}")

    decisions = {}          # id -> новый пол
    stats = collections.Counter()
    samples = collections.defaultdict(list)

    for it in items:
        name = it.get("name") or ""
        cur = it.get("gender")

        if KIDS.search(name):
            decisions[it["id"]] = "kids"
            stats["детские"] += 1
            samples["детские"].append(name)
            continue

        if cur in ("male", "female"):
            continue

        mid = str(it.get("id", "")).replace("dv8_", "")
        g = gender_from_description(feed.get(mid))
        if g:
            decisions[it["id"]] = g
            stats[f"по описанию -> {g}"] += 1
            samples["описание"].append(f"{g}: {name}")

    print("\n--- решения ---")
    for k, v in stats.most_common():
        print(f"  {k:24s} {v}")
    print(f"  ИТОГО: {len(decisions)}")

    for key in ("детские", "описание"):
        if samples[key]:
            print(f"\n  примеры ({key}):")
            for s in samples[key][:5]:
                print(f"    {s[:70]}")

    left = sum(1 for it in items
               if it.get("gender") not in ("male", "female") and it["id"] not in decisions)
    print(f"\n  останется без пола: {left} — только распознавание по фото")

    if not a.apply:
        print("\n(показ без записи — добавь --apply)")
        return

    for rel in a.files:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        backup = path + ".bak"
        if not os.path.exists(backup):
            shutil.copy(path, backup)
        doc, rows = load_json(path)
        n = 0
        for it in rows:
            if it.get("id") in decisions:
                it["gender"] = decisions[it["id"]]
                n += 1
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        print(f"обновлён {rel}: {n} позиций (копия в {os.path.basename(backup)})")


if __name__ == "__main__":
    main()
