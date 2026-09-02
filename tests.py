"""Проверка на реальных искажениях: дореформенная орфография,
падежи и типичные подмены букв в OCR."""

import sys
from surnamefind.search import find_in_text, stem_query

# (текст, должно ли найтись)
CASES_KUZNETSOV = [
    ("крестьянинъ Кузнецовъ Иванъ", True),                 # точное, с еромъ
    ("у крестьянина Кузнѣцова Ивана", True),               # ять + родительный
    ("отдано Кузнецову Петру", True),                      # дательный
    ("подписано Кузнецовымъ", True),                       # творительный
    ("жена его Кузнецова Марья", True),                    # женская форма
    ("Кузнс цовъ", False),                                 # разорван пробелом — не ловим
    ("Кузиецовъ", True),                                   # OCR: н->и
    ("Кузнсцовъ", True),                                   # OCR: е->с
    ("Кузнецо-\nвымъ", True),                              # перенос строки
    ("KyзнeцoвЪ", True),                                   # латиница вперемешку
    ("Кузнечиковъ", False),                                # другая фамилия
    ("Ковалевъ Сидоръ", False),
    ("кузнецъ Иванъ", False),                              # ремесло, не фамилия
]

CASES_ADJ = [
    ("Ивановскій Петръ", "Ивановский", True),
    ("Ивановскаго Петра", "Ивановский", True),
    ("Ивановскому", "Ивановский", True),
    ("Ивановой Анны", "Ивановский", False),
]


def main():
    failures = []

    for text, expected in CASES_KUZNETSOV:
        hits = find_in_text(text, "Кузнецовъ")
        got = bool(hits)
        mark = "ok " if got == expected else "FAIL"
        if got != expected:
            failures.append((text, expected, hits))
        detail = f" -> {hits[0].raw!r} cost={hits[0].cost}" if hits else ""
        print(f"  [{mark}] {text!r:36}{detail}")

    print()
    for text, query, expected in CASES_ADJ:
        hits = find_in_text(text, query)
        got = bool(hits)
        mark = "ok " if got == expected else "FAIL"
        if got != expected:
            failures.append((text, expected, hits))
        detail = f" -> {hits[0].raw!r} cost={hits[0].cost}" if hits else ""
        print(f"  [{mark}] {text!r:36}{detail}")

    print(f"\nоснова 'Кузнецовъ'  -> {stem_query('Кузнецовъ')!r}")
    print(f"основа 'Кузнѣцова'  -> {stem_query('Кузнѣцова')!r}")
    print(f"основа 'Ивановскій' -> {stem_query('Ивановскій')!r}")

    bad = hyphen_suite()
    total = len(CASES_KUZNETSOV) + len(CASES_ADJ) + len(CASES_HYPHEN)
    print(f"\n{len(failures) + bad} провал(ов) из {total}")
    return 1 if (failures or bad) else 0



CASES_HYPHEN = [
    # Слово разорвано переносом, продолжение испорчено печатью или курсивом.
    # Реальный случай: bv0000386 стр. 208, 'Могу-' / 'чевъ' -> '4:65'.
    ("Тосифъ Даниловь Могу-\n4:65. 1854 15", "Могучевъ", True),
    ("урядникъ Могу-\nчевъ Иванъ", "Могучевъ", True),      # обе половины целы
    ("въ получе-\nни жалованья", "Могучевъ", False),       # 'получе' не начало
    ("изъ мага-\nзина войсковаго", "Кармазинъ", False),    # 'мага' не начало
    ("казакъ Карма-\n3инъ Петръ", "Кармазинъ", True),
    # Обычное слово со строчной: реальный ложный след в bv0000407 стр. 654
    ("для лицъ, не могу-\nщихъ представить", "Могучевъ", False),
]


def hyphen_suite():
    bad = 0
    print("\nразрыв переносом:")
    for text, query, expected in CASES_HYPHEN:
        hits = find_in_text(text, query)
        got = bool(hits)
        if got != expected:
            bad += 1
        mark = "ok " if got == expected else "FAIL"
        det = f" -> {hits[0].raw!r} половина={hits[0].partial}" if hits else ""
        print(f"  [{mark}] {text.splitlines()[0][:28]!r:32}{det}")
    return bad

if __name__ == "__main__":
    sys.exit(main())
