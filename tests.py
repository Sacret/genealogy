"""Проверка на реальных искажениях: дореформенная орфография,
падежи и типичные подмены букв в OCR."""

import re
import sys
from catalog import SKIP_BY_ID, build, classify, years_covered
from journal import THUMBS, crops_for, markup, render, shot_page, year_strip
from docstore import BIG_SCAN_PIXELS, ROOT, allow_big_scans
import boxes
from prune import GITIGNORE, KEEP_LINE, finding_pages
from find import bare_old_spelling, corpus_documents, kin_persons, year_range
from events import next_event_id, uncovered_findings, validate_event
from namesakes import validate_registry
from eval import evaluate as evaluate_ocr, load_corpus as load_ocr_corpus
from audit import (validate_box, validate_meta, validate_quality,
                   validate_verdict)
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
           + years_suite() + compact_suite() + social_suite() + chips_suite()
           + persons_suite() + doclinks_suite()
           + pagelist_suite() + bigscan_suite() + thumbs_suite()
           + namesakes_suite()
           + pamyatnye_suite() + gitignore_suite() + boxes_suite()
           + events_suite() + registry_suite() + corpus_suite() + audit_suite())
    bad += ocr_eval_suite()
    total = (len(CASES_KUZNETSOV) + len(CASES_ADJ) + len(CASES_HYPHEN)
             + len(CASES_SPELLING) + len(CASES_CATALOG) + 6 + len(CASES_YEARS)
             + len(CASES_PERSONS) + len(CASES_DOCLINKS) + 4 + 3 + 3 + 2 + 8 + 6 + 16
             + 9 + 9 + 10 + 10 + 7 + 12 + 7 + 5)
    print(f"\n{len(failures) + bad} провал(ов) из {total}")
    return 1 if (failures or bad) else 0


def ocr_eval_suite():
    print("\nэталон OCR:")
    corpus = load_ocr_corpus()
    categories = {category for case in corpus["cases"]
                  for category in case["categories"]}
    toy = {
        "version": 1,
        "cases": [
            {"id": "positive", "categories": ["test"],
             "present": ["Кузнецовъ"], "absent": ["Кармазинъ"]},
            {"id": "negative", "categories": ["test"],
             "present": [], "absent": ["Могучевъ"]},
        ],
    }
    result = evaluate_ocr(toy, {
        "positive": "крестьянинъ Кузнецовъ Иванъ",
        "negative": "казакъ Рогачевъ Петръ",
    })
    required = {"clean-book", "table", "newspaper-columns", "italic",
                "hyphenation", "bleed-through", "negative"}
    checks = [
        ("не меньше восьми страниц", len(corpus["cases"]) >= 8),
        ("не меньше 25 истинных фамилий",
         sum(len(case["present"]) for case in corpus["cases"]) >= 25),
        ("покрыты все трудные типы", required <= categories),
        ("recall считается по истинным фамилиям",
         result["found"] == result["truth"] == 1),
        ("ложные кандидаты считаются на страницу",
         result["false_candidates"] == 1
         and result["false_candidates_per_page"] == 0.5),
    ]
    bad = 0
    for label, ok in checks:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {label}")
    return bad


