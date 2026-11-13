#!/usr/bin/env python3
"""Пересобрать журнал и каталог так, как их соберёт CI на чистой копии.

    tools/rebuild_committed.py pn0030596 pn0030595

Пока параллельная цепочка качает следующие выпуски, их тома уже лежат в
`vol/` и попадают в `documents.json` как «в работе», а в журнал — как
«не проверена». В коммит они не идут, и CI на чистой копии собирает другие
байты. Названные тома здесь считаются несуществующими: их путь подменяется
на пустой, а из списка документов они выпадают.
"""

import argparse

import _root  # noqa: F401
import docstore


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("hide", nargs="+", help="тома, которых нет в коммите")
    hidden = set(ap.parse_args().hide)

    real_dir, real_docs = docstore.doc_dir, docstore.documents

    def doc_dir(ident):
        path = real_dir(ident)
        return path.with_name(f".скрыт-{ident}") if ident in hidden else path

    def documents():
        return [i for i in real_docs() if i not in hidden]

    import catalog
    import journal
    for mod in (docstore, catalog, journal):
        for name, fake in (("doc_dir", doc_dir), ("documents", documents)):
            if hasattr(mod, name):
                setattr(mod, name, fake)
    journal.rebuild()
    catalog.refresh()


if __name__ == "__main__":
    main()
