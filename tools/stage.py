#!/usr/bin/env python3
"""Подготовить коммит после пачки: пересобрать производное и собрать индекс.

    tools/stage.py pn0030634 pn0030633
    tools/stage.py pn0030634 --also places.json requests.md

Пересобирает `documents.json` и журнал, обновляет список находок в
`.gitignore` и кладёт в индекс git ровно то, что относится к названным
томам: их папки (сканы и слои, закрытые `.gitignore`, git не возьмёт сам),
`documents.json`, `catalog.jsonl`, `journal.html`, `journal/`, `.gitignore`.
Общие реестры (`places.json`, `requests.md`, `persons.json`, `events.jsonl`,
`namesakes.json`) добавляются только по `--also`: в них может лежать чужая
незаконченная правка.

Сам коммит скрипт не делает — он за человеком или по его просьбе:
заголовок по-русски, в повелительном наклонении («Проверить Донские
областные ведомости № 238 и 239 за 1908 год»).
"""

import argparse
import subprocess
import sys

import _root
import catalog
import docstore
import journal
import prune

ALWAYS = ("documents.json", "catalog.jsonl", "journal.html", "journal",
          ".gitignore")
REGISTRIES = ("places.json", "requests.md", "persons.json", "events.jsonl",
              "namesakes.json")
SURNAMES = ("Кармазинъ", "Могучевъ")


def git(*args):
    return subprocess.run(["git", *args], cwd=_root.ROOT, text=True,
                          capture_output=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("idents", nargs="+")
    ap.add_argument("--also", nargs="+", default=[], choices=REGISTRIES,
                    metavar="ФАЙЛ", help="общие реестры, изменённые этой пачкой: "
                                         + ", ".join(REGISTRIES))
    a = ap.parse_args()

    problems = []
    for ident in a.idents:
        if not (docstore.doc_dir(ident) / "meta.json").exists():
            problems.append(f"{ident}: нет такого тома")
            continue
        got = docstore.latest_verdicts(ident)
        problems += [f"{ident}: нет вердикта на «{s}»"
                     for s in SURNAMES if s not in got]
    if problems:
        sys.exit("коммит готовить рано:\n  " + "\n  ".join(problems))

    catalog.refresh()
    journal.rebuild()
    prune.sync_gitignore()

    paths = [docstore.doc_rel(i) for i in a.idents] + list(ALWAYS) + a.also
    r = git("add", "--", *paths)
    if r.returncode:
        sys.exit(r.stderr.strip())
    print(git("status", "--short").stdout.rstrip())
    loose = [f for f in REGISTRIES if f not in a.also
             and git("diff", "--quiet", "--", f).returncode]
    if loose:
        print("\nизменены, но в индекс не взяты (нужны — назовите в --also): "
              + ", ".join(loose))
    print("\nиндекс собран; коммит — отдельной командой")


if __name__ == "__main__":
    main()