def events_suite():
    print("\nструктурированные события:")
    base = {"id": "e0001", "person": "i0026", "date": "1911-03-27",
            "type": "election", "description": "Избран в комиссию",
            "document": "pn0024347", "page": 4,
            "certainty": "confirmed", "basis": "explicit"}
    roster = {"i0026": {}}
    docs = {"pn0024347": {"pages": 4}}
    cases = [
        ("правильная запись", base, []),
        ("неизвестный человек", {**base, "person": "i9999"},
         ["неизвестная персона i9999"]),
        ("плохая дата", {**base, "date": "27 марта 1911"},
         ["date должен иметь вид YYYY, YYYY-MM или YYYY-MM-DD"]),
        ("страница вне документа", {**base, "page": 5},
         ["страница 5 за пределами документа"]),
        ("неизвестный документ", {**base, "document": "bv9999999"},
         ["неизвестный документ bv9999999"]),
        ("плохая уверенность", {**base, "certainty": "sure"},
         ["неизвестная certainty"]),
    ]
    bad = 0
    for label, event, expected in cases:
        got = validate_event(event, roster, docs)
        ok = got == expected
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {label}")
    for label, records, expected in [
            ("первый номер", [], "e0001"),
            ("следующий номер", [{"id": "e0012"}], "e0013")]:
        got = next_event_id(records)
        ok = got == expected
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {label} -> {got}")
    findings = {("bv0000404", 197, "i0010"),
                ("bv0000404", 217, "i0026")}
    records = [{"document": "bv0000404", "page": 197, "person": "i0010"}]
    missing = uncovered_findings(records, findings)
    for label, ok in [
            ("покрытая находка не потеряна",
             ("bv0000404", 197, "i0010") not in missing),
            ("непокрытая находка названа",
             missing == [("bv0000404", 217, "i0026")])]:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {label}")
    return bad


def registry_suite():
    print("\nоднофамильцы и гипотезы:")
    person = {
        "name": "Иван Могучев",
        "mentions": [{"document": "bv0000001", "page": 2,
                      "date": "1884", "description": "Произведён в урядники"}],
    }
    base = {
        "people": {"u-moguchev-ivan": person, "u-moguchev-ivan-1910": person},
        "hypotheses": [{"left": "u-moguchev-ivan",
                        "right": "u-moguchev-ivan-1910",
                        "relation": "probably_same", "basis": "Имя и станица"}],
    }
    roster = {"i0010": {}}
    docs = {"bv0000001": {"pages": 4}}
    cases = [
        ("правильный реестр", base, []),
        ("неверный временный ID",
         {**base, "people": {"ivan": person}}, ["ID должен иметь вид"]),
        ("неизвестный документ",
         {**base, "people": {"u-moguchev-ivan": {
             **person, "mentions": [{**person["mentions"][0], "document": "bv9999999"}]}}},
         ["неизвестный документ"]),
        ("страница вне документа",
         {**base, "people": {"u-moguchev-ivan": {
             **person, "mentions": [{**person["mentions"][0], "page": 5}]}}},
         ["за пределами документа"]),
        ("неизвестная связь",
         {**base, "hypotheses": [{**base["hypotheses"][0], "relation": "maybe"}]},
         ["неизвестная relation"]),
        ("неизвестный участник",
         {**base, "hypotheses": [{**base["hypotheses"][0], "right": "u-missing"}]},
         ["неизвестный участник"]),
        ("связь с родословной",
         {**base, "hypotheses": [{**base["hypotheses"][0], "right": "i0010",
                                   "relation": "possibly_related"}]}, []),
    ]
    bad = 0
    for label, data, expected in cases:
        got = validate_registry(data, roster, docs)
        ok = (not got) if not expected else all(any(part in error for error in got)
                                                for part in expected)
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {label}")
    return bad


def audit_suite():
    print("\nаудит данных:")
    meta = {"url": "https://vivaldi.dspl.ru/bv0000001",
            "title": "Том", "pages": 4, "dpi": 400}
    quality = {"threshold": 60.0, "pages": {str(n): 61.0 for n in range(1, 5)},
               "weak": []}
    verdict = {"date": "2026-09-17T12:00:00+04:00", "surname": "Могучевъ",
               "status": "found", "verdict": "Найдена", "confirmed": ["2"],
               "kin": ["2"], "persons": {"2": "i0010"}}
    box = {"page": 2, "box": [10, 20, 110, 80], "size": [1000, 1500]}
    cases = [
        ("правильные метаданные", not validate_meta(meta, "bv0000001")),
        ("чужой ID в URL",
         any("URL" in e for e in validate_meta(meta, "bv0000002"))),
        ("страница kept_pages вне тома",
         bool(validate_meta({**meta, "kept_pages": [5]}, "bv0000001"))),
        ("правильная оценка качества", not validate_quality(quality, 4)),
        ("округлённый порог неоднозначен",
         not validate_quality({**quality, "pages": {**quality["pages"], "2": 60.0},
                               "weak": [2]}, 4)),
        ("слабая страница потеряна",
         bool(validate_quality({**quality,
                                "pages": {**quality["pages"], "2": 59.9}}, 4))),
        ("правильный вердикт",
         validate_verdict(verdict, 4, {"i0010": {}}) == ([], [])),
        ("kin вне confirmed",
         bool(validate_verdict({**verdict, "confirmed": []}, 4,
                               {"i0010": {}})[0])),
        ("неизвестная персона",
         bool(validate_verdict(verdict, 4, {}, current=True)[0])),
        ("старый status — предупреждение",
         validate_verdict({k: v for k, v in verdict.items() if k != "status"},
                          4, {"i0010": {}}, current=False)[0] == []),
        ("правильная рамка", not validate_box("p0002_могучев_1.png", box, 4)),
        ("рамка вышла за страницу",
         bool(validate_box("p0002_могучев_1.png",
                           {**box, "box": [10, 20, 1001, 80]}, 4))),
    ]
    bad = 0
    for label, ok in cases:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {label}")
    return bad


