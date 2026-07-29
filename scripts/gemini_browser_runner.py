#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gemini-примерка через ТВОЙ браузер (Google AI Studio, бесплатные лимиты) — ЭТАП 2.

Идея: гоняет модель premium_model + каждую вещь из garments/ через AI Studio с тем же
промтом-одеванием, что мы отработали. Уважает суточный лимит: поймал «quota/rate limit» —
ждёт и продолжает. Резюмируемый: если tryon_out/<id>.png уже есть — пропускает.

⚠️ Честно:
  • Автоматизация бесплатного UI AI Studio — серая зона по ToS Google (ты используешь свой
    аккаунт и свои лимиты — решение твоё).
  • Я НЕ мог протестировать селекторы AI Studio у тебя. Первый запуск почти наверняка
    потребует подстройки 2-3 селекторов (помечены [ТЮНИНГ]). Запусти, пришли что не так — поправлю.

Установка (один раз):
    pip install playwright
    playwright install chromium
Запуск:
    python scripts/gemini_browser_runner.py
Первый запуск: откроется Chrome в отдельном профиле → ВОЙДИ в Google и открой AI Studio,
выбери модель «Gemini 2.5 Flash Image», затем вернись в терминал и нажми Enter.
"""

import os
import sys
import time
import glob

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ─────────────────────────── КОНФИГ ───────────────────────────
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.abspath(os.path.join(HERE, "..", "app", "src", "main", "res", "drawable-nodpi"))

MODEL_IMAGE = os.path.join(APP, "premium_model.jpg")   # фикс-модель (та же, что в приложении)
GARMENTS_DIR = os.path.join(HERE, "garments")           # вещи из ingest_shein.py
OUT_DIR = os.path.join(HERE, "tryon_out")               # сюда падают «надетые» фото
PROFILE_DIR = os.path.join(HERE, ".chrome_profile")     # отдельный профиль (логин Google живёт тут)

AISTUDIO_URL = "https://aistudio.google.com/prompts/new_chat"

PROMPT = (
    "Dress the woman in the FIRST image with the garment from the SECOND image. "
    "CRITICAL: keep the woman's body, pose, proportions, position, hands, the camera angle, "
    "the framing, the image dimensions and the plain white background PIXEL-IDENTICAL to the first image. "
    "Change NOTHING except adding the garment onto her body. "
    "The garment must sit naturally with realistic folds, draping and soft shadows, truly worn. "
    "Preserve the garment's exact colors and all details (lace, ruffles, hems, prints). "
    "Opaque fabric. Photorealistic. Output the full body on the same white background."
)

# тайминги (сек)
PER_ITEM_PAUSE = 8          # пауза между вещами (щадим лимит)
RESULT_TIMEOUT = 180        # ждать картинку максимум
RATELIMIT_BACKOFF = 900     # поймал лимит → ждать 15 мин, потом повтор
MAX_RETRIES_ITEM = 3

# ── тексты, по которым ловим лимит [ТЮНИНГ при необходимости] ──
RATE_MARKERS = ["rate limit", "quota", "resource has been exhausted",
                "you've reached", "try again later", "limit reached", "исчерпан", "лимит"]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def garments_queue():
    files = sorted(glob.glob(os.path.join(GARMENTS_DIR, "*.jpg")))
    todo = []
    for f in files:
        pid = os.path.splitext(os.path.basename(f))[0]
        if not os.path.exists(os.path.join(OUT_DIR, pid + ".png")):
            todo.append((pid, f))
    return todo


def page_has_ratelimit(page):
    try:
        body = page.inner_text("body").lower()
    except Exception:
        return False
    return any(m in body for m in RATE_MARKERS)


def run_one(page, model_img, garment_img, out_path):
    """Один прогон: приложить 2 фото, вставить промт, запустить, забрать картинку."""
    page.goto(AISTUDIO_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)

    # 1) приложить обе картинки. AI Studio держит скрытый <input type=file>.
    #    [ТЮНИНГ] если не сработает — возможно, нужно сперва кликнуть кнопку «+»/картинка.
    file_inputs = page.locator('input[type="file"]')
    if file_inputs.count() == 0:
        # попробуем открыть меню вставки ассета
        for label in ["Insert assets", "Add", "Image", "Upload"]:
            btn = page.get_by_role("button", name=label)
            if btn.count():
                btn.first.click()
                page.wait_for_timeout(800)
                break
    page.locator('input[type="file"]').first.set_input_files([model_img, garment_img])
    page.wait_for_timeout(3500)  # дать превью подгрузиться

    # 2) вставить промт. [ТЮНИНГ] селектор поля ввода.
    box = None
    for sel in ['textarea', '[contenteditable="true"]', '[aria-label*="prompt" i]', '[placeholder*="prompt" i]']:
        loc = page.locator(sel)
        if loc.count():
            box = loc.first
            break
    if box is None:
        raise RuntimeError("не нашёл поле ввода промта [ТЮНИНГ]")
    box.click()
    box.fill(PROMPT)
    page.wait_for_timeout(500)

    # 3) запустить — в AI Studio это Ctrl+Enter
    page.keyboard.press("Control+Enter")

    # 4) ждать сгенерённую картинку в ответе
    deadline = time.time() + RESULT_TIMEOUT
    result_img = None
    while time.time() < deadline:
        if page_has_ratelimit(page):
            raise RateLimit()
        # берём последнюю картинку-ответ (blob/data), не превью-инпуты
        imgs = page.locator('img[src^="blob:"], img[src^="data:image"]')
        n = imgs.count()
        if n:
            cand = imgs.nth(n - 1)
            try:
                box_sz = cand.bounding_box()
                if box_sz and box_sz["width"] > 200 and box_sz["height"] > 200:
                    result_img = cand
                    break
            except Exception:
                pass
        page.wait_for_timeout(1500)

    if result_img is None:
        raise RuntimeError("картинка не появилась за отведённое время")

    result_img.scroll_into_view_if_needed()
    page.wait_for_timeout(800)
    result_img.screenshot(path=out_path)   # чистый PNG самой картинки, без UI
    return True


class RateLimit(Exception):
    pass


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.exists(MODEL_IMAGE):
        log("НЕ найдена модель:", MODEL_IMAGE); return
    todo = garments_queue()
    log(f"В очереди: {len(todo)} вещей. Готовые пропускаю.")
    if not todo:
        log("Всё уже сделано."); return

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR, channel="chrome", headless=False,
            args=["--start-maximized"], no_viewport=True,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # первый вход — логин вручную
        page.goto(AISTUDIO_URL, wait_until="domcontentloaded")
        input("\n>>> Войди в Google, открой AI Studio, выбери модель 'Gemini 2.5 Flash Image'. "
              "Потом нажми Enter здесь...\n")

        done = 0
        for pid, gpath in todo:
            out = os.path.join(OUT_DIR, pid + ".png")
            for attempt in range(1, MAX_RETRIES_ITEM + 1):
                try:
                    log(f"[{pid}] попытка {attempt}…")
                    run_one(page, MODEL_IMAGE, gpath, out)
                    done += 1
                    log(f"[{pid}] ✓ сохранено -> {out}  ({done}/{len(todo)})")
                    break
                except RateLimit:
                    log(f"[{pid}] ⏳ лимит. Жду {RATELIMIT_BACKOFF//60} мин и продолжу…")
                    time.sleep(RATELIMIT_BACKOFF)
                except (PWTimeout, RuntimeError) as e:
                    log(f"[{pid}] ошибка: {e}")
                    if attempt == MAX_RETRIES_ITEM:
                        log(f"[{pid}] пропускаю после {attempt} попыток")
                    else:
                        page.wait_for_timeout(3000)
            time.sleep(PER_ITEM_PAUSE)

        log(f"Готово. Сделано за сессию: {done}. Результаты: {OUT_DIR}")
        ctx.close()


if __name__ == "__main__":
    main()
