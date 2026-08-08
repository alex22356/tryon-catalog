"""
Подкатегории: из 60+ видов магазина делаем ~20 понятных групп.

Зачем: в приложении четыре крупные категории (верх, низ, обувь, цельное), а у
магазина внутри них 31 вид верха и 22 низа — «Short Sleeve T-shirts»,
«Round Neck Jumpers» и так далее. Для иконок это слишком мелко, поэтому
сводим к группам, по которым человек действительно ищет.

Используется в ingest_awin.py: поле subCategory уезжает в каталог.
"""

# merchant_category магазина -> ключ подкатегории.
# Ключ латиницей: он уходит в данные, а перевод живёт в приложении.
SUBCATEGORY = {
    # ── верх ───────────────────────────────────────────────
    "Short Sleeve T-shirts": "tshirt",
    "Long Sleeve T-shirts": "tshirt",
    "Short Sleeve Tops": "tshirt",
    "Long Sleeve Tops": "tshirt",
    "Sleeveless Tops": "top",
    "Strapless Tops": "top",
    "Cami Tops": "top",
    "Vests": "top",

    "Casual Shirts": "shirt",
    "Shirts": "shirt",
    "Dress Shirts": "shirt",
    "Short Sleeve Polo Shirts": "polo",
    "Long Sleeve Polo Shirts": "polo",

    "Zip Sweatshirts": "hoodie",
    "Hooded Sweatshirts": "hoodie",
    "Crew Neck Sweatshirts": "hoodie",

    "Round Neck Jumpers": "jumper",
    "V-Neck Jumpers": "jumper",
    "Polo Neck Jumpers": "jumper",
    "Cardigans": "jumper",

    "Jackets": "jacket",
    "Denim Jackets": "jacket",
    "Biker Jackets": "jacket",
    "Coats": "jacket",
    "Gilets": "jacket",
    "Plain Shacket": "jacket",
    "Check Shacket": "jacket",
    "Print Shacket": "jacket",

    "Blazer": "blazer",
    "Blazers": "blazer",
    "Waistcoat": "blazer",
    "Waistcoats": "blazer",

    # ── низ ────────────────────────────────────────────────
    "Plain Trousers": "trousers",
    "Patterned Trousers": "trousers",
    "Cargo Trousers": "trousers",

    "Straight Jeans": "jeans",
    "Slim Jeans": "jeans",
    "Wide Leg Jeans": "jeans",
    "Loose Jeans": "jeans",
    "Skinny Jeans": "jeans",
    "Flare Jeans": "jeans",
    "Bootcut Jeans": "jeans",
    "Mom Jeans": "jeans",

    "Fashion Shorts": "shorts",
    "Denim Shorts": "shorts",
    "Sport Shorts": "shorts",
    "Cargo Shorts": "shorts",
    "Beach Shorts": "shorts",

    "Short Skirts": "skirt",
    "Mini Skirt": "skirt",
    "Midi Skirts": "skirt",
    "Maxi Skirts": "skirt",

    "Open Hem Joggers": "joggers",
    "Cuffed Joggers": "joggers",
    "Sport Leggings": "joggers",

    # ── цельное ────────────────────────────────────────────
    "Short Dresses": "dress_short",
    "Mini Dresses": "dress_short",
    "Midi Dresses": "dress_midi",
    "Long Dresses": "dress_long",
    "Maxi Dresses": "dress_long",
    "Body Suits": "bodysuit",
    "Playsuits": "jumpsuit",
    "Jumpsuits": "jumpsuit",
    "Pyjamas": "pyjamas",

    # Костюмы и комплекты. В фиде DV8 их сейчас нет, но заготовлено под
    # будущие магазины: женские костюмы существуют не меньше мужских,
    # и когда лента их принесёт, они лягут сюда без правок.
    "Suits": "suit",
    "Suit Sets": "suit",
    "Two Piece Sets": "suit",
    "Co-ords": "suit",
    "Co-ord Sets": "suit",
    "Tracksuits": "suit",

    # ── обувь ──────────────────────────────────────────────
    "Laced Trainers": "trainers",
    "Slip On Trainers": "trainers",
    "Laced Shoes": "shoes",
    "Slip On Shoes": "shoes",
    "Laced Boots": "boots",
    "Slip On Boots": "boots",
    "Knee High Boots": "boots",
    "Thigh High Boots": "boots",
    "Heels": "heels",
    "Sandals": "sandals",
    "Flip Flops": "sandals",
    "Slide On Flip Flops": "sandals",
}


def sub_category(merchant_category):
    """Ключ подкатегории или None, если вид магазина незнакомый."""
    return SUBCATEGORY.get((merchant_category or "").strip())


if __name__ == "__main__":
    # Самопроверка на живом фиде: какие виды магазина остались без группы
    import collections
    import csv
    import gzip
    import os
    import re

    dest = os.path.join(os.environ.get("TEMP", "/tmp"), "dv8_feed.csv.gz")
    if not os.path.exists(dest):
        print("фида нет — запусти сначала build_catalog.py")
        raise SystemExit

    seen, unknown = collections.Counter(), collections.Counter()
    with gzip.open(dest, "rt", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f, delimiter="|"):
            mc = (row.get("merchant_category") or "").strip()
            if not mc:
                continue
            key = sub_category(mc)
            if key:
                seen[key] += 1
            else:
                unknown[mc] += 1

    print(f"групп получилось: {len(seen)}")
    for k, v in seen.most_common():
        print(f"   {k:14s} {v}")
    if unknown:
        print(f"\nбез группы ({sum(unknown.values())} строк):")
        for k, v in unknown.most_common(15):
            print(f"   {k:34s} {v}")
