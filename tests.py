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
import docstore
import ocr_pages
import quality
import tess
import run as pipeline
import stripes
from audit import stripes_unproven, validate_stripes_marker
import verdict
from audit import validate_evidence
from find import weak_pages_unmentioned

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
           + events_suite() + registry_suite() + corpus_suite() + audit_suite()
           + tess_suite() + queue_run_suite() + stripes_suite()
           + verdict_suite())
    bad += ocr_eval_suite()
    total = (len(CASES_KUZNETSOV) + len(CASES_ADJ) + len(CASES_HYPHEN)
             + len(CASES_SPELLING) + len(CASES_CATALOG) + 6 + len(CASES_YEARS)
             + len(CASES_PERSONS) + len(CASES_DOCLINKS) + 4 + 3 + 3 + 2 + 8 + 6 + 16
             + 9 + 9 + 10 + 10 + 7 + 12 + 7 + 5 + 17 + 16 + 17 + 38)
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
        ("фильтр видит поиск", re.search(
            r"data-s='no' data-n='[^']*'></span></li>", html) is not None),
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
         len(re.findall(r"<tr data-k='[^']*' data-s='\w+' data-n='[^']*'>", html))
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


def tess_suite():
    import json
    import pathlib
    import shutil
    import subprocess
    import tempfile
    from types import SimpleNamespace
    print("\nодин проход Tesseract:")
    tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop"
           "\twidth\theight\tconf\ttext\n"
           "1\t1\t0\t0\t0\t0\t0\t0\t10\t10\t-1\t\n"
           "5\t1\t1\t1\t1\t1\t0\t0\t5\t5\t90.0\tКазакъ\n"
           "5\t1\t1\t1\t1\t2\t0\t0\t5\t5\t50.0\tИванъ\n"
           "5\t1\t1\t1\t1\t3\t0\t0\t5\t5\t-1\t \n")
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        base = pathlib.Path(cmd[2])
        base.with_suffix(".txt").write_text("Казакъ Иванъ\n", encoding="utf-8")
        base.with_suffix(".tsv").write_text(tsv, encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    def crash_run(cmd, **kw):
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"Error boom")

    def raises(fn, *args):
        try:
            fn(*args)
        except Exception as e:
            return type(e).__name__
        return None

    text, conf, words = tess.recognize("p0001.jpg", "rus", 6, fake_run)
    old_root = docstore.ROOT
    old_recognize = tess.recognize
    tmp = tempfile.TemporaryDirectory()
    docstore.ROOT = pathlib.Path(tmp.name)
    try:
        d = docstore.ROOT / "bv0000903"
        (d / "scans").mkdir(parents=True)
        (d / "ocr").mkdir()
        scans = []
        for n in (1, 2, 3):
            f = d / "scans" / f"p{n:04d}.jpg"
            f.write_bytes(b"x")
            scans.append(f)

        tess.recognize = lambda img, lang, psm, run=None: (
            ("Текст страницы\n", 71.5, 120))
        n1, stat1, err1 = ocr_pages.run((scans[0], d / "ocr/p0001.txt", "rus", "6"))
        written = ((d / "ocr/p0001.txt").read_text(encoding="utf-8")
                   if (d / "ocr/p0001.txt").exists() else None)
        leftover = (d / "ocr/p0001.part").exists()
        n1b, stat1b, err1b = ocr_pages.run(
            (scans[0], d / "ocr/p0001.txt", "rus", "6"))

        def boom(img, lang, psm, run=None):
            raise tess.TesseractError("упал")
        tess.recognize = boom
        n2, stat2, err2 = ocr_pages.run((scans[1], d / "ocr/p0002.txt", "rus", "6"))
        failed_left = (d / "ocr/p0002.txt").exists() or (d / "ocr/p0002.part").exists()

        ocr_pages.save_stats(d, "rus", "6", {"1": {"conf": 71.5, "words": 120}})
        same = ocr_pages.load_stats(d, "rus", "6")
        other_psm = ocr_pages.load_stats(d, "rus", "4")
        (d / ocr_pages.STATS).write_text("{не json", encoding="utf-8")
        broken = ocr_pages.load_stats(d, "rus", "6")

        # замер: страница 1 из статистики, 2 и 3 — распознаются
        ocr_pages.save_stats(d, "rus", "6", {"1": {"conf": 71.5, "words": 120}})
        measured = []

        def measure_fake(img, lang, psm, run=None):
            measured.append(img.name)
            return "", 40.0, 10
        tess.recognize = measure_fake
        stats, reused = quality.measure(scans, "6", d, 2)
        measured_all = sorted(measured)
        measured.clear()
        quality.measure(scans, "6", d, 2, remeasure=True)
        remeasured = sorted(measured)
        tess.recognize = boom
        try:
            quality.measure(scans, "6", d, 2, remeasure=True)
            exited = False
        except SystemExit as e:
            exited = "стр. 1, 2, 3" in str(e)
        tess.recognize = old_recognize

        real = None
        if shutil.which("tesseract"):
            img = pathlib.Path(__file__).parent / "bench" / "p020_150.jpg"
            plain = subprocess.run(
                ["tesseract", str(img), "-", "-l", "rus", "--psm", "6"],
                capture_output=True, text=True).stdout
            real_text, _, real_words = tess.recognize(img, "rus", 6)
            real = real_text == plain and real_words > 0
    finally:
        tess.recognize = old_recognize
        docstore.ROOT = old_root
        tmp.cleanup()

    checks = [
        ("уверенность — среднее по словам без служебных строк",
         parse_ok(tess.parse_tsv(tsv))),
        ("страница без слов даёт нули", tess.parse_tsv("level\ttext\n") == (0.0, 0)),
        ("текст и статистика из одного вызова",
         text == "Казакъ Иванъ\n" and conf == 70.0 and words == 2
         and len(calls) == 1),
        ("tesseract просят сразу txt и tsv", calls[0][0][-2:] == ["txt", "tsv"]),
        ("потоки OpenMP ограничены, срок задан",
         calls[0][1]["env"]["OMP_THREAD_LIMIT"] == "1"
         and calls[0][1]["timeout"] == tess.TIMEOUT),
        ("упавший tesseract — ошибка, а не пустой текст",
         raises(tess.recognize, "p.jpg", "rus", 6, crash_run) == "TesseractError"),
        ("и в режиме «только текст»",
         raises(tess.text_only, "p.jpg", "rus", 6, crash_run) == "TesseractError"),
        ("страница записана вместе со статистикой",
         n1 == 1 and stat1 == {"conf": 71.5, "words": 120} and err1 is None
         and written == "Текст страницы\n" and not leftover),
        ("готовая страница пропускается", (stat1b, err1b) == (None, None)),
        ("упавшая страница ошибка, файла нет",
         n2 == 2 and stat2 is None and "упал" in err2 and not failed_left),
        ("статистика того же режима читается",
         same == {"1": {"conf": 71.5, "words": 120}}),
        ("статистика чужого режима не годится", other_psm == {}),
        ("битая статистика — как её нет", broken == {}),
        ("замер берёт готовое и дочитывает остальное",
         measured_all == ["p0002.jpg", "p0003.jpg"] and reused == 1
         and [x[0] for x in stats] == [1, 2, 3]),
        ("--remeasure распознаёт всё заново",
         remeasured == ["p0001.jpg", "p0002.jpg", "p0003.jpg"]),
        ("сбой замера не пишет частичный итог", exited),
    ]
    if real is None:
        checks.append(("реальный tesseract: пропущено, его нет", True))
    else:
        checks.append(("реальный tesseract: текст как при обычном вызове", real))
    bad = 0
    for label, ok in checks:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {label}")
    return bad


