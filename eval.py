"""Оценка OCR по небольшому корпусу вручную проверенных страниц.

Главная метрика здесь не CER, а пригодность текста для фамильного поиска:
доля найденных настоящих фамилий и число ложных кандидатов на страницу.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from surnamefind.search import find_in_text


ROOT = Path(__file__).parent
DEFAULT_CORPUS = ROOT / "bench" / "corpus.json"


def ocr(image, lang="rus", psm="6", extra=()):
    """Распознать один скан и явно сообщить об ошибке Tesseract."""
    cmd = ["tesseract", str(image), "-", "-l", lang, "--psm", str(psm), *extra]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode:
        detail = result.stderr.strip() or f"код возврата {result.returncode}"
        raise RuntimeError(f"Tesseract не распознал {image}: {detail}")
    return result.stdout


def load_corpus(path=DEFAULT_CORPUS):
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_corpus(data, path.parent)
    if errors:
        raise ValueError("битый эталонный корпус:\n- " + "\n- ".join(errors))
    return data


def validate_corpus(data, base=ROOT):
    """Проверить контракт корпуса; пути разрешаются относительно его файла."""
    errors = []
    if not isinstance(data, dict) or data.get("version") != 1:
        return ["version должна быть равна 1"]
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        return ["cases должен быть непустым списком"]
    seen = set()
    for number, case in enumerate(cases, 1):
        where = f"cases[{number}]"
        if not isinstance(case, dict):
            errors.append(f"{where}: ожидается объект")
            continue
        ident = case.get("id")
        if not isinstance(ident, str) or not ident:
            errors.append(f"{where}: нет id")
        elif ident in seen:
            errors.append(f"{where}: повторный id {ident!r}")
        else:
            seen.add(ident)
        image = case.get("image")
        if not isinstance(image, str) or not image:
            errors.append(f"{where}: нет image")
        elif not (Path(base) / image).is_file():
            errors.append(f"{where}: нет файла image {image!r}")
        categories = case.get("categories")
        if not isinstance(categories, list) or not categories or not all(
                isinstance(item, str) and item for item in categories):
            errors.append(f"{where}: categories должен быть непустым списком строк")
        present = case.get("present")
        absent = case.get("absent")
        if not isinstance(present, list) or not all(isinstance(x, str) and x for x in present):
            errors.append(f"{where}: present должен быть списком строк")
            present = []
        if not isinstance(absent, list) or not all(isinstance(x, str) and x for x in absent):
            errors.append(f"{where}: absent должен быть списком строк")
            absent = []
        overlap = sorted(set(present) & set(absent))
        if overlap:
            errors.append(f"{where}: фамилии одновременно present и absent: {', '.join(overlap)}")
        stored = case.get("stored", {})
        if not isinstance(stored, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in stored.items()):
            errors.append(f"{where}: stored должен быть объектом layer: path")
        else:
            for layer, value in stored.items():
                if not (Path(base) / value).is_file():
                    errors.append(f"{where}: нет слоя {layer} {value!r}")
    return errors


def score_page(text, present, absent):
    """Посчитать отдачу и ложные кандидаты одной страницы."""
    hits, misses, false = [], [], []
    for surname in present:
        matches = find_in_text(text, surname)
        (hits if matches else misses).append(surname)
    for surname in absent:
        for match in find_in_text(text, surname):
            false.append({"surname": surname, "raw": match.raw,
                          "cost": match.cost, "partial": match.partial})
    return {"hits": hits, "misses": misses, "false": false}


def evaluate(corpus, texts):
    """Оценить ``{case_id: text}``; удобно также для тестов и иных OCR."""
    rows = []
    for case in corpus["cases"]:
        if case["id"] not in texts:
            continue
        score = score_page(texts[case["id"]], case["present"], case["absent"])
        rows.append({"id": case["id"], "categories": case["categories"],
                     "truth": len(case["present"]), **score})
    truth = sum(row["truth"] for row in rows)
    found = sum(len(row["hits"]) for row in rows)
    false = sum(len(row["false"]) for row in rows)
    return {
        "pages": len(rows), "corpus_pages": len(corpus["cases"]),
        "truth": truth, "found": found,
        "recall": found / truth if truth else 0.0,
        "false_candidates": false,
        "false_candidates_per_page": false / len(rows) if rows else 0.0,
        "rows": rows,
    }


def tesseract_texts(corpus, base, lang, psm, extra):
    texts = {}
    for case in corpus["cases"]:
        image = Path(base) / case["image"]
        texts[case["id"]] = ocr(image, lang=lang, psm=psm, extra=extra)
    return texts


def stored_texts(corpus, base, layer):
    texts = {}
    for case in corpus["cases"]:
        stored = case.get("stored", {})
        paths = list(stored.values()) if layer == "all" else [stored[layer]] if layer in stored else []
        if paths:
            texts[case["id"]] = "\n".join(
                (Path(base) / path).read_text(encoding="utf-8") for path in paths)
    return texts


def print_report(result, label):
    print(label)
    for row in result["rows"]:
        missed = ", ".join(row["misses"]) or "—"
        false = ", ".join(f"{hit['surname']}←{hit['raw']}" for hit in row["false"]) or "—"
        print(f"  {row['id']:30} {len(row['hits']):2d}/{row['truth']:<2d}  "
              f"пропущены: {missed}; ложные: {false}")
    print(f"\nитого: {result['found']}/{result['truth']} "
          f"({result['recall']:.1%}); ложных кандидатов: "
          f"{result['false_candidates']} ({result['false_candidates_per_page']:.2f}/стр.)")
    if result["pages"] != result["corpus_pages"]:
        print(f"оценено страниц: {result['pages']}/{result['corpus_pages']} "
              "(для остальных нет выбранного сохранённого слоя)")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--engine", choices=("tesseract", "stored"), default="tesseract")
    parser.add_argument("--lang", default="rus", help="модель Tesseract")
    parser.add_argument("--psm", default="6", help="режим сегментации Tesseract")
    parser.add_argument("--extra", action="append", default=[],
                        help="дополнительный аргумент Tesseract; можно повторять")
    parser.add_argument("--layer", default="ocr",
                        help="сохранённый слой (ocr, ocr_bands, ocr_cols, ocr_prep или all)")
    parser.add_argument("--json", action="store_true", help="машиночитаемый результат")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        corpus = load_corpus(args.corpus)
        base = args.corpus.parent
        if args.engine == "tesseract":
            texts = tesseract_texts(corpus, base, args.lang, args.psm, args.extra)
            label = f"tesseract: {args.lang} psm {args.psm}"
        else:
            texts = stored_texts(corpus, base, args.layer)
            label = f"сохранённый слой: {args.layer}"
        result = evaluate(corpus, texts)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(error, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"label": label, **result}, ensure_ascii=False, indent=2))
    else:
        print_report(result, label)
    return 0 if result["pages"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