def corpus_suite():
    print("\nглобальный поиск:")
    bad = 0
    for label, spec, expected in [
            ("один год", "1912", (1912, 1912)),
            ("диапазон", "1900:1912", (1900, 1912)),
            ("без фильтра", None, None)]:
        got = year_range(spec)
        ok = got == expected
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {label} -> {got}")
    for label, spec in [("неверный год", "191x"),
                        ("обратный диапазон", "1912:1900")]:
        try:
            year_range(spec)
            ok = False
        except ValueError:
            ok = True
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {label}")

    # Реальные метаданные одновременно проверяют фильтр года, заголовка и
    # наличие OCR, не заставляя тест перечитывать весь корпус.
    got = corpus_documents(["bv0000404", "pn0024347"], (1894, 1894))
    ok = [ident for ident, _, _ in got] == ["bv0000404"]
    bad += not ok
    print(f"  [{'ok ' if ok else 'FAIL'}] фильтр года")
    got = corpus_documents(["bv0000404", "pn0024347"], title="ведомости")
    ok = [ident for ident, _, _ in got] == ["pn0024347"]
    bad += not ok
    print(f"  [{'ok ' if ok else 'FAIL'}] фильтр заголовка")
    return bad



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
    # Газета подходит под `Дон` и без своего правила ушла бы к книгам
    # третьей очереди, а её там триста выпусков.
    ("Донские областные ведомости: 1912, № 207 (29 сентября)", "4_газеты"),
    # Именная роспись перебивает отсев по теме: дорога та же, что в
    # правиле ниже, но здесь перечислены люди.
    ("Адрес-Календарь служащих Владикавказской железной дороги: на 1913 г.",
     "3_донской_край_прочее"),
    ("Экономическое обследование железнодорожных линий Владикавказской "
     "железной дороги", "не_будут_просмотрены"),
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

    # Пролистанное руками сильнее заголовка: «Область войска Донского по
    # переписи» — это вторая очередь, но имён в книге не нашлось, и
    # причина должна доехать до `documents.json`.
    ident = "bv0000260"
    rec = {"id": ident,
           "title": "Область войска Донского по переписи 1873 года: Вып. 1, кн. 2"}
    out = build({ident: rec})
    got = [r for r in out["не_будут_просмотрены"] if r["id"] == ident]
    checks = [
        ("отложен по номеру", bool(got)),
        ("причина названа", bool(got) and got[0].get("причина") == SKIP_BY_ID[ident]),
        ("в очереди его нет",
         all(r["id"] != ident for q in out["очередь"].values() for r in q)),
    ]
    for name, ok in checks:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {name}")

    # Длинная серия отсеивается по заголовку, но с причиной — иначе том
    # лежал бы среди отсеянных по теме, а тема у него как раз подходящая.
    rec = {"id": "bv0000209",
           "title": "Сборник правительственных распоряжений по казачьим "
                    "войскам: Т. 1"}
    out = build({"bv0000209": rec})
    got = [r for r in out["не_будут_просмотрены"] if r["id"] == "bv0000209"]
    checks = [
        ("серия отсеяна по заголовку", bool(got)),
        ("причина названа", bool(got) and "имён нет" in got[0].get("причина", "")),
    ]
    for name, ok in checks:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {name}")

    # Газетная подшивка выложена вперемешку: id по возрастанию — это
    # прыжки по годам. Очередь должна идти по времени, иначе читать её
    # подряд нельзя. Последние два — тот самый сдвоенный «№ 1» 1911 года
    # (в библиотеке pn0024070 и pn0024276): порядок по номеру их не
    # различил бы, а по дате различает. Номера здесь выдуманные нарочно:
    # с настоящими проверка ломалась бы, как только выпуск скачают —
    # `build` уводит просмотренное из очереди в просмотренные.
    paper = {n: {"id": n, "title": t} for n, t in [
        ("pn9990001", "Донские областные ведомости: 1912, № 207 (29 сентября)"),
        ("pn9990002", "Донские областные ведомости: 1911, № 81 (15 октября)"),
        ("pn9990003", "Донские областные ведомости: 1911, № 1 (4 января)"),
        ("pn9990004", "Донские областные ведомости: 1911, № 1 (1 января)"),
    ]}
    order = [r["id"] for r in build(paper)["очередь"]["4_газеты"]]
    ok = order == ["pn9990004", "pn9990003", "pn9990002", "pn9990001"]
    bad += not ok
    print(f"  [{'ok ' if ok else 'FAIL'}] газета по времени, а не по id"
          f" -> {order}")
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
    # Альманах о годе молчит, и полоса до него не доводит: ряд кончается
    # последним известным годом. Пузырь в хвосте — его единственная ссылка.
    ("дело без года — пузырь в хвосте",
     {"a": _doc(1897, "absent"), "b": _doc(None, "absent")},
     ["#no-year", "class='year noyear no'", ">без года"]),
    ("два дела без года — с цифрой",
     {"a": _doc(1897, "absent"), "b": _doc(None, "absent"),
      "c": _doc(None, "found")},
     ["class='year noyear maybe'", "<i class=more>2</i>"]),
]


