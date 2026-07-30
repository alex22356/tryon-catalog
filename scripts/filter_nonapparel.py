#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Отсев НЕ-одежды из каталога.

Закладка и кнопка «Earn» берут всё, что попалось на странице, включая блоки
«рекомендуем» — а там бывают игрушки, чехлы, кружки, наклейки. Такой товар
нельзя ни примерить, ни показать на манекене.

Проверка двухступенчатая, чтобы не жечь время:
  1) явные слова (toy, squishy, mug, phone case...) — сразу в OTHER;
  2) спорные (нет ни одного «одёжного» слова в названии) — спрашиваем локальный ИИ.

Товары с category = OTHER не идут ни в примерку, ни в публикацию.

Запуск:
    python scripts/filter_nonapparel.py            # проверить и пометить
    python scripts/filter_nonapparel.py --dry      # только показать, не менять
"""

import io
import os
import re
import sys
import json
import time
import argparse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRODUCTS = os.path.join(HERE, "shein_products.json")
OLLAMA = "http://localhost:11434/api/generate"
MODEL = "qwen3:4b-instruct"

# явно не одежда
NON_APPAREL = re.compile(
    r"\b(toy|toys|squishy|squish|plush|plushie|stress ball|fidget|pudding|slime|"
    r"keychain|key ring|phone case|phone holder|mug|cup|bottle|sticker|stickers|"
    r"lamp|pillow|cushion|blanket|rug|curtain|towel|candle|vase|"
    r"notebook|pencil|charger|cable|earphone|headphone|speaker|mouse pad|"
    r"lipstick|mascara|shampoo|face cream|body lotion|nail polish|"
    r"storage box|organizer|hanger|laundry basket|glue|scissors)\b", re.I)
# ВНИМАНИЕ: не добавлять сюда общие слова (light, cream, nail, brush, set) —
# они ловят одежду: "Light Beige", "Cream Knit", "Nail Detail".

# слова, по которым видно, что это носимая вещь
APPAREL = re.compile(
    r"\b(shirt|t-?shirt|tee|top|blouse|cardigan|sweater|sweatshirt|hoodie|jacket|"
    r"coat|blazer|vest|cami|camisole|tank|dress|gown|skirt|pants|trousers|jeans|"
    r"shorts|leggings|jumpsuit|romper|bodysuit|set|suit|boot|boots|shoe|shoes|"
    r"sneaker|sneakers|heel|heels|sandal|sandals|loafer|flats|bra|bralette|"
    r"swimsuit|bikini|robe|kimono|poncho|scarf|hat|cap|belt|bag|handbag|"
    r"necklace|earring|bracelet|ring|sunglasses|socks|tights|pyjama|pajama)\b", re.I)

QUESTION = ("Is this product something a person WEARS on their body "
            "(clothing, footwear, or a wearable accessory)? "
            "Answer with one word: YES or NO.\n\nProduct: %s\nAnswer:")


VISION_MODEL = "qwen2.5vl:3b"
GARMENTS = os.path.join(HERE, "garments")
VISION_Q = ("Look at this product photo. Does it show clothing, footwear or a wearable "
            "accessory that a person puts on their body? Answer one word: YES or NO.")


def ask_photo(pid):
    """Судим по ФОТО — точнее, чем по обрезанному названию. None = не смог ответить."""
    import base64
    path = os.path.join(GARMENTS, pid + ".jpg")
    if not os.path.exists(path):
        return None
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    body = json.dumps({"model": VISION_MODEL, "prompt": VISION_Q, "images": [b64],
                       "stream": False,
                       "options": {"temperature": 0, "num_predict": 5}}).encode()
    req = urllib.request.Request(OLLAMA, data=body,
                                 headers={"Content-Type": "application/json"})
    raw = json.load(urllib.request.urlopen(req, timeout=300))["response"].upper()
    if "YES" in raw:
        return True
    if "NO" in raw:
        return False
    return None


def ask(name):
    body = json.dumps({"model": MODEL, "prompt": QUESTION % name, "stream": False,
                       "options": {"temperature": 0, "num_predict": 5}}).encode()
    req = urllib.request.Request(OLLAMA, data=body,
                                 headers={"Content-Type": "application/json"})
    raw = json.load(urllib.request.urlopen(req, timeout=120))["response"].upper()
    if "NO" in raw and "YES" not in raw:
        return False
    return True


def alive():
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=4)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    items = json.load(io.open(PRODUCTS, encoding="utf-8"))
    obvious, doubtful = [], []
    for p in items:
        if p.get("category") == "OTHER":
            continue
        n = p["name"]
        if NON_APPAREL.search(n):
            obvious.append(p)
        elif not APPAREL.search(n):
            doubtful.append(p)

    print(f"всего товаров: {len(items)}")
    print(f"явно не одежда: {len(obvious)}")
    print(f"спорных (спрошу ИИ): {len(doubtful)}")

    flagged = list(obvious)
    for p in obvious:
        print(f"  ✗ {p['name'][:58]}")

    if doubtful and alive():
        print(f"\nспрашиваю локальный ИИ про {len(doubtful)}…")
        for p in doubtful:
            try:
                verdict = ask_photo(p["id"])          # смотрим фото
                if verdict is None:
                    verdict = ask(p["name"])          # запасной путь — по названию
                if not verdict:
                    flagged.append(p)
                    print(f"  ✗ по фото: не носимое — {p['name'][:48]}")
            except Exception as e:
                print(f"  ! {p['id']}: {str(e)[:50]}")
    elif doubtful:
        print("Ollama не отвечает — спорные пропускаю")

    if args.dry:
        print(f"\n[dry] помечено бы: {len(flagged)}")
        return

    for p in flagged:
        p["category"] = "OTHER"
        p["excluded"] = "not wearable"
    json.dump(items, io.open(PRODUCTS, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    wear = sum(1 for p in items if p.get("category") != "OTHER")
    print(f"\nпомечено как не-одежда: {len(flagged)}")
    print(f"остаётся носимых товаров: {wear}")


if __name__ == "__main__":
    main()
