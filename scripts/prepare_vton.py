"""
Фаза 0 пайплайна примерки на Kaggle.

Манекен ФИКСИРОВАН, поэтому маски областей тела считаются ОДИН РАЗ здесь,
а не в ноутбуке. Это главный трюк: он позволяет не тащить в Kaggle
DensePose/detectron2 (самая хрупкая часть IDM-VTON) — маска подаётся готовой.

Что делает:
  1. Строит силуэт манекена (фон нейтрально-серый, тело тёплое → признак R-B).
  2. Нарезает маски: top / bottom / full_body / feet.
  3. Экспортирует список товаров для прогона (garments.json).

Запуск:
    python scripts/prepare_vton.py            # всё + пилот на 10 товарах
    python scripts/prepare_vton.py --all      # список всех товаров, не только пилот
"""
import argparse
import json
import os
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT.parent / "app" / "src" / "main" / "res" / "drawable-nodpi"
OUT = ROOT / "vton"
CATALOG = ROOT / "dv8_products.json"

# Доля от высоты кадра. Замерено по профилю ширины силуэта premium_model.jpg (896x1200).
LM = {
    "shoulder": 0.205,   # линия плеч
    "waist":    0.430,   # талия — стык верха и низа (совпадает с WAIST в MannequinView)
    "hip":      0.500,   # бёдра
    "hem":      0.600,   # низ боди (середина бедра)
    "knee":     0.730,
    "ankle":    0.885,   # щиколотки — выше начинаются стопы
}

# Верх/платья/обувь: равномерное расширение силуэта. Значения проверены пилотом —
# принты и цвет переносятся верно, ничего не трогаем.
DILATE = {"top": 34, "full_body": 34, "feet": 22}

# Низ — отдельная геометрия. Две проблемы, вскрытые пилотом:
#  1) при облегающей маске широкие брюки садились как лосины;
#  2) при широкой маске захватывались РУКИ (на уровне бёдер они примыкают к телу,
#     силуэт там — один сплошной кусок) и модель закрашивала их пятнами.
# Решение: трапеция по ширине корпуса + запас на крой, с явным вырезом под руки.
BOTTOM_HALF  = (100, 62)   # полуширина корпуса: талия -> щиколотки (px)
BOTTOM_FLARE = (30, 46)    # запас на свободный крой: талия -> щиколотки (px)
ARM_KEEP     = 6           # на столько пикселей расширяем вырез вокруг рук
ARM_END      = 0.63        # ниже этой доли высоты рук уже нет (кисти заканчиваются)


def silhouette(img: Image.Image) -> np.ndarray:
    """Силуэт тела. Фон студии нейтральный (R≈G≈B), кожа/боди тёплые (R заметно > B)."""
    a = np.asarray(img.convert("RGB")).astype(int)
    warmth = a[:, :, 0] - a[:, :, 2]
    sil = warmth > 15
    # чистим одиночный шум построчно: оставляем только «толстые» горизонтальные куски
    for y in range(sil.shape[0]):
        xs = np.where(sil[y])[0]
        if len(xs) and len(xs) < 12:
            sil[y, :] = False
    return sil


def dilate(mask: np.ndarray, px: int) -> np.ndarray:
    """Расширение маски без scipy — сдвигами по 4 направлениям."""
    out = mask.copy()
    for _ in range(px):
        out[1:, :] |= out[:-1, :]
        out[:-1, :] |= out[1:, :]
        out[:, 1:] |= out[:, :-1]
        out[:, :-1] |= out[:, 1:]
    return out


def band(sil: np.ndarray, y0: float, y1: float, grow: int) -> np.ndarray:
    """Маска = силуэт в вертикальной полосе [y0..y1], расширенный на grow пикселей."""
    h = sil.shape[0]
    m = np.zeros_like(sil)
    m[int(y0 * h): int(y1 * h), :] = sil[int(y0 * h): int(y1 * h), :]
    m = dilate(m, grow)
    out = np.zeros_like(sil)
    out[int(y0 * h): int(y1 * h), :] = m[int(y0 * h): int(y1 * h), :]
    return out


