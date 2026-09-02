#!/usr/bin/env python3
"""Пакетное распознавание документа. Возобновляемое, параллельное по ядрам."""

import argparse, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

from docstore import doc_dir, load_meta


def run(job):
    img, dst, lang, psm = job
    if dst.exists() and dst.stat().st_size > 0:
        return True
    out = subprocess.run(["tesseract", str(img), "-", "-l", lang, "--psm", psm],
                         capture_output=True, text=True)
    dst.write_text(out.stdout, encoding="utf-8")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ident", help="идентификатор документа, напр. bv0000407")
    ap.add_argument("--lang", default="rus")
    ap.add_argument("--psm", default="6")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--redo", action="store_true", help="распознать заново поверх старого")
    a = ap.parse_args()

    d = doc_dir(a.ident)
    out = d / "ocr"
    out.mkdir(parents=True, exist_ok=True)
    scans = sorted((d / "scans").glob("p*.jpg"))
    if not scans:
        sys.exit(f"нет сканов в {d / 'scans'} — сначала fetch.py")
    if a.redo:
        for f in out.glob("p*.txt"):
            f.unlink()

    jobs = [(f, out / (f.stem + ".txt"), a.lang, a.psm) for f in scans]
    print(f"{load_meta(a.ident).get('title', a.ident)}: {len(jobs)} страниц, "
          f"{a.lang} --psm {a.psm}")
    fresh = 0
    with ThreadPoolExecutor(a.jobs) as ex:
        for i, cached in enumerate(ex.map(run, jobs), 1):
            fresh += not cached
            if i % 50 == 0:
                print(f"  {i}/{len(jobs)}", file=sys.stderr)
    print(f"готово: {len(jobs)} страниц, заново распознано {fresh}")


if __name__ == "__main__":
    main()
