"""
Приёмка примерок из архивов Kaggle: проверка, сжатие, подключение к каталогу.

Что делает:
  1. распаковывает tryon_p*_*.zip;
  2. проверяет каждую картинку и отбраковывает негодные;
  3. пережимает в WebP — 105 МБ вместо 247 при неразличимой глазом разнице
     (важно: у GitHub Pages лимит сайта 1 ГБ, а картинки остаются в истории
     репозитория навсегда);
  4. проставляет overlayUrl и preCut в dv8_products.json.

    python scripts/ingest_tryons.py --zips ~/Downloads      # показать
    python scripts/ingest_tryons.py --zips ~/Downloads --apply
"""
import argparse
import collections
import glob
import io
import json
import os
import shutil
import zipfile

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRODUCTS = os.path.join(ROOT, "products")
SOURCE = os.path.join(ROOT, "dv8_products.json")
CONFIG = os.path.join(ROOT, "publish_config.json")

TARGET = (896, 1200)   # размер манекена: оверлей кладётся на него один в один
QUALITY = 88


def load_base_url():
    """Куда приложение ходит за картинками — берём из настроек публикации."""
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
        url = cfg.get("productsBaseUrl") or cfg.get("baseUrl")
        if url:
            return url.rstrip("/")
    return "https://alex22356.github.io/tryon-catalog/products"


def check(img):
    """
    Отбраковка. Возвращает причину или None, если картинка годная.

    Проверяем то, что реально ломалось на прогонах: пустой холст (модель
    вернула фон), почти однотонное изображение, неверная пропорция.
    """
    if img.size[0] < 400 or img.size[1] < 500:
        return f"мелкая {img.size}"

    ratio = img.size[0] / img.size[1]
    if abs(ratio - TARGET[0] / TARGET[1]) > 0.05:
        return f"пропорция {ratio:.2f}"

    a = np.asarray(img.convert("L").resize((64, 86)), dtype=np.float32)
    if a.std() < 12:
        return f"почти однотонная (разброс {a.std():.1f})"

    # Центральная треть — там должна быть вещь. Если она не отличается от
    # краёв, значит манекен вышел пустым.
    centre = a[20:66, 16:48]
    if abs(float(centre.mean()) - float(a.mean())) < 2 and centre.std() < 15:
        return "в центре пусто"

    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zips", default=os.path.expanduser("~/Downloads"),
                    help="папка с архивами tryon_p*_*.zip")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    zips = sorted(glob.glob(os.path.join(a.zips, "tryon_p*_*.zip")))
    if not zips:
        print(f"архивов tryon_p*_*.zip в {a.zips} не найдено")
        return
    print(f"архивов: {len(zips)}")

    stats = collections.Counter()
    rejects = []
    saved = {}
    total_bytes = 0

    if a.apply:
        os.makedirs(PRODUCTS, exist_ok=True)

    for z in zips:
        with zipfile.ZipFile(z) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".png")]
            print(f"  {os.path.basename(z)}: {len(names)} картинок")
            for n in names:
                pid = os.path.splitext(os.path.basename(n))[0]
                try:
                    img = Image.open(io.BytesIO(zf.read(n))).convert("RGB")
                except Exception as e:
                    stats["не читается"] += 1
                    rejects.append((pid, type(e).__name__))
                    continue

                why = check(img)
                if why:
                    stats["брак"] += 1
                    rejects.append((pid, why))
                    continue

                if img.size != TARGET:
                    img = img.resize(TARGET, Image.LANCZOS)

                buf = io.BytesIO()
                img.save(buf, "WEBP", quality=QUALITY, method=4)
                total_bytes += buf.tell()
                stats["годных"] += 1
                saved[pid] = buf.getvalue()

    print(f"\n--- итог ---")
    for k, v in stats.most_common():
        print(f"  {k}: {v}")
    print(f"  объём после сжатия: {total_bytes/1024/1024:.0f} МБ")

    if rejects:
        print(f"\n  забраковано ({len(rejects)}), первые 10:")
        for pid, why in rejects[:10]:
            print(f"    {pid}: {why}")

    if not a.apply:
        print("\n(показ без записи — добавь --apply)")
        return

    for pid, data in saved.items():
        with open(os.path.join(PRODUCTS, f"overlay_{pid}.webp"), "wb") as f:
            f.write(data)
    print(f"\nзаписано картинок: {len(saved)} → {PRODUCTS}")

    base_url = load_base_url()
    with open(SOURCE, encoding="utf-8") as f:
        doc = json.load(f)
    items = doc if isinstance(doc, list) else (doc.get("items") or doc.get("products"))

    n = 0
    for it in items:
        if it.get("id") in saved:
            it["overlayUrl"] = f"{base_url}/overlay_{it['id']}.webp"
            it["preCut"] = True
            n += 1
    with open(SOURCE, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    print(f"подключено к каталогу: {n} позиций")
    print("\nдальше: python scripts/build_catalog.py, затем git add/commit/push")


if __name__ == "__main__":
    main()
