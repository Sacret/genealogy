#!/usr/bin/env python3
"""Структурированные факты о людях с точной ссылкой на источник.

Текстовая биография остаётся удобной для чтения, а events.jsonl хранит
машиночитаемый слой под ней: дату, тип события, человека и доказательство.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone

from docstore import ROOT, documents, latest_verdicts, load_meta, persons


EVENTS = ROOT / "events.jsonl"
DATE_RE = re.compile(r"^\d{4}(?:-\d{2}(?:-\d{2})?)?$")
CERTAINTIES = {"confirmed", "probable", "possible"}
BASES = {"explicit", "inference", "absence"}


def read_events(path=EVENTS):
    if not path.exists():
        return []
    out = []
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{no}: неверный JSON: {exc.msg}") from exc
    return out


def validate_event(event, roster=None, known_documents=None):
    """Возвращает список нарушений контракта одной записи."""
    errors = []
    required = ("id", "person", "date", "type", "description",
                "document", "page", "certainty", "basis")
    for key in required:
        if event.get(key) in (None, ""):
            errors.append(f"нет поля {key}")

    if event.get("id") and not re.fullmatch(r"e\d{4,}", str(event["id"])):
        errors.append("id должен иметь вид e0001")
    if event.get("date") and not DATE_RE.fullmatch(str(event["date"])):
        errors.append("date должен иметь вид YYYY, YYYY-MM или YYYY-MM-DD")
    if event.get("certainty") not in CERTAINTIES:
        errors.append("неизвестная certainty")
    if event.get("basis") not in BASES:
        errors.append("неизвестный basis")
    try:
        page = int(event.get("page"))
        if page < 1:
            errors.append("page должен быть положительным")
    except (TypeError, ValueError):
        errors.append("page должен быть целым числом")
        page = None

    if roster is not None and event.get("person") not in roster:
        errors.append(f"неизвестная персона {event.get('person')}")
    if known_documents is not None:
        meta = known_documents.get(event.get("document"))
        if meta is None:
            errors.append(f"неизвестный документ {event.get('document')}")
        elif page and meta.get("pages") and page > int(meta["pages"]):
            errors.append(f"страница {page} за пределами документа")
    return errors


def next_event_id(records):
    numbers = [int(r["id"][1:]) for r in records
               if re.fullmatch(r"e\d{4,}", str(r.get("id", "")))]
    return f"e{max(numbers, default=0) + 1:04d}"


def known_documents(records):
    return {r["document"]: load_meta(r["document"])
            for r in records if r.get("document")}


def linked_findings():
    """Источники, где вердикт уже связывает страницу с конкретным человеком."""
    out = set()
    for ident in documents():
        for verdict in latest_verdicts(ident).values():
            for page, person in (verdict.get("persons") or {}).items():
                out.add((ident, int(page), person))
    return out


def uncovered_findings(records, findings=None):
    """Персональные находки, для которых ещё нет ни одного события."""
    findings = linked_findings() if findings is None else set(findings)
    covered = {(r.get("document"), int(r.get("page", 0)), r.get("person"))
               for r in records if str(r.get("page", "")).isdigit()}
    return sorted(findings - covered)


def audit(records):
    roster = persons()
    docs = known_documents(records)
    errors = []
    seen = set()
    for no, event in enumerate(records, 1):
        for error in validate_event(event, roster, docs):
            errors.append(f"строка {no}, {event.get('id', '?')}: {error}")
        ident = event.get("id")
        if ident in seen:
            errors.append(f"строка {no}: повторный id {ident}")
        seen.add(ident)
    for document, page, person in uncovered_findings(records):
        errors.append(f"нет события для {person}: {document}, стр. {page}")
    return errors


def append_event(event, path=EVENTS):
    records = read_events(path)
    event = dict(event)
    event["id"] = next_event_id(records)
    event["recorded"] = datetime.now(timezone.utc).astimezone().isoformat(
        timespec="seconds")
    errors = validate_event(
        event, persons(), {event["document"]: load_meta(event["document"])})
    if errors:
        raise ValueError("; ".join(errors))
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="проверить весь реестр")
    check.set_defaults(action="check")

    listing = sub.add_parser("list", help="показать события")
    listing.add_argument("--person")
    listing.set_defaults(action="list")

    add = sub.add_parser("add", help="добавить подтверждённый факт")
    add.add_argument("--person", required=True)
    add.add_argument("--date", required=True)
    add.add_argument("--type", required=True)
    add.add_argument("--description", required=True)
    add.add_argument("--document", required=True)
    add.add_argument("--page", required=True, type=int)
    add.add_argument("--quote")
    add.add_argument("--place")
    add.add_argument("--role")
    add.add_argument("--certainty", choices=sorted(CERTAINTIES),
                     default="confirmed")
    add.add_argument("--basis", choices=sorted(BASES), default="explicit")
    add.set_defaults(action="add")
    args = parser.parse_args()

    try:
        records = read_events()
        if args.action == "check":
            errors = audit(records)
            if errors:
                print("\n".join(errors), file=sys.stderr)
                return 1
            print(f"события: {len(records)}, ошибок нет")
        elif args.action == "list":
            rows = [r for r in records
                    if not args.person or r.get("person") == args.person]
            roster = persons()
            for row in sorted(rows, key=lambda r: (r["date"], r["id"])):
                name = roster.get(row["person"], {}).get("имя", row["person"])
                print(f"{row['date']:10}  {name}: {row['description']} "
                      f"({row['document']}, стр. {row['page']})")
        else:
            values = vars(args).copy()
            values.pop("command")
            values.pop("action")
            event = append_event({k: v for k, v in values.items() if v is not None})
            print(f"добавлено {event['id']}")
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
