#!/usr/bin/env python3
"""Скачивание страниц из Vivaldi (vivaldi.dspl.ru и другие установки).

API вьюера, найденный в его же JS (объект RouteConstants в main.js):
    /page/sizes                  -> [{"Page":1,"Size":{...}}, ...]
    /page/{n}/image/{dpi}        -> JPEG целой страницы, без тайлов
    /search/{query}              -> полнотекстовый поиск (пуст, если нет OCR)

Складывает в <идентификатор>/scans/ и записывает meta.json.
Возобновляемо: уже лежащие файлы пропускаются.
"""

import argparse, http.client, json, socket, sys, threading, time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

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


def _watchdog(conn, deadline, stalled):
    """Потолок на всю попытку, а не на отдельное чтение сокета.

    Таймаута `urlopen` для этого мало: он ограничивает одну операцию с
    сокетом, поэтому соединение, отдающее по байту раз в минуту, живёт
    вечно. На стр. 313 bv0000391 так и вышло — процесс 16 минут простоял
    в SSL-чтении заголовков ответа, пока curl брал ту же страницу за две
    секунды.

    Раньше срок держал SIGALRM, но сигналы ставятся только из главного
    потока, так что в пуле защита пропадала — а качаем мы теперь именно
    в пуле. Сторожевой таймер вместо этого рвёт сам сокет: shutdown()
    будит чтение, заблокированное в другом потоке, и оно падает обычным
    OSError, то есть попадает в общий разбор ниже и приводит к повтору.
    """
    def cut():
        stalled.set()
        try:
            if conn.sock is not None:
                conn.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass            # успели закрыть сами — сторожу больше нечего рвать

    t = threading.Timer(deadline, cut)
    t.daemon = True
    t.start()
    return t


def get(url, referer, retries=5, timeout=30, deadline=60):
    """Скачать с повторами.

    Ловим OSError целиком, а не URLError: сброс соединения приходит
    как ConnectionResetError, который URLError не является, и на длинном
    прогоне одна такая осечка иначе убивает всю оставшуюся работу.
    """
    u = urllib.parse.urlsplit(url)
    cls = (http.client.HTTPSConnection if u.scheme == "https"
           else http.client.HTTPConnection)
    path = u.path + (f"?{u.query}" if u.query else "")
    headers = {"User-Agent": UA, "Referer": referer}

    for attempt in range(retries):
        conn = cls(u.netloc, timeout=timeout)
        stalled = threading.Event()
        guard = _watchdog(conn, deadline, stalled)
        try:
            conn.request("GET", path, headers=headers)
            r = conn.getresponse()
            if r.status != 200:
                raise OSError(f"HTTP {r.status} {r.reason}")
            return r.read()
        except OSError as e:            # URLError, ConnectionResetError, таймауты
            if stalled.is_set():
                e = Stalled(f"нет ответа за {deadline} с")
            if attempt == retries - 1:
                raise e
            time.sleep(min(2 ** attempt, 15))   # библиотека маленькая, не давим
        finally:
            guard.cancel()
            conn.close()


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
    ap.add_argument("--delay", type=float, default=0.5,
                    help="пауза после каждой страницы, сек (на каждый поток)")
    ap.add_argument("--workers", type=int, default=4,
                    help="сколько страниц тянуть одновременно")
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
    # Узкое место — не мы, а сервер: одна страница в 700 КБ приходит за 12 с
    # (57 КБ/с), при этом стороннее соединение получает свою за те же 12 с и
    # основной цикл не замедляет. Душат каждое соединение по отдельности, а не
    # клиента целиком, поэтому потоки складываются почти линейно: 513 страниц
    # это два часа в одиночку и полчаса вчетвером.
    todo = [n for n in wanted
            if not ((out / f"p{n:04d}.jpg").exists()
                    and (out / f"p{n:04d}.jpg").stat().st_size > 1024)]
    skipped = len(list(wanted)) - len(todo)
    done = 0
    failed = []
    lock = threading.Lock()

    def fetch_one(n):
        nonlocal done
        dst = out / f"p{n:04d}.jpg"
        try:
            data = get(f"{base}/page/{n}/image/{a.dpi}", referer)
        except OSError as e:
            # Одна безнадёжная страница не должна ронять прогон на сотни
            # страниц: запоминаем и идём дальше, добрать можно повтором.
            with lock:
                failed.append(n)
            print(f"  стр. {n}: не удалось ({e})", file=sys.stderr)
            return
        # Пишем через временное имя: оборванный на середине процесс иначе
        # оставит обрезанный JPEG, который пройдёт проверку по размеру и
        # молча испортит распознавание.
        tmp = dst.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(dst)
        with lock:
            done += 1
            if done % 25 == 0:
                print(f"  {done}/{len(todo)}  скачано {done}, пропущено {skipped}",
                      file=sys.stderr)
        time.sleep(a.delay)

    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        list(pool.map(fetch_one, todo))

    if failed:
        print(f"НЕ СКАЧАНЫ {len(failed)} стр.: "
              f"{','.join(map(str, failed))}\n  добрать: fetch.py {base} "
              f"--pages {','.join(map(str, failed))}")
    print(f"готово: скачано {done}, уже было {skipped}, ошибок {len(failed)}")


if __name__ == "__main__":
    main()
