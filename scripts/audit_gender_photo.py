"""
Сверяет пол товара с тем, кто изображён на его фотографии.

Зачем. Отдел магазина в фиде оказался ненадёжным: женские карго-брюки Only с
размерами 6R/10R лежали в Men's Clothing, мужская рубашка Jack & Jones — в
Women's. Сорок пять таких нашлись по спору с брендом и уже исправлены. Но
остаются те, чей бренд ни о чём не говорит: их пол по-прежнему держится
только на отделе, и проверить его нечем, кроме снимка.

Разметку по фото делает classify_by_photo.py, но она срабатывает лишь там,
где текстовые признаки молчат. Здесь мы применяем её как ПРОВЕРКУ: спрашиваем
модель о товарах, чей пол уже определён отделом, и смотрим, где она спорит.

Ollama локальная и бесплатная, около семи секунд на снимок.

    python scripts/audit_gender_photo.py --limit 40    # проба
    python scripts/audit_gender_photo.py               # все спорные

Скрипт ничего не меняет: он только пишет отчёт photo_audit.json и печатает
расхождения. Что делать с находками — решает человек.
"""
import argparse
import csv
import gzip
import json
import os
import re
import sys
import time

# Под Git Bash у Python вывод в cp1252, и первая же строка с кириллицей
# роняет прогон с UnicodeEncodeError. Под PowerShell всё работает, поэтому
# ошибка вылезает только при запуске из другой оболочки.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import classify_by_photo as cbp
import ingest_awin as ia

ROOT = os.path.dirname(HERE)
REPORT = os.path.join(ROOT, "photo_audit.json")


def weak_items():
    """Товары, чей пол держится только на отделе магазина."""
    with open(os.path.join(ROOT, "public", "catalog.json"), encoding="utf-8") as f:
        by_id = {i["id"]: i for i in json.load(f)["items"]}
    feed = os.path.join(os.environ.get("TEMP", "/tmp"), "dv8_feed.csv.gz")
    if not os.path.exists(feed):
        sys.exit("нет ленты в кеше — сначала build_catalog.py")
    out, seen = [], set()
    with gzip.open(feed, "rt", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f, delimiter="|"):
            m = re.search(r"m-(\d+)\.aspx", row.get("merchant_deep_link") or "")
            if not m:
                continue
            pid = "dv8_" + m.group(1)
            if pid in seen or pid not in by_id:
                continue
            seen.add(pid)
            it = by_id[pid]
            g, src = ia.detect_gender(row, it.get("sizes") or [], it.get("category"))
            if src == "category_name":
                out.append((it, g))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = все")
    a = ap.parse_args()

    work = weak_items()
    if a.limit:
        work = work[:a.limit]
    print(f"проверяем: {len(work)} товаров", flush=True)

    report, conflicts, silent = [], [], 0
    t0 = time.time()
    for n, (it, assigned) in enumerate(work, 1):
        try:
            photo = cbp.ask(cbp.fetch_image(it))
        except Exception as e:
            print(f"  сбой {it['id']}: {type(e).__name__}", flush=True)
            continue
        if photo is None:
            # Модель не увидела человека — обычно снимок одной вещи на белом.
            # Это не ошибка разметки, спорить тут не с чем.
            silent += 1
        elif photo != assigned:
            conflicts.append({"id": it["id"], "name": it["name"], "brand": it.get("brand"),
                              "assigned": assigned, "photo": photo,
                              "sub": it.get("subCategory"), "url": it["imageUrl"]})
        report.append({"id": it["id"], "assigned": assigned, "photo": photo})
        if n % 20 == 0 or n == len(work):
            el = time.time() - t0
            print(f"  {n}/{len(work)}  {el/n:.1f} с/шт  осталось ~{(len(work)-n)*el/n/60:.0f} мин"
                  f"  спорных {len(conflicts)}", flush=True)

    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    print(f"\nбез человека на снимке: {silent} — про них фото ничего не говорит")
    print(f"спорят с отделом: {len(conflicts)}")
    for c in conflicts:
        print(f"  отдел={c['assigned']:<7} фото={c['photo']:<7} {(c['brand'] or '?'):<16} {c['name'][:50]}")
    print(f"\nотчёт: {REPORT}")


if __name__ == "__main__":
    main()
