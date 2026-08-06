"""
Проверка каталога на противоречия. Запускать ПОСЛЕ каждой пересборки.

Ничего не чинит — только считает и показывает. Возвращает ненулевой код,
если доля проблем выше порога, чтобы конвейер падал заметно, а не тихо.

    python scripts/audit_catalog.py
    python scripts/audit_catalog.py --file public/catalog.json --max-unknown 5
"""
import argparse
import collections
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WORD = lambda *w: re.compile(r"\b(" + "|".join(w) + r")\b", re.I)

FEMALE_WORDS = WORD("women", "womens", "woman", "female", "ladies", "lady")
MALE_WORDS = WORD("men", "mens", "man", "male", "gents", "gent")
# «junior» и «kids» — детское. «baby tee» и «babydoll» — ВЗРОСЛЫЕ женские фасоны,
# поэтому голого «baby» здесь нет: на этом уже обжигались.
KIDS_WORDS = WORD("junior", "kids", "childrens", "infant", "toddler")

# Слова, по которым видно категорию. Порядок важен: обувь проверяется первой,
# иначе «High Top» у кроссовок уедет в TOP.
CATEGORY_WORDS = [
    ("FOOTWEAR", WORD("trainer", "trainers", "shoe", "shoes", "boot", "boots", "sandal",
                      "sandals", "loafer", "loafers", "heel", "heels", "sneaker", "sneakers",
                      "slider", "sliders", "pump", "pumps", "mule", "mules", "espadrille",
                      "plimsoll", "brogue", "flip")),
    ("FULL_BODY", WORD("dress", "dresses", "jumpsuit", "playsuit", "romper", "bodysuit",
                       "catsuit", "gown")),
    ("BOTTOM", WORD("jean", "jeans", "trouser", "trousers", "pant", "pants", "shorts",
                    "skirt", "skirts", "legging", "leggings", "jogger", "joggers", "chino",
                    "chinos", "culotte", "culottes", "cargo", "cargos")),
    ("TOP", WORD("shirt", "tee", "t-shirt", "blouse", "hoodie", "hoody", "jumper", "sweater",
                 "sweatshirt", "cardigan", "jacket", "coat", "vest", "polo", "knit", "bralet",
                 "gilet", "blazer", "parka", "tank", "cami", "waistcoat", "bodywarmer")),
]


def load(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    items = d if isinstance(d, list) else (d.get("items") or d.get("products") or [])
    return d, items


def guess_categories(name):
    """Все категории, на которые намекает название. Обувь имеет приоритет."""
    hits = []
    for cat, rx in CATEGORY_WORDS:
        if rx.search(name):
            hits.append(cat)
            if cat == "FOOTWEAR":
                return hits  # «High Top» у кроссовка — это высота, а не футболка
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.path.join("public", "catalog.json"))
    ap.add_argument("--max-unknown", type=float, default=8.0,
                    help="допустимый %% позиций с неопределённым полом")
    ap.add_argument("--max-conflict", type=float, default=1.0,
                    help="допустимый %% прямых противоречий")
    a = ap.parse_args()

    path = a.file if os.path.isabs(a.file) else os.path.join(ROOT, a.file)
    _, items = load(path)
    n = len(items)
    print(f"каталог: {path}")
    print(f"позиций: {n}\n")

    if not n:
        print("ПУСТО")
        return 1

    # --- распределения ---
    for field in ("category", "gender"):
        c = collections.Counter(str(i.get(field)) for i in items)
        line = "  ".join(f"{k}={v}" for k, v in c.most_common())
        print(f"{field:9s} {line}")

    # --- противоречия ---
    cat_conflict, gender_conflict, kids = [], [], []
    for it in items:
        name = it.get("name") or ""
        hits = guess_categories(name)
        if hits and it.get("category") not in hits:
            cat_conflict.append((it, hits))

        f, m = bool(FEMALE_WORDS.search(name)), bool(MALE_WORDS.search(name))
        g = it.get("gender")
        if f and not m and g == "male":
            gender_conflict.append((it, "female"))
        elif m and not f and g == "female":
            gender_conflict.append((it, "male"))

        if KIDS_WORDS.search(name):
            kids.append(it)

    unknown = [i for i in items if i.get("gender") in (None, "", "unisex", "unknown")]
    no_image = [i for i in items if not i.get("imageUrl")]
    no_price = [i for i in items if not i.get("price")]

    print(f"\n--- проблемы ---")
    print(f"пол противоречит названию : {len(gender_conflict):5d}  ({len(gender_conflict)/n*100:.1f}%)")
    print(f"категория противоречит имени: {len(cat_conflict):5d}  ({len(cat_conflict)/n*100:.1f}%)")
    print(f"пол не определён           : {len(unknown):5d}  ({len(unknown)/n*100:.1f}%)")
    print(f"детские вещи в каталоге    : {len(kids):5d}")
    print(f"без картинки               : {len(no_image):5d}")
    print(f"без цены                   : {len(no_price):5d}")

    for title, rows in (("пол", gender_conflict), ("категория", cat_conflict)):
        if rows:
            print(f"\n  примеры ({title}):")
            for it, should in rows[:6]:
                got = it.get("gender") if title == "пол" else it.get("category")
                exp = should if isinstance(should, str) else "/".join(should)
                print(f"    [{str(got):9s} -> {exp:9s}] {(it.get('name') or '')[:56]}")

    if unknown:
        brands = collections.Counter(i.get("brand") for i in unknown)
        print(f"\n  бренды с неопределённым полом: "
              + ", ".join(f"{b}({c})" for b, c in brands.most_common(8)))

    # --- вердикт ---
    bad = False
    if len(unknown) / n * 100 > a.max_unknown:
        print(f"\nПРОВАЛ: неопределённых {len(unknown)/n*100:.1f}% > {a.max_unknown}%")
        bad = True
    if len(gender_conflict) / n * 100 > a.max_conflict:
        print(f"ПРОВАЛ: противоречий по полу {len(gender_conflict)/n*100:.1f}% > {a.max_conflict}%")
        bad = True
    if not bad:
        print("\nПРОВЕРКА ПРОЙДЕНА")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
