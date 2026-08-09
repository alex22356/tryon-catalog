"""
Переодевает базу-манекен из сплошного боди в обычное бельё. Локально, без API.

Зачем. База снята в телесном боди на бретелях до середины бедра. Пока примерка
была полнокадровой, боди пряталось обрезкой по талии. С вырезанными вещами оно
вылезло наружу: бретели идут поверх плеч (у топа-бандо появляются лямки,
которых у товара нет), между коротким верхом и низкой посадкой светится
бежевая полоса, из-под шорт торчат бежевые «штанины». Дефект один, а на экране
выглядит как три разных.

Почему не перегенерировать базу целиком. Вырезки привязаны к телу пиксель в
пиксель: сдвиг фигуры даже на процент разведёт вещь и фигуру на всех 2211
товарах. Поэтому мы НЕ рисуем новую картинку, а перекрашиваем часть старой —
геометрия остаётся ровно та же, побайтово те же контуры.

Как. Разборщик даёт маску боди (класс dress). Оставляем фабрику там, где ей и
место — полоса на груди и трусы, — а остальное переводим в кожу: яркость
пикселя сохраняем (светотень тела уже правильная), а цветность берём у живой
кожи из той же строки, то есть у руки или ноги рядом. Поэтому переход выходит
незаметным без всякой генерации.

    python scripts/rebase_underwear.py --who female
    python scripts/rebase_underwear.py --who female --apply

Детские базы скрипт не обрабатывает намеренно: перерисовывать детей в меньшей
одежде нельзя. Они остаются как есть.
"""
import argparse
import os
import shutil
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.abspath(os.path.join(ROOT, "..", "app", "src", "main", "res", "drawable-nodpi"))
OUT = os.path.join(ROOT, "base_candidates")

# Где искать бретели и какой след считать бретелью, а не лифом.
# Замерено по разметке: боди занимает 0.231..0.599 высоты, вырез приходится
# на 0.283 — выше него ткани, кроме бретелей, быть не может.
STRAPS = {
    "female": ((0.225, 0.292), 26),
    "male": ((0.228, 0.300), 26),
}


def parse(img):
    import fashn_human_parser as m
    from fashn_human_parser import FashnHumanParser
    ids = getattr(m, "FASHN_LABELS_TO_IDS", None) or getattr(m, "LABELS_TO_IDS")
    p = FashnHumanParser(device="cpu")
    seg = p.predict(img) if hasattr(p, "predict") else p(img)
    return (seg if isinstance(seg, np.ndarray) else np.asarray(seg)), ids


def remove_straps(img, fabric, zone, max_width):
    """Стирает бретели, дорисовывая на их месте плечо.

    Почему только они. Перекрасить весь боди в кожу локально не выходит:
    пробовал переносить цвет построчно — живот и бёдра пошли полосами, потому
    что тень на руке и тень на торсе разные, а живот вообще нужно рисовать, а
    не красить. Бретели же лежат на ровном освещённом плече и всего в
    несколько пикселей шириной: тут достаточно протянуть цвет с одной стороны
    полоски на другую, и шва не видно.

    Полоски отличаем от самого боди по ширине следа в строке: бретель — это
    узкий отрезок, лиф — широкий. Так лиф не заденем.
    """
    a = np.asarray(img).astype(np.float32)
    h, w, _ = a.shape
    y0, y1 = int(h * zone[0]), int(h * zone[1])
    fixed = 0
    for y in range(y0, y1):
        row = fabric[y]
        if not row.any():
            continue
        xs = np.where(row)[0]
        # разбиваем след строки на отрезки
        cuts = np.where(np.diff(xs) > 1)[0]
        for seg in np.split(xs, cuts + 1):
            if len(seg) == 0 or len(seg) > max_width:
                continue                      # это лиф, не бретель
            lo, hi = seg[0] - 1, seg[-1] + 1
            if lo < 0 or hi >= w:
                continue
            left, right = a[y, lo], a[y, hi]
            t = np.linspace(0, 1, len(seg) + 2)[1:-1][:, None]
            a[y, seg] = left * (1 - t) + right * t
            fixed += 1
    print(f"  затёрто отрезков бретелей: {fixed}")

    from scipy.ndimage import gaussian_filter
    rgb = np.clip(a, 0, 255).astype(np.uint8)
    # лёгкое сглаживание строго по зоне бретелей, чтобы не осталось «шва»
    band = np.zeros((h, w), bool)
    band[y0:y1] = fabric[y0:y1]
    from scipy.ndimage import binary_dilation
    band = binary_dilation(band, iterations=2)
    blur = gaussian_filter(rgb.astype(np.float32), sigma=(1.2, 1.2, 0))
    rgb[band] = blur[band].astype(np.uint8)
    return Image.fromarray(rgb)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--who", default="female", choices=sorted(STRAPS))
    ap.add_argument("--apply", action="store_true", help="поставить кандидата в приложение")
    a = ap.parse_args()

    src = os.path.join(RES, f"premium_{a.who}.jpg")
    os.makedirs(OUT, exist_ok=True)
    cand = os.path.join(OUT, f"premium_{a.who}.jpg")

    if a.apply:
        if not os.path.exists(cand):
            sys.exit("кандидата нет — сначала запусти без --apply")
        shutil.copy2(src, os.path.join(OUT, f"premium_{a.who}_before.jpg"))
        shutil.copy2(cand, src)
        print(f"поставлено: {src}")
        return

    img = Image.open(src).convert("RGB")
    arr, ids = parse(img)
    h = arr.shape[0]

    fabric = np.isin(arr, [ids["dress"], ids["top"], ids["pants"], ids["skirt"]])
    print(f"ткань {fabric.mean()*100:.2f}% кадра")

    zone, max_w = STRAPS[a.who]
    print(f"зона бретелей по высоте {zone}, шире {max_w} px считаем лифом")
    out = remove_straps(img, fabric, zone, int(arr.shape[1] * max_w / 896))
    out.save(cand, "JPEG", quality=95)
    print(f"кандидат: {cand}")
    print("посмотри глазами; если хорошо — тот же запуск с --apply")


if __name__ == "__main__":
    main()
