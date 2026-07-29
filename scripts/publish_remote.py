#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Публикация каталога в сеть — чтобы КАЖДЫЙ телефон получал товары сам, без обновления приложения.

Что делает:
  1) картинки примерок из app/src/main/assets/products/ → копирует в products/ (папка в git);
  2) товары с примеркой → вписывает в curated.json с сетевыми ссылками
     (file:///android_asset/... → https://<baseUrl>/products/...);
  3) дальше GitHub Actions собирает public/catalog.json из curated.json и публикует на Pages.

Итог: git push → через минуту новые товары у всех клиентов.

Настройка (один раз): baseUrl в publish_config.json  (уже заполнен).

Запуск:
    python scripts/publish_remote.py
"""

import os
import sys
import json
import shutil

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_ASSETS = os.path.abspath(os.path.join(HERE, "..", "app", "src", "main", "assets"))
APP_CATALOG = os.path.join(APP_ASSETS, "catalog.json")
APP_PRODUCTS = os.path.join(APP_ASSETS, "products")
PRODUCTS = os.path.join(HERE, "products")          # отслеживается git — уезжает на Pages
CURATED = os.path.join(HERE, "curated.json")
CONFIG = os.path.join(HERE, "publish_config.json")

ASSET_PREFIX = "file:///android_asset/products/"
DEFAULT_CONFIG = {
    "_note": "baseUrl — где лежит опубликованный каталог. GitHub Pages: "
             "https://<логин>.github.io/<репозиторий> (без слэша на конце).",
    "baseUrl": ""
}


def log(*a):
    print(*a, flush=True)


def main():
    if not os.path.exists(CONFIG):
        json.dump(DEFAULT_CONFIG, open(CONFIG, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        log(f"Создал {CONFIG} — впиши baseUrl и запусти снова")
        return
    base = (json.load(open(CONFIG, encoding="utf-8")).get("baseUrl") or "").rstrip("/")
    if not base:
        log("baseUrl пустой в publish_config.json")
        return
    if not os.path.exists(APP_CATALOG):
        log("нет каталога:", APP_CATALOG)
        return

    os.makedirs(PRODUCTS, exist_ok=True)
    app_items = json.load(open(APP_CATALOG, encoding="utf-8")).get("items", [])

    curated = {"items": []}
    if os.path.exists(CURATED):
        curated = json.load(open(CURATED, encoding="utf-8"))
    by_id = {i["id"]: i for i in curated.get("items", [])}

    copied = published = 0
    for src_item in app_items:
        # публикуем только товары с готовой AI-примеркой (локальные ассеты)
        if not str(src_item.get("imageUrl", "")).startswith(ASSET_PREFIX):
            continue
        item = dict(src_item)
        ok = True
        for key in ("imageUrl", "overlayUrl"):
            url = item.get(key) or ""
            if not url.startswith(ASSET_PREFIX):
                continue
            fname = url[len(ASSET_PREFIX):]
            src = os.path.join(APP_PRODUCTS, fname)
            if not os.path.exists(src):
                log(f"  ! нет файла {fname} — товар {item['id']} пропущен")
                ok = False
                break
            dst = os.path.join(PRODUCTS, fname)
            if not os.path.exists(dst) or os.path.getmtime(src) > os.path.getmtime(dst):
                shutil.copy2(src, dst)
                copied += 1
            item[key] = f"{base}/products/{fname}"
        if not ok:
            continue
        by_id[item["id"]] = item
        published += 1

    curated["items"] = list(by_id.values())
    curated["_comment"] = ("Ручные и AI-примеренные товары. Товары с preCut/overlayUrl "
                           "добавляет scripts/publish_remote.py — не правь их вручную.")
    json.dump(curated, open(CURATED, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    log(f"картинок скопировано в products/: {copied}")
    log(f"товаров с примеркой опубликовано: {published}")
    log(f"всего в curated.json:             {len(curated['items'])}")
    log("")
    log("Дальше — выложить (тогда каталог увидят все телефоны):")
    log('  git add -A && git commit -m "catalog update" && git push')
    log(f"Приложение читает: {base}/catalog.json")


if __name__ == "__main__":
    main()