def compact_suite():
    """Дела, где ничего не найдено, — строкой списка, а не карточкой.

    Таких сотни, и карточки с одинаковым серым итогом прятали те немногие,
    где что-то нашлось. Строка при этом остаётся целью ссылок из вердиктов
    и видна фильтру.
    """
    bad = 0
    print("\nдела без находок:")
    empty = {**_doc(1911, "absent"),
             "meta": {"title": "Ведомости № 1", "url": "https://x/item/1",
                      "pages": 4},
             "coverage": {"total": 4, "weak": 2, "rescued": 2}}
    two = {**empty, "meta": {**empty["meta"], "title": "Ведомости № 2"},
           "coverage": None}
    mixed = _doc(1911, "found")
    mixed["rows"].append({**mixed["rows"][0], "surname": "Кармазинъ",
                          "status": "absent"})
    html = render({"a": empty, "b": two, "c": mixed, "d": _doc(1912, "absent")})
    checks = [
        ("пустое дело — не карточка", "<section class=doc id='a'>" not in html),
        ("а строка с якорем", "<li class=nil-doc id='a'>" in html),
        ("название — ссылка во вьюер",
         "<a href='https://x/item/1/view/' target=_blank>Ведомости № 1</a>"
         in html),
        ("читаемость и страницы в скобках",
         "(читаемо 50%, 4 стр.)" in html),
        ("без замера так и сказано", "(читаемость не измерена, 4 стр.)" in html),
        ("подряд идущие — один список",
         html.count("<div class=nils>") == 2
         and html.index("id='a'") < html.index("id='b'")
         < html.index("</ul></div>")),
        ("дело с находкой — карточка", "<section class=doc id='c'>" in html),
        ("карточка года — раньше списка",
         html.index("<section class=doc id='c'>")
         < html.index("<div class=nils>")),
        ("список года закрыт до следующего",
         "</ul></div>\n</div>\n<div class=year-mark id='g1912'>"
         in render({"a": empty, "d": _doc(1912, "absent")})),
        ("последний список закрыт", "</ul></div>\n</div>\n<footer>" in html),
        ("фильтр видит поиск", "data-s='no'></span></li>" in html),
    ]
    for name, ok in checks:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {name}")
    return bad


