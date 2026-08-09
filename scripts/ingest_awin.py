#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль для импорта обновленного товарного фида Awin (DV8 Fashion).
Реализует глубокую фильтрацию, детекцию пола по 4 уровням и управление стоком размеров.
"""

import os
import csv
import io
import gzip
import html
import json
import re
import urllib.request
from datetime import date

from subcategories import sub_category

# Пороги и настройки
MIN_DISCOUNT = 40
MIN_SEARCH_PRICE = 0.01
SOURCE_NAME = "DV8 Fashion"

# Категории-исключения (нельзя надеть на манекен)
EXCLUDED_CATEGORY_NAMES = {
    "Clothing Accessories", "Bags", "Women's Jewellery", "Lingerie",
    "Men's Underwear", "Men's Accessories", "Gifts", "Glassware"
}

# Маппинг категорий (ТОЧНОЕ совпадение merchant_category)
CAT_MAP = {
    "FULL_BODY": [
        "Pyjamas", "Body Suits", "Jumpsuits", "Playsuits",
        "Short Dresses", "Long Dresses", "Midi Dresses"
    ],
    "BOTTOM": [
        "Straight Jeans", "Skinny Jeans", "Loose Jeans", "Wide Leg Jeans",
        "Flare Jeans", "Bootcut Jeans", "Mom Jeans", "Plain Trousers",
        "Patterned Trousers", "Cargo Trousers", "Cuffed Joggers",
        "Open Hem Joggers", "Sport Leggings", "Maxi Skirts", "Midi Skirts",
        "Short Skirts", "Mini Skirt", "Denim Shorts", "Fashion Shorts",
        "Cargo Shorts", "Sport Shorts", "Beach Shorts"
    ],
    "FOOTWEAR": [
        "Laced Trainers", "Slip On Trainers", "Laced Shoes", "Slip On Shoes",
        "Laced Boots", "Knee High Boots", "Thigh High Boots", "Heels", "Sandals"
    ],
    "TOP": [
        "T-shirt", "Top", "Shirt", "Sweatshirt", "Hoodie", "Jumper", "Blouse",
        "Polo", "Jacket", "Coat", "Cardigan", "Vest", "Gilet", "Blazer",
        "Shacket", "Waistcoat", "Waistcoats"
    ]
}

# Признаки пола для нейтральных категорий
FEMALE_CATEGORIES = {
    "Heels", "Knee High Boots", "Thigh High Boots", "Maxi Skirts", "Midi Skirts",
    "Short Skirts", "Mini Skirt", "Short Dresses", "Long Dresses", "Midi Dresses",
    "Playsuits", "Jumpsuits", "Body Suits", "Cami Tops", "Strapless Tops", "Sport Leggings"
}
# «Waistcoat» отсюда убран намеренно. Магазин кладёт в эту категорию и женские
# жилеты, которых сейчас много: из 26 позиций почти все оказались женскими —
# Vero Moda, Only, JDY, Noisy May, Saint Genies. А правило по категории стоит
# ВЫШЕ правила по бренду, поэтому женский бренд не успевал сработать, и жилеты
# уезжали на мужской манекен. Проверено по фотографиям товаров.
MALE_CATEGORIES = {"Ties", "Dress Shirts", "Boxers", "Briefs"}

FEMALE_BRANDS = {
    "only", "veromoda", "pieces", "tally weijl", "noisy may", "kaiia", "jdy",
    "jjxx", "daisy street", "public desire", "girl in mind", "ax paris", "vila", "saint genies"
}
MALE_BRANDS = {"jack & jones", "only & sons", "selected homme", "capo"}

CLOTHING_ORDER = ["2XS", "XS", "S", "M", "L", "XL", "2XL", "3XL"]

def log(msg):
    print(f"[ingest_awin] {msg}", flush=True)

def normalize_color(color):
    if not color: return "NA"
    c = color.lower()
    if "navy" in c: return "Blue"
    if any(x in c for x in ["stone", "sand", "camel"]): return "Beige"
    if any(x in c for x in ["cream", "ivory"]): return "White"
    if "charcoal" in c: return "Grey"
    return color.capitalize()

def get_size_info(sizes):
    if not sizes: return "unknown", None
    if all(s.upper() == "ONE" for s in sizes): return "one", None

    # 1. Waist (28R, 30S...)
    if any(re.search(r"^\d+[RSL]$", s, re.I) for s in sizes):
        return "waist", None

    # 2. Clothing (XS, M...)
    if any(s.upper() in CLOTHING_ORDER for s in sizes):
        return "clothing", None

    # 3. Numeric (Shoes or UK)
    try:
        nums = []
        for s in sizes:
            m = re.search(r"(\d+)", s)
            if m: nums.append(int(m.group(1)))

        if nums:
            mx = max(nums)
            if mx <= 15:
                # Проверка на UK Numeric (чётные 4-24)
                if all(n % 2 == 0 and 4 <= n <= 24 for n in nums):
                    return "uk_numeric", None
                return "shoe_uk", "UK"
            if mx >= 36:
                return "shoe_eu", "EU"
    except: pass

    return "unknown", None

def sort_sizes(sizes, size_type):
    if size_type == "clothing":
        return sorted(list(sizes), key=lambda x: CLOTHING_ORDER.index(x.upper()) if x.upper() in CLOTHING_ORDER else 99)
    if size_type in ["shoe_uk", "shoe_eu", "uk_numeric"]:
        return sorted(list(sizes), key=lambda x: float(re.search(r"(\d+\.?\d*)", x).group(1)) if re.search(r"\d", x) else 999)
    return sorted(list(sizes))

def load_photo_gender():
    """
    Разметка по фотографии товара (scripts/classify_by_photo.py, локальный
    qwen2.5vl). Точность измерена на 60 позициях с известным полом: 96.7%.

    Нужна потому, что у ~450 товаров пола нет НИГДЕ: ни в названии, ни в
    бренде, ни в сетке размеров, ни в одной из 50 колонок фида. Проверено
    поимённо, включая все 5 строк на товар. Страница магазина закрыта
    Azure WAF, туда не ходим.
    """
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "photo_gender.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {k.replace("dv8_", ""): v for k, v in raw.items() if v in ("male", "female")}


PHOTO_GENDER = load_photo_gender()


def load_tryon_overlays():
    """
    Список товаров, для которых уже посчитана примерка (products/*.webp).

    Держим отдельным файлом по той же причине, что и разметку пола: этот
    модуль ПЕРЕСОЗДАЁТ dv8_products.json из ленты при каждой сборке, и всё
    дописанное туда задним числом стирается. Один раз уже потеряли 2213 штук.
    """
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tryon_overlays.json")
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return set(json.load(f))


TRYON_OVERLAYS = load_tryon_overlays()
OVERLAY_BASE = "https://alex22356.github.io/tryon-catalog/products"


def load_tryon_cutouts():
    """
    Товары с ВЫРЕЗАННОЙ вещью на прозрачном фоне (scripts/build_masks.py).

    Отличие от обычной примерки принципиальное: та — кадр всего тела, и два
    таких кадра нельзя наложить, приходится прятать лишнее обрезкой по талии.
    Вырезка накладывается свободно, длинные вещи не режутся, шва нет.

    Отдельным файлом по той же причине, что и всё остальное: этот модуль
    пересоздаёт dv8_products.json из ленты и стирает всё дописанное.
    """
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tryon_cutouts.json")
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return set(json.load(f))


TRYON_CUTOUTS = load_tryon_cutouts()


def gender_from_description(row):
    """
    Пол из текста описания: «The Women's Nelson Sweatshirt is made from...».
    Колонка description раньше не читалась вовсе, а там местами прямо сказано.
    Мнемоники распаковываем: Women&apos;s -> Women's.
    """
    desc = html.unescape(row.get("description") or "")
    f = re.search(r"\b(women|womens|woman|ladies|female)\b", desc, re.I)
    m = re.search(r"\b(men|mens|man|male|gents)\b", desc, re.I)
    if f and not m: return "female"
    if m and not f: return "male"
    return None


def detect_gender(row, sizes, app_cat):
    cat_name = row.get("category_name", "")
    name = row.get("product_name", "").lower()
    brand = row.get("brand_name", "").lower()
    merchant_cat = row.get("merchant_category", "")

    # a) Детские вещи — ПЕРВЫМИ. Иначе "Jack & Jones Junior Shirt" с отделом
    #    "Men's Clothing" уедет в мужские: отдел сработает раньше.
    #    Голого "baby" тут нет: "baby tee" и "babydoll" — взрослые женские фасоны.
    if re.search(r"\b(junior|kids|childrens|infant|toddler)\b", name, re.I):
        return "kids", "name_kids"

    # b) Пол в САМОМ названии товара — сильнее любого поля магазина.
    #    Выше описания: описания магазин копирует между вариантами — у
    #    «Reebok Womens Classic Nylon Shoes» в тексте стоит «these men's
    #    Reebok shoes», и женские кроссовки уезжали в мужские.
    if re.search(r"\b(women|woman|womens|ladies|lady|girls|girl|female)\b", name): return "female", "name"
    if re.search(r"\b(men|man|mens|boys|boy|male|gents|gent)\b", name): return "male", "name"

    # c) Однозначно женский или мужской бренд.
    #
    #    Стоит ВЫШЕ отдела магазина намеренно. Отдел врёт, и это проверено:
    #    «Only Safai Cargo Trousers» с размерами 6R/10R/14S лежит в Men's
    #    Clothing, «Noisy May Moni Jeans» с размерами 25R/27S — тоже, а
    #    мужская рубашка Jack & Jones — в Women's. Таких нашлось 45, и
    #    каждый показывался не тому человеку.
    #
    #    Списки короткие и однозначные: Only, Vero Moda, JDY, Noisy May —
    #    женские линейки Bestseller, Only & Sons и Selected Homme — мужские
    #    того же холдинга. Бренд здесь знание о мире, а отдел — поле, которое
    #    магазин заполняет как придётся.
    if brand in FEMALE_BRANDS: return "female", "brand"
    if brand in MALE_BRANDS: return "male", "brand"

    # d) Кто изображён на снимке товара (см. load_photo_gender выше).
    #
    #    Тоже выше отдела. Проверено прогоном scripts/audit_gender_photo.py по
    #    248 товарам, чей пол держался только на отделе: снимок разошёлся с
    #    отделом у 16, и на контактном листе в 14 случаях прав оказался
    #    снимок — мужские чино-шорты и оксфорды отдел записал женскими, а
    #    женскую цветочную блузку и широкие карго мужскими. Два спорных —
    #    брюки на белом фоне без модели, там ошибиться может кто угодно.
    mid = re.search(r"/m-(\d+)\.aspx", row.get("merchant_deep_link") or "")
    if mid and mid.group(1) in PHOTO_GENDER:
        return PHOTO_GENDER[mid.group(1)], "photo"

    # e) Отдел магазина. Ниже названия, бренда и снимка — причины выше.
    if cat_name.startswith("Women"): return "female", "category_name"
    if cat_name.startswith("Men"): return "male", "category_name"

    # f) Текст описания — когда в названии пола нет
    g = gender_from_description(row)
    if g: return g, "description"

    # g) Эвристики для нейтральных (General Clothing, Shoes...)

    # 1. Обувь по размеру
    if cat_name == "Shoes" or app_cat == "FOOTWEAR":
        nums = [float(m.group(1)) for s in sizes for m in [re.search(r"(\d+\.?\d*)", s)] if m]
        if nums:
            mx = max(nums)
            if mx <= 15: # UK
                if mx <= 8: return "female", "shoe_size"
                if mx >= 9: return "male", "shoe_size"
            elif mx >= 36: # EU
                if mx <= 41: return "female", "shoe_size"
                if mx >= 44: return "male", "shoe_size"

    # 2. По категории
    if merchant_cat in FEMALE_CATEGORIES: return "female", "category"
    if merchant_cat in MALE_CATEGORIES: return "male", "category"

    # 3. По сетке размеров. Слабый признак: женские карго тоже размечают
    #    поясом, поэтому и стоит последним.
    stype, _ = get_size_info(sizes)
    if stype == "uk_numeric": return "female", "size_system"
    if stype == "waist": return "male", "size_system"

    # Проверка по бренду и по снимку раньше стояли здесь, в самом низу.
    # Обе поднялись выше отдела магазина — см. пункты «в» и «г».

    # «не определили» — это НЕ «подходит всем». Приложение unknown прячет.
    return "unknown", "none"

def ingest():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    url_file = os.path.join(repo_root, "feed_url.txt")

    feed_url = ""
    if os.path.exists(url_file):
        with open(url_file, "r") as f:
            content = f.read()
            match = re.search(r"(https?://\S+)", content)
            if match: feed_url = match.group(1).strip()
    if not feed_url:
        feed_url = os.environ.get("AWIN_DV8_FEED_URL")
    if not feed_url:
        log("ОШИБКА: URL фида не найден")
        return []

    log("Загрузка фида...")
    try:
        req = urllib.request.Request(feed_url, headers={"User-Agent": "tryon-catalog/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            compressed_data = resp.read()
        with gzip.GzipFile(fileobj=io.BytesIO(compressed_data)) as f:
            csv_text = f.read().decode("utf-8", errors="replace")
    except Exception as e:
        log(f"ОШИБКА: {e}")
        return []

    reader = csv.DictReader(io.StringIO(csv_text), delimiter="|")

    rows_count = 0
    raw_products = {} # key -> list of rows

    for row in reader:
        rows_count += 1
        link = row.get("merchant_deep_link", "")
        match = re.search(r"m-(\d+)\.aspx", link)
        page_id = match.group(1) if match else None
        key = page_id if page_id else row.get("merchant_image_url")
        if not key: continue

        if key not in raw_products: raw_products[key] = []
        raw_products[key].append(row)

    final_items = []
    stats = {
        "rows": rows_count, "unique": len(raw_products),
        "skipped_excluded_cat": 0, "skipped_discount": 0, "skipped_no_stock": 0, "skipped_no_cat": 0,
        "cat_counts": {"TOP":0, "BOTTOM":0, "FULL_BODY":0, "FOOTWEAR":0},
        "gender_counts": {"female":0, "male":0, "unisex":0, "kids":0, "unknown":0},
        "gender_source": {"category_name":0, "name_kids":0, "description":0, "name":0, "shoe_size":0, "category":0, "brand":0, "size_system":0, "photo":0, "none":0},
        "size_types": {}, "colors": {}
    }

    total_discount = 0

    for key, variants in raw_products.items():
        base = variants[0]

        # 1. Отсев мусора
        if base.get("category_name") in EXCLUDED_CATEGORY_NAMES:
            stats["skipped_excluded_cat"] += 1
            continue

        # 2. Фильтр размеров (только те, что в наличии)
        available_variants = [v for v in variants if int(v.get("size_stock_amount", 0)) > 0]
        if not available_variants:
            stats["skipped_no_stock"] += 1
            continue

        # 3. Категория приложения
        merchant_cat = base.get("merchant_category", "")
        app_cat = None
        for ac, mlist in CAT_MAP.items():
            if merchant_cat in mlist:
                app_cat = ac; break

        if not app_cat:
            top_keywords = ["T-shirt", "Top", "Shirt", "Sweatshirt", "Hoodie", "Jumper", "Blouse", "Polo", "Jacket", "Coat", "Cardigan", "Vest", "Gilet", "Blazer", "Shacket", "Waistcoat"]
            if any(k.lower() in merchant_cat.lower() for k in top_keywords):
                app_cat = "TOP"
            else:
                stats["skipped_no_cat"] += 1; continue

        # 4. Цена и скидка
        try:
            price = float(base.get("search_price", 0))
            rrp = float(base.get("rrp_price", 0))
        except: continue

        discount = 0
        if rrp > price: discount = int(round((rrp - price) / rrp * 100))
        if discount < MIN_DISCOUNT:
            stats["skipped_discount"] += 1; continue

        # 5. Обработка данных
        sizes_set = {v.get("dimensions", "").strip() for v in available_variants if v.get("dimensions")}
        size_type, size_system = get_size_info(sizes_set)
        gender, source = detect_gender(base, sizes_set, app_cat)

        color_group = normalize_color(base.get("colour", ""))

        item = {
            "id": f"dv8_{key}",
            "name": base.get("product_name", "").rsplit(" - ", 1)[0].strip(),
            "price": price,
            "rrpPrice": rrp,
            "discountPct": discount,
            "category": app_cat,
            # Подкатегория для второго уровня иконок в приложении:
            # 31 вид верха у магазина сводим к шести понятным группам.
            "subCategory": sub_category(base.get("merchant_category")),
            "imageUrl": base.get("merchant_image_url"),
            "productUrl": base.get("aw_deep_link"),
            "store": base.get("merchant_name", SOURCE_NAME),
            "brand": base.get("brand_name"),
            "colour": base.get("colour", ""),
            "colourGroup": color_group,
            "currency": base.get("currency"),
            "sizes": sort_sizes(sizes_set, size_type),
            "sizeType": size_type,
            "gender": gender,
            "inStock": True,
            "fitDx": 0.0, "fitDy": 0.0, "fitScale": 1.0,
            "preCut": False
        }

        # Готовая примерка, если она есть: оверлей кладётся на манекен 1:1.
        # Вырезка предпочтительнее целого кадра — ей не нужна обрезка.
        if item["id"] in TRYON_CUTOUTS:
            item["overlayUrl"] = f"{OVERLAY_BASE}/cut_{item['id']}.webp"
            item["preCut"] = True
            item["overlayCutout"] = True
        elif item["id"] in TRYON_OVERLAYS:
            item["overlayUrl"] = f"{OVERLAY_BASE}/overlay_{item['id']}.webp"
            item["preCut"] = True
        if size_system: item["sizeSystem"] = size_system

        final_items.append(item)

        # Обновление статистики
        total_discount += discount
        stats["cat_counts"][app_cat] += 1
        stats["gender_counts"][gender] += 1
        stats["gender_source"][source] += 1
        stats["size_types"][size_type] = stats["size_types"].get(size_type, 0) + 1
        stats["colors"][color_group] = stats["colors"].get(color_group, 0) + 1

    log("--- Статистика Awin (DV8) ---")
    log(f"Всего строк: {stats['rows']}, Уникальных товаров: {stats['unique']}, Надеваемых: {len(final_items)}")
    log(f"Отсеяно: Мусор {stats['skipped_excluded_cat']}, Нет размера {stats['skipped_no_stock']}, Скидка {stats['skipped_discount']}, Категория {stats['skipped_no_cat']}")
    log(f"По полу: {stats['gender_counts']}")
    log(f"Источник пола: {stats['gender_source']}")
    log(f"Типы размеров: {stats['size_types']}")
    log(f"Топ цветов: {dict(sorted(stats['colors'].items(), key=lambda x: x[1], reverse=True)[:10])}")
    if final_items: log(f"Средняя скидка: {round(total_discount / len(final_items), 1)}%")

    return final_items

if __name__ == "__main__":
    items = ingest()
    if items:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo_root, "dv8_products.json"), "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        print(json.dumps(items[:2], ensure_ascii=False, indent=2))
