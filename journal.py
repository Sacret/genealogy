#!/usr/bin/env python3
"""Сводный журнал поисков по всем документам -> journal.html в корне.

HTML здесь — представление, а не хранилище. Источник истины остаётся
в <документ>/searches.jsonl: дописывание в него атомарно и переживает
обрыв процесса, тогда как перезапись общего файла — нет. Журнал
пересобирается целиком при каждом поиске.
"""

import html
import pathlib
import re
from collections import OrderedDict

from docstore import ROOT, documents, load_meta, read_log

STATUS = {
    "found":   ("найдена",      "ok"),
    "absent":  ("не найдена",   "no"),
    "unclear": ("не проверена", "wait"),
}

CSS = """
:root {
  --bg: #faf8f5; --card: #fff; --ink: #1c1a17; --dim: #6b6560;
  --line: #e3ddd4; --accent: #7a4a2b;
  --ok-bg: #e6f0e4; --ok-ink: #2f5c28;
  --no-bg: #ece9e5; --no-ink: #6b6560;
  --wait-bg: #f7ecd8; --wait-ink: #8a5f18;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #171513; --card: #201d1a; --ink: #ece7e0; --dim: #9a918a;
    --line: #322d28; --accent: #d09a6e;
    --ok-bg: #1f3320; --ok-ink: #9ed095;
    --no-bg: #2a2622; --no-ink: #9a918a;
    --wait-bg: #3a2e18; --wait-ink: #e0b463;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 40px 24px 80px; background: var(--bg); color: var(--ink);
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.wrap { max-width: 1000px; margin: 0 auto; }
h1 { font-size: 26px; font-weight: 600; margin: 0 0 4px; letter-spacing: -.01em; }
.sub { color: var(--dim); margin: 0 0 28px; font-size: 14px; }

.stats { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 28px; }
.stat {
  background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 12px 18px; min-width: 108px;
}
.stat b { display: block; font-size: 24px; font-weight: 600; letter-spacing: -.02em; }
.stat span { color: var(--dim); font-size: 12px; text-transform: uppercase;
             letter-spacing: .06em; }

#filter {
  width: 100%; padding: 11px 14px; margin-bottom: 26px; font: inherit;
  background: var(--card); color: var(--ink);
  border: 1px solid var(--line); border-radius: 9px;
}
#filter:focus { outline: 2px solid var(--accent); outline-offset: -1px; }

.doc { margin-bottom: 34px; }
.doc h2 { font-size: 17px; font-weight: 600; margin: 0 0 3px; }
.doc .meta { color: var(--dim); font-size: 13px; margin-bottom: 8px; }
.cov { font-size: 12.5px; color: var(--dim); margin-bottom: 12px;
       max-width: 80ch; line-height: 1.5; }
.cov .badge { margin-right: 6px; }
.doc .meta a { color: var(--accent); }

details.searches { margin: 0; }
details.searches summary {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 10px 14px; cursor: pointer; list-style: none;
  background: var(--card); border: 1px solid var(--line); border-radius: 10px;
}
details.searches summary::-webkit-details-marker { display: none; }
details.searches summary::before {
  content: ''; flex: none; width: 0; height: 0;
  border-left: 5px solid var(--dim);
  border-top: 4px solid transparent; border-bottom: 4px solid transparent;
  transition: transform .15s ease;
}
details.searches[open] summary::before { transform: rotate(90deg); }
details.searches summary:hover { border-color: var(--accent); }
details.searches summary:focus-visible { outline: 2px solid var(--accent);
                                         outline-offset: -1px; }
/* Раскрытая шапка срастается с таблицей в одну карточку: общий контур,
   а нижняя граница шапки работает разделителем перед строкой заголовков. */
details.searches[open] summary { border-radius: 10px 10px 0 0; }
details.searches[open] table { border-radius: 0 0 10px 10px; border-top: none; }
.sum-badge { font-weight: 600; }
.sum-count { margin-left: auto; color: var(--dim); font-size: 12.5px;
             white-space: nowrap; }

table { width: 100%; border-collapse: collapse; background: var(--card);
        border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
th { text-align: left; font-size: 11.5px; text-transform: uppercase;
     letter-spacing: .06em; color: var(--dim); font-weight: 600;
     padding: 10px 14px; border-bottom: 1px solid var(--line); white-space: nowrap; }
td { padding: 11px 14px; border-top: 1px solid var(--line); vertical-align: top; }
tr.hidden { display: none; }
.surname { font-weight: 600; white-space: nowrap; }
.when, .num { color: var(--dim); font-size: 13px; white-space: nowrap; }
.num { text-align: right; font-variant-numeric: tabular-nums; }

.badge { display: inline-block; padding: 2px 9px; border-radius: 20px;
         font-size: 12px; font-weight: 600; white-space: nowrap; }
.badge.ok { background: var(--ok-bg); color: var(--ok-ink); }
.badge.no { background: var(--no-bg); color: var(--no-ink); }
.badge.wait { background: var(--wait-bg); color: var(--wait-ink); }
.note { color: var(--dim); font-size: 13px; margin-top: 5px; max-width: 62ch; }

.pages { font-size: 13px; line-height: 1.9; }
.pages a { color: var(--accent); text-decoration: none;
           border-bottom: 1px solid transparent; }
.pages a:hover { border-bottom-color: var(--accent); }
.pages a.hit { font-weight: 700; }
.empty { color: var(--dim); }
footer { color: var(--dim); font-size: 12.5px; margin-top: 40px;
         border-top: 1px solid var(--line); padding-top: 14px; }
"""

