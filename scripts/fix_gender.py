"""
Доопределяет пол там, где лента Awin его не дала.

Идея: списки брендов в ingest_awin.py заполнены руками (15 женских, 4 мужских),
а брендов в каталоге 102. Вместо того чтобы дописывать их вручную вечно,
выводим карту бренд->пол ИЗ САМИХ ДАННЫХ: если у бренда 85%+ уже определённых
позиций одного пола, значит бренд этого пола.

Результат кладётся в brand_gender.json — файл можно править руками, а
ingest_awin.py его подхватит. Так знание накапливается, а не теряется
при каждой пересборке.

    python scripts/fix_gender.py              # только показать
    python scripts/fix_gender.py --apply      # записать
"""
import argparse
import collections
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAND_MAP = os.path.join(ROOT, "brand_gender.json")

WORD = lambda *w: re.compile(r"\b(" + "|".join(w) + r")\b", re.I)
FEMALE_WORDS = WORD("women", "womens", "woman", "female", "ladies", "lady")
MALE_WORDS = WORD("men", "mens", "man", "male", "gents", "gent")
# «baby tee» и «babydoll» — взрослые женские фасоны, голого «baby» тут нет
KIDS_WORDS = WORD("junior", "kids", "childrens", "infant", "toddler")

MIN_ITEMS = 8      # меньше — статистика ненадёжна
MIN_PURITY = 0.85  # ниже — бренд действительно выпускает обе линейки


def load(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    items = d if isinstance(d, list) else (d.get("items") or d.get("products") or [])
    return d, items


def derive_brand_map(items):
    """Бренд -> пол, если бренд достаточно однороден."""
    counts = collections.defaultdict(collections.Counter)
    for it in items:
        b = (it.get("brand") or "").strip().lower()
        if b and it.get("gender") in ("male", "female"):
            counts[b][it["gender"]] += 1

    decisive, mixed = {}, {}
    for b, c in counts.items():
        total = sum(c.values())
        top, n = c.most_common(1)[0]
        if total >= MIN_ITEMS and n / total >= MIN_PURITY:
            decisive[b] = top
        elif total >= MIN_ITEMS:
            mixed[b] = dict(c)
    return decisive, mixed


def size_system_gender(it):
    """UK 8/10/12 — женская сетка. 32R/34L (обхват+длина) — мужская."""
    st = it.get("sizeType")
    if st == "uk_numeric":
        return "female"
    if st == "waist":
        sizes = it.get("sizes") or []
        if any(re.search(r"\d+\s*[LRS]\b", str(s)) for s in sizes):
            return "male"
    return None


def resolve(it, brand_map):
    """Пол по убыванию надёжности. Возвращает (пол, источник)."""
    name = it.get("name") or ""

    if KIDS_WORDS.search(name):
        return "kids", "название"

    f, m = bool(FEMALE_WORDS.search(name)), bool(MALE_WORDS.search(name))
    if f and not m:
        return "female", "название"
    if m and not f:
        return "male", "название"

    b = (it.get("brand") or "").strip().lower()
    if b in brand_map:
        return brand_map[b], "бренд"

    g = size_system_gender(it)
    if g:
        return g, "сетка размеров"

    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.path.join("public", "catalog.json"))
    ap.add_argument("--also", nargs="*", default=["dv8_products.json"],
                    help="другие файлы, куда применить те же правки")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    path = a.file if os.path.isabs(a.file) else os.path.join(ROOT, a.file)
    doc, items = load(path)

    brand_map, mixed = derive_brand_map(items)
    print(f"позиций: {len(items)}")
    print(f"брендов однозначных: {len(brand_map)}  (>= {MIN_ITEMS} шт, >= {MIN_PURITY:.0%} одного пола)")
    print(f"брендов смешанных:  {len(mixed)}  — им нужен другой признак")
    for b, c in list(mixed.items())[:8]:
        print(f"    {b:20s} {c}")

    changes = collections.Counter()
    sources = collections.Counter()
    unresolved = []
    for it in items:
        cur = it.get("gender")
        if cur in ("male", "female"):
            continue  # уже определено лентой — не трогаем
        new, src = resolve(it, brand_map)
        if new:
            changes[(cur, new)] += 1
            sources[src] += 1
            it["_new_gender"] = new
        else:
            unresolved.append(it)

    print(f"\n--- что изменится ---")
    for (old, new), n in changes.most_common():
        print(f"  {str(old):8s} -> {new:8s}  {n} шт")
    print(f"  по признакам: {dict(sources)}")
    print(f"  ИТОГО: {sum(changes.values())}")
    print(f"\n  осталось неопределённых: {len(unresolved)} "
          f"({len(unresolved)/len(items)*100:.1f}%)")
    rest = collections.Counter(i.get("brand") for i in unresolved)
    print(f"  их бренды: " + ", ".join(f"{b}({c})" for b, c in rest.most_common(8)))

    if not a.apply:
        print("\n(показ без записи — добавь --apply)")
        return

    # карта брендов ложится в файл: её можно править руками, ingest её подхватит
    with open(BRAND_MAP, "w", encoding="utf-8") as f:
        json.dump({"derived": brand_map, "mixed_needs_review": mixed}, f,
                  ensure_ascii=False, indent=2)
    print(f"\nзаписана карта брендов: {os.path.basename(BRAND_MAP)}")

    ids = {}
    for it in items:
        if "_new_gender" in it:
            ids[it.get("id")] = it.pop("_new_gender")
            it["gender"] = ids[it["id"]]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    print(f"обновлён: {a.file} ({len(ids)} позиций)")

    for other in a.also:
        op = os.path.join(ROOT, other)
        if not os.path.exists(op):
            continue
        odoc, oitems = load(op)
        k = 0
        for it in oitems:
            if it.get("id") in ids:
                it["gender"] = ids[it["id"]]
                k += 1
        with open(op, "w", encoding="utf-8") as f:
            json.dump(odoc, f, ensure_ascii=False)
        print(f"обновлён: {other} ({k} позиций)")


if __name__ == "__main__":
    main()
