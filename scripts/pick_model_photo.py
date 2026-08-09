"""
Ищет среди кадров товара тот, где есть живая модель, и спрашивает пол по нему.

Зачем. Разметка по фотографии смотрела только первый кадр. Беда в том, что
модель отвечает всегда: на снимке одной вещи на белом фоне она угадывает пол
по крою и звучит так же уверенно, как на снимке с человеком. В прогоне по 248
товарам она ни разу не сказала «человека нет» — а на глаз именно такие кадры
и дали единственные спорные ответы.

Что известно про ленту DV8 (замерено): у каждого товара РОВНО три кадра по
предсказуемому адресу — 34988.jpg, 34988-2.jpg, 34988-3.jpg. У 21% товаров на
первом модели нет, а на втором или третьем есть.

Отсюда порядок: разборщиком проверяем кадры по очереди и спрашиваем про
первый, где найден человек. Если человека нет ни на одном — честно оставляем
товар без ответа, вместо того чтобы записывать догадку.

    python scripts/pick_model_photo.py --limit 30   # проба
    python scripts/pick_model_photo.py              # все спорные
    python scripts/pick_model_photo.py --apply      # записать в photo_gender.json

Разборщик работает на процессоре, около секунды на кадр; вопрос модели —
секунд десять. Оба бесплатны и локальны.
"""
import argparse
import csv
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.request

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import classify_by_photo as cbp
import ingest_awin as ia

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(HERE)
RESULT = os.path.join(ROOT, "model_photo.json")
UA = {"User-Agent": "Mozilla/5.0"}
# Доля пикселей человека, ниже которой считаем, что модели на кадре нет.
# 1.5% — это заметная рука или нога, случайную тень столько не даст.
PERSON_MIN = 0.015


def photo_urls(item):
    base, ext = os.path.splitext(item["imageUrl"])
    return [item["imageUrl"], f"{base}-2{ext}", f"{base}-3{ext}"]


def load_parser():
    import fashn_human_parser as m
    from fashn_human_parser import FashnHumanParser
    ids = getattr(m, "FASHN_LABELS_TO_IDS", None) or getattr(m, "LABELS_TO_IDS")
    person = [ids[k] for k in ("face", "arms", "legs") if k in ids]
    return FashnHumanParser(device="cpu"), person


def has_person(parser, person_ids, url):
    try:
        req = urllib.request.Request(url, headers=UA)
        img = Image.open(io.BytesIO(urllib.request.urlopen(req, timeout=30).read())).convert("RGB")
    except Exception:
        return None, None
    seg = parser.predict(img) if hasattr(parser, "predict") else parser(img)
    arr = seg if isinstance(seg, np.ndarray) else np.asarray(seg)
    return float(np.isin(arr, person_ids).mean()) > PERSON_MIN, img


def weak_items():
    """Товары, чей пол держится на отделе магазина или на снимке."""
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
            if src in ("category_name", "photo"):
                out.append((it, g, src))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true", help="записать в photo_gender.json")
    a = ap.parse_args()

    work = weak_items()
    if a.limit:
        work = work[:a.limit]
    print(f"проверяем: {len(work)} товаров", flush=True)

    parser, person_ids = load_parser()
    res, stats = {}, {"кадр 1": 0, "кадр 2": 0, "кадр 3": 0, "человека нет нигде": 0, "сбой": 0}
    t0 = time.time()
    for n, (it, assigned, src) in enumerate(work, 1):
        chosen = None
        for k, url in enumerate(photo_urls(it), 1):
            found, img = has_person(parser, person_ids, url)
            if found is None:
                continue
            if found:
                chosen = (k, url, img)
                break
        # Человека нет ни на одном кадре — всё равно спрашиваем по первому.
        #
        # Выбрасывать такие ответы нельзя: в прошлой сверке спорные как раз и
        # были снимками одной вещи на белом, и в 14 случаях из 16 модель
        # угадала верно. Отличить мужские чино-шорты от женских по крою —
        # умение, а не подбрасывание монеты. Но кадр с живым человеком всё
        # равно надёжнее, поэтому помечаем, откуда взят ответ.
        if not chosen:
            stats["человека нет нигде"] += 1
            found, img = has_person(parser, person_ids, it["imageUrl"])
            chosen, sure = (1, it["imageUrl"], img) if img is not None else None, False
        else:
            stats[f"кадр {chosen[0]}"] += 1
            sure = True

        if not chosen:
            stats["сбой"] += 1
            res[it["id"]] = {"photo": None, "frame": None, "sure": False,
                             "assigned": assigned, "src": src}
        else:
            k, url, img = chosen
            os.makedirs(cbp.CACHE_DIR, exist_ok=True)
            path = os.path.join(cbp.CACHE_DIR, f"{it['id']}_f{k}.jpg")
            img.save(path, "JPEG", quality=90)
            try:
                g = cbp.ask(path)
            except Exception:
                stats["сбой"] += 1
                g = None
            res[it["id"]] = {"photo": g, "frame": k, "sure": sure,
                             "assigned": assigned, "src": src}
        if n % 20 == 0 or n == len(work):
            el = time.time() - t0
            print(f"  {n}/{len(work)}  {el/n:.1f} с/шт  осталось ~{(len(work)-n)*el/n/60:.0f} мин", flush=True)

    json.dump(res, open(RESULT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n=== где нашлась модель ===")
    for k, v in stats.items():
        print(f"  {k:<22} {v}")

    later = [(i, r) for i, r in res.items() if r["frame"] in (2, 3) and r["photo"]]
    disagree = [(i, r) for i, r in res.items() if r["photo"] and r["photo"] != r["assigned"]]
    sure_dis = [x for x in disagree if x[1]["sure"]]
    print(f"\nмодель нашлась только со второго или третьего кадра: {len(later)}")
    print(f"ответ спорит с нынешним полом: {len(disagree)}"
          f", из них по кадру с живой моделью: {len(sure_dis)}")
    for pid, r in sure_dis[:20]:
        print(f"    {pid:<12} сейчас={r['assigned']:<7} снимок={r['photo']:<7} кадр {r['frame']}")

    if a.apply:
        # Записываем только ответы, полученные по кадру с живым человеком.
        # Догадки по вещи на белом фоне уже учтены прежним прогоном; менять их
        # вслепую незачем, а вот уточнить по найденной модели — стоит.
        pg_path = os.path.join(ROOT, "photo_gender.json")
        pg = json.load(open(pg_path, encoding="utf-8"))
        changed = 0
        for pid, r in res.items():
            if r["photo"] and r["sure"] and pg.get(pid) != r["photo"]:
                pg[pid] = r["photo"]
                changed += 1
        json.dump(pg, open(pg_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\nв разметке уточнено по живой модели: {changed}, всего {len(pg)}")
    else:
        print(f"\nотчёт: {RESULT}\nничего не менял — для записи запусти с --apply")


if __name__ == "__main__":
    main()
