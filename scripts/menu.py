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

# affiliate-кабинет: ходишь по каталогу и жмёшь «Earn» — ссылка с ценой падает в буфер
AFFILIATE = {
    "US": "https://m.shein.com/us/affiliate/?cdn_rsite=cf&ref=m&rep=dir&ret=mus",
    "EU": "https://m.shein.com/eur/affiliate/?cdn_rsite=cf&ref=m&rep=dir",
}


def run(script, *args):
    """
    Запуск шага. Ctrl+C останавливает ТОЛЬКО шаг и возвращает в меню:
    в Windows-консоли Ctrl+C летит всей группе процессов, поэтому здесь его глотаем.
    """
    try:
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *args], cwd=HERE)
    except KeyboardInterrupt:
        print("\n(шаг остановлен — возвращаюсь в меню)")
        return None


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


def act_earn():
    """Основной способ: affiliate-кабинет + ловец буфера в одном действии."""
    head("1 · Собираю товары из affiliate-кабинета")
    print("Регион:  1 — US    2 — EU")
    region = {"1": "US", "2": "EU"}.get(input("Регион [1/2, Enter=US]: ").strip(), "US")
    print(f"\nОткрываю кабинет SHEIN ({region}).")
    print("Ходи по каталогу и жми «Earn» на нужных товарах —")
    print("ссылку, название, ЦЕНУ и число продаж я подхвачу сам.")
    print("Закончил — вернись сюда и нажми Ctrl+C.\n")
    webbrowser.open(AFFILIATE[region])
    time.sleep(2)
    run("catch_links.py", "--region", region)


def act_grab():
    head("Собрать пачкой закладкой (без цен)")
    print("Открываю инструкцию и категорию SHEIN.")
    print("Прокрути страницу → нажми закладку «Собрать товары SHEIN» → потом пункт 2.")
    print("Так быстрее (десятки товаров за раз), но БЕЗ цен и без партнёрских ссылок.")
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
    print("Полностью автоматически из браузера — нельзя: Google намеренно")
    print("блокирует автоматизацию залогиненного аккаунта, а Chrome 136+")
    print("запрещает отладочный порт на основном профиле. Ломать это не будем.")
    print()
    print("  1 — ЧЕРЕЗ API      полный автомат, ~4 цента за вещь (нужен биллинг)")
    print("  2 — С ПОМОЩНИКОМ   бесплатно: я готовлю файлы и промт,")
    print("                     ты делаешь 3 действия, результат забираю сам")
    print()
    print("  8 — старые попытки через браузер (не работают, оставлено для истории)")
    choice = input("Как запускать? [1/2, Enter=2]: ").strip()

    if choice == "1":
        print("\nСколько вещей примерить? (Enter = все из очереди)")
        n = input("Количество: ").strip()
        if n.isdigit():
            run("gemini_api_runner.py", "--limit", n)
        else:
            run("gemini_api_runner.py")
        return

    if choice in ("2", ""):
        print("\nСколько вещей сделать за раз? (Enter = все из очереди)")
        n = input("Количество: ").strip()
        if n.isdigit():
            run("assist_tryon.py", "--limit", n)
        else:
            run("assist_tryon.py")
        return

    print("\n  2 — Chrome на моём профиле  (виснет)")
    print("  3 — отдельный профиль       (заблокируют вход)")
    print("  4 — порт 9222               (Chrome 136+ запрещает)")
    choice = input("Что пробуем? [2/3/4]: ").strip()

    if choice == "2":
        print("\nЗАКРОЙ Chrome полностью, потом нажми Enter.")
        input("[Enter] когда закрыл ")
        run("gemini_browser_runner.py", "--real-profile")
    elif choice == "3":
        run("gemini_browser_runner.py")
    elif choice == "4":
        bat = os.path.join(HERE, "tools", "chrome_debug.bat")
        print("\nОткрою запускатель Chrome с портом 9222.")
        print("Учти: Chrome 136+ игнорирует порт на основном профиле — скорее всего не выйдет.")
        input("\n[Enter] чтобы запустить Chrome ")
        if os.path.exists(bat):
            subprocess.Popen(["cmd", "/c", "start", "", bat], shell=False)
        else:
            print("не нашёл", bat)
        input("\n[Enter] когда Chrome открылся и модель выбрана ")
        run("gemini_browser_runner.py", "--attach")
    else:
        print("не понял выбор — вернись в меню и выбери 1")


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


def act_catch():
    head("5 · Ловлю ссылки из буфера")
    print("Копируй ссылки в браузере (Convert Link → Copy, share, или адресную строку).")
    print("Каждую новую сразу запишу. Закончил — Ctrl+C.")
    run("catch_links.py")


def act_enrich():
    head("6 · Разметка атрибутов локальным ИИ")
    print("Определю пол, цвет, сезон, повод, стиль — для фильтров и ИИ-стилиста.")
    run("enrich_attrs.py")


def act_all():
    act_update()
    act_enrich()
    act_tryon()
    act_deploy()


MENU = """
============================================================
             КАТАЛОГ ПРИМЕРОЧНОЙ
============================================================

  1  СОБИРАТЬ ТОВАРЫ   кабинет SHEIN + ловлю «Earn» (с ценами)
  2  Обновить каталог  разобрать пойманное
  3  Примерка          одеть модель в новые вещи
  4  Выложить всем     опубликовать клиентам

  5  Собрать пачкой    закладкой с категории (быстро, без цен)
  6  Разметить ИИ      пол, цвет, стиль, сезон (локальный ИИ)

  9  Всё сразу (2 → 6 → 3 → 4)
  0  Выход
"""


def main():
    actions = {"1": act_earn, "2": act_update, "3": act_tryon, "4": act_deploy,
               "5": act_grab, "6": act_enrich, "9": act_all}
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
        except KeyboardInterrupt:
            # Ctrl+C = «хватит, назад в меню», а не выход из программы
            print("\n(остановлено)")
        except Exception as e:
            print("\nОшибка:", e)
        try:
            input("\n[Enter] — вернуться в меню ")
        except (EOFError, KeyboardInterrupt):
            pass


if __name__ == "__main__":
    main()