def social_suite():
    """Превью ссылки содержит полный OG- и Twitter-набор."""
    bad = 0
    print("\nпревью для соцсетей:")
    page = render({})
    title = "Журнал генеалогических поисков"
    description = ("Дореволюционные документы: какие фамилии по каким "
                   "делам уже проверены.")
    image = "https://sacret.github.io/genealogy/social-card.png"
    checks = [
        ("title", f"<title>{title}</title>" in page),
        ("description", f"<meta name=description content='{description}'>" in page),
        ("canonical", "<link rel=canonical href='https://sacret.github.io/genealogy/'>" in page),
        ("OG locale и type", "property='og:locale' content='ru_RU'" in page
         and "property='og:type' content='website'" in page),
        ("OG site_name", f"property='og:site_name' content='{title}'" in page),
        ("OG title", f"property='og:title' content='{title}'" in page),
        ("OG description", f"property='og:description' content='{description}'" in page),
        ("OG URL", "property='og:url' content='https://sacret.github.io/genealogy/'" in page),
        ("OG image", f"property='og:image' content='{image}'" in page),
        ("размер и MIME картинки", "property='og:image:type' content='image/png'" in page
         and "property='og:image:width' content='1734'" in page
         and "property='og:image:height' content='907'" in page),
        ("OG alt", "property='og:image:alt'" in page),
        ("Twitter large image", "name=twitter:card content='summary_large_image'" in page),
        ("Twitter title", f"name=twitter:title content='{title}'" in page),
        ("Twitter description", f"name=twitter:description content='{description}'" in page),
        ("Twitter image", f"name=twitter:image content='{image}'" in page),
        ("Twitter alt", "name=twitter:image:alt" in page),
    ]
    for name, ok in checks:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {name}")
    return bad


def chips_suite():
    """Фильтр по итогу: пузыри и метка статуса на строке.

    Строка таблицы несёт свой итог в `data-s`, и по нему её прячет
    фильтр. Без метки на строке фильтровать пришлось бы по тексту
    вердикта, где слова «не найдена» стоят и в разборе отклонённых
    кандидатов.
    """
    bad = 0
    print("\nфильтр по итогу:")
    html = render({"a": _doc(1899, "absent"), "b": _doc(1900, "found"),
                   "c": _doc(1901, "found", kin=["7"])})
    checks = [
        ("пузырь «не найдена»", "data-s='no'" in html),
        ("пузырь «родство не установлено»", "data-s='maybe'" in html),
        ("пузырь «родство подтверждено»", "data-s='ok'" in html),
        ("итог назван на каждой строке",
         len(re.findall(r"<tr data-k='[^']*' data-s='\w+'>", html))
         == html.count("<tr data-k=")),
        ("счёт рядом с пузырём", "<span class=n>1</span>" in html),
    ]
    for name, ok in checks:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {name}")

    # Выбирать не из чего — фильтр не рисуется вовсе: пузырь-одиночка
    # только притворялся бы фильтром, ничего не отсекая.
    one = render({"a": _doc(1899, "absent"), "b": _doc(1900, "absent")})
    ok = "class=chips" not in one
    bad += not ok
    print(f"  [{'ok ' if ok else 'FAIL'}] при одном итоге фильтра нет")
    return bad


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

    # Дела одного года собраны в блок, надпись с годом стоит над блоком —
    # одна на всю пачку — и вместе с блоком прячется под фильтром, чтобы не
    # висеть над пустотой. На широком экране блок задаёт метке границы: она
    # едет с прокруткой до последнего дела своего года. Якорь для ссылки из
    # полосы — снаружи блока, и потому переживает фильтр.
    # Дела здесь с находками: пустые печатаются строкой списка, а не
    # карточкой, и у них своя проверка — compact_suite.
    html = render({"a": _doc(1873, "found"), "b": _doc(1874, "found"),
                   "c": _doc(1874, "found")})
    checks = [
        ("блок на каждый год", html.count("<div class=year-block>") == 2),
        ("надпись одна на блок", html.count("class=year-tag") == 2),
        ("надпись открывает блок",
         "<div class=year-block>\n"
         "<div class=year-tag aria-hidden=true><span>1874</span></div>\n"
         "<section class=doc id='b'>" in html),
        ("оба дела года в одном блоке",
         html.index("<section class=doc id='b'>")
         < html.index("<section class=doc id='c'>")
         < html.index("</div>\n<footer>")),
        ("блок закрыт до следующего года",
         html.index("<section class=doc id='a'>")
         < html.index("</div>\n<div class=year-mark id='g1874'>")),
        ("якорь остаётся снаружи",
         html.index("id='g1874'") < html.index("<div class=year-block>\n"
                                               "<div class=year-tag "
                                               "aria-hidden=true>"
                                               "<span>1874</span>")),
    ]
    for name, ok in checks:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {name}")

    # Дела без года идут последними и подряд: пачка у них одна, и якорь с
    # надписью ставятся один раз, иначе ссылка из полосы вела бы в середину
    # пачки.
    html = render({"a": _doc(1873, "found"), "b": _doc(None, "found"),
                   "c": _doc(None, "found")})
    checks = [
        ("якорь без года один", html.count("id='no-year'") == 1),
        ("якорь перед первым делом без года",
         html.index("id='no-year'")
         < html.index("<section class=doc id='b'>")
         < html.index("<section class=doc id='c'>")),
        ("надпись «без года» одна",
         html.count("<span>без года</span>") == 1),
        ("оба дела без года в одном блоке",
         html.count("<div class=year-block>") == 2),
    ]
    for name, ok in checks:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {name}")
    return bad


