"""Проверка на реальных искажениях: дореформенная орфография,
падежи и типичные подмены букв в OCR."""

import sys
from catalog import classify, years_covered
from journal import markup, render, year_strip
from prune import GITIGNORE, KEEP_LINE, finding_pages
from find import bare_old_spelling, kin_persons
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

    bad_joins = join_checks()
    print(f"\nоснова 'Кузнецовъ'  -> {stem_query('Кузнецовъ')!r}")
    print(f"основа 'Кузнѣцова'  -> {stem_query('Кузнѣцова')!r}")
    print(f"основа 'Ивановскій' -> {stem_query('Ивановскій')!r}")

    bad = (bad_joins + hyphen_suite() + spelling_suite() + catalog_suite()
           + years_suite() + persons_suite() + doclinks_suite()
           + pagelist_suite()
           + pamyatnye_suite() + gitignore_suite())
    total = (len(CASES_KUZNETSOV) + len(CASES_ADJ) + len(CASES_HYPHEN)
             + len(CASES_SPELLING) + len(CASES_CATALOG) + len(CASES_YEARS)
             + len(CASES_PERSONS) + len(CASES_DOCLINKS) + 4 + 2 + 8)
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
    # Две колонки: перенос кончает ПРАВУЮ, а следующая строка начинается
    # с ЛЕВОЙ. Склейка сращивает чужие слова, и фамилия исчезает как
    # токен. Реальный случай: bv0000040 стр. 96, фельдшер Могучев —
    # печать чистая, распознано верно, а поиск его не видел.
    ("Евд. Дмитр. Добры-\nМогучевъ А. І., Троицкій базаръ.", "Могучевъ", True),
    ("сл. Мартынов-\nКармазинъ Ф. П., Азовскій баз.", "Кармазинъ", True),
    # Настоящий перенос при этом остаётся переносом, а не двумя словами.
    ("мѣщанинъ Кузне-\nцовъ Иванъ", "Кармазинъ", False),
    # Разрыв БЕЗ дефиса, да ещё с затёкшей между половинами соседней
    # колонкой. Реальный случай: bv0000042 стр. 129, «Алекс. Іос. Мо» /
    # «...сестеръ ми- | гучевъ». Продолжение ищется среди слов следующей
    # строки, а не только сразу за разрывом.
    ("Алекс. Іос. Мо\nчеркасской общины сестеръ ми- | гучевъ.",
     "Могучевъ", True),
    ("Филиппъ Петр. Кар\nтамъ-же, Азовск. баз. | мазинъ", "Кармазинъ", True),
]



def join_checks():
    """Склейка через конец строки: смотрим на само склеенное слово.

    Булев «нашлось / не нашлось» тут не годится: хвост 'гучевъ' проходит
    порог и в одиночку, так что проверять надо, собралась ли фамилия
    целиком. Заодно это проверяет условие на прописную букву — без него
    правило склеивало бы обрывки обычных слов.
    """
    bad = 0
    print("\nсклейка через конец строки:")
    joined = [m.raw for m in find_in_text(
        "Алекс. Іос. Мо\nчеркасской общины сестеръ ми- | гучевъ.", "Могучевъ")]
    if "Могучевъ" not in joined:
        bad += 1
    print(f"  [{'ok ' if 'Могучевъ' in joined else 'FAIL'}] "
          f"колонка между половинами -> {joined}")

    lower = [m.raw for m in find_in_text(
        "не мо\nгучевъ вовсе", "Могучевъ")]
    ok = not any(len(r) > 6 for r in lower)      # 'могучевъ' склеиться не должно
    bad += not ok
    print(f"  [{'ok ' if ok else 'FAIL'}] обломок со строчной не склеен -> {lower}")
    return bad


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


