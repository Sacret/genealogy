#!/usr/bin/env python3
"""Реестр однофамильцев и проверяемых гипотез об их тождестве.

Постоянные идентификаторы ``i...`` принадлежат родословной, временные
``u-...`` — людям, чьё место в ней пока не установлено. Один временный ID
собирает бесспорно принадлежащие одному человеку упоминания; сомнительные
склейки и родство записываются отдельными гипотезами.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from docstore import ROOT, documents, load_meta, persons


REGISTRY = ROOT / "namesakes.json"
TEMP_ID = re.compile(r"^u-[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}(?:-\d{2}(?:-\d{2})?)?$")
RELATIONS = {
    "confirmed_same_person",
    "probably_same",
    "possibly_related",
    "distinct_person",
}


def read_registry(path=REGISTRY):
    if not Path(path).exists():
        return {"people": {}, "hypotheses": []}
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("реестр должен быть JSON-объектом")
    return value


def write_registry(data, path=REGISTRY):
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _valid_page(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def validate_registry(data, roster=None, known_documents=None):
    """Вернуть нарушения контракта, не меняя реестр."""
    errors = []
    people = data.get("people")
    hypotheses = data.get("hypotheses")
    if not isinstance(people, dict):
        return ["people должен быть объектом"]
    if not isinstance(hypotheses, list):
        errors.append("hypotheses должен быть списком")
        hypotheses = []

    for ident, person in people.items():
        where = f"people.{ident}"
        if not TEMP_ID.fullmatch(ident):
            errors.append(f"{where}: ID должен иметь вид u-surname-name")
        if not isinstance(person, dict):
            errors.append(f"{where}: запись должна быть объектом")
            continue
        if not isinstance(person.get("name"), str) or not person["name"].strip():
            errors.append(f"{where}: нет поля name")
        mentions = person.get("mentions")
        if not isinstance(mentions, list) or not mentions:
            errors.append(f"{where}: mentions должен быть непустым списком")
            continue
        for number, mention in enumerate(mentions, 1):
            source = f"{where}.mentions[{number}]"
            if not isinstance(mention, dict):
                errors.append(f"{source}: упоминание должно быть объектом")
                continue
            document, page = mention.get("document"), mention.get("page")
            if not document:
                errors.append(f"{source}: нет поля document")
            if not _valid_page(page):
                errors.append(f"{source}: page должен быть положительным целым")
            if not isinstance(mention.get("description"), str) or not mention["description"].strip():
                errors.append(f"{source}: нет поля description")
            date = mention.get("date")
            if date is not None and (not isinstance(date, str) or not DATE_RE.fullmatch(date)):
                errors.append(f"{source}: date должен иметь вид YYYY, YYYY-MM или YYYY-MM-DD")
            if known_documents is not None and document:
                meta = known_documents.get(document)
                if meta is None:
                    errors.append(f"{source}: неизвестный документ {document}")
                elif _valid_page(page) and isinstance(meta.get("pages"), int) and page > meta["pages"]:
                    errors.append(f"{source}: страница {page} за пределами документа")
    known_people = set(people)
    if roster is not None:
        known_people.update(roster)
    seen_hypotheses = set()
    for number, hypothesis in enumerate(hypotheses, 1):
        where = f"hypotheses[{number}]"
        if not isinstance(hypothesis, dict):
            errors.append(f"{where}: гипотеза должна быть объектом")
            continue
        left, right = hypothesis.get("left"), hypothesis.get("right")
        if left not in known_people:
            errors.append(f"{where}: неизвестный участник {left!r}")
        if right not in known_people:
            errors.append(f"{where}: неизвестный участник {right!r}")
        if left == right:
            errors.append(f"{where}: участники должны различаться")
        if hypothesis.get("relation") not in RELATIONS:
            errors.append(f"{where}: неизвестная relation {hypothesis.get('relation')!r}")
        if not isinstance(hypothesis.get("basis"), str) or not hypothesis["basis"].strip():
            errors.append(f"{where}: нет поля basis")
        pair = tuple(sorted((str(left), str(right))))
        if pair in seen_hypotheses:
            errors.append(f"{where}: гипотеза между {left} и {right} повторяется")
        seen_hypotheses.add(pair)
    return errors


def audit(data):
    docs = {ident: load_meta(ident) for ident in documents()}
    return validate_registry(data, persons(), docs)


def show_dossier(data, ident):
    person = data.get("people", {}).get(ident)
    if person is None:
        raise ValueError(f"неизвестный временный ID {ident}")
    print(f"{person['name']} ({ident})")
    if person.get("description"):
        print(person["description"])
    print("\nИсточники:")
    for item in sorted(person["mentions"], key=lambda x: (x.get("date", ""), x["document"], x["page"])):
        date = f"{item['date']}: " if item.get("date") else ""
        print(f"  {date}{item['description']} ({item['document']}, стр. {item['page']})")
    links = [h for h in data.get("hypotheses", []) if ident in (h.get("left"), h.get("right"))]
    if links:
        print("\nГипотезы:")
        for item in links:
            other = item["right"] if item["left"] == ident else item["left"]
            print(f"  {item['relation']}: {other} — {item['basis']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="проверить реестр")
    sub.add_parser("list", help="показать временных людей")
    show = sub.add_parser("show", help="показать досье и связанные гипотезы")
    show.add_argument("person")
    add = sub.add_parser("add", help="создать досье с первым упоминанием")
    add.add_argument("--id", required=True)
    add.add_argument("--name", required=True)
    add.add_argument("--about", help="краткое описание человека")
    add.add_argument("--document", required=True)
    add.add_argument("--page", required=True, type=int)
    add.add_argument("--date")
    add.add_argument("--description", required=True)
    add.add_argument("--quote")
    mention = sub.add_parser("mention", help="добавить источник в досье")
    mention.add_argument("person")
    mention.add_argument("--document", required=True)
    mention.add_argument("--page", required=True, type=int)
    mention.add_argument("--date")
    mention.add_argument("--description", required=True)
    mention.add_argument("--quote")
    link = sub.add_parser("link", help="записать гипотезу между людьми")
    link.add_argument("left")
    link.add_argument("right")
    link.add_argument("--relation", required=True, choices=sorted(RELATIONS))
    link.add_argument("--basis", required=True)
    args = parser.parse_args(argv)
    try:
        data = read_registry()
        if args.command == "check":
            errors = audit(data)
            if errors:
                print("\n".join(errors), file=sys.stderr)
                return 1
            print(f"однофамильцев: {len(data.get('people', {}))}, "
                  f"гипотез: {len(data.get('hypotheses', []))}, ошибок нет")
        elif args.command == "list":
            for ident, person in sorted(data.get("people", {}).items(), key=lambda x: x[1]["name"]):
                print(f"{ident:44} {person['name']} ({len(person['mentions'])} источн.)")
        elif args.command == "show":
            show_dossier(data, args.person)
        else:
            if args.command in {"add", "mention"}:
                item = {"document": args.document, "page": args.page,
                        "description": args.description}
                if args.date:
                    item["date"] = args.date
                if args.quote:
                    item["quote"] = args.quote
            if args.command == "add":
                if args.id in data.setdefault("people", {}):
                    raise ValueError(f"ID {args.id} уже существует")
                person = {"name": args.name, "mentions": [item]}
                if args.about:
                    person["description"] = args.about
                data["people"][args.id] = person
            elif args.command == "mention":
                if args.person not in data.get("people", {}):
                    raise ValueError(f"неизвестный временный ID {args.person}")
                data["people"][args.person]["mentions"].append(item)
            elif args.command == "link":
                data.setdefault("hypotheses", []).append({
                    "left": args.left, "right": args.right,
                    "relation": args.relation, "basis": args.basis,
                })
            errors = audit(data)
            if errors:
                raise ValueError("; ".join(errors))
            write_registry(data)
            print("реестр обновлён")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
