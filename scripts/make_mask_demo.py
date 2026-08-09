"""
Собирает Kaggle-ноутбук: показ вырезания вещи разборщиком тела + проверка брака.

Это ПОКАЗ на шести трудных случаях, а не боевой прогон. Вещи подобраны
замерами: длинное платье (нынешний шов режет его пополам), белый топ на
телесном манекене (тот самый «призрак», из-за которого раньше отказались
от вычитания), юбка (у них больше всего сбоев), плюс обычные футболка и
джинсы для сравнения.

Картинки скачиваются с Pages, загружать ничего не надо.
Код проверки берётся из scripts/mask_qc.py — он вшивается в ноутбук целиком,
чтобы ноутбук был самодостаточным.

ВАЖНО про формат: source ячейки — список строк С переводами на конце.
Собирать через split("\\n") нельзя, переводы срежутся и код склеится в одну
строку. Только splitlines(keepends=True).
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "vton", "mask_demo.ipynb")
QC_SRC = os.path.join(ROOT, "scripts", "mask_qc.py")
BASE_URL = "https://alex22356.github.io/tryon-catalog/products"
BASE_RAW = "https://raw.githubusercontent.com/alex22356/tryon-catalog/main/vton/model.jpg"

DEMO = [
    {"id": "dv8_34400", "cat": "tops", "label": "длинное платье — шов режет его пополам"},
    {"id": "dv8_22594", "cat": "tops", "label": "белый топ — проверка на призрака"},
    {"id": "dv8_32143", "cat": "tops", "label": "обычная футболка"},
    {"id": "dv8_32645", "cat": "bottoms", "label": "юбка"},
    {"id": "dv8_29439", "cat": "bottoms", "label": "джинсы"},
]
PAIRS = [("dv8_34400", "dv8_29439"), ("dv8_32143", "dv8_32645")]
PROBLEM_ID = "dv8_34400"   # вещь, на которой нашлась дыра


def cell(kind, text):
    return {
        "cell_type": kind,
        "metadata": {},
        "source": text.splitlines(keepends=True),
        **({"execution_count": None, "outputs": []} if kind == "code" else {}),
    }


HEAD = """# Вырезание вещи + проверка на брак

Проверяем на **самых трудных** случаях, подобранных замерами:

- длинное платье — нынешняя обрезка по талии режет его пополам
- белый топ на телесном манекене — тот самый «призрак», из-за которого
  раньше отказались от вычитания фона
- юбка — у них больше всего сбоев генерации
- обычные футболка и джинсы для сравнения

