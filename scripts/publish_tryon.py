#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Автопубликация готовых примерок в приложение — ЭТАП 3 конвейера.

Берёт «надетые» фото из tryon_out/<id>.png (результат Gemini/VTON) и делает ВСЁ сам:
  1) overlay  — ресайз под размер premium_model → наложение на манекен 1:1;
  2) thumb    — авто-обрезка по категории (для кнопки товара показываем саму вещь);
  3) кладёт оба файла в app/src/main/assets/products/;
  4) прописывает/обновляет товар в app/src/main/assets/catalog.json
     (imageUrl, overlayUrl, preCut=true, нейтральные fit-якоря, productUrl, категория).

Идемпотентно: запускай сколько угодно — обновит существующие, добавит новые.
Не трогает товары, которых нет в tryon_out (в т.ч. демо-позиции каталога).

Запуск:
    python scripts/publish_tryon.py
    python scripts/publish_tryon.py --only shein_5x4po236q8lx      # один товар
"""

import os
import re
import sys
import json
import argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from PIL import Image

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.abspath(os.path.join(HERE, "..", "app", "src", "main"))

TRYON_DIR = os.path.join(HERE, "tryon_out")
PRODUCTS_JSON = os.path.join(HERE, "shein_products.json")
CONFIG = os.path.join(HERE, "publish_config.json")
MODEL_IMG = os.path.join(APP, "res", "drawable-nodpi", "premium_model.jpg")
ASSETS_PRODUCTS = os.path.join(APP, "assets", "products")
CATALOG = os.path.join(APP, "assets", "catalog.json")
ASSET_URL = "file:///android_asset/products/"

# вертикальные полосы обрезки миниатюры по категории (доля высоты кадра)
THUMB_BAND = {
    # Настроено под вертикальную базу 896x1200: плечи ~0.22, шортики ~0.58,
    # ноги 0.60-0.90, ступни ~0.93. Проверять на первой реальной примерке.
    "TOP":       (0.20, 0.58),
    "BOTTOM":    (0.45, 0.98),
    "FULL_BODY": (0.18, 0.98),
    "FOOTWEAR":  (0.88, 1.00),
    "ACCESSORY": (0.18, 0.98),
}


def log(*a):
    print(*a, flush=True)


def body_bbox(im):
    """Горизонтальные границы фигуры (не-белые пиксели) — чтобы миниатюра была по центру вещи."""
    g = im.convert("L").resize((im.width // 4, im.height // 4))
    px = g.load()
    cols = []
    for x in range(g.width):
        for y in range(g.height):
            if px[x, y] < 235:
                cols.append(x)
                break
    if not cols:
        return 0, im.width
    return min(cols) * 4, (max(cols) + 1) * 4


def make_thumb(worn, category):
    top_f, bot_f = THUMB_BAND.get(category, THUMB_BAND["TOP"])
    W, H = worn.size
    y0, y1 = int(H * top_f), int(H * bot_f)
    band = worn.crop((0, y0, W, y1))
    x0, x1 = body_bbox(band)
    pad = int((x1 - x0) * 0.10) + 8
    x0 = max(0, x0 - pad)
    x1 = min(W, x1 + pad)
    return band.crop((x0, 0, x1, band.height))


def load_products():
    if not os.path.exists(PRODUCTS_JSON):
        return {}
    return {p["id"]: p for p in json.load(open(PRODUCTS_JSON, encoding="utf-8"))}


def affiliate_template():
    """Шаблон партнёрской ссылки из publish_config.json (пусто = выключено)."""
    if not os.path.exists(CONFIG):
        return ""
    try:
        return (json.load(open(CONFIG, encoding="utf-8")).get("affiliateTemplate") or "").strip()
    except Exception:
        return ""


def affiliate_url(url, template):
    """
    Подставляет партнёрские метки в обычную ссылку на товар.
    onelink-ссылки не трогаем — они уже партнёрские.
    """
    if not url or not template:
        return url
    if "onelink.shein.com" in url:
        return url
    return template.replace("{url}", url.split("?")[0])


def load_catalog():
    if os.path.exists(CATALOG):
        return json.load(open(CATALOG, encoding="utf-8"))
    return {"version": 1, "items": []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="обработать только этот id")
    args = ap.parse_args()

    if not os.path.exists(MODEL_IMG):
        log("НЕ найдена модель:", MODEL_IMG)
        return
    os.makedirs(ASSETS_PRODUCTS, exist_ok=True)
    MW, MH = Image.open(MODEL_IMG).size

    products = load_products()
    catalog = load_catalog()
    by_id = {it["id"]: it for it in catalog.get("items", [])}
    aff_tpl = affiliate_template()
    if aff_tpl:
        log(f"партнёрские метки: включены  ({aff_tpl[:52]}…)")
    else:
        log("партнёрские метки: ВЫКЛЮЧЕНЫ — переходы не принесут комиссию")
        log("  включить: affiliateTemplate в publish_config.json")

    if not os.path.isdir(TRYON_DIR):
        log("Нет папки с примерками:", TRYON_DIR)
        return
    files = sorted(f for f in os.listdir(TRYON_DIR)
                   if f.lower().endswith((".png", ".jpg")))
    if args.only:
        files = [f for f in files if os.path.splitext(f)[0] == args.only]
    if not files:
        log("Нет готовых примерок в", TRYON_DIR)
        return

    done = 0
    for fn in files:
        pid = os.path.splitext(fn)[0]
        worn = Image.open(os.path.join(TRYON_DIR, fn)).convert("RGB")
        meta = products.get(pid, {})
        category = meta.get("category", "TOP")

        # 1) overlay 1:1 под размер модели
        ov_name = f"overlay_{pid}.jpg"
        worn.resize((MW, MH), Image.LANCZOS).save(
            os.path.join(ASSETS_PRODUCTS, ov_name), quality=90)

        # 2) миниатюра-товар
        th_name = f"thumb_{pid}.jpg"
        make_thumb(worn, category).save(
            os.path.join(ASSETS_PRODUCTS, th_name), quality=90)

        # 3) запись в каталог
        item = by_id.get(pid, {"id": pid})
        item.update({
            "id": pid,
            "name": meta.get("name", item.get("name", pid))[:70],
            "price": item.get("price", meta.get("price", 0.0)),
            "category": category,
            "imageUrl": ASSET_URL + th_name,
            "overlayUrl": ASSET_URL + ov_name,
            "preCut": True,
            "fitDx": 0.0, "fitDy": 0.0, "fitScale": 1.0,
            "store": meta.get("store", "SHEIN"),
            "gender": "female",
            "inStock": True,
            "approx": True,
        })
        if meta.get("productUrl"):
            item["productUrl"] = affiliate_url(meta["productUrl"], aff_tpl)
        by_id[pid] = item
        done += 1
        log(f"✓ {pid}  [{category}]  overlay+thumb → assets/products")

    catalog["items"] = list(by_id.values())
    catalog["version"] = int(catalog.get("version", 1)) + 1
    json.dump(catalog, open(CATALOG, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    log(f"\nОпубликовано: {done}. Товаров в каталоге: {len(catalog['items'])}")
    log("Каталог:", CATALOG)
    log("Дальше: ./gradlew installDebug")


if __name__ == "__main__":
    main()
