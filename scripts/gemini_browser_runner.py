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

# Твой настоящий профиль Chrome. Если он уже залогинен в Google, входить не нужно —
# значит блокировка «Couldn't sign you in» не сработает.
REAL_PROFILE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")


def chrome_is_running():
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                             capture_output=True, text=True, timeout=20).stdout
        return "chrome.exe" in out
    except Exception:
        return False

AISTUDIO_URL = "https://aistudio.google.com/prompts/new_chat"

_KEEP = (
    "CRITICAL: keep the woman's body, pose, proportions, position, hands, the camera angle, "
    "the framing, the image dimensions and the plain white background PIXEL-IDENTICAL to the first image. "
    "Preserve the item's exact colors and all details. Photorealistic. "
    "Output the full body on the same white background."
)

# Промт зависит от категории: обувь надевается на ноги, а не на торс.
PROMPTS = {
    "TOP": "Dress the woman in the FIRST image with the top from the SECOND image. "
           "Change NOTHING except adding this top onto her upper body. It must sit naturally "
           "with realistic folds, draping and soft shadows, truly worn. Opaque fabric. " + _KEEP,
    "BOTTOM": "Dress the woman in the FIRST image with the trousers/skirt from the SECOND image. "
              "Change NOTHING except adding this garment onto her lower body, correctly at the waist, "
              "with realistic folds and soft shadows. " + _KEEP,
    "FULL_BODY": "Dress the woman in the FIRST image with the dress/outfit from the SECOND image. "
                 "Change NOTHING except adding this garment onto her body, with realistic drape "
                 "and soft shadows. " + _KEEP,
    "FOOTWEAR": "Put the shoes from the SECOND image onto the feet of the woman in the FIRST image. "
                "Change NOTHING except replacing her footwear: both shoes correctly on her feet, "
                "correct scale and perspective, contacting the ground with a soft contact shadow. "
                "Do not alter her legs, clothing or pose. " + _KEEP,
    "ACCESSORY": "Add the accessory from the SECOND image onto the woman in the FIRST image, "
                 "worn in its natural place (bag in hand or on shoulder, jewellery on neck/wrist, "
                 "hat on head), at correct scale with a soft shadow. Change nothing else. " + _KEEP,
}
PROMPT = PROMPTS["TOP"]   # запасной вариант, если категория неизвестна

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


def categories():
    """id → категория (из shein_products.json), чтобы выбрать правильный промт."""
    path = os.path.join(HERE, "shein_products.json")
    if not os.path.exists(path):
        return {}
    import json
    return {p["id"]: p.get("category", "TOP")
            for p in json.load(open(path, encoding="utf-8"))}


def products():
    """Товары в порядке добавления (последние — в конце)."""
    path = os.path.join(HERE, "shein_products.json")
    if not os.path.exists(path):
        return []
    import json
    return json.load(open(path, encoding="utf-8"))


def garments_queue(only_picked=True, limit=0):
    """
    Очередь на примерку.

    only_picked=True (по умолчанию) — ТОЛЬКО товары, которые ты выбрал сам
    кнопкой «Earn» (у них есть цена). Массовый сбор закладкой — не берём:
    он собирался для наполнения, а не для примерки.
    only_picked=False (--all) — все подряд.
    """
    cats = categories()
    todo = []
    for p in reversed(products()):                 # новые первыми
        pid = p["id"]
        if p.get("category") in (None, "OTHER"):   # не-одежда
            continue
        if only_picked and not p.get("price"):     # не выбирался вручную
            continue
        gpath = os.path.join(GARMENTS_DIR, pid + ".jpg")
        if not os.path.exists(gpath):
            continue
        if os.path.exists(os.path.join(OUT_DIR, pid + ".png")):
            continue                               # уже примерено
        todo.append((pid, gpath, cats.get(pid, "TOP")))
        if limit and len(todo) >= limit:
            break
    return todo


def page_has_ratelimit(page):
    try:
        body = page.inner_text("body").lower()
    except Exception:
        return False
    return any(m in body for m in RATE_MARKERS)