def pamyatnye_suite():
    """Годы памятных книжек: двойной том и полнота разбиения.

    «Памятная книжка на 1893-1894 год» — одна книга за два года, и
    первый прогон разреза назвал 1894-й ненайденным, хотя он лежит под
    той же обложкой. Отсюда `years_covered` и эта проверка.
    """
    bad = 0
    print("\nпамятные книжки по годам:")
    cases = [
        ("Памятная книжка Области войска Донского: на 1893-1894 год",
         [1893, 1894]),
        ("Памятная книжка Области войска Донского: на 1885 год", [1885]),
        ("[Приказы по войску Донскому]: [за 1873 год]", [1873]),
        # Подшивка целиком — не том за полвека: разворачивать нечего.
        ("Памятная книжка Области войска Донского: на 1866-1916 годы", [1866]),
        ("Донской календарь: без года", []),
    ]
    for title, expected in cases:
        got = years_covered(title)
        bad += got != expected
        print(f"  [{'ok ' if got == expected else 'FAIL'}] {title[:46]!r:50}"
              f" -> {got}")

    # Разряды должны покрывать промежуток целиком и не пересекаться:
    # иначе год, выпавший из всех, читается как «источника нет», а он
    # есть. Проверяется на живом documents.json — его и читают глазами.
    import json as _json
    from docstore import ROOT
    doc = _json.loads((ROOT / "documents.json").read_text(encoding="utf-8"))
    pk = doc.get("памятные_книжки_по_годам")
    if not pk:
        bad += 1
        print("  [FAIL] разреза по годам нет в documents.json")
    else:
        parts = [pk[k] for k in ("просмотрены", "в_очереди",
                                 "проверены_яндекс_архивом", "нет_нигде")]
        union = set().union(*(set(x) for x in parts))
        total = sum(len(x) for x in parts)
        span = set(range(min(union), max(union) + 1))
        checks = [("промежуток покрыт целиком", union == span),
                  ("разряды не пересекаются", total == len(union)),
                  ("книга есть — года нет в «нет нигде»",
                   not (set(pk["нет_нигде"]) & set(pk["есть_в_библиотеке"])))]
        for name, ok in checks:
            bad += not ok
            print(f"  [{'ok ' if ok else 'FAIL'}] {name}")
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


# Родство подтверждает человек, и в журнале оно должно быть названо
# поимённо: --kin без --person не принимается, а лишний или незнакомый
# идентификатор — ошибка, а не молчаливая потеря имени.
CASES_PERSONS = [
    ("один человек на все страницы",
     ("i0023", ["80", "208"]), {"80": "i0023", "208": "i0023"}),
    ("роспись по страницам",
     ("197=i0010,217=i0026", ["197", "217"]),
     {"197": "i0010", "217": "i0026"}),
    ("родство без имени",       (None, ["254"]),            SystemExit),
    ("имя без родства",         ("i0026", []),              SystemExit),
    ("незнакомый идентификатор", ("i9999", ["7"]),          SystemExit),
    ("названа лишняя страница",
     ("7=i0026,8=i0026", ["7"]), SystemExit),
    ("страница осталась без имени",
     ("197=i0010", ["197", "217"]), SystemExit),
]


def persons_suite():
    bad = 0
    print("\nкто найден:")
    for name, (spec, kin), expected in CASES_PERSONS:
        try:
            got = kin_persons(spec, kin)
        except SystemExit:
            got = SystemExit
        ok = got == expected
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {name:30} -> {got}")

    # Ссылка на родословную стоит в свёрнутой сводке — там, где итог
    # читается сразу, — и не повторяется в развёрнутой строке: рядом с
    # вердиктом человек и так назван.
    doc = _doc(1907, "found", kin=["254"])
    doc["rows"][0]["persons"] = {"254": "i0117"}
    html = render({"bv0000035": doc})
    for want in ("family.sacret.ru/persons/i0117/", "Филипп Петрович Кармазин"):
        if want not in html:
            bad += 1
            print(f"  [FAIL] в журнале нет {want!r}")
    summary, _, rest = html.partition("</summary>")
    if "class=person" not in summary:
        bad += 1
        print("  [FAIL] в свёрнутой сводке имени нет")
    if "class=person" in rest:
        bad += 1
        print("  [FAIL] имя повторено в развёрнутой строке")
    # Находка без родства именем не подписывается: молчание — не подтверждение.
    plain = render({"bv0000035": _doc(1907, "found")})
    if "class=person" in plain:
        bad += 1
        print("  [FAIL] однофамилец подписан именем родственника")
    if not bad:
        print("  [ok ] ссылка на родословную — в сводке, и только там")
    return bad


