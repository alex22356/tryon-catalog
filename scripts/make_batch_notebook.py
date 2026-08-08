"""
Собирает боевой Kaggle-ноутбук примерки каталога через FASHN VTON v1.5.

Лежит в scripts/, а не в vton/: из vton/ файлы .py трижды исчезали в корзину.

ВАЖНО про формат ноутбука: source ячейки — это СПИСОК СТРОК, каждая со своим
переводом строки на конце. Собирать его через text.split("\\n") нельзя —
переводы срезаются, редактор склеивает всё в одну строку, и код превращается
в один сплошной комментарий. Только splitlines(keepends=True).
"""
import base64
import io
import json
import os

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "public", "catalog.json")
DRAWABLE = os.path.join(os.path.dirname(ROOT), "app", "src", "main", "res", "drawable-nodpi")
OUT = os.path.join(ROOT, "vton", "fashn_batch.ipynb")

CAT = {"TOP": "tops", "BOTTOM": "bottoms", "FULL_BODY": "one-pieces"}
# Пол товара -> манекен. kids идёт на мальчика: детский раздел DV8 почти
# целиком мальчиковый, разделения boy/girl в данных пока нет (см. BACKLOG.md).
BASE_FOR = {
    "female": "premium_female", "male": "premium_male",
    "kids": "premium_boy", "boy": "premium_boy", "girl": "premium_girl",
}


def cell(kind, text):
    """Ячейка с сохранением переводов строк — иначе редактор склеит всё в одну."""
    return {
        "cell_type": kind,
        "metadata": {},
        "source": text.splitlines(keepends=True),
        **({"execution_count": None, "outputs": []} if kind == "code" else {}),
    }


def build_worklist():
    with open(CATALOG, encoding="utf-8") as f:
        items = json.load(f)["items"]
    work, skipped = [], 0
    for it in items:
        if it.get("overlayUrl"):
            continue
        cat = CAT.get(it.get("category"))
        if not cat or not it.get("imageUrl"):
            continue
        base = BASE_FOR.get(it.get("gender"))
        if not base:
            skipped += 1
            continue
        work.append({"id": it["id"], "url": it["imageUrl"], "cat": cat, "base": base})
    return work, skipped


def embed(names):
    out = {}
    for n in sorted(names):
        img = Image.open(os.path.join(DRAWABLE, n + ".jpg")).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=92)
        out[n] = base64.b64encode(buf.getvalue()).decode()
        print(f"  вшит манекен {n}: {img.size}")
    return out