def bigscan_suite():
    """Складень в 196 Мпикс должен открываться.

    Pillow по умолчанию отказывается открывать картинку больше 178
    Мпикс, и на стр. 530 «Донских дел» (bv0000008) падала бинаризация:
    скан складня при 400 dpi — 11478×17056. Проверяется не сам файл
    (сканы в репозитории не лежат), а то, что потолок поднят и что
    поднят он выше этой страницы, но не в бесконечность.
    """
    from PIL import Image
    before = Image.MAX_IMAGE_PIXELS
    allow_big_scans()
    checks = [
        ("потолок поднят", Image.MAX_IMAGE_PIXELS == BIG_SCAN_PIXELS),
        ("складень проходит", Image.MAX_IMAGE_PIXELS > 11478 * 17056),
        ("но не снят совсем", Image.MAX_IMAGE_PIXELS is not None),
    ]
    Image.MAX_IMAGE_PIXELS = before
    print("\nбольшие сканы:")
    bad = 0
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


# Вырезки вшивались в страницу как data:URI, и журнал весил 4.2 МБ, из
# которых 3.1 — девяносто две картинки в base64. Тянулись они все разом,
# хотя почти все лежат в свёрнутых таблицах и в годах, куда никто не
# заходил. Теперь превью — файл рядом, и браузер берёт его по подходу.
# Цена ошибки здесь тихая: забытый атрибут или подобранное чужим обходом
# превью не роняют сборку, а просто выкладывают журнал с пустыми местами
# на месте находок.
def thumbs_suite():
    """Превью вырезок: файл рядом с журналом, а не data:URI внутри него."""
    from PIL import Image
    bad = 0
    print("\nпревью вырезок:")
    ident, surname, page = "pn0024233", "Кармазинъ", "35"
    row = {"surname": surname, "status": "found", "date": "2026-01-01",
           "verdict": "", "confirmed": [page], "kin": [page],
           "persons": {page: "i0117"},
           "hits": 9, "pages_with_hits": [page]}
    html = render({ident: {"meta": {}, "rows": [row],
                           "coverage": None, "year": 1912}})
    cell = html.split("<td class=result>")[1].split("</td>")[0]
    srcs = re.findall(r"<img alt='[^']*' src='([^']+)'", cell)
    files = [ROOT / src for src in srcs]
    crop = sorted((ROOT / ident / "crops").glob(f"p00{page}_кармазин_*.png"))[0]
    thumb = crop.parent / THUMBS / crop.name
    checks = [
        ("картинки вырезок — файлы, а не data:URI",
         bool(srcs) and not any(src.startswith("data:") for src in srcs)),
        ("и лежат в crops/thumbs/",
         all(f"/crops/{THUMBS}/" in src for src in srcs)),
        ("каждая нашлась на диске", all(f.exists() for f in files)),
        ("грузятся по подходу", cell.count("loading=lazy") == len(srcs)),
        ("размер проставлен — иначе страница дёрнется под курсором",
         len(re.findall(r"width=\d+ height=\d+", cell)) == len(srcs)),
        ("страница не несёт вырезок в себе", "data:image/png" not in html),
        # Вырезка с газетной полосы бывает в две тысячи пикселей шириной,
        # и в строку она всё равно не влезает. Узкую (эта — 200 px)
        # превью не растягивает, но и её облегчает: цвет со скана строке
        # не нужен, а серый PNG вдвое легче.
        ("превью не шире строки",
         thumb.exists() and Image.open(thumb).width <= 620),
        ("и не тяжелее самой вырезки",
         thumb.stat().st_size <= crop.stat().st_size),
        # Вырезки журнал ищет шаблоном `pNNNN_основа_*.png` в самой
        # crops/: лежи превью там же, оно попало бы в журнал второй
        # картинкой той же находки.
        ("превью не принято за вторую вырезку",
         len(crops_for(ident, surname, [page])) == len(srcs)),
    ]
    # Pages выкладывает рядом с журналом ровно то, на что он ссылается, и
    # список собирает грепом по самому journal.html. Раньше картинки были
    # внутри страницы, и атрибут src в том грепе не значился.
    deploy = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    checks.append(("деплой забирает и превью", "(href|src|data-page)" in deploy))
    for name, ok in checks:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {name}")
    return bad


