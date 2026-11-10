#!/usr/bin/env python3
"""Вердикты пачки из файла заметок: черновик `verdict.py` плюс разбор глазами.

    tools/verdicts.py заметки.py            # показать тексты, ничего не писать
    tools/verdicts.py заметки.py --write    # записать через find.py --verdict

Файл заметок — обычный Python с двумя словарями:

    A = "pn0030634"
    D = {A: "Номер на четырёх полосах, 7 ноября 1908 г. Стр. 1 — …"}
    N = {(A, "Кармазинъ"): "кандидатов нет.",
         (A, "Могучевъ"): ("unclear", "стр. 4 — «Мо…евъ», не читается.")}

`D` — описание номера: что на полосах и чем прочитано (по скану, по
слоям). `N` — разбор кандидатов по фамилии; статус по умолчанию `absent`,
иной задаётся парой. Вердикт `found` здесь не пишется: ему нужны
`--pages`, вырезка и решение пользователя о родстве — его записывают
`find.py` руками, а пару из `N` убирают.

Текст складывается так: автоматическая часть черновика, «Разбор остальных
кандидатов: <N> <D>», оговорка о полноте. Писать — только после просмотра
без `--write`: шаблонная фраза, верная для одного выпуска, легко уезжает
во все. Пишется всё, что есть в `N`, поэтому один файл — одна пара
выпусков. Отказ `find.py` (орфография, слабые полосы) виден в выводе:
в конце названо, сколько вердиктов записано и сколько нет.
"""

import argparse
import runpy
import subprocess
import sys

import _root
import verdict

MARK = "ОГОВОРКА О ПОЛНОТЕ"


def compose(ident, surname, note, about):
    head, sep, tail = verdict.build_text(verdict.gather(ident, surname)) \
        .partition(MARK)
    return (f"{head.rstrip()}\n\nРазбор остальных кандидатов: {note} {about}"
            f"\n\n{sep}{tail}").strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("notes", help="файл заметок со словарями N и D")
    ap.add_argument("--write", action="store_true",
                    help="записать вердикты; без флага тексты только печатаются")
    a = ap.parse_args()
    ns = runpy.run_path(a.notes)
    notes, about = ns["N"], ns["D"]
    missing = sorted({ident for ident, _ in notes} - set(about))
    if missing:
        sys.exit("в D нет описания номера: " + ", ".join(missing))

    written, refused = 0, []
    for (ident, surname), note in notes.items():
        status = "absent"
        if isinstance(note, tuple):
            status, note = note
        if status == "found":
            sys.exit(f"{ident} {surname}: found пишется руками через find.py "
                     "(--pages, вырезка, родство решает пользователь)")
        text = compose(ident, surname, note, about[ident])
        if not a.write:
            print(f"===== {ident} {surname} [{status}]\n{text}\n")
            continue
        r = subprocess.run(
            [sys.executable, str(_root.ROOT / "find.py"), ident, surname,
             "--status", status, "--verdict", text],
            capture_output=True, text=True)
        if r.returncode:
            refused.append(f"{ident} {surname}")
            print(f"!! {ident} {surname}: не записан\n{r.stderr.strip()[-800:]}")
        else:
            written += 1
            print(f"{ident} {surname}: записан {status}")
    if a.write:
        print(f"\nзаписано {written} из {len(notes)}")
        if refused:
            sys.exit("не записаны: " + ", ".join(refused))


if __name__ == "__main__":
    main()
