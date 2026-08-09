"""
Собирает боевой Kaggle-ноутбук: вырезание вещей из всех примерок верха и низа.

Что делает прогон:
  1. качает примерки с Pages (они уже опубликованы, загружать нечего);
  2. разборщиком тела берёт маску ИМЕННО нужного класса — дорисованные
     генератором шорты в маску верха не попадают, это главное отличие от
     вычитания фона;
  3. чистит маску (заполнение замкнутых пустот — та самая дыра на бедре);
  4. считает признаки брака и пишет отчёт;
  5. сохраняет прозрачные RGBA WebP.

Цельные вещи и обувь НЕ трогаем: они рисуются в одиночку, шва у них нет,
вырезание им ничего не даёт, а риск даёт.

Формат ячеек: только splitlines(keepends=True) — иначе переводы строк
срезаются и код склеивается в одну строку.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "public", "catalog.json")
QC_SRC = os.path.join(ROOT, "scripts", "mask_qc.py")
OUT = os.path.join(ROOT, "vton", "masks_batch.ipynb")
BASE_URL = "https://alex22356.github.io/tryon-catalog/products"

CAT_TO_FASHN = {"TOP": "tops", "BOTTOM": "bottoms"}


def cell(kind, text):
    return {
        "cell_type": kind,
        "metadata": {},
        "source": text.splitlines(keepends=True),
        **({"execution_count": None, "outputs": []} if kind == "code" else {}),
    }


def build_worklist():
    with open(CATALOG, encoding="utf-8") as f:
        items = json.load(f)["items"]
    work = []
    for it in items:
        if not it.get("overlayUrl"):
            continue
        cat = CAT_TO_FASHN.get(it.get("category"))
        if not cat:
            continue                      # цельные и обувь пропускаем
        work.append({"id": it["id"], "cat": cat, "app_cat": it["category"]})
    return work


SETUP = """!pip install -q fashn-human-parser scipy 2>&1 | tail -2
import torch
print('карта:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'процессор')
print('к работе:', len(WORK), 'позиций')
"""

WORKER = """import argparse, json, os, sys, time, urllib.request
import numpy as np
from PIL import Image

sys.path.insert(0, '/kaggle/working')
from mask_qc import clean_mask, qc

import fashn_human_parser as m
from fashn_human_parser import FashnHumanParser

ap = argparse.ArgumentParser()
ap.add_argument('--shard', type=int, default=0)
ap.add_argument('--nshards', type=int, default=1)
ap.add_argument('--limit', type=int, default=0)
a = ap.parse_args()

OUT_DIR = '/kaggle/working/cut'
CACHE = '/kaggle/working/src'
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(CACHE, exist_ok=True)

work = json.load(open('/kaggle/working/work.json', encoding='utf-8'))
mine = work[a.shard::a.nshards]
mine = [w for w in mine if not os.path.exists(OUT_DIR + '/' + w['id'] + '.webp')]
if a.limit:
    mine = mine[:a.limit]
print('[' + str(a.shard) + '] к работе:', len(mine), flush=True)

parser = FashnHumanParser(device='cuda' if torch.cuda.is_available() else 'cpu')
COVER = getattr(m, 'CATEGORY_TO_BODY_COVERAGE')
GROUPS = (getattr(m, 'BODY_COVERAGE_TO_FASHN_LABELS', None)
          or getattr(m, 'BODY_COVERAGE_TO_LABELS'))
IDS = getattr(m, 'FASHN_LABELS_TO_IDS', None) or getattr(m, 'LABELS_TO_IDS')

BASE_URL = 'https://alex22356.github.io/tryon-catalog/products'
report, t0, failed = [], time.time(), 0

for k, w in enumerate(mine, 1):
    path = CACHE + '/' + w['id'] + '.webp'
    try:
        if not os.path.exists(path):
            req = urllib.request.Request(BASE_URL + '/overlay_' + w['id'] + '.webp',
                                         headers={'User-Agent': 'Mozilla/5.0'})
            open(path, 'wb').write(urllib.request.urlopen(req, timeout=40).read())
        img = Image.open(path).convert('RGB')

        seg = parser.predict(img) if hasattr(parser, 'predict') else parser(img)
        arr = seg if isinstance(seg, np.ndarray) else np.asarray(seg)
        ids = [IDS[l] for l in GROUPS[COVER[w['cat']]]]
        raw = np.isin(arr, ids)

        clean, alpha = clean_mask(raw)
        r = qc(raw, clean, w['app_cat'])
        r['id'] = w['id']
        r['cat'] = w['app_cat']
        report.append(r)

        rgba = img.convert('RGBA')
        rgba.putalpha(Image.fromarray(alpha))
        rgba.save(OUT_DIR + '/' + w['id'] + '.webp', 'WEBP', quality=88, method=4)
    except Exception as e:
        failed += 1
        print('[' + str(a.shard) + '] СБОЙ ' + w['id'] + ':', type(e).__name__, e, flush=True)

    if k % 50 == 0:
        el = time.time() - t0
        json.dump(report, open('/kaggle/working/report_' + str(a.shard) + '.json', 'w'),
                  ensure_ascii=False)
        print('[' + str(a.shard) + '] ' + str(k) + '/' + str(len(mine)),
              round(el/k, 2), 'с/шт  осталось ~',
              round((len(mine)-k)*el/k/60), 'мин  сбоев', failed, flush=True)

json.dump(report, open('/kaggle/working/report_' + str(a.shard) + '.json', 'w'),
          ensure_ascii=False)
print('[' + str(a.shard) + '] ВСЁ: сделано', len(report), 'сбоев', failed, flush=True)
"""

SMOKE = """!cd /kaggle/working && python worker.py --shard 0 --nshards 1 --limit 5
"""

RUN = """import os, subprocess, threading, time

t0 = time.time()
n_gpu = max(1, torch.cuda.device_count())
procs = [
    subprocess.Popen(
        ['python', '/kaggle/working/worker.py', '--shard', str(s), '--nshards', str(n_gpu)],
        env=dict(os.environ, CUDA_VISIBLE_DEVICES=str(s)),
        cwd='/kaggle/working',
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for s in range(n_gpu)
]

def pump(p):
    for line in p.stdout:
        print(line.rstrip(), flush=True)

th = [threading.Thread(target=pump, args=(p,), daemon=True) for p in procs]
[t.start() for t in th]
[p.wait() for p in procs]
[t.join(timeout=5) for t in th]

done = len(os.listdir('/kaggle/working/cut'))
print('готово', done, 'из', len(WORK), 'за', round((time.time()-t0)/60), 'мин')
"""

REPORT = """import glob, json, collections
import numpy as np
from mask_qc import summarise

rows = []
for f in glob.glob('/kaggle/working/report_*.json'):
    rows += json.load(open(f, encoding='utf-8'))
print(summarise(rows))

# Распределение шероховатости — по нему калибруется порог.
# Временное значение 9.0 поставлено на подделках; настоящие вещи покажут правду.
rough = np.array([r['roughness'] for r in rows])
print()
print('шероховатость края по', len(rough), 'вещам:')
for q in (50, 75, 90, 95, 99):
    print('   ', q, '-й процентиль:', round(float(np.percentile(rough, q)), 1))
print('    выше нынешнего порога 9.0:', int((rough > 9).sum()),
      '(' + str(round(float((rough > 9).mean()*100), 1)) + '%)')

json.dump(rows, open('/kaggle/working/mask_report.json', 'w'), ensure_ascii=False)
"""

SHOW_BAD = """import random
from PIL import Image, ImageDraw

bad = [r for r in rows if not r['ok']]
print('с замечаниями:', len(bad))
if bad:
    random.seed(1)
    pick = random.sample(bad, min(8, len(bad)))
    w, h = 150, 200
    sheet = Image.new('RGB', (w*len(pick), h+34), 'white')
    dr = ImageDraw.Draw(sheet)
    for i, r in enumerate(pick):
        p = '/kaggle/working/cut/' + r['id'] + '.webp'
        if not os.path.exists(p):
            continue
        im = Image.open(p).convert('RGBA').resize((w, h))
        bg = Image.new('RGB', (w, h), (235, 235, 235))
        bg.paste(im, (0, 0), im)
        sheet.paste(bg, (i*w, 0))
        dr.text((i*w+2, h+3), r['problems'][0][:24], fill='black')
        dr.text((i*w+2, h+17), r['id'][:20], fill='gray')
    sheet.save('/kaggle/working/bad_masks.jpg', quality=90)
    display(sheet)
"""

PACK = """import os, zipfile
files = sorted(os.listdir('/kaggle/working/cut'))
for n, i in enumerate(range(0, len(files), 700), 1):
    part = files[i:i+700]
    name = '/kaggle/working/cut_' + str(n).zfill(2) + '.zip'
    with zipfile.ZipFile(name, 'w', zipfile.ZIP_STORED) as z:
        for f in part:
            z.write('/kaggle/working/cut/' + f, f)
    print(os.path.basename(name), len(part), 'шт')
print('плюс mask_report.json и bad_masks.jpg')
"""


def main():
    work = build_worklist()
    qc_code = open(QC_SRC, encoding="utf-8").read()
    import collections
    by_cat = collections.Counter(w["app_cat"] for w in work)
    print("к работе:", len(work), dict(by_cat))

    cells = [
        cell("markdown", f"""# Вырезание вещей из примерок

**{len(work)} позиций**: верх {by_cat['TOP']}, низ {by_cat['BOTTOM']}.
Цельные вещи и обувь пропущены — они рисуются в одиночку, шва у них нет.

Разборщик берёт маску ИМЕННО нужного класса, поэтому дорисованные генератором
шорты в маску верха не попадают. Это главное отличие от вычитания фона,
которое в проекте уже пробовали и отвергли.

**Справа:** `Accelerator → GPU T4 x2`, `Internet → On`.
Для долгого прогона — **Save Version → Save & Run All**.

Возобновление внутри запуска есть: готовое пропускается.
"""),
        cell("code", "WORK = " + json.dumps(work, ensure_ascii=False) + "\n"),
        cell("code", SETUP),
        cell("code", "%%writefile /kaggle/working/mask_qc.py\n" + qc_code),
        cell("code", "import json\n"
                     "json.dump(WORK, open('/kaggle/working/work.json', 'w', encoding='utf-8'),"
                     " ensure_ascii=False)\n"
                     "print('список записан:', len(WORK))\n"),
        cell("code", "%%writefile /kaggle/working/worker.py\n" + WORKER),
        cell("markdown", "## Проба на пяти вещах\n\nПоймать ошибку за минуту, а не за час.\n"),
        cell("code", SMOKE),
        cell("markdown", "## Боевой прогон\n"),
        cell("code", RUN),
        cell("markdown", "## Отчёт о браке\n"),
        cell("code", REPORT),
        cell("markdown", "### Что помечено как брак\n"),
        cell("code", SHOW_BAD),
        cell("markdown", "## Забрать\n"),
        cell("code", PACK),
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
