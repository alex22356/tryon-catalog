"""
Убирает звёздочку Gemini из правого нижнего угла базы манекена.

Фон там ровный, но с лёгким градиентом, поэтому просто залить белым нельзя —
будет видно пятно. Берём чистый кусок фона строго над знаком (та же вертикаль,
тот же градиент) и вклеиваем через размытую маску.

База меняется → все ранее сгенерированные примерки становятся негодными
(оверлеи кладутся 1:1). Делать это надо ДО массовой генерации.
"""
import os
import sys

import numpy as np
from PIL import Image, ImageFilter

SRC = r"C:\Users\krasn\AndroidStudioProjects\MyApplication\app\src\main\res\drawable-nodpi\premium_model.jpg"
BACKUP = SRC.replace(".jpg", "_watermarked.jpg")


def find_mark(img, search_box):
    """Ищет пиксели, выбивающиеся из локального фона, внутри области поиска."""
    x0, y0, x1, y1 = search_box
    region = np.asarray(img.crop(search_box).convert("L"), dtype=np.float32)

    # Фон = сильно размытая версия себя; знак от неё отклоняется.
    blur = np.asarray(
        img.crop(search_box).convert("L").filter(ImageFilter.GaussianBlur(25)),
        dtype=np.float32,
    )
    diff = np.abs(region - blur)

    ys, xs = np.where(diff > diff.max() * 0.35)
    if len(xs) == 0:
        return None
    return (x0 + xs.min(), y0 + ys.min(), x0 + xs.max() + 1, y0 + ys.max() + 1), diff.max()


def main():
    img = Image.open(SRC).convert("RGB")
    w, h = img.size
    print(f"база: {w}x{h}")

    found = find_mark(img, (w - 200, h - 200, w, h))
    if not found:
        print("знак не найден — возможно, уже убран")
        return
    (mx0, my0, mx1, my1), strength = found
    pad = 8
    box = (max(0, mx0 - pad), max(0, my0 - pad), min(w, mx1 + pad), min(h, my1 + pad))
    bw, bh = box[2] - box[0], box[3] - box[1]
    print(f"знак: {mx0},{my0}..{mx1},{my1}  (контраст {strength:.1f})")
    print(f"заплатка: {bw}x{bh} в {box[0]},{box[1]}")

    # Чистый фон берём строго выше знака — тот же столбец, тот же градиент.
    src_y = box[1] - bh - 20
    if src_y < 0:
        src_y = box[3] + 20
    patch = img.crop((box[0], src_y, box[2], src_y + bh))

    # Проверяем, что донорский кусок действительно чистый.
    pl = np.asarray(patch.convert("L"), dtype=np.float32)
    if pl.std() > 6:
        print(f"ВНИМАНИЕ: донорский участок неоднороден (разброс {pl.std():.1f})")

    # Подгоняем яркость под окрестность заплатки.
    target = np.asarray(
        img.crop((box[0], box[3], box[2], min(h, box[3] + 15))).convert("L"), dtype=np.float32
    )
    if target.size:
        shift = float(target.mean() - pl.mean())
        patch = Image.fromarray(
            np.clip(np.asarray(patch, dtype=np.float32) + shift, 0, 255).astype(np.uint8)
        )
        print(f"поправка яркости: {shift:+.1f}")

    # Растушёванная маска, чтобы не было видно шва.
    mask = Image.new("L", (bw, bh), 0)
    inner = 10
    mask.paste(255, (inner, inner, bw - inner, bh - inner))
    mask = mask.filter(ImageFilter.GaussianBlur(inner / 2))

    out = img.copy()
    out.paste(patch, (box[0], box[1]), mask)

    if not os.path.exists(BACKUP):
        img.save(BACKUP, quality=97)
        print(f"оригинал сохранён: {os.path.basename(BACKUP)}")

    out.save(SRC, quality=97, subsampling=0)
    print(f"записано: {SRC}")

    # Контроль: знак должен перестать находиться.
    again = find_mark(Image.open(SRC).convert("RGB"), (w - 200, h - 200, w, h))
    print(f"проверка: остаточный контраст {again[1]:.1f}" if again else "проверка: чисто")

    # Кроп до/после для глаза.
    scratch = sys.argv[1] if len(sys.argv) > 1 else "."
    cmp = Image.new("RGB", (400, 200), "white")
    cmp.paste(img.crop((w - 200, h - 200, w, h)).resize((200, 200), Image.NEAREST), (0, 0))
    cmp.paste(out.crop((w - 200, h - 200, w, h)).resize((200, 200), Image.NEAREST), (200, 0))
    cmp.save(os.path.join(scratch, "watermark_before_after.png"))
    print("сравнение: watermark_before_after.png (слева было, справа стало)")


if __name__ == "__main__":
    main()
