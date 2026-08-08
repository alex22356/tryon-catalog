"""
Собирает боевой Kaggle-ноутбук: примерка всего каталога через FASHN VTON v1.5.

Что учтено по итогам разведки:
  * bf16 на T4 ПРОГРАММНЫЙ — torch.cuda.is_bf16_supported() врёт, и первый
    прогон дал 408 с на картинку вместо 42. Гасим автовыбор, ставим fp16.
  * 20 шагов: разница с 30 и 50 на глаз мала, а Real-ESRGAN на апскейле даёт
    больше, чем лишние шаги.
  * Две карты T4: по процессу на каждую.
  * Возобновление: фоновая сессия Kaggle рубится на 9-м часу, перезапуск
    досчитает остаток.
  * Обувь пропускаем — FASHN знает только tops/bottoms/one-pieces.

Список работ вшивается в ноутбук, картинки качаются с CDN магазина на месте:
2242 фото в ноутбук не поместятся.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "public", "catalog.json")
BASE = os.path.join(
    os.path.dirname(ROOT), "app", "src", "main", "res", "drawable-nodpi", "premium_female.jpg"
)
OUT = os.path.join(ROOT, "vton", "fashn_batch.ipynb")

# Наши категории -> категории FASHN
CATEGORY_MAP = {"TOP": "tops", "BOTTOM": "bottoms", "FULL_BODY": "one-pieces"}


def md(s):
    return {"cell_type": "markdown", "metadata": {}, "source": s.strip().split("\n")}


def code(s):
    return {"cell_type": "code", "execution_count": None, "metadata": {},
            "outputs": [], "source": s.strip().split("\n")}


def build_worklist():
    with open(CATALOG, encoding="utf-8") as f:
        items = json.load(f)["items"]
    work = []
    for it in items:
        if it.get("overlayUrl"):
            continue                      # примерка уже есть
        cat = CATEGORY_MAP.get(it.get("category"))
        if not cat:
            continue                      # обувь и прочее — не к FASHN
        if not it.get("imageUrl"):
            continue
        work.append({"id": it["id"], "url": it["imageUrl"], "cat": cat})
    return work


def embed_base():
    import base64
    import io
    from PIL import Image

    img = Image.open(BASE).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()


WORKER = r'''
import argparse, base64, json, os, sys, time, urllib.request
import torch
from PIL import Image

# T4 — это Turing, аппаратного bf16 нет. torch.cuda.is_bf16_supported()
# всё равно возвращает True (учитывает программную эмуляцию) — и модель
# считает 408 с вместо 42. Гасим автовыбор, ставим fp16: он у Turing на
# тензорных ядрах.
torch.cuda.is_bf16_supported = lambda *a, **k: False

sys.path.insert(0, "/kaggle/working/fashn/src")
from fashn_vton import TryOnPipeline

ap = argparse.ArgumentParser()
ap.add_argument("--shard", type=int, required=True)
ap.add_argument("--nshards", type=int, default=2)
ap.add_argument("--steps", type=int, default=20)
ap.add_argument("--limit", type=int, default=0, help="0 = без ограничения")
a = ap.parse_args()

OUT = "/kaggle/working/out"
CACHE = "/kaggle/working/garments"
os.makedirs(OUT, exist_ok=True)
os.makedirs(CACHE, exist_ok=True)

tasks = json.load(open("/kaggle/working/worklist.json", encoding="utf-8"))
mine = tasks[a.shard::a.nshards]                  # через одну — нагрузка ровнее
mine = [t for t in mine if not os.path.exists(f"{OUT}/{t['id']}.png")]   # возобновление
if a.limit:
    mine = mine[:a.limit]
print(f"[gpu{a.shard}] к работе: {len(mine)}", flush=True)

pipe = TryOnPipeline(weights_dir="/kaggle/working/weights")
pipe.inference_dtype = torch.float16
pipe.tryon_model.half()

# Манекен один и тот же, а определитель поз работает на процессоре
# (onnxruntime собран под CUDA 13, на Kaggle 12-я) — считаем позу один раз.
_raw, _cache = pipe.pose_model, {}
def cached_pose(img_bgr):
    key = hash(img_bgr.tobytes())
    if key not in _cache:
        _cache[key] = _raw(img_bgr)
    return _cache[key]
pipe.pose_model = cached_pose

# Апскейлер: родные 576x864 тянем до манекена 896x1200
up_model = None
try:
    from spandrel import ModelLoader
    up_model = ModelLoader().load_from_file("/kaggle/working/up/realesrgan_x2.pth").cuda().eval()
    print(f"[gpu{a.shard}] апскейлер поднят", flush=True)
except Exception as e:
    print(f"[gpu{a.shard}] БЕЗ апскейлера ({type(e).__name__}) — будет Ланцош", flush=True)

import numpy as np
TARGET = (896, 1200)

def upscale(img):
    if up_model is None:
        return img.resize(TARGET, Image.LANCZOS)
    t = torch.from_numpy(np.asarray(img, np.float32) / 255).permute(2, 0, 1)[None].cuda()
    with torch.no_grad():
        o = up_model(t)
    big = Image.fromarray((o[0].permute(1, 2, 0).clamp(0, 1).cpu().numpy() * 255).astype("uint8"))
    return big.resize(TARGET, Image.LANCZOS)

person = Image.open("/kaggle/working/model.jpg")
t0, done, failed = time.time(), 0, 0

for k, t in enumerate(mine, 1):
    path = f"{CACHE}/{t['id']}.jpg"
    try:
        if not os.path.exists(path):
            req = urllib.request.Request(t["url"], headers={"User-Agent": "Mozilla/5.0"})
            with open(path, "wb") as f:
                f.write(urllib.request.urlopen(req, timeout=40).read())
        garment = Image.open(path).convert("RGB")
        res = pipe(person_image=person, garment_image=garment, category=t["cat"],
                   garment_photo_type="model", num_timesteps=a.steps)
        upscale(res.images[0]).save(f"{OUT}/{t['id']}.png")
        done += 1
    except Exception as e:
        failed += 1
        print(f"[gpu{a.shard}] СБОЙ {t['id']}: {type(e).__name__} {e}", flush=True)

    if k % 25 == 0:
        el = time.time() - t0
        left = (len(mine) - k) * el / k / 3600
        print(f"[gpu{a.shard}] {k}/{len(mine)}  {el/k:.1f} с/шт  "
              f"осталось ~{left:.1f} ч  сбоев {failed}", flush=True)

print(f"[gpu{a.shard}] ВСЁ: сделано {done}, сбоев {failed}", flush=True)
'''


def main():
    work = build_worklist()
    print(f"к прогону: {len(work)} позиций")

    cells = [
        md(f"""