def namesakes_suite():
    """Страница с несколькими однофамильцами: подпись одна, а не под каждой.

    `--kin` и `--person` называют страницу, а не место на ней, и журнал
    ставил эту подпись под каждой вырезкой страницы. Пока вырезка одна,
    разницы нет; на стр. 35 выпуска pn0024233 их три — Филипп Петрович
    Кармазин, его сын Анисим и посторонний Василий Демьянов, — и все три
    оказались подписаны именем Филиппа Петровича, то есть журнал выдал
    двух однофамильцев за доказанного предка. Ровно та ошибка, от которой
    заведён `--kin`.
    """
    bad = 0
    print("\nоднофамильцы на одной странице:")
    crops = sorted((ROOT / "pn0024233" / "crops").glob("p0035_кармазин_*.png"))
    row = {"surname": "Кармазинъ", "status": "found", "date": "2026-01-01",
           "verdict": "", "confirmed": ["35"], "kin": ["35"],
           "persons": {"35": "i0117"},
           "hits": 9, "pages_with_hits": ["35"]}
    doc = {"meta": {}, "rows": [row], "coverage": None, "year": 1912}
    html = render({"pn0024233": doc})
    cell = html.split("<td class=result>")[1].split("</td>")[0]
    # Считаем только подписи под вырезками: «родство подтверждено» стоит
    # ещё и в бейдже самой строки, и он тут ни при чём.
    heads = re.findall(r"<div class='cap crophead'>.*?</div>", cell, re.S)
    checks = [
        ("вырезок на странице три", len(crops) == 3),
        ("показаны все три", cell.count("<div class=crop>") == 3),
        ("подпись под вырезками одна", len(heads) == 1),
        ("и родство в ней названо один раз",
         sum(h.count("родство подтверждено") for h in heads) == 1),
        ("имя названо один раз", cell.count("Филипп Петрович Кармазин") == 1),
        ("сказано, что вырезок несколько", "вырезок несколько" in cell),
        ("подпись стоит над группой",
         cell.index("родство подтверждено") < cell.index("<div class=crop>")),
    ]
    for name, ok in checks:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {name}")

    # Одна вырезка — подпись как была, без оговорки про однофамильцев.
    one = {**row, "confirmed": ["38"], "kin": ["38"],
           "persons": {"38": "i0026"}, "surname": "Могучевъ"}
    solo = render({"pn0024233": {**doc, "rows": [one]}})
    scell = solo.split("<td class=result>")[1].split("</td>")[0]
    for name, ok in [("одна вырезка — без оговорки",
                      "вырезок несколько" not in scell),
                     ("и с именем", "Алексей Иосифович Могучев" in scell)]:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {name}")
    return bad


