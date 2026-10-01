#!/usr/bin/env python3
"""Весь конвейер по документу одной командой.

    python3 run.py https://vivaldi.dspl.ru/bv0000387
    python3 run.py https://vivaldi.dspl.ru/bv0000387 --find Кармазинъ Могучевъ
    python3 run.py --next 5 --find Кармазинъ Могучевъ       # пять из очереди
    python3 run.py --next 3 --queue газеты --dry-run         # только показать

Шаги идут по порядку и каждый возобновляем, так что повторный запуск
дочитывает недостающее, а не начинает заново.

`--next N` берёт документы из `documents.json` — сначала «в работе», потом
очереди по приоритету — и, пока один распознаётся, уже скачивает
следующий: сеть и процессор заняты разным, и раньше стояли по очереди.
Упавший документ не останавливает остальные; в конце названо, что упало,
и код возврата ненулевой.
"""

import argparse
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import catalog
from docstore import doc_dir, doc_id, load_meta, read_log


class StepFailed(Exception):
    pass


def step(title, cmd, capture=False):
    """Запустить шаг. С `capture` вывод не печатается, а возвращается.

    Фоновое скачивание иначе вперемешку с распознаванием соседнего
    документа превращало вывод в кашу.
    """
    if not capture:
        print(f"\n=== {title} ===", flush=True)
    t = time.monotonic()
    r = subprocess.run([sys.executable, *cmd], capture_output=capture,
                       text=capture)
    if r.returncode:
        tail = ""
        if capture:
            tail = "\n" + "\n".join(((r.stderr or "") + (r.stdout or ""))
                                    .strip().splitlines()[-5:])
        raise StepFailed(f"шаг «{title}» упал (код {r.returncode}){tail}")
    if capture:
        return (r.stdout or "") + (r.stderr or "")
    print(f"    {time.monotonic() - t:.0f} c", flush=True)


def is_processed(ident):
    """Скачан, распознан и измерен целиком: больше конвейеру тут делать нечего."""
    meta = load_meta(ident)
    total = meta.get("pages")
    folder = doc_dir(ident)
    return bool(total
                and len(list((folder / "ocr").glob("p*.txt"))) == total
                and (folder / "quality.json").exists())


def needs_work(ident, surnames):
    """Нужен ли документу прогон: не обработан, либо не искали нужные фамилии."""
    if not is_processed(ident):
        return True
    searched = {r.get("surname") for r in read_log(ident)
                if r.get("type", "search") == "search"}
    return any(s not in searched for s in surnames)


def pick_next(n, documents, surnames=(), queue=None, needs=needs_work):
    """Первые n документов к обработке: «в работе», затем очереди по порядку.

    `queue` — часть названия очереди («газеты», «2_»); без него берутся все.
    Документ, у которого конвейер уже отработал, пропускается: иначе без
    вердикта он оставался бы в «в работе» и каждый запуск начинался бы с
    одних и тех же n томов.
    """
    candidates = []
    if not queue:
        candidates += documents.get("в_работе", [])
    for name, items in documents.get("очередь", {}).items():
        if not queue or queue in name:
            candidates += items
    out = []
    for rec in candidates:
        if len(out) >= n:
            break
        if needs(rec["id"], surnames):
            out.append(rec)
    return out


def fetch_doc(a, base):
    return step(f"скачивание {doc_id(base)}",
                ["fetch.py", base, "--dpi", str(a.dpi), "--delay", str(a.delay)],
                capture=a.background)