# Примерка каталога — FASHN VTON v1.5

**{len(work)} позиций**, обувь исключена (FASHN знает только tops/bottoms/one-pieces —
её отдельно через Gemini).

**Перед запуском справа:** `Accelerator → GPU T4 x2`, `Internet → On`.
Для долгого прогона — **Save Version → Save & Run All**, тогда считает в фоне
и переживает закрытый ноутбук. Лимит фоновой сессии — 9 часов, поэтому в воркер
встроено возобновление: перезапуск досчитает остаток.

Расчёт: ~42 с на вещь на двух картах → около 13 часов на весь список.
Недельная норма Kaggle 30 GPU-часов, уходит ~26.
"""),
        code("WORKLIST = " + json.dumps(work, ensure_ascii=False)),
        code('BASE_MODEL_B64 = "' + embed_base() + '"'),
        md("## Установка"),
        code("""
!git clone -q https://github.com/fashn-AI/fashn-vton-1.5.git /kaggle/working/fashn
%cd /kaggle/working/fashn
!pip install -q -e . 2>&1 | tail -2
!pip install -q spandrel 2>&1 | tail -1
!python scripts/download_weights.py --weights-dir /kaggle/working/weights 2>&1 | tail -2
"""),
        code("""
import base64, io, json, os, urllib.request
from PIL import Image

