#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Обогащение товаров атрибутами — ЛОКАЛЬНЫМ ИИ (Ollama), бесплатно и без внешних лимитов.

Из названия товара достаёт то, что нужно и для фильтров в приложении,
и для ИИ-стилиста, чтобы он мог осмысленно подбирать образы:

  gender    MALE | FEMALE | UNISEX | KIDS
  color     основной цвет
  season    SUMMER | WINTER | SPRING_FALL | ALL
  occasion  CASUAL | WORK | PARTY | SPORT | BEACH | HOME
  style     стиль одним словом (boho, y2k, minimal, western…)
  sleeve    SHORT | LONG | SLEEVELESS | NA
  fit       SLIM | REGULAR | LOOSE | NA

Идемпотентно: у кого атрибуты уже есть — пропускает.

Запуск:
    python scripts/enrich_attrs.py            # все без атрибутов
    python scripts/enrich_attrs.py --limit 20 # только первые 20 (проверить)
    python scripts/enrich_attrs.py --force    # переразметить всё заново
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

ENUMS = {
    "gender": ["MALE", "FEMALE", "UNISEX", "KIDS"],
    "season": ["SUMMER", "WINTER", "SPRING_FALL", "ALL"],
    "occasion": ["CASUAL", "WORK", "PARTY", "SPORT", "BEACH", "HOME"],
    "sleeve": ["SHORT", "LONG", "SLEEVELESS", "NA"],
    "fit": ["SLIM", "REGULAR", "LOOSE", "NA"],
}

PROMPT = """Extract clothing attributes from the product title. Answer with ONE LINE of JSON only, no explanation.

gender: MALE if the title says Men/Man/Male. KIDS if Kids/Girls/Boys/Baby/Toddler.
        Otherwise FEMALE (this is a womenswear catalogue — dresses, blouses, peplum,
        babydoll, ruffles, skirts are always FEMALE). Use UNISEX only for genuinely
        unisex basics with no gender hint at all.
color:  ONLY a real colour word: WHITE BLACK GREY BEIGE BROWN RED PINK ORANGE YELLOW
        GREEN BLUE NAVY PURPLE GOLD SILVER MULTI. Words like Solid/Print/Striped/
        Floral/Pattern are NOT colours — answer NA if no real colour is named.
season: SUMMER | WINTER | SPRING_FALL | ALL
occasion: CASUAL | WORK | PARTY | SPORT | BEACH | HOME
style: one short lowercase token: boho, y2k, minimal, western, elegant, street,
       vintage, sporty, romantic, classic, cottagecore
sleeve: SHORT | LONG | SLEEVELESS | NA
fit: SLIM | REGULAR | LOOSE | NA

Title: %s
JSON:"""

COLORS = {"WHITE", "BLACK", "GREY", "GRAY", "BEIGE", "BROWN", "RED", "PINK", "ORANGE",
          "YELLOW", "GREEN", "BLUE", "NAVY", "PURPLE", "GOLD", "SILVER", "MULTI",
          "CREAM", "IVORY", "KHAKI", "BURGUNDY", "TEAL", "MINT", "LILAC", "APRICOT"}

# ВАЖНО: только по границам слов. Иначе "Women's" ловится как "men's",
# а "Babydoll" — как "baby" (были такие баги).
WOMEN_RE = re.compile(r"\b(women|woman|womens|women's|ladies|lady)\b", re.I)
KIDS_RE = re.compile(r"\b(kid|kids|girl|girls|boy|boys|baby|babies|toddler|child|children)\b", re.I)
MALE_RE = re.compile(r"\b(men|mens|men's|man|mans|man's|male)\b", re.I)
# женские вещи по смыслу, даже если слова "women" в названии нет
FEMALE_ITEM_RE = re.compile(
    r"\b(dress|skirt|blouse|peplum|babydoll|bodysuit|cami|camisole|bra|bralette|"
    r"ruffle|ruffled|maxi|midi|leggings|jumpsuit|romper)\b", re.I)


def ollama(prompt, timeout=180):
    body = json.dumps({"model": MODEL, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0, "num_predict": 120}}).encode()
    req = urllib.request.Request(OLLAMA, data=body,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))["response"]


def parse_attrs(raw, name=""):
    m = re.search(r"\{.*?\}", raw, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    out = {}
    for k, allowed in ENUMS.items():
        v = str(d.get(k, "")).upper().strip()
        out[k] = v if v in allowed else allowed[-1]

    # цвет — только настоящий цвет, иначе NA (модель любит писать SOLID/PRINT)
    c = str(d.get("color", "")).strip().upper()
    out["color"] = c if c in COLORS else "NA"

    # стиль — цифры оставляем, иначе y2k превращается в yk
    out["style"] = re.sub(r"[^a-z0-9]", "", str(d.get("style", "")).lower())[:14] or "casual"

    # пол: название важнее догадки модели. Порядок важен —
    # "Women's" сильнее всего, иначе его перебьют слабые совпадения.
    n = name or ""
    if WOMEN_RE.search(n):
        out["gender"] = "FEMALE"
    elif KIDS_RE.search(n):
        out["gender"] = "KIDS"
    elif MALE_RE.search(n):
        out["gender"] = "MALE"
    elif FEMALE_ITEM_RE.search(n):
        out["gender"] = "FEMALE"
    return out


def alive():
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=4)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if not alive():
        print("Ollama не отвечает. Запусти:  ollama serve")
        return
    items = json.load(io.open(PRODUCTS, encoding="utf-8"))
    todo = [p for p in items if args.force or not p.get("attrs")]
    if args.limit:
        todo = todo[:args.limit]
    if not todo:
        print("все товары уже размечены")
        return

    print(f"размечаю локальным ИИ ({MODEL}): {len(todo)} из {len(items)}")
    t0 = time.time()
    done = fail = 0
    for i, p in enumerate(todo, 1):
        try:
            attrs = parse_attrs(ollama(PROMPT % p["name"]), p["name"])
        except Exception as e:
            print(f"  ! {p['id']}: {str(e)[:60]}")
            fail += 1
            continue
        if not attrs:
            fail += 1
            continue
        p["attrs"] = attrs
        p["gender"] = attrs["gender"]        # дублируем наверх — приложение фильтрует по нему
        done += 1
        if i <= 5 or i % 25 == 0:
            print(f"  [{i}/{len(todo)}] {attrs['gender']:6} {attrs['color']:8} "
                  f"{attrs['occasion']:7} {attrs['style']:10} {p['name'][:34]}")
        if i % 20 == 0:                       # сохраняем по ходу — прервёшь, не потеряешь
            json.dump(items, io.open(PRODUCTS, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)

    json.dump(items, io.open(PRODUCTS, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\nразмечено: {done}, ошибок: {fail}, за {time.time()-t0:.0f}s")

    # сводка — сразу видно, что получилось
    cnt = {}
    for p in items:
        g = (p.get("attrs") or {}).get("gender")
        if g:
            cnt[g] = cnt.get(g, 0) + 1
    print("по полу:", cnt)


if __name__ == "__main__":
    main()
