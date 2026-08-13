"""
Проверяет, что картинка каждого товара действительно открывается.

Зачем: в ленте попадаются пустые карточки — цена есть, изображения нет.
Человек видит дыру и не понимает, что это. Причина обычно в том, что магазин
снял фото, а строка в фиде осталась.

Товары с битой картинкой лучше не показывать вовсе, чем показывать пустыми.

    python scripts/check_images.py            # проверить всё
    python scripts/check_images.py --limit 200

Пишет broken_images.json со списком неоткрывшихся. Ничего не меняет.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "public", "catalog.json")
OUT = os.path.join(ROOT, "broken_images.json")
UA = {"User-Agent": "Mozilla/5.0"}

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def check(url):
    """Возвращает None если всё хорошо, иначе причину."""
    try:
        req = urllib.request.Request(url, headers=UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=25) as r:
            if r.status != 200:
                return f"HTTP {r.status}"
            size = int(r.headers.get("Content-Length") or 0)
            # Совсем крошечный файл — это заглушка «нет фото», а не товар
            if 0 < size < 1500:
                return f"пустышка, {size} байт"
            return None
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except Exception as e:
        return type(e).__name__


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    items = json.load(open(CATALOG, encoding="utf-8"))["items"]
    if a.limit:
        items = items[:a.limit]
    print(f"проверяем картинок: {len(items)}", flush=True)

    bad, done, t0 = [], 0, time.time()
    with ThreadPoolExecutor(max_workers=12) as pool:
        for it, why in zip(items, pool.map(lambda i: check(i["imageUrl"]), items)):
            done += 1
            if why:
                bad.append({"id": it["id"], "name": it["name"], "why": why,
                            "url": it["imageUrl"], "category": it["category"]})
            if done % 400 == 0:
                el = time.time() - t0
                print(f"  {done}/{len(items)}  битых {len(bad)}  "
                      f"осталось ~{(len(items)-done)*el/done/60:.0f} мин", flush=True)

    json.dump(bad, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nбитых картинок: {len(bad)} из {len(items)}")
    for b in bad[:25]:
        print(f"  {b['id']:<16} {b['why']:<16} {b['name'][:52]}")
    print(f"\nсписок: {OUT}")


if __name__ == "__main__":
    main()
