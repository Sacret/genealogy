"""Поиск фамилии в OCR-тексте."""

import re
from dataclasses import dataclass

from .normalize import normalize, normalize_marked, dehyphenate
from .match import prefix_distance, default_threshold, score

_TOKEN_RE = re.compile(r"[А-Яа-яЁёѢѣІіЇїѲѳѴѵЅѕѦѧѪѫA-Za-z]+")

# Падежные окончания, которые срезаем с *запроса*, чтобы получить
# инвариантную основу. Порядок важен — длинные первыми.
_ADJ_ENDINGS = re.compile(r"(ск|цк)(ии|ого|аго|ому|им|ом|ая|ои|ую|ие|их|ими)$")
_NOUN_ENDINGS = re.compile(r"(ов|ев|ин|ын)(а|у|ым|ом|е|ои|ы|ых|ыми)$")


def stem_query(surname: str):
    """Основа фамилии и позиции ненадёжных (дореформенных) букв в ней.

    Возвращает (основа, [индексы]). Отсечение окончания идёт с конца,
    поэтому индексы внутри оставшейся части не съезжают.
    """
    w, fragile = normalize_marked(surname)
    w = _ADJ_ENDINGS.sub(r"\1", w)
    w = _NOUN_ENDINGS.sub(r"\1", w)
    return w, [i for i in fragile if i < len(w)]


# Слово, разорванное переносом, даёт две половины. Если вторая испорчена
# (стёртая печать, курсив, соседняя числовая колонка), склейка бесполезна:
# токена с фамилией в тексте не возникнет. Но первая половина обычно цела,
# и она — начало фамилии. Так был пропущен 'Могу-' / 'чевъ' на стр. 208
# документа bv0000386.
# Дефис + пробел, а не только конец строки: в таблицах в ту же строку
# затекает соседняя колонка, и перенос перестаёт быть последним символом
# ("Могу- к |"). Внутрисловный дефис без пробела ("Штабсъ-Капитаны")
# под правило не подпадает.
_HYPHEN_END = re.compile(r"([А-Яа-яЁёѢѣІіЇїѲѳѴѵA-Za-z]{2,})[-‐‑–](?=[ \t]*\n|[ \t]+)")
# Слово, начинающее строку сразу после переноса: то самое, которое
# склейка приклеивает к хвосту предыдущей строки.
_AFTER_BREAK_RE = re.compile(
    r"[-‐‑–\u00ad]\s*\n\s*([А-Яа-яЁёѢѣІіЇїѲѳѴѵЅѕѦѧѪѫA-Za-z]+)")

PARTIAL_MAX_COST = 0.8
MIN_FRAGMENT = 4        # короче — слишком слабое свидетельство для обычного поиска


@dataclass
class Match:
    page: str          # идентификатор страницы/скана
    start: int         # смещение в исходном тексте страницы
    end: int
    raw: str           # как написано в документе
    cost: float        # накопленный штраф
    score: float       # 0..1, для сортировки
    context: str       # окружающий текст для глазной проверки
    partial: bool = False   # совпало только начало (слово разорвано переносом)


def find_in_text(text: str, surname: str, page: str = "-",
                 threshold: float = None, context_chars: int = 60,
                 min_fragment: int = MIN_FRAGMENT):
    """Ищет фамилию в одной странице текста.

    Токенизируем ИСХОДНЫЙ текст (чтобы сохранить смещения), нормализуем
    каждый токен по отдельности и сравниваем с основой запроса.
    """
    stem, fragile = stem_query(surname)
    if threshold is None:
        threshold = default_threshold(stem)

    original = text          # до склейки переносов
    text = dehyphenate(text)
    results = []
    for m in _TOKEN_RE.finditer(text):
        hit = _test_token(m.group(), stem, fragile, threshold)
        if hit is None:
            continue
        cost, sc = hit
        lo = max(0, m.start() - context_chars)
        hi = min(len(text), m.end() + context_chars)
        results.append(Match(
            page=page,
            start=m.start(),
            end=m.end(),
            raw=m.group(),
            cost=cost,
            score=sc,
            context=" ".join(text[lo:hi].split()),
        ))
    results.extend(_find_after_break(original, stem, fragile, page,
                                     threshold, context_chars))
    results.extend(_find_line_joins(original, stem, fragile, page,
                                    threshold, context_chars))
    results.extend(_find_hyphen_starts(original, stem, fragile, page,
                                       context_chars, min_fragment))
    results.sort(key=lambda r: (-r.score, r.start))
    return results


def _test_token(raw: str, stem, fragile, threshold):
    """(cost, score) для токена, если он проходит порог, иначе None."""
    norm = normalize(raw)
    if not norm:
        return None
    # быстрый отсев: длина не может отличаться сильнее, чем порог
    if len(norm) < len(stem) - threshold - 1:
        return None
    cost, _ = prefix_distance(stem, norm, fragile=fragile)
    if cost > threshold:
        return None
    return round(cost, 2), round(score(stem, norm, fragile=fragile), 3)


