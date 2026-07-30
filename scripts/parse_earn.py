#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Разбор того, что SHEIN кладёт в буфер по кнопке «Earn» в affiliate-кабинете.

Пример содержимого буфера:
    🔥Snag these must-haves on SHEIN before they're gone!
    💰Price[$6.59] -75%
    🛒Pastel Blue Breathable Summer Knit Cardigan ... 100+ sold
    🎁60% OFF COUPON for every New User!
    https://onelink.shein.com/45/5xbr4ogmr9u0

Отсюда достаём то, чего не было нигде больше:
    name      название товара
    price     6.59
    currency  USD / EUR (по символу)
    discount  75
    sold      100   ← сигнал популярности
    url       партнёрская onelink

Регулярками, а не ИИ: формат жёсткий, и цену угадывать нельзя.
Локальный ИИ потом добавляет пол/цвет/стиль (scripts/enrich_attrs.py).
"""

import re

CUR = {"$": "USD", "€": "EUR", "£": "GBP"}

RE_URL = re.compile(r"https://onelink\.shein\.com/\S+")
RE_PRICE = re.compile(r"Price\s*\[\s*([$€£])\s*([\d.,]+)\s*\]", re.I)
RE_DISC = re.compile(r"-\s*(\d{1,2})\s*%")
RE_SOLD = re.compile(r"([\d.,]+)\s*([km]?)\+?\s*sold", re.I)
# строка с названием: после 🛒, либо самая длинная «товарная» строка
RE_CART_LINE = re.compile(r"[🛒🛍]\s*(.+)")

JUNK = ("snag these", "must-haves", "coupon", "off coupon", "new user",
        "price[", "shein before", "free shipping")


def parse_earn(text: str):
    """Возвращает dict или None, если это не «Earn»-payload."""
    m = RE_URL.search(text or "")
    if not m:
        return None
    url = m.group(0).split("?")[0]

    out = {"url": url}

    p = RE_PRICE.search(text)
    if p:
        out["currency"] = CUR.get(p.group(1), "USD")
        try:
            out["price"] = round(float(p.group(2).replace(",", ".")), 2)
        except ValueError:
            pass

    d = RE_DISC.search(text)
    if d:
        out["discount"] = int(d.group(1))

    s = RE_SOLD.search(text)
    if s:
        try:
            n = float(s.group(1).replace(",", ""))
            mult = {"k": 1000, "m": 1000000}.get(s.group(2).lower(), 1)
            out["sold"] = int(n * mult)
        except ValueError:
            pass

    # название
    name = ""
    cart = RE_CART_LINE.search(text)
    if cart:
        name = cart.group(1)
    else:
        for line in text.splitlines():
            line = line.strip()
            low = line.lower()
            if len(line) > 25 and not line.startswith("http") \
                    and not any(j in low for j in JUNK):
                if len(line) > len(name):
                    name = line
    # хвосты вида "100+ sold"
    name = RE_SOLD.sub("", name)
    name = re.sub(r"^[^\w]+", "", name).strip(" ,.-")
    if name:
        out["name"] = name[:90]

    return out


if __name__ == "__main__":
    sample = ("🔥Snag these must-haves on SHEIN before they're gone!\n"
              "💰Price[$6.59] -75%\n"
              "🛒Pastel Blue Breathable Summer Knit Cardigan Lightweight Long Sleeve "
              "Crewneck Sweater Thin Sunscreen Cover Up For Daily Wear 100+ sold\n"
              "🎁60% OFF COUPON for every New User!\n"
              "https://onelink.shein.com/45/5xbr4ogmr9u0")
    import json
    print(json.dumps(parse_earn(sample), ensure_ascii=False, indent=2))