def queue_run_suite():
    import json
    import pathlib
    import tempfile
    import threading
    from types import SimpleNamespace
    print("\nочередь в run.py:")
    rec = lambda i: {"id": i, "url": f"https://x/{i}/view/", "title": i}
    docs = {"в_работе": [rec("bv1"), rec("bv2")],
            "очередь": {"1_приказы": [rec("bv3")],
                        "2_казачество": [rec("bv4"), rec("bv5")],
                        "4_газеты": [rec("pn1"), rec("pn2")]}}
    everything = lambda ident, surnames: True
    ids = lambda picked: [r["id"] for r in picked]

    # Реальный диск: готовый документ и документ без поиска нужной фамилии.
    old_root = docstore.ROOT
    tmp = tempfile.TemporaryDirectory()
    docstore.ROOT = pathlib.Path(tmp.name)
    try:
        ready = docstore.ROOT / "bv0000911"
        (ready / "ocr").mkdir(parents=True)
        for n in (1, 2):
            (ready / "ocr" / f"p{n:04d}.txt").write_text("текст", encoding="utf-8")
        docstore.save_meta("bv0000911", pages=2, url="u", title="Том")
        half = docstore.ROOT / "bv0000912"
        (half / "ocr").mkdir(parents=True)
        (half / "ocr" / "p0001.txt").write_text("текст", encoding="utf-8")
        docstore.save_meta("bv0000912", pages=2, url="u", title="Том")
        no_quality = pipeline.is_processed("bv0000911")
        (ready / "quality.json").write_text("{}", encoding="utf-8")
        processed = pipeline.is_processed("bv0000911")
        half_done = pipeline.is_processed("bv0000912")
        none_yet = pipeline.is_processed("bv0000999")
        (ready / "searches.jsonl").write_text(json.dumps(
            {"type": "search", "surname": "Кармазинъ"}) + "\n", encoding="utf-8")
        skip_known = pipeline.needs_work("bv0000911", ["Кармазинъ"])
        need_other = pipeline.needs_work("bv0000911", ["Кармазинъ", "Могучевъ"])
        skip_plain = pipeline.needs_work("bv0000911", [])
    finally:
        docstore.ROOT = old_root
        tmp.cleanup()

    # Конвейер: скачивание следующего должно идти, пока читается этот.
    started = {"bv2": threading.Event()}
    order, lock = [], threading.Lock()

    def fetch(a, base):
        with lock:
            order.append("fetch " + base)
        if base == "bv2":
            started["bv2"].set()
        if base == "bad":
            raise pipeline.StepFailed("упал")

    overlapped = []

    def process(a, base):
        if base == "bv1":
            overlapped.append(started["bv2"].wait(5))
        with lock:
            order.append("process " + base)

    args = SimpleNamespace(background=False)
    results = pipeline.run_batch(args, ["bv1", "bv2"], fetch, process)
    order.clear()
    mixed = pipeline.run_batch(args, ["bad", "bv3"], fetch, process)

    checks = [
        ("сначала «в работе», потом очереди по порядку",
         ids(pipeline.pick_next(4, docs, (), None, everything))
         == ["bv1", "bv2", "bv3", "bv4"]),
        ("берётся не больше N",
         len(pipeline.pick_next(2, docs, (), None, everything)) == 2),
        ("--queue газеты берёт только газеты",
         ids(pipeline.pick_next(5, docs, (), "газеты", everything))
         == ["pn1", "pn2"]),
        ("готовое пропускается, берётся следующее",
         ids(pipeline.pick_next(
             2, docs, (), None, lambda i, s: i not in ("bv1", "bv3")))
         == ["bv2", "bv4"]),
        ("очереди не хватает — берётся сколько есть",
         len(pipeline.pick_next(50, docs, (), None, everything)) == 7),
        ("без quality.json документ не обработан", not no_quality),
        ("целиком распознанный и измеренный — обработан", processed),
        ("половина страниц — не обработан", not half_done),
        ("неизвестный документ — не обработан", not none_yet),
        ("обработанный и найденный не берётся", not skip_known),
        ("новая фамилия возвращает документ в работу", need_other),
        ("без фамилий обработанный не берётся", not skip_plain),
        ("скачивание следующего началось до конца этого",
         overlapped == [True]),
        ("оба документа обработаны", results == {"bv1": None, "bv2": None}),
        ("упавшее скачивание не останавливает соседа",
         mixed["bad"] is not None and mixed["bv3"] is None
         and "process bv3" in order),
        ("у упавшего нет шагов обработки", "process bad" not in order),
    ]
    bad = 0
    for label, ok in checks:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {label}")
    return bad


