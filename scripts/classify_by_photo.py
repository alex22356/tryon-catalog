"""
Определяет пол товара по его фотографии — для позиций, где текстовых признаков нет.

Зачем: у 449 товаров пол не указан ни в названии, ни в бренде, ни в размерах,
ни в одной из 50 колонок фида Awin. Страница магазина закрыта Azure WAF.
Остаётся снимок — на нём видно и модель, и крой.

Работает локально через Ollama (qwen2.5vl:3b), бесплатно, ~7 с на картинку.

    python scripts/classify_by_photo.py --validate 60   # измерить точность
    python scripts/classify_by_photo.py --run           # разметить неизвестные
    python scripts/classify_by_photo.py --apply         # записать в каталог
"""
import argparse
import base64
import collections
import json
import os
import random
import shutil
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "public", "catalog.json")
CACHE_DIR = os.path.join(ROOT, "photo_cache")
RESULTS = os.path.join(ROOT, "photo_gender.json")
OLLAMA = "http://localhost:11434/api/generate"
MODEL = "qwen2.5vl:3b"

PROMPT = (
    "Look at this clothing product photo from an online shop. "
    "Is this garment sold for women or for men? "
    "Judge by the person wearing it and by the cut of the garment. "
    "Answer with exactly one word: WOMEN, MEN, or UNSURE."
)


def load_catalog():
    with open(CATALOG, encoding="utf-8") as f:
        doc = json.load(f)
    return doc, doc["items"]


def fetch_image(item):
    """Скачивает фото один раз и кладёт в кеш."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{item['id']}.jpg")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path
    req = urllib.request.Request(item["imageUrl"], headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=40).read()
    with open(path, "wb") as f:
        f.write(data)
    return path


def ask(path):
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    body = json.dumps({
        "model": MODEL,
        "prompt": PROMPT,
        "images": [b64],
        "stream": False,
        "options": {"temperature": 0},   # без разброса: один снимок — один ответ
    }).encode()
    req = urllib.request.Request(OLLAMA, data=body,
                                 headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=240).read())
    if "error" in resp:
        raise RuntimeError(resp["error"])
    word = (resp.get("response") or "").strip().upper()
    for key, val in (("WOMEN", "female"), ("MEN", "male")):
        if key in word:
            return val
    return None


def load_results():
    if os.path.exists(RESULTS):
        with open(RESULTS, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_results(d):
    with open(RESULTS, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def validate(items, n):
    """Проверка на товарах, где пол УЖЕ известен: сколько раз модель совпадёт."""
    known = [i for i in items if i.get("gender") in ("male", "female") and i.get("imageUrl")]
    random.seed(42)
    sample = random.sample(known, min(n, len(known)))
    print(f"проверка на {len(sample)} позициях с известным полом\n")

    hits = collections.Counter()
    errors = []
    t0 = time.time()
    for k, it in enumerate(sample, 1):
        try:
            got = ask(fetch_image(it))
        except Exception as e:
            print(f"  [{k}] ошибка: {type(e).__name__}")
            hits["сбой"] += 1
            continue
        truth = it["gender"]
        if got is None:
            hits["не уверена"] += 1
        elif got == truth:
            hits["верно"] += 1
        else:
            hits["ОШИБКА"] += 1
            errors.append((truth, got, it["name"]))
        if k % 10 == 0:
            print(f"  {k}/{len(sample)}  {dict(hits)}")

    total = sum(hits.values())
    decided = hits["верно"] + hits["ОШИБКА"]
    print(f"\n--- итог за {(time.time()-t0)/60:.1f} мин ---")
    for key, v in hits.most_common():
        print(f"  {key:12s} {v:4d}  ({v/total*100:.0f}%)")
    if decided:
        print(f"\n  точность там, где ответила: {hits['верно']/decided*100:.1f}%")
    if errors:
        print("\n  где ошиблась:")
        for truth, got, name in errors[:10]:
            print(f"    правильно {truth:6s}, ответила {got:6s} | {name[:52]}")
    return hits


def run(items):
    todo = [i for i in items
            if i.get("gender") not in ("male", "female", "kids") and i.get("imageUrl")]
    done = load_results()
    todo = [i for i in todo if i["id"] not in done]
    print(f"к распознаванию: {len(todo)} (уже готово {len(done)})")

    t0 = time.time()
    for k, it in enumerate(todo, 1):
        try:
            got = ask(fetch_image(it))
        except Exception as e:
            print(f"  [{k}] {it['id']} сбой: {type(e).__name__} {e}")
            continue
        done[it["id"]] = got or "unsure"
        if k % 20 == 0:
            save_results(done)
            el = time.time() - t0
            print(f"  {k}/{len(todo)}  {el/k:.1f} с/шт  осталось ~{(len(todo)-k)*el/k/60:.0f} мин")
    save_results(done)
    print(f"\nготово: {collections.Counter(done.values())}")


def apply_results(doc, items):
    done = load_results()
    if not done:
        print("нет файла с результатами — сперва --run")
        return
    n = 0
    for it in items:
        g = done.get(it["id"])
        if g in ("male", "female") and it.get("gender") not in ("male", "female", "kids"):
            it["gender"] = g
            n += 1
    backup = CATALOG + ".bak2"
    if not os.path.exists(backup):
        shutil.copy(CATALOG, backup)
    with open(CATALOG, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    print(f"записано {n} позиций (копия в {os.path.basename(backup)})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", type=int, metavar="N")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    doc, items = load_catalog()
    if a.validate:
        validate(items, a.validate)
    elif a.run:
        run(items)
    elif a.apply:
        apply_results(doc, items)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
