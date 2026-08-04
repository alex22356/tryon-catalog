#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль для импорта товарного фида Awin (DV8 Fashion).
Скачивает CSV фид, группирует варианты (размеры) в уникальные товары,
фильтрует по наличию и скидке, мапит категории.
"""

import os
import csv
import io
import gzip
import json
import re
import urllib.request
from datetime import date

# Пороги и настройки
MIN_DISCOUNT = 40
MIN_SEARCH_PRICE = 0.01
SOURCE_NAME = "DV8 Fashion"

# Маппинг категорий (ключевые слова в merchant_category)
CAT_RULES = {
    "TOP": ["T-shirt", "Top", "Shirt", "Sweatshirt", "Hoodie", "Jumper", "Blouse", "Polo", "Jacket", "Coat"],
    "BOTTOM": ["Jean", "Trouser", "Short", "Skirt", "Legging", "Chino", "Jogger"],
    "FULL_BODY": ["Dress", "Jumpsuit", "Playsuit"],
    "FOOTWEAR": ["Trainer", "Shoe", "Boot", "Sandal", "Heel", "Sneaker"]
}

def log(msg):
    print(f"[ingest_awin] {msg}", flush=True)

def get_product_page_id(merchant_deep_link):
    """Извлекает числовой ID из ссылки вида ...m-12345.aspx"""
    match = re.search(r"m-(\d+)\.aspx", merchant_deep_link)
    if match:
        return match.group(1)
    return None

def map_category(merchant_cat):
    """Определяет категорию приложения на основе ключевых слов в merchant_category."""
    cat_str = merchant_cat.lower()
    for app_cat, keywords in CAT_RULES.items():
        for kw in keywords:
            if kw.lower() in cat_str:
                return app_cat
    return None

def detect_gender(name, brand, category, path):
    """Определяет пол по названию, бренду или категориям."""
    # Источник сигнала: Name + Brand + Category + Path (все в нижнем регистре)
    text = f"{name} {brand} {category} {path}".lower()

    # 1. ЖЕНСКИЕ признаки (проверяем ПЕРВЫМИ, так как womens содержит mens)
    # Используем word boundaries \b. Учитываем также Women's/Men's.
    female_pattern = r"\b(women|woman|womens|ladies|lady|girls|girl|female)\b"
    if re.search(female_pattern, text) or "women's" in text or "woman's" in text or "lady's" in text:
        return "female"

    # 2. МУЖСКИЕ признаки
    male_pattern = r"\b(men|man|mens|boys|boy|male|gents|gent)\b"
    if re.search(male_pattern, text) or "men's" in text or "man's" in text or "boy's" in text:
        return "male"

    return "unisex"

def clean_name_and_get_size(product_name):
    """Извлекает размер (после ' - ') и возвращает чистое имя."""
    if " - " in product_name:
        parts = product_name.rsplit(" - ", 1)
        return parts[0].strip(), parts[1].strip()
    return product_name.strip(), ""

def ingest():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    url_file = os.path.join(repo_root, "feed_url.txt")

    feed_url = ""
    if os.path.exists(url_file):
        with open(url_file, "r") as f:
            feed_url = f.read().strip()

    if not feed_url:
        feed_url = os.environ.get("AWIN_DV8_FEED_URL")

    if not feed_url:
        log("ОШИБКА: URL фида не найден в feed_url.txt или переменной окружения")
        return []

    log(f"Скачиваю фид: {feed_url[:60]}...")
    try:
        req = urllib.request.Request(feed_url, headers={"User-Agent": "tryon-catalog/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            compressed_data = resp.read()

        with gzip.GzipFile(fileobj=io.BytesIO(compressed_data)) as f:
            csv_text = f.read().decode("utf-8", errors="replace")
    except Exception as e:
        log(f"ОШИБКА при загрузке/распаковке: {e}")
        return []

    reader = csv.DictReader(io.StringIO(csv_text), delimiter="|")

    rows_count = 0
    products_map = {}
    skipped_cat = 0
    skipped_price = 0
    skipped_stock = 0
    skipped_discount = 0

    for row in reader:
        rows_count += 1

        # 1. Базовые фильтры
        if row.get("in_stock") != "1":
            skipped_stock += 1
            continue

        try:
            price = float(row.get("search_price", 0))
            rrp = float(row.get("rrp_price", 0))
        except (ValueError, TypeError):
            skipped_price += 1
            continue

        if price < MIN_SEARCH_PRICE:
            skipped_price += 1
            continue

        # Скидка
        discount_pct = 0
        if rrp > price:
            discount_pct = int(round((rrp - price) / rrp * 100))

        if rrp <= price or discount_pct < MIN_DISCOUNT:
            skipped_discount += 1
            continue

        # 2. Определение категории
        app_cat = map_category(row.get("merchant_category", ""))
        if not app_cat:
            skipped_cat += 1
            continue

        # 3. Группировка
        # ID страницы товара - основной ключ группировки
        page_id = get_product_page_id(row.get("merchant_deep_link", ""))
        group_key = page_id if page_id else row.get("merchant_image_url")

        if not group_key:
            continue

        name, size = clean_name_and_get_size(row.get("product_name", ""))

        if group_key not in products_map:
            products_map[group_key] = {
                "id": f"dv8_{page_id}" if page_id else f"dv8_{hash(group_key)}",
                "name": name,
                "price": price,
                "rrpPrice": rrp,
                "discountPct": discount_pct,
                "category": app_cat,
                "imageUrl": row.get("merchant_image_url"),
                "productUrl": row.get("aw_deep_link"),
                "store": row.get("merchant_name", SOURCE_NAME),
                "brand": row.get("brand_name"),
                "colour": row.get("colour"),
                "currency": row.get("currency"),
                "sizes": set(),
                "gender": detect_gender(row.get("product_name", ""), row.get("brand_name", ""), row.get("merchant_category", ""), row.get("merchant_product_category_path", "")),
                "inStock": True,
                "fitDx": 0.0,
                "fitDy": 0.0,
                "fitScale": 1.0,
                "preCut": False
            }

        if size:
            products_map[group_key]["sizes"].add(size)

    # Приводим к финальному списку и сортируем размеры
    final_items = []
    total_discount = 0
    cat_stats = {"TOP": 0, "BOTTOM": 0, "FULL_BODY": 0, "FOOTWEAR": 0}
    gender_stats = {"female": 0, "male": 0, "unisex": 0}

    for p in products_map.values():
        p["sizes"] = sorted(list(p["sizes"]))
        final_items.append(p)
        total_discount += p["discountPct"]
        cat_stats[p["category"]] += 1
        gender_stats[p["gender"]] += 1

    # Статистика
    log("--- Статистика Awin (DV8) ---")
    log(f"Всего строк в фиде: {rows_count}")
    log(f"Уникальных товаров: {len(products_map)}")
    log(f"Прошло фильтр: {len(final_items)}")
    log(f"Скидка < {MIN_DISCOUNT}%: {skipped_discount}")
    log(f"Нет в наличии: {skipped_stock}")
    log(f"Пропущено из-за категории: {skipped_cat}")
    if final_items:
        log(f"Средняя скидка: {round(total_discount / len(final_items), 1)}%")
    log(f"По категориям: {cat_stats}")
    log(f"По полу: {gender_stats}")

    return final_items

if __name__ == "__main__":
    # Для отладки можно запустить модуль отдельно
    items = ingest()
    if items:
        # Пишем локальную копию
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out_path = os.path.join(repo_root, "dv8_products.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        log(f"Записано в {out_path}")
        print(json.dumps(items[:2], ensure_ascii=False, indent=2))
