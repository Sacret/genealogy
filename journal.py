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

import boxes
from docstore import (ROOT, documents, latest_verdicts, load_meta,
                      meta_year, persons as roster, read_log)

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
  --bar: rgba(250, 248, 245, .86);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #171513; --card: #201d1a; --ink: #ece7e0; --dim: #9a918a;
    --line: #322d28; --accent: #d09a6e;
    --ok-bg: #1f3320; --ok-ink: #9ed095;
    --maybe-bg: #1b2b39; --maybe-ink: #8fbede;
    --no-bg: #2a2622; --no-ink: #9a918a;
    --wait-bg: #3a2e18; --wait-ink: #e0b463;
    --bar: rgba(23, 21, 19, .88);
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

/* Телефон: шесть счётчиков ложатся сеткой по два в ряд. Строкой они
   разбиваются по длине подписи — «поисков» и «не проверено» выходят
   разной ширины, и ряды не совпадают краями. В сетке колонки равны,
   а число под ними всё равно читается слева. */
@media (max-width: 700px) {
  .stats { display: grid; grid-template-columns: 1fr 1fr; }
  .stat { min-width: 0; }
}

#filter {
  width: 100%; padding: 11px 14px; margin-bottom: 10px; font: inherit;
  background: var(--card); color: var(--ink);
  border: 1px solid var(--line); border-radius: 9px;
}
#filter:focus { outline: 2px solid var(--accent); outline-offset: -1px; }

/* Фильтр по итогу. Чипы повторяют цвета итоговых пузырей — серый,
   синий, зелёный, — чтобы связь «нажал этот цвет — остались такие
   строки» читалась без подписи. Невыбранный чип приглушён, но не
   обесцвечен: цвет и есть его смысл. */
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 0; }
.badge.chip {
  font-family: inherit; font-size: 12.5px; line-height: 1.4; cursor: pointer;
  padding: 5px 13px; border: 1px solid transparent; opacity: .75;
}
.badge.chip:hover { opacity: .92; }
.badge.chip[aria-pressed=true] { opacity: 1; border-color: currentColor; }
.badge.chip:focus-visible { outline: 2px solid var(--accent);
                            outline-offset: 2px; }
.badge.chip .n { opacity: .7; margin-left: 6px;
                 font-variant-numeric: tabular-nums; }
/* Чип, под который при наборе фамилии не осталось ни одной строки.
   Не убираем и не запираем: нажатый чип, ушедший в ноль, иначе было бы
   нечем отжать. Правило стоит последним — оно должно перебивать и
   :hover, и нажатое состояние, а вес у всех трёх одинаковый. */
.badge.chip.zero { opacity: .35; }

/* Подпись для читалки: с глаз уведена, из дерева доступности — нет.
   display: none выкинул бы её и оттуда. */
.sr { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0;
      overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }

/* Узкое окно: подпись уходит вся, остаются цвет и число. Три чипа со
   словами занимают здесь больше строки, а цвет в журнале и так значит
   ровно это — он назван в подписи под полосой лет и красит каждый итог
   в таблице. Чем чип был, говорят title и подпись для читалки. */
@media (max-width: 700px) {
  .badge.chip .lbl { display: none; }
  .badge.chip .n { margin-left: 0; opacity: 1; min-width: 1.6em;
                   display: inline-block; text-align: center; }
}

/* Сколько строк осталось после фильтра. Пока фильтр не тронут, строки
   нет вовсе: без фильтра это число уже стоит в счётчиках наверху. */
.count { display: none; color: var(--dim); font-size: 13px;
         margin: 12px 0 0; }
.count.on { display: block; }
.count b { color: var(--ink); font-weight: 600;
           font-variant-numeric: tabular-nums; }

/* Шапка. Отступ снизу — полем, а не полями детей: иначе нижний margin
   чипов схлопывался бы наружу и не попадал в offsetHeight, по которому
   распорка держит место уехавшей шапки. */
.bar { padding-bottom: 26px; }
.barspace { height: 0; }

/* Прокрученная шапка: садится сверху и сжимается в две строки — знак с
   названием и счётчики, под ними поиск с фильтром. Полоса лет, подзаголовок
   и подпись под полосой уходят: это чтение, а не управление, и на них
   возвращаются наверх. Поле поиска и чипы здесь те же самые, не копия, —
   второе поле пришлось бы синхронизировать с первым, а вместе с ним и
   фокус, и каретку. */
.bar.stuck {
  position: fixed; top: 0; left: 0; right: 0; z-index: 60;
  margin: 0; padding: 9px 24px 10px;
  background: var(--bar); border-bottom: 1px solid var(--line);
  backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
}
/* Шапка растянута по окну, содержимое — по колонке, как всё остальное. */
.bar.stuck > .barmain, .bar.stuck > .bartools {
  max-width: 1000px; margin: 0 auto;
}
.bar.stuck .sub, .bar.stuck .years, .bar.stuck .years-note { display: none; }

.bar.stuck .barmain { display: flex; align-items: center; gap: 14px;
                      min-width: 0; }
.bar.stuck h1 { font-size: 15px; margin: 0; gap: 8px; white-space: nowrap;
                flex: none; }
.bar.stuck h1 .mark { width: 20px; height: 20px; }
/* Шесть счётчиков в строку на телефон не встают. Полоса прокручивается
   вбок — сама, без полосы прокрутки: обрезать нечего, все шесть нужны. */
.bar.stuck .stats {
  display: flex; flex: 1; min-width: 0; margin: 0; gap: 6px; flex-wrap: nowrap;
  overflow-x: auto; scrollbar-width: none;
}
.bar.stuck .stats::-webkit-scrollbar { display: none; }
.bar.stuck .stat {
  display: flex; align-items: baseline; gap: 5px; flex: none;
  min-width: 0; padding: 0; border: none; background: none;
}
.bar.stuck .stat b { display: inline; font-size: 14px; }
.bar.stuck .stat span { font-size: 12px; text-transform: none;
                        letter-spacing: 0; }
