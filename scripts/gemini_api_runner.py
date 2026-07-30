#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Примерка через Gemini API — полный автомат, без браузера.

Почему так: браузерные пути закрыты Google намеренно
  • вход из автоматизированного браузера блокируется;
  • Chrome 136+ игнорирует отладочный порт на основном профиле (защита куки).
Бесплатной генерации картинок в API нет ни на одной модели (free limit 0),
поэтому нужен включённый биллинг: ~4 цента за вещь.

Что делает: берёт очередь (те же товары, что и браузерный раннер),
для каждой вещи шлёт в API базовую модель + фото вещи с промтом под категорию,
кладёт результат в tryon_out/<id>.png. Резюмируемый: готовое пропускает.

Запуск:
    python scripts/gemini_api_runner.py                 # вся очередь
    python scripts/gemini_api_runner.py --limit 3       # только 3 (проверить)
    python scripts/gemini_api_runner.py --all           # включая массовый сбор
    python scripts/gemini_api_runner.py --model gemini-3.1-flash-image
"""

import io
import os
import sys
import json
import time
import base64
import argparse
import urllib.request
import urllib.error

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gemini_browser_runner import (
    HERE, MODEL_IMAGE, OUT_DIR, PROMPTS, PROMPT, garments_queue, products, log)

LOCAL_PROPS = os.path.abspath(os.path.join(HERE, "..", "local.properties"))
DEFAULT_MODEL = "gemini-2.5-flash-image"
API = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s"

PRICE_PER_IMAGE = 0.04      # ориентировочно, для подсчёта в выводе


def api_key():
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    if os.path.exists(LOCAL_PROPS):
        for line in io.open(LOCAL_PROPS, encoding="utf-8"):
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def b64(path):
    return base64.b64encode(open(path, "rb").read()).decode()


def generate(key, model, prompt, model_img, garment_img, timeout=240):
    """Один запрос. Возвращает bytes картинки или бросает исключение."""
    body = {"contents": [{"parts": [
        {"text": prompt},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64(model_img)}},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64(garment_img)}},
    ]}]}
    req = urllib.request.Request(API % (model, key), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=timeout))
    for cand in r.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            d = part.get("inlineData") or part.get("inline_data")
            if d and d.get("data"):
                return base64.b64decode(d["data"])
    # картинки нет — покажем, что ответила модель
    texts = [p.get("text", "") for c in r.get("candidates", [])
             for p in c.get("content", {}).get("parts", [])]
    raise RuntimeError("картинки в ответе нет. " + (" ".join(texts)[:160] or str(r)[:160]))


def explain_http(e):
    msg = e.read().decode("utf-8", "replace")
    if e.code == 429 and "limit: 0" in msg:
        return ("БИЛЛИНГ НЕ ВКЛЮЧЁН: бесплатная квота на генерацию картинок = 0.\n"
                "        Включи оплату в Google AI Studio → Get API key → проект с биллингом.\n"
                "        Стоимость ~4 цента за вещь.")
    if e.code == 429:
        return "слишком часто — подожду и повторю"
    if e.code in (401, 403):
        return "ключ не принят (проверь GEMINI_API_KEY в local.properties)"
    return f"HTTP {e.code}: {msg[:160]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    key = api_key()
    if not key:
        log("нет GEMINI_API_KEY (local.properties)"); return
    if not os.path.exists(MODEL_IMAGE):
        log("нет базовой модели:", MODEL_IMAGE); return

    todo = garments_queue(only_picked=not args.all, limit=args.limit)
    names = {p["id"]: p for p in products()}
    if not todo:
        log("Нечего примерять."); return

    os.makedirs(OUT_DIR, exist_ok=True)
    log(f"Модель: {args.model}")
    log(f"В очереди: {len(todo)}   ориентировочно ~${len(todo) * PRICE_PER_IMAGE:.2f}")
    log("")

    done = fail = 0
    t_start = time.time()
    for n, (pid, gpath, cat) in enumerate(todo, 1):
        title = names.get(pid, {}).get("name", "")[:46]
        log(f"[{n}/{len(todo)}] {cat}  {title}")
        out = os.path.join(OUT_DIR, pid + ".png")
        prompt = PROMPTS.get(cat, PROMPT)

        for attempt in range(1, 4):
            t0 = time.time()
            try:
                data = generate(key, args.model, prompt, MODEL_IMAGE, gpath)
                open(out, "wb").write(data)
                done += 1
                log(f"    ✓ {time.time()-t0:.0f}с, {len(data)//1024} КБ  "
                    f"→ tryon_out/{pid}.png   ({done} из {len(todo)})")
                break
            except urllib.error.HTTPError as e:
                reason = explain_http(e)
                log(f"    ! {reason}")
                if "БИЛЛИНГ" in reason:
                    log("")
                    log("Останавливаюсь: без биллинга генерация невозможна.")
                    return
                if e.code == 429 and attempt < 3:
                    time.sleep(30)
                    continue
                fail += 1
                break
            except Exception as e:
                log(f"    ! ошибка: {str(e)[:120]}")
                if attempt < 3:
                    time.sleep(5)
                    continue
                fail += 1

    dt = time.time() - t_start
    log("")
    log(f"ИТОГ: сделано {done}, ошибок {fail}, за {dt/60:.1f} мин "
        f"(~${done * PRICE_PER_IMAGE:.2f})")
    if done:
        log("Дальше: пункт 2 — опубликует в приложение, пункт 4 — выложит клиентам")


if __name__ == "__main__":
    main()