# Вердикт постоянно ссылается на соседние тома: «ТОТ ЖЕ ЧЕЛОВЕК, что в
# bv0000386». В журнале они лежат на одной странице, и номер должен вести
# на якорь карточки — но только тот, который на странице есть.
CASES_DOCLINKS = [
    ("соседнее дело — ссылка",
     ("см. bv0000386 стр. 208", {"bv0000386"}, None),
     "<a class=doclink href='#bv0000386'>bv0000386</a>"),
    ("дела нет на странице — текстом",
     ("см. bv0000999", {"bv0000386"}, None), "bv0000999"),
    ("на себя не ссылаемся",
     ("в этом же bv0000386", {"bv0000386"}, "bv0000386"), "bv0000386"),
    ("номер внутри цитаты тоже ссылка",
     ("«как в bv0000386»", {"bv0000386"}, None),
     "<a class=doclink href='#bv0000386'>bv0000386</a>"),
]


def doclinks_suite():
    bad = 0
    print("\nссылки на соседние дела:")
    for name, (text, known, skip), wanted in CASES_DOCLINKS:
        html = markup(text, known, skip)
        ok = wanted in html
        # «текстом» значит именно текстом: якоря быть не должно.
        if not wanted.startswith("<") and "class=doclink" in html:
            ok = False
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {name:34} -> {html}")
    return bad


def _doc(year, status, kin=()):
    """Дело для полосы лет: год, исход поиска и есть ли подтверждённое родство."""
    row = {"surname": "Могучевъ", "status": status, "date": "2026-01-01",
           "verdict": "", "confirmed": list(kin) or ["7"], "kin": list(kin),
           "hits": len(kin), "pages_with_hits": list(kin)}
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

    # Надпись с годом стоит у первого дела года и внутри секции: под
    # фильтром секция прячется, и год должен уйти вместе с ней, чтобы не
    # висеть над пустотой. Якорь для ссылки из полосы — наоборот, снаружи
    # секции, и потому переживает фильтр.
    html = render({"a": _doc(1873, "absent"), "b": _doc(1874, "absent"),
                   "c": _doc(1874, "found")})
    checks = [
        ("год назван раз на год", html.count("class=year-tag") == 2),
        ("надпись внутри секции",
         "<section class=doc id='b'>\n<div class=year-tag aria-hidden=true>1874"
         in html),
        ("якорь остаётся снаружи",
         html.index("id='g1874'") < html.index("<section class=doc id='b'>")),
    ]
    for name, ok in checks:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {name}")
    return bad


def pagelist_suite():
    """Подтверждённая страница обязана стоять в списке страниц и жирным.

    Поиск находит не всё: фельдшера Могучева на стр. 104 тома bv0000039
    нашёл глаз, а не матчер, и в журнале эта страница не появилась вовсе —
    список показывал одних отклонённых кандидатов, ни один номер не был
    выделен, и находка читалась как её отсутствие.
    """
    bad = 0
    print("\nсписок страниц:")
    row = {"surname": "Могучевъ", "status": "found", "date": "2026-01-01",
           "verdict": "", "confirmed": ["104"], "kin": ["104"],
           "persons": {"104": "i0026"},
           "hits": 3, "pages_with_hits": ["75", "99", "290"]}
    doc = {"meta": {}, "rows": [row], "coverage": None, "year": 1909}
    html = render({"bv0000039": doc})
    cell = html.split("<td class=pages ")[1].split("</td>")[0]
    checks = [
        ("страница находки в списке", ">104</a>" in cell),
        ("она выделена жирным", "class=hit href='" in cell
                                and ">104</a>" in cell.split("class=hit")[1][:80]),
        ("кандидаты не потерялись", ">75</a>" in cell and ">290</a>" in cell),
        ("порядок числовой", cell.index(">99</a>") < cell.index(">104</a>")),
    ]
    for name, ok in checks:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {name}")
    return bad


if __name__ == "__main__":
    sys.exit(main())
