#!/usr/bin/env python3
"""Проверка целостности всей накопленной исследовательской базы.

Команда ничего не исправляет и не обращается в сеть. Ошибки означают
нарушенный контракт данных и дают ненулевой код возврата. Предупреждения —
допустимые старые или незавершённые состояния; с ``--strict`` они тоже
считаются ошибками.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from events import uncovered_findings, validate_event


ROOT = Path(__file__).parent
DOC_ID = re.compile(r"^(?:bv|ot|pn)\d{7}$")
PERSON_ID = re.compile(r"^i\d{4,}$")
PAGE_FILE = re.compile(r"^p(\d+)\.(?:txt|jpg)$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}T")
STATUSES = {"found", "absent", "unclear", "pending"}


@dataclass(frozen=True)
class Issue:
    level: str
    path: str
    message: str

    def __str__(self):
        label = "ОШИБКА" if self.level == "error" else "ВНИМАНИЕ"
        return f"{label}: {self.path}: {self.message}"


class Report:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.issues = []
        self.stats = {}

    def add(self, level, path, message):
        try:
            shown = str(Path(path).relative_to(self.root))
        except ValueError:
            shown = str(path)
        self.issues.append(Issue(level, shown, message))

    def error(self, path, message):
        self.add("error", path, message)

    def warn(self, path, message):
        self.add("warning", path, message)

    @property
    def errors(self):
        return [item for item in self.issues if item.level == "error"]

    @property
    def warnings(self):
        return [item for item in self.issues if item.level == "warning"]


def read_json(path, report, expected=dict):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        report.error(path, "файл отсутствует")
        return None
    except (OSError, json.JSONDecodeError) as exc:
        report.error(path, f"не читается как JSON: {exc}")
        return None
    if not isinstance(value, expected):
        report.error(path, f"ожидался {expected.__name__}, получен "
                     f"{type(value).__name__}")
        return None
    return value


def read_jsonl(path, report):
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        report.error(path, f"не читается: {exc}")
        return rows
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            report.error(path, f"строка {number}: неверный JSON: {exc.msg}")
            continue
        if not isinstance(row, dict):
            report.error(path, f"строка {number}: запись должна быть объектом")
            continue
        rows.append((number, row))
    return rows


def required(record, fields):
    return [field for field in fields if record.get(field) in (None, "")]


def page_set(values, total, label):
    errors, pages = [], set()
    if not isinstance(values, list):
        return set(), [f"{label} должен быть списком"]
    for value in values:
        try:
            page = int(value)
        except (TypeError, ValueError):
            errors.append(f"{label}: неверная страница {value!r}")
            continue
        if str(value).strip() != str(page):
            errors.append(f"{label}: неверная страница {value!r}")
        elif not 1 <= page <= total:
            errors.append(f"{label}: страница {page} вне диапазона 1..{total}")
        elif page in pages:
            errors.append(f"{label}: страница {page} повторяется")
        pages.add(page)
    return pages, errors


def validate_meta(meta, ident):
    errors = []
    for field in required(meta, ("url", "title", "pages", "dpi")):
        errors.append(f"нет поля {field}")
    for field in ("pages", "dpi"):
        value = meta.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            errors.append(f"{field} должен быть положительным целым числом")
    url = meta.get("url")
    if isinstance(url, str) and url.rstrip("/").rsplit("/", 1)[-1] != ident:
        errors.append(f"URL не соответствует идентификатору {ident}")
    if "год" in meta and (not isinstance(meta["год"], int)
                           or not 1600 <= meta["год"] <= 2100):
        errors.append("год должен быть целым числом от 1600 до 2100")
    kept = meta.get("kept_pages")
    if kept is not None and isinstance(meta.get("pages"), int):
        _, page_errors = page_set(kept, meta["pages"], "kept_pages")
        errors.extend(page_errors)
    elif kept is not None:
        errors.append("kept_pages нельзя проверить без правильного pages")
    if meta.get("pruned") is True and kept is None:
        errors.append("у очищенного документа нет kept_pages")
    return errors


def validate_quality(data, total):
    errors = []
    threshold = data.get("threshold")
    scores = data.get("pages")
    weak = data.get("weak")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        errors.append("threshold должен быть числом")
    if not isinstance(scores, dict):
        errors.append("pages должен быть объектом")
        return errors
    weak_pages, page_errors = page_set(weak, total, "weak")
    errors.extend(page_errors)
    seen = set()
    for key, score in scores.items():
        try:
            page = int(key)
        except (TypeError, ValueError):
            errors.append(f"pages: неверный номер {key!r}")
            continue
        if str(page) != str(key) or not 1 <= page <= total:
            errors.append(f"pages: страница {key!r} вне диапазона 1..{total}")
        seen.add(page)
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            errors.append(f"pages[{key!r}] должен быть числом")
        elif isinstance(threshold, (int, float)):
            # score хранится с одним знаком. Ровно на пороге исходное
            # неокруглённое значение могло лежать с любой его стороны.
            if score < threshold and page not in weak_pages:
                errors.append(f"страница {page} ниже порога, но её нет в weak")
            if score > threshold and page in weak_pages:
                errors.append(f"страница {page} выше порога, но есть в weak")
    missing = set(range(1, total + 1)) - seen
    if missing:
        errors.append(f"нет оценок для {len(missing)} стр. "
                      f"(первая: {min(missing)})")
    return errors


def validate_verdict(row, total, people, current=True):
    errors, warnings = [], []
    for field in required(row, ("date", "surname", "verdict")):
        errors.append(f"нет поля {field}")
    status = row.get("status")
    if status is None and not current:
        warnings.append("старый вердикт без явного поля status")
    elif status is None:
        errors.append("нет поля status")
    elif status not in STATUSES:
        errors.append(f"неизвестный status {status!r}")
    confirmed, bad = page_set(row.get("confirmed", []), total, "confirmed")
    errors.extend(bad)
    kin, bad = page_set(row.get("kin", []), total, "kin")
    errors.extend(bad)
    if not kin <= confirmed:
        errors.append("kin должен быть подмножеством confirmed")
    persons = row.get("persons") or {}
    if not isinstance(persons, dict):
        errors.append("persons должен быть объектом")
        persons = {}
    person_pages = set()
    for value, pid in persons.items():
        pages, bad = page_set([value], total, "persons")
        errors.extend(bad)
        person_pages.update(pages)
        if pid not in people:
            errors.append(f"неизвестная персона {pid!r}")
    if not person_pages <= kin:
        errors.append("страницы persons должны входить в kin")
    if status is not None and status != "found" and confirmed:
        errors.append("confirmed допустим только при status=found")
    if status == "found" and "confirmed" not in row:
        warnings.append("старый вердикт found без явного поля confirmed")
    return errors, warnings


def validate_search(row, total):
    errors = []
    fields = ("date", "surname", "stem", "threshold", "document", "url",
              "pages_total", "hits", "exact", "pages_with_hits")
    for field in required(row, fields):
        # Нулевые hits/exact и пустой список страниц — правильные значения.
        if field not in row or row[field] is None or row[field] == "":
            errors.append(f"нет поля {field}")
    pages, bad = page_set(row.get("pages_with_hits", []), total,
                          "pages_with_hits")
    errors.extend(bad)
    for field in ("hits", "exact"):
        value = row.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"{field} должен быть неотрицательным целым числом")
    if (isinstance(row.get("hits"), int) and isinstance(row.get("exact"), int)
            and row["exact"] > row["hits"]):
        errors.append("exact не может быть больше hits")
    if row.get("pages_total") != total:
        errors.append(f"pages_total должен быть равен {total}")
    if row.get("hits") == 0 and pages:
        errors.append("при hits=0 список pages_with_hits должен быть пуст")
    return errors


def validate_box(name, item, total):
    errors = []
    if not isinstance(item, dict):
        return ["запись должна быть объектом"]
    page, box, size = item.get("page"), item.get("box"), item.get("size")
    if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= total:
        errors.append(f"page должен лежать в диапазоне 1..{total}")
    match = re.match(r"^p(\d+)_.*\.png$", name)
    if not match:
        errors.append("имя вырезки должно иметь вид pNNNN_слово_N.png")
    elif isinstance(page, int) and int(match.group(1)) != page:
        errors.append("страница в имени не совпадает с полем page")
    numbers = (isinstance(box, list) and len(box) == 4
               and isinstance(size, list) and len(size) == 2
               and all(isinstance(n, int) and not isinstance(n, bool)
                       for n in box + size))
    if not numbers:
        errors.append("box и size должны быть списками из 4 и 2 целых чисел")
    elif not (size[0] > 0 and size[1] > 0
              and 0 <= box[0] < box[2] <= size[0]
              and 0 <= box[1] < box[3] <= size[1]):
        errors.append("box должен быть непустым прямоугольником внутри size")
    return errors


def validate_date(value):
    if not isinstance(value, str) or not DATE.match(value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def file_pages(folder, suffix):
    pages = set()
    for path in folder.glob(f"p*.{suffix}"):
        match = PAGE_FILE.match(path.name)
        if match:
            pages.add(int(match.group(1)))
    return pages


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def audit_project(root=ROOT):
    report = Report(root)
    root = report.root
    docs = {}
    for path in sorted(root.glob("*/meta.json")):
        ident = path.parent.name
        meta = read_json(path, report)
        if meta is None:
            continue
        docs[ident] = meta
        if not DOC_ID.fullmatch(ident):
            report.error(path, "имя каталога не похоже на ID документа")
        for message in validate_meta(meta, ident):
            report.error(path, message)

    people_file = root / "persons.json"
    people_data = read_json(people_file, report) or {}
    people = people_data.get("люди", {})
    if not isinstance(people, dict):
        report.error(people_file, "поле люди должно быть объектом")
        people = {}
    for pid, person in people.items():
        if not PERSON_ID.fullmatch(pid):
            report.error(people_file, f"неверный идентификатор персоны {pid!r}")
        if not isinstance(person, dict):
            report.error(people_file, f"персона {pid} должна быть объектом")
            continue
        for field in required(person, ("имя", "url")):
            report.error(people_file, f"у {pid} нет поля {field}")
        if isinstance(person.get("url"), str) and not person["url"].rstrip("/").endswith(pid):
            report.error(people_file, f"URL персоны {pid} не заканчивается её ID")

    # Общие JSON-файлы тоже входят в аудит, даже когда их внутренняя схема
    # свободнее и пока ограничивается объектом верхнего уровня.
    shared = {"places.json": None, "documents.json": None}
    for name in shared:
        shared[name] = read_json(root / name, report)

    catalog_path = root / "catalog.jsonl"
    catalog_rows = read_jsonl(catalog_path, report)
    catalog_ids = set()
    for number, row in catalog_rows:
        where = f"{catalog_path.relative_to(root)}:{number}"
        ident = row.get("id")
        if not isinstance(ident, str) or not DOC_ID.fullmatch(ident):
            report.error(where, f"неверный id {ident!r}")
        elif ident in catalog_ids:
            report.error(where, f"повторный id {ident}")
        catalog_ids.add(ident)
        if row.get("title") is not None and not isinstance(row.get("title"), str):
            report.error(where, "title должен быть строкой или null")
        if not row.get("title") and not row.get("reason"):
            report.error(where, "у записи без title должна быть причина")
        if not validate_date(row.get("date")):
            report.error(where, "date должна быть корректной датой ISO 8601")

    latest_findings = set()
    log_rows = 0
    for ident, meta in docs.items():
        folder, total = root / ident, meta.get("pages")
        if not isinstance(total, int) or total < 1:
            continue
        expected = set(range(1, total + 1))
        ocr = file_pages(folder / "ocr", "txt")
        if ocr != expected:
            missing, extra = expected - ocr, ocr - expected
            detail = []
            if missing:
                detail.append(f"нет {len(missing)} стр. (первая: {min(missing)})")
            if extra:
                detail.append(f"лишних {len(extra)} стр. (первая: {min(extra)})")
            report.error(folder / "ocr", "; ".join(detail))

        quality_path = folder / "quality.json"
        if quality_path.exists():
            quality = read_json(quality_path, report)
            if quality is not None:
                for message in validate_quality(quality, total):
                    report.error(quality_path, message)
        else:
            report.warn(quality_path, "надёжность OCR не измерена")

        log_path = folder / "searches.jsonl"
        rows = read_jsonl(log_path, report) if log_path.exists() else []
        log_rows += len(rows)
        latest = {}
        searches = set()
        for number, row in rows:
            if row.get("type") == "verdict":
                latest[row.get("surname")] = (number, row)
        for number, row in rows:
            where = f"{log_path.relative_to(root)}:{number}"
            kind = row.get("type", "search")
            if kind == "search":
                for message in validate_search(row, total):
                    report.error(where, message)
                searches.add(row.get("surname"))
            elif kind == "verdict":
                current = latest.get(row.get("surname"), (None,))[0] == number
                errors, warnings = validate_verdict(row, total, people, current)
                for message in errors:
                    report.error(where, message)
                for message in warnings:
                    report.warn(where, message)
            else:
                report.error(where, f"неизвестный type {kind!r}")
            if row.get("date") and not validate_date(row["date"]):
                report.error(where, "date должна быть корректной датой ISO 8601")
        for surname, (_, row) in latest.items():
            if surname not in searches:
                report.error(log_path, f"для вердикта {surname!r} нет поиска")
            if row.get("status") != "found":
                continue
            for page in row.get("confirmed", []):
                try:
                    page = int(page)
                except (TypeError, ValueError):
                    continue
                scan = folder / "scans" / f"p{page:04d}.jpg"
                if not scan.exists():
                    report.error(scan, "нет сохранённой страницы находки")
                crops = list((folder / "crops").glob(f"p{page:04d}_*.png"))
                if not crops:
                    report.error(folder / "crops",
                                 f"нет вырезки подтверждённой находки, стр. {page}")
            for page, pid in (row.get("persons") or {}).items():
                try:
                    latest_findings.add((ident, int(page), pid))
                except (TypeError, ValueError):
                    pass

        boxes_path = folder / "crops" / "boxes.json"
        if boxes_path.exists():
            boxes = read_json(boxes_path, report)
            if boxes is not None:
                for name, item in boxes.items():
                    for message in validate_box(name, item, total):
                        report.error(boxes_path, f"{name}: {message}")
                    if not (boxes_path.parent / name).exists():
                        report.error(boxes_path, f"{name}: файла вырезки нет")

    # Ссылки на дела в биографиях и географическом реестре должны вести
    # в реально присутствующую часть корпуса. documents.json — каталог и
    # очередь, поэтому его ID, напротив, не обязаны быть скачаны.
    known = set(docs)
    for path, data in ((people_file, people_data),
                       (root / "places.json", shared["places.json"])):
        if data is None:
            continue
        refs = {match.group() for text in strings(data)
                for match in re.finditer(r"\b(?:bv|ot|pn)\d{7}\b", text)}
        for ident in sorted(refs - known):
            report.error(path, f"ссылка на неизвестный документ {ident}")

    events_path = root / "events.jsonl"
    event_rows = read_jsonl(events_path, report) if events_path.exists() else []
    events, event_ids = [], set()
    for number, event in event_rows:
        events.append(event)
        where = f"{events_path.relative_to(root)}:{number}"
        for message in validate_event(event, people, docs):
            report.error(where, message)
        if event.get("id") in event_ids:
            report.error(where, f"повторный id {event.get('id')}")
        event_ids.add(event.get("id"))
    # Передаём уже вычисленный набор: так аудит работает и с произвольным
    # временным root в тестах, не обращаясь к глобальному docstore.ROOT.
    for ident, page, pid in uncovered_findings(events, latest_findings):
        report.error(events_path, f"нет события для {pid}: {ident}, стр. {page}")

    report.stats = {"documents": len(docs), "catalog": len(catalog_rows),
                    "log_records": log_rows,
                    "people": len(people), "events": len(events)}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true",
                        help="считать предупреждения ошибками")
    args = parser.parse_args(argv)
    report = audit_project()
    for issue in report.issues:
        print(issue, file=sys.stderr if issue.level == "error" else sys.stdout)
    stats = report.stats
    print("проверено: "
          f"{stats['documents']} документов, {stats['catalog']} записей каталога, "
          f"{stats['log_records']} записей журнала, "
          f"{stats['people']} персон, {stats['events']} событий")
    print(f"ошибок: {len(report.errors)}, предупреждений: {len(report.warnings)}")
    return 1 if report.errors or (args.strict and report.warnings) else 0


if __name__ == "__main__":
    sys.exit(main())
