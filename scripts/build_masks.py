"""
Вырезает вещи из всех примерок — локально, на видеокарте или процессоре.

Зачем: примерка это кадр ВСЕГО тела (модель в вещи плюс бельё базы). Два
таких кадра нельзя наложить друг на друга, поэтому сейчас лишнее прячется
обрезкой по талии — грубо: длинные вещи режутся пополам, на стыке видна
полоска белья. Вырезанная вещь эту нужду снимает совсем.

Разборщик берёт маску ИМЕННО нужного класса по категории товара, поэтому
дорисованные генератором шорты в маску верха не попадают. Этим он и лучше
вычитания базы, которое в проекте уже пробовали и отвергли.

    python scripts/build_masks.py --limit 20     # проба
    python scripts/build_masks.py                # весь каталог
    python scripts/build_masks.py --device cpu   # если видеокарта занята

Прогон возобновляемый: готовое пропускается, можно прерывать.
"""
import argparse
import collections
import json
import os
import sys
import time
import urllib.request

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mask_qc import clean_mask, qc, summarise

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "public", "catalog.json")
PRODUCTS = os.path.join(ROOT, "products")
CUTS = os.path.join(ROOT, "products_cut")
REPORT = os.path.join(ROOT, "mask_report.json")
BASE_URL = "https://alex22356.github.io/tryon-catalog/products"

# наши категории -> категории разборщика
CAT_TO_FASHN = {"TOP": "tops", "BOTTOM": "bottoms", "FULL_BODY": "one-pieces"}

# Линия талии для запасного правила. Замерено по 43 вещам низа: пояс
# начинается в среднем на 0.467 высоты, нижняя четверть — с 0.437.
# Берём 0.40 с запасом, чтобы не срезать высокую посадку.
WAIST_LINE = 0.40


def worklist():
    with open(CATALOG, encoding="utf-8") as f:
        items = json.load(f)["items"]
    out = []
    for it in items:
        if not it.get("overlayUrl"):
            continue
        cat = CAT_TO_FASHN.get(it.get("category"))
        if not cat:
            continue                       # обувь не примеряется
        out.append({"id": it["id"], "cat": cat, "app_cat": it["category"]})
    return out


def source_image(pid):
    """Берём примерку с диска, если она есть, иначе качаем с сайта."""
    # Семь ручных позиций SHEIN лежат в JPEG, остальные в WebP — пробуем оба
    for ext in ("webp", "jpg"):
        local = os.path.join(PRODUCTS, f"overlay_{pid}.{ext}")
        if os.path.exists(local):
            return Image.open(local).convert("RGB")
    req = urllib.request.Request(f"{BASE_URL}/overlay_{pid}.webp",
                                 headers={"User-Agent": "Mozilla/5.0"})
    import io as _io
    data = urllib.request.urlopen(req, timeout=40).read()
    return Image.open(_io.BytesIO(data)).convert("RGB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = все")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    a = ap.parse_args()

    import torch
    dev = a.device
    if dev == "auto":
        dev = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"считаем на: {dev}"
          + (f" ({torch.cuda.get_device_name(0)})" if dev == "cuda" else ""))

    from fashn_human_parser import FashnHumanParser
    import fashn_human_parser as m

    parser = FashnHumanParser(device=dev)
    COVER = getattr(m, "CATEGORY_TO_BODY_COVERAGE")
    GROUPS = (getattr(m, "BODY_COVERAGE_TO_FASHN_LABELS", None)
              or getattr(m, "BODY_COVERAGE_TO_LABELS"))
    IDS = getattr(m, "FASHN_LABELS_TO_IDS", None) or getattr(m, "LABELS_TO_IDS")

    os.makedirs(CUTS, exist_ok=True)
    work = worklist()
    done_ids = {f[:-len(".webp")] for f in os.listdir(CUTS) if f.endswith(".webp")}
    todo = [w for w in work if w["id"] not in done_ids]
    if a.limit:
        todo = todo[:a.limit]

    by_cat = collections.Counter(w["app_cat"] for w in work)
    print(f"всего с примерками: {len(work)} {dict(by_cat)}")
    print(f"готово раньше: {len(done_ids)} | к работе: {len(todo)}")

    report = json.load(open(REPORT, encoding="utf-8")) if os.path.exists(REPORT) else []
    seen = {r["id"] for r in report}
    t0, failed = time.time(), 0

    for k, w in enumerate(todo, 1):
        try:
            img = source_image(w["id"])
            seg = parser.predict(img) if hasattr(parser, "predict") else parser(img)
            arr = seg if isinstance(seg, np.ndarray) else np.asarray(seg)
            ids = [IDS[l] for l in GROUPS[COVER[w["cat"]]]]
            raw = np.isin(arr, ids)

            # Запасное правило для низа. Разборщик часто относит длинные юбки
            # и комплекты к классу «платье» — проверено на 12 пустых масках,
            # у 11 нашлось именно оно. Тогда берём платье, но ТОЛЬКО ниже
            # линии талии: вещь категории «низ» выше пояса быть не может,
            # значит ошибиться нельзя.
            if w["app_cat"] == "BOTTOM" and raw.mean() < 0.01:
                spare = np.isin(arr, [IDS["dress"], IDS["top"]])
                cut_at = int(spare.shape[0] * WAIST_LINE)
                spare[:cut_at] = False
                if spare.mean() > raw.mean():
                    raw = spare

            clean, alpha = clean_mask(raw)
            r = qc(raw, clean, w["app_cat"])
            r["id"], r["cat"] = w["id"], w["app_cat"]
            if w["id"] not in seen:
                report.append(r)

            rgba = img.convert("RGBA")
            rgba.putalpha(Image.fromarray(alpha))
            rgba.save(os.path.join(CUTS, f"{w['id']}.webp"), "WEBP",
                      quality=88, method=4)
        except Exception as e:
            failed += 1
            print(f"  СБОЙ {w['id']}: {type(e).__name__} {e}")

        if k % 25 == 0 or k == len(todo):
            el = time.time() - t0
            json.dump(report, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False)
            left = (len(todo) - k) * el / k / 60
            print(f"  {k}/{len(todo)}  {el/k:.2f} с/шт  осталось ~{left:.0f} мин  "
                  f"сбоев {failed}", flush=True)

    json.dump(report, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False)

    print()
    print(summarise(report))
    rough = np.array([r["roughness"] for r in report if r.get("roughness")])
    if len(rough):
        print("\nшероховатость края (по ней калибруется порог):")
        for q in (50, 75, 90, 95, 99):
            print(f"   {q}-й процентиль: {np.percentile(rough, q):.1f}")

    if os.path.isdir(CUTS):
        files = [f for f in os.listdir(CUTS) if f.endswith(".webp")]
        size = sum(os.path.getsize(os.path.join(CUTS, f)) for f in files)
        print(f"\nвырезок: {len(files)}, объём {size/1024/1024:.0f} МБ")


if __name__ == "__main__":
    main()
