"""
Примерка обуви через Gemini: обувь надевается на нашу базу-манекен.

Почему именно так, а не «попроси только обувь на прозрачном фоне». Вырезки
одежды садятся идеально не потому, что вырезаны, а потому что отрисованы на
НАШЕЙ базе и вырезаны на месте — совмещение достаётся бесплатно. Если просить
обувь отдельной картинкой, мы возвращаемся к задаче «куда её поставить»: с
каким наклоном, в каком масштабе, под каким ракурсом. Именно на этом сломалась
прошлая попытка — пара ложилась пятнами сбоку от босых ступней.

Открытая FASHN VTON 1.5, на которой сделана вся одежда, обувь не умеет
(категории только tops/bottoms/one-pieces), поэтому здесь Gemini.

    python scripts/tryon_shoes.py --limit 5      # проба, ~35 центов
    python scripts/tryon_shoes.py                # вся обувь

Скрипт только складывает кадры в shoes_raw/ и печатает проверку совмещения.
Вырезание — отдельным шагом, после того как человек посмотрит глазами.
"""
import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RES = os.path.abspath(os.path.join(ROOT, "..", "app", "src", "main", "res", "drawable-nodpi"))
OUT = os.path.join(ROOT, "shoes_raw")
PROPS = os.path.abspath(os.path.join(ROOT, "..", "local.properties"))
CATALOG = os.path.join(ROOT, "public", "catalog.json")

MODEL = "gemini-3.1-flash-image"
API = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROMPT = (
    "Image 1 is a full-body photo of a model standing barefoot. "
    "Image 2 shows a pair of shoes on a plain background. "
    "Put exactly those shoes on the model's feet, both feet, correctly sized and "
    "oriented to how the feet are standing, resting flat on the same floor. "
    "Keep the shoes' colour, material, straps and details exactly as in image 2. "
    "Everything else in image 1 must stay pixel for pixel the same: the same person, "
    "the same pose, the same body, the same clothing, the same framing, the same "
    "lighting, the same plain background. Do not move, rescale or re-pose the body. "
    "Change nothing above the ankles."
)


def api_key():
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    with open(PROPS, encoding="utf-8") as f:
        for line in f:
            if line.startswith("GEMINI_API_KEY"):
                return line.split("=", 1)[1].strip()
    sys.exit("нет GEMINI_API_KEY")


def b64_file(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def b64_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return base64.b64encode(urllib.request.urlopen(req, timeout=40).read()).decode()


def generate(key, base_path, shoe_url):
    body = {"contents": [{"parts": [
        {"text": PROMPT},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64_file(base_path)}},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64_url(shoe_url)}},
    ]}]}
    req = urllib.request.Request(API % (MODEL, key), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=300))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Gemini {e.code}: {e.read().decode('utf-8', 'replace')[:600]}")
    cand = (resp.get("candidates") or [{}])[0]
    for part in (cand.get("content") or {}).get("parts", []):
        blob = part.get("inline_data") or part.get("inlineData")
        if blob:
            return Image.open(io.BytesIO(base64.b64decode(blob["data"])))
    raise RuntimeError(f"нет картинки, finishReason={cand.get('finishReason')}")


def legs_line(img):
    """Где кончаются ноги и какой они ширины — по коже.

    Если ступни уехали, вырезка ляжет мимо, поэтому сверяем именно низ.
    """
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    h, w, _ = a.shape
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    skin = (r > g) & (g > b) & ((r - b) > 18) & (r > 90) & (r < 245)
    rows = skin.mean(axis=1)
    ys = np.where(rows > 0.003)[0]
    if not len(ys):
        return None
    knee = int(h * 0.75)
    line = skin[knee]
    on = np.where(line)[0]
    return {"низ кожи": ys.max() / h,
            "ширина ног на 0.75": (on.max() - on.min()) / w if len(on) else 0.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5)
    a = ap.parse_args()

    items = json.load(open(CATALOG, encoding="utf-8"))["items"]
    shoes = [i for i in items if i["category"] == "FOOTWEAR"]
    # берём вперемешку разные виды, чтобы проба была честной
    by_sub = {}
    for i in shoes:
        by_sub.setdefault(i.get("subCategory"), []).append(i)
    pick = []
    while len(pick) < a.limit and any(by_sub.values()):
        for k in list(by_sub):
            if by_sub[k] and len(pick) < a.limit:
                pick.append(by_sub[k].pop(0))
    print(f"обуви в каталоге: {len(shoes)}, берём: {len(pick)}")
    print(f"примерно к оплате: ${len(pick) * 0.067:.2f}\n")

    os.makedirs(OUT, exist_ok=True)
    key = api_key()
    bases = {"male": os.path.join(RES, "premium_male.jpg"),
             "female": os.path.join(RES, "premium_female.jpg")}

    for n, it in enumerate(pick, 1):
        base_path = bases.get(it.get("gender"), bases["female"])
        t0 = time.time()
        try:
            img = generate(key, base_path, it["imageUrl"])
        except SystemExit as e:
            print(e)
            return
        except Exception as e:
            print(f"  сбой {it['id']}: {type(e).__name__} {e}")
            continue
        orig = Image.open(base_path)
        if img.size != orig.size:
            img = img.resize(orig.size, Image.LANCZOS)
        path = os.path.join(OUT, f"{it['id']}.jpg")
        img.convert("RGB").save(path, "JPEG", quality=95)

        was, now = legs_line(orig), legs_line(img)
        drift = max(abs(was[k] - now[k]) for k in was) if was and now else 1.0
        flag = "  <-- ноги уехали, вырезка ляжет мимо" if drift > 0.02 else ""
        print(f"  {n}/{len(pick)} {it['id']:<11} {it.get('subCategory'):<9} "
              f"{time.time()-t0:4.0f} с  сдвиг низа {drift*100:4.1f}%{flag}")

    print(f"\nкадры: {OUT}\nпосмотри глазами — вырезать будем только если сидит верно")


if __name__ == "__main__":
    main()
