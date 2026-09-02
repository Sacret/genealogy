#!/usr/bin/env python3
"""Скачивание страниц из Vivaldi (vivaldi.dspl.ru и другие установки).

API вьюера, найденный в его же JS (объект RouteConstants в main.js):
    /page/sizes                  -> [{"Page":1,"Size":{...}}, ...]
    /page/{n}/image/{dpi}        -> JPEG целой страницы, без тайлов
    /search/{query}              -> полнотекстовый поиск (пуст, если нет OCR)

Складывает в <идентификатор>/scans/ и записывает meta.json.
Возобновляемо: уже лежащие файлы пропускаются.
"""

import argparse, json, signal, sys, threading, time, urllib.request, urllib.error

from docstore import UA, doc_id, doc_dir, fetch_title, load_meta, save_meta


def parse_pages(spec: str):
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return sorted(set(out))


class Stalled(OSError):
    """Попытка съела весь отведённый ей срок целиком."""


def _hard_deadline(seconds):
    """Потолок на всю попытку, а не на отдельное чтение сокета.

    Таймаута `urlopen` для этого мало: он ограничивает одну операцию с
    сокетом, поэтому соединение, отдающее по байту раз в минуту, живёт
    вечно. На стр. 313 bv0000391 так и вышло — процесс 16 минут простоял
    в SSL-чтении заголовков ответа, пока curl брал ту же страницу за две
    секунды. SIGALRM рвёт блокирующий вызов независимо от того, что там
    с сокетом; Stalled наследует OSError, так что попадает в общий разбор
    ниже и приводит к обычному повтору.

    Сигналы ставятся только из главного потока, поэтому в потоках (prep.py,
    crop.py зовут ensure_page) остаётся голый таймаут сокета.
    """
    if threading.current_thread() is not threading.main_thread():
        return lambda: None

    def fire(sig, frame):
        raise Stalled(f"нет ответа за {seconds} с")

    prev = signal.signal(signal.SIGALRM, fire)
    signal.alarm(seconds)
    return lambda: (signal.alarm(0), signal.signal(signal.SIGALRM, prev))


def get(url, referer, retries=5, timeout=30, deadline=60):
    """Скачать с повторами.

    Ловим OSError целиком, а не URLError: сброс соединения приходит
    как ConnectionResetError, который URLError не является, и на длинном
    прогоне одна такая осечка иначе убивает всю оставшуюся работу.
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
    for attempt in range(retries):
        disarm = _hard_deadline(deadline)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except OSError as e:            # URLError, ConnectionResetError, таймауты
            if attempt == retries - 1:
                raise
            time.sleep(min(2 ** attempt, 15))   # библиотека маленькая, не давим
        finally:
            disarm()


def page_count(base):
    return len(json.loads(get(f"{base}/page/sizes", f"{base}/view/")))


def ensure_page(ident: str, n: int) -> "pathlib.Path":
    """Вернуть скан страницы, дотянув его, если он был выброшен.

    Благодаря этому сканы можно считать кэшем, а не данными: любой
    инструмент, которому нужна картинка, получит её независимо от того,
    чистили каталог или нет.
    """
    import pathlib
    meta = load_meta(ident)
    dst = doc_dir(ident) / "scans" / f"p{n:04d}.jpg"
    if dst.exists() and dst.stat().st_size > 1024:
        return dst
    base, dpi = meta.get("url"), meta.get("dpi", 400)
    if not base:
        raise SystemExit(f"{ident}: в meta.json нет url, страницу не дотянуть")
    dst.parent.mkdir(parents=True, exist_ok=True)
    print(f"  страница {n} отсутствует — качаю заново…", file=sys.stderr)
    dst.write_bytes(get(f"{base}/page/{n}/image/{dpi}", f"{base}/view/"))
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base", help="напр. https://vivaldi.dspl.ru/bv0000407")
    ap.add_argument("--dpi", type=int, default=400)
    ap.add_argument("--first", type=int, default=1)
    ap.add_argument("--last", type=int)
    ap.add_argument("--pages", help="только эти страницы: 179,494,500-505")
    ap.add_argument("--delay", type=float, default=0.5, help="пауза между запросами, сек")
    a = ap.parse_args()

    base = a.base.rstrip("/")
    referer = f"{base}/view/"
    ident = doc_id(base)
    total = page_count(base)
    last = min(a.last or total, total)

    out = doc_dir(ident) / "scans"
    out.mkdir(parents=True, exist_ok=True)
    save_meta(ident, url=base, title=fetch_title(base), pages=total, dpi=a.dpi)

    wanted = parse_pages(a.pages) if a.pages else range(a.first, last + 1)
    print(f"{ident}: страниц {total}; качаем {len(list(wanted))} шт. @ {a.dpi} dpi")
    done = skipped = 0
    failed = []
    for n in wanted:
        dst = out / f"p{n:04d}.jpg"
        if dst.exists() and dst.stat().st_size > 1024:
            skipped += 1
            continue
        try:
            dst.write_bytes(get(f"{base}/page/{n}/image/{a.dpi}", referer))
        except OSError as e:
            # Одна безнадёжная страница не должна ронять прогон на сотни
            # страниц: запоминаем и идём дальше, добрать можно повтором.
            failed.append(n)
            print(f"  стр. {n}: не удалось ({e})", file=sys.stderr)
            continue
        done += 1
        if done % 25 == 0:
            print(f"  {n}/{last}  скачано {done}, пропущено {skipped}", file=sys.stderr)
        time.sleep(a.delay)
    if failed:
        print(f"НЕ СКАЧАНЫ {len(failed)} стр.: "
              f"{','.join(map(str, failed))}\n  добрать: fetch.py {base} "
              f"--pages {','.join(map(str, failed))}")
    print(f"готово: скачано {done}, уже было {skipped}, ошибок {len(failed)}")


if __name__ == "__main__":
    main()
