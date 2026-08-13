"""
Делает недостающие картинки карточек из вырезок.

Зачем. Семь ручных позиций SHEIN ссылались на thumb_<id>.jpg, которого никто
никогда не создавал: в ленте они выглядели пустыми плитками — цена есть,
изображения нет. Человек видит дыру и не понимает, товар это или сбой.

Брать неоткуда, кроме собственных файлов: страница магазина закрыта, а
исходные снимки мы не храним. Зато есть вырезка — вещь на прозрачном фоне.
Обрезаем её по краям вещи, кладём на белое, и получается ровно то, что нужно
карточке.

    python scripts/make_thumbs.py          # посмотреть, чего не хватает
    python scripts/make_thumbs.py --write  # создать файлы
"""
import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "public", "catalog.json")
PRODUCTS = os.path.join(ROOT, "products")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def missing():
    """Товары, чья картинка карточки — наш собственный файл, которого нет."""
    items = json.load(open(CATALOG, encoding="utf-8"))["items"]
    out = []
    for it in items:
        url = it.get("imageUrl") or ""
        if "/products/" not in url:
            continue                      # чужая картинка, не наша забота
        name = url.rsplit("/", 1)[-1]
        if not os.path.exists(os.path.join(PRODUCTS, name)):
            out.append((it, name))
    return out


def build(pid, dst_name):
    """Собирает картинку карточки из вырезки товара."""
    src = os.path.join(PRODUCTS, f"cut_{pid}.webp")
    if not os.path.exists(src):
        return None
    img = Image.open(src).convert("RGBA")
    alpha = np.asarray(img)[..., 3]
    ys, xs = np.where(alpha > 24)
    if not len(ys):
        return None
    # поля вокруг вещи, чтобы она не упиралась в край
    pad = int(max(img.size) * 0.03)
    box = (max(0, xs.min() - pad), max(0, ys.min() - pad),
           min(img.width, xs.max() + pad), min(img.height, ys.max() + pad))
    cut = img.crop(box)
    canvas = Image.new("RGB", cut.size, "white")
    canvas.paste(cut, mask=cut.split()[3])
    canvas.thumbnail((900, 900), Image.LANCZOS)
    canvas.save(os.path.join(PRODUCTS, dst_name), "JPEG", quality=88)
    return canvas.size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    gaps = missing()
    print(f"карточек без картинки: {len(gaps)}")
    made = skipped = 0
    for it, name in gaps:
        if not a.write:
            print(f"  {it['id']:<16} нужен {name}")
            continue
        size = build(it["id"], name)
        if size:
            made += 1
            print(f"  {it['id']:<16} -> {name}  {size[0]}x{size[1]}")
        else:
            skipped += 1
            print(f"  {it['id']:<16} вырезки нет, собрать не из чего")
    if a.write:
        print(f"\nсоздано: {made}, пропущено: {skipped}")
    else:
        print("\nничего не менял — для создания запусти с --write")


if __name__ == "__main__":
    main()
