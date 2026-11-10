#!/usr/bin/env python3
"""Слова на «Кар», «Мог», «Кочет», «Ровен», «Миус» по всем слоям чтения тома.

    tools/words.py pn0030634 pn0030633
    tools/words.py pn0030634 --also 'пригульн\\w*'

Печатает слово и полосы, где оно встретилось в любом слое, включая
`ocr_stripes/`, которого `find.py` не читает. Это выписка для разбора
глазами, а не результат: каждое слово сверяется по скану. Латинские
двойники букв (K, a, p, M, o) учтены — OCR подставляет их часто.
"""

import argparse
import collections
import re

import _root  # noqa: F401
import docstore

LAYERS = ("ocr", "ocr_bands", "ocr_cols", "ocr_prep", "ocr_stripes")
PATTERN = (r"[КK][аa][рp]\w*|[МM][оo][гr]\w*|кочет\w*|ровен\w*|миус\w*")
# Обычные слова, которыми выписка иначе забита на каждой полосе.
SKIP = {"могли", "может", "могут", "могло", "могла", "мог", "могущих",
        "могущие", "могущий", "могилы", "могила", "карта", "карты", "картины"}


def words(ident, pattern):
    hits = collections.defaultdict(set)
    for layer in LAYERS:
        for f in sorted((docstore.doc_dir(ident) / layer).glob("p*.txt")):
            page = int(re.search(r"\d+", f.stem).group())
            text = f.read_text(encoding="utf-8", errors="replace")
            for word in pattern.findall(text):
                hits[word.lower()].add(page)
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("idents", nargs="+")
    ap.add_argument("--also", action="append", default=[], metavar="REGEX",
                    help="ещё одно выражение для слова, можно несколько")
    a = ap.parse_args()
    pattern = re.compile(r"\b(?:%s)" % "|".join([PATTERN, *a.also]), re.I)
    for ident in a.idents:
        print("==", ident)
        hits = words(ident, pattern)
        for word in sorted(hits):
            if word not in SKIP:
                print(f"  {word}: {sorted(hits[word])}")


if __name__ == "__main__":
    main()
