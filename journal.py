#!/usr/bin/env python3
"""Сводный журнал поисков по всем документам -> journal.html в корне.

HTML здесь — представление, а не хранилище. Источник истины остаётся
в <документ>/searches.jsonl: дописывание в него атомарно и переживает
обрыв процесса, тогда как перезапись общего файла — нет. Журнал
пересобирается целиком при каждом поиске.
"""

import base64
import html
import io
import pathlib
import re
from collections import OrderedDict

from docstore import (ROOT, documents, latest_verdicts, load_meta,
                      persons as roster, read_log)

STATUS = {
    "found":   ("найдена",      "ok"),
    "absent":  ("не найдена",   "no"),
    "unclear": ("не проверена", "wait"),
}

# Найденная фамилия ещё не значит найденный предок. Могучевыхъ из одной
# станицы в приказах несколько семей, и зелёный на всех разом обещает
# родство там, где его никто не устанавливал. Поэтому находка красится
# по полю `kin` вердикта: зелёным — только названные там страницы.
KIN_LABEL  = "найдена, родство подтверждено"
MAYBE_LABEL = "найдена, родство не установлено"

# Подтверждённое родство — это всегда чей-то предок поимённо, и журнал
# называет его и уводит на страницу родословной (persons.json). Иначе
# зелёная строка сообщает только «кто-то из семьи», а вопрос «кто именно
# и что о нём уже известно» остаётся без ответа ровно там, где на него
# есть ответ.
#
# Имя стоит в свёрнутой сводке и под вырезкой — то есть там, где итог
# читается сразу, и там, где находка видна глазом. В развёрнутой строке
# его нет: рядом с вердиктом человек и так назван, а третья ссылка на то
# же самое только загромождала бы разбор.