.bar.stuck .stat + .stat::before { content: '·'; color: var(--dim);
                                   opacity: .55; margin-right: 6px; }

.bar.stuck .bartools { display: flex; align-items: center; gap: 10px;
                       margin-top: 8px; }
/* Поле не ужимается ниже читаемого: на телефоне чипам есть куда деться —
   они прокручиваются вбок, — а полю ввода некуда. */
.bar.stuck #filter { flex: 1 1 180px; min-width: 150px; max-width: 360px;
                     margin: 0; padding: 7px 12px; font-size: 14px; }
.bar.stuck .chips { flex: 0 1 auto; margin: 0; flex-wrap: nowrap;
                    overflow-x: auto; scrollbar-width: none; padding: 2px 0; }
.bar.stuck .chips::-webkit-scrollbar { display: none; }
.bar.stuck .chip { flex: none; }
/* В севшей шапке строка одна на всё — поле, чипы и счётчик остатка, — и
   хвост про родство в неё не входит ни при какой ширине: набранная
   фамилия добавляет счётчик, а широкое окно тут же отдаёт лишнее место
   полю ввода. Слово «найдена» и число говорят главное, остальное
   досказывает цвет; целиком подпись стоит в развёрнутой шапке. */
.bar.stuck .chip .tail { display: none; }
.bar.stuck .count { margin: 0 0 0 auto; white-space: nowrap; }
/* Узкое окно: сколько осталось — сказано и в самих чипах, а название
   рядом со знаком не нужно, знак и есть название. */
@media (max-width: 760px) { .bar.stuck .count { display: none; } }
@media (max-width: 520px) { .bar.stuck h1 .name { display: none; } }

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
/* Только клетки: у `th` свой кегль, и правило для `.num` без `td`
   перебивало его по специфичности — «Кандидатов» в шапке набирался на
   полтора пункта крупнее остальных заголовков и не капителью. */
td.when, td.num { color: var(--dim); font-size: 13px; white-space: nowrap; }
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

.pages { font-size: 13px; }
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
/* Пузырь дел без года: тот же ряд, но слово вместо числа, поэтому
   без табличных цифр и с полями пошире — иначе буквы жмутся к краю. */
.year.noyear { min-width: auto; padding: 5px 10px;
               font-variant-numeric: normal; }
/* Цифра поднимается своим align-self, а не vertical-align: у флексового
   элемента vertical-align не работает вовсе, а в строке он растил бы
   строчный бокс — с этого и съезжал текст. */
.year .more { font-style: normal; font-size: 10px; opacity: .75;
              margin-left: 3px; align-self: flex-start; line-height: 1; }
/* Пустая метка года перед первым делом этого года: цель ссылки из полосы.
   Отступ сверху — чтобы заголовок дела не прилипал к краю окна, а на
   прокрученной странице ещё и не ушёл под севшую шапку: её высоту JS
   кладёт в --stuck-h. То же и для ссылок между делами, ведущих на секцию. */
.year-mark, .doc { scroll-margin-top: calc(var(--stuck-h, 0px) + 16px); }
.year-mark { height: 0; }

/* Видимая метка года — отдельно от якоря: якорь лежит снаружи блока,
   чтобы ссылка из полосы работала и при включённом фильтре, а надпись
   стоит внутри и вместе с блоком исчезает — иначе год висел бы над
   пустотой.
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
   с десяток пикселей, ниже — метка обрезалась бы.
   В этом поле метка ещё и едет со страницей: год стоит над первым делом,
   но принадлежит всей пачке, а пачка в иной год длиннее экрана — уехавшая
   вверх надпись оставила бы дела без года. Поэтому метка растянута на всю
   высоту блока (сверху донизу, но абсолютом — места в колонке не занимает),
   а надпись внутри липнет к верху окна, пока блок не кончится, и уходит
   вместе с последним делом года. Отступ сверху — на высоту севшей шапки,
   иначе год оказался бы под ней. В узком окне ничего этого нет: там
   надпись — строка в потоке, и липнуть ей некуда. */
@media (min-width: 1180px) {
  .year-block { position: relative; }
  .year-tag {
    display: block; position: absolute; left: -76px; top: 3px; bottom: 0;
    width: 60px; margin: 0; text-align: right; font-size: 13px;
    letter-spacing: .04em;
  }
  .year-tag::after { display: none; }
  .year-tag span { position: sticky; display: block;
                   top: calc(var(--stuck-h, 0px) + 16px); }
}
/* Блок, из которого фильтр выбрал все дела до одного: гасим и метку,
   иначе год висел бы над пустотой. Выше по специфичности правила в
   @media, так что гасит и на широком экране. */
.year-tag.off { display: none; }
.years-note { color: var(--dim); font-size: 12.5px; margin: 0 0 26px;
              max-width: 80ch; }

/* Вырезка из скана: слово, ради которого всё и делалось. Белая подложка —
   сканы серые, на тёмной теме иначе получается дыра. */
/* Пять столбцов на телефон не помещаются: минимальная ширина таблицы
   около 600 px, и вместе с ней вбок уезжает вся страница — вырезка из
   скана оказывается за краем экрана, хотя раскрывают строку именно
   ради неё. Ниже 700 px строка перестаёт быть строкой и раскладывается
   карточкой: фамилия, итог с вердиктом, страницы, а служебные числа —
   подписью внизу. Шапка там не нужна, её работу берут подписи из
   `data-l`: столбец, потерявший заголовок, иначе оставляет голое число. */
@media (max-width: 700px) {
  table, tbody { display: block; }
  thead { display: none; }
  tr { display: flex; flex-wrap: wrap; align-items: baseline;
       column-gap: 12px; row-gap: 7px; padding: 12px 14px;
       border-top: 1px solid var(--line); }
  td { display: block; flex: 1 0 100%; border-top: none; padding: 0; }
  td.surname { order: 1; font-size: 16px; }
  td.result { order: 2; }
  td.pages { order: 3; }
  td.num { order: 4; flex: 0 0 auto; text-align: left; }
  td.when { order: 5; flex: 0 0 auto; }
  td.num::before, td.pages::before { content: attr(data-l) ': ';
                                     color: var(--dim); }
}