JS = """
const box = document.getElementById('filter');
box.addEventListener('input', () => {
  const q = box.value.trim().toLowerCase();
  document.querySelectorAll('tbody tr').forEach(tr => {
    tr.classList.toggle('hidden', q && !tr.dataset.k.includes(q));
  });
  document.querySelectorAll('.doc').forEach(d => {
    const any = d.querySelectorAll('tbody tr:not(.hidden)').length;
    d.style.display = any ? '' : 'none';
  });
  // Свёрнутая таблица прячет как раз то, что искали, поэтому на время
  // фильтра совпадения раскрываются сами. Что человек открыл руками до
  // фильтра, запоминается и возвращается, когда поле опустеет.
  document.querySelectorAll('details.searches').forEach(det => {
    if (q) {
      if (det.dataset.was === undefined) det.dataset.was = det.open ? '1' : '';
      det.open = true;
    } else if (det.dataset.was !== undefined) {
      det.open = det.dataset.was === '1';
      delete det.dataset.was;
    }
  });
});
"""


def _favicon() -> str:
    """Ять на тёмном поле — см. icon.py. Если модуля нет, обходимся без."""
    try:
        import icon
        return icon.data_uri()
    except Exception:
        return ""


def e(s):
    return html.escape(str(s), quote=True)


def plural(n, one, few, many):
    """Русское склонение при числительном: 1 поиск, 2 поиска, 5 поисков."""
    if 11 <= n % 100 <= 14:
        return many
    d = n % 10
    return one if d == 1 else few if 2 <= d <= 4 else many


def coverage(ident):
    """Насколько распознаванию этого документа можно верить.

    Отрицательный результат ("фамилия не встречается") имеет силу только
    там, где текст читаем. Без этой строки журнал обещает больше, чем
    обосновано: 29% страниц bv0000386 Tesseract читает плохо, и именно
    на такой странице был пропущен "Могучевъ".
    """
    import json as _json
    d = ROOT / ident
    qf = d / "quality.json"
    if not qf.exists():
        return None
    q = _json.loads(qf.read_text(encoding="utf-8"))
    weak = set(q.get("weak", []))
    bands = d / "ocr_bands"
    rescued = {int(f.stem[1:]) for f in bands.glob("p*.txt")} if bands.exists() else set()
    return {"total": len(q.get("pages", {})), "weak": len(weak),
            "rescued": len(weak & rescued)}


def collect():
    """Документы -> список поисков с приклеенным последним вердиктом."""
    docs = OrderedDict()
    for ident in documents():
        meta = load_meta(ident)
        log = read_log(ident)
        verdicts = {r["surname"]: r for r in log if r.get("type") == "verdict"}
        # Только последний поиск по каждой фамилии: повторные прогоны
        # (после починки конвейера, с другим порогом) иначе дублируют
        # строку и тянут за собой один и тот же вердикт. Полная история
        # остаётся в searches.jsonl.
        latest = {}
        for r in log:
            if r.get("type", "search") != "search":
                continue
            latest[r["surname"]] = r
        rows = []
        for r in latest.values():
            v = verdicts.get(r["surname"])
            rows.append({**r,
                         "status": (v or {}).get("status", "unclear"),
                         "verdict": (v or {}).get("verdict", "")})
        rows.sort(key=lambda r: r["date"])
        if rows or meta:
            docs[ident] = {"meta": meta, "rows": rows,
                           "coverage": coverage(ident)}
    return docs