def process_doc(a, base):
    """Всё после скачивания: распознавание, замер, перечитывание, поиск."""
    ident = doc_id(base)

    # Газета набрана колонками, и `--psm 6` («страница — один блок»)
    # сращивает соседние столбцы в одну строку: на первой полосе за
    # 1 января 1911 г. так вышло «поучеве, которге обратило на него |
    # Адександръ». `--psm 4` («колонка текста переменной высоты») ищет
    # столбцы сам. На десяти выпусках за январь 1911 г. это подняло
    # среднюю уверенность с 54,7 до 60,8, а число ненадёжных полос
    # уронило с 32 из 38 до 15; книге тот же режим ни помог, ни повредил
    # (64,3 против 64,9 на дюжине страниц bv0000233), так что у книг
    # остаётся прежний.
    psm = a.psm or ("4" if catalog.NEWSPAPER.search(
        load_meta(ident).get("title", "")) else "6")
    step("распознавание", ["ocr_pages.py", ident, "--lang", "rus",
                           "--psm", psm])
    # Полутоновые сканы (тонкая бумага, просвечивает оборот) даёт не всякий
    # том, но проверка дешёвая и сама пропускает уже бинарные.
    step("бинаризация", ["prep.py", ident])
    # Замер тем же режимом, каким читали: иначе quality.json называет
    # ненадёжным не тот текст, по которому потом ищут.
    step("замер надёжности", ["quality.py", ident, "--psm", psm])
    if not a.skip_rescue:
        # Дорогой шаг: только страницы, которые quality.py признал плохими.
        step("перечитывание полосами", ["rescue.py", ident])
        # Газете полосы помогают мало: лента пересекает все столбцы разом.
        # Её ненадёжные полосы перечитываются ещё и по колонкам — тем же
        # разрезом, каким crop.py потом ищет место находки.
        if psm == "4":
            step("перечитывание колонками", ["rescue.py", ident, "--cols"])

    for surname in a.find:
        step(f"поиск: {surname}", ["find.py", ident, surname])

    # Без --find поиска не было, а значит, и никто не пересобрал список:
    # том уже скачан и распознан, и в очереди ему больше не место.
    catalog.refresh(ident)
    print(f"\nготово: {load_meta(ident).get('title', ident)}")


def run_batch(a, bases, fetch=fetch_doc, process=process_doc):
    """Документы по очереди; скачивание следующего идёт, пока читается этот.

    Вперёд уходит ровно один документ: больше места на диске под сканы
    не отдаётся. Результат — {база: ошибка | None}.
    """
    results = {}
    a.background = len(bases) > 1
    with ThreadPoolExecutor(1) as net:
        pending = {0: net.submit(fetch, a, bases[0])} if bases else {}
        for i, base in enumerate(bases):
            try:
                out = pending.pop(i).result()
                if out:
                    print(f"\n=== скачано {doc_id(base)} (в фоне) ===\n"
                          + "\n".join(out.strip().splitlines()[-3:]), flush=True)
            except Exception as e:
                results[base] = e
                if i + 1 < len(bases):
                    pending[i + 1] = net.submit(fetch, a, bases[i + 1])
                continue
            if i + 1 < len(bases):
                pending[i + 1] = net.submit(fetch, a, bases[i + 1])
            try:
                process(a, base)
                results[base] = None
            except (Exception, SystemExit) as e:    # StepFailed и выход из шага
                results[base] = e                   # не останавливают соседей
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", help="URL документа во вьюере Vivaldi")
    ap.add_argument("--next", type=int, metavar="N", dest="next_n",
                    help="вместо URL: взять N документов из очереди "
                         "documents.json")
    ap.add_argument("--queue", help="только очередь, в названии которой есть "
                                    "это слово: газеты, 2_, приказы")
    ap.add_argument("--dry-run", action="store_true",
                    help="с --next: показать выбор и ничего не качать")
    ap.add_argument("--dpi", type=int, default=400)
    ap.add_argument("--delay", type=float, default=0.4)
    ap.add_argument("--find", nargs="*", default=[], help="фамилии для поиска")
    ap.add_argument("--psm", help="режим разбора страницы для tesseract; "
                                  "по умолчанию 6, у газет 4")
    ap.add_argument("--skip-rescue", action="store_true",
                    help="без перечитывания ненадёжных страниц полосами")
    a = ap.parse_args()

    if bool(a.base) == bool(a.next_n):
        ap.error("нужен либо URL документа, либо --next N")
    if (a.queue or a.dry_run) and not a.next_n:
        ap.error("--queue и --dry-run работают только с --next")
    if a.next_n is not None and a.next_n < 1:
        ap.error("--next должен быть положительным")

    if a.next_n:
        if not a.dry_run:
            catalog.refresh()   # очередь по состоянию диска, без сети
        documents = json.loads(catalog.DOCUMENTS.read_text(encoding="utf-8"))
        picked = pick_next(a.next_n, documents, a.find, a.queue)
        if not picked:
            sys.exit("в очереди нечего брать" + (f" ({a.queue})" if a.queue else ""))
        for rec in picked:
            print(f"  {rec['id']}  {rec['title'][:90]}")
        if a.dry_run:
            return
        bases = [rec["url"] for rec in picked]
    else:
        bases = [a.base]

    results = run_batch(a, bases)
    failed = {b: e for b, e in results.items() if e}
    if len(bases) > 1:
        print(f"\nитог: обработано {len(bases) - len(failed)} из {len(bases)}")
    for base, err in failed.items():
        print(f"  {doc_id(base)}: {err}", file=sys.stderr)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
