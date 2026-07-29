#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ОДИН запуск — весь конвейер каталога. Максимум автономности без обхода чужих защит.

Делает по порядку:
  1) ingest   — разбирает НОВЫЕ ссылки из shein_links.txt (имя, фото, категория) → shein_products.json
  2) classify — уточняет категорию ЛОКАЛЬНЫМ ИИ (Ollama), где ключевые слова ненадёжны
  3) publish  — готовые примерки из tryon_out/ кладёт в приложение и прописывает в catalog.json
  4) report   — что осталось примерить

Ссылки берутся легально: фид Awin (когда одобрят), Convert Link, или tools/link_grabber.html
во время твоего обычного просмотра. Автопарсинг SHEIN невозможен — отдают капчу.

Запуск:
    python scripts/run_pipeline.py
    python scripts/run_pipeline.py --no-ai      # без локального ИИ (только ключевые слова)
"""

import os
import sys
import json
import time
import argparse
import subprocess
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(HERE, "scripts")
PRODUCTS = os.path.join(HERE, "shein_products.json")
GARMENTS = os.path.join(HERE, "garments")
TRYON = os.path.join(HERE, "tryon_out")

OLLAMA = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3:4b-instruct"
VALID = {"TOP", "BOTTOM", "FULL_BODY", "FOOTWEAR", "ACCESSORY"}


def hr(t):
    print(f"\n{'─' * 58}\n{t}\n{'─' * 58}", flush=True)


def run(script, *args):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *args],
                          cwd=HERE).returncode


# ─────────────── шаг 2: локальный ИИ для категорий ───────────────
def ollama_up():
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        return True
    except Exception:
        return False


def ai_category(name):
    prompt = ("Classify this clothing product into exactly one category: "
              "TOP, BOTTOM, FULL_BODY, FOOTWEAR, ACCESSORY.\n"
              "FULL_BODY = dresses, jumpsuits, sets. ACCESSORY = bags, jewellery, hats, belts.\n"
              "Answer with the single category word only.\n\n"
              f"Product: {name}\nCategory:")
    body = json.dumps({"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0, "num_predict": 8}}).encode()
    req = urllib.request.Request(OLLAMA, data=body,
                                 headers={"Content-Type": "application/json"})
    raw = json.load(urllib.request.urlopen(req, timeout=120))["response"]
    for w in VALID:
        if w in raw.upper():
            return w
    return None


AMBIGUOUS_HINTS = ("set", "2pcs", "2 pcs", "piece", "romper", "jumpsuit",
                   "co-ord", "coord", "bodysuit", "vest", "cover")


def needs_ai(p):
    """Уточняем ИИ только спорные: наборы/комбинезоны и всё, что упало в TOP по дефолту."""
    n = p.get("name", "").lower()
    if any(h in n for h in AMBIGUOUS_HINTS):
        return True
    return p.get("category") == "TOP" and not any(
        k in n for k in ("shirt", "tee", "top", "cardigan", "jacket", "blouse",
                         "sweater", "hoodie", "coat", "cami", "tank", "peplum"))


def classify(use_ai):
    if not os.path.exists(PRODUCTS):
        print("нет shein_products.json — пропускаю")
        return
    items = json.load(open(PRODUCTS, encoding="utf-8"))
    todo = [p for p in items if needs_ai(p) and not p.get("aiCategory")]
    if not todo:
        print("спорных товаров нет — категории в порядке")
        return
    if not use_ai:
        print(f"спорных: {len(todo)} (ИИ отключён флагом --no-ai)")
        return
    if not ollama_up():
        print(f"спорных: {len(todo)}, но Ollama не отвечает — пропускаю.")
        print("  подсказка: запусти 'ollama serve'")
        return

    print(f"уточняю локальным ИИ ({OLLAMA_MODEL}): {len(todo)} шт.")
    changed = 0
    for p in todo:
        try:
            cat = ai_category(p["name"])
        except Exception as e:
            print("  ! ошибка ИИ:", str(e)[:70])
            break
        if not cat:
            continue
        p["aiCategory"] = cat
        if cat != p.get("category"):
            print(f"  {p['category']} → {cat}   {p['name'][:46]}")
            p["category"] = cat
            changed += 1
    json.dump(items, open(PRODUCTS, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"уточнено: {len(todo)}, исправлено категорий: {changed}")


# ─────────────── шаг 4: отчёт ───────────────
def report():
    products = json.load(open(PRODUCTS, encoding="utf-8")) if os.path.exists(PRODUCTS) else []
    done = {os.path.splitext(f)[0] for f in os.listdir(TRYON)} if os.path.isdir(TRYON) else set()
    by_cat = {}
    pending = []
    for p in products:
        c = p.get("category", "?")
        by_cat[c] = by_cat.get(c, 0) + 1
        if p["id"] not in done and c != "FOOTWEAR":
            pending.append(p)

    print(f"товаров собрано: {len(products)}   {by_cat}")
    print(f"примерок готово: {len(done)}")
    print(f"ждут примерки:   {len(pending)}  (обувь не примеряется — VTON её не надевает)")
    no_price = [p for p in products if not p.get("price")]
    if no_price:
        print(f"без цены:        {len(no_price)}  ← заполнятся из фида Awin")
    if pending:
        print("\nследующие на примерку:")
        for p in pending[:8]:
            print(f"  · [{p['category']}] {p['name'][:52]}")
        if len(pending) > 8:
            print(f"  … и ещё {len(pending) - 8}")
        print("\nзапусти:  python scripts/gemini_browser_runner.py")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ai", action="store_true", help="без локального ИИ")
    args = ap.parse_args()
    t0 = time.time()

    hr("1/4  Разбор новых ссылок")
    run("ingest_shein.py")

    hr("2/4  Уточнение категорий (локальный ИИ)")
    classify(use_ai=not args.no_ai)

    hr("3/4  Публикация готовых примерок в приложение")
    if os.path.isdir(TRYON) and any(f.lower().endswith((".png", ".jpg"))
                                    for f in os.listdir(TRYON)):
        run("publish_tryon.py")
    else:
        print("готовых примерок нет — пропускаю")

    hr("4/4  Итог")
    report()
    print(f"\nвсё за {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