Ничего загружать не надо. **Справа:** `Internet → On`, любой GPU.
"""

SETUP = """!pip install -q fashn-human-parser scipy 2>&1 | tail -2
import torch
print('карта:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'процессор')
"""

DOWNLOAD = """import os, urllib.request
from PIL import Image, ImageDraw

os.makedirs('/kaggle/working/in', exist_ok=True)

def get(url, dst):
    if not os.path.exists(dst):
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        open(dst, 'wb').write(urllib.request.urlopen(req, timeout=60).read())
    return dst

base = Image.open(get(BASE_RAW, '/kaggle/working/in/base.jpg')).convert('RGB')
for d in DEMO:
    get(BASE_URL + '/overlay_' + d['id'] + '.webp', '/kaggle/working/in/' + d['id'] + '.webp')
print('база', base.size, '| примерок скачано:', len(DEMO))
base
"""

LABELS = """import fashn_human_parser as m
from fashn_human_parser import FashnHumanParser

# Таблицы меток: от них зависит, что попадёт в «верх», а что в «низ».
# Смотрим своими глазами, а не верим на слово.
for attr in ('LABELS', 'FASHN_LABELS_TO_IDS', 'LABELS_TO_IDS',
             'CATEGORY_TO_BODY_COVERAGE', 'BODY_COVERAGE_TO_LABELS',
             'BODY_COVERAGE_TO_FASHN_LABELS'):
    if hasattr(m, attr):
        print('---', attr, '---')
        print(getattr(m, attr))
"""

MASKS = """import numpy as np

parser = FashnHumanParser(device='cuda' if torch.cuda.is_available() else 'cpu')

COVER = getattr(m, 'CATEGORY_TO_BODY_COVERAGE', None)
GROUPS = (getattr(m, 'BODY_COVERAGE_TO_FASHN_LABELS', None)
          or getattr(m, 'BODY_COVERAGE_TO_LABELS', None))
IDS = getattr(m, 'FASHN_LABELS_TO_IDS', None) or getattr(m, 'LABELS_TO_IDS', None)

def garment_mask(img, cat):
    seg = parser.predict(img) if hasattr(parser, 'predict') else parser(img)
    arr = seg if isinstance(seg, np.ndarray) else np.asarray(seg)
    ids = [IDS[l] for l in GROUPS[COVER[cat]]]
    return np.isin(arr, ids)

raw = {}
for d in DEMO:
    img = Image.open('/kaggle/working/in/' + d['id'] + '.webp').convert('RGB')
    raw[d['id']] = garment_mask(img, d['cat'])
    print(d['label'][:44].ljust(46), 'маска', round(raw[d['id']].mean()*100, 1), '% кадра')
"""

CLEAN_AND_QC = """CATS = {d['id']: ('TOP' if d['cat'] == 'tops' else 'BOTTOM') for d in DEMO}

cuts, rows = {}, []
for d in DEMO:
    img = Image.open('/kaggle/working/in/' + d['id'] + '.webp').convert('RGB')
    mk = raw[d['id']]
    m2, alpha = clean_mask(mk)
    rgba = img.convert('RGBA')
    rgba.putalpha(Image.fromarray(alpha))
    cuts[d['id']] = rgba

    r = qc(mk, m2, CATS[d['id']])
    rows.append(r)
    verdict = 'чисто' if r['ok'] else '; '.join(r['problems'])
    print(d['label'][:40].ljust(42), 'дыра', str(round(r['hole_share']*100)).rjust(3) + '%',
          '| шерох', str(r['roughness']).rjust(5), '|', verdict)

print()
print(summarise(rows))
"""

HOLE = """img = Image.open('/kaggle/working/in/' + PROBLEM_ID + '.webp').convert('RGB')
W, H = img.size
box = (int(W*0.25), int(H*0.30), int(W*0.75), int(H*0.62))

rr = img.convert('RGBA')
rr.putalpha(Image.fromarray(raw[PROBLEM_ID].astype('uint8') * 255))

def on_grey(rgba):
    g = Image.new('RGB', rgba.size, (232, 232, 232))
    g.paste(rgba, (0, 0), rgba)
    return g

tiles = [('исходный кадр', img.crop(box)),
         ('маска БЕЗ обработки', on_grey(rr).crop(box)),
         ('маска ПОСЛЕ обработки', on_grey(cuts[PROBLEM_ID]).crop(box))]
w = 330
h = int(w * (box[3]-box[1]) / (box[2]-box[0]))
sheet = Image.new('RGB', (w*3+16, h+20), 'white')
dr = ImageDraw.Draw(sheet)
for k, (t, im) in enumerate(tiles):
    sheet.paste(im.resize((w, h)), (k*(w+8), 0))
    dr.text((k*(w+8)+3, h+4), t, fill='black')
sheet.save('/kaggle/working/hole_fix.jpg', quality=95)
sheet
"""

GRID = """tiles = []
for d in DEMO:
    src = Image.open('/kaggle/working/in/' + d['id'] + '.webp').convert('RGB')
    cut = cuts[d['id']]
    onbase = base.copy().convert('RGBA')
    onbase.alpha_composite(cut.resize(base.size))
    tiles.append((d['label'], src, cut, onbase.convert('RGB')))

w, h = 190, 254
sheet = Image.new('RGB', (w*3+20, (h+20)*len(tiles)), 'white')
dr = ImageDraw.Draw(sheet)
for i, (label, a, b, c) in enumerate(tiles):
    y = i*(h+20)
    sheet.paste(a.resize((w, h)), (0, y))
    chk = Image.new('RGB', (w, h), (235, 235, 235))
    small = b.resize((w, h))
    chk.paste(small, (0, 0), small)
    sheet.paste(chk, (w+10, y))
    sheet.paste(c.resize((w, h)), (w*2+20, y))
    dr.text((2, y+h+4), label[:62], fill='black')
sheet.save('/kaggle/working/masks.jpg', quality=92)
sheet
"""

COMPARE = """WAIST = 0.44
W, H = base.size
outs = []
for top_id, bot_id in PAIRS:
    old = base.copy()
    bo = Image.open('/kaggle/working/in/' + bot_id + '.webp').convert('RGB').resize((W, H))
    to = Image.open('/kaggle/working/in/' + top_id + '.webp').convert('RGB').resize((W, H))
    old.paste(bo.crop((0, int(H*WAIST), W, H)), (0, int(H*WAIST)))
    old.paste(to.crop((0, 0, W, int(H*WAIST))), (0, 0))

    new = base.copy().convert('RGBA')
    new.alpha_composite(cuts[bot_id].resize((W, H)))
    new.alpha_composite(cuts[top_id].resize((W, H)))
    outs.append((old, new.convert('RGB')))

w, h = 300, 402
sheet = Image.new('RGB', (w*2+16, (h+22)*len(outs)), 'white')
dr = ImageDraw.Draw(sheet)
for i, (old, new) in enumerate(outs):
    y = i*(h+22)
    sheet.paste(old.resize((w, h)), (0, y))
    sheet.paste(new.resize((w, h)), (w+16, y))
    dr.text((2, y+h+4), 'СЕЙЧАС: обрезка по талии', fill='black')
    dr.text((w+18, y+h+4), 'СТАНЕТ: вырезанные вещи', fill='black')
sheet.save('/kaggle/working/compare.jpg', quality=93)
sheet
"""


def main():
    qc_code = open(QC_SRC, encoding="utf-8").read()

    cells = [
        cell("markdown", HEAD),
        cell("code", SETUP),
        cell("code",
             "DEMO = " + json.dumps(DEMO, ensure_ascii=False) + "\n"
             "PAIRS = " + json.dumps(PAIRS) + "\n"
             'PROBLEM_ID = "' + PROBLEM_ID + '"\n'
             'BASE_URL = "' + BASE_URL + '"\n'
             'BASE_RAW = "' + BASE_RAW + '"\n'),
        cell("markdown", "## Качаем картинки\n"),
        cell("code", DOWNLOAD),
        cell("markdown", "## Какие классы отдаёт разборщик\n"),
        cell("code", LABELS),
        cell("markdown", "## Сырые маски\n"),
        cell("code", MASKS),
        cell("markdown",
             "## Обработка и проверка на брак\n\n"
             "Заполнение пустот закрывает ЗАМКНУТЫЕ дыры — ту самую, что нашлась\n"
             "на бедре. Законный разрез спереди идёт до подола, он не замкнут,\n"
             "и потому остаётся. В этом различии весь смысл приёма.\n"),
        cell("code", "%%writefile /kaggle/working/mask_qc.py\n" + qc_code),
        cell("code", "import sys\nsys.path.insert(0, '/kaggle/working')\n"
                     "from mask_qc import clean_mask, qc, summarise\nprint('проверка загружена')\n"),
        cell("code", CLEAN_AND_QC),
        cell("markdown", "### Дыра: было и стало\n\nКрупно та область, на которую ты показал.\n"),
        cell("code", HOLE),
        cell("markdown", "### Кадр → маска → вырезка на манекене\n"),
        cell("code", GRID),
        cell("markdown", "## Главное: верх и низ вместе\n"),
        cell("code", COMPARE),
        cell("markdown", "## Забрать\n"),
        cell("code", "!cd /kaggle/working && zip -q mask_demo.zip masks.jpg compare.jpg "
                     "hole_fix.jpg && ls -lh mask_demo.zip\n"),
    ]

    nb = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.11"},
            "accelerator": "GPU",
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(nb, f, ensure_ascii=False)
    print("готово:", OUT, f"({os.path.getsize(OUT)//1024} КБ, ячеек {len(cells)})")


if __name__ == "__main__":
    main()
