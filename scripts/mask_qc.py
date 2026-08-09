"""
Проверка масок на брак — чтобы не разглядывать 2033 картинки глазами.

Ловит то, что реально ломается:
  * дыра внутри вещи — разборщик принял просвечивающую ткань за кожу
    (нашлось на белом платье: сквозь бедро было видно тело);
  * обрывки — куски чужой вещи, отвалившиеся от основной маски;
  * пустая маска — вещь не нашлась вовсе;
  * рваный край — маска в лохмотьях;
  * маска на лице — одежды там быть не может.

Каждой вещи выставляется набор чисел. Смотреть глазами нужно только те,
где числа вышли за порог, а это единицы процентов.

Используется и в показе (vton/mask_demo.ipynb), и в боевом прогоне.
"""
import numpy as np
from scipy import ndimage

# Порог шероховатости края. ВРЕМЕННЫЙ: поставлен между "слегка обгрызенный"
# (5.8) и "призрак" (10.6), замеренными на подделках. Откалибровать по
# распределению настоящих масок после первого прогона.
# Откалибровано по 2218 настоящим маскам: медиана 4.4, 95-й процентиль 7.0,
# 99-й 7.7. Ставим 8.0 — чуть выше 99-го, чтобы ловить отклонения,
# но не придираться к кружеву и плиссировке.
ROUGHNESS_LIMIT = 8.0


def clean_mask(mask, min_px=800, close_r=3, feather=1.2):
    """
    Приводит сырую маску в порядок.

    Заполнение пустот закрывает ЗАМКНУТЫЕ дыры — ту самую, что была на бедре.
    Законный разрез спереди идёт до подола и не замкнут, поэтому остаётся
    на месте. В этом различии весь смысл приёма.

    Возвращает (маска, альфа 0..255).
    """
    m = mask.astype(bool)
    m = ndimage.binary_fill_holes(m)
    if close_r:
        k = np.ones((close_r * 2 + 1, close_r * 2 + 1), bool)
        m = ndimage.binary_closing(m, structure=k)
        m = ndimage.binary_fill_holes(m)

    lab, n = ndimage.label(m)
    if n > 1:
        sizes = ndimage.sum(m, lab, range(1, n + 1))
        keep = [i + 1 for i, sz in enumerate(sizes) if sz >= min_px]
        m = np.isin(lab, keep)

    a = m.astype(np.float32) * 255.0
    if feather:
        a = ndimage.gaussian_filter(a, feather)
    return m, np.clip(a, 0, 255).astype(np.uint8)


def qc(raw_mask, clean_m, category, shape=None):
    """
    Признаки брака для одной вещи. Возвращает словарь чисел и список бед.

    Пороги подобраны так, чтобы отмечать явное, а не придираться:
    нормальная вещь не должна их задевать.
    """
    h, w = raw_mask.shape[:2]
    area = float(raw_mask.sum())
    total = float(h * w)
    problems = []

    if area < total * 0.01:
        problems.append("маска почти пустая")

    # ── дыры: сколько пикселей добавило заполнение пустот
    filled = ndimage.binary_fill_holes(raw_mask.astype(bool))
    hole_px = float(filled.sum() - raw_mask.sum())
    hole_share = hole_px / max(area, 1)
    if hole_share > 0.03:
        problems.append(f"дыра внутри вещи ({hole_share*100:.0f}% площади)")

    # ── обрывки: доля площади вне самого большого куска
    lab, n = ndimage.label(raw_mask.astype(bool))
    frag_share = 0.0
    if n > 1:
        sizes = ndimage.sum(raw_mask, lab, range(1, n + 1))
        frag_share = float((sizes.sum() - sizes.max()) / max(sizes.sum(), 1))
        if frag_share > 0.05:
            problems.append(f"обрывки ({frag_share*100:.0f}% площади в стороне)")

    # ── рваный край: периметр к корню площади.
    #    Замерено на подделках: ровный прямоугольник 4.0, слегка обгрызенный
    #    5.8, съеденный пятнами («призрак») 10.6. Порог ВРЕМЕННЫЙ — его надо
    #    откалибровать по распределению настоящих масок: у кружева и бахромы
    #    шероховатость законно высокая, и абсолютное число тут врёт.
    #    После первого прогона взять примерно 95-й процентиль.
    edge = clean_m ^ ndimage.binary_erosion(clean_m)
    rough = float(edge.sum() / max(np.sqrt(clean_m.sum()), 1))
    if rough > ROUGHNESS_LIMIT:
        problems.append(f"рваный край (шероховатость {rough:.0f})")

    # ── лицо: одежды выше 0.12 высоты быть не должно
    face = float(clean_m[: int(h * 0.12)].mean())
    if face > 0.02:
        problems.append(f"маска заходит на лицо ({face*100:.0f}%)")

    # ── не своя зона: верх глубоко внизу или низ высоко вверху.
    #    Не обязательно брак — длинное пальто законно спускается,
    #    поэтому это подсказка к просмотру, а не приговор.
    out_zone = 0.0
    if category == "TOP":
        out_zone = float(clean_m[int(h * 0.75):].mean())
        if out_zone > 0.05:
            problems.append(f"верх достаёт до голеней ({out_zone*100:.0f}%) — проверить")
    elif category == "BOTTOM":
        out_zone = float(clean_m[: int(h * 0.30)].mean())
        if out_zone > 0.02:
            problems.append(f"низ заходит на грудь ({out_zone*100:.0f}%)")

    return {
        "area_share": round(area / total, 4),
        "hole_share": round(hole_share, 4),
        "frag_share": round(frag_share, 4),
        "roughness": round(rough, 1),
        "face_share": round(face, 4),
        "out_zone": round(out_zone, 4),
        "problems": problems,
        "ok": not problems,
    }


def summarise(rows):
    """Свод по всем вещам: сколько чистых, что чаще всего ломается."""
    import collections
    total = len(rows)
    bad = [r for r in rows if not r["ok"]]
    kinds = collections.Counter(
        p.split(" (")[0] for r in bad for p in r["problems"]
    )
    lines = [f"проверено: {total}",
             f"чистых: {total - len(bad)} ({(total-len(bad))/max(total,1)*100:.1f}%)",
             f"с замечаниями: {len(bad)}"]
    for k, v in kinds.most_common():
        lines.append(f"   {k}: {v}")
    return "\n".join(lines)
