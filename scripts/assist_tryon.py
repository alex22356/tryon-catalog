#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Помощник примерки: генерацию делаешь ты, всю рутину делаю я.

Почему так, а не полный автомат: Google намеренно блокирует автоматизацию
залогиненного аккаунта (и в AI Studio, и в gemini.google.com), а Chrome 136+
запрещает подключение по отладочному порту к основному профилю. Ломать эти
защиты мы не будем. Полный автомат возможен только через API (нужен биллинг).

Что делает помощник для КАЖДОЙ вещи:
  1) кладёт рядом два готовых файла: 1_model.jpg и 2_<вещь>.jpg
  2) копирует нужный промт (под категорию) в буфер обмена
  3) открывает Gemini и папку с файлами
  4) ЖДЁТ, пока ты сохранишь результат — и сам кладёт его в tryon_out/<id>.png

Тебе на вещь: перетащить 2 файла → Ctrl+V (промт) → скачать картинку.

Запуск:
    python scripts/assist_tryon.py
    python scripts/assist_tryon.py --limit 3
"""

import io
import os
import sys
import json
import time
import shutil
import argparse
import subprocess
import webbrowser

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gemini_browser_runner import PROMPTS, PROMPT, garments_queue, products  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.abspath(os.path.join(HERE, "..", "app", "src", "main", "res", "drawable-nodpi"))
MODEL_IMAGE = os.path.join(APP, "premium_model.jpg")
OUT_DIR = os.path.join(HERE, "tryon_out")
STAGE = os.path.join(HERE, "_to_gemini")          # сюда кладём пару файлов
DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")
GEMINI_URL = "https://gemini.google.com/app"

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")


def log(*a):
    print(*a, flush=True)


def to_clipboard(text):
    try:
        p = subprocess.Popen(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ($input | Out-String)"],
                             stdin=subprocess.PIPE, text=True, encoding="utf-8")
        p.communicate(text)
        return p.returncode == 0
    except Exception as e:
        log("не смог положить промт в буфер:", str(e)[:60])
        return False


def newest_download(after_ts):
    """Самый свежий файл-картинка в Downloads, появившийся после отметки времени."""
    best, best_t = None, after_ts
    try:
        for f in os.listdir(DOWNLOADS):
            if not f.lower().endswith(IMG_EXT):
                continue
            p = os.path.join(DOWNLOADS, f)
            try:
                t = os.path.getmtime(p)
            except OSError:
                continue
            if t > best_t:
                best, best_t = p, t
    except FileNotFoundError:
        pass
    return best


def wait_for_result(after_ts, pid, timeout=900):
    """Ждём, пока ты сохранишь картинку. Как появится — забираем."""
    log("    жду, когда ты сохранишь картинку (Ctrl+C — пропустить эту вещь)")
    t0 = time.time()
    last_tick = 0
    while time.time() - t0 < timeout:
        f = newest_download(after_ts)
        if f:
            size1 = os.path.getsize(f)
            time.sleep(1.5)                       # дать докачаться
            if os.path.getsize(f) != size1:
                continue
            dst = os.path.join(OUT_DIR, pid + ".png")
            os.makedirs(OUT_DIR, exist_ok=True)
            shutil.move(f, dst)
            log(f"    ✓ забрал: {os.path.basename(f)} -> tryon_out/{pid}.png")
            return True
        el = int(time.time() - t0)
        if el and el % 20 == 0 and el != last_tick:
            last_tick = el
            log(f"    … жду {el} сек")
        time.sleep(1)
    log("    ! не дождался, пропускаю")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--all", action="store_true", help="включая собранные закладкой")
    args = ap.parse_args()

    if not os.path.exists(MODEL_IMAGE):
        log("нет базовой модели:", MODEL_IMAGE)
        return
    todo = garments_queue(only_picked=not args.all, limit=args.limit)
    if not todo:
        log("Нечего примерять — всё уже готово.")
        return

    names = {p["id"]: p for p in products()}
    os.makedirs(STAGE, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    log("=" * 62)
    log(f" Примерка с помощником: {len(todo)} вещей")
    log("=" * 62)
    log("На каждую вещь: перетащи 2 файла в Gemini → Ctrl+V → скачай результат.")
    log("Файлы и промт я подготовлю сам, скачанное заберу сам.\n")

    webbrowser.open(GEMINI_URL)
    time.sleep(2)

    done = 0
    for n, (pid, gpath, cat) in enumerate(todo, 1):
        item = names.get(pid, {})
        log("─" * 62)
        log(f"[{n}/{len(todo)}]  {cat}   {item.get('name','')[:46]}")

        # 1) готовим пару файлов
        for f in os.listdir(STAGE):
            try:
                os.remove(os.path.join(STAGE, f))
            except OSError:
                pass
        shutil.copy(MODEL_IMAGE, os.path.join(STAGE, "1_model.jpg"))
        shutil.copy(gpath, os.path.join(STAGE, "2_garment.jpg"))
        log(f"    файлы готовы: {STAGE}")

        # 2) промт под категорию — в буфер
        prompt = PROMPTS.get(cat, PROMPT)
        if to_clipboard(prompt):
            log(f"    промт [{cat}] скопирован — жми Ctrl+V в Gemini")

        # 3) открываем папку, чтобы удобно перетащить
        try:
            subprocess.Popen(["explorer", STAGE])
        except Exception:
            pass

        # 4) ждём результат
        mark = time.time()
        try:
            if wait_for_result(mark, pid):
                done += 1
        except KeyboardInterrupt:
            log("\n    пропускаю по Ctrl+C")
            continue

    log("─" * 62)
    log(f"ИТОГ: готово {done} из {len(todo)}")
    if done:
        log("Дальше: пункт 2 (в приложение) или пункт 4 (выложить клиентам)")


if __name__ == "__main__":
    main()
