"""Общая подготовка скриптов из tools/: корень проекта в пути импорта.

Скрипты пачек годами жили в /tmp прошлых сессий и переносились копированием
с заменой зашитых путей — и терялись вместе с /tmp. Здесь у них путь один:
от собственного файла. Каждый начинается с `import _root`, после чего
доступны `docstore`, `verdict` и остальные модули корня; запускать можно из
любой папки.
"""

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def out_dir(explicit=None) -> pathlib.Path:
    """Куда класть картинки для просмотра: --out, $TOOLS_OUT или временная папка.

    В репозиторий они не идут никогда: это рабочие вырезки, а не `crops/`.
    """
    path = pathlib.Path(explicit or os.environ.get("TOOLS_OUT")
                        or pathlib.Path(tempfile.gettempdir()) / "genealogy-tools")
    path.mkdir(parents=True, exist_ok=True)
    return path


def scan(ident: str, page: int) -> pathlib.Path:
    """Скан страницы; вычищенный дотягивается из библиотеки."""
    from fetch import ensure_page
    return ensure_page(ident, page)


def open_scan(ident: str, page: int):
    from PIL import Image

    import docstore
    docstore.allow_big_scans()
    return Image.open(scan(ident, page))
