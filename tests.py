"""Проверка на реальных искажениях: дореформенная орфография,
падежи и типичные подмены букв в OCR."""

import sys
from catalog import classify
from journal import year_strip
from prune import GITIGNORE, KEEP_LINE, finding_pages
from find import bare_old_spelling
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

    bad = (hyphen_suite() + spelling_suite() + catalog_suite()
           + years_suite() + gitignore_suite())
    total = (len(CASES_KUZNETSOV) + len(CASES_ADJ) + len(CASES_HYPHEN)
             + len(CASES_SPELLING) + len(CASES_CATALOG) + len(CASES_YEARS))
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



# (текст вердикта, что проверка обязана назвать нарушением)
CASES_SPELLING = [
    ("Та же станица, что у Алексѣя Могучева", ["Алексѣя"]),
    ("Та же станица, что у Алексея Могучева", []),
    # Внутри кавычек и апострофов старое написание законно.
    ("'урядникъ Іосифъ Даниловъ Могучевъ', на службе с 1854 г.", []),
    ("«Могучевъ Иванъ, переп. 1904 года» — награда объявлена", []),
    ("Отклонены: Караваевъ, 'Карасевъ', «Каргинъ»", ["Караваевъ"]),
    # Капслок правило не отменяет: так вышло с томом за 1909 год.
    ("ЭТО ПОВТОРЕНИЕ ПУТИ АЛЕКСѢЯ МОГУЧЕВА", ["АЛЕКСѢЯ"]),
    # Внутренний еръ — не старое написание.
    ("награда объявлена, разъяснение дано", []),
    ("писарь Управленія Донскаго округа", ["Донскаго", "Управленія"]),
    ("благо и Чикаго на -аго не похожи", []),
]


def spelling_suite():
    bad = 0
    print("\nорфография вердикта:")
    for text, expected in CASES_SPELLING:
        got = bare_old_spelling(text)
        if got != expected:
            bad += 1
        mark = "ok " if got == expected else "FAIL"
        print(f"  [{mark}] {text[:44]!r:48} -> {got}")
    return bad

# (заголовок из каталога, куда он должен лечь)
CASES_CATALOG = [
    ("[Приказы по войску Донскому]: за 1898 год",
     "1_приказы_по_войску_донскому"),
    # Эти годы уже проверены поиском Яндекс.Архива — заново не берём.
    ("Памятная книжка Области войска Донского: на 1913 год",
     "не_будут_просмотрены"),
    # А за 1902 год в той подшивке дыра, так что книжка осталась бы нашей.
    ("Памятная книжка Области войска Донского: на 1902 год",
     "2_казачество_войско_донское_новочеркасск"),
    # Чужая губерния под правило про Яндекс не подпадает.
    ("Памятная книжка Таврической губернии: на 1913 год",
     "2_казачество_войско_донское_новочеркасск"),
    ("Новочеркасск: справочная книжка с приложением плана города",
     "2_казачество_войско_донское_новочеркасск"),
    # Отсев спрашивается раньше: это труды советского втуза, а не город.
    ("Известия Северо-Кавказского индустриального института в Новочеркасске: Т. I",
     "не_будут_просмотрены"),
    # Обратная сторона того же порядка: «Свод законов» отбирается по томам.
    ("Свод законов Российской Империи: Т. 2: Учреждение гражданского "
     "управления казаков", "2_казачество_войско_донское_новочеркасск"),
    ("Свод законов Российской Империи: Т. 12, ч. 1: Общий устав Российских "
     "железных дорог", "не_будут_просмотрены"),
    ("Журналы заседаний Ростовской-на-Дону Городской Думы: за 1901 год",
     "3_донской_край_прочее"),
    ("Сборник материалов для описания местностей и племен Кавказа: Вып. 3",
     "не_будут_просмотрены"),
    # Незнакомое название не должно тихо уходить в отсев.
    ("Списки студентов Казанского университета", "нерассортированы"),
]


def catalog_suite():
    bad = 0
    print("\nраскладка каталога:")
    for title, expected in CASES_CATALOG:
        got = classify(title)
        if got != expected:
            bad += 1
        mark = "ok " if got == expected else "FAIL"
        print(f"  [{mark}] {title[:52]!r:56} -> {got}")
    return bad


def gitignore_suite():
    """Страница находки должна пережить чистку сканов.

    Список исключений в .gitignore вёлся руками и разошёлся с журналом:
    из шестнадцати страниц находок в репозиторий попали десять, причём
    три из недостающих — с подтверждённым родством. Теперь список пишет
    prune.sync_gitignore, а эта проверка ловит расхождение.
    """
    listed = {m.group(1) for m in
              (KEEP_LINE.match(ln) for ln in
               GITIGNORE.read_text(encoding="utf-8").splitlines()) if m}
    need = set(finding_pages())
    print("\nстраницы находок в .gitignore:")
    print(f"  находок {len(need)}, перечислено {len(listed)}")
    for path in sorted(need - listed):
        print(f"  [FAIL] не перечислена: {path}")
    for path in sorted(listed - need):
        print(f"  [FAIL] лишняя запись:  {path}")
    if need == listed:
        print("  [ok ] совпадает")
    return len(need ^ listed)


def _doc(year, status, kin=()):
    """Дело для полосы лет: год, исход поиска и есть ли подтверждённое родство."""
    row = {"surname": "Могучевъ", "status": status, "date": "2026-01-01",
           "verdict": "", "confirmed": list(kin) or ["7"], "kin": list(kin)}
    return {"meta": {}, "rows": [row], "coverage": None, "year": year}


# Приказы шли по одному на год, и клетка полосы вела прямо на дело. С
# адрес-календарями за 1899-й окажутся и приказы, и памятная книжка:
# клетка должна вести на год, а цвет — браться по лучшему исходу за год,
# иначе находка в одной книге пропала бы за «не найдено» в другой.
CASES_YEARS = [
    ("находка и пусто за один год",
     {"a": _doc(1899, "absent"), "b": _doc(1899, "found", kin=["7"])},
     ["#g1899", "class='year ok'", "<i class=more>2</i>"]),
    ("однофамилец бьёт «не найдено»",
     {"a": _doc(1902, "absent"), "b": _doc(1902, "found")},
     ["#g1902", "class='year maybe'"]),
    ("одно дело за год — без цифры",
     {"a": _doc(1897, "absent")},
     ["#g1897", "class='year no'"]),
]


def years_suite():
    bad = 0
    print("\nполоса лет:")
    for name, docs, wanted in CASES_YEARS:
        html = year_strip(docs)
        missing = [w for w in wanted if w not in html]
        if missing:
            bad += 1
        mark = "ok " if not missing else "FAIL"
        print(f"  [{mark}] {name:34} " +
              (f"нет: {missing}" if missing else "ok"))
    # Цифра появляется только там, где дел больше одного.
    single = year_strip({"a": _doc(1897, "absent")})
    if "<i class=more>" in single:
        bad += 1
        print("  [FAIL] одиночный год помечен цифрой")
    return bad


if __name__ == "__main__":
    sys.exit(main())