CSS = """
:root {
  --bg: #faf8f5; --card: #fff; --ink: #1c1a17; --dim: #6b6560;
  --line: #e3ddd4; --accent: #7a4a2b;
  --ok-bg: #e6f0e4; --ok-ink: #2f5c28;
  --maybe-bg: #e0eaf3; --maybe-ink: #2c5578;
  --no-bg: #ece9e5; --no-ink: #6b6560;
  --wait-bg: #f7ecd8; --wait-ink: #8a5f18;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #171513; --card: #201d1a; --ink: #ece7e0; --dim: #9a918a;
    --line: #322d28; --accent: #d09a6e;
    --ok-bg: #1f3320; --ok-ink: #9ed095;
    --maybe-bg: #1b2b39; --maybe-ink: #8fbede;
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
h1 { font-size: 26px; font-weight: 600; margin: 0 0 4px; letter-spacing: -.01em;
     display: flex; align-items: center; gap: 11px; }
/* Ять нарисован со скруглением и полем внутри самой картинки (icon.py),
   поэтому здесь ни рамки, ни радиуса не нужно. Кегль в два раза больше
   показанного — иначе на retina засечки мылятся. */
h1 .mark { width: 32px; height: 32px; flex: none; }
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

.doc { position: relative; margin-bottom: 34px; }
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
.badge.maybe { background: var(--maybe-bg); color: var(--maybe-ink); }
.badge.hit { background: var(--ok-bg); color: var(--ok-ink); }
.badge.no { background: var(--no-bg); color: var(--no-ink); }
.badge.wait { background: var(--wait-bg); color: var(--wait-ink); }
.person { display: inline-block; margin-left: 7px; font-size: 12px;
          font-weight: 600; color: var(--accent); text-decoration: none;
          border-bottom: 1px dotted currentColor; }
.person:hover { border-bottom-style: solid; }
.sum-badge + .person { margin-right: 4px; }
.doclink { color: var(--accent); text-decoration: none;
           border-bottom: 1px dotted currentColor; white-space: nowrap; }
.doclink:hover { border-bottom-style: solid; }
.note { color: var(--dim); font-size: 13px; margin-top: 5px; max-width: 62ch; }
/* Вердикт длинный, и в нём три разных голоса: мой пересказ, цитата из
   приказа и то, что автор выделил капслоком. Курсив и жирный разводят их
   по слоям, абзац отделяет разбор одного человека от другого. */
.note p { margin: 0 0 .7em; }
.note p:last-child { margin-bottom: 0; }
.note em { font-style: italic; color: var(--ink); }
.note strong { font-weight: 650; color: var(--ink); letter-spacing: .01em; }

.pages { font-size: 13px; line-height: 1.9; }
.pages a { color: var(--accent); text-decoration: none;
           border-bottom: 1px solid transparent; }
.pages a:hover { border-bottom-color: var(--accent); }
.pages a.hit { font-weight: 700; color: var(--ok-ink); }
.pages a.maybe { font-weight: 700; color: var(--maybe-ink); }

/* Полоса лет: год с документом кликабелен и окрашен итогом, год без
   документа — пустая клетка. Пробелы в ряду приказов видно сразу, а
   именно они говорят, куда идти дальше. */
.years { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 8px; }
.year {
  min-width: 52px; text-align: center; padding: 5px 6px; border-radius: 7px;
  font-size: 12px; font-variant-numeric: tabular-nums; text-decoration: none;
  border: 1px solid var(--line); background: var(--card); color: var(--dim);
  /* Клетки тянутся по высоте самой высокой в ряду, поэтому год центруется
     внутри клетки, а не держится на отступе: иначе клетка с цифрой дел
     поднимала бы ряд, и во всех соседних год оседал бы кверху. */
  display: inline-flex; align-items: center; justify-content: center;
  line-height: 1.2;
}
a.year:hover { border-color: var(--accent); }
.year.maybe { background: var(--maybe-bg); color: var(--maybe-ink);
              border-color: transparent; }
.year.ok { background: var(--ok-bg); color: var(--ok-ink); border-color: transparent;
           font-weight: 600; }
.year.no { background: var(--no-bg); color: var(--no-ink); border-color: transparent; }
.year.wait { background: var(--wait-bg); color: var(--wait-ink);
             border-color: transparent; }
.year.gap { background: transparent; border-style: dashed; opacity: .55; }
/* Цифра поднимается своим align-self, а не vertical-align: у флексового
   элемента vertical-align не работает вовсе, а в строке он растил бы
   строчный бокс — с этого и съезжал текст. */
.year .more { font-style: normal; font-size: 10px; opacity: .75;
              margin-left: 3px; align-self: flex-start; line-height: 1; }
/* Пустая метка года перед первым делом этого года: цель ссылки из полосы.
   Отступ сверху — чтобы заголовок дела не прилипал к краю окна. */
.year-mark { height: 0; scroll-margin-top: 16px; }

/* Видимая метка года у первого дела этого года — отдельно от якоря:
   якорь лежит снаружи секции, чтобы ссылка из полосы работала и при
   включённом фильтре, а надпись стоит внутри и вместе с секцией
   исчезает — иначе год висел бы над пустотой.
   Годы в журнале идут подряд, но заголовки дел названы книгами, а не
   годами, и на прокрутке ряд карточек читается как сплошной. Надпись
   размечает его на годы: в узком окне — строкой над делом, на широком
   экране — в пустом поле слева от колонки, где она ничего не двигает. */
.year-tag {
  display: flex; align-items: center; gap: 10px; margin: 0 0 12px;
  color: var(--dim); font-size: 12px; font-weight: 600;
  letter-spacing: .1em; font-variant-numeric: tabular-nums;
}
.year-tag::after { content: ''; flex: 1; height: 1px; background: var(--line); }
/* Поле слева существует, только когда колонка (1000px) и поля тела
   разошлись достаточно широко: при 1180px до края окна остаётся ещё
   с десяток пикселей, ниже — метка обрезалась бы. */
@media (min-width: 1180px) {
  .year-tag {
    display: block; position: absolute; left: -76px; top: 3px; width: 60px;
    margin: 0; text-align: right; font-size: 13px; letter-spacing: .04em;
  }
  .year-tag::after { display: none; }
}
.years-note { color: var(--dim); font-size: 12.5px; margin: 0 0 26px;
              max-width: 80ch; }

/* Вырезка из скана: слово, ради которого всё и делалось. Белая подложка —
   сканы серые, на тёмной теме иначе получается дыра. */
.crop { margin-top: 8px; }
.crop img { display: block; max-width: 100%; border: 1px solid var(--line);
            border-radius: 8px; background: #fff; padding: 4px; }
.crop .cap { color: var(--dim); font-size: 12px; margin-top: 4px; }
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

// Ссылка на родословную стоит и в свёрнутой сводке, внутри <summary>:
// без этого клик по имени заодно схлопывал бы таблицу, которую человек
// как раз открыл, чтобы прочесть вердикт.
document.querySelectorAll('summary .person').forEach(a => {
  a.addEventListener('click', e => e.stopPropagation());
});

// Таблицы свёрнуты, и ссылка на соседнее дело приводила бы к закрытой
// карточке: пришли читать вердикт, а видно только заголовок. Поэтому
// дело, на которое указывает якорь, раскрывается само — и при переходе
// по ссылке, и при открытии страницы с готовым #bv0000386 в адресе.
function openTarget() {
  const id = decodeURIComponent(location.hash.slice(1));
  const sec = id && document.getElementById(id);
  const det = sec && sec.querySelector('details.searches');
  if (det) det.open = true;
}
addEventListener('hashchange', openTarget);
openTarget();
"""


