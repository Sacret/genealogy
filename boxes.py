#!/usr/bin/env python3
"""Координаты вырезок: где на странице стоит то, что показано в вырезке.

`crop.py` знает прямоугольник найденного слова — он по нему и режет, — но
до сих пор выбрасывал его сразу после сохранения файла. В имени
`p0044_могучев_1.png` оставалась одна страница, и журнал мог показать
вырезку, но не мог показать её место: развёрнутая рядом полная страница
была бы листом в две тысячи пикселей, на котором ищи это слово заново.

Координаты копятся в `<документ>/crops/boxes.json`, по файлу на запись:

    {"p0044_могучев_1.png": {"page": 44,
                             "box": [162, 328, 462, 430],
                             "size": [2138, 3162]}}

`box` — тот самый прямоугольник, по которому резали, вместе с полями
вокруг слова. То есть рамка в журнале обводит ровно то, что показано в
вырезке, и сверять их между собой не приходится.

Вырезки, снятые до появления этого файла, не нужно перечитывать заново:
`im.crop(box)` копирует пиксели без пересжатия, так что вырезка — точный
кусок скана, и её место ищется прямым сравнением:

    python3 boxes.py              # по всем документам
    python3 boxes.py bv0000043    # по одному

Найденных страниц это не касается: считается только то, что уже лежит в
`crops/`. Страницы, выброшенные `prune.py`, пропускаются — качать их
заново ради вырезки, которой в журнале нет, незачем; если всё же нужно,
есть `--fetch`.
"""

import argparse, json, pathlib, sys

from docstore import ROOT, allow_big_scans

NAME = "boxes.json"


def path(ident: str) -> pathlib.Path:
    return ROOT / ident / "crops" / NAME


def load(ident: str) -> dict:
    """Координаты всех вырезок документа. Нет файла — пустой словарь."""
    f = path(ident)
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save(ident: str, data: dict) -> pathlib.Path:
    """Записать координаты, выбросив записи об исчезнувших файлах.

    Повторный прогон `crop.py` по той же странице перезаписывает вырезки
    под теми же именами, но их может стать меньше — скажем, подняли
    порог. Запись о `_2`, которого больше нет, осталась бы висеть и
    когда-нибудь досталась бы чужому файлу с тем же именем.
    """
    d = path(ident)
    d.parent.mkdir(parents=True, exist_ok=True)
    live = {k: v for k, v in sorted(data.items()) if (d.parent / k).exists()}
    d.write_text(json.dumps(live, ensure_ascii=False, indent=1) + "\n",
                 encoding="utf-8")
    return d


def record(ident: str, name: str, page: int, box, size) -> None:
    """Запомнить, откуда вырезан один файл."""
    data = load(ident)
    data[name] = {"page": int(page),
                  "box": [int(v) for v in box],
                  "size": [int(v) for v in size]}
    save(ident, data)


def probes(crop, n=8):
    """Точки, по которым ищется вырезка: самые тёмные из редкой сетки.

    Сравнивать всю вырезку с каждым местом страницы — это миллиарды
    сравнений. Но достаточно нескольких точек, чтобы отсеять почти все
    места разом: если в предполагаемом начале вырезки пиксель белый, а в
    ней самой там чёрный, дальше смотреть нечего.

    Тёмные точки для этого лучше любых других: скан газеты почти весь
    белый, и чёрный пиксель — редкость, которая рубит перебор сразу.
    Берутся они из сетки по вырезке, а не подряд, чтобы не оказаться
    восемью соседями внутри одной буквы.
    """
    h, w = crop.shape
    pts = [(y, x)
           for y in range(0, h, max(1, h // 12))
           for x in range(0, w, max(1, w // 12))]
    pts.sort(key=lambda p: int(crop[p]))
    return pts[:n]


def locate(page: pathlib.Path, crop: pathlib.Path):
    """Прямоугольник вырезки на странице или None, если её там нет.

    Совпадение ищется точное, пиксель в пиксель: вырезка была сделана
    `im.crop()` из этого же файла, без масштабирования и пересжатия.
    Похожее место не годится — на странице со списком фамилий соседние
    строки различаются парой букв, и «почти совпало» поставило бы рамку
    на однофамильца.
    """
    import numpy as np
    from PIL import Image

    P = np.asarray(Image.open(page).convert("L"))
    C = np.asarray(Image.open(crop).convert("L"))
    H, W = P.shape
    h, w = C.shape
    if h > H or w > W:
        return None
    ys, xs = H - h + 1, W - w + 1

    mask = np.ones((ys, xs), bool)
    for y, x in probes(C):
        mask &= P[y:y + ys, x:x + xs] == C[y, x]
        if not mask.any():
            return None
    for y, x in np.argwhere(mask):
        if np.array_equal(P[y:y + h, x:x + w], C):
            return (int(x), int(y), int(x) + w, int(y) + h)
    return None


def crops(ident: str):
    """Файлы вырезок документа: `pNNNN_основа_N.png`, по возрастанию."""
    d = ROOT / ident / "crops"
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("p[0-9]*.png")):
        try:
            page = int(f.name[1:5])
        except ValueError:
            continue
        out.append((page, f))
    return out


def rebuild(ident: str, fetch=False, force=False) -> tuple:
    """Досчитать координаты вырезок документа. Возвращает (сколько
    посчитано, сколько пропущено)."""
    data = load(ident)
    done = skip = 0
    for page, f in crops(ident):
        if f.name in data and not force:
            continue
        img = ROOT / ident / "scans" / f"p{page:04d}.jpg"
        if not img.exists():
            if not fetch:
                skip += 1
                print(f"  {f.name}: скана нет, пропускаю")
                continue
            from fetch import ensure_page
            img = ensure_page(ident, page)
        from PIL import Image
        box = locate(img, f)
        if not box:
            skip += 1
            print(f"  {f.name}: на странице не нашлась")
            continue
        data[f.name] = {"page": page, "box": list(box),
                        "size": list(Image.open(img).size)}
        done += 1
        print(f"  {f.name}: {box}")
    if done:
        save(ident, data)
    return done, skip


def main():
    allow_big_scans()
    ap = argparse.ArgumentParser()
    ap.add_argument("idents", nargs="*", help="по умолчанию — все документы")
    ap.add_argument("--fetch", action="store_true",
                    help="дотягивать выброшенные страницы из библиотеки")
    ap.add_argument("--force", action="store_true",
                    help="пересчитать и то, что уже посчитано")
    a = ap.parse_args()

    idents = a.idents or sorted(
        p.parent.name for p in ROOT.glob("*/crops") if p.is_dir())
    done = skip = 0
    for ident in idents:
        if not crops(ident):
            continue
        print(ident)
        d, s = rebuild(ident, fetch=a.fetch, force=a.force)
        done, skip = done + d, skip + s
    print(f"\nпосчитано {done}, пропущено {skip}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
