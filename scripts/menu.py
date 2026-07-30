#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Меню каталога примерочной. Запускается ярлыком с рабочего стола (catalog.bat).

Главное упрощение: ссылки берутся ПРЯМО ИЗ БУФЕРА ОБМЕНА.
Скопировал в браузере закладкой — нажал 2 — дальше всё само.
"""

import os
import re
import sys
import json
import time
import webbrowser
import subprocess

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(HERE, "scripts")
LINKS = os.path.join(HERE, "shein_links.txt")
GRABBER = os.path.join(HERE, "tools", "link_grabber.html")

GIT_ID = ["-c", "user.name=Alex", "-c", "user.email=alexstopinie@gmail.com"]

# что считаем ссылкой на товар SHEIN
RE_ONELINK = re.compile(r"https://onelink\.shein\.com/\S+")
RE_PRODUCT = re.compile(r"https://[^\s\"'<>]*?-p-\d+\.html")

CATEGORIES = [
    "https://us.shein.com/Women-Clothing-c-2030.html",
    "https://us.shein.com/Women-Tops-c-1739.html",
    "https://us.shein.com/Women-Dresses-c-1727.html",
    "https://us.shein.com/Women-Shoes-c-1745.html",
]


def run(script, *args):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *args], cwd=HERE)


def git(*args):
    return subprocess.run(["git", *GIT_ID, *args], cwd=HERE)


def clipboard():
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                           capture_output=True, text=True, timeout=25, encoding="utf-8")
        return r.stdout or ""
    except Exception as e:
        print("не смог прочитать буфер:", e)
        return ""


def existing_links():
    if not os.path.exists(LINKS):
        return set()
    out = set()
    for line in open(LINKS, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line)
    return out


def key_of(url):
    """Ключ для дедупликации: goods_id, иначе сама ссылка."""
    m = re.search(r"-p-(\d+)\.html", url)
    return m.group(1) if m else url.rstrip("/")


def take_extracted(text):
    """Закладка «Собрать товары» кладёт в буфер JSON с готовыми данными — фетчить нечего."""
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, list) or not data:
        return None
    if not all(isinstance(x, dict) and "id" in x and "img" in x for x in data[:3]):
        return None

    sys.path.insert(0, SCRIPTS)
    import ingest_shein
    print(f"в буфере товаров: {len(data)}  (данные уже сняты со страницы)")
    added = ingest_shein.ingest_extracted(data)
    print(f"добавлено новых: {added}   (остальные уже были)")
    return added


def take_from_clipboard():
    text = clipboard()
    if not text.strip():
        print("Буфер пустой.")
        print("Сначала пункт 1 → в браузере нажми закладку «Собрать товары SHEIN».")
        return 0

    # 1) формат закладки — готовые товары
    res = take_extracted(text.strip())
    if res is not None:
        return res

    # 2) обычные ссылки (onelink из Convert Link)
    found = RE_ONELINK.findall(text) + RE_PRODUCT.findall(text)
    # чистим query-хвосты у товарных ссылок
    found = [re.match(r"(https://[^?#]*?-p-\d+\.html)", u).group(1)
             if "-p-" in u and re.match(r"(https://[^?#]*?-p-\d+\.html)", u) else u
             for u in found]
    if not found:
        print("В буфере нет ссылок на товары SHEIN.")
        print("Скопировал точно закладкой? В буфере сейчас:", text[:70].replace("\n", " "))
        return 0

    have = {key_of(u) for u in existing_links()}
    fresh, seen = [], set()
    for u in found:
        k = key_of(u)
        if k in have or k in seen:
            continue
        seen.add(k)
        fresh.append(u)

    print(f"в буфере ссылок: {len(found)}   новых: {len(fresh)}   (дубли пропущены)")
    if not fresh:
        return 0
    with open(LINKS, "a", encoding="utf-8") as f:
        f.write(f"\n# --- добавлено {time.strftime('%Y-%m-%d %H:%M')} ---\n")
        for u in fresh:
            f.write(u + "\n")
    print(f"записал в shein_links.txt: {len(fresh)}")
    return len(fresh)


def head(t):
    print("\n" + "=" * 60)
    print(" " + t)
    print("=" * 60)


def act_grab():
    head("1 · Собрать товары")
    print("Открываю инструкцию и категорию SHEIN в браузере.")
    print("Там: прокрути страницу вниз → нажми закладку «Собрать товары SHEIN».")
    print("Потом вернись сюда и нажми 2.")
    if os.path.exists(GRABBER):
        webbrowser.open("file:///" + GRABBER.replace("\\", "/"))
    time.sleep(1)
    webbrowser.open(CATEGORIES[0])


def act_update():
    head("2 · Забираю ссылки из буфера и обновляю каталог")
    take_from_clipboard()
    run("run_pipeline.py")


def act_tryon():
    head("3 · Примерка (Gemini)")
    print("Откроется браузер. Войди в Google, открой AI Studio,")
    print("выбери модель Gemini 2.5 Flash Image — потом вернись сюда и нажми Enter.")
    run("gemini_browser_runner.py")


def act_deploy():
    head("4 · Выкладываю для всех клиентов")
    run("publish_remote.py")
    git("add", "-A")
    git("commit", "-m", "catalog update")
    r = git("push")
    if r.returncode == 0:
        print("\nГотово. Через минуту каталог обновится у всех телефонов:")
        print("https://alex22356.github.io/tryon-catalog/catalog.json")
    else:
        print("\npush не прошёл — смотри сообщение выше.")


def act_all():
    act_update()
    act_tryon()
    act_deploy()


MENU = """
============================================================
             КАТАЛОГ ПРИМЕРОЧНОЙ
============================================================

  1  Собрать товары    открыть SHEIN (там жмёшь закладку)
  2  Обновить каталог  забрать из буфера и разобрать
  3  Примерка          одеть модель в новые вещи
  4  Выложить всем     опубликовать клиентам

  9  Всё сразу (2 → 3 → 4)
  0  Выход
"""


def main():
    actions = {"1": act_grab, "2": act_update, "3": act_tryon, "4": act_deploy, "9": act_all}
    while True:
        print(MENU)
        try:
            choice = input("Выбери пункт: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if choice == "0":
            return
        fn = actions.get(choice)
        if not fn:
            continue
        try:
            fn()
        except Exception as e:
            print("\nОшибка:", e)
        input("\n[Enter] — вернуться в меню ")


if __name__ == "__main__":
    main()