.cropgroup { margin-top: 12px; }
.crophead { margin-bottom: 2px; }
.crop { margin-top: 8px; }
.crop img { display: block; max-width: 100%; border: 1px solid var(--line);
            border-radius: 8px; background: #fff; padding: 4px; }
.crop .cap { color: var(--dim); font-size: 12px; margin-top: 4px; }

/* Вырезка в строке — скан в натуральную величину, четыреста пикселей по
   ширине: слово видно, а написание по буквам уже нет. Клик открывает её
   поверх страницы, растянутой под окно. Открывает кнопка, а не картинка
   со слушателем: до вырезки надо доходить и с клавиатуры. */
.crop .shot { display: block; padding: 0; border: 0; background: none;
              font: inherit; cursor: zoom-in; border-radius: 8px; }
.crop .shot:focus-visible { outline: 2px solid var(--accent);
                            outline-offset: 3px; }

.lb { padding: 0; border: 1px solid var(--line); border-radius: 12px;
      background: var(--card); color: var(--ink);
      width: min(1080px, 94vw); max-width: 94vw;
      height: min(720px, 88vh); max-height: 88vh; overflow: hidden;
      box-shadow: 0 20px 60px rgba(0, 0, 0, .35); }
/* Раскладка — только открытому окну: display в правиле без [open] перебил
   бы браузерное display: none, и окно стояло бы в странице всегда. */
.lb[open] { display: flex; flex-direction: column; }
.lb::backdrop { background: rgba(0, 0, 0, .55); }
.lbbar { display: flex; align-items: center; gap: 8px; flex: none;
         padding: 10px 12px; border-bottom: 1px solid var(--line); }
.lbtitle { font-size: 14px; font-weight: 600; margin-right: auto;
           overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lbbar button, .lbbar a { font: inherit; font-size: 13px; line-height: 1.4;
       color: var(--ink); background: var(--bg); border: 1px solid var(--line);
       border-radius: 8px; padding: 4px 10px; cursor: pointer;
       text-decoration: none; }
.lbbar button:hover, .lbbar a:hover { border-color: var(--accent); }
.lbbar button:focus-visible, .lbbar a:focus-visible {
       outline: 2px solid var(--accent); outline-offset: 2px; }
.lbzoom { color: var(--dim); font-size: 12.5px; min-width: 3.6em;
          text-align: center; font-variant-numeric: tabular-nums; }
.lbstage { flex: 1; overflow: auto; background: var(--bg);
           overscroll-behavior: contain; }
.lbstage.grab { cursor: grabbing; }
/* Подложка тянется по картинке и не меньше окна: обычный блок в прокрутке
   шире себя не станет, и увеличенная вырезка обрезалась бы слева — домотать
   до её начала было бы нечем. */
.lbpad { display: inline-flex; align-items: center; justify-content: center;
         min-width: 100%; min-height: 100%; padding: 16px; }
/* Белое поле вокруг скана сделано рамкой, а не отступом, и не просто так:
   рамка вокруг найденного слова ставится в долях от картинки, а проценты
   у absolute-потомка отмеряются от padding-box — то есть изнутри border,
   но снаружи padding. С отступом рамка съезжала бы на его ширину. */
.lbshell { position: relative; display: block; background: #fff;
           border: 6px solid #fff; border-radius: 6px; overflow: hidden; }
.lbshell img { display: block; user-select: none; -webkit-user-drag: none; }

/* Найденное слово на развёрнутой странице. Цвет здесь один на обе темы и
   взят не из палитры: рамка лежит не на фоне журнала, а на скане, а скан
   белый и в тёмной теме тоже. Тень в полэкрана гасит всё, кроме рамки, —
   без неё на листе в две тысячи пикселей, ужатом до восьмисот, слово
   пришлось бы искать заново, ради чего страницу и открывали. Гасит не
   до черноты: соседние строки должны читаться, иначе окружение находки —
   список это или приказ — так и осталось бы неизвестным. */
.lbframe { position: absolute; border: 2px solid #d1452b; border-radius: 3px;
           pointer-events: none; box-shadow: 0 0 0 9999px rgba(0, 0, 0, .38); }

/* Узкое окно: заголовок уезжает под кнопки, чтобы «полный размер» и
   крестик не выдавливались за край. */
@media (max-width: 700px) {
  .lbbar { flex-wrap: wrap; }
  .lbtitle { order: 2; flex: 1 0 100%; margin-right: 0; }
  /* С переключателем страницы кнопок стало на одну больше, и на телефоне
     шапка вставала в три ряда, съедая треть окна. Уходит «полный размер»:
     та же ссылка стоит в строке журнала, под самой вырезкой. */
  #lbfull { display: none; }
}
.empty { color: var(--dim); }
footer { color: var(--dim); font-size: 12.5px; margin-top: 40px;
         border-top: 1px solid var(--line); padding-top: 14px; }
"""

JS = """
const box = document.getElementById('filter');
const chips = Array.from(document.querySelectorAll('.chip'));
const count = document.getElementById('count');
const bar = document.getElementById('bar');
const space = document.getElementById('barspace');

function plural(n, one, few, many) {
  if (n % 100 >= 11 && n % 100 <= 14) return many;
  const d = n % 10;
  return d === 1 ? one : d >= 2 && d <= 4 ? few : many;
}

// Оба фильтра — поле и чипы итога — сходятся здесь. Раздельные
// обработчики второй раз показывали бы строки, спрятанные первым.
// Ни одного нажатого чипа значит «любой итог», а не «никакой»: иначе
// первое же нажатие пустило бы страницу в ноль строк.
function apply() {
  const q = box.value.trim().toLowerCase();
  const on = chips.filter(c => c.getAttribute('aria-pressed') === 'true')
                  .map(c => c.dataset.s);
  const active = q || on.length;
  let shown = 0;
  // Строки считаются и по одному полю тоже: число на чипе должно говорить,
  // сколько строк он оставит из набранных сейчас, а не сколько их в
  // журнале вообще. Поэтому подсчёт идёт до чипов и мимо них — иначе
  // первое же нажатие обнулило бы соседние чипы и отжать их было бы не по
  // чему.
  const tally = {};
  document.querySelectorAll('tbody tr').forEach(tr => {
    const hit = !q || tr.dataset.k.includes(q);
    if (hit) tally[tr.dataset.s] = (tally[tr.dataset.s] || 0) + 1;
    const hide = !hit || (on.length && !on.includes(tr.dataset.s));
    tr.classList.toggle('hidden', hide);
    if (!hide) shown++;
  });
  chips.forEach(c => {
    const n = q ? tally[c.dataset.s] || 0 : +c.dataset.n;
    c.querySelector('.n').textContent = n;
    c.classList.toggle('zero', !n);
  });
  // Метка года — одна на блок и стоит над ним, так что переезжать ей
  // никуда не нужно: фильтр, спрятавший первое дело года, просто
  // укорачивает блок сверху. Гаснет метка только у блока, из которого
  // выбрали все дела до одного. Дело, по которому ещё не искали, строк не
  // имеет вовсе — при снятом фильтре ему прятаться не за что.
  let docs = 0;
  document.querySelectorAll('.year-block').forEach(b => {
    let left = 0;
    b.querySelectorAll('.doc').forEach(d => {
      const any = !active || d.querySelectorAll('tbody tr:not(.hidden)').length;
      d.style.display = any ? '' : 'none';
      if (any) { left++; docs++; }
    });
    const tag = b.querySelector('.year-tag');
    if (tag) tag.classList.toggle('off', !left);
  });
  // Сколько осталось — и строк, и документов сразу: одна фамилия тянется
  // через десятки книг, и «12 поисков» само по себе не говорит, много это
  // документов или один. Пустой ответ называется словами: без надписи
  // фильтр, срезавший всё, выглядел бы поломкой страницы.
  count.classList.toggle('on', !!active);
  if (active) {
    count.innerHTML = shown
      ? `Показано <b>${shown}</b> ${plural(shown, 'поиск', 'поиска', 'поисков')}`
        + ` в <b>${docs}</b> ${plural(docs, 'документе', 'документах', 'документах')}`
      : 'Ничего не нашлось';
  }
  // Свёрнутая таблица прячет как раз то, что искали, поэтому на время
  // фильтра совпадения раскрываются сами. Что человек открыл руками до
  // фильтра, запоминается и возвращается, когда фильтр снимут.
  document.querySelectorAll('details.searches').forEach(det => {
    if (active) {
      if (det.dataset.was === undefined) det.dataset.was = det.open ? '1' : '';
      det.open = true;
    } else if (det.dataset.was !== undefined) {
      det.open = det.dataset.was === '1';
      delete det.dataset.was;
    }
  });
}
box.addEventListener('input', apply);
chips.forEach(c => c.addEventListener('click', () => {
  // Чипы независимы: можно оставить и находки с родством, и находки без
  // него. Повторное нажатие снимает — иначе выбранный по ошибке итог
  // снимался бы только перезагрузкой страницы.
  c.setAttribute('aria-pressed',
                 c.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
  apply();
}));

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

// Шапка садится сверху ровно в тот миг, когда её развёрнутый низ доходит
// до края окна: сжатая шапка встаёт на то же место, которое занимал этот
// низ, и подмена не видна — ни скачка, ни всплытия пустой полосы. Место
// уехавшей шапки держит распорка: без неё страница подпрыгнула бы на всю
// её высоту, потому что севшая шапка выпадает из потока.
let top0 = 0, full = 0, compact = 0;

function measure() {
  bar.classList.remove('stuck');
  space.style.height = '0px';
  full = bar.offsetHeight;
  top0 = bar.getBoundingClientRect().top + scrollY;
  // Сжатую высоту меряем ею же самой: она зависит от ширины окна — на
  // телефоне чипы переносятся, счётчики прокручиваются, — и посчитать её
  // заранее нельзя. Между двумя замерами браузер не рисует, так что
  // мигания нет.
  bar.classList.add('stuck');
  compact = bar.offsetHeight;
  bar.classList.remove('stuck');
  document.documentElement.style.setProperty('--stuck-h', compact + 'px');
  sync(true);
}

function sync(force) {
  const on = scrollY > top0 + full - compact;
  if (!force && on === bar.classList.contains('stuck')) return;
  bar.classList.toggle('stuck', on);
  space.style.height = on ? full + 'px' : '0px';
}

// Окно с вырезкой. Вырезка мелкая — сотня пикселей в высоту, — и в строке
// журнала она годится, чтобы узнать слово, но не чтобы читать буквы. Здесь
// она открывается во весь экран и тянется дальше кнопками: спор о том, «ъ»
// там или «ь», решается только увеличением.
//
// Открывается окно, впрочем, страницей, а не вырезкой: вырезка отвечает,
// нашлась ли фамилия, а страница — где она стоит, в алфавитном списке или
// в объявлении о торгах. Найденное слово обведено рамкой по координатам из
// boxes.json, остальное притушено. Страница — файл рядом с журналом, и
// пока она грузится, в окне стоит вырезка, вшитая в страницу; она же
// остаётся насовсем, если файла рядом не оказалось.
const lb = document.getElementById('lb');
const lbimg = document.getElementById('lbimg');
const stage = document.getElementById('lbstage');
const zlabel = document.getElementById('lbzoom');
const frame = document.getElementById('lbframe');
const pagebtn = document.getElementById('lbpage');
const fulllink = document.getElementById('lbfull');
const caption = document.getElementById('lbtitle');
// Свои размеры картинки берём у превью: оно уже нарисовано, а у картинки в
// окне naturalWidth появляется только после загрузки — считать по нему
// значило бы мерить нули. Они же и мерка увеличения: 100% — это вырезка
// такой, какой она стоит в строке, независимо от того, сколько пикселей
// в самом скане.
let nw = 1, nh = 1, zoom = 1, want = 0;
// С чего окно открыли и что в нём сейчас: вырезка или целая страница.
let shot = null, whole = false;

function setZoom(z) {
  const pw = lbimg.offsetWidth || 1, ph = lbimg.offsetHeight || 1;
  // Что было в середине окна, там и остаётся: без пересчёта прокрутки
  // увеличение уводило бы вырезку в левый верхний угол.
  const cx = (stage.scrollLeft + stage.clientWidth / 2) / pw;
  const cy = (stage.scrollTop + stage.clientHeight / 2) / ph;
  // Нижний порог у вырезки и у страницы разный. Вырезке меньше половины
  // натуральной величины незачем — она и так мельче окна; страницу же
  // «по окну» ужимает впятеро, и порог в половину не давал вписать её
  // целиком, останавливая ровно на 50% и обрезая лист по краям.
  zoom = Math.min(16, Math.max(whole ? .05 : .5, z));
  lbimg.style.width = Math.round(nw * zoom) + 'px';
  zlabel.textContent = Math.round(zoom * 100) + '%';
  stage.scrollLeft = cx * lbimg.offsetWidth - stage.clientWidth / 2;
  stage.scrollTop = cy * lbimg.offsetHeight - stage.clientHeight / 2;
}

// «По окну» значит разное для вырезки и для страницы. Вырезка меньше окна
// во все стороны, и показать её один к одному значило бы открыть окно ради
// той же марки, что стояла в строке: её тянем вверх, но не выше
// восьмикратной — дальше растёт не буква, а зерно скана. Страницу, наоборот,
// вписываем целиком: за этим её и просят.
function fit() {
  const w = (stage.clientWidth - 44) / nw, h = (stage.clientHeight - 44) / nh;
  setZoom(whole ? Math.min(w, h) : Math.max(1, Math.min(w, h, 8)));
  stage.scrollLeft = (stage.scrollWidth - stage.clientWidth) / 2;
  stage.scrollTop = (stage.scrollHeight - stage.clientHeight) / 2;
}


function showCrop() {
  const img = shot.querySelector('img');
  whole = false;
  frame.hidden = true;
  nw = img.naturalWidth || img.offsetWidth || 1;
  nh = img.naturalHeight || img.offsetHeight || 1;
  lbimg.src = img.src;
  lbimg.alt = img.alt;
  caption.textContent = img.alt;
  fulllink.href = shot.dataset.full;
  fulllink.title = 'Открыть файл вырезки';
  pagebtn.textContent = 'страница целиком';
  fit();
  // В страницу вырезка вшита ужатой до 620 px — в скане их две тысячи, и
  // вшивать столько восемьдесят раз значило бы вчетверо утяжелить журнал
  // ради картинок, которые почти всегда просто пролистывают. Поэтому окно,
  // открывшись, подменяет превью файлом из crops/: место и размер те же,
  // резкость — скана. Журнал, унесённый от своих папок, файла не найдёт и
  // останется с превью — так же, как ссылка «полный размер».
  const mine = ++want;
  const hi = new Image();
  hi.onload = () => { if (mine === want) lbimg.src = hi.src; };
  hi.src = shot.dataset.full;
}

function showWhole() {
  const b = shot.dataset.box.split(',').map(Number);
  const sz = shot.dataset.size.split(',').map(Number);
  const mine = ++want;
  const hi = new Image();
  // Страница лежит файлом рядом с журналом, а не внутри него. Не нашлась —
  // значит журнал унесли от папок или страницу вычистил prune.py. Тогда
  // остаёмся с вырезкой и убираем переключатель: кнопка, которая ничего не
  // открывает, хуже, чем её отсутствие.
  hi.onerror = () => {
    if (mine !== want) return;
    shot.removeAttribute('data-page');
    pagebtn.hidden = true;
    showCrop();
  };
  hi.onload = () => {
    if (mine !== want) return;
    whole = true;
    nw = sz[0];
    nh = sz[1];
    lbimg.src = hi.src;
    // Рамка ставится в долях от картинки, поэтому увеличение её не
    // касается: доли те же и при 50%, и при 600%.
    frame.style.left = 100 * b[0] / nw + '%';
    frame.style.top = 100 * b[1] / nh + '%';
    frame.style.width = 100 * (b[2] - b[0]) / nw + '%';
    frame.style.height = 100 * (b[3] - b[1]) / nh + '%';
    frame.hidden = false;
    caption.textContent = lbimg.alt + ' · страница целиком';
    fulllink.href = shot.dataset.page;
    fulllink.title = 'Открыть файл страницы';
    pagebtn.textContent = 'одна вырезка';
    // Страница открывается вписанной в окно, а не наведённой на слово:
    // за ней идут ради того, что находку окружает, и первым делом
    // смотрят, что это за лист вообще — список, приказ или объявление.
    // Искать на нём слово глазами не приходится и при таком уменьшении:
    // рамка — единственное непритушенное место на листе.
    fit();
  };
  hi.src = shot.dataset.page;
}

document.querySelectorAll('.crop .shot').forEach(btn => {
  btn.addEventListener('click', () => {
    shot = btn;
    pagebtn.hidden = !btn.dataset.page;
    lb.showModal();
    showCrop();   // размеры окна известны только после showModal
    if (btn.dataset.page) showWhole();
  });
});

pagebtn.addEventListener('click', () => whole ? showCrop() : showWhole());

document.getElementById('lbin').addEventListener('click', () => setZoom(zoom * 1.5));
document.getElementById('lbout').addEventListener('click', () => setZoom(zoom / 1.5));
document.getElementById('lbfit').addEventListener('click', fit);
document.getElementById('lbclose').addEventListener('click', () => lb.close());
// Щелчок мимо окна закрывает: цель события — сам <dialog> только тогда,
// когда попали в поле вокруг него.
lb.addEventListener('click', e => { if (e.target === lb) lb.close(); });
lb.addEventListener('keydown', e => {
  const k = e.key;
  if (k === '+' || k === '=') setZoom(zoom * 1.5);
  else if (k === '-' || k === '_') setZoom(zoom / 1.5);
  else if (k === '0') fit();
  // Переключатель есть и на клавише: страницу с вырезкой сличают туда-сюда,
  // и каждый раз целиться в кнопку — лишнее движение.
  else if ((k === 'п' || k === 'p') && !pagebtn.hidden)
    whole ? showCrop() : showWhole();
  else return;
  e.preventDefault();
});
// Колесо само по себе мотает увеличенную вырезку — это его обычная работа.
// Увеличивает щипок трекпада, который браузер шлёт как колесо с ctrl.
stage.addEventListener('wheel', e => {
  if (!e.ctrlKey && !e.metaKey) return;
  e.preventDefault();
  setZoom(zoom * (e.deltaY < 0 ? 1.12 : 1 / 1.12));
}, {passive: false});
// Тянуть мышью: полосы прокрутки для картинки — неудобная мелочь. Палец не
// трогаем: сенсорный экран мотает содержимое сам, и вторая, своя прокрутка
// поверх его собственной уводила бы вырезку вдвое быстрее пальца.
let drag = null;
stage.addEventListener('pointerdown', e => {
  if (e.button || e.pointerType !== 'mouse') return;
  drag = {x: e.clientX, y: e.clientY, l: stage.scrollLeft, t: stage.scrollTop};
  stage.setPointerCapture(e.pointerId);
  stage.classList.add('grab');
});
stage.addEventListener('pointermove', e => {
  if (!drag) return;
  stage.scrollLeft = drag.l - (e.clientX - drag.x);
  stage.scrollTop = drag.t - (e.clientY - drag.y);
});
['pointerup', 'pointercancel'].forEach(t => stage.addEventListener(t, () => {
  drag = null;
  stage.classList.remove('grab');
}));

addEventListener('scroll', () => sync(), {passive: true});
addEventListener('resize', () => { measure(); if (lb.open) fit(); });
// Знак нарисован в самой странице, но кегли считает шрифт: до его загрузки
// высота шапки не окончательная, и порог оказался бы на десяток пикселей
// не там.
addEventListener('load', measure);
measure();
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
# Газетный `pn` сюда попал не сразу, и полгода ссылки между выпусками
# оставались простым текстом: подшивка ссылается сама на себя чаще любых
# книг — объявление печатается трижды, список присяжных продолжается из
# номера в номер, — и как раз эти цепочки читателю и нужны.
DOC_ID = re.compile(r"\b(?:bv|ot|pn)\d{7}\b")

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


def group_by_page(crops):
    """Вырезки одной страницы — вместе, в порядке, который дал crops_for.

    Подпись про родство и имя человека журнал знает по **странице**:
    `--kin` и `--person` называют страницу, а не место на ней. Пока
    вырезка одна, это одно и то же, и подпись можно ставить под
    картинкой. Когда их несколько, повторённая под каждой подпись
    объявляет всех однофамильцев одним человеком — а именно так и
    вышло на стр. 35 выпуска pn0024233, где рядом стоят Филипп
    Петрович Кармазин, его сын Анисим и посторонний Василий Демьянов:
    все три вырезки были подписаны именем Филиппа Петровича.

    Поэтому подпись поднимается над группой и произносится один раз,
    а сами вырезки идут без имён. Разбор, кто из них кто, остаётся
    в вердикте — там он и был всё это время.
    """
    out = OrderedDict()
    for page, f in crops:
        out.setdefault(page, []).append(f)
    return list(out.items())


def shot_page(ident: str, f: pathlib.Path):
    """Страница, с которой снята вырезка, и место вырезки на ней.

    Вырезка отвечает на вопрос «нашлась ли фамилия», страница — на
    вопрос «где она стоит»: в алфавитном списке, в приказе или в
    объявлении о торгах. Второй вопрос задают сразу после первого, и до
    сих пор на него отвечали, открывая скан руками.

    Координаты лежат в `crops/boxes.json` (см. boxes.py), но одних их
    мало: скан — это мегабайт рядом с журналом, а не внутри него, и
    prune.py оставляет на диске только страницы подтверждённых находок.
    Нет файла — нет и предложения его открыть.
    """
    info = boxes.load(ident).get(f.name)
    if not info:
        return None
    page = ROOT / ident / "scans" / f"p{int(info['page']):04d}.jpg"
    if not page.exists():
        return None
    return page.relative_to(ROOT), info["box"], info["size"]


def shot_html(ident: str, surname: str, page: int, f: pathlib.Path) -> str:
    """Вырезка в строке журнала: картинка-кнопка и подпись под ней."""
    place = shot_page(ident, f)
    crop = e(str(f.relative_to(ROOT)))
    if place:
        src, box, size = place
        where = (f" data-page='{e(str(src))}'"
                 f" data-box='{','.join(str(int(v)) for v in box)}'"
                 f" data-size='{','.join(str(int(v)) for v in size)}'")
        title = "Открыть страницу целиком, с выделенным словом"
        cap = (f"вырезка из скана, <a href='{e(str(src))}'>страница целиком</a>"
               f", <a href='{crop}'>полный размер</a>")
    else:
        where, title = "", "Открыть вырезку крупно"
        cap = f"вырезка из скана, <a href='{crop}'>полный размер</a>"
    return (f"<div class=crop>"
            f"<button class=shot type=button title='{title}' "
            f"data-full='{crop}'{where}>"
            f"<img alt='{e(surname)}, стр. {page}' src='{thumb_uri(f)}'>"
            f"</button>"
            f"<div class=cap>{cap}</div></div>")


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


# Дела без года стоят в конце списка, и до них полоса не доводит: ряд
# кончается последним известным годом. Пузырь в хвосте полосы — их
# единственная ссылка, а якорь у него отдельный: года у такого дела нет,
# и `g...` для него не построить.
NOYEAR_ANCHOR = "no-year"


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
    noyear, noyear_cls = 0, None
    for ident, d in docs.items():
        st = {r["status"] for r in d["rows"]}
        kin = any(confirmed_pages(r)[1] for r in d["rows"])
        cls = ("ok" if kin else
               "maybe" if "found" in st else
               "wait" if (not st or "unclear" in st) else "no")
        y = d.get("year")
        if y is None:
            # Год не проставлен: либо книга о нём молчит (альманах,
            # справочник), либо он есть, но полосу рвёт — «Донские дела»
            # издают документы 1648-1654 годов, и один такой том растянул
            # бы ряд на два с половиной века пустых клеток. Цвет считается
            # так же, как у года с двумя делами: по лучшему исходу.
            noyear += 1
            if RANK.get(cls, 9) < RANK.get(noyear_cls, 9):
                noyear_cls = cls
            continue
        counts[y] = counts.get(y, 0) + 1
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
    if noyear:
        cells.append(
            f"<a class='year noyear {noyear_cls}' href='#{NOYEAR_ANCHOR}' "
            f"title='{noyear} {plural(noyear, 'дело', 'дела', 'дел')} "
            "без года: том, который в полосу лет не встаёт'>без года"
            + (f"<i class=more>{noyear}</i>" if noyear > 1 else "") + "</a>")
    seen, gaps = sum(counts.values()), (hi - lo + 1) - len(years)
    # Счёт полосы меньше общего числа дел ровно на дела без года, и глаз
    # об это спотыкается: вверху 550, здесь 542. Называем оба числа сразу,
    # чтобы разницу не пришлось выводить самому. Склоняется при этом
    # последнее числительное — «542 из 550 дел», но «542 дела», когда дел
    # без года нет и второго числа не появляется.
    total = seen + noyear
    of_all = f" из {total}" if noyear else ""
    cases = plural(total, 'дело', 'дела', 'дел')
    note = (f"{lo}—{hi}: просмотрено {seen}{of_all} {cases} за {len(years)} "
            f"{plural(len(years), 'год', 'года', 'лет')}, "
            f"{gaps} {plural(gaps, 'год', 'года', 'лет')} в промежутке "
            "не открывали." if gaps else
            f"{lo}—{hi}: сплошь, без пробелов, {seen}{of_all} {cases}.")
    # Дела без года в счёт полосы не входят: она про годы, а у них его нет.
    # Но и потеряться они не должны, поэтому названы отдельно — сразу за
    # числом «542 из 550», разницу в котором эта строка и объясняет.
    multi = (f" Ещё {noyear} {plural(noyear, 'дело', 'дела', 'дел')} "
             "без года — они в конце списка." if noyear else "")
    if any(n > 1 for n in counts.values()):
        multi += (" Цифра в клетке — сколько дел за этот год; цвет по "
                  "лучшему из них.")
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
                           "year": meta_year(meta)}
    # По году, а не по номеру дела: 407 это 1897-й, а 392 — 1888-й.
    # Дела без года в заголовке уходят в конец, порядок между ними прежний.
    return OrderedDict(sorted(docs.items(),
                              key=lambda kv: (kv[1]["year"] is None,
                                              kv[1]["year"] or 0, kv[0])))


# Порядок чипов — от «ничего нет» к «нашли и знаем кого»: так же читается
# и сам поиск. Пузырь «не проверена» появляется, только если такие строки
# есть: сейчас их нет ни одной, и пустой чип обещал бы несуществующий срез.
CHIP_ORDER = ("no", "maybe", "ok", "wait")


def status_chips(searches) -> str:
    """Фильтр по итогу: три пузыря теми же цветами, что и сами итоги.

    Считается по строкам поисков, а не по документам: у одного документа
    итоги по двум фамилиям бывают разными, и «дел с находкой» и «находок»
    — разные числа. Число рядом с чипом — сколько строк он оставит.
    """
    seen = {}
    for r in searches:
        label, cls = row_badge(r)
        seen.setdefault(cls, [label, 0])
        seen[cls][1] += 1
    if len(seen) < 2:
        return ""            # выбирать не из чего — фильтр только мешал бы
    out = ["<div class=chips role=group aria-label='Фильтр по итогу'>"]
    for cls in CHIP_ORDER:
        if cls not in seen:
            continue
        label, n = seen[cls]
        # Подпись чипа набрана трижды, и это не описка. Видимая часть
        # разобрана на слово и хвост про родство, потому что гаснут они
        # порознь: в севшей шапке с набранной фамилией уходит хвост, на
        # узком экране — вся подпись, и остаются цвет с числом. Читалке
        # же нужна подпись целиком и всегда, поэтому рядом лежит третья,
        # уведённая с глаз: aria-label тут не годится — он перебивает
        # содержимое кнопки вместе с числом, а число меняется на ходу и
        # в атрибуте протухло бы.
        head, _, tail = label.partition(", ")
        out.append(f"<button type=button class='badge {cls} chip' "
                   f"data-s='{cls}' data-n='{n}' aria-pressed=false "
                   f"title='{e(label)}'>"
                   f"<span class=sr>{e(label)}</span>"
                   f"<span class=lbl aria-hidden=true>{e(head)}"
                   + (f"<span class=tail>, {e(tail)}</span>" if tail else "")
                   + f"</span><span class=n>{n}</span></button>")
    out.append("</div>")
    return "".join(out)


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
           # Шапка едет со страницей, а прокрученная садится сверху,
           # сжавшись в две строки: знак с названием и счётчики, под ними
           # поиск с фильтром. Поле поиска при этом одно на всю страницу —
           # отдельная строка-двойник требовала бы держать в согласии и
           # текст, и фокус, и каретку.
           "<header class=bar id=bar>",
           "<div class=barmain>",
           "<h1>" + (f"<img class=mark alt='' src='{mark}'>" if mark else "")
           + "<span class=name>Журнал поисков</span></h1>",
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
           "</div>",
           year_strip(docs),
           "<div class=bartools>",
           "<input id=filter type=search placeholder='Фильтр по фамилии, "
           "документу или странице…' autocomplete=off>",
           status_chips(searches),
           "<p id=count class=count role=status aria-live=polite></p>",
           "</div>",
           "</header>",
           # Место, которое шапка занимала в потоке: пока она сидит сверху,
           # распорка держит её прежнюю высоту, и страница не дёргается.
           "<div class=barspace id=barspace></div>"]

    # Дела одного года собраны в блок, и метка года стоит над блоком, а не
    # у первого дела: год — свойство всей пачки. На широком экране метка
    # едет по левому полю до последнего дела своего года, и блок задаёт ей
    # границы. Дела уже отсортированы по годам, так что новая пачка — это
    # просто смена года; дела без года идут последними и все подряд, так
    # что пачка у них одна, к ней и ведёт пузырь «без года».
    # Якорь стоит снаружи блока нарочно: фильтр по фамилии прячет и дела, и
    # метку, а ссылка из полосы лет должна вести куда-то и тогда.
    seen_year, open_block = object(), False
    for ident, d in docs.items():
        meta, rows = d["meta"], d["rows"]
        title = meta.get("title") or ident
        url = meta.get("url", "")
        if d.get("year") != seen_year:
            if open_block:
                out.append("</div>")
            seen_year, open_block = d.get("year"), True
            label = str(seen_year) if seen_year else "без года"
            anchor = year_anchor(seen_year) if seen_year else NOYEAR_ANCHOR
            out.append(f"<div class=year-mark id='{anchor}'></div>")
            out.append("<div class=year-block>")
            # Год уже назван в строке под заголовком каждого дела, так что
            # метка — чистая навигация глазом, и читалке её повторять
            # незачем. Надпись обёрнута в span: на широком экране липнет к
            # верху окна именно он, а сама метка растянута на блок.
            out.append("<div class=year-tag aria-hidden=true>"
                       f"<span>{label}</span></div>")
        out.append(f"<section class=doc id='{e(ident)}'>")
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
            out.append(f"<tr data-k='{e(key)}' data-s='{cls}'>")
            out.append(f"<td class=when>{e(r['date'][:16].replace('T', ' '))}</td>")
            out.append(f"<td class=surname>{e(r['surname'])}</td>")
            out.append(f"<td class=num data-l='Кандидатов'>{r['hits']}</td>")
            out.append(f"<td class=pages data-l='Страницы'>"
                       f"{', '.join(links) or '—'}</td>")
            note = (f"<div class=note>"
                    f"{markup(r['verdict'], docs.keys(), ident)}</div>"
                    if r["verdict"] else "")
            shots = ""
            people = roster()
            for page, files in group_by_page(
                    crops_for(ident, r["surname"], confirmed)):
                who = ("родство подтверждено" if str(page) in kin
                       else "родство не установлено")
                mark_cls = "hit" if str(page) in kin else "maybe"
                pid = (r.get("persons") or {}).get(str(page))
                named = (person_link(pid, people[pid]) if pid in people else "")
                many = (" · вырезок несколько: на странице стоят однофамильцы, "
                        "кто из них кто — сказано в вердикте"
                        if len(files) > 1 else "")
                shots += (f"<div class=cropgroup>"
                          f"<div class='cap crophead'>стр. {page} · "
                          f"<span class='badge {mark_cls}'>{who}</span>"
                          f"{named}{many}</div>")
                shots += "".join(
                    shot_html(ident, r["surname"], page, f) for f in files)
                shots += "</div>"
            out.append(f"<td class=result><span class='badge {cls}'>{label}"
                       f"</span>{note}{shots}</td>")
            out.append("</tr>")
        out.append("</tbody></table></details></section>")

    if open_block:
        out.append("</div>")
    out.append("<footer>Пересобирается автоматически при каждом поиске. "
               "Источник — <code>&lt;документ&gt;/searches.jsonl</code>.</footer>")
    out.append("</div>")   # .wrap
    # Окно для вырезки — одно на всю страницу: пятьдесят копий одной и той
    # же разметки, по одной на картинку, весили бы столько же, сколько сами
    # вырезки. Стоит вне .wrap: модальное окно всё равно рисуется поверх
    # страницы, а внутри колонки его легко принять за часть текста.
    out.append(
        "<dialog id=lb class=lb aria-label='Вырезка из скана'>"
        "<div class=lbbar>"
        "<span class=lbtitle id=lbtitle></span>"
        "<button id=lbout type=button aria-label='Уменьшить' "
        "title='Уменьшить (−)'>−</button>"
        "<span class=lbzoom id=lbzoom role=status></span>"
        "<button id=lbin type=button aria-label='Увеличить' "
        "title='Увеличить (+)'>+</button>"
        "<button id=lbfit type=button title='Вписать в окно (0)'>по окну</button>"
        "<button id=lbpage type=button title='Страница целиком или одна "
        "вырезка (п)'>страница целиком</button>"
        "<a id=lbfull href='#' title='Открыть файл вырезки'>полный размер</a>"
        # Фокус при открытии — на крестике: окно всё равно закрывают чаще,
        # чем крутят, а без autofocus браузер ставит его на первую кнопку —
        # «уменьшить», у которой в этот миг обычно нечего уменьшать.
        "<button id=lbclose type=button autofocus aria-label='Закрыть' "
        "title='Закрыть (Esc)'>×</button>"
        "</div>"
        "<div class=lbstage id=lbstage><div class=lbpad>"
        "<div class=lbshell><img id=lbimg alt=''>"
        "<div class=lbframe id=lbframe hidden></div>"
        "</div></div></div>"
        "</dialog>")
    out.append(f"<script>{JS}</script></body></html>")
    return "\n".join(out)


def rebuild() -> pathlib.Path:
    dst = ROOT / "journal.html"
    dst.write_text(render(collect()), encoding="utf-8")
    return dst


if __name__ == "__main__":
    print(rebuild())