WORKER = '''import argparse, json, os, sys, time, urllib.request
import numpy as np
import torch
from PIL import Image

# T4 — это Turing, аппаратного bf16 у него нет, а torch.cuda.is_bf16_supported()
# всё равно возвращает True (учитывает программную эмуляцию). Из-за этого первый
# прогон дал 408 с на картинку вместо 40. Гасим автовыбор и ставим fp16 вручную:
# у Turing он идёт через тензорные ядра.
torch.cuda.is_bf16_supported = lambda *a, **k: False

sys.path.insert(0, "/kaggle/working/fashn/src")
from fashn_vton import TryOnPipeline

ap = argparse.ArgumentParser()
ap.add_argument("--shard", type=int, required=True)
ap.add_argument("--nshards", type=int, default=2)
ap.add_argument("--steps", type=int, default=20)
ap.add_argument("--limit", type=int, default=0)
a = ap.parse_args()

OUT, CACHE = "/kaggle/working/out", "/kaggle/working/garments"
os.makedirs(OUT, exist_ok=True)
os.makedirs(CACHE, exist_ok=True)

tasks = json.load(open("/kaggle/working/worklist.json", encoding="utf-8"))
mine = tasks[a.shard::a.nshards]
mine = [t for t in mine if not os.path.exists(f"{OUT}/{t['id']}.png")]
if a.limit:
    mine = mine[:a.limit]
print(f"[gpu{a.shard}] к работе: {len(mine)}", flush=True)

pipe = TryOnPipeline(weights_dir="/kaggle/working/weights")
pipe.inference_dtype = torch.float16
pipe.tryon_model.half()

# Манекенов немного, а вещей тысячи — поза каждого считается один раз.
# Определитель поз работает на процессоре (onnxruntime собран под CUDA 13,
# на Kaggle 12-я), так что экономия заметная.
_raw, _pose_cache = pipe.pose_model, {}
def cached_pose(img_bgr):
    key = hash(img_bgr.tobytes())
    if key not in _pose_cache:
        _pose_cache[key] = _raw(img_bgr)
    return _pose_cache[key]
pipe.pose_model = cached_pose

up_model = None
try:
    from spandrel import ModelLoader
    up_model = ModelLoader().load_from_file("/kaggle/working/up/realesrgan_x2.pth").cuda().eval()
    print(f"[gpu{a.shard}] апскейлер поднят", flush=True)
except Exception as e:
    print(f"[gpu{a.shard}] БЕЗ апскейлера ({type(e).__name__}) — будет Ланцош", flush=True)

TARGET = (896, 1200)

def upscale(img):
    if up_model is None:
        return img.resize(TARGET, Image.LANCZOS)
    t = torch.from_numpy(np.asarray(img, np.float32) / 255).permute(2, 0, 1)[None].cuda()
    with torch.no_grad():
        o = up_model(t)
    big = Image.fromarray((o[0].permute(1, 2, 0).clamp(0, 1).cpu().numpy() * 255).astype("uint8"))
    return big.resize(TARGET, Image.LANCZOS)

_bases = {}
def base_for(name):
    if name not in _bases:
        _bases[name] = Image.open(f"/kaggle/working/bases/{name}.jpg")
    return _bases[name]

t0, done, failed = time.time(), 0, 0
for k, t in enumerate(mine, 1):
    path = f"{CACHE}/{t['id']}.jpg"
    try:
        if not os.path.exists(path):
            req = urllib.request.Request(t["url"], headers={"User-Agent": "Mozilla/5.0"})
            with open(path, "wb") as f:
                f.write(urllib.request.urlopen(req, timeout=40).read())
        garment = Image.open(path).convert("RGB")
        res = pipe(
            person_image=base_for(t["base"]),
            garment_image=garment,
            category=t["cat"],
            garment_photo_type="model",
            num_timesteps=a.steps,
        )
        upscale(res.images[0]).save(f"{OUT}/{t['id']}.png")
        done += 1
    except Exception as e:
        failed += 1
        print(f"[gpu{a.shard}] СБОЙ {t['id']}: {type(e).__name__} {e}", flush=True)

    if k % 25 == 0:
        el = time.time() - t0
        print(f"[gpu{a.shard}] {k}/{len(mine)}  {el/k:.1f} с/шт  "
              f"осталось ~{(len(mine)-k)*el/k/3600:.1f} ч  сбоев {failed}", flush=True)

print(f"[gpu{a.shard}] ВСЁ: сделано {done}, сбоев {failed}", flush=True)
'''