def _favicon() -> str:
    """Ять на тёмном поле — см. icon.py. Если модуля нет, обходимся без."""
    try:
        import icon
        return icon.data_uri()
    except Exception:
        return ""


def _mark(px=64) -> str:
    """Ять для заголовка — отдельно от favicon.

    Favicon отдаётся как .ico из трёх мелких размеров: в 16 px у ятя
    слипаются засечки, и icon.py рисует для него особый, упрощённый
    вариант. В заголовке места вдвое больше, поэтому берём обычную
    отрисовку крупным кеглем и отдаём PNG.
    """
    try:
        import icon
        buf = io.BytesIO()
        icon.render(px).save(buf, "PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
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


# --- разметка вердикта -------------------------------------------------
#
# Вердикт вычитывается глазами и потому длинный: цитата из приказа, разбор
# отклонённых кандидатов, оговорка о полноте. Сплошным абзацем всё это
# читается плохо, а главное — цитата из документа неотличима от моего
# пересказа. Разметка ставится здесь, при сборке страницы, а не хранится
# в searches.jsonl: источник истины остаётся простым текстом, и правило
# задним числом приводит в порядок все вердикты, включая записанные
# годы назад.
#
# Курсив — цитаты: «…» это выписка из приказа, '…' — распознанная форма
# слова. Жирный — то, что автор вердикта уже выделил капслоком: сам итог
# («НЕ НАЙДЕНА»), имя человека в разборе, оговорки вроде «СТАНИЦА НЕ
# НАЗВАНА». Своей эмфазы журнал не придумывает.

# Апострофы в вердиктах ставятся парами, и пары считаются слева направо,
# поэтому длину цитаты ограничивать не нужно — а нельзя: потолок в 80
# знаков пропускал длинную выписку (bv0000407, стр. 494) и спаривал её
# закрывающий апостроф со следующим открывающим, так что курсивом
# оказывалась не цитата, а мой текст между двумя цитатами.
# Вердикты постоянно ссылаются на соседние тома: «ТОТ ЖЕ ЧЕЛОВЕК, что в
# bv0000386 (1873, стр. 208)». В журнале все дела лежат на одной странице,
# так что такой номер — готовая ссылка на якорь соседней карточки, и
# читать цепочку находок можно не листая глазами.
DOC_ID = re.compile(r"\b(?:bv|ot)\d{7}\b")

QUOTE = re.compile(r"«[^»]*»|'[^']*'")

# Капслоком набирается только кириллица: латинские CAPS в вердиктах —
# это OCR и bv-номера, выделять их незачем. Дореформенные прописные
# входят в набор: без них «АЛЕКСѢЙ МОГУЧЕВЪ» разваливается надвое, и
# ять посередине остаётся невыделенным.
CAP = "А-ЯЁІѢѲѴ"
CAPS = re.compile(rf"[{CAP}]{{2,}}(?:[ \u00a0-]+[{CAP}]+)*")

# Абзац начинается с номера разбираемого человека — «(1)», «(2)» —
# или с капслочной врезки. Кроме них у вердиктов есть устойчивые зачины
# проверочной части: с них начинается не новая мысль, а новый раздел.
BREAK = re.compile(
    rf"(?<=[.!?])\s+(?=\(\d\)\s|[{CAP}]{{2,}}(?:[ \u00a0-]+[{CAP}]+)+[ ,.:—]"
    rf"|[{CAP}]{{2,}}(?:[ \u00a0-]+[{CAP}]+)*:"
    r"|Отклонен|Проверочные поиски|Режим --short|Сверены все|Счёт по)")


def markup(text: str, known=(), skip=None) -> str:
    """Текст вердикта -> HTML: цитаты курсивом, капслок жирным, абзацы.

    `known` — дела, которые есть на этой же странице: только их номера
    становятся ссылками. Ссылка на якорь, которого нет, ведёт в никуда
    и молча: лучше оставить номер текстом. `skip` — само это дело, на
    себя ссылаться незачем.
    """
    def link(s):
        """s уже экранирован: подставляем якоря в готовый HTML."""
        return DOC_ID.sub(
            lambda m: (f"<a class=doclink href='#{m.group()}'>{m.group()}</a>"
                       if m.group() in known and m.group() != skip
                       else m.group()), s)

    def inline(s):
        out, pos = [], 0
        for m in QUOTE.finditer(s):
            out.append(caps(s[pos:m.start()]))
            # Кавычки-ёлочки — часть цитаты и остаются, а прямые апострофы
            # были в тексте заменой курсиву: раз курсив теперь настоящий,
            # они только сорят.
            q = m.group()
            q = q[1:-1] if q[0] == "'" else q
            out.append(f"<em>{link(e(q))}</em>")
            pos = m.end()
        out.append(caps(s[pos:]))
        return "".join(out)

    def caps(s):
        out, pos = [], 0
        for m in CAPS.finditer(s):
            out.append(link(e(s[pos:m.start()])))
            out.append(f"<strong>{e(m.group())}</strong>")
            pos = m.end()
        out.append(link(e(s[pos:])))
        return "".join(out)

    return "".join(f"<p>{inline(p.strip())}</p>"
                   for p in BREAK.split(text.strip()) if p.strip())


def doc_year(meta) -> int | None:
    """Год документа из заголовка: «[Приказы ...]: [за 1873 год]» -> 1873.

    Годы нужны затем, что документы называются bv0000386, bv0000392,
    bv0000407 — и номер дела в библиотеке не имеет ничего общего с
    хронологией: 407 это 1897 год, а 392 — 1888. Отсортированный по
    номерам журнал перемешивает эпохи и прячет главное, ради чего он
    читается: за какие годы уже смотрели.
    """
    m = re.search(r"\b(1[6-9]\d{2})\b", meta.get("title") or "")
    return int(m.group(1)) if m else None


def _page_key(p):
    """Номера страниц идут по-числовому: '99' раньше '104', а не наоборот."""
    return (0, int(p)) if str(p).isdigit() else (1, 0, str(p))


def confirmed_pages(r):
    """Страницы находки и подмножество тех, где родство установлено.

    Для старых вердиктов, записанных без поля `confirmed`, номера всё ещё
    разбираются из текста — он врёт, но реже, чем пустота на месте находки.
    Поля `kin` у них нет вовсе, и такая находка честно показывается как
    «родство не установлено»: молчание — не подтверждение.
    """
    if r["status"] != "found":
        return set(), set()
    conf = set(r.get("confirmed")
               or re.findall(r"стр\.?\s*(\d+)", r.get("verdict", ""), re.I))
    kin = set(r.get("kin") or []) & conf
    return conf, kin


def row_badge(r):
    """Подпись и цвет итога. Находка расщепляется по установленному родству."""
    label, cls = STATUS[r["status"]]
    if r["status"] != "found":
        return label, cls
    _, kin = confirmed_pages(r)
    return (KIN_LABEL, "ok") if kin else (MAYBE_LABEL, "maybe")


def kin_people(r):
    """Кто найден: записи реестра для страниц с подтверждённым родством.

    Порядок — по номеру страницы, повторы убраны: один человек, найденный
    на трёх страницах тома, называется один раз. Неизвестный
    идентификатор молча пропускается — реестр правится руками, и
    опечатка в нём не должна ронять сборку журнала.
    """
    known = roster()
    _, kin = confirmed_pages(r)
    by_page = r.get("persons") or {}
    out = OrderedDict()
    for page in sorted(kin, key=int):
        pid = by_page.get(page)
        if pid in known:
            out[pid] = known[pid]
    return out


def person_link(pid, who):
    title = " · ".join(x for x in (who.get("годы"), who.get("кто")) if x)
    return (f"<a class=person href='{e(who.get('url', ''))}' target=_blank "
            f"title='{e(title)}'>{e(who.get('имя', pid))}</a>")


def person_links(r):
    return "".join(person_link(pid, who) for pid, who in kin_people(r).items())


def crops_for(ident, surname, pages):
    """Вырезки подтверждённых находок: <документ>/crops/pNNNN_основа_N.png.

    Только страницы, названные в вердикте: в crops/ лежат и отсеявшиеся
    кандидаты, а показывать их рядом со словом «найдена» — значит выдавать
    посторонние слова за находку.
    """
    try:
        from surnamefind.search import stem_query
        stem, _ = stem_query(surname)   # второе — позиции дореформенных литер
    except Exception:
        return []
    d = ROOT / ident / "crops"
    if not d.exists():
        return []
    out = []
    for page in sorted(pages, key=int):
        for f in sorted(d.glob(f"p{int(page):04d}_{stem}_*.png")):
            out.append((int(page), f))
    return out


def thumb_uri(path: pathlib.Path, max_w=620) -> str:
    """Вырезка внутрь страницы, data:URI.

    Журнал должен открываться сам по себе, без соседних папок: его
    показывают как результат работы, и картинка, отвалившаяся из-за
    относительного пути, обесценивает именно ту строку, ради которой
    всё делалось. Полноразмерный файл остаётся в crops/ и доступен
    по ссылке рядом.
    """
    try:
        from PIL import Image
        im = Image.open(path)
        if im.width > max_w:
            im = im.resize((max_w, round(im.height * max_w / im.width)),
                           Image.LANCZOS)
        buf = io.BytesIO()
        im.convert("L").save(buf, "PNG", optimize=True)
        data = buf.getvalue()
    except Exception:
        data = path.read_bytes()
    return "data:image/png;base64," + base64.b64encode(data).decode()


# Чем меньше, тем важнее показать: за год могло быть два дела, и полоса
# должна назвать лучший исход, а не последний по алфавиту.
RANK = {"ok": 0, "maybe": 1, "wait": 2, "no": 3, "gap": 4}


def year_anchor(y: int) -> str:
    """Имя якоря года. Отдельное от имён дел: `bv0000407` — это дело, а
    `g1897` — год, и по году может лежать не одно дело."""
    return f"g{y}"


def year_strip(docs) -> str:
    """Сплошной ряд лет от первого до последнего: где документ, где пробел.

    Клетка ведёт на год, а не на дело. Пока журнал состоял из одних
    приказов, год и дело были одним и тем же, и клетка вела прямо на
    `bv...`. С адрес-календарями год перестаёт быть уникальным: «Приказы
    за 1899» и «Памятная книжка на 1899» — разные дела одного года, и
    ссылка на дело увела бы мимо половины года. Цвет при этом берётся по
    лучшему исходу за год: если родственник найден хоть в одной книге,
    год зелёный.
    """
    years, counts = {}, {}
    for ident, d in docs.items():
        y = d.get("year")
        if y is None:
            continue
        counts[y] = counts.get(y, 0) + 1
        st = {r["status"] for r in d["rows"]}
        kin = any(confirmed_pages(r)[1] for r in d["rows"])
        cls = ("ok" if kin else
               "maybe" if "found" in st else
               "wait" if (not st or "unclear" in st) else "no")
        # Два дела за один год — берём лучший исход: год всё равно проверен.
        if RANK.get(cls, 9) < RANK.get(years.get(y, "gap"), 9):
            years[y] = cls
    if not years:
        return ""
    lo, hi = min(years), max(years)
    cells = []
    for y in range(lo, hi + 1):
        if y in years:
            n = counts[y]
            title = (f" title='{n} {plural(n, 'дело', 'дела', 'дел')} "
                     f"за этот год'" if n > 1 else "")
            cells.append(f"<a class='year {years[y]}' "
                         f"href='#{year_anchor(y)}'{title}>{y}"
                         + (f"<i class=more>{n}</i>" if n > 1 else "")
                         + "</a>")
        else:
            cells.append(f"<span class='year gap' title='не смотрели'>{y}</span>")
    seen, gaps = sum(counts.values()), (hi - lo + 1) - len(years)
    note = (f"{lo}—{hi}: просмотрено {seen} "
            f"{plural(seen, 'дело', 'дела', 'дел')} за {len(years)} "
            f"{plural(len(years), 'год', 'года', 'лет')}, "
            f"{gaps} {plural(gaps, 'год', 'года', 'лет')} в промежутке "
            "не открывали." if gaps else
            f"{lo}—{hi}: сплошь, без пробелов, {seen} "
            f"{plural(seen, 'дело', 'дела', 'дел')}.")
    multi = (" Цифра в клетке — сколько дел за этот год; цвет по лучшему "
             "из них." if any(n > 1 for n in counts.values()) else "")
    return ("<div class=years>" + "".join(cells) + "</div>"
            f"<p class=years-note>{note}{multi} Зелёный — найден человек, чьё "
            "родство установлено; синий — фамилия найдена, но это "
            "однофамилец или родство не доказано; серый — искали и не "
            "нашли, пунктир — дело не смотрели.</p>")


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
        verdicts = latest_verdicts(ident)
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
                         "verdict": (v or {}).get("verdict", ""),
                         "confirmed": (v or {}).get("confirmed"),
                         "kin": (v or {}).get("kin"),
                         "persons": (v or {}).get("persons")})
        rows.sort(key=lambda r: r["date"])
        if rows or meta:
            docs[ident] = {"meta": meta, "rows": rows,
                           "coverage": coverage(ident),
                           "year": doc_year(meta)}
    # По году, а не по номеру дела: 407 это 1897-й, а 392 — 1888-й.
    # Дела без года в заголовке уходят в конец, порядок между ними прежний.
    return OrderedDict(sorted(docs.items(),
                              key=lambda kv: (kv[1]["year"] is None,
                                              kv[1]["year"] or 0, kv[0])))


