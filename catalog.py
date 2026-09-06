#!/usr/bin/env python3
"""Каталог библиотеки: что вообще есть, что просмотрено, что дальше.

    python3 catalog.py                    # добрать новые id и пересобрать documents.json
    python3 catalog.py --to 800           # раздвинуть каталог до bv0000800
    python3 catalog.py --range 500-800    # опросить только этот отрезок
    python3 catalog.py --recheck          # перезапросить id, которые не ответили
    python3 catalog.py --rebuild          # без сети: пересобрать из кэша

Заголовки кэшируются в `catalog.jsonl` и больше не перезапрашиваются:
опрос пятисот номеров занимает около получаса, а меняются они редко.
Формат — JSONL, как и у `searches.jsonl`, по той же причине: дописывание
переживает обрыв на середине, а при чтении побеждает последняя строка
про данный номер, так что `--recheck` не портит накопленное.

Раскладка по категориям — правилами ниже. Заголовок, не подошедший ни
под одно правило, не пропадает: он попадает в `нерассортированы` и
печатается по концу работы. Иначе расширение каталога тихо сваливало бы
всё новое в «не будут просмотрены».
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from docstore import ROOT, UA, latest_verdicts, load_meta, meta_year

CATALOG = ROOT / "catalog.jsonl"
DOCUMENTS = ROOT / "documents.json"
LIBRARY = "https://vivaldi.dspl.ru"
DEFAULT_CEILING = 500


# --- правила раскладки ------------------------------------------------
#
# Порядок проверки: приказы → отсев → казачество → Донской край → остаток.
# Отсев стоит раньше казачества нарочно: «Известия института в
# Новочеркасске» — это труды советского втуза, а не документ о городе.

ORDERS = re.compile(r"Приказы по войску Донскому", re.I)

SKIP = re.compile("|".join([
    r"экслибрис",
    r"Жизнь и подвиги графа Матвея Ивановича Платова",
    r"владычества русских на Кавказе",
    r"местностей и племен Кавказа",
    r"Описание Отечественной войны 1812",
    r"Книжное дело на Северном Кавказе",
    r"Кавказская война в отдельных очерках",
    r"Очерки истории Азова",
    r"Донские древности",
    r"Историко-археологические исследования",
    r"Сборник сведений о Северном Кавказе",
    r"Кавказский календарь",
    r"Елка\. Подарок на рождество",
    r"История ростовского радио",
    r"Краеведческие записки",
    r"Сообщения советского информбюро",
    r"Экологические проблемы",
    r"^Избранное",
    r"Царствующий Дом Романовых",
    r"Административно-территориальное деление Ростовской области",
    r"Известия Ростовского областного музея краеведения",
    r"Известия Северо-Кавказского индустриального института",
    r"истории армянского народа",
    r"Инструкция библиотекарям",
    r"Инструкция для агентов по коммерческой части",
    r"Очерк сети русских железных дорог",
    r"Трагедия русской армии",
    r"Военные усилия России в мировой войне",
    r"Общий устав Российских железных дорог",
    r"^Война: хроника и отклики",
    # Отрезок bv0000525-595: железные дороги, Кавказ, война и беллетристика.
    # Начала названий здесь нарочно узкие — SKIP спрашивается раньше
    # COSSACK, и широкое «История» или «Отчет» унесло бы из очереди
    # войсковые отчёты.
    r"^Драмы",
    r"^История: Т\.",
    r"Мифы классической древности",
    r"Экономическое обследование железнодорожных линий",
    r"Владикавказской (железной дороги|жел\. дор\.)",
    r"Расчеты воздействия объектов транспорта",
    r"деятельности Русских железных дорог",
    r"Акты собранные Кавказской Археографической комиссией",
    r"государь император Николай Александрович",
    r"славы Кубанцев",
    r"Законодательные акты, вызванные войною",
    r"Пути сообщения на театре войны",
]), re.I)

COSSACK = re.compile("|".join([
    r"по казачьим войскам",
    r"Всевеликого [Вв]ойска Донского",
    r"Памятная книжка",
    r"Вся Область войска Донского",
    r"Вся Донская область",
    r"Донско(й|-Азовский) календарь",
    r"Новочеркасск",
    r"наказного атамана",
    r"войска Донского по переписи",
    r"населенных мест области войска Донского",
    r"Статистика землевладения",
    r"Донской архив",
    r"истории Войска Донского",
    r"^Донские дела",
    r"Донская церковная старина",
    r"Донского Войскового статистического комитета",
    r"История (Донского войска|войска Донского)",
    r"^Донцы",
    r"Донского епархиального училищного совета",
    r"движении раскола",
    r"по воинской повинности",
    r"Первого Донского окружного земства",
    r"управления казаков",
]), re.I)

# Внутри второй очереди вперёд идёт то, где есть готовые списки имён:
# домовладельцы, чины, жители. Нормативные сборники и отчёты — следом.
SPRAVOCHNIK = re.compile("|".join([
    r"Памятная книжка",
    r"Вся Область войска Донского",
    r"Вся Донская область",
    r"Донско(й|-Азовский) календарь",
    r"^Новочеркасск: справочная книжка",
    r"населенных мест области войска Донского",
    r"войска Донского по переписи",
    r"Донской архив",
    r"Статистика землевладения",
]), re.I)

REGION = re.compile(r"Ростов|Таганрог|Нахичеван|Азов|Дон|животноводства", re.I)

# Проверено вне этого конвейера — заново не берём. Причина попадает в
# documents.json рядом с документом: без неё том лежал бы среди
# отсеянных по теме, и через полгода было бы не понять, почему
# «Памятная книжка» оказалась ненужной.
#
# Яндекс.Архив выложил «Памятную книжку Области Войска Донского» за
# 1866-1916 и ищет по ней сам. Годы перечислены поимённо, а не взяты
# диапазоном: в подшивке дыры (1870, 1872, 1882-1884, 1886, 1889, 1899,
# 1902), и книжка за такой год, попадись она в библиотеке, проверена
# не была бы.
YANDEX_PAMYATNYE_URL = ("https://yandex.ru/archive/catalog/"
                        "13a71db7-f648-4858-ae27-e421af4130ba/books")
YANDEX_PAMYATNYE_YEARS = {
    1866, 1867, 1868, 1869, 1871, 1873, 1874, 1875, 1876, 1877, 1878,
    1879, 1880, 1881, 1885, 1887, 1888, 1890, 1891, 1892, 1893, 1895,
    1896, 1897, 1898, 1900, 1901, 1903, 1904, 1905, 1906, 1907, 1908,
    1909, 1910, 1911, 1912, 1913, 1914, 1915, 1916,
}
PAMYATNAYA = re.compile(r"Памятная книжка.*[Вв]ойска Донского", re.I)

# Пролистано глазами во вьюере библиотеки: в книге одни таблицы, личных
# имён нет вовсе, и качать восемьсот страниц ради отрицательного ответа
# незачем. Отсев здесь по номеру, а не по заголовку: соседние книги той
# же переписи различаются одной цифрой в названии, а заранее сказать,
# какая из них поимённая, нельзя — это видно только в самой книге.
# Причина пишется рядом с документом в `documents.json`, иначе том лежал
# бы среди отсеянных по теме и через полгода было бы не понять, чем
# перепись не подошла.
NO_NAMES = {
    "bv0000260": "просмотрено вручную: сводные таблицы переписи, имён нет",
    "bv0000261": "просмотрено вручную: сводные таблицы переписи, имён нет",
    "bv0000262": "просмотрено вручную: сводные таблицы переписи, имён нет",
    "bv0000263": "просмотрено вручную: сводные таблицы переписи, имён нет",
    "bv0000264": "просмотрено вручную: таблицы поселений, "
                 "жители только счётом",
    "bv0000265": "просмотрено вручную: таблицы поселений, "
                 "жители только счётом",
    "bv0000477": "просмотрено вручную: сводные таблицы землевладения, "
                 "владельцы только счётом",
}

# Скан снят плохо: том брать стоит, но отрицательный ответ поиска по нему
# будет стоить немного, поэтому в своей очереди он идёт последним — после
# тех, где распознавание отработает в полную силу. Причина пишется рядом
# с документом в `documents.json`: иначе место в хвосте очереди выглядело
# бы случайностью, а не решением.
POOR_SCAN = {
    "ot0000011": "плохое качество скана",
    "ot0000013": "плохое качество скана",
}

QUEUES = ("1_приказы_по_войску_донскому",
          "2_казачество_войско_донское_новочеркасск",
          "3_донской_край_прочее")


# --- кэш заголовков ---------------------------------------------------

def ident_of(n: int) -> str:
    return "bv%07d" % n


def number_of(ident: str) -> int:
    return int(ident[2:])


def load_catalog() -> dict:
    """id → запись. Побеждает последняя строка: --recheck дописывает."""
    out = {}
    if CATALOG.exists():
        for line in CATALOG.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                out[rec["id"]] = rec
    return out


def append(records: list) -> None:
    if not records:
        return
    with CATALOG.open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def probe(ident: str, timeout: float, retries: int) -> dict:
    """Спросить у вьюера заголовок документа.

    Отсутствующий номер отвечает 404 или 500 — это ответ, и повторять
    его незачем. А вот сорванное соединение повторить стоит: на длинном
    прогоне такие осечки попадаются регулярно. Совсем глухие номера тоже
    есть (bv0000000 не отвечает и за минуту) — они запоминаются как
    «нет ответа» и переспрашиваются только по `--recheck`.
    """
    url = f"{LIBRARY}/{ident}/view/"
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    reason = "нет ответа"
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                html = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 500):
                return {"id": ident, "title": None, "reason": str(e.code),
                        "date": now}
            reason = f"HTTP {e.code}"
        except Exception as e:                       # обрыв, таймаут, TLS
            reason = type(e).__name__
        else:
            m = re.search(r'<app-root[^>]*\bdata-title="([^"]*)"', html)
            if m:
                return {"id": ident, "title": m.group(1).strip(), "date": now}
            m = re.search(r"<title>(.*?)</title>", html, re.S)
            title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
            return {"id": ident, "title": title or None,
                    "reason": None if title else "без заголовка", "date": now}
    return {"id": ident, "title": None, "reason": reason, "date": now}


def scan(idents: list, workers: int, timeout: float, retries: int) -> dict:
    """Опросить номера, дописывая кэш по ходу дела."""
    got, batch = {}, []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, rec in enumerate(ex.map(
                lambda x: probe(x, timeout, retries), idents), 1):
            got[rec["id"]] = rec
            batch.append(rec)
            if len(batch) >= 20:
                append(batch)
                batch = []
            if i % 25 == 0 or i == len(idents):
                print(f"  {i}/{len(idents)}  {rec['id']}  "
                      f"{rec['title'] or '— ' + str(rec.get('reason'))}",
                      flush=True)
    append(batch)
    return got


# --- раскладка --------------------------------------------------------

def year(title: str):
    m = re.search(r"(?:за|на)\s*\[?(\d{4})", title)
    return int(m.group(1)) if m else None


def years_covered(title: str) -> list:
    """Годы, которые том закрывает: «на 1893-1894 год» — это два года.

    `year` берёт первый и тем задаёт место документа в списке, а здесь
    важно другое: назвать 1894-й ненайденным было бы неправдой, раз он
    напечатан под одной обложкой с 1893-м. Разворачивается только
    короткий промежуток: «на 1866-1916» в заглавии подшивки — это не том
    за полвека, а описание серии.
    """
    m = re.search(r"(?:за|на)\s*\[?(\d{4})(?:\s*[-–—]\s*(\d{4}))?", title)
    if not m:
        return []
    lo = int(m.group(1))
    hi = int(m.group(2) or lo)
    return list(range(lo, hi + 1)) if lo <= hi <= lo + 5 else [lo]


def checked_elsewhere(title: str):
    """Проверено помимо этого конвейера — и чем именно."""
    if PAMYATNAYA.search(title) and year(title) in YANDEX_PAMYATNYE_YEARS:
        return f"проверена поиском Яндекс.Архива: {YANDEX_PAMYATNYE_URL}"
    return None


def classify(title: str) -> str:
    """Куда положить документ с таким заголовком.

    Порядок правил — единственное, что здесь неочевидно, поэтому решение
    вынесено отдельно. Первым спрашивается `checked_elsewhere`: уже
    проверенное чужим поиском не должно попасть даже в приказы, какой бы
    интересной ни была тема. Дальше `SKIP` спрашивается раньше `COSSACK`, иначе
    «Известия Северо-Кавказского индустриального института в
    Новочеркасске» попали бы в очередь по слову «Новочеркасск».
    Обратная сторона такого порядка — в `SKIP` нельзя класть широкие
    начала названий: «Свод законов Российской Империи» разложен по
    томам, и том об управлении казаков нужен, а том об уставе железных
    дорог нет.
    """
    if checked_elsewhere(title):
        return "не_будут_просмотрены"
    if ORDERS.search(title):
        return "1_приказы_по_войску_донскому"
    if SKIP.search(title):
        return "не_будут_просмотрены"
    if COSSACK.search(title):
        return "2_казачество_войско_донское_новочеркасск"
    if REGION.search(title):
        return "3_донской_край_прочее"
    return "нерассортированы"


def build(catalog: dict) -> dict:
    """Разложить кэш по категориям.

    Просмотренность берётся не из наличия папки, а из вердиктов в
    `searches.jsonl`: скачанный и распознанный том без вердикта — это
    работа на середине, и в очереди он нужнее, чем в отчёте о сделанном.
    """
    out = {
        "источник": f"{LIBRARY}/ (ДГПБ, Vivaldi)",
        "диапазон": "",
        "собрано": datetime.now().date().isoformat(),
        "просмотрены": [],
        "в_работе": [],
        "очередь": {name: [] for name in QUEUES},
        "не_будут_просмотрены": [],
        "нерассортированы": [],
        "нет_документа": [],
    }

    for ident, cached in sorted(catalog.items()):
        title = cached.get("title")
        if not title:
            out["нет_документа"].append(
                {"id": ident, "причина": cached.get("reason")})
            continue

        rec = {"id": ident, "url": f"{LIBRARY}/{ident}/view/", "title": title}
        # У скачанного тома год мог быть проставлен руками в meta.json —
        # заголовки вроде «Новочеркасск: справочная книжка» о нём молчат,
        # и без этого документ выпадал бы из хронологии и здесь, и в журнале.
        meta = load_meta(ident)
        y = meta_year({**meta, "title": title})
        if y:
            rec["год"] = y
        if ident in POOR_SCAN:
            rec["качество"] = POOR_SCAN[ident]

        verdicts = latest_verdicts(ident) if meta else {}
        if verdicts:
            rec["вердикты"] = {s: v.get("status", "unclear")
                               for s, v in verdicts.items()}
            out["просмотрены"].append(rec)
            continue
        if meta:
            out["в_работе"].append(rec)
            continue

        bucket = classify(title)
        why = checked_elsewhere(title)
        # Просмотренное руками сильнее любого тематического правила:
        # заголовок обещает списки жителей, а в книге их нет.
        if ident in NO_NAMES:
            bucket, why = "не_будут_просмотрены", NO_NAMES[ident]
        if why:
            rec["причина"] = why
        if bucket == "2_казачество_войско_донское_новочеркасск":
            rec["подгруппа"] = ("адрес-календари и справочники"
                                if SPRAVOCHNIK.search(title)
                                else "войсковые документы и отчёты")
        if bucket in out["очередь"]:
            out["очередь"][bucket].append(rec)
        else:
            out[bucket].append(rec)

    q2 = out["очередь"]["2_казачество_войско_донское_новочеркасск"]
    q2.sort(key=lambda r: (r["подгруппа"] != "адрес-календари и справочники",
                           r["id"]))

    # Плохие сканы — в конец своей очереди. Сортировка устойчива, так что
    # порядок остальных, включая подгруппы второй очереди, не меняется.
    for items in out["очередь"].values():
        items.sort(key=lambda r: r["id"] in POOR_SCAN)

    # Диапазон — про сплошной опрос bv-номеров; тома с другим префиксом
    # приходят по прямой ссылке и границ опроса не двигают.
    numbers = sorted(number_of(i) for i in catalog if i.startswith("bv"))
    if numbers:
        out["диапазон"] = f"{ident_of(numbers[0])} — {ident_of(numbers[-1])}"
    # Номера могли опрашиваться отрезками, так что границы ещё не значат,
    # что между ними спрошено всё: сколько именно — говорит это число.
    out["опрошено"] = len(catalog)

    done = sorted(y for y in (r.get("год") for r in out["просмотрены"]
                              if ORDERS.search(r["title"])) if y)
    todo = sorted(r["год"] for r in out["очередь"]["1_приказы_по_войску_донскому"]
                  if "год" in r)
    span = range(min(done + todo), max(done + todo) + 1) if done or todo else []
    out["приказы_по_годам"] = {
        "просмотрены": done,
        "в_очереди": todo,
        "не_найдены_в_каталоге": [y for y in span
                                  if y not in done and y not in todo],
    }

    # Памятные книжки — вторая сплошная серия после приказов, и вопрос к
    # ней тот же: какие годы закрыты, какие нет. Но считается она иначе.
    # Большую часть подшивки закрывает поиск Яндекс.Архива, и такие тома
    # сюда не берутся вовсе (`checked_elsewhere`), так что «нет в очереди»
    # для них означает «уже проверено», а не «пропущено». Поэтому годы
    # разложены по источникам: библиотека ДГПБ отвечает на вопрос «какие
    # есть», а последний разряд — на вопрос «каких не хватает», и в нём
    # остаются годы, которых нет ни там, ни там.
    have, seen, queued, elsewhere = set(), set(), set(), set()
    for ident, cached in sorted(catalog.items()):
        title = cached.get("title") or ""
        if not PAMYATNAYA.search(title):
            continue
        ys = years_covered(title)
        have.update(ys)
        if load_meta(ident) and latest_verdicts(ident):
            seen.update(ys)
        elif checked_elsewhere(title):
            # Том закрыт Яндексом целиком, вместе со вторым годом под той
            # же обложкой: список годов там составлен по заглавиям, и
            # «1893» в нём — это та же книжка «на 1893-1894 год».
            elsewhere.update(ys)
        else:
            queued.update(ys)
    known = have | YANDEX_PAMYATNYE_YEARS
    span = range(min(known), max(known) + 1) if known else []
    checked = {y for y in span
               if y in YANDEX_PAMYATNYE_YEARS or y in elsewhere} - seen - queued
    out["памятные_книжки_по_годам"] = {
        "есть_в_библиотеке": sorted(have),
        "просмотрены": sorted(seen),
        "в_очереди": sorted(queued),
        "проверены_яндекс_архивом": sorted(checked),
        "нет_нигде": [y for y in span if y not in seen | queued | checked],
    }
    return out


def write(out: dict) -> str:
    DOCUMENTS.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    return DOCUMENTS.name


def refresh(ident: str = None) -> str:
    """Пересобрать `documents.json` по кэшу, не ходя в сеть.

    Зовётся из `find.py` после каждого поиска и вердикта — по тем же
    причинам, что и `journal.rebuild()`: список, который обновляют
    руками, назавтра врёт. Вердикт переводит документ из очереди в
    просмотренные, поиск без вердикта — в «в работе».

    Если документа ещё нет в каталоге (том взяли по прямой ссылке, за
    пределами опрошенного отрезка), заголовок берётся из его `meta.json`
    и дописывается в кэш. Сети для этого не нужно, а иначе просмотренный
    том не попал бы в список вовсе: `build` перебирает каталог, а не
    папки в корне.
    """
    catalog = load_catalog()
    if ident and ident not in catalog:
        title = load_meta(ident).get("title")
        if title:
            rec = {"id": ident, "title": title,
                   "date": datetime.now(timezone.utc).astimezone()
                                   .isoformat(timespec="seconds")}
            append([rec])
            catalog[ident] = rec
    return write(build(catalog))


# --- команда ----------------------------------------------------------

def parse_range(spec: str):
    a, _, b = spec.partition("-")
    return int(a), int(b or a)


def main():
    ap = argparse.ArgumentParser(
        description="Каталог документов библиотеки и очередь на просмотр")
    ap.add_argument("--range", metavar="A-B",
                    help="какие номера опросить, например 500-800")
    ap.add_argument("--to", type=int, metavar="N",
                    help="раздвинуть каталог до bv00000NN включительно")
    ap.add_argument("--recheck", action="store_true",
                    help="перезапросить номера, записанные как отсутствующие")
    ap.add_argument("--rebuild", action="store_true",
                    help="не ходить в сеть, только пересобрать documents.json")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=45)
    ap.add_argument("--retries", type=int, default=3)
    a = ap.parse_args()

    catalog = load_catalog()
    # Только bv: у документов с другим префиксом своя нумерация, и
    # bv0000011 не становится опрошенным оттого, что есть ot0000011.
    known = {number_of(i) for i in catalog if i.startswith("bv")}

    if not a.rebuild:
        if a.range:
            lo, hi = parse_range(a.range)
        else:
            lo, hi = 0, max([DEFAULT_CEILING, *known])
        if a.to is not None:
            hi = max(hi, a.to)

        todo = [ident_of(n) for n in range(lo, hi + 1)
                if n not in known
                or (a.recheck and not catalog[ident_of(n)].get("title"))]
        if todo:
            print(f"опрашиваю {len(todo)} номеров "
                  f"({ident_of(lo)} — {ident_of(hi)})", flush=True)
            catalog.update(scan(todo, a.workers, a.timeout, a.retries))
        else:
            print(f"новых номеров в {ident_of(lo)} — {ident_of(hi)} нет")

    out = build(catalog)
    write(out)

    rows = [("просмотрены", len(out["просмотрены"]))]
    if out["в_работе"]:
        rows.append(("в работе", len(out["в_работе"])))
    rows += [(name, len(items)) for name, items in out["очередь"].items()]
    rows += [("не будут просмотрены", len(out["не_будут_просмотрены"])),
             ("нет документа", len(out["нет_документа"]))]
    width = max(len(name) for name, _ in rows)

    print(f"\n{DOCUMENTS.name}: {out['диапазон']}")
    for name, count in rows:
        print(f"  {name:<{width}}  {count:4d}")
    if out["в_работе"]:
        print("  в работе: " + ", ".join(r["id"] for r in out["в_работе"]))

    orders = out["приказы_по_годам"]
    if orders["в_очереди"]:
        print("\nприказы, которых ещё не было:",
              ", ".join(str(y) for y in orders["в_очереди"]))

    if out["нерассортированы"]:
        print(f"\nне подошли ни под одно правило — допишите их в catalog.py "
              f"({len(out['нерассортированы'])}):")
        for rec in out["нерассортированы"]:
            print(f"  {rec['id']}  {rec['title'][:90]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