# Вырезка показывает, что фамилия нашлась; страница — где она стоит, в
# алфавитном списке или в объявлении о торгах. Второе держится на
# координатах из crops/boxes.json, и цена ошибки здесь не «рамка чуть
# левее»: страница со списком фамилий — это сорок строк, различающихся
# парой букв, и рамка, поставленная не туда, обводит однофамильца.
def boxes_suite():
    """Координаты вырезок и рамка вокруг находки в журнале."""
    from PIL import Image

    allow_big_scans()
    bad = 0
    print("\nместо вырезки на странице:")
    ident, name = "bv0000043", "p0044_могучев_1.png"
    crop = ROOT / ident / "crops" / name
    scan = ROOT / ident / "scans" / "p0044.jpg"
    kept = boxes.load(ident).get(name, {})
    found = boxes.locate(scan, crop)
    # Та же вырезка, но чужая страница: совпадения быть не должно. Это и
    # есть главная проверка — «похоже» здесь не годится, а `locate` ищет
    # точное вхождение пиксель в пиксель.
    # Страница должна храниться в Git: локальный кэш сканов богаче чистого
    # checkout, и прежняя p0048.jpg делала тест зелёным только на рабочей
    # машине. p0599.jpg — другая подтверждённая находка того же тома.
    other = boxes.locate(ROOT / ident / "scans" / "p0599.jpg", crop)

    place = shot_page(ident, crop)
    gone = shot_page(ident, ROOT / ident / "crops" / "p0598_багрлмов_1.png")

    checks = [
        ("координаты записаны", bool(kept) and len(kept.get("box", [])) == 4),
        ("вырезка нашлась на своей странице", found is not None),
        ("и там, где записано", found == tuple(kept.get("box", []))),
        ("на чужой странице не нашлась", other is None),
        ("размер страницы записан верно",
         kept.get("size") == list(Image.open(scan).size)),
        ("бокс лежит внутри страницы",
         bool(kept) and 0 <= kept["box"][0] < kept["box"][2] <= kept["size"][0]
         and 0 <= kept["box"][1] < kept["box"][3] <= kept["size"][1]),
        ("журнал знает страницу вырезки",
         place is not None and str(place[0]) == f"{ident}/scans/p0044.jpg"),
        # Скан этой страницы выброшен prune.py, и предлагать её нечем:
        # ссылка на несуществующий файл хуже, чем её отсутствие.
        ("без скана страницы не предлагает", gone is None),
    ]
    for label, ok in checks:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {label}")

    # И то же самое глазами журнала: у вырезки с координатами есть и
    # страница, и рамка, а сама рамка задана в долях от скана.
    row = {"surname": "Могучевъ", "status": "found", "date": "2026-01-01",
           "verdict": "", "confirmed": ["44"], "kin": ["44"],
           "persons": {"44": "i0026"}, "hits": 3, "pages_with_hits": ["44"]}
    html = render({ident: {"meta": {}, "rows": [row], "coverage": None,
                           "year": 1911}})
    cell = html.split("<td class=result>")[1].split("</td>")[0]
    for label, ok in [
            ("в строке стоит страница", "data-page=" in cell),
            ("и координаты при ней",
             f"data-box='{','.join(str(v) for v in kept.get('box', []))}'"
             in cell)]:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {label}")
    return bad


if __name__ == "__main__":
    sys.exit(main())
