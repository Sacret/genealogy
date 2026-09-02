#!/usr/bin/env python3
"""Хранилище документов и журнал поисков.

Каждый документ живёт в папке по своему идентификатору из URL:

    bv0000407/
        meta.json       заголовок, URL, число страниц, dpi
        scans/          pNNNN.jpg
        ocr/            pNNNN.txt
        searches.jsonl  журнал: что искали, когда, с каким результатом
"""

import json
import pathlib
import re
import urllib.request
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122 Safari/537.36")

ROOT = pathlib.Path(__file__).parent


def doc_id(base_url: str) -> str:
    """Идентификатор документа — последний сегмент URL: bv0000407."""
    return base_url.rstrip("/").rsplit("/", 1)[-1]


def doc_dir(ident: str) -> pathlib.Path:
    return ROOT / ident


def fetch_title(base_url: str) -> str:
    """Заголовок документа из вьюера.

    Берём data-title у <app-root>: там чистое название, тогда как в
    <title> ещё приклеены имя библиотеки и слово Vivaldi.
    """
    req = urllib.request.Request(f"{base_url.rstrip('/')}/view/",
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        html = r.read().decode("utf-8", "replace")
    m = re.search(r'<app-root[^>]*\bdata-title="([^"]*)"', html)
    if m:
        return m.group(1).strip()
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else "(без названия)"


def load_meta(ident: str) -> dict:
    p = doc_dir(ident) / "meta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_meta(ident: str, **fields) -> dict:
    d = doc_dir(ident)
    d.mkdir(parents=True, exist_ok=True)
    meta = load_meta(ident)
    meta.update(fields)
    (d / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def log_search(ident: str, surname: str, stem: str, threshold: float, hits) -> None:
    """Дописывает запись в журнал поисков по документу.

    Формат — JSONL: дописывание атомарно и не портит уже накопленное,
    даже если процесс прервали на середине.
    """
    meta = load_meta(ident)
    pages = sorted({h.page for h in hits})
    rec = {
        "type": "search",
        "date": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "surname": surname,
        "stem": stem,
        "threshold": threshold,
        "document": meta.get("title", ""),
        "url": meta.get("url", ""),
        "pages_total": meta.get("pages"),
        "hits": len(hits),
        "exact": sum(1 for h in hits if h.cost == 0),
        "pages_with_hits": [p.replace(".txt", "").lstrip("p").lstrip("0") or "0"
                            for p in pages],
    }
    with (doc_dir(ident) / "searches.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def add_verdict(ident: str, surname: str, verdict: str,
                status: str = "unclear", confirmed=None, kin=None) -> None:
    """Итог проверки глазами.

    Сырое число попаданий обманчиво: пять кандидатов на 'Кармазинъ'
    оказались словом 'Кармалинъ' и именем 'Харлампій'. Без явного
    вердикта журнал через месяц читается как 'фамилия найдена'.

    `confirmed` — страницы, где фамилия действительно стоит. Их нельзя
    вывести из текста вердикта: в нём номера страниц называются и у
    отклонённых кандидатов ('стр. 336 — Текучевъ'), и у находок в
    соседних томах, а журнал выделял такие номера как подтверждённые
    и подставлял к ним вырезки.

    `kin` — подмножество `confirmed`: страницы, где найден человек, чьё
    родство установлено, а не просто однофамилец. Разделять это важно:
    Могучевыхъ из ст. Кочетовской в приказах несколько семей, и журнал,
    красящий их все одинаково, обещает больше, чем известно.
    """
    rec = {
        "type": "verdict",
        "date": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "surname": surname,
        "status": status,          # found | absent | unclear
        "verdict": verdict,
    }
    if confirmed is not None:
        rec["confirmed"] = [str(p) for p in confirmed]
    if kin is not None:
        rec["kin"] = [str(p) for p in kin]
    with (doc_dir(ident) / "searches.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def documents() -> list:
    """Все документы в корне: папка с meta.json — это документ."""
    out = []
    for meta in sorted(ROOT.glob("*/meta.json")):
        out.append(meta.parent.name)
    return out


def read_log(ident: str) -> list:
    p = doc_dir(ident) / "searches.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def latest_verdicts(ident: str) -> dict:
    """Последний вердикт по каждой фамилии.

    Вердикты дописываются, а не переписываются: перепроверка добавляет
    строку. Читателя интересует последняя — прежние остаются историей
    и не должны, например, удерживать от чистки страницы, которые
    новый вердикт уже отвёл.
    """
    return {r["surname"]: r for r in read_log(ident) if r.get("type") == "verdict"}


def print_log(ident: str) -> None:
    log = read_log(ident)
    meta = load_meta(ident)
    print(f"{meta.get('title', ident)}")
    print(f"{meta.get('url', '')}   страниц: {meta.get('pages', '?')}\n")
    if not log:
        print("по этому документу ещё ничего не искали")
        return

    verdicts = {r["surname"]: r for r in log if r.get("type") == "verdict"}
    searches = [r for r in log if r.get("type", "search") == "search"]

    # как и в journal.html — последний прогон по каждой фамилии
    latest = {}
    for r in searches:
        latest[r["surname"]] = r
    searches = sorted(latest.values(), key=lambda r: r["date"])

    print(f"{'дата':17} {'фамилия':14} {'кандидатов':>10}  страницы")
    for r in searches:
        when = r["date"][:16].replace("T", " ")
        pages = ", ".join(r["pages_with_hits"][:10]) or "—"
        if len(r["pages_with_hits"]) > 10:
            pages += f" … (+{len(r['pages_with_hits']) - 10})"
        print(f"{when:17} {r['surname']:14} {r['hits']:>10}  {pages}")
        if r["surname"] in verdicts:
            print(f"{'':17} └─ проверено: {verdicts[r['surname']]['verdict']}")
