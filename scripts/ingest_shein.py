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
import urllib.parse
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
    """ID товара. Для прямых ссылок — настоящий goods_id, для onelink — его код."""
    url = url.strip()
    m = re.search(r"-p-(\d+)\.html", url)       # https://us.shein.com/...-p-482098838.html
    if m:
        return m.group(1)
    m = re.search(r"/([0-9a-z]+)(?:\?|$)", url)  # https://onelink.shein.com/45/5x4po236q8lx
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


# Маркетинговые заголовки, которые onelink иногда отдаёт вместо названия товара
GENERIC_TITLE = ("snag these", "must-haves", "shop online", "women's & men's",
                 "free shipping", "% off", "shein before")


def name_from_slug(url: str) -> str:
    """Название из адреса товара: .../Women-s-Round-Neck-...-p-135097572.html"""
    m = re.search(r"/([^/]+?)-p-\d+", url)
    if not m:
        return ""
    s = m.group(1)
    for _ in range(2):                      # адрес бывает двойного кодирования
        s = urllib.parse.unquote(s)
    s = s.replace("-", " ").replace("  ", " ").strip()
    s = re.sub(r"\bWomen s\b", "Women's", s)
    return s[:80]


def fetch(url: str):
    """Возвращает (name, image, goods_id) — goods_id может быть None."""
    r = requests.get(url, headers=UA, timeout=25)
    t = r.text
    title = re.search(r'og:title" content="([^"]*)"', t)
    img = re.search(r'og:image" content="([^"]*)"', t)
    if not (title and img):
        return None

    name = clean(title.group(1))
    gid = None
    # onelink прячет внутри и id товара, и его настоящий адрес
    m = re.search(r"-p-(\d+)", t)
    if m:
        gid = m.group(1)

    # заголовок маркетинговый → берём название из адреса товара
    if any(k in name.lower() for k in GENERIC_TITLE):
        target = re.search(r"https://[a-z]{2}\.shein\.com/[^\"'\\ ]*?-p-\d+", t)
        slug_name = name_from_slug(target.group(0)) if target else ""
        if slug_name:
            name = clean(slug_name)

    return name, img.group(1), gid


def read_links():
    out = []
    for line in open(LINKS, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def load_products():
    if not os.path.exists(OUT_JSON):
        return {}
    return {p["id"]: p for p in json.load(open(OUT_JSON, encoding="utf-8"))}


def save_products(products):
    json.dump(list(products.values()), open(OUT_JSON, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


def ingest_extracted(records):
    """
    Товары, данные которых УЖЕ сняты со страницы в браузере
    (закладка «Собрать товары»): [{id, name, img, url}].
    Никаких запросов к страницам SHEIN — качаем только фото с их CDN.
    """
    os.makedirs(GARMENTS, exist_ok=True)
    products = load_products()
    added = 0
    for r in records:
        gid = str(r.get("id") or "").strip()
        name = clean(str(r.get("name") or ""))
        img = str(r.get("img") or "").strip()
        if not (gid and name and img):
            continue
        pid = "shein_" + gid
        if pid in products:
            continue
        img = bigger(img)
        cat = categorize(name)
        try:
            data = requests.get(img, headers=UA, timeout=25).content
            if len(data) < 2000:
                raise ValueError("слишком маленький файл")
            open(os.path.join(GARMENTS, pid + ".jpg"), "wb").write(data)
        except Exception as e:
            print(f"  ! фото не скачалось {pid}: {str(e)[:50]}")
            continue
        products[pid] = {
            "id": pid,
            "name": name[:80],
            "category": cat,
            "garmentRef": img,
            "productUrl": r.get("url") or f"https://us.shein.com/-p-{gid}.html",
            "store": "SHEIN",
        }
        added += 1
        print(f"+ {pid}  [{cat}]  {name[:46]}")
    save_products(products)
    return added


def main():
    os.makedirs(GARMENTS, exist_ok=True)
    products = {}
    if os.path.exists(OUT_JSON):
        for p in json.load(open(OUT_JSON, encoding="utf-8")):
            products[p["id"]] = p

    links = read_links()
    added = upgraded = 0
    done_urls = {p.get("productUrl") for p in products.values()}
    for url in links:
        if url in done_urls:
            continue
        # для onelink настоящий id узнаём только со страницы, поэтому сначала пробный ключ
        pid = "shein_" + code_of(url)
        if pid in products:
            continue
        try:
            got = fetch(url)
            if not got:
                print("SKIP (нет og-тегов):", url)
                continue
            name, img, gid = got
            # goods_id — единый ключ: так onelink склеивается с товаром, собранным закладкой
            if gid:
                pid = "shein_" + gid

            # Товар уже есть (собран закладкой) → это апгрейд до партнёрской ссылки
            if pid in products:
                if "onelink.shein.com" in url:
                    products[pid]["productUrl"] = url
                    upgraded += 1
                    print(f"↑ {pid}  партнёрская ссылка  {products[pid]['name'][:38]}")
                    done_urls.add(url)
                continue

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
            done_urls.add(url)
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
    aff = sum(1 for p in items if "onelink.shein.com" in (p.get("productUrl") or ""))
    print(f"\nГотово. Новых: {added}. Партнёрских ссылок добавлено: {upgraded}.")
    print(f"Всего: {len(items)}   партнёрских: {aff}   обычных: {len(items) - aff}")
    print(f"По категориям: {by_cat}")
    print(f"JSON: {OUT_JSON}\nФото вещей: {GARMENTS}")


if __name__ == "__main__":
    main()
