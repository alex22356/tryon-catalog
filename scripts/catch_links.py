#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ловец ссылок: работает в фоне и подхватывает КАЖДУЮ скопированную ссылку SHEIN.

Как пользоваться:
  1) запустил → выбрал регион (US или EU);
  2) ходишь по SHEIN / по своему affiliate-кабинету и копируешь ссылки
     (Convert Link → Copy, или share-кнопка) — по одной, как удобно;
  3) каждая новая ссылка сразу попадает в shein_links.txt с пометкой региона;
  4) закончил — Ctrl+C. Дальше пункт «Обновить каталог».

Ничего не запрашивает у SHEIN — только читает буфер обмена.
Формат строки в файле:  US|https://onelink.shein.com/45/xxxx
"""

import io
import os
import re
import sys
import time
import argparse
import subprocess

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINKS = os.path.join(HERE, "shein_links.txt")

RE_LINK = re.compile(r"https://(?:onelink\.shein\.com/\S+|[a-z0-9.]*shein\.com/[^\s\"'<>]*?-p-\d+\.html)")
POLL = 0.8          # как часто смотреть в буфер, сек

REGIONS = {"1": "US", "2": "EU"}


def clipboard():
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                           capture_output=True, text=True, timeout=20, encoding="utf-8")
        return r.stdout or ""
    except Exception:
        return ""


def key_of(url):
    m = re.search(r"-p-(\d+)\.html", url)
    return m.group(1) if m else url.split("?")[0].rstrip("/")


def known_keys():
    keys = set()
    if not os.path.exists(LINKS):
        return keys
    for line in io.open(LINKS, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        url = line.split("|", 1)[-1].strip()
        keys.add(key_of(url))
    return keys


def clean(url):
    """Отрезаем query-мусор у товарных ссылок, onelink оставляем как есть."""
    if "onelink.shein.com" in url:
        return url
    m = re.match(r"(https://[^?#]*?-p-\d+\.html)", url)
    return m.group(1) if m else url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", choices=["US", "EU"], help="регион без вопроса")
    args = ap.parse_args()

    region = args.region
    if not region:
        print("Для какого региона собираем?")
        print("  1 — US   (m.shein.com/us/…)")
        print("  2 — EU   (m.shein.com/eur или локальный домен)")
        region = REGIONS.get(input("Регион [1/2]: ").strip(), "US")

    keys = known_keys()
    print(f"\nЛовлю ссылки для региона {region}. Уже в файле: {len(keys)} товаров.")
    print("Копируй ссылки в браузере — я подхвачу каждую сам.")
    print("Закончил — нажми Ctrl+C.\n")

    last = ""
    caught = 0
    try:
        while True:
            text = clipboard()
            if text and text != last:
                last = text
                for raw in RE_LINK.findall(text):
                    url = clean(raw)
                    k = key_of(url)
                    if k in keys:
                        print(f"  · уже есть: {url[:62]}")
                        continue
                    keys.add(k)
                    with io.open(LINKS, "a", encoding="utf-8") as f:
                        f.write(f"{region}|{url}\n")
                    caught += 1
                    aff = "партнёрская" if "onelink" in url else "обычная"
                    print(f"  ✓ [{caught}] {aff}: {url[:58]}")
            time.sleep(POLL)
    except KeyboardInterrupt:
        print(f"\nПоймано новых ссылок: {caught}   (регион {region})")
        if caught:
            print("Дальше: пункт «Обновить каталог»")


if __name__ == "__main__":
    main()