def parse_ok(result):
    conf, words = result
    return abs(conf - 70.0) < 1e-9 and words == 2


def stripes_suite():
    import json
    import pathlib
    import tempfile
    from types import SimpleNamespace
    print("\nсплошной проход лентами:")
    boxes = stripes.stripe_boxes(1000)
    ident = "pn0000901"
    old_root = docstore.ROOT
    tmp = tempfile.TemporaryDirectory()
    docstore.ROOT = pathlib.Path(tmp.name)
    try:
        d = docstore.ROOT / ident
        (d / "scans").mkdir(parents=True)
        for n in (1, 2, 3):
            (d / "scans" / f"p{n:04d}.jpg").write_bytes(b"x" * 2048)
        docstore.save_meta(ident, url="https://x/" + ident, pages=3, dpi=400)
        calls = []

        def reader(scan, lang):
            calls.append(scan.name)
            if scan.name == "p0002.jpg":
                raise RuntimeError("tesseract: код 1")
            return f"казакъ Кармазинъ {scan.name}\nКарась"

        first = stripes.process(ident, [1, 2, 3], "rus", 2, reader)
        partial_marker = stripes.write_marker(ident, "rus")
        marker_early = (d / "stripes.json").exists()
        calls.clear()
        second = stripes.process(ident, [1, 2, 3], "rus", 2, reader)
        failed_again = sorted(second[2])
        # страница 2 снова падает; починим читатель и дочитаем её одну
        stripes.process(ident, [1, 2, 3], "rus", 2,
                        lambda scan, lang: "Карась")
        marker = stripes.write_marker(ident, "rus")
        texts = {n: (d / "ocr_stripes" / f"p{n:04d}.txt").read_text(encoding="utf-8")
                 for n in (1, 2, 3)}
        words = stripes.words_by_prefix(texts, ["кар"])["кар"]

        def run_ok(cmd, **kw):
            return SimpleNamespace(returncode=0, stdout="текст".encode(),
                                   stderr=b"")

        def run_bad(cmd, **kw):
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"boom")

        from PIL import Image
        im = Image.new("L", (50, 20), 255)
        try:
            stripes.read_stripe(im, "rus", tmp.name, run_bad)
            bad_raises = False
        except RuntimeError:
            bad_raises = True
        row = {"date": "2026-10-05T10:00:00+04:00",
               "verdict": "ОГОВОРКА: сплошным проходом вертикальными лентами"}
        checks = [
            ("ленты покрывают лист от края до края",
             boxes[0][0] == 0 and boxes[-1][1] == 1000),
            ("ширина ленты — пятая часть, шаг — десятая",
             boxes[0] == (0, 200) and boxes[1][0] == 100),
            ("ленты идут внахлёст без дыр",
             all(b[0] < a[1] for a, b in zip(boxes, boxes[1:]))),
            ("узкий лист не зацикливает разбиение",
             stripes.stripe_boxes(3)[-1][1] == 3),
            ("упавшая страница не роняет остальные",
             first[0] == 2 and list(first[2]) == [2]),
            ("пока страница не прочитана, маркера нет",
             partial_marker is None and not marker_early),
            ("повтор не перечитывает готовое",
             sorted(calls) == ["p0002.jpg"] and second[1] == 2),
            ("ошибка страницы видна и при повторе", failed_again == [2]),
            ("после дочитывания документ отмечен целиком",
             marker is not None and marker["pages"] == marker["of"] == 3),
            ("выписка слов по началу собирает страницы",
             sorted(words.get("карась", ())) == [1, 2, 3]),
            ("упавший tesseract — ошибка, а не пустая лента", bad_raises),
            ("успешный tesseract отдаёт текст",
             stripes.read_stripe(im, "rus", tmp.name, run_ok) == "текст"),
            ("целый маркер проходит аудит",
             validate_stripes_marker(marker, 3) == []),
            ("неполный маркер — ошибка",
             bool(validate_stripes_marker({"pages": 2, "of": 3}, 3))),
            ("новый вердикт без маркера — предупреждение",
             stripes_unproven(row, False)),
            ("с маркером предупреждения нет", not stripes_unproven(row, True)),
            ("прежний вердикт не трогаем",
             not stripes_unproven({**row, "date": "2026-09-30T10:00:00+04:00"},
                                  False)),
        ]
    finally:
        docstore.ROOT = old_root
        tmp.cleanup()
    bad = 0
    for label, ok in checks:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {label}")
    return bad


