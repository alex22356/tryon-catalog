#!/usr/bin/env python3
"""
Собирает public/catalog.json для приложения.

Источники:
  1. curated.json — товары, добавленные вручную (магазины без фидов).
  2. feeds.json   — список аффилиат-фидов (CSV/XML), скачиваются и приводятся к общему формату.

Правила:
  - товары с inStock=false отбрасываются;
  - товар из фида перекрывает ручной с тем же id;
  - всем проставляется checkedAt (дата сборки).

Зависимости: только стандартная библиотека (никаких pip install).
"""

import csv
import io
import json
import os
import shutil
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timezone, datetime

# Windows-консоль по умолчанию cp1252 — принудительно UTF-8, чтобы кириллица не падала.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "public")
OUT_FILE = os.path.join(OUT_DIR, "catalog.json")
SITE_DIR = os.path.join(ROOT, "site")

VALID_CATEGORIES = {"TOP", "BOTTOM", "FULL_BODY", "FOOTWEAR"}
TODAY = date.today().isoformat()


def log(msg):
    print(f"[catalog] {msg}", flush=True)


def read_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalise(raw, source, approx):
    """Приводит запись к формату приложения. Возвращает None, если запись негодная."""
    category = str(raw.get("category", "")).upper().strip()
    if category not in VALID_CATEGORIES:
        return None
    item_id = str(raw.get("id", "")).strip()
    image = str(raw.get("imageUrl", "")).strip()
    if not item_id or not image:
        return None
    if not raw.get("inStock", True):
        return None
    try:
        price = round(float(raw.get("price", 0)), 2)
    except (TypeError, ValueError):
        return None

    item = {
        "id": item_id,
        "name": str(raw.get("name", "")).strip() or "Без названия",
        "price": price,
        "category": category,
        "imageUrl": image,
        "store": str(raw.get("store", source)).strip(),
        "inStock": True,
        "approx": bool(raw.get("approx", approx)),
        "checkedAt": TODAY,
    }
    url = str(raw.get("productUrl", "")).strip()
    if url:
        item["productUrl"] = url

    # Поля AI-примерки и посадки — переносим как есть, приложение их понимает.
    overlay = str(raw.get("overlayUrl", "")).strip()
    if overlay:
        item["overlayUrl"] = overlay
    if raw.get("preCut"):
        item["preCut"] = True
    gender = str(raw.get("gender", "")).strip()
    if gender:
        item["gender"] = gender
    for key in ("fitDx", "fitDy", "fitScale"):
        if key in raw:
            try:
                item[key] = round(float(raw[key]), 4)
            except (TypeError, ValueError):
                pass
    return item


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "tryon-catalog/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def parse_csv(data, mapping):
    text = data.decode("utf-8", errors="replace")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        rows.append({target: row.get(src, "") for target, src in mapping.items()})
    return rows


def parse_xml(data, mapping, item_tag):
    root = ET.fromstring(data)
    rows = []
    for node in root.iter(item_tag):
        row = {}
        for target, src in mapping.items():
            found = node.find(src)
            row[target] = found.text if found is not None and found.text else ""
        rows.append(row)
    return rows


def load_feeds(feeds):
    items = []
    for feed in feeds:
        if not feed.get("enabled", True):
            continue
        name = feed.get("name", "feed")
        url = feed.get("url", "")
        if not url or url.startswith("PUT_"):
            log(f"пропускаю {name}: URL не задан")
            continue
        try:
            data = fetch(url)
            fmt = feed.get("format", "csv").lower()
            mapping = feed.get("mapping", {})
            if fmt == "csv":
                rows = parse_csv(data, mapping)
            else:
                rows = parse_xml(data, mapping, feed.get("itemTag", "item"))
            got = 0
            for row in rows:
                item = normalise(row, name, approx=False)
                if item:
                    items.append(item)
                    got += 1
            log(f"{name}: {got} товаров")
        except Exception as exc:  # фид не должен ронять сборку
            log(f"ОШИБКА {name}: {exc}")
    return items


def main():
    curated_raw = read_json(os.path.join(ROOT, "curated.json"), {"items": []})
    feeds_raw = read_json(os.path.join(ROOT, "feeds.json"), {"feeds": []})

    curated = []
    for raw in curated_raw.get("items", []):
        item = normalise(raw, "curated", approx=True)
        if item:
            curated.append(item)
    log(f"ручных товаров: {len(curated)}")

    # 3. DV8 Fashion (Awin Feed)
    try:
        sys.path.insert(0, os.path.dirname(__file__))
        import ingest_awin
        dv8_items = ingest_awin.ingest()
        # Сохраняем промежуточный результат для контроля
        dv8_path = os.path.join(ROOT, "dv8_products.json")
        with open(dv8_path, "w", encoding="utf-8") as f:
            json.dump(dv8_items, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        log(f"ОШИБКА DV8: {exc}")
        dv8_items = []

    feed_items = load_feeds(feeds_raw.get("feeds", []))

    # Товар из фида перекрывает ручной с тем же id
    merged = {item["id"]: item for item in curated}
    for item in dv8_items + feed_items:
        merged[item["id"]] = item

    items = sorted(merged.values(), key=lambda i: (i["price"], i["name"]))
    if not items:
        log("ОШИБКА: каталог пуст — не публикую, чтобы не сломать приложение")
        return 1

    catalog = {
        "version": int(datetime.now(timezone.utc).timestamp()),
        "updatedAt": TODAY,
        "items": items,
    }

    os.makedirs(OUT_DIR, exist_ok=True)

    # Лендинг (site/) публикуется рядом с каталогом
    if os.path.isdir(SITE_DIR):
        for name in os.listdir(SITE_DIR):
            src = os.path.join(SITE_DIR, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(OUT_DIR, name))
        log(f"лендинг скопирован из site/")

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    log(f"готово: {len(items)} товаров → {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
