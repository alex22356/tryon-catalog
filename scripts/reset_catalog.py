#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Полный сброс собранного каталога — начать с нуля.

Что стирает:
  shein_products.json   все собранные товары
  garments/             скачанные фото вещей
  earn_data.json        цены/популярность из кнопки «Earn»
  shein_links.txt       список ссылок (остаётся только заголовок)
  tryon_out/            результаты примерки
  products/             картинки, подготовленные к публикации
  app assets/products/  картинки, встроенные в приложение
  каталог приложения    товары с примеркой (демо-позиции остаются)

Что НЕ трогает: скрипты, настройки (publish_config, feeds), демо-товары curated.json.

Запуск:
    python scripts/reset_catalog.py --yes
"""

import io
import os
import sys
import json
import glob
import argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_ASSETS = os.path.abspath(os.path.join(HERE, "..", "app", "src", "main", "assets"))

LINKS_HEADER = """# Ссылки SHEIN. Заполняется автоматически:
#   пункт 1 меню — кабинет SHEIN, ловит кнопку «Earn» (с ценами)
#   пункт 5 меню — закладка, собирает пачкой с категории
# Формат: US|https://...   или   EU|https://...
"""


def rm_glob(pattern):
    n = 0
    for f in glob.glob(pattern):
        try:
            os.remove(f)
            n += 1
        except Exception as e:
            print("  ! не удалил %s: %s" % (os.path.basename(f), str(e)[:40]))
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="подтвердить сброс")
    args = ap.parse_args()

    if not args.yes:
        print("Это СТИРАЕТ все собранные товары, фото и примерки.")
        print("Запусти с --yes, если уверен:  python scripts/reset_catalog.py --yes")
        return

    print("Сбрасываю каталог…\n")

    # товары
    p = os.path.join(HERE, "shein_products.json")
    was = 0
    if os.path.exists(p):
        try:
            was = len(json.load(io.open(p, encoding="utf-8")))
        except Exception:
            pass
    json.dump([], io.open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("  товаров стёрто:            %d" % was)

    # фото вещей
    print("  фото вещей удалено:        %d" % rm_glob(os.path.join(HERE, "garments", "*")))

    # примерки
    print("  результатов примерки:      %d" % rm_glob(os.path.join(HERE, "tryon_out", "*")))

    # подготовленные к публикации
    print("  картинок в products/:      %d" % rm_glob(os.path.join(HERE, "products", "*")))

    # встроенные в приложение
    print("  картинок в приложении:     %d" % rm_glob(os.path.join(APP_ASSETS, "products", "*")))

    # цены из Earn
    json.dump({}, io.open(os.path.join(HERE, "earn_data.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("  данные «Earn»:             очищены")

    # ссылки
    io.open(os.path.join(HERE, "shein_links.txt"), "w", encoding="utf-8").write(LINKS_HEADER)
    print("  список ссылок:             очищен")

    # каталоги: убрать товары с примеркой, демо оставить
    for path in (os.path.join(APP_ASSETS, "catalog.json"), os.path.join(HERE, "curated.json")):
        if not os.path.exists(path):
            continue
        d = json.load(io.open(path, encoding="utf-8"))
        before = len(d.get("items", []))
        d["items"] = [i for i in d.get("items", [])
                      if not i.get("preCut") and not str(i.get("id", "")).startswith("shein_")]
        json.dump(d, io.open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("  %-26s %d -> %d" % (os.path.basename(path) + ":", before, len(d["items"])))

    print("\nГотово. Каталог пуст, можно начинать с шага 1.")


if __name__ == "__main__":
    main()