def run_one(page, model_img, garment_img, out_path, prompt=PROMPT):
    """Один прогон: приложить 2 фото, вставить промт, запустить, забрать картинку."""
    log("    · открываю чистый чат AI Studio")
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
    log("    · прикладываю 2 фото (модель + вещь)")
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
    log("    · вставляю промт (%d симв.)" % len(prompt))
    box.click()
    box.fill(prompt)
    page.wait_for_timeout(500)

    # 3) запустить — в AI Studio это Ctrl+Enter
    log("    · запускаю генерацию (Ctrl+Enter)")
    page.keyboard.press("Control+Enter")

    # 4) ждать сгенерённую картинку в ответе
    deadline = time.time() + RESULT_TIMEOUT
    result_img = None
    waited = 0
    while time.time() < deadline:
        if waited and waited % 15 == 0:
            log("    · жду картинку… %ds" % waited)
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
        waited += 1.5

    if result_img is None:
        raise RuntimeError("картинка не появилась за отведённое время")

    log("    · картинка получена, сохраняю")
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

    take_all = "--all" in sys.argv
    limit = 0
    for i, a in enumerate(sys.argv):
        if a == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])

    todo = garments_queue(only_picked=not take_all, limit=limit)
    names = {p["id"]: p for p in products()}

    if take_all:
        log(f"Режим: ВСЕ товары. В очереди: {len(todo)}")
    else:
        log(f"Режим: только выбранные тобой (кнопкой «Earn»). В очереди: {len(todo)}")
        log("       массовый сбор закладкой не берём — он был для наполнения")
    if not todo:
        log("Нечего примерять. Если нужны и старые — запусти с --all"); return

    log("")
    log("Что будет примерено (новые первыми):")
    for i, (pid, _, cat) in enumerate(todo[:12], 1):
        p = names.get(pid, {})
        price = f"{p.get('price')} {p.get('currency','')}" if p.get("price") else "-"
        log(f"  {i:2}. [{cat:9}] {price:11} {p.get('name','')[:44]}")
    if len(todo) > 12:
        log(f"  … и ещё {len(todo) - 12}")
    log("")

    # Какой профиль Chrome использовать
    use_real = "--real-profile" in sys.argv
    profile = REAL_PROFILE if use_real else PROFILE_DIR

    if use_real:
        if not os.path.isdir(REAL_PROFILE):
            log("НЕ найден профиль Chrome:", REAL_PROFILE); return
        if chrome_is_running():
            log("")
            log("Chrome сейчас запущен — он держит профиль, взять его нельзя.")
            log("ЗАКРОЙ Chrome полностью (все окна) и запусти этот пункт снова.")
            log("Если после закрытия всё равно ругается — проверь в Диспетчере задач,")
            log("не остались ли процессы chrome.exe в фоне.")
            return
        log(f"Работаю на твоём профиле Chrome: {REAL_PROFILE}")
        log("Вход в Google не потребуется — сессия уже есть в профиле.")

    with sync_playwright() as p:
        log("запускаю Chrome…")
        ctx = p.chromium.launch_persistent_context(
            profile, channel="chrome", headless=False,
            args=["--start-maximized"], no_viewport=True,
        )
        log("Chrome запущен, вкладок: %d" % len(ctx.pages))
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        log("открываю AI Studio: %s" % AISTUDIO_URL)
        try:
            page.goto(AISTUDIO_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log("не смог открыть страницу: %s" % str(e)[:90])
        log("страница: %s" % page.url[:70])
        try:
            log("заголовок: %s" % page.title()[:60])
        except Exception:
            pass
        if use_real:
            input("\n>>> Проверь: AI Studio открылся уже под твоим аккаунтом? "
                  "Выбери модель 'Gemini 2.5 Flash Image' и нажми Enter здесь...\n")
        else:
            input("\n>>> Войди в Google, открой AI Studio, выбери модель "
                  "'Gemini 2.5 Flash Image'. Потом нажми Enter здесь...\n")

        done = 0
        for pid, gpath, cat in todo:
            out = os.path.join(OUT_DIR, pid + ".png")
            for attempt in range(1, MAX_RETRIES_ITEM + 1):
                try:
                    log(f"[{pid}] [{cat}] попытка {attempt}…")
                    run_one(page, MODEL_IMAGE, gpath, out, PROMPTS.get(cat, PROMPT))
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
