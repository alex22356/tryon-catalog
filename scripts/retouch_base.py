"""
Перерисовывает бельё на базах-манекенах — и проверяет, что тело не сдвинулось.

Зачем. База снята в телесном боди на бретелях до середины бедра. Пока примерка
была полнокадровой, боди пряталось обрезкой. С вырезанными вещами оно вылезло:
бретели идут поверх плеч (у топа-бандо появляются лямки, которых нет), между
коротким верхом и низкой посадкой видна бежевая полоса, из-под шорт торчат
бежевые «штанины». Дефект один, а выглядит как три.

Чинится ОДНОЙ картинкой: вырезки содержат только одежду, база — отдельный слой.
Перегенерировать 2211 примерок не нужно.

Но есть жёсткое условие: тело должно остаться на прежнем месте. Вырезки
привязаны к нему пиксель в пиксель, и сдвиг даже на пару процентов разведёт
вещь и фигуру. Поэтому скрипт не подменяет базу молча — он кладёт кандидата
рядом и печатает замеры совмещения. Решение принимает человек.

    python scripts/retouch_base.py --who female          # сделать кандидата
    python scripts/retouch_base.py --who female --check  # только замерить
    python scripts/retouch_base.py --who female --apply  # поставить в приложение

Детские базы скрипт не трогает: перерисовывать детей в меньшей одежде нельзя.
"""
import argparse
import base64
import io
import json
import os
import shutil
import sys
import urllib.error
import urllib.request

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.abspath(os.path.join(ROOT, "..", "app"))
RES = os.path.join(APP, "src", "main", "res", "drawable-nodpi")
OUT = os.path.join(ROOT, "base_candidates")
PROPS = os.path.abspath(os.path.join(ROOT, "..", "local.properties"))

DEFAULT_MODEL = "gemini-3.1-flash-image"
API = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s"

KEEP = ("Keep the person, the pose, the body proportions, the camera framing, "
        "the lighting and the plain studio background EXACTLY as they are. "
        "Do not move, rescale or re-pose the body. Do not change the face or hair. "
        "Change nothing except the garment described below.")

PROMPTS = {
    "female": "Replace the nude bodysuit with plain seamless nude underwear: a "
              "simple strapless bandeau top and matching briefs. No shoulder "
              "straps at all. Bare shoulders, bare midriff, bare thighs. " + KEEP,
    "male": "Replace the nude bodysuit with plain nude boxer briefs only. "
            "No shoulder straps, bare chest, bare midriff, bare thighs. " + KEEP,
}


def api_key():
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    with open(PROPS, encoding="utf-8") as f:
        for line in f:
            if line.startswith("GEMINI_API_KEY"):
                return line.split("=", 1)[1].strip()
    sys.exit("нет GEMINI_API_KEY: ни в окружении, ни в local.properties")


def landmarks(img):
    """Опорные точки фигуры. Кожу узнаём по R>G>B — фон и бельё так не выглядят."""
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    h, w, _ = a.shape
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    skin = (r > g) & (g > b) & ((r - b) > 18) & (r > 90) & (r < 245)
    rows, cols = skin.mean(axis=1), skin.mean(axis=0)
    ys, xs = np.where(rows > 0.003)[0], np.where(cols > 0.003)[0]
    if not len(ys) or not len(xs):
        return None
    # ширина фигуры на трёх высотах: плечи, талия, бёдра
    body = (np.abs(a - np.median(a[:20, :20].reshape(-1, 3), axis=0)).sum(axis=2) > 45)
    widths = {}
    for name, frac in (("плечи", 0.28), ("талия", 0.46), ("бёдра", 0.56)):
        line = body[int(h * frac)]
        on = np.where(line)[0]
        widths[name] = (on.max() - on.min()) / w if len(on) else 0.0
    return {"верх": ys.min() / h, "низ": ys.max() / h,
            "слева": xs.min() / w, "справа": xs.max() / w, **widths}


def compare(old, new):
    a, b = landmarks(old), landmarks(new)
    if a is None or b is None:
        print("  не нашёл фигуру на одной из картинок")
        return 1.0
    worst = 0.0
    for k in a:
        d = abs(a[k] - b[k])
        worst = max(worst, d)
        flag = "  <-- сдвиг" if d > 0.01 else ""
        print(f"    {k:<8} было {a[k]:.3f}  стало {b[k]:.3f}  разница {d*100:5.2f}%{flag}")
    return worst


def generate(key, prompt, src_path, model):
    with open(src_path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    body = {"contents": [{"parts": [
        {"text": prompt},
        {"inline_data": {"mime_type": "image/jpeg", "data": data}},
    ]}]}
    req = urllib.request.Request(API % (model, key), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=240))
    except urllib.error.HTTPError as e:
        # Голый код ответа ничего не объясняет: у 429 внутри лежит, какая именно
        # квота кончилась и через сколько можно повторить.
        raise SystemExit(f"Gemini {e.code}: {e.read().decode('utf-8', 'replace')[:900]}")
    for part in resp["candidates"][0]["content"]["parts"]:
        blob = part.get("inline_data") or part.get("inlineData")
        if blob:
            return Image.open(io.BytesIO(base64.b64decode(blob["data"])))
    raise RuntimeError("в ответе нет картинки: " + json.dumps(resp)[:400])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--who", default="female", choices=sorted(PROMPTS))
    ap.add_argument("--model", default=DEFAULT_MODEL, help="картиночная модель")
    ap.add_argument("--check", action="store_true", help="только замерить кандидата")
    ap.add_argument("--apply", action="store_true", help="поставить кандидата в приложение")
    a = ap.parse_args()

    src = os.path.join(RES, f"premium_{a.who}.jpg")
    os.makedirs(OUT, exist_ok=True)
    cand = os.path.join(OUT, f"premium_{a.who}.jpg")

    if a.apply:
        if not os.path.exists(cand):
            sys.exit("кандидата нет — сначала сгенерируй")
        shutil.copy2(src, os.path.join(OUT, f"premium_{a.who}_before.jpg"))
        shutil.copy2(cand, src)
        print(f"поставлено: {src}\nстарая база сохранена в base_candidates/")
        return

    if not a.check:
        img = generate(api_key(), PROMPTS[a.who], src, a.model)
        # размер обязан совпасть: вырезки 896x1200 привязаны к нему
        orig = Image.open(src)
        if img.size != orig.size:
            print(f"  модель вернула {img.size}, привожу к {orig.size}")
            img = img.resize(orig.size, Image.LANCZOS)
        img.convert("RGB").save(cand, "JPEG", quality=95)
        print(f"кандидат: {cand}")

    print("\nсовмещение с прежней базой:")
    worst = compare(Image.open(src), Image.open(cand))
    print(f"\n  худшее расхождение: {worst*100:.2f}%")
    print("  до 1% вещи сядут как прежде; больше — вырезки поедут, брать нельзя")


if __name__ == "__main__":
    main()
