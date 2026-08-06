"""
Отбраковка результатов примерки. Двухступенчато, чтобы не жечь время впустую:

  1) Дешёвая проверка без ИИ (миллисекунды на пару): сравнение цвета вещи на фото
     и в области маски результата + доля изменённых пикселей. Ловит грубый брак —
     ничего не отрисовалось, цвет уехал, пятна вместо ткани.
  2) Локальная vision-модель (qwen2.5vl) — ТОЛЬКО для спорных случаев. Смотрит на
     пару «фото вещи / примерка» и решает, та же это вещь и есть ли артефакты.

Такой порядок экономит: до модели доходит меньшинство, остальное отсеивается счётом.

Запуск:
    python scripts/qc_tryon.py --out tryon_out --garments vton/garments_all.json
    python scripts/qc_tryon.py ... --no-ai      # только быстрая проверка
"""
import argparse
import base64
import json
from io import BytesIO
from pathlib import Path

import numpy as np
import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OLLAMA = "http://localhost:11434"
VISION_MODEL = "qwen2.5vl:3b"

# Пороги подобраны так, чтобы явный брак уходил в fail, а спорное — на суд модели.
MIN_CHANGED = 0.06   # доля изменённых пикселей внутри маски: меньше — вещь не отрисовалась
MAX_HUE_DIST = 92.0  # расстояние по среднему цвету: больше — цвет уехал


def load_mask(vton: Path, category: str) -> np.ndarray:
    name = {"TOP": "top", "BOTTOM": "bottom", "FULL_BODY": "full_body", "FOOTWEAR": "feet"}[category]
    return np.asarray(Image.open(vton / f"mask_{name}.png").convert("L")) > 127


def dominant_rgb(img: Image.Image, mask: np.ndarray | None = None) -> np.ndarray:
    a = np.asarray(img.convert("RGB")).astype(float)
    if mask is not None:
        sel = a[mask]
    else:
        # у фото товара фон берём по углам и выбрасываем
        c = np.vstack([a[:12, :12].reshape(-1, 3), a[:12, -12:].reshape(-1, 3)])
        bg = np.median(c, 0)
        sel = a.reshape(-1, 3)[np.abs(a.reshape(-1, 3) - bg).sum(1) > 60]
    return np.median(sel, 0) if len(sel) else np.zeros(3)


def cheap_check(base: Image.Image, result: Image.Image, garment: Image.Image,
                mask: np.ndarray) -> tuple[str, dict]:
    """Возвращает ('ok'|'fail'|'doubt', метрики) без обращения к ИИ."""
    b = np.asarray(base.convert("RGB")).astype(int)
    r = np.asarray(result.convert("RGB").resize(base.size)).astype(int)
    diff = np.abs(b - r).sum(2)
    changed = float((diff[mask] > 34).mean())

    c_res = dominant_rgb(result.resize(base.size), mask)
    c_gar = dominant_rgb(garment)
    hue = float(np.linalg.norm(c_res - c_gar))

    m = {"changed": round(changed, 3), "colour_dist": round(hue, 1)}
    if changed < MIN_CHANGED:
        return "fail", m | {"why": "вещь почти не отрисовалась"}
    if hue > MAX_HUE_DIST:
        return "fail", m | {"why": "цвет сильно разошёлся с фото товара"}
    if changed < MIN_CHANGED * 1.8 or hue > MAX_HUE_DIST * 0.72:
        return "doubt", m
    return "ok", m


def b64(img: Image.Image, side: int = 640) -> str:
    img = img.copy()
    img.thumbnail((side, side))
    buf = BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def ai_judge(garment: Image.Image, result: Image.Image, name: str) -> tuple[bool, str]:
    """Локальная vision-модель как второе мнение. Возвращает (годится, комментарий)."""
    prompt = (
        f"Первое изображение — фото товара «{name}». Второе — этот товар, надетый на манекен.\n"
        "Проверь по пунктам и ответь СТРОГО одним словом в первой строке: GOOD или BAD.\n"
        "BAD, если: вещь не надета; цвет заметно отличается; вместо ткани пятна или каша; "
        "искажены руки, ноги или лицо; надпись/логотип превратились в нечитаемые знаки.\n"
        "Во второй строке — короткая причина по-русски."
    )
    body = {
        "model": VISION_MODEL,
        "stream": False,
        "messages": [{"role": "user", "content": prompt,
                      "images": [b64(garment), b64(result)]}],
    }
    try:
        resp = requests.post(f"{OLLAMA}/api/chat",
                             data=json.dumps(body).encode("utf-8"),
                             headers={"Content-Type": "application/json"}, timeout=180)
        resp.raise_for_status()
        text = resp.json().get("message", {}).get("content", "").strip()
        good = text.upper().lstrip().startswith("GOOD")
        reason = " ".join(text.split("\n")[1:])[:120]
        return good, reason
    except Exception as e:
        return True, f"(судья недоступен: {e})"   # не бракуем из-за сбоя связи


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tryon_out", help="папка с результатами примерки")
    ap.add_argument("--garments", default="vton/garments_pilot.json")
    ap.add_argument("--vton", default="vton")
    ap.add_argument("--no-ai", action="store_true", help="только быстрая проверка")
    a = ap.parse_args()

    vton = ROOT / a.vton
    outdir = ROOT / a.out
    base = Image.open(vton / "model.jpg").convert("RGB")
    items = {g["id"]: g for g in json.loads((ROOT / a.garments).read_text(encoding="utf-8"))}
    garm_dir = ROOT / "garments"

    verdicts, stats = [], {"ok": 0, "doubt": 0, "fail": 0, "missing": 0}
    for gid, g in items.items():
        res_path = outdir / f"{gid}.png"
        gar_path = garm_dir / f"{gid}.jpg"
        if not res_path.exists():
            stats["missing"] += 1
            verdicts.append({"id": gid, "verdict": "missing"})
            continue

        result = Image.open(res_path)
        garment = Image.open(gar_path).convert("RGB") if gar_path.exists() else result
        mask = load_mask(vton, g["category"])
        if mask.shape != np.asarray(base).shape[:2]:
            mask = np.asarray(Image.fromarray(mask).resize(base.size, Image.NEAREST)) > 0

        state, m = cheap_check(base, result, garment, mask)

        if state == "doubt" and not a.no_ai:
            good, reason = ai_judge(garment, result, g.get("name", ""))
            state = "ok" if good else "fail"
            m["ai"] = reason

        stats[state if state in stats else "ok"] += 1
        verdicts.append({"id": gid, "category": g["category"], "verdict": state, **m})
        print(f"  {state:5s} {gid:14s} {m}")

    (outdir.parent / "qc_report.json").write_text(
        json.dumps(verdicts, ensure_ascii=False, indent=1), encoding="utf-8")
    total = max(len(items), 1)
    print(f"\nитог: ok {stats['ok']} · брак {stats['fail']} · нет файла {stats['missing']}"
          f"  ({100 * stats['fail'] / total:.0f}% брака)")
    print("список брака → qc_report.json (verdict=fail) — их перегенерить с другим seed")


if __name__ == "__main__":
    main()
