#!/usr/bin/env python3
"""
Ингест товаров SHEIN по affiliate-ссылкам (onelink) — ЭТАП 1 конвейера каталога.

Что делает:
  - читает shein_links.txt (по одной onelink-ссылке на строку, # = коммент);
  - для КАЖДОЙ НОВОЙ ссылки тянет со страницы og:title (имя) и og:image (фото вещи);
  - чистит имя, определяет категорию (TOP/BOTTOM/FULL_BODY/FOOTWEAR);
  - качает фото-референс вещи в garments/<id>.jpg;
  - копит всё в shein_products.json (инкрементально: уже собранное пропускает).

Дальше garments/*.jpg идут в ЭТАП 2 (примерка: Gemini / CatVTON / API) → «надетые» фото,
которые становятся imageUrl/overlayUrl товара в каталоге приложения.

Запуск:  python scripts/ingest_shein.py
Зависимости:  pip install requests
"""
import json
import os
import re
import sys
import html
import time
import requests

# Windows-консоль по умолчанию cp1252 — принудительно UTF-8, чтобы кириллица в выводе не падала.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINKS = os.path.join(HERE, "shein_links.txt")
OUT_JSON = os.path.join(HERE, "shein_products.json")
GARMENTS = os.path.join(HERE, "garments")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# порядок важен: обувь/платья/низ проверяем ДО общего TOP
CATEGORY_RULES = [
    ("FOOTWEAR", ["boot", "shoe", "sneaker", "heel", "sandal", "loafer", "flats", "ballet"]),
    ("FULL_BODY", ["dress", "gown", "jumpsuit", "romper", "bodysuit", "2pcs", "2 pcs", " set,", " set "]),
    ("BOTTOM", ["pants", "trouser", "jeans", "skirt", "shorts", "legging", "palazzo", "culotte"]),
    ("TOP", ["shirt", "tee", "t-shirt", "top", "cardigan", "jacket", "blouse", "sweater",
             "hoodie", "coat", "cami", "tank", "peplum", "vest"]),
]


def code_of(url: str) -> str:
    m = re.search(r"/([0-9a-z]+)(?:\?|$)", url.strip())
    return m.group(1) if m else re.sub(r"\W+", "", url)[-12:]


def categorize(name: str) -> str:
    n = name.lower()
    for cat, keys in CATEGORY_RULES:
        if any(k in n for k in keys):
            return cat
    return "TOP"  # разумный дефолт


def clean(s: str) -> str:
    return html.unescape(s).replace("–", "-").strip()


def bigger(url: str) -> str:
    # апгрейд превью до крупного размера, если это ltwebstatic
    return re.sub(r"_thumbnail_\d+x\d*", "_thumbnail_900x", url)


def fetch(url: str):
    r = requests.get(url, headers=UA, timeout=25)
    t = r.text
    title = re.search(r'og:title" content="([^"]*)"', t)
    img = re.search(r'og:image" content="([^"]*)"', t)
    if not (title and img):
        return None
    return clean(title.group(1)), img.group(1)


def read_links():
    out = []
    for line in open(LINKS, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def main():
    os.makedirs(GARMENTS, exist_ok=True)
    products = {}
    if os.path.exists(OUT_JSON):
        for p in json.load(open(OUT_JSON, encoding="utf-8")):
            products[p["id"]] = p

    links = read_links()
    added = 0
    for url in links:
        pid = "shein_" + code_of(url)
        if pid in products:
            continue
        try:
            got = fetch(url)
            if not got:
                print("SKIP (нет og-тегов):", url)
                continue
            name, img = got
            img = bigger(img)
            cat = categorize(name)
            # качаем референс вещи
            gpath = os.path.join(GARMENTS, pid + ".jpg")
            data = requests.get(img, headers=UA, timeout=25).content
            open(gpath, "wb").write(data)
            products[pid] = {
                "id": pid,
                "name": name[:80],
                "category": cat,
                "garmentRef": img,          # фото вещи (для этапа примерки)
                "productUrl": url,          # партнёрская ссылка «Открыть в магазине»
                "store": "SHEIN",
                # imageUrl / overlayUrl проставятся после этапа 2 (примерка)
            }
            added += 1
            print(f"+ {pid}  [{cat}]  {name[:50]}")
            time.sleep(0.3)
        except Exception as e:
            print("ERR", url, e)

    items = list(products.values())
    json.dump(items, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    by_cat = {}
    for p in items:
        by_cat[p["category"]] = by_cat.get(p["category"], 0) + 1
    print(f"\nГотово. Новых: {added}. Всего: {len(items)}. По категориям: {by_cat}")
    print(f"JSON: {OUT_JSON}\nФото вещей: {GARMENTS}")


if __name__ == "__main__":
    main()