def main():
    work, skipped = build_worklist()
    bases = embed({w["base"] for w in work})
    import collections
    by_base = collections.Counter(w["base"] for w in work)
    print(f"позиций: {len(work)}, пропущено без пола: {skipped}")
    print(f"по манекенам: {dict(by_base)}")

    cells = [
        cell("markdown", f"""# Примерка каталога — FASHN VTON v1.5

**{len(work)} позиций.** Каждая вещь примеряется на манекене СВОЕГО пола:
женские на женском ({by_base['premium_female']}), мужские на мужском
({by_base['premium_male']}), детские на детском ({by_base.get('premium_boy', 0)}).
Иначе оверлей не совпадёт с базой в приложении — он кладётся на неё один в один.

Обувь исключена: FASHN знает только tops / bottoms / one-pieces, её отдельно
через Gemini. Позиции с неразмеченным полом пропущены: {skipped} шт.

## Перед запуском

Справа в панели: `Accelerator` → **GPU T4 x2**, `Internet` → **On**.
Без интернета не скачаются ни модель, ни фотографии товаров.

## Как гнать в фоне

1. В ячейке ниже оставь `PART = 1`, нажми **Save Version → Save & Run All**
2. Дождись, пока сессия появится в списке версий
3. Поменяй на `PART = 2`, снова **Save Version → Save & Run All**

Kaggle разрешает два фоновых коммита одновременно, поэтому обе половины
считаются параллельно — около 6 часов вместо 12.

**Почему части, а не один прогон:** фоновая сессия обрывается на 9-м часу и
всегда начинает с пустой `/kaggle/working`. Возобновление внутри одного запуска
работает, между запусками — нет. Если часть не успеет, поставь `NPARTS = 4`.
"""),
        cell("code", """# ═══════════ НАСТРОЙКА ЗАПУСКА ═══════════
PART = 1
NPARTS = 2
# ═════════════════════════════════════════
print(f"эта сессия считает часть {PART} из {NPARTS}")
"""),
        cell("code", "WORKLIST = " + json.dumps(work, ensure_ascii=False) + "\n"),
        cell("code", "BASES_B64 = " + json.dumps(bases) + "\n"),
        cell("markdown", "## Установка\n"),
        cell("code", """!git clone -q https://github.com/fashn-AI/fashn-vton-1.5.git /kaggle/working/fashn
%cd /kaggle/working/fashn
!pip install -q -e . 2>&1 | tail -2
!pip install -q spandrel 2>&1 | tail -1
!python scripts/download_weights.py --weights-dir /kaggle/working/weights 2>&1 | tail -2
"""),
        cell("code", """import base64, collections, io, json, os, urllib.request
from PIL import Image

os.makedirs('/kaggle/working/up', exist_ok=True)
os.makedirs('/kaggle/working/bases', exist_ok=True)
for name, b64 in BASES_B64.items():
    Image.open(io.BytesIO(base64.b64decode(b64))).save(f'/kaggle/working/bases/{name}.jpg')

# Срез через шаг, а не первая половина подряд: так вещи разных типов
# распределятся по частям поровну
MY_PART = WORKLIST[PART-1::NPARTS]
json.dump(MY_PART, open('/kaggle/working/worklist.json', 'w', encoding='utf-8'), ensure_ascii=False)

try:
    urllib.request.urlretrieve(
        'https://huggingface.co/ai-forever/Real-ESRGAN/resolve/main/RealESRGAN_x2.pth',
        '/kaggle/working/up/realesrgan_x2.pth')
    print('апскейлер скачан')
except Exception as e:
    print('апскейлер не скачался, будет Ланцош:', e)

print(f'всего в каталоге: {len(WORKLIST)}')
print(f'в части {PART}: {len(MY_PART)} — примерно {len(MY_PART)*40/2/3600:.1f} ч на двух картах')
print('по манекенам:', dict(collections.Counter(w['base'] for w in MY_PART)))
"""),
        cell("markdown", """## Пробный запуск

Три вещи на одной карте — поймать ошибку за минуту, а не за час.
Здесь же видно, сработала ли починка типа данных: ждём около **40 секунд** на
вещь, а не 400.
"""),
        cell("code", "%%writefile /kaggle/working/worker.py\n" + WORKER),
        cell("code", "!cd /kaggle/working && python worker.py --shard 0 --nshards 1 --limit 3\n"),
        cell("code", """import os
from PIL import Image
outs = sorted(os.listdir('/kaggle/working/out'))[:3]
print('получено:', outs)
if outs:
    im = Image.open(f'/kaggle/working/out/{outs[0]}')
    print('размер результата:', im.size, '(должен быть 896x1200)')
    display(im.resize((im.width//2, im.height//2)))
"""),
        cell("markdown", """## Боевой прогон на двух картах

Задачи делятся через одну, готовое пропускается. Если сессия оборвётся —
запустить эту ячейку заново, она продолжит с места остановки.
"""),
        cell("code", """import os, subprocess, threading, time

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
print(f"\\nготово {n} из {len(MY_PART)} за {(time.time()-t0)/3600:.1f} ч")
"""),
        cell("markdown", "## Контроль качества\n\nСлучайная дюжина: не пустые ли, тот ли манекен.\n"),
        cell("code", """import os, random
from PIL import Image
files = os.listdir('/kaggle/working/out')
random.seed(1)
pick = random.sample(files, min(12, len(files)))
sheet = Image.new('RGB', (6*150, 2*200), 'white')
for i, f in enumerate(pick):
    sheet.paste(Image.open(f'/kaggle/working/out/{f}').resize((150, 200)),
                ((i % 6)*150, (i // 6)*200))
sheet.save('/kaggle/working/qc.jpg', quality=88)
sheet
"""),
        cell("markdown", "## Забрать результаты\n\nАрхивы бьём по 500 штук и метим номером части.\n"),
        cell("code", """import os, zipfile
files = sorted(os.listdir('/kaggle/working/out'))
for n, i in enumerate(range(0, len(files), 500), 1):
    chunk = files[i:i+500]
    name = f'/kaggle/working/tryon_p{PART}_{n:02d}.zip'
    with zipfile.ZipFile(name, 'w', zipfile.ZIP_STORED) as z:
        for f in chunk:
            z.write(f'/kaggle/working/out/{f}', f)
    print(f'{os.path.basename(name)} — {len(chunk)} шт')
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
    print(f"\nготово: {OUT} ({os.path.getsize(OUT)/1024/1024:.1f} МБ, ячеек {len(cells)})")


if __name__ == "__main__":
    main()