def verdict_suite():
    import json
    import pathlib
    import tempfile
    from types import SimpleNamespace
    print("\nчерновик вердикта:")
    ok_doc, bad_doc = "bv0000901", "bv0000902"
    dismissed = {"кармазин": {"кармалин": "другая фамилия"}}
    quality = {"threshold": 60.0, "pages": {"1": 52.6, "2": 71.0, "3": 50.9},
               "weak": [1, 3]}
    old_root = docstore.ROOT
    tmp = tempfile.TemporaryDirectory()
    docstore.ROOT = pathlib.Path(tmp.name)
    try:
        def make(ident, texts):
            d = docstore.ROOT / ident
            (d / "ocr").mkdir(parents=True)
            for n, text in enumerate(texts, 1):
                (d / "ocr" / f"p{n:04d}.txt").write_text(text, encoding="utf-8")
            docstore.save_meta(ident, url=f"https://x/{ident}",
                               title="Газета", pages=len(texts), dpi=400)
            (d / "quality.json").write_text(json.dumps(quality), encoding="utf-8")

        make(ok_doc, ["казакъ Кармалинъ Иванъ", "объявленіе о продажѣ",
                      "стихотвореніе"])
        make(bad_doc, ["урядникъ Кармазинъ Яковъ", "объявленіе", "стихи"])
        ctx = verdict.gather(ok_doc, "Кармазинъ", dismissed=dismissed)
        bad_ctx = verdict.gather(bad_doc, "Кармазинъ", dismissed=dismissed)
        make("bv0000903", ["станица въ Мусскомъ округѣ", "Мирскому сходу",
                           "стихи"])
        place_ctx = verdict.gather("bv0000903", "Кармазинъ", dismissed=dismissed)
        text = verdict.build_text(ctx)

        def exits(fn, *args):
            try:
                fn(*args)
            except SystemExit:
                return True
            return False
        hit = lambda cost, partial=False, raw="Кармалинъ": SimpleNamespace(
            cost=cost, partial=partial, raw=raw)

        checks = [
            ("искажённое слово из списка отсеяно",
             verdict.classify(hit(2.0), {"кармалин": "x"}) == ("dismissed", "x")),
            ("точное совпадение не отсеивается даже из списка",
             verdict.classify(hit(0), {"кармалин": "x"})[0] == "exact"),
            ("обрывок переноса не отсеивается",
             verdict.classify(hit(0.5, True), {"кармалин": "x"})[0] == "partial"),
            ("слово не из списка уходит человеку",
             verdict.classify(hit(2.0), {})[0] == "review"),
            ("в чистом документе всё отсеяно", not verdict.unresolved(ctx)),
            ("чистый документ можно записать", verdict.problems(ctx) == []),
            ("в тексте слабые страницы с оценками",
             "стр. 1 — 52,6, стр. 3 — 50,9" in text),
            ("порог и число слабых страниц в тексте",
             "ниже порога 60: 2 страницы" in text),
            ("отсеянное названо вместе с причиной",
             "«Кармалинъ» (другая фамилия; стр. 1)" in text),
            ("места названы непросмотренными",
             "Места найдены не были" in text),
            ("точная Кармазинъ блокирует автозапись",
             any("решает человек" in p for p in verdict.problems(bad_ctx, "x"))),
            ("без разбора неотсеянное блокирует",
             any("--note" in p for p in verdict.problems(
                 {**ctx, "place_hits": {"Ровеньки": [2]}}))),
            ("с --note место можно записать",
             verdict.problems({**ctx, "place_hits": {"Ровеньки": [2]}},
                              "стр. 2 — другая Ровеньки") == []),
            ("без stripes.json о лентах ни слова",
             "сплошной проход" not in text and ctx["stripes"] is None),
            ("со stripes.json проход лентами назван в оговорке",
             "сплошной проход вертикальными лентами (3 из 3"
             in verdict.coverage_text({**ctx, "stripes": {"pages": 3, "of": 3}})),
            ("искажённое место («Мусскомъ») найдено, шум («Мирскому») нет",
             place_ctx["place_hits"] == {"Миусский округ": [1]}),
            ("заметка к документу без неразобранного отклоняется",
             any("разбирать нечего" in p for p in verdict.problems(ctx, "x"))),
            ("дореформенное в заметке ловится до записи",
             any("дореформенное" in p for p in verdict.problems(
                 {**ctx, "place_hits": {"Ровеньки": [2]}}, "стр. 2 — Ровенекъ"))),
            ("заметка привязывается к своему документу",
             verdict.parse_notes([f"{bad_doc}=стр. 1"], [ok_doc, bad_doc])
             == {bad_doc: "стр. 1"}),
            ("один документ — заметка без имени",
             verdict.parse_notes(["стр. 1"], [ok_doc]) == {ok_doc: "стр. 1"}),
            ("несколько документов — заметка без имени отклоняется",
             exits(verdict.parse_notes, ["стр. 1"], [ok_doc, bad_doc])),
            ("обрывок с чужим продолжением отсеивается",
             verdict.continuation_fits(
                 SimpleNamespace(raw="Мо-", context="село Мо- рушка, поле"),
                 "могучев", [], 3.0) is False),
            ("обрыв с настоящим продолжением остаётся",
             verdict.continuation_fits(
                 SimpleNamespace(raw="Мо-", context="казакъ Мо- гучевъ Иванъ"),
                 "могучев", [], 3.0) is True),
            ("нечитаемое продолжение — судить не по чему",
             verdict.continuation_fits(
                 SimpleNamespace(raw="Могу-", context="казакъ Могу- 4:65 и"),
                 "могучев", [], 3.0) is None),
            ("склонение: 1 страница, 2 страницы, 5 страниц",
             [verdict.plural(n, "а", "б", "в") for n in (1, 2, 5, 11, 22)]
             == ["а", "б", "в", "в", "б"]),
        ]

        written = verdict.record(ctx)
        rows = docstore.read_log(ok_doc)
        row = [r for r in rows if r["type"] == "verdict"][-1]
        errors, warnings = validate_verdict(row, 3, {})
        ev_errors, ev_warnings = validate_evidence(row["evidence"], 3, quality)
        drift = {**quality, "weak": [1]}
        checks += [
            ("вердикт записан со status absent",
             row["status"] == "absent" and row["verdict"] == written),
            ("вердикт проходит аудит", (errors, warnings) == ([], [])),
            ("evidence проходит аудит", (ev_errors, ev_warnings) == ([], [])),
            ("перед вердиктом записан поиск",
             [r["type"] for r in rows] == ["search", "verdict"]),
            ("расхождение с quality.json — предупреждение",
             bool(validate_evidence(row["evidence"], 3, drift)[1])),
            ("у не последнего вердикта расхождение не предупреждает",
             validate_evidence(row["evidence"], 3, drift, current=False)[1] == []),
            ("слой вне списка — ошибка",
             bool(validate_evidence({**row["evidence"],
                                     "layers": {"ocr_x": 1}}, 3)[0])),
            ("слабая страница вне тома — ошибка",
             bool(validate_evidence({**row["evidence"],
                                     "weak_pages": {"9": 40.0}}, 3)[0])),
            ("молчание о слабых страницах ловится",
             weak_pages_unmentioned(ok_doc, "НЕ НАЙДЕНА. Всё чисто.") == [1, 3]),
            ("названная страница снимает замечание",
             weak_pages_unmentioned(ok_doc, "НЕ НАЙДЕНА. Стр. 3 — шум.") == []),
            ("упоминание порога снимает замечание",
             weak_pages_unmentioned(ok_doc, "НЕ НАЙДЕНА, слабые страницы разобраны") == []),
            ("дата и номер выпуска не считаются названной страницей",
             weak_pages_unmentioned(
                 ok_doc, "НЕ НАЙДЕНА. Газета, 3 августа 1885, № 1.") == [1, 3]),
            ("полосы диапазоном: «полосы 2–3» называет стр. 3",
             weak_pages_unmentioned(ok_doc, "НЕ НАЙДЕНА. Полосы 2–3 — шум.") == []),
        ]
    finally:
        docstore.ROOT = old_root
        tmp.cleanup()
    bad = 0
    for label, ok in checks:
        bad += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {label}")
    return bad


if __name__ == "__main__":
    sys.exit(main())