def render(docs) -> str:
    searches = [r for d in docs.values() for r in d["rows"]]
    names = {r["surname"].lower() for r in searches}
    found = sum(1 for r in searches if r["status"] == "found")
    absent = sum(1 for r in searches if r["status"] == "absent")
    todo = sum(1 for r in searches if r["status"] == "unclear")

    out = ["<!doctype html><html lang=ru><head><meta charset=utf-8>",
           "<meta name=viewport content='width=device-width,initial-scale=1'>",
           "<title>Журнал поисков</title>",
           f"<link rel='icon' href='{_favicon()}'>",
           f"<style>{CSS}</style></head><body>",
           "<div class=wrap>",
           "<h1>Журнал поисков</h1>",
           "<p class=sub>Дореволюционные документы: какие фамилии по каким "
           "делам уже проверены.</p>",
           "<div class=stats>",
           f"<div class=stat><b>{len(docs)}</b><span>документов</span></div>",
           f"<div class=stat><b>{len(searches)}</b><span>поисков</span></div>",
           f"<div class=stat><b>{len(names)}</b><span>фамилий</span></div>",
           f"<div class=stat><b>{found}</b><span>найдено</span></div>",
           f"<div class=stat><b>{absent}</b><span>не найдено</span></div>",
           f"<div class=stat><b>{todo}</b><span>не проверено</span></div>",
           "</div>",
           "<input id=filter type=search placeholder='Фильтр по фамилии, "
           "документу или странице…' autocomplete=off>"]

    for ident, d in docs.items():
        meta, rows = d["meta"], d["rows"]
        title = meta.get("title") or ident
        url = meta.get("url", "")
        out.append("<section class=doc>")
        out.append(f"<h2>{e(title)}</h2>")
        bits = [f"<code>{e(ident)}</code>"]
        if url:
            bits.append(f"<a href='{e(url)}/view/' target=_blank>{e(url)}</a>")
        if meta.get("pages"):
            bits.append(f"{meta['pages']} стр.")
        if meta.get("dpi"):
            bits.append(f"{meta['dpi']} dpi")
        out.append(f"<div class=meta>{' · '.join(bits)}</div>")

        cov = d.get("coverage")
        if cov and cov["total"]:
            ok = cov["total"] - cov["weak"]
            pct = ok / cov["total"]
            cls = "ok" if pct >= 0.85 else ("wait" if pct >= 0.6 else "no")
            out.append(
                "<div class=cov><span class='badge " + cls + "'>"
                + f"читаемо {pct:.0%}</span> {ok} стр. распознаны надёжно, "
                + f"{cov['weak']} — нет (из них {cov['rescued']} перечитаны "
                + "полосами). «Не найдена» на ненадёжной странице не "
                + "гарантирует отсутствия.</div>")
        elif meta.get("pruned"):
            out.append("<div class=cov><span class='badge wait'>не измерено</span>"
                       " сканы вычищены — для замера надёжности их нужно "
                       "дотянуть заново.</div>")

        if not rows:
            out.append("<p class=empty>По этому документу ещё ничего не искали.</p>"
                       "</section>")
            continue

        # Таблица свёрнута: на виду остаётся ответ по каждой фамилии, а
        # подробности — даты, число кандидатов, страницы, текст вердикта —
        # разворачиваются по клику. Документов много, и при развёрнутых
        # таблицах главный вопрос к журналу («искали ли это и чем кончилось»)
        # тонет в подробностях проверки.
        rank = {"found": 0, "unclear": 1, "absent": 2}
        chips = []
        for r in sorted(rows, key=lambda r: (rank[r["status"]], r["surname"].lower())):
            label, cls = STATUS[r["status"]]
            chips.append(f"<span class='badge {cls} sum-badge'>"
                         f"{e(r['surname'])} — {label}</span>")
        word = plural(len(rows), "поиск", "поиска", "поисков")
        out.append("<details class=searches><summary>" + "".join(chips)
                   + f"<span class=sum-count>{len(rows)} {word}</span></summary>")
        out.append("<table><thead><tr><th>Дата</th><th>Фамилия</th>"
                   "<th class=num>Кандидатов</th><th>Страницы</th>"
                   "<th>Итог</th></tr></thead><tbody>")
        for r in rows:
            label, cls = STATUS[r["status"]]
            pages = r.get("pages_with_hits") or []
            # Страницы, названные в вердикте, выделены жирным: именно они
            # подтверждены глазами, остальные — кандидаты, которые отсеялись.
            confirmed = set()
            if r["status"] == "found":
                confirmed = set(re.findall(r"стр\.?\s*(\d+)",
                                           r.get("verdict", ""), re.I))
            links = []
            for p in pages:
                cl = " class=hit" if p in confirmed else ""
                links.append(f"<a{cl} href='{e(url)}/view/?#page={e(p)}' "
                             f"target=_blank>{e(p)}</a>")
            key = " ".join([r["surname"], title, ident, *pages]).lower()
            out.append(f"<tr data-k='{e(key)}'>")
            out.append(f"<td class=when>{e(r['date'][:16].replace('T', ' '))}</td>")
            out.append(f"<td class=surname>{e(r['surname'])}</td>")
            out.append(f"<td class=num>{r['hits']}</td>")
            out.append(f"<td class=pages>{', '.join(links) or '—'}</td>")
            note = (f"<div class=note>{e(r['verdict'])}</div>" if r["verdict"] else "")
            out.append(f"<td><span class='badge {cls}'>{label}</span>{note}</td>")
            out.append("</tr>")
        out.append("</tbody></table></details></section>")

    out.append("<footer>Пересобирается автоматически при каждом поиске. "
               "Источник — <code>&lt;документ&gt;/searches.jsonl</code>.</footer>")
    out.append(f"</div><script>{JS}</script></body></html>")
    return "\n".join(out)


def rebuild() -> pathlib.Path:
    dst = ROOT / "journal.html"
    dst.write_text(render(collect()), encoding="utf-8")
    return dst


if __name__ == "__main__":
    print(rebuild())
