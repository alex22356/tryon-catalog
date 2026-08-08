"""
Примерка на манекен через Qwen-Image-Edit (20B) на арендованной видеокарте.

Зачем отдельно от Kaggle-ноутбуков: на T4 (14.6 ГБ) эта модель не помещается —
4 бита требуют ~15 ГБ, а выгрузка слоёв несовместима с квантованием bitsandbytes.
На карте с 24 ГБ (RTX 4090) всё влезает целиком, поэтому здесь нет ни max_memory,
ни offload, ни прочих ухищрений — просто загрузка и работа.

Установка на арендованной машине:
    pip install -U torch diffusers transformers accelerate bitsandbytes pillow requests

Проверка качества на 10 вещах (это первое, что стоит сделать):
    python qwen_tryon.py --garments garments_pilot.json --limit 10

Боевой прогон:
    python qwen_tryon.py --garments garments_all.json --out tryon_out
    nohup python qwen_tryon.py --garments garments_all.json > run.log 2>&1 &

Рядом со скриптом должны лежать model.jpg (манекен) и файл со списком товаров.
Прогон возобновляемый: уже готовые картинки пропускаются.
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import torch
from PIL import Image

MODEL_ID = "Qwen/Qwen-Image-Edit-2509"
HDRS = {"User-Agent": "Mozilla/5.0", "Accept": "image/jpeg,image/png,image/*"}

WHAT = {
    "TOP":       "the top garment from the second image",
    "BOTTOM":    "the trousers or skirt from the second image",
    "FULL_BODY": "the dress from the second image",
    "FOOTWEAR":  "the shoes from the second image, worn on her feet",
}

NEGATIVE = "distorted hands, extra limbs, changed face, blurry, low quality, watermark, cropped"


def build_prompt(category: str) -> str:
    """Инструкция собрана из шаблона, который уже показал себя на Gemini."""
    return (
        f'Dress the woman in the first image in {WHAT.get(category, "the garment from the second image")}. '
        "Keep her face, body shape, pose, hands, skin and the plain background EXACTLY the same. "
        "Reproduce the garment exactly: same colour, same pattern, same print, same text and logos, "
        "same cut and length. If the garment is loose or wide, it must hang loosely away from the body. "
        "Add natural folds, draping and soft shadows. Opaque fabric, not see-through. "
        "Photorealistic studio photo, full body from head to feet."
    )


def download_garments(items: list, garm_dir: Path) -> list:
    garm_dir.mkdir(parents=True, exist_ok=True)

    def one(g):
        p = garm_dir / f"{g['id']}.jpg"
        if not p.exists():
            try:
                r = requests.get(g["imageUrl"], headers=HDRS, timeout=30)
                r.raise_for_status()
                p.write_bytes(r.content)
            except Exception:
                return None
        try:
            Image.open(p).convert("RGB")   # бывает AVIF под расширением .jpg
            return g
        except Exception:
            return None

    with ThreadPoolExecutor(24) as ex:
        return [g for g in ex.map(one, items) if g]


def load_pipe(quant4: bool):
    from diffusers import DiffusionPipeline

    # 4090 — архитектура Ada, bf16 поддерживается аппаратно (в отличие от T4).
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    kwargs = {"torch_dtype": dtype}

    if quant4:
        from diffusers.quantizers import PipelineQuantizationConfig
        kwargs["quantization_config"] = PipelineQuantizationConfig(
            quant_backend="bitsandbytes_4bit",
            quant_kwargs={"load_in_4bit": True, "bnb_4bit_quant_type": "nf4",
                          "bnb_4bit_compute_dtype": dtype},
            components_to_quantize=["transformer", "text_encoder"],
        )

    pipe = DiffusionPipeline.from_pretrained(MODEL_ID, **kwargs)
    pipe.to("cuda")            # целиком на карту: 24 ГБ хватает, выгрузка не нужна
    try:
        pipe.enable_vae_tiling()
    except Exception:
        pass

    free, total = torch.cuda.mem_get_info()
    print(f"модель загружена ({dtype}, quant4={quant4}); "
          f"видеопамять занято {(total - free) / 1e9:.1f} / {total / 1e9:.1f} ГБ")
    return pipe


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="model.jpg", help="фото манекена")
    ap.add_argument("--garments", default="garments_pilot.json")
    ap.add_argument("--out", default="tryon_out")
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--guidance", type=float, default=4.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit", type=int, default=0, help="взять только первые N товаров")
    ap.add_argument("--skip-footwear", action="store_true")
    ap.add_argument("--no-quant", action="store_true", help="без 4-бит (нужно ~40 ГБ)")
    a = ap.parse_args()

    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    model_img = Image.open(a.model).convert("RGB")
    orig = model_img.size

    items = json.loads(Path(a.garments).read_text(encoding="utf-8"))
    if a.skip_footwear:
        items = [g for g in items if g.get("category") != "FOOTWEAR"]
    if a.limit:
        items = items[: a.limit]

    print(f"манекен {orig} · товаров {len(items)}")
    items = download_garments(items, Path("garments"))
    todo = [g for g in items if not (out / f"{g['id']}.png").exists()]
    print(f"скачано {len(items)} · к обработке {len(todo)} (готово {len(items) - len(todo)})")
    if not todo:
        return

    pipe = load_pipe(quant4=not a.no_quant)

    import inspect
    accepts = set(inspect.signature(pipe.__call__).parameters)

    log, t0 = [], time.time()
    for i, g in enumerate(todo, 1):
        try:
            cloth = Image.open(Path("garments") / f"{g['id']}.jpg").convert("RGB")
            kw = {
                "image": [model_img, cloth],
                "prompt": build_prompt(g.get("category", "TOP")),
                "num_inference_steps": a.steps,
                "generator": torch.Generator(device="cuda").manual_seed(a.seed),
            }
            if "negative_prompt" in accepts:
                kw["negative_prompt"] = NEGATIVE
            if "true_cfg_scale" in accepts:
                kw["true_cfg_scale"] = a.guidance
            elif "guidance_scale" in accepts:
                kw["guidance_scale"] = a.guidance

            img = pipe(**kw).images[0]
            img.resize(orig, Image.LANCZOS).save(out / f"{g['id']}.png")
            log.append({"id": g["id"], "ok": True})
            sp = (time.time() - t0) / i
            left = sp * (len(todo) - i) / 3600
            print(f"[{i}/{len(todo)}] ok {g['id']}  {sp:.0f} с/шт · осталось ~{left:.1f} ч", flush=True)
        except Exception as e:
            log.append({"id": g["id"], "ok": False, "err": str(e)[:200]})
            print(f"[{i}/{len(todo)}] ОШИБКА {g['id']}: {e}", flush=True)

    Path("run_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    bad = [x for x in log if not x["ok"]]
    sp = (time.time() - t0) / max(len(todo), 1)
    print(f"\nготово {len(log) - len(bad)} · ошибок {len(bad)} · {sp:.0f} с/шт")
    print(f"на 2245 вещей ≈ {sp * 2245 / 3600:.1f} ч")


if __name__ == "__main__":
    main()