os.makedirs('/kaggle/working/up', exist_ok=True)
Image.open(io.BytesIO(base64.b64decode(BASE_MODEL_B64))).save('/kaggle/working/model.jpg')
json.dump(WORKLIST, open('/kaggle/working/worklist.json', 'w', encoding='utf-8'), ensure_ascii=False)

try:
    urllib.request.urlretrieve(
        'https://huggingface.co/ai-forever/Real-ESRGAN/resolve/main/RealESRGAN_x2.pth',
        '/kaggle/working/up/realesrgan_x2.pth')
    print('апскейлер скачан')
except Exception as e:
    print('апскейлер не скачался, будет Ланцош:', e)

print('позиций к прогону:', len(WORKLIST))
Image.open('/kaggle/working/model.jpg')
"""),
        md("""
## Пробный запуск

Три вещи на одной карте — поймать ошибку за минуту, а не за час.
Здесь же видно, сработала ли починка типа данных: должно быть ~40 с, не 400.
"""),
        code("%%writefile /kaggle/working/worker.py\n" + WORKER.strip()),
        code("""
!cd /kaggle/working && python worker.py --shard 0 --nshards 1 --limit 3
"""),
        code("""
from PIL import Image
import os
outs = sorted(os.listdir('/kaggle/working/out'))[:3]
print('получено:', outs)
if outs:
    im = Image.open(f'/kaggle/working/out/{outs[0]}')
    print('размер результата:', im.size, '(должен быть 896x1200)')
    im.resize((im.width//2, im.height//2))
"""),
        md("""
## Боевой прогон на двух картах

Задачи делятся через одну, готовое пропускается. Если сессия оборвётся —
запустить эту ячейку заново, она продолжит с места остановки.
"""),
        code("""
import os, subprocess, threading, time

t0 = time.time()
procs = [
    subprocess.Popen(
        ['python', '/kaggle/working/worker.py', '--shard', str(s), '--nshards', '2'],
        env=dict(os.environ, CUDA_VISIBLE_DEVICES=str(s)),
        cwd='/kaggle/working',
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for s in range(2)
]

def pump(p):
    for line in p.stdout:
        print(line.rstrip(), flush=True)

th = [threading.Thread(target=pump, args=(p,), daemon=True) for p in procs]
[t.start() for t in th]
[p.wait() for p in procs]
[t.join(timeout=5) for t in th]

n = len(os.listdir('/kaggle/working/out'))
print(f"\\nготово {n} из {len(WORKLIST)} за {(time.time()-t0)/3600:.1f} ч")
"""),
        md("## Контроль качества\n\nСмотрим случайную дюжину — не пустые ли, не съехала ли поза."),
        code("""
import random, os
from PIL import Image
files = os.listdir('/kaggle/working/out')
random.seed(1); pick = random.sample(files, min(12, len(files)))
sheet = Image.new('RGB', (6*150, 2*200), 'white')
for i, f in enumerate(pick):
    im = Image.open(f'/kaggle/working/out/{f}').resize((150, 200))
    sheet.paste(im, ((i % 6)*150, (i // 6)*200))
sheet.save('/kaggle/working/qc.jpg', quality=88)
sheet
"""),
        md("## Забрать результаты\n\nАрхив бьём по 500 штук — иначе получится слишком большой файл."),
        code("""
import os, zipfile
files = sorted(os.listdir('/kaggle/working/out'))
for n, i in enumerate(range(0, len(files), 500), 1):
    part = files[i:i+500]
    with zipfile.ZipFile(f'/kaggle/working/tryon_{n:02d}.zip', 'w', zipfile.ZIP_STORED) as z:
        for f in part:
            z.write(f'/kaggle/working/out/{f}', f)
    print(f'tryon_{n:02d}.zip — {len(part)} шт')
"""),
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

    print(f"готово: {OUT}")
    print(f"размер: {os.path.getsize(OUT)/1024/1024:.1f} МБ")


if __name__ == "__main__":
    main()