def band_bottom(sil: np.ndarray, y0: float, y1: float) -> np.ndarray:
    """
    Маска низа: сплошная трапеция вокруг корпуса МИНУС руки.

    Сплошная (а не по силуэту ног) — чтобы юбке было куда лечь между ног.
    Ширина задаётся от центра тела, а не размахом силуэта: на уровне бёдер
    силуэт включает прижатые руки, и опора на него затягивала их в маску.
    Пиксели рук дополнительно вырезаются — брюки их не закрывают.
    """
    h, w = sil.shape
    top, bot = int(y0 * h), int(y1 * h)
    cx = int(np.median([np.where(sil[y])[0].mean() for y in range(top, min(bot, h))
                        if sil[y].any()]))

    out = np.zeros_like(sil)
    for y in range(top, min(bot, h)):
        t = (y - top) / max(bot - top - 1, 1)
        half = (BOTTOM_HALF[0] + (BOTTOM_HALF[1] - BOTTOM_HALF[0]) * t
                + BOTTOM_FLARE[0] + (BOTTOM_FLARE[1] - BOTTOM_FLARE[0]) * t)
        out[y, max(int(cx - half), 0): min(int(cx + half) + 1, w)] = True

    # Вырезаем руки — но ТОЛЬКО там, где они есть (выше кистей). Ниже рук нет,
    # и та же проверка начала бы срезать внешние края расставленных ног.
    arms = np.zeros_like(sil)
    arm_end = int(ARM_END * h)
    for y in range(top, min(bot, arm_end)):
        t = (y - top) / max(bot - top - 1, 1)
        core = BOTTOM_HALF[0] + (BOTTOM_HALF[1] - BOTTOM_HALF[0]) * t
        xs = np.where(sil[y])[0]
        arms[y, xs[np.abs(xs - cx) > core]] = True
    out &= ~dilate(arms, ARM_KEEP)
    return out


def save_mask(mask: np.ndarray, path: Path) -> None:
    Image.fromarray((mask * 255).astype(np.uint8), mode="L").save(path)


def build_masks() -> None:
    src = APP / "premium_model.jpg"
    img = Image.open(src)
    sil = silhouette(img)
    H, W = sil.shape
    print(f"манекен: {W}x{H}, силуэт {100 * sil.mean():.1f}% кадра")

    prof = sil.sum(1)
    for name, fr in LM.items():
        print(f"   {name:9s} y={fr:.3f} ({int(fr * H):4d}px)  ширина тела {prof[int(fr * H)]}px")

    masks = {
        # верх: от плеч до бёдер (рукава закрывают руки — силуэт годится как есть)
        "top":       band(sil, LM["shoulder"] - 0.02, LM["hip"] + 0.02, DILATE["top"]),
        # низ: трапеция с вырезом под руки
        "bottom":    band_bottom(sil, LM["waist"] - 0.02, LM["ankle"]),
        # платье/комбинезон: от плеч до колена
        "full_body": band(sil, LM["shoulder"] - 0.02, LM["knee"], DILATE["full_body"]),
        # обувь: от щиколоток вниз до конца кадра
        "feet":      band(sil, LM["ankle"], 1.0, DILATE["feet"]),
    }

    OUT.mkdir(exist_ok=True)
    img.convert("RGB").save(OUT / "model.jpg", quality=95)
    for name, m in masks.items():
        save_mask(m, OUT / f"mask_{name}.png")
        print(f"   mask_{name}.png — покрытие {100 * m.mean():.1f}%")


def export_garments(pilot: bool) -> None:
    if not CATALOG.exists():
        print(f"! каталог не найден: {CATALOG}")
        return
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else data.get("items", data)

    # для VTON нужен id, фото и категория; всё остальное ноутбуку не нужно
    rec = [
        {
            "id": i["id"],
            "imageUrl": i.get("imageUrl"),
            "category": i.get("category"),
            "name": i.get("name", "")[:80],
        }
        for i in items
        if i.get("imageUrl")
    ]

    if pilot:
        # пилот: берём по несколько штук каждой категории, чтобы увидеть все режимы
        quota = {"TOP": 4, "BOTTOM": 3, "FULL_BODY": 2, "FOOTWEAR": 1}
        picked, seen = [], {k: 0 for k in quota}
        for r in rec:
            c = r["category"]
            if c in quota and seen[c] < quota[c]:
                picked.append(r)
                seen[c] += 1
            if len(picked) == sum(quota.values()):
                break
        rec = picked

    OUT.mkdir(exist_ok=True)
    path = OUT / ("garments_pilot.json" if pilot else "garments_all.json")
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    by_cat = {}
    for r in rec:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    print(f"{path.name}: {len(rec)} товаров {by_cat}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="экспортировать весь каталог, а не пилот")
    a = ap.parse_args()
    build_masks()
    export_garments(pilot=not a.all)
    print(f"\nготово → {OUT}")
