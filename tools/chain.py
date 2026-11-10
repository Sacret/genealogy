#!/usr/bin/env python3
"""Полная цепочка чтения газетного выпуска: все слои по всем полосам.

    tools/chain.py pn0030634 pn0030633
    tools/chain.py --next 6                 # шесть следующих из очереди газет

`run.py` штатно перечитывает только полосы ниже порога 60, а газете нужны
все (см. «Газеты» в README): после него идут `rescue.py` по всем полосам,
то же с `--cols` и сплошной проход `stripes.py`. Адрес берётся по самому
идентификатору — семь цифр ДГПБ, девять РНБ. Упавший шаг скачивания
повторяется: сервер библиотеки временами отдаёт 404 и таймауты.

Выпуски идут по очереди; для двух потоков запустить команду дважды с
разными идентификаторами.
"""

import argparse
import json
import subprocess
import sys
import time

import _root
import catalog
import docstore

SURNAMES = ("Кармазинъ", "Могучевъ")


def call(script, *args) -> int:
    return subprocess.run([sys.executable, str(_root.ROOT / script), *args]).returncode


def next_newspapers(n):
    documents = json.loads(catalog.DOCUMENTS.read_text(encoding="utf-8"))
    out = []
    for name, items in documents.get("очередь", {}).items():
        if "газет" in name and isinstance(items, list):
            out += [rec["id"] for rec in items]
    return out[:n]


def process(ident, a) -> bool:
    url = f"{docstore.library_of(ident)}/{ident}"
    for attempt in range(1, a.retries + 1):
        if call("run.py", url, "--dpi", str(a.dpi), "--find", *SURNAMES) == 0:
            break
        print(f"!! {ident}: run.py упал, попытка {attempt} из {a.retries}",
              flush=True)
        if attempt == a.retries:
            return False
        time.sleep(a.pause)
    pages = docstore.load_meta(ident).get("pages")
    if not pages:
        print(f"!! {ident}: в meta.json нет числа страниц", flush=True)
        return False
    span = f"1-{pages}"
    ok = (call("rescue.py", ident, "--pages", span) == 0
          and call("rescue.py", ident, "--pages", span, "--cols") == 0
          and call("stripes.py", ident, "--jobs", str(a.jobs)) == 0)
    print(f"== {ident}: {'готов' if ok else 'НЕ ДОЧИТАН'}, полос {pages}",
          flush=True)
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("idents", nargs="*", help="идентификаторы выпусков")
    ap.add_argument("--next", type=int, dest="next_n", metavar="N",
                    help="вместо идентификаторов: N следующих из очереди газет")
    ap.add_argument("--dpi", type=int, default=400,
                    help="у части номеров 400 dpi даёт HTTP 500 — тогда 300")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--pause", type=int, default=60, help="секунд между попытками")
    ap.add_argument("--jobs", type=int, default=4, help="потоков у stripes.py")
    a = ap.parse_args()
    if bool(a.idents) == bool(a.next_n):
        ap.error("нужны либо идентификаторы, либо --next N")
    idents = a.idents or next_newspapers(a.next_n)
    bad = [i for i in idents if not docstore.DOC_ID.fullmatch(i)]
    if bad:
        ap.error("не идентификатор документа: " + ", ".join(bad))
    failed = [i for i in idents if not process(i, a)]
    if failed:
        sys.exit("не дочитаны: " + ", ".join(failed))


if __name__ == "__main__":
    main()
