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
EARN_DATA = os.path.join(HERE, "earn_data.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
# ВАЖНО: без Accept CDN отдаёт AVIF (Ollama и часть Android их не читают).
IMG_HEADERS = {**UA, "Accept": "image/jpeg,image/png;q=0.9,*/*;q=0.1"}


def save_image(data: bytes, path: str) -> bool:
    """
    Пишет фото вещи ВСЕГДА как настоящий JPEG.
    SHEIN может отдать AVIF/WEBP — конвертируем, иначе ломается и разметка, и примерка.
    """
    if len(data) < 2000:
        return False
    if data[:2] == b"\xff\xd8":                      # уже JPEG
        with open(path, "wb") as f:
            f.write(data)
        return True
    try:
        from PIL import Image
        import io as _io
        im = Image.open(_io.BytesIO(data))
        im.convert("RGB").save(path, "JPEG", quality=90)
        return True
    except Exception as e:
        print(f"  ! не смог сконвертировать картинку: {str(e)[:60]}")
        return False

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
    """
    Возвращает [(region, url)]. Строка может быть с пометкой региона:
        US|https://onelink.shein.com/45/xxxx
    Без пометки — считаем US (так писалось раньше).
    """
    out = []
    for line in open(LINKS, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            reg, url = line.split("|", 1)
            reg = reg.strip().upper()
            if reg not in ("US", "EU"):
                reg, url = "US", line
        else:
            reg, url = "US", line
        out.append((reg, url.strip()))
    return out


def load_earn():
    """Цена / популярность / название из кнопки «Earn» affiliate-кабинета."""
    if not os.path.exists(EARN_DATA):
        return {}
    try:
        with open(EARN_DATA, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def apply_earn(item, info):
    """Переносит в товар то, что мы иначе получить не можем: цену и продажи."""
    if not info:
        return item
    if info.get("price"):
        item["price"] = info["price"]
        item["currency"] = info.get("currency", "USD")
        item["approx"] = False
    if info.get("discount"):
        item["discount"] = info["discount"]
    if info.get("sold"):
        item["sold"] = info["sold"]          # сигнал популярности
    # название из Earn лучше маркетингового og:title
    nm = (info.get("name") or "").strip()
    if nm and len(nm) > 20 and any(k in (item.get("name") or "").lower() for k in GENERIC_TITLE):
        item["name"] = nm[:80]
    return item


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
            data = requests.get(img, headers=IMG_HEADERS, timeout=25).content
            if not save_image(data, os.path.join(GARMENTS, pid + ".jpg")):
                raise ValueError("картинка не сохранилась")
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
    earn = load_earn()
    added = upgraded = 0
    done_urls = {p.get("productUrl") for p in products.values()}
    for region, url in links:
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
                item = products[pid]
                regions = set(item.get("regions") or [item.get("region", "US")])
                regions.add(region)
                item["regions"] = sorted(regions)
                apply_earn(item, earn.get(url))
                if "onelink.shein.com" in url:
                    item["productUrl"] = url
                    item.setdefault("productUrlByRegion", {})[region] = url
                    upgraded += 1
                    print(f"↑ {pid}  партнёрская ссылка [{region}]  {item['name'][:34]}")
                done_urls.add(url)
                continue

            img = bigger(img)
            cat = categorize(name)
            # качаем референс вещи
            gpath = os.path.join(GARMENTS, pid + ".jpg")
            data = requests.get(img, headers=IMG_HEADERS, timeout=25).content
            if not save_image(data, gpath):
                raise ValueError("картинка не сохранилась")
            products[pid] = {
                "id": pid,
                "name": name[:80],
                "category": cat,
                "garmentRef": img,          # фото вещи (для этапа примерки)
                "productUrl": url,          # ссылка «Открыть в магазине»
                "productUrlByRegion": {region: url},
                "regions": [region],        # где товар доступен: US / EU
                "gender": "FEMALE",         # TODO уточняется на этапе атрибутов
                "store": "SHEIN",
                # imageUrl / overlayUrl проставятся после этапа 2 (примерка)
            }
            apply_earn(products[pid], earn.get(url))
            done_urls.add(url)
            added += 1
            print(f"+ {pid}  [{cat}][{region}]  {name[:44]}")
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