def _find_after_break(text, stem, fragile, page, threshold, context_chars):
    """Слово, стоящее сразу после переноса, — в том виде, что до склейки.

    Склейка переносов права для одной колонки и вредна для двух: в
    справочнике строка кончается переносом ПРАВОЙ колонки, а следующая
    начинается с ЛЕВОЙ, и dehyphenate сращивает чужие друг другу слова.
    Так пропал фельдшер Могучев на стр. 96 тома bv0000040: печать чистая,
    распознано верно — «Добры-\nМогучевъ А. І.» превратилось в токен
    'ДобрыМогучевъ', и фамилии в тексте не стало.

    Поэтому первое слово после каждого переноса проверяется ещё раз,
    целым. Проход узкий — только эти слова, — так что счёт кандидатов
    от него почти не растёт.
    """
    out = []
    for m in _AFTER_BREAK_RE.finditer(text):
        hit = _test_token(m.group(1), stem, fragile, threshold)
        if hit is None:
            continue
        cost, sc = hit
        lo = max(0, m.start(1) - context_chars)
        hi = min(len(text), m.end(1) + context_chars)
        out.append(Match(
            page=page, start=m.start(1), end=m.end(1), raw=m.group(1),
            cost=cost, score=sc,
            context=" ".join(text[lo:hi].split())))
    return out


def _find_line_joins(text, stem, fragile, page, threshold, context_chars):
    """Слово, разорванное концом строки БЕЗ дефиса.

    Перенос обычно помечен дефисом, и его склеивает dehyphenate. Но
    дефис бывает не пропечатан или срезан сканированием, и тогда фамилия
    лежит двумя кусками, между которыми только перевод строки. В
    двухколоночном справочнике вдобавок мешает соседняя колонка: на
    стр. 129 тома bv0000042 фельдшер Могучев набран как

        ... Алекс. Іос. Мо
        черкасской общины сестеръ ми- | гучевъ.

    — правая колонка разорвана, а между её половинами затекла левая.
    Перечитывание полосами тут не помогает: полоса идёт во всю ширину
    страницы и склеивает колонки так же.

    Поэтому обломок в конце строки пробуется склеить не только со
    следующим словом, но с любым словом следующей строки. Сеть широкая,
    но улов узкий: сравнение идёт с НАЧАЛА основы, так что склейка
    проходит порог, только если обломок сам по себе — начало фамилии,
    а хвост её дописывает. Пары с дефисом сюда не попадают: их уже
    склеил dehyphenate.
    """
    out = []
    lines = text.split("\n")
    offset, starts = 0, []
    for ln in lines:
        starts.append(offset)
        offset += len(ln) + 1
    for i, line in enumerate(lines[:-1]):
        tail_words = list(_TOKEN_RE.finditer(line))
        if not tail_words:
            continue
        head_m = tail_words[-1]
        if head_m.end() != len(line.rstrip()):
            continue                      # обломок должен кончать строку
        head = head_m.group()
        # Фамилия в печати с прописной; без этого правило тонет в
        # обычных словах, разорванных вёрсткой.
        if not head[:1].isupper():
            continue
        nhead = normalize(head)
        if not nhead or len(nhead) >= len(stem):
            continue                      # целое слово ловится обычным путём
        if prefix_distance(nhead, stem, fragile=fragile)[0] > PARTIAL_MAX_COST:
            continue                      # обломок не похож на начало фамилии
        for m in _TOKEN_RE.finditer(lines[i + 1]):
            hit = _test_token(head + m.group(), stem, fragile, threshold)
            if hit is None:
                continue
            cost, sc = hit
            a = starts[i] + head_m.start()
            b = starts[i + 1] + m.end()
            lo, hi = max(0, a - context_chars), min(len(text), b + context_chars)
            out.append(Match(
                page=page, start=a, end=b, raw=head + m.group(),
                cost=cost, score=sc,
                context=" ".join(text[lo:hi].split())))
            break                         # одного продолжения довольно
    return out


def _find_hyphen_starts(text, stem, fragile, page, context_chars, min_fragment):
    """Половинки фамилии, оставшиеся перед переносом строки.

    Обломок сравнивается как *начало* основы: 'Могу' против 'могучев'
    совпадает без штрафа, 'полу' или 'мага' — нет. Порог жёсткий: иначе
    каждое второе перенесённое слово попадёт в выдачу.
    """
    out = []
    for m in _HYPHEN_END.finditer(text):
        word = m.group(1)
        # Фамилия в печати всегда с прописной. Без этого условия правило
        # тонет в обычных словах: 'могу-щихъ', 'получе-нія', 'магази-на'
        # дают обломок, неотличимый от начала фамилии.
        if not word[:1].isupper():
            continue
        frag = normalize(word)
        if len(frag) < min_fragment or len(frag) >= len(stem):
            continue                      # целое слово ловится обычным путём
        cost, _ = prefix_distance(frag, stem, fragile=fragile)
        if cost > PARTIAL_MAX_COST:
            continue
        lo = max(0, m.start(1) - context_chars)
        hi = min(len(text), m.end(1) + context_chars)
        out.append(Match(
            page=page, start=m.start(1), end=m.end(1), raw=m.group(1) + "-",
            cost=round(cost, 2),
            score=round(max(0.0, 1.0 - cost / len(frag)) * 0.9, 3),
            context=" ".join(text[lo:hi].split()),
            partial=True))
    return out


def find_in_pages(pages, surname: str, **kw):
    """pages: итерируемое из (page_id, text)."""
    out = []
    for page_id, text in pages:
        out.extend(find_in_text(text, surname, page=page_id, **kw))
    # Сортировка по всему документу, а не внутри страниц: иначе лучшее
    # совпадение тонет среди шума с более ранних страниц.
    out.sort(key=lambda r: (-r.score, r.page, r.start))
    return out