def render(docs) -> str:
    mark = _mark()
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
           "<h1>" + (f"<img class=mark alt='' src='{mark}'>" if mark else "")
           + "Журнал поисков</h1>",
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
           year_strip(docs),
           "<input id=filter type=search placeholder='Фильтр по фамилии, "
           "документу или странице…' autocomplete=off>"]

    # Якорь года ставится перед первым делом этого года. Дела уже
    # отсортированы по годам, так что «первое» — это просто смена года.
    # Якорь стоит снаружи секции нарочно: фильтр по фамилии прячет саму
    # секцию, а ссылка из полосы лет должна вести куда-то и тогда.
    seen_year = object()
    for ident, d in docs.items():
        meta, rows = d["meta"], d["rows"]
        title = meta.get("title") or ident
        url = meta.get("url", "")
        starts_year = bool(d.get("year")) and d["year"] != seen_year
        if starts_year:
            seen_year = d["year"]
            out.append(f"<div class=year-mark id='{year_anchor(seen_year)}'></div>")
        out.append(f"<section class=doc id='{e(ident)}'>")
        # Год уже назван в строке под заголовком, так что метка — чистая
        # навигация глазом, и читалке её повторять незачем.
        if starts_year:
            out.append(f"<div class=year-tag aria-hidden=true>{seen_year}</div>")
        out.append(f"<h2>{e(title)}</h2>")
        bits = [f"<code>{e(ident)}</code>"]
        if d.get("year"):
            bits.insert(0, f"<b>{d['year']}</b>")
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
            label, cls = row_badge(r)
            chips.append(f"<span class='badge {cls} sum-badge'>"
                         f"{e(r['surname'])} — {label}</span>"
                         + person_links(r))
        word = plural(len(rows), "поиск", "поиска", "поисков")
        out.append("<details class=searches><summary>" + "".join(chips)
                   + f"<span class=sum-count>{len(rows)} {word}</span></summary>")
        out.append("<table><thead><tr><th>Дата</th><th>Фамилия</th>"
                   "<th class=num>Кандидатов</th><th>Страницы</th>"
                   "<th>Итог</th></tr></thead><tbody>")
        for r in rows:
            label, cls = row_badge(r)
            pages = r.get("pages_with_hits") or []
            # Страницы с подтверждённой находкой выделены жирным, и только
            # к ним журнал подставляет вырезку. Берутся они из поля
            # `confirmed` вердикта: вытаскивать номера из его текста
            # (что делалось раньше) нельзя — там названы и отклонённые
            # кандидаты, и находки в соседних томах, так что 'Текучевъ'
            # на стр. 336 попадал в журнал как найденный Могучевъ.
            # Для старых вердиктов, записанных без поля, остаётся разбор
            # текста: он врёт, но реже, чем пустота на месте находки.
            confirmed, kin = confirmed_pages(r)
            # Подтверждённая страница может не значиться среди кандидатов:
            # поиск её не нашёл, а глаз нашёл. Так вышло с фельдшером
            # Могучевым на стр. 104 тома bv0000039 — склейка переносов
            # съела фамилию как токен, страница в журнал не попала вовсе,
            # и находка выглядела списком отклонённых кандидатов без
            # единого выделенного номера. Поэтому список страниц — это
            # объединение кандидатов с подтверждёнными.
            pages = sorted(set(pages) | confirmed, key=_page_key)
            links = []
            for p in pages:
                cl = (" class=hit" if p in kin else
                      " class=maybe" if p in confirmed else "")
                links.append(f"<a{cl} href='{e(url)}/view/?#page={e(p)}' "
                             f"target=_blank>{e(p)}</a>")
            key = " ".join([r["surname"], title, ident, *pages]).lower()
            out.append(f"<tr data-k='{e(key)}'>")
            out.append(f"<td class=when>{e(r['date'][:16].replace('T', ' '))}</td>")
            out.append(f"<td class=surname>{e(r['surname'])}</td>")
            out.append(f"<td class=num>{r['hits']}</td>")
            out.append(f"<td class=pages>{', '.join(links) or '—'}</td>")
            note = (f"<div class=note>"
                    f"{markup(r['verdict'], docs.keys(), ident)}</div>"
                    if r["verdict"] else "")
            shots = ""
            people = roster()
            for page, f in crops_for(ident, r["surname"], confirmed):
                who = ("родство подтверждено" if str(page) in kin
                       else "родство не установлено")
                mark_cls = "hit" if str(page) in kin else "maybe"
                pid = (r.get("persons") or {}).get(str(page))
                named = (person_link(pid, people[pid]) if pid in people else "")
                shots += (f"<div class=crop><img alt='{e(r['surname'])}, "
                          f"стр. {page}' src='{thumb_uri(f)}'>"
                          f"<div class=cap>стр. {page} — вырезка из скана, "
                          f"<a href='{e(f.relative_to(ROOT))}'>полный размер</a>"
                          f" · <span class='badge {mark_cls}'>{who}</span>"
                          f"{named}</div></div>")
            out.append(f"<td><span class='badge {cls}'>{label}</span>{note}{shots}</td>")
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
