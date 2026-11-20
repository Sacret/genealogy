#!/usr/bin/env python3
"""Кандидаты `find.py --loose` одним списком, объединённые по нескольким запускам.

    tools/loose.py pn0030948 pn0030947
    tools/loose.py pn0030948 --seeds 12

`find.py --loose` от запуска к запуску подписывает одни и те же группы
кандидатов разными словами: порядок множеств зависит от хэша строк, а
`--json` с `--loose` пуст. Полный список для заметок — объединение выводов
при разных `PYTHONHASHSEED`. Строки печатаются с числом запусков, в которых
встретились. Все поиски — с `--no-log`, журнал не трогается.
"""

import argparse
import collections
import os
import subprocess
import sys

import _root

SURNAMES = ("Кармазинъ", "Могучевъ")


def union(ident, surname, seeds):
    seen = collections.Counter()
    for seed in range(1, seeds + 1):
        env = dict(os.environ, PYTHONHASHSEED=str(seed))
        out = subprocess.run(
            [sys.executable, str(_root.ROOT / "find.py"), ident, surname,
             "--loose", "--no-log"],
            env=env, capture_output=True, text=True).stdout
        seen.update({line.rstrip() for line in out.splitlines() if line.strip()})
    return seen


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("idents", nargs="+")
    ap.add_argument("--seeds", type=int, default=8, help="сколько запусков")
    ap.add_argument("--surname", action="append", help="вместо обеих фамилий")
    a = ap.parse_args()
    for ident in a.idents:
        for surname in a.surname or SURNAMES:
            print(f"##### {ident} {surname}")
            for line, n in union(ident, surname, a.seeds).most_common():
                print(f"{n:3} {line}")


if __name__ == "__main__":
    main()
